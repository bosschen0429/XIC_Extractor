"""Build and validate a disposable checkout architecture census.

The census records facts derived from tracked or unignored Python source and
``pyproject.toml``. Grimp supplies the static module graph. Import Linter owns
the separate blocking dependency contracts in ``pyproject.toml``
``[tool.importlinter]``.

Neither surface interprets dynamic loaders or proves installed-package,
console-wrapper, or frozen-application behavior.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
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
REPOSITORY_ROOTS = (
    "xic_extractor",
    "scripts",
    "tools",
    "xic_inspector",
    "xic_workbench",
)
STATIC_DEPENDENCY_SCOPE = "grimp_static_import_graph"
_GIT_LOCAL_ENVIRONMENT_VARIABLES = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)

ARCHITECTURE_OWNER = "docs/architecture-contract.md"
LAYOUT_OWNER = "docs/project-layout.md"

# Historical immutable provenance may be exempted only with a path, value, and
# reason. Current contracts have no exemptions.
HISTORICAL_IDENTIFIER_ALLOWLIST: Mapping[tuple[str, str], str] = {}
VERSIONED_IDENTIFIER = re.compile(
    r"(?<![a-z0-9_])([a-z][a-z0-9_]*(?:[_.]v[0-9]+|_by_[0-9]+))(?![a-z0-9_])"
)

# Pointers only. The referenced files retain authority for their claims.
CENSUS_OWNER_REFS: Mapping[str, str] = {
    "python_modules": LAYOUT_OWNER,
    "internal_static_imports": ARCHITECTURE_OWNER,
    "project_entrypoint_declarations": ARCHITECTURE_OWNER,
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
    """Validate census integrity and executable architecture ownership gates."""
    problems: list[str] = []
    tracked_paths = repository_tracked_path_keys(repo_root)
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
    problems.extend(validate_semantic_identifiers(repo_root))
    problems.extend(validate_documented_code_names(repo_root))
    problems.extend(validate_ppm_arithmetic(repo_root))
    return problems


PPM_ARITHMETIC_OWNER = "xic_extractor/mass_accuracy.py"
_PPM_SCALES = (1e6, 1e-6)


def validate_ppm_arithmetic(
    repo_root: Path,
    paths: Sequence[Path] | None = None,
) -> list[str]:
    """Reject ppm conversions written outside the mass-accuracy owner.

    Multiplying or dividing by 1e6 or 1e-6 is a ppm conversion, except as the
    direct argument of ``round()``, which quantizes m/z into an integer key.
    """
    if paths is None:
        paths = tuple(
            path
            for path in repository_python_paths(repo_root)
            if path.parts[0] in REPOSITORY_ROOTS
        )
    problems: list[str] = []
    for relative_path in paths:
        path_key = relative_path.as_posix()
        if path_key == PPM_ARITHMETIC_OWNER:
            continue
        source = (repo_root / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source, path_key)
        rounded = {
            id(argument)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "round"
            for argument in node.args[:1]
        }
        for node in ast.walk(tree):
            operands: tuple[ast.expr, ...]
            if isinstance(node, ast.BinOp):
                operands = (node.left, node.right)
            elif isinstance(node, ast.AugAssign):
                operands = (node.value,)
            else:
                continue
            if (
                isinstance(node.op, (ast.Mult, ast.Div))
                and id(node) not in rounded
                and any(_is_ppm_scale(operand) for operand in operands)
            ):
                problems.append(
                    f"{path_key}:{node.lineno}: ppm arithmetic outside "
                    f"{PPM_ARITHMETIC_OWNER}; call its functions"
                )
    return problems


def _is_ppm_scale(node: ast.expr) -> bool:
    if not isinstance(node, ast.Constant) or type(node.value) not in (int, float):
        return False
    assert isinstance(node.value, (int, float))
    return abs(node.value) in _PPM_SCALES


DOCUMENTED_CODE_NAME = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")
DOCUMENTED_CODE_NAME_DOCS = ("docs/product/", "docs/user/")
CODE_NAME_SOURCE_SUFFIXES = frozenset(
    {".py", ".js", ".html", ".css", ".toml", ".json", ".yml", ".yaml", ".csv", ".tsv"}
)


def validate_documented_code_names(
    repo_root: Path,
    doc_paths: Sequence[Path] | None = None,
    source_paths: Sequence[Path] | None = None,
) -> list[str]:
    """Reject backticked snake_case names in product docs that no source defines."""
    tracked = (
        repository_tracked_paths(repo_root)
        if doc_paths is None or source_paths is None
        else ()
    )
    if doc_paths is None:
        doc_paths = tuple(
            path
            for path in tracked
            if path.suffix == ".md"
            and path.as_posix().startswith(DOCUMENTED_CODE_NAME_DOCS)
        )
    if source_paths is None:
        source_paths = tuple(
            path for path in tracked if path.suffix in CODE_NAME_SOURCE_SUFFIXES
        )
    known: set[str] = set()
    for path in source_paths:
        try:
            text = (repo_root / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        known.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
    problems: list[str] = []
    for path in doc_paths:
        source = (repo_root / path).read_text(encoding="utf-8")
        for line_number, line in enumerate(source.splitlines(), start=1):
            for name in DOCUMENTED_CODE_NAME.findall(line):
                if name not in known:
                    problems.append(
                        f"{path.as_posix()}:{line_number}: documented name {name} "
                        "is not defined in any tracked source"
                    )
    return problems


def validate_semantic_identifiers(
    repo_root: Path,
    paths: Sequence[Path] | None = None,
) -> list[str]:
    """Reject historical version and ticket labels used as current identifiers."""
    scan_repository = paths is None
    if paths is None:
        paths = tuple(
            path
            for path in repository_tracked_paths(repo_root)
            if path.suffix == ".py"
            or (
                path.suffix == ".md"
                and (path.parts[0] == "docs" or len(path.parts) == 1)
            )
        )
    problems: list[str] = []
    observed_exemptions: set[tuple[str, str]] = set()
    for (path_key, value), reason in HISTORICAL_IDENTIFIER_ALLOWLIST.items():
        if not reason.strip():
            problems.append(
                f"{path_key}: {value}: historical identifier exemption needs a reason"
            )
    for relative_path in paths:
        path_key = relative_path.as_posix()
        try:
            source = (repo_root / relative_path).read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{path_key}: identifier scan failed: {exc}")
            continue
        if relative_path.suffix == ".py":
            try:
                tree = ast.parse(source, path_key)
            except SyntaxError as exc:
                problems.append(f"{path_key}: identifier AST scan failed: {exc}")
                continue
            exemptions = {
                child
                for node in tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name)
                    and target.id == "HISTORICAL_IDENTIFIER_ALLOWLIST"
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else (node.target,)
                    )
                )
                for child in ast.walk(node)
            }
            candidates: Iterator[tuple[int, str]] = (
                (node.lineno, node.value)
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node not in exemptions
            )
        else:
            candidates = enumerate(source.splitlines(), start=1)
        for line, value in candidates:
            for match in VERSIONED_IDENTIFIER.finditer(value):
                identifier = match.group(1)
                if (path_key, identifier) in HISTORICAL_IDENTIFIER_ALLOWLIST:
                    observed_exemptions.add((path_key, identifier))
                else:
                    problems.append(
                        f"{path_key}:{line}: versioned identifier {identifier}"
                    )
    if scan_repository:
        for path_key, identifier in (
            HISTORICAL_IDENTIFIER_ALLOWLIST.keys() - observed_exemptions
        ):
            problems.append(
                f"{path_key}: unused historical identifier exemption {identifier}"
            )
    return sorted(set(problems))


def repository_tracked_paths(repo_root: Path = ROOT) -> tuple[Path, ...]:
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
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_git_subprocess_environment(),
    )
    candidates = sorted(
        Path(line) for line in completed.stdout.splitlines() if line.strip()
    )
    return tuple(path for path in candidates if (repo_root / path).is_file())


def repository_python_paths(repo_root: Path = ROOT) -> tuple[Path, ...]:
    return tuple(
        path for path in repository_tracked_paths(repo_root) if path.suffix == ".py"
    )


def repository_tracked_path_keys(repo_root: Path = ROOT) -> frozenset[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--cached"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_git_subprocess_environment(),
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
        encoding="utf-8",
        env=_git_subprocess_environment(),
    )
    head = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_git_subprocess_environment(),
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
        env=_git_subprocess_environment(),
    )
    return completed.returncode == 0


def _git_subprocess_environment() -> dict[str, str]:
    """Drop caller-repository bindings before running Git against an exact root."""

    environment = dict(os.environ)
    for name in tuple(environment):
        if (
            name in _GIT_LOCAL_ENVIRONMENT_VARIABLES
            or name.startswith("GIT_CONFIG_KEY_")
            or name.startswith("GIT_CONFIG_VALUE_")
        ):
            environment.pop(name)
    return environment


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
        "schema_version": "architecture_census",
        "claim": "derived_disposable_not_authority",
        "static_dependency_scope": STATIC_DEPENDENCY_SCOPE,
        "static_dependency_gate": "pyproject.toml [tool.importlinter]",
        "source_identity": _source_identity(repo_root, census),
        "owners": dict(CENSUS_OWNER_REFS),
        "census": census.serializable(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# Construction-time hooks where this repository grew self-validation and sealing.
CONSTRUCTION_HOOKS = frozenset({"__post_init__", "__init_subclass__"})


@dataclass
class _DefenseScope:
    path: str
    name: str
    fingerprint: str
    line: int
    hook_statements: int


def _internal_defense_profile(source: str, path: str) -> list[_DefenseScope]:
    """Count statements in construction hooks; not a semantic trust analysis."""
    result: list[_DefenseScope] = []

    def scan(scope: Any, name: str) -> None:
        nodes: list[ast.AST] = []
        children: list[Any] = []
        body: list[ast.stmt] = []

        def collect(node: ast.AST) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                children.append(node)
                return
            nodes.append(node)
            for child in ast.iter_child_nodes(node):
                collect(child)

        for statement in scope.body:
            if (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                continue
            body.append(statement)
            collect(statement)
        if getattr(scope, "name", None) in CONSTRUCTION_HOOKS:
            result.append(
                _DefenseScope(
                    path=path,
                    name=name,
                    fingerprint=ast.dump(scope.args)
                    + "".join(ast.dump(n) for n in body),
                    line=scope.lineno,
                    hook_statements=sum(isinstance(n, ast.stmt) for n in nodes),
                )
            )
        for child in children:
            scan(child, child.name if name == "<module>" else name + "." + child.name)

    scan(ast.parse(source), "<module>")
    return result


def _match_defense_scopes(
    previous: list[_DefenseScope], current: list[_DefenseScope]
) -> dict[int, _DefenseScope]:
    """Match path and name, name across moves, then exact-body renames, not copies."""
    remaining = list(previous)
    matched: dict[int, _DefenseScope] = {}
    for key in (lambda s: (s.path, s.name), lambda s: s.name):
        for index, scope in enumerate(current):
            for old in remaining if index not in matched else ():
                if key(old) == key(scope):
                    matched[index] = old
                    remaining.remove(old)
                    break
    for index, scope in enumerate(current):
        if index in matched:
            continue
        candidates = [old for old in remaining if old.fingerprint == scope.fingerprint]
        peers = [
            other
            for i, other in enumerate(current)
            if i not in matched and other.fingerprint == scope.fingerprint
        ]
        if len(candidates) == len(peers) == 1:
            matched[index] = candidates[0]
            remaining.remove(candidates[0])
    return matched


def validate_internal_defense_growth(
    repo_root: Path,
    base: str,
    *,
    staged: bool = False,
) -> list[str]:
    """Reject growth relative to Git; unchanged legacy is not an allowlist."""

    def git(*args: str) -> bytes:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), *args],
            env=_git_subprocess_environment(),
            stderr=subprocess.PIPE,
        )

    try:
        commit = git("rev-parse", "--verify", base + "^{commit}").decode().strip()
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"internal-defense baseline is unavailable: {base}") from exc
    flags = ("--cached",) if staged else ()
    changes = iter(
        git(
            "diff",
            *flags,
            "-M",
            "--name-status",
            "-z",
            "--diff-filter=ADMRT",
            commit,
            "--",
            "xic_extractor",
        )
        .decode()
        .split("\0")[:-1]
    )
    previous: list[_DefenseScope] = []
    current: list[_DefenseScope] = []
    for status in changes:
        original = next(changes)
        relative = next(changes) if status.startswith("R") else original
        if not relative.endswith(".py"):
            continue
        path = (repo_root / relative).resolve()
        if not path.is_relative_to(repo_root.resolve()):
            raise ValueError("internal-defense source escapes repository")
        if status != "A":
            before = git("show", commit + ":" + original).decode("utf-8-sig")
            previous += _internal_defense_profile(before, relative)
        if status != "D":
            after = git("show", ":" + relative) if staged else path.read_bytes()
            current += _internal_defense_profile(after.decode("utf-8-sig"), relative)
    matched = _match_defense_scopes(previous, current)
    problems = []
    for index, scope in enumerate(current):
        old = matched[index].hook_statements if index in matched else 0
        if scope.hook_statements > old:
            problems.append(
                f"{scope.path}:{scope.line}: internal-defense construction-hook "
                f"growth in {scope.name} ({old} -> {scope.hook_statements} "
                "statements); remove unnecessary machinery or seek "
                "a maintainer decision on the gate scope"
            )
    return problems


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--internal-defense-base",
        metavar="REF",
        help="Reject internal-defense growth against this Git commit.",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Inspect index source instead of working files (requires base).",
    )
    modes.add_argument(
        "--json-output",
        type=Path,
        help=(
            "Optional disposable JSON output outside the repo or under an ignored path."
        ),
    )
    args = parser.parse_args(argv)
    if args.staged and args.internal_defense_base is None:
        parser.error("--staged requires --internal-defense-base")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.internal_defense_base is not None:
        try:
            problems = validate_internal_defense_growth(
                ROOT,
                args.internal_defense_base,
                staged=args.staged,
            )
        except (OSError, subprocess.CalledProcessError, SyntaxError, ValueError) as exc:
            problems = [str(exc)]
        problems.extend(validate_ppm_arithmetic(ROOT))
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            print("Internal-defense growth and ppm arithmetic gates passed.")
        return int(bool(problems))
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
