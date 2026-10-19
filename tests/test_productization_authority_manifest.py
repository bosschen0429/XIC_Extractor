from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.check_productization_state import artifact_sha256

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    ROOT
    / "docs/superpowers/productization/status/"
    "productization_authority_manifest.json"
)

NEGATIVE_SCOPE_IDS = {
    "all_stability",
    "apex_delta_clean",
    "width_only_clean",
    "shape_margin_clean",
    "shape_clean_reintegration_stable_writer_probe",
    "backfill_expansion_raw_trace_expected_diff_666_cells",
    "backfill_expansion_clean_target_selective_activation_84_cells",
}


def test_manifest_has_no_registered_file_activation_writer() -> None:
    manifest = _read_json(MANIFEST_PATH)
    policy = manifest["authority_policy"]

    assert policy["unregistered_scope_policy"] == "fail_closed"
    assert policy["product_writer_allowed_scopes"] == []
    assert "quality_blockers" in policy["forbidden_authority_sources"]
    assert "broad_backfill_candidate_universe" in (
        policy["forbidden_authority_sources"]
    )

    assert manifest["current_authority"] == {}

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


def test_manifest_compact_artifact_hashes_match() -> None:
    manifest = _read_json(MANIFEST_PATH)
    compact_artifacts = (
        manifest["explanation_only_sources"][0],
    )
    for source in compact_artifacts:
        assert artifact_sha256(ROOT / source["artifact"]) == source["artifact_sha256"]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
