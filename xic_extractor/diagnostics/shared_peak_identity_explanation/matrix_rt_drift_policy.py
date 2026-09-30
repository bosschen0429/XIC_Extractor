from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.tabular_io import (
    optional_float as diagnostic_optional_float,
)
from xic_extractor.tabular_io import (
    optional_int,
    read_tsv_required,
    text_value,
    write_tsv,
)

from .machine_evidence_support import MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS

MATRIX_RT_DRIFT_POLICY_OPTIONAL_COLUMNS = (
    "drift_reference_artifacts",
    "istd_trend_sample_count",
    "istd_trend_injection_order_span",
    "istd_phase_summary",
)
MATRIX_RT_DRIFT_POLICY_COLUMNS = (
    *MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS,
    *MATRIX_RT_DRIFT_POLICY_OPTIONAL_COLUMNS,
)

_ALIGNMENT_CELL_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "apex_rt",
    "rt_delta_sec",
)
_ALIGNMENT_REVIEW_COLUMNS = (
    "feature_family_id",
    "family_center_mz",
)
_OWNER_EDGE_COLUMNS = (
    "left_sample_stem",
    "right_sample_stem",
    "left_precursor_mz",
    "right_precursor_mz",
    "left_rt_min",
    "right_rt_min",
    "decision",
    "rt_raw_delta_sec",
    "rt_drift_corrected_delta_sec",
    "drift_prior_source",
    "reason",
)
_RT_NORMALIZATION_FAMILY_COLUMNS = (
    "feature_family_id",
    "modelled_cell_count",
    "raw_rt_range_min",
    "normalized_rt_range_min",
    "rt_range_improvement_min",
    "normalized_rt_support",
    "anchor_support_level",
    "local_residual_window_min",
)
_TARGETED_ISTD_SUMMARY_COLUMNS = (
    "target_label",
    "role",
    "active_tag",
    "targeted_positive_count",
    "coverage_denominator_count",
    "primary_match_count",
    "selected_feature_id",
    "sample_rt_p95_abs_delta_min",
)
_RT_NORMALIZATION_LEAVE_ONE_OUT_COLUMNS = (
    "target_label",
    "evaluated_count",
    "p95_abs_error_min",
    "status",
)
_ISTD_RT_TREND_COLUMNS = (
    "target_label",
    "sample_stem",
    "injection_order",
    "injection_phase",
    "observed_rt_min",
)
_ISTD_PHASE_SUMMARY_COLUMNS = (
    "target_label",
    "injection_phase",
    "sample_count",
    "injection_order_min",
    "injection_order_max",
    "observed_rt_min_min",
    "observed_rt_median_min",
    "observed_rt_max_min",
    "observed_rt_iqr_min",
)
_DRIFT_SUPPORT_SOURCES = {
    "targeted_istd_trend": "sample_istd_aligned",
    "batch_istd_trend": "matrix_reference_aligned",
}
_MIN_EXPLAINED_SHIFT_SEC = 10.0
_MIN_ANCHOR_LOCAL_TREND_EVALUATED_COUNT = 20


