from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from xic_extractor.alignment.config import (
    AlignmentConfig,
    BackfillXicBackend,
    OwnerBuildXicBackend,
)
from xic_extractor.applications.untargeted.provisional_matrix import (
    run_untargeted_provisional_matrix_workflow,
)
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.extraction.provisional_run_manifest import (
    ProvisionalRunRuntimeConfiguration,
    mint_retained_sample_metadata_fingerprint_receipt,
    publish_provisional_run_bundle_atomic,
)
from xic_extractor.extraction.untargeted_input_planning import (
    is_pure_single_provider_cid_nl,
)
from xic_extractor.output.alignment.artifact_write_receipts import (
    AlignmentArtifactFingerprintBatch,
)
from xic_extractor.output.alignment.provisional_matrix import (
    PROVISIONAL_VALIDATION_STATUS,
)
from xic_extractor.presets import (
    bind_cid_nl_recipe,
    load_preset,
)
from xic_extractor.processing_identity import fingerprint_file_content
from xic_extractor.raw_reader import preflight_raw_reader
from xic_extractor.sample_metadata import SampleMetadata, load_sample_metadata


@dataclass(frozen=True, slots=True)
class _OperatorInputs:
    raw_files: tuple[Path, ...]
    dll_dir: Path
    discovery_settings: DiscoverySettings
    alignment_config: AlignmentConfig
    sample_metadata_path: Path
    sample_metadata: tuple[SampleMetadata, ...]
    owner_build_xic_backend: OwnerBuildXicBackend
    code_revision: str


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    started = time.perf_counter()
    heartbeat: _Heartbeat | None = None
    try:
        inputs = _resolve_inputs(args)
        output_dir = args.output_dir.resolve()
        manifest_root = args.manifest_root.resolve()
        attempt_dir = output_dir.with_name(f"{output_dir.name}.attempt")
        if output_dir.exists() or attempt_dir.exists():
            raise ValueError("final or attempt output already exists")
        if not manifest_root.is_dir():
            raise ValueError("manifest root does not exist")
        try:
            output_dir.relative_to(manifest_root)
        except ValueError as exc:
            raise ValueError("output directory must stay under manifest root") from exc
        runtime_configuration = ProvisionalRunRuntimeConfiguration(
            sample_count=len(inputs.raw_files),
            raw_workers=args.raw_workers,
            raw_xic_batch_size=args.raw_xic_batch_size,
            owner_build_xic_backend=inputs.owner_build_xic_backend,
            owner_backfill_xic_backend=_backfill_backend(
                args.owner_backfill_xic_backend
            ),
            owner_backfill_window_strategy=args.owner_backfill_window_strategy,
            owner_backfill_superwindow_span_factor=(
                args.owner_backfill_superwindow_span_factor
            ),
        )

        if args.preflight_only:
            print("Preflight status: pass (no RAW opened)")
            print(f"Sample count: {len(inputs.raw_files)}")
            print(
                "Discovery tags: "
                + ", ".join(
                    profile.tag
                    for profile in inputs.discovery_settings.neutral_loss_profiles
                )
            )
            print("Precursor filter: none")
            print("Backfill scope: full-audit")
            print(f"Validation status: {PROVISIONAL_VALIDATION_STATUS}")
            return 0

        attempt_dir.mkdir(parents=True)
        retained_inputs_dir = attempt_dir / "inputs"
        retained_inputs_dir.mkdir()
        retained_sample_metadata = retained_inputs_dir / (
            "sample_metadata"
            + _retained_sample_metadata_suffix(inputs.sample_metadata_path)
        )
        timing_recorder = TimingRecorder(
            "untargeted-provisional-matrix",
            live_output_path=attempt_dir / "timing.live.json",
            instrument_writers=True,
        )
        timing_recorder.set_measurement_contract(
            cache_state="uncontrolled_os_cache_no_eviction",
            aggregation={
                "nested_wall": "separate",
                "exclusive_wall": "non_overlapping",
            },
            repetition_noise_design={
                "paired_comparable_observations": True,
                "no_raw_synthetic": "repeated observations with observed noise",
                "fixture_cohort": "paired per-sample/interleaved where practical",
                "full_cohort": "one comparable pair with 85-sample/stage observations",
                "effect_rule": (
                    "effect must clear observed noise; small-sample p95 is descriptive"
                ),
            },
            numeric_budget={
                "scope": "W0 programme decision, not XIC-wide rule",
                "first_full_cohort_milestone_seconds": 2700,
                "minimum_comparable_improvement_fraction": 0.25,
                "next_milestone_seconds": 1800,
            },
        )
        for metric, reason in (
            (
                "ipc_bytes",
                "Discovery reports partial pickle payload-size estimates for "
                "submitted process jobs/results; executor framing and other "
                "workflow IPC remain unavailable",
            ),
            (
                "process_tree_peak_rss_bytes",
                "No reliable process-tree RSS owner is available in W0a",
            ),
            (
                "worker_peak_rss_bytes",
                "No reliable worker RSS owner is available in W0a",
            ),
        ):
            timing_recorder.observe(
                metric,
                value=None,
                unit="bytes",
                availability="unavailable",
                source="untargeted_provisional_matrix.runner",
                reason=reason,
            )
        timing_recorder.observe(
            "fingerprint_coverage",
            value=None,
            scope="acquisition_sources",
            unit="calls",
            availability="unavailable",
            source="untargeted_provisional_matrix.runner",
            reason=(
                "acquisition fingerprint calls occur inside Discovery/runtime "
                "owners and are not wired to this recorder"
            ),
        )
        shutil.copyfile(inputs.sample_metadata_path, retained_sample_metadata)
        source_metadata_fingerprint = fingerprint_file_content(
            inputs.sample_metadata_path,
            observation=lambda event: _record_fingerprint_observation(
                timing_recorder, event
            ),
        )
        retained_metadata_fingerprint = (
            mint_retained_sample_metadata_fingerprint_receipt(
                retained_sample_metadata,
                root=attempt_dir,
                observation=lambda event: _record_fingerprint_observation(
                    timing_recorder, event
                ),
            )
        )
        if (
            source_metadata_fingerprint
            != retained_metadata_fingerprint.fingerprint
        ):
            raise ValueError("retained sample metadata does not match its source")
        heartbeat = _Heartbeat(attempt_dir / "heartbeat.json", started=started)
        heartbeat.write_best_effort(
            event="started",
            sample_count=len(inputs.raw_files),
        )
        def progress(event: Mapping[str, object]) -> None:
            timing_recorder.heartbeat_event(event)
            heartbeat.progress(event)

        run = run_untargeted_provisional_matrix_workflow(
            raw_files=inputs.raw_files,
            dll_dir=inputs.dll_dir,
            output_dir=attempt_dir / "workflow",
            discovery_settings=inputs.discovery_settings,
            alignment_config=inputs.alignment_config,
            raw_workers=runtime_configuration.raw_workers,
            raw_xic_batch_size=runtime_configuration.raw_xic_batch_size,
            owner_build_xic_backend=runtime_configuration.owner_build_xic_backend,
            owner_backfill_xic_backend=(
                runtime_configuration.owner_backfill_xic_backend
            ),
            owner_backfill_window_strategy=(
                runtime_configuration.owner_backfill_window_strategy
            ),
            owner_backfill_superwindow_span_factor=(
                runtime_configuration.owner_backfill_superwindow_span_factor
            ),
            sample_column_injection_order=(
                None
                if is_pure_single_provider_cid_nl(inputs.discovery_settings)
                else retained_sample_metadata
            ),
            sample_metadata=inputs.sample_metadata,
            timing_recorder=timing_recorder,
            progress_callback=progress,
            capture_alignment_artifact_fingerprints=True,
        )
        timing_recorder.write_json(attempt_dir / "timing.json")
        matrix_path = run.alignment_outputs.provisional_matrix_tsv
        identity_path = run.alignment_outputs.provisional_identity_tsv
        provenance_path = run.alignment_outputs.provisional_cell_provenance_tsv
        summary_path = run.alignment_outputs.provisional_summary_json
        if (
            matrix_path is None
            or identity_path is None
            or provenance_path is None
            or summary_path is None
        ):
            raise RuntimeError("provisional output bundle is incomplete")
        alignment_fingerprint_batch = (
            run.alignment_outputs.artifact_fingerprint_batch
        )
        if not isinstance(
            alignment_fingerprint_batch,
            AlignmentArtifactFingerprintBatch,
        ):
            raise RuntimeError(
                "provisional output bundle omitted Alignment fingerprints"
            )
        matrix_relative = matrix_path.relative_to(attempt_dir)
        identity_relative = identity_path.relative_to(attempt_dir)
        provenance_relative = provenance_path.relative_to(attempt_dir)
        heartbeat.write_best_effort(
            event="completed",
            provisional_matrix=str(matrix_relative),
        )
        publish_provisional_run_bundle_atomic(
            attempt_dir,
            output_dir,
            code_revision=inputs.code_revision,
            preset_name=args.preset,
            discovery_settings=inputs.discovery_settings,
            alignment_config=inputs.alignment_config,
            runtime=runtime_configuration,
            retained_sample_metadata=retained_sample_metadata,
            discovery_batch_index=run.discovery_index,
            artifacts={
                "provisional_matrix": matrix_path,
                "provisional_identity": identity_path,
                "provisional_cell_provenance": provenance_path,
                "provisional_summary": summary_path,
            },
            retained_sample_metadata_fingerprint=retained_metadata_fingerprint,
            alignment_artifact_fingerprints=alignment_fingerprint_batch,
        )
    except Exception as exc:
        if heartbeat is not None:
            heartbeat.write_best_effort(
                event="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"Validation status: {PROVISIONAL_VALIDATION_STATUS}")
    print(f"Provisional Matrix TSV: {output_dir / matrix_relative}")
    print(f"Row identity TSV: {output_dir / identity_relative}")
    print(f"Cell provenance TSV: {output_dir / provenance_relative}")
    return 0


def _resolve_inputs(args: argparse.Namespace) -> _OperatorInputs:
    raw_dir = args.raw_dir.resolve()
    dll_dir = args.dll_dir.resolve()
    sample_metadata_path = args.sample_metadata.resolve()
    if not raw_dir.is_dir():
        raise ValueError("RAW directory does not exist")
    if not dll_dir.is_dir():
        raise ValueError("Thermo DLL directory does not exist")
    raw_reader_errors = preflight_raw_reader(dll_dir)
    if raw_reader_errors:
        raise ValueError(
            "Thermo RAW reader preflight failed: " + " ".join(raw_reader_errors)
        )
    code_revision = _executing_checkout_revision(args.code_revision)
    if not sample_metadata_path.is_file():
        raise ValueError("sample metadata does not exist")
    _retained_sample_metadata_suffix(sample_metadata_path)

    metadata = load_sample_metadata(sample_metadata_path)
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
    casing_mismatches = tuple(
        f"{row.raw_stem} != {raw_by_stem[key].stem}"
        for row in metadata
        if (key := row.raw_stem.casefold()) in raw_by_stem
        and row.raw_stem != raw_by_stem[key].stem
    )
    if casing_mismatches:
        raise ValueError(
            "RAW stem casing differs from sample metadata: "
            + ", ".join(casing_mismatches)
        )
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

    preset = load_preset(args.preset)
    recipe = bind_cid_nl_recipe(
        preset,
        "full",
        recipe_overrides=(
            {"owner_build_xic_backend": args.owner_build_xic_backend}
            if args.owner_build_xic_backend is not None
            else None
        ),
    )
    return _OperatorInputs(
        raw_files=tuple(raw_by_stem[row.raw_stem.casefold()] for row in metadata),
        dll_dir=dll_dir,
        discovery_settings=recipe.discovery_settings,
        alignment_config=recipe.alignment_config,
        sample_metadata_path=sample_metadata_path,
        sample_metadata=metadata,
        owner_build_xic_backend=recipe.owner_build_xic_backend,
        code_revision=code_revision,
    )


class _Heartbeat:
    def __init__(self, path: Path, *, started: float) -> None:
        self._path = path
        self._started = started

    def progress(self, event: Mapping[str, object]) -> None:
        event_name = event.get("event")
        if not isinstance(event_name, str):
            raise ValueError("heartbeat progress event requires an event name")
        self.write_best_effort(
            event=event_name,
            **{key: value for key, value in event.items() if key != "event"},
        )

    def write_best_effort(self, *, event: str, **values: object) -> None:
        try:
            _write_json_atomic(
                self._path,
                {
                    "validation_status": PROVISIONAL_VALIDATION_STATUS,
                    "event": event,
                    "elapsed_seconds": time.perf_counter() - self._started,
                    **values,
                },
            )
        except OSError as exc:
            print(
                f"warning: could not update heartbeat ({event}): {exc}",
                file=sys.stderr,
            )


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for attempt in range(5):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def _record_fingerprint_observation(
    recorder: TimingRecorder,
    event: Mapping[str, object],
) -> None:
    availability = str(event.get("availability", "unavailable"))
    reason = str(event.get("reason", ""))
    scope = str(event.get("scope", "run"))
    recorder.observe(
        "fingerprint_calls",
        value=event.get("calls"),
        scope=scope,
        unit="calls",
        source="processing_identity.fingerprint_file_content",
        availability=availability,
        reason=reason,
    )
    recorder.observe(
        "fingerprint_bytes",
        value=event.get("bytes"),
        scope=scope,
        unit="bytes",
        source="processing_identity.fingerprint_file_content",
        availability=availability,
        reason=reason,
    )
    recorder.observe(
        "fingerprint_time_sec",
        value=event.get("elapsed_sec"),
        scope=scope,
        unit="seconds",
        source="processing_identity.fingerprint_file_content",
        availability=availability,
        reason=reason,
    )


def _backfill_backend(value: str) -> BackfillXicBackend:
    mapped = {
        "raw": "raw",
        "ms1-index": "ms1_index",
        "ms1-index-hybrid": "ms1_index_hybrid",
    }.get(value)
    if mapped is None:
        raise ValueError(f"unsupported owner Backfill XIC backend: {value}")
    return cast(BackfillXicBackend, mapped)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete Untargeted Discovery universe through existing "
            "Alignment/Backfill and publish a provisional diagnostic Matrix."
        )
    )
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument("--sample-metadata", type=Path, required=True)
    parser.add_argument("--preset", required=True)
    parser.add_argument(
        "--code-revision",
        type=_code_revision,
        help="Optional assertion; the clean executing checkout revision is recorded.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest-root", type=Path, default=Path.cwd())
    parser.add_argument("--expected-sample-count", type=_positive_int)
    parser.add_argument("--raw-workers", type=_positive_int, default=1)
    parser.add_argument("--raw-xic-batch-size", type=_positive_int, default=1)
    parser.add_argument(
        "--owner-build-xic-backend",
        choices=("raw", "raw-super-window", "ms1-index"),
    )
    parser.add_argument(
        "--owner-backfill-xic-backend",
        choices=("raw", "ms1-index", "ms1-index-hybrid"),
        default="raw",
    )
    parser.add_argument(
        "--owner-backfill-window-strategy",
        choices=("exact", "super-window"),
        default="exact",
    )
    parser.add_argument(
        "--owner-backfill-superwindow-span-factor",
        type=_positive_int,
        default=2,
    )
    parser.add_argument("--preflight-only", action="store_true")
    return parser.parse_args(argv)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def _code_revision(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise argparse.ArgumentTypeError(
            "code revision must be a lowercase 40-character Git SHA"
        )
    return value


def _retained_sample_metadata_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".tsv", ".txt"}:
        raise ValueError("sample metadata must use .csv, .tsv, or .txt")
    return suffix


def _executing_checkout_revision(asserted_revision: str | None) -> str:
    repo_root = Path(__file__).resolve().parents[1]
    observed_root = Path(_git_output(repo_root, "rev-parse", "--show-toplevel"))
    if observed_root.resolve() != repo_root:
        raise ValueError("runner source is not bound to the executing Git checkout")
    revision = _code_revision(_git_output(repo_root, "rev-parse", "HEAD"))
    if _git_output(repo_root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("executing Git checkout must be clean")
    if asserted_revision is not None and asserted_revision != revision:
        raise ValueError("asserted code revision differs from executing checkout")
    return revision


def _git_output(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo_root), *args),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise ValueError("executing Git checkout is unavailable") from exc
    if completed.returncode != 0:
        raise ValueError("executing Git checkout is unavailable")
    return completed.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
