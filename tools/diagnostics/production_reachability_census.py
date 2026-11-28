"""Report product-package symbols that no production code refers to.

A symbol defined under ``xic_extractor/`` counts as reachable when its name is
loaded anywhere in the production roots outside its own definition, or when a
``[project.scripts]`` entry point names it. Maintained tools under ``tools/``
also reach ``xic_extractor/diagnostics/``, which holds their reusable logic. A
load of an import alias (``from m import name as alias``) counts as a load of
``name``. References are otherwise matched by name, so an unrelated symbol with
the same name hides a dead one, and names reached only through ``getattr`` or
string tables are reported as unreachable.

Without ``--base`` the report is evidence for triage and never fails. With
``--base REF`` it fails when the tree (or, with ``--staged``, the index) has an
unreachable symbol that REF does not: a change that leaves a symbol without its
last caller removes the symbol in the same change. Symbols already unreachable
at REF never fail.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.tabular_io import write_tsv

ROOT = Path(__file__).resolve().parents[2]
DEFINITION_ROOT = "xic_extractor"
PRODUCTION_ROOTS = ("xic_extractor", "xic_workbench", "scripts", "xic_inspector")
TOOL_ROOT = "tools"
TOOL_REACHED_PACKAGE = "xic_extractor/diagnostics/"
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
    sources = {
        path.relative_to(repo_root).as_posix(): path.read_text(encoding="utf-8")
        for root_name in (*PRODUCTION_ROOTS, TOOL_ROOT)
        for path in sorted((repo_root / root_name).rglob("*.py"))
    }
    return unreachable_symbols(
        sources, (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    )


def unreachable_symbols(
    sources: Mapping[str, str], pyproject: str
) -> tuple[SymbolDefinition, ...]:
    """``sources`` maps repository-relative POSIX paths to Python source."""
    definitions: list[SymbolDefinition] = []
    production_loads: dict[str, list[_NameLoad]] = {}
    tool_loads: dict[str, list[_NameLoad]] = {}
    for relative in sorted(sources):
        root_name = relative.partition("/")[0]
        if root_name not in (*PRODUCTION_ROOTS, TOOL_ROOT):
            continue
        tree = ast.parse(sources[relative], filename=relative)
        loads = tool_loads if root_name == TOOL_ROOT else production_loads
        for load in _name_loads(tree, relative):
            loads.setdefault(load.name, []).append(load)
        if root_name == DEFINITION_ROOT:
            definitions.extend(_definitions(tree, relative))
    entry_point_names = _entry_point_names(pyproject)

    def reached(definition: SymbolDefinition) -> bool:
        candidates = list(production_loads.get(definition.name, ()))
        if definition.path.startswith(TOOL_REACHED_PACKAGE):
            candidates += tool_loads.get(definition.name, ())
        return any(not _inside(load, definition) for load in candidates)

    return tuple(
        definition
        for definition in definitions
        if not _is_dunder(definition.name)
        and definition.name not in entry_point_names
        and not reached(definition)
    )


def git_sources(repo_root: Path, revision: str | None) -> tuple[dict[str, str], str]:
    """Sources and pyproject at ``revision``, or in the index when it is None."""

    def git(*args: str, stdin: bytes | None = None) -> bytes:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            input=stdin,
            capture_output=True,
            check=True,
        ).stdout

    roots = (*PRODUCTION_ROOTS, TOOL_ROOT)
    if revision is None:
        listed = git("ls-files", "-z", "--", *roots)
        prefix = ":"
    else:
        commit = git("rev-parse", "--verify", revision + "^{commit}").decode().strip()
        listed = git("ls-tree", "-r", "-z", "--name-only", commit, "--", *roots)
        prefix = commit + ":"
    paths = [path for path in listed.decode().split("\0") if path.endswith(".py")]
    objects = [prefix + path for path in (*paths, "pyproject.toml")]
    batch = git("cat-file", "--batch", stdin="\n".join(objects).encode() + b"\n")
    texts: list[str] = []
    offset = 0
    for name in objects:
        header_end = batch.index(b"\n", offset)
        header = batch[offset:header_end].split()
        if header[-1] == b"missing":
            raise ValueError(f"git object is missing: {name}")
        size = int(header[2])
        body = batch[header_end + 1 : header_end + 1 + size]
        texts.append(body.decode("utf-8-sig"))
        offset = header_end + 1 + size + 1
    return dict(zip(paths, texts[:-1], strict=True)), texts[-1]


def orphaned_since(
    repo_root: Path, base: str, *, staged: bool = False
) -> tuple[SymbolDefinition, ...]:
    """Symbols unreachable now that were reachable, or absent, at ``base``."""
    previous = {
        symbol.qualified_name
        for symbol in unreachable_symbols(*git_sources(repo_root, base))
    }
    current = (
        unreachable_symbols(*git_sources(repo_root, None))
        if staged
        else find_unreachable_symbols(repo_root)
    )
    return tuple(symbol for symbol in current if symbol.qualified_name not in previous)


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
    aliases = {
        alias.asname: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname and alias.asname != alias.name
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            yield _NameLoad(node.id, path, node.lineno)
            if node.id in aliases:
                yield _NameLoad(aliases[node.id], path, node.lineno)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            yield _NameLoad(node.attr, path, node.lineno)


def _inside(load: _NameLoad, definition: SymbolDefinition) -> bool:
    return (
        load.path == definition.path
        and definition.line <= load.line <= definition.end_line
    )


def _entry_point_names(pyproject: str) -> frozenset[str]:
    scripts = tomllib.loads(pyproject)
    targets = scripts.get("project", {}).get("scripts", {}).values()
    return frozenset(target.rpartition(":")[2] for target in targets)


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report xic_extractor symbols with no reference from production "
            "code. Without --base it always exits 0 on a readable tree."
        ),
    )
    parser.add_argument(
        "--output-tsv",
        type=Path,
        help="Write the report here instead of standard output.",
    )
    parser.add_argument(
        "--base",
        metavar="REF",
        help="Fail on symbols unreachable now but not at this Git commit.",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Check the index instead of working files (requires --base).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.staged and args.base is None:
        parser.error("--staged requires --base")
    if args.base is not None:
        try:
            orphaned = orphaned_since(ROOT, args.base, staged=args.staged)
        except (OSError, subprocess.CalledProcessError, SyntaxError, ValueError) as exc:
            print(f"reachability baseline failed: {exc}", file=sys.stderr)
            return 1
        for symbol in orphaned:
            print(
                f"{symbol.path}:{symbol.line}: {symbol.qualified_name} has no "
                "production caller; remove it in this change, or give it the "
                "caller this change meant to add",
                file=sys.stderr,
            )
        if not orphaned:
            print(f"No symbol lost its production caller since {args.base}.")
        return int(bool(orphaned))
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
