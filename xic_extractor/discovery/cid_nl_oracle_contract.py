"""Executable no-RAW contracts for the CID-NL oracle packet.

These validators reject malformed diagnostic manifests and reduce frozen gate
results. They do not discover candidates, read RAW files, or grant product
authority.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CANONICAL_GATE_TABLE_SHA256 = (
    "b1e3e67cc0051b9eb9b0bf41bf9163477ed6b3da66582b19cba74b0fcd7f4b99"
)
_CANONICAL_VERDICT_TABLE_SHA256 = (
    "5100fb80ae7ade82df50e2971891b20be639c4753150cf1a2d307de8dc6f33e0"
)
_MANIFEST_PAYLOAD_SHA256_FIELDS = {
    "cid_nl_tested_tracer_proposal_manifest_v1": "proposal_tsv_sha256",
    "cid_nl_formal_holdout_sampling_manifest_v1": "holdout_tsv_sha256",
    "cid_nl_reference_universe_manifest_v1": "reference_universe_tsv_sha256",
    "cid_nl_oracle_narrow_scope_manifest_v1": "narrow_scope_tsv_sha256",
    "cid_nl_oracle_lineage_binding_v1": "lineage_binding_tsv_sha256",
    "cid_nl_oracle_statistical_design_manifest_v1": (
        "statistical_design_tsv_sha256"
    ),
}
_OBSERVATION_STATES = {
    "observed",
    "missing",
    "absent",
    "unassessable",
    "ambiguous",
    "failed",
}
_PROJECTIONS = {"select_value", "leave_missing", "hold"}
_SAMPLE_MATERIAL_STRATA = {
    "dna_only_biological",
    "dna_and_rna_mixed_biological",
    "pooled_qc",
    "not_applicable",
}
_REFERENCE_SOURCE_TYPES = {
    "fixed_spike_internal_standard",
    "authentic_standard",
    "frozen_identity_profile",
    "attributable_library_ms2",
    "independent_benchmark",
    "blinded_systematic_review",
    "matched_negative",
}
_PHYSICAL_PRESENCE_BASES = {
    "fixed_spike_design",
    "authentic_standard_design",
    "sample_local_observed_evidence",
    "unknown",
    "not_applicable",
}
_METHOD_DETECTABILITY_BASES = {
    "in_scope_expected_control",
    "sample_local_supported",
    "out_of_method_scope",
    "unknown",
    "not_applicable",
}
_SAMPLE_LOCAL_EVIDENCE_STATUSES = {
    "supported",
    "unsupported",
    "unassessable",
    "not_reviewed",
    "not_applicable",
}
_REFERENCE_LABELS = {
    "expected_positive_control",
    "independently_supported_positive",
    "supported_negative",
    "unassessable",
    "method_out_of_scope",
    "excluded",
}
_REFERENCE_CLAIM_SCOPES = {
    "sample_local_presence_and_method_detectability",
    "sample_local_observed_feature",
    "identity_profile_support_only",
    "matched_negative_only",
    "scope_exclusion_only",
}
_UNIVERSE_CONSTRUCTION_METHODS = {
    "complete_control_census",
    "independent_bounded_enumeration",
    "probability_sample_from_independent_frame",
    "preregistered_challenge_only",
}
_SHARED_STAGE_STATUSES = {"independent", "shared_declared", "unknown"}
_SHARED_LOGIC_DISPOSITIONS = {
    "independent_recovery_eligible",
    "exclude_from_independent_recovery",
}
_FRAME_COMPLETENESS_STATES = {
    "complete",
    "incomplete_known_undercoverage",
    "unknown",
}
_RECOVERY_CLAIM_LABELS = {
    "full_bounded_reference_recovery",
    "known_reference_recovery",
    "expected_control_recovery_only",
    "not_applicable",
}

_PROPOSAL_REQUIRED_FIELDS = (
    "schema_version",
    "proposal_manifest_id",
    "proposal_row_id",
    "processing_request_id",
    "processing_attempt_id",
    "acquisition_source_id",
    "sample_key",
    "tracer_id",
    "tracer_version",
    "tracer_config_sha256",
    "kernel_object_ref",
    "peak_hypothesis_id",
    "hypothesis_identity_sha256",
    "workflow_policy",
    "precursor_mz",
    "neutral_loss_da",
    "observation_state",
    "observation_origin",
    "selection_state",
    "projection_outcome",
    "evidence_artifact_ids",
    "source_row_sha256",
)

_SAMPLING_REQUIRED_FIELDS = (
    "schema_version",
    "holdout_manifest_id",
    "case_id",
    "presentation_id",
    "case_role",
    "analysis_role",
    "primary_presentation",
    "source_universe",
    "machine_conditioned_source",
    "estimand_id",
    "sampling_design_type",
    "sampling_frame_id",
    "sampling_frame_size",
    "sampling_stratum",
    "stratum_frame_size",
    "independent_hypothesis_group_id",
    "family_group_id",
    "batch_group_id",
    "study_group_id",
    "instrument_group_id",
    "dependency_group_ids",
    "acquisition_source_id",
    "sample_key",
    "sample_material_stratum",
    "method_scope",
    "precursor_mz",
    "neutral_loss_da",
    "search_rt_start_min",
    "search_rt_end_min",
    "evidence_scope",
    "case_evidence_artifact_ids",
    "case_locator_sha256",
    "review_order",
)

_REFERENCE_REQUIRED_FIELDS = (
    "schema_version",
    "reference_universe_id",
    "reference_row_id",
    "reference_family_id",
    "acquisition_source_id",
    "sample_key",
    "sample_material_stratum",
    "method_scope",
    "precursor_mz",
    "neutral_loss_da",
    "reference_source_type",
    "reference_source_id",
    "reference_source_artifact_ids",
    "reference_source_sha256s",
    "physical_presence_basis",
    "method_detectability_basis",
    "sample_local_evidence_status",
    "reference_label",
    "reference_claim_scope",
    "universe_construction_method",
    "reference_enumerator_dependency_manifest_id",
    "reference_enumerator_dependency_manifest_sha256",
    "shared_raw_adapter_status",
    "shared_candidate_generation_status",
    "shared_decision_logic_status",
    "shared_logic_overlap_disposition",
    "uses_tested_tracer_output_for_membership",
    "uses_legacy_output_for_membership",
    "uses_library_record_as_sample_presence",
    "sampling_frame_id",
    "sampling_frame_size",
    "sampling_stratum",
    "stratum_frame_size",
    "frame_completeness",
    "recovery_claim_label",
    "independent_hypothesis_group_id",
    "batch_group_id",
    "study_group_id",
    "instrument_group_id",
    "source_row_sha256",
)

_REVIEW_REQUIRED_FIELDS = (
    "schema_version",
    "oracle_packet_id",
    "holdout_manifest_id",
    "case_id",
    "presentation_id",
    "review_record_id",
    "review_phase",
    "analysis_role",
    "reviewer_id",
    "reviewed_at_utc",
    "review_status",
    "evidence_sufficiency",
    "feature_identity_decision",
    "observation_state",
    "peak_decision",
    "reason_codes",
    "evidence_artifact_ids",
    "evidence_bundle_sha256",
    "source_sampling_row_sha256",
    "source_roles_visible",
    "machine_mapping_visible",
    "legacy_mapping_visible",
    "evaluated_system_scores_visible",
    "source_row_sha256",
)

_ADJUDICATION_REQUIRED_FIELDS = (
    "schema_version",
    "oracle_packet_id",
    "adjudication_record_id",
    "case_id",
    "adjudication_type",
    "trigger_review_record_ids",
    "trigger_reason_codes",
    "adjudicator_id",
    "adjudicated_at_utc",
    "machine_mapping_visible",
    "legacy_mapping_visible",
    "packet_effect",
    "source_row_sha256",
)

_NARROW_SCOPE_REQUIRED_FIELDS = (
    "schema_version",
    "narrow_scope_manifest_id",
    "narrow_scope_id",
    "scope_precedence",
    "claim_label",
    "membership_predicate",
    "scope_membership_manifest_id",
    "scope_membership_manifest_sha256",
    "source_universe",
    "workflow_policy",
    "method_scope",
    "included_sampling_strata",
    "excluded_sampling_strata",
    "required_gate_ids",
    "allowed_overall_failed_gate_ids",
    "minimum_independent_units_parameter",
    "gatekeeping_rule_id",
    "membership_frozen_before_reveal",
    "machine_mapping_visible_at_seal",
    "source_row_sha256",
)

_NARROW_SCOPE_SEAL_REQUIRED_FIELDS = (
    "schema_version",
    "narrow_scope_manifest_id",
    "narrow_scope_tsv_sha256",
    "scope_membership_bundle_sha256",
    "scope_count",
    "all_membership_manifests_present",
    "all_scope_gate_sets_resolvable",
    "machine_mapping_revealed",
    "holdout_labels_revealed",
    "sealed_at_utc",
    "sealed_by",
    "seal_status",
    "supersedes_narrow_scope_manifest_id",
)

_REFERENCE_SEAL_REQUIRED_FIELDS = (
    "schema_version",
    "reference_universe_id",
    "reference_universe_tsv_sha256",
    "bounded_dataset_manifest_sha256",
    "method_scope_sha256",
    "source_manifest_sha256",
    "reference_enumerator_dependency_manifest_sha256",
    "universe_construction_protocol_sha256",
    "frame_completeness",
    "sampling_frame_size",
    "frame_size_by_stratum_sha256",
    "undercoverage_disposition",
    "recovery_claim_label",
    "row_count",
    "eligible_positive_row_count",
    "expected_positive_control_count",
    "independently_supported_positive_count",
    "supported_negative_count",
    "unassessable_count",
    "method_out_of_scope_count",
    "independence_audit_passed",
    "unresolved_shared_candidate_or_decision_overlap_count",
    "source_roles_revealed_to_reviewer",
    "machine_mapping_revealed_to_reviewer",
    "legacy_mapping_revealed_to_reviewer",
    "sealed_at_utc",
    "sealed_by",
    "seal_status",
    "supersedes_reference_universe_id",
)

_LINEAGE_REQUIRED_FIELDS = (
    "schema_version",
    "oracle_packet_id",
    "binding_id",
    "binding_role",
    "processing_request_id",
    "processing_attempt_id",
    "evidence_artifact_id",
    "artifact_schema_version",
    "artifact_semantic_role",
    "artifact_sha256",
    "upstream_evidence_artifact_ids",
    "staleness_status",
    "source_row_sha256",
)

_STATISTICAL_REQUIRED_FIELDS = (
    "schema_version",
    "statistical_design_manifest_id",
    "estimand_id",
    "gate_id",
    "claim_label",
    "target_population",
    "method_scope",
    "workflow_policy",
    "evaluation_unit",
    "success_event",
    "frame_id",
    "frame_completeness",
    "sampling_design_type",
    "point_estimator",
    "uncertainty_method",
    "confidence_sidedness",
    "confidence_level",
    "threshold_parameter_id",
    "threshold_value",
    "comparator",
    "planning_method",
    "design_value",
    "power",
    "target_interval_width",
    "minimum_independent_unit_count",
    "primary_cluster_key",
    "secondary_grouping_keys",
    "stratum_weight_rule",
    "machine_hold_handling",
    "human_unassessable_handling",
    "adaptive_supplemental_handling",
    "hidden_duplicate_handling",
    "incomplete_frame_claim_downgrade",
    "maximum_review_budget",
    "parameter_source",
    "frozen_before_reveal",
    "source_row_sha256",
)


class OracleContractError(ValueError):
    """Raised when a CID-NL oracle row violates the frozen contract."""


def validate_proposal_row(row: Mapping[str, Any]) -> None:
    """Validate one tested-tracer proposal row."""

    _require_schema_version(
        row,
        "cid_nl_tested_tracer_proposal_manifest_v1",
    )
    _require_fields(row, _PROPOSAL_REQUIRED_FIELDS)
    for field in (
        "tracer_config_sha256",
        "hypothesis_identity_sha256",
        "source_row_sha256",
    ):
        if not _SHA256_RE.fullmatch(_required_text(row, field)):
            raise OracleContractError(f"{field} must be SHA256")
    if _required_text(row, "workflow_policy") not in {"targeted", "untargeted"}:
        raise OracleContractError("invalid workflow_policy")
    if _required_text(row, "observation_origin") not in {
        "discovery",
        "alignment",
        "backfill",
    }:
        raise OracleContractError("invalid observation_origin")
    validate_canonical_row_hash(row)
    _validate_proposal_crosswalk(row)


def validate_proposal_manifest_rows(
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Validate proposal-manifest identity and denominator cardinality."""

    primary_keys: set[tuple[str, str]] = set()
    scientific_keys: set[tuple[str, str, str, str]] = set()
    manifest_bindings: dict[str, tuple[str, str, str, str, str]] = {}
    for row in rows:
        validate_proposal_row(row)
        manifest_id = _required_text(row, "proposal_manifest_id")
        primary_key = (manifest_id, _required_text(row, "proposal_row_id"))
        if primary_key in primary_keys:
            raise OracleContractError("duplicate proposal primary key")
        primary_keys.add(primary_key)
        scientific_key = (
            _required_text(row, "processing_request_id"),
            _required_text(row, "kernel_object_ref"),
            _required_text(row, "acquisition_source_id"),
            _required_text(row, "workflow_policy"),
        )
        if scientific_key in scientific_keys:
            raise OracleContractError("duplicate proposal scientific key")
        scientific_keys.add(scientific_key)
        binding = (
            _required_text(row, "processing_request_id"),
            _required_text(row, "processing_attempt_id"),
            _required_text(row, "tracer_id"),
            _required_text(row, "tracer_version"),
            _required_text(row, "tracer_config_sha256"),
        )
        previous = manifest_bindings.setdefault(manifest_id, binding)
        if previous != binding:
            raise OracleContractError(
                "proposal manifest mixes request, attempt, or tracer identity"
            )
    if len(manifest_bindings) > 1:
        raise OracleContractError("proposal rows mix manifest IDs")


