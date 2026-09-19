from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.csv_io import (
    DiscoveryBatchInput,
    read_discovery_batch_index,
    read_discovery_candidates_csv,
)
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.neutral_loss import (
    CandidateMS2Evidence,
    collect_candidate_ms2_evidence,
)
from xic_extractor.raw_reader import Ms2ScanEvent
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS
from xic_extractor.tabular_io import read_tsv_required, write_tsv

CANDIDATE_MS2_PATTERN_SCHEMA_VERSION = "shared_peak_identity_candidate_ms2_pattern"
CANDIDATE_MS2_PATTERN_COLUMNS = (
    "candidate_ms2_pattern_schema_version",
    "feature_family_id",
    "sample_stem",
    "candidate_ms2_pattern_status",
    "candidate_ms2_evidence_level",
    "source_candidate_id",
    "source_candidate_status",
    "source_discovery_feature_family_id",
    "source_ms2_support",
    "source_evidence_tier",
    "source_evidence_score",
    "source_matched_tag_count",
    "source_matched_tag_names",
    "source_best_ms2_scan_id",
    "source_seed_scan_ids",
    "source_product_mz",
    "source_observed_neutral_loss_da",
    "source_neutral_loss_mass_error_ppm",
    "family_product_mz",
    "family_observed_neutral_loss_da",
    "product_mz_delta_ppm",
    "observed_loss_delta_ppm",
    "candidate_ms2_similarity_score",
    "matched_product_count",
    "matched_neutral_loss_count",
    "apex_ms2_delta_sec",
    "ms2_alignment_source",
    "raw_ms2_trigger_scan_count",
    "raw_ms2_strict_nl_scan_count",
    "raw_ms2_best_loss_ppm",
    "raw_ms2_best_scan_rt",
    "raw_ms2_best_product_base_ratio",
    "raw_ms2_diagnostic_product_absence_reason",
    "raw_ms2_nearest_product_loss_ppm",
    "raw_ms2_nearest_product_base_ratio",
    "raw_ms2_nearest_product_mz",
    "raw_ms2_trace_product_point_count",
    "raw_ms2_trace_product_apex_rt",
    "raw_ms2_trace_product_apex_delta_sec",
    "raw_ms2_trace_strength",
    "raw_file_path",
    "raw_reader_runtime",
    "nl_ppm_warn",
    "nl_ppm_max",
    "ms2_precursor_tol_da",
    "nl_min_intensity_ratio",
    "reason",
    "diagnostic_only",
)
_ALIGNMENT_CELL_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "apex_rt",
    "peak_start_rt",
    "peak_end_rt",
    "source_candidate_id",
)
_ALIGNMENT_REVIEW_COLUMNS = (
    "feature_family_id",
    "family_center_mz",
    "family_product_mz",
    "family_observed_neutral_loss_da",
)
_DIRECT_CANDIDATE_PATTERN_SOURCE = "discovery_source_candidate"
_RAW_BOUNDARY_PATTERN_SOURCE = "raw_boundary_scan"


class MS2ScanSourceContext(Protocol):
    def iter_ms2_scans(
        self, rt_min: float, rt_max: float
    ) -> Iterator[Ms2ScanEvent]: ...


RawScanSourceFactory = Callable[[str], AbstractContextManager[MS2ScanSourceContext]]


@dataclass(frozen=True)
class RawCandidateMS2PatternConfig:
    nl_ppm_warn: float = AlignmentConfig().observed_loss_tolerance_ppm
    nl_ppm_max: float = AlignmentConfig().max_ppm
    ms2_precursor_tol_da: float = float(
        CANONICAL_SETTINGS_DEFAULTS["ms2_precursor_tol_da"]
    )
    nl_min_intensity_ratio: float = float(
        CANONICAL_SETTINGS_DEFAULTS["nl_min_intensity_ratio"]
    )


@dataclass(frozen=True)
class _BoundaryPeak:
    peak_start: float
    peak_end: float


@dataclass(frozen=True)
class _BoundaryCandidate:
    peak: _BoundaryPeak
    selection_apex_rt: float
    ms2_evidence_peak_start: float | None = None
    ms2_evidence_peak_end: float | None = None


