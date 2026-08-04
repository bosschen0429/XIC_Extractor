from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol, cast

from xic_extractor.config import ExtractionConfig
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    DiscoveryCandidateUniverseReceipt,
    discovery_receipt_index_fields,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.discovery.csv_writer import (
    format_discovery_csv_value,
    write_discovery_candidates_csv,
    write_discovery_review_csv,
)
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.models import (
    DiscoveryBatchOutputs,
    DiscoveryCandidate,
    DiscoveryRunOutputs,
    DiscoverySettings,
    ReviewPriority,
)
from xic_extractor.discovery.ms1_backfill import MS1XicSource, backfill_ms1_candidates
from xic_extractor.discovery.ms2_seeds import MS2ScanSource, collect_strict_nl_seeds
from xic_extractor.discovery.peak_anchor import assign_peak_anchors
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_acquisition_source_identity,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.tabular_io import write_delimited_rows

_BATCH_INDEX_COLUMNS = (
    "schema_version",
    "sample_stem",
    "raw_file",
    "candidate_csv",
    "review_csv",
    "candidate_count",
    "high_count",
    "medium_count",
    "low_count",
    *DISCOVERY_BATCH_RECEIPT_COLUMNS,
)


class DiscoveryRawHandle(MS1XicSource, MS2ScanSource, Protocol):
    """RAW reader surface needed by single-file discovery orchestration."""


RawOpener = Callable[[Path, Path], AbstractContextManager[DiscoveryRawHandle]]


def run_discovery(
    raw_path: Path,
    *,
    output_dir: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw_opener: RawOpener | None = None,
    timing_recorder: TimingRecorder | None = None,
) -> DiscoveryRunOutputs:
    opener = raw_opener or _default_raw_opener
    recorder = timing_recorder or TimingRecorder.disabled("discovery")
    sample_stem = raw_path.stem
    discovered = _discover_raw_file(
        raw_path,
        settings=settings,
        peak_config=peak_config,
        raw_opener=opener,
        timing_recorder=recorder,
    )
    with recorder.stage(
        "discover.peak_anchor",
        sample_stem=sample_stem,
        metrics={"input_count": len(discovered)},
    ) as stage:
        candidates = assign_peak_anchors(discovered, settings=settings)
        stage.metrics["candidate_count"] = len(candidates)
    return _write_dual_csvs(
        output_dir,
        candidates,
        timing_recorder=recorder,
        sample_stem=sample_stem,
    )


def run_discovery_batch(
    raw_paths: tuple[Path, ...],
    *,
    output_dir: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw_opener: RawOpener | None = None,
    timing_recorder: TimingRecorder | None = None,
) -> DiscoveryBatchOutputs:
    recorder = timing_recorder or TimingRecorder.disabled("discovery")
    index_rows: list[dict[str, str]] = []
    per_sample: list[DiscoveryRunOutputs] = []
    for raw_path in raw_paths:
        sample_stem = raw_path.stem
        acquisition_source = _acquisition_source(raw_path)
        opener = raw_opener or _owner_bound_raw_opener(acquisition_source)
        discovered = _discover_raw_file(
            raw_path,
            settings=settings,
            peak_config=peak_config,
            raw_opener=opener,
            timing_recorder=recorder,
        )
        with recorder.stage(
            "discover.peak_anchor",
            sample_stem=sample_stem,
            metrics={"input_count": len(discovered)},
        ) as stage:
            candidates = assign_peak_anchors(discovered, settings=settings)
            stage.metrics["candidate_count"] = len(candidates)
        sample_output_dir = output_dir / sample_stem
        outputs = _write_dual_csvs(
            sample_output_dir,
            candidates,
            timing_recorder=recorder,
            sample_stem=sample_stem,
        )
        per_sample.append(outputs)
        receipt = mint_discovery_candidate_universe_receipt(
            sample_stem=sample_stem,
            acquisition_source=acquisition_source,
            candidate_csv=outputs.candidates_csv,
            candidates=candidates,
        )
        index_rows.append(
            _batch_index_row(
                raw_path=raw_path,
                outputs=outputs,
                candidates=candidates,
                receipt=receipt,
            )
        )

    return DiscoveryBatchOutputs(
        batch_index_csv=_write_batch_index(
            output_dir / "discovery_batch_index.csv",
            index_rows,
            timing_recorder=recorder,
        ),
        per_sample=tuple(per_sample),
    )


