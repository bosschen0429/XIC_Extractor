from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from xic_extractor.alignment.production_candidate_gate import (
    TIER2_TRACE_EVIDENCE_REQUIRED_COLUMNS,
    GateSourceContext,
    tier2_candidate_subset_signature,
)

TraceValue = float | int | str
TraceLoader = Callable[
    [str, float, float, float, float],
    tuple[Iterable[TraceValue], Iterable[TraceValue]],
]

CRITERIA_VERSION = "tier2_trace_identity_diagnostic"
PRODUCER_VERSION = "raw_trace_reread_tier2"


@dataclass(frozen=True)
class Tier2TraceProducerConfig:
    ppm_tolerance: float = 20.0
    rt_padding_min: float = 0.05
    scans_target: int = 5
    min_scan_support_score: float = 0.50
    max_apex_delta_sec: float = 30.0
    max_boundary_width_sec: float = 180.0
    max_rescued_apex_span_sec: float = 21.0
    min_rescued_boundary_overlap: float = 0.50
    max_rescued_cells_per_family: int = 8


@dataclass(frozen=True)
class _TraceMetrics:
    cell_apex_rt: float
    tier2_apex_rt: float
    apex_delta_sec: float
    scan_support_score: float
    scan_availability_score: float
    trace_scan_count: int
    boundary_start_rt: float
    boundary_end_rt: float
    boundary_width_sec: float
    trace_apex_intensity: float
    trace_baseline_noise: float
    trace_signal_to_noise_proxy: float
    trace_apex_prominence_score: float


def build_tier2_trace_evidence_rows(
    *,
    candidate_rows: Sequence[Mapping[str, object]],
    cells_by_family: Mapping[str, Sequence[Mapping[str, object]]],
    source_context: GateSourceContext,
    raw_manifest_sha256: str,
    source_expected_sample_count: int,
    trace_loader: TraceLoader,
    config: Tier2TraceProducerConfig,
    producer_command: str,
    generated_at_utc: str,
    python_executable: str,
    dll_dir: str,
) -> tuple[dict[str, str], ...]:
    subset = tier2_candidate_subset_signature(candidate_rows)
    rows: list[dict[str, str]] = []
    for candidate in sorted(
        (_string_row(row) for row in candidate_rows),
        key=lambda row: row.get("feature_family_id", ""),
    ):
        rows.append(
            _family_evidence_row(
                candidate=candidate,
                cell_rows=tuple(
                    _string_row(row)
                    for row in cells_by_family.get(
                        candidate.get("feature_family_id", ""),
                        (),
                    )
                ),
                source_context=source_context,
                raw_manifest_sha256=raw_manifest_sha256,
                candidate_subset_sha256=subset.sha256,
                candidate_subset_count=subset.count,
                source_expected_sample_count=source_expected_sample_count,
                trace_loader=trace_loader,
                config=config,
                producer_command=producer_command,
                generated_at_utc=generated_at_utc,
                python_executable=python_executable,
                dll_dir=dll_dir,
            )
        )
    return tuple(rows)


