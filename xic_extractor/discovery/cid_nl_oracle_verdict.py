"""Synthetic contract metrics and frozen verdict reduction for the CID-NL oracle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from xic_extractor.discovery.cid_nl_oracle_contract import (
    _CANONICAL_GATE_TABLE_SHA256,
    _CANONICAL_VERDICT_TABLE_SHA256,
    _validate_holdout_sampling_rows_binding,
    _validate_proposal_crosswalk,
    validate_narrow_scope_row,
)
from xic_extractor.discovery.cid_nl_oracle_primitives import (
    OracleContractError,
    _boolean,
    _optional_number,
    _parse_tsv_bytes,
    _positive_integer,
    _required_text,
    _tokens,
    sha256_bytes,
)


@dataclass(frozen=True)
class SyntheticMetricSummary:
    primary_case_count: int
    proposal_positive_count: int
    proposal_correct_count: int
    reference_positive_count: int
    reference_recalled_count: int
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
        reference_recalled_count=sum(
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
    holdout_sampling_rows: Sequence[Mapping[str, Any]] = (),
    holdout_manifest_bytes: bytes = b"",
    holdout_manifest_seal: Mapping[str, Any] | None = None,
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
        raise OracleContractError("verdict table bytes are not canonical")
    verdict_rules = {
        _required_text(row, "rule_id"): row
        for row in _parse_tsv_bytes(verdict_table_bytes)
    }
    if any(
        row.get("schema_version") != "cid_nl_oracle_verdict_table"
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
        raise OracleContractError("gate table bytes are not canonical")
    if any(
        row.get("schema_version") != "cid_nl_oracle_gate_table"
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
    if narrow_scopes:
        if holdout_manifest_seal is None:
            raise OracleContractError(
                "NARROW evaluation requires a sealed holdout manifest"
            )
        _validate_holdout_sampling_rows_binding(
            holdout_sampling_rows,
            manifest_bytes=holdout_manifest_bytes,
            manifest_seal=holdout_manifest_seal,
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
                holdout_sampling_rows=holdout_sampling_rows,
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