def _validate_proposal_crosswalk(row: Mapping[str, Any]) -> None:
    """Validate proposal state/value mechanics without manifest identity."""

    observation = _required_text(row, "observation_state")
    selection = _required_text(row, "selection_state")
    projection = _required_text(row, "projection_outcome")
    if observation not in _OBSERVATION_STATES:
        raise OracleContractError("invalid observation_state")
    if selection not in {"selected", "not_selected", "no_selection"}:
        raise OracleContractError("invalid selection_state")
    if projection not in _PROJECTIONS:
        raise OracleContractError("invalid projection_outcome")

    if projection == "select_value":
        if observation != "observed" or selection != "selected":
            raise OracleContractError(
                "select_value requires observed state and selected hypothesis"
            )
        left = _finite_number(row, "candidate_left_rt_min")
        apex = _finite_number(row, "candidate_apex_rt_min")
        right = _finite_number(row, "candidate_right_rt_min")
        area = _finite_number(row, "candidate_area")
        if not left <= apex <= right or left == right:
            raise OracleContractError("selected apex must be inside boundaries")
        if area < 0:
            raise OracleContractError("selected area must be non-negative")
    elif projection == "leave_missing":
        if observation != "absent" or selection != "no_selection":
            raise OracleContractError(
                "leave_missing requires absent state and no_selection"
            )
    elif observation == "absent":
        raise OracleContractError("absent state requires leave_missing")
    elif observation in {"missing", "unassessable", "ambiguous", "failed"}:
        if projection != "hold" or selection == "selected":
            raise OracleContractError(
                f"{observation} requires hold and forbids selected state"
            )
    elif observation == "observed" and selection != "selected":
        if projection != "hold":
            raise OracleContractError(
                "observed without eligible selection requires hold"
            )


