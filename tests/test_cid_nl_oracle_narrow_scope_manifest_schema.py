from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "docs/superpowers/schemas/"
    "cid_nl_oracle_narrow_scope_manifest_schema.json"
)


def test_narrow_scope_schema_forbids_post_hoc_scope_selection() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["schema_version"] == "cid_nl_oracle_narrow_scope_manifest_schema"
    assert schema["validation_status"] == "diagnostic_only"

    required = set(schema["required_columns"])
    assert {
        "narrow_scope_id",
        "scope_precedence",
        "membership_predicate",
        "scope_membership_manifest_sha256",
        "required_gate_ids",
        "allowed_overall_failed_gate_ids",
        "gatekeeping_rule_id",
        "membership_frozen_before_reveal",
        "machine_mapping_visible_at_seal",
    } <= required

    integrity = schema["integrity_rules"]
    assert integrity["membership_predicate_and_exact_membership_hash_required"]
    assert integrity[
        "membership_rows_bind_existing_primary_holdout_rows_by_source_sha256"
    ]
    assert integrity["membership_rows_must_match_structured_scope_fields"]
    assert integrity["membership_frozen_before_machine_and_holdout_reveal"]
    assert integrity["narrow_scope_pass_is_derived_from_raw_gate_results"]
    assert integrity[
        "no_passing_preregistered_scope_means_nonhard_failure_redraw"
    ]

    assert all(value is False for value in schema["authority_rules"].values())
