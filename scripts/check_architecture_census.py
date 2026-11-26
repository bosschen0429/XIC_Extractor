"""Repository-wide source and documentation gates.

Import Linter owns static dependency contracts in ``pyproject.toml``
``[tool.importlinter]``; this script owns the checks it cannot express.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOTS = (
    "xic_extractor",
    "scripts",
    "tools",
    "xic_inspector",
    "xic_workbench",
)
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

HISTORICAL_IDENTIFIER_ALLOWLIST: Mapping[tuple[str, str], str] = {}
VERSIONED_IDENTIFIER = re.compile(
    r"(?<![a-z0-9_])([a-z][a-z0-9_]*(?:[_.]v[0-9]+|_by_[0-9]+))(?![a-z0-9_])"
)


def validate_repository(repo_root: Path) -> list[str]:
    """Run every repository-wide gate against the current checkout."""
    problems: list[str] = []
    problems.extend(validate_semantic_identifiers(repo_root))
    problems.extend(validate_documented_code_names(repo_root))
    problems.extend(validate_ppm_arithmetic(repo_root))
    problems.extend(validate_typed_input_guards(repo_root))
    problems.extend(validate_test_source_boundaries(repo_root))
    problems.extend(validate_frozen_entrypoints(repo_root))
    return problems


def validate_test_source_boundaries(
    repo_root: Path,
    paths: Sequence[Path] | None = None,
) -> list[str]:
    """Reject permissive production patches and tests reading production Python.

    Follow local imports, assignments, loop paths and helper arguments/returns.
    This is a static syntax gate, not execution or a general Python resolver.
    Temporary fixture paths and tests' own files are not production source.
    """
    if paths is None:
        paths = repository_python_paths(repo_root)
    problems = []
    for path in paths:
        if path.parts[0] != "tests":
            continue
        tree = ast.parse((repo_root / path).read_text(encoding="utf-8-sig"))
        problems.extend(_test_source_boundary_problems(tree, path))
    return problems


def _test_source_boundary_problems(tree: ast.Module, path: Path) -> list[str]:
    problems: list[str] = []
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }

    def scope(node: ast.AST) -> ast.AST:
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return node
        return tree

    bindings: dict[tuple[ast.AST, str], list[ast.AST | str | None]] = {}

    def bind(owner: ast.AST, name: str, value: ast.AST | str | None) -> None:
        bindings.setdefault((owner, name), []).append(value)

    def lookup(node: ast.AST, name: str) -> list[ast.AST | str | None]:
        owner = scope(node)
        while True:
            if (owner, name) in bindings:
                return bindings[owner, name]
            if owner is tree:
                return []
            owner = scope(owner)

    for node in ast.walk(tree):
        owner = scope(node)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                module = (
                    alias.name
                    if isinstance(node, ast.Import)
                    else f"{node.module}.{alias.name}"
                )
                bind(
                    owner,
                    alias.asname
                    or (
                        alias.name.split(".")[0]
                        if isinstance(node, ast.Import)
                        else alias.name
                    ),
                    module,
                )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bind(owner, target.id, node.value)
        elif isinstance(node, (ast.For, ast.comprehension)) and isinstance(
            node.target, ast.Name
        ):
            bind(owner, node.target.id, node.iter)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bind(owner, node.name, node)
            for arg in node.args.args:
                bind(
                    node,
                    arg.arg,
                    "temporary"
                    if arg.arg in {"tmp_path", "tmp_path_factory"}
                    else None,
                )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            for helper in lookup(node, node.func.id):
                if isinstance(helper, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for arg, value in zip(helper.args.args, node.args, strict=False):
                        bind(helper, arg.arg, value)

    def origins(
        node: ast.AST | str | None, seen: frozenset[ast.AST] = frozenset()
    ) -> set[str]:
        if node is None or node in seen:
            return set()
        if isinstance(node, str):
            return (
                {"production"}
                if node.split(".")[0] in REPOSITORY_ROOTS
                else ({"temporary"} if node == "temporary" else set())
            )
        seen = seen | {node}
        if isinstance(node, ast.Attribute) and node.attr in sys.stdlib_module_names:
            return set()
        values: list[ast.AST | str | None]
        if isinstance(node, ast.Name):
            values = lookup(node, node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            parts = re.split(r"[\\/.]", node.value)
            return (
                {"production"}
                if any(part in REPOSITORY_ROOTS for part in parts)
                else set()
            ) | ({"python"} if node.value.endswith(".py") else set())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            values = [
                child.value
                for child in ast.walk(node)
                if isinstance(child, ast.Return) and scope(child) is node
            ]
        elif isinstance(node, ast.Call):
            values = [node.func]
            if isinstance(node.func, ast.Attribute) or (
                isinstance(node.func, ast.Name)
                and (
                    node.func.id in {"Path", "str", "type"}
                    or "pathlib.Path" in lookup(node, node.func.id)
                )
            ):
                values.extend(node.args)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "repository_python_paths"
            ):
                return {"production", "python"} | set().union(
                    *(origins(argument, seen) for argument in node.args)
                )
        else:
            values = list(ast.iter_child_nodes(node))
        result = set().union(*(origins(value, seen) for value in values))
        if isinstance(node, ast.Attribute) and node.attr == "__file__":
            result.add("python")
        return result

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else node.func.id
            if isinstance(node.func, ast.Name)
            else ""
        )
        if isinstance(node.func, ast.Name):
            imported = lookup(node, node.func.id)
            if len(imported) == 1 and isinstance(imported[0], str):
                called = imported[0].rsplit(".", 1)[-1]
        if (
            called == "setattr"
            and node.args
            and any(
                k.arg == "raising"
                and isinstance(k.value, ast.Constant)
                and k.value.value is False
                for k in node.keywords
            )
        ):
            if "production" in origins(node.args[0]):
                problems.append(
                    f"{path.as_posix()}:{node.lineno}: permissive-production-patch: "
                    "remove raising=False"
                )
        source = None
        if called in {"read_text", "read_bytes", "open"} and isinstance(
            node.func, ast.Attribute
        ):
            source = node.func.value
        elif called in {"getsource", "getsourcelines", "open"} and node.args:
            source = node.args[0]
        evidence = origins(source)
        if (
            "production" in evidence
            and "temporary" not in evidence
            and (called in {"getsource", "getsourcelines"} or "python" in evidence)
        ):
            problems.append(
                f"{path.as_posix()}:{node.lineno}: production-source-read: "
                "assert behavior or use the architecture gate"
            )
    return problems


def validate_frozen_entrypoints(repo_root: Path) -> list[str]:
    """Require frozen-application entry points to call freeze_support first."""
    problems = []
    for path in (Path("scripts/run_extraction.py"), Path("xic_workbench/cli.py")):
        tree = ast.parse((repo_root / path).read_text(encoding="utf-8-sig"))
        main = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main"
            ),
            None,
        )
        if (
            main is None
            or ast.unparse(main.body[0]) != "multiprocessing.freeze_support()"
        ):
            problems.append(f"{path.as_posix()}: main must call freeze_support first")
    return problems


TARGETED_REASON_PRODUCERS = (
    "xic_extractor/peak_detection/evidence_semantics.py",
    "xic_extractor/peak_detection/selection_decision.py",
    "xic_extractor/extraction/targeted_product_projection.py",
    "xic_extractor/extraction/targeted_batch_selection.py",
    "xic_extractor/applications/targeted/production_workflow.py",
    "xic_extractor/output/csv_writers.py",
)
TARGETED_CELL_DECISION = "xic_extractor/alignment/cell_decision/owner.py"
_REASON_CODE_SHAPE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+:?$")


def targeted_reason_code_literals(repo_root: Path) -> frozenset[str]:
    """Code-shaped string literals in the Targeted reason producers."""
    found: set[str] = set()
    for path_key in TARGETED_REASON_PRODUCERS:
        tree = ast.parse((repo_root / path_key).read_text(encoding="utf-8"))
        found.update(
            node.value.rstrip(":")
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _REASON_CODE_SHAPE.match(node.value)
        )
    return frozenset(found)


def targeted_no_write_variants(repo_root: Path) -> frozenset[str]:
    """No-write variants a Targeted cell decision can return.

    The Targeted publication saves each one as ``finalized_<variant>``.
    """
    tree = ast.parse((repo_root / TARGETED_CELL_DECISION).read_text(encoding="utf-8"))
    target_paths = {"_decide_target", "_payload_from_integration"}
    return frozenset(
        str(call.args[0].value)
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef) and function.name in target_paths
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "NoWriteCellDecision"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    )


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
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and abs(node.value) in _PPM_SCALES
    )


def validate_typed_input_guards(
    repo_root: Path,
    paths: Sequence[Path] | None = None,
) -> list[str]:
    """Reject directly raising negative type guards covered by input annotations."""
    if paths is None:
        paths = repository_python_paths(repo_root)
    problems = []
    for path in paths:
        if path.parts[0] != "xic_extractor":
            continue
        tree = ast.parse((repo_root / path).read_text(encoding="utf-8-sig"))
        for line, value in sorted(_typed_input_guards(tree)):
            problems.append(
                f"{path.as_posix()}:{line}: redundant type guard for {value}; "
                "the input annotation is already covered by the runtime types"
            )
    return problems


def _typed_input_guards(tree: ast.Module) -> Iterator[tuple[int, str]]:
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not any(
            isinstance(statement, ast.Raise) for statement in node.body
        ):
            continue
        owner = parents[node]
        while owner is not tree and not isinstance(owner, scopes):
            owner = parents[owner]
        if not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        positional = owner.args.posonlyargs + owner.args.args
        annotations = {
            arg.arg: arg.annotation for arg in positional + owner.args.kwonlyargs
        }
        cls = parents[owner]
        if (
            isinstance(cls, ast.ClassDef)
            and positional
            and not any(
                ast.unparse(decorator) in {"classmethod", "staticmethod"}
                for decorator in owner.decorator_list
            )
            and any(
                ast.unparse(
                    decorator.func if isinstance(decorator, ast.Call) else decorator
                )
                in {"dataclass", "dataclasses.dataclass"}
                for decorator in cls.decorator_list
            )
        ):
            receiver = positional[0].arg
            annotations.update(
                {
                    f"{receiver}.{field.target.id}": field.annotation
                    for field in cls.body
                    if isinstance(field, ast.AnnAssign)
                    and isinstance(field.target, ast.Name)
                }
            )
        for test in _guard_terms(node.test):
            guard = _negative_type_guard(test)
            if guard is None:
                continue
            target, runtime = guard
            value = ast.unparse(target)
            annotation = annotations.get(value)
            if annotation is None:
                continue
            declared = _declared_guard_types(annotation)
            if not declared & {"object", "Any", "typing.Any"} and declared <= runtime:
                yield test.lineno, value


def _guard_terms(test: ast.expr) -> Iterator[ast.expr]:
    if isinstance(test, ast.BoolOp):
        for term in test.values:
            yield from _guard_terms(term)
    else:
        yield test


def _negative_type_guard(test: ast.expr) -> tuple[ast.expr, set[str]] | None:
    match test:
        case ast.UnaryOp(
            op=ast.Not(),
            operand=ast.Call(func=ast.Name(id="isinstance"), args=[target, types]),
        ):
            return target, _runtime_guard_types(types)
        case ast.Compare(
            left=ast.Call(func=ast.Name(id="type"), args=[target]),
            ops=[ast.IsNot()],
            comparators=[kind],
        ):
            return target, {ast.unparse(kind)}
        case _:
            return None


def _runtime_guard_types(types: ast.expr) -> set[str]:
    if isinstance(types, ast.Tuple):
        return set().union(*map(_runtime_guard_types, types.elts))
    if isinstance(types, ast.BinOp) and isinstance(types.op, ast.BitOr):
        return _runtime_guard_types(types.left) | _runtime_guard_types(types.right)
    name = ast.unparse(types)
    return {"None"} if name in {"type(None)", "NoneType", "types.NoneType"} else {name}


def _declared_guard_types(annotation: ast.expr) -> set[str]:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return {ast.unparse(annotation)}
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _declared_guard_types(annotation.left) | _declared_guard_types(
            annotation.right
        )
    if isinstance(annotation, ast.Subscript):
        kind = ast.unparse(annotation.value)
        if kind in {"Optional", "typing.Optional"}:
            return _declared_guard_types(annotation.slice) | {"None"}
        if kind in {"Union", "typing.Union"} and isinstance(
            annotation.slice, ast.Tuple
        ):
            return set().union(*map(_declared_guard_types, annotation.slice.elts))
        return {kind}
    return {ast.unparse(annotation)}


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
    parser.add_argument(
        "--internal-defense-base",
        metavar="REF",
        help="Reject internal-defense growth against this Git commit.",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Inspect index source instead of working files (requires base).",
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
        problems.extend(validate_typed_input_guards(ROOT))
        problems.extend(validate_test_source_boundaries(ROOT))
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            print("Internal-defense, typed-input, ppm and test-source gates passed.")
        return int(bool(problems))
    problems = validate_repository(ROOT)
    if problems:
        print("Repository gates failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("Repository gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
