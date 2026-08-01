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
    path.write_text(text, encoding="utf-8")


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


def test_referrer_bound_spec_can_be_replaced_with_short_stub(
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
        stub_bound=True,
        evidence=_retirement_evidence(
            rel,
            text,
            exact_referrers=["docs/product/backfill.md"],
        ),
    )

    assert result.retired == []
    assert result.stubbed == [(rel, "docs/product/backfill.md")]
    text = spec.read_text(encoding="utf-8")
    assert "# Retired Document Stub" in text
    assert "Current repo authority: `docs/product/backfill.md`" in text
    assert "source_repo_path:docs/superpowers/specs/2026-07-01-backfill-spec.md" in text


def test_sweep_finds_markdown_transient_candidates_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    spec = repo / "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    schema = repo / "docs/superpowers/specs/not-a-spec.json"
    readme = repo / "docs/superpowers/specs/README.md"
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
