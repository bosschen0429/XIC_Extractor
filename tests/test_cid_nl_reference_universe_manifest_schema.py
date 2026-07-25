from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "docs/superpowers/schemas/"
    "cid_nl_reference_universe_manifest_schema.v1.json"
)


def test_reference_universe_schema_keeps_recovery_independent() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["schema_version"] == "cid_nl_reference_universe_manifest_schema_v1"
    assert schema["validation_status"] == "diagnostic_only"

    required = set(schema["required_columns"])
    assert {
        "reference_family_id",
        "acquisition_source_id",
        "sample_key",
        "sample_material_stratum",
        "method_scope",
        "reference_source_type",
        "physical_presence_basis",
        "method_detectability_basis",
        "sample_local_evidence_status",
        "reference_label",
        "universe_construction_method",
        "reference_enumerator_dependency_manifest_sha256",
        "shared_candidate_generation_status",
        "shared_decision_logic_status",
        "shared_logic_overlap_disposition",
        "uses_tested_tracer_output_for_membership",
        "uses_legacy_output_for_membership",
        "uses_library_record_as_sample_presence",
        "inclusion_probability",
        "analysis_weight",
        "sampling_frame_size",
        "stratum_frame_size",
        "frame_completeness",
        "undercoverage_disposition",
        "recovery_claim_label",
        "review_record_id",
        "source_row_sha256",
    } <= required

    independence = schema["independence_rules"]
    assert independence["uses_tested_tracer_output_for_membership_must_be_false"]
    assert independence["uses_legacy_output_for_membership_must_be_false"]
    assert independence["uses_library_record_as_sample_presence_must_be_false"]
    assert independence[
        "tested_tracer_scores_rankings_thresholds_and_calls_forbidden_during_construction"
    ]
    assert independence[
        "shared_candidate_or_decision_logic_with_tested_tracer_requires_bias_limitation_and_cannot_claim_independent_recovery"
    ]
    assert independence[
        "dependency_manifest_and_shared_stage_dispositions_are_required_for_auditable_independence"
    ]


def test_reference_universe_schema_preserves_scope_and_denominators() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    conditional = schema["conditional_rules"]

    assert conditional[
        "expected_positive_control_requires_in_scope_expected_control_detectability"
    ]
    assert conditional[
        "in_scope_fixed_spike_or_authentic_standard_must_be_labeled_expected_positive_control"
    ]
    assert conditional[
        "independently_supported_positive_requires_supported_sample_local_evidence"
    ]
    assert conditional[
        "library_or_profile_source_alone_cannot_assert_sample_local_presence"
    ]
    assert conditional["method_out_of_scope_rows_cannot_enter_recovery_denominator"]
    assert conditional[
        "paired_analyte_presence_cannot_be_inferred_from_internal_standard_presence"
    ]
    assert conditional[
        "rna_or_nl132_evidence_cannot_create_nl116_dna_only_expected_positive"
    ]

    denominator = schema["recovery_denominator_rules"]
    assert set(denominator["eligible_labels"]) == {
        "expected_positive_control",
        "independently_supported_positive",
    }
    assert denominator["tested_tracer_proposal_rows_may_define_denominator"] is False
    assert denominator["legacy_machine_rows_may_define_denominator"] is False
    assert (
        denominator["spectral_library_membership_may_define_sample_presence"]
        is False
    )

    authority = schema["authority_rules"]
    assert all(value is False for value in authority.values())
