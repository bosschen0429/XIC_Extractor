from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "docs/superpowers/schemas/"
    "cid_nl_oracle_lineage_binding_schema.v1.json"
)


def test_cid_nl_oracle_lineage_schema_binds_canonical_identities() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["schema_version"] == "cid_nl_oracle_lineage_binding_schema_v1"
    assert schema["validation_status"] == "diagnostic_only"

    required = set(schema["required_columns"])
    assert {
        "acquisition_source_id",
        "sample_key",
        "processing_request_id",
        "processing_attempt_id",
        "evidence_artifact_id",
        "artifact_schema_version",
        "artifact_semantic_role",
        "artifact_sha256",
        "kernel_object_ref",
        "review_record_id",
        "review_record_schema_version",
        "review_record_sha256",
        "staleness_status",
        "supersedes_binding_id",
    } <= required

    role_required = schema["role_required_fields"]
    assert {
        "proposal_manifest_id",
        "proposal_row_id",
        "acquisition_source_id",
        "sample_key",
        "kernel_object_ref",
    } <= set(role_required["tested_tracer_proposal"])
    assert {
        "case_id",
        "presentation_id",
        "review_record_id",
        "review_record_schema_version",
        "review_record_sha256",
    } <= set(role_required["human_review_event"])
    assert {
        "gate_id",
        "gate_table_sha256",
        "gate_input_artifact_ids",
        "gate_status",
        "gate_observed_value",
        "gate_threshold_resolved_value",
        "gate_verdict_dependency",
    } <= set(role_required["gate_result"])


def test_cid_nl_oracle_lineage_schema_does_not_invent_authority_or_identity() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    integrity = schema["integrity_rules"]

    assert integrity["kernel_object_ref_is_processing_request_namespaced"]
    assert integrity["case_id_is_locator_not_lineage_identity"]
    assert integrity["path_display_name_timestamp_and_row_order_forbidden_as_identity"]
    assert integrity["missing_source_bytes_recorded_not_replayable"]
    assert integrity["historical_provenance_must_not_be_invented"]
    assert integrity[
        "gate_result_binding_requires_gate_table_input_artifacts_status_observed_threshold_and_verdict_dependency"
    ]
    assert integrity["missing_required_binding_verdict"] == "inconclusive"

    authority = schema["authority_rules"]
    assert all(value is False for value in authority.values())
