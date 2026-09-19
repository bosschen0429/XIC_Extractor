from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .schema import WRONG_PEAK_ROOT_CAUSE_SCHEMA_VERSION

_FAMILY_CONSENSUS_CONFLICT_REASON = (
    "family_ms1_overlay_competing_peak_matches_family_consensus"
)
_LOW_DOMINANCE_MAX_RATIO = 0.50
_LARGE_RT_DELTA_SEC = 30.0
_ALTERNATE_MIN_RELATIVE_INTENSITY = 0.05
_ALTERNATE_STRONG_RELATIVE_INTENSITY = 0.25
_SELECTED_BOUNDARY_TOLERANCE_MIN = 0.02
_SELECTED_APEX_TOLERANCE_MIN = 0.05
_WRONG_PEAK_TOKENS = frozenset(
    {
        "family_ms1_overlay_competing_peak_matches_family_consensus",
        "rt_mode_status=mode_conflict",
        "rt_mode_status=mode_split_required",
        "rt_mode_status=consolidation_no_go",
        "peak_hypothesis_status=cross_mode_rescue_blocked",
        "product_selection_action=block_cross_mode_rescue",
    }
)
_QC_CONFLICT_TOKENS = frozenset(
    {
        "candidate_ms2_pattern_status=conflict",
        "ms1_pattern_status=conflict",
    }
)
_QC_CONFLICT_MISSING_EVIDENCE = frozenset(
    {
        "matrix_rt_drift_policy_not_supportive",
        "peak_hypothesis_not_supportive",
        "rt_mode_not_supportive",
    }
)


@dataclass(frozen=True)
class OverlayTrace:
    family_id: str
    sample_stem: str
    artifact_path: str
    family_center_rt: float | None
    rt: tuple[float, ...]
    intensity: tuple[float, ...]


@dataclass(frozen=True)
class PeakCandidate:
    rt: float
    intensity: float
    relative_intensity: float


@dataclass(frozen=True)
class AlternatePeakProposal:
    trace_data_status: str
    trace_data_artifact: str
    status: str
    rt: float | None
    intensity: float | None
    relative_intensity: float | None
    delta_from_selected_sec: float | None
    delta_from_family_center_sec: float | None
    basis: str
    next_action: str


def load_overlay_trace_data(
    paths: Sequence[Path],
) -> dict[tuple[str, str], OverlayTrace]:
    traces: dict[tuple[str, str], OverlayTrace] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        family_id = str(payload.get("family_id") or "")
        family_center_rt = _float_or_none(payload.get("family_center_rt"))
        for trace_payload in payload.get("traces", []):
            sample_stem = str(trace_payload.get("sample_stem") or "")
            if not family_id or not sample_stem:
                continue
            rt_values, intensity_values = _paired_numeric_trace(
                trace_payload.get("rt", ()),
                trace_payload.get("intensity", ()),
            )
            traces[(family_id, sample_stem)] = OverlayTrace(
                family_id=family_id,
                sample_stem=sample_stem,
                artifact_path=str(path),
                family_center_rt=family_center_rt,
                rt=rt_values,
                intensity=intensity_values,
            )
    return traces


