from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.tabular_io import (
    read_tsv_required,
    text_value,
    write_tsv,
)

from .machine_evidence_support import (
    MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS,
    MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS,
)
from .ms1_peak_quality_vector import build_peak_quality_vector
from .overlay_trace_data import load_overlay_trace_data

MS1_PATTERN_COHERENCE_OPTIONAL_COLUMNS = (
    "shape_metric_source",
    "anchor_peak_rt",
    "anchor_peak_delta_sec",
    "anchor_peak_own_max_shape_similarity",
    "family_ms1_overlay_verdict",
    "cell_height",
    "local_window_max_intensity",
    "trace_max_intensity",
    "cell_to_local_window_max_ratio",
    "local_window_to_global_max_ratio",
    "local_window_apex_delta_sec",
    "global_trace_apex_delta_sec",
    "family_ms1_overlay_trace_data_json",
    "peak_quality_vector_status",
    "peak_quality_vector_basis",
    "peak_quality_trace_point_count",
    "peak_quality_boundary_point_count",
    "peak_quality_signal_to_noise_proxy",
    "peak_quality_fwhm_sec",
    "peak_quality_sharpness_score",
    "peak_quality_zigzag_score",
    "peak_quality_tailing_ratio",
    "peak_quality_boundary_margin_ratio",
    "peak_quality_feature_count",
    "peak_quality_vector_reason",
)
MS1_PATTERN_COHERENCE_COLUMNS = (
    *MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS,
    *MS1_PATTERN_COHERENCE_OPTIONAL_COLUMNS,
)

_ALIGNMENT_CELL_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "apex_rt",
    "peak_start_rt",
    "peak_end_rt",
    "rt_delta_sec",
    "trace_quality",
    "scan_support_score",
)
_PRESENT_STATUSES = frozenset(
    {
        "detected",
        "backfilled",
        "selected",
        "present",
        "duplicate_assigned",
    }
)
_SUPPORTIVE_STATUSES = frozenset({"detected", "backfilled", "selected", "present"})
_MIN_REFERENCE_PEAKS = 3
_MIN_BOUNDARY_WIDTH_SIMILARITY = 0.35
_SUPPORTIVE_BOUNDARY_WIDTH_SIMILARITY = 0.55
_HIGH_LOCAL_INTERFERENCE_SCORE = 0.80
_OVERLAY_SHAPE_SUPPORT_MIN = 0.50
_OVERLAY_LOCAL_DOMINANCE_MIN = 0.50
_OVERLAY_LOCAL_APEX_SUPPORT_SEC = 3.0
_OVERLAY_FAMILY_CONSENSUS_APEX_CONFLICT_SEC = 30.0
_OVERLAY_COMPETING_PEAK_TO_CONSENSUS_SEC = 15.0
_OVERLAY_COMPETING_PEAK_TO_SELECTED_MIN_RATIO = 0.25
_OVERLAY_ANCHOR_PEAK_CLUSTER_MAX_DELTA_SEC = 21.0
_OVERLAY_ANCHOR_PEAK_LOCAL_HALF_WINDOW_MIN = 0.35
_OVERLAY_ANCHOR_PEAK_GRID_SIZE = 81


@dataclass(frozen=True)
class _PeakCell:
    feature_family_id: str
    sample_stem: str
    status: str
    apex_rt: float | None
    peak_start_rt: float | None
    peak_end_rt: float | None
    rt_delta_sec: float | None
    trace_quality: str
    scan_support_score: float | None
    gap_fill_state: str
    gap_fill_reason: str

    @property
    def is_present(self) -> bool:
        return self.status in _PRESENT_STATUSES

    @property
    def has_boundary(self) -> bool:
        return (
            self.apex_rt is not None
            and self.peak_start_rt is not None
            and self.peak_end_rt is not None
            and self.peak_end_rt > self.peak_start_rt
        )

    @property
    def width_sec(self) -> float | None:
        if not self.has_boundary:
            return None
        if self.peak_start_rt is None or self.peak_end_rt is None:
            return None
        return max(0.0, (self.peak_end_rt - self.peak_start_rt) * 60.0)

    @property
    def is_duplicate_loser(self) -> bool:
        return (
            self.gap_fill_reason == "not_requested_duplicate_loser"
            or (
                self.gap_fill_state == "not_filled"
                and "duplicate_loser" in self.gap_fill_reason
            )
        )


@dataclass(frozen=True)
class _OverlayMetric:
    family_id: str
    sample_stem: str
    family_verdict: str
    shape_similarity: float | None
    cell_height: float | None
    local_window_max_intensity: float | None
    trace_max_intensity: float | None
    local_window_to_global_max_ratio: float | None
    local_window_apex_delta_sec: float | None
    global_trace_apex_delta_sec: float | None
    cell_apex_rt: float | None
    cell_start_rt: float | None
    cell_end_rt: float | None
    rt_min: float | None
    rt_max: float | None
    trace_rt: tuple[float, ...]
    trace_intensity: tuple[float, ...]
    family_consensus_apex_rt: float | None
    anchor_peak_rt: float | None
    anchor_peak_delta_sec: float | None
    anchor_peak_own_max_shape_similarity: float | None
    source_json: Path

    @property
    def cell_apex_inside_trace_window(self) -> bool:
        if self.cell_apex_rt is None or self.rt_min is None or self.rt_max is None:
            return True
        return self.rt_min <= self.cell_apex_rt <= self.rt_max


