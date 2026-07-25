from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "docs/superpowers/schemas/"
    "cid_nl_oracle_review_adjudication_schema.v1.json"
)


def test_review_schema_separates_local_policy_and_unblinded_phases() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["schema_version"] == "cid_nl_oracle_review_adjudication_schema_v1"
    assert schema["validation_status"] == "diagnostic_only"
    assert set(schema["allowed_review_phases"]) == {
        "sample_local_blinded",
        "cross_sample_policy_blinded",
        "hidden_duplicate_sample_local_blinded",
        "hidden_duplicate_policy_blinded",
        "unblinded_failure_taxonomy",
    }

    required = set(schema["required_review_event_columns"])
    assert {
        "review_record_id",
        "case_id",
        "presentation_id",
        "review_phase",
        "evidence_sufficiency",
        "feature_identity_decision",
        "observation_state",
        "peak_decision",
        "targeted_projection",
        "untargeted_projection",
        "local_label_seal_id",
        "local_review_record_id",
        "local_review_record_sha256",
        "machine_mapping_visible",
        "legacy_mapping_visible",
        "predecessor_review_record_id",
        "source_row_sha256",
    } <= required

    rules = schema["review_conditional_rules"]
    assert rules["sample_local_phase_requires_all_evaluated_system_outputs_hidden"]
    assert rules["cross_sample_phase_requires_sealed_sample_local_label"]
    assert rules[
        "cross_sample_phase_requires_immutable_local_record_id_and_sha256"
    ]
    assert rules["cross_sample_phase_enforces_observation_projection_crosswalk"]
    assert rules[
        "cross_sample_context_cannot_change_sample_local_identity_peak_or_boundary"
    ]
    assert rules["unblinded_taxonomy_cannot_change_confirmatory_label"]
    assert rules["unassessable_is_completed_observation_not_pending_review"]
    assert rules[
        "hidden_duplicate_and_primary_must_use_same_reviewer_within_each_phase"
    ]
    assert rules[
        "every_designated_primary_requires_complete_sample_local_and_policy_phases"
    ]
    assert schema["label_seal_expected_values"] == {
        "source_roles_revealed": False,
        "machine_mapping_revealed": False,
        "legacy_mapping_revealed": False,
    }


def test_adjudication_schema_is_append_only_and_post_reveal_fail_closed() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    required = set(schema["required_adjudication_event_columns"])

    assert {
        "adjudication_record_id",
        "adjudication_type",
        "trigger_review_record_ids",
        "resulting_confirmatory_review_record_id",
        "adjudicated_dimension",
        "adjudicated_value",
        "adjudication_rule_id",
        "affected_gate_ids",
        "packet_effect",
        "superseding_packet_id",
        "predecessor_adjudication_record_id",
        "source_row_sha256",
    } <= required

    rules = schema["adjudication_rules"]
    assert rules["adjudication_is_append_only"]
    assert rules["trigger_records_are_immutable"]
    assert rules[
        "pre_reveal_label_resolution_requires_dimension_value_and_rule"
    ]
    assert rules["post_reveal_integrity_incident_cannot_change_confirmatory_label"]
    assert rules["post_reveal_integrity_incident_requires_packet_inconclusive"]
    assert rules["post_reveal_case_removal_and_denominator_recomputation_forbidden"]
    assert rules["duplicate_presentations_never_enter_confirmatory_metric_denominators"]

    assert all(
        value is False for value in schema["authority_rules"].values()
    )
