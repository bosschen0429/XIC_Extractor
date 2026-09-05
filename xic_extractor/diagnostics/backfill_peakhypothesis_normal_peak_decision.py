"""PeakHypothesis normal-peak backfill decision surface.

This diagnostic turns reviewed PeakHypothesis promotion rows plus FULL COHORT
same-peak review evidence into an explicit normal-peak decision. It does not
write matrices or mutate alignment artifacts.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.diagnostics.diagnostic_io import (
    format_diagnostic_value,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "backfill_peakhypothesis_normal_peak_decision_v1"
NORMAL_PEAK_SHAPE_DEFINITION = (
    "gaussian15_asls_residual_selected_shape_context_single_complete_unimodal_peak;"
    "raw_spikes_neighbor_contact_family_multiplet_not_blockers"
)

DECISION_COLUMNS = (
    "schema_version",
    "source_run_id",
    "peak_hypothesis_id",
    "activation_unit_scope",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
    "area_policy",
    "matrix_quantitative_use",
    "promotion_decision",
    "full_cohort_matched_peak_hypothesis_id",
    "full_cohort_cell_status",
    "full_cohort_primary_matrix_area",
    "full_cohort_primary_matrix_area_source",
    "normal_peak_quantitation_area",
    "normal_peak_quantitation_area_source",
    "normal_peak_quantitation_area_source_field",
    "normal_peak_quantitation_boundary_start_rt",
    "normal_peak_quantitation_boundary_end_rt",
    "normal_peak_quantitation_boundary_source",
    "full_cohort_include_in_primary_matrix",
    "full_cohort_consolidation_state",
    "manual_same_peak_verdict",
    "same_peak_verdict",
    "same_peak_verdict_source",
    "normal_peak_shape_definition",
    "normal_peak_decision",
    "normal_peak_backfill_required",
    "normal_peak_decision_reasons",
    "normal_peak_decision_blockers",
    "consolidation_policy_effect",
)

PROMOTION_REQUIRED_COLUMNS = (
    "peak_hypothesis_id",
    "activation_unit_scope",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
    "promotion_decision",
    "promotion_blockers",
    "area_policy",
    "matrix_quantitative_use",
    "projected_matrix_value",
)

FULL_COHORT_SLICE_REQUIRED_COLUMNS = (
    "peak_hypothesis_id",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
    "full_cohort_matched_peak_hypothesis_id",
    "full_cohort_cell_status",
    "full_cohort_primary_matrix_area",
    "full_cohort_primary_matrix_area_source",
    "full_cohort_include_in_primary_matrix",
    "full_cohort_consolidation_state",
    "full_cohort_acceptance_gate_status",
    "full_cohort_slice_blockers",
)

MANUAL_VERDICT_REQUIRED_COLUMNS = (
    "source_peak_hypothesis_id",
    "sample_stem",
    "full_cohort_matched_peak_hypothesis_id",
    "reviewer_verdict",
)

MACHINE_SHAPE_REQUIRED_COLUMNS = (
    "source_peak_hypothesis_id",
    "sample_stem",
    "full_cohort_matched_peak_hypothesis_id",
    "machine_shape_decision",
    "machine_shape_reasons",
    "machine_shape_blockers",
    "gaussian15_selected_segment_peak_count",
    "gaussian15_lobe_start_rt",
    "gaussian15_lobe_end_rt",
    "gaussian15_lobe_area",
    "gaussian15_lobe_area_source",
    "gaussian15_lobe_boundary_source",
    "machine_same_peak_verdict",
    "machine_same_peak_reasons",
    "machine_same_peak_blockers",
)

_ALLOWED_CONSOLIDATION_BLOCKERS = {
    "full_cohort_candidate_not_primary_matrix_row",
    "full_cohort_candidate_family_consolidation_review_required",
}
_GAUSSIAN15_AREA_SOURCE = "gaussian15_positive_asls_residual"


@dataclass(frozen=True)
class NormalPeakDecisionIndex:
    rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


@dataclass(frozen=True)
class NormalPeakDecisionOutputs:
    decisions_tsv: Path
    summary_json: Path


def build_normal_peak_decision_index(
    *,
    promotion_rows: Sequence[Mapping[str, Any]],
    full_cohort_acceptance_gate_rows: Sequence[Mapping[str, Any]],
    manual_verdict_rows: Sequence[Mapping[str, Any]],
    machine_shape_rows: Sequence[Mapping[str, Any]] = (),
    source_run_id: str = "",
) -> NormalPeakDecisionIndex:
    full_cohort_by_key = {
        _promotion_key(row): row for row in full_cohort_acceptance_gate_rows
    }
    manual_by_key = {_manual_key(row): row for row in manual_verdict_rows}
    shape_by_key = {_machine_shape_key(row): row for row in machine_shape_rows}
    rows = tuple(
        _decision_row(
            promotion_row=promotion_row,
            full_cohort_row=full_cohort_by_key.get(_promotion_key(promotion_row), {}),
            manual_by_key=manual_by_key,
            machine_shape_by_key=shape_by_key,
            machine_shape_required=bool(machine_shape_rows),
            source_run_id=source_run_id,
        )
        for promotion_row in promotion_rows
    )
    return NormalPeakDecisionIndex(
        rows=rows,
        summary=_summary(rows, source_run_id=source_run_id),
    )


def write_normal_peak_decision_outputs(
    output_dir: Path,
    index: NormalPeakDecisionIndex,
) -> NormalPeakDecisionOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    decisions_tsv = output_dir / "backfill_peakhypothesis_normal_peak_decisions.tsv"
    summary_json = (
        output_dir / "backfill_peakhypothesis_normal_peak_decision_summary.json"
    )
    write_tsv(
        decisions_tsv,
        index.rows,
        DECISION_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(index.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return NormalPeakDecisionOutputs(
        decisions_tsv=decisions_tsv,
        summary_json=summary_json,
    )


def _decision_row(
    *,
    promotion_row: Mapping[str, Any],
    full_cohort_row: Mapping[str, Any],
    manual_by_key: Mapping[tuple[str, str, str], Mapping[str, Any]],
    machine_shape_by_key: Mapping[tuple[str, str, str], Mapping[str, Any]],
    machine_shape_required: bool,
    source_run_id: str,
) -> dict[str, Any]:
    full_cohort_id = _value(full_cohort_row, "full_cohort_matched_peak_hypothesis_id")
    manual = manual_by_key.get(
        (
            _value(promotion_row, "peak_hypothesis_id"),
            _value(promotion_row, "sample_stem"),
            full_cohort_id,
        ),
        {},
    )
    machine_shape = machine_shape_by_key.get(
        (
            _value(promotion_row, "peak_hypothesis_id"),
            _value(promotion_row, "sample_stem"),
            full_cohort_id,
        ),
        {},
    )
    decision, required, reasons, blockers, consolidation_effect = _classify(
        promotion_row=promotion_row,
        full_cohort_row=full_cohort_row,
        manual_row=manual,
        machine_shape_row=machine_shape,
        machine_shape_required=machine_shape_required,
    )
    resolved_area_policy = _resolved_area_policy(promotion_row, machine_shape)
    quantitation_area = _normal_peak_quantitation_area(
        full_cohort_row,
        machine_shape,
        machine_shape_required=machine_shape_required,
    )
    same_peak = _same_peak_verdict(
        manual_row=manual,
        machine_shape_row=machine_shape,
        machine_shape_required=machine_shape_required,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "peak_hypothesis_id": _value(promotion_row, "peak_hypothesis_id"),
        "activation_unit_scope": _value(promotion_row, "activation_unit_scope"),
        "feature_family_id": _value(promotion_row, "feature_family_id"),
        "seed_group_id": _value(promotion_row, "seed_group_id"),
        "sample_stem": _value(promotion_row, "sample_stem"),
        "area_policy": resolved_area_policy,
        "matrix_quantitative_use": _value(
            promotion_row,
            "matrix_quantitative_use",
        ),
        "promotion_decision": _value(promotion_row, "promotion_decision"),
        "full_cohort_matched_peak_hypothesis_id": full_cohort_id,
        "full_cohort_cell_status": _value(full_cohort_row, "full_cohort_cell_status"),
        "full_cohort_primary_matrix_area": _value(
            full_cohort_row,
            "full_cohort_primary_matrix_area",
        ),
        "full_cohort_primary_matrix_area_source": _value(
            full_cohort_row,
            "full_cohort_primary_matrix_area_source",
        ),
        "normal_peak_quantitation_area": quantitation_area["area"],
        "normal_peak_quantitation_area_source": quantitation_area["area_source"],
        "normal_peak_quantitation_area_source_field": quantitation_area[
            "area_source_field"
        ],
        "normal_peak_quantitation_boundary_start_rt": quantitation_area[
            "boundary_start_rt"
        ],
        "normal_peak_quantitation_boundary_end_rt": quantitation_area[
            "boundary_end_rt"
        ],
        "normal_peak_quantitation_boundary_source": quantitation_area[
            "boundary_source"
        ],
        "full_cohort_include_in_primary_matrix": _value(
            full_cohort_row,
            "full_cohort_include_in_primary_matrix",
        ),
        "full_cohort_consolidation_state": _value(
            full_cohort_row,
            "full_cohort_consolidation_state",
        ),
        "manual_same_peak_verdict": _value(manual, "reviewer_verdict"),
        "same_peak_verdict": same_peak["verdict"],
        "same_peak_verdict_source": same_peak["source"],
        "normal_peak_shape_definition": NORMAL_PEAK_SHAPE_DEFINITION,
        "normal_peak_decision": decision,
        "normal_peak_backfill_required": required,
        "normal_peak_decision_reasons": ";".join(reasons),
        "normal_peak_decision_blockers": ";".join(blockers),
        "consolidation_policy_effect": consolidation_effect,
    }


def _classify(
    *,
    promotion_row: Mapping[str, Any],
    full_cohort_row: Mapping[str, Any],
    manual_row: Mapping[str, Any],
    machine_shape_row: Mapping[str, Any],
    machine_shape_required: bool,
) -> tuple[str, bool, tuple[str, ...], tuple[str, ...], str]:
    area_policy = _resolved_area_policy(promotion_row, machine_shape_row)
    if area_policy == "nonstandard_assessable_area":
        return (
            "review_only_nonstandard_peak",
            False,
            (),
            ("nonstandard_peak_out_of_goal_scope",),
            "not_applicable_nonstandard_peak",
        )
    blockers = _normal_peak_blockers(
        promotion_row,
        full_cohort_row,
        manual_row,
        machine_shape_row,
        machine_shape_required=machine_shape_required,
    )
    if blockers:
        return ("blocked", False, (), tuple(blockers), "")
    consolidation_override = _has_only_allowed_consolidation_blockers(full_cohort_row)
    reasons = [
        "standard_peak_same_peak_supported",
        "positive_gaussian15_area",
    ]
    if machine_shape_required:
        reasons.append("machine_gaussian15_standard_peak_shape_supported")
        reasons.append("machine_gaussian15_lobe_area_selected")
        same_peak = _same_peak_verdict(
            manual_row=manual_row,
            machine_shape_row=machine_shape_row,
            machine_shape_required=machine_shape_required,
        )
        if same_peak["source"] == "machine_gaussian15_trace":
            reasons.append("machine_same_peak_supported")
    effect = "not_needed_primary_candidate"
    if consolidation_override:
        reasons.append("consolidation_not_blocking_normal_peak")
        effect = "allow_same_peak_peakhypothesis_candidate_despite_non_primary"
    return ("require_backfill", True, tuple(reasons), (), effect)


def _normal_peak_blockers(
    promotion_row: Mapping[str, Any],
    full_cohort_row: Mapping[str, Any],
    manual_row: Mapping[str, Any],
    machine_shape_row: Mapping[str, Any],
    *,
    machine_shape_required: bool,
) -> list[str]:
    blockers: list[str] = []
    if (
        _resolved_area_policy(promotion_row, machine_shape_row)
        != "standard_assessable_area"
    ):
        blockers.append("normal_peak_area_policy_not_standard")
    if _value(promotion_row, "matrix_quantitative_use") != "standard_quantitative_use":
        blockers.append("normal_peak_quantitative_use_not_standard")
    if _value(promotion_row, "promotion_decision") != "promote_matrix_write":
        blockers.append("promotion_not_marked_for_matrix_write")
    if _value(promotion_row, "promotion_blockers"):
        blockers.append("promotion_blockers_present")
    if not full_cohort_row:
        blockers.append("missing_full_cohort_acceptance_gate_row")
    if _value(full_cohort_row, "full_cohort_cell_status") not in {
        "detected",
        "rescued",
    }:
        blockers.append("full_cohort_cell_not_detected_or_rescued")
    if (
        machine_shape_required
        and _value(machine_shape_row, "machine_shape_decision")
        == "standard_peak_shape_supported"
    ):
        if not _positive_number(_value(machine_shape_row, "gaussian15_lobe_area")):
            blockers.append("machine_gaussian15_lobe_area_not_positive")
        elif _value(machine_shape_row, "gaussian15_lobe_area_source") != (
            _GAUSSIAN15_AREA_SOURCE
        ):
            blockers.append("machine_gaussian15_lobe_area_source_not_gaussian15")
        boundary_source = _value(machine_shape_row, "gaussian15_lobe_boundary_source")
        if boundary_source and boundary_source != "baseline_return":
            blockers.append("machine_gaussian15_lobe_boundary_not_baseline_return")
    else:
        if _value(full_cohort_row, "full_cohort_primary_matrix_area_source") != (
            _GAUSSIAN15_AREA_SOURCE
        ):
            blockers.append("full_cohort_area_source_not_gaussian15")
        if not _positive_number(
            _value(full_cohort_row, "full_cohort_primary_matrix_area")
        ):
            blockers.append("full_cohort_area_not_positive")
    same_peak = _same_peak_verdict(
        manual_row=manual_row,
        machine_shape_row=machine_shape_row,
        machine_shape_required=machine_shape_required,
    )
    if same_peak["verdict"] != "same_peak_supported":
        blockers.append("same_peak_not_supported")
    if same_peak["source"] == "machine_gaussian15_trace" and _value(
        machine_shape_row,
        "machine_same_peak_blockers",
    ):
        blockers.append("machine_same_peak_blockers_present")
    if machine_shape_required:
        shape_decision = _value(machine_shape_row, "machine_shape_decision")
        if not machine_shape_row:
            blockers.append("machine_shape_evidence_missing")
        elif shape_decision != "standard_peak_shape_supported":
            blockers.append("machine_standard_peak_shape_not_supported")
        if _value(machine_shape_row, "machine_shape_blockers"):
            blockers.append("machine_shape_blockers_present")
    disallowed = set(
        _semicolon_values(_value(full_cohort_row, "full_cohort_slice_blockers"))
    )
    disallowed -= _ALLOWED_CONSOLIDATION_BLOCKERS
    if disallowed:
        blockers.append("full_cohort_disallowed_slice_blockers_present")
    return blockers


def _summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_run_id: str,
) -> dict[str, Any]:
    decisions = Counter(_value(row, "normal_peak_decision") for row in rows)
    normal_count = sum(
        1 for row in rows if _value(row, "area_policy") == "standard_assessable_area"
    )
    required_count = decisions.get("require_backfill", 0)
    blocked_count = decisions.get("blocked", 0)
    review_only_count = decisions.get("review_only_nonstandard_peak", 0)
    consolidation_override_count = sum(
        1
        for row in rows
        if _value(row, "consolidation_policy_effect")
        == "allow_same_peak_peakhypothesis_candidate_despite_non_primary"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "row_count": len(rows),
        "normal_peak_candidate_count": normal_count,
        "required_backfill_count": required_count,
        "review_only_nonstandard_count": review_only_count,
        "blocked_count": blocked_count,
        "consolidation_override_count": consolidation_override_count,
        "decision_counts": dict(sorted(decisions.items())),
        "normal_peak_policy_status": _policy_status(
            normal_count=normal_count,
            required_count=required_count,
            blocked_count=blocked_count,
        ),
        "normal_peak_shape_definition": NORMAL_PEAK_SHAPE_DEFINITION,
        "matrix_contract_changed": False,
        "product_behavior_changed": False,
        "next_action": (
            "wire_normal_peak_decisions_into_activation_or_matrix_writer"
            if required_count
            else "review_normal_peak_decision_blockers"
        ),
    }


def _policy_status(
    *,
    normal_count: int,
    required_count: int,
    blocked_count: int,
) -> str:
    if normal_count and required_count == normal_count and blocked_count == 0:
        return "normal_peak_backfill_required_all_reviewed_candidates"
    if blocked_count:
        return "normal_peak_backfill_blocked_or_incomplete"
    return "no_normal_peak_candidates"


def _promotion_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        _value(row, "peak_hypothesis_id"),
        _value(row, "feature_family_id"),
        _value(row, "seed_group_id"),
        _value(row, "sample_stem"),
    )


def _manual_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _value(row, "source_peak_hypothesis_id"),
        _value(row, "sample_stem"),
        _value(row, "full_cohort_matched_peak_hypothesis_id"),
    )


def _machine_shape_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _value(row, "source_peak_hypothesis_id"),
        _value(row, "sample_stem"),
        _value(row, "full_cohort_matched_peak_hypothesis_id"),
    )


def _resolved_area_policy(
    promotion_row: Mapping[str, Any],
    machine_shape_row: Mapping[str, Any],
) -> str:
    shape_decision = _value(machine_shape_row, "machine_shape_decision")
    if shape_decision == "standard_peak_shape_supported":
        return "standard_assessable_area"
    if shape_decision == "nonstandard_peak_shape":
        return "nonstandard_assessable_area"
    return _value(promotion_row, "area_policy")


def _same_peak_verdict(
    *,
    manual_row: Mapping[str, Any],
    machine_shape_row: Mapping[str, Any],
    machine_shape_required: bool,
) -> dict[str, str]:
    manual_verdict = _value(manual_row, "reviewer_verdict")
    if manual_verdict:
        return {"verdict": manual_verdict, "source": "manual_review"}
    machine_verdict = _value(machine_shape_row, "machine_same_peak_verdict")
    if machine_shape_required and machine_verdict:
        return {
            "verdict": machine_verdict,
            "source": "machine_gaussian15_trace",
        }
    return {"verdict": "", "source": ""}


def _normal_peak_quantitation_area(
    full_cohort_row: Mapping[str, Any],
    machine_shape_row: Mapping[str, Any],
    *,
    machine_shape_required: bool,
) -> dict[str, str]:
    if (
        machine_shape_required
        and _value(machine_shape_row, "machine_shape_decision")
        == "standard_peak_shape_supported"
    ):
        return {
            "area": _value(machine_shape_row, "gaussian15_lobe_area"),
            "area_source": _value(machine_shape_row, "gaussian15_lobe_area_source"),
            "area_source_field": "gaussian15_lobe_area",
            "boundary_start_rt": _value(machine_shape_row, "gaussian15_lobe_start_rt"),
            "boundary_end_rt": _value(machine_shape_row, "gaussian15_lobe_end_rt"),
            "boundary_source": _value(
                machine_shape_row,
                "gaussian15_lobe_boundary_source",
            ),
        }
    return {
        "area": _value(full_cohort_row, "full_cohort_primary_matrix_area"),
        "area_source": _value(
            full_cohort_row, "full_cohort_primary_matrix_area_source"
        ),
        "area_source_field": "full_cohort_primary_matrix_area",
        "boundary_start_rt": "",
        "boundary_end_rt": "",
        "boundary_source": "",
    }


def _has_only_allowed_consolidation_blockers(row: Mapping[str, Any]) -> bool:
    blockers = set(_semicolon_values(_value(row, "full_cohort_slice_blockers")))
    return bool(blockers) and blockers <= _ALLOWED_CONSOLIDATION_BLOCKERS


def _positive_number(value: str) -> bool:
    try:
        parsed = float(value)
    except ValueError:
        return False
    return math.isfinite(parsed) and parsed > 0


def _semicolon_values(value: str) -> tuple[str, ...]:
    return tuple(part for part in text_value(value).split(";") if part)


def _value(row: Mapping[str, Any], column: str) -> str:
    return text_value(row.get(column, ""))
