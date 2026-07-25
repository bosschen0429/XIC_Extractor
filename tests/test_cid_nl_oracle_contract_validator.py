from __future__ import annotations

import json
from pathlib import Path

import pytest

from xic_extractor.discovery.cid_nl_oracle_contract import (
    _ADJUDICATION_REQUIRED_FIELDS,
    _LINEAGE_REQUIRED_FIELDS,
    _REFERENCE_REQUIRED_FIELDS,
    _REVIEW_REQUIRED_FIELDS,
    OracleContractError,
    canonical_row_sha256,
    canonical_tsv_bytes,
    sha256_bytes,
    sha256_canonical_json,
    validate_adjudication_event,
    validate_adjudication_event_set,
    validate_lineage_binding_row,
    validate_lineage_binding_rows,
    validate_manifest_seal,
    validate_proposal_manifest_rows,
    validate_proposal_row,
    validate_reference_manifest_rows,
    validate_reference_row,
    validate_review_event,
    validate_review_event_set,
    validate_review_label_seal,
    validate_sampling_manifest_rows,
    validate_sampling_row,
    validate_sampling_statistical_binding,
    validate_statistical_design_row,
)

ROOT = Path(__file__).resolve().parents[1]
GATE_TABLE = (
    ROOT
    / "docs/superpowers/validation/cid_nl_oracle_packet_preflight_v1/"
    "cid_nl_oracle_gate_table_v1.tsv"
)


def _proposal(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "cid_nl_tested_tracer_proposal_manifest_v1",
        "proposal_manifest_id": "PM1",
        "proposal_row_id": "PR1",
        "processing_request_id": "REQ1",
        "processing_attempt_id": "ATT1",
        "acquisition_source_id": "AS1",
        "sample_key": "S1",
        "tracer_id": "T1",
        "tracer_version": "v1",
        "tracer_config_sha256": "a" * 64,
        "kernel_object_ref": "REQ1:H1",
        "peak_hypothesis_id": "H1",
        "hypothesis_identity_sha256": "b" * 64,
        "workflow_policy": "untargeted",
        "precursor_mz": 200.0,
        "neutral_loss_da": 116.0,
        "observation_state": "observed",
        "observation_origin": "discovery",
        "selection_state": "selected",
        "projection_outcome": "select_value",
        "candidate_left_rt_min": 9.5,
        "candidate_apex_rt_min": 10.0,
        "candidate_right_rt_min": 10.5,
        "candidate_area": 100.0,
        "evidence_artifact_ids": "E1",
        "source_row_sha256": "",
    }
    row.update(overrides)
    row["source_row_sha256"] = canonical_row_sha256(row)
    return row


def _sampling(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "cid_nl_formal_holdout_sampling_manifest_v1",
        "holdout_manifest_id": "HM1",
        "case_id": "C1",
        "presentation_id": "P1",
        "case_role": "random_representative",
        "analysis_role": "primary_confirmatory",
        "primary_presentation": "TRUE",
        "machine_conditioned_source": "FALSE",
        "estimand_id": "reference_recovery",
        "sampling_design_type": "stratified_probability_sample",
        "sampling_frame_id": "FRAME1",
        "sampling_frame_size": "100",
        "sampling_stratum": "dna_only",
        "stratum_frame_size": "20",
        "inclusion_probability": "0.5",
        "analysis_weight": "2",
        "independent_hypothesis_group_id": "H1",
        "family_group_id": "F1",
        "batch_group_id": "B1",
        "study_group_id": "S1",
        "instrument_group_id": "I1",
        "dependency_group_ids": "H1|F1|B1|S1|I1",
        "duplicate_of_case_id": "",
        "source_universe": "independent_reference",
        "acquisition_source_id": "AS1",
        "sample_key": "S1",
        "sample_material_stratum": "dna_only_biological",
        "method_scope": "nl116_only",
        "reference_source_type": "blinded_systematic_review",
        "reference_claim_scope": "sample_local_observed_feature",
        "precursor_mz": "200.0",
        "neutral_loss_da": "116.0",
        "search_rt_start_min": "9.0",
        "search_rt_end_min": "11.0",
        "evidence_scope": "ms1_ms2",
        "case_evidence_artifact_ids": "E1",
        "case_locator_sha256": "e" * 64,
        "review_order": "1",
        "source_role_hidden_from_reviewer": "TRUE",
        "machine_mapping_hidden_from_reviewer": "TRUE",
        "legacy_mapping_hidden_from_reviewer": "TRUE",
    }
    row.update(overrides)
    return row


