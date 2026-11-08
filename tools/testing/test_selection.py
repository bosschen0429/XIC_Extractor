"""Select the tests a change can affect, and the CI marker gates it needs.

Selection is derived from the repository rather than registered by hand: the
static import graph maps a changed module, test included, to every test that
imports it; a path named in code, a test, or a checked-in register links a
script, document, or data file to the tests that read it. Anything the
derivation cannot place runs the whole suite.

    python -m tools.testing.test_selection             # tests this branch affects
    python -m tools.testing.test_selection -- -n 0     # same, in one process
    python -m tools.testing.test_selection --print     # show the selection only
    python -m tools.testing.test_selection --all       # every ordinary test
"""

from __future__ import annotations

import argparse
import ast
import io
import os
import re
import shutil
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Sequence, cast

import grimp

FIRST_PARTY_PACKAGES = (
    "xic_extractor",
    "xic_inspector",
    "xic_workbench",
    "scripts",
    "tools",
    "tests",
)

# Inputs that configure every test session. A change here runs the whole suite.
FULL_RUN_INPUTS = re.compile(
    r"^(pyproject\.toml|uv\.lock|(.*/)?conftest\.py"
    r"|tools/testing/(test_selection|test_sharding|pytest_temp)\.py)$"
)
# Text files a register-reading test can open; one that lists a changed path
# links that path to the tests reading it.
DATA_SUFFIXES = frozenset(
    {".csv", ".json", ".md", ".toml", ".tsv", ".txt", ".yaml", ".yml"}
)

ALL_TESTS = "ALL"

# Code that globs Python files reads source as text, so any module can feed it;
# so can every test that runs such code.
SOURCE_SCAN = re.compile(r"""\.r?glob\(\s*[rf]?["'][^"']*\.py["']""")

# Markers whose tests are expensive and guard a known set of inputs. A change
# that touches none of a marker's paths skips that marker's tests; a change that
# touches one selects every test carrying the marker. CI
# (.github/workflows/ci.yml) and local runs both read this table.
RELEVANCE_RULES: dict[str, re.Pattern[str]] = {
    "browser_e2e": re.compile(
        r"^(xic_workbench/|xic_inspector/"
        r"|tests/(test_browser_diagnostics|test_workbench_browser"
        r"|test_xic_inspector_browser)\.py$"
        # Shared test-support modules (fixtures, fakes, browser diagnostics)
        # can feed browser journeys; any change to them runs the browser lane.
        r"|tests/(?!test_)[^/]+\.py$"
        r"|tools/testing/(test_selection|test_sharding|pytest_temp)\.py$"
        r"|\.github/workflows/ci\.yml$"
        r"|pyproject\.toml$|uv\.lock$)"
    ),
    # Include whole shipped package roots: an installed/frozen import can depend
    # on any of their code or data. A shortlist of entry points misses package
    # inputs and resources, including the offline user documentation.
    "packaging_build": re.compile(
        r"^((pyproject\.toml|uv\.lock|MANIFEST\.in)$"
        r"|\.github/workflows/(build|ci)\.yml$"
        r"|tests/test_pyinstaller_spec\.py$"
        r"|docs/user/"
        r"|(xic_extractor|xic_inspector|xic_workbench|scripts|tools|packaging|config)/)"
    ),
    # PowerShell replays of the CI workflow steps read only the workflow and the
    # selection tooling those steps call.
    "ci_workflow": re.compile(
        r"^(\.github/workflows/ci\.yml"
        r"|tools/testing/(test_selection|test_sharding|pytest_temp)\.py"
        r"|tests/test_test_selection\.py)$"
    ),
}
# The main CI pytest session: the census runs once in the lint job, and serial
# tests run after the parallel session.
CI_BASE_MARKER_EXPRESSION = "not current_repository_census and not serial_ci"


@dataclass(frozen=True)
class SelectionPlan:
    """Test files to run (None means every test) and the marker expression."""

    files: tuple[str, ...] | None
    marker_expression: str


def discover_test_files(root: Path = Path("tests")) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                path
                for path in root.rglob("test_*.py")
                if "__pycache__" not in path.parts
            ),
            key=lambda path: path.as_posix(),
        )
    )