def _family_evidence_row(
    *,
    candidate: Mapping[str, str],
    cell_rows: Sequence[Mapping[str, str]],
    source_context: GateSourceContext,
    raw_manifest_sha256: str,
    candidate_subset_sha256: str,
    candidate_subset_count: int,
    source_expected_sample_count: int,
    trace_loader: TraceLoader,
    config: Tier2TraceProducerConfig,
    producer_command: str,
    generated_at_utc: str,
    python_executable: str,
    dll_dir: str,
) -> dict[str, str]:
    base = _base_row(
        candidate=candidate,
        source_context=source_context,
        raw_manifest_sha256=raw_manifest_sha256,
        candidate_subset_sha256=candidate_subset_sha256,
        candidate_subset_count=candidate_subset_count,
        source_expected_sample_count=source_expected_sample_count,
        producer_command=producer_command,
        generated_at_utc=generated_at_utc,
        python_executable=python_executable,
        dll_dir=dll_dir,
    )
    family_mz = _finite_float(candidate.get("family_center_mz"))
    if family_mz is None:
        return _blocked_row(base, "blocked", "fail", "missing_family_center_mz")

    seed_cells, rescued_cells = _split_detected_and_rescued_cells(cell_rows)
    if len(seed_cells) != 1:
        blocker = (
            "missing_detected_seed_cell"
            if not seed_cells
            else "multiple_detected_seed_cells"
        )
        return _blocked_row(base, "blocked", "fail", blocker)
    rescued_cells = tuple(
        sorted(rescued_cells, key=lambda row: _sort_area(row), reverse=True)[
            : max(0, config.max_rescued_cells_per_family)
        ]
    )

    seed_metrics, seed_blocker = _trace_metrics_for_cell(
        seed_cells[0],
        family_mz=family_mz,
        trace_loader=trace_loader,
        config=config,
        error_blocker="raw_unavailable",
    )
    if seed_metrics is None:
        evidence_status, raw_status, coherence_status = _status_for_unavailable_blocker(
            seed_blocker
        )
        return _blocked_row(
            base,
            evidence_status,
            raw_status,
            seed_blocker,
            coherence_status=coherence_status,
        )

    _apply_seed_metrics(base, seed_metrics)
    seed_metric_blockers = _trace_metric_blockers(seed_metrics, config)
    if seed_metric_blockers:
        (
            evidence_status,
            raw_status,
            coherence_status,
        ) = _status_for_trace_metric_blockers(seed_metric_blockers)
        return _blocked_row(
            base,
            evidence_status,
            raw_status,
            *seed_metric_blockers,
            coherence_status=coherence_status,
        )

    rescue_metrics: list[_TraceMetrics] = []
    rescue_blockers: list[str] = []
    for rescued_cell in rescued_cells:
        metric, blocker = _trace_metrics_for_cell(
            rescued_cell,
            family_mz=family_mz,
            trace_loader=trace_loader,
            config=config,
            error_blocker="raw_unavailable",
        )
        if metric is None:
            rescue_blockers.append(blocker)
            continue
        metric_blockers = _trace_metric_blockers(metric, config)
        if metric_blockers:
            rescue_blockers.extend(metric_blockers)
            continue
        rescue_metrics.append(metric)

    rescued_checked = len(rescued_cells)
    rescued_supported = len(rescue_metrics)
    apex_span_sec = _rescued_apex_span_sec(seed_metrics, rescue_metrics)
    boundary_overlap_min = _rescued_boundary_overlap_min(seed_metrics, rescue_metrics)
    seed_rescued_apex_span_sec = _seed_rescued_apex_span_sec(
        seed_metrics,
        rescue_metrics,
    )
    rescued_only_apex_span_sec = _rescued_only_apex_span_sec(rescue_metrics)
    rescued_pairwise_boundary_overlap_min = _rescued_pairwise_boundary_overlap_min(
        rescue_metrics
    )
    family_consensus_boundary_overlap_min = _family_consensus_boundary_overlap_min(
        seed_metrics,
        rescue_metrics,
    )
    coherence_blockers = _coherence_blockers(
        rescued_checked=rescued_checked,
        rescued_supported=rescued_supported,
        apex_span_sec=apex_span_sec,
        boundary_overlap_min=boundary_overlap_min,
        config=config,
    )
    rescue_unavailable_blockers = tuple(
        blocker
        for blocker in rescue_blockers
        if blocker in _UNAVAILABLE_BLOCKERS
    )
    base.update(
        {
            "rescued_cell_count_checked": str(rescued_checked),
            "rescued_cell_count_supported": str(rescued_supported),
            "rescued_apex_rt_span_sec": _format_float(apex_span_sec),
            "rescued_boundary_overlap_min": _format_float(boundary_overlap_min),
            "seed_rescued_boundary_overlap_min": _format_float(
                boundary_overlap_min
            ),
            "rescued_pairwise_boundary_overlap_min": _format_float(
                rescued_pairwise_boundary_overlap_min
            ),
            "family_consensus_boundary_overlap_min": _format_float(
                family_consensus_boundary_overlap_min
            ),
            "seed_rescued_apex_span_sec": _format_float(
                seed_rescued_apex_span_sec
            ),
            "rescued_only_apex_span_sec": _format_float(
                rescued_only_apex_span_sec
            ),
        }
    )
    if rescue_unavailable_blockers:
        return _blocked_row(
            base,
            "inconclusive",
            "pass",
            *rescue_unavailable_blockers,
            coherence_status="inconclusive",
        )

    base.update(
        {
            "evidence_status": "inconclusive",
            "support_component": "",
            "raw_trace_reread_status": "pass",
            "coherence_status": "inconclusive",
            "challenge_blockers": ";".join(
                _ordered_tokens(
                    (
                        "tier2_diagnostic_only",
                        *coherence_blockers,
                        *tuple(sorted(set(rescue_blockers))),
                    )
                )
            ),
            "dependent_context": (
                "neighbor_interference_not_assessed;"
                "raw_trace_reread;"
                "rescued_coherence"
            ),
        }
    )
    return _ordered_row(base)


