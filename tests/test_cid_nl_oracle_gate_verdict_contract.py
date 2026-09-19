from __future__ import annotations

import csv
import itertools
import json
from pathlib import Path

import pytest

from xic_extractor.discovery.cid_nl_oracle_contract import (
    _NARROW_SCOPE_REQUIRED_FIELDS,
    _NARROW_SCOPE_SEAL_REQUIRED_FIELDS,
)
from xic_extractor.discovery.cid_nl_oracle_primitives import (
    OracleContractError,
    canonical_row_sha256,
    canonical_tsv_bytes,
    sha256_bytes,
    sha256_canonical_json,
)
from xic_extractor.discovery.cid_nl_oracle_verdict import reduce_oracle_verdict

ROOT = Path(__file__).resolve().parents[1]
PACKET = (
    ROOT
    / "docs/superpowers/validation/cid_nl_oracle_packet_preflight_v1"
)
GATE_TABLE = PACKET / "cid_nl_oracle_gate_table_v1.tsv"
VERDICT_TABLE = PACKET / "cid_nl_oracle_verdict_table_v1.tsv"


def _tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _resolve_verdict(
    rules: list[dict[str, str]],
    *,
    packet_integrity_state: str,
    required_input_state: str,
    hard_blocker_state: str,
    required_nonhard_gate_state: str,
    derived_preregistered_narrow_scope_state: str,
    performance_state: str,
) -> str:
    state = {
        "packet_integrity_state": packet_integrity_state,
        "required_input_state": required_input_state,
        "hard_blocker_state": hard_blocker_state,
        "required_nonhard_gate_state": required_nonhard_gate_state,
        "derived_preregistered_narrow_scope_state": (
            derived_preregistered_narrow_scope_state
        ),
        "performance_state": performance_state,
    }
    for rule in sorted(rules, key=lambda row: int(row["precedence"])):
        if all(
            expected == "any"
            or expected == actual
            or (expected == "not_pass" and actual != "pass")
            for field, actual in state.items()
            if (expected := rule[field])
        ):
            return rule["verdict"]
    raise AssertionError(f"no verdict for {state}")


def _holdout_row(case_id: str, presentation_id: str) -> dict[str, str]:
    row = {
        "holdout_manifest_id": "HM1",
        "case_id": case_id,
        "presentation_id": presentation_id,
        "primary_presentation": "TRUE",
        "source_universe": "independent_reference",
        "workflow_policy": "untargeted",
        "method_scope": "nl116_only",
        "sampling_stratum": "reference_positive",
        "source_row_sha256": "",
    }
    row["source_row_sha256"] = canonical_row_sha256(row)
    return row


def _membership_bytes(
    case_id: str,
    presentation_id: str,
    holdout_row: dict[str, str],
) -> bytes:
    return canonical_tsv_bytes(
        [
            {
                "case_id": case_id,
                "presentation_id": presentation_id,
                "source_sampling_row_sha256": canonical_row_sha256(
                    holdout_row
                ),
            }
        ]
    )


