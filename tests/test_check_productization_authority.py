from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.check_productization_authority as authority_checker
from scripts.check_productization_authority import (
    DEFAULT_DEPENDENT_MANIFEST,
    DEFAULT_MANIFEST,
    DEPENDENT_ALLOWED_SCOPE_ORDER,
    DEPENDENT_HOLD_STATUS,
    DEPENDENT_PACKAGE_AUTHORITY_ARTIFACT,
    DEPENDENT_PACKAGE_AUTHORITY_SHA256,
    check_productization_authority,
    main,
)
from scripts.check_productization_state import artifact_sha256
from xic_extractor.alignment.production_acceptance import (
    ProductionAcceptanceManifestError,
)
from xic_extractor.alignment.quantitative_projection_authority import (
    QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1,
)

BACKFILL_EXTERNALIZED_SUMMARY = (
    "docs/superpowers/validation/"
    "backfill_generated_policy_externalized_artifacts_v1/"
    "backfill_generated_policy_externalized_artifacts_summary.json"
)


@pytest.mark.ledger_snapshot
def test_productization_authority_checker_accepts_repo_artifacts() -> None:
    assert check_productization_authority() == []
    assert main([]) == 0


def test_productization_authority_checker_replays_exact_dependent_hold() -> None:
    payload = json.loads(DEFAULT_DEPENDENT_MANIFEST.read_text(encoding="utf-8"))

    assert tuple(payload) == QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1
    assert payload["status"] == DEPENDENT_HOLD_STATUS
    assert payload["package_authority_artifact"] == (
        DEPENDENT_PACKAGE_AUTHORITY_ARTIFACT
    )
    assert payload["package_authority_sha256"] == (DEPENDENT_PACKAGE_AUTHORITY_SHA256)
    assert tuple(payload["allowed_registered_writer_scope_ids"]) == (
        DEPENDENT_ALLOWED_SCOPE_ORDER
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
        ("pretty", "owner-rendered canonical bytes"),
        ("extra_lf", "owner-rendered canonical bytes"),
        ("no_lf", "owner-rendered canonical bytes"),
        ("crlf", "owner-rendered canonical bytes"),
        ("bom", "invalid JSON"),
    ),
)
def test_productization_authority_checker_rejects_noncanonical_manifest_bytes(
    tmp_path: Path,
    mutation: str,
    expected_problem: str,
) -> None:
    canonical = DEFAULT_DEPENDENT_MANIFEST.read_bytes()
    if mutation == "pretty":
        payload = json.loads(canonical.decode("utf-8"))
        raw = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    elif mutation == "extra_lf":
        raw = canonical + b"\n"
    elif mutation == "no_lf":
        raw = canonical.removesuffix(b"\n")
    elif mutation == "crlf":
        raw = canonical.replace(b"\n", b"\r\n")
    else:
        raw = b"\xef\xbb\xbf" + canonical
    manifest = tmp_path / f"dependent-{mutation}.json"
    manifest.write_bytes(raw)

    problems = check_productization_authority(dependent_manifest_path=manifest)

    assert any(expected_problem in problem for problem in problems)


def test_productization_authority_checker_reads_dependent_manifest_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_bytes = Path.read_bytes
    dependent_read_count = 0

    def tracked_read_bytes(path: Path) -> bytes:
        nonlocal dependent_read_count
        if path == DEFAULT_DEPENDENT_MANIFEST:
            dependent_read_count += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    assert check_productization_authority() == []
    assert dependent_read_count == 1


def test_productization_authority_public_gate_propagates_owner_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_current_membership(**_kwargs: object) -> None:
        raise ProductionAcceptanceManifestError(
            ("crossed current cell membership",)
        )

    monkeypatch.setattr(
        authority_checker,
        "load_checked_production_acceptance_manifest",
        reject_current_membership,
    )

    assert check_productization_authority() == [
        "current Backfill production acceptance: crossed current cell membership"
    ]


