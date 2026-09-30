from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from .schema import (
    SHADOW_ALIGNMENT_SUMMARY_SCHEMA_VERSION,
    SHADOW_LABEL_SCHEMA_VERSION,
    SHADOW_READINESS_SCHEMA_VERSION,
    validate_row_tokens,
)

_DECISION_STATUSES = frozenset({"aligned", "partial", "contradicted", "unresolved"})


def build_shadow_label_rows(
    explanations: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    rows = [_shadow_label_row(row) for row in explanations]
    return tuple(
        sorted(rows, key=lambda row: (row["feature_family_id"], row["sample_id"]))
    )


def build_shadow_alignment_summary(
    shadow_rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    scopes: list[tuple[str, str, list[Mapping[str, str]]]] = [
        ("overall", "not_applicable", list(shadow_rows))
    ]
    labels = sorted({row["manual_label"] for row in shadow_rows if row["manual_label"]})
    scopes.extend(
        (
            "manual_label",
            label,
            [row for row in shadow_rows if row["manual_label"] == label],
        )
        for label in labels
    )
    return tuple(
        _summary_row(scope, manual_label, rows)
        for scope, manual_label, rows in scopes
    )


def build_shadow_readiness(
    *,
    run_facts: Mapping[str, str],
    shadow_rows: Sequence[Mapping[str, str]],
    machine_evidence_support_rows: Sequence[Mapping[str, str]] = (),
) -> dict[str, str]:
    seed_rows_total = _int_value(run_facts.get("seed_rows_total"))
    aligned_or_partial = sum(
        1
        for row in shadow_rows
        if row["shadow_alignment_status"] in {"aligned", "partial"}
    )
    contradicted = sum(
        1 for row in shadow_rows if row["shadow_alignment_status"] == "contradicted"
    )
    context_only = sum(
        1 for row in shadow_rows if row["shadow_alignment_status"] == "context_only"
    )
    unjudgeable = sum(
        1 for row in shadow_rows if row["shadow_alignment_status"] == "unjudgeable"
    )
    decision_denominator = sum(
        1 for row in shadow_rows if row["shadow_alignment_status"] in _DECISION_STATUSES
    )
    alignment_fraction = (
        aligned_or_partial / decision_denominator if decision_denominator else 0.0
    )
    machine_evidence = _machine_evidence_readiness(machine_evidence_support_rows)
    gate_status = _shadow_gate_status(
        run_facts=run_facts,
        contradicted=contradicted,
        alignment_fraction=alignment_fraction,
        machine_evidence_basis=machine_evidence["machine_evidence_basis"],
    )
    semantic_evidence = (
        "blast_radius_current"
        if gate_status == "shadow_ready_candidate"
        else "seed_only_manual_oracle_derived"
    )
    machine_ready = "TRUE" if gate_status == "shadow_ready_candidate" else "FALSE"
    row = {
        "shadow_readiness_schema_version": SHADOW_READINESS_SCHEMA_VERSION,
        "shadow_mode": "shadow_label_alignment",
        "shadow_gate_status": gate_status,
        "readiness_label": "diagnostic_only",
        "seed_rows_total": str(seed_rows_total),
        "shadow_rows_total": str(len(shadow_rows)),
        "aligned_or_partial_rows": str(aligned_or_partial),
        "contradicted_rows": str(contradicted),
        "context_only_rows": str(context_only),
        "human_unjudgeable_rows": str(unjudgeable),
        "alignment_fraction": _format_fraction(alignment_fraction),
        "blast_radius_assessed": run_facts.get("blast_radius_assessed", "not_assessed"),
        "max_overfit_risk": run_facts.get("max_overfit_risk", "unassessed"),
        "blast_radius_stale_artifact_count": run_facts.get(
            "blast_radius_stale_artifact_count", "0"
        ),
        "semantic_generalization_evidence": semantic_evidence,
        "machine_evidence_basis": machine_evidence["machine_evidence_basis"],
        "machine_evidence_supported_rows": machine_evidence[
            "machine_evidence_supported_rows"
        ],
        "machine_observed_partial_rows": machine_evidence[
            "machine_observed_partial_rows"
        ],
        "machine_observed_conflict_rows": machine_evidence[
            "machine_observed_conflict_rows"
        ],
        "machine_proxy_only_rows": machine_evidence["machine_proxy_only_rows"],
        "manual_oracle_derived_rows": machine_evidence[
            "manual_oracle_derived_rows"
        ],
        "machine_evidence_coverage_fraction": machine_evidence[
            "machine_evidence_coverage_fraction"
        ],
        "machine_evidence_blockers": machine_evidence["machine_evidence_blockers"],
        "machine_only_labeler_ready": machine_ready,
        "clear_answer": _clear_answer(
            gate_status=gate_status,
            contradicted=contradicted,
            alignment_fraction=alignment_fraction,
            run_facts=run_facts,
            machine_evidence=machine_evidence,
        ),
        "next_action": _next_action(gate_status, machine_evidence),
    }
    validate_row_tokens(row)
    return row


def _shadow_label_row(explanation: Mapping[str, str]) -> dict[str, str]:
    gap_class = explanation["evidence_gap_class"]
    manual_label = explanation["manual_label"]
    shadow_label = _shadow_label(gap_class, manual_label)
    alignment_status = _alignment_status(manual_label, shadow_label)
    row = {
        "shadow_label_schema_version": SHADOW_LABEL_SCHEMA_VERSION,
        "oracle_row_id": explanation["oracle_row_id"],
        "feature_family_id": explanation["feature_family_id"],
        "sample_id": explanation["sample_id"],
        "manual_label": manual_label,
        "manual_confidence": explanation["manual_confidence"],
        "machine_current_label": explanation["machine_current_label"],
        "machine_match_status": explanation["machine_match_status"],
        "evidence_gap_class": gap_class,
        "shadow_label": shadow_label,
        "shadow_alignment_status": alignment_status,
        "manual_machine_direction": _manual_machine_direction(gap_class),
        "evidence_chain_gap": _evidence_chain_gap(gap_class),
        "required_evidence_to_promote": _required_evidence_to_promote(gap_class),
        "diagnostic_only": "TRUE",
    }
    validate_row_tokens(row)
    return row


def _shadow_label(gap_class: str, manual_label: str) -> str:
    if gap_class == "machine_agrees_with_manual":
        return {
            "pass": "manual_like_pass_candidate",
            "suspect": "manual_like_suspect_candidate",
            "fail": "manual_like_fail_candidate",
            "human_unjudgeable": "human_unjudgeable_like",
            "not_applicable": "delta_mass_context_only",
        }.get(manual_label, "unresolved_gap")
    if gap_class == "machine_too_conservative_low_opportunity":
        return "low_opportunity_supported"
    if gap_class == "machine_too_conservative_shape_or_pattern_unmodeled":
        if manual_label == "suspect":
            return "manual_like_suspect_candidate"
        return "manual_like_pass_candidate"
    if gap_class == "machine_too_permissive_rt_pattern_conflict":
        return "rt_pattern_conflict_blocked"
    if gap_class == "machine_too_permissive_scope_rule_conflict":
        return "manual_like_fail_candidate"
    if gap_class in {"boundary_reference_ambiguous", "rt_drift_policy_gap"}:
        return "manual_like_suspect_candidate"
    if gap_class == "human_unjudgeable_shape_bad":
        return "human_unjudgeable_like"
    if gap_class == "delta_mass_related_context_only":
        return "delta_mass_context_only"
    return "unresolved_gap"


def _alignment_status(manual_label: str, shadow_label: str) -> str:
    if shadow_label == "delta_mass_context_only" or manual_label == "not_applicable":
        return "context_only"
    if manual_label == "human_unjudgeable":
        if shadow_label == "human_unjudgeable_like":
            return "unjudgeable"
        return "unresolved"
    if shadow_label == "unresolved_gap":
        return "unresolved"
    if manual_label == "pass":
        if shadow_label in {"manual_like_pass_candidate", "low_opportunity_supported"}:
            return "aligned"
        if shadow_label == "manual_like_suspect_candidate":
            return "partial"
        return "contradicted"
    if manual_label == "suspect":
        if shadow_label == "manual_like_suspect_candidate":
            return "aligned"
        if shadow_label in {
            "manual_like_pass_candidate",
            "low_opportunity_supported",
            "rt_pattern_conflict_blocked",
        }:
            return "partial"
        return "contradicted"
    if manual_label == "fail":
        if shadow_label in {
            "manual_like_fail_candidate",
            "rt_pattern_conflict_blocked",
        }:
            return "aligned"
        if shadow_label == "manual_like_suspect_candidate":
            return "partial"
        return "contradicted"
    return "unresolved"


def _manual_machine_direction(gap_class: str) -> str:
    if gap_class == "machine_agrees_with_manual":
        return "machine_agrees"
    if gap_class.startswith("machine_too_conservative"):
        return "machine_too_conservative"
    if gap_class.startswith("machine_too_permissive"):
        return "machine_too_permissive"
    if gap_class in {"boundary_reference_ambiguous", "rt_drift_policy_gap"}:
        return "ambiguous_policy"
    if gap_class in {"human_unjudgeable_shape_bad", "delta_mass_related_context_only"}:
        return "context_only"
    return "unresolved"


def _evidence_chain_gap(gap_class: str) -> str:
    return {
        "machine_agrees_with_manual": "none",
        "machine_too_conservative_low_opportunity": "opportunity_metric_missing",
        "machine_too_conservative_shape_or_pattern_unmodeled": (
            "shape_pattern_metric_missing"
        ),
        "machine_too_permissive_rt_pattern_conflict": (
            "rt_pattern_conflict_not_blocking"
        ),
        "machine_too_permissive_scope_rule_conflict": "manual_scope_rule_not_modeled",
        "boundary_reference_ambiguous": "boundary_reference_policy_missing",
        "rt_drift_policy_gap": "rt_drift_policy_missing",
        "human_unjudgeable_shape_bad": "human_unjudgeable_shape_context",
        "delta_mass_related_context_only": "delta_mass_family_model_missing",
    }.get(gap_class, "manual_evidence_not_represented")


def _required_evidence_to_promote(gap_class: str) -> str:
    return {
        "machine_agrees_with_manual": "none",
        "machine_too_conservative_low_opportunity": (
            "intensity_opportunity_metric;dda_opportunity_policy"
        ),
        "machine_too_conservative_shape_or_pattern_unmodeled": (
            "formal_shape_metric;formal_pattern_metric"
        ),
        "machine_too_permissive_rt_pattern_conflict": (
            "rt_pattern_conflict_gate;candidate_aligned_ms2_pattern"
        ),
        "machine_too_permissive_scope_rule_conflict": (
            "manual_scope_policy;sample_level_negative_evidence"
        ),
        "boundary_reference_ambiguous": "boundary_reference_policy",
        "rt_drift_policy_gap": "matrix_rt_drift_policy",
        "human_unjudgeable_shape_bad": "human_review_or_retire_from_training",
        "delta_mass_related_context_only": "delta_mass_family_model",
    }.get(gap_class, "new_machine_evidence")


def _summary_row(
    scope: str,
    manual_label: str,
    rows: Sequence[Mapping[str, str]],
) -> dict[str, str]:
    status_counts = Counter(row["shadow_alignment_status"] for row in rows)
    decision_denominator = sum(
        1 for row in rows if row["shadow_alignment_status"] in _DECISION_STATUSES
    )
    aligned_or_partial = status_counts["aligned"] + status_counts["partial"]
    alignment_fraction = (
        aligned_or_partial / decision_denominator if decision_denominator else 0.0
    )
    gap_counts = Counter(row["evidence_gap_class"] for row in rows)
    dominant_gaps = ";".join(
        gap for gap, _count in gap_counts.most_common(3)
    )
    row = {
        "shadow_summary_schema_version": SHADOW_ALIGNMENT_SUMMARY_SCHEMA_VERSION,
        "scope": scope,
        "manual_label": manual_label,
        "row_count": str(len(rows)),
        "aligned_count": str(status_counts["aligned"]),
        "partial_count": str(status_counts["partial"]),
        "contradicted_count": str(status_counts["contradicted"]),
        "unjudgeable_count": str(status_counts["unjudgeable"]),
        "context_only_count": str(status_counts["context_only"]),
        "unresolved_count": str(status_counts["unresolved"]),
        "alignment_fraction": _format_fraction(alignment_fraction),
        "dominant_gap_classes": dominant_gaps,
        "recommended_next_action": _summary_next_action(status_counts),
    }
    validate_row_tokens(row)
    return row


def _summary_next_action(status_counts: Counter[str]) -> str:
    if status_counts["contradicted"]:
        return "inspect_ms2_pattern"
    if status_counts["unresolved"]:
        return "flag_for_shadow_gate_review"
    if status_counts["partial"]:
        return "add_shape_metric"
    return "no_action"


def _shadow_gate_status(
    *,
    run_facts: Mapping[str, str],
    contradicted: int,
    alignment_fraction: float,
    machine_evidence_basis: str,
) -> str:
    if (
        run_facts.get("vocabulary_special_casing_detected") == "TRUE"
        or _int_value(run_facts.get("seed_rows_unexplained")) > 0
        or _int_value(run_facts.get("seed_rows_inconclusive")) > 0
    ):
        return "blocked_by_vocabulary"
    if run_facts.get("max_overfit_risk") in {"medium", "high"}:
        return "blocked_by_overfit_risk"
    if (
        run_facts.get("blast_radius_assessed") == "present_current"
        and run_facts.get("max_overfit_risk") == "low"
        and _int_value(run_facts.get("blast_radius_stale_artifact_count")) == 0
        and contradicted == 0
        and alignment_fraction >= 0.9
        and machine_evidence_basis == "machine_observed_sufficient"
    ):
        return "shadow_ready_candidate"
    return "exploratory_only"


def _clear_answer(
    *,
    gate_status: str,
    contradicted: int,
    alignment_fraction: float,
    run_facts: Mapping[str, str],
    machine_evidence: Mapping[str, str],
) -> str:
    if gate_status == "shadow_ready_candidate":
        return (
            "Shadow completed as shadow_ready_candidate: seed shadow labels align with "
            "manual semantics and current blast-radius facts are low risk; this is "
            "still diagnostic_only and not production promotion."
        )
    blockers: list[str] = []
    if run_facts.get("blast_radius_assessed") != "present_current":
        blockers.append("blast_radius_not_current")
    if run_facts.get("max_overfit_risk") == "unassessed":
        blockers.append("overfit_risk_unassessed")
    if contradicted:
        blockers.append("seed_shadow_contradictions")
    if alignment_fraction < 0.9:
        blockers.append("seed_alignment_fraction_below_0_9")
    if machine_evidence.get("machine_evidence_basis") != "machine_observed_sufficient":
        blockers.append("machine_evidence_not_sufficient")
    return (
        "Shadow completed as exploratory_only: shadow labels can organize the manual "
        "seed semantics, but the machine-only evidence chain is not ready for "
        "autonomous pass/fail. Blockers: "
        + ";".join(blockers or ["generalization_not_established"])
        + "."
    )


def _next_action(gate_status: str, machine_evidence: Mapping[str, str]) -> str:
    if gate_status == "shadow_ready_candidate":
        return "plan_machine_label_shadow_validation"
    if gate_status == "blocked_by_vocabulary":
        return "revise_vocabulary_or_oracle_before"
    if gate_status == "blocked_by_overfit_risk":
        return "revise_vocabulary_or_expand_evidence_chain"
    blockers = set(machine_evidence.get("machine_evidence_blockers", "").split(";"))
    if (
        "formal_pattern_metric" in blockers
        or "candidate_aligned_ms2_pattern" in blockers
    ):
        return "add_ms1_or_ms2_pattern_metric_then_rerun"
    if "matrix_rt_drift_policy" in blockers:
        return "add_matrix_rt_drift_policy_then_rerun"
    if "dda_opportunity_policy" in blockers:
        return "add_dda_opportunity_policy_then_rerun"
    if "shape_metric_not_supportive" in blockers:
        return "calibrate_cwt_shape_metric_against_manual_rows"
    return "add_shape_pattern_opportunity_metrics_then_rerun"


def _machine_evidence_readiness(
    support_rows: Sequence[Mapping[str, str]],
) -> dict[str, str]:
    decision_rows = [
        row
        for row in support_rows
        if row.get("evidence_support_status") not in {"context_only", "not_evaluable"}
    ]
    supported = sum(
        1
        for row in decision_rows
        if row.get("evidence_support_status") == "machine_observed_sufficient"
    )
    observed_partial = sum(
        1
        for row in decision_rows
        if row.get("evidence_support_status") == "machine_observed_partial"
    )
    observed_conflict = sum(
        1
        for row in decision_rows
        if row.get("evidence_support_status") == "machine_observed_conflict"
    )
    proxy_only = sum(
        1
        for row in decision_rows
        if row.get("evidence_support_status")
        in {"machine_proxy_only", "blocked_missing_metric"}
    )
    manual_derived = sum(
        1
        for row in decision_rows
        if row.get("evidence_support_status") == "manual_derived_only"
        or row.get("shape_basis_status") in {"manual_oracle_derived", "mixed"}
        or row.get("pattern_basis_status") in {"manual_oracle_derived", "mixed"}
        or row.get("opportunity_basis_status") in {"manual_oracle_derived", "mixed"}
        or row.get("scope_basis_status") in {"manual_oracle_derived", "mixed"}
    )
    coverage_fraction = supported / len(decision_rows) if decision_rows else 0.0
    blockers = _support_blockers(decision_rows)
    basis = "not_assessed"
    if support_rows:
        if supported == len(decision_rows) and decision_rows:
            basis = "machine_observed_sufficient"
        elif observed_partial or observed_conflict:
            basis = "machine_observed_partial"
        else:
            basis = "machine_proxy_or_manual_derived"
    return {
        "machine_evidence_basis": basis,
        "machine_evidence_supported_rows": str(supported),
        "machine_observed_partial_rows": str(observed_partial),
        "machine_observed_conflict_rows": str(observed_conflict),
        "machine_proxy_only_rows": str(proxy_only),
        "manual_oracle_derived_rows": str(manual_derived),
        "machine_evidence_coverage_fraction": _format_fraction(coverage_fraction),
        "machine_evidence_blockers": blockers,
    }


def _support_blockers(rows: Sequence[Mapping[str, str]]) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for token in str(row.get("missing_machine_evidence", "")).split(";"):
            if token and token not in seen:
                tokens.append(token)
                seen.add(token)
    return ";".join(tokens)


def _format_fraction(value: float) -> str:
    return f"{value:.6f}"


def _int_value(value: str | None) -> int:
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0