def _reference(**overrides: object) -> dict[str, object]:
    dependency_bytes = json.dumps(
        {
            "uses_tested_tracer_output_for_membership": False,
            "uses_legacy_output_for_membership": False,
            "uses_library_record_as_sample_presence": False,
            "shared_raw_adapter_status": "shared_declared",
            "shared_candidate_generation_status": "independent",
            "shared_decision_logic_status": "independent",
            "shared_logic_overlap_disposition": "independent_recovery_eligible",
            "stages": {
                "raw_adapter": {
                    "component_id": "raw-adapter",
                    "version": "v1",
                    "config_sha256": "1" * 64,
                    "status": "shared_declared",
                },
                "candidate_generation": {
                    "component_id": "independent-enumerator",
                    "version": "v1",
                    "config_sha256": "2" * 64,
                    "status": "independent",
                },
                "decision_logic": {
                    "component_id": "human-protocol",
                    "version": "v1",
                    "config_sha256": "3" * 64,
                    "status": "independent",
                },
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    source_hash = sha256_bytes(b"source evidence")
    dependency_hash = sha256_bytes(dependency_bytes)
    config_hash = sha256_bytes(b"enumerator config")
    row: dict[str, object] = {
        "schema_version": "cid_nl_reference_universe_manifest_v1",
        "reference_universe_id": "RU1",
        "reference_row_id": "RR1",
        "reference_family_id": "RF1",
        "acquisition_source_id": "AS1",
        "sample_key": "S1",
        "sample_material_stratum": "dna_only_biological",
        "method_scope": "nl116_only",
        "precursor_mz": "200.0",
        "neutral_loss_da": "116.0474",
        "reference_source_type": "blinded_systematic_review",
        "reference_source_id": "RS1",
        "reference_source_artifact_ids": "SRC1",
        "reference_source_sha256s": source_hash,
        "uses_tested_tracer_output_for_membership": "FALSE",
        "uses_legacy_output_for_membership": "FALSE",
        "uses_library_record_as_sample_presence": "FALSE",
        "reference_enumerator_dependency_manifest_id": "DEP1",
        "reference_enumerator_dependency_manifest_sha256": dependency_hash,
        "shared_raw_adapter_status": "shared_declared",
        "shared_candidate_generation_status": "independent",
        "shared_decision_logic_status": "independent",
        "shared_logic_overlap_disposition": "independent_recovery_eligible",
        "universe_construction_method": "independent_bounded_enumeration",
        "independent_enumerator_id": "human-protocol-1",
        "independent_enumerator_version": "v1",
        "independent_enumerator_config_sha256": config_hash,
        "reference_label": "independently_supported_positive",
        "physical_presence_basis": "sample_local_observed_evidence",
        "method_detectability_basis": "sample_local_supported",
        "sample_local_evidence_status": "supported",
        "reference_claim_scope": "sample_local_observed_feature",
        "review_record_id": "REV1",
        "sampling_frame_id": "FRAME1",
        "sampling_stratum": "all",
        "frame_completeness": "complete",
        "recovery_claim_label": "known_reference_recovery",
        "sampling_frame_size": "1",
        "stratum_frame_size": "1",
        "inclusion_probability": "",
        "analysis_weight": "",
        "undercoverage_disposition": "",
        "independent_hypothesis_group_id": "H1",
        "batch_group_id": "B1",
        "study_group_id": "ST1",
        "instrument_group_id": "I1",
        "source_row_sha256": "",
    }
    row.update(overrides)
    row["source_row_sha256"] = canonical_row_sha256(row)
    return row


def _reference_for_sampling(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "sampling_frame_size": "100",
        "sampling_stratum": "dna_only",
        "stratum_frame_size": "20",
    }
    values.update(overrides)
    return _reference(**values)


def _reference_artifacts() -> dict[str, bytes]:
    dependency_bytes = json.dumps(
        {
            "uses_tested_tracer_output_for_membership": False,
            "uses_legacy_output_for_membership": False,
            "uses_library_record_as_sample_presence": False,
            "shared_raw_adapter_status": "shared_declared",
            "shared_candidate_generation_status": "independent",
            "shared_decision_logic_status": "independent",
            "shared_logic_overlap_disposition": "independent_recovery_eligible",
            "stages": {
                "raw_adapter": {
                    "component_id": "raw-adapter",
                    "version": "v1",
                    "config_sha256": "1" * 64,
                    "status": "shared_declared",
                },
                "candidate_generation": {
                    "component_id": "independent-enumerator",
                    "version": "v1",
                    "config_sha256": "2" * 64,
                    "status": "independent",
                },
                "decision_logic": {
                    "component_id": "human-protocol",
                    "version": "v1",
                    "config_sha256": "3" * 64,
                    "status": "independent",
                },
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "SRC1": b"source evidence",
        "DEP1": dependency_bytes,
        "enumerator-config:human-protocol-1": b"enumerator config",
    }


def _validate_reference(row: dict[str, object]) -> None:
    validate_reference_row(
        row,
        artifact_bytes_by_id=_reference_artifacts(),
    )


def _reference_manifest_bundle(
    rows: list[dict[str, object]],
) -> tuple[bytes, dict[str, object], dict[str, bytes]]:
    manifest_bytes = canonical_tsv_bytes(rows)
    seal_sources = {
        "bounded_dataset_manifest_sha256": b"bounded dataset",
        "method_scope_sha256": b"method scope",
        "source_manifest_sha256": b"source manifest",
        "reference_enumerator_dependency_manifest_sha256": (
            _reference_artifacts()["DEP1"]
        ),
        "universe_construction_protocol_sha256": b"construction protocol",
    }
    labels = [str(row["reference_label"]) for row in rows]
    stratum_sizes = {
        str(row["sampling_stratum"]): int(str(row["stratum_frame_size"]))
        for row in rows
    }
    seal: dict[str, object] = {
        "schema_version": "cid_nl_reference_universe_manifest_v1",
        "reference_universe_id": "RU1",
        "reference_universe_tsv_sha256": sha256_bytes(manifest_bytes),
        **{
            field: sha256_bytes(payload)
            for field, payload in seal_sources.items()
        },
        "independence_audit_passed": "TRUE",
        "unresolved_shared_candidate_or_decision_overlap_count": "0",
        "sampling_frame_size": str(len(rows)),
        "frame_completeness": "complete",
        "frame_size_by_stratum_sha256": sha256_canonical_json(
            stratum_sizes
        ),
        "undercoverage_disposition": "none",
        "recovery_claim_label": "full_bounded_reference_recovery",
        "row_count": str(len(rows)),
        "eligible_positive_row_count": str(
            sum(
                label
                in {
                    "expected_positive_control",
                    "independently_supported_positive",
                }
                for label in labels
            )
        ),
        "expected_positive_control_count": str(
            labels.count("expected_positive_control")
        ),
        "independently_supported_positive_count": str(
            labels.count("independently_supported_positive")
        ),
        "supported_negative_count": str(labels.count("supported_negative")),
        "unassessable_count": str(labels.count("unassessable")),
        "method_out_of_scope_count": str(
            labels.count("method_out_of_scope")
        ),
        "source_roles_revealed_to_reviewer": "FALSE",
        "machine_mapping_revealed_to_reviewer": "FALSE",
        "legacy_mapping_revealed_to_reviewer": "FALSE",
        "sealed_at_utc": "2026-07-25T00:00:00Z",
        "sealed_by": "custodian",
        "seal_status": "sealed",
        "supersedes_reference_universe_id": "none",
    }
    return manifest_bytes, seal, seal_sources


def _local_review(
    *,
    sampling_row: dict[str, object] | None = None,
    **overrides: object,
) -> dict[str, object]:
    source_sampling_row = sampling_row or _sampling()
    row: dict[str, object] = {
        "schema_version": "cid_nl_oracle_review_event_v1",
        "oracle_packet_id": "OP1",
        "holdout_manifest_id": "HM1",
        "case_id": "C1",
        "review_record_id": "R1",
        "presentation_id": "P1",
        "review_phase": "sample_local_blinded",
        "analysis_role": "primary_confirmatory",
        "reviewer_id": "reviewer-1",
        "reviewed_at_utc": "2026-07-25T00:00:00Z",
        "review_status": "completed",
        "evidence_sufficiency": "sufficient",
        "machine_mapping_visible": "FALSE",
        "legacy_mapping_visible": "FALSE",
        "evaluated_system_scores_visible": "FALSE",
        "source_roles_visible": "FALSE",
        "feature_identity_decision": "accept",
        "observation_state": "observed",
        "peak_decision": "acceptable",
        "human_left_rt_min": "9.5",
        "human_apex_rt_min": "10.0",
        "human_right_rt_min": "10.5",
        "targeted_projection": "not_adjudicated",
        "untargeted_projection": "not_adjudicated",
        "reason_codes": "clean_peak",
        "evidence_artifact_ids": "E1",
        "evidence_bundle_sha256": "a" * 64,
        "source_sampling_row_sha256": canonical_row_sha256(
            source_sampling_row
        ),
        "source_row_sha256": "",
    }
    row.update(overrides)
    row["source_row_sha256"] = canonical_row_sha256(row)
    return row


def _policy_review(
    *,
    local_record: dict[str, object] | None = None,
    **overrides: object,
) -> dict[str, object]:
    local = local_record or _local_review()
    row = {
        **local,
        "review_record_id": "R2",
        "review_phase": "cross_sample_policy_blinded",
        "local_review_record_id": "R1",
        "local_review_record_sha256": canonical_row_sha256(local),
        "local_label_seal_id": "LS1",
        "targeted_projection": "select_value",
        "untargeted_projection": "select_value",
    }
    row.update(overrides)
    row["source_row_sha256"] = canonical_row_sha256(row)
    return row


def _local_label_seal(
    local: dict[str, object],
    *,
    seal_id: str = "LS1",
) -> dict[str, object]:
    return {
        "local_label_seal_id": seal_id,
        "machine_mapping_revealed": "FALSE",
        "legacy_mapping_revealed": "FALSE",
        "review_record_hashes": {
            str(local["review_record_id"]): canonical_row_sha256(local)
        },
    }


def _adjudication(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "cid_nl_oracle_adjudication_event_v1",
        "oracle_packet_id": "OP1",
        "adjudication_record_id": "A1",
        "case_id": "C1",
        "adjudication_type": "pre_reveal_hidden_duplicate_inconsistency",
        "trigger_review_record_ids": "R1|R2",
        "trigger_reason_codes": "boundary_disagreement",
        "adjudicator_id": "reviewer-1",
        "adjudicated_at_utc": "2026-07-25T00:00:00Z",
        "machine_mapping_visible": "FALSE",
        "legacy_mapping_visible": "FALSE",
        "resulting_confirmatory_review_record_id": "R3",
        "adjudicated_dimension": "peak_boundary",
        "adjudicated_value": "9.5|10.5",
        "adjudication_rule_id": "hidden_duplicate_consensus_v1",
        "affected_gate_ids": "reviewer_repeatability",
        "packet_effect": "confirm_frozen_label",
        "source_row_sha256": "c" * 64,
    }
    row.update(overrides)
    row["source_row_sha256"] = canonical_row_sha256(row)
    return row


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "observation_state": "failed",
            "selection_state": "no_selection",
            "projection_outcome": "leave_missing",
        },
        {
            "observation_state": "absent",
            "selection_state": "no_selection",
            "projection_outcome": "hold",
        },
        {
            "observation_state": "missing",
            "selection_state": "no_selection",
            "projection_outcome": "leave_missing",
        },
        {
            "candidate_left_rt_min": 9.5,
            "candidate_apex_rt_min": 11.0,
            "candidate_right_rt_min": 10.5,
        },
    ],
)
def test_proposal_validator_rejects_every_crosswalk_class(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(OracleContractError):
        validate_proposal_row(_proposal(**overrides))


def test_proposal_manifest_rejects_duplicate_keys_and_mixed_attempts() -> None:
    first = _proposal()
    duplicate_scientific = _proposal(proposal_row_id="PR2")
    with pytest.raises(OracleContractError, match="scientific key"):
        validate_proposal_manifest_rows([first, duplicate_scientific])

    different_candidate = _proposal(
        proposal_row_id="PR2",
        kernel_object_ref="REQ1:H2",
        peak_hypothesis_id="H2",
        hypothesis_identity_sha256="c" * 64,
        processing_attempt_id="ATT2",
    )
    with pytest.raises(OracleContractError, match="mixes request"):
        validate_proposal_manifest_rows([first, different_candidate])


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "case_role": "hidden_duplicate",
            "analysis_role": "repeatability_only",
            "primary_presentation": "TRUE",
            "duplicate_of_case_id": "C1",
        },
        {"analysis_weight": "3"},
        {
            "sampling_design_type": "finite_census",
            "inclusion_probability": "0.5",
            "analysis_weight": "2",
        },
        {
            "sampling_design_type": "nonprobability_challenge",
            "analysis_role": "primary_confirmatory",
        },
        {"sample_material_stratum": "unknown_material"},
        {"machine_mapping_hidden_from_reviewer": "FALSE"},
    ],
)
def test_sampling_validator_rejects_invalid_design_and_duplicate_rows(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(OracleContractError):
        validate_sampling_row(_sampling(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "reference_source_type": "attributable_library_ms2",
            "physical_presence_basis": "unknown",
        },
        {
            "shared_candidate_generation_status": "shared_declared",
            "shared_logic_overlap_disposition": "independent_recovery_eligible",
        },
        {
            "frame_completeness": "incomplete_known_undercoverage",
            "recovery_claim_label": "full_bounded_reference_recovery",
            "undercoverage_disposition": "known gaps",
        },
        {"uses_tested_tracer_output_for_membership": "TRUE"},
        {"reference_enumerator_dependency_manifest_id": ""},
        {"independent_enumerator_config_sha256": "not-a-hash"},
        {
            "universe_construction_method": (
                "probability_sample_from_independent_frame"
            ),
            "inclusion_probability": "0.5",
            "analysis_weight": "3",
        },
    ],
)
def test_reference_validator_rejects_nonindependent_or_overclaimed_rows(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(OracleContractError):
        _validate_reference(_reference(**overrides))


def test_reference_validator_required_fields_match_schema() -> None:
    schema = json.loads(
        (
            ROOT
            / "docs/superpowers/schemas/"
            "cid_nl_reference_universe_manifest_schema.v1.json"
        ).read_text(encoding="utf-8")
    )
    expected = set(schema["required_columns"]) - set(schema["nullable_columns"])

    assert set(_REFERENCE_REQUIRED_FIELDS) == expected


@pytest.mark.parametrize(
    "field",
    (
        "sample_key",
        "precursor_mz",
        "neutral_loss_da",
        "reference_source_id",
        "independent_hypothesis_group_id",
        "batch_group_id",
        "study_group_id",
        "instrument_group_id",
    ),
)
def test_reference_validator_rejects_missing_scientific_identity(
    field: str,
) -> None:
    with pytest.raises(OracleContractError, match=field):
        _validate_reference(_reference(**{field: ""}))


@pytest.mark.parametrize(
    "field",
    (
        "sample_material_stratum",
        "reference_source_type",
        "physical_presence_basis",
        "method_detectability_basis",
        "sample_local_evidence_status",
        "reference_label",
        "reference_claim_scope",
        "universe_construction_method",
        "shared_raw_adapter_status",
        "shared_candidate_generation_status",
        "shared_decision_logic_status",
        "shared_logic_overlap_disposition",
        "frame_completeness",
        "recovery_claim_label",
    ),
)
def test_reference_validator_rejects_every_unknown_enum(field: str) -> None:
    with pytest.raises(OracleContractError, match=field):
        _validate_reference(_reference(**{field: "unknown_enum"}))


def test_full_reference_recovery_requires_cross_row_completeness_seal() -> None:
    row = _reference(recovery_claim_label="full_bounded_reference_recovery")
    with pytest.raises(OracleContractError, match="verified complete"):
        _validate_reference(row)

    manifest_bytes, seal, seal_sources = _reference_manifest_bundle([row])
    validate_reference_manifest_rows(
        [row],
        seal=seal,
        manifest_bytes=manifest_bytes,
        artifact_bytes_by_id=_reference_artifacts(),
        seal_artifact_bytes_by_sha_field=seal_sources,
    )

    with pytest.raises(OracleContractError, match="frame size"):
        validate_reference_manifest_rows(
            [row],
            seal={**seal, "sampling_frame_size": "2"},
            manifest_bytes=manifest_bytes,
            artifact_bytes_by_id=_reference_artifacts(),
            seal_artifact_bytes_by_sha_field=seal_sources,
        )


def test_reference_manifest_rejects_wrong_counts_foreign_universe_and_pk() -> None:
    row = _reference(recovery_claim_label="full_bounded_reference_recovery")
    manifest_bytes, seal, seal_sources = _reference_manifest_bundle([row])
    with pytest.raises(OracleContractError, match="row_count"):
        validate_reference_manifest_rows(
            [row],
            seal={**seal, "eligible_positive_row_count": "0"},
            manifest_bytes=manifest_bytes,
            artifact_bytes_by_id=_reference_artifacts(),
            seal_artifact_bytes_by_sha_field=seal_sources,
        )
    wrong_frame_row = _reference(
        recovery_claim_label="full_bounded_reference_recovery",
        sampling_frame_size="999",
    )
    wrong_frame_bytes, wrong_frame_seal, seal_sources = (
        _reference_manifest_bundle([wrong_frame_row])
    )
    with pytest.raises(OracleContractError, match="frame size"):
        validate_reference_manifest_rows(
            [wrong_frame_row],
            seal=wrong_frame_seal,
            manifest_bytes=wrong_frame_bytes,
            artifact_bytes_by_id=_reference_artifacts(),
            seal_artifact_bytes_by_sha_field=seal_sources,
        )

    unknown_label = _reference(
        reference_label="supported_negativ",
        recovery_claim_label="full_bounded_reference_recovery",
    )
    unknown_bytes, unknown_seal, seal_sources = _reference_manifest_bundle(
        [unknown_label]
    )
    with pytest.raises(OracleContractError, match="reference_label"):
        validate_reference_manifest_rows(
            [unknown_label],
            seal=unknown_seal,
            manifest_bytes=unknown_bytes,
            artifact_bytes_by_id=_reference_artifacts(),
            seal_artifact_bytes_by_sha_field=seal_sources,
        )

    row1 = _reference(
        recovery_claim_label="full_bounded_reference_recovery",
        sampling_frame_size="2",
        stratum_frame_size="2",
    )
    row2 = _reference(
        reference_row_id="RR2",
        reference_family_id="RF2",
        acquisition_source_id="AS2",
        sample_key="S2",
        independent_hypothesis_group_id="H2",
        recovery_claim_label="full_bounded_reference_recovery",
        sampling_frame_size="2",
        stratum_frame_size="2",
    )
    rows = [row1, row2]
    manifest_bytes, seal, seal_sources = _reference_manifest_bundle(rows)

    foreign = {
        **row2,
        "reference_universe_id": "RU2",
        "source_row_sha256": "",
    }
    foreign["source_row_sha256"] = canonical_row_sha256(foreign)
    foreign_rows = [row1, foreign]
    foreign_bytes, foreign_seal, seal_sources = _reference_manifest_bundle(
        foreign_rows
    )
    with pytest.raises(OracleContractError, match="universe mismatch"):
        validate_reference_manifest_rows(
            foreign_rows,
            seal=foreign_seal,
            manifest_bytes=foreign_bytes,
            artifact_bytes_by_id=_reference_artifacts(),
            seal_artifact_bytes_by_sha_field=seal_sources,
        )

    duplicate_pk = {
        **row2,
        "reference_row_id": "RR1",
        "source_row_sha256": "",
    }
    duplicate_pk["source_row_sha256"] = canonical_row_sha256(duplicate_pk)
    duplicate_rows = [row1, duplicate_pk]
    duplicate_bytes, duplicate_seal, seal_sources = (
        _reference_manifest_bundle(duplicate_rows)
    )
    with pytest.raises(OracleContractError, match="primary key"):
        validate_reference_manifest_rows(
            duplicate_rows,
            seal=duplicate_seal,
            manifest_bytes=duplicate_bytes,
            artifact_bytes_by_id=_reference_artifacts(),
            seal_artifact_bytes_by_sha_field=seal_sources,
        )


def test_review_validator_binds_policy_to_immutable_local_record() -> None:
    local = _local_review()
    seal = _local_label_seal(local)
    validate_review_event(local)
    validate_review_event(
        _policy_review(),
        local_record=local,
        local_label_seal=seal,
    )

    with pytest.raises(OracleContractError, match="cannot change"):
        validate_review_event(
            _policy_review(
                feature_identity_decision="reject",
                observation_state="absent",
                peak_decision="not_applicable",
                human_left_rt_min="",
                human_apex_rt_min="",
                human_right_rt_min="",
                targeted_projection="leave_missing",
                untargeted_projection="leave_missing",
            ),
            local_record=local,
            local_label_seal=seal,
        )
    with pytest.raises(OracleContractError, match="requires leave_missing"):
        absent_local = _local_review(
            feature_identity_decision="reject",
            observation_state="absent",
            peak_decision="not_applicable",
            human_left_rt_min="",
            human_apex_rt_min="",
            human_right_rt_min="",
        )
        validate_review_event(
            _policy_review(
                local_record=absent_local,
                targeted_projection="hold",
                untargeted_projection="hold",
            ),
            local_record=absent_local,
            local_label_seal=_local_label_seal(absent_local),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("oracle_packet_id", "OTHER_PACKET"),
        ("holdout_manifest_id", "OTHER_MANIFEST"),
        ("case_id", "OTHER_CASE"),
        ("presentation_id", "OTHER_PRESENTATION"),
    ),
)
def test_policy_review_requires_same_local_case_and_presentation(
    field: str,
    value: str,
) -> None:
    local = _local_review()

    with pytest.raises(OracleContractError, match=field):
        validate_review_event(
            _policy_review(**{field: value}),
            local_record=local,
            local_label_seal=_local_label_seal(local),
        )