def build_matrix_rt_drift_policy_rows(
    *,
    alignment_cells_tsv: Path,
    alignment_review_tsv: Path,
    oracle_keys: Iterable[tuple[str, str]],
    owner_edge_evidence_tsv: Path | None = None,
    rt_normalization_families_tsv: Path | None = None,
    targeted_istd_benchmark_summary_tsv: Path | None = None,
    rt_normalization_leave_one_anchor_out_tsv: Path | None = None,
    istd_rt_trend_tsv: Path | None = None,
    istd_phase_summary_tsv: Path | None = None,
    config: AlignmentConfig | None = None,
) -> tuple[dict[str, str], ...]:
    """Build diagnostic-only matrix RT drift policy rows from existing artifacts."""

    if (targeted_istd_benchmark_summary_tsv is None) != (
        rt_normalization_leave_one_anchor_out_tsv is None
    ):
        raise ValueError(
            "targeted ISTD anchor-local trend evidence requires both "
            "targeted_istd_benchmark_summary_tsv and "
            "rt_normalization_leave_one_anchor_out_tsv"
        )
    if (istd_rt_trend_tsv is not None or istd_phase_summary_tsv is not None) and (
        targeted_istd_benchmark_summary_tsv is None
        or rt_normalization_leave_one_anchor_out_tsv is None
    ):
        raise ValueError(
            "ISTD RT trend provenance requires targeted ISTD anchor-local trend "
            "evidence: targeted_istd_benchmark_summary_tsv and "
            "rt_normalization_leave_one_anchor_out_tsv"
        )
    config = config or AlignmentConfig()
    cells = _cell_by_key(
        read_tsv_required(alignment_cells_tsv, _ALIGNMENT_CELL_COLUMNS)
    )
    families = _family_by_id(
        read_tsv_required(alignment_review_tsv, _ALIGNMENT_REVIEW_COLUMNS)
    )
    owner_edges = (
        read_tsv_required(owner_edge_evidence_tsv, _OWNER_EDGE_COLUMNS)
        if owner_edge_evidence_tsv is not None
        else ()
    )
    normalized_families = (
        _rt_normalization_by_family(
            read_tsv_required(
                rt_normalization_families_tsv,
                _RT_NORMALIZATION_FAMILY_COLUMNS,
            )
        )
        if rt_normalization_families_tsv is not None
        else {}
    )
    targeted_anchor_trends = (
        _targeted_anchor_trend_by_family(
            read_tsv_required(
                targeted_istd_benchmark_summary_tsv,
                _TARGETED_ISTD_SUMMARY_COLUMNS,
            ),
            read_tsv_required(
                rt_normalization_leave_one_anchor_out_tsv,
                _RT_NORMALIZATION_LEAVE_ONE_OUT_COLUMNS,
            ),
            istd_trend_rows=(
                read_tsv_required(istd_rt_trend_tsv, _ISTD_RT_TREND_COLUMNS)
                if istd_rt_trend_tsv is not None
                else ()
            ),
            istd_phase_summary_rows=(
                read_tsv_required(
                    istd_phase_summary_tsv,
                    _ISTD_PHASE_SUMMARY_COLUMNS,
                )
                if istd_phase_summary_tsv is not None
                else ()
            ),
            source_paths=(
                targeted_istd_benchmark_summary_tsv,
                rt_normalization_leave_one_anchor_out_tsv,
                istd_rt_trend_tsv,
                istd_phase_summary_tsv,
            ),
        )
        if targeted_istd_benchmark_summary_tsv is not None
        and rt_normalization_leave_one_anchor_out_tsv is not None
        else {}
    )
    rows = [
        _row_for_key(
            feature_family_id=feature_family_id,
            sample_stem=sample_stem,
            cell=cells.get((feature_family_id, sample_stem)),
            family=families.get(feature_family_id, {}),
            owner_edges=owner_edges,
            rt_normalization_family=normalized_families.get(feature_family_id),
            targeted_anchor_trend=targeted_anchor_trends.get(feature_family_id),
            config=config,
        )
        for feature_family_id, sample_stem in oracle_keys
    ]
    return tuple(
        sorted(rows, key=lambda row: (row["feature_family_id"], row["sample_stem"]))
    )