def select_test_files(
    changed: Sequence[str],
    *,
    root: Path = Path("."),
    packages: Sequence[str] = FIRST_PARTY_PACKAGES,
    test_package: str = "tests",
) -> tuple[str, ...] | None:
    """Return the test files ``changed`` can affect, or None to run them all."""

    if any(FULL_RUN_INPUTS.match(path) for path in changed):
        return None
    repository = _Repository(root, packages, test_package)
    selected: set[str] = set()
    for path in changed:
        tests = repository.dependent_tests(path)
        if tests is None:
            return None
        selected.update(tests)
    for marker in relevant_markers(changed):
        selected.update(
            t for t, source in repository.test_sources.items() if marker in source
        )
    return tuple(sorted(selected))


class _Repository:
    """Source text and the import graph that selection derives from."""

    def __init__(self, root: Path, packages: Sequence[str], test_package: str):
        self.packages = frozenset(packages)
        self.test_package = test_package
        self.test_sources = {
            path.relative_to(root).as_posix(): _read(path)
            for path in discover_test_files(root / test_package)
        }
        self.graph = grimp.build_graph(*packages, cache_dir=None)
        self.test_modules = {_module_name(path): path for path in self.test_sources}
        # Test files that name a path are matched directly; counting them as
        # owners would also select every test that imports their helpers.
        self.module_sources = {
            module: _dependency_source(path)
            for package in packages
            for path in (root / package).rglob("*.py")
            if (module := _module_name(path.relative_to(root).as_posix()))
            in self.graph.modules
            and module not in self.test_modules
        }
        self.data_sources = {
            path: _read(root / path)
            for path in _tracked_files(root)
            if Path(path).suffix in DATA_SUFFIXES and (root / path).is_file()
        }
        scanning = {
            module
            for module, source in self.module_sources.items()
            if SOURCE_SCAN.search(source)
        }
        self.source_scanners = self._tests_importing(scanning) | {
            test
            for test, source in self.test_sources.items()
            if SOURCE_SCAN.search(source)
        }

    def dependent_tests(
        self, path: str, *, follow_data: bool = True
    ) -> set[str] | None:
        """Tests a change to ``path`` can affect; None when none can be placed."""
        tests = self.source_scanners.copy() if path.endswith(".py") else set()
        if follow_data:
            # A register or document that lists the path is checked by the
            # tests that read that register.
            for listing in _naming(self.data_sources, (path,)) - {path}:
                tests.update(self.dependent_tests(listing, follow_data=False) or ())
        if path.endswith(".py"):
            module = _module_name(path)
            if module not in self.graph.modules:
                # A deleted or unpackaged module has no graph node to follow.
                return None
            references = (*self._file_references(path, directories=False), module)
            owners = {module}
        else:
            package = _owning_package(path)
            owners = {package} if package in self.graph.modules else set()
            references = (*self._file_references(path, directories=True), *owners)
        # Code that names the file in a string (importlib.resources, a
        # subprocess command, a data path) depends on it like an importer.
        owners.update(_naming(self.module_sources, references))
        tests.update(self._tests_importing(owners))
        tests.update(_naming(self.test_sources, references))
        if not tests and not path.endswith(".py"):
            return None
        return tests

    def _tests_importing(self, modules: set[str]) -> set[str]:
        affected = modules.union(
            *(self.graph.find_downstream_modules(m) for m in modules)
        )
        return {self.test_modules[m] for m in affected if m in self.test_modules}

    def _file_references(self, path: str, *, directories: bool) -> tuple[str, ...]:
        """Spellings code uses to read a file: path suffixes, name, parent dirs.

        Modules are read whole-directory only by source scanners, so a module
        omits the directory spellings a data file keeps.
        """
        parts = Path(path).parts
        references = ["/".join(parts[i:]) for i in range(len(parts) - 1)]
        references.append(parts[-1])
        if not directories:
            return tuple(references)
        # Code that builds a data file name from its stem quotes the stem.
        stem = Path(path).stem
        references.extend((f'"{stem}"', f"'{stem}'"))
        # A package root is named by nearly every path inside it; its imports
        # are what the graph follows, so start below it.
        top = 2 if parts[0] in self.packages else 1
        parents = [Path(*parts[:i]).as_posix() for i in range(top, len(parts))]
        if parts[0] == self.test_package:
            # Tests name their own fixtures relative to the test directory.
            parents += ["/".join(parts[1:i]) for i in range(2, len(parts))]
        for directory in parents:
            references.extend((directory + "/", f'"{directory}"', f"'{directory}'"))
        return tuple(references)