def _write_dual_csvs(
    output_dir: Path,
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    timing_recorder: TimingRecorder,
    sample_stem: str,
) -> DiscoveryRunOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / "discovery_candidates.csv"
    review_path = output_dir / "discovery_review.csv"
    candidates_tmp = candidates_path.with_name(f"{candidates_path.name}.tmp")
    review_tmp = review_path.with_name(f"{review_path.name}.tmp")

    try:
        with timing_recorder.stage(
            "discover.write_candidates_csv",
            sample_stem=sample_stem,
            metrics={"row_count": len(candidates)},
        ):
            write_discovery_candidates_csv(candidates_tmp, candidates)
        with timing_recorder.stage(
            "discover.write_review_csv",
            sample_stem=sample_stem,
            metrics={"row_count": len(candidates)},
        ):
            write_discovery_review_csv(review_tmp, candidates)
        candidates_tmp.replace(candidates_path)
        review_tmp.replace(review_path)
    except Exception:
        candidates_tmp.unlink(missing_ok=True)
        review_tmp.unlink(missing_ok=True)
        raise

    return DiscoveryRunOutputs(
        candidates_csv=candidates_path,
        review_csv=review_path,
    )


def _batch_index_row(
    *,
    raw_path: Path,
    outputs: DiscoveryRunOutputs,
    candidates: tuple[DiscoveryCandidate, ...],
    receipt: DiscoveryCandidateUniverseReceipt,
) -> dict[str, str]:
    counts = _priority_counts(candidates)
    return {
        "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        "sample_stem": format_discovery_csv_value("sample_stem", raw_path.stem),
        "raw_file": format_discovery_csv_value("raw_file", raw_path),
        "candidate_csv": format_discovery_csv_value(
            "candidate_csv", outputs.candidates_csv
        ),
        "review_csv": format_discovery_csv_value("review_csv", outputs.review_csv),
        "candidate_count": str(len(candidates)),
        "high_count": str(counts["HIGH"]),
        "medium_count": str(counts["MEDIUM"]),
        "low_count": str(counts["LOW"]),
        **discovery_receipt_index_fields(receipt),
    }


def _priority_counts(
    candidates: tuple[DiscoveryCandidate, ...],
) -> dict[ReviewPriority, int]:
    counts: dict[ReviewPriority, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for candidate in candidates:
        counts[candidate.review_priority] += 1
    return counts


def _write_batch_index(
    output_path: Path,
    rows: list[dict[str, str]],
    *,
    timing_recorder: TimingRecorder,
) -> Path:
    with timing_recorder.stage(
        "discover.write_batch_index",
        metrics={"raw_count": len(rows), "row_count": len(rows)},
    ):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_delimited_rows(
            output_path,
            rows,
            _BATCH_INDEX_COLUMNS,
            delimiter=",",
            encoding="utf-8",
            extrasaction="raise",
        )
    return output_path


def _discover_raw_file(
    raw_path: Path,
    *,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw_opener: RawOpener,
    timing_recorder: TimingRecorder,
) -> tuple[DiscoveryCandidate, ...]:
    sample_stem = raw_path.stem
    with raw_opener(raw_path, peak_config.dll_dir) as raw:
        with timing_recorder.stage(
            "discover.ms2_seeds",
            sample_stem=sample_stem,
        ) as stage:
            seeds = collect_strict_nl_seeds(raw, raw_file=raw_path, settings=settings)
            stage.metrics["seed_count"] = len(seeds)
        with timing_recorder.stage(
            "discover.group_seeds",
            sample_stem=sample_stem,
        ) as stage:
            groups = group_discovery_seeds(seeds, settings=settings)
            stage.metrics["group_count"] = len(groups)
        with timing_recorder.stage(
            "discover.ms1_backfill",
            sample_stem=sample_stem,
        ) as stage:
            candidates = backfill_ms1_candidates(
                raw,
                groups,
                settings=settings,
                peak_config=peak_config,
            )
            stage.metrics["candidate_count"] = len(candidates)
        return candidates


def _default_raw_opener(
    raw_path: Path,
    dll_dir: Path,
) -> AbstractContextManager[DiscoveryRawHandle]:
    from xic_extractor.raw_reader import open_raw

    return cast(AbstractContextManager[DiscoveryRawHandle], open_raw(raw_path, dll_dir))


def _owner_bound_raw_opener(
    acquisition_source: AcquisitionSourceIdentity,
) -> RawOpener:
    """Bind the production batch reader to the content identity it will receipt."""

    def _open(
        raw_path: Path,
        dll_dir: Path,
    ) -> AbstractContextManager[DiscoveryRawHandle]:
        from xic_extractor.raw_reader import open_raw

        return cast(
            AbstractContextManager[DiscoveryRawHandle],
            open_raw(
                raw_path,
                dll_dir,
                expected_acquisition_source=acquisition_source,
            ),
        )

    return _open


def _acquisition_source(raw_path: Path) -> AcquisitionSourceIdentity:
    return build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