def validate_sampling_row(row: Mapping[str, Any]) -> None:
    """Validate one formal holdout presentation and its sampling design."""

    _require_schema_version(
        row,
        "cid_nl_formal_holdout_sampling_manifest_v1",
    )
    _require_fields(row, _SAMPLING_REQUIRED_FIELDS)
    for field in ("case_locator_sha256",):
        if not _SHA256_RE.fullmatch(_required_text(row, field)):
            raise OracleContractError(f"{field} must be SHA256")
    role = _required_text(row, "case_role")
    analysis_role = _required_text(row, "analysis_role")
    if role not in {
        "challenge",
        "random_representative",
        "hidden_duplicate",
        "adaptive_supplemental",
    }:
        raise OracleContractError("invalid case_role")
    if analysis_role not in {
        "primary_confirmatory",
        "repeatability_only",
        "supplemental_descriptive",
    }:
        raise OracleContractError("invalid analysis_role")
    if _required_text(row, "sample_material_stratum") not in (
        _SAMPLE_MATERIAL_STRATA
    ):
        raise OracleContractError("invalid sample_material_stratum")
    _boolean(row, "machine_conditioned_source")
    primary = _boolean(row, "primary_presentation")
    design = _required_text(row, "sampling_design_type")
    frame_size = _positive_integer(row, "sampling_frame_size")
    stratum_size = _positive_integer(row, "stratum_frame_size")
    if stratum_size > frame_size:
        raise OracleContractError("stratum_frame_size cannot exceed frame size")

    probability = _optional_number(row, "inclusion_probability")
    weight = _optional_number(row, "analysis_weight")
    if design == "finite_census":
        if probability != 1.0 or weight != 1.0:
            raise OracleContractError("finite census requires probability=weight=1")
    elif design in {
        "independent_equal_probability_sample",
        "stratified_probability_sample",
        "clustered_probability_sample",
    }:
        if probability is None or not 0 < probability <= 1:
            raise OracleContractError(
                "probability sample requires inclusion_probability in (0, 1]"
            )
        if weight is None or not math.isclose(
            weight,
            1.0 / probability,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise OracleContractError("analysis_weight must equal 1/pi")
    elif design == "nonprobability_challenge":
        if analysis_role == "primary_confirmatory":
            raise OracleContractError(
                "nonprobability challenge cannot enter representative metrics"
            )
    elif design == "incomplete_reference_frame":
        if analysis_role != "supplemental_descriptive":
            raise OracleContractError(
                "incomplete reference frame rows are descriptive only"
            )
    else:
        raise OracleContractError("invalid sampling_design_type")

    group_fields = (
        "independent_hypothesis_group_id",
        "family_group_id",
        "batch_group_id",
        "study_group_id",
        "instrument_group_id",
    )
    if not all(_required_text(row, field) for field in group_fields):
        raise OracleContractError("all dependency grouping keys are required")

    duplicate_of = str(row.get("duplicate_of_case_id", "") or "").strip()
    if role == "hidden_duplicate":
        if not duplicate_of or primary or analysis_role != "repeatability_only":
            raise OracleContractError(
                "hidden duplicate must link its case, be non-primary, "
                "and be repeatability_only"
            )
    elif duplicate_of:
        raise OracleContractError(
            "non-hidden-duplicate row must not set duplicate_of_case_id"
        )
    if role == "adaptive_supplemental" and (
        analysis_role != "supplemental_descriptive"
    ):
        raise OracleContractError(
            "adaptive supplemental row must be supplemental_descriptive"
        )
    for field in (
        "source_role_hidden_from_reviewer",
        "machine_mapping_hidden_from_reviewer",
        "legacy_mapping_hidden_from_reviewer",
    ):
        if not _boolean(row, field):
            raise OracleContractError(f"{field} must be TRUE at seal")

    source_universe = _required_text(row, "source_universe")
    reference_type = str(row.get("reference_source_type", "") or "").strip()
    reference_scope = str(row.get("reference_claim_scope", "") or "").strip()
    if source_universe == "independent_reference":
        if not reference_type or not reference_scope:
            raise OracleContractError(
                "independent reference row requires source type and claim scope"
            )
    elif source_universe == "tested_tracer_proposal":
        if reference_type or reference_scope:
            raise OracleContractError(
                "tested-tracer proposal row forbids reference claim fields"
            )
    elif source_universe == "matched_negative":
        if not reference_scope:
            raise OracleContractError(
                "matched-negative row requires reference claim scope"
            )
    elif source_universe != "contract_challenge":
        raise OracleContractError("invalid source_universe")


def validate_sampling_manifest_rows(
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Validate cross-row primary/duplicate invariants."""

    by_case: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    presentation_keys: set[tuple[str, str]] = set()
    manifest_ids: set[str] = set()
    frame_invariants: dict[tuple[str, str], tuple[int, str]] = {}
    stratum_sizes: dict[tuple[str, str, str], int] = {}
    for row in rows:
        validate_sampling_row(row)
        manifest_id = _required_text(row, "holdout_manifest_id")
        manifest_ids.add(manifest_id)
        frame_id = _required_text(row, "sampling_frame_id")
        frame_key = (manifest_id, frame_id)
        frame_invariant = (
            _positive_integer(row, "sampling_frame_size"),
            _required_text(row, "method_scope"),
        )
        if (
            frame_invariants.setdefault(frame_key, frame_invariant)
            != frame_invariant
        ):
            raise OracleContractError(
                "sampling rows disagree on frame-level invariants"
            )
        stratum_key = (
            manifest_id,
            frame_id,
            _required_text(row, "sampling_stratum"),
        )
        stratum_size = _positive_integer(row, "stratum_frame_size")
        if stratum_sizes.setdefault(stratum_key, stratum_size) != stratum_size:
            raise OracleContractError(
                "sampling rows disagree on stratum frame size"
            )
        presentation_key = (
            manifest_id,
            _required_text(row, "presentation_id"),
        )
        if presentation_key in presentation_keys:
            raise OracleContractError("duplicate presentation primary key")
        presentation_keys.add(presentation_key)
        case_key = (
            _required_text(row, "holdout_manifest_id"),
            _required_text(row, "case_id"),
        )
        by_case.setdefault(case_key, []).append(row)
    if len(manifest_ids) > 1:
        raise OracleContractError("sampling rows mix holdout manifest IDs")
    for (_, case_id), case_rows in by_case.items():
        primary_count = sum(
            _boolean(row, "primary_presentation") for row in case_rows
        )
        if primary_count != 1:
            raise OracleContractError(
                f"{case_id} must have exactly one primary presentation"
            )
        primary_row = next(
            row for row in case_rows if _boolean(row, "primary_presentation")
        )
        for row in case_rows:
            if row.get("case_role") == "hidden_duplicate":
                if row.get("duplicate_of_case_id") != case_id:
                    raise OracleContractError(
                        "hidden duplicate must reuse its underlying case_id"
                    )
                if _required_text(
                    row,
                    "case_locator_sha256",
                ) != _required_text(primary_row, "case_locator_sha256"):
                    raise OracleContractError(
                        "hidden duplicate must reuse its scientific locator"
                    )
                scientific_locator_fields = (
                    "source_universe",
                    "acquisition_source_id",
                    "sample_key",
                    "sample_material_stratum",
                    "method_scope",
                    "reference_source_type",
                    "reference_claim_scope",
                    "precursor_mz",
                    "neutral_loss_da",
                    "search_rt_start_min",
                    "search_rt_end_min",
                    "evidence_scope",
                    "case_evidence_artifact_ids",
                    "independent_hypothesis_group_id",
                    "family_group_id",
                    "batch_group_id",
                    "study_group_id",
                    "instrument_group_id",
                    "dependency_group_ids",
                )
                if any(
                    str(row.get(field, "") or "").strip()
                    != str(primary_row.get(field, "") or "").strip()
                    for field in scientific_locator_fields
                ):
                    raise OracleContractError(
                        "hidden duplicate scientific locator fields differ"
                    )


def validate_sampling_statistical_binding(
    sampling_rows: Sequence[Mapping[str, Any]],
    statistical_rows: Sequence[Mapping[str, Any]],
    *,
    gate_table_bytes: bytes,
    reference_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Bind every sampled case to exactly one compatible estimand design."""

    if sha256_bytes(gate_table_bytes) != _CANONICAL_GATE_TABLE_SHA256:
        raise OracleContractError("gate table bytes are not canonical v1")
    gate_definitions = {
        _required_text(row, "gate_id"): row
        for row in _parse_tsv_bytes(gate_table_bytes)
    }
    designs: dict[str, list[Mapping[str, Any]]] = {}
    design_manifest_ids: set[str] = set()
    design_primary_keys: set[tuple[str, str, str]] = set()
    for statistical_row in statistical_rows:
        validate_statistical_design_row(statistical_row)
        gate_id = _required_text(statistical_row, "gate_id")
        gate_definition = gate_definitions.get(gate_id)
        if gate_definition is None:
            raise OracleContractError("statistical design gate is not frozen")
        for design_field, gate_field in (
            ("evaluation_unit", "evaluation_unit"),
            ("comparator", "comparator"),
        ):
            if _required_text(
                statistical_row,
                design_field,
            ) != _required_text(gate_definition, gate_field):
                raise OracleContractError(
                    f"statistical {design_field} mismatches frozen gate"
                )
        if (
            gate_definition.get("threshold_kind")
            == "preregistered_parameter"
            and _required_text(
                statistical_row,
                "threshold_parameter_id",
            )
            != _required_text(gate_definition, "threshold_parameter")
        ):
            raise OracleContractError(
                "statistical threshold parameter mismatches frozen gate"
            )
        if (
            gate_definition.get("threshold_kind") == "fixed"
            and not _threshold_values_equal(
                _required_text(statistical_row, "threshold_value"),
                _required_text(gate_definition, "threshold_value"),
            )
        ):
            raise OracleContractError(
                "statistical threshold value mismatches frozen gate"
            )
        estimand_id = _required_text(statistical_row, "estimand_id")
        primary_key = (
            _required_text(
                statistical_row,
                "statistical_design_manifest_id",
            ),
            estimand_id,
            gate_id,
        )
        if primary_key in design_primary_keys:
            raise OracleContractError("duplicate statistical design primary key")
        design_primary_keys.add(primary_key)
        design_manifest_ids.add(primary_key[0])
        designs.setdefault(estimand_id, []).append(statistical_row)
    if len(design_manifest_ids) > 1:
        raise OracleContractError("statistical rows mix design manifest IDs")
    route_fields = (
        "method_scope",
        "workflow_policy",
        "claim_label",
        "target_population",
        "success_event",
        "frame_id",
        "frame_completeness",
        "sampling_design_type",
        "point_estimator",
        "uncertainty_method",
        "primary_cluster_key",
        "secondary_grouping_keys",
        "stratum_weight_rule",
    )
    for estimand_id, estimand_rows in designs.items():
        route_signatures = {
            tuple(_required_text(row, field) for field in route_fields)
            for row in estimand_rows
        }
        if len(route_signatures) != 1:
            raise OracleContractError(
                f"{estimand_id} has inconsistent uncertainty routes"
            )
    reference_frame_signatures: dict[
        tuple[str, str],
        tuple[int, int, str, str],
    ] = {}
    reference_frame_invariants: dict[str, tuple[int, str, str]] = {}
    for reference_row in reference_rows:
        _require_schema_version(
            reference_row,
            "cid_nl_reference_universe_manifest_v1",
        )
        _require_fields(reference_row, _REFERENCE_REQUIRED_FIELDS)
        validate_canonical_row_hash(reference_row)
        frame_key = (
            _required_text(reference_row, "sampling_frame_id"),
            _required_text(reference_row, "sampling_stratum"),
        )
        signature = (
            _positive_integer(reference_row, "sampling_frame_size"),
            _positive_integer(reference_row, "stratum_frame_size"),
            _required_text(reference_row, "method_scope"),
            _required_text(reference_row, "frame_completeness"),
        )
        previous_signature = reference_frame_signatures.setdefault(
            frame_key,
            signature,
        )
        if previous_signature != signature:
            raise OracleContractError(
                "reference rows disagree on frame signature"
            )
        frame_invariant = (
            signature[0],
            signature[2],
            signature[3],
        )
        previous_invariant = reference_frame_invariants.setdefault(
            frame_key[0],
            frame_invariant,
        )
        if previous_invariant != frame_invariant:
            raise OracleContractError(
                "reference strata disagree on frame-level invariants"
            )
    sampling_frame_invariants: dict[str, tuple[int, str, str]] = {}
    for sampling_row in sampling_rows:
        validate_sampling_row(sampling_row)
        estimand_id = _required_text(sampling_row, "estimand_id")
        design_rows = designs.get(estimand_id)
        if not design_rows:
            raise OracleContractError("sampling estimand has no statistical design")
        design_row = design_rows[0]
        sampling_frame_id = _required_text(
            sampling_row,
            "sampling_frame_id",
        )
        sampling_frame_invariant = (
            _positive_integer(sampling_row, "sampling_frame_size"),
            _required_text(sampling_row, "method_scope"),
            _required_text(design_row, "frame_completeness"),
        )
        previous_sampling_invariant = sampling_frame_invariants.setdefault(
            sampling_frame_id,
            sampling_frame_invariant,
        )
        if previous_sampling_invariant != sampling_frame_invariant:
            raise OracleContractError(
                "sampling strata disagree on frame-level invariants"
            )
        if _required_text(sampling_row, "method_scope") != _required_text(
            design_row,
            "method_scope",
        ):
            raise OracleContractError("sampling method scope does not match estimand")
        if _required_text(sampling_row, "sampling_frame_id") != _required_text(
            design_row,
            "frame_id",
        ):
            raise OracleContractError("sampling frame does not match estimand frame")
        if sampling_row.get("source_universe") == "independent_reference":
            frame_key = (
                sampling_frame_id,
                _required_text(sampling_row, "sampling_stratum"),
            )
            reference_signature = reference_frame_signatures.get(frame_key)
            if reference_signature is None:
                raise OracleContractError(
                    "sampling frame or stratum does not match reference universe"
                )
            expected_signature = (
                _positive_integer(sampling_row, "sampling_frame_size"),
                _positive_integer(sampling_row, "stratum_frame_size"),
                _required_text(sampling_row, "method_scope"),
                _required_text(design_row, "frame_completeness"),
            )
            if reference_signature != expected_signature:
                raise OracleContractError(
                    "sampling frame signature does not match reference universe"
                )
        if _required_text(sampling_row, "sampling_design_type") != _required_text(
            design_row,
            "sampling_design_type",
        ):
            raise OracleContractError(
                "sampling design does not match estimand uncertainty route"
            )
        cluster_key = _required_text(design_row, "primary_cluster_key")
        if cluster_key not in sampling_row:
            raise OracleContractError(
                "sampling row lacks preregistered primary cluster key"
            )
        _required_text(sampling_row, cluster_key)
        for secondary_key in _tokens(design_row, "secondary_grouping_keys"):
            if secondary_key not in sampling_row:
                raise OracleContractError(
                    "sampling row lacks preregistered secondary grouping key"
                )
            _required_text(sampling_row, secondary_key)
        weight_rule = _required_text(design_row, "stratum_weight_rule")
        if weight_rule == "inverse_inclusion_probability":
            probability = _optional_number(
                sampling_row,
                "inclusion_probability",
            )
            weight = _optional_number(sampling_row, "analysis_weight")
            if probability is None or weight is None or not math.isclose(
                weight,
                1.0 / probability,
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                raise OracleContractError(
                    "sampling row violates preregistered stratum weight rule"
                )
        elif weight_rule == "unit_weight_census":
            if (
                sampling_row.get("sampling_design_type") != "finite_census"
                or _optional_number(sampling_row, "inclusion_probability") != 1.0
                or _optional_number(sampling_row, "analysis_weight") != 1.0
            ):
                raise OracleContractError(
                    "unit_weight_census requires finite census with unit weight"
                )
        elif weight_rule == "descriptive_only":
            if sampling_row.get("sampling_design_type") not in {
                "nonprobability_challenge",
                "incomplete_reference_frame",
            }:
                raise OracleContractError(
                    "descriptive_only weight rule requires descriptive design"
                )
        else:
            raise OracleContractError("invalid stratum_weight_rule")


def validate_reference_row(
    row: Mapping[str, Any],
    *,
    artifact_bytes_by_id: Mapping[str, bytes],
) -> None:
    """Validate one independent reference-universe row."""

    _validate_reference_row(
        row,
        artifact_bytes_by_id=artifact_bytes_by_id,
        full_recovery_manifest_verified=False,
    )


def _validate_reference_row(
    row: Mapping[str, Any],
    *,
    artifact_bytes_by_id: Mapping[str, bytes],
    full_recovery_manifest_verified: bool,
) -> None:
    """Validate a row; only the manifest validator may unlock full recovery."""

    _require_schema_version(
        row,
        "cid_nl_reference_universe_manifest_v1",
    )
    _require_fields(row, _REFERENCE_REQUIRED_FIELDS)
    validate_canonical_row_hash(row)
    enum_fields = {
        "sample_material_stratum": _SAMPLE_MATERIAL_STRATA,
        "reference_source_type": _REFERENCE_SOURCE_TYPES,
        "physical_presence_basis": _PHYSICAL_PRESENCE_BASES,
        "method_detectability_basis": _METHOD_DETECTABILITY_BASES,
        "sample_local_evidence_status": _SAMPLE_LOCAL_EVIDENCE_STATUSES,
        "reference_label": _REFERENCE_LABELS,
        "reference_claim_scope": _REFERENCE_CLAIM_SCOPES,
        "universe_construction_method": _UNIVERSE_CONSTRUCTION_METHODS,
        "shared_raw_adapter_status": _SHARED_STAGE_STATUSES,
        "shared_candidate_generation_status": _SHARED_STAGE_STATUSES,
        "shared_decision_logic_status": _SHARED_STAGE_STATUSES,
        "shared_logic_overlap_disposition": _SHARED_LOGIC_DISPOSITIONS,
        "frame_completeness": _FRAME_COMPLETENESS_STATES,
        "recovery_claim_label": _RECOVERY_CLAIM_LABELS,
    }
    for field, allowed in enum_fields.items():
        if _required_text(row, field) not in allowed:
            raise OracleContractError(f"invalid {field}")
    source_ids = _tokens(row, "reference_source_artifact_ids")
    source_hashes = _tokens(row, "reference_source_sha256s")
    if not source_ids or len(source_ids) != len(source_hashes):
        raise OracleContractError("reference source IDs and hashes must align")
    for artifact_id, expected_hash in zip(
        source_ids,
        source_hashes,
        strict=True,
    ):
        _validate_bound_bytes(
            artifact_bytes_by_id,
            artifact_id,
            expected_hash,
        )

    for field in (
        "uses_tested_tracer_output_for_membership",
        "uses_legacy_output_for_membership",
        "uses_library_record_as_sample_presence",
    ):
        if _boolean(row, field):
            raise OracleContractError(f"{field} must be FALSE")

    dependency_id = _required_text(
        row,
        "reference_enumerator_dependency_manifest_id",
    )
    dependency_hash = _required_text(
        row,
        "reference_enumerator_dependency_manifest_sha256",
    )
    if not _SHA256_RE.fullmatch(dependency_hash):
        raise OracleContractError("dependency manifest digest must be SHA256")
    _validate_bound_bytes(
        artifact_bytes_by_id,
        dependency_id,
        dependency_hash,
    )
    try:
        dependency_manifest = json.loads(
            artifact_bytes_by_id[dependency_id].decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OracleContractError(
            "dependency manifest must be auditable JSON"
        ) from exc
    if not isinstance(dependency_manifest, Mapping):
        raise OracleContractError("dependency manifest must be an object")
    for field in (
        "uses_tested_tracer_output_for_membership",
        "uses_legacy_output_for_membership",
        "uses_library_record_as_sample_presence",
    ):
        if _boolean(dependency_manifest, field):
            raise OracleContractError(
                "dependency manifest declares forbidden membership dependency"
            )
    shared_raw = _required_text(row, "shared_raw_adapter_status")
    shared_candidate = _required_text(row, "shared_candidate_generation_status")
    shared_decision = _required_text(row, "shared_decision_logic_status")
    for field, status in (
        ("shared_raw_adapter_status", shared_raw),
        ("shared_candidate_generation_status", shared_candidate),
        ("shared_decision_logic_status", shared_decision),
    ):
        if status not in {"independent", "shared_declared", "unknown"}:
            raise OracleContractError(f"invalid {field}")
        if dependency_manifest.get(field) != status:
            raise OracleContractError(
                f"dependency manifest mismatch for {field}"
            )
    overlap = _required_text(row, "shared_logic_overlap_disposition")
    if dependency_manifest.get("shared_logic_overlap_disposition") != overlap:
        raise OracleContractError("dependency overlap disposition mismatch")
    stages = dependency_manifest.get("stages")
    if not isinstance(stages, Mapping):
        raise OracleContractError("dependency manifest requires stage identities")
    for stage_name, expected_status in (
        ("raw_adapter", shared_raw),
        ("candidate_generation", shared_candidate),
        ("decision_logic", shared_decision),
    ):
        stage = stages.get(stage_name)
        if not isinstance(stage, Mapping):
            raise OracleContractError(f"missing dependency stage: {stage_name}")
        for field in ("component_id", "version", "config_sha256"):
            _required_text(stage, field)
        if not _SHA256_RE.fullmatch(str(stage["config_sha256"])):
            raise OracleContractError("stage config digest must be SHA256")
        if stage.get("status") != expected_status:
            raise OracleContractError("dependency stage status mismatch")
    if shared_candidate != "independent" or shared_decision != "independent":
        if overlap != "exclude_from_independent_recovery":
            raise OracleContractError(
                "shared or unknown candidate/decision logic must be excluded "
                "from independent recovery"
            )
    elif overlap != "independent_recovery_eligible":
        raise OracleContractError(
            "independent candidate and decision logic require eligible disposition"
        )

    construction = _required_text(row, "universe_construction_method")
    if construction in {
        "independent_bounded_enumeration",
        "probability_sample_from_independent_frame",
    }:
        enumerator_id = _required_text(row, "independent_enumerator_id")
        _required_text(row, "independent_enumerator_version")
        config_hash = _required_text(
            row,
            "independent_enumerator_config_sha256",
        )
        if not _SHA256_RE.fullmatch(config_hash):
            raise OracleContractError("enumerator config digest must be SHA256")
        _validate_bound_bytes(
            artifact_bytes_by_id,
            f"enumerator-config:{enumerator_id}",
            config_hash,
        )
    elif construction not in {
        "complete_control_census",
        "preregistered_challenge_only",
    }:
        raise OracleContractError("invalid universe_construction_method")

    label = _required_text(row, "reference_label")
    source_type = _required_text(row, "reference_source_type")
    presence = _required_text(row, "physical_presence_basis")
    detectability = _required_text(row, "method_detectability_basis")
    local_evidence = _required_text(row, "sample_local_evidence_status")
    claim = _required_text(row, "reference_claim_scope")
    if label == "expected_positive_control":
        if presence not in {"fixed_spike_design", "authentic_standard_design"}:
            raise OracleContractError(
                "expected control requires fixed-spike or authentic-standard basis"
            )
        if detectability != "in_scope_expected_control":
            raise OracleContractError(
                "expected control requires in-scope method detectability"
            )
    if label == "independently_supported_positive":
        if local_evidence != "supported":
            raise OracleContractError(
                "supported positive requires supported sample-local evidence"
            )
        if source_type in {"attributable_library_ms2", "frozen_identity_profile"}:
            if presence != "sample_local_observed_evidence":
                raise OracleContractError(
                    "library/profile source alone cannot assert sample presence"
                )
        _required_text(row, "review_record_id")
    if label in {"unassessable", "method_out_of_scope", "excluded"} and (
        claim
        in {
            "sample_local_presence_and_method_detectability",
            "sample_local_observed_feature",
        }
    ):
        raise OracleContractError(
            f"{label} cannot carry a positive sample-local claim"
        )

    completeness = _required_text(row, "frame_completeness")
    recovery_claim = _required_text(row, "recovery_claim_label")
    frame_size = _positive_integer(row, "sampling_frame_size")
    stratum_size = _positive_integer(row, "stratum_frame_size")
    if stratum_size > frame_size:
        raise OracleContractError("stratum_frame_size cannot exceed frame size")
    if completeness == "complete":
        if (
            recovery_claim == "full_bounded_reference_recovery"
            and not full_recovery_manifest_verified
        ):
            raise OracleContractError(
                "full recovery requires verified complete reference manifest"
            )
    elif completeness in {"incomplete_known_undercoverage", "unknown"}:
        if recovery_claim == "full_bounded_reference_recovery":
            raise OracleContractError(
                "incomplete or unknown frame forbids full recovery claim"
            )
        _required_text(row, "undercoverage_disposition")
    else:
        raise OracleContractError("invalid frame_completeness")

    probability = _optional_number(row, "inclusion_probability")
    weight = _optional_number(row, "analysis_weight")
    if construction == "complete_control_census":
        if probability != 1.0 or weight != 1.0:
            raise OracleContractError(
                "complete control census requires probability=weight=1"
            )
    elif construction == "probability_sample_from_independent_frame":
        if probability is None or not 0 < probability <= 1:
            raise OracleContractError(
                "probability reference sample requires pi in (0, 1]"
            )
        if weight is None or not math.isclose(
            weight,
            1.0 / probability,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise OracleContractError("reference analysis_weight must equal 1/pi")


def validate_reference_manifest_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    seal: Mapping[str, Any],
    manifest_bytes: bytes,
    artifact_bytes_by_id: Mapping[str, bytes],
    seal_artifact_bytes_by_sha_field: Mapping[str, bytes],
) -> None:
    """Verify completeness before permitting a full bounded recovery claim."""

    if not rows:
        raise OracleContractError("reference manifest cannot be empty")
    _require_fields(seal, _REFERENCE_SEAL_REQUIRED_FIELDS)
    _require_schema_version(
        seal,
        "cid_nl_reference_universe_manifest_v1",
    )
    if seal.get("seal_status") != "sealed":
        raise OracleContractError("reference universe must be sealed")
    universe_id = _required_text(seal, "reference_universe_id")
    for field in (
        "source_roles_revealed_to_reviewer",
        "machine_mapping_revealed_to_reviewer",
        "legacy_mapping_revealed_to_reviewer",
    ):
        if _boolean(seal, field):
            raise OracleContractError("reference universe was revealed before seal")
    for field in (
        "bounded_dataset_manifest_sha256",
        "method_scope_sha256",
        "source_manifest_sha256",
        "reference_enumerator_dependency_manifest_sha256",
        "universe_construction_protocol_sha256",
    ):
        if not _SHA256_RE.fullmatch(_required_text(seal, field)):
            raise OracleContractError(f"{field} must be SHA256")
        payload = seal_artifact_bytes_by_sha_field.get(field)
        if payload is None or sha256_bytes(payload) != _required_text(
            seal,
            field,
        ):
            raise OracleContractError(f"{field} does not bind source bytes")
    if manifest_bytes != canonical_tsv_bytes(rows):
        raise OracleContractError("reference manifest bytes do not match rows")
    if sha256_bytes(manifest_bytes) != _required_text(
        seal,
        "reference_universe_tsv_sha256",
    ):
        raise OracleContractError("reference manifest digest mismatch")
    if not _boolean(seal, "independence_audit_passed"):
        raise OracleContractError("independence audit must pass")
    unresolved_overlap = _required_text(
        seal,
        "unresolved_shared_candidate_or_decision_overlap_count",
    )
    if int(unresolved_overlap):
        raise OracleContractError("shared candidate/decision overlap remains")

    primary_keys: set[tuple[str, str]] = set()
    scientific_keys: set[tuple[str, str, str]] = set()
    sampling_frame_ids: set[str] = set()
    stratum_sizes: dict[str, int] = {}
    stratum_counts: dict[str, int] = {}
    reference_label_counts: dict[str, int] = {}
    for row in rows:
        if _required_text(row, "reference_universe_id") != universe_id:
            raise OracleContractError("reference row/seal universe mismatch")
        primary_key = (
            universe_id,
            _required_text(row, "reference_row_id"),
        )
        if primary_key in primary_keys:
            raise OracleContractError("duplicate reference primary key")
        primary_keys.add(primary_key)
        sampling_frame_ids.add(_required_text(row, "sampling_frame_id"))
        key = (
            _required_text(row, "reference_family_id"),
            _required_text(row, "acquisition_source_id"),
            _required_text(row, "method_scope"),
        )
        if key in scientific_keys:
            raise OracleContractError("duplicate reference scientific key")
        scientific_keys.add(key)
        stratum = _required_text(row, "sampling_stratum")
        size = _positive_integer(row, "stratum_frame_size")
        previous = stratum_sizes.setdefault(stratum, size)
        if previous != size:
            raise OracleContractError("inconsistent stratum frame size")
        stratum_counts[stratum] = stratum_counts.get(stratum, 0) + 1
        label = _required_text(row, "reference_label")
        reference_label_counts[label] = reference_label_counts.get(label, 0) + 1

    frame_size = _positive_integer(seal, "sampling_frame_size")
    if len(sampling_frame_ids) != 1:
        raise OracleContractError(
            "reference manifest mixes sampling frame IDs"
        )
    for row in rows:
        if _positive_integer(row, "sampling_frame_size") != frame_size:
            raise OracleContractError(
                "reference row frame size does not match seal"
            )
        if (
            _required_text(row, "frame_completeness") != "complete"
            or _required_text(row, "recovery_claim_label")
            != "full_bounded_reference_recovery"
        ):
            raise OracleContractError(
                "full reference manifest requires complete-frame rows"
            )
    if len(rows) != frame_size or sum(stratum_sizes.values()) != frame_size:
        raise OracleContractError("complete frame size does not match membership")
    if stratum_counts != stratum_sizes:
        raise OracleContractError("stratum membership count does not match frame")
    if _required_text(seal, "frame_completeness") != "complete":
        raise OracleContractError("full manifest verification requires complete frame")
    if (
        _required_text(seal, "recovery_claim_label")
        != "full_bounded_reference_recovery"
    ):
        raise OracleContractError(
            "full manifest seal requires full recovery claim"
        )
    expected_strata_hash = sha256_canonical_json(stratum_sizes)
    if expected_strata_hash != _required_text(
        seal,
        "frame_size_by_stratum_sha256",
    ):
        raise OracleContractError("stratum frame digest mismatch")
    if int(_required_text(seal, "row_count")) != len(rows):
        raise OracleContractError("reference seal row_count mismatch")
    expected_counts = {
        "eligible_positive_row_count": (
            reference_label_counts.get("expected_positive_control", 0)
            + reference_label_counts.get(
                "independently_supported_positive",
                0,
            )
        ),
        "expected_positive_control_count": reference_label_counts.get(
            "expected_positive_control",
            0,
        ),
        "independently_supported_positive_count": reference_label_counts.get(
            "independently_supported_positive",
            0,
        ),
        "supported_negative_count": reference_label_counts.get(
            "supported_negative",
            0,
        ),
        "unassessable_count": reference_label_counts.get("unassessable", 0),
        "method_out_of_scope_count": reference_label_counts.get(
            "method_out_of_scope",
            0,
        ),
    }
    for field, expected_count in expected_counts.items():
        if _nonnegative_integer(seal, field) != expected_count:
            raise OracleContractError(
                f"reference seal {field} does not match manifest"
            )

    for row in rows:
        _validate_reference_row(
            row,
            artifact_bytes_by_id=artifact_bytes_by_id,
            full_recovery_manifest_verified=True,
        )


def validate_review_event(
    row: Mapping[str, Any],
    *,
    local_record: Mapping[str, Any] | None = None,
    local_label_seal: Mapping[str, Any] | None = None,
) -> None:
    """Validate one blinded review event and its immutable local binding."""

    _require_schema_version(row, "cid_nl_oracle_review_event_v1")
    _require_fields(row, _REVIEW_REQUIRED_FIELDS)
    validate_canonical_row_hash(row)
    phase = _required_text(row, "review_phase")
    machine_visible = _boolean(row, "machine_mapping_visible")
    legacy_visible = _boolean(row, "legacy_mapping_visible")
    scores_visible = _boolean(row, "evaluated_system_scores_visible")
    source_visible = _boolean(row, "source_roles_visible")
    if _required_text(row, "review_status") not in {
        "completed",
        "invalid_before_reveal",
    }:
        raise OracleContractError("invalid review_status")
    if _required_text(row, "evidence_sufficiency") not in {
        "sufficient",
        "insufficient",
    }:
        raise OracleContractError("invalid evidence_sufficiency")
    if _required_text(row, "analysis_role") not in {
        "primary_confirmatory",
        "repeatability_only",
        "supplemental_descriptive",
    }:
        raise OracleContractError("invalid analysis_role")
    if phase != "unblinded_failure_taxonomy" and (
        machine_visible or legacy_visible or scores_visible or source_visible
    ):
        raise OracleContractError(
            "confirmatory review phases require all evaluated-system roles hidden"
        )

    sample_local_phases = {
        "sample_local_blinded",
        "hidden_duplicate_sample_local_blinded",
    }
    policy_phases = {
        "cross_sample_policy_blinded",
        "hidden_duplicate_policy_blinded",
    }
    if phase in sample_local_phases:
        _validate_local_label(row)
    elif phase in policy_phases:
        _validate_local_label(row)
        if local_record is None:
            raise OracleContractError(
                "policy review requires immutable sample-local record"
            )
        seal_id = _required_text(row, "local_label_seal_id")
        if local_label_seal is None:
            raise OracleContractError("policy review requires local label seal")
        if _required_text(local_label_seal, "local_label_seal_id") != seal_id:
            raise OracleContractError("local label seal id mismatch")
        if _boolean(local_label_seal, "machine_mapping_revealed") or _boolean(
            local_label_seal,
            "legacy_mapping_revealed",
        ):
            raise OracleContractError("local label seal must precede reveal")
        record_id = _required_text(row, "local_review_record_id")
        record_hash = _required_text(row, "local_review_record_sha256")
        if record_id != _required_text(local_record, "review_record_id"):
            raise OracleContractError("local review record id mismatch")
        for field in (
            "oracle_packet_id",
            "holdout_manifest_id",
            "case_id",
            "presentation_id",
        ):
            if _required_text(row, field) != _required_text(
                local_record,
                field,
            ):
                raise OracleContractError(f"local review {field} mismatch")
        if not _SHA256_RE.fullmatch(record_hash):
            raise OracleContractError("local review record digest must be SHA256")
        if record_hash != canonical_row_sha256(local_record):
            raise OracleContractError("local review record digest mismatch")
        sealed_hashes = local_label_seal.get("review_record_hashes")
        if not isinstance(sealed_hashes, Mapping) or str(
            sealed_hashes.get(record_id, "")
        ) != record_hash:
            raise OracleContractError("local record missing from label seal")
        for field in (
            "feature_identity_decision",
            "observation_state",
            "peak_decision",
            "human_apex_rt_min",
            "human_left_rt_min",
            "human_right_rt_min",
        ):
            if str(row.get(field, "")) != str(local_record.get(field, "")):
                raise OracleContractError(
                    f"policy phase cannot change sample-local {field}"
                )
        for policy in ("targeted_projection", "untargeted_projection"):
            _validate_projection_crosswalk(
                _required_text(row, "observation_state"),
                _required_text(row, policy),
            )
    elif phase == "unblinded_failure_taxonomy":
        _required_text(row, "predecessor_review_record_id")
    else:
        raise OracleContractError("invalid review_phase")

    analysis_role = _required_text(row, "analysis_role")
    if phase.startswith("hidden_duplicate_") and (
        analysis_role != "repeatability_only"
    ):
        raise OracleContractError(
            "hidden duplicate review must be repeatability_only"
        )


def validate_review_event_set(
    rows: Sequence[Mapping[str, Any]],
    *,
    sampling_rows: Sequence[Mapping[str, Any]],
    local_label_seals_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    """Validate local/policy bindings and complete duplicate review pairs."""

    review_by_id = {
        _required_text(row, "review_record_id"): row for row in rows
    }
    if len(review_by_id) != len(rows):
        raise OracleContractError("duplicate review_record_id")
    validate_sampling_manifest_rows(sampling_rows)
    sampling_by_presentation = {
        _required_text(row, "presentation_id"): row for row in sampling_rows
    }
    local_by_id = {
        _required_text(row, "review_record_id"): row
        for row in rows
        if row.get("review_phase")
        in {
            "sample_local_blinded",
            "hidden_duplicate_sample_local_blinded",
        }
    }
    local_positions = {
        _required_text(row, "review_record_id"): index
        for index, row in enumerate(rows)
        if row.get("review_phase")
        in {
            "sample_local_blinded",
            "hidden_duplicate_sample_local_blinded",
        }
    }
    prior_confirmatory_ids: set[str] = set()
    phases_by_presentation: dict[str, set[str]] = {}
    completed_phases_by_presentation: dict[str, set[str]] = {}
    for index, row in enumerate(rows):
        phase = _required_text(row, "review_phase")
        presentation = _required_text(row, "presentation_id")
        sampling_row = sampling_by_presentation.get(presentation)
        if sampling_row is None:
            raise OracleContractError(
                "review presentation is absent from sampling manifest"
            )
        for field in ("holdout_manifest_id", "case_id", "analysis_role"):
            if _required_text(row, field) != _required_text(
                sampling_row,
                field,
            ):
                raise OracleContractError(
                    f"review {field} does not match sampling manifest"
                )
        if _required_text(
            row,
            "source_sampling_row_sha256",
        ) != canonical_row_sha256(sampling_row):
            raise OracleContractError(
                "review does not bind its sampling row"
            )
        local_record = None
        if phase in {
            "cross_sample_policy_blinded",
            "hidden_duplicate_policy_blinded",
        }:
            local_id = _required_text(row, "local_review_record_id")
            local_record = local_by_id.get(local_id)
            if local_positions.get(local_id, len(rows)) >= index:
                raise OracleContractError(
                    "policy review requires an earlier sample-local event"
                )
        seal = None
        if phase in {
            "cross_sample_policy_blinded",
            "hidden_duplicate_policy_blinded",
        }:
            seal = local_label_seals_by_id.get(
                _required_text(row, "local_label_seal_id")
            )
        validate_review_event(
            row,
            local_record=local_record,
            local_label_seal=seal,
        )
        if phase == "unblinded_failure_taxonomy":
            predecessor_id = _required_text(
                row,
                "predecessor_review_record_id",
            )
            predecessor = review_by_id.get(predecessor_id)
            if (
                predecessor is None
                or predecessor_id not in prior_confirmatory_ids
            ):
                raise OracleContractError(
                    "taxonomy predecessor must be an earlier confirmatory event"
                )
            if predecessor.get("review_phase") not in {
                "cross_sample_policy_blinded",
                "hidden_duplicate_policy_blinded",
            } or predecessor.get("review_status") != "completed":
                raise OracleContractError(
                    "taxonomy requires an earlier completed policy review"
                )
            for field in (
                "oracle_packet_id",
                "holdout_manifest_id",
                "case_id",
                "presentation_id",
            ):
                if _required_text(row, field) != _required_text(
                    predecessor,
                    field,
                ):
                    raise OracleContractError(
                        f"taxonomy predecessor {field} mismatch"
                    )
            for field in (
                "feature_identity_decision",
                "observation_state",
                "peak_decision",
                "human_apex_rt_min",
                "human_left_rt_min",
                "human_right_rt_min",
                "targeted_projection",
                "untargeted_projection",
            ):
                if str(row.get(field, "")) != str(predecessor.get(field, "")):
                    raise OracleContractError(
                        "unblinded taxonomy cannot change confirmatory label"
                    )
        else:
            prior_confirmatory_ids.add(_required_text(row, "review_record_id"))
        presentation_phases = phases_by_presentation.setdefault(
            presentation,
            set(),
        )
        if phase in presentation_phases:
            raise OracleContractError(
                "duplicate review phase for presentation"
            )
        presentation_phases.add(phase)
        if row.get("review_status") == "completed":
            completed_phases_by_presentation.setdefault(
                presentation,
                set(),
            ).add(phase)

    required_primary_phases = {
        "sample_local_blinded",
        "cross_sample_policy_blinded",
    }
    required_duplicate_phases = {
        "hidden_duplicate_sample_local_blinded",
        "hidden_duplicate_policy_blinded",
    }
    for presentation, phases in phases_by_presentation.items():
        if phases & required_duplicate_phases and not (
            required_duplicate_phases
            <= completed_phases_by_presentation.get(presentation, set())
        ):
            raise OracleContractError(
                f"{presentation} hidden duplicate requires complete "
                "sample-local and policy phases"
            )
    expected_primaries = {
        _required_text(row, "presentation_id")
        for row in sampling_rows
        if _boolean(row, "primary_presentation")
        and row.get("analysis_role") == "primary_confirmatory"
    }
    for presentation in expected_primaries:
        if not required_primary_phases <= phases_by_presentation.get(
            presentation, set()
        ) or not required_primary_phases <= completed_phases_by_presentation.get(
            presentation, set()
        ):
            raise OracleContractError(
                f"{presentation} primary presentation requires complete "
                "sample-local and policy phases"
            )
    expected_duplicates = {
        _required_text(row, "presentation_id")
        for row in sampling_rows
        if row.get("case_role") == "hidden_duplicate"
    }
    observed_duplicates = {
        presentation
        for presentation, phases in phases_by_presentation.items()
        if phases & required_duplicate_phases
    }
    if observed_duplicates != expected_duplicates:
        raise OracleContractError(
            "hidden duplicate reviews do not match sealed sampling manifest"
        )


def validate_adjudication_event(row: Mapping[str, Any]) -> None:
    """Validate one append-only pre- or post-reveal adjudication event."""

    _require_schema_version(row, "cid_nl_oracle_adjudication_event_v1")
    _require_fields(row, _ADJUDICATION_REQUIRED_FIELDS)
    validate_canonical_row_hash(row)
    machine_visible = _boolean(row, "machine_mapping_visible")
    legacy_visible = _boolean(row, "legacy_mapping_visible")

    adjudication_type = _required_text(row, "adjudication_type")
    packet_effect = _required_text(row, "packet_effect")
    pre_reveal_types = {
        "pre_reveal_hidden_duplicate_inconsistency",
        "pre_reveal_inter_reviewer_disagreement",
        "pre_reveal_case_validity",
    }
    if adjudication_type in pre_reveal_types:
        if machine_visible or legacy_visible:
            raise OracleContractError(
                "pre-reveal adjudication requires machine and legacy mapping hidden"
            )
        if packet_effect == "confirm_frozen_label":
            for field in (
                "resulting_confirmatory_review_record_id",
                "adjudicated_dimension",
                "adjudicated_value",
                "adjudication_rule_id",
            ):
                _required_text(row, field)
        elif packet_effect == "invalidate_case_before_reveal":
            _required_text(row, "affected_gate_ids")
        else:
            raise OracleContractError(
                "pre-reveal adjudication must confirm or invalidate before reveal"
            )
    elif adjudication_type == "post_reveal_integrity_incident":
        if packet_effect != "packet_inconclusive":
            raise OracleContractError(
                "post-reveal integrity incident must make packet inconclusive"
            )
        if str(row.get("resulting_confirmatory_review_record_id", "") or "").strip():
            raise OracleContractError(
                "post-reveal incident cannot replace confirmatory label"
            )
        _required_text(row, "affected_gate_ids")
    else:
        raise OracleContractError("invalid adjudication_type")


def validate_adjudication_event_set(
    rows: Sequence[Mapping[str, Any]],
    *,
    review_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Validate append-only adjudication links over immutable review records."""

    for review_row in review_rows:
        _require_schema_version(
            review_row,
            "cid_nl_oracle_review_event_v1",
        )
        _require_fields(review_row, _REVIEW_REQUIRED_FIELDS)
        validate_canonical_row_hash(review_row)
    review_by_id = {
        _required_text(row, "review_record_id"): row for row in review_rows
    }
    if len(review_by_id) != len(review_rows):
        raise OracleContractError("duplicate review_record_id")
    adjudication_ids: set[str] = set()
    adjudication_by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        validate_adjudication_event(row)
        record_id = _required_text(row, "adjudication_record_id")
        if record_id in adjudication_ids:
            raise OracleContractError("duplicate adjudication_record_id")
        triggers = set(_tokens(row, "trigger_review_record_ids"))
        if not triggers or not triggers <= set(review_by_id):
            raise OracleContractError(
                "adjudication triggers must bind immutable review records"
            )
        packet_id = _required_text(row, "oracle_packet_id")
        case_id = _required_text(row, "case_id")
        pre_reveal = _required_text(row, "adjudication_type").startswith(
            "pre_reveal_"
        )
        for trigger_id in triggers:
            trigger = review_by_id[trigger_id]
            if pre_reveal:
                _validate_pre_reveal_review_reference(trigger)
            if (
                _required_text(trigger, "oracle_packet_id") != packet_id
                or _required_text(trigger, "case_id") != case_id
            ):
                raise OracleContractError(
                    "adjudication triggers must match packet and case"
                )
        resulting = str(
            row.get("resulting_confirmatory_review_record_id", "") or ""
        ).strip()
        if resulting and resulting not in review_by_id:
            raise OracleContractError(
                "adjudication result must reference immutable review record"
            )
        if resulting:
            result_row = review_by_id[resulting]
            _validate_pre_reveal_review_reference(result_row)
            if (
                _required_text(result_row, "oracle_packet_id") != packet_id
                or _required_text(result_row, "case_id") != case_id
            ):
                raise OracleContractError(
                    "adjudication result must match packet and case"
                )
            if (
                result_row.get("review_phase") != "sample_local_blinded"
                or result_row.get("analysis_role") != "primary_confirmatory"
                or result_row.get("review_status") != "completed"
            ):
                raise OracleContractError(
                    "adjudication result must be a primary blinded local review"
                )
        predecessor = str(
            row.get("predecessor_adjudication_record_id", "") or ""
        ).strip()
        if predecessor and predecessor not in adjudication_ids:
            raise OracleContractError(
                "adjudication predecessor must reference an earlier event"
            )
        superseding = str(row.get("superseding_packet_id", "") or "").strip()
        if predecessor and not superseding:
            raise OracleContractError(
                "adjudication correction requires superseding packet"
            )
        if predecessor:
            predecessor_row = adjudication_by_id[predecessor]
            if _required_text(predecessor_row, "case_id") != case_id:
                raise OracleContractError(
                    "adjudication predecessor case mismatch"
                )
            predecessor_packet = _required_text(
                predecessor_row,
                "oracle_packet_id",
            )
            if predecessor_packet == packet_id or superseding != packet_id:
                raise OracleContractError(
                    "adjudication correction must move to its superseding packet"
                )
        adjudication_ids.add(record_id)
        adjudication_by_id[record_id] = row


def _validate_pre_reveal_review_reference(
    row: Mapping[str, Any],
) -> None:
    """Require a completed, fully blinded immutable review record."""

    if _required_text(row, "review_phase") not in {
        "sample_local_blinded",
        "cross_sample_policy_blinded",
        "hidden_duplicate_sample_local_blinded",
        "hidden_duplicate_policy_blinded",
    } or _required_text(row, "review_status") != "completed":
        raise OracleContractError(
            "pre-reveal adjudication requires completed blinded review records"
        )
    if any(
        _boolean(row, field)
        for field in (
            "machine_mapping_visible",
            "legacy_mapping_visible",
            "evaluated_system_scores_visible",
            "source_roles_visible",
        )
    ):
        raise OracleContractError(
            "pre-reveal adjudication requires all evaluated-system roles hidden"
        )


def validate_narrow_scope_row(
    row: Mapping[str, Any],
    *,
    membership_bytes: bytes,
    manifest_bytes: bytes,
    manifest_seal: Mapping[str, Any],
) -> None:
    """Validate one preregistered candidate scope for a NARROW verdict."""

    _require_fields(row, _NARROW_SCOPE_REQUIRED_FIELDS)
    _require_fields(manifest_seal, _NARROW_SCOPE_SEAL_REQUIRED_FIELDS)
    _require_schema_version(
        row,
        "cid_nl_oracle_narrow_scope_manifest_v1",
    )
    _require_schema_version(
        manifest_seal,
        "cid_nl_oracle_narrow_scope_manifest_v1",
    )
    validate_canonical_row_hash(row)
    if not _boolean(row, "membership_frozen_before_reveal"):
        raise OracleContractError("narrow scope membership must be frozen")
    if _boolean(row, "machine_mapping_visible_at_seal"):
        raise OracleContractError("machine mapping must be hidden at scope seal")
    _positive_integer(row, "scope_precedence")
    _required_text(row, "membership_predicate")
    digest = _required_text(row, "scope_membership_manifest_sha256")
    if not _SHA256_RE.fullmatch(digest):
        raise OracleContractError("scope membership digest must be SHA256")
    if not membership_bytes.strip() or sha256_bytes(membership_bytes) != digest:
        raise OracleContractError("scope membership bytes do not match digest")
    _validate_scope_membership_bytes(membership_bytes)
    if _required_text(manifest_seal, "seal_status") != "sealed":
        raise OracleContractError("narrow scope manifest must be sealed")
    if sha256_bytes(manifest_bytes) != _required_text(
        manifest_seal,
        "narrow_scope_tsv_sha256",
    ):
        raise OracleContractError("narrow scope manifest digest mismatch")
    manifest_rows = _parse_tsv_bytes(manifest_bytes)
    if int(_required_text(manifest_seal, "scope_count")) != len(
        manifest_rows
    ):
        raise OracleContractError("narrow scope seal count mismatch")
    normalized_row = {str(key): str(value) for key, value in row.items()}
    if normalized_row not in manifest_rows:
        raise OracleContractError("narrow scope row is not sealed in manifest")
    membership_digests = manifest_seal.get("scope_membership_digests")
    if not isinstance(membership_digests, Mapping):
        raise OracleContractError("scope membership digest map is required")
    normalized_digests = {
        str(key): str(value) for key, value in membership_digests.items()
    }
    if normalized_digests.get(
        _required_text(row, "scope_membership_manifest_id")
    ) != digest:
        raise OracleContractError("membership digest missing from sealed bundle")
    if sha256_canonical_json(normalized_digests) != _required_text(
        manifest_seal,
        "scope_membership_bundle_sha256",
    ):
        raise OracleContractError("scope membership bundle digest mismatch")
    _required_text(manifest_seal, "sealed_at_utc")
    _required_text(manifest_seal, "sealed_by")
    for field in (
        "all_membership_manifests_present",
        "all_scope_gate_sets_resolvable",
    ):
        if not _boolean(manifest_seal, field):
            raise OracleContractError(f"{field} must be TRUE")
    for field in ("machine_mapping_revealed", "holdout_labels_revealed"):
        if _boolean(manifest_seal, field):
            raise OracleContractError(f"{field} must be FALSE at seal")
    if _required_text(manifest_seal, "narrow_scope_manifest_id") != _required_text(
        row,
        "narrow_scope_manifest_id",
    ):
        raise OracleContractError("narrow scope row/seal manifest mismatch")
    if not _tokens(row, "required_gate_ids"):
        raise OracleContractError("narrow scope requires at least one gate")
    if not _tokens(row, "allowed_overall_failed_gate_ids"):
        raise OracleContractError(
            "narrow scope must declare which overall failures it can survive"
        )


def validate_lineage_binding_row(row: Mapping[str, Any]) -> None:
    """Validate role-specific #123 lineage bindings for oracle artifacts."""

    _require_schema_version(row, "cid_nl_oracle_lineage_binding_v1")
    _require_fields(row, _LINEAGE_REQUIRED_FIELDS)
    validate_canonical_row_hash(row)
    for field in ("artifact_sha256", "source_row_sha256"):
        if not _SHA256_RE.fullmatch(_required_text(row, field)):
            raise OracleContractError(f"{field} must be SHA256")
    if _required_text(row, "staleness_status") not in {
        "current",
        "superseded",
        "stale",
        "not_replayable",
    }:
        raise OracleContractError("invalid staleness_status")

    role = _required_text(row, "binding_role")
    role_required = {
        "tested_tracer_proposal": (
            "proposal_manifest_id",
            "proposal_row_id",
            "acquisition_source_id",
            "sample_key",
            "kernel_object_ref",
        ),
        "holdout_case_evidence": (
            "case_id",
            "presentation_id",
            "acquisition_source_id",
            "sample_key",
            "source_universe",
            "sampling_stratum",
        ),
        "human_review_event": (
            "case_id",
            "presentation_id",
            "review_record_id",
            "review_record_schema_version",
            "review_record_sha256",
        ),
        "machine_comparison": (
            "case_id",
            "presentation_id",
            "kernel_object_ref",
        ),
        "gate_result": (
            "gate_id",
            "gate_table_sha256",
            "gate_input_artifact_ids",
            "gate_status",
            "gate_observed_value",
            "gate_threshold_resolved_value",
            "gate_verdict_dependency",
        ),
    }
    if role not in role_required:
        raise OracleContractError("invalid binding_role")
    for field in role_required[role]:
        _required_text(row, field)
    for field in (
        "review_record_sha256",
        "gate_table_sha256",
    ):
        if row.get(field) and not _SHA256_RE.fullmatch(_required_text(row, field)):
            raise OracleContractError(f"{field} must be SHA256")


def validate_lineage_binding_rows(
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Validate lineage primary keys and attempt-to-request cardinality."""

    primary_keys: set[tuple[str, str]] = set()
    request_by_attempt: dict[tuple[str, str], str] = {}
    packet_ids: set[str] = set()
    for row in rows:
        validate_lineage_binding_row(row)
        packet_id = _required_text(row, "oracle_packet_id")
        packet_ids.add(packet_id)
        primary_key = (packet_id, _required_text(row, "binding_id"))
        if primary_key in primary_keys:
            raise OracleContractError("duplicate lineage binding primary key")
        primary_keys.add(primary_key)
        attempt_key = (
            packet_id,
            _required_text(row, "processing_attempt_id"),
        )
        request_id = _required_text(row, "processing_request_id")
        previous = request_by_attempt.setdefault(attempt_key, request_id)
        if previous != request_id:
            raise OracleContractError(
                "processing attempt references multiple requests"
            )
    if len(packet_ids) > 1:
        raise OracleContractError("lineage rows mix oracle packet IDs")


def validate_statistical_design_row(row: Mapping[str, Any]) -> None:
    """Validate one preregistered estimand and uncertainty route."""

    _require_schema_version(
        row,
        "cid_nl_oracle_statistical_design_manifest_v1",
    )
    if not _boolean(row, "frozen_before_reveal"):
        raise OracleContractError("statistical design must be frozen before reveal")
    _require_fields(row, _STATISTICAL_REQUIRED_FIELDS)
    validate_canonical_row_hash(row)
    design = _required_text(row, "sampling_design_type")
    uncertainty = _required_text(row, "uncertainty_method")
    sidedness = _required_text(row, "confidence_sidedness")
    if sidedness not in {
        "one_sided_lower",
        "one_sided_upper",
        "two_sided",
        "not_applicable",
    }:
        raise OracleContractError("invalid confidence_sidedness")
    allowed = {
        "finite_census": {"finite_census_descriptive"},
        "independent_equal_probability_sample": {
            "wilson_score",
            "clopper_pearson_exact",
        },
        "stratified_probability_sample": {
            "design_based_ratio_variance",
            "design_preserving_cluster_resampling",
        },
        "clustered_probability_sample": {
            "design_based_ratio_variance",
            "design_preserving_cluster_resampling",
        },
        "nonprobability_challenge": {"descriptive_challenge_only"},
        "incomplete_reference_frame": {"partial_identification_bounds"},
    }
    if design not in allowed or uncertainty not in allowed[design]:
        raise OracleContractError(
            "uncertainty method is incompatible with sampling design"
        )
    if uncertainty in {"wilson_score", "clopper_pearson_exact"}:
        level = _finite_number(row, "confidence_level")
        if not 0 < level < 1:
            raise OracleContractError("confidence_level must be in (0, 1)")
        if sidedness == "not_applicable":
            raise OracleContractError("confidence sidedness must be specified")
        comparator = _required_text(row, "comparator")
        if (
            comparator == "greater_than_or_equal"
            and sidedness == "one_sided_upper"
        ) or (
            comparator == "less_than_or_equal"
            and sidedness == "one_sided_lower"
        ):
            raise OracleContractError(
                "confidence sidedness conflicts with comparator"
            )
    if design == "incomplete_reference_frame":
        downgrade = _required_text(row, "incomplete_frame_claim_downgrade")
        if downgrade not in {
            "known_reference_recovery",
            "expected_control_recovery_only",
            "partial_identification_bounds",
        }:
            raise OracleContractError(
                "incomplete frame requires a bounded claim downgrade"
            )
    if row.get("hidden_duplicate_handling") != "repeatability_only":
        raise OracleContractError("hidden duplicates must be repeatability_only")
    if row.get("adaptive_supplemental_handling") != "descriptive_only":
        raise OracleContractError(
            "adaptive supplemental cases must be descriptive_only"
        )


def validate_manifest_seal(
    schema: Mapping[str, Any],
    seal: Mapping[str, Any],
    *,
    artifact_bytes_by_sha_field: Mapping[str, bytes],
    manifest_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Reject incomplete seals using a schema's declared required fields."""

    expected_schema_version = schema.get(
        "manifest_schema_version"
    ) or schema.get("binding_schema_version")
    if expected_schema_version is not None and seal.get(
        "schema_version"
    ) != expected_schema_version:
        raise OracleContractError(
            "seal schema_version does not match declared contract"
        )
    fields = schema.get("seal_required_fields")
    if not isinstance(fields, Sequence) or isinstance(fields, str):
        raise OracleContractError("schema seal_required_fields must be a list")
    for field_value in fields:
        field = str(field_value)
        value = seal.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise OracleContractError(f"seal field {field} is required")
        if "sha256" in field and not _SHA256_RE.fullmatch(str(value)):
            raise OracleContractError(f"seal field {field} must be SHA256")
        if "sha256" in field:
            payload = artifact_bytes_by_sha_field.get(field)
            if payload is None or sha256_bytes(payload) != str(value).lower():
                raise OracleContractError(
                    f"seal field {field} does not bind artifact bytes"
                )
        if (
            field.endswith("_count")
            or field in {"row_count", "scope_count", "sampling_frame_size"}
        ):
            _nonnegative_integer(seal, field)
        if any(
            token in field
            for token in (
                "_revealed",
                "_present",
                "_resolvable",
                "_passed",
                "_frozen",
            )
        ):
            _boolean(seal, field)
    if seal.get("seal_status") not in {"sealed", "valid"}:
        raise OracleContractError("seal_status must be sealed or valid")
    payload_sha256_field = _MANIFEST_PAYLOAD_SHA256_FIELDS.get(
        str(expected_schema_version)
    )
    if payload_sha256_field is None:
        raise OracleContractError(
            "manifest schema has no declared canonical payload field"
        )
    manifest_payload = artifact_bytes_by_sha_field.get(payload_sha256_field)
    if manifest_payload is None:
        raise OracleContractError(
            f"missing canonical manifest bytes: {payload_sha256_field}"
        )
    if manifest_payload != canonical_tsv_bytes(manifest_rows):
        raise OracleContractError(
            "sealed manifest bytes do not match manifest rows"
        )
    derived_counts = _derive_manifest_counts(manifest_rows)
    for field, expected in derived_counts.items():
        if field in seal and _nonnegative_integer(seal, field) != expected:
            raise OracleContractError(
                f"seal field {field} does not match manifest rows"
            )
    required_row_fields = {
        str(field) for field in schema.get("required_columns", ())
    }
    for field_value in fields:
        field = str(field_value)
        if field not in required_row_fields:
            continue
        row_values = {
            str(row.get(field, "") or "").strip()
            for row in manifest_rows
            if str(row.get(field, "") or "").strip()
        }
        if (
            len(row_values) != 1
            or any(
                not str(row.get(field, "") or "").strip()
                for row in manifest_rows
            )
            or str(seal.get(field)) not in row_values
        ):
            raise OracleContractError(
                f"seal field {field} does not match manifest rows"
            )
    for field, expected in schema.get("seal_expected_values", {}).items():
        if seal.get(field) != expected:
            raise OracleContractError(
                f"seal field {field} must equal {expected!r}"
            )


def validate_review_label_seal(
    schema: Mapping[str, Any],
    seal: Mapping[str, Any],
    *,
    artifact_bytes_by_sha_field: Mapping[str, bytes],
    review_rows: Sequence[Mapping[str, Any]],
    adjudication_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Bind a review label seal to its immutable event logs and counts."""

    expected_schema_version = _required_text(
        schema,
        "label_seal_schema_version",
    )
    if seal.get("schema_version") != expected_schema_version:
        raise OracleContractError(
            "label seal schema_version does not match declared contract"
        )
    fields = schema.get("label_seal_required_fields")
    if not isinstance(fields, Sequence) or isinstance(fields, str):
        raise OracleContractError(
            "schema label_seal_required_fields must be a list"
        )
    for field_value in fields:
        field = str(field_value)
        value = seal.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise OracleContractError(f"label seal field {field} is required")
        if "sha256" in field:
            if not _SHA256_RE.fullmatch(str(value)):
                raise OracleContractError(
                    f"label seal field {field} must be SHA256"
                )
            payload = artifact_bytes_by_sha_field.get(field)
            if payload is None or sha256_bytes(payload) != str(value).lower():
                raise OracleContractError(
                    f"label seal field {field} does not bind artifact bytes"
                )
        if field.endswith("_count"):
            _nonnegative_integer(seal, field)
    for field in (
        "source_roles_revealed",
        "machine_mapping_revealed",
        "legacy_mapping_revealed",
    ):
        _boolean(seal, field)
    if seal.get("seal_status") not in {"sealed", "valid"}:
        raise OracleContractError("label seal_status must be sealed or valid")

    review_payload = artifact_bytes_by_sha_field.get("review_event_log_sha256")
    adjudication_payload = artifact_bytes_by_sha_field.get(
        "adjudication_event_log_sha256"
    )
    if review_payload != canonical_tsv_bytes(review_rows):
        raise OracleContractError(
            "sealed review log bytes do not match review rows"
        )
    if adjudication_payload != canonical_tsv_bytes(adjudication_rows):
        raise OracleContractError(
            "sealed adjudication log bytes do not match adjudication rows"
        )
    for review_row in review_rows:
        _require_schema_version(
            review_row,
            "cid_nl_oracle_review_event_v1",
        )
        _require_fields(review_row, _REVIEW_REQUIRED_FIELDS)
        validate_canonical_row_hash(review_row)
    packet_id = _required_text(seal, "oracle_packet_id")
    if any(
        _required_text(row, "oracle_packet_id") != packet_id
        for row in (*review_rows, *adjudication_rows)
    ):
        raise OracleContractError(
            "label seal packet ID does not match sealed event logs"
        )
    validate_adjudication_event_set(
        adjudication_rows,
        review_rows=review_rows,
    )

    derived_counts = _derive_manifest_counts(review_rows)
    for field in (
        "primary_confirmatory_case_count",
        "hidden_duplicate_presentation_count",
        "invalid_before_reveal_case_count",
        "unassessable_primary_case_count",
    ):
        if _nonnegative_integer(seal, field) != derived_counts[field]:
            raise OracleContractError(
                f"label seal field {field} does not match review rows"
            )


def _derive_manifest_counts(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    return {
        "row_count": len(rows),
        "scope_count": len(rows),
        "binding_row_count": len(rows),
        "estimand_count": len(
            {
                str(row.get("estimand_id", "") or "").strip()
                for row in rows
                if str(row.get("estimand_id", "") or "").strip()
            }
        ),
        "distinct_acquisition_source_count": len(
            {
                str(row.get("acquisition_source_id", "") or "").strip()
                for row in rows
                if str(row.get("acquisition_source_id", "") or "").strip()
            }
        ),
        "distinct_peak_hypothesis_count": len(
            {
                str(row.get("kernel_object_ref", "") or "").strip()
                for row in rows
                if str(row.get("kernel_object_ref", "") or "").strip()
            }
        ),
        "distinct_processing_request_count": len(
            {
                str(row.get("processing_request_id", "") or "").strip()
                for row in rows
                if str(row.get("processing_request_id", "") or "").strip()
            }
        ),
        "distinct_processing_attempt_count": len(
            {
                str(row.get("processing_attempt_id", "") or "").strip()
                for row in rows
                if str(row.get("processing_attempt_id", "") or "").strip()
            }
        ),
        "distinct_evidence_artifact_count": len(
            {
                str(row.get("evidence_artifact_id", "") or "").strip()
                for row in rows
                if str(row.get("evidence_artifact_id", "") or "").strip()
            }
        ),
        "primary_case_count": len(
            {
                str(row.get("case_id", "") or "").strip()
                for row in rows
                if (
                    row.get("primary_presentation")
                    in {True, "TRUE", "true", "1", 1}
                    or row.get("analysis_role") == "primary_confirmatory"
                )
            }
        ),
        "primary_confirmatory_case_count": len(
            {
                str(row.get("case_id", "") or "").strip()
                for row in rows
                if row.get("analysis_role") == "primary_confirmatory"
            }
        ),
        "challenge_case_count": len(
            {
                str(row.get("case_id", "") or "").strip()
                for row in rows
                if row.get("case_role") == "challenge"
            }
        ),
        "random_representative_case_count": len(
            {
                str(row.get("case_id", "") or "").strip()
                for row in rows
                if row.get("case_role") == "random_representative"
            }
        ),
        "hidden_duplicate_presentation_count": len(
            {
                str(row.get("presentation_id", "") or "").strip()
                for row in rows
                if (
                    row.get("case_role") == "hidden_duplicate"
                    or row.get("analysis_role") == "repeatability_only"
                )
            }
        ),
        "invalid_before_reveal_case_count": len(
            {
                str(row.get("case_id", "") or "").strip()
                for row in rows
                if row.get("review_status") == "invalid_before_reveal"
            }
        ),
        "unassessable_primary_case_count": len(
            {
                str(row.get("case_id", "") or "").strip()
                for row in rows
                if row.get("analysis_role") == "primary_confirmatory"
                and row.get("observation_state") == "unassessable"
            }
        ),
        "missing_required_binding_count": 0,
    }


@dataclass(frozen=True)
class SyntheticMetricSummary:
    primary_case_count: int
    proposal_positive_count: int
    proposal_correct_count: int
    reference_positive_count: int
    reference_recovered_count: int
    hidden_duplicate_count: int
    supplemental_count: int


def aggregate_synthetic_metrics(
    rows: Sequence[Mapping[str, Any]],
) -> SyntheticMetricSummary:
    """Derive synthetic denominators without trusting answer columns."""

    valid_rows = [
        row for row in rows if synthetic_contract_violation(row) is None
    ]
    primary = [
        row
        for row in valid_rows
        if row.get("analysis_role") == "primary_confirmatory"
    ]
    proposals = [
        row for row in primary if row.get("projection_outcome") == "select_value"
    ]
    references = [
        row
        for row in primary
        if row.get("feature_identity_decision") == "accept"
        and row.get("observation_state") == "observed"
    ]
    return SyntheticMetricSummary(
        primary_case_count=len(primary),
        proposal_positive_count=len(proposals),
        proposal_correct_count=sum(
            row.get("feature_identity_decision") == "accept"
            and row.get("observation_state") == "observed"
            for row in proposals
        ),
        reference_positive_count=len(references),
        reference_recovered_count=sum(
            row.get("projection_outcome") == "select_value"
            for row in references
        ),
        hidden_duplicate_count=sum(
            row.get("analysis_role") == "repeatability_only"
            for row in valid_rows
        ),
        supplemental_count=sum(
            row.get("analysis_role") == "supplemental_descriptive"
            for row in valid_rows
        ),
    )


def synthetic_contract_violation(row: Mapping[str, Any]) -> str | None:
    """Return the first frozen gate violated by one synthetic scenario."""

    if _boolean(row, "reference_membership_uses_tested_tracer"):
        return "reference_universe_independence"
    if not _boolean(row, "lineage_replay_match"):
        return "replay_determinism"
    if row.get("observation_state") == "not_applicable":
        return None
    proposal_row = {
        "observation_state": row.get("observation_state"),
        "selection_state": row.get("selection_state"),
        "projection_outcome": row.get("projection_outcome"),
        "candidate_left_rt_min": 0.0,
        "candidate_apex_rt_min": (
            0.5 if row.get("apex_inside_boundaries") == "TRUE" else 2.0
        ),
        "candidate_right_rt_min": 1.0,
        "candidate_area": 1.0,
    }
    try:
        _validate_proposal_crosswalk(proposal_row)
    except OracleContractError as exc:
        message = str(exc)
        if "select_value requires observed" in message:
            return "non_observed_selected_value"
        if "apex must be inside" in message:
            return "synthetic_contract_suite"
        return "observation_state_collapse"
    identity = row.get("feature_identity_decision")
    observation = row.get("observation_state")
    if identity == "unassessable" and observation != "unassessable":
        return "review_label_crosswalk"
    if identity == "accept" and observation != "observed":
        return "review_label_crosswalk"
    if identity == "reject" and observation not in {"absent", "observed"}:
        return "review_label_crosswalk"
    if row.get("boundary_contained_in_human_envelope") == "FALSE":
        return "boundary_containment"
    area = _optional_number(row, "area_retention_ratio")
    if area is not None and area < 0.98:
        return "area_retention"
    return None


Verdict = Literal["GO", "NARROW", "REDRAW", "inconclusive"]


def _verdict_for_rule(
    rules: Mapping[str, Mapping[str, Any]],
    rule_id: str,
) -> Verdict:
    row = rules.get(rule_id)
    if row is None:
        raise OracleContractError(f"missing verdict rule: {rule_id}")
    verdict = _required_text(row, "verdict")
    if verdict not in {"GO", "NARROW", "REDRAW", "inconclusive"}:
        raise OracleContractError("invalid verdict value")
    return cast(Verdict, verdict)


def reduce_oracle_verdict(
    gate_results: Sequence[Mapping[str, Any]],
    narrow_scopes: Sequence[Mapping[str, Any]],
    *,
    gate_table_bytes: bytes,
    verdict_table_bytes: bytes,
    narrow_membership_bytes_by_id: Mapping[str, bytes],
    narrow_manifest_bytes_by_id: Mapping[str, bytes],
    narrow_manifest_seals_by_id: Mapping[str, Mapping[str, Any]],
    narrow_scope_gate_results_by_id: Mapping[
        tuple[str, str],
        Sequence[Mapping[str, Any]],
    ],
) -> Verdict:
    """Reduce raw gate results; NARROW is derived only from sealed scopes."""

    if sha256_bytes(verdict_table_bytes) != _CANONICAL_VERDICT_TABLE_SHA256:
        raise OracleContractError("verdict table bytes are not canonical v1")
    verdict_rules = {
        _required_text(row, "rule_id"): row
        for row in _parse_tsv_bytes(verdict_table_bytes)
    }
    if any(
        row.get("schema_version") != "cid_nl_oracle_verdict_table_v1"
        for row in verdict_rules.values()
    ):
        raise OracleContractError("invalid verdict table schema version")
    by_id = {_required_text(row, "gate_id"): row for row in gate_results}
    if len(by_id) != len(gate_results):
        raise OracleContractError("duplicate gate_id in gate results")
    if not by_id:
        return _verdict_for_rule(verdict_rules, "fail_closed_fallback")
    gate_definitions = {
        _required_text(row, "gate_id"): row
        for row in _parse_tsv_bytes(gate_table_bytes)
    }
    if sha256_bytes(gate_table_bytes) != _CANONICAL_GATE_TABLE_SHA256:
        raise OracleContractError("gate table bytes are not canonical v1")
    if any(
        row.get("schema_version") != "cid_nl_oracle_gate_table_v1"
        for row in gate_definitions.values()
    ):
        raise OracleContractError("invalid gate table schema version")
    expected = set(gate_definitions)
    if len(expected) != 25 or set(by_id) != expected:
        return _verdict_for_rule(verdict_rules, "fail_closed_fallback")
    if any(
        row.get("gate_group")
        != gate_definitions[gate_id].get("gate_group")
        for gate_id, row in by_id.items()
    ):
        raise OracleContractError("gate result group mismatches frozen table")

    statuses = {_required_text(row, "status") for row in gate_results}
    if not statuses <= {"pass", "fail", "unknown"}:
        raise OracleContractError("invalid gate result status")
    if "unknown" in statuses:
        return _verdict_for_rule(
            verdict_rules,
            "required_input_missing_or_unknown",
        )
    failed = {
        gate_id
        for gate_id, row in by_id.items()
        if row.get("status") == "fail"
    }
    if any(
        gate_definitions[gate_id].get("fail_verdict") == "inconclusive"
        for gate_id in failed
    ):
        return _verdict_for_rule(
            verdict_rules,
            "required_input_missing_or_unknown",
        )
    if any(
        row.get("gate_group") == "integrity" and row.get("status") != "pass"
        for row in gate_results
    ):
        return _verdict_for_rule(
            verdict_rules,
            "integrity_invalid_or_unknown",
        )
    if any(
        row.get("gate_group") == "hard_blocker" and row.get("status") == "fail"
        for row in gate_results
    ):
        return _verdict_for_rule(
            verdict_rules,
            "valid_packet_hard_blocker_failed",
        )

    if not failed:
        return _verdict_for_rule(verdict_rules, "all_required_gates_pass")
    if any(
        gate_definitions[gate_id].get("narrow_scope_rule") == "not_allowed"
        for gate_id in failed
    ):
        return _verdict_for_rule(
            verdict_rules,
            "nonhard_failure_without_passing_narrow_scope",
        )

    performance_gate_ids = {
        gate_id
        for gate_id, row in gate_definitions.items()
        if row.get("gate_group") == "performance"
    }
    scientific_gate_ids = expected - performance_gate_ids
    failed_nonperformance = {
        gate_id
        for gate_id in failed
        if gate_id not in performance_gate_ids
    }

    scope_primary_keys: set[tuple[str, str]] = set()
    scope_precedences: set[int] = set()
    scopes_by_manifest: dict[str, list[Mapping[str, Any]]] = {}
    for scope in narrow_scopes:
        manifest_id = _required_text(scope, "narrow_scope_manifest_id")
        scope_key = (
            manifest_id,
            _required_text(scope, "narrow_scope_id"),
        )
        if scope_key in scope_primary_keys:
            raise OracleContractError("duplicate narrow scope primary key")
        scope_primary_keys.add(scope_key)
        precedence = _positive_integer(scope, "scope_precedence")
        if precedence in scope_precedences:
            raise OracleContractError("duplicate narrow scope precedence")
        scope_precedences.add(precedence)
        scopes_by_manifest.setdefault(manifest_id, []).append(scope)

    for manifest_id, manifest_scopes in scopes_by_manifest.items():
        manifest_bytes = narrow_manifest_bytes_by_id.get(manifest_id)
        manifest_seal = narrow_manifest_seals_by_id.get(manifest_id)
        if manifest_bytes is None or manifest_seal is None:
            return _verdict_for_rule(
                verdict_rules,
                "required_input_missing_or_unknown",
            )
        manifest_rows = _parse_tsv_bytes(manifest_bytes)
        normalized_scopes = [
            {str(key): str(value) for key, value in scope.items()}
            for scope in manifest_scopes
        ]
        def sort_key(row: Mapping[str, Any]) -> tuple[str, str]:
            return (
                str(row.get("narrow_scope_manifest_id", "")),
                str(row.get("narrow_scope_id", "")),
            )

        if sorted(manifest_rows, key=sort_key) != sorted(
            normalized_scopes,
            key=sort_key,
        ):
            raise OracleContractError(
                "caller scopes do not match complete sealed manifest"
            )
        membership_ids = {
            _required_text(scope, "scope_membership_manifest_id")
            for scope in manifest_scopes
        }
        digest_map = manifest_seal.get("scope_membership_digests")
        if not isinstance(digest_map, Mapping) or {
            str(key) for key in digest_map
        } != membership_ids:
            raise OracleContractError(
                "sealed scope membership set does not match manifest"
            )
        for scope in manifest_scopes:
            membership_id = _required_text(
                scope,
                "scope_membership_manifest_id",
            )
            membership_bytes = narrow_membership_bytes_by_id.get(
                membership_id
            )
            if membership_bytes is None:
                return _verdict_for_rule(
                    verdict_rules,
                    "required_input_missing_or_unknown",
                )
            validate_narrow_scope_row(
                scope,
                membership_bytes=membership_bytes,
                manifest_bytes=manifest_bytes,
                manifest_seal=manifest_seal,
            )

    for scope in sorted(
        narrow_scopes,
        key=lambda row: _positive_integer(row, "scope_precedence"),
    ):
        membership_id = _required_text(scope, "scope_membership_manifest_id")
        manifest_id = _required_text(scope, "narrow_scope_manifest_id")
        membership_bytes = narrow_membership_bytes_by_id.get(membership_id)
        manifest_bytes = narrow_manifest_bytes_by_id.get(manifest_id)
        manifest_seal = narrow_manifest_seals_by_id.get(manifest_id)
        scope_gate_results = narrow_scope_gate_results_by_id.get(
            (
                manifest_id,
                _required_text(scope, "narrow_scope_id"),
            )
        )
        if (
            membership_bytes is None
            or manifest_bytes is None
            or manifest_seal is None
            or scope_gate_results is None
        ):
            return _verdict_for_rule(
                verdict_rules,
                "required_input_missing_or_unknown",
            )
        required = set(_tokens(scope, "required_gate_ids"))
        allowed_failed = set(_tokens(scope, "allowed_overall_failed_gate_ids"))
        if not required <= expected or not allowed_failed <= expected:
            raise OracleContractError("narrow scope gate IDs are not resolvable")
        if not failed <= allowed_failed:
            continue
        if failed_nonperformance and not failed_nonperformance <= required:
            continue
        if not failed_nonperformance and not scientific_gate_ids <= required:
            continue
        scoped_by_id = {
            _required_text(row, "gate_id"): row
            for row in scope_gate_results
        }
        if len(scoped_by_id) != len(scope_gate_results):
            raise OracleContractError("duplicate scope-specific gate result")
        if set(scoped_by_id) != required:
            raise OracleContractError(
                "scope-specific gate results do not match required gates"
            )
        if all(
            scoped_by_id[gate_id].get("status") == "pass"
            for gate_id in required
        ):
            rule_id = (
                "performance_failure_after_scientific_pass"
                if not failed_nonperformance
                else "nonhard_failure_with_passing_narrow_scope"
            )
            return _verdict_for_rule(verdict_rules, rule_id)
    if not failed_nonperformance:
        return _verdict_for_rule(verdict_rules, "fail_closed_fallback")
    return _verdict_for_rule(
        verdict_rules,
        "nonhard_failure_without_passing_narrow_scope",
    )


def _validate_local_label(row: Mapping[str, Any]) -> None:
    identity = _required_text(row, "feature_identity_decision")
    observation = _required_text(row, "observation_state")
    peak = _required_text(row, "peak_decision")
    if identity not in {"accept", "reject", "unassessable", "not_applicable"}:
        raise OracleContractError("invalid feature_identity_decision")
    if observation not in _OBSERVATION_STATES | {"not_applicable"}:
        raise OracleContractError("invalid observation_state")
    if peak not in {
        "acceptable",
        "wrong_peak",
        "no_applicable_peak",
        "unassessable",
        "not_applicable",
    }:
        raise OracleContractError("invalid peak_decision")
    if identity == "accept":
        if observation != "observed":
            raise OracleContractError(
                "accepted identity requires observed state"
            )
        if peak != "acceptable":
            raise OracleContractError(
                "accepted observed identity requires acceptable peak"
            )
        left = _finite_number(row, "human_left_rt_min")
        apex = _finite_number(row, "human_apex_rt_min")
        right = _finite_number(row, "human_right_rt_min")
        if not left <= apex <= right or left == right:
            raise OracleContractError("human apex must be inside boundaries")
    elif identity == "reject":
        if peak != "not_applicable":
            raise OracleContractError("rejected identity requires N/A peak")
        if observation not in {"observed", "absent"}:
            raise OracleContractError(
                "rejected identity requires observed or absent state"
            )
        _require_blank_boundaries(row)
    elif identity == "unassessable":
        if observation != "unassessable" or peak != "unassessable":
            raise OracleContractError(
                "unassessable identity requires completed unassessable state"
            )
        _require_blank_boundaries(row)
    elif observation != "not_applicable" or peak != "not_applicable":
        raise OracleContractError(
            "not-applicable identity requires not-applicable state"
        )
    else:
        _require_blank_boundaries(row)


def _require_blank_boundaries(row: Mapping[str, Any]) -> None:
    if any(
        str(row.get(field, "") or "").strip()
        for field in (
            "human_left_rt_min",
            "human_apex_rt_min",
            "human_right_rt_min",
        )
    ):
        raise OracleContractError(
            "non-accepted identity requires blank boundaries"
        )


def _validate_projection_crosswalk(
    observation: str,
    projection: str,
) -> None:
    if projection not in _PROJECTIONS:
        raise OracleContractError("invalid policy projection")
    if observation == "observed":
        if projection not in {"select_value", "hold"}:
            raise OracleContractError("observed state forbids leave_missing")
    elif observation == "absent":
        if projection != "leave_missing":
            raise OracleContractError("absent state requires leave_missing")
    elif observation in {"missing", "unassessable", "ambiguous", "failed"}:
        if projection != "hold":
            raise OracleContractError(f"{observation} state requires hold")
    else:
        raise OracleContractError("invalid observation state for policy")


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = str(row.get(field, "") or "").strip()
    if not value:
        raise OracleContractError(f"{field} is required")
    return value


def _require_schema_version(
    row: Mapping[str, Any],
    expected: str,
) -> None:
    if _required_text(row, "schema_version") != expected:
        raise OracleContractError(
            f"schema_version must be {expected}"
        )


def _require_fields(
    row: Mapping[str, Any],
    fields: Iterable[str],
) -> None:
    for field in fields:
        _required_text(row, field)


def canonical_row_sha256(
    row: Mapping[str, Any],
    *,
    hash_field: str = "source_row_sha256",
) -> str:
    """Hash one canonical row while excluding its self-referential digest."""

    payload = {
        str(key): value
        for key, value in row.items()
        if str(key) != hash_field
    }
    return sha256_canonical_json(payload)


def validate_canonical_row_hash(
    row: Mapping[str, Any],
    *,
    hash_field: str = "source_row_sha256",
) -> None:
    expected = _required_text(row, hash_field)
    if not _SHA256_RE.fullmatch(expected):
        raise OracleContractError(f"{hash_field} must be SHA256")
    if expected.lower() != canonical_row_sha256(row, hash_field=hash_field):
        raise OracleContractError(f"{hash_field} does not bind canonical row")


def sha256_canonical_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_utf8_text_normalized_to_lf(value: bytes) -> str:
    """Hash UTF-8 text after normalizing platform line endings to LF."""

    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OracleContractError(
            "normalized text hash input must be UTF-8"
        ) from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return sha256_bytes(normalized.encode("utf-8"))


def canonical_tsv_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    if not rows:
        return b""
    columns = sorted({str(key) for row in rows for key in row})
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=columns,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    return output.getvalue().encode("utf-8")


def _parse_tsv_bytes(value: bytes) -> list[dict[str, str]]:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OracleContractError("TSV bytes must be UTF-8") from exc
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def _validate_scope_membership_bytes(value: bytes) -> None:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OracleContractError(
            "scope membership must be UTF-8 TSV"
        ) from exc
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if reader.fieldnames is None or "case_id" not in reader.fieldnames:
        raise OracleContractError(
            "scope membership requires a case_id column"
        )
    case_ids: set[str] = set()
    for row in reader:
        case_id = str(row.get("case_id", "") or "").strip()
        if not case_id:
            raise OracleContractError(
                "scope membership case_id is required"
            )
        if case_id in case_ids:
            raise OracleContractError(
                "duplicate case_id in scope membership"
            )
        case_ids.add(case_id)
    if not case_ids:
        raise OracleContractError("scope membership cannot be empty")


def _validate_bound_bytes(
    artifact_bytes_by_id: Mapping[str, bytes],
    artifact_id: str,
    expected_hash: str,
) -> None:
    if not _SHA256_RE.fullmatch(expected_hash):
        raise OracleContractError(f"{artifact_id} digest must be SHA256")
    payload = artifact_bytes_by_id.get(artifact_id)
    if payload is None:
        raise OracleContractError(f"missing bound artifact bytes: {artifact_id}")
    if sha256_bytes(payload) != expected_hash.lower():
        raise OracleContractError(f"artifact digest mismatch: {artifact_id}")


def _boolean(row: Mapping[str, Any], field: str) -> bool:
    value = row.get(field)
    if value in {True, "TRUE", "true", "1", 1}:
        return True
    if value in {False, "FALSE", "false", "0", 0}:
        return False
    raise OracleContractError(f"{field} must be boolean")


def _threshold_values_equal(left: str, right: str) -> bool:
    try:
        return math.isclose(
            float(left),
            float(right),
            rel_tol=1e-12,
            abs_tol=1e-15,
        )
    except ValueError:
        return left == right


def _finite_number(row: Mapping[str, Any], field: str) -> float:
    value = _optional_number(row, field)
    if value is None:
        raise OracleContractError(f"{field} must be finite")
    return value


def _optional_number(row: Mapping[str, Any], field: str) -> float | None:
    value = row.get(field)
    if value is None:
        return None
    if value in {"", "not_applicable"}:
        return None
    if isinstance(value, bool):
        raise OracleContractError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise OracleContractError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise OracleContractError(f"{field} must be finite")
    return parsed


def _positive_integer(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool):
        raise OracleContractError(f"{field} must be a positive integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise OracleContractError(
            f"{field} must be a positive integer"
        ) from exc
    if parsed <= 0 or str(parsed) != str(value).strip():
        raise OracleContractError(f"{field} must be a positive integer")
    return parsed


def _nonnegative_integer(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool):
        raise OracleContractError(f"{field} must be a non-negative integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise OracleContractError(
            f"{field} must be a non-negative integer"
        ) from exc
    if parsed < 0 or str(parsed) != str(value).strip():
        raise OracleContractError(f"{field} must be a non-negative integer")
    return parsed


def _tokens(row: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = row.get(field, "")
    if isinstance(value, str):
        return tuple(token.strip() for token in value.split("|") if token.strip())
    if isinstance(value, Iterable):
        return tuple(str(token).strip() for token in value if str(token).strip())
    raise OracleContractError(f"{field} must be a token list")