def _sealed_scope_bundle(
    *,
    required_gate_ids: tuple[str, ...],
    allowed_failed_gate_ids: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, object]]:
    holdout_rows = [_holdout_row("C1", "P1"), _holdout_row("C2", "P2")]
    holdout_bytes = canonical_tsv_bytes(holdout_rows)
    holdout_seal = {
        "holdout_manifest_seal_id": "HMS1",
        "holdout_manifest_id": "HM1",
        "holdout_tsv_sha256": sha256_bytes(holdout_bytes),
        "case_counts_and_staged_limits_frozen": "TRUE",
        "source_roles_revealed_to_reviewer": "FALSE",
        "machine_mapping_revealed_to_reviewer": "FALSE",
        "legacy_mapping_revealed_to_reviewer": "FALSE",
        "seal_status": "sealed",
    }
    membership_bytes = _membership_bytes("C1", "P1", holdout_rows[0])
    scope = {
        "schema_version": "cid_nl_oracle_narrow_scope_manifest_v1",
        "narrow_scope_manifest_id": "NSM1",
        "narrow_scope_id": "NS1",
        "scope_precedence": "1",
        "claim_label": "scientific-evaluation-only",
        "membership_predicate": "source_universe == independent_reference",
        "scope_membership_manifest_id": "MEM1",
        "scope_membership_manifest_sha256": sha256_bytes(membership_bytes),
        "source_universe": "independent_reference",
        "workflow_policy": "untargeted",
        "method_scope": "nl116_only",
        "included_sampling_strata": "all",
        "excluded_sampling_strata": "none",
        "minimum_independent_units_parameter": "n_min",
        "gatekeeping_rule_id": "scope_gate_v1",
        "required_gate_ids": "|".join(required_gate_ids),
        "allowed_overall_failed_gate_ids": "|".join(allowed_failed_gate_ids),
        "membership_frozen_before_reveal": "TRUE",
        "machine_mapping_visible_at_seal": "FALSE",
        "source_row_sha256": "",
    }
    scope["source_row_sha256"] = canonical_row_sha256(scope)
    manifest_bytes = canonical_tsv_bytes([scope])
    seal = {
        "schema_version": "cid_nl_oracle_narrow_scope_manifest_v1",
        "narrow_scope_manifest_id": "NSM1",
        "narrow_scope_tsv_sha256": sha256_bytes(manifest_bytes),
        "scope_membership_digests": {
            "MEM1": sha256_bytes(membership_bytes)
        },
        "scope_membership_bundle_sha256": "",
        "scope_count": "1",
        "all_membership_manifests_present": "TRUE",
        "all_scope_gate_sets_resolvable": "TRUE",
        "machine_mapping_revealed": "FALSE",
        "holdout_labels_revealed": "FALSE",
        "sealed_at_utc": "2026-07-25T00:00:00Z",
        "sealed_by": "custodian",
        "seal_status": "sealed",
        "supersedes_narrow_scope_manifest_id": "none",
    }
    seal["scope_membership_bundle_sha256"] = sha256_canonical_json(
        seal["scope_membership_digests"]
    )
    kwargs: dict[str, object] = {
        "gate_table_bytes": GATE_TABLE.read_bytes(),
        "verdict_table_bytes": VERDICT_TABLE.read_bytes(),
        "holdout_sampling_rows": holdout_rows,
        "holdout_manifest_bytes": holdout_bytes,
        "holdout_manifest_seal": holdout_seal,
        "narrow_membership_bytes_by_id": {"MEM1": membership_bytes},
        "narrow_manifest_bytes_by_id": {"NSM1": manifest_bytes},
        "narrow_manifest_seals_by_id": {"NSM1": seal},
        "narrow_scope_gate_results_by_id": {
            ("NSM1", "NS1"): [
                {"gate_id": gate_id, "status": "pass"}
                for gate_id in required_gate_ids
            ]
        },
    }
    return scope, kwargs


def test_gate_table_is_complete_fail_closed_and_parameterized() -> None:
    rows = {row["gate_id"]: row for row in _tsv(GATE_TABLE)}

    required = {
        "oracle_packet_integrity",
        "required_input_completeness",
        "blindness_and_custody",
        "reference_universe_independence",
        "independent_unit_sufficiency",
        "calibration_evidence_portability",
        "targeted_authority_leak",
        "non_observed_selected_value",
        "trusted_value_overwrite",
        "observation_state_collapse",
        "lineage_mismatch",
        "replay_determinism",
        "synthetic_contract_suite",
        "boundary_containment",
        "area_retention",
        "targeted_dominance",
        "untargeted_non_inferiority",
        "nl116_istd_absolute_recovery",
        "proposal_precision",
        "reference_recovery",
        "reviewer_repeatability",
        "unassessable_rate",
        "automated_coverage",
        "automated_selective_risk",
        "performance_budget",
    }
    assert set(rows) == required
    assert all(row["required_inputs"] for row in rows.values())
    assert all(row["unknown_outcome"] for row in rows.values())
    assert all(row["fail_verdict"] for row in rows.values())
    assert all(row["required_for_go"] == "TRUE" for row in rows.values())
    assert all("UNSET" not in value for row in rows.values() for value in row.values())

    parameterized = {
        "independent_unit_sufficiency",
        "nl116_istd_absolute_recovery",
        "proposal_precision",
        "reference_recovery",
        "reviewer_repeatability",
        "unassessable_rate",
        "automated_selective_risk",
    }
    assert all(rows[gate]["threshold_parameter"] for gate in parameterized)
    assert {
        rows[gate]["status"] for gate in parameterized
    } == {"contract_complete_parameter_required"}
    assert rows["performance_budget"]["threshold_kind"] == "external_contract"

    assert rows["area_retention"]["threshold_value"] == "0.98"
    assert rows["untargeted_non_inferiority"]["threshold_value"] == "2"
    assert rows["automated_coverage"]["threshold_value"] == "0.90"