def _base_row(
    *,
    candidate: Mapping[str, str],
    source_context: GateSourceContext,
    raw_manifest_sha256: str,
    candidate_subset_sha256: str,
    candidate_subset_count: int,
    source_expected_sample_count: int,
    producer_command: str,
    generated_at_utc: str,
    python_executable: str,
    dll_dir: str,
) -> dict[str, str]:
    row = {column: "" for column in TIER2_TRACE_EVIDENCE_REQUIRED_COLUMNS}
    row.update(
        {
            "feature_family_id": candidate.get("feature_family_id", ""),
            "criteria_version": CRITERIA_VERSION,
            "producer_version": PRODUCER_VERSION,
            "source_alignment_review_sha256": source_context.review_sha256,
            "source_alignment_cells_sha256": source_context.cell_sha256,
            "source_raw_manifest_sha256": raw_manifest_sha256,
            "source_candidate_subset_sha256": candidate_subset_sha256,
            "source_candidate_subset_count": str(candidate_subset_count),
            "source_expected_sample_count": str(source_expected_sample_count),
            "raw_reader_runtime": "pythonnet",
            "python_executable": python_executable,
            "dll_dir": dll_dir,
            "producer_command": producer_command,
            "generated_at_utc": generated_at_utc,
        }
    )
    return row


def _blocked_row(
    row: dict[str, str],
    evidence_status: str,
    raw_trace_reread_status: str,
    *blockers: str,
    coherence_status: str = "fail",
) -> dict[str, str]:
    row.update(
        {
            "evidence_status": evidence_status,
            "support_component": "",
            "raw_trace_reread_status": raw_trace_reread_status,
            "coherence_status": coherence_status,
            "challenge_blockers": ";".join(
                _ordered_tokens(("tier2_diagnostic_only", *blockers))
            ),
            "dependent_context": (
                "neighbor_interference_not_assessed;raw_trace_reread"
            ),
            "neighbor_interference_status": "not_assessed",
        }
    )
    return _ordered_row(row)


