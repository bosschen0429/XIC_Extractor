from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.mode_track_table_finalization import (
    VALIDATION_STATUS,
    ModeTrackReviewBundleSummary,
    finalize_reviewed_mode_track_projection,
    inspect_mode_track_review_bundle,
)
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.extraction.untargeted_cid_nl_mode_track_workflow import (
    ModeTrackWorkflowRun,
    run_untargeted_cid_nl_mode_track_workflow,
)
from xic_extractor.presets import apply_to_discovery, load_preset
from xic_extractor.sample_metadata import SampleMetadata, load_sample_metadata

_MANUAL_REVIEW_FILENAME = "manual_review_v1.json"
_REVIEW_REPLAY_FILENAME = "manual_review_observation_replay_v1.json"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    started = time.perf_counter()
    heartbeat: _Heartbeat | None = None
    try:
        inputs = _resolve_inputs(args)
        output_dir = args.output_dir.resolve()
        manifest_root = args.manifest_root.resolve()
        attempt_dir = output_dir.with_name(f"{output_dir.name}.attempt")
        staging_dir = output_dir.with_name(f"{output_dir.name}.staging")
        if any(path.exists() for path in (output_dir, attempt_dir, staging_dir)):
            raise ValueError("final, attempt, or staging output already exists")
        if not manifest_root.is_dir():
            raise ValueError("manifest root does not exist")
        try:
            attempt_relpath = attempt_dir.relative_to(manifest_root)
        except ValueError as exc:
            raise ValueError("output directory must stay under manifest root") from exc
        if args.preflight_only:
            print("Preflight status: pass (no RAW opened)")
            print(f"Sample count: {len(inputs.sample_metadata)}")
            print(
                "Reviewed Backfill cells: "
                f"{inputs.review_summary.reviewed_cell_count}"
            )
            print(
                "Reviewed family counts: "
                + ", ".join(
                    f"{family_id}={count}"
                    for family_id, count in (
                        inputs.review_summary.feature_family_counts
                    )
                )
            )
            print(f"Validation status: {VALIDATION_STATUS}")
            return 0
        attempt_dir.mkdir(parents=True)
        heartbeat = _Heartbeat(attempt_dir / "timing.live.json", started=started)
        heartbeat.write(event="started", sample_count=len(inputs.sample_metadata))

        source_path = attempt_dir / "mode_track_source.json"
        run = run_untargeted_cid_nl_mode_track_workflow(
            raw_files=inputs.raw_files,
            sample_metadata=inputs.sample_metadata,
            dll_dir=inputs.dll_dir,
            output_dir=attempt_dir / "workflow",
            settings=inputs.settings,
            precursor_mz=args.precursor_mz,
            source_artifact_relpath=(
                attempt_relpath / source_path.name
            ).as_posix(),
            sample_identity_namespace=args.sample_identity_namespace,
            progress_callback=heartbeat.progress,
        )
        _validate_projection_census(
            run,
            sample_metadata=inputs.sample_metadata,
            expected_backfill_cell_count=(
                inputs.review_summary.reviewed_cell_count
            ),
            expected_family_counts=dict(
                inputs.review_summary.feature_family_counts
            ),
        )
        source_path.write_bytes(run.projection.source_artifact_bytes)
        heartbeat.write(
            event="projection_complete",
            pending_cell_count=len(run.projection.pending_acceptance_rows),
            call_cost=dict(run.call_cost),
        )
        final = finalize_reviewed_mode_track_projection(
            projection=run.projection,
            manual_review_path=inputs.manual_review_path,
            manual_review_replay_path=inputs.manual_review_replay_path,
            output_dir=output_dir,
        )
        try:
            heartbeat.write(
                event="completed",
                adopted_cell_count=final.adopted_cell_count,
                rollback_status=final.rollback_status,
                final_output=str(output_dir),
            )
        except OSError as exc:
            print(
                f"warning: could not update completion heartbeat: {exc}",
                file=sys.stderr,
            )
    except Exception as exc:
        if heartbeat is not None:
            heartbeat.write(event="failed", error=f"{type(exc).__name__}: {exc}")
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"Validation status: {VALIDATION_STATUS}")
    print(f"Alignment Matrix TSV: {final.matrix_tsv}")
    print(f"Alignment Matrix workbook: {final.matrix_xlsx}")
    return 0


