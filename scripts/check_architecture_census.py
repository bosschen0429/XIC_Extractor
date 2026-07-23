"""Build and validate a disposable checkout architecture census.

The census records facts derived from tracked or unignored Python source and
``pyproject.toml``. Grimp supplies the static module graph. Import Linter owns
the separate blocking dependency contracts in ``.importlinter``.

Neither surface interprets dynamic loaders or proves installed-package,
console-wrapper, or frozen-application behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import grimp

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOTS = ("xic_extractor", "gui", "scripts", "tools")
STATIC_DEPENDENCY_SCOPE = "grimp_static_import_graph"

ARCHITECTURE_OWNER = "docs/architecture-contract.md"
PUBLIC_CONTRACT_OWNER = "docs/agent/architecture-public-contracts.md"
LAYOUT_OWNER = "docs/project-layout.md"

# Pointers only. The referenced files retain authority for their claims.
CENSUS_OWNER_REFS: Mapping[str, str] = {
    "python_modules": LAYOUT_OWNER,
    "internal_static_imports": ARCHITECTURE_OWNER,
    "project_entrypoint_declarations": PUBLIC_CONTRACT_OWNER,
    "package_discovery_patterns": LAYOUT_OWNER,
}


@dataclass(frozen=True)
class StaticImportReference:
    source_path: str
    source_module: str
    imported_module: str
    line: int


@dataclass(frozen=True)
class ArchitectureCensus:
    python_modules: tuple[str, ...]
    internal_static_imports: tuple[StaticImportReference, ...]
    project_entrypoint_declarations: tuple[str, ...]
    package_discovery_patterns: tuple[str, ...]

    def serializable(self) -> dict[str, list[str] | list[dict[str, Any]]]:
        return {
            "python_modules": list(self.python_modules),
            "internal_static_imports": [
                asdict(reference) for reference in self.internal_static_imports
            ],
            "project_entrypoint_declarations": list(
                self.project_entrypoint_declarations
            ),
            "package_discovery_patterns": list(self.package_discovery_patterns),
        }


def build_architecture_census(repo_root: Path = ROOT) -> ArchitectureCensus:
    repo_root = repo_root.resolve()
    python_paths = repository_python_paths(repo_root)
    project = _read_toml(repo_root / "pyproject.toml")
    find_config = (
        project.get("tool", {})
        .get("setuptools", {})
        .get("packages", {})
        .get("find", {})
    )
    entrypoints = project.get("project", {}).get("scripts", {})
    return ArchitectureCensus(
        python_modules=tuple(
            path.as_posix()
            for path in python_paths
            if path.parts and path.parts[0] in (*REPOSITORY_ROOTS, "tests")
        ),
        internal_static_imports=_build_internal_static_imports(
            repo_root,
            python_paths,
        ),
        project_entrypoint_declarations=tuple(
            f"{name}={target}" for name, target in sorted(entrypoints.items())
        ),
        package_discovery_patterns=tuple(
            f"{key}:{pattern}"
            for key in ("include", "exclude")
            for pattern in find_config.get(key, ())
        ),
    )


def validate_architecture_census(
    repo_root: Path,
    census: ArchitectureCensus,
) -> list[str]:
    """Validate observation integrity without rechecking owner semantics."""
    problems: list[str] = []
    tracked_paths = repository_tracked_paths(repo_root)
    for section, owner_ref in CENSUS_OWNER_REFS.items():
        if not getattr(census, section):
            problems.append(f"architecture census section is empty: {section}")
        if owner_ref not in tracked_paths:
            problems.append(
                f"architecture census owner is not an exact tracked path: {owner_ref}"
            )
        owner = _repo_reference_path(repo_root, owner_ref)
        if owner is None or not owner.is_file():
            problems.append(f"architecture census owner does not resolve: {owner_ref}")
    return problems


def repository_python_paths(repo_root: Path = ROOT) -> tuple[Path, ...]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "*.py",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    candidates = sorted(
        Path(line) for line in completed.stdout.splitlines() if line.strip()
    )
    return tuple(path for path in candidates if (repo_root / path).is_file())


def repository_tracked_paths(repo_root: Path = ROOT) -> frozenset[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--cached"],
        check=True,
        capture_output=True,
        text=True,
    )
    return frozenset(line for line in completed.stdout.splitlines() if line)


def module_name_from_path(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _build_internal_static_imports(
    repo_root: Path,
    python_paths: Sequence[Path],
) -> tuple[StaticImportReference, ...]:
    module_paths = {
        module_name_from_path(path): path
        for path in python_paths
        if path.parts and path.parts[0] in REPOSITORY_ROOTS
    }
    with _prepend_sys_path(repo_root):
        graph = grimp.build_graph(
            *REPOSITORY_ROOTS,
            include_external_packages=False,
            cache_dir=None,
            exclude_type_checking_imports=False,
        )
    references: list[StaticImportReference] = []
    allowed_modules = frozenset(module_paths)
    for source_module in sorted(graph.modules & allowed_modules):
        source_path = module_paths[source_module]
        for imported_module in sorted(
            graph.find_modules_directly_imported_by(source_module)
        ):
            if imported_module not in allowed_modules:
                continue
            details = graph.get_import_details(
                importer=source_module,
                imported=imported_module,
            )
            references.extend(
                StaticImportReference(
                    source_path=source_path.as_posix(),
                    source_module=source_module,
                    imported_module=imported_module,
                    line=int(detail["line_number"]),
                )
                for detail in details
            )
    return tuple(sorted(set(references), key=_import_sort_key))


@contextmanager
def _prepend_sys_path(path: Path) -> Iterator[None]:
    value = str(path.resolve())
    sys.path.insert(0, value)
    try:
        yield
    finally:
        sys.path.remove(value)


def _repo_reference_path(repo_root: Path, value: str) -> Path | None:
    relative = Path(value)
    if not value or relative.is_absolute() or relative.drive or ".." in relative.parts:
        return None
    resolved = (repo_root / relative).resolve()
    return resolved if resolved.is_relative_to(repo_root.resolve()) else None


def _import_sort_key(
    reference: StaticImportReference,
) -> tuple[str, int, str]:
    return reference.source_path, reference.line, reference.imported_module


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _source_identity(
    repo_root: Path,
    census: ArchitectureCensus,
) -> dict[str, Any]:
    status = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    encoded = json.dumps(
        census.serializable(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "head": head,
        "worktree_dirty": bool(status.stdout.strip()),
        "census_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _output_is_allowed(path: Path, repo_root: Path) -> bool:
    resolved = path.resolve()
    root = repo_root.resolve()
    if not resolved.is_relative_to(root):
        return True
    relative = resolved.relative_to(root)
    completed = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--quiet", "--", str(relative)],
        check=False,
    )
    return completed.returncode == 0


def write_architecture_census_json(
    path: Path,
    census: ArchitectureCensus,
    repo_root: Path = ROOT,
) -> None:
    if not _output_is_allowed(path, repo_root):
        raise ValueError(
            "architecture census JSON must be outside the repo or git-ignored"
        )
    payload = {
        "schema_version": "architecture_census_v2",
        "claim": "derived_disposable_not_authority",
        "static_dependency_scope": STATIC_DEPENDENCY_SCOPE,
        "static_dependency_gate": ".importlinter",
        "source_identity": _source_identity(repo_root, census),
        "owners": dict(CENSUS_OWNER_REFS),
        "census": census.serializable(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json-output",
        type=Path,
        help=(
            "Optional disposable JSON output outside the repo or under an ignored path."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    census = build_architecture_census(ROOT)
    problems = validate_architecture_census(ROOT, census)
    if args.json_output is not None:
        try:
            write_architecture_census_json(args.json_output, census, ROOT)
        except ValueError as exc:
            problems.append(str(exc))
    if problems:
        print("Architecture census failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    counts = {key: len(value) for key, value in census.serializable().items()}
    summary = ", ".join(f"{key}={count}" for key, count in counts.items())
    print(f"Architecture census valid ({summary}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
