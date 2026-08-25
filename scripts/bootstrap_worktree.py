"""Create or verify the complete development environment for this worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

SYNC_COMMAND = (
    "uv",
    "sync",
    "--locked",
    "--extra",
    "dev",
    "--group",
    "dev",
)
SYNC_CHECK_COMMAND = (*SYNC_COMMAND, "--check")
REQUIRED_DISTRIBUTIONS = (
    "import-linter",
    "mypy",
    "pytest",
    "ruff",
    "xic-extractor",
)
STAMP_NAME = ".xic-worktree-bootstrap.json"
LINKED_VENV_REASON = (
    "worktree .venv is a link or junction; remove only the link before bootstrap"
)
# Keep caller network/index credentials and offline policy, but do not let an
# inherited shell redirect the project/environment or weaken install semantics.
UV_SYNC_SEMANTIC_ENV_VARS = (
    "UV_CONFIG_FILE",
    "UV_DEV",
    "UV_NO_CACHE",
    "UV_NO_CONFIG",
    "UV_NO_DEFAULT_GROUPS",
    "UV_NO_DEV",
    "UV_NO_EDITABLE",
    "UV_NO_GROUP",
    "UV_NO_INSTALL_LOCAL",
    "UV_NO_INSTALL_PROJECT",
    "UV_NO_INSTALL_WORKSPACE",
    "UV_NO_PROJECT",
    "UV_NO_SOURCES",
    "UV_NO_SOURCES_PACKAGE",
    "UV_NO_SYNC",
    "UV_ONLY_GROUP",
    "UV_ONLY_INSTALL_LOCAL",
    "UV_ONLY_INSTALL_PROJECT",
    "UV_ONLY_INSTALL_WORKSPACE",
    "UV_PROJECT",
    "UV_PROJECT_ENVIRONMENT",
    "UV_WORKING_DIR",
)


@dataclass(frozen=True)
class BootstrapState:
    ready: bool
    reason: str
    contract_digest: str
    distributions: dict[str, str]

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "reason": self.reason,
            "contract_digest": self.contract_digest,
            "distributions": self.distributions,
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _contract_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for name in ("pyproject.toml", "uv.lock"):
        path = root / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _root_identity(root: Path) -> str:
    return os.path.normcase(str(root.resolve()))


def _venv_python(root: Path) -> Path:
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def _venv_is_link(root: Path) -> bool:
    venv = root / ".venv"
    is_junction = getattr(venv, "is_junction", None)
    return (
        venv.is_symlink()
        or bool(is_junction and is_junction())
        or os.path.normcase(os.path.realpath(venv))
        != os.path.normcase(os.path.abspath(venv))
    )


def _shared_cache_dir(root: Path) -> Path:
    dot_git = root / ".git"
    if dot_git.is_dir():
        return root / ".uv-cache"
    try:
        gitdir_line = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return root / ".uv-cache"
    prefix = "gitdir:"
    if not gitdir_line.lower().startswith(prefix):
        return root / ".uv-cache"
    gitdir = Path(gitdir_line[len(prefix) :].strip()).resolve()
    if gitdir.parent.name == "worktrees":
        return gitdir.parent.parent.parent / ".uv-cache"
    return root / ".uv-cache"


def _inspect_distributions(python: Path) -> dict[str, str]:
    probe = (
        "import importlib.metadata,json;"
        f"names={list(REQUIRED_DISTRIBUTIONS)!r};"
        "print(json.dumps({name:importlib.metadata.version(name) for name in names},"
        "sort_keys=True))"
    )
    completed = subprocess.run(
        [str(python), "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return {}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def _sync_environment(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    for name in UV_SYNC_SEMANTIC_ENV_VARS:
        env.pop(name, None)
    env["UV_CACHE_DIR"] = str(_shared_cache_dir(root))
    return env


def _environment_is_synchronized(root: Path) -> tuple[bool, str]:
    if shutil.which("uv") is None:
        return False, "uv is not available on PATH"
    completed = subprocess.run(
        SYNC_CHECK_COMMAND,
        cwd=root,
        env=_sync_environment(root),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return True, ""
    failure_lines = (completed.stdout + "\n" + completed.stderr).splitlines()
    detail = "\n".join(failure_lines[-20:]).strip()
    return False, detail or f"uv sync --check exited {completed.returncode}"


def inspect_state(root: Path) -> BootstrapState:
    contract_digest = _contract_digest(root)
    if _venv_is_link(root):
        return BootstrapState(
            ready=False,
            reason=LINKED_VENV_REASON,
            contract_digest=contract_digest,
            distributions={},
        )
    python = _venv_python(root)
    if not python.is_file():
        expected = python.relative_to(root)
        return BootstrapState(
            ready=False,
            reason=f"worktree interpreter is missing: {expected}",
            contract_digest=contract_digest,
            distributions={},
        )

    distributions = _inspect_distributions(python)
    missing = sorted(set(REQUIRED_DISTRIBUTIONS) - set(distributions))
    if missing:
        return BootstrapState(
            ready=False,
            reason=f"required distributions are missing: {', '.join(missing)}",
            contract_digest=contract_digest,
            distributions=distributions,
        )

    stamp_path = root / ".venv" / STAMP_NAME
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return BootstrapState(
            ready=False,
            reason="bootstrap stamp is missing or invalid",
            contract_digest=contract_digest,
            distributions=distributions,
        )
    if stamp.get("repo_root") != _root_identity(root):
        return BootstrapState(
            ready=False,
            reason="bootstrap stamp belongs to a different worktree",
            contract_digest=contract_digest,
            distributions=distributions,
        )
    if stamp.get("contract_digest") != contract_digest:
        return BootstrapState(
            ready=False,
            reason="pyproject.toml or uv.lock changed after bootstrap",
            contract_digest=contract_digest,
            distributions=distributions,
        )
    if stamp.get("distributions") != distributions:
        return BootstrapState(
            ready=False,
            reason="installed distribution versions changed after bootstrap",
            contract_digest=contract_digest,
            distributions=distributions,
        )
    synchronized, detail = _environment_is_synchronized(root)
    if not synchronized:
        return BootstrapState(
            ready=False,
            reason=(
                "locked development environment is not synchronized: "
                f"{detail}"
            ),
            contract_digest=contract_digest,
            distributions=distributions,
        )
    return BootstrapState(
        ready=True,
        reason="worktree development environment is ready",
        contract_digest=contract_digest,
        distributions=distributions,
    )


def _write_stamp(root: Path, state: BootstrapState) -> None:
    stamp_path = root / ".venv" / STAMP_NAME
    payload = {
        "repo_root": _root_identity(root),
        "contract_digest": state.contract_digest,
        "distributions": state.distributions,
    }
    stamp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def bootstrap(root: Path) -> BootstrapState:
    if _venv_is_link(root):
        raise RuntimeError(LINKED_VENV_REASON)
    current = inspect_state(root)
    if current.ready:
        return current
    if shutil.which("uv") is None:
        raise RuntimeError("uv is not available on PATH")

    completed = subprocess.run(
        SYNC_COMMAND,
        cwd=root,
        env=_sync_environment(root),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        failure_lines = (completed.stdout + "\n" + completed.stderr).splitlines()
        failure_tail = "\n".join(failure_lines[-20:])
        raise RuntimeError(
            "uv sync failed; classify sandbox, network, or dependency state "
            f"before retrying. Error tail:\n{failure_tail}"
        )

    python = _venv_python(root)
    distributions = _inspect_distributions(python)
    missing = sorted(set(REQUIRED_DISTRIBUTIONS) - set(distributions))
    if missing:
        raise RuntimeError(
            "uv sync completed but required distributions are missing: "
            + ", ".join(missing)
        )
    state = BootstrapState(
        ready=True,
        reason="worktree development environment is ready",
        contract_digest=_contract_digest(root),
        distributions=distributions,
    )
    _write_stamp(root, state)
    return state


def _local_env_state(root: Path, *, provision: bool) -> dict[str, object]:
    target = root / ".env.xic-local"

    def inspect(path: Path) -> dict[str, object]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeError:
            return {"ready": False, "reason": "local env is not valid UTF-8"}
        except OSError:
            return {"ready": False, "reason": "local env is missing"}
        roots = [
            line.removeprefix("XIC_REPO_ROOT=")
            for line in lines
            if line.startswith("XIC_REPO_ROOT=")
        ]
        if len(roots) != 1:
            return {
                "ready": False,
                "reason": "local env must contain exactly one repo root line",
            }
        configured = Path(roots[0])
        if not configured.is_absolute() or (
            _root_identity(configured) != _root_identity(root)
        ):
            return {
                "ready": False,
                "reason": "local env root does not match this worktree",
            }
        return {"ready": True, "reason": "local env is ready"}

    if target.exists() or target.is_symlink():
        return inspect(target)
    if not provision:
        return {"ready": False, "reason": "local env is missing"}

    source = _shared_cache_dir(root).parent / ".env.xic-local"
    try:
        text = source.read_bytes().decode("utf-8")
    except (OSError, UnicodeError):
        return {
            "ready": False,
            "reason": "main worktree local env is unavailable",
        }
    lines = text.splitlines(keepends=True)
    root_indexes = [
        index for index, line in enumerate(lines)
        if line.startswith("XIC_REPO_ROOT=")
    ]
    if len(root_indexes) != 1:
        return {
            "ready": False,
            "reason": "main worktree local env must contain exactly one repo root line",
        }
    index = root_indexes[0]
    ending = "\r\n" if lines[index].endswith("\r\n") else (
        "\n" if lines[index].endswith("\n") else ""
    )
    lines[index] = f"XIC_REPO_ROOT={root.resolve()}{ending}"
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=root,
            prefix=".env.xic-local.",
            delete=False,
        ) as temp:
            temp.write("".join(lines))
            temp_name = temp.name
        os.replace(temp_name, target)
    except OSError:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        return {"ready": False, "reason": "local env materialization failed"}
    return {"ready": True, "reason": "local env materialize completed"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the environment without installing or downloading anything",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a compact machine-readable result",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="also provision or verify the worktree-local env",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = _repo_root()
    try:
        state = inspect_state(root) if args.check else bootstrap(root)
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"ready": False, "reason": str(exc)}))
        else:
            print(f"Worktree bootstrap failed: {exc}", file=sys.stderr)
        return 1

    local = None
    if args.local:
        env_state = _local_env_state(root, provision=not args.check)
        local = {
            "ready": bool(state.ready and env_state["ready"]),
            "env_ready": env_state["ready"],
            "env_reason": env_state["reason"],
        }

    if args.json:
        payload = state.as_dict()
        if local is not None:
            payload["local"] = local
        print(json.dumps(payload, sort_keys=True))
    else:
        print(state.reason)
        if local is not None:
            print(f"Local env: {local['env_reason']}")
        if not state.ready and not _venv_is_link(root):
            print(
                "Run `uv run --cache-dir .uv-cache --no-project python "
                "scripts/bootstrap_worktree.py` from this worktree.",
                file=sys.stderr,
            )
    ready = state.ready if local is None else local["ready"]
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
