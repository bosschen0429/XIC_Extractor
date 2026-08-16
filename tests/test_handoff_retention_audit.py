from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tools.diagnostics.docs_scan import load_repository_text_view
from tools.diagnostics.handoff_retention_audit import run_handoff_retention_audit

HEADER = "path\tretention_decision\trepo_owner\tnext_review_event\trationale"
CURRENT = "docs/superpowers/handoffs/current/codex-example.md"
ARCHIVE = "docs/superpowers/handoffs/archive/2026-07-01_codex-example.md"
ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _inventory(root: Path, *rows: str) -> None:
    _write(
        root / "docs/superpowers/handoffs/RETENTION.tsv",
        "\n".join((HEADER, *rows)) + "\n",
    )


def _audit(root: Path, **kwargs: object):
    git_env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    if not (root / ".git").exists():
        subprocess.run(
            ["git", "init"],
            cwd=root,
            check=True,
            capture_output=True,
            env=git_env,
        )
    subprocess.run(
        ["git", "add", "--all"],
        cwd=root,
        check=True,
        capture_output=True,
        env=git_env,
    )
    return run_handoff_retention_audit(
        root,
        **kwargs,
    )


def test_missing_retention_row_is_blocker(tmp_path: Path) -> None:
    _write(tmp_path / CURRENT, "# Current\n\nBranch: `codex/example`\n")
    _inventory(tmp_path)

    result = _audit(tmp_path)

    assert any("no retention inventory row" in msg.message for msg in result.blockers)


