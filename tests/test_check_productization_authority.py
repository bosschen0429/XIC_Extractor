from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.check_productization_authority import (
    DEFAULT_MANIFEST,
    check_productization_authority,
    main,
)

BACKFILL_EXTERNALIZED_SUMMARY = (
    "docs/superpowers/validation/"
    "backfill_generated_policy_externalized_artifacts/"
    "backfill_generated_policy_externalized_artifacts_summary.json"
)


@pytest.mark.ledger_snapshot
def test_productization_authority_checker_accepts_repo_artifacts() -> None:
    assert check_productization_authority() == []
    assert main([]) == 0


def test_productization_authority_checker_rejects_duplicate_json_field(
    tmp_path: Path,
) -> None:
    text = DEFAULT_MANIFEST.read_text(encoding="utf-8")
    text = text.replace(
        '"status": "active"',
        '"status": "active", "status": "active"',
        1,
    )
    manifest = tmp_path / "writer-duplicate.json"
    manifest.write_text(text, encoding="utf-8")

    problems = check_productization_authority(manifest_path=manifest)

    assert any("duplicate JSON object key" in problem for problem in problems)


@pytest.mark.parametrize("mutation", ("extra_scope", "unregistered_writer_lane"))
def test_productization_authority_checker_keeps_writer_manifest_unchanged(
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    if mutation == "extra_scope":
        payload["authority_policy"]["product_writer_allowed_scopes"].append(
            "cid_nl_adopt_ready_feature_inclusion_95_cells"
        )
    else:
        payload["current_authority"]["unregistered_matrix_writer"] = {
            "authority_scope": "forbidden_unregistered_writer_scope"
        }
    manifest = tmp_path / f"writer-{mutation}.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    problems = check_productization_authority(manifest_path=manifest)

    if mutation == "extra_scope":
        assert any("exact registered scopes in order" in p for p in problems)
    else:
        assert any("exact registered lanes" in p for p in problems)


def test_productization_authority_checker_rejects_retired_cid_nl_scope(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / DEFAULT_MANIFEST.name
    shutil.copyfile(DEFAULT_MANIFEST, manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["authority_policy"]["product_writer_allowed_scopes"].append(
        "cid_nl_adopt_ready_feature_inclusion_95_cells"
    )
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_productization_authority(manifest_path=manifest)

    assert any("exact registered scopes" in problem for problem in problems)


def test_productization_authority_checker_rejects_retired_clean_target_scope(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / DEFAULT_MANIFEST.name
    shutil.copyfile(DEFAULT_MANIFEST, manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["authority_policy"]["product_writer_allowed_scopes"].append(
        "backfill_expansion_clean_target_selective_activation_84_cells"
    )
    payload["current_authority"][
        "backfill_expansion_clean_target_selective_activation"
    ] = {
        "authority_scope": (
            "backfill_expansion_clean_target_selective_activation_84_cells"
        )
    }
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_productization_authority(manifest_path=manifest)

    assert any("exact registered scopes" in problem for problem in problems)
    assert any("exact registered lanes" in problem for problem in problems)


@pytest.mark.parametrize(
    "scope_id",
    (
        "backfill_expansion_raw_trace_expected_diff_666_cells",
        "backfill_expansion_clean_target_selective_activation_84_cells",
    ),
)
def test_productization_authority_checker_pins_retired_scope_status(
    tmp_path: Path,
    scope_id: str,
) -> None:
    manifest = tmp_path / DEFAULT_MANIFEST.name
    shutil.copyfile(DEFAULT_MANIFEST, manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    scope = next(
        item
        for item in payload["blocked_or_negative_evidence_scopes"]
        if item["scope_id"] == scope_id
    )
    scope["status"] = "held_candidate"
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_productization_authority(manifest_path=manifest)

    assert f"{scope_id} status must be retired" in problems


def test_productization_authority_checker_requires_compact_artifact(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / DEFAULT_MANIFEST.name
    shutil.copyfile(DEFAULT_MANIFEST, manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["explanation_only_sources"][0]["artifact"] = (
        "docs/superpowers/validation/missing_backfill_summary.json"
    )
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_productization_authority(manifest_path=manifest)

    assert any(
        "explanation compact artifact is missing: "
        "docs/superpowers/validation/missing_backfill_summary.json" in problem
        for problem in problems
    )
