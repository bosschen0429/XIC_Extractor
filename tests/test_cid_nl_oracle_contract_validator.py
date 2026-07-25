from __future__ import annotations

import json
from pathlib import Path

import pytest

from xic_extractor.discovery.cid_nl_oracle_contract import (
    OracleContractError,
    canonical_row_sha256,
    canonical_tsv_bytes,
    sha256_bytes,
    sha256_canonical_json,
    validate_adjudication_event,
    validate_adjudication_event_set,
    validate_lineage_binding_row,
    validate_manifest_seal,
    validate_proposal_row,
    validate_reference_manifest_rows,
    validate_reference_row,
    validate_review_event,
    validate_review_event_set,
    validate_sampling_manifest_rows,
    validate_sampling_row,
    validate_sampling_statistical_binding,
    validate_statistical_design_row,
)

ROOT = Path(__file__).resolve().parents[1]


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
        "reference_universe_id": "RU1",
        "reference_row_id": "RR1",
        "reference_family_id": "RF1",
        "acquisition_source_id": "AS1",
        "method_scope": "nl116_only",
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
        "reference_source_type": "blinded_systematic_review",
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
        "source_row_sha256": "",
    }
    row.update(overrides)
    row["source_row_sha256"] = canonical_row_sha256(row)
    return row


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


def _local_review(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "review_record_id": "R1",
        "presentation_id": "P1",
        "review_phase": "sample_local_blinded",
        "analysis_role": "primary_confirmatory",
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


def _local_label_seal(local: dict[str, object]) -> dict[str, object]:
    return {
        "local_label_seal_id": "LS1",
        "machine_mapping_revealed": "FALSE",
        "legacy_mapping_revealed": "FALSE",
        "review_record_hashes": {
            str(local["review_record_id"]): canonical_row_sha256(local)
        },
    }


def _adjudication(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
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


def test_full_reference_recovery_requires_cross_row_completeness_seal() -> None:
    row = _reference(recovery_claim_label="full_bounded_reference_recovery")
    with pytest.raises(OracleContractError, match="verified complete"):
        _validate_reference(row)

    manifest_bytes = canonical_tsv_bytes([row])
    seal_sources = {
        "bounded_dataset_manifest_sha256": b"bounded dataset",
        "method_scope_sha256": b"method scope",
        "source_manifest_sha256": b"source manifest",
        "reference_enumerator_dependency_manifest_sha256": (
            _reference_artifacts()["DEP1"]
        ),
        "universe_construction_protocol_sha256": b"construction protocol",
    }
    seal = {
        "schema_version": "cid_nl_reference_universe_manifest_v1",
        "reference_universe_id": "RU1",
        "reference_universe_tsv_sha256": sha256_bytes(manifest_bytes),
        **{
            field: sha256_bytes(payload)
            for field, payload in seal_sources.items()
        },
        "independence_audit_passed": "TRUE",
        "unresolved_shared_candidate_or_decision_overlap_count": "0",
        "sampling_frame_size": "1",
        "frame_completeness": "complete",
        "frame_size_by_stratum_sha256": sha256_canonical_json({"all": 1}),
        "row_count": "1",
        "source_roles_revealed_to_reviewer": "FALSE",
        "machine_mapping_revealed_to_reviewer": "FALSE",
        "legacy_mapping_revealed_to_reviewer": "FALSE",
        "sealed_at_utc": "2026-07-25T00:00:00Z",
        "sealed_by": "custodian",
        "seal_status": "sealed",
    }
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
            _policy_review(observation_state="absent"),
            local_record=local,
            local_label_seal=seal,
        )
    with pytest.raises(OracleContractError, match="requires leave_missing"):
        absent_local = {**local, "observation_state": "absent"}
        validate_review_event(
            _policy_review(
                local_record=absent_local,
                observation_state="absent",
                targeted_projection="hold",
                untargeted_projection="hold",
            ),
            local_record=absent_local,
            local_label_seal=_local_label_seal(absent_local),
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
        case_role="hidden_duplicate",
        analysis_role="repeatability_only",
        primary_presentation="FALSE",
        duplicate_of_case_id="C1",
    )
    validate_sampling_manifest_rows([primary, duplicate])

    with pytest.raises(OracleContractError, match="exactly one primary"):
        validate_sampling_manifest_rows(
            [primary, {**primary, "primary_presentation": "TRUE"}]
        )


def test_hidden_duplicate_requires_both_review_phases() -> None:
    local = _local_review(
        review_record_id="RD1",
        presentation_id="PD1",
        review_phase="hidden_duplicate_sample_local_blinded",
        analysis_role="repeatability_only",
    )
    policy = _policy_review(
        local_record=local,
        presentation_id="PD1",
        review_phase="hidden_duplicate_policy_blinded",
        analysis_role="repeatability_only",
        local_review_record_id="RD1",
    )
    validate_review_event_set(
        [local, policy],
        sampling_rows=[
            _sampling(
                presentation_id="PD1",
                case_role="hidden_duplicate",
                analysis_role="repeatability_only",
                primary_presentation="FALSE",
                duplicate_of_case_id="C1",
            )
        ],
        local_label_seals_by_id={"LS1": _local_label_seal(local)},
    )

    with pytest.raises(OracleContractError, match="complete"):
        validate_review_event_set(
            [local],
            sampling_rows=[
                _sampling(
                    presentation_id="PD1",
                    case_role="hidden_duplicate",
                    analysis_role="repeatability_only",
                    primary_presentation="FALSE",
                    duplicate_of_case_id="C1",
                )
            ],
            local_label_seals_by_id={"LS1": _local_label_seal(local)},
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


def test_lineage_gate_result_requires_gate_specific_binding_fields() -> None:
    row = {
        "oracle_packet_id": "P1",
        "binding_id": "B1",
        "binding_role": "gate_result",
        "processing_request_id": "REQ1",
        "processing_attempt_id": "ATT1",
        "evidence_artifact_id": "ART1",
        "artifact_schema_version": "v1",
        "artifact_semantic_role": "gate_result",
        "artifact_sha256": "a" * 64,
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
    validate_manifest_seal(
        schema,
        seal,
        artifact_bytes_by_sha_field=bound_bytes,
    )

    with pytest.raises(OracleContractError, match="must be SHA256"):
        validate_manifest_seal(
            schema,
            {**seal, "narrow_scope_tsv_sha256": "not-a-hash"},
            artifact_bytes_by_sha_field=bound_bytes,
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
        "evaluation_unit": "reference case",
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
    validate_sampling_statistical_binding([_sampling()], [design])

    with pytest.raises(OracleContractError, match="frame"):
        validate_sampling_statistical_binding(
            [_sampling(sampling_frame_id="OTHER")],
            [design],
        )
