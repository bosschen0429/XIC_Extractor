from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.diagnostics.retire_docs import (
    VAULT_ARCHIVE_DIR,
    copy_source_only,
    find_retire_candidates,
    retire_files,
)
from tools.diagnostics.retire_docs import (
    main as retire_docs_main,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _completed_spec(owner: str = "docs/product/backfill.md") -> str:
    return "\n".join(
        [
            "# Backfill spec",
            "",
            "Doc placement: formal_repo_doc",
            "Doc kind: spec",
            "Doc lifecycle: implemented",
            f"Repo owner: {owner}",
            "Doc exit rule: retire after product owner absorption.",
            "",
            "Durable conclusion already lives in the product owner.",
        ]
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _retirement_evidence(
    rel: str,
    text: str,
    *,
    exact_referrers: list[str] | None = None,
) -> dict[str, object]:
    return {
        "version": 1,
        "entries": [
            {
                "source_path": rel,
                "review_result": "pass_can_retire",
                "owner_paths": ["docs/product/backfill.md"],
                "owner_anchors": [
                    "docs/product/backfill.md#current-backfill-direction"
                ],
                "absorbed_claims": [
                    "durable conclusion already lives in product owner"
                ],
                "absorbed_negative_claims": ["negative_payload=none"],
                "source_copy_readback_verified": True,
                "source_hash": _sha256(text),
                "exact_referrers": exact_referrers or [],
                "active_followups": [],
            }
        ],
    }


def _discard_evidence(rel: str, text: str) -> dict[str, object]:
    evidence = _retirement_evidence(rel, text)
    entry = evidence["entries"][0]
    assert isinstance(entry, dict)
    entry.pop("source_copy_readback_verified")
    entry.update(
        {
            "history_disposition": "discard_git_history",
            "discard_reason": "Disposable construction record.",
            "operator_approved_temporal_retirement": True,
        }
    )
    return evidence


def _same_path_copy_evidence(rel: str, text: str) -> dict[str, object]:
    source_hash = _sha256(text)
    return {
        "transaction": "body_to_compatibility_index",
        "source_hash": source_hash,
        "entries": [
            {
                "source_path": rel,
                "transaction": "body_to_compatibility_index",
                "source_hash": source_hash,
                "unresolved_claims": [],
            }
        ],
    }


def test_source_copy_only_writes_and_reads_back_exact_bytes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/plans/control-plane.md"
    source = repo / rel
    text = "# Source\r\n\r\nExact history.\r\n"
    source.parent.mkdir(parents=True)
    source.write_bytes(text.encode("utf-8"))

    result = copy_source_only(
        source,
        repo,
        vault,
        evidence=_same_path_copy_evidence(rel, text),
        execute=True,
    )

    assert result.error == ""
    assert result.readback_verified
    assert result.destination is not None
    assert result.destination.stem == (
        f"control-plane-source-{result.source_hash[:12]}"
    )
    assert result.destination.read_bytes() == source.read_bytes()
    assert result.locator_readback_verified
    assert result.locator_destination is not None
    assert result.locator_destination.stem == (
        f"control-plane-source-{result.source_hash[:12]}-locator"
    )
    locator_text = result.locator_destination.read_text(encoding="utf-8")
    assert f"source_repo_path:{rel}" in locator_text
    assert f"[[{result.destination.stem}]]" in locator_text
    assert result.source_hash in locator_text
    assert _sha256(locator_text) == result.locator_hash


def test_source_copy_only_rejects_conflicting_locator_sidecar(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/plans/control-plane.md"
    source = repo / rel
    text = "# Source\n"
    _write(source, text)
    source_hash = _sha256(text)
    locator = (
        vault
        / VAULT_ARCHIVE_DIR
        / f"control-plane-source-{source_hash[:12]}-locator.md"
    )
    _write(locator, "# Unrelated note\n")

    result = copy_source_only(
        source,
        repo,
        vault,
        evidence=_same_path_copy_evidence(rel, text),
        execute=True,
    )

    assert result.error == "source-copy locator exists with different bytes"
    assert not result.readback_verified
    assert not result.locator_readback_verified
    assert not (
        vault
        / VAULT_ARCHIVE_DIR
        / f"control-plane-source-{source_hash[:12]}.md"
    ).exists()


def test_source_copy_only_rejects_stale_evidence_hash(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/plans/control-plane.md"
    source = repo / rel
    _write(source, "# Source\n")
    evidence = _same_path_copy_evidence(rel, "# Different source\n")

    result = copy_source_only(
        source,
        repo,
        vault,
        evidence=evidence,
        execute=True,
    )

    assert result.error == "source-copy evidence source_hash mismatch"
    assert not result.readback_verified


@pytest.mark.parametrize(
    "malformed_unresolved_claims",
    ("CP-123", [1], {"id": "CP-123"}),
)
def test_source_copy_only_rejects_malformed_unresolved_claims(
    tmp_path: Path,
    malformed_unresolved_claims: object,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/plans/control-plane.md"
    source = repo / rel
    _write(source, "# Source\n")
    evidence = _same_path_copy_evidence(rel, "# Source\n")
    entry = evidence["entries"][0]
    assert isinstance(entry, dict)
    entry["unresolved_claims"] = malformed_unresolved_claims

    result = copy_source_only(
        source,
        repo,
        vault,
        evidence=evidence,
        execute=True,
    )

    assert result.error == (
        "source-copy evidence unresolved_claims must be a string list"
    )
    assert not result.readback_verified
    assert result.destination is None


def test_completed_unreferenced_spec_without_retirement_evidence_is_kept(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    spec = repo / "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    _write(spec, _completed_spec())

    result = retire_files([spec], repo, vault, execute=True)

    assert result.retired == []
    assert result.kept == [
        (
            "docs/superpowers/specs/2026-07-01-backfill-spec.md",
            "missing retirement evidence",
        )
    ]
    assert spec.exists()


def test_completed_unreferenced_spec_with_pass_can_retire_evidence_retires(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    spec = repo / rel
    text = _completed_spec()
    _write(spec, text)

    result = retire_files(
        [spec],
        repo,
        vault,
        execute=True,
        evidence=_retirement_evidence(rel, text),
    )

    assert result.retired == [rel]
    assert not spec.exists()
    assert (vault / VAULT_ARCHIVE_DIR / "2026-07-01-backfill-spec.md").exists()


def test_approved_discard_retires_unmarked_plan_without_vault(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    rel = "docs/superpowers/plans/2026-07-01-unmarked-plan.md"
    plan = repo / rel
    text = "# Historical construction plan\n"
    _write(plan, text)

    result = retire_files(
        [plan],
        repo,
        None,
        execute=True,
        evidence=_discard_evidence(rel, text),
    )

    assert result.retired == [rel]
    assert result.errors == []
    assert not plan.exists()


def test_discard_requires_explicit_reason_and_operator_approval(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    rel = "docs/superpowers/specs/2026-07-01-example.md"
    spec = repo / rel
    text = _completed_spec()
    _write(spec, text)
    evidence = _discard_evidence(rel, text)
    entry = evidence["entries"][0]
    assert isinstance(entry, dict)
    entry["discard_reason"] = ""

    result = retire_files([spec], repo, None, execute=True, evidence=evidence)

    assert result.kept == [(rel, "discard_git_history requires a discard_reason")]
    assert spec.exists()


def test_discard_batch_ignores_refs_between_approved_batch_members(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    first_rel = "docs/superpowers/plans/2026-07-01-first.md"
    second_rel = "docs/superpowers/specs/2026-07-01-second.md"
    first = repo / first_rel
    second = repo / second_rel
    first_text = f"# First\n\nSee `{second_rel}`.\n"
    second_text = "# Second\n"
    _write(first, first_text)
    _write(second, second_text)
    first_entry = _discard_evidence(first_rel, first_text)["entries"][0]
    second_entry = _discard_evidence(second_rel, second_text)["entries"][0]
    evidence = {"entries": [first_entry, second_entry]}

    result = retire_files(
        [first, second],
        repo,
        None,
        execute=True,
        evidence=evidence,
    )

    assert result.retired == [first_rel, second_rel]
    assert result.referrer_bound == []


@pytest.mark.parametrize(
    "blocking_state",
    ("active_lifecycle", "missing_evidence", "hash_mismatch", "vault_copy_failure"),
)
def test_batch_member_not_retired_remains_a_referrer(
    tmp_path: Path,
    blocking_state: str,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    referrer_rel = "docs/superpowers/plans/2026-07-01-referrer.md"
    target_rel = "docs/superpowers/specs/2026-07-01-target.md"
    referrer = repo / referrer_rel
    target = repo / target_rel
    referrer_text = _completed_spec() + f"\n\nSee `{target_rel}`.\n"
    target_text = _completed_spec()
    evidence_entries: list[object] = []

    if blocking_state == "active_lifecycle":
        referrer_text = referrer_text.replace(
            "Doc lifecycle: implemented",
            "Doc lifecycle: active",
        )
    elif blocking_state == "hash_mismatch":
        referrer_entry = _discard_evidence(referrer_rel, referrer_text)["entries"][0]
        assert isinstance(referrer_entry, dict)
        referrer_entry["source_hash"] = "not-the-real-hash"
        evidence_entries.append(referrer_entry)
    elif blocking_state == "vault_copy_failure":
        referrer_entry = _retirement_evidence(referrer_rel, referrer_text)["entries"][0]
        evidence_entries.append(referrer_entry)
        source_hash = _sha256(referrer_text)
        archive_destination = vault / VAULT_ARCHIVE_DIR / referrer.name
        for collision_path in (
            archive_destination,
            archive_destination.with_name(
                f"{archive_destination.stem}-{source_hash[:12]}.md"
            ),
            archive_destination.with_name(
                f"{archive_destination.stem}-{source_hash}.md"
            ),
        ):
            _write(collision_path, "# Different archived note\n")

    target_entry = _discard_evidence(target_rel, target_text)["entries"][0]
    evidence_entries.append(target_entry)
    _write(referrer, referrer_text)
    _write(target, target_text)

    result = retire_files(
        [referrer, target],
        repo,
        vault,
        execute=True,
        evidence={"entries": evidence_entries},
    )

    assert target_rel not in result.retired
    assert result.referrer_bound == [(target_rel, (referrer_rel,))]
    assert referrer.exists()
    assert target.exists()


def test_retirement_reuses_existing_vault_copy_only_when_source_matches(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    spec = repo / rel
    text = _completed_spec()
    _write(spec, text)
    _write(vault / "somewhere/2026-07-01-backfill-spec.md", text)

    result = retire_files(
        [spec],
        repo,
        vault,
        execute=True,
        evidence=_retirement_evidence(rel, text),
    )

    assert result.already_in_vault == [
        (rel, vault / "somewhere/2026-07-01-backfill-spec.md")
    ]
    assert result.copied_to_vault == []
    assert result.retired == [rel]


def test_retirement_does_not_trust_same_title_unrelated_vault_note(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    spec = repo / rel
    text = _completed_spec()
    _write(spec, text)
    _write(
        vault / "somewhere/2026-07-01-backfill-spec.md",
        "# Different note\n\nThis is not a source copy.\n",
    )

    result = retire_files(
        [spec],
        repo,
        vault,
        execute=True,
        evidence=_retirement_evidence(rel, text),
    )

    assert result.already_in_vault == []
    assert result.copied_to_vault == [
        (rel, vault / VAULT_ARCHIVE_DIR / "2026-07-01-backfill-spec.md")
    ]
    assert result.retired == [rel]
    assert (vault / VAULT_ARCHIVE_DIR / "2026-07-01-backfill-spec.md").read_text(
        encoding="utf-8"
    ) == text


def test_retirement_uses_hash_suffix_when_archive_title_collides(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    spec = repo / rel
    text = _completed_spec()
    _write(spec, text)
    archive_destination = vault / VAULT_ARCHIVE_DIR / spec.name
    _write(archive_destination, "# Different archived note\n")

    result = retire_files(
        [spec],
        repo,
        vault,
        execute=True,
        evidence=_retirement_evidence(rel, text),
    )

    expected = archive_destination.with_name(
        f"{archive_destination.stem}-{_sha256(text)[:12]}.md"
    )
    assert result.copied_to_vault == [(rel, expected)]
    assert expected.read_text(encoding="utf-8") == text
    assert archive_destination.read_text(encoding="utf-8") == (
        "# Different archived note\n"
    )
    assert result.retired == [rel]


def test_retirement_refuses_to_overwrite_all_hash_collision_destinations(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    spec = repo / rel
    text = _completed_spec()
    source_hash = _sha256(text)
    _write(spec, text)
    archive_destination = vault / VAULT_ARCHIVE_DIR / spec.name
    collision_paths = (
        archive_destination,
        archive_destination.with_name(
            f"{archive_destination.stem}-{source_hash[:12]}.md"
        ),
        archive_destination.with_name(
            f"{archive_destination.stem}-{source_hash}.md"
        ),
    )
    for index, collision_path in enumerate(collision_paths):
        _write(collision_path, f"# Different archived note {index}\n")

    result = retire_files(
        [spec],
        repo,
        vault,
        execute=True,
        evidence=_retirement_evidence(rel, text),
    )

    assert result.errors == [
        (rel, "vault archive destinations all contain different content")
    ]
    assert result.copied_to_vault == []
    assert result.retired == []
    assert spec.read_text(encoding="utf-8") == text
    for index, collision_path in enumerate(collision_paths):
        assert collision_path.read_text(encoding="utf-8") == (
            f"# Different archived note {index}\n"
        )


def test_retirement_reuses_wrapped_vault_source_copy_with_matching_body(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    spec = repo / rel
    text = _completed_spec()
    _write(spec, text)
    _write(
        vault / "somewhere/2026-07-01-backfill-spec.md",
        "\n".join(
            [
                "---",
                f'source_repo_path: "{rel}"',
                "---",
                "",
                "## Original Content",
                text,
            ]
        ),
    )

    result = retire_files(
        [spec],
        repo,
        vault,
        execute=True,
        evidence=_retirement_evidence(rel, text),
    )

    assert result.already_in_vault == [
        (rel, vault / "somewhere/2026-07-01-backfill-spec.md")
    ]
    assert result.copied_to_vault == []
    assert result.retired == [rel]


def test_retirement_evidence_file_is_not_counted_as_repo_referrer(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    spec = repo / rel
    text = _completed_spec()
    evidence = _retirement_evidence(rel, text)
    _write(spec, text)
    _write(
        repo
        / (
            "docs/superpowers/file-management/docs-cleanup/"
            "2026-07-01-retirement-evidence.json"
        ),
        json.dumps(evidence),
    )

    result = retire_files(
        [spec],
        repo,
        vault,
        execute=True,
        evidence=evidence,
    )

    assert result.referrer_bound == []
    assert result.retired == [rel]
    assert not spec.exists()


def test_retirement_evidence_hash_mismatch_is_kept(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    rel = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    spec = repo / rel
    text = _completed_spec()
    _write(spec, text)
    evidence = _retirement_evidence(rel, text)
    entry = evidence["entries"][0]
    assert isinstance(entry, dict)
    entry["source_hash"] = "not-the-real-hash"

    result = retire_files([spec], repo, vault, execute=True, evidence=evidence)

    assert result.retired == []
    assert result.kept == [(rel, "retirement evidence source_hash mismatch")]
    assert spec.exists()


def test_active_spec_is_kept_even_when_swept(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    spec = repo / "docs/superpowers/specs/2026-07-01-active-spec.md"
    _write(
        spec,
        _completed_spec().replace(
            "Doc lifecycle: implemented",
            "Doc lifecycle: active",
        ),
    )

    result = retire_files([spec], repo, vault, execute=True)

    assert result.retired == []
    assert result.kept == [
        ("docs/superpowers/specs/2026-07-01-active-spec.md", "active lifecycle")
    ]
    assert spec.exists()


def test_missing_lifecycle_metadata_is_not_auto_retired(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    note = repo / "docs/superpowers/plans/2026-07-01-unmarked-plan.md"
    _write(note, "# Plan\n\nHistorical implementation note.\n")

    result = retire_files([note], repo, vault, execute=True)

    assert result.retired == []
    assert result.kept == [
        (
            "docs/superpowers/plans/2026-07-01-unmarked-plan.md",
            (
                "missing or invalid lifecycle metadata; run product-absorption "
                "review before retirement"
            ),
        )
    ]
    assert note.exists()


def test_referrer_bound_spec_is_kept_without_stub_option(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    spec = repo / "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    rel = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    _write(spec, _completed_spec())
    _write(repo / "docs/product/backfill.md", f"# Backfill\n\nSee `{rel}`.\n")

    result = retire_files([spec], repo, vault, execute=True)

    assert result.retired == []
    assert result.referrer_bound == [(rel, ("docs/product/backfill.md",))]
    assert spec.exists()


def test_referrer_bound_spec_cannot_be_replaced_with_short_stub(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    spec = repo / "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    rel = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    text = _completed_spec()
    _write(spec, text)
    _write(repo / "docs/product/backfill.md", f"# Backfill\n\nSee `{rel}`.\n")

    result = retire_files(
        [spec],
        repo,
        vault,
        execute=True,
        evidence=_retirement_evidence(
            rel,
            text,
            exact_referrers=["docs/product/backfill.md"],
        ),
    )

    assert result.retired == []
    assert result.referrer_bound == [(rel, ("docs/product/backfill.md",))]
    assert spec.read_text(encoding="utf-8") == text


def test_sweep_finds_markdown_transient_candidates_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    spec = repo / "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    schema = repo / "docs/superpowers/specs/not-a-spec.json"
    readme = repo / "docs/superpowers/notes/README.md"
    _write(spec, _completed_spec())
    _write(schema, "{}\n")
    _write(readme, "# Specs\n")

    result = find_retire_candidates(repo)

    assert result == [spec]


def test_retire_docs_cli_returns_nonzero_on_result_errors(
    tmp_path: Path,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    repo.mkdir()
    vault.mkdir()

    code = retire_docs_main(
        [
            "--execute",
            "--repo-root",
            str(repo),
            "--vault-path",
            str(vault),
            "docs/superpowers/specs/missing.md",
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "Errors (1)" in output
    assert "file does not exist" in output