def build_wrong_peak_root_cause_rows(
    *,
    machine_evidence_support_rows: Sequence[Mapping[str, str]],
    alignment_cell_rows: Sequence[Mapping[str, str]],
    overlay_traces: Mapping[tuple[str, str], OverlayTrace] | None = None,
) -> tuple[dict[str, str], ...]:
    cells_by_key = _index_rows_by_family_sample(alignment_cell_rows)
    trace_by_key = overlay_traces or {}
    rows: list[dict[str, str]] = []
    for support in machine_evidence_support_rows:
        if not _is_wrong_peak_support(support):
            continue
        family_id = support.get("feature_family_id", "")
        sample_id = _first_text(support.get("sample_id"), support.get("sample_stem"))
        cell = _lookup_family_sample_row(cells_by_key, family_id, support)
        trace = _lookup_family_sample_trace(trace_by_key, family_id, support)
        metrics = _parse_metrics(support.get("observed_machine_metrics", ""))
        selected_apex_rt = _first_float(
            cell.get("apex_rt"),
            metrics.get("apex_rt"),
        )
        selected_start_rt = _float_or_none(cell.get("peak_start_rt"))
        selected_end_rt = _float_or_none(cell.get("peak_end_rt"))
        selected_rt_delta_sec = _first_float(
            cell.get("rt_delta_sec"),
            metrics.get("rt_delta_sec"),
        )
        family_center_rt = _first_float(
            cell.get("family_center_rt"),
            trace.family_center_rt if trace is not None else None,
        )
        alternate = _propose_alternate_peak(
            trace,
            selected_apex_rt=selected_apex_rt,
            selected_start_rt=selected_start_rt,
            selected_end_rt=selected_end_rt,
            selected_rt_delta_sec=selected_rt_delta_sec,
            family_center_rt=family_center_rt,
        )
        rows.append(
            {
                "wrong_peak_root_cause_schema_version": (
                    WRONG_PEAK_ROOT_CAUSE_SCHEMA_VERSION
                ),
                "feature_family_id": family_id,
                "sample_id": sample_id,
                "machine_current_label": support.get("machine_current_label", ""),
                "root_cause_class": _classify_root_cause(metrics),
                "secondary_root_cause_tokens": ";".join(
                    _secondary_root_cause_tokens(metrics)
                ),
                "selection_failure_mode": _classify_selection_failure(
                    metrics,
                    cell,
                ),
                "selected_cell_status": cell.get("status", ""),
                "selected_area": cell.get("area", ""),
                "selected_apex_rt": _format_float(selected_apex_rt),
                "selected_peak_start_rt": _format_float(selected_start_rt),
                "selected_peak_end_rt": _format_float(selected_end_rt),
                "selected_rt_delta_sec": _format_float(selected_rt_delta_sec),
                "selected_cell_height": _first_text(
                    cell.get("height", ""),
                    metrics.get("ms1_cell_height", ""),
                ),
                "selected_local_window_max_intensity": metrics.get(
                    "ms1_local_window_max_intensity",
                    "",
                ),
                "selected_cell_to_local_window_max_ratio": metrics.get(
                    "ms1_cell_to_local_window_max_ratio",
                    "",
                ),
                "selected_shape_correlation_score": metrics.get(
                    "ms1_shape_correlation_score",
                    "",
                ),
                "selected_qc_reference_status": metrics.get(
                    "qc_ms1_reference_status",
                    "",
                ),
                "selected_qc_reference_sample": metrics.get(
                    "qc_ms1_reference_sample",
                    "",
                ),
                "selected_qc_reference_apex_abs_delta_sec": metrics.get(
                    "qc_ms1_reference_apex_abs_delta_sec",
                    "",
                ),
                "selected_qc_reference_shape_similarity": metrics.get(
                    "qc_ms1_reference_shape_similarity",
                    "",
                ),
                "selected_ms2_pattern_status": metrics.get(
                    "candidate_ms2_pattern_status",
                    "",
                ),
                "selected_ms2_trigger_scan_count": metrics.get(
                    "candidate_ms2_raw_trigger_scan_count",
                    "",
                ),
                "selected_ms2_strict_nl_scan_count": metrics.get(
                    "candidate_ms2_raw_strict_nl_scan_count",
                    "",
                ),
                "family_center_rt": _format_float(family_center_rt),
                "trace_data_status": alternate.trace_data_status,
                "trace_data_artifact": alternate.trace_data_artifact,
                "alternate_peak_status": alternate.status,
                "alternate_peak_rt": _format_float(alternate.rt),
                "alternate_peak_intensity": _format_float(alternate.intensity),
                "alternate_peak_relative_intensity": _format_float(
                    alternate.relative_intensity
                ),
                "alternate_peak_delta_from_selected_sec": _format_float(
                    alternate.delta_from_selected_sec
                ),
                "alternate_peak_delta_from_family_center_sec": _format_float(
                    alternate.delta_from_family_center_sec
                ),
                "alternate_peak_basis": alternate.basis,
                "recommended_next_action": alternate.next_action,
                "diagnostic_only": "TRUE",
            }
        )
    return tuple(
        sorted(rows, key=lambda row: (row["feature_family_id"], row["sample_id"]))
    )


