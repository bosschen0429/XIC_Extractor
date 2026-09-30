from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from .oracle import ManualOracleRow
from .schema import (
    EXPLANATION_SCHEMA_VERSION,
    RUN_FACTS_SCHEMA_VERSION,
    validate_row_tokens,
)

_REQUIRED_BLAST_RADIUS_SURFACE_IDS = (
    "fixture_cohort_alignment_review",
    "fixture_cohort_alignment_cells",
    "full_cohort_alignment_review",
    "full_cohort_alignment_cells",
)

_RISK_SEVERITY = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "unassessed": -1,
}

_BLAST_RADIUS_RISK_SCOPES = frozenset(
    {
        "non_seed_same_family",
        "all_available_fixture_cohort",
        "all_available_full_cohort",
        "overall",
    }
)


def classify_explanations(
    oracle_rows: Sequence[ManualOracleRow],
    evidence_rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    evidence_by_oracle: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in evidence_rows:
        evidence_by_oracle[row["oracle_row_id"]].append(row)
    explanations = [
        _classify_one(oracle, tuple(evidence_by_oracle.get(oracle.oracle_row_id, ())))
        for oracle in oracle_rows
    ]
    return tuple(
        sorted(
            explanations,
            key=lambda row: (row["feature_family_id"], row["sample_id"]),
        )
    )


def build_slice0_run_facts(
    explanations: Sequence[Mapping[str, str]],
    *,
    durable_oracle_path: Path,
    durable_oracle_sha256: str | None = None,
) -> dict[str, str]:
    decision_rows = [
        row for row in explanations if row.get("manual_label") != "not_applicable"
    ]
    explained = [
        row for row in decision_rows if row.get("explanation_status") == "explained"
    ]
    unexplained = [
        row for row in decision_rows if row.get("explanation_status") == "unexplained"
    ]
    inconclusive = [
        row for row in decision_rows if row.get("explanation_status") == "inconclusive"
    ]
    has_unexplained_gap = any(
        row.get("evidence_gap_class") == "unexplained_machine_manual_gap"
        for row in decision_rows
    )
    return {
        "run_facts_schema_version": RUN_FACTS_SCHEMA_VERSION,
        "slice": "slice0",
        "seed_rows_total": str(len(decision_rows)),
        "seed_rows_explained": str(len(explained)),
        "seed_rows_unexplained": str(len(unexplained)),
        "seed_rows_inconclusive": str(len(inconclusive)),
        "vocabulary_special_casing_detected": "TRUE"
        if has_unexplained_gap
        else "FALSE",
        "blast_radius_assessed": "not_run_slice0",
        "blast_radius_stale_artifact_count": "0",
        "max_overfit_risk": "unassessed",
        "durable_oracle_path": str(durable_oracle_path),
        "durable_oracle_sha256": durable_oracle_sha256
        or _sha256_file(durable_oracle_path),
    }


def build_slice1_run_facts(
    *,
    slice0_run_facts: Mapping[str, str],
    manifest_rows: Sequence[Mapping[str, str]],
    summary_rows: Sequence[Mapping[str, str]],
) -> dict[str, str]:
    return {
        **slice0_run_facts,
        "slice": "slice1",
        "blast_radius_assessed": _blast_radius_assessment_status(manifest_rows),
        "blast_radius_stale_artifact_count": str(
            sum(
                1
                for row in manifest_rows
                if row.get("artifact_status") == "present_stale_hash_mismatch"
            )
        ),
        "max_overfit_risk": _max_overfit_risk(summary_rows),
    }


def _classify_one(
    oracle: ManualOracleRow,
    evidence_rows: Sequence[Mapping[str, str]],
) -> dict[str, str]:
    tags = set(oracle.manual_reason_tags)
    sample_evidence = [
        row for row in evidence_rows if row.get("source_role") == "rescued_cell"
    ]
    matched_source_row_ids = tuple(
        row["source_row_id"]
        for row in evidence_rows
        if row.get("source_role") != "manual_oracle" and row.get("source_row_id")
    )
    machine_match_status = (
        "not_applicable"
        if oracle.is_sentinel
        else _match_status_from_evidence(sample_evidence)
    )
    machine_row = sample_evidence[0] if sample_evidence else {}
    evidence_gap_class = _evidence_gap_class(oracle, tags, sample_evidence)
    status = (
        "explained"
        if evidence_gap_class != "unexplained_machine_manual_gap"
        else "unexplained"
    )
    row = {
        "explanation_schema_version": EXPLANATION_SCHEMA_VERSION,
        "oracle_row_id": oracle.oracle_row_id,
        "feature_family_id": oracle.feature_family_id,
        "sample_id": oracle.sample_id,
        "manual_label": oracle.manual_label,
        "manual_label_source": oracle.data["manual_label_source"],
        "manual_confidence": oracle.data["manual_confidence"],
        "manual_scope": oracle.manual_scope,
        "manual_reason_tags": oracle.data["manual_reason_tags"],
        "machine_current_label": "not_applicable"
        if oracle.is_sentinel
        else machine_row.get("machine_current_label", "not_available"),
        "machine_reason": machine_row.get("machine_reason", ""),
        "machine_match_status": machine_match_status,
        "matched_source_row_ids": ";".join(matched_source_row_ids),
        "machine_source_role": "not_applicable"
        if oracle.is_sentinel
        else machine_row.get("source_role", "not_available"),
        "machine_blockers": _join_unique(
            row.get("machine_blockers", "") for row in evidence_rows
        ),
        "evidence_gap_class": evidence_gap_class,
        "secondary_gap_tags": _secondary_tags(tags),
        "explanation_status": status,
        "smallest_missing_fact": _smallest_missing_fact(evidence_gap_class, status),
        "recommended_next_action": _next_action(evidence_gap_class),
        "source_roles_seen": _join_unique(
            row.get("source_role", "") for row in evidence_rows
        ),
        "source_artifacts": _join_unique(
            row.get("source_artifact", "") for row in evidence_rows
        ),
    }
    validate_row_tokens(row)
    return row


def _match_status_from_evidence(sample_evidence: Sequence[Mapping[str, str]]) -> str:
    if not sample_evidence:
        return "no_match"
    if len(sample_evidence) == 1:
        return "single_match"
    return "ambiguous_multiple_matches"


def _evidence_gap_class(
    oracle: ManualOracleRow,
    tags: set[str],
    sample_evidence: Sequence[Mapping[str, str]],
) -> str:
    if oracle.manual_scope == "family_level_context" or "delta_mass_related" in tags:
        return "delta_mass_related_context_only"
    if oracle.manual_label == "human_unjudgeable" or "shape_bad" in tags:
        return "human_unjudgeable_shape_bad"
    if oracle.manual_label == "fail":
        if _has_positive_machine_cell(sample_evidence):
            if "rt_too_far" in tags or "pattern_mismatch" in tags:
                return "machine_too_permissive_rt_pattern_conflict"
            if (
                oracle.manual_scope == "scope_derived_unmentioned_fail"
                or "scope_derived_unmentioned_fail" in tags
            ):
                return "machine_too_permissive_scope_rule_conflict"
            return "unexplained_machine_manual_gap"
        return "machine_agrees_with_manual"
    if "boundary_ambiguous" in tags:
        return "boundary_reference_ambiguous"
    if "rt_drift_possible" in tags and oracle.manual_label == "suspect":
        return "rt_drift_policy_gap"
    if "low_intensity" in tags or "dda_stochastic_missing" in tags:
        return "machine_too_conservative_low_opportunity"
    if (
        "shape_complete" in tags
        or "pattern_similar" in tags
        or "pattern_partial" in tags
    ):
        return "machine_too_conservative_shape_or_pattern_unmodeled"
    return "unexplained_machine_manual_gap"


def _has_positive_machine_cell(sample_evidence: Sequence[Mapping[str, str]]) -> bool:
    return any(
        row.get("machine_current_label") in {"detected", "backfilled"}
        for row in sample_evidence
    )


def _secondary_tags(tags: set[str]) -> str:
    return ";".join(sorted(tags))


def _smallest_missing_fact(evidence_gap_class: str, status: str) -> str:
    if status != "explained":
        return "manual_evidence_not_represented"
    if evidence_gap_class == "machine_too_permissive_scope_rule_conflict":
        return "direct_manual_cell_review"
    return "none"


def _next_action(evidence_gap_class: str) -> str:
    return {
        "machine_agrees_with_manual": "no_action",
        "machine_too_conservative_low_opportunity": "add_opportunity_metric",
        "machine_too_conservative_shape_or_pattern_unmodeled": "add_shape_metric",
        "machine_too_permissive_rt_pattern_conflict": "inspect_ms2_pattern",
        "machine_too_permissive_scope_rule_conflict": "inspect_manual_eic",
        "boundary_reference_ambiguous": "check_boundary_reference",
        "rt_drift_policy_gap": "flag_for_shadow_gate_review",
        "human_unjudgeable_shape_bad": "inspect_manual_eic",
        "delta_mass_related_context_only": "flag_for_shadow_gate_review",
        "unexplained_machine_manual_gap": "flag_for_shadow_gate_review",
    }[evidence_gap_class]


def _join_unique(values: Iterable[str]) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    for value in values:
        for token in str(value or "").split(";"):
            if token and token not in seen:
                tokens.append(token)
                seen.add(token)
    return ";".join(tokens)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _blast_radius_assessment_status(
    manifest_rows: Sequence[Mapping[str, str]],
) -> str:
    by_id = {row.get("artifact_id", ""): row for row in manifest_rows}
    for artifact_id in ("full_cohort_alignment_review", "full_cohort_alignment_cells"):
        row = by_id.get(artifact_id)
        if row is None:
            return "full_cohort_not_assessed"
        if row.get("artifact_status") in {"missing", "present_missing_required_fields"}:
            return "full_cohort_not_assessed"
        if row.get("missing_required_fields"):
            return "full_cohort_not_assessed"
    for artifact_id in (
        "fixture_cohort_alignment_review",
        "fixture_cohort_alignment_cells",
    ):
        row = by_id.get(artifact_id)
        if row is None:
            return "fixture_cohort_not_assessed"
        if row.get("artifact_status") in {"missing", "present_missing_required_fields"}:
            return "fixture_cohort_not_assessed"
        if row.get("missing_required_fields"):
            return "fixture_cohort_not_assessed"
    required_rows = [
        by_id[artifact_id] for artifact_id in _REQUIRED_BLAST_RADIUS_SURFACE_IDS
    ]
    if any(
        row.get("artifact_status") == "present_stale_hash_mismatch"
        for row in required_rows
    ):
        return "stale_hash_mismatch"
    if any(row.get("artifact_status") != "present_current" for row in required_rows):
        return "not_assessed"
    return "present_current"


def _max_overfit_risk(summary_rows: Sequence[Mapping[str, str]]) -> str:
    seeded_risks = [
        row.get("overfit_risk", "unassessed")
        for row in summary_rows
        if row.get("scope") in _BLAST_RADIUS_RISK_SCOPES
        and _int_value(row.get("seed_count")) > 0
    ]
    if not seeded_risks:
        return "none"
    concrete_risks = [
        risk for risk in seeded_risks if risk in {"none", "low", "medium", "high"}
    ]
    if not concrete_risks:
        return "unassessed"
    max_risk = concrete_risks[0]
    max_score = _RISK_SEVERITY[max_risk]
    for risk in concrete_risks[1:]:
        score = _RISK_SEVERITY[risk]
        if score > max_score:
            max_risk = risk
            max_score = score
    return max_risk


def _int_value(value: str | None) -> int:
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0
