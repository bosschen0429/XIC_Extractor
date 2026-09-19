from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "docs/superpowers/schemas/"
    "cid_nl_oracle_statistical_design_manifest_schema.json"
)


def test_statistical_design_schema_dispatches_by_sampling_design() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert (
        schema["schema_version"]
        == "cid_nl_oracle_statistical_design_manifest_schema"
    )
    assert schema["validation_status"] == "diagnostic_only"

    required = set(schema["required_columns"])
    assert {
        "estimand_id",
        "gate_id",
        "frame_completeness",
        "sampling_design_type",
        "point_estimator",
        "uncertainty_method",
        "confidence_sidedness",
        "confidence_level",
        "threshold_parameter_id",
        "threshold_value",
        "primary_cluster_key",
        "secondary_grouping_keys",
        "incomplete_frame_claim_downgrade",
        "frozen_before_reveal",
    } <= required

    rules = schema["conditional_rules"]
    assert rules["finite_census_requires_descriptive_uncertainty_for_same_frame"]
    assert rules["stratified_or_clustered_sample_requires_design_aware_uncertainty"]
    assert rules[
        "incomplete_reference_frame_requires_partial_bounds_or_known_reference_claim"
    ]
    assert rules["machine_hold_remains_in_unconditional_recovery_denominator"]
    assert rules["hidden_duplicates_excluded_from_scientific_denominators"]
    assert rules[
        "confidence_bound_methods_require_finite_open_interval_level_and_compatible_sidedness"
    ]
    assert schema["seal_expected_values"] == {
        "all_required_gate_parameters_resolved": True,
        "all_uncertainty_routes_resolved": True,
        "all_group_keys_resolved": True,
        "machine_mapping_revealed": False,
        "holdout_labels_revealed": False,
    }

    assert all(value is False for value in schema["authority_rules"].values())