def _index_rows_by_family_sample(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    indexed: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        family_id = row.get("feature_family_id", "")
        if not family_id:
            continue
        for sample_key in _sample_keys(row):
            indexed.setdefault((family_id, sample_key), row)
    return indexed


def _lookup_family_sample_row(
    index: Mapping[tuple[str, str], Mapping[str, str]],
    family_id: str,
    row: Mapping[str, str],
) -> Mapping[str, str]:
    for sample_key in _sample_keys(row):
        match = index.get((family_id, sample_key))
        if match is not None:
            return match
    return {}


def _lookup_family_sample_trace(
    index: Mapping[tuple[str, str], OverlayTrace],
    family_id: str,
    row: Mapping[str, str],
) -> OverlayTrace | None:
    for sample_key in _sample_keys(row):
        match = index.get((family_id, sample_key))
        if match is not None:
            return match
    return None


def _sample_keys(row: Mapping[str, str]) -> tuple[str, ...]:
    keys: list[str] = []
    for field in ("sample_id", "sample_stem"):
        value = row.get(field, "").strip()
        if value and value not in keys:
            keys.append(value)
    return tuple(keys)


def _is_wrong_peak_support(row: Mapping[str, str]) -> bool:
    metrics = _split_tokens(row.get("observed_machine_metrics"))
    missing = _split_tokens(row.get("missing_machine_evidence"))
    if _has_any(metrics, _WRONG_PEAK_TOKENS) or "rt_pattern_conflict_gate" in missing:
        return True
    if "qc_ms1_reference_status=conflict" not in metrics:
        return False
    return _has_any(metrics, _QC_CONFLICT_TOKENS) or _has_any(
        missing,
        _QC_CONFLICT_MISSING_EVIDENCE,
    )


def _split_tokens(value: object) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value or "").split(";") if part.strip())


def _has_any(values: Sequence[str], candidates: frozenset[str]) -> bool:
    return any(
        value == candidate or candidate in value
        for value in values
        for candidate in candidates
    )


def _classify_root_cause(metrics: Mapping[str, str]) -> str:
    if metrics.get("ms1_pattern_reason") == _FAMILY_CONSENSUS_CONFLICT_REASON:
        return "selected_peak_conflicts_with_family_consensus"
    if metrics.get("qc_ms1_reference_status") == "conflict":
        return "selected_peak_conflicts_with_qc_reference"
    local_ratio = _float_or_none(
        metrics.get("ms1_cell_to_local_window_max_ratio")
    )
    if local_ratio is not None and local_ratio < _LOW_DOMINANCE_MAX_RATIO:
        return "selected_peak_low_local_dominance"
    if metrics.get("candidate_ms2_pattern_status") == "conflict":
        return "selected_peak_candidate_ms2_conflict"
    if (
        metrics.get("candidate_ms2_pattern_status") == "not_observed"
        and _int_or_zero(metrics.get("candidate_ms2_raw_trigger_scan_count")) > 0
    ):
        return "selected_peak_ms2_not_supportive"
    return "wrong_peak_conflict_unclassified"


def _secondary_root_cause_tokens(metrics: Mapping[str, str]) -> tuple[str, ...]:
    tokens: list[str] = []
    local_ratio = _float_or_none(
        metrics.get("ms1_cell_to_local_window_max_ratio")
    )
    shape_correlation = _float_or_none(
        metrics.get("ms1_shape_correlation_score")
    )
    if metrics.get("ms1_pattern_reason") == _FAMILY_CONSENSUS_CONFLICT_REASON:
        tokens.append("family_consensus_conflict")
    if metrics.get("qc_ms1_reference_status") == "conflict":
        tokens.append("qc_reference_conflict")
    if local_ratio is not None and local_ratio < _LOW_DOMINANCE_MAX_RATIO:
        tokens.append("low_local_peak_dominance")
    if shape_correlation is not None and shape_correlation < 0.5:
        tokens.append("low_shape_correlation")
    if metrics.get("candidate_ms2_pattern_status") == "conflict":
        tokens.append("candidate_ms2_conflict")
    if metrics.get("candidate_ms2_pattern_status") == "not_observed":
        tokens.append("candidate_ms2_not_observed")
    if metrics.get("ms1_peak_quality_vector_reason"):
        tokens.append(metrics["ms1_peak_quality_vector_reason"])
    return tuple(dict.fromkeys(tokens))


