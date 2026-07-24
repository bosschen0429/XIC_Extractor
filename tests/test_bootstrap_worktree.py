import json
import subprocess
from pathlib import Path

import pytest

from scripts import bootstrap_worktree


def _write_contract(root: Path, *, lock_text: str = "lock") -> None:
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (root / "uv.lock").write_text(lock_text, encoding="utf-8")


def test_inspect_state_fails_fast_when_worktree_interpreter_is_missing(
    tmp_path: Path,
) -> None:
    _write_contract(tmp_path)

    state = bootstrap_worktree.inspect_state(tmp_path)

    assert state.ready is False
    assert state.reason == (
        f"worktree interpreter is missing: "
        f"{bootstrap_worktree._venv_python(tmp_path).relative_to(tmp_path)}"
    )


def test_venv_python_uses_current_platform_layout(
    tmp_path: Path, monkeypatch
) -> None:
    windows_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    posix_python = tmp_path / ".venv" / "bin" / "python"
    windows_python.parent.mkdir(parents=True)
    posix_python.parent.mkdir(parents=True)
    windows_python.touch()
    posix_python.touch()

    monkeypatch.setattr(bootstrap_worktree.os, "name", "nt")
    assert bootstrap_worktree._venv_python(tmp_path) == windows_python

    monkeypatch.setattr(bootstrap_worktree.os, "name", "posix")
    assert bootstrap_worktree._venv_python(tmp_path) == posix_python


def test_check_rejects_interpreter_from_other_platform_only(
    tmp_path: Path, monkeypatch
) -> None:
    _write_contract(tmp_path)
    windows_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    windows_python.parent.mkdir(parents=True)
    windows_python.touch()
    monkeypatch.setattr(bootstrap_worktree.os, "name", "posix")
    monkeypatch.setattr(bootstrap_worktree, "_repo_root", lambda: tmp_path)

    assert bootstrap_worktree.main(["--check"]) == 1


def test_linked_worktree_venv_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    _write_contract(tmp_path)
    monkeypatch.setattr(bootstrap_worktree, "_venv_is_link", lambda _root: True)

    state = bootstrap_worktree.inspect_state(tmp_path)

    assert state.ready is False
    assert state.reason == bootstrap_worktree.LINKED_VENV_REASON
    with pytest.raises(RuntimeError, match="link or junction"):
        bootstrap_worktree.bootstrap(tmp_path)


def test_inspect_state_requires_current_contract_stamp(
    tmp_path: Path, monkeypatch
) -> None:
    _write_contract(tmp_path)
    python = bootstrap_worktree._venv_python(tmp_path)
    python.parent.mkdir(parents=True)
    python.touch()
    versions = {
        name: "1.0" for name in bootstrap_worktree.REQUIRED_DISTRIBUTIONS
    }
    monkeypatch.setattr(
        bootstrap_worktree,
        "_inspect_distributions",
        lambda _python: versions,
    )
    monkeypatch.setattr(
        bootstrap_worktree,
        "_environment_is_synchronized",
        lambda _root: (True, ""),
    )
    stamp = python.parent.parent / bootstrap_worktree.STAMP_NAME
    stamp.write_text(
        json.dumps(
            {
                "repo_root": bootstrap_worktree._root_identity(tmp_path),
                "contract_digest": bootstrap_worktree._contract_digest(tmp_path),
                "distributions": versions,
            }
        ),
        encoding="utf-8",
    )

    assert bootstrap_worktree.inspect_state(tmp_path).ready is True

    _write_contract(tmp_path, lock_text="changed")

    state = bootstrap_worktree.inspect_state(tmp_path)
    assert state.ready is False
    assert state.reason == "pyproject.toml or uv.lock changed after bootstrap"


def test_inspect_state_rejects_stamp_from_another_worktree(
    tmp_path: Path, monkeypatch
) -> None:
    worktree = tmp_path / "current"
    sibling = tmp_path / "sibling"
    worktree.mkdir()
    sibling.mkdir()
    _write_contract(worktree)
    python = bootstrap_worktree._venv_python(worktree)
    python.parent.mkdir(parents=True)
    python.touch()
    versions = {
        name: "1.0" for name in bootstrap_worktree.REQUIRED_DISTRIBUTIONS
    }
    monkeypatch.setattr(
        bootstrap_worktree,
        "_inspect_distributions",
        lambda _python: versions,
    )
    stamp = python.parent.parent / bootstrap_worktree.STAMP_NAME
    stamp.write_text(
        json.dumps(
            {
                "repo_root": bootstrap_worktree._root_identity(sibling),
                "contract_digest": bootstrap_worktree._contract_digest(worktree),
                "distributions": versions,
            }
        ),
        encoding="utf-8",
    )

    state = bootstrap_worktree.inspect_state(worktree)

    assert state.ready is False
    assert state.reason == "bootstrap stamp belongs to a different worktree"