@dataclass(frozen=True)
class _AnchorPeakCluster:
    anchor_peak_rt: float
    sample_stems: frozenset[str]


@dataclass(frozen=True)
class _AnchorPeakAssignment:
    anchor_peak_rt: float | None
    anchor_peak_delta_sec: float | None
    own_max_shape_similarity: float | None


def build_ms1_pattern_coherence_rows(
    *,
    alignment_cells_tsv: Path,
    oracle_keys: Iterable[tuple[str, str]],
    matrix_rt_drift_policy_tsv: Path | None = None,
    family_ms1_overlay_trace_data_jsons: Sequence[Path] | None = None,
    config: AlignmentConfig | None = None,
) -> tuple[dict[str, str], ...]:
    """Build diagnostic-only MS1 boundary-constellation evidence.

    The current artifact set has peak apex and integration boundaries, but not
    raw trace vectors. Without RAW-backed overlay inputs this producer therefore
    emits a conservative sample-boundary constellation metric and leaves shape
    correlation empty. When family MS1 overlay JSON artifacts are supplied, the
    producer enriches matching rows with RAW-backed trace shape/local-window
    metrics from that existing diagnostic surface.
    """

    config = config or AlignmentConfig()
    matrix_drift_rows = (
        read_tsv_required(
            matrix_rt_drift_policy_tsv,
            MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS,
        )
        if matrix_rt_drift_policy_tsv is not None
        else ()
    )
    return build_ms1_pattern_coherence_rows_from_cell_rows(
        cell_rows=_read_cell_rows(alignment_cells_tsv),
        oracle_keys=oracle_keys,
        matrix_rt_drift_rows=matrix_drift_rows,
        family_ms1_overlay_trace_data_jsons=family_ms1_overlay_trace_data_jsons,
        config=config,
    )


def build_ms1_pattern_coherence_rows_from_cell_rows(
    *,
    cell_rows: Iterable[Mapping[str, str]],
    oracle_keys: Iterable[tuple[str, str]],
    matrix_rt_drift_rows: Sequence[Mapping[str, str]] = (),
    family_ms1_overlay_trace_data_jsons: Sequence[Path] | None = None,
    config: AlignmentConfig | None = None,
) -> tuple[dict[str, str], ...]:
    """Build MS1 coherence rows from already-loaded alignment cell rows."""

    config = config or AlignmentConfig()
    cells = tuple(_peak_cell(row) for row in cell_rows)
    cell_by_key = {(cell.feature_family_id, cell.sample_stem): cell for cell in cells}
    cells_by_family = _group_cells(cells, key=lambda cell: cell.feature_family_id)
    cells_by_sample = _group_cells(cells, key=lambda cell: cell.sample_stem)
    matrix_drift = _matrix_drift_by_key(matrix_rt_drift_rows)
    overlay_metrics = _overlay_metrics_by_key(
        family_ms1_overlay_trace_data_jsons or ()
    )

    rows = [
        _row_for_key(
            feature_family_id=feature_family_id,
            sample_stem=sample_stem,
            cell=cell_by_key.get((feature_family_id, sample_stem)),
            family_cells=cells_by_family.get(feature_family_id, ()),
            sample_cells=cells_by_sample.get(sample_stem, ()),
            matrix_drift_row=matrix_drift.get((feature_family_id, sample_stem)),
            overlay_metric=overlay_metrics.get((feature_family_id, sample_stem)),
            config=config,
        )
        for feature_family_id, sample_stem in oracle_keys
    ]
    return tuple(
        sorted(rows, key=lambda row: (row["feature_family_id"], row["sample_stem"]))
    )