def test_productization_authority_public_gate_uses_writer_eligible_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked = authority_checker.load_checked_production_acceptance_manifest(
        manifest_path=(
            authority_checker.ROOT
            / authority_checker.CURRENT_BACKFILL_ACCEPTANCE_REL
        ),
        repo_root=authority_checker.ROOT,
    )
    rows = [dict(row) for row in checked.rows]
    rows[0].update(
        acceptance_decision="require_review",
        write_authority="FALSE",
        matrix_write_allowed="FALSE",
    )
    monkeypatch.setattr(
        authority_checker,
        "load_checked_production_acceptance_manifest",
        lambda **_kwargs: SimpleNamespace(rows=tuple(rows)),
    )

    assert check_productization_authority() == [
        "current Backfill owner-checked membership does not match retained "
        "activation values"
    ]


def test_productization_authority_accepts_consistent_membership_size_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked = authority_checker.load_checked_production_acceptance_manifest(
        manifest_path=(
            authority_checker.ROOT
            / authority_checker.CURRENT_BACKFILL_ACCEPTANCE_REL
        ),
        repo_root=authority_checker.ROOT,
    )
    rows = [dict(row) for row in checked.rows]
    changed_key = (rows[0]["peak_hypothesis_id"], rows[0]["sample_stem"])
    rows[0].update(
        acceptance_decision="require_review",
        write_authority="FALSE",
        matrix_write_allowed="FALSE",
    )
    monkeypatch.setattr(
        authority_checker,
        "load_checked_production_acceptance_manifest",
        lambda **_kwargs: SimpleNamespace(rows=tuple(rows)),
    )
    original_reader = authority_checker.read_tsv_with_header_snapshot
    activation_path = (
        authority_checker.ROOT
        / authority_checker.CURRENT_BACKFILL_ACTIVATION_VALUES_REL
    )

    def remove_changed_activation(
        path: Path,
        *,
        exact_columns: tuple[str, ...],
    ) -> tuple[tuple[str, ...], list[dict[str, str]], str]:
        header, activation_rows, sha256 = original_reader(
            path,
            exact_columns=exact_columns,
        )
        if path == activation_path:
            activation_rows = [
                row
                for row in activation_rows
                if (row["peak_hypothesis_id"], row["sample_stem"]) != changed_key
            ]
        return header, activation_rows, sha256

    monkeypatch.setattr(
        authority_checker,
        "read_tsv_with_header_snapshot",
        remove_changed_activation,
    )

    assert check_productization_authority() == []