def _classify_selection_failure(
    metrics: Mapping[str, str],
    cell: Mapping[str, str],
) -> str:
    local_ratio = _float_or_none(metrics.get("ms1_cell_to_local_window_max_ratio"))
    if local_ratio is not None and local_ratio < _LOW_DOMINANCE_MAX_RATIO:
        return "selected_peak_not_local_dominant"
    reason = cell.get("reason", "")
    if "duplicate MS1 peak claim" in reason:
        return "duplicate_owner_peak_claim_selected_conflicting_peak"
    rt_delta = _first_float(cell.get("rt_delta_sec"), metrics.get("rt_delta_sec"))
    if rt_delta is not None and abs(rt_delta) > _LARGE_RT_DELTA_SEC:
        return "large_rt_delta_selected_peak"
    if metrics.get("qc_ms1_reference_status") == "conflict":
        return "qc_reference_conflict_selected_peak"
    if metrics.get("ms1_pattern_reason") == _FAMILY_CONSENSUS_CONFLICT_REASON:
        return "family_consensus_conflict_selected_peak"
    return "unknown_selection_failure_mode"


def _propose_alternate_peak(
    trace: OverlayTrace | None,
    *,
    selected_apex_rt: float | None,
    selected_start_rt: float | None,
    selected_end_rt: float | None,
    selected_rt_delta_sec: float | None,
    family_center_rt: float | None,
) -> AlternatePeakProposal:
    if trace is None:
        return AlternatePeakProposal(
            trace_data_status="missing",
            trace_data_artifact="",
            status="trace_data_missing",
            rt=None,
            intensity=None,
            relative_intensity=None,
            delta_from_selected_sec=None,
            delta_from_family_center_sec=None,
            basis="",
            next_action="generate_family_ms1_overlay_trace_data",
        )
    maxima = _local_maxima(trace.rt, trace.intensity)
    if not maxima:
        return _no_alternate(trace, "no_local_maxima")
    max_intensity = max(trace.intensity, default=0.0)
    if max_intensity <= 0:
        return _no_alternate(trace, "empty_trace")
    viable = [
        PeakCandidate(
            rt=candidate.rt,
            intensity=candidate.intensity,
            relative_intensity=candidate.intensity / max_intensity,
        )
        for candidate in maxima
        if candidate.intensity / max_intensity >= _ALTERNATE_MIN_RELATIVE_INTENSITY
        and not _inside_selected_peak(
            candidate.rt,
            selected_apex_rt=selected_apex_rt,
            selected_start_rt=selected_start_rt,
            selected_end_rt=selected_end_rt,
        )
    ]
    if not viable:
        return _no_alternate(trace, "no_peak_outside_selected_boundary")
    preferred = _directional_candidates(
        viable,
        selected_start_rt=selected_start_rt,
        selected_end_rt=selected_end_rt,
        selected_rt_delta_sec=selected_rt_delta_sec,
    )
    candidate = max(preferred or viable, key=lambda item: item.intensity)
    status = (
        "candidate_found"
        if candidate.relative_intensity >= _ALTERNATE_STRONG_RELATIVE_INTENSITY
        else "weak_candidate_found"
    )
    next_action = (
        "inspect_alternate_peak_before_retarget"
        if status == "candidate_found"
        else "inspect_root_cause_before_retarget"
    )
    return AlternatePeakProposal(
        trace_data_status="present",
        trace_data_artifact=trace.artifact_path,
        status=status,
        rt=candidate.rt,
        intensity=candidate.intensity,
        relative_intensity=candidate.relative_intensity,
        delta_from_selected_sec=_delta_sec(candidate.rt, selected_apex_rt),
        delta_from_family_center_sec=_delta_sec(candidate.rt, family_center_rt),
        basis="raw_overlay_local_max_outside_selected_boundary",
        next_action=next_action,
    )