def test_review_validator_required_fields_match_schema() -> None:
    schema = json.loads(
        (
            ROOT
            / "docs/superpowers/schemas/"
            "cid_nl_oracle_review_adjudication_schema.v1.json"
        ).read_text(encoding="utf-8")
    )
    expected = set(schema["required_review_event_columns"]) - set(
        schema["nullable_review_columns"]
    )

    assert set(_REVIEW_REQUIRED_FIELDS) == expected


@pytest.mark.parametrize(
    "observation_state",
    ("missing", "absent", "unassessable", "ambiguous", "failed"),
)
def test_review_validator_rejects_accept_without_observed(
    observation_state: str,
) -> None:
    with pytest.raises(OracleContractError, match="requires observed"):
        validate_review_event(
            _local_review(observation_state=observation_state)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", "future"),
        ("review_status", "pending"),
        ("evidence_sufficiency", "unknown"),
        ("feature_identity_decision", "garbage"),
        ("peak_decision", "garbage"),
    ),
)
def test_review_validator_rejects_unknown_schema_or_enum(
    field: str,
    value: str,
) -> None:
    with pytest.raises(OracleContractError):
        validate_review_event(_local_review(**{field: value}))


def test_rejected_review_requires_blank_boundaries() -> None:
    with pytest.raises(OracleContractError, match="blank boundaries"):
        validate_review_event(
            _local_review(
                feature_identity_decision="reject",
                observation_state="absent",
                peak_decision="not_applicable",
            )
        )


