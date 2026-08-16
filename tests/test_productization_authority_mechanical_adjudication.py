from __future__ import annotations

import csv
import json
from collections import Counter
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
SCHEMA_PATH = (
    ROOT / "docs/superpowers/schemas/mechanical_adjudication_schema.v1.json"
)
INDEX_PATH = (
    ROOT / "docs/superpowers/validation/mechanical_adjudication_index_v1.tsv"
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

    assert backfill["candidate_audit_universe_rows"] == 4613
    assert backfill["current_product_authority_rows"] == 511
    assert backfill["detected_flagged_rows"] == 0
    assert backfill["blocked_rows"] == 4102
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


def test_dependent_hold_does_not_change_mechanical_writer_authority() -> None:
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


def test_mechanical_adjudication_index_covers_candidate_universe_and_schema() -> None:
    schema = _read_json(SCHEMA_PATH)
    header, rows = _read_tsv(INDEX_PATH)

    assert set(schema["required_columns"]) <= set(header)
    assert len(rows) == 4613
    assert len({row["row_id"] for row in rows}) == len(rows)

    decisions = Counter(row["decision"] for row in rows)
    assert decisions == {"write_ready": 511, "evidence_required": 4102}

    authority_counts = Counter(row["write_authority"] for row in rows)
    assert authority_counts == {"FALSE": 4102, "TRUE": 511}

    evidence_grades = Counter(row["evidence_grade"] for row in rows)
    assert evidence_grades == {"A": 511, "C": 3015, "D": 1087}

    next_evidence = Counter(row["next_required_evidence"] for row in rows)
    assert next_evidence == {
        "none_current_scope_writer_approved": 511,
        "independent_peak_choice_or_area_truth": 3015,
        "recover_trace_overlay_or_reintegration_evidence": 1087,
    }


def test_write_authority_is_fail_closed_to_manifest_scope() -> None:
    manifest = _read_json(MANIFEST_PATH)
    allowed_scopes = set(manifest["authority_policy"]["product_writer_allowed_scopes"])
    _, rows = _read_tsv(INDEX_PATH)

    for row in rows:
        assert row["write_authority"] in {"TRUE", "FALSE"}
        assert row["may_touch_matrix"] in {"TRUE", "FALSE"}
        assert row["explanation_only"] in {"TRUE", "FALSE"}

        if row["write_authority"] == "TRUE":
            assert row["decision"] == "write_ready"
            assert row["source_policy_decision"] == "write_ready"
            assert row["explanation_only"] == "FALSE"
            assert row["may_touch_matrix"] == "TRUE"
            assert row["product_authority_scope"] in allowed_scopes
        else:
            assert row["may_touch_matrix"] == "FALSE"
            assert row["product_authority_scope"] == ""

    registered_scopes = {
        row["product_authority_scope"]
        for row in rows
        if row["product_authority_scope"]
    }
    assert registered_scopes == {APPROVED_SCOPE}


def test_explanations_quality_blockers_and_parked_scopes_cannot_write() -> None:
    _, rows = _read_tsv(INDEX_PATH)

    explanation_rows = [row for row in rows if row["explanation_only"] == "TRUE"]
    assert len(explanation_rows) == 4102
    assert {row["write_authority"] for row in explanation_rows} == {"FALSE"}

    blocker_rows = [row for row in rows if row["blockers"]]
    assert blocker_rows
    assert {row["write_authority"] for row in blocker_rows} == {"FALSE"}

    source_quality_rows = [row for row in rows if row["source_quality_blockers"]]
    assert len(source_quality_rows) == len(rows)
    source_quality_write_rows = [
        row for row in source_quality_rows if row["write_authority"] == "TRUE"
    ]
    assert len(source_quality_write_rows) == 511
    assert {
        row["product_authority_scope"] for row in source_quality_write_rows
    } == {APPROVED_SCOPE}

    authority_scopes = {
        row["product_authority_scope"]
        for row in rows
        if row["product_authority_scope"]
    }
    assert PARKED_SCOPE not in authority_scopes
    assert not (NEGATIVE_SCOPE_IDS & authority_scopes)


def test_index_source_hashes_match_manifest_and_available_artifacts() -> None:
    manifest = _read_json(MANIFEST_PATH)
    _, rows = _read_tsv(INDEX_PATH)
    source_hash_sets = {row["source_hashes"] for row in rows}

    assert len(source_hash_sets) == 1
    source_hashes = _parse_semicolon_pairs(source_hash_sets.pop())
    assert (
        source_hashes["policy"]
        == manifest["current_authority"]["backfill"][
            "externalized_source_artifact_sha256"
        ]
    )
    assert (
        source_hashes["quality"]
        == manifest["explanation_only_sources"][0][
            "externalized_source_artifact_sha256"
        ]
    )

    compact_artifacts = {
        "policy_summary": manifest["current_authority"]["backfill"],
        "quality_summary": manifest["explanation_only_sources"][0],
    }
    for source in compact_artifacts.values():
        path = ROOT / source["artifact"]
        assert artifact_sha256(path) == source["artifact_sha256"]

    externalized_artifacts = {
        "policy": ROOT
        / manifest["current_authority"]["backfill"]["externalized_source_artifact"],
        "quality": ROOT
        / manifest["explanation_only_sources"][0]["externalized_source_artifact"],
    }
    for source_id, path in externalized_artifacts.items():
        if path.exists():
            assert artifact_sha256(path) == source_hashes[source_id]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_tsv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return tuple(reader.fieldnames or ()), list(reader)


def _parse_semicolon_pairs(value: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in value.split(";"):
        key, _, item = part.partition("=")
        parsed[key] = item
    return parsed