@pytest.mark.parametrize("mutation", ("truncated", "crossed_value"))
def test_productization_authority_public_gate_reconciles_current_membership(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    original_reader = authority_checker.read_tsv_with_header_snapshot
    activation_path = (
        authority_checker.ROOT
        / authority_checker.CURRENT_BACKFILL_ACTIVATION_VALUES_REL
    )

    def mutate_activation(
        path: Path,
        *,
        exact_columns: tuple[str, ...],
    ) -> tuple[tuple[str, ...], list[dict[str, str]], str]:
        header, rows, sha256 = original_reader(path, exact_columns=exact_columns)
        if path == activation_path:
            rows = [dict(row) for row in rows]
            if mutation == "truncated":
                rows.pop()
            else:
                rows[0]["projected_matrix_value"] = "999"
        return header, rows, sha256

    monkeypatch.setattr(
        authority_checker,
        "read_tsv_with_header_snapshot",
        mutate_activation,
    )

    assert check_productization_authority() == [
        "current Backfill owner-checked membership does not match retained "
        "activation values"
    ]


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
            "docs/superpowers/schemas/productization_authority_manifest.v1.json"
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
            "issue256_untargeted_owner_backfill_canonical_quantitative_activation_v1/"
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
            "untargeted_owner_backfill_canonical_quantitative_projection_v1"
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
    payload["current_authority"]["backfill"][
        "artifact"
    ] = "docs/superpowers/validation/missing_backfill_summary.json"
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_productization_authority(manifest_path=manifest)

    assert any(
        "policy compact artifact is missing: "
        "docs/superpowers/validation/missing_backfill_summary.json" in problem
        for problem in problems
    )


def test_productization_authority_checker_rejects_stale_backfill_acceptance_hash(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / DEFAULT_MANIFEST.name
    shutil.copyfile(DEFAULT_MANIFEST, manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["current_authority"]["backfill"][
        "acceptance_artifact_sha256"
    ] = "STALE_ACCEPTANCE_SHA"
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_productization_authority(manifest_path=manifest)

    assert any(
        "compact summary acceptance sha256 does not match manifest" in problem
        for problem in problems
    )


def test_productization_authority_checker_validates_compact_summary_contents(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / DEFAULT_MANIFEST.name
    summary = tmp_path / BACKFILL_EXTERNALIZED_SUMMARY
    shutil.copyfile(DEFAULT_MANIFEST, manifest)
    summary.parent.mkdir(parents=True)
    shutil.copyfile(
        DEFAULT_MANIFEST.parent.parent
        / "validation/backfill_generated_policy_externalized_artifacts_v1/"
        / "backfill_generated_policy_externalized_artifacts_summary.json",
        summary,
    )

    summary_payload = json.loads(summary.read_text(encoding="utf-8"))
    policy = summary_payload["externalized_artifacts"]["standard_peak_backfill_policy"]
    quality = summary_payload["externalized_artifacts"][
        "standard_peak_backfill_policy_quality_explanations"
    ]
    acceptance = summary_payload["externalized_artifacts"][
        "narrow_product_writer_expected_diff_acceptance"
    ]
    policy["path"] = "output/stale_policy.tsv"
    policy["sha256"] = "STALE_POLICY_SHA"
    policy["write_authority"] = False
    policy["product_authority_scope"] = "wrong_scope"
    quality["sha256"] = "STALE_QUALITY_SHA"
    quality["write_authority"] = True
    quality["may_grant_write_authority"] = True
    acceptance["path"] = "output/stale_acceptance.json"
    acceptance["acceptance_status"] = "fail"
    acceptance["activation_application_status"] = "not_applied"
    acceptance["expected_scope"] = "wrong_scope"
    acceptance["activation_scope_audit_tsv"] = "output/wrong_policy.tsv"
    acceptance["activation_scope_audit_sha256"] = "STALE_ACCEPTANCE_POLICY_SHA"
    summary.write_text(json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8")

    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    compact_relative = summary.relative_to(tmp_path).as_posix()
    manifest_payload["current_authority"]["backfill"]["artifact"] = compact_relative
    manifest_payload["current_authority"]["backfill"][
        "artifact_sha256"
    ] = artifact_sha256(summary)
    manifest_payload["explanation_only_sources"][0]["artifact"] = compact_relative
    manifest_payload["explanation_only_sources"][0][
        "artifact_sha256"
    ] = artifact_sha256(summary)
    manifest.write_text(
        json.dumps(manifest_payload, indent=2) + "\n",
        encoding="utf-8",
    )

    problems = check_productization_authority(
        manifest_path=manifest,
        repo_root=tmp_path,
    )

    assert any(
        "compact summary policy path does not match manifest" in problem
        for problem in problems
    )
    assert any(
        "compact summary policy sha256 does not match manifest" in problem
        for problem in problems
    )
    assert any(
        "compact summary policy write_authority does not match manifest" in problem
        for problem in problems
    )
    assert any(
        "compact summary policy product_authority_scope does not match manifest"
        in problem
        for problem in problems
    )
    assert any(
        "compact summary quality sha256 does not match manifest" in problem
        for problem in problems
    )
    assert any(
        "compact summary quality write_authority does not match manifest" in problem
        for problem in problems
    )
    assert any(
        "compact summary quality may_grant_write_authority does not match manifest"
        in problem
        for problem in problems
    )
    assert any(
        "compact summary acceptance path does not match manifest" in problem
        for problem in problems
    )
    assert any(
        "compact summary acceptance status must be pass" in problem
        for problem in problems
    )
    assert any(
        "compact summary acceptance application status must be applied" in problem
        for problem in problems
    )
    assert any(
        "compact summary acceptance expected_scope does not match manifest"
        in problem
        for problem in problems
    )
    assert any(
        "compact summary acceptance activation_scope_audit_tsv does not match policy"
        in problem
        for problem in problems
    )
    assert any(
        "compact summary acceptance activation_scope_audit_sha256 does not match policy"
        in problem
        for problem in problems
    )


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reseal_dependent_manifest(payload: dict[str, object]) -> None:
    projected = {
        field: payload[field]
        for field in QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1[:-1]
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
        "quantitative-projection-authority-manifest.v1:" + digest
    )