def test_hidden_duplicate_requires_repeatability_role() -> None:
    duplicate = _local_review(
        review_phase="hidden_duplicate_sample_local_blinded",
        analysis_role="primary_confirmatory",
    )
    with pytest.raises(OracleContractError, match="repeatability_only"):
        validate_review_event(duplicate)


def test_sampling_manifest_keeps_one_primary_per_underlying_case() -> None:
    primary = _sampling()
    duplicate = _sampling(
        presentation_id="P2",
        case_role="hidden_duplicate",
        analysis_role="repeatability_only",
        primary_presentation="FALSE",
        duplicate_of_case_id="C1",
        review_order="2",
    )
    validate_sampling_manifest_rows([primary, duplicate])

    with pytest.raises(OracleContractError, match="exactly one primary"):
        validate_sampling_manifest_rows(
            [
                primary,
                {
                    **primary,
                    "presentation_id": "P2",
                    "primary_presentation": "TRUE",
                    "review_order": "2",
                },
            ]
        )


def test_sampling_manifest_rejects_duplicate_presentation_primary_key() -> None:
    with pytest.raises(OracleContractError, match="duplicate presentation"):
        validate_sampling_manifest_rows(
            [
                _sampling(),
                _sampling(
                    case_id="C2",
                    presentation_id="P1",
                    sample_key="S2",
                    review_order="2",
                ),
            ]
        )


def test_sampling_manifest_binds_frame_method_scope() -> None:
    with pytest.raises(
        OracleContractError,
        match="frame-level invariants",
    ):
        validate_sampling_manifest_rows(
            [
                _sampling(),
                _sampling(
                    case_id="C2",
                    presentation_id="P2",
                    acquisition_source_id="AS2",
                    sample_key="S2",
                    sampling_stratum="rna_only",
                    stratum_frame_size="30",
                    method_scope="nl132_only",
                    review_order="2",
                ),
            ]
        )


def test_sampling_manifest_scopes_cases_and_reuses_duplicate_locator() -> None:
    with pytest.raises(OracleContractError, match="scientific locator"):
        validate_sampling_manifest_rows(
            [
                _sampling(),
                _sampling(
                    presentation_id="P2",
                    case_role="hidden_duplicate",
                    analysis_role="repeatability_only",
                    primary_presentation="FALSE",
                    duplicate_of_case_id="C1",
                    case_locator_sha256="f" * 64,
                    review_order="2",
                ),
            ]
        )
    with pytest.raises(
        OracleContractError,
        match="scientific locator fields",
    ):
        validate_sampling_manifest_rows(
            [
                _sampling(),
                _sampling(
                    presentation_id="P2",
                    case_role="hidden_duplicate",
                    analysis_role="repeatability_only",
                    primary_presentation="FALSE",
                    duplicate_of_case_id="C1",
                    sample_key="OTHER_SAMPLE",
                    review_order="2",
                ),
            ]
        )
    with pytest.raises(
        OracleContractError,
        match="scientific locator fields",
    ):
        validate_sampling_manifest_rows(
            [
                _sampling(),
                _sampling(
                    presentation_id="P2",
                    case_role="hidden_duplicate",
                    analysis_role="repeatability_only",
                    primary_presentation="FALSE",
                    duplicate_of_case_id="C1",
                    family_group_id="OTHER_FAMILY",
                    review_order="2",
                ),
            ]
        )

    with pytest.raises(OracleContractError, match="holdout manifest"):
        validate_sampling_manifest_rows(
            [
                _sampling(
                    holdout_manifest_id="HM1",
                    primary_presentation="FALSE",
                    case_role="hidden_duplicate",
                    analysis_role="repeatability_only",
                    duplicate_of_case_id="C1",
                ),
                _sampling(
                    holdout_manifest_id="HM2",
                    presentation_id="P2",
                ),
            ]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_universe", "matched_negative"),
        ("acquisition_source_id", "AS2"),
        ("sample_key", "S2"),
        ("sample_material_stratum", "pooled_qc"),
        ("method_scope", "nl132_only"),
        ("reference_source_type", "matched_negative"),
        ("reference_claim_scope", "matched_negative_only"),
        ("precursor_mz", "201.0"),
        ("neutral_loss_da", "132.0"),
        ("search_rt_start_min", "8.0"),
        ("search_rt_end_min", "12.0"),
        ("evidence_scope", "ms1_only"),
        ("case_evidence_artifact_ids", "E2"),
        ("independent_hypothesis_group_id", "H2"),
        ("family_group_id", "F2"),
        ("batch_group_id", "B2"),
        ("study_group_id", "S2"),
        ("instrument_group_id", "I2"),
        ("dependency_group_ids", "H2|F2|B2|S2|I2"),
    ),
)
def test_hidden_duplicate_reuses_every_scientific_locator_field(
    field: str,
    value: str,
) -> None:
    duplicate = _sampling(
        presentation_id="P2",
        case_role="hidden_duplicate",
        analysis_role="repeatability_only",
        primary_presentation="FALSE",
        duplicate_of_case_id="C1",
        review_order="2",
        **{field: value},
    )
    expected = (
        "frame-level invariants"
        if field == "method_scope"
        else "locator fields"
    )
    with pytest.raises(OracleContractError, match=expected):
        validate_sampling_manifest_rows([_sampling(), duplicate])


