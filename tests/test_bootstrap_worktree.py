import json
import subprocess
from pathlib import Path

from scripts import bootstrap_worktree


def _write_contract(root: Path, *, lock_text: str = "lock") -> None:
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (root / "uv.lock").write_text(lock_text, encoding="utf-8")


def test_inspect_state_fails_fast_when_worktree_venv_is_missing(
    tmp_path: Path,
) -> None:
    _write_contract(tmp_path)

    state = bootstrap_worktree.inspect_state(tmp_path)

    assert state.ready is False
    assert state.reason == "worktree .venv is missing"


def test_inspect_state_requires_current_contract_stamp(
    tmp_path: Path, monkeypatch
) -> None:
    _write_contract(tmp_path)
    python = tmp_path / ".venv" / "bin" / "python"
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
    python = worktree / ".venv" / "bin" / "python"
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
    captured_env: dict[str, str] = {}

    def fake_run(*_args, **kwargs):
        captured_env.update(kwargs["env"])
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap_worktree.subprocess, "run", fake_run)

    state = bootstrap_worktree.bootstrap(worktree)

    assert state.ready is True
    assert captured_env["UV_CACHE_DIR"] == str(main_repo / ".uv-cache")
    assert "VIRTUAL_ENV" not in captured_env
