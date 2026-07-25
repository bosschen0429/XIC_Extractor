from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "docs/superpowers/schemas/"
    "cid_nl_formal_holdout_sampling_manifest_schema.v1.json"
)


def test_cid_nl_holdout_sampling_schema_preserves_analysis_units() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert (
        schema["schema_version"]
        == "cid_nl_formal_holdout_sampling_manifest_schema_v1"
    )
    assert schema["validation_status"] == "diagnostic_only"
    assert schema["row_unit"] == (
        "one blinded presentation of one underlying holdout case"
    )

    rules = schema["analysis_rules"]
    assert rules["case_id_is_primary_analysis_unit"]
    assert rules["exactly_one_primary_presentation_per_case"]
    assert rules["hidden_duplicates_enter_confirmatory_denominators"] is False
    assert rules["hidden_duplicates_measure_repeatability_only"]
    assert rules["adaptive_supplemental_enters_representative_denominators"] is False
    assert rules["proposal_universe_cannot_define_reference_recovery_denominator"]

    conditional = schema["conditional_rules"]
    assert conditional["hidden_duplicate_requires_duplicate_of_case_id"]
    assert conditional["hidden_duplicate_requires_repeatability_only_analysis_role"]
    assert conditional[
        "random_representative_primary_requires_positive_analysis_weight"
    ]
    assert conditional[
        "adaptive_supplemental_requires_supplemental_descriptive_analysis_role"
    ]
    assert conditional[
        "independent_reference_requires_reference_source_type_and_claim_scope"
    ]
    assert conditional["tested_tracer_proposal_forbids_reference_claim_fields"]
    assert conditional[
        "probability_sample_requires_analysis_weight_equal_inverse_inclusion_probability"
    ]
    assert conditional["all_dependency_group_keys_required"]


def test_cid_nl_holdout_sampling_schema_stays_blinded_and_fail_closed() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    required = set(schema["required_columns"])

    assert {
        "case_id",
        "presentation_id",
        "analysis_role",
        "source_universe",
        "estimand_id",
        "sampling_design_type",
        "sampling_frame_id",
        "sampling_frame_size",
        "stratum_frame_size",
        "inclusion_probability",
        "analysis_weight",
        "independent_hypothesis_group_id",
        "family_group_id",
        "batch_group_id",
        "study_group_id",
        "instrument_group_id",
        "acquisition_source_id",
        "sample_key",
        "case_evidence_artifact_ids",
        "case_locator_sha256",
        "source_role_hidden_from_reviewer",
        "machine_mapping_hidden_from_reviewer",
        "legacy_mapping_hidden_from_reviewer",
    } <= required

    integrity = schema["integrity_rules"]
    assert integrity["source_role_hidden_from_domain_reviewer_until_label_seal"]
    assert integrity["machine_and_legacy_mappings_hidden_until_label_seal"]
    assert integrity["case_counts_and_staged_limits_frozen_before_review"]
    assert integrity["post_reveal_case_removal_forbidden"]
    assert integrity["corruption_discovered_after_reveal_verdict"] == "inconclusive"

    authority = schema["authority_rules"]
    assert authority["matrix_authority"] is False
    assert authority["selected_area_authority"] is False
    assert authority["counted_detection_authority"] is False
    assert authority["activation_or_export_authority"] is False
