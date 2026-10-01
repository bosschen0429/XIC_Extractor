from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.check_productization_authority import (
    DEFAULT_DEPENDENT_MANIFEST,
    DEFAULT_MANIFEST,
    DEPENDENT_HOLD_STATUS,
    DEPENDENT_PACKAGE_AUTHORITY_ARTIFACT,
    DEPENDENT_PACKAGE_AUTHORITY_SHA256,
    check_productization_authority,
    main,
)
from xic_extractor.alignment.quantitative_projection_authority import (
    QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS,
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


def test_productization_authority_checker_replays_exact_dependent_hold() -> None:
    payload = json.loads(DEFAULT_DEPENDENT_MANIFEST.read_text(encoding="utf-8"))

    assert tuple(payload) == QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS
    assert payload["status"] == DEPENDENT_HOLD_STATUS
    assert payload["package_authority_artifact"] == (
        DEPENDENT_PACKAGE_AUTHORITY_ARTIFACT
    )
    assert payload["package_authority_sha256"] == (DEPENDENT_PACKAGE_AUTHORITY_SHA256)
    assert tuple(payload["allowed_registered_writer_scope_ids"]) == (
        ("backfill_policy_write_ready_rows",)
    )
    assert payload["activation_summary_artifact"] == ""
    assert payload["activation_summary_sha256"] == ""
    assert payload["current_effect_cell_count"] == 0


@pytest.mark.parametrize("mutation", ("missing", "extra", "reordered"))
def test_productization_authority_checker_rejects_dependent_manifest_shape(
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = json.loads(DEFAULT_DEPENDENT_MANIFEST.read_text(encoding="utf-8"))
    if mutation == "missing":
        del payload["current_effect_cell_count"]
    elif mutation == "extra":
        payload["future_authority"] = True
    else:
        payload = {key: payload[key] for key in reversed(tuple(payload))}
    manifest = tmp_path / f"dependent-{mutation}.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    problems = check_productization_authority(dependent_manifest_path=manifest)

    assert any("fields" in problem and "order" in problem for problem in problems)
    assert any("owner replay failed" in problem for problem in problems)


def test_productization_authority_checker_rejects_duplicate_json_field(
    tmp_path: Path,
) -> None:
    text = DEFAULT_DEPENDENT_MANIFEST.read_text(encoding="utf-8")
    text = text.replace(
        '"status":"HOLD_PENDING_VALIDATION"',
        '"status":"HOLD_PENDING_VALIDATION","status":"HOLD_PENDING_VALIDATION"',
        1,
    )
    manifest = tmp_path / "dependent-duplicate.json"
    manifest.write_text(text, encoding="utf-8")

    problems = check_productization_authority(dependent_manifest_path=manifest)

    assert any("duplicate JSON object key" in problem for problem in problems)


@pytest.mark.parametrize(
    ("mutation", "expected_problem"),
    (
        ("crossed", "package artifact is crossed"),
        ("resealed_hash", "package hash drifted"),
        (
            "resealed_hold_count",
            "HOLD manifest requires current_effect_cell_count zero",
        ),
        ("status", "must remain HOLD_PENDING_VALIDATION"),
        ("active", "ACTIVE is outside this ticket"),
    ),
)
def test_productization_authority_checker_rejects_crossed_resealed_or_active(
    tmp_path: Path,
    mutation: str,
    expected_problem: str,
) -> None:
    payload = json.loads(DEFAULT_DEPENDENT_MANIFEST.read_text(encoding="utf-8"))
    if mutation == "crossed":
        payload["package_authority_artifact"] = (
            "docs/superpowers/productization/status/productization_authority_manifest.json"
        )
        payload["package_authority_sha256"] = _raw_sha256(DEFAULT_MANIFEST)
    elif mutation == "resealed_hash":
        payload["package_authority_sha256"] = "0" * 64
    elif mutation == "resealed_hold_count":
        payload["current_effect_cell_count"] = 1
    elif mutation == "status":
        payload["status"] = "PAUSED"
    else:
        payload["status"] = "ACTIVE"
        payload["activation_summary_artifact"] = (
            "docs/superpowers/validation/"
            "untargeted_owner_backfill_canonical_quantitative_activation/"
            "activation_summary.json"
        )
        payload["activation_summary_sha256"] = "a" * 64
        payload["current_effect_cell_count"] = 1
    _reseal_dependent_manifest(payload)
    manifest = tmp_path / f"dependent-{mutation}.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    problems = check_productization_authority(dependent_manifest_path=manifest)

    assert any(expected_problem in problem for problem in problems)
    assert any("owner replay failed" in problem for problem in problems)


@pytest.mark.parametrize("mutation", ("extra_scope", "dependent_writer_lane"))
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
        payload["current_authority"][
            "untargeted_owner_backfill_canonical_quantitative_projection"
        ] = {"authority_scope": "forbidden_dependent_writer_scope"}
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
    payload["explanation_only_sources"][0][
        "artifact"
    ] = "docs/superpowers/validation/missing_backfill_summary.json"
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_productization_authority(manifest_path=manifest)

    assert any(
        "explanation compact artifact is missing: "
        "docs/superpowers/validation/missing_backfill_summary.json" in problem
        for problem in problems
    )


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reseal_dependent_manifest(payload: dict[str, object]) -> None:
    projected = {
        field: payload[field]
        for field in QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS[:-1]
    }
    digest = hashlib.sha256(
        json.dumps(
            projected,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload["manifest_content_id"] = (
        "quantitative-projection-authority-manifest:" + digest
    )