def build_candidate_ms2_pattern_rows(
    *,
    alignment_cells_tsv: Path,
    alignment_review_tsv: Path,
    discovery_batch_index_csv: Path,
    oracle_keys: Iterable[tuple[str, str]],
    config: AlignmentConfig | None = None,
    raw_dll_dir: Path | None = None,
    raw_scan_source_factory: RawScanSourceFactory | None = None,
    raw_config: RawCandidateMS2PatternConfig | None = None,
) -> tuple[dict[str, str], ...]:
    config = config or AlignmentConfig()
    raw_config = raw_config or RawCandidateMS2PatternConfig()
    cells = _cell_by_key(
        read_tsv_required(alignment_cells_tsv, _ALIGNMENT_CELL_COLUMNS)
    )
    family_context = _family_context_by_id(
        read_tsv_required(alignment_review_tsv, _ALIGNMENT_REVIEW_COLUMNS)
    )
    batch = read_discovery_batch_index(discovery_batch_index_csv)
    candidates = _candidate_by_id(batch)
    if raw_scan_source_factory is None and raw_dll_dir is not None:
        raw_scan_source_factory = _raw_scan_source_factory(batch, raw_dll_dir)
    rows = [
        _row_for_key(
            feature_family_id=feature_family_id,
            sample_stem=sample_stem,
            cell=cells.get((feature_family_id, sample_stem)),
            family_context=family_context.get(feature_family_id, {}),
            candidates=candidates,
            config=config,
            raw_scan_source_factory=raw_scan_source_factory,
            raw_files=batch.raw_files,
            raw_config=raw_config,
        )
        for feature_family_id, sample_stem in oracle_keys
    ]
    return tuple(
        sorted(rows, key=lambda row: (row["feature_family_id"], row["sample_stem"]))
    )


