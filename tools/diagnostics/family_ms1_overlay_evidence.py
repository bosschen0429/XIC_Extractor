"""MS1 shape and coverage evidence for legacy family-id peak-group overlays."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

import numpy as np

from tools.diagnostics.family_ms1_overlay_models import (
    ABSOLUTE_TRACE_APEX_CLUSTER_FRACTION_MIN,
    APEX_ALIGN_GRID_SIZE,
    APEX_ALIGN_HALF_WINDOW_MIN,
    DDA_TRIGGER_HEIGHT_RATIO_MIN,
    DDA_TRIGGER_SHAPE_SUPPORT_FRACTION_MIN,
    FAMILY_SHAPE_SUPPORT_FRACTION_MIN,
    GLOBAL_APEX_CONFLICT_DELTA_MIN,
    GLOBAL_APEX_REVIEW_FRACTION_MIN,
    LOCAL_APEX_SUPPORT_DELTA_MIN,
    LOW_LOCAL_TO_GLOBAL_RATIO,
    MS1_ASSESSABLE_COVERAGE_MIN,
    SHAPE_SUPPORT_MIN,
    TraceOverlayRow,
)
from xic_extractor.peak_detection.evidence_facts import (
    TraceWindowEvidenceFacts,
    gaussian15_trace_window_evidence,
)
from xic_extractor.peak_detection.ms1_shape_identity import (
    DEFAULT_OWN_MAX_SMOOTH_POINTS,
    OwnMaxTrace,
    gaussian_smooth_values,
    own_max_normalized_trace,
)

FamilyMs1EvidenceScalar = int | float | str | bool | None
_FAMILY_MS1_EVIDENCE_CONTEXT_MINT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class FamilyMs1EvidenceContext:
    """Owner-minted, immutable binding for one overlay output transaction."""

    rows: tuple[TraceOverlayRow, ...]
    normalized_by_row: Mapping[TraceOverlayRow, OwnMaxTrace]
    window_facts_by_row: Mapping[TraceOverlayRow, TraceWindowEvidenceFacts]
    shape_similarity: Mapping[str, float | None]
    absolute_shape_similarity: Mapping[str, float | None]
    evidence_summary: Mapping[str, FamilyMs1EvidenceScalar]
    _binding_seal: tuple[int, ...] = field(repr=False, compare=False)
    _mint_token: object = field(repr=False, compare=False)

    def require_exact_rows(self, rows: Sequence[TraceOverlayRow]) -> None:
        if self._mint_token is not _FAMILY_MS1_EVIDENCE_CONTEXT_MINT_TOKEN:
            raise ValueError("exact owner-minted Gaussian15 context is required")
        if self._binding_seal != _family_ms1_context_binding_seal(
            self.rows,
            self.normalized_by_row,
            self.window_facts_by_row,
            self.shape_similarity,
            self.absolute_shape_similarity,
            self.evidence_summary,
        ):
            raise ValueError("exact owner-minted Gaussian15 context is required")
        expected_rows = tuple(rows)
        if len(expected_rows) != len(self.rows) or any(
            expected is not observed
            for expected, observed in zip(expected_rows, self.rows, strict=True)
        ):
            raise ValueError("exact owner-minted Gaussian15 context is required")


def build_family_ms1_evidence_summary(
    rows: Sequence[TraceOverlayRow],
) -> dict[str, FamilyMs1EvidenceScalar]:
    return dict(_build_family_ms1_evidence_context(rows).evidence_summary)


def _build_family_ms1_evidence_summary(
    rows: Sequence[TraceOverlayRow],
    *,
    window_facts_by_row: Mapping[TraceOverlayRow, TraceWindowEvidenceFacts],
    shape_similarity: Mapping[str, float | None],
    absolute_shape_similarity: Mapping[str, float | None],
) -> dict[str, int | float | str | bool | None]:
    detected = [row for row in rows if row.status == "detected"]
    rescued = [row for row in rows if row.status == "backfilled"]
    status_rows = [row for row in rows if row.status in {"detected", "backfilled"}]
    evaluable = [
        row for row in status_rows if shape_similarity.get(row.sample_stem) is not None
    ]
    shape_supported = [
        row
        for row in evaluable
        if (shape_similarity.get(row.sample_stem) or 0.0) >= SHAPE_SUPPORT_MIN
    ]
    absolute_shape_evaluable = [
        row
        for row in status_rows
        if absolute_shape_similarity.get(row.sample_stem) is not None
    ]
    absolute_shape_supported = [
        row
        for row in absolute_shape_evaluable
        if (absolute_shape_similarity.get(row.sample_stem) or 0.0) >= SHAPE_SUPPORT_MIN
    ]
    (
        absolute_trace_apex_assessable,
        absolute_trace_apex_clustered,
        absolute_trace_apex_median_rt,
        absolute_trace_apex_delta_abs_median,
    ) = _absolute_trace_apex_cluster(
        status_rows,
        window_facts_by_row=window_facts_by_row,
    )
    global_apex_assessable = [
        row
        for row in status_rows
        if _global_trace_apex_delta(
            row,
            window_facts=window_facts_by_row[row],
        )
        is not None
    ]
    selected_apex_in_trace_window = [
        row
        for row in status_rows
        if window_facts_by_row[row].query_apex_in_trace_window
    ]
    global_apex_interference = [
        row
        for row in global_apex_assessable
        if _abs_or_none(
            _global_trace_apex_delta(
                row,
                window_facts=window_facts_by_row[row],
            )
        )
        is not None
        and (
            _abs_or_none(
                _global_trace_apex_delta(
                    row,
                    window_facts=window_facts_by_row[row],
                )
            )
            or 0.0
        )
        > GLOBAL_APEX_CONFLICT_DELTA_MIN
    ]
    local_apex_assessable = [
        row
        for row in status_rows
        if window_facts_by_row[row].local_apex_delta_min is not None
    ]
    local_apex_supported = [
        row
        for row in local_apex_assessable
        if _abs_or_none(window_facts_by_row[row].local_apex_delta_min) is not None
        and (_abs_or_none(window_facts_by_row[row].local_apex_delta_min) or 0.0)
        <= LOCAL_APEX_SUPPORT_DELTA_MIN
    ]
    low_local_to_global = [
        row
        for row in evaluable
        if window_facts_by_row[row].local_to_global_max_ratio is not None
        and (window_facts_by_row[row].local_to_global_max_ratio or 0.0)
        < LOW_LOCAL_TO_GLOBAL_RATIO
    ]
    shape_fraction = _safe_fraction(len(shape_supported), len(evaluable))
    global_apex_assessable_fraction = _safe_fraction(
        len(global_apex_assessable),
        len(status_rows),
    )
    selected_apex_in_trace_window_fraction = _safe_fraction(
        len(selected_apex_in_trace_window),
        len(status_rows),
    )
    global_interference_fraction = _safe_fraction(
        len(global_apex_interference),
        len(global_apex_assessable),
    )
    local_support_fraction = _safe_fraction(
        len(local_apex_supported),
        len(local_apex_assessable),
    )
    local_global_low_fraction = _safe_fraction(len(low_local_to_global), len(evaluable))
    absolute_shape_fraction = _safe_fraction(
        len(absolute_shape_supported),
        len(absolute_shape_evaluable),
    )
    absolute_trace_apex_cluster_fraction = _safe_fraction(
        len(absolute_trace_apex_clustered),
        len(absolute_trace_apex_assessable),
    )
    detected_height_median = _median_value(row.cell_height for row in detected)
    rescued_height_median = _median_value(row.cell_height for row in rescued)
    detected_local_max_median = _median_value(
        window_facts_by_row[row].local_max_intensity for row in detected
    )
    rescued_local_max_median = _median_value(
        window_facts_by_row[row].local_max_intensity for row in rescued
    )
    height_ratio = _positive_ratio(detected_height_median, rescued_height_median)
    local_max_ratio = _positive_ratio(
        detected_local_max_median,
        rescued_local_max_median,
    )
    dda_trigger_limited_ms2_support = (
        len(detected) >= 2
        and len(rescued) >= 2
        and shape_fraction is not None
        and shape_fraction >= DDA_TRIGGER_SHAPE_SUPPORT_FRACTION_MIN
        and (
            (height_ratio is not None and height_ratio >= DDA_TRIGGER_HEIGHT_RATIO_MIN)
            or (
                local_max_ratio is not None
                and local_max_ratio >= DDA_TRIGGER_HEIGHT_RATIO_MIN
            )
        )
    )

    enough_ms1_coverage = (
        global_apex_assessable_fraction is not None
        and global_apex_assessable_fraction >= MS1_ASSESSABLE_COVERAGE_MIN
        and selected_apex_in_trace_window_fraction is not None
        and selected_apex_in_trace_window_fraction >= MS1_ASSESSABLE_COVERAGE_MIN
    )
    has_low_selected_peak_dominance = (
        local_global_low_fraction is not None and local_global_low_fraction >= 0.50
    )
    apex_aligned_shape_support = (
        enough_ms1_coverage
        and shape_fraction is not None
        and shape_fraction >= FAMILY_SHAPE_SUPPORT_FRACTION_MIN
        and local_support_fraction is not None
        and local_support_fraction >= FAMILY_SHAPE_SUPPORT_FRACTION_MIN
        and not has_low_selected_peak_dominance
    )
    absolute_own_max_shape_support = (
        enough_ms1_coverage
        and absolute_shape_fraction is not None
        and absolute_shape_fraction >= FAMILY_SHAPE_SUPPORT_FRACTION_MIN
        and absolute_trace_apex_cluster_fraction is not None
        and absolute_trace_apex_cluster_fraction
        >= ABSOLUTE_TRACE_APEX_CLUSTER_FRACTION_MIN
        and not has_low_selected_peak_dominance
    )
    scaled_shape_support_overrides_global_interference = (
        apex_aligned_shape_support or absolute_own_max_shape_support
    )

    if len(detected) < 2:
        family_verdict = "insufficient_nl_seed_support"
    elif scaled_shape_support_overrides_global_interference:
        family_verdict = "ms1_shape_supports_family_backfill"
    elif (
        global_interference_fraction is not None
        and global_interference_fraction >= GLOBAL_APEX_REVIEW_FRACTION_MIN
    ):
        family_verdict = "review_required_neighboring_ms1_interference"
    elif not enough_ms1_coverage:
        family_verdict = "review_required_low_ms1_assessable_coverage"
    elif has_low_selected_peak_dominance:
        family_verdict = "review_required_low_selected_peak_dominance"
    else:
        family_verdict = "review_required_uncertain_ms1_shape"

    return {
        "family_verdict": family_verdict,
        "trace_count": len(rows),
        "detected_count": len(detected),
        "backfilled_count": len(rescued),
        "detected_backfilled_count": len(status_rows),
        "evaluable_trace_count": len(evaluable),
        "global_apex_assessable_trace_count": len(global_apex_assessable),
        "global_apex_assessable_fraction": global_apex_assessable_fraction,
        "selected_apex_in_trace_window_count": len(selected_apex_in_trace_window),
        "selected_apex_in_trace_window_fraction": (
            selected_apex_in_trace_window_fraction
        ),
        "local_apex_assessable_trace_count": len(local_apex_assessable),
        "shape_supported_count": len(shape_supported),
        "shape_supported_fraction": shape_fraction,
        "absolute_own_max_evaluable_trace_count": len(absolute_shape_evaluable),
        "absolute_own_max_shape_supported_count": len(absolute_shape_supported),
        "absolute_own_max_shape_supported_fraction": absolute_shape_fraction,
        "absolute_trace_apex_assessable_count": len(absolute_trace_apex_assessable),
        "absolute_trace_apex_cluster_count": len(absolute_trace_apex_clustered),
        "absolute_trace_apex_cluster_fraction": absolute_trace_apex_cluster_fraction,
        "absolute_trace_apex_median_rt": absolute_trace_apex_median_rt,
        "absolute_trace_apex_delta_abs_median_min": (
            absolute_trace_apex_delta_abs_median
        ),
        "global_apex_interference_count": len(global_apex_interference),
        "global_apex_interference_fraction": global_interference_fraction,
        "local_apex_supported_count": len(local_apex_supported),
        "local_apex_supported_fraction": local_support_fraction,
        "low_selected_peak_dominance_count": len(low_local_to_global),
        "low_selected_peak_dominance_fraction": local_global_low_fraction,
        "dda_trigger_limited_ms2_support": dda_trigger_limited_ms2_support,
        "detected_height_median": detected_height_median,
        "rescued_height_median": rescued_height_median,
        "detected_to_rescued_height_median_ratio": height_ratio,
        "detected_local_window_max_median": detected_local_max_median,
        "rescued_local_window_max_median": rescued_local_max_median,
        "detected_to_rescued_local_window_max_median_ratio": local_max_ratio,
        "detected_shape_similarity_median": _median_value(
            shape_similarity.get(row.sample_stem) for row in detected
        ),
        "rescued_shape_similarity_median": _median_value(
            shape_similarity.get(row.sample_stem) for row in rescued
        ),
        "global_trace_apex_delta_abs_median_min": _median_value(
            _abs_or_none(
                _global_trace_apex_delta(
                    row,
                    window_facts=window_facts_by_row[row],
                )
            )
            for row in evaluable
        ),
        "local_window_apex_delta_abs_median_min": _median_value(
            _abs_or_none(window_facts_by_row[row].local_apex_delta_min)
            for row in evaluable
        ),
        "local_window_to_global_max_ratio_median": _median_value(
            window_facts_by_row[row].local_to_global_max_ratio for row in evaluable
        ),
    }


def _build_family_ms1_evidence_maps(
    rows: Sequence[TraceOverlayRow],
) -> tuple[
    dict[TraceOverlayRow, OwnMaxTrace],
    dict[TraceOverlayRow, TraceWindowEvidenceFacts],
    dict[str, float | None],
    dict[str, float | None],
]:
    normalized_by_row: dict[TraceOverlayRow, OwnMaxTrace] = {}
    window_facts_by_row: dict[TraceOverlayRow, TraceWindowEvidenceFacts] = {}
    for row in rows:
        normalized_trace, window_facts = gaussian15_trace_window_evidence(
            row.rt,
            row.intensity,
            query_apex_rt_min=row.cell_apex_rt,
            half_window_min=APEX_ALIGN_HALF_WINDOW_MIN,
        )
        normalized_by_row[row] = normalized_trace
        window_facts_by_row[row] = window_facts
    return (
        normalized_by_row,
        window_facts_by_row,
        _apex_aligned_shape_similarity(
            rows,
            normalized_by_row=normalized_by_row,
        ),
        _absolute_own_max_shape_similarity(
            rows,
            normalized_by_row=normalized_by_row,
        ),
    )


def _build_family_ms1_evidence_context(
    rows: Sequence[TraceOverlayRow],
) -> FamilyMs1EvidenceContext:
    (
        normalized_by_row,
        window_facts_by_row,
        shape_similarity,
        absolute_shape_similarity,
    ) = _build_family_ms1_evidence_maps(rows)
    evidence_summary = _build_family_ms1_evidence_summary(
        rows,
        window_facts_by_row=window_facts_by_row,
        shape_similarity=shape_similarity,
        absolute_shape_similarity=absolute_shape_similarity,
    )
    return _bind_family_ms1_evidence_context(
        rows,
        normalized_by_row=normalized_by_row,
        window_facts_by_row=window_facts_by_row,
        shape_similarity=shape_similarity,
        absolute_shape_similarity=absolute_shape_similarity,
        evidence_summary=evidence_summary,
    )


def _bind_family_ms1_evidence_context(
    rows: Sequence[TraceOverlayRow],
    *,
    normalized_by_row: Mapping[TraceOverlayRow, OwnMaxTrace],
    window_facts_by_row: Mapping[TraceOverlayRow, TraceWindowEvidenceFacts],
    shape_similarity: Mapping[str, float | None],
    absolute_shape_similarity: Mapping[str, float | None],
    evidence_summary: Mapping[str, FamilyMs1EvidenceScalar],
) -> FamilyMs1EvidenceContext:
    bound_rows = tuple(rows)
    bound_normalized = MappingProxyType(dict(normalized_by_row))
    bound_window_facts = MappingProxyType(dict(window_facts_by_row))
    bound_shape = MappingProxyType(dict(shape_similarity))
    bound_absolute_shape = MappingProxyType(dict(absolute_shape_similarity))
    bound_summary = MappingProxyType(dict(evidence_summary))
    expected_rows = set(bound_rows)
    if (
        set(bound_normalized) != expected_rows
        or set(bound_window_facts) != expected_rows
        or any(
            not isinstance(bound_normalized[row], OwnMaxTrace)
            or not isinstance(bound_window_facts[row], TraceWindowEvidenceFacts)
            for row in bound_rows
        )
    ):
        raise ValueError("complete Gaussian15 trace context is required")
    return FamilyMs1EvidenceContext(
        rows=bound_rows,
        normalized_by_row=bound_normalized,
        window_facts_by_row=bound_window_facts,
        shape_similarity=bound_shape,
        absolute_shape_similarity=bound_absolute_shape,
        evidence_summary=bound_summary,
        _binding_seal=_family_ms1_context_binding_seal(
            bound_rows,
            bound_normalized,
            bound_window_facts,
            bound_shape,
            bound_absolute_shape,
            bound_summary,
        ),
        _mint_token=_FAMILY_MS1_EVIDENCE_CONTEXT_MINT_TOKEN,
    )


def _family_ms1_context_binding_seal(
    rows: tuple[TraceOverlayRow, ...],
    normalized_by_row: Mapping[TraceOverlayRow, OwnMaxTrace],
    window_facts_by_row: Mapping[TraceOverlayRow, TraceWindowEvidenceFacts],
    shape_similarity: Mapping[str, float | None],
    absolute_shape_similarity: Mapping[str, float | None],
    evidence_summary: Mapping[str, FamilyMs1EvidenceScalar],
) -> tuple[int, ...]:
    return (
        id(rows),
        id(normalized_by_row),
        id(window_facts_by_row),
        id(shape_similarity),
        id(absolute_shape_similarity),
        id(evidence_summary),
    )


def _apex_aligned_shape_similarity(
    rows: Sequence[TraceOverlayRow],
    *,
    normalized_by_row: dict[TraceOverlayRow, OwnMaxTrace] | None = None,
) -> dict[str, float | None]:
    grid = np.linspace(
        -APEX_ALIGN_HALF_WINDOW_MIN,
        APEX_ALIGN_HALF_WINDOW_MIN,
        APEX_ALIGN_GRID_SIZE,
    )
    traces: dict[str, np.ndarray] = {}
    for row in rows:
        rt, normalized = _apex_aligned_normalized_trace(
            row,
            normalized_trace=(
                normalized_by_row.get(row) if normalized_by_row is not None else None
            ),
        )
        if rt.size < 2:
            continue
        traces[row.sample_stem] = np.interp(
            grid,
            rt,
            normalized,
            left=np.nan,
            right=np.nan,
        )
    if not traces:
        return {}
    reference_traces = [
        traces[row.sample_stem]
        for row in rows
        if row.sample_stem in traces and row.group == "detected_seed"
    ]
    if not reference_traces:
        return {sample: None for sample in traces}
    stack = np.vstack(tuple(reference_traces))
    finite_columns = np.isfinite(stack).any(axis=0)
    if not np.any(finite_columns):
        return {sample: None for sample in traces}
    detected_anchor_trace = np.nanmedian(stack[:, finite_columns], axis=0)
    return {
        sample: _pearson_similarity(values[finite_columns], detected_anchor_trace)
        for sample, values in traces.items()
    }


def _absolute_own_max_shape_similarity(
    rows: Sequence[TraceOverlayRow],
    *,
    normalized_by_row: dict[TraceOverlayRow, OwnMaxTrace] | None = None,
) -> dict[str, float | None]:
    traces: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for row in rows:
        rt, normalized = _absolute_own_max_normalized_trace(
            row,
            normalized_trace=(
                normalized_by_row.get(row) if normalized_by_row is not None else None
            ),
        )
        if rt.size < 2:
            continue
        traces[row.sample_stem] = (rt, normalized)
    if not traces:
        return {}
    rt_min = max(float(rt[0]) for rt, _normalized in traces.values())
    rt_max = min(float(rt[-1]) for rt, _normalized in traces.values())
    if not math.isfinite(rt_min) or not math.isfinite(rt_max) or rt_max <= rt_min:
        return {sample: None for sample in traces}
    grid = np.linspace(rt_min, rt_max, APEX_ALIGN_GRID_SIZE)
    interpolated = {
        sample: np.interp(grid, rt, normalized, left=np.nan, right=np.nan)
        for sample, (rt, normalized) in traces.items()
    }
    stack = np.vstack(tuple(interpolated.values()))
    finite_columns = np.isfinite(stack).all(axis=0)
    if not np.any(finite_columns):
        return {sample: None for sample in interpolated}
    median_trace = np.nanmedian(stack[:, finite_columns], axis=0)
    return {
        sample: _pearson_similarity(values[finite_columns], median_trace)
        for sample, values in interpolated.items()
    }


def _absolute_own_max_normalized_trace(
    row: TraceOverlayRow,
    *,
    smooth_points: int = DEFAULT_OWN_MAX_SMOOTH_POINTS,
    normalized_trace: OwnMaxTrace | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    trace = normalized_trace or _own_max_trace(row, smooth_points=smooth_points)
    if not trace.is_usable:
        return np.array([], dtype=float), np.array([], dtype=float)
    return np.asarray(trace.rt, dtype=float), np.asarray(trace.intensity, dtype=float)


def _apex_aligned_normalized_trace(
    row: TraceOverlayRow,
    *,
    smooth_points: int = DEFAULT_OWN_MAX_SMOOTH_POINTS,
    normalized_trace: OwnMaxTrace | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    trace = normalized_trace or _own_max_trace(row, smooth_points=smooth_points)
    if not trace.is_usable:
        return np.array([], dtype=float), np.array([], dtype=float)
    trace_rt = np.asarray(trace.rt, dtype=float)
    trace_intensity = np.asarray(trace.intensity, dtype=float)
    trace_apex_rt = float(trace_rt[int(np.argmax(trace_intensity))])
    apex_rt = row.cell_apex_rt if row.cell_apex_rt is not None else trace_apex_rt
    if apex_rt is None or not math.isfinite(apex_rt):
        return np.array([], dtype=float), np.array([], dtype=float)
    rt = trace_rt - apex_rt
    mask = (
        np.isfinite(rt)
        & np.isfinite(trace_intensity)
        & (rt >= -APEX_ALIGN_HALF_WINDOW_MIN)
        & (rt <= APEX_ALIGN_HALF_WINDOW_MIN)
    )
    if not np.any(mask):
        return np.array([], dtype=float), np.array([], dtype=float)
    local_intensity = trace_intensity[mask]
    local_max = float(np.max(local_intensity)) if local_intensity.size else 0.0
    if local_max <= 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    return rt[mask], local_intensity / local_max


def _absolute_trace_apex_cluster(
    rows: Sequence[TraceOverlayRow],
    *,
    window_facts_by_row: Mapping[TraceOverlayRow, TraceWindowEvidenceFacts]
    | None = None,
) -> tuple[list[TraceOverlayRow], list[TraceOverlayRow], float | None, float | None]:
    assessable = [
        row
        for row in rows
        if (
            window_facts_by_row[row].trace_apex_rt_min
            if window_facts_by_row is not None
            else _trace_window_facts(row).trace_apex_rt_min
        )
        is not None
    ]
    trace_apex_by_row = {
        row: (
            window_facts_by_row[row].trace_apex_rt_min
            if window_facts_by_row is not None
            else _trace_window_facts(row).trace_apex_rt_min
        )
        for row in assessable
    }
    median_rt = _median_value(trace_apex_by_row.values())
    if median_rt is None:
        return assessable, [], None, None
    clustered = [
        row
        for row in assessable
        if trace_apex_by_row[row] is not None
        and abs((trace_apex_by_row[row] or 0.0) - median_rt)
        <= GLOBAL_APEX_CONFLICT_DELTA_MIN
    ]
    delta_median = _median_value(
        abs((trace_apex_by_row[row] or 0.0) - median_rt)
        for row in assessable
        if trace_apex_by_row[row] is not None
    )
    return assessable, clustered, median_rt, delta_median


def _own_max_trace(
    row: TraceOverlayRow,
    *,
    smooth_points: int = DEFAULT_OWN_MAX_SMOOTH_POINTS,
) -> OwnMaxTrace:
    return own_max_normalized_trace(
        row.rt,
        row.intensity,
        smooth_points=smooth_points,
    )


def _gaussian_smooth_values(values: np.ndarray, *, points: int) -> np.ndarray:
    """Compatibility adapter for diagnostic rendering; evidence uses OwnMaxTrace."""

    return np.asarray(
        gaussian_smooth_values(
            tuple(float(value) for value in values),
            points=points,
        ),
        dtype=float,
    )


def _pearson_similarity(
    values: np.ndarray,
    reference: np.ndarray,
) -> float | None:
    mask = np.isfinite(values) & np.isfinite(reference)
    if int(np.sum(mask)) < 5:
        return None
    x = values[mask]
    y = reference[mask]
    x_std = float(np.std(x))
    y_std = float(np.std(y))
    if x_std <= 1e-12 or y_std <= 1e-12:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _trace_window_facts(
    row: TraceOverlayRow,
) -> TraceWindowEvidenceFacts:
    _trace, facts = gaussian15_trace_window_evidence(
        row.rt,
        row.intensity,
        query_apex_rt_min=row.cell_apex_rt,
        half_window_min=APEX_ALIGN_HALF_WINDOW_MIN,
    )
    return facts


def _global_trace_apex_delta(
    row: TraceOverlayRow,
    *,
    window_facts: TraceWindowEvidenceFacts | None = None,
) -> float | None:
    facts = window_facts or _trace_window_facts(row)
    return facts.global_trace_apex_delta_min


def _local_window_peak(
    row: TraceOverlayRow,
    *,
    window_facts: TraceWindowEvidenceFacts | None = None,
) -> tuple[float | None, float | None]:
    facts = window_facts or _trace_window_facts(row)
    return facts.local_apex_delta_min, facts.local_max_intensity


def _local_to_global_max_ratio(
    row: TraceOverlayRow,
    *,
    window_facts: TraceWindowEvidenceFacts | None = None,
) -> float | None:
    facts = window_facts or _trace_window_facts(row)
    return facts.local_to_global_max_ratio


def _selected_apex_in_trace_window(row: TraceOverlayRow) -> bool:
    if row.cell_apex_rt is None or not math.isfinite(row.cell_apex_rt):
        return False
    return _trace_window_facts(row).query_apex_in_trace_window


def _abs_or_none(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return abs(value)


def _safe_fraction(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _positive_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        return None
    if denominator <= 0:
        return None
    return numerator / denominator


def _finite_values(values: Iterable[float | None]) -> list[float]:
    return [value for value in values if value is not None and math.isfinite(value)]


def _median_value(values: Iterable[float | None]) -> float | None:
    finite = _finite_values(values)
    if not finite:
        return None
    return float(np.median(np.asarray(finite, dtype=float)))