def write_matrix_rt_drift_policy_rows(
    path: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(path, rows, MATRIX_RT_DRIFT_POLICY_COLUMNS, lineterminator="\n")


def _cell_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    return {(row["feature_family_id"], row["sample_stem"]): row for row in rows}


def _family_by_id(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    return {row["feature_family_id"]: row for row in rows}


def _rt_normalization_by_family(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    return {row["feature_family_id"]: row for row in rows}


def _targeted_anchor_trend_by_family(
    targeted_summary_rows: Sequence[Mapping[str, str]],
    leave_one_out_rows: Sequence[Mapping[str, str]],
    *,
    istd_trend_rows: Sequence[Mapping[str, str]] = (),
    istd_phase_summary_rows: Sequence[Mapping[str, str]] = (),
    source_paths: Sequence[Path | None] = (),
) -> dict[str, Mapping[str, str]]:
    leave_one_out_by_target = {
        row["target_label"]: row for row in leave_one_out_rows
    }
    trend_summary_by_target = _istd_trend_summary_by_target(istd_trend_rows)
    phase_summary_by_target = _istd_phase_summary_by_target(istd_phase_summary_rows)
    source_artifacts = "|".join(str(path) for path in source_paths if path is not None)
    trends: dict[str, Mapping[str, str]] = {}
    for row in targeted_summary_rows:
        feature_family_id = text_value(row.get("selected_feature_id"))
        target_label = text_value(row.get("target_label"))
        if not feature_family_id or not target_label:
            continue
        leave_one_out = leave_one_out_by_target.get(target_label)
        if leave_one_out is None:
            continue
        evidence = {
            "target_label": target_label,
            "role": text_value(row.get("role")),
            "active_tag": text_value(row.get("active_tag")),
            "targeted_positive_count": text_value(
                row.get("targeted_positive_count")
            ),
            "coverage_denominator_count": text_value(
                row.get("coverage_denominator_count")
            ),
            "primary_match_count": text_value(row.get("primary_match_count")),
            "sample_rt_p95_abs_delta_min": text_value(
                row.get("sample_rt_p95_abs_delta_min")
            ),
            "leave_one_evaluated_count": text_value(
                leave_one_out.get("evaluated_count")
            ),
            "leave_one_p95_abs_error_min": text_value(
                leave_one_out.get("p95_abs_error_min")
            ),
            "leave_one_status": text_value(leave_one_out.get("status")),
            "drift_reference_artifacts": source_artifacts,
            **trend_summary_by_target.get(target_label, {}),
            **phase_summary_by_target.get(target_label, {}),
        }
        existing = trends.get(feature_family_id)
        if existing is None or _anchor_trend_rank(evidence) > _anchor_trend_rank(
            existing
        ):
            trends[feature_family_id] = evidence
    return trends


def _anchor_trend_rank(row: Mapping[str, str]) -> tuple[int, int]:
    return (
        optional_int(row.get("targeted_positive_count")) or 0,
        optional_int(row.get("leave_one_evaluated_count")) or 0,
    )


def _row_for_key(
    *,
    feature_family_id: str,
    sample_stem: str,
    cell: Mapping[str, str] | None,
    family: Mapping[str, str],
    owner_edges: Sequence[Mapping[str, str]],
    rt_normalization_family: Mapping[str, str] | None,
    targeted_anchor_trend: Mapping[str, str] | None,
    config: AlignmentConfig,
) -> dict[str, str]:
    base = _base_row(feature_family_id, sample_stem)
    if cell is None:
        return {**base, "reason": "alignment_cell_missing"}
    raw_delta = _abs_float(cell.get("rt_delta_sec"))
    if raw_delta is None:
        return {**base, "reason": "rt_delta_missing"}
    if raw_delta <= config.preferred_rt_sec:
        return {
            **base,
            "matrix_rt_drift_status": "rt_close",
            "drift_evidence_level": "family_consensus_aligned",
            "raw_rt_delta_sec": _format_float(raw_delta),
            "drift_corrected_delta_sec": _format_float(raw_delta),
            "matrix_shift_sec": "0",
            "drift_reference_count": "1",
            "drift_reference_source": "alignment_cell_rt_delta",
            "drift_compatible_status": "compatible",
            "reason": "alignment_rt_within_preferred_window",
        }

    owner_row = _owner_edge_row(
        base=base,
        cell=cell,
        family=family,
        sample_stem=sample_stem,
        raw_delta=raw_delta,
        owner_edges=owner_edges,
        config=config,
    )
    if owner_row is not None:
        return owner_row

    normalized_row = _rt_normalization_row(
        base=base,
        raw_delta=raw_delta,
        family=rt_normalization_family,
        config=config,
    )
    if normalized_row is not None:
        return normalized_row

    anchor_local_trend_row = _anchor_local_trend_row(
        base=base,
        raw_delta=raw_delta,
        trend=targeted_anchor_trend,
        config=config,
    )
    if anchor_local_trend_row is not None:
        return anchor_local_trend_row

    return {
        **base,
        "raw_rt_delta_sec": _format_float(raw_delta),
        "reason": "no_supportive_matrix_rt_drift_artifact",
    }


def _base_row(feature_family_id: str, sample_stem: str) -> dict[str, str]:
    return {
        "feature_family_id": feature_family_id,
        "sample_stem": sample_stem,
        "matrix_rt_drift_status": "inconclusive",
        "drift_evidence_level": "not_available",
        "raw_rt_delta_sec": "",
        "drift_corrected_delta_sec": "",
        "matrix_shift_sec": "",
        "drift_reference_count": "0",
        "drift_reference_source": "",
        "drift_compatible_status": "not_available",
        "reason": "",
        "diagnostic_only": "TRUE",
        "drift_reference_artifacts": "",
        "istd_trend_sample_count": "",
        "istd_trend_injection_order_span": "",
        "istd_phase_summary": "",
    }


def _owner_edge_row(
    *,
    base: Mapping[str, str],
    cell: Mapping[str, str],
    family: Mapping[str, str],
    sample_stem: str,
    raw_delta: float,
    owner_edges: Sequence[Mapping[str, str]],
    config: AlignmentConfig,
) -> dict[str, str] | None:
    matches = [
        edge
        for edge in owner_edges
        if _edge_matches_cell(
            edge=edge,
            sample_stem=sample_stem,
            cell=cell,
            family=family,
            config=config,
        )
    ]
    if not matches:
        return None
    supportive = [
        edge
        for edge in matches
        if _owner_edge_supports_drift(edge, config=config)
    ]
    if supportive:
        best = min(
            supportive,
            key=lambda edge: _optional_float(
                edge.get("rt_drift_corrected_delta_sec")
            )
            or float("inf"),
        )
        corrected = _optional_float(best.get("rt_drift_corrected_delta_sec"))
        if corrected is None:
            raise AssertionError("supportive owner edge must have corrected delta")
        drift_source = text_value(best.get("drift_prior_source"))
        return {
            **base,
            "matrix_rt_drift_status": "drift_supported",
            "drift_evidence_level": _DRIFT_SUPPORT_SOURCES[drift_source],
            "raw_rt_delta_sec": _format_float(raw_delta),
            "drift_corrected_delta_sec": _format_float(corrected),
            "matrix_shift_sec": _format_float(max(raw_delta - corrected, 0.0)),
            "drift_reference_count": str(len(matches)),
            "drift_reference_source": f"owner_edge_evidence:{drift_source}",
            "drift_compatible_status": "compatible",
            "reason": "owner_edge_drift_corrected_close",
        }
    contradictory = [
        edge
        for edge in matches
        if _owner_edge_contradicts_drift(edge, config=config)
    ]
    if contradictory:
        worst = max(
            contradictory,
            key=lambda edge: _optional_float(
                edge.get("rt_drift_corrected_delta_sec")
            )
            or 0.0,
        )
        corrected = _optional_float(worst.get("rt_drift_corrected_delta_sec"))
        return {
            **base,
            "matrix_rt_drift_status": "drift_not_supported",
            "drift_evidence_level": "matrix_reference_aligned",
            "raw_rt_delta_sec": _format_float(raw_delta),
            "drift_corrected_delta_sec": _format_float(corrected),
            "matrix_shift_sec": _format_float(
                0.0 if corrected is None else corrected - raw_delta
            ),
            "drift_reference_count": str(len(matches)),
            "drift_reference_source": "owner_edge_evidence:contradictory",
            "drift_compatible_status": "conflict",
            "reason": "owner_edge_drift_contradictory",
        }
    return None


def _edge_matches_cell(
    *,
    edge: Mapping[str, str],
    sample_stem: str,
    cell: Mapping[str, str],
    family: Mapping[str, str],
    config: AlignmentConfig,
) -> bool:
    side = _sample_side(edge, sample_stem)
    if side == "":
        return False
    family_mz = _optional_float(family.get("family_center_mz"))
    edge_mz = _optional_float(edge.get(f"{side}_precursor_mz"))
    if family_mz is None or edge_mz is None:
        return False
    if _ppm(edge_mz, family_mz) > config.max_ppm:
        return False
    cell_apex = _optional_float(cell.get("apex_rt"))
    edge_rt = _optional_float(edge.get(f"{side}_rt_min"))
    if cell_apex is None or edge_rt is None:
        return False
    return abs(cell_apex - edge_rt) * 60.0 <= max(config.owner_apex_close_sec, 1.0)


def _sample_side(edge: Mapping[str, str], sample_stem: str) -> str:
    if text_value(edge.get("left_sample_stem")) == sample_stem:
        return "left"
    if text_value(edge.get("right_sample_stem")) == sample_stem:
        return "right"
    return ""


def _owner_edge_supports_drift(
    edge: Mapping[str, str],
    *,
    config: AlignmentConfig,
) -> bool:
    corrected = _optional_float(edge.get("rt_drift_corrected_delta_sec"))
    raw = _abs_float(edge.get("rt_raw_delta_sec"))
    source = text_value(edge.get("drift_prior_source"))
    if text_value(edge.get("decision")) != "strong_edge":
        return False
    if corrected is None or raw is None or source not in _DRIFT_SUPPORT_SOURCES:
        return False
    if corrected > config.preferred_rt_sec:
        return False
    return raw - corrected >= _MIN_EXPLAINED_SHIFT_SEC


def _owner_edge_contradicts_drift(
    edge: Mapping[str, str],
    *,
    config: AlignmentConfig,
) -> bool:
    corrected = _optional_float(edge.get("rt_drift_corrected_delta_sec"))
    raw = _abs_float(edge.get("rt_raw_delta_sec"))
    source = text_value(edge.get("drift_prior_source"))
    if corrected is None or raw is None or source not in _DRIFT_SUPPORT_SOURCES:
        return False
    return corrected > raw + _MIN_EXPLAINED_SHIFT_SEC and corrected > (
        config.preferred_rt_sec
    )


def _rt_normalization_row(
    *,
    base: Mapping[str, str],
    raw_delta: float,
    family: Mapping[str, str] | None,
    config: AlignmentConfig,
) -> dict[str, str] | None:
    if family is None:
        return None
    support = text_value(family.get("normalized_rt_support"))
    modelled_count = optional_int(family.get("modelled_cell_count")) or 0
    improvement_min = _optional_float(family.get("rt_range_improvement_min"))
    normalized_range_min = _optional_float(family.get("normalized_rt_range_min"))
    if improvement_min is None or modelled_count <= 0:
        return None
    improvement_sec = improvement_min * 60.0
    corrected = (
        max(raw_delta - improvement_sec, 0.0)
        if normalized_range_min is None
        else min(max(raw_delta - improvement_sec, 0.0), normalized_range_min * 60.0)
    )
    if (
        support == "improved"
        and improvement_sec >= _MIN_EXPLAINED_SHIFT_SEC
        and corrected <= config.preferred_rt_sec
    ):
        return {
            **base,
            "matrix_rt_drift_status": "drift_supported",
            "drift_evidence_level": "matrix_reference_aligned",
            "raw_rt_delta_sec": _format_float(raw_delta),
            "drift_corrected_delta_sec": _format_float(corrected),
            "matrix_shift_sec": _format_float(max(raw_delta - corrected, 0.0)),
            "drift_reference_count": str(modelled_count),
            "drift_reference_source": "rt_normalization_families",
            "drift_compatible_status": "compatible",
            "reason": "rt_normalization_family_range_improved",
        }
    if (
        support == "worsened"
        and improvement_sec <= -_MIN_EXPLAINED_SHIFT_SEC
        and raw_delta > config.preferred_rt_sec
    ):
        corrected = raw_delta + abs(improvement_sec)
        return {
            **base,
            "matrix_rt_drift_status": "drift_not_supported",
            "drift_evidence_level": "matrix_reference_aligned",
            "raw_rt_delta_sec": _format_float(raw_delta),
            "drift_corrected_delta_sec": _format_float(corrected),
            "matrix_shift_sec": _format_float(corrected - raw_delta),
            "drift_reference_count": str(modelled_count),
            "drift_reference_source": "rt_normalization_families",
            "drift_compatible_status": "conflict",
            "reason": "rt_normalization_family_range_worsened",
        }
    return None


def _anchor_local_trend_row(
    *,
    base: Mapping[str, str],
    raw_delta: float,
    trend: Mapping[str, str] | None,
    config: AlignmentConfig,
) -> dict[str, str] | None:
    if trend is None:
        return None
    if text_value(trend.get("role")) != "ISTD":
        return None
    if text_value(trend.get("active_tag")) != "TRUE":
        return None
    if (optional_int(trend.get("primary_match_count")) or 0) != 1:
        return None
    targeted_positive_count = optional_int(trend.get("targeted_positive_count")) or 0
    coverage_denominator_count = (
        optional_int(trend.get("coverage_denominator_count")) or 0
    )
    evaluated_count = optional_int(trend.get("leave_one_evaluated_count")) or 0
    if min(
        targeted_positive_count,
        coverage_denominator_count,
        evaluated_count,
    ) < _MIN_ANCHOR_LOCAL_TREND_EVALUATED_COUNT:
        return None
    if text_value(trend.get("leave_one_status")) != "PASS":
        return None
    targeted_p95_min = _optional_float(trend.get("sample_rt_p95_abs_delta_min"))
    leave_one_p95_min = _optional_float(trend.get("leave_one_p95_abs_error_min"))
    if targeted_p95_min is None or leave_one_p95_min is None:
        return None
    corrected = max(targeted_p95_min, leave_one_p95_min) * 60.0
    if corrected > config.preferred_rt_sec:
        return None
    if raw_delta - corrected < _MIN_EXPLAINED_SHIFT_SEC:
        return None
    return {
        **base,
        "matrix_rt_drift_status": "drift_supported",
        "drift_evidence_level": "sample_istd_aligned",
        "raw_rt_delta_sec": _format_float(raw_delta),
        "drift_corrected_delta_sec": _format_float(corrected),
        "matrix_shift_sec": _format_float(max(raw_delta - corrected, 0.0)),
        "drift_reference_count": str(
            min(
                targeted_positive_count,
                coverage_denominator_count,
                evaluated_count,
            )
        ),
        "drift_reference_source": (
            "targeted_istd_benchmark+rt_normalization_leave_one_anchor_out"
        ),
        "drift_compatible_status": "compatible",
        "reason": "targeted_istd_anchor_local_trend_supported",
        "drift_reference_artifacts": text_value(
            trend.get("drift_reference_artifacts")
        ),
        "istd_trend_sample_count": text_value(trend.get("istd_trend_sample_count")),
        "istd_trend_injection_order_span": text_value(
            trend.get("istd_trend_injection_order_span")
        ),
        "istd_phase_summary": text_value(trend.get("istd_phase_summary")),
    }


def _istd_trend_summary_by_target(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    grouped: dict[str, list[Mapping[str, str]]] = {}
    for row in rows:
        target_label = text_value(row.get("target_label"))
        if not target_label:
            continue
        grouped.setdefault(target_label, []).append(row)
    summaries: dict[str, dict[str, str]] = {}
    for target_label, target_rows in grouped.items():
        orders = [
            value
            for row in target_rows
            if (value := optional_int(row.get("injection_order"))) is not None
        ]
        if orders:
            span = f"{min(orders)}-{max(orders)}"
        else:
            span = ""
        summaries[target_label] = {
            "istd_trend_sample_count": str(len(target_rows)),
            "istd_trend_injection_order_span": span,
        }
    return summaries


def _istd_phase_summary_by_target(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    grouped: dict[str, list[Mapping[str, str]]] = {}
    for row in rows:
        target_label = text_value(row.get("target_label"))
        if not target_label:
            continue
        grouped.setdefault(target_label, []).append(row)
    summaries: dict[str, dict[str, str]] = {}
    for target_label, target_rows in grouped.items():
        phase_parts = []
        for row in target_rows:
            phase = text_value(row.get("injection_phase"))
            count = text_value(row.get("sample_count"))
            median = text_value(row.get("observed_rt_median_min"))
            iqr = text_value(row.get("observed_rt_iqr_min"))
            if phase:
                phase_parts.append(
                    f"{phase}:n={count},median={median},iqr={iqr}"
                )
        summaries[target_label] = {"istd_phase_summary": "|".join(phase_parts)}
    return summaries


def _ppm(observed: float, reference: float) -> float:
    if reference == 0:
        return float("inf")
    return abs(observed - reference) / abs(reference) * 1_000_000.0


def _abs_float(value: object) -> float | None:
    parsed = _optional_float(value)
    return None if parsed is None else abs(parsed)


def _optional_float(value: object) -> float | None:
    return diagnostic_optional_float(str(value or "").strip().strip("'"))


def _format_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6g}"