def test_injected_repository_view_must_match_requested_root(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _inventory(first)
    _inventory(second)
    _audit(first)
    _audit(second)
    first_view = load_repository_text_view(first)

    result = run_handoff_retention_audit(second, repository_view=first_view)

    assert any(
        "repository view root mismatch" in msg.message for msg in result.blockers
    )


def test_complete_legacy_inventory_is_blocked_and_reports_candidates(
    tmp_path: Path,
) -> None:
    _write(tmp_path / CURRENT, "# Current\n\nBranch: `codex/example`\n")
    _write(tmp_path / ARCHIVE, "# Archive\n\nCompleted review narrative.\n")
    _inventory(
        tmp_path,
        (
            f"{CURRENT}\tactive_current\tPR #1\tpr_merge_or_close\t"
            "Active branch handoff."
        ),
        (
            f"{ARCHIVE}\tmove_to_obsidian_after_pr\tdocs/agent/example.md\t"
            "pr_merge_or_close\tReview details belong in Obsidian after PR."
        ),
    )

    result = _audit(tmp_path)

    assert any(
        "tracked handoff state is forbidden" in m.message for m in result.blockers
    )
    assert any("must remain header-only" in m.message for m in result.blockers)
    assert result.summary["move_to_obsidian_after_pr"] == [ARCHIVE]


def test_default_audit_does_not_report_due_event_warnings(tmp_path: Path) -> None:
    removal = "docs/superpowers/handoffs/archive/2026-07-01_codex-removal.tsv"
    _write(tmp_path / CURRENT, "# Current\n\nBranch: `codex/example`\n")
    _write(tmp_path / ARCHIVE, "# Archive\n\nReview narrative.\n")
    _write(tmp_path / removal, "path\tdecision\n")
    _inventory(
        tmp_path,
        (
            f"{CURRENT}\tactive_current\tPR #1\tpr_merge_or_close\t"
            "Active branch handoff."
        ),
        (
            f"{ARCHIVE}\tmove_to_obsidian_after_pr\tdocs/agent/example.md\t"
            "pr_merge_or_close\tReview details belong in Obsidian after PR."
        ),
        (
            f"{removal}\tremove_after_merge_approval\tdocs/agent/example.md\t"
            "pr_merge_or_close\tTemporary manifest needs explicit removal approval."
        ),
    )

    result = _audit(tmp_path)

    assert any(
        "tracked handoff state is forbidden" in m.message for m in result.blockers
    )
    assert not any("due for event" in msg.message for msg in result.messages)
    assert "due_event" not in result.summary


def test_public_manifest_under_handoff_archive_is_blocker(tmp_path: Path) -> None:
    path = (
        "docs/superpowers/handoffs/archive/"
        "2026-07-01_codex-docs-cleanup_git-rm-candidate-manifest.tsv"
    )
    _write(tmp_path / path, "path\tcandidate_group\n")
    _inventory(
        tmp_path,
        (
            f"{path}\tremove_after_merge_approval\tdocs/agent/example.md\t"
            "referrer_audit\tManifest is intentionally misplaced in fixture."
        ),
    )

    result = _audit(tmp_path)

    assert any("not handoffs" in msg.message for msg in result.blockers)


def test_branch_named_productization_current_handoff_is_still_forbidden(
    tmp_path: Path,
) -> None:
    path = "docs/superpowers/handoffs/current/codex-productization.md"
    _write(tmp_path / path, "# Current\n\nBranch: `codex/productization`\n")
    _inventory(
        tmp_path,
        (
            f"{path}\tactive_current\tPR #1\tpr_merge_or_close\t"
            "Active branch handoff for productization work."
        ),
    )

    result = _audit(tmp_path)

    assert any(
        "tracked handoff state is forbidden" in m.message for m in result.blockers
    )


def test_git_ignored_local_handoff_does_not_need_inventory(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    _write(
        tmp_path / ".gitignore",
        "\n".join(
            [
                "docs/superpowers/handoffs/current/*",
                "docs/superpowers/handoffs/archive/*",
            ]
        )
        + "\n",
    )
    _write(
        tmp_path / "docs/superpowers/handoffs/current/ACTIVE.local.md",
        "# Local handoff\n\nStatus: active.\n",
    )
    _inventory(tmp_path)
    subprocess.run(
        ["git", "add", ".gitignore", "docs/superpowers/handoffs/RETENTION.tsv"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    result = run_handoff_retention_audit(tmp_path)

    assert result.blockers == ()
    assert result.summary["handoff_files"] == 0


def test_force_added_ignored_handoff_still_needs_inventory(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    _write(
        tmp_path / ".gitignore",
        "docs/superpowers/handoffs/current/*\n",
    )
    path = "docs/superpowers/handoffs/current/ACTIVE.local.md"
    _write(tmp_path / path, "# Local handoff\n\nStatus: active.\n")
    _inventory(tmp_path)
    subprocess.run(
        ["git", "add", ".gitignore", "docs/superpowers/handoffs/RETENTION.tsv"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "-f", path],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    result = run_handoff_retention_audit(tmp_path)

    assert any("no retention inventory row" in msg.message for msg in result.blockers)


def test_missing_worktree_handoff_is_audited_from_staged_content(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    _write(tmp_path / CURRENT, "# Current\n\nBranch: `codex/example`\n")
    _inventory(
        tmp_path,
        (
            f"{CURRENT}\tactive_current\tPR #1\tpr_merge_or_close\t"
            "Active branch handoff."
        ),
    )
    subprocess.run(
        ["git", "add", CURRENT, "docs/superpowers/handoffs/RETENTION.tsv"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / CURRENT).unlink()

    result = run_handoff_retention_audit(tmp_path)

    assert any(
        "tracked handoff state is forbidden" in msg.message
        for msg in result.blockers
    )
    assert not any(
        "missing from the working tree" in msg.message for msg in result.messages
    )


def test_nested_git_root_without_fixture_fallback_is_blocker(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    nested = tmp_path / "nested"
    _write(nested / CURRENT, "# Current\n\nBranch: `codex/example`\n")
    _inventory(
        nested,
        (
            f"{CURRENT}\tactive_current\tPR #1\tpr_merge_or_close\t"
            "Active branch handoff."
        ),
    )

    result = run_handoff_retention_audit(nested)

    assert any(
        "must equal the Git worktree root" in msg.message for msg in result.blockers
    )


def test_repo_gitignore_declares_handoff_local_defaults() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "docs/superpowers/handoffs/current/*" in gitignore
    assert "docs/superpowers/handoffs/archive/*" in gitignore
    assert "cc-framework-improvements-productization.md" not in gitignore
    assert "!docs/superpowers/handoffs/archive/public/**" not in gitignore


def test_due_event_reports_post_merge_cleanup_warnings(tmp_path: Path) -> None:
    removal = "docs/superpowers/handoffs/archive/2026-07-01_codex-removal.tsv"
    _write(tmp_path / CURRENT, "# Current\n\nBranch: `codex/example`\n")
    _write(tmp_path / ARCHIVE, "# Archive\n\nReview narrative.\n")
    _write(tmp_path / removal, "path\tdecision\n")
    _inventory(
        tmp_path,
        (
            f"{CURRENT}\tactive_current\tPR #1\tpr_merge_or_close\t"
            "Active branch handoff."
        ),
        (
            f"{ARCHIVE}\tmove_to_obsidian_after_pr\tdocs/agent/example.md\t"
            "pr_merge_or_close\tReview details belong in Obsidian after PR."
        ),
        (
            f"{removal}\tremove_after_merge_approval\tdocs/agent/example.md\t"
            "pr_merge_or_close\tTemporary manifest needs explicit removal approval."
        ),
    )

    result = _audit(
        tmp_path,
        due_event="pr_merge_or_close",
    )

    assert any(
        "tracked handoff state is forbidden" in m.message for m in result.blockers
    )
    assert result.summary["due_event"] == "pr_merge_or_close"
    assert result.summary["due_paths"] == [ARCHIVE, removal, CURRENT]
    assert result.summary["due_retention_decisions"] == {
        "active_current": 1,
        "move_to_obsidian_after_pr": 1,
        "remove_after_merge_approval": 1,
    }
    warnings = [msg.message for msg in result.messages]
    assert any(
        "branch current handoff needs closeout review" in msg for msg in warnings
    )
    assert any("move useful branch/review history" in msg for msg in warnings)
    assert any("tracked removal candidate" in msg for msg in warnings)


def test_current_handoff_with_archive_decision_is_blocker(tmp_path: Path) -> None:
    _write(tmp_path / CURRENT, "# Current\n\nBranch: `codex/example`\n")
    _inventory(
        tmp_path,
        (
            f"{CURRENT}\tkeep_repo_public_evidence\tdocs/agent/example.md\t"
            "manual_review\tWrong decision for current."
        ),
    )

    result = _audit(tmp_path)

    assert any(
        "current handoff must be active_current" in msg.message
        for msg in result.blockers
    )


def test_long_tracked_current_handoff_is_blocker_and_warning(tmp_path: Path) -> None:
    _write(
        tmp_path / CURRENT,
        "# Current\n" + "\n".join(f"line {index}" for index in range(205)),
    )
    _inventory(
        tmp_path,
        (
            f"{CURRENT}\tactive_current\tPR #1\tpr_merge_or_close\t"
            "Active branch handoff."
        ),
    )

    result = _audit(tmp_path)

    assert any(
        "tracked handoff state is forbidden" in m.message for m in result.blockers
    )
    assert any("target is <=" in msg.message for msg in result.messages)