def test_narrow_scope_required_fields_match_schema() -> None:
    schema = json.loads(
        (
            ROOT
            / "docs/superpowers/schemas/"
            "cid_nl_oracle_narrow_scope_manifest_schema.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert set(_NARROW_SCOPE_REQUIRED_FIELDS) == set(
        schema["required_columns"]
    )
    assert set(_NARROW_SCOPE_SEAL_REQUIRED_FIELDS) == set(
        schema["seal_required_fields"]
    )


def test_verdict_table_is_total_and_has_frozen_precedence() -> None:
    rules = _tsv(VERDICT_TABLE)
    states = ("pass", "fail", "unknown")
    for combination in itertools.product(states, repeat=6):
        verdict = _resolve_verdict(
            rules,
            packet_integrity_state=combination[0],
            required_input_state=combination[1],
            hard_blocker_state=combination[2],
            required_nonhard_gate_state=combination[3],
            derived_preregistered_narrow_scope_state=combination[4],
            performance_state=combination[5],
        )
        assert verdict in {"GO", "NARROW", "REDRAW", "inconclusive"}

    assert _resolve_verdict(
        rules,
        packet_integrity_state="pass",
        required_input_state="pass",
        hard_blocker_state="pass",
        required_nonhard_gate_state="pass",
        derived_preregistered_narrow_scope_state="pass",
        performance_state="pass",
    ) == "GO"
    assert _resolve_verdict(
        rules,
        packet_integrity_state="pass",
        required_input_state="pass",
        hard_blocker_state="fail",
        required_nonhard_gate_state="pass",
        derived_preregistered_narrow_scope_state="pass",
        performance_state="pass",
    ) == "REDRAW"
    assert _resolve_verdict(
        rules,
        packet_integrity_state="pass",
        required_input_state="pass",
        hard_blocker_state="pass",
        required_nonhard_gate_state="fail",
        derived_preregistered_narrow_scope_state="pass",
        performance_state="pass",
    ) == "NARROW"
    assert _resolve_verdict(
        rules,
        packet_integrity_state="unknown",
        required_input_state="pass",
        hard_blocker_state="pass",
        required_nonhard_gate_state="pass",
        derived_preregistered_narrow_scope_state="pass",
        performance_state="pass",
    ) == "inconclusive"


def test_narrow_verdict_is_derived_from_presealed_scope_membership() -> None:
    gate_rows = _tsv(GATE_TABLE)
    gate_results = [
        {
            "gate_id": row["gate_id"],
            "gate_group": row["gate_group"],
            "status": (
                "fail" if row["gate_id"] == "proposal_precision" else "pass"
            ),
        }
        for row in gate_rows
    ]
    scope, kwargs = _sealed_scope_bundle(
        required_gate_ids=(
            "oracle_packet_integrity",
            "proposal_precision",
            "reference_recovery",
        ),
        allowed_failed_gate_ids=("proposal_precision",),
    )

    assert reduce_oracle_verdict(gate_results, [], **kwargs) == "REDRAW"
    assert reduce_oracle_verdict(gate_results, [scope], **kwargs) == "NARROW"

    post_hoc_scope = {**scope, "membership_frozen_before_reveal": "FALSE"}
    post_hoc_scope["source_row_sha256"] = canonical_row_sha256(post_hoc_scope)
    with pytest.raises(ValueError, match="sealed manifest"):
        reduce_oracle_verdict(gate_results, [post_hoc_scope], **kwargs)


def test_narrow_membership_must_bind_an_in_scope_primary_holdout_row() -> None:
    gate_rows = _tsv(GATE_TABLE)
    gate_results = [
        {
            "gate_id": row["gate_id"],
            "gate_group": row["gate_group"],
            "status": (
                "fail" if row["gate_id"] == "proposal_precision" else "pass"
            ),
        }
        for row in gate_rows
    ]
    scope, kwargs = _sealed_scope_bundle(
        required_gate_ids=("proposal_precision",),
        allowed_failed_gate_ids=("proposal_precision",),
    )
    nonexistent_membership = _membership_bytes(
        "C9",
        "P9",
        _holdout_row("C9", "P9"),
    )
    invalid_scope = {
        **scope,
        "scope_membership_manifest_sha256": sha256_bytes(
            nonexistent_membership
        ),
        "source_row_sha256": "",
    }
    invalid_scope["source_row_sha256"] = canonical_row_sha256(invalid_scope)
    invalid_manifest = canonical_tsv_bytes([invalid_scope])
    digest_map = {
        "MEM1": invalid_scope["scope_membership_manifest_sha256"]
    }
    original_seal = kwargs["narrow_manifest_seals_by_id"]["NSM1"]
    invalid_seal = {
        **original_seal,
        "narrow_scope_tsv_sha256": sha256_bytes(invalid_manifest),
        "scope_membership_digests": digest_map,
        "scope_membership_bundle_sha256": sha256_canonical_json(digest_map),
    }
    invalid_kwargs = {
        **kwargs,
        "narrow_membership_bytes_by_id": {
            "MEM1": nonexistent_membership
        },
        "narrow_manifest_bytes_by_id": {"NSM1": invalid_manifest},
        "narrow_manifest_seals_by_id": {"NSM1": invalid_seal},
    }
    with pytest.raises(OracleContractError, match="absent from primary holdout"):
        reduce_oracle_verdict(
            gate_results,
            [invalid_scope],
            **invalid_kwargs,
        )


def test_narrow_scope_must_retest_the_failed_scientific_gate() -> None:
    gate_rows = _tsv(GATE_TABLE)
    gate_results = [
        {
            "gate_id": row["gate_id"],
            "gate_group": row["gate_group"],
            "status": (
                "fail" if row["gate_id"] == "reference_recovery" else "pass"
            ),
        }
        for row in gate_rows
    ]
    insufficient_scope, insufficient_kwargs = _sealed_scope_bundle(
        required_gate_ids=("oracle_packet_integrity",),
        allowed_failed_gate_ids=("reference_recovery",),
    )
    assert reduce_oracle_verdict(
        gate_results,
        [insufficient_scope],
        **insufficient_kwargs,
    ) == "REDRAW"

    sufficient_scope, sufficient_kwargs = _sealed_scope_bundle(
        required_gate_ids=(
            "oracle_packet_integrity",
            "reference_recovery",
        ),
        allowed_failed_gate_ids=("reference_recovery",),
    )
    assert reduce_oracle_verdict(
        gate_results,
        [sufficient_scope],
        **sufficient_kwargs,
    ) == "NARROW"


def test_scope_results_use_manifest_scoped_primary_key() -> None:
    gate_rows = _tsv(GATE_TABLE)
    gate_results = [
        {
            "gate_id": row["gate_id"],
            "gate_group": row["gate_group"],
            "status": (
                "fail" if row["gate_id"] == "proposal_precision" else "pass"
            ),
        }
        for row in gate_rows
    ]
    first_scope, first_kwargs = _sealed_scope_bundle(
        required_gate_ids=("proposal_precision",),
        allowed_failed_gate_ids=("reference_recovery",),
    )
    second_membership = _membership_bytes(
        "C2",
        "P2",
        _holdout_row("C2", "P2"),
    )
    second_scope = {
        **first_scope,
        "narrow_scope_manifest_id": "NSM2",
        "scope_precedence": "2",
        "scope_membership_manifest_id": "MEM2",
        "scope_membership_manifest_sha256": sha256_bytes(
            second_membership
        ),
        "allowed_overall_failed_gate_ids": "proposal_precision",
        "source_row_sha256": "",
    }
    second_scope["source_row_sha256"] = canonical_row_sha256(second_scope)
    second_manifest = canonical_tsv_bytes([second_scope])
    second_digests = {
        "MEM2": second_scope["scope_membership_manifest_sha256"]
    }
    first_seal = first_kwargs["narrow_manifest_seals_by_id"]["NSM1"]
    second_seal = {
        **first_seal,
        "narrow_scope_manifest_id": "NSM2",
        "narrow_scope_tsv_sha256": sha256_bytes(second_manifest),
        "scope_membership_digests": second_digests,
        "scope_membership_bundle_sha256": sha256_canonical_json(
            second_digests
        ),
    }
    combined_kwargs = {
        **first_kwargs,
        "narrow_membership_bytes_by_id": {
            "MEM1": _membership_bytes(
                "C1",
                "P1",
                _holdout_row("C1", "P1"),
            ),
            "MEM2": second_membership,
        },
        "narrow_manifest_bytes_by_id": {
            "NSM1": first_kwargs["narrow_manifest_bytes_by_id"]["NSM1"],
            "NSM2": second_manifest,
        },
        "narrow_manifest_seals_by_id": {
            "NSM1": first_seal,
            "NSM2": second_seal,
        },
        "narrow_scope_gate_results_by_id": {
            ("NSM1", "NS1"): [
                {"gate_id": "proposal_precision", "status": "pass"}
            ],
            ("NSM2", "NS1"): [
                {"gate_id": "proposal_precision", "status": "pass"}
            ],
        },
    }
    assert reduce_oracle_verdict(
        gate_results,
        [first_scope, second_scope],
        **combined_kwargs,
    ) == "NARROW"


def test_performance_only_failure_narrows_after_scientific_pass() -> None:
    gate_rows = _tsv(GATE_TABLE)
    gate_results = [
        {
            "gate_id": row["gate_id"],
            "gate_group": row["gate_group"],
            "status": (
                "fail" if row["gate_id"] == "performance_budget" else "pass"
            ),
        }
        for row in gate_rows
    ]
    scientific_gate_ids = tuple(
        row["gate_id"]
        for row in gate_rows
        if row["gate_group"] != "performance"
    )
    scope, kwargs = _sealed_scope_bundle(
        required_gate_ids=scientific_gate_ids,
        allowed_failed_gate_ids=("performance_budget",),
    )

    assert reduce_oracle_verdict(
        gate_results,
        [],
        gate_table_bytes=GATE_TABLE.read_bytes(),
        verdict_table_bytes=VERDICT_TABLE.read_bytes(),
        narrow_membership_bytes_by_id={},
        narrow_manifest_bytes_by_id={},
        narrow_manifest_seals_by_id={},
        narrow_scope_gate_results_by_id={},
    ) == "inconclusive"
    assert reduce_oracle_verdict(
        gate_results,
        [scope],
        **kwargs,
    ) == "NARROW"


def test_redraw_only_gates_cannot_be_narrowed_by_hostile_scope() -> None:
    gate_rows = _tsv(GATE_TABLE)
    for failed_gate in gate_rows:
        if (
            failed_gate["narrow_scope_rule"] != "not_allowed"
            or failed_gate["fail_verdict"] == "inconclusive"
        ):
            continue
        gate_results = [
            {
                "gate_id": row["gate_id"],
                "gate_group": row["gate_group"],
                "status": (
                    "fail"
                    if row["gate_id"] == failed_gate["gate_id"]
                    else "pass"
                ),
            }
            for row in gate_rows
        ]
        scope, kwargs = _sealed_scope_bundle(
            required_gate_ids=("oracle_packet_integrity",),
            allowed_failed_gate_ids=(failed_gate["gate_id"],),
        )
        assert reduce_oracle_verdict(
            gate_results,
            [scope],
            **kwargs,
        ) == "REDRAW", failed_gate["gate_id"]


def test_narrow_scope_rejects_partial_or_duplicate_contract_rows() -> None:
    gate_rows = _tsv(GATE_TABLE)
    gate_results = [
        {
            "gate_id": row["gate_id"],
            "gate_group": row["gate_group"],
            "status": (
                "fail" if row["gate_id"] == "proposal_precision" else "pass"
            ),
        }
        for row in gate_rows
    ]
    scope, kwargs = _sealed_scope_bundle(
        required_gate_ids=(
            "oracle_packet_integrity",
            "proposal_precision",
        ),
        allowed_failed_gate_ids=("proposal_precision",),
    )
    partial = {
        **scope,
        "included_sampling_strata": "",
        "source_row_sha256": "",
    }
    partial["source_row_sha256"] = canonical_row_sha256(partial)
    with pytest.raises(OracleContractError, match="sealed manifest"):
        reduce_oracle_verdict(gate_results, [partial], **kwargs)

    duplicate = {
        **scope,
        "narrow_scope_id": "NS2",
        "source_row_sha256": "",
    }
    duplicate["source_row_sha256"] = canonical_row_sha256(duplicate)
    with pytest.raises(OracleContractError, match="precedence"):
        reduce_oracle_verdict(
            gate_results,
            [scope, duplicate],
            **kwargs,
        )

    duplicated_results = {
        **kwargs,
        "narrow_scope_gate_results_by_id": {
            ("NSM1", "NS1"): [
                {"gate_id": "oracle_packet_integrity", "status": "pass"},
                {"gate_id": "oracle_packet_integrity", "status": "pass"},
            ]
        },
    }
    with pytest.raises(OracleContractError, match="duplicate scope-specific"):
        reduce_oracle_verdict(
            gate_results,
            [scope],
            **duplicated_results,
        )

    second_membership = _membership_bytes(
        "C2",
        "P2",
        _holdout_row("C2", "P2"),
    )
    second_scope = {
        **scope,
        "narrow_scope_id": "NS2",
        "scope_precedence": "2",
        "scope_membership_manifest_id": "MEM2",
        "scope_membership_manifest_sha256": sha256_bytes(
            second_membership
        ),
        "source_row_sha256": "",
    }
    second_scope["source_row_sha256"] = canonical_row_sha256(second_scope)
    two_scope_manifest = canonical_tsv_bytes([scope, second_scope])
    original_seal = kwargs["narrow_manifest_seals_by_id"]["NSM1"]
    two_scope_digests = {
        "MEM1": scope["scope_membership_manifest_sha256"],
        "MEM2": second_scope["scope_membership_manifest_sha256"],
    }
    two_scope_seal = {
        **original_seal,
        "narrow_scope_tsv_sha256": sha256_bytes(two_scope_manifest),
        "scope_membership_digests": two_scope_digests,
        "scope_membership_bundle_sha256": sha256_canonical_json(
            two_scope_digests
        ),
        "scope_count": "2",
    }
    incomplete_caller_kwargs = {
        **kwargs,
        "narrow_membership_bytes_by_id": {
            "MEM1": _membership_bytes(
                "C1",
                "P1",
                _holdout_row("C1", "P1"),
            ),
            "MEM2": second_membership,
        },
        "narrow_manifest_bytes_by_id": {"NSM1": two_scope_manifest},
        "narrow_manifest_seals_by_id": {"NSM1": two_scope_seal},
    }
    with pytest.raises(OracleContractError, match="complete sealed manifest"):
        reduce_oracle_verdict(
            gate_results,
            [scope],
            **incomplete_caller_kwargs,
        )

    missing_high_precedence_results = {
        **incomplete_caller_kwargs,
        "narrow_scope_gate_results_by_id": {
            ("NSM1", "NS2"): [
                {"gate_id": gate_id, "status": "pass"}
                for gate_id in (
                    "oracle_packet_integrity",
                    "proposal_precision",
                )
            ]
        },
    }
    assert reduce_oracle_verdict(
        gate_results,
        [scope, second_scope],
        **missing_high_precedence_results,
    ) == "inconclusive"

    duplicate_row = {
        "case_id": "C1",
        "presentation_id": "P1",
        "source_sampling_row_sha256": canonical_row_sha256(
            _holdout_row("C1", "P1")
        ),
    }
    duplicate_membership = canonical_tsv_bytes(
        [duplicate_row, duplicate_row]
    )
    duplicate_member_scope = {
        **scope,
        "scope_membership_manifest_sha256": sha256_bytes(
            duplicate_membership
        ),
        "source_row_sha256": "",
    }
    duplicate_member_scope["source_row_sha256"] = canonical_row_sha256(
        duplicate_member_scope
    )
    duplicate_member_manifest = canonical_tsv_bytes(
        [duplicate_member_scope]
    )
    duplicate_member_digests = {
        "MEM1": duplicate_member_scope[
            "scope_membership_manifest_sha256"
        ]
    }
    duplicate_member_seal = {
        **original_seal,
        "narrow_scope_tsv_sha256": sha256_bytes(
            duplicate_member_manifest
        ),
        "scope_membership_digests": duplicate_member_digests,
        "scope_membership_bundle_sha256": sha256_canonical_json(
            duplicate_member_digests
        ),
    }
    duplicate_member_kwargs = {
        **kwargs,
        "narrow_membership_bytes_by_id": {
            "MEM1": duplicate_membership,
        },
        "narrow_manifest_bytes_by_id": {
            "NSM1": duplicate_member_manifest,
        },
        "narrow_manifest_seals_by_id": {
            "NSM1": duplicate_member_seal,
        },
    }
    with pytest.raises(OracleContractError, match="duplicate case_id"):
        reduce_oracle_verdict(
            gate_results,
            [duplicate_member_scope],
            **duplicate_member_kwargs,
        )


@pytest.mark.parametrize(
    "failed_gate_id",
    (
        "independent_unit_sufficiency",
        "reviewer_repeatability",
        "unassessable_rate",
    ),
)
def test_packet_sufficiency_failures_are_inconclusive(
    failed_gate_id: str,
) -> None:
    gate_results = [
        {
            "gate_id": row["gate_id"],
            "gate_group": row["gate_group"],
            "status": "fail" if row["gate_id"] == failed_gate_id else "pass",
        }
        for row in _tsv(GATE_TABLE)
    ]

    assert reduce_oracle_verdict(
        gate_results,
        [],
        gate_table_bytes=GATE_TABLE.read_bytes(),
        verdict_table_bytes=VERDICT_TABLE.read_bytes(),
        narrow_membership_bytes_by_id={},
        narrow_manifest_bytes_by_id={},
        narrow_manifest_seals_by_id={},
        narrow_scope_gate_results_by_id={},
    ) == "inconclusive"


def test_single_gate_failures_follow_frozen_no_scope_disposition() -> None:
    gate_rows = _tsv(GATE_TABLE)

    for failed_gate in gate_rows:
        gate_results = [
            {
                "gate_id": row["gate_id"],
                "gate_group": row["gate_group"],
                "status": (
                    "fail"
                    if row["gate_id"] == failed_gate["gate_id"]
                    else "pass"
                ),
            }
            for row in gate_rows
        ]
        expected = (
            "inconclusive"
            if (
                failed_gate["fail_verdict"] == "inconclusive"
                or failed_gate["gate_group"] == "performance"
            )
            else "REDRAW"
        )

        assert reduce_oracle_verdict(
            gate_results,
            [],
            gate_table_bytes=GATE_TABLE.read_bytes(),
            verdict_table_bytes=VERDICT_TABLE.read_bytes(),
            narrow_membership_bytes_by_id={},
            narrow_manifest_bytes_by_id={},
            narrow_manifest_seals_by_id={},
            narrow_scope_gate_results_by_id={},
        ) == expected, failed_gate["gate_id"]


def test_missing_gate_result_is_inconclusive() -> None:
    gate_rows = _tsv(GATE_TABLE)
    gate_results = [
        {
            "gate_id": row["gate_id"],
            "gate_group": row["gate_group"],
            "status": "pass",
        }
        for row in gate_rows[:-1]
    ]

    assert reduce_oracle_verdict(
        gate_results,
        [],
        gate_table_bytes=GATE_TABLE.read_bytes(),
        verdict_table_bytes=VERDICT_TABLE.read_bytes(),
        narrow_membership_bytes_by_id={},
        narrow_manifest_bytes_by_id={},
        narrow_manifest_seals_by_id={},
        narrow_scope_gate_results_by_id={},
    ) == "inconclusive"


def test_reducer_rejects_substituted_verdict_table() -> None:
    gate_rows = _tsv(GATE_TABLE)
    gate_results = [
        {
            "gate_id": row["gate_id"],
            "gate_group": row["gate_group"],
            "status": "pass",
        }
        for row in gate_rows
    ]
    substituted = VERDICT_TABLE.read_bytes().replace(
        b"\tGO\tEvery required gate",
        b"\tREDRAW\tEvery required gate",
    )
    with pytest.raises(OracleContractError, match="verdict table bytes"):
        reduce_oracle_verdict(
            gate_results,
            [],
            gate_table_bytes=GATE_TABLE.read_bytes(),
            verdict_table_bytes=substituted,
            narrow_membership_bytes_by_id={},
            narrow_manifest_bytes_by_id={},
            narrow_manifest_seals_by_id={},
            narrow_scope_gate_results_by_id={},
        )
