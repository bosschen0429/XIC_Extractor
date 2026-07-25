from __future__ import annotations

import csv
import itertools
from pathlib import Path

import pytest

from xic_extractor.discovery.cid_nl_oracle_contract import (
    canonical_row_sha256,
    canonical_tsv_bytes,
    reduce_oracle_verdict,
    sha256_bytes,
    sha256_canonical_json,
)

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
    membership_bytes = b"case_id\nC1\n"
    scope = {
        "narrow_scope_manifest_id": "NSM1",
        "narrow_scope_id": "NS1",
        "scope_precedence": "1",
        "claim_label": "independent-reference-only",
        "membership_predicate": "source_universe == independent_reference",
        "scope_membership_manifest_id": "MEM1",
        "scope_membership_manifest_sha256": sha256_bytes(membership_bytes),
        "source_universe": "independent_reference",
        "workflow_policy": "untargeted",
        "method_scope": "nl116_only",
        "minimum_independent_units_parameter": "n_min",
        "gatekeeping_rule_id": "scope_gate_v1",
        "required_gate_ids": "oracle_packet_integrity|reference_recovery",
        "allowed_overall_failed_gate_ids": "proposal_precision",
        "membership_frozen_before_reveal": "TRUE",
        "machine_mapping_visible_at_seal": "FALSE",
        "source_row_sha256": "",
    }
    scope["source_row_sha256"] = canonical_row_sha256(scope)
    manifest_bytes = canonical_tsv_bytes([scope])
    seal = {
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
    }
    seal["scope_membership_bundle_sha256"] = sha256_canonical_json(
        seal["scope_membership_digests"]
    )
    kwargs = {
        "gate_table_bytes": GATE_TABLE.read_bytes(),
        "narrow_membership_bytes_by_id": {"MEM1": membership_bytes},
        "narrow_manifest_bytes_by_id": {"NSM1": manifest_bytes},
        "narrow_manifest_seals_by_id": {"NSM1": seal},
        "narrow_scope_gate_results_by_id": {
            "NS1": [
                {"gate_id": gate_id, "status": "pass"}
                for gate_id in (
                    "oracle_packet_integrity",
                    "reference_recovery",
                )
            ]
        },
    }

    assert reduce_oracle_verdict(gate_results, [], **kwargs) == "REDRAW"
    assert reduce_oracle_verdict(gate_results, [scope], **kwargs) == "NARROW"

    post_hoc_scope = {**scope, "membership_frozen_before_reveal": "FALSE"}
    post_hoc_scope["source_row_sha256"] = canonical_row_sha256(post_hoc_scope)
    with pytest.raises(ValueError, match="membership must be frozen"):
        reduce_oracle_verdict(gate_results, [post_hoc_scope], **kwargs)


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

    assert reduce_oracle_verdict(
        gate_results,
        [],
        gate_table_bytes=GATE_TABLE.read_bytes(),
        narrow_membership_bytes_by_id={},
        narrow_manifest_bytes_by_id={},
        narrow_manifest_seals_by_id={},
        narrow_scope_gate_results_by_id={},
    ) == "NARROW"


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
        narrow_membership_bytes_by_id={},
        narrow_manifest_bytes_by_id={},
        narrow_manifest_seals_by_id={},
        narrow_scope_gate_results_by_id={},
    ) == "inconclusive"