def _apply_seed_metrics(row: dict[str, str], metrics: _TraceMetrics) -> None:
    row.update(
        {
            "seed_apex_rt": _format_float(metrics.cell_apex_rt),
            "tier2_apex_rt": _format_float(metrics.tier2_apex_rt),
            "apex_delta_sec": _format_float(metrics.apex_delta_sec),
            "scan_support_score": _format_float(metrics.scan_support_score),
            "scan_availability_score": _format_float(
                metrics.scan_availability_score
            ),
            "trace_scan_count": str(metrics.trace_scan_count),
            "boundary_start_rt": _format_float(metrics.boundary_start_rt),
            "boundary_end_rt": _format_float(metrics.boundary_end_rt),
            "boundary_width_sec": _format_float(metrics.boundary_width_sec),
            "trace_apex_intensity": _format_float(metrics.trace_apex_intensity),
            "trace_baseline_noise": _format_float(metrics.trace_baseline_noise),
            "trace_signal_to_noise_proxy": _format_float(
                metrics.trace_signal_to_noise_proxy
            ),
            "trace_apex_prominence_score": _format_float(
                metrics.trace_apex_prominence_score
            ),
            "scan_support_basis": "scan_count_only",
            "neighbor_interference_ratio": "",
            "neighbor_interference_status": "not_assessed",
        }
    )


def _trace_metrics_for_cell(
    cell: Mapping[str, str],
    *,
    family_mz: float,
    trace_loader: TraceLoader,
    config: Tier2TraceProducerConfig,
    error_blocker: str,
) -> tuple[_TraceMetrics | None, str]:
    sample_stem = cell.get("sample_stem", "")
    cell_apex_rt = _finite_float(cell.get("apex_rt"))
    boundary_start = _finite_float(cell.get("peak_start_rt"))
    boundary_end = _finite_float(cell.get("peak_end_rt"))
    if not sample_stem or cell_apex_rt is None:
        return None, "candidate_window_unavailable"
    if (
        boundary_start is None
        or boundary_end is None
        or boundary_start >= boundary_end
    ):
        return None, "candidate_window_unavailable"

    rt_min = max(0.0, boundary_start - config.rt_padding_min)
    rt_max = boundary_end + config.rt_padding_min
    try:
        rt_values, intensity_values = trace_loader(
            sample_stem,
            family_mz,
            rt_min,
            rt_max,
            config.ppm_tolerance,
        )
    except Exception:
        return None, error_blocker

    try:
        rt = tuple(float(value) for value in rt_values)
        intensity = tuple(float(value) for value in intensity_values)
    except (TypeError, ValueError):
        return None, "metric_unavailable"
    if len(rt) != len(intensity):
        return None, "metric_unavailable"

    region = tuple(
        (rt_value, intensity_value)
        for rt_value, intensity_value in zip(rt, intensity, strict=True)
        if boundary_start <= rt_value <= boundary_end
    )
    if not region:
        return None, "metric_unavailable"
    apex_rt, apex_intensity = max(region, key=lambda item: item[1])
    region_intensities = tuple(value for _rt, value in region)
    scan_availability_score = _scan_support_score(
        region_intensities,
        scans_target=config.scans_target,
    )
    context_apex, noise, signal_to_noise, prominence = _trace_context_scores(
        region_intensities
    )
    return (
        _TraceMetrics(
            cell_apex_rt=cell_apex_rt,
            tier2_apex_rt=apex_rt,
            apex_delta_sec=abs(apex_rt - cell_apex_rt) * 60.0,
            scan_support_score=scan_availability_score,
            scan_availability_score=scan_availability_score,
            trace_scan_count=len(region),
            boundary_start_rt=boundary_start,
            boundary_end_rt=boundary_end,
            boundary_width_sec=(boundary_end - boundary_start) * 60.0,
            trace_apex_intensity=max(apex_intensity, context_apex),
            trace_baseline_noise=noise,
            trace_signal_to_noise_proxy=signal_to_noise,
            trace_apex_prominence_score=prominence,
        ),
        "",
    )


