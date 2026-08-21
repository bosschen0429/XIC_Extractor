from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.check_productization_state import artifact_sha256

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    ROOT / "docs/superpowers/schemas/productization_authority_manifest.v1.json"
)
DEPENDENT_MANIFEST_PATH = (
    ROOT
    / "docs/superpowers/schemas/quantitative_projection_authority_manifest.v1.json"
)

APPROVED_SCOPE = "backfill_policy_write_ready_rows"
CID_NL_APPROVED_SCOPE = "cid_nl_adopt_ready_feature_inclusion_95_cells"
PARKED_SCOPE = "broad_backfill"
NEGATIVE_SCOPE_IDS = {
    "all_stability",
    "apex_delta_clean",
    "width_only_clean",
    "shape_margin_clean",
    "shape_clean_reintegration_stable_writer_probe",
    "backfill_expansion_raw_trace_expected_diff_666_cells",
    "backfill_expansion_clean_target_selective_activation_84_cells",
}


def test_manifest_freezes_current_backfill_authority() -> None:
    manifest = _read_json(MANIFEST_PATH)
    policy = manifest["authority_policy"]
    backfill = manifest["current_authority"]["backfill"]

    assert policy["unregistered_scope_policy"] == "fail_closed"
    assert set(policy["product_writer_allowed_scopes"]) == {
        APPROVED_SCOPE,
        CID_NL_APPROVED_SCOPE,
    }
    assert "quality_blockers" in policy["forbidden_authority_sources"]
    assert "broad_backfill_candidate_universe" in (
        policy["forbidden_authority_sources"]
    )

    assert {
        "candidate_audit_universe_rows",
        "current_product_authority_rows",
        "detected_flagged_rows",
        "blocked_rows",
    }.isdisjoint(backfill)
    assert backfill["authority_scope"] == APPROVED_SCOPE

    cid_nl = manifest["current_authority"]["cid_nl_default_activation"]
    assert cid_nl["current_product_authority_rows"] == 95
    assert cid_nl["existing_successor_context_rows"] == 337
    assert cid_nl["omitted_no_target_rows"] == 27
    assert cid_nl["authority_scope"] == CID_NL_APPROVED_SCOPE
    assert artifact_sha256(ROOT / cid_nl["artifact"]) == cid_nl["artifact_sha256"]
    assert (
        artifact_sha256(ROOT / cid_nl["acceptance_artifact"])
        == cid_nl["acceptance_artifact_sha256"]
    )
    assert (
        artifact_sha256(ROOT / cid_nl["compact_manifest"])
        == cid_nl["compact_manifest_sha256"]
    )

    broad_backfill = manifest["parked_lanes"][PARKED_SCOPE]
    assert broad_backfill["status"] == "parked"
    assert broad_backfill["may_grant_write_authority"] is False

    explanation_sources = manifest["explanation_only_sources"]
    assert explanation_sources
    assert all(
        source["may_grant_write_authority"] is False
        for source in explanation_sources
    )

    blocked_scope_ids = {
        scope["scope_id"]
        for scope in manifest["blocked_or_negative_evidence_scopes"]
    }
    assert NEGATIVE_SCOPE_IDS <= blocked_scope_ids
    retired_scopes = {
        scope["scope_id"]: scope["status"]
        for scope in manifest["blocked_or_negative_evidence_scopes"]
        if scope["scope_id"].startswith("backfill_expansion_")
    }
    assert retired_scopes == {
        "backfill_expansion_raw_trace_expected_diff_666_cells": "retired",
        "backfill_expansion_clean_target_selective_activation_84_cells": "retired",
    }


def test_dependent_hold_does_not_change_writer_authority() -> None:
    writer_manifest = _read_json(MANIFEST_PATH)
    dependent_manifest = _read_json(DEPENDENT_MANIFEST_PATH)
    approved_scope_order = (
        APPROVED_SCOPE,
        CID_NL_APPROVED_SCOPE,
    )

    assert tuple(
        writer_manifest["authority_policy"]["product_writer_allowed_scopes"]
    ) == approved_scope_order
    assert tuple(writer_manifest["current_authority"]) == (
        "backfill",
        "cid_nl_default_activation",
    )
    assert dependent_manifest["status"] == "HOLD_PENDING_VALIDATION"
    assert tuple(dependent_manifest["allowed_registered_writer_scope_ids"]) == (
        approved_scope_order
    )
    assert dependent_manifest["current_effect_cell_count"] == 0
    assert dependent_manifest["activation_summary_artifact"] == ""
    assert dependent_manifest["activation_summary_sha256"] == ""


def test_manifest_compact_artifact_hashes_match() -> None:
    manifest = _read_json(MANIFEST_PATH)
    compact_artifacts = (
        manifest["current_authority"]["backfill"],
        manifest["explanation_only_sources"][0],
    )
    for source in compact_artifacts:
        assert artifact_sha256(ROOT / source["artifact"]) == source["artifact_sha256"]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