def _tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files"], capture_output=True, text=True
    )
    return result.stdout.splitlines() if result.returncode == 0 else []


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _dependency_source(path: Path) -> str:
    """Keep executable source spellings, not paths mentioned only in prose."""
    source = _read(path)
    docstrings = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and ast.get_docstring(node, clean=False) is not None
        ):
            value = cast(ast.Expr, node.body[0]).value
            docstrings.append(
                (
                    (value.lineno, value.col_offset),
                    (value.end_lineno, value.end_col_offset),
                )
            )
    lines = source.splitlines()
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING:
            line, column = token.start
            # AST columns count UTF-8 bytes; tokenize columns count characters.
            position = (line, len(lines[line - 1][:column].encode("utf-8")))
            if any(start <= position < end for start, end in docstrings):
                continue
        tokens.append(token)
    return tokenize.untokenize(tokens)


def _module_name(path: str) -> str:
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _owning_package(path: str) -> str:
    """The package whose directory holds a data file."""
    return ".".join(Path(path).parent.parts)


def _naming(sources: dict[str, str], references: Sequence[str]) -> set[str]:
    return {
        name
        for name, source in sources.items()
        if any(reference in source for reference in references)
    }


def relevant_markers(changed_paths: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        marker
        for marker, pattern in RELEVANCE_RULES.items()
        if any(pattern.match(path) for path in changed_paths)
    )


def irrelevant_markers(changed_paths: Sequence[str]) -> tuple[str, ...]:
    """Return the relevance-gated markers that no changed path touches."""
    relevant = relevant_markers(changed_paths)
    return tuple(marker for marker in RELEVANCE_RULES if marker not in relevant)


def plan_for_change(changed: Sequence[str], *, base_expression: str) -> SelectionPlan:
    exclusions = [f"not {marker}" for marker in irrelevant_markers(changed)]
    return SelectionPlan(
        files=select_test_files(changed),
        marker_expression=" and ".join(filter(None, [base_expression, *exclusions])),
    )


def changed_paths(base: str, head: str | None = None) -> list[str] | None:
    """Paths changed from ``base``; with no ``head``, include uncommitted files.

    Returns None when git cannot answer, so callers fall back to running every
    test instead of silently skipping them.
    """
    # A rename must report its old path too: code still naming it is affected.
    diff = ["git", "diff", "--no-renames", "--name-only", base]
    diff += [head] if head else []
    commands = [diff]
    if head is None:
        commands.append(["git", "ls-files", "--others", "--exclude-standard"])
    paths: list[str] = []
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        paths.extend(line for line in result.stdout.splitlines() if line)
    return paths


def _local_change_base() -> str | None:
    result = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/master"], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def ci_plan(base: str, head: str) -> SelectionPlan | None:
    paths = changed_paths(base, head)
    if paths is None:
        return None
    return plan_for_change(paths, base_expression=CI_BASE_MARKER_EXPRESSION)


# Packaging builds verify installed and frozen artifacts. CI runs them for every
# change that can affect packaging; local runs include them only on request.
LOCAL_SKIPPED_MARKERS = ("packaging_build",)


def _local_plan(*, run_all: bool, all_markers: bool) -> SelectionPlan:
    if run_all and all_markers:
        return SelectionPlan(files=None, marker_expression="")
    base = _local_change_base()
    paths = changed_paths(base) if base else None
    if paths is None:
        print("Could not diff against origin/master; running every test", flush=True)
        plan = SelectionPlan(files=None, marker_expression="")
    else:
        plan = plan_for_change(paths, base_expression="")
    if all_markers:
        return SelectionPlan(files=plan.files, marker_expression="")
    clauses = [clause for clause in plan.marker_expression.split(" and ") if clause]
    clauses += [
        f"not {marker}"
        for marker in LOCAL_SKIPPED_MARKERS
        if f"not {marker}" not in clauses
    ]
    return SelectionPlan(
        files=None if run_all else plan.files,
        marker_expression=" and ".join(clauses),
    )


