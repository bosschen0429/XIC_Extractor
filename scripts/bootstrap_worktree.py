"""Create or verify the complete development environment for this worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
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
REQUIRED_DISTRIBUTIONS = (
    "import-linter",
    "mypy",
    "pytest",
    "ruff",
    "xic-extractor",
)
STAMP_NAME = ".xic-worktree-bootstrap.json"


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


def _venv_python(root: Path) -> Path:
    windows_python = root / ".venv" / "Scripts" / "python.exe"
    if windows_python.is_file():
        return windows_python
    return root / ".venv" / "bin" / "python"


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


def inspect_state(root: Path) -> BootstrapState:
    contract_digest = _contract_digest(root)
    python = _venv_python(root)
    if not python.is_file():
        return BootstrapState(
            ready=False,
            reason="worktree .venv is missing",
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
    return BootstrapState(
        ready=True,
        reason="worktree development environment is ready",
        contract_digest=contract_digest,
        distributions=distributions,
    )


def _write_stamp(root: Path, state: BootstrapState) -> None:
    stamp_path = root / ".venv" / STAMP_NAME
    payload = {
        "contract_digest": state.contract_digest,
        "distributions": state.distributions,
    }
    stamp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def bootstrap(root: Path) -> BootstrapState:
    current = inspect_state(root)
    if current.ready:
        return current
    if shutil.which("uv") is None:
        raise RuntimeError("uv is not available on PATH")

    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.setdefault("UV_CACHE_DIR", str(_shared_cache_dir(root)))
    completed = subprocess.run(
        SYNC_COMMAND,
        cwd=root,
        env=env,
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

    if args.json:
        print(json.dumps(state.as_dict(), sort_keys=True))
    else:
        print(state.reason)
        if not state.ready:
            print(
                "Run `uv run --cache-dir .uv-cache --no-project python "
                "scripts/bootstrap_worktree.py` from this worktree.",
                file=sys.stderr,
            )
    return 0 if state.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