@dataclass(frozen=True, slots=True)
class _OperatorInputs:
    raw_files: tuple[Path, ...]
    sample_metadata: tuple[SampleMetadata, ...]
    dll_dir: Path
    settings: DiscoverySettings
    manual_review_path: Path
    manual_review_replay_path: Path
    review_summary: ModeTrackReviewBundleSummary


def _resolve_inputs(args: argparse.Namespace) -> _OperatorInputs:
    raw_dir = args.raw_dir.resolve()
    dll_dir = args.dll_dir.resolve()
    metadata_path = args.sample_metadata.resolve()
    review_bundle = args.review_bundle_dir.resolve()
    if not raw_dir.is_dir():
        raise ValueError("RAW directory does not exist")
    if not dll_dir.is_dir():
        raise ValueError("Thermo DLL directory does not exist")
    if not any(
        path.is_file() and path.suffix.lower() == ".dll"
        for path in dll_dir.iterdir()
    ):
        raise ValueError("Thermo DLL directory contains no DLL files")
    if not metadata_path.is_file():
        raise ValueError("sample metadata does not exist")
    if not review_bundle.is_dir():
        raise ValueError("review bundle directory does not exist")
    manual_review_path = review_bundle / _MANUAL_REVIEW_FILENAME
    manual_review_replay_path = review_bundle / _REVIEW_REPLAY_FILENAME
    if not manual_review_path.is_file() or not manual_review_replay_path.is_file():
        raise ValueError("review bundle is missing the reviewed JSON inputs")
    review_summary = inspect_mode_track_review_bundle(
        manual_review_path=manual_review_path,
        manual_review_replay_path=manual_review_replay_path,
    )
    if (
        args.expected_backfill_cell_count is not None
        and review_summary.reviewed_cell_count
        != args.expected_backfill_cell_count
    ):
        raise ValueError(
            f"expected {args.expected_backfill_cell_count} reviewed Backfill "
            f"cells, found {review_summary.reviewed_cell_count}"
        )
    expected_family_counts = _expected_family_counts(
        args.expected_backfill_family_count
    )
    observed_family_counts = dict(review_summary.feature_family_counts)
    if expected_family_counts and expected_family_counts != observed_family_counts:
        raise ValueError(
            "reviewed Backfill family counts differ: expected "
            f"{_format_family_counts(expected_family_counts)}, found "
            f"{_format_family_counts(observed_family_counts)}"
        )

    metadata = load_sample_metadata(metadata_path)
    if not metadata or any(not row.raw_stem for row in metadata):
        raise ValueError("sample metadata must contain non-empty raw_stem values")
    if args.expected_sample_count is not None and len(metadata) != (
        args.expected_sample_count
    ):
        raise ValueError(
            f"expected {args.expected_sample_count} samples, found {len(metadata)}"
        )
    raw_by_stem: dict[str, Path] = {}
    for path in raw_dir.iterdir():
        if not path.is_file() or path.suffix.lower() != ".raw":
            continue
        key = path.stem.casefold()
        if key in raw_by_stem:
            raise ValueError(f"duplicate RAW stem: {path.stem}")
        raw_by_stem[key] = path.resolve()
    metadata_keys = {row.raw_stem.casefold() for row in metadata}
    missing = tuple(
        row.raw_stem for row in metadata if row.raw_stem.casefold() not in raw_by_stem
    )
    extra = tuple(
        path.stem for key, path in raw_by_stem.items() if key not in metadata_keys
    )
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing RAW: {', '.join(missing)}")
        if extra:
            details.append(f"RAW without metadata: {', '.join(sorted(extra))}")
        raise ValueError("; ".join(details))
    raw_files = tuple(raw_by_stem[row.raw_stem.casefold()] for row in metadata)
    preset = load_preset(args.preset)
    settings = apply_to_discovery(preset)
    if len(settings.neutral_loss_profiles) != 1:
        raise ValueError("ModeTrack runner requires a single-tag CID-NL preset")
    return _OperatorInputs(
        raw_files=raw_files,
        sample_metadata=metadata,
        dll_dir=dll_dir,
        settings=settings,
        manual_review_path=manual_review_path,
        manual_review_replay_path=manual_review_replay_path,
        review_summary=review_summary,
    )


