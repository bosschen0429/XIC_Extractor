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


def _ready_state() -> bootstrap_worktree.BootstrapState:
    return bootstrap_worktree.BootstrapState(
        ready=True,
        reason="worktree development environment is ready",
        contract_digest="digest",
        distributions={"pytest": "1.0"},
    )


def test_default_cli_path_does_not_inspect_local_resources(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(bootstrap_worktree, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(bootstrap_worktree, "bootstrap", lambda _root: _ready_state())

    def reject_local(*_args, **_kwargs):
        raise AssertionError("default bootstrap must not inspect local resources")

    monkeypatch.setattr(bootstrap_worktree, "_local_env_state", reject_local)
    monkeypatch.setattr(bootstrap_worktree, "_codegraph_state", reject_local)

    assert bootstrap_worktree.main([]) == 0
    assert capsys.readouterr().out == (
        "worktree development environment is ready\n"
    )


def test_local_bootstrap_materializes_env_with_rebound_worktree_root(
    tmp_path: Path, monkeypatch
) -> None:
    main_root = tmp_path / "main"
    worktree = tmp_path / "feature"
    main_root.mkdir()
    worktree.mkdir()
    source = main_root / ".env.xic-local"
    source.write_text(
        "XIC_RAW_ROOT=D:\\RAW\nXIC_REPO_ROOT=C:\\old-root\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bootstrap_worktree,
        "_shared_cache_dir",
        lambda _root: main_root / ".uv-cache",
    )

    state = bootstrap_worktree._local_env_state(worktree, provision=True)

    assert state["ready"] is True
    assert (worktree / ".env.xic-local").read_text(encoding="utf-8") == (
        f"XIC_RAW_ROOT=D:\\RAW\nXIC_REPO_ROOT={worktree.resolve()}\n"
    )
    assert source.read_text(encoding="utf-8") == (
        "XIC_RAW_ROOT=D:\\RAW\nXIC_REPO_ROOT=C:\\old-root\n"
    )


def test_local_bootstrap_rejects_existing_mismatched_env_without_writing(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".env.xic-local"
    original = b"XIC_REPO_ROOT=C:\\wrong-root\r\nXIC_RAW_ROOT=D:\\RAW\r\n"
    target.write_bytes(original)

    state = bootstrap_worktree._local_env_state(tmp_path, provision=True)

    assert state == {
        "ready": False,
        "reason": "local env root does not match this worktree",
    }
    assert target.read_bytes() == original


def _codegraph_status(root: Path, **overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "initialized": True,
        "projectPath": str(root.resolve()),
        "pendingChanges": {"total": 0},
        "worktreeMismatch": None,
        "index": {"reindexRecommended": False},
    }
    payload.update(overrides)
    return payload


def test_codegraph_resolves_windows_cmd_shim(monkeypatch) -> None:
    requested: list[str] = []
    monkeypatch.setattr(bootstrap_worktree.os, "name", "nt")
    monkeypatch.setattr(
        bootstrap_worktree.shutil,
        "which",
        lambda command: requested.append(command) or "resolved-codegraph.cmd",
    )

    assert bootstrap_worktree._codegraph_executable() == "resolved-codegraph.cmd"
    assert requested == ["codegraph.cmd"]


def test_local_bootstrap_initializes_absent_codegraph(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[tuple[str, ...]] = []
    statuses = [
        subprocess.CompletedProcess(
            [],
            0,
            json.dumps(_codegraph_status(tmp_path, initialized=False)),
            "",
        ),
        subprocess.CompletedProcess(
            [], 0, json.dumps(_codegraph_status(tmp_path)), ""
        ),
    ]

    def fake_run(command, **_kwargs):
        commands.append(tuple(command))
        if command[1] == "status":
            return statuses.pop(0)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(bootstrap_worktree.subprocess, "run", fake_run)

    state = bootstrap_worktree._codegraph_state(tmp_path, provision=True)

    assert state["ready"] is True
    assert [command[1] for command in commands] == ["status", "init", "status"]


def test_local_bootstrap_syncs_pending_codegraph(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[tuple[str, ...]] = []
    statuses = [
        _codegraph_status(tmp_path, pendingChanges={"total": 2}),
        _codegraph_status(tmp_path),
    ]

    def fake_run(command, **_kwargs):
        commands.append(tuple(command))
        if command[1] == "status":
            return subprocess.CompletedProcess(
                command, 0, json.dumps(statuses.pop(0)), ""
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(bootstrap_worktree.subprocess, "run", fake_run)

    state = bootstrap_worktree._codegraph_state(tmp_path, provision=True)

    assert state["ready"] is True
    assert [command[1] for command in commands] == ["status", "sync", "status"]
    assert "--quiet" in commands[1]


def test_check_local_only_runs_codegraph_status(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[tuple[str, ...]] = []

    def fake_run(command, **_kwargs):
        commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 1, "", "not initialized")

    monkeypatch.setattr(bootstrap_worktree.subprocess, "run", fake_run)

    state = bootstrap_worktree._codegraph_state(tmp_path, provision=False)

    assert state["ready"] is False
    assert [command[1] for command in commands] == ["status"]