def test_hidden_duplicate_requires_both_review_phases() -> None:
    primary_sampling = _sampling()
    duplicate_sampling = _sampling(
        presentation_id="PD1",
        case_role="hidden_duplicate",
        analysis_role="repeatability_only",
        primary_presentation="FALSE",
        duplicate_of_case_id="C1",
        review_order="2",
    )
    primary_local = _local_review(sampling_row=primary_sampling)
    primary_policy = _policy_review(
        local_record=primary_local,
        local_label_seal_id="LS_PRIMARY",
    )
    local = _local_review(
        sampling_row=duplicate_sampling,
        review_record_id="RD1",
        presentation_id="PD1",
        review_phase="hidden_duplicate_sample_local_blinded",
        analysis_role="repeatability_only",
    )
    policy = _policy_review(
        local_record=local,
        review_record_id="RD2",
        presentation_id="PD1",
        review_phase="hidden_duplicate_policy_blinded",
        analysis_role="repeatability_only",
        local_review_record_id="RD1",
        local_label_seal_id="LS_DUPLICATE",
    )
    validate_review_event_set(
        [primary_local, primary_policy, local, policy],
        sampling_rows=[primary_sampling, duplicate_sampling],
        local_label_seals_by_id={
            "LS_PRIMARY": _local_label_seal(
                primary_local,
                seal_id="LS_PRIMARY",
            ),
            "LS_DUPLICATE": _local_label_seal(
                local,
                seal_id="LS_DUPLICATE",
            ),
        },
    )

    with pytest.raises(OracleContractError, match="complete"):
        validate_review_event_set(
            [primary_local, primary_policy, local],
            sampling_rows=[primary_sampling, duplicate_sampling],
            local_label_seals_by_id={
                "LS_PRIMARY": _local_label_seal(
                    primary_local,
                    seal_id="LS_PRIMARY",
                ),
                "LS_DUPLICATE": _local_label_seal(
                    local,
                    seal_id="LS_DUPLICATE",
                ),
            },
        )


def test_primary_review_is_complete_and_chronological() -> None:
    sampling = _sampling()
    local = _local_review(sampling_row=sampling)
    policy = _policy_review(local_record=local)
    seal = _local_label_seal(local)
    validate_review_event_set(
        [local, policy],
        sampling_rows=[sampling],
        local_label_seals_by_id={"LS1": seal},
    )

    with pytest.raises(OracleContractError, match="primary presentation"):
        validate_review_event_set(
            [],
            sampling_rows=[sampling],
            local_label_seals_by_id={},
        )
    with pytest.raises(OracleContractError, match="earlier sample-local"):
        validate_review_event_set(
            [policy, local],
            sampling_rows=[sampling],
            local_label_seals_by_id={"LS1": seal},
        )
    taxonomy = _policy_review(
        local_record=local,
        review_record_id="R3",
        review_phase="unblinded_failure_taxonomy",
        predecessor_review_record_id="R2",
        machine_mapping_visible="TRUE",
        legacy_mapping_visible="TRUE",
        evaluated_system_scores_visible="TRUE",
        source_roles_visible="TRUE",
    )
    validate_review_event_set(
        [local, policy, taxonomy],
        sampling_rows=[sampling],
        local_label_seals_by_id={"LS1": seal},
    )
    early_taxonomy = {
        **taxonomy,
        "predecessor_review_record_id": "R1",
        "source_row_sha256": "",
    }
    early_taxonomy["source_row_sha256"] = canonical_row_sha256(
        early_taxonomy
    )
    with pytest.raises(OracleContractError, match="completed policy"):
        validate_review_event_set(
            [local, early_taxonomy, policy],
            sampling_rows=[sampling],
            local_label_seals_by_id={"LS1": seal},
        )
    changed_taxonomy = {
        **taxonomy,
        "targeted_projection": "hold",
        "source_row_sha256": "",
    }
    changed_taxonomy["source_row_sha256"] = canonical_row_sha256(
        changed_taxonomy
    )
    with pytest.raises(OracleContractError, match="cannot change"):
        validate_review_event_set(
            [local, policy, changed_taxonomy],
            sampling_rows=[sampling],
            local_label_seals_by_id={"LS1": seal},
        )
    invalid_local = _local_review(
        sampling_row=sampling,
        review_status="invalid_before_reveal",
    )
    invalid_policy = _policy_review(
        local_record=invalid_local,
        review_status="invalid_before_reveal",
    )
    with pytest.raises(OracleContractError, match="primary presentation"):
        validate_review_event_set(
            [invalid_local, invalid_policy],
            sampling_rows=[sampling],
            local_label_seals_by_id={
                "LS1": _local_label_seal(invalid_local)
            },
        )


def test_review_event_set_rejects_duplicate_record_ids() -> None:
    first = _local_review(review_record_id="R1")
    duplicate = _local_review(
        review_record_id="R1",
        presentation_id="P2",
    )

    with pytest.raises(OracleContractError, match="duplicate review_record_id"):
        validate_review_event_set(
            [first, duplicate],
            sampling_rows=[],
            local_label_seals_by_id={},
        )


def test_adjudication_validator_enforces_reveal_boundary() -> None:
    validate_adjudication_event(_adjudication())

    with pytest.raises(OracleContractError, match="mapping hidden"):
        validate_adjudication_event(
            _adjudication(machine_mapping_visible="TRUE")
        )

    post_reveal = _adjudication(
        adjudication_type="post_reveal_integrity_incident",
        machine_mapping_visible="TRUE",
        legacy_mapping_visible="TRUE",
        resulting_confirmatory_review_record_id="",
        adjudicated_dimension="",
        adjudicated_value="",
        adjudication_rule_id="",
        packet_effect="packet_inconclusive",
    )
    validate_adjudication_event(post_reveal)
    with pytest.raises(OracleContractError, match="must be boolean"):
        validate_adjudication_event(
            _adjudication(
                adjudication_type="post_reveal_integrity_incident",
                machine_mapping_visible="MAYBE",
                legacy_mapping_visible="MAYBE",
                resulting_confirmatory_review_record_id="",
                adjudicated_dimension="",
                adjudicated_value="",
                adjudication_rule_id="",
                packet_effect="packet_inconclusive",
            )
        )

    replacement = {
        **post_reveal,
        "resulting_confirmatory_review_record_id": "R4",
        "source_row_sha256": "",
    }
    replacement["source_row_sha256"] = canonical_row_sha256(replacement)
    with pytest.raises(OracleContractError, match="cannot replace"):
        validate_adjudication_event(replacement)


def test_adjudication_set_binds_immutable_triggers_and_predecessors() -> None:
    first = _adjudication()
    validate_adjudication_event_set(
        [first],
        review_rows=[
            _local_review(review_record_id="R1"),
            _local_review(review_record_id="R2"),
            _local_review(review_record_id="R3"),
        ],
    )

    with pytest.raises(OracleContractError, match="immutable review"):
        invalid = {
            **first,
            "trigger_review_record_ids": "UNKNOWN",
            "source_row_sha256": "",
        }
        invalid["source_row_sha256"] = canonical_row_sha256(invalid)
        validate_adjudication_event_set(
            [invalid],
            review_rows=[
                _local_review(review_record_id="R1"),
                _local_review(review_record_id="R2"),
                _local_review(review_record_id="R3"),
            ],
        )

    next_reviews = [
        _local_review(oracle_packet_id="OP2", review_record_id="R4"),
        _local_review(oracle_packet_id="OP2", review_record_id="R5"),
        _local_review(oracle_packet_id="OP2", review_record_id="R6"),
    ]
    correction = _adjudication(
        schema_version="cid_nl_oracle_adjudication_event_v1",
        oracle_packet_id="OP2",
        adjudication_record_id="A2",
        trigger_review_record_ids="R4|R5",
        resulting_confirmatory_review_record_id="R6",
        predecessor_adjudication_record_id="A1",
        superseding_packet_id="OP2",
    )
    validate_adjudication_event_set(
        [first, correction],
        review_rows=[
            _local_review(review_record_id="R1"),
            _local_review(review_record_id="R2"),
            _local_review(review_record_id="R3"),
            *next_reviews,
        ],
    )

    same_packet_correction = {
        **correction,
        "oracle_packet_id": "OP1",
        "superseding_packet_id": "OP1",
        "source_row_sha256": "",
    }
    same_packet_correction["source_row_sha256"] = canonical_row_sha256(
        same_packet_correction
    )
    with pytest.raises(OracleContractError, match="superseding packet"):
        validate_adjudication_event_set(
            [first, same_packet_correction],
            review_rows=[
                _local_review(review_record_id="R1"),
                _local_review(review_record_id="R2"),
                _local_review(review_record_id="R3"),
                _local_review(review_record_id="R4"),
                _local_review(review_record_id="R5"),
                _local_review(review_record_id="R6"),
            ],
        )


