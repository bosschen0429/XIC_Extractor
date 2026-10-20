from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import MappingProxyType

import pytest

from tools.diagnostics.docs_scan import (
    RepositoryTextView,
    RepositoryViewError,
    _parse_batch_blobs,
    _parse_index_entries,
    load_repository_text_view,
    require_repository_text_view,
)


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="xic-docs-snapshot-") as directory:
        yield Path(directory)


def _git_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }


def _git(root: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=input_text,
        env=_git_env(),
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> None:
    root.mkdir()
    _git(root, "init")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_repository_view_reads_staged_content_not_later_worktree_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    path = root / "docs/example.md"
    _write(path, "staged A\n")
    _git(root, "add", "docs/example.md")
    _write(path, "worktree B\n")

    view = load_repository_text_view(root)

    assert view.paths == ("docs/example.md",)
    assert view.read_text("docs/example.md") == "staged A\n"
    assert view.read_text("docs/example.md") == "staged A\n"


def test_repository_view_keeps_staged_file_missing_from_worktree(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    path = root / "docs/superpowers/specs/rogue.json"
    _write(path, "{}\n")
    _git(root, "add", "docs/superpowers/specs/rogue.json")
    path.unlink()

    view = load_repository_text_view(root)

    assert view.contains("docs/superpowers/specs/rogue.json")
    assert view.read_text("docs/superpowers/specs/rogue.json") == "{}\n"


def test_repository_view_ignores_staged_deletion_with_retained_worktree_copy(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    path = root / "docs/deleted.md"
    _write(path, "retained worktree copy\n")
    _git(root, "add", "docs/deleted.md")
    _git(root, "rm", "--cached", "--force", "docs/deleted.md")

    view = load_repository_text_view(root)

    assert not view.contains("docs/deleted.md")
    assert view.paths == ()


def test_repository_view_ignores_untracked_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _write(root / "docs/untracked.md", "untracked\n")

    view = load_repository_text_view(root)

    assert not view.contains("docs/untracked.md")
    assert view.paths == ()


def test_repository_view_rejects_nested_git_root_without_fixture_fallback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    nested = root / "nested"
    nested.mkdir()

    with pytest.raises(RepositoryViewError, match="must equal the Git worktree root"):
        load_repository_text_view(nested)
    with pytest.raises(RepositoryViewError, match="must equal the Git worktree root"):
        load_repository_text_view(nested, allow_filesystem_fallback=True)


def test_repository_view_uses_explicit_isolated_filesystem_fallback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fixture"
    _write(root / "docs/example.md", "fixture\n")

    view = load_repository_text_view(root, allow_filesystem_fallback=True)

    assert view.source == "filesystem_fallback"
    assert view.read_text("docs/example.md") == "fixture\n"


def test_repository_view_rejects_non_utf8_index_text(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    path = root / "docs/bad.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe")
    _git(root, "add", "docs/bad.md")

    with pytest.raises(RepositoryViewError, match="not UTF-8"):
        load_repository_text_view(root)


def test_index_parser_rejects_malformed_and_duplicate_records() -> None:
    oid = b"a" * 40
    with pytest.raises(RepositoryViewError, match="malformed"):
        _parse_index_entries(b"not-an-index-record\0")
    with pytest.raises(RepositoryViewError, match="malformed"):
        _parse_index_entries(b"100644 " + oid + b" 0\tdocs/not-terminated.md")
    with pytest.raises(RepositoryViewError, match="non-zero stage"):
        _parse_index_entries(b"100644 " + oid + b" 2\tdocs/conflict.md\0")
    with pytest.raises(RepositoryViewError, match="duplicate normalized path"):
        _parse_index_entries(
            b"100644 " + oid + b" 0\tdocs/a.md\x00"
            b"100644 " + oid + b" 0\tdocs/./a.md\0"
        )


def test_batch_parser_rejects_malformed_and_negative_byte_counts() -> None:
    oid = "a" * 40
    with pytest.raises(RepositoryViewError, match="malformed"):
        _parse_batch_blobs(b"not-a-complete-header", (oid,))
    with pytest.raises(RepositoryViewError, match="byte count"):
        _parse_batch_blobs(f"{oid} blob -1\n\n".encode(), (oid,))


def test_repository_view_rejects_caller_fabrication(tmp_path: Path) -> None:
    fabricated = RepositoryTextView(
        root=tmp_path.resolve(),
        source="git_index",
        entries=MappingProxyType({}),
        texts=MappingProxyType({}),
    )

    with pytest.raises(RepositoryViewError, match="owner-created"):
        require_repository_text_view(tmp_path, fabricated)


def test_repository_view_uses_bounded_git_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _write(root / "docs/a.md", "A\n")
    _write(root / "docs/b.md", "B\n")
    _git(root, "add", "docs/a.md", "docs/b.md")
    real_run = subprocess.run
    calls: list[tuple[str, ...]] = []

    def counted_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        command = args[0]
        if isinstance(command, list) and command and command[0] == "git":
            calls.append(tuple(str(item) for item in command))
        return real_run(*args, **kwargs)

    monkeypatch.setattr("tools.diagnostics.docs_scan.subprocess.run", counted_run)

    view = load_repository_text_view(root)

    assert view.read_text("docs/a.md") == "A\n"
    assert view.read_text("docs/b.md") == "B\n"
    assert len(calls) == 3