def _validate_projection_census(
    run: ModeTrackWorkflowRun,
    *,
    sample_metadata: tuple[SampleMetadata, ...],
    expected_backfill_cell_count: int | None,
    expected_family_counts: Mapping[str, int],
) -> None:
    expected_samples = {row.raw_stem for row in sample_metadata}
    matrix_samples = tuple(run.projection.matrix_header[2:])
    if len(matrix_samples) != len(expected_samples) or set(matrix_samples) != (
        expected_samples
    ):
        raise RuntimeError("ModeTrack Matrix sample columns are incomplete")
    pending_count = len(run.projection.pending_acceptance_rows)
    if (
        expected_backfill_cell_count is not None
        and pending_count != expected_backfill_cell_count
    ):
        raise RuntimeError(
            f"expected {expected_backfill_cell_count} Backfill cells, "
            f"found {pending_count}"
        )
    observed_family_counts: dict[str, int] = {}
    for row in run.projection.pending_acceptance_rows:
        family_id = row["feature_family_id"]
        observed_family_counts[family_id] = observed_family_counts.get(family_id, 0) + 1
    if observed_family_counts != dict(expected_family_counts):
        raise RuntimeError(
            "fresh Backfill family counts differ from reviewed evidence: expected "
            f"{_format_family_counts(expected_family_counts)}, found "
            f"{_format_family_counts(observed_family_counts)}"
        )


class _Heartbeat:
    def __init__(self, path: Path, *, started: float) -> None:
        self._path = path
        self._started = started
        self._completed_samples: list[str] = []

    def progress(self, event: Mapping[str, object]) -> None:
        sample_stem = event.get("sample_stem")
        if isinstance(sample_stem, str) and sample_stem not in self._completed_samples:
            self._completed_samples.append(sample_stem)
        event_name = event.get("event")
        if not isinstance(event_name, str):
            raise ValueError("heartbeat progress event requires an event name")
        self.write(
            event=event_name,
            **{key: value for key, value in event.items() if key != "event"},
        )

    def write(self, *, event: str, **values: object) -> None:
        _write_json_atomic(
            self._path,
            {
                "validation_status": VALIDATION_STATUS,
                "event": event,
                "elapsed_seconds": time.perf_counter() - self._started,
                "completed_samples": list(self._completed_samples),
                **values,
            },
        )


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run RAW through Discovery, ModeTrack alignment, conservative Backfill, "
            "and one diagnostic-only final table."
        )
    )
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument("--sample-metadata", type=Path, required=True)
    parser.add_argument("--preset", required=True)
    parser.add_argument("--precursor-mz", type=_positive_float, required=True)
    parser.add_argument("--review-bundle-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest-root", type=Path, default=Path.cwd())
    parser.add_argument("--expected-sample-count", type=_positive_int)
    parser.add_argument("--expected-backfill-cell-count", type=_positive_int)
    parser.add_argument(
        "--expected-backfill-family-count",
        action="append",
        default=[],
        type=_family_count,
        metavar="FAMILY_ID=COUNT",
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument(
        "--sample-identity-namespace",
        required=True,
    )
    return parser.parse_args(argv)


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise argparse.ArgumentTypeError("value must be finite and > 0")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return parsed


def _family_count(value: str) -> tuple[str, int]:
    family_id, separator, raw_count = value.partition("=")
    if not separator or not family_id.strip():
        raise argparse.ArgumentTypeError("value must use FAMILY_ID=COUNT")
    try:
        count = _positive_int(raw_count)
    except (ValueError, argparse.ArgumentTypeError) as exc:
        raise argparse.ArgumentTypeError("count must be an integer > 0") from exc
    return family_id.strip(), count


def _expected_family_counts(
    values: Sequence[tuple[str, int]],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for family_id, count in values:
        if family_id in result:
            raise ValueError(f"duplicate expected family count: {family_id}")
        result[family_id] = count
    return result


def _format_family_counts(counts: Mapping[str, int]) -> str:
    return ", ".join(
        f"{family_id}={count}" for family_id, count in sorted(counts.items())
    )


if __name__ == "__main__":
    raise SystemExit(main())