def write_candidate_ms2_pattern_rows(
    path: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(path, rows, CANDIDATE_MS2_PATTERN_COLUMNS, lineterminator="\n")


def _row_for_key(
    *,
    feature_family_id: str,
    sample_stem: str,
    cell: Mapping[str, str] | None,
    family_context: Mapping[str, str],
    candidates: Mapping[str, DiscoveryCandidate],
    config: AlignmentConfig,
    raw_scan_source_factory: RawScanSourceFactory | None,
    raw_files: Mapping[str, Path | None],
    raw_config: RawCandidateMS2PatternConfig,
) -> dict[str, str]:
    source_candidate_id = str((cell or {}).get("source_candidate_id", "")).strip()
    base = {
        "candidate_ms2_pattern_schema_version": CANDIDATE_MS2_PATTERN_SCHEMA_VERSION,
        "feature_family_id": feature_family_id,
        "sample_stem": sample_stem,
        "candidate_ms2_pattern_status": "not_available",
        "candidate_ms2_evidence_level": "not_available",
        "source_candidate_id": source_candidate_id,
        "source_candidate_status": "not_available",
        "source_discovery_feature_family_id": "",
        "source_ms2_support": "",
        "source_evidence_tier": "",
        "source_evidence_score": "",
        "source_matched_tag_count": "",
        "source_matched_tag_names": "",
        "source_best_ms2_scan_id": "",
        "source_seed_scan_ids": "",
        "source_product_mz": "",
        "source_observed_neutral_loss_da": "",
        "source_neutral_loss_mass_error_ppm": "",
        "family_product_mz": family_context.get("family_product_mz", ""),
        "family_observed_neutral_loss_da": family_context.get(
            "family_observed_neutral_loss_da", ""
        ),
        "product_mz_delta_ppm": "",
        "observed_loss_delta_ppm": "",
        "candidate_ms2_similarity_score": "",
        "matched_product_count": "",
        "matched_neutral_loss_count": "",
        "apex_ms2_delta_sec": "",
        "ms2_alignment_source": "",
        "raw_ms2_trigger_scan_count": "",
        "raw_ms2_strict_nl_scan_count": "",
        "raw_ms2_best_loss_ppm": "",
        "raw_ms2_best_scan_rt": "",
        "raw_ms2_best_product_base_ratio": "",
        "raw_ms2_diagnostic_product_absence_reason": "",
        "raw_ms2_nearest_product_loss_ppm": "",
        "raw_ms2_nearest_product_base_ratio": "",
        "raw_ms2_nearest_product_mz": "",
        "raw_ms2_trace_product_point_count": "",
        "raw_ms2_trace_product_apex_rt": "",
        "raw_ms2_trace_product_apex_delta_sec": "",
        "raw_ms2_trace_strength": "",
        "raw_file_path": "",
        "raw_reader_runtime": "",
        "nl_ppm_warn": "",
        "nl_ppm_max": "",
        "ms2_precursor_tol_da": "",
        "nl_min_intensity_ratio": "",
        "reason": "",
        "diagnostic_only": "TRUE",
    }
    if cell is None:
        return {**base, "reason": "alignment_cell_missing"}
    if not source_candidate_id:
        raw_row = _raw_boundary_row_for_missing_source(
            base=base,
            sample_stem=sample_stem,
            cell=cell,
            family_context=family_context,
            raw_scan_source_factory=raw_scan_source_factory,
            raw_file=raw_files.get(sample_stem),
            raw_config=raw_config,
        )
        if raw_row is not None:
            return raw_row
        return {**base, "reason": "source_candidate_id_missing"}
    candidate = candidates.get(source_candidate_id)
    if candidate is None:
        return {
            **base,
            "source_candidate_status": "missing_from_discovery_batch",
            "reason": "source_candidate_not_found_in_batch_index",
        }

    product_delta = _ppm_delta(
        candidate.product_mz,
        _float_or_none(family_context.get("family_product_mz")),
    )
    observed_loss_delta = _ppm_delta(
        candidate.observed_neutral_loss_da,
        _float_or_none(family_context.get("family_observed_neutral_loss_da")),
    )
    matched_neutral_loss_count = max(0, candidate.matched_tag_count)
    has_ms2_pattern = matched_neutral_loss_count > 0 and bool(
        candidate.matched_tag_names
    )
    pattern_status = _pattern_status(
        has_ms2_pattern=has_ms2_pattern,
        product_delta_ppm=product_delta,
        observed_loss_delta_ppm=observed_loss_delta,
        source_neutral_loss_error_ppm=candidate.neutral_loss_mass_error_ppm,
        nl_ppm_warn=raw_config.nl_ppm_warn,
        nl_ppm_max=raw_config.nl_ppm_max,
        config=config,
    )
    evidence_level = (
        "sample_candidate_aligned"
        if pattern_status in {"supportive", "partial_support", "conflict"}
        else "sample_candidate_no_observed_pattern"
    )
    return {
        **base,
        "candidate_ms2_pattern_status": pattern_status,
        "candidate_ms2_evidence_level": evidence_level,
        "source_candidate_status": "found",
        "source_discovery_feature_family_id": candidate.feature_family_id,
        "source_ms2_support": candidate.ms2_support,
        "source_evidence_tier": candidate.evidence_tier,
        "source_evidence_score": str(candidate.evidence_score),
        "source_matched_tag_count": str(candidate.matched_tag_count),
        "source_matched_tag_names": ";".join(candidate.matched_tag_names),
        "source_best_ms2_scan_id": str(candidate.best_ms2_scan_id),
        "source_seed_scan_ids": ";".join(str(scan) for scan in candidate.seed_scan_ids),
        "source_product_mz": _format_float(candidate.product_mz),
        "source_observed_neutral_loss_da": _format_float(
            candidate.observed_neutral_loss_da
        ),
        "source_neutral_loss_mass_error_ppm": _format_float(
            candidate.neutral_loss_mass_error_ppm
        ),
        "product_mz_delta_ppm": _format_optional_float(product_delta),
        "observed_loss_delta_ppm": _format_optional_float(observed_loss_delta),
        "candidate_ms2_similarity_score": _pattern_similarity_score(pattern_status),
        "matched_product_count": "1" if has_ms2_pattern else "0",
        "matched_neutral_loss_count": str(matched_neutral_loss_count),
        "apex_ms2_delta_sec": _apex_ms2_delta_sec(cell, candidate),
        "ms2_alignment_source": _DIRECT_CANDIDATE_PATTERN_SOURCE,
        "nl_ppm_warn": _format_float(raw_config.nl_ppm_warn),
        "nl_ppm_max": _format_float(raw_config.nl_ppm_max),
        "reason": _reason(
            has_ms2_pattern=has_ms2_pattern,
            pattern_status=pattern_status,
            product_delta_ppm=product_delta,
            observed_loss_delta_ppm=observed_loss_delta,
            source_neutral_loss_status=_source_neutral_loss_status(
                candidate.neutral_loss_mass_error_ppm,
                nl_ppm_warn=raw_config.nl_ppm_warn,
                nl_ppm_max=raw_config.nl_ppm_max,
            ),
        ),
    }


def _pattern_status(
    *,
    has_ms2_pattern: bool,
    product_delta_ppm: float | None,
    observed_loss_delta_ppm: float | None,
    source_neutral_loss_error_ppm: float | None,
    nl_ppm_warn: float,
    nl_ppm_max: float,
    config: AlignmentConfig,
) -> str:
    if not has_ms2_pattern:
        return "not_observed"
    source_neutral_loss_status = _source_neutral_loss_status(
        source_neutral_loss_error_ppm,
        nl_ppm_warn=nl_ppm_warn,
        nl_ppm_max=nl_ppm_max,
    )
    if source_neutral_loss_status == "NL_FAIL":
        return "conflict"
    product_conflict = (
        product_delta_ppm is not None
        and abs(product_delta_ppm) > config.product_mz_tolerance_ppm
    )
    loss_conflict = (
        observed_loss_delta_ppm is not None
        and abs(observed_loss_delta_ppm) > nl_ppm_max
    )
    if product_conflict or loss_conflict:
        return "conflict"
    if source_neutral_loss_status == "WARN":
        return "partial_support"
    if product_delta_ppm is None or observed_loss_delta_ppm is None:
        return "partial_support"
    return "supportive"


def _reason(
    *,
    has_ms2_pattern: bool,
    pattern_status: str,
    product_delta_ppm: float | None,
    observed_loss_delta_ppm: float | None,
    source_neutral_loss_status: str,
) -> str:
    if not has_ms2_pattern:
        return "source_candidate_has_no_matched_ms2_pattern_tags"
    if pattern_status == "conflict":
        if source_neutral_loss_status == "NL_FAIL":
            return "source_candidate_neutral_loss_outside_targeted_max_ppm"
        return "source_candidate_ms2_pattern_conflicts_with_family_context"
    if source_neutral_loss_status == "WARN":
        return "source_candidate_neutral_loss_warn_band_matches_family_context"
    if product_delta_ppm is None or observed_loss_delta_ppm is None:
        return "source_candidate_ms2_pattern_present_without_complete_family_context"
    return "source_candidate_ms2_pattern_matches_family_context"


def _source_neutral_loss_status(
    neutral_loss_error_ppm: float | None,
    *,
    nl_ppm_warn: float,
    nl_ppm_max: float,
) -> str:
    if neutral_loss_error_ppm is None or not math.isfinite(neutral_loss_error_ppm):
        return "UNKNOWN"
    abs_error = abs(neutral_loss_error_ppm)
    if abs_error > nl_ppm_max:
        return "NL_FAIL"
    if abs_error > nl_ppm_warn:
        return "WARN"
    return "OK"


def _pattern_similarity_score(pattern_status: str) -> str:
    if pattern_status == "supportive":
        return "1"
    if pattern_status == "partial_support":
        return "0.5"
    if pattern_status == "conflict":
        return "0"
    return ""


def _raw_boundary_row_for_missing_source(
    *,
    base: Mapping[str, str],
    sample_stem: str,
    cell: Mapping[str, str],
    family_context: Mapping[str, str],
    raw_scan_source_factory: RawScanSourceFactory | None,
    raw_file: Path | None,
    raw_config: RawCandidateMS2PatternConfig,
) -> dict[str, str] | None:
    if raw_scan_source_factory is None:
        return None
    precursor_mz = _float_or_none(family_context.get("family_center_mz"))
    neutral_loss_da = _float_or_none(
        family_context.get("family_observed_neutral_loss_da")
    )
    apex_rt = _float_or_none(cell.get("apex_rt"))
    peak_start = _float_or_none(cell.get("peak_start_rt"))
    peak_end = _float_or_none(cell.get("peak_end_rt"))
    if (
        precursor_mz is None
        or neutral_loss_da is None
        or apex_rt is None
        or peak_start is None
        or peak_end is None
        or peak_start >= peak_end
    ):
        return {**base, "reason": "raw_boundary_context_unavailable"}

    candidate = _BoundaryCandidate(
        peak=_BoundaryPeak(peak_start=peak_start, peak_end=peak_end),
        selection_apex_rt=apex_rt,
    )
    try:
        with raw_scan_source_factory(sample_stem) as raw:
            evidence = collect_candidate_ms2_evidence(
                raw,
                candidate=candidate,
                precursor_mz=precursor_mz,
                neutral_loss_da=neutral_loss_da,
                nl_ppm_warn=raw_config.nl_ppm_warn,
                nl_ppm_max=raw_config.nl_ppm_max,
                ms2_precursor_tol_da=raw_config.ms2_precursor_tol_da,
                nl_min_intensity_ratio=raw_config.nl_min_intensity_ratio,
            )
    except Exception:
        return {**base, "reason": "raw_boundary_reader_error"}

    pattern_status = _raw_pattern_status(evidence)
    evidence_level = (
        "sample_boundary_aligned"
        if pattern_status in {"supportive", "partial_support", "conflict"}
        else "sample_boundary_no_observed_pattern"
    )
    return {
        **base,
        "candidate_ms2_pattern_status": pattern_status,
        "candidate_ms2_evidence_level": evidence_level,
        "candidate_ms2_similarity_score": _raw_similarity_score(evidence),
        "matched_product_count": str(evidence.trace.product_point_count),
        "matched_neutral_loss_count": str(evidence.strict_nl_scan_count),
        "apex_ms2_delta_sec": _format_optional_float(
            None
            if evidence.best_scan_rt is None
            else abs(evidence.best_scan_rt - apex_rt) * 60.0
        ),
        "ms2_alignment_source": _RAW_BOUNDARY_PATTERN_SOURCE,
        "raw_ms2_trigger_scan_count": str(evidence.trigger_scan_count),
        "raw_ms2_strict_nl_scan_count": str(evidence.strict_nl_scan_count),
        "raw_ms2_best_loss_ppm": _format_optional_float(evidence.best_loss_ppm),
        "raw_ms2_best_scan_rt": _format_optional_float(evidence.best_scan_rt),
        "raw_ms2_best_product_base_ratio": _format_optional_float(
            evidence.best_product_base_ratio
        ),
        "raw_ms2_diagnostic_product_absence_reason": (
            evidence.diagnostic_product_absence_reason
        ),
        "raw_ms2_nearest_product_loss_ppm": _format_optional_float(
            evidence.nearest_product_loss_ppm
        ),
        "raw_ms2_nearest_product_base_ratio": _format_optional_float(
            evidence.nearest_product_base_ratio
        ),
        "raw_ms2_nearest_product_mz": _format_optional_float(
            evidence.nearest_product_mz
        ),
        "raw_ms2_trace_product_point_count": str(evidence.trace.product_point_count),
        "raw_ms2_trace_product_apex_rt": _format_optional_float(
            evidence.trace.product_apex_rt
        ),
        "raw_ms2_trace_product_apex_delta_sec": _format_optional_float(
            None
            if evidence.trace.product_apex_delta_min is None
            else evidence.trace.product_apex_delta_min * 60.0
        ),
        "raw_ms2_trace_strength": evidence.trace.strength,
        "raw_file_path": "" if raw_file is None else str(raw_file),
        "raw_reader_runtime": "pythonnet",
        "nl_ppm_warn": _format_float(raw_config.nl_ppm_warn),
        "nl_ppm_max": _format_float(raw_config.nl_ppm_max),
        "ms2_precursor_tol_da": _format_float(raw_config.ms2_precursor_tol_da),
        "nl_min_intensity_ratio": _format_float(raw_config.nl_min_intensity_ratio),
        "reason": _raw_reason(evidence, pattern_status),
    }


def _raw_pattern_status(evidence: CandidateMS2Evidence) -> str:
    if evidence.nl_status == "OK":
        return "supportive"
    if evidence.nl_status == "WARN":
        return "partial_support"
    if _has_decisive_nonmatching_base_peak(evidence):
        return "conflict"
    return "not_observed"


def _raw_reason(evidence: CandidateMS2Evidence, pattern_status: str) -> str:
    if pattern_status == "supportive":
        return "raw_boundary_ms2_pattern_matches_family_context"
    if pattern_status == "partial_support":
        return "raw_boundary_ms2_pattern_warn_band_matches_family_context"
    if pattern_status == "conflict":
        if evidence.diagnostic_product_absence_reason:
            return (
                "raw_boundary_ms2_trigger_without_expected_neutral_loss_product:"
                f"{evidence.diagnostic_product_absence_reason}"
            )
        return "raw_boundary_ms2_trigger_without_expected_neutral_loss_product"
    if evidence.ms2_present:
        return "raw_boundary_ms2_trigger_without_decisive_pattern"
    return "raw_boundary_ms2_not_observed"


def _raw_similarity_score(evidence: CandidateMS2Evidence) -> str:
    if evidence.nl_status == "OK":
        return "1"
    if evidence.nl_status == "WARN":
        return "0.5"
    if evidence.ms2_present:
        return "0"
    return ""


def _has_decisive_nonmatching_base_peak(evidence: CandidateMS2Evidence) -> bool:
    return (
        evidence.ms2_present
        and evidence.nl_status == "NL_FAIL"
        and evidence.diagnostic_product_absence_reason
        == "product_outside_diagnostic_window"
        and evidence.nearest_product_base_ratio is not None
        and math.isclose(evidence.nearest_product_base_ratio, 1.0, abs_tol=1e-6)
    )


def _candidate_by_id(batch: DiscoveryBatchInput) -> dict[str, DiscoveryCandidate]:
    candidates: dict[str, DiscoveryCandidate] = {}
    for sample_stem in batch.sample_order:
        for candidate in read_discovery_candidates_csv(
            batch.candidate_csvs[sample_stem]
        ):
            candidates[candidate.candidate_id] = candidate
    return candidates


def _raw_scan_source_factory(
    batch: DiscoveryBatchInput,
    dll_dir: Path,
) -> RawScanSourceFactory:
    def open_sample(sample_stem: str) -> AbstractContextManager[MS2ScanSourceContext]:
        from xic_extractor.raw_reader import open_raw

        raw_path = batch.raw_files.get(sample_stem)
        if raw_path is None:
            raise FileNotFoundError(f"{sample_stem}: raw_file is unavailable")
        return open_raw(raw_path, dll_dir)

    return open_sample


def _cell_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    return {
        (row["feature_family_id"], row["sample_stem"]): row
        for row in rows
    }


def _family_context_by_id(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    return {row["feature_family_id"]: row for row in rows}


def _ppm_delta(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None or reference == 0.0:
        return None
    return (value - reference) / reference * 1_000_000.0


def _apex_ms2_delta_sec(
    cell: Mapping[str, str],
    candidate: DiscoveryCandidate,
) -> str:
    cell_apex = _float_or_none(cell.get("apex_rt"))
    if cell_apex is None:
        return ""
    return _format_float(abs(cell_apex - candidate.best_seed_rt) * 60.0)


def _float_or_none(value: object) -> float | None:
    try:
        parsed = float(str(value or "").strip().strip("'"))
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return ""
    return _format_float(value)


def _format_float(value: float) -> str:
    return f"{value:.6g}"
