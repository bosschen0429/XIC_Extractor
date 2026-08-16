from __future__ import annotations

import json
from pathlib import Path

from xic_extractor.discovery.cid_nl_oracle_contract import sha256_canonical_json

ROOT = Path(__file__).resolve().parents[1]
SEAL = (
    ROOT
    / "docs/superpowers/validation/cid_nl_oracle_packet_preflight_v1/"
    "cid_nl_oracle_owner_policy_seal_v1.json"
)


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
