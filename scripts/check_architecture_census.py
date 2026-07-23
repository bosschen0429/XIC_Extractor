"""Build and validate a disposable architecture census.

The census contains only facts derived from the current checkout. Human
decisions stay in their existing owner documents; this module points to those
owners and does not copy product, lifecycle, or writer-authority claims.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_diagnostics_index import entry_point_files  # noqa: E402
from tools.testing.test_shards import shard_for_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOTS = ("xic_extractor", "gui", "scripts", "tools")
REMOVAL_GATE = "forbidden_import_absent"

ARCHITECTURE_OWNER = "docs/architecture-contract.md"
PUBLIC_CONTRACT_OWNER = "docs/agent/architecture-public-contracts.md"
LAYOUT_OWNER = "docs/project-layout.md"
DIAGNOSTICS_OWNER = "tools/diagnostics/INDEX.md"
SHARD_OWNER = "tools/testing/test_shards.py"

# Pointers only. The referenced files retain authority for their claims.
CENSUS_OWNER_REFS: Mapping[str, str] = {
    "python_modules": LAYOUT_OWNER,
    "internal_imports": ARCHITECTURE_OWNER,
    "project_entrypoints": PUBLIC_CONTRACT_OWNER,
    "package_patterns": LAYOUT_OWNER,
    "frozen_bundle_inputs": "xic_extractor.spec",
    "workflow_files": "docs/agent/execution-gates.md",
    "diagnostic_entrypoints": DIAGNOSTICS_OWNER,
    "test_shards": SHARD_OWNER,
}


@dataclass(frozen=True)
class ImportReference:
    source_path: str
    source_module: str
    imported_module: str
    line: int


@dataclass(frozen=True)
class ArchitectureCensus:
    python_modules: tuple[str, ...]
    internal_imports: tuple[ImportReference, ...]
    project_entrypoints: tuple[str, ...]
    package_patterns: tuple[str, ...]
    frozen_bundle_inputs: tuple[str, ...]
    workflow_files: tuple[str, ...]
    diagnostic_entrypoints: tuple[str, ...]
    test_shards: tuple[str, ...]

    def serializable(self) -> dict[str, list[str] | list[dict[str, Any]]]:
        return {
            "python_modules": list(self.python_modules),
            "internal_imports": [
                asdict(reference) for reference in self.internal_imports
            ],
            "project_entrypoints": list(self.project_entrypoints),
            "package_patterns": list(self.package_patterns),
            "frozen_bundle_inputs": list(self.frozen_bundle_inputs),
            "workflow_files": list(self.workflow_files),
            "diagnostic_entrypoints": list(self.diagnostic_entrypoints),
            "test_shards": list(self.test_shards),
        }


@dataclass(frozen=True)
class DependencyRule:
    rule_id: str
    source_prefix: str
    forbidden_prefixes: tuple[str, ...]
    owner_ref: str


@dataclass(frozen=True)
class DependencyException:
    rule_id: str
    source_path: str
    imported_prefix: str
    owner_ref: str
    rationale: str
    removal_gate: str = REMOVAL_GATE


# This is a coarse repository-wide floor. Existing lane-specific boundary tests
# remain responsible for narrower file- and adapter-level constraints.
DEPENDENCY_RULES = (
    DependencyRule(
        "domain_no_outer_layer",
        "xic_extractor",
        ("gui", "scripts", "tools"),
        ARCHITECTURE_OWNER,
    ),
    DependencyRule(
        "peak_detection_stays_inner",
        "xic_extractor.peak_detection",
        (
            "xic_extractor.alignment",
            "xic_extractor.diagnostics",
            "xic_extractor.discovery",
            "xic_extractor.extraction",
            "xic_extractor.output",
            "xic_extractor.raw_reader",
        ),
        ARCHITECTURE_OWNER,
    ),
    DependencyRule(
        "configuration_stays_inner",
        "xic_extractor.configuration",
        (
            "xic_extractor.alignment",
            "xic_extractor.diagnostics",
            "xic_extractor.discovery",
            "xic_extractor.extraction",
            "xic_extractor.output",
            "xic_extractor.raw_reader",
        ),
        ARCHITECTURE_OWNER,
    ),
    DependencyRule(
        "output_does_not_read_raw",
        "xic_extractor.output",
        ("xic_extractor.raw_reader",),
        ARCHITECTURE_OWNER,
    ),
)

# Exceptions are migration debt. The only supported removal gate is executable:
# once the forbidden import disappears, the exception is stale and fails.
DEPENDENCY_EXCEPTIONS: tuple[DependencyException, ...] = ()


def scan_imports_from_source(
    source: str, *, source_module: str, source_path: Path
) -> tuple[ImportReference, ...]:
    tree = ast.parse(source, filename=str(source_path))
    references: list[ImportReference] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_import_from_base(
                node,
                source_module=source_module,
                source_is_package=source_path.name == "__init__.py",
            )
            if base:
                modules.append(base)
                modules.extend(
                    f"{base}.{alias.name}" for alias in node.names if alias.name != "*"
                )
        elif isinstance(node, ast.Call):
            dynamic_module = _literal_dynamic_import(node)
            if dynamic_module:
                modules.append(dynamic_module)
        references.extend(
            ImportReference(
                source_path=source_path.as_posix(),
                source_module=source_module,
                imported_module=module,
                line=getattr(node, "lineno", 0),
            )
            for module in modules
        )
    return tuple(
        sorted(
            set(references),
            key=lambda ref: (ref.source_path, ref.line, ref.imported_module),
        )
    )


def _literal_dynamic_import(node: ast.Call) -> str:
    if not node.args:
        return ""
    first = node.args[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return ""
    direct = isinstance(node.func, ast.Name) and node.func.id in {
        "__import__",
        "import_module",
    }
    qualified = (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
        and node.func.attr == "import_module"
    )
    return first.value if direct or qualified else ""


def _resolve_import_from_base(
    node: ast.ImportFrom,
    *,
    source_module: str,
    source_is_package: bool,
) -> str:
    if node.level == 0:
        return node.module or ""
    package = source_module if source_is_package else source_module.rpartition(".")[0]
    parts = package.split(".") if package else []
    parents_to_remove = node.level - 1
    if parents_to_remove > len(parts):
        return ""
    base_parts = parts[: len(parts) - parents_to_remove]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


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
    internal_imports: list[ImportReference] = []
    for path in python_paths:
        if not path.parts or path.parts[0] not in REPOSITORY_ROOTS:
            continue
        source = (repo_root / path).read_text(encoding="utf-8-sig")
        internal_imports.extend(
            reference
            for reference in scan_imports_from_source(
                source,
                source_module=module_name_from_path(path),
                source_path=path,
            )
            if reference.imported_module.split(".", 1)[0] in REPOSITORY_ROOTS
        )

    entrypoints = project.get("project", {}).get("scripts", {})
    workflow_dir = repo_root / ".github" / "workflows"
    diagnostics_dir = repo_root / "tools" / "diagnostics"
    return ArchitectureCensus(
        python_modules=tuple(
            path.as_posix()
            for path in python_paths
            if path.parts and path.parts[0] in (*REPOSITORY_ROOTS, "tests")
        ),
        internal_imports=tuple(sorted(set(internal_imports), key=_import_sort_key)),
        project_entrypoints=tuple(
            f"{name}={target}" for name, target in sorted(entrypoints.items())
        ),
        package_patterns=tuple(
            f"{key}:{pattern}"
            for key in ("include", "exclude")
            for pattern in find_config.get(key, ())
        ),
        frozen_bundle_inputs=_pyinstaller_inputs(
            repo_root / CENSUS_OWNER_REFS["frozen_bundle_inputs"]
        ),
        workflow_files=tuple(
            path.relative_to(repo_root).as_posix()
            for path in sorted(
                (*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml"))
            )
        ),
        diagnostic_entrypoints=tuple(
            f"tools/diagnostics/{name}" for name in entry_point_files(diagnostics_dir)
        ),
        test_shards=tuple(
            f"{path.as_posix()} -> {shard_for_path(path) or 'unassigned'}"
            for path in python_paths
            if path.parts and path.parts[0] == "tests" and path.name.startswith("test_")
        ),
    )


def check_architecture_census(
    repo_root: Path = ROOT,
    *,
    census: ArchitectureCensus | None = None,
) -> list[str]:
    repo_root = repo_root.resolve()
    observed = census or build_architecture_census(repo_root)
    problems: list[str] = []
    tracked_paths = repository_tracked_paths(repo_root)
    for section, owner_ref in CENSUS_OWNER_REFS.items():
        if not getattr(observed, section):
            problems.append(f"architecture census section is empty: {section}")
        if owner_ref not in tracked_paths:
            problems.append(
                f"architecture census owner is not an exact tracked path: {owner_ref}"
            )
        owner = _repo_reference_path(repo_root, owner_ref)
        if owner is None or not owner.is_file():
            problems.append(f"architecture census owner does not resolve: {owner_ref}")
    if any(item.endswith(" -> unassigned") for item in observed.test_shards):
        problems.append("architecture census contains unassigned pytest modules")
    problems.extend(check_project_layout_package_snapshot(repo_root))
    problems.extend(check_dependency_rules(repo_root))
    problems.extend(_check_project_entrypoints(repo_root))
    return problems


def check_dependency_rules(
    repo_root: Path = ROOT,
    *,
    python_paths: Sequence[Path] | None = None,
    rules: Sequence[DependencyRule] = DEPENDENCY_RULES,
    exceptions: Sequence[DependencyException] = DEPENDENCY_EXCEPTIONS,
) -> list[str]:
    repo_root = repo_root.resolve()
    paths = (
        tuple(python_paths)
        if python_paths is not None
        else repository_python_paths(repo_root)
    )
    problems = validate_dependency_exceptions(exceptions, repo_root, rules=rules)
    used_exceptions: set[DependencyException] = set()
    violations: set[tuple[str, str, int, str]] = set()

    for path in paths:
        if not path.parts or path.parts[0] not in REPOSITORY_ROOTS:
            continue
        source_module = module_name_from_path(path)
        try:
            imports = scan_imports_from_source(
                (repo_root / path).read_text(encoding="utf-8-sig"),
                source_module=source_module,
                source_path=path,
            )
        except (OSError, SyntaxError, UnicodeError) as exc:
            problems.append(f"{path.as_posix()}: import scan failed: {exc}")
            continue
        for rule in rules:
            if not _matches_prefix(source_module, rule.source_prefix):
                continue
            for reference in imports:
                forbidden = next(
                    (
                        prefix
                        for prefix in rule.forbidden_prefixes
                        if _matches_prefix(reference.imported_module, prefix)
                    ),
                    None,
                )
                if forbidden is None:
                    continue
                exception = next(
                    (
                        candidate
                        for candidate in exceptions
                        if candidate.rule_id == rule.rule_id
                        and candidate.source_path == path.as_posix()
                        and _matches_prefix(
                            reference.imported_module, candidate.imported_prefix
                        )
                    ),
                    None,
                )
                if exception is not None:
                    used_exceptions.add(exception)
                    continue
                violations.add(
                    (rule.rule_id, path.as_posix(), reference.line, forbidden)
                )

    problems.extend(
        f"{rule_id}: {path}:{line} imports forbidden dependency {prefix}"
        for rule_id, path, line, prefix in sorted(violations)
    )
    problems.extend(
        "stale dependency exception: "
        f"{exception.rule_id} {exception.source_path} -> {exception.imported_prefix}"
        for exception in exceptions
        if exception not in used_exceptions
    )
    return problems


def validate_dependency_exceptions(
    exceptions: Sequence[DependencyException],
    repo_root: Path,
    *,
    rules: Sequence[DependencyRule] = DEPENDENCY_RULES,
) -> list[str]:
    rule_ids = {rule.rule_id for rule in rules}
    problems: list[str] = []
    for exception in exceptions:
        label = f"dependency exception {exception.rule_id}:{exception.source_path}"
        if exception.rule_id not in rule_ids:
            problems.append(f"{label}: unknown rule_id")
        for field in ("source_path", "imported_prefix", "owner_ref", "rationale"):
            if not getattr(exception, field).strip():
                problems.append(f"{label}: {field} is blank")
        if exception.removal_gate != REMOVAL_GATE:
            problems.append(
                f"{label}: removal_gate must be executable {REMOVAL_GATE!r}"
            )
        for field in ("owner_ref", "source_path"):
            value = getattr(exception, field)
            resolved = _repo_reference_path(repo_root, value)
            if value and (resolved is None or not resolved.is_file()):
                problems.append(f"{label}: {field} does not resolve")
    return problems


def check_project_layout_package_snapshot(repo_root: Path = ROOT) -> list[str]:
    project = _read_toml(repo_root / "pyproject.toml")
    actual = (
        project.get("tool", {})
        .get("setuptools", {})
        .get("packages", {})
        .get("find", {})
    )
    layout_path = repo_root / LAYOUT_OWNER
    try:
        layout = layout_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"project layout package snapshot cannot be read: {exc}"]
    blocks = re.findall(
        r"```toml\s*(\[tool\.setuptools\.packages\.find\].*?)```",
        layout,
        flags=re.DOTALL,
    )
    if not blocks:
        return ["docs/project-layout.md is missing packages.find snapshot"]
    try:
        documented = (
            tomllib.loads(blocks[0])
            .get("tool", {})
            .get("setuptools", {})
            .get("packages", {})
            .get("find", {})
        )
    except tomllib.TOMLDecodeError as exc:
        return [f"docs/project-layout.md packages.find snapshot is invalid: {exc}"]
    return [
        f"docs/project-layout.md packages.find {key} disagrees with pyproject.toml"
        for key in ("include", "exclude")
        if documented.get(key) != actual.get(key)
    ]


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
    return tuple(
        sorted(Path(line) for line in completed.stdout.splitlines() if line.strip())
    )


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


def _pyinstaller_inputs(spec_path: Path) -> tuple[str, ...]:
    tree = ast.parse(spec_path.read_text(encoding="utf-8"), filename=str(spec_path))
    discovered: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _called_name(node.func)
        if call_name not in {
            "collect_data_files",
            "Analysis",
            "PYZ",
            "EXE",
            "COLLECT",
        }:
            continue
        for descendant in ast.walk(node):
            if isinstance(descendant, ast.Constant) and isinstance(
                descendant.value, str
            ):
                discovered.add(f"{call_name}:{descendant.value}")
    return tuple(sorted(discovered))


def _called_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _check_project_entrypoints(repo_root: Path) -> list[str]:
    project = _read_toml(repo_root / "pyproject.toml")
    problems: list[str] = []
    for name, target in project.get("project", {}).get("scripts", {}).items():
        if not _entrypoint_declaration_resolves(repo_root, name, target):
            problems.append(f"project entrypoint does not resolve: {name}={target}")
    return problems


def _entrypoint_declaration_resolves(
    repo_root: Path,
    name: str,
    target: str,
) -> bool:
    entrypoint = EntryPoint(name=name, value=target, group="console_scripts")
    try:
        module_name = entrypoint.module
        attribute_path = entrypoint.attr
    except (AttributeError, ValueError):
        return False
    if not attribute_path:
        return False

    module_path = repo_root / Path(*module_name.split(".")).with_suffix(".py")
    package_path = repo_root / Path(*module_name.split(".")) / "__init__.py"
    path = module_path if module_path.is_file() else package_path
    if not path.is_file():
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return False
    root_attribute = attribute_path.split(".", 1)[0]
    return root_attribute in _module_exported_names(tree)


def _module_exported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_bound_names(target))
        elif isinstance(node, ast.AnnAssign):
            names.update(_bound_names(node.target))
    return names


def _bound_names(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return {name for element in node.elts for name in _bound_names(element)}
    return set()


def _repo_reference_path(repo_root: Path, value: str) -> Path | None:
    relative = Path(value)
    if not value or relative.is_absolute() or relative.drive or ".." in relative.parts:
        return None
    resolved = (repo_root / relative).resolve()
    return resolved if resolved.is_relative_to(repo_root.resolve()) else None


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _import_sort_key(reference: ImportReference) -> tuple[str, int, str]:
    return reference.source_path, reference.line, reference.imported_module


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _source_identity(repo_root: Path, census: ArchitectureCensus) -> dict[str, Any]:
    completed = subprocess.run(
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
        census.serializable(), sort_keys=True, separators=(",", ":")
    ).encode()
    return {
        "head": head,
        "worktree_dirty": bool(completed.stdout.strip()),
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
    path: Path, census: ArchitectureCensus, repo_root: Path = ROOT
) -> None:
    if not _output_is_allowed(path, repo_root):
        raise ValueError(
            "architecture census JSON must be outside the repo or git-ignored"
        )
    payload = {
        "schema_version": "architecture_census_v1",
        "claim": "derived_disposable_not_authority",
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
    problems = check_architecture_census(ROOT, census=census)
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