def _run_plan(plan: SelectionPlan, pytest_args: Sequence[str]) -> int:
    owned_pytest_args = _intersect_marker_selection(
        pytest_args,
        required=plan.marker_expression,
    )
    if owned_pytest_args is None:
        print("Invalid pytest marker selection", file=sys.stderr)
        return 2
    marker_expression = _marker_expression(owned_pytest_args)
    print(f"Markers: {marker_expression or '(none)'}", flush=True)
    if plan.files is None:
        print("Running every test", flush=True)
    elif not plan.files:
        print("No tests are affected by this change", flush=True)
        return 0
    else:
        print(f"Running {len(plan.files)} affected test files", flush=True)
    files = plan.files or ()
    parallel_options, other_args = _split_parallel_options(
        owned_pytest_args[2:] if marker_expression else owned_pytest_args
    )
    if not _runs_in_parallel(parallel_options, other_args):
        exit_codes = [subprocess.call([*_pytest_command(), *owned_pytest_args, *files])]
    else:
        exit_codes = _run_parallel_then_serial(
            marker_expression,
            _with_default_parallelism(parallel_options),
            other_args,
            plan.files,
        )
    return _settle_exit_codes(exit_codes, every_test=plan.files is None)


# Mirrors CI: xdist owns parallelism, and serial_ci tests run after the parallel
# session because their bounded subprocess or build timeouts are not
# parallel-safe.
SERIAL_MARKER = "serial_ci"


def _with_default_parallelism(parallel_options: Sequence[str]) -> tuple[str, ...]:
    options = tuple(parallel_options)
    if _requested_workers(options) is None:
        options = ("-n", "auto", *options)
    if not any(option.split("=", 1)[0] == "--dist" for option in options):
        options = (*options, "--dist", "worksteal")
    return options


def _run_parallel_then_serial(
    marker_expression: str,
    parallel_options: Sequence[str],
    other_args: Sequence[str],
    files: tuple[str, ...] | None,
) -> list[int]:
    parallel_markers = _and_markers(marker_expression, f"not {SERIAL_MARKER}")
    print(f"Parallel session ({' '.join(parallel_options)})", flush=True)
    # Native BLAS pools must not multiply the worker budget.
    environment = {"OPENBLAS_NUM_THREADS": "1", **os.environ}
    exit_codes = [
        subprocess.call(
            [
                *_pytest_command(),
                "-m",
                parallel_markers,
                *parallel_options,
                *other_args,
                *(files or ()),
            ],
            env=environment,
        )
    ]
    serial_files = _files_naming_serial_marker(files)
    if serial_files:
        print(f"Serial session ({SERIAL_MARKER})", flush=True)
        exit_codes.append(
            subprocess.call(
                [
                    *_pytest_command(),
                    "-m",
                    _and_markers(marker_expression, SERIAL_MARKER),
                    *other_args,
                    *serial_files,
                ]
            )
        )
    return exit_codes


def _settle_exit_codes(exit_codes: Sequence[int], *, every_test: bool) -> int:
    failures = [code for code in exit_codes if code not in (0, 5)]
    if failures:
        return failures[0]
    if all(code == 5 for code in exit_codes):
        if every_test:
            return 5
        # A selected subset can contain only tests excluded by the marker gates.
        print(
            "No tests ran after filtering; no verification was performed.", flush=True
        )
    return 0


def _and_markers(*expressions: str) -> str:
    present = [expression for expression in expressions if expression]
    if len(present) == 1:
        return present[0]
    return " and ".join(f"({expression})" for expression in present)


def _files_naming_serial_marker(files: tuple[str, ...] | None) -> tuple[str, ...]:
    """Test files that can hold serial tests: a marked file names the marker."""
    candidates = (
        files
        if files is not None
        else tuple(path.as_posix() for path in discover_test_files())
    )
    return tuple(
        path
        for path in candidates
        if SERIAL_MARKER in Path(path).read_text(encoding="utf-8")
    )


