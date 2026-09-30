from __future__ import annotations

from pathlib import Path

from xic_extractor.diagnostics.shared_peak_identity_explanation.classifier import (
    build_slice0_run_facts,
    classify_explanations,
)
from xic_extractor.diagnostics.shared_peak_identity_explanation.oracle import (
    ManualOracleRow,
    load_manual_oracle,
)

ORACLE = Path("docs/superpowers/fixtures/shared_peak_identity_manual_oracle.tsv")


def test_classifier_covers_seed_vocabulary_without_unexplained_rows() -> None:
    oracle_rows = load_manual_oracle(ORACLE)
    evidence = _real_evidence(oracle_rows)

    explanations = classify_explanations(oracle_rows, evidence)
    facts = build_slice0_run_facts(explanations, durable_oracle_path=ORACLE)

    by_id = {row["oracle_row_id"]: row for row in explanations}
    assert (
        by_id["FAM000144|TumorBC2312_DNA"]["evidence_gap_class"]
        == "machine_too_permissive_rt_pattern_conflict"
    )
    assert (
        by_id["FAM001227|BenignfatBC1055_DNA"]["evidence_gap_class"]
        == "machine_too_permissive_scope_rule_conflict"
    )
    assert (
        by_id["FAM001227|BenignfatBC1055_DNA"]["smallest_missing_fact"]
        == "direct_manual_cell_review"
    )
    assert (
        by_id["FAM001227|__family_context__"]["evidence_gap_class"]
        == "delta_mass_related_context_only"
    )
    assert {
        row["evidence_gap_class"]
        for row in explanations
        if row["feature_family_id"] == "FAM001589"
    } == {"human_unjudgeable_shape_bad"}
    assert facts["seed_rows_explained"] == facts["seed_rows_total"]
    assert facts["seed_rows_unexplained"] == "0"
    assert facts["seed_rows_inconclusive"] == "0"
    assert facts["vocabulary_special_casing_detected"] == "FALSE"


def test_classifier_is_not_family_id_specific() -> None:
    oracle_rows = load_manual_oracle(ORACLE)
    evidence = _real_evidence(oracle_rows)
    explanations = classify_explanations(oracle_rows, evidence)
    target = {
        key: value
        for key, value in explanations[0].items()
        if key not in {"feature_family_id", "oracle_row_id", "sample_id"}
    }
    mutated = dict(oracle_rows[0].data)
    mutated["feature_family_id"] = "FAM999999"
    mutated["oracle_row_id"] = f"FAM999999|{mutated['sample_id']}"
    mutated_explanation = classify_explanations([ManualOracleRow(mutated)], [])
    assert mutated_explanation[0]["evidence_gap_class"] == target["evidence_gap_class"]
    assert mutated_explanation[0]["explanation_status"] == target["explanation_status"]


def test_classifier_no_match_behavior_is_explicit() -> None:
    manual_pass = _manual_row(
        manual_label="pass",
        manual_scope="reviewed_cell",
        manual_reason_tags="rt_close;shape_complete;pattern_similar",
    )
    manual_fail = _manual_row(
        manual_label="fail",
        manual_scope="reviewed_cell",
        manual_reason_tags="rt_too_far;pattern_mismatch",
        sample_id="SampleFail",
    )

    explanations = classify_explanations([manual_pass, manual_fail], [])
    by_id = {row["oracle_row_id"]: row for row in explanations}

    assert by_id["FAMTEST|SamplePass"]["machine_match_status"] == "no_match"
    assert (
        by_id["FAMTEST|SamplePass"]["evidence_gap_class"]
        == "machine_too_conservative_shape_or_pattern_unmodeled"
    )
    assert by_id["FAMTEST|SamplePass"]["smallest_missing_fact"] == "none"
    assert by_id["FAMTEST|SamplePass"]["explanation_status"] == "explained"
    assert by_id["FAMTEST|SampleFail"]["machine_match_status"] == "no_match"
    assert (
        by_id["FAMTEST|SampleFail"]["evidence_gap_class"]
        == "machine_agrees_with_manual"
    )
    assert by_id["FAMTEST|SampleFail"]["smallest_missing_fact"] == "none"


