"""Pick the test files that a working-tree change can actually reach.

This is a local feedback tool, not a gate. It resolves which tests to run from
the *static* import graph, so it cannot see importlib loads, entry points,
subprocess invocations, or a test that asserts on an artifact it never imports.
CI still runs every shard, and that is what decides whether a change is good.

    uv run python -m tools.testing.affected            # list affected tests
    uv run python -m tools.testing.affected --run      # run them under pytest
    uv run python -m tools.testing.affected --base HEAD~3 --run -- -x

When a change touches something whose blast radius cannot be narrowed -- the
shared conftest, the dependency lockfile, this selector itself -- the answer is
every test, and the reason is printed.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections import defaultdict, deque
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Iterable, Sequence

from tools.testing.test_shards import _pytest_command, discover_test_files

# A change to any of these invalidates the narrowing itself, so the selector
# refuses to narrow and says so. Keep this list short: every entry is a case
# where the tool gives up its whole value, so it must earn the place.
BROAD_IMPACT_PATTERNS: tuple[str, ...] = (
    "tests/conftest.py",
    "tools/testing/*.py",
    "pyproject.toml",
    "uv.lock",
)

SOURCE_ROOTS: tuple[str, ...] = ("xic_extractor/", "scripts/", "tools/", "gui/")


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], text=True, encoding="utf-8", errors="replace"
    )


def module_name(path: str) -> str:
    name = path[:-3].replace("/", ".")
    return name[:-9] if name.endswith(".__init__") else name


def _tracked_python_files() -> list[str]:
    return [line for line in _git("ls-files", "*.py").split("\n") if line.strip()]


def _direct_imports(path: str, known: set[str]) -> set[str]:
    """Internal modules imported by one file, resolved against known modules."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return set()
    me = module_name(path)
    # `from . import x` is relative to the containing package, and for an
    # __init__.py that package is the module itself -- module_name() already
    # dropped the `.__init__`. Subtracting node.level from the module would
    # climb one level too far there, resolving alignment/__init__.py's
    # `from .models import X` as xic_extractor.alignment.models.
    package = me if path.endswith("__init__.py") else me.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                base = ".".join(parts[: max(0, len(parts) - (node.level - 1))])
                module = f"{base}.{node.module}" if node.module and base else (
                    node.module or base
                )
            else:
                module = node.module or ""
            candidates = [module]
            if module:
                # `from pkg.mod import name` may name a submodule, not an object.
                candidates += [f"{module}.{alias.name}" for alias in node.names]
        found.update(c for c in candidates if c in known)
    return found


def build_reverse_index() -> tuple[dict[str, set[str]], dict[str, str]]:
    """Map each internal module to the test files that can reach it."""
    files = _tracked_python_files()
    modules = {
        module_name(f): f for f in files if f.startswith(SOURCE_ROOTS)
    }
    known = set(modules)

    graph = {name: _direct_imports(path, known) for name, path in modules.items()}

    reverse: dict[str, set[str]] = defaultdict(set)
    for test_path in (p.as_posix() for p in discover_test_files()):
        seen: set[str] = set()
        queue: deque[str] = deque(_direct_imports(test_path, known))
        while queue:
            module = queue.popleft()
            if module in seen:
                continue
            seen.add(module)
            queue.extend(graph.get(module, ()))
        for module in seen:
            reverse[module].add(test_path)
    return reverse, modules


def changed_files(base: str | None) -> list[str]:
    """Files differing from base, including staged and unstaged work."""
    if base:
        diff = _git("diff", "--name-only", f"{base}...HEAD")
    else:
        diff = ""
    pending = _git("diff", "--name-only", "HEAD")
    staged = _git("diff", "--name-only", "--cached")
    untracked = _git("ls-files", "--others", "--exclude-standard")
    merged = {
        line.strip()
        for line in f"{diff}\n{pending}\n{staged}\n{untracked}".split("\n")
        if line.strip()
    }
    return sorted(merged)


def _is_broad(path: str) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in BROAD_IMPACT_PATTERNS)


def select(changed: Iterable[str]) -> tuple[list[str], list[str], list[str]]:
    """Return (test files to run, broad-impact reasons, unresolved changes)."""
    changed = list(changed)
    broad = [p for p in changed if _is_broad(p)]
    all_tests = sorted(p.as_posix() for p in discover_test_files())
    if broad:
        return all_tests, broad, []

    reverse, modules = build_reverse_index()
    selected: set[str] = set()
    unresolved: list[str] = []
    for path in changed:
        if path.startswith("tests/") and path.endswith(".py"):
            if path in all_tests:
                selected.add(path)
            continue
        if not path.endswith(".py"):
            unresolved.append(path)
            continue
        name = module_name(path)
        if name not in modules:
            unresolved.append(path)
            continue
        selected.update(reverse.get(name, ()))
    return sorted(selected), [], unresolved


def _report(
    selected: Sequence[str], broad: Sequence[str], unresolved: Sequence[str]
) -> None:
    total = len(discover_test_files())
    if broad:
        print(
            f"Cannot narrow: {len(broad)} change(s) invalidate selection "
            f"-> running all {total} test files",
            file=sys.stderr,
        )
        for path in broad:
            print(f"  broad impact: {path}", file=sys.stderr)
    if unresolved:
        print(
            f"{len(unresolved)} change(s) the import graph cannot resolve; "
            "they contribute no tests. Judge these yourself:",
            file=sys.stderr,
        )
        for path in unresolved:
            print(f"  unresolved: {path}", file=sys.stderr)
    share = (100.0 * len(selected) / total) if total else 0.0
    print(
        f"{len(selected)} of {total} test files affected ({share:.1f}%)",
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base",
        default=None,
        help="Also include everything changed since this ref (e.g. master)",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run the affected tests instead of listing them",
    )
    args, rest = parser.parse_known_args(argv)
    pytest_args = rest[1:] if rest[:1] == ["--"] else rest

    changed = changed_files(args.base)
    if not changed:
        print("No changes detected", file=sys.stderr)
        return 0

    selected, broad, unresolved = select(changed)
    _report(selected, broad, unresolved)

    if not selected:
        print("No affected test files", file=sys.stderr)
        return 0
    if not args.run:
        for path in selected:
            print(path)
        return 0
    print(f"Running {len(selected)} test files", flush=True)
    return subprocess.call([*_pytest_command(), *pytest_args, *selected])


if __name__ == "__main__":
    raise SystemExit(main())