def _no_alternate(trace: OverlayTrace, basis: str) -> AlternatePeakProposal:
    return AlternatePeakProposal(
        trace_data_status="present",
        trace_data_artifact=trace.artifact_path,
        status="no_alternate_peak_found",
        rt=None,
        intensity=None,
        relative_intensity=None,
        delta_from_selected_sec=None,
        delta_from_family_center_sec=None,
        basis=basis,
        next_action="block_without_retarget_candidate",
    )


def _local_maxima(
    rt_values: Sequence[float],
    intensity_values: Sequence[float],
) -> tuple[PeakCandidate, ...]:
    candidates: list[PeakCandidate] = []
    for index in range(1, min(len(rt_values), len(intensity_values)) - 1):
        current = intensity_values[index]
        if current <= 0:
            continue
        if (
            current >= intensity_values[index - 1]
            and current >= intensity_values[index + 1]
        ):
            candidates.append(
                PeakCandidate(
                    rt=rt_values[index],
                    intensity=current,
                    relative_intensity=0.0,
                )
            )
    return tuple(candidates)


def _directional_candidates(
    candidates: Sequence[PeakCandidate],
    *,
    selected_start_rt: float | None,
    selected_end_rt: float | None,
    selected_rt_delta_sec: float | None,
) -> tuple[PeakCandidate, ...]:
    if selected_rt_delta_sec is None:
        return ()
    if selected_rt_delta_sec < -_LARGE_RT_DELTA_SEC and selected_end_rt is not None:
        return tuple(
            candidate for candidate in candidates if candidate.rt > selected_end_rt
        )
    if selected_rt_delta_sec > _LARGE_RT_DELTA_SEC and selected_start_rt is not None:
        return tuple(
            candidate for candidate in candidates if candidate.rt < selected_start_rt
        )
    return ()


def _inside_selected_peak(
    rt: float,
    *,
    selected_apex_rt: float | None,
    selected_start_rt: float | None,
    selected_end_rt: float | None,
) -> bool:
    if selected_start_rt is not None and selected_end_rt is not None:
        return (
            selected_start_rt - _SELECTED_BOUNDARY_TOLERANCE_MIN
            <= rt
            <= selected_end_rt + _SELECTED_BOUNDARY_TOLERANCE_MIN
        )
    if selected_apex_rt is not None:
        return abs(rt - selected_apex_rt) <= _SELECTED_APEX_TOLERANCE_MIN
    return False


def _parse_metrics(*values: str) -> dict[str, str]:
    metrics: dict[str, str] = {}
    for value in values:
        for token in str(value or "").split(";"):
            token = token.strip()
            if not token or "=" not in token:
                continue
            key, raw_value = token.split("=", 1)
            metrics[key] = raw_value.strip()
    return metrics


def _paired_numeric_trace(
    rt_values: object,
    intensity_values: object,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    rt_out: list[float] = []
    intensity_out: list[float] = []
    if not isinstance(rt_values, list) or not isinstance(intensity_values, list):
        return (), ()
    for rt_value, intensity_value in zip(rt_values, intensity_values, strict=False):
        rt = _float_or_none(rt_value)
        intensity = _float_or_none(intensity_value)
        if rt is None or intensity is None:
            continue
        rt_out.append(rt)
        intensity_out.append(intensity)
    return tuple(rt_out), tuple(intensity_out)


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _first_float(*values: object) -> float | None:
    for value in values:
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _float_or_none(value: object) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        parsed = float(value)
    else:
        try:
            parsed = float(str(value).strip().lstrip("'"))
        except ValueError:
            return None
    return parsed if math.isfinite(parsed) else None


def _int_or_zero(value: object) -> int:
    parsed = _float_or_none(value)
    return 0 if parsed is None else int(parsed)


def _delta_sec(rt: float | None, reference_rt: float | None) -> float | None:
    if rt is None or reference_rt is None:
        return None
    return (rt - reference_rt) * 60.0


def _format_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6g}"
