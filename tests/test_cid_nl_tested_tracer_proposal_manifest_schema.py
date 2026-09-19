from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "docs/superpowers/schemas/"
    "cid_nl_tested_tracer_proposal_manifest_schema.json"
)


def test_cid_nl_tested_tracer_proposal_manifest_schema_is_fail_closed() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert (
        schema["schema_version"]
        == "cid_nl_tested_tracer_proposal_manifest_schema"
    )
    assert schema["validation_status"] == "diagnostic_only"
    assert schema["row_unit"] == (
        "one PeakHypothesis x AcquisitionSource x workflow_policy"
    )

    required = set(schema["required_columns"])
    assert {
        "processing_request_id",
        "processing_attempt_id",
        "acquisition_source_id",
        "sample_key",
        "tracer_id",
        "tracer_version",
        "tracer_config_sha256",
        "kernel_object_ref",
        "peak_hypothesis_id",
        "workflow_policy",
        "observation_state",
        "observation_origin",
        "selection_state",
        "projection_outcome",
        "evidence_artifact_ids",
        "source_row_sha256",
    } <= required

    integrity = schema["integrity_rules"]
    assert integrity["all_emitted_candidates_included_before_denominator_filtering"]
    assert integrity["legacy_or_placeholder_rows_forbidden"]
    assert integrity["candidate_universe_complete_required"]
    assert integrity["seal_before_formal_holdout_labels_open"]
    assert integrity["seal_before_machine_or_legacy_mapping_reveal_to_reviewer"]
    assert integrity["missing_or_invalid_seal_verdict"] == "inconclusive"
    assert schema["seal_expected_values"] == {
        "candidate_universe_complete": True,
        "machine_mapping_revealed_to_reviewer": False,
        "legacy_mapping_revealed_to_reviewer": False,
    }

    conditional = schema["conditional_rules"]
    assert conditional["select_value_requires_observed_state"]
    assert conditional["select_value_requires_selected_state"]
    assert conditional["select_value_requires_apex_boundaries_and_area"]
    assert conditional["leave_missing_requires_absent_state"]
    assert conditional["missing_unassessable_ambiguous_and_failed_require_hold"]
    assert conditional["non_observed_state_forbids_selected_state"]
    assert conditional[
        "blank_candidate_metrics_do_not_remove_the_row_from_candidate_universe"
    ]


def test_cid_nl_proposal_denominator_is_separate_from_reference_recovery() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    denominator = schema["precision_denominator_rules"]

    assert denominator["rule_id"] == "cid_nl_tested_tracer_select_value"
    assert denominator["hold_and_leave_missing_are_not_positive_proposals"]
    assert denominator["reference_recovery_uses_this_manifest"] is False

    authority = schema["authority_rules"]
    assert authority["matrix_authority"] is False
    assert authority["selected_area_authority"] is False
    assert authority["counted_detection_authority"] is False
    assert authority["activation_or_export_authority"] is False
