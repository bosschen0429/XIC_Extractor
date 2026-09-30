from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.sample_groups import classify_sample_group
from xic_extractor.tabular_io import text_value, write_tsv

from .overlay_trace_data import load_overlay_trace_data

QC_MS1_PATTERN_REFERENCE_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "qc_reference_status",
    "qc_reference_evidence_level",
    "qc_reference_policy",
    "local_qc_reference_status",
    "qc_consensus_status",
    "qc_consensus_support_count",
    "qc_consensus_conflict_count",
    "qc_consensus_inconclusive_count",
    "qc_consensus_usable_qc_count",
    "qc_reference_conflict_status",
    "target_injection_order",
    "nearest_qc_sample_stem",
    "nearest_qc_injection_order",
    "nearest_qc_injection_order_delta",
    "target_apex_rt",
    "nearest_qc_apex_rt",
    "target_minus_qc_apex_delta_sec",
    "target_qc_apex_abs_delta_sec",
    "target_qc_shape_similarity",
    "target_local_window_to_global_max_ratio",
    "nearest_qc_local_window_to_global_max_ratio",
    "target_cell_to_local_window_max_ratio",
    "nearest_qc_cell_to_local_window_max_ratio",
    "family_ms1_overlay_trace_data_json",
    "reason",
    "diagnostic_only",
)

_SHAPE_SUPPORT_MIN = 0.70
_SHAPE_PARTIAL_MIN = 0.50
_SHAPE_CONFLICT_MAX = 0.30
_LOCAL_SIGNAL_MIN = 0.50
_APEX_CLOSE_SEC = 12.0
_APEX_CONFLICT_SEC = 30.0
_ALIGNMENT_HALF_WINDOW_MIN = 0.30
_ALIGNMENT_POINTS = 61
_MIN_CORRELATION_POINTS = 8
_QC_SUPPORT = frozenset({"supportive", "partial_support"})
_QC_CONFLICT = frozenset({"conflict"})


@dataclass(frozen=True)
class _TraceMetric:
    family_id: str
    sample_stem: str
    group: str
    cell_apex_rt: float | None
    trace_apex_rt: float | None
    cell_height: float | None
    local_window_max_intensity: float | None
    trace_max_intensity: float | None
    local_window_to_global_max_ratio: float | None
    family_center_rt: float | None
    rt: tuple[float, ...]
    intensity: tuple[float, ...]
    source_json: Path

    @property
    def is_qc(self) -> bool:
        return self.group == "QC" or classify_sample_group(self.sample_stem) == "QC"

    @property
    def apex_rt(self) -> float | None:
        if self.trace_apex_rt is not None:
            return self.trace_apex_rt
        return self.cell_apex_rt

    @property
    def cell_to_local_window_max_ratio(self) -> float | None:
        return _ratio_or_none(self.cell_height, self.local_window_max_intensity)


@dataclass(frozen=True)
class _QcComparison:
    metric: _TraceMetric
    order: int
    status: str
    reason: str
    order_delta: int
    apex_delta_sec: float | None
    apex_abs_delta_sec: float | None
    shape_similarity: float | None


@dataclass(frozen=True)
class _QcConsensus:
    status: str
    support_count: int
    conflict_count: int
    inconclusive_count: int
    usable_count: int


def build_qc_ms1_pattern_reference_rows(
    *,
    family_ms1_overlay_trace_data_jsons: Sequence[Path],
    oracle_keys: Iterable[tuple[str, str]],
    injection_order: Mapping[str, int],
    max_injection_order_delta: int | None = None,
) -> tuple[dict[str, str], ...]:
    """Compare each reviewed sample to local QC and QC consensus traces."""

    metrics_by_key = _trace_metrics_by_key(family_ms1_overlay_trace_data_jsons)
    metrics_by_family = _metrics_by_family(metrics_by_key.values())
    rows = [
        _row_for_key(
            feature_family_id=feature_family_id,
            sample_stem=sample_stem,
            target=metrics_by_key.get((feature_family_id, sample_stem)),
            family_metrics=metrics_by_family.get(feature_family_id, ()),
            injection_order=injection_order,
            max_injection_order_delta=max_injection_order_delta,
        )
        for feature_family_id, sample_stem in oracle_keys
    ]
    return tuple(
        sorted(rows, key=lambda row: (row["feature_family_id"], row["sample_stem"]))
    )