def test_adjudication_rejects_cross_packet_or_case_links() -> None:
    reviews = [
        _local_review(review_record_id="R1"),
        _local_review(review_record_id="R2"),
        _local_review(review_record_id="R3"),
    ]
    for field, value in (
        ("oracle_packet_id", "OTHER_PACKET"),
        ("case_id", "OTHER_CASE"),
    ):
        with pytest.raises(OracleContractError, match="packet and case"):
            validate_adjudication_event_set(
                [_adjudication(**{field: value})],
                review_rows=reviews,
            )

    taxonomy_result = _local_review(
        review_record_id="R3",
        review_phase="unblinded_failure_taxonomy",
    )
    with pytest.raises(
        OracleContractError,
        match="completed blinded review records",
    ):
        validate_adjudication_event_set(
            [_adjudication()],
            review_rows=[reviews[0], reviews[1], taxonomy_result],
        )
    with pytest.raises(
        OracleContractError,
        match="completed blinded review records",
    ):
        validate_adjudication_event_set(
            [
                _adjudication(
                    trigger_review_record_ids="R3",
                    resulting_confirmatory_review_record_id="R1",
                )
            ],
            review_rows=[reviews[0], reviews[1], taxonomy_result],
        )
    invalid_result = _local_review(
        review_record_id="R3",
        review_status="invalid_before_reveal",
    )
    with pytest.raises(
        OracleContractError,
        match="completed blinded review records",
    ):
        validate_adjudication_event_set(
            [_adjudication()],
            review_rows=[reviews[0], reviews[1], invalid_result],
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        (
            "evaluated_system_scores_visible",
            "TRUE",
            "evaluated-system roles hidden",
        ),
        (
            "source_roles_visible",
            "TRUE",
            "evaluated-system roles hidden",
        ),
        (
            "review_phase",
            "unblinded_failure_taxonomy",
            "completed blinded review records",
        ),
    ],
)
def test_adjudication_rejects_unblinded_trigger_records(
    field: str,
    value: str,
    match: str,
) -> None:
    trigger = _local_review(review_record_id="R1", **{field: value})
    reviews = [
        trigger,
        _local_review(review_record_id="R2"),
        _local_review(review_record_id="R3"),
    ]

    with pytest.raises(OracleContractError, match=match):
        validate_adjudication_event_set(
            [_adjudication()],
            review_rows=reviews,
        )


def test_adjudication_rejects_mutated_review_record_hash() -> None:
    trigger = _local_review(review_record_id="R1")
    trigger["reason_codes"] = "mutated_after_seal"

    with pytest.raises(
        OracleContractError,
        match="does not bind canonical row",
    ):
        validate_adjudication_event_set(
            [_adjudication()],
            review_rows=[
                trigger,
                _local_review(review_record_id="R2"),
                _local_review(review_record_id="R3"),
            ],
        )


@pytest.mark.parametrize(
    "field",
    [
        "evaluated_system_scores_visible",
        "source_roles_visible",
    ],
)
def test_adjudication_rejects_unblinded_result_record(field: str) -> None:
    result = _local_review(review_record_id="R3", **{field: "TRUE"})

    with pytest.raises(
        OracleContractError,
        match="evaluated-system roles hidden",
    ):
        validate_adjudication_event_set(
            [_adjudication()],
            review_rows=[
                _local_review(review_record_id="R1"),
                _local_review(review_record_id="R2"),
                result,
            ],
        )


def test_adjudication_and_lineage_required_fields_match_schemas() -> None:
    review_schema = json.loads(
        (
            ROOT
            / "docs/superpowers/schemas/"
            "cid_nl_oracle_review_adjudication_schema.v1.json"
        ).read_text(encoding="utf-8")
    )
    expected_adjudication = set(
        review_schema["required_adjudication_event_columns"]
    ) - set(review_schema["nullable_adjudication_columns"])
    assert set(_ADJUDICATION_REQUIRED_FIELDS) == expected_adjudication

    lineage_schema = json.loads(
        (
            ROOT
            / "docs/superpowers/schemas/"
            "cid_nl_oracle_lineage_binding_schema.v1.json"
        ).read_text(encoding="utf-8")
    )
    expected_lineage = set(lineage_schema["required_columns"]) - set(
        lineage_schema["nullable_columns"]
    )
    assert set(_LINEAGE_REQUIRED_FIELDS) == expected_lineage


def test_lineage_gate_result_requires_gate_specific_binding_fields() -> None:
    row = {
        "schema_version": "cid_nl_oracle_lineage_binding_v1",
        "oracle_packet_id": "P1",
        "binding_id": "B1",
        "binding_role": "gate_result",
        "processing_request_id": "REQ1",
        "processing_attempt_id": "ATT1",
        "evidence_artifact_id": "ART1",
        "artifact_schema_version": "v1",
        "artifact_semantic_role": "gate_result",
        "artifact_sha256": "a" * 64,
        "upstream_evidence_artifact_ids": "ART0",
        "staleness_status": "current",
        "source_row_sha256": "",
        "gate_id": "proposal_precision",
        "gate_table_sha256": "c" * 64,
        "gate_input_artifact_ids": "ART0",
        "gate_status": "pass",
        "gate_observed_value": "0.95",
        "gate_threshold_resolved_value": "0.90",
        "gate_verdict_dependency": "required_nonhard_gate_state",
    }
    row["source_row_sha256"] = canonical_row_sha256(row)
    validate_lineage_binding_row(row)

    invalid = {**row, "gate_observed_value": "", "source_row_sha256": ""}
    invalid["source_row_sha256"] = canonical_row_sha256(invalid)
    with pytest.raises(OracleContractError, match="gate_observed_value"):
        validate_lineage_binding_row(invalid)

    duplicate = {
        **row,
        "evidence_artifact_id": "ART2",
        "artifact_sha256": "d" * 64,
        "source_row_sha256": "",
    }
    duplicate["source_row_sha256"] = canonical_row_sha256(duplicate)
    with pytest.raises(OracleContractError, match="primary key"):
        validate_lineage_binding_rows([row, duplicate])

    crossed_request = {
        **row,
        "binding_id": "B2",
        "processing_request_id": "REQ2",
        "source_row_sha256": "",
    }
    crossed_request["source_row_sha256"] = canonical_row_sha256(
        crossed_request
    )
    with pytest.raises(OracleContractError, match="multiple requests"):
        validate_lineage_binding_rows([row, crossed_request])


def test_manifest_seal_validator_rejects_missing_or_invalid_hashes() -> None:
    schema_path = (
        ROOT
        / "docs/superpowers/schemas/"
        "cid_nl_oracle_narrow_scope_manifest_schema.v1.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    seal = {
        field: "value" for field in schema["seal_required_fields"]
    }
    seal["schema_version"] = schema["manifest_schema_version"]
    manifest_rows = [
        {
            "schema_version": schema["manifest_schema_version"],
            "narrow_scope_manifest_id": "value",
            "narrow_scope_id": "NS1",
        }
    ]
    manifest_bytes = canonical_tsv_bytes(manifest_rows)
    for field in schema["seal_required_fields"]:
        if "sha256" in field:
            seal[field] = sha256_bytes(field.encode())
        if field.endswith("_count"):
            seal[field] = "1"
    seal.update(schema["seal_expected_values"])
    seal["seal_status"] = "sealed"
    bound_bytes = {
        field: field.encode()
        for field in schema["seal_required_fields"]
        if "sha256" in field
    }
    seal["narrow_scope_tsv_sha256"] = sha256_bytes(manifest_bytes)
    bound_bytes["narrow_scope_tsv_sha256"] = manifest_bytes
    validate_manifest_seal(
        schema,
        seal,
        artifact_bytes_by_sha_field=bound_bytes,
        manifest_rows=manifest_rows,
    )

    with pytest.raises(OracleContractError, match="must be SHA256"):
        validate_manifest_seal(
            schema,
            {**seal, "narrow_scope_tsv_sha256": "not-a-hash"},
            artifact_bytes_by_sha_field=bound_bytes,
            manifest_rows=manifest_rows,
        )
    with pytest.raises(OracleContractError, match="schema_version"):
        validate_manifest_seal(
            schema,
            {**seal, "schema_version": "future"},
            artifact_bytes_by_sha_field=bound_bytes,
            manifest_rows=manifest_rows,
        )
    with pytest.raises(OracleContractError, match="scope_count"):
        validate_manifest_seal(
            schema,
            {**seal, "scope_count": "2"},
            artifact_bytes_by_sha_field=bound_bytes,
            manifest_rows=manifest_rows,
        )
    with pytest.raises(OracleContractError, match="do not match manifest rows"):
        validate_manifest_seal(
            schema,
            seal,
            artifact_bytes_by_sha_field=bound_bytes,
            manifest_rows=[
                {
                    "schema_version": schema["manifest_schema_version"],
                    "narrow_scope_manifest_id": "value",
                    "narrow_scope_id": "NS2",
                }
            ],
        )
    with pytest.raises(
        OracleContractError,
        match="narrow_scope_manifest_id",
    ):
        missing_invariant = [
            {
                "schema_version": schema["manifest_schema_version"],
                "narrow_scope_id": "NS1",
            }
        ]
        missing_bytes = canonical_tsv_bytes(missing_invariant)
        validate_manifest_seal(
            schema,
            {
                **seal,
                "narrow_scope_tsv_sha256": sha256_bytes(missing_bytes),
            },
            artifact_bytes_by_sha_field={
                **bound_bytes,
                "narrow_scope_tsv_sha256": missing_bytes,
            },
            manifest_rows=missing_invariant,
        )


