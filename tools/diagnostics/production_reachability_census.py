"""Report product-package symbols that no production code refers to.

A symbol defined under ``xic_extractor/`` counts as reachable when its name is
loaded anywhere in the production roots outside its own definition, or when a
``[project.scripts]`` entry point names it. References are matched by name, so
an unrelated symbol with the same name hides a dead one, and names reached only
through ``getattr`` or string tables are reported as unreachable. The report
is evidence for triage; it never fails.
"""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.diagnostics.diagnostic_io import write_tsv

ROOT = Path(__file__).resolve().parents[2]
DEFINITION_ROOT = "xic_extractor"
PRODUCTION_ROOTS = ("xic_extractor", "gui", "scripts", "xic_inspector")
REPORT_COLUMNS = ("kind", "qualified_name", "path", "line")


@dataclass(frozen=True, slots=True)
class SymbolDefinition:
    kind: str
    name: str
    qualified_name: str
    path: str
    line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class _NameLoad:
    name: str
    path: str
    line: int


def find_unreachable_symbols(repo_root: Path = ROOT) -> tuple[SymbolDefinition, ...]:
    definitions: list[SymbolDefinition] = []
    loads: list[_NameLoad] = []
    for root_name in PRODUCTION_ROOTS:
        for path in sorted((repo_root / root_name).rglob("*.py")):
            relative = path.relative_to(repo_root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            loads.extend(_name_loads(tree, relative))
            if root_name == DEFINITION_ROOT:
                definitions.extend(_definitions(tree, relative))
    entry_point_names = _entry_point_names(repo_root / "pyproject.toml")
    loads_by_name: dict[str, list[_NameLoad]] = {}
    for load in loads:
        loads_by_name.setdefault(load.name, []).append(load)
    return tuple(
        definition
        for definition in definitions
        if not _is_dunder(definition.name)
        and definition.name not in entry_point_names
        and not any(
            not _inside(load, definition)
            for load in loads_by_name.get(definition.name, ())
        )
    )


def _definitions(tree: ast.Module, path: str) -> Iterable[SymbolDefinition]:
    module = path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            yield _definition("class", node, f"{module}.{node.name}", path)
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield _definition(
                        "method", member, f"{module}.{node.name}.{member.name}", path
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield _definition("function", node, f"{module}.{node.name}", path)


def _definition(
    kind: str,
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    qualified_name: str,
    path: str,
) -> SymbolDefinition:
    first_line = min(
        [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
    )
    return SymbolDefinition(
        kind=kind,
        name=node.name,
        qualified_name=qualified_name,
        path=path,
        line=first_line,
        end_line=node.end_lineno or node.lineno,
    )


def _name_loads(tree: ast.Module, path: str) -> Iterable[_NameLoad]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            yield _NameLoad(node.id, path, node.lineno)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            yield _NameLoad(node.attr, path, node.lineno)


def _inside(load: _NameLoad, definition: SymbolDefinition) -> bool:
    return (
        load.path == definition.path
        and definition.line <= load.line <= definition.end_line
    )


def _entry_point_names(pyproject: Path) -> frozenset[str]:
    scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    targets = scripts.get("project", {}).get("scripts", {}).values()
    return frozenset(target.rpartition(":")[2] for target in targets)


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report xic_extractor symbols with no reference from production "
            "code. Report-only: always exits 0 on a readable tree."
        ),
    )
    parser.add_argument(
        "--output-tsv",
        type=Path,
        help="Write the report here instead of standard output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    unreachable = find_unreachable_symbols()
    rows = [
        {
            "kind": symbol.kind,
            "qualified_name": symbol.qualified_name,
            "path": symbol.path,
            "line": symbol.line,
        }
        for symbol in unreachable
    ]
    if args.output_tsv is None:
        print("\t".join(REPORT_COLUMNS))
        for row in rows:
            print("\t".join(str(row[column]) for column in REPORT_COLUMNS))
    else:
        args.output_tsv.parent.mkdir(parents=True, exist_ok=True)
        write_tsv(args.output_tsv, rows, REPORT_COLUMNS)
    counts = Counter(symbol.kind for symbol in unreachable)
    summary = ", ".join(f"{kind}={counts[kind]}" for kind in sorted(counts))
    print(f"unreachable symbols: {len(unreachable)} ({summary})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
