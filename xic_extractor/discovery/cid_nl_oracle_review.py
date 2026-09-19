"""Blinded review and adjudication contracts for the CID-NL oracle packet."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from xic_extractor.discovery.cid_nl_oracle_contract import (
    _OBSERVATION_STATES,
    _PROJECTIONS,
    validate_sampling_manifest_rows,
)
from xic_extractor.discovery.cid_nl_oracle_primitives import (
    _SHA256_RE,
    OracleContractError,
    _boolean,
    _finite_number,
    _require_fields,
    _require_schema_version,
    _required_text,
    _tokens,
    canonical_row_sha256,
    validate_canonical_row_hash,
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


def validate_review_event(
    row: Mapping[str, Any],
    *,
    local_record: Mapping[str, Any] | None = None,
    local_label_seal: Mapping[str, Any] | None = None,
) -> None:
    """Validate one blinded review event and its immutable local binding."""

    _require_schema_version(row, "cid_nl_oracle_review_event")
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
    review_by_presentation_phase: dict[
        tuple[str, str], Mapping[str, Any]
    ] = {}
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
        review_by_presentation_phase[(presentation, phase)] = row
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
    primary_presentation_by_case = {
        _required_text(row, "case_id"): _required_text(
            row,
            "presentation_id",
        )
        for row in sampling_rows
        if _boolean(row, "primary_presentation")
    }
    for sampling_row in sampling_rows:
        if sampling_row.get("case_role") != "hidden_duplicate":
            continue
        case_id = _required_text(sampling_row, "case_id")
        duplicate_presentation = _required_text(
            sampling_row,
            "presentation_id",
        )
        primary_presentation = primary_presentation_by_case[case_id]
        for primary_phase, duplicate_phase in (
            (
                "sample_local_blinded",
                "hidden_duplicate_sample_local_blinded",
            ),
            (
                "cross_sample_policy_blinded",
                "hidden_duplicate_policy_blinded",
            ),
        ):
            primary_review = review_by_presentation_phase[
                (primary_presentation, primary_phase)
            ]
            duplicate_review = review_by_presentation_phase[
                (duplicate_presentation, duplicate_phase)
            ]
            if _required_text(
                primary_review,
                "reviewer_id",
            ) != _required_text(duplicate_review, "reviewer_id"):
                raise OracleContractError(
                    "hidden duplicate must be reviewed by the same reviewer "
                    "as its primary presentation"
                )


def validate_adjudication_event(row: Mapping[str, Any]) -> None:
    """Validate one append-only pre- or post-reveal adjudication event."""

    _require_schema_version(row, "cid_nl_oracle_adjudication_event")
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
            "cid_nl_oracle_review_event",
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