def test_classifier_detects_scope_rule_manual_machine_disagreement() -> None:
    oracle = _manual_row(
        manual_label="fail",
        manual_scope="scope_derived_unmentioned_fail",
        manual_reason_tags="scope_derived_unmentioned_fail",
        sample_id="SampleFail",
    )
    evidence = [
        {
            "oracle_row_id": oracle.oracle_row_id,
            "source_role": "rescued_cell",
            "source_row_id": "alignment_cells.tsv:1",
            "machine_current_label": "backfilled",
            "machine_reason": "primary family consolidation",
            "machine_blockers": "",
            "source_artifact": "alignment_cells.tsv",
        }
    ]

    explanation = classify_explanations([oracle], evidence)[0]

    assert explanation["machine_match_status"] == "single_match"
    assert explanation["machine_current_label"] == "backfilled"
    assert (
        explanation["evidence_gap_class"]
        == "machine_too_permissive_scope_rule_conflict"
    )
    assert explanation["smallest_missing_fact"] == "direct_manual_cell_review"
    assert explanation["explanation_status"] == "explained"


def test_classifier_preserves_first_seen_unique_diagnostic_tokens() -> None:
    oracle = _manual_row(
        manual_label="fail",
        manual_scope="reviewed_cell",
        manual_reason_tags="rt_too_far;pattern_mismatch",
        sample_id="SampleFail",
    )
    evidence = [
        {
            "oracle_row_id": oracle.oracle_row_id,
            "source_role": "rescued_cell",
            "source_row_id": "alignment_cells.tsv:1",
            "machine_current_label": "backfilled",
            "machine_reason": "synthetic classifier coverage fixture",
            "machine_blockers": "rt_conflict;pattern_conflict;rt_conflict",
            "source_artifact": "alignment_cells.tsv",
        },
        {
            "oracle_row_id": oracle.oracle_row_id,
            "source_role": "selected_peak",
            "source_row_id": "alignment_review.tsv:1",
            "machine_current_label": "detected",
            "machine_reason": "synthetic classifier coverage fixture",
            "machine_blockers": "pattern_conflict;scope_conflict",
            "source_artifact": "alignment_review.tsv",
        },
    ]

    explanation = classify_explanations([oracle], evidence)[0]

    assert explanation["machine_blockers"] == (
        "rt_conflict;pattern_conflict;scope_conflict"
    )
    assert explanation["source_roles_seen"] == "rescued_cell;selected_peak"
    assert explanation["source_artifacts"] == (
        "alignment_cells.tsv;alignment_review.tsv"
    )


def _real_evidence(oracle_rows):
    return [
        {
            "oracle_row_id": row.oracle_row_id,
            "source_role": "rescued_cell",
            "source_row_id": f"alignment_cells.tsv:{index}",
            "machine_current_label": "backfilled",
            "machine_reason": "synthetic classifier coverage fixture",
            "machine_blockers": "",
            "source_artifact": "synthetic_alignment_cells.tsv",
        }
        for index, row in enumerate(oracle_rows, start=1)
        if row.manual_label == "fail"
    ]


def _manual_row(
    *,
    manual_label: str,
    manual_scope: str,
    manual_reason_tags: str,
    sample_id: str = "SamplePass",
) -> ManualOracleRow:
    return ManualOracleRow(
        {
            "oracle_row_id": f"FAMTEST|{sample_id}",
            "feature_family_id": "FAMTEST",
            "sample_id": sample_id,
            "manual_label": manual_label,
            "manual_label_source": "direct_eic_ms2_review",
            "manual_confidence": "high",
            "manual_scope": manual_scope,
            "manual_reason_tags": manual_reason_tags,
        }
    )
