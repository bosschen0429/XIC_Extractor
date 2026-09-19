from __future__ import annotations

import hashlib
import json
from pathlib import Path

from xic_extractor.discovery.cid_nl_oracle_primitives import sha256_canonical_json

ROOT = Path(__file__).resolve().parents[1]
SEAL = (
    ROOT
    / "docs/superpowers/validation/cid_nl_oracle_packet_preflight/"
    "cid_nl_oracle_owner_policy_seal.json"
)
SUMMARY = SEAL.with_name("cid_nl_oracle_packet_preflight_summary.json")


def test_packet_summary_binds_current_owner_policy_seal_bytes() -> None:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    owner_policy = summary["owner_policy"]

    assert owner_policy["policy_seal"] == SEAL.name
    assert owner_policy["policy_seal_sha256"] == hashlib.sha256(
        SEAL.read_bytes()
    ).hexdigest()


def test_owner_policy_seal_binds_scope_rationale_and_decision_hashes() -> None:
    seal = json.loads(SEAL.read_text(encoding="utf-8"))
    decisions = {row["gate_id"]: row for row in seal["decisions"]}

    assert seal["owner_identity"] == "Chao-hu-Lab repository owner"
    assert seal["owner_role"] == "repository_domain_owner"
    assert seal["decision_source"] == "captured_in_policy_seal"
    assert seal["decision_context"].endswith("/issues/125")
    assert set(decisions) == {
        "untargeted_non_inferiority",
        "automated_coverage",
    }
    assert all(row["scope"] and row["rationale"] for row in decisions.values())

    for decision in decisions.values():
        payload = {
            key: value
            for key, value in decision.items()
            if key != "decision_record_sha256"
        }
        assert decision["decision_record_sha256"] == sha256_canonical_json(
            payload
        )

    assert all(value is False for value in seal["authority"].values())