def test_proposal_seal_binds_distinct_hypothesis_count() -> None:
    schema = json.loads(
        (
            ROOT
            / "docs/superpowers/schemas/"
            "cid_nl_tested_tracer_proposal_manifest_schema.v1.json"
        ).read_text(encoding="utf-8")
    )
    config_bytes = b"tracer config"
    row = _proposal(tracer_config_sha256=sha256_bytes(config_bytes))
    manifest_bytes = canonical_tsv_bytes([row])
    seal: dict[str, object] = {
        "schema_version": schema["manifest_schema_version"],
        "proposal_manifest_id": row["proposal_manifest_id"],
        "proposal_tsv_sha256": sha256_bytes(manifest_bytes),
        "processing_request_id": row["processing_request_id"],
        "processing_attempt_id": row["processing_attempt_id"],
        "tracer_id": row["tracer_id"],
        "tracer_version": row["tracer_version"],
        "tracer_config_sha256": row["tracer_config_sha256"],
        "row_count": "1",
        "distinct_peak_hypothesis_count": "1",
        "distinct_acquisition_source_count": "1",
        "precision_denominator_rule_id": "cid_nl_tested_tracer_select_value_v1",
        "candidate_universe_complete": "TRUE",
        "machine_mapping_revealed_to_reviewer": "FALSE",
        "legacy_mapping_revealed_to_reviewer": "FALSE",
        "sealed_at_utc": "2026-07-25T00:00:00Z",
        "sealed_by": "custodian",
        "seal_status": "sealed",
        "supersedes_manifest_id": "none",
    }
    artifacts = {
        "proposal_tsv_sha256": manifest_bytes,
        "tracer_config_sha256": config_bytes,
    }
    validate_manifest_seal(
        schema,
        seal,
        artifact_bytes_by_sha_field=artifacts,
        manifest_rows=[row],
    )

    with pytest.raises(
        OracleContractError,
        match="distinct_peak_hypothesis_count",
    ):
        validate_manifest_seal(
            schema,
            {**seal, "distinct_peak_hypothesis_count": "999"},
            artifact_bytes_by_sha_field=artifacts,
            manifest_rows=[row],
        )


def test_review_label_seal_binds_event_logs_and_counts() -> None:
    schema = json.loads(
        (
            ROOT
            / "docs/superpowers/schemas/"
            "cid_nl_oracle_review_adjudication_schema.v1.json"
        ).read_text(encoding="utf-8")
    )
    reviews = [
        _local_review(review_record_id="R1"),
        _local_review(review_record_id="R2"),
        _local_review(review_record_id="R3"),
    ]
    adjudications = [_adjudication()]
    review_bytes = canonical_tsv_bytes(reviews)
    adjudication_bytes = canonical_tsv_bytes(adjudications)
    seal = {
        "schema_version": schema["label_seal_schema_version"],
        "oracle_packet_id": "OP1",
        "holdout_manifest_seal_id": "HS1",
        "review_event_log_sha256": sha256_bytes(review_bytes),
        "adjudication_event_log_sha256": sha256_bytes(adjudication_bytes),
        "primary_confirmatory_case_count": "1",
        "hidden_duplicate_presentation_count": "0",
        "invalid_before_reveal_case_count": "0",
        "unassessable_primary_case_count": "0",
        "source_roles_revealed": "FALSE",
        "machine_mapping_revealed": "FALSE",
        "legacy_mapping_revealed": "FALSE",
        "sealed_at_utc": "2026-07-25T00:00:00Z",
        "sealed_by": "custodian",
        "seal_status": "sealed",
        "supersedes_label_seal_id": "none",
    }
    artifacts = {
        "review_event_log_sha256": review_bytes,
        "adjudication_event_log_sha256": adjudication_bytes,
    }
    validate_review_label_seal(
        schema,
        seal,
        artifact_bytes_by_sha_field=artifacts,
        review_rows=reviews,
        adjudication_rows=adjudications,
    )

    with pytest.raises(
        OracleContractError,
        match="primary_confirmatory_case_count",
    ):
        validate_review_label_seal(
            schema,
            {**seal, "primary_confirmatory_case_count": "2"},
            artifact_bytes_by_sha_field=artifacts,
            review_rows=reviews,
            adjudication_rows=adjudications,
        )
    with pytest.raises(
        OracleContractError,
        match="review log bytes do not match",
    ):
        validate_review_label_seal(
            schema,
            seal,
            artifact_bytes_by_sha_field=artifacts,
            review_rows=[*reviews, _local_review(review_record_id="R4")],
            adjudication_rows=adjudications,
        )
    with pytest.raises(OracleContractError, match="packet ID"):
        validate_review_label_seal(
            schema,
            {**seal, "oracle_packet_id": "OP2"},
            artifact_bytes_by_sha_field=artifacts,
            review_rows=reviews,
            adjudication_rows=adjudications,
        )


def test_statistical_design_validator_rejects_wrong_uncertainty_dispatch() -> None:
    row = {
        "schema_version": "cid_nl_oracle_statistical_design_manifest_v1",
        "statistical_design_manifest_id": "SD1",
        "estimand_id": "proposal_precision",
        "gate_id": "proposal_precision",
        "claim_label": "proposal precision",
        "target_population": "sealed proposals",
        "method_scope": "nl116_only",
        "workflow_policy": "untargeted",
        "evaluation_unit": "proposal",
        "success_event": "supported proposal",
        "frame_id": "FRAME1",
        "frame_completeness": "complete",
        "sampling_design_type": "stratified_probability_sample",
        "point_estimator": "weighted_ratio",
        "uncertainty_method": "design_preserving_cluster_resampling",
        "threshold_parameter_id": "proposal_precision_floor",
        "threshold_value": "0.8",
        "comparator": "greater_than_or_equal",
        "planning_method": "precision_target",
        "design_value": "0.8",
        "power": "not_applicable",
        "target_interval_width": "0.1",
        "minimum_independent_unit_count": "20",
        "parameter_source": "owner_policy_seal",
        "primary_cluster_key": "family_group_id",
        "secondary_grouping_keys": "batch_group_id|study_group_id",
        "stratum_weight_rule": "inverse_inclusion_probability",
        "machine_hold_handling": "retain_in_denominator",
        "human_unassessable_handling": "report_separately",
        "confidence_level": "0.95",
        "confidence_sidedness": "one_sided_lower",
        "incomplete_frame_claim_downgrade": "not_applicable",
        "hidden_duplicate_handling": "repeatability_only",
        "adaptive_supplemental_handling": "descriptive_only",
        "frozen_before_reveal": "TRUE",
        "maximum_review_budget": "100",
        "source_row_sha256": "",
    }
    row["source_row_sha256"] = canonical_row_sha256(row)
    validate_statistical_design_row(row)

    with pytest.raises(OracleContractError, match="incompatible"):
        invalid = {**row, "uncertainty_method": "wilson_score"}
        invalid["source_row_sha256"] = canonical_row_sha256(invalid)
        validate_statistical_design_row(invalid)
    with pytest.raises(OracleContractError, match="confidence_sidedness"):
        invalid = {**row, "confidence_sidedness": "unknown"}
        invalid["source_row_sha256"] = canonical_row_sha256(invalid)
        validate_statistical_design_row(invalid)