def _scan_support_score(
    intensities: tuple[float, ...],
    *,
    scans_target: int,
) -> float:
    if scans_target <= 0 or not intensities:
        return 0.0
    apex = max(intensities)
    if apex <= 0:
        return 0.0
    return min(1.0, len(intensities) / scans_target)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _trace_context_scores(
    intensities: tuple[float, ...],
) -> tuple[float, float, float, float]:
    if not intensities:
        return 0.0, 0.0, 0.0, 0.0
    apex = max(intensities)
    baseline = _median(intensities)
    noise = _median(tuple(abs(value - baseline) for value in intensities))
    denominator = max(noise, 1.0)
    signal_to_noise = max(0.0, apex - baseline) / denominator
    edge_max = max(intensities[0], intensities[-1])
    prominence = max(0.0, apex - edge_max) / max(abs(apex), 1.0)
    return apex, noise, signal_to_noise, prominence


def _trace_metric_blockers(
    metrics: _TraceMetrics,
    config: Tier2TraceProducerConfig,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if metrics.trace_scan_count < config.scans_target:
        blockers.append("metric_unavailable")
    if metrics.scan_support_score < 0.20:
        blockers.append("low_scan_support")
    elif metrics.scan_support_score < config.min_scan_support_score:
        blockers.append("weak_scan_support")
    if metrics.apex_delta_sec > config.max_apex_delta_sec:
        blockers.append("apex_delta_exceeds_threshold")
    if (
        metrics.boundary_width_sec <= 0.0
        or metrics.boundary_width_sec > config.max_boundary_width_sec
    ):
        blockers.append("boundary_width_out_of_range")
    return tuple(blockers)


def _status_for_trace_metric_blockers(
    blockers: Sequence[str],
) -> tuple[str, str, str]:
    if "metric_unavailable" in blockers:
        return "inconclusive", "inconclusive", "inconclusive"
    if set(blockers) == {"weak_scan_support"}:
        return "not_supported", "fail", "fail"
    return "blocked", "fail", "fail"


_UNAVAILABLE_BLOCKERS = frozenset(
    {
        "candidate_window_unavailable",
        "metric_unavailable",
        "raw_unavailable",
        "runtime_unavailable",
    }
)


def _status_for_unavailable_blocker(blocker: str) -> tuple[str, str, str]:
    if blocker in _UNAVAILABLE_BLOCKERS:
        return "inconclusive", "inconclusive", "inconclusive"
    return "blocked", "fail", "fail"


def _coherence_blockers(
    *,
    rescued_checked: int,
    rescued_supported: int,
    apex_span_sec: float,
    boundary_overlap_min: float,
    config: Tier2TraceProducerConfig,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if rescued_checked < 1 or rescued_supported < 1:
        blockers.append("rescued_cell_support_low")
    elif rescued_supported / rescued_checked < 0.50:
        blockers.append("rescued_cell_support_low")
    if apex_span_sec > config.max_rescued_apex_span_sec:
        blockers.append("rescued_apex_span_wide")
    if boundary_overlap_min < config.min_rescued_boundary_overlap:
        blockers.append("rescued_boundary_overlap_low")
    return tuple(blockers)


def _rescued_apex_span_sec(
    seed_metrics: _TraceMetrics,
    rescue_metrics: Sequence[_TraceMetrics],
) -> float:
    apex_values = [
        seed_metrics.tier2_apex_rt,
        *(item.tier2_apex_rt for item in rescue_metrics),
    ]
    if len(apex_values) < 2:
        return 0.0
    return (max(apex_values) - min(apex_values)) * 60.0


def _seed_rescued_apex_span_sec(
    seed_metrics: _TraceMetrics,
    rescue_metrics: Sequence[_TraceMetrics],
) -> float | None:
    if not rescue_metrics:
        return None
    return max(
        abs(seed_metrics.tier2_apex_rt - item.tier2_apex_rt) * 60.0
        for item in rescue_metrics
    )


def _rescued_only_apex_span_sec(
    rescue_metrics: Sequence[_TraceMetrics],
) -> float | None:
    if len(rescue_metrics) < 2:
        return None
    apex_values = [item.tier2_apex_rt for item in rescue_metrics]
    return (max(apex_values) - min(apex_values)) * 60.0


def _rescued_boundary_overlap_min(
    seed_metrics: _TraceMetrics,
    rescue_metrics: Sequence[_TraceMetrics],
) -> float:
    if not rescue_metrics:
        return 0.0
    return min(
        _boundary_overlap_fraction(seed_metrics, item) for item in rescue_metrics
    )


def _rescued_pairwise_boundary_overlap_min(
    rescue_metrics: Sequence[_TraceMetrics],
) -> float | None:
    if len(rescue_metrics) < 2:
        return None
    values = [
        _boundary_overlap_fraction(first, second)
        for index, first in enumerate(rescue_metrics)
        for second in rescue_metrics[index + 1 :]
    ]
    return min(values) if values else None


def _family_consensus_boundary_overlap_min(
    seed_metrics: _TraceMetrics,
    rescue_metrics: Sequence[_TraceMetrics],
) -> float | None:
    metrics = (seed_metrics, *tuple(rescue_metrics))
    if len(metrics) < 2:
        return None
    consensus_start = _median(tuple(item.boundary_start_rt for item in metrics))
    consensus_end = _median(tuple(item.boundary_end_rt for item in metrics))
    if consensus_start >= consensus_end:
        return 0.0
    consensus = _TraceMetrics(
        cell_apex_rt=seed_metrics.cell_apex_rt,
        tier2_apex_rt=seed_metrics.tier2_apex_rt,
        apex_delta_sec=seed_metrics.apex_delta_sec,
        scan_support_score=seed_metrics.scan_support_score,
        scan_availability_score=seed_metrics.scan_availability_score,
        trace_scan_count=seed_metrics.trace_scan_count,
        boundary_start_rt=consensus_start,
        boundary_end_rt=consensus_end,
        boundary_width_sec=(consensus_end - consensus_start) * 60.0,
        trace_apex_intensity=seed_metrics.trace_apex_intensity,
        trace_baseline_noise=seed_metrics.trace_baseline_noise,
        trace_signal_to_noise_proxy=seed_metrics.trace_signal_to_noise_proxy,
        trace_apex_prominence_score=seed_metrics.trace_apex_prominence_score,
    )
    return min(_boundary_overlap_fraction(consensus, item) for item in metrics)


def _boundary_overlap_fraction(first: _TraceMetrics, second: _TraceMetrics) -> float:
    start = max(first.boundary_start_rt, second.boundary_start_rt)
    end = min(first.boundary_end_rt, second.boundary_end_rt)
    overlap = max(0.0, end - start)
    first_width = max(0.0, first.boundary_end_rt - first.boundary_start_rt)
    second_width = max(0.0, second.boundary_end_rt - second.boundary_start_rt)
    denominator = max(first_width, second_width)
    if denominator <= 0.0:
        return 0.0
    return overlap / denominator


def _finite_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value))
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _sort_area(row: Mapping[str, str]) -> float:
    return _finite_float(row.get("area")) or 0.0


def _split_detected_and_rescued_cells(
    cell_rows: Sequence[Mapping[str, str]],
) -> tuple[tuple[Mapping[str, str], ...], tuple[Mapping[str, str], ...]]:
    seed_cells: list[Mapping[str, str]] = []
    rescued_cells: list[Mapping[str, str]] = []
    for row in cell_rows:
        status = row.get("status")
        if status == "detected":
            seed_cells.append(row)
        elif status == "rescued":
            rescued_cells.append(row)
    return tuple(seed_cells), tuple(rescued_cells)


def _string_row(row: Mapping[str, object]) -> dict[str, str]:
    return {str(key): "" if value is None else str(value) for key, value in row.items()}


def _format_float(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.6g}"


def _ordered_tokens(tokens: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return tuple(ordered)


def _ordered_row(row: Mapping[str, str]) -> dict[str, str]:
    return {
        column: row.get(column, "") for column in TIER2_TRACE_EVIDENCE_REQUIRED_COLUMNS
    }