def write_qc_ms1_pattern_reference_rows(
    path: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(path, rows, QC_MS1_PATTERN_REFERENCE_COLUMNS, lineterminator="\n")


def _trace_metrics_by_key(
    paths: Sequence[Path],
) -> dict[tuple[str, str], _TraceMetric]:
    metrics: dict[tuple[str, str], _TraceMetric] = {}
    for path in paths:
        bundle = load_overlay_trace_data(path)
        family_id = bundle.family_id
        family_center_rt = bundle.family_center_rt
        for trace in bundle.traces:
            trace_values = trace.values
            sample_stem = trace.sample_stem
            if not sample_stem:
                continue
            metric = _TraceMetric(
                family_id=family_id,
                sample_stem=sample_stem,
                group=trace.group,
                cell_apex_rt=trace.optional_float("cell_apex_rt"),
                trace_apex_rt=trace.optional_float("trace_apex_rt"),
                cell_height=trace.optional_float("cell_height"),
                local_window_max_intensity=_optional_float(
                    trace_values.get("local_window_max_intensity")
                ),
                trace_max_intensity=trace.optional_float("trace_max_intensity"),
                local_window_to_global_max_ratio=_optional_float(
                    trace_values.get("local_window_to_global_max_ratio")
                ),
                family_center_rt=family_center_rt,
                rt=trace.optional_float_sequence("rt"),
                intensity=trace.optional_float_sequence("intensity"),
                source_json=path,
            )
            metrics[(family_id, sample_stem)] = metric
    return metrics


def _metrics_by_family(
    metrics: Iterable[_TraceMetric],
) -> dict[str, tuple[_TraceMetric, ...]]:
    grouped: dict[str, list[_TraceMetric]] = {}
    for metric in metrics:
        grouped.setdefault(metric.family_id, []).append(metric)
    return {family_id: tuple(values) for family_id, values in grouped.items()}


def _row_for_key(
    *,
    feature_family_id: str,
    sample_stem: str,
    target: _TraceMetric | None,
    family_metrics: Sequence[_TraceMetric],
    injection_order: Mapping[str, int],
    max_injection_order_delta: int | None,
) -> dict[str, str]:
    base = _base_row(feature_family_id, sample_stem)
    if target is None:
        return {**base, "reason": "family_ms1_overlay_target_trace_missing"}
    target_order = injection_order.get(sample_stem)
    if target_order is None:
        return {
            **base,
            "family_ms1_overlay_trace_data_json": str(target.source_json),
            "reason": "target_injection_order_missing",
        }
    comparisons = _qc_comparisons(
        target=target,
        target_order=target_order,
        family_metrics=family_metrics,
        injection_order=injection_order,
        max_injection_order_delta=max_injection_order_delta,
    )
    consensus = _qc_consensus(comparisons)
    nearest_qc = _nearest_qc_comparison(comparisons)
    if nearest_qc is None:
        return {
            **base,
            "target_injection_order": str(target_order),
            "qc_consensus_status": consensus.status,
            "qc_consensus_support_count": str(consensus.support_count),
            "qc_consensus_conflict_count": str(consensus.conflict_count),
            "qc_consensus_inconclusive_count": str(consensus.inconclusive_count),
            "qc_consensus_usable_qc_count": str(consensus.usable_count),
            "family_ms1_overlay_trace_data_json": str(target.source_json),
            "reason": "nearest_qc_reference_missing",
        }
    (
        final_status,
        final_reason,
        reference_policy,
        conflict_status,
    ) = _combine_local_and_consensus(
        local_status=nearest_qc.status,
        local_reason=nearest_qc.reason,
        consensus=consensus,
    )
    return {
        **base,
        "qc_reference_status": final_status,
        "qc_reference_evidence_level": _qc_reference_evidence_level(
            reference_policy,
        ),
        "qc_reference_policy": reference_policy,
        "local_qc_reference_status": nearest_qc.status,
        "qc_consensus_status": consensus.status,
        "qc_consensus_support_count": str(consensus.support_count),
        "qc_consensus_conflict_count": str(consensus.conflict_count),
        "qc_consensus_inconclusive_count": str(consensus.inconclusive_count),
        "qc_consensus_usable_qc_count": str(consensus.usable_count),
        "qc_reference_conflict_status": conflict_status,
        "target_injection_order": str(target_order),
        "nearest_qc_sample_stem": nearest_qc.metric.sample_stem,
        "nearest_qc_injection_order": str(nearest_qc.order),
        "nearest_qc_injection_order_delta": str(nearest_qc.order_delta),
        "target_apex_rt": _format_float(target.apex_rt),
        "nearest_qc_apex_rt": _format_float(nearest_qc.metric.apex_rt),
        "target_minus_qc_apex_delta_sec": _format_float(
            nearest_qc.apex_delta_sec
        ),
        "target_qc_apex_abs_delta_sec": _format_float(
            nearest_qc.apex_abs_delta_sec
        ),
        "target_qc_shape_similarity": _format_float(nearest_qc.shape_similarity),
        "target_local_window_to_global_max_ratio": _format_float(
            target.local_window_to_global_max_ratio
        ),
        "nearest_qc_local_window_to_global_max_ratio": _format_float(
            nearest_qc.metric.local_window_to_global_max_ratio
        ),
        "target_cell_to_local_window_max_ratio": _format_float(
            target.cell_to_local_window_max_ratio
        ),
        "nearest_qc_cell_to_local_window_max_ratio": _format_float(
            nearest_qc.metric.cell_to_local_window_max_ratio
        ),
        "family_ms1_overlay_trace_data_json": str(target.source_json),
        "reason": final_reason,
    }


def _base_row(feature_family_id: str, sample_stem: str) -> dict[str, str]:
    return {
        "feature_family_id": feature_family_id,
        "sample_stem": sample_stem,
        "qc_reference_status": "not_available",
        "qc_reference_evidence_level": "not_available",
        "qc_reference_policy": "not_available",
        "local_qc_reference_status": "not_available",
        "qc_consensus_status": "not_available",
        "qc_consensus_support_count": "0",
        "qc_consensus_conflict_count": "0",
        "qc_consensus_inconclusive_count": "0",
        "qc_consensus_usable_qc_count": "0",
        "qc_reference_conflict_status": "none",
        "target_injection_order": "",
        "nearest_qc_sample_stem": "",
        "nearest_qc_injection_order": "",
        "nearest_qc_injection_order_delta": "",
        "target_apex_rt": "",
        "nearest_qc_apex_rt": "",
        "target_minus_qc_apex_delta_sec": "",
        "target_qc_apex_abs_delta_sec": "",
        "target_qc_shape_similarity": "",
        "target_local_window_to_global_max_ratio": "",
        "nearest_qc_local_window_to_global_max_ratio": "",
        "target_cell_to_local_window_max_ratio": "",
        "nearest_qc_cell_to_local_window_max_ratio": "",
        "family_ms1_overlay_trace_data_json": "",
        "reason": "",
        "diagnostic_only": "TRUE",
    }


def _qc_comparisons(
    *,
    target: _TraceMetric,
    target_order: int,
    family_metrics: Sequence[_TraceMetric],
    injection_order: Mapping[str, int],
    max_injection_order_delta: int | None,
) -> tuple[_QcComparison, ...]:
    comparisons: list[_QcComparison] = []
    for metric in family_metrics:
        if metric.sample_stem == target.sample_stem or not metric.is_qc:
            continue
        order = injection_order.get(metric.sample_stem)
        if order is None:
            continue
        order_delta = abs(order - target_order)
        apex_delta_sec = _apex_delta_sec(target, metric)
        apex_abs_delta_sec = (
            abs(apex_delta_sec) if apex_delta_sec is not None else None
        )
        shape_similarity = _apex_aligned_shape_similarity(target, metric)
        status, reason = _status_and_reason(
            target=target,
            qc_metric=metric,
            order_delta=order_delta,
            max_injection_order_delta=max_injection_order_delta,
            apex_abs_delta_sec=apex_abs_delta_sec,
            shape_similarity=shape_similarity,
        )
        comparisons.append(
            _QcComparison(
                metric=metric,
                order=order,
                status=status,
                reason=reason,
                order_delta=order_delta,
                apex_delta_sec=apex_delta_sec,
                apex_abs_delta_sec=apex_abs_delta_sec,
                shape_similarity=shape_similarity,
            )
        )
    return tuple(sorted(comparisons, key=lambda item: item.order_delta))


def _nearest_qc_comparison(
    comparisons: Sequence[_QcComparison],
) -> _QcComparison | None:
    if not comparisons:
        return None
    return min(
        comparisons,
        key=lambda item: (
            0
            if _has_local_signal(item.metric) and item.metric.apex_rt is not None
            else 1,
            _family_centered_qc_rank(item.metric),
            item.order_delta,
            item.metric.sample_stem,
        ),
    )


def _qc_consensus(comparisons: Sequence[_QcComparison]) -> _QcConsensus:
    support_count = sum(1 for item in comparisons if item.status in _QC_SUPPORT)
    conflict_count = sum(1 for item in comparisons if item.status in _QC_CONFLICT)
    inconclusive_count = sum(
        1
        for item in comparisons
        if item.status not in _QC_SUPPORT and item.status not in _QC_CONFLICT
    )
    usable_count = sum(
        1
        for item in comparisons
        if _has_local_signal(item.metric) and item.metric.apex_rt is not None
    )
    if not comparisons:
        status = "not_available"
    elif support_count and conflict_count:
        status = "mixed_conflict"
    elif support_count >= 2:
        status = "supportive"
    elif support_count == 1:
        status = "partial_support"
    elif conflict_count >= 2:
        status = "conflict"
    else:
        status = "inconclusive"
    return _QcConsensus(
        status=status,
        support_count=support_count,
        conflict_count=conflict_count,
        inconclusive_count=inconclusive_count,
        usable_count=usable_count,
    )


def _combine_local_and_consensus(
    *,
    local_status: str,
    local_reason: str,
    consensus: _QcConsensus,
) -> tuple[str, str, str, str]:
    if consensus.status in _QC_SUPPORT:
        if local_status in _QC_CONFLICT:
            return (
                "inconclusive",
                "local_qc_conflicts_with_qc_consensus",
                "qc_consensus_with_local_conflict",
                "local_vs_consensus_conflict",
            )
        if local_status in _QC_SUPPORT:
            status = (
                "supportive"
                if consensus.status == "supportive"
                else "partial_support"
            )
            return (
                status,
                "qc_consensus_and_local_qc_support",
                "qc_consensus_with_local_support",
                "none",
            )
        return (
            "partial_support",
            "qc_consensus_support_local_qc_uninformative",
            "qc_consensus_fallback_valid_qc",
            "local_qc_uninformative",
        )
    if consensus.status == "conflict":
        if local_status in _QC_CONFLICT:
            return (
                "conflict",
                "qc_consensus_and_local_qc_conflict",
                "qc_consensus_with_local_conflict",
                "none",
            )
        if local_status in _QC_SUPPORT:
            return (
                "inconclusive",
                "local_qc_conflicts_with_qc_consensus",
                "qc_consensus_with_local_conflict",
                "local_vs_consensus_conflict",
            )
        return (
            "inconclusive",
            "qc_consensus_conflict_without_local_confirmation",
            "qc_consensus_conflict_review",
            "local_qc_uninformative",
        )
    if consensus.status == "mixed_conflict":
        return (
            "inconclusive",
            "qc_consensus_mixed_support_and_conflict",
            "qc_consensus_mixed_review",
            "consensus_mixed_conflict",
        )
    if local_status in _QC_SUPPORT or local_status in _QC_CONFLICT:
        return (
            "inconclusive",
            "local_qc_reference_without_consensus",
            "nearest_valid_qc_local_condition_only",
            "consensus_missing",
        )
    return (
        "inconclusive",
        local_reason,
        "local_qc_uninformative",
        "none",
    )


def _qc_reference_evidence_level(reference_policy: str) -> str:
    if reference_policy == "qc_consensus_with_local_support":
        return "qc_consensus_with_local_qc_overlay"
    if reference_policy == "qc_consensus_fallback_valid_qc":
        return "qc_consensus_qc_overlay"
    if reference_policy == "qc_consensus_with_local_conflict":
        return "qc_consensus_with_local_qc_overlay"
    if reference_policy == "qc_consensus_conflict_review":
        return "qc_consensus_review_only"
    if reference_policy == "qc_consensus_mixed_review":
        return "qc_consensus_mixed"
    if reference_policy == "nearest_valid_qc_local_condition_only":
        return "nearest_valid_qc_local_condition_only"
    return "local_qc_uninformative"


def _status_and_reason(
    *,
    target: _TraceMetric,
    qc_metric: _TraceMetric,
    order_delta: int,
    max_injection_order_delta: int | None,
    apex_abs_delta_sec: float | None,
    shape_similarity: float | None,
) -> tuple[str, str]:
    if (
        max_injection_order_delta is not None
        and order_delta > max_injection_order_delta
    ):
        return "inconclusive", "nearest_qc_reference_outside_injection_window"
    if apex_abs_delta_sec is None:
        return "inconclusive", "target_or_qc_apex_missing"
    target_signal = _has_local_signal(target)
    qc_signal = _has_local_signal(qc_metric)
    if not qc_signal:
        return "inconclusive", "nearest_qc_lacks_complete_reference_peak"
    if (
        apex_abs_delta_sec >= _APEX_CONFLICT_SEC
        and target_signal
        and qc_signal
    ):
        return "conflict", "nearest_qc_peak_separated_from_target_cell"
    if shape_similarity is None:
        return "inconclusive", "target_qc_shape_similarity_unavailable"
    if (
        apex_abs_delta_sec <= _APEX_CLOSE_SEC
        and shape_similarity >= _SHAPE_SUPPORT_MIN
        and target_signal
    ):
        return "supportive", "nearest_qc_ms1_pattern_supported"
    if (
        apex_abs_delta_sec <= _APEX_CONFLICT_SEC
        and shape_similarity >= _SHAPE_PARTIAL_MIN
        and target_signal
    ):
        return "partial_support", "nearest_qc_ms1_pattern_partial"
    if shape_similarity <= _SHAPE_CONFLICT_MAX and target_signal:
        return "conflict", "nearest_qc_ms1_pattern_mismatch"
    if not target_signal:
        return "inconclusive", "target_expected_window_low_signal"
    return "inconclusive", "nearest_qc_ms1_pattern_not_decisive"


def _has_local_signal(metric: _TraceMetric) -> bool:
    ratio = metric.local_window_to_global_max_ratio
    if ratio is not None:
        return (
            ratio >= _LOCAL_SIGNAL_MIN
            and metric.local_window_max_intensity is not None
            and metric.local_window_max_intensity > 0
            and metric.apex_rt is not None
        )
    if metric.trace_apex_rt is not None and (
        metric.trace_max_intensity is not None and metric.trace_max_intensity > 0
    ):
        return True
    if _has_trace_vector(metric):
        return False
    cell_ratio = metric.cell_to_local_window_max_ratio
    if cell_ratio is not None:
        return cell_ratio >= _LOCAL_SIGNAL_MIN
    return metric.cell_height is not None and metric.cell_height > 0


def _family_centered_qc_rank(metric: _TraceMetric) -> int:
    if metric.family_center_rt is None or metric.apex_rt is None:
        return 0
    return 0 if _qc_family_center_delta_sec(metric) <= _APEX_CONFLICT_SEC else 1


def _has_family_centered_qc_signal(metric: _TraceMetric) -> bool:
    return (
        metric.family_center_rt is not None
        and _has_local_signal(metric)
        and metric.apex_rt is not None
        and _family_centered_qc_rank(metric) == 0
    )


def _qc_family_center_delta_sec(metric: _TraceMetric) -> float:
    if metric.family_center_rt is None or metric.apex_rt is None:
        return 0.0
    return abs(metric.apex_rt - metric.family_center_rt) * 60.0


def _has_trace_vector(metric: _TraceMetric) -> bool:
    return len(metric.rt) == len(metric.intensity) and len(metric.rt) > 0


def _apex_delta_sec(left: _TraceMetric, right: _TraceMetric) -> float | None:
    if left.apex_rt is None or right.apex_rt is None:
        return None
    return (left.apex_rt - right.apex_rt) * 60.0


def _apex_aligned_shape_similarity(
    left: _TraceMetric,
    right: _TraceMetric,
) -> float | None:
    if left.apex_rt is None or right.apex_rt is None:
        return None
    if len(left.rt) != len(left.intensity) or len(right.rt) != len(right.intensity):
        return None
    if len(left.rt) < 2 or len(right.rt) < 2:
        return None
    grid = _regular_grid(
        -_ALIGNMENT_HALF_WINDOW_MIN,
        _ALIGNMENT_HALF_WINDOW_MIN,
        _ALIGNMENT_POINTS,
    )
    pairs: list[tuple[float, float]] = []
    left_x = tuple(rt - left.apex_rt for rt in left.rt)
    right_x = tuple(rt - right.apex_rt for rt in right.rt)
    for x_value in grid:
        left_y = _interpolate(left_x, left.intensity, x_value)
        right_y = _interpolate(right_x, right.intensity, x_value)
        if left_y is None or right_y is None:
            continue
        pairs.append((left_y, right_y))
    if len(pairs) < _MIN_CORRELATION_POINTS:
        return None
    left_values = _normalized_values(tuple(value for value, _ in pairs))
    right_values = _normalized_values(tuple(value for _, value in pairs))
    if left_values is None or right_values is None:
        return None
    return _pearson(left_values, right_values)


def _regular_grid(start: float, stop: float, points: int) -> tuple[float, ...]:
    if points <= 1:
        return (start,)
    step = (stop - start) / (points - 1)
    return tuple(start + step * index for index in range(points))


def _interpolate(
    x_values: Sequence[float],
    y_values: Sequence[float],
    x_target: float,
) -> float | None:
    if not x_values or x_target < x_values[0] or x_target > x_values[-1]:
        return None
    for index in range(1, len(x_values)):
        x_left = x_values[index - 1]
        x_right = x_values[index]
        if x_left <= x_target <= x_right:
            y_left = y_values[index - 1]
            y_right = y_values[index]
            if x_right == x_left:
                return y_left
            fraction = (x_target - x_left) / (x_right - x_left)
            return y_left + fraction * (y_right - y_left)
    return None


def _normalized_values(values: Sequence[float]) -> tuple[float, ...] | None:
    if not values:
        return None
    min_value = min(values)
    max_value = max(values)
    span = max_value - min_value
    if span <= 0:
        return None
    return tuple((value - min_value) / span for value in values)


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    left_ss = sum((value - left_mean) ** 2 for value in left)
    right_ss = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_ss * right_ss)
    if denominator <= 0:
        return None
    return numerator / denominator


def _ratio_or_none(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _optional_float(value: object) -> float | None:
    text = text_value(value).strip("'")
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _optional_float_sequence(value: object) -> tuple[float, ...]:
    if not isinstance(value, list):
        return ()
    parsed = []
    for item in value:
        parsed_value = _optional_float(item)
        if parsed_value is not None:
            parsed.append(parsed_value)
    return tuple(parsed)


def _format_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6g}"