def test_sampling_rows_bind_to_matching_estimand_frame_and_design() -> None:
    design = {
        "schema_version": "cid_nl_oracle_statistical_design_manifest_v1",
        "statistical_design_manifest_id": "SD1",
        "estimand_id": "reference_recovery",
        "gate_id": "reference_recovery",
        "claim_label": "reference recovery",
        "target_population": "sealed reference positives",
        "method_scope": "nl116_only",
        "workflow_policy": "untargeted",
        "evaluation_unit": "PeakHypothesis_x_AcquisitionSource_x_policy",
        "success_event": "recovered",
        "frame_id": "FRAME1",
        "frame_completeness": "complete",
        "sampling_design_type": "stratified_probability_sample",
        "point_estimator": "weighted_ratio",
        "uncertainty_method": "design_preserving_cluster_resampling",
        "threshold_parameter_id": "reference_recovery_floor",
        "threshold_value": "0.8",
        "comparator": "greater_than_or_equal",
        "planning_method": "precision_target",
        "design_value": "0.8",
        "power": "not_applicable",
        "target_interval_width": "0.1",
        "minimum_independent_unit_count": "20",
        "parameter_source": "owner_policy_seal",
        "primary_cluster_key": "family_group_id",
        "secondary_grouping_keys": "batch_group_id|study_group_id",
        "stratum_weight_rule": "inverse_inclusion_probability",
        "machine_hold_handling": "retain_in_denominator",
        "human_unassessable_handling": "report_separately",
        "confidence_level": "0.95",
        "confidence_sidedness": "one_sided_lower",
        "incomplete_frame_claim_downgrade": "not_applicable",
        "hidden_duplicate_handling": "repeatability_only",
        "adaptive_supplemental_handling": "descriptive_only",
        "frozen_before_reveal": "TRUE",
        "maximum_review_budget": "100",
        "source_row_sha256": "",
    }
    design["source_row_sha256"] = canonical_row_sha256(design)
    validate_sampling_statistical_binding(
        [_sampling()],
        [design],
        gate_table_bytes=GATE_TABLE.read_bytes(),
        reference_rows=[_reference_for_sampling()],
    )
    for reference in (
        _reference_for_sampling(sampling_frame_size="99"),
        _reference_for_sampling(stratum_frame_size="19"),
        _reference_for_sampling(sampling_stratum="rna_only"),
    ):
        with pytest.raises(OracleContractError, match="reference universe"):
            validate_sampling_statistical_binding(
                [_sampling()],
                [design],
                gate_table_bytes=GATE_TABLE.read_bytes(),
                reference_rows=[reference],
            )
    second_sampling = _sampling(
        case_id="C2",
        presentation_id="P2",
        acquisition_source_id="AS2",
        sample_key="S2",
        sampling_stratum="rna_only",
        stratum_frame_size="30",
    )
    second_reference = _reference_for_sampling(
        reference_row_id="RR2",
        reference_family_id="RF2",
        acquisition_source_id="AS2",
        sample_key="S2",
        sampling_stratum="rna_only",
        stratum_frame_size="30",
    )
    validate_sampling_statistical_binding(
        [_sampling(), second_sampling],
        [design],
        gate_table_bytes=GATE_TABLE.read_bytes(),
        reference_rows=[
            _reference_for_sampling(),
            second_reference,
        ],
    )
    inconsistent_sampling = {
        **second_sampling,
        "sampling_frame_size": "200",
    }
    with pytest.raises(
        OracleContractError,
        match="sampling strata disagree",
    ):
        validate_sampling_statistical_binding(
            [_sampling(), inconsistent_sampling],
            [design],
            gate_table_bytes=GATE_TABLE.read_bytes(),
            reference_rows=[
                _reference_for_sampling(),
                second_reference,
            ],
        )
    inconsistent_reference = _reference_for_sampling(
        reference_row_id="RR2",
        reference_family_id="RF2",
        acquisition_source_id="AS2",
        sample_key="S2",
        sampling_frame_size="200",
        sampling_stratum="rna_only",
        stratum_frame_size="30",
    )
    with pytest.raises(
        OracleContractError,
        match="reference strata disagree",
    ):
        validate_sampling_statistical_binding(
            [_sampling(), second_sampling],
            [design],
            gate_table_bytes=GATE_TABLE.read_bytes(),
            reference_rows=[
                _reference_for_sampling(),
                inconsistent_reference,
            ],
        )
    for field, value in (
        ("evaluation_unit", "wrong_unit"),
        ("comparator", "less_than_or_equal"),
        ("threshold_parameter_id", "wrong_parameter"),
    ):
        mutated = {**design, field: value, "source_row_sha256": ""}
        mutated["source_row_sha256"] = canonical_row_sha256(mutated)
        with pytest.raises(OracleContractError, match="mismatches frozen gate"):
            validate_sampling_statistical_binding(
                [_sampling()],
                [mutated],
                gate_table_bytes=GATE_TABLE.read_bytes(),
                reference_rows=[_reference_for_sampling()],
            )
    second_gate = {
        **design,
        "gate_id": "automated_selective_risk",
        "source_row_sha256": "",
    }
    second_gate["source_row_sha256"] = canonical_row_sha256(second_gate)
    with pytest.raises(OracleContractError, match="mismatches frozen gate"):
        validate_sampling_statistical_binding(
            [_sampling()],
            [design, second_gate],
            gate_table_bytes=GATE_TABLE.read_bytes(),
            reference_rows=[_reference_for_sampling()],
        )
    same_shape_other_denominator = {
        **design,
        "gate_id": "proposal_precision",
        "claim_label": "proposal precision",
        "target_population": "sealed proposals",
        "success_event": "supported proposal",
        "threshold_parameter_id": "proposal_precision_floor",
        "source_row_sha256": "",
    }
    same_shape_other_denominator["source_row_sha256"] = canonical_row_sha256(
        same_shape_other_denominator
    )
    with pytest.raises(OracleContractError, match="inconsistent uncertainty"):
        validate_sampling_statistical_binding(
            [_sampling()],
            [design, same_shape_other_denominator],
            gate_table_bytes=GATE_TABLE.read_bytes(),
            reference_rows=[_reference_for_sampling()],
        )

    wrong_fixed_threshold = {
        **design,
        "estimand_id": "area_retention",
        "gate_id": "area_retention",
        "claim_label": "area retention",
        "target_population": "eligible observed accepted peaks",
        "evaluation_unit": "PeakHypothesis_x_AcquisitionSource",
        "success_event": "area retained",
        "threshold_parameter_id": "not_applicable",
        "threshold_value": "0.10",
        "source_row_sha256": "",
    }
    wrong_fixed_threshold["source_row_sha256"] = canonical_row_sha256(
        wrong_fixed_threshold
    )
    with pytest.raises(OracleContractError, match="threshold value"):
        validate_sampling_statistical_binding(
            [_sampling(estimand_id="area_retention")],
            [wrong_fixed_threshold],
            gate_table_bytes=GATE_TABLE.read_bytes(),
            reference_rows=[_reference_for_sampling()],
        )

    with pytest.raises(OracleContractError, match="frame"):
        validate_sampling_statistical_binding(
            [_sampling(sampling_frame_id="OTHER")],
            [design],
            gate_table_bytes=GATE_TABLE.read_bytes(),
            reference_rows=[_reference_for_sampling()],
        )
    with pytest.raises(OracleContractError, match="method scope"):
        validate_sampling_statistical_binding(
            [_sampling(method_scope="nl132_only")],
            [design],
            gate_table_bytes=GATE_TABLE.read_bytes(),
            reference_rows=[_reference_for_sampling()],
        )
    other_frame_design = {
        **design,
        "frame_id": "OTHER",
        "source_row_sha256": "",
    }
    other_frame_design["source_row_sha256"] = canonical_row_sha256(
        other_frame_design
    )
    with pytest.raises(OracleContractError, match="reference universe"):
        validate_sampling_statistical_binding(
            [_sampling(sampling_frame_id="OTHER")],
            [other_frame_design],
            gate_table_bytes=GATE_TABLE.read_bytes(),
            reference_rows=[_reference_for_sampling()],
        )
