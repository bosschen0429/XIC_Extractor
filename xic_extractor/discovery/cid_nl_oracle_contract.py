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
from typing import Any, Literal

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CANONICAL_GATE_TABLE_SHA256 = (
    "b1e3e67cc0051b9eb9b0bf41bf9163477ed6b3da66582b19cba74b0fcd7f4b99"
)
_OBSERVATION_STATES = {
    "observed",
    "missing",
    "absent",
    "unassessable",
    "ambiguous",
    "failed",
}
_PROJECTIONS = {"select_value", "leave_missing", "hold"}

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

    by_case: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        validate_sampling_row(row)
        by_case.setdefault(_required_text(row, "case_id"), []).append(row)
    for case_id, case_rows in by_case.items():
        primary_count = sum(
            _boolean(row, "primary_presentation") for row in case_rows
        )
        if primary_count != 1:
            raise OracleContractError(
                f"{case_id} must have exactly one primary presentation"
            )
        for row in case_rows:
            if row.get("case_role") == "hidden_duplicate":
                if row.get("duplicate_of_case_id") != case_id:
                    raise OracleContractError(
                        "hidden duplicate must reuse its underlying case_id"
                    )


def validate_sampling_statistical_binding(
    sampling_rows: Sequence[Mapping[str, Any]],
    statistical_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Bind every sampled case to exactly one compatible estimand design."""

    designs: dict[str, Mapping[str, Any]] = {}
    for design_row in statistical_rows:
        validate_statistical_design_row(design_row)
        estimand_id = _required_text(design_row, "estimand_id")
        if estimand_id in designs:
            raise OracleContractError("duplicate estimand_id")
        designs[estimand_id] = design_row
    for sampling_row in sampling_rows:
        validate_sampling_row(sampling_row)
        estimand_id = _required_text(sampling_row, "estimand_id")
        design_row = designs.get(estimand_id)
        if design_row is None:
            raise OracleContractError("sampling estimand has no statistical design")
        if _required_text(sampling_row, "sampling_frame_id") != _required_text(
            design_row,
            "frame_id",
        ):
            raise OracleContractError("sampling frame does not match estimand frame")
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

    _require_fields(
        row,
        (
            "reference_universe_id",
            "reference_row_id",
            "reference_family_id",
            "reference_source_artifact_ids",
            "reference_source_sha256s",
            "review_record_id",
            "sampling_frame_id",
            "sampling_stratum",
            "source_row_sha256",
        ),
    )
    validate_canonical_row_hash(row)
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
    _require_fields(
        seal,
        (
            "schema_version",
            "reference_universe_id",
            "bounded_dataset_manifest_sha256",
            "method_scope_sha256",
            "source_manifest_sha256",
            "reference_enumerator_dependency_manifest_sha256",
            "universe_construction_protocol_sha256",
            "sealed_at_utc",
            "sealed_by",
            "seal_status",
        ),
    )
    if seal.get("seal_status") != "sealed":
        raise OracleContractError("reference universe must be sealed")
    if _required_text(seal, "reference_universe_id") != _required_text(
        rows[0],
        "reference_universe_id",
    ):
        raise OracleContractError("reference row/seal universe mismatch")
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

    scientific_keys: set[tuple[str, str, str]] = set()
    stratum_sizes: dict[str, int] = {}
    stratum_counts: dict[str, int] = {}
    for row in rows:
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

    frame_size = _positive_integer(seal, "sampling_frame_size")
    if len(rows) != frame_size or sum(stratum_sizes.values()) != frame_size:
        raise OracleContractError("complete frame size does not match membership")
    if stratum_counts != stratum_sizes:
        raise OracleContractError("stratum membership count does not match frame")
    if _required_text(seal, "frame_completeness") != "complete":
        raise OracleContractError("full manifest verification requires complete frame")
    expected_strata_hash = sha256_canonical_json(stratum_sizes)
    if expected_strata_hash != _required_text(
        seal,
        "frame_size_by_stratum_sha256",
    ):
        raise OracleContractError("stratum frame digest mismatch")
    if int(_required_text(seal, "row_count")) != len(rows):
        raise OracleContractError("reference seal row_count mismatch")

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

    validate_canonical_row_hash(row)
    phase = _required_text(row, "review_phase")
    machine_visible = _boolean(row, "machine_mapping_visible")
    legacy_visible = _boolean(row, "legacy_mapping_visible")
    scores_visible = _boolean(row, "evaluated_system_scores_visible")
    source_visible = _boolean(row, "source_roles_visible")
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

    local_by_id = {
        _required_text(row, "review_record_id"): row
        for row in rows
        if row.get("review_phase")
        in {
            "sample_local_blinded",
            "hidden_duplicate_sample_local_blinded",
        }
    }
    review_by_id = {
        _required_text(row, "review_record_id"): row for row in rows
    }
    if len(review_by_id) != len(rows):
        raise OracleContractError("duplicate review_record_id")
    prior_confirmatory_ids: set[str] = set()
    phases_by_presentation: dict[str, set[str]] = {}
    for row in rows:
        phase = _required_text(row, "review_phase")
        local_record = None
        if phase in {
            "cross_sample_policy_blinded",
            "hidden_duplicate_policy_blinded",
        }:
            local_id = _required_text(row, "local_review_record_id")
            local_record = local_by_id.get(local_id)
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
            for field in (
                "feature_identity_decision",
                "observation_state",
                "peak_decision",
                "human_apex_rt_min",
                "human_left_rt_min",
                "human_right_rt_min",
            ):
                if str(row.get(field, "")) != str(predecessor.get(field, "")):
                    raise OracleContractError(
                        "unblinded taxonomy cannot change confirmatory label"
                    )
        else:
            prior_confirmatory_ids.add(_required_text(row, "review_record_id"))
        presentation = _required_text(row, "presentation_id")
        phases_by_presentation.setdefault(presentation, set()).add(phase)

    required_duplicate_phases = {
        "hidden_duplicate_sample_local_blinded",
        "hidden_duplicate_policy_blinded",
    }
    for presentation, phases in phases_by_presentation.items():
        if phases & required_duplicate_phases and not (
            required_duplicate_phases <= phases
        ):
            raise OracleContractError(
                f"{presentation} hidden duplicate requires complete "
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

    for field in (
        "adjudication_record_id",
        "case_id",
        "adjudication_type",
        "trigger_review_record_ids",
        "trigger_reason_codes",
        "adjudicator_id",
        "adjudicated_at_utc",
        "packet_effect",
        "source_row_sha256",
    ):
        _required_text(row, field)
    validate_canonical_row_hash(row)

    adjudication_type = _required_text(row, "adjudication_type")
    packet_effect = _required_text(row, "packet_effect")
    pre_reveal_types = {
        "pre_reveal_hidden_duplicate_inconsistency",
        "pre_reveal_inter_reviewer_disagreement",
        "pre_reveal_case_validity",
    }
    if adjudication_type in pre_reveal_types:
        if _boolean(row, "machine_mapping_visible") or _boolean(
            row,
            "legacy_mapping_visible",
        ):
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

    review_ids = {
        _required_text(row, "review_record_id") for row in review_rows
    }
    adjudication_ids: set[str] = set()
    for row in rows:
        validate_adjudication_event(row)
        record_id = _required_text(row, "adjudication_record_id")
        if record_id in adjudication_ids:
            raise OracleContractError("duplicate adjudication_record_id")
        triggers = set(_tokens(row, "trigger_review_record_ids"))
        if not triggers or not triggers <= review_ids:
            raise OracleContractError(
                "adjudication triggers must bind immutable review records"
            )
        resulting = str(
            row.get("resulting_confirmatory_review_record_id", "") or ""
        ).strip()
        if resulting and resulting not in review_ids:
            raise OracleContractError(
                "adjudication result must reference immutable review record"
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
        adjudication_ids.add(record_id)


def validate_narrow_scope_row(
    row: Mapping[str, Any],
    *,
    membership_bytes: bytes,
    manifest_bytes: bytes,
    manifest_seal: Mapping[str, Any],
) -> None:
    """Validate one preregistered candidate scope for a NARROW verdict."""

    _require_fields(
        row,
        (
            "narrow_scope_manifest_id",
            "narrow_scope_id",
            "claim_label",
            "scope_membership_manifest_id",
            "source_universe",
            "workflow_policy",
            "method_scope",
            "minimum_independent_units_parameter",
            "gatekeeping_rule_id",
            "source_row_sha256",
        ),
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
    if _required_text(manifest_seal, "seal_status") != "sealed":
        raise OracleContractError("narrow scope manifest must be sealed")
    if sha256_bytes(manifest_bytes) != _required_text(
        manifest_seal,
        "narrow_scope_tsv_sha256",
    ):
        raise OracleContractError("narrow scope manifest digest mismatch")
    manifest_rows = _parse_tsv_bytes(manifest_bytes)
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
    if int(_required_text(manifest_seal, "scope_count")) != len(
        normalized_digests
    ):
        raise OracleContractError("narrow scope seal count mismatch")
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

    for field in (
        "oracle_packet_id",
        "binding_id",
        "binding_role",
        "processing_request_id",
        "processing_attempt_id",
        "evidence_artifact_id",
        "artifact_schema_version",
        "artifact_semantic_role",
        "artifact_sha256",
        "staleness_status",
        "source_row_sha256",
    ):
        _required_text(row, field)
    validate_canonical_row_hash(row)
    for field in ("artifact_sha256", "source_row_sha256"):
        if not _SHA256_RE.fullmatch(_required_text(row, field)):
            raise OracleContractError(f"{field} must be SHA256")

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


def validate_statistical_design_row(row: Mapping[str, Any]) -> None:
    """Validate one preregistered estimand and uncertainty route."""

    if not _boolean(row, "frozen_before_reveal"):
        raise OracleContractError("statistical design must be frozen before reveal")
    _require_fields(row, _STATISTICAL_REQUIRED_FIELDS)
    validate_canonical_row_hash(row)
    design = _required_text(row, "sampling_design_type")
    uncertainty = _required_text(row, "uncertainty_method")
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
        sidedness = _required_text(row, "confidence_sidedness")
        if sidedness == "not_applicable":
            raise OracleContractError("confidence sidedness must be specified")
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
) -> None:
    """Reject incomplete seals using a schema's declared required fields."""

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
    for field, expected in schema.get("seal_expected_values", {}).items():
        if seal.get(field) != expected:
            raise OracleContractError(
                f"seal field {field} must equal {expected!r}"
            )


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


def reduce_oracle_verdict(
    gate_results: Sequence[Mapping[str, Any]],
    narrow_scopes: Sequence[Mapping[str, Any]],
    *,
    gate_table_bytes: bytes,
    narrow_membership_bytes_by_id: Mapping[str, bytes],
    narrow_manifest_bytes_by_id: Mapping[str, bytes],
    narrow_manifest_seals_by_id: Mapping[str, Mapping[str, Any]],
    narrow_scope_gate_results_by_id: Mapping[
        str,
        Sequence[Mapping[str, Any]],
    ],
) -> Verdict:
    """Reduce raw gate results; NARROW is derived only from sealed scopes."""

    by_id = {_required_text(row, "gate_id"): row for row in gate_results}
    if len(by_id) != len(gate_results):
        raise OracleContractError("duplicate gate_id in gate results")
    if not by_id:
        return "inconclusive"
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
        return "inconclusive"
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
        return "inconclusive"
    if any(
        row.get("gate_group") == "integrity" and row.get("status") != "pass"
        for row in gate_results
    ):
        return "inconclusive"
    if any(
        row.get("gate_group") == "hard_blocker" and row.get("status") == "fail"
        for row in gate_results
    ):
        return "REDRAW"

    failed = {
        gate_id
        for gate_id, row in by_id.items()
        if row.get("status") == "fail"
    }
    if not failed:
        return "GO"

    failed_nonperformance = {
        gate_id
        for gate_id in failed
        if by_id[gate_id].get("gate_group") != "performance"
    }
    if not failed_nonperformance:
        return "NARROW"

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
            _required_text(scope, "narrow_scope_id")
        )
        if (
            membership_bytes is None
            or manifest_bytes is None
            or manifest_seal is None
            or scope_gate_results is None
        ):
            continue
        validate_narrow_scope_row(
            scope,
            membership_bytes=membership_bytes,
            manifest_bytes=manifest_bytes,
            manifest_seal=manifest_seal,
        )
        required = set(_tokens(scope, "required_gate_ids"))
        allowed_failed = set(_tokens(scope, "allowed_overall_failed_gate_ids"))
        if not required <= expected or not allowed_failed <= expected:
            raise OracleContractError("narrow scope gate IDs are not resolvable")
        if not failed_nonperformance <= allowed_failed:
            continue
        scoped_by_id = {
            _required_text(row, "gate_id"): row
            for row in scope_gate_results
        }
        if set(scoped_by_id) != required:
            raise OracleContractError(
                "scope-specific gate results do not match required gates"
            )
        if all(
            scoped_by_id[gate_id].get("status") == "pass"
            for gate_id in required
        ):
            return "NARROW"
    return "REDRAW"


def _validate_local_label(row: Mapping[str, Any]) -> None:
    identity = _required_text(row, "feature_identity_decision")
    observation = _required_text(row, "observation_state")
    peak = _required_text(row, "peak_decision")
    if identity == "accept" and observation == "observed":
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
    elif identity == "unassessable":
        if observation != "unassessable" or peak != "unassessable":
            raise OracleContractError(
                "unassessable identity requires completed unassessable state"
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


def _finite_number(row: Mapping[str, Any], field: str) -> float:
    value = _optional_number(row, field)
    if value is None:
        raise OracleContractError(f"{field} must be finite")
    return value


def _optional_number(row: Mapping[str, Any], field: str) -> float | None:
    value = row.get(field)
    if value in {None, "", "not_applicable"}:
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