def test_inspect_state_requires_the_complete_locked_environment(
    tmp_path: Path, monkeypatch
) -> None:
    _write_contract(tmp_path)
    python = bootstrap_worktree._venv_python(tmp_path)
    python.parent.mkdir(parents=True)
    python.touch()
    versions = {
        name: "1.0" for name in bootstrap_worktree.REQUIRED_DISTRIBUTIONS
    }
    monkeypatch.setattr(
        bootstrap_worktree,
        "_inspect_distributions",
        lambda _python: versions,
    )
    monkeypatch.setattr(
        bootstrap_worktree,
        "_environment_is_synchronized",
        lambda _root: (False, "PyQt6 is missing"),
    )
    stamp = python.parent.parent / bootstrap_worktree.STAMP_NAME
    stamp.write_text(
        json.dumps(
            {
                "repo_root": bootstrap_worktree._root_identity(tmp_path),
                "contract_digest": bootstrap_worktree._contract_digest(tmp_path),
                "distributions": versions,
            }
        ),
        encoding="utf-8",
    )

    state = bootstrap_worktree.inspect_state(tmp_path)

    assert state.ready is False
    assert state.reason == (
        "locked development environment is not synchronized: PyQt6 is missing"
    )


def test_bootstrap_sync_command_is_locked_and_installs_both_dev_surfaces() -> None:
    assert bootstrap_worktree.SYNC_COMMAND == (
        "uv",
        "sync",
        "--locked",
        "--extra",
        "dev",
        "--group",
        "dev",
    )
    assert bootstrap_worktree.SYNC_CHECK_COMMAND == (
        *bootstrap_worktree.SYNC_COMMAND,
        "--check",
    )


def test_locked_environment_check_uses_sanitized_worktree_context(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(bootstrap_worktree.shutil, "which", lambda _name: "uv")
    monkeypatch.setenv("VIRTUAL_ENV", "other-worktree")
    monkeypatch.setenv("UV_PROJECT", "other-project")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr="environment is not up-to-date",
        )

    monkeypatch.setattr(bootstrap_worktree.subprocess, "run", fake_run)

    synchronized, detail = bootstrap_worktree._environment_is_synchronized(
        tmp_path
    )

    assert synchronized is False
    assert detail == "environment is not up-to-date"
    assert captured["command"] == bootstrap_worktree.SYNC_CHECK_COMMAND
    assert captured["cwd"] == tmp_path
    env = captured["env"]
    assert isinstance(env, dict)
    assert "VIRTUAL_ENV" not in env
    assert "UV_PROJECT" not in env


def test_linked_worktree_reuses_main_repo_uv_cache(tmp_path: Path) -> None:
    main_repo = tmp_path / "repo"
    worktree = main_repo / ".worktrees" / "feature"
    gitdir = main_repo / ".git" / "worktrees" / "feature"
    gitdir.mkdir(parents=True)
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")

    assert bootstrap_worktree._shared_cache_dir(worktree) == (
        main_repo / ".uv-cache"
    )


def test_bootstrap_overrides_inherited_environment_for_linked_worktree(
    tmp_path: Path, monkeypatch
) -> None:
    main_repo = tmp_path / "repo"
    worktree = main_repo / ".worktrees" / "feature"
    gitdir = main_repo / ".git" / "worktrees" / "feature"
    gitdir.mkdir(parents=True)
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    _write_contract(worktree)
    python = bootstrap_worktree._venv_python(worktree)
    python.parent.mkdir(parents=True)
    python.touch()
    versions = {
        name: "1.0" for name in bootstrap_worktree.REQUIRED_DISTRIBUTIONS
    }
    monkeypatch.setattr(
        bootstrap_worktree,
        "_inspect_distributions",
        lambda _python: versions,
    )
    monkeypatch.setattr(bootstrap_worktree.shutil, "which", lambda _name: "uv")
    monkeypatch.setenv("UV_CACHE_DIR", "inherited-cache")
    monkeypatch.setenv("VIRTUAL_ENV", "inherited-venv")
    for name in bootstrap_worktree.UV_SYNC_SEMANTIC_ENV_VARS:
        monkeypatch.setenv(name, "inherited-value")
    captured_env: dict[str, str] = {}

    def fake_run(*_args, **kwargs):
        captured_env.update(kwargs["env"])
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap_worktree.subprocess, "run", fake_run)

    state = bootstrap_worktree.bootstrap(worktree)

    assert state.ready is True
    assert captured_env["UV_CACHE_DIR"] == str(main_repo / ".uv-cache")
    assert "VIRTUAL_ENV" not in captured_env
    assert not (
        set(bootstrap_worktree.UV_SYNC_SEMANTIC_ENV_VARS) & set(captured_env)
    )