def write_ms1_pattern_coherence_rows(
    path: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(path, rows, MS1_PATTERN_COHERENCE_COLUMNS, lineterminator="\n")


def _read_cell_rows(path: Path) -> tuple[Mapping[str, str], ...]:
    return read_tsv_required(path, _ALIGNMENT_CELL_COLUMNS)


def _peak_cell(row: Mapping[str, str]) -> _PeakCell:
    return _PeakCell(
        feature_family_id=text_value(row.get("feature_family_id")),
        sample_stem=text_value(row.get("sample_stem")),
        status=text_value(row.get("status")),
        apex_rt=_optional_float(row.get("apex_rt")),
        peak_start_rt=_optional_float(row.get("peak_start_rt")),
        peak_end_rt=_optional_float(row.get("peak_end_rt")),
        rt_delta_sec=_optional_float(row.get("rt_delta_sec")),
        trace_quality=text_value(row.get("trace_quality")),
        scan_support_score=_optional_float(row.get("scan_support_score")),
        gap_fill_state=text_value(row.get("gap_fill_state")),
        gap_fill_reason=text_value(row.get("gap_fill_reason")),
    )


def _group_cells(
    cells: Sequence[_PeakCell],
    *,
    key: Callable[[_PeakCell], str],
) -> dict[str, tuple[_PeakCell, ...]]:
    grouped: dict[str, list[_PeakCell]] = {}
    for cell in cells:
        grouped.setdefault(key(cell), []).append(cell)
    return {group_key: tuple(values) for group_key, values in grouped.items()}


def _matrix_drift_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    return {(row["feature_family_id"], row["sample_stem"]): row for row in rows}


def _row_for_key(
    *,
    feature_family_id: str,
    sample_stem: str,
    cell: _PeakCell | None,
    family_cells: Sequence[_PeakCell],
    sample_cells: Sequence[_PeakCell],
    matrix_drift_row: Mapping[str, str] | None,
    overlay_metric: _OverlayMetric | None,
    config: AlignmentConfig,
) -> dict[str, str]:
    base = _base_row(feature_family_id, sample_stem)
    if cell is None:
        return {**base, "reason": "alignment_cell_missing"}
    if not cell.is_present:
        return {**base, "reason": "alignment_cell_not_present"}
    if not cell.has_boundary:
        return {**base, "reason": "alignment_cell_boundary_missing"}
    if cell.is_duplicate_loser:
        return {
            **base,
            "ms1_pattern_status": "conflict",
            "ms1_pattern_evidence_level": "not_available",
            "reason": "alignment_gap_fill_duplicate_loser",
        }

    reference_cells = tuple(
        peer
        for peer in family_cells
        if peer.status in _SUPPORTIVE_STATUSES
        and peer.has_boundary
        and not peer.is_duplicate_loser
    )
    reference_count = len(reference_cells)
    median_width = _median_width(reference_cells)
    boundary_score = _boundary_width_similarity(cell.width_sec, median_width)
    apex_coherence_sec = _apex_coherence_sec(
        cell=cell,
        reference_cells=reference_cells,
        matrix_drift_row=matrix_drift_row,
    )
    drift_status = _drift_compatible_status(
        apex_coherence_sec=apex_coherence_sec,
        matrix_drift_row=matrix_drift_row,
        config=config,
    )
    local_count, local_interference = _local_interference(cell, sample_cells)
    stability_score = _relative_width_stability_score(reference_cells)
    status, reason = _status_and_reason(
        cell=cell,
        reference_count=reference_count,
        boundary_score=boundary_score,
        apex_coherence_sec=apex_coherence_sec,
        drift_status=drift_status,
        local_interference_score=local_interference,
        config=config,
    )
    row = {
        **base,
        "ms1_pattern_status": status,
        "ms1_pattern_evidence_level": (
            "sample_boundary_constellation"
            if status in {"supportive", "partial_support", "conflict"}
            else "not_available"
        ),
        "apex_coherence_sec": _format_float(apex_coherence_sec),
        "boundary_overlap_score": _format_float(boundary_score),
        "shape_correlation_score": "",
        "relative_pattern_stability_score": _format_float(stability_score),
        "local_interference_score": _format_float(local_interference),
        "constellation_peak_count": str(local_count + 1),
        "reference_peak_count": str(reference_count),
        "drift_compatible_status": drift_status,
        "reason": reason,
    }
    if overlay_metric is not None:
        row = _with_overlay_metric(
            row,
            overlay_metric,
            cell=cell,
            matrix_drift_row=matrix_drift_row,
        )
    return row


def _base_row(feature_family_id: str, sample_stem: str) -> dict[str, str]:
    return {
        "feature_family_id": feature_family_id,
        "sample_stem": sample_stem,
        "ms1_pattern_status": "not_available",
        "ms1_pattern_evidence_level": "not_available",
        "apex_coherence_sec": "",
        "boundary_overlap_score": "",
        "shape_correlation_score": "",
        "relative_pattern_stability_score": "",
        "local_interference_score": "",
        "constellation_peak_count": "0",
        "reference_peak_count": "0",
        "drift_compatible_status": "not_available",
        "reason": "",
        "diagnostic_only": "TRUE",
        "shape_metric_source": "",
        "anchor_peak_rt": "",
        "anchor_peak_delta_sec": "",
        "anchor_peak_own_max_shape_similarity": "",
        "family_ms1_overlay_verdict": "",
        "cell_height": "",
        "local_window_max_intensity": "",
        "trace_max_intensity": "",
        "cell_to_local_window_max_ratio": "",
        "local_window_to_global_max_ratio": "",
        "local_window_apex_delta_sec": "",
        "global_trace_apex_delta_sec": "",
        "family_ms1_overlay_trace_data_json": "",
        "peak_quality_vector_status": "not_available",
        "peak_quality_vector_basis": "",
        "peak_quality_trace_point_count": "0",
        "peak_quality_boundary_point_count": "0",
        "peak_quality_signal_to_noise_proxy": "",
        "peak_quality_fwhm_sec": "",
        "peak_quality_sharpness_score": "",
        "peak_quality_zigzag_score": "",
        "peak_quality_tailing_ratio": "",
        "peak_quality_boundary_margin_ratio": "",
        "peak_quality_feature_count": "0",
        "peak_quality_vector_reason": "",
    }


def _median_width(cells: Sequence[_PeakCell]) -> float | None:
    widths = [width for cell in cells if (width := cell.width_sec) is not None]
    if not widths:
        return None
    return statistics.median(widths)


def _boundary_width_similarity(
    width_sec: float | None,
    median_width_sec: float | None,
) -> float | None:
    if width_sec is None or median_width_sec is None:
        return None
    larger = max(width_sec, median_width_sec)
    if larger <= 0:
        return None
    return max(0.0, min(width_sec, median_width_sec) / larger)


def _apex_coherence_sec(
    *,
    cell: _PeakCell,
    reference_cells: Sequence[_PeakCell],
    matrix_drift_row: Mapping[str, str] | None,
) -> float | None:
    corrected_delta = _optional_float(
        (matrix_drift_row or {}).get("drift_corrected_delta_sec")
    )
    if _matrix_drift_compatible(matrix_drift_row) and corrected_delta is not None:
        return abs(corrected_delta)
    if cell.rt_delta_sec is not None:
        return abs(cell.rt_delta_sec)
    apex_values = [
        peer.apex_rt
        for peer in reference_cells
        if peer.apex_rt is not None and peer is not cell
    ]
    if cell.apex_rt is None or not apex_values:
        return None
    return abs(cell.apex_rt - statistics.median(apex_values)) * 60.0


def _drift_compatible_status(
    *,
    apex_coherence_sec: float | None,
    matrix_drift_row: Mapping[str, str] | None,
    config: AlignmentConfig,
) -> str:
    if _matrix_drift_compatible(matrix_drift_row):
        return "compatible"
    if matrix_drift_row and matrix_drift_row.get("matrix_rt_drift_status") == (
        "drift_not_supported"
    ):
        return "conflict"
    if apex_coherence_sec is None:
        return "not_available"
    if apex_coherence_sec <= config.preferred_rt_sec:
        return "compatible"
    if apex_coherence_sec <= config.max_rt_sec:
        return "unmodeled_shift"
    return "conflict"


def _matrix_drift_compatible(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return (
        row.get("drift_compatible_status") == "compatible"
        and row.get("matrix_rt_drift_status") in {"rt_close", "drift_supported"}
    )


def _local_interference(
    cell: _PeakCell,
    sample_cells: Sequence[_PeakCell],
) -> tuple[int, float]:
    if (
        cell.peak_start_rt is None
        or cell.peak_end_rt is None
        or cell.apex_rt is None
    ):
        return 0, 0.0
    local_peers = [
        peer
        for peer in sample_cells
        if peer is not cell
        and peer.is_present
        and peer.has_boundary
        and peer.peak_start_rt is not None
        and peer.peak_end_rt is not None
        and _interval_overlap_fraction(
            cell.peak_start_rt,
            cell.peak_end_rt,
            peer.peak_start_rt,
            peer.peak_end_rt,
        )
        >= 0.5
    ]
    return len(local_peers), min(1.0, len(local_peers) / 25.0)


def _interval_overlap_fraction(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> float:
    overlap = max(0.0, min(left_end, right_end) - max(left_start, right_start))
    denominator = min(left_end - left_start, right_end - right_start)
    if denominator <= 0:
        return 0.0
    return overlap / denominator


def _relative_width_stability_score(cells: Sequence[_PeakCell]) -> float | None:
    widths = [width for cell in cells if (width := cell.width_sec) is not None]
    if len(widths) < 2:
        return None
    median_width = statistics.median(widths)
    if median_width <= 0:
        return None
    median_abs_deviation = statistics.median(
        abs(width - median_width) for width in widths
    )
    relative_deviation = median_abs_deviation / median_width
    return max(0.0, min(1.0, 1.0 - relative_deviation))


def _status_and_reason(
    *,
    cell: _PeakCell,
    reference_count: int,
    boundary_score: float | None,
    apex_coherence_sec: float | None,
    drift_status: str,
    local_interference_score: float,
    config: AlignmentConfig,
) -> tuple[str, str]:
    if drift_status == "conflict":
        return "conflict", "rt_or_matrix_drift_conflict"
    if apex_coherence_sec is not None and apex_coherence_sec > config.max_rt_sec:
        return "conflict", "apex_outside_max_rt_window"
    if boundary_score is None:
        return "inconclusive", "boundary_similarity_unavailable"
    if boundary_score < _MIN_BOUNDARY_WIDTH_SIMILARITY:
        return "inconclusive", "boundary_width_similarity_too_low"
    if reference_count < _MIN_REFERENCE_PEAKS:
        return "inconclusive", "insufficient_family_reference_peaks"
    if drift_status != "compatible":
        return "inconclusive", "rt_shift_requires_independent_drift_policy"
    if cell.status not in _SUPPORTIVE_STATUSES:
        return "partial_support", "non_primary_cell_status"
    if local_interference_score >= _HIGH_LOCAL_INTERFERENCE_SCORE:
        return "partial_support", "high_local_interference_context"
    if boundary_score >= _SUPPORTIVE_BOUNDARY_WIDTH_SIMILARITY:
        return "supportive", "alignment_cell_boundary_constellation_supported"
    return "partial_support", "boundary_constellation_partial"


def _overlay_metrics_by_key(
    paths: Sequence[Path],
) -> dict[tuple[str, str], _OverlayMetric]:
    metrics: dict[tuple[str, str], _OverlayMetric] = {}
    for path in paths:
        bundle = load_overlay_trace_data(path)
        family_id = bundle.family_id
        evidence_summary = bundle.evidence_summary
        family_verdict = text_value(evidence_summary.get("family_verdict"))
        rt_min = bundle.rt_min
        rt_max = bundle.rt_max
        parsed_traces = bundle.trace_mappings
        family_consensus_apex_rt = _family_consensus_apex_rt(parsed_traces)
        anchor_assignments = _anchor_peak_assignments(parsed_traces)
        for trace in parsed_traces:
            sample_stem = text_value(trace.get("sample_stem"))
            if not sample_stem:
                continue
            cell_apex_rt = _optional_float(trace.get("cell_apex_rt"))
            anchor_assignment = anchor_assignments.get(
                sample_stem,
                _AnchorPeakAssignment(None, None, None),
            )
            metric = _OverlayMetric(
                family_id=family_id,
                sample_stem=sample_stem,
                family_verdict=family_verdict,
                shape_similarity=_optional_float(
                    trace.get("apex_aligned_shape_similarity")
                ),
                cell_height=_optional_float(trace.get("cell_height")),
                local_window_max_intensity=_optional_float(
                    trace.get("local_window_max_intensity")
                ),
                trace_max_intensity=_optional_float(trace.get("trace_max_intensity")),
                local_window_to_global_max_ratio=_optional_float(
                    trace.get("local_window_to_global_max_ratio")
                ),
                local_window_apex_delta_sec=_minutes_to_seconds(
                    _optional_float(trace.get("local_window_apex_delta_min"))
                ),
                global_trace_apex_delta_sec=_minutes_to_seconds(
                    _optional_float(trace.get("global_trace_apex_delta_min"))
                ),
                cell_apex_rt=cell_apex_rt,
                cell_start_rt=_optional_float(trace.get("cell_start_rt")),
                cell_end_rt=_optional_float(trace.get("cell_end_rt")),
                rt_min=rt_min,
                rt_max=rt_max,
                trace_rt=_optional_float_sequence(trace.get("rt")),
                trace_intensity=_optional_float_sequence(trace.get("intensity")),
                family_consensus_apex_rt=family_consensus_apex_rt,
                anchor_peak_rt=anchor_assignment.anchor_peak_rt,
                anchor_peak_delta_sec=anchor_assignment.anchor_peak_delta_sec,
                anchor_peak_own_max_shape_similarity=(
                    anchor_assignment.own_max_shape_similarity
                ),
                source_json=path,
            )
            metrics[(family_id, sample_stem)] = metric
    return metrics


def _with_overlay_metric(
    row: Mapping[str, str],
    metric: _OverlayMetric,
    *,
    cell: _PeakCell,
    matrix_drift_row: Mapping[str, str] | None = None,
) -> dict[str, str]:
    shape_similarity = _preferred_shape_similarity(metric)
    peak_quality = build_peak_quality_vector(
        trace_rt=metric.trace_rt,
        trace_intensity=metric.trace_intensity,
        cell_start_rt=metric.cell_start_rt,
        cell_end_rt=metric.cell_end_rt,
    )
    enriched = {
        **row,
        "shape_correlation_score": _format_float(shape_similarity),
        "shape_metric_source": _shape_metric_source(metric),
        "anchor_peak_rt": _format_float(metric.anchor_peak_rt),
        "anchor_peak_delta_sec": _format_float(metric.anchor_peak_delta_sec),
        "anchor_peak_own_max_shape_similarity": (
            _format_float(metric.anchor_peak_own_max_shape_similarity)
        ),
        "family_ms1_overlay_verdict": metric.family_verdict,
        "cell_height": _format_float(metric.cell_height),
        "local_window_max_intensity": _format_float(
            metric.local_window_max_intensity
        ),
        "trace_max_intensity": _format_float(metric.trace_max_intensity),
        "cell_to_local_window_max_ratio": _format_float(
            _ratio_or_none(metric.cell_height, metric.local_window_max_intensity)
        ),
        "local_window_to_global_max_ratio": _format_float(
            metric.local_window_to_global_max_ratio
        ),
        "local_window_apex_delta_sec": _format_float(
            metric.local_window_apex_delta_sec
        ),
        "global_trace_apex_delta_sec": _format_float(
            metric.global_trace_apex_delta_sec
        ),
        "family_ms1_overlay_trace_data_json": str(metric.source_json),
        "peak_quality_vector_status": peak_quality.status,
        "peak_quality_vector_basis": peak_quality.basis,
        "peak_quality_trace_point_count": str(peak_quality.trace_point_count),
        "peak_quality_boundary_point_count": str(
            peak_quality.boundary_point_count
        ),
        "peak_quality_signal_to_noise_proxy": _format_float(
            peak_quality.signal_to_noise_proxy
        ),
        "peak_quality_fwhm_sec": _format_float(peak_quality.fwhm_sec),
        "peak_quality_sharpness_score": _format_float(
            peak_quality.sharpness_score
        ),
        "peak_quality_zigzag_score": _format_float(peak_quality.zigzag_score),
        "peak_quality_tailing_ratio": _format_float(peak_quality.tailing_ratio),
        "peak_quality_boundary_margin_ratio": _format_float(
            peak_quality.boundary_margin_ratio
        ),
        "peak_quality_feature_count": str(peak_quality.feature_count),
        "peak_quality_vector_reason": peak_quality.reason,
    }
    if not metric.cell_apex_inside_trace_window:
        enriched = {
            **enriched,
            "reason": _join_reason(
                enriched.get("reason", ""),
                "family_ms1_overlay_trace_window_does_not_cover_cell_apex",
            ),
        }
        if _requires_anchor_peak_evidence(cell):
            if enriched.get("ms1_pattern_status") == "conflict":
                return enriched
            return {
                **enriched,
                "ms1_pattern_status": "inconclusive",
                "ms1_pattern_evidence_level": "trace_constellation",
            }
        return enriched
    if _overlay_anchor_peak_mismatch(metric):
        return {
            **enriched,
            "ms1_pattern_status": "conflict",
            "ms1_pattern_evidence_level": "trace_constellation",
            "reason": "family_ms1_overlay_anchor_peak_mismatch",
        }
    if _overlay_anchor_peak_supports_shape(metric):
        if enriched.get("drift_compatible_status") == "conflict":
            return enriched
        return {
            **enriched,
            "ms1_pattern_status": "supportive",
            "ms1_pattern_evidence_level": "trace_constellation",
            "reason": "family_ms1_overlay_anchor_peak_own_max_shape_supported",
        }
    if _overlay_anchor_peak_shape_below_threshold(metric):
        if enriched.get("drift_compatible_status") == "conflict":
            return enriched
        return {
            **enriched,
            "ms1_pattern_status": "inconclusive",
            "ms1_pattern_evidence_level": "trace_constellation",
            "reason": "family_ms1_overlay_anchor_peak_shape_below_threshold",
        }
    if _overlay_lacks_complete_expected_peak(metric):
        return {
            **enriched,
            "ms1_pattern_status": "conflict",
            "ms1_pattern_evidence_level": "trace_constellation",
            "reason": "family_ms1_overlay_expected_window_lacks_complete_peak",
        }
    if _overlay_selected_peak_loses_to_family_consensus(
        metric,
        matrix_drift_row=matrix_drift_row,
    ):
        return {
            **enriched,
            "ms1_pattern_status": "conflict",
            "ms1_pattern_evidence_level": "trace_constellation",
            "reason": "family_ms1_overlay_competing_peak_matches_family_consensus",
        }
    if _requires_anchor_peak_evidence(cell) and _overlay_anchor_peak_evidence_missing(
        metric,
    ):
        if enriched.get("ms1_pattern_status") == "conflict":
            return enriched
        return {
            **enriched,
            "ms1_pattern_status": "inconclusive",
            "ms1_pattern_evidence_level": "trace_constellation",
            "reason": "family_ms1_overlay_anchor_peak_evidence_unavailable",
        }
    if _overlay_supports_shape(metric):
        if enriched.get("drift_compatible_status") == "conflict":
            return enriched
        status = (
            "supportive"
            if enriched.get("drift_compatible_status") == "compatible"
            else "partial_support"
        )
        reason = (
            "family_ms1_overlay_shape_supported"
            if status == "supportive"
            else "family_ms1_overlay_shape_supported_rt_policy_pending"
        )
        return {
            **enriched,
            "ms1_pattern_status": status,
            "ms1_pattern_evidence_level": "trace_constellation",
            "reason": reason,
        }
    if _overlay_shape_metric_inconclusive(metric):
        if enriched.get("drift_compatible_status") == "conflict":
            return enriched
        return {
            **enriched,
            "ms1_pattern_status": "partial_support",
            "ms1_pattern_evidence_level": "trace_constellation",
            "reason": "family_ms1_overlay_shape_metric_inconclusive_apex_or_height",
        }
    return enriched


def _anchor_peak_assignments(
    traces: Sequence[Mapping[str, object]],
) -> dict[str, _AnchorPeakAssignment]:
    grid = np.linspace(
        -_OVERLAY_ANCHOR_PEAK_LOCAL_HALF_WINDOW_MIN,
        _OVERLAY_ANCHOR_PEAK_LOCAL_HALF_WINDOW_MIN,
        _OVERLAY_ANCHOR_PEAK_GRID_SIZE,
    )
    clusters = _detected_anchor_peak_clusters(traces)
    normalized: dict[str, np.ndarray | None] = {}
    apex_by_sample: dict[str, float | None] = {}
    for trace in traces:
        sample_stem = text_value(trace.get("sample_stem"))
        if not sample_stem:
            continue
        apex_by_sample[sample_stem] = _optional_float(trace.get("cell_apex_rt"))
        normalized[sample_stem] = _anchor_local_own_max_trace(trace, grid=grid)
    if not clusters:
        return {
            sample: _AnchorPeakAssignment(None, None, None)
            for sample in normalized
        }
    references = _anchor_peak_reference_traces(clusters, normalized)
    assignments: dict[str, _AnchorPeakAssignment] = {}
    for sample_stem, values in normalized.items():
        apex_rt = apex_by_sample.get(sample_stem)
        cluster = _nearest_anchor_peak_cluster(clusters, apex_rt)
        if cluster is None:
            assignments[sample_stem] = _AnchorPeakAssignment(None, None, None)
            continue
        reference = references.get(cluster.anchor_peak_rt)
        similarity = None
        if values is not None and reference is not None:
            reference_trace, reference_columns = reference
            similarity = _pearson_similarity(
                values[reference_columns],
                reference_trace,
            )
        assignments[sample_stem] = _AnchorPeakAssignment(
            cluster.anchor_peak_rt,
            _rt_delta_sec(apex_rt, cluster.anchor_peak_rt),
            similarity,
        )
    return assignments


def _detected_anchor_peak_clusters(
    traces: Sequence[Mapping[str, object]],
) -> tuple[_AnchorPeakCluster, ...]:
    detected = sorted(
        (
            (text_value(trace.get("sample_stem")), apex_rt)
            for trace in traces
            if text_value(trace.get("sample_stem"))
            and text_value(trace.get("status")) == "detected"
            and (apex_rt := _optional_float(trace.get("cell_apex_rt"))) is not None
        ),
        key=lambda item: item[1],
    )
    if not detected:
        return ()
    threshold_min = _OVERLAY_ANCHOR_PEAK_CLUSTER_MAX_DELTA_SEC / 60.0
    clusters: list[list[tuple[str, float]]] = []
    current: list[tuple[str, float]] = []
    for sample_stem, apex_rt in detected:
        if not current:
            current.append((sample_stem, apex_rt))
            continue
        current_anchor = statistics.median(apex for _, apex in current)
        if abs(apex_rt - current_anchor) <= threshold_min:
            current.append((sample_stem, apex_rt))
        else:
            clusters.append(current)
            current = [(sample_stem, apex_rt)]
    if current:
        clusters.append(current)
    return tuple(
        _AnchorPeakCluster(
            anchor_peak_rt=statistics.median(apex for _, apex in cluster),
            sample_stems=frozenset(sample for sample, _ in cluster),
        )
        for cluster in clusters
    )


def _anchor_peak_reference_traces(
    clusters: Sequence[_AnchorPeakCluster],
    normalized: Mapping[str, np.ndarray | None],
) -> dict[float, tuple[np.ndarray, np.ndarray]]:
    references: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for cluster in clusters:
        reference_values = [
            values
            for sample in cluster.sample_stems
            if (values := normalized.get(sample)) is not None
        ]
        if not reference_values:
            continue
        reference_stack = np.vstack(reference_values)
        reference_columns = np.isfinite(reference_stack).all(axis=0)
        if not np.any(reference_columns):
            continue
        references[cluster.anchor_peak_rt] = (
            np.nanmedian(reference_stack[:, reference_columns], axis=0),
            reference_columns,
        )
    return references


def _nearest_anchor_peak_cluster(
    clusters: Sequence[_AnchorPeakCluster],
    apex_rt: float | None,
) -> _AnchorPeakCluster | None:
    if apex_rt is None or not clusters:
        return None
    return min(clusters, key=lambda cluster: abs(apex_rt - cluster.anchor_peak_rt))


def _anchor_local_own_max_trace(
    trace: Mapping[str, object],
    *,
    grid: np.ndarray,
) -> np.ndarray | None:
    cell_apex_rt = _optional_float(trace.get("cell_apex_rt"))
    if cell_apex_rt is None:
        return None
    rt = np.asarray(_optional_float_sequence(trace.get("rt")), dtype=float)
    intensity = np.asarray(
        _optional_float_sequence(trace.get("intensity")),
        dtype=float,
    )
    if rt.size != intensity.size or rt.size < 3:
        return None
    relative_rt = rt - cell_apex_rt
    mask = (
        np.isfinite(relative_rt)
        & np.isfinite(intensity)
        & (relative_rt >= grid[0])
        & (relative_rt <= grid[-1])
    )
    if np.count_nonzero(mask) < 3:
        return None
    relative_rt = relative_rt[mask]
    intensity = intensity[mask]
    order = np.argsort(relative_rt)
    relative_rt = relative_rt[order]
    intensity = intensity[order]
    max_intensity = float(np.max(intensity)) if intensity.size else 0.0
    if max_intensity <= 0:
        return None
    return np.interp(
        grid,
        relative_rt,
        intensity / max_intensity,
        left=np.nan,
        right=np.nan,
    )


def _preferred_shape_similarity(metric: _OverlayMetric) -> float | None:
    if metric.anchor_peak_own_max_shape_similarity is not None:
        return metric.anchor_peak_own_max_shape_similarity
    return metric.shape_similarity


def _shape_metric_source(metric: _OverlayMetric) -> str:
    if metric.anchor_peak_own_max_shape_similarity is not None:
        return "family_ms1_overlay_anchor_peak_own_max"
    if metric.shape_similarity is not None:
        return "family_ms1_overlay_raw_trace"
    return "family_ms1_overlay_raw_trace_unscored"


def _overlay_anchor_peak_mismatch(metric: _OverlayMetric) -> bool:
    if metric.anchor_peak_delta_sec is None:
        return False
    return (
        abs(metric.anchor_peak_delta_sec)
        > _OVERLAY_ANCHOR_PEAK_CLUSTER_MAX_DELTA_SEC
    )


def _overlay_anchor_peak_supports_shape(metric: _OverlayMetric) -> bool:
    if metric.anchor_peak_delta_sec is None:
        return False
    if abs(metric.anchor_peak_delta_sec) > _OVERLAY_ANCHOR_PEAK_CLUSTER_MAX_DELTA_SEC:
        return False
    score = metric.anchor_peak_own_max_shape_similarity
    return score is not None and score >= _OVERLAY_SHAPE_SUPPORT_MIN


def _overlay_anchor_peak_shape_below_threshold(metric: _OverlayMetric) -> bool:
    if metric.anchor_peak_delta_sec is None:
        return False
    if abs(metric.anchor_peak_delta_sec) > _OVERLAY_ANCHOR_PEAK_CLUSTER_MAX_DELTA_SEC:
        return False
    score = metric.anchor_peak_own_max_shape_similarity
    return score is not None and score < _OVERLAY_SHAPE_SUPPORT_MIN


def _requires_anchor_peak_evidence(cell: _PeakCell) -> bool:
    return (
        cell.status == "backfilled"
        or "backfill" in cell.gap_fill_state
        or "backfill" in cell.gap_fill_reason
    )


def _overlay_anchor_peak_evidence_missing(metric: _OverlayMetric) -> bool:
    return (
        metric.anchor_peak_rt is None
        or metric.anchor_peak_delta_sec is None
        or metric.anchor_peak_own_max_shape_similarity is None
    )


def _family_consensus_apex_rt(traces: Sequence[Mapping[str, object]]) -> float | None:
    apex_values = [
        value
        for trace in traces
        if (value := _optional_float(trace.get("cell_apex_rt"))) is not None
    ]
    if len(apex_values) < _MIN_REFERENCE_PEAKS:
        return None
    return statistics.median(apex_values)


def _overlay_selected_peak_loses_to_family_consensus(
    metric: _OverlayMetric,
    *,
    matrix_drift_row: Mapping[str, str] | None,
) -> bool:
    if _sample_istd_drift_supported(matrix_drift_row):
        return False
    if metric.family_consensus_apex_rt is None or metric.cell_apex_rt is None:
        return False
    selected_delta_sec = abs(metric.cell_apex_rt - metric.family_consensus_apex_rt) * 60
    if selected_delta_sec <= _OVERLAY_FAMILY_CONSENSUS_APEX_CONFLICT_SEC:
        return False
    if _cell_boundary_contains_rt(metric, metric.family_consensus_apex_rt):
        return False
    competing_height = _local_peak_height_near_rt(
        metric.trace_rt,
        metric.trace_intensity,
        target_rt=metric.family_consensus_apex_rt,
        max_delta_sec=_OVERLAY_COMPETING_PEAK_TO_CONSENSUS_SEC,
    )
    selected_height = metric.cell_height or metric.local_window_max_intensity
    if competing_height is None or selected_height is None or selected_height <= 0:
        return False
    return (
        competing_height / selected_height
        >= _OVERLAY_COMPETING_PEAK_TO_SELECTED_MIN_RATIO
    )


def _sample_istd_drift_supported(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return (
        row.get("matrix_rt_drift_status") == "drift_supported"
        and row.get("drift_evidence_level") == "sample_istd_aligned"
        and row.get("drift_compatible_status") == "compatible"
    )


def _cell_boundary_contains_rt(metric: _OverlayMetric, rt: float) -> bool:
    if metric.cell_start_rt is None or metric.cell_end_rt is None:
        return False
    return metric.cell_start_rt <= rt <= metric.cell_end_rt


def _local_peak_height_near_rt(
    trace_rt: Sequence[float],
    trace_intensity: Sequence[float],
    *,
    target_rt: float,
    max_delta_sec: float,
) -> float | None:
    if len(trace_rt) != len(trace_intensity) or len(trace_rt) < 3:
        return None
    max_delta_min = max_delta_sec / 60.0
    candidates: list[float] = []
    for index in range(1, len(trace_rt) - 1):
        rt = trace_rt[index]
        if abs(rt - target_rt) > max_delta_min:
            continue
        intensity = trace_intensity[index]
        if intensity >= trace_intensity[index - 1] and intensity >= trace_intensity[
            index + 1
        ]:
            candidates.append(intensity)
    if not candidates:
        return None
    return max(candidates)


def _overlay_supports_shape(metric: _OverlayMetric) -> bool:
    if metric.shape_similarity is None:
        return False
    if metric.shape_similarity < _OVERLAY_SHAPE_SUPPORT_MIN:
        return False
    if (
        metric.local_window_to_global_max_ratio is not None
        and metric.local_window_to_global_max_ratio < _OVERLAY_LOCAL_DOMINANCE_MIN
    ):
        return False
    if (
        metric.local_window_apex_delta_sec is not None
        and abs(metric.local_window_apex_delta_sec) > _OVERLAY_LOCAL_APEX_SUPPORT_SEC
    ):
        return False
    return metric.family_verdict == "ms1_shape_supports_family_backfill"


def _overlay_lacks_complete_expected_peak(metric: _OverlayMetric) -> bool:
    if not metric.cell_apex_inside_trace_window:
        return False
    if metric.local_window_to_global_max_ratio is not None:
        return metric.local_window_to_global_max_ratio < _OVERLAY_LOCAL_DOMINANCE_MIN
    return False


def _overlay_shape_metric_inconclusive(metric: _OverlayMetric) -> bool:
    if metric.family_verdict != "ms1_shape_supports_family_backfill":
        return False
    if (
        metric.local_window_to_global_max_ratio is not None
        and metric.local_window_to_global_max_ratio < _OVERLAY_LOCAL_DOMINANCE_MIN
    ):
        return False
    return (
        metric.shape_similarity is not None
        and metric.shape_similarity < _OVERLAY_SHAPE_SUPPORT_MIN
    ) or (
        metric.local_window_apex_delta_sec is not None
        and abs(metric.local_window_apex_delta_sec) > _OVERLAY_LOCAL_APEX_SUPPORT_SEC
    )


def _join_reason(left: str, right: str) -> str:
    if not left:
        return right
    if right in left.split(";"):
        return left
    return f"{left};{right}"


def _minutes_to_seconds(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 60.0


def _ratio_or_none(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _optional_float(value: object) -> float | None:
    text = text_value(value).strip("'")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _optional_float_sequence(value: object) -> tuple[float, ...]:
    if not isinstance(value, list):
        return ()
    parsed = []
    for item in value:
        parsed_value = _optional_float(item)
        if parsed_value is not None:
            parsed.append(parsed_value)
    return tuple(parsed)


def _rt_delta_sec(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return (left - right) * 60.0


def _pearson_similarity(left: np.ndarray, right: np.ndarray) -> float | None:
    mask = np.isfinite(left) & np.isfinite(right)
    if np.count_nonzero(mask) < 3:
        return None
    left = left[mask]
    right = right[mask]
    if float(np.std(left)) == 0.0 or float(np.std(right)) == 0.0:
        return None
    value = float(np.corrcoef(left, right)[0, 1])
    return value if math.isfinite(value) else None


def _format_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6g}"