def _split_parallel_options(
    pytest_args: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Separate caller xdist scheduling options from other pytest arguments."""
    parallel: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(pytest_args):
        argument = pytest_args[index]
        name = argument.split("=", 1)[0]
        if name in ("--numprocesses", "--dist", "--maxprocesses"):
            width = 1 if "=" in argument else 2
        elif argument == "-n":
            width = 2
        elif argument.startswith("-n"):
            width = 1
        else:
            remaining.append(argument)
            index += 1
            continue
        parallel.extend(pytest_args[index : index + width])
        index += width
    return tuple(parallel), tuple(remaining)


def _runs_in_parallel(
    parallel_options: Sequence[str], other_args: Sequence[str]
) -> bool:
    if find_spec("xdist") is None:
        return False
    if any(argument in ("no:xdist", "-pno:xdist") for argument in other_args):
        return False
    return _requested_workers(parallel_options) != "0"


def _requested_workers(parallel_options: Sequence[str]) -> str | None:
    for index, argument in enumerate(parallel_options):
        if argument in ("-n", "--numprocesses") and index + 1 < len(parallel_options):
            return parallel_options[index + 1]
        if argument.startswith("--numprocesses="):
            return argument.split("=", 1)[1]
        if argument.startswith("-n") and argument != "-n":
            return argument[2:]
    return None


def _intersect_marker_selection(
    pytest_args: Sequence[str],
    *,
    required: str,
) -> tuple[str, ...] | None:
    """Keep the runner-owned marker boundary when callers add ``-m`` filters."""
    remaining: list[str] = []
    caller_expressions: list[str] = []
    index = 0
    while index < len(pytest_args):
        argument = pytest_args[index]
        if argument == "-m":
            if index + 1 >= len(pytest_args):
                return None
            separated_expression = pytest_args[index + 1]
            if not separated_expression or separated_expression.startswith("-"):
                return None
            caller_expressions.append(separated_expression)
            index += 2
            continue
        if argument.startswith("-m") and argument != "-m":
            compact_expression = argument[2:].removeprefix("=")
            if not compact_expression:
                return None
            caller_expressions.append(compact_expression)
            index += 1
            continue
        remaining.append(argument)
        index += 1

    if not caller_expressions:
        return ("-m", required, *remaining) if required else tuple(remaining)
    owned = (required,) if required else ()
    expression = " and ".join(f"({item})" for item in (*caller_expressions, *owned))
    return ("-m", expression, *remaining)


def _marker_expression(owned_pytest_args: Sequence[str]) -> str:
    return owned_pytest_args[1] if owned_pytest_args[:1] == ("-m",) else ""


def _pytest_command() -> list[str]:
    if find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest"]
    uv_exe = shutil.which("uv")
    if uv_exe:
        return [uv_exe, "run", "pytest"]
    pytest_exe = shutil.which("pytest")
    if pytest_exe:
        return [pytest_exe]
    return [sys.executable, "-m", "pytest"]


def _print_plan(plan: SelectionPlan, pytest_args: Sequence[str] = ()) -> int:
    owned_pytest_args = _intersect_marker_selection(
        pytest_args, required=plan.marker_expression
    )
    if owned_pytest_args is None:
        print("Invalid pytest marker selection", file=sys.stderr)
        return 2
    print(_marker_expression(owned_pytest_args))
    if plan.files is None:
        print(ALL_TESTS)
    else:
        print("\n".join(plan.files))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", default="tests", help="Test root directory")
    parser.add_argument(
        "--print",
        action="store_true",
        help=(
            "Print the effective marker expression and candidate test files "
            "without running tests"
        ),
    )
    parser.add_argument(
        "--all", action="store_true", help="Run every test, not only affected ones"
    )
    parser.add_argument(
        "--all-markers",
        action="store_true",
        help=(
            "Also run relevance-gated tests this change does not touch, and "
            "packaging builds"
        ),
    )
    parser.add_argument(
        "--ci-plan",
        action="store_true",
        help=(
            "Print the CI marker expression, then the affected test files "
            f"(or {ALL_TESTS}), for --base..--head and exit"
        ),
    )
    parser.add_argument("--base", help="Base revision for --ci-plan")
    parser.add_argument("--head", help="Head revision for --ci-plan")
    args, rest = parser.parse_known_args(argv)
    pytest_args = rest[1:] if rest[:1] == ["--"] else rest
    if args.ci_plan:
        if not (args.base and args.head):
            parser.error("--ci-plan requires --base and --head")
        plan = ci_plan(args.base, args.head)
        if plan is None:
            print("git diff failed", file=sys.stderr)
            return 2
        return _print_plan(plan)
    plan = _local_plan(run_all=args.all, all_markers=args.all_markers)
    if args.print:
        return _print_plan(plan, pytest_args)
    return _run_plan(plan, pytest_args)


if __name__ == "__main__":
    raise SystemExit(main())
