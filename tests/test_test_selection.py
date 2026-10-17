import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from _pytest.mark.expression import Expression

from tools.testing import test_selection
from tools.testing.test_selection import (
    SNAPSHOT_TESTS,
    SelectionPlan,
    discover_snapshot_tests,
    select_test_files,
)


@pytest.fixture
def selection_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal importable repository: one package and one test package."""
    files = {
        "selpkg/__init__.py": "",
        "selpkg/core.py": "VALUE = 1\n",
        "selpkg/consumer.py": "from selpkg.core import VALUE\n",
        "selpkg/cli.py": "print('cli')\n",
        "seltests/__init__.py": "",
        "seltests/test_consumer.py": (
            "from selpkg.consumer import VALUE\n\ndef test_value(): assert VALUE\n"
        ),
        "seltests/test_unrelated.py": "def test_unrelated(): pass\n",
        "seltests/test_reuses_unrelated.py": (
            "from seltests.test_unrelated import test_unrelated\n"
        ),
        "seltests/test_cli_subprocess.py": "SCRIPT = 'selpkg/cli.py'\n",
        "seltests/test_docs.py": "GUIDE = 'docs/guide.md'\n",
        "seltests/test_workflow.py": "WORKFLOW = '.github/workflows/ci.yml'\n",
        "seltests/test_marked.py": "import pytest\n\npytest.mark.browser_e2e\n",
        "selpkg/data/__init__.py": "",
        "selpkg/data/preset.toml": "name = 'preset'\n",
        "selpkg/loader.py": "PACKAGE = 'selpkg.data'\n",
        "seltests/test_loader.py": "from selpkg.loader import PACKAGE\n",
        "seltests/test_scanner.py": "SOURCES = ROOT.glob('*.py')\n",
        "seltests/test_joined.py": "TABLE = ROOT / 'tables' / 'joined.tsv'\n",
        "tables/joined.tsv": "a\tb\n",
        "seltests/fixtures/case.json": "{}\n",
        "seltests/test_fixture.py": "CASE = HERE / 'fixtures' / 'case.json'\n",
        "registers/catalog.tsv": "owner\nselpkg/cli.py\n",
        "seltests/test_register.py": "REGISTER = 'registers/catalog.tsv'\n",
        "resources/presets/rna_case.toml": "name = 'rna'\n",
        "selpkg/resource_loader.py": (
            '"""See docs/guide.md."""\n'
            "NAME = BASE / 'presets' / ('rna_case' + '.toml')  # docs/guide.md\n"
            "class Guide:\n"
            '    """See docs/guide.md."""\n'
            "def describe():\n"
            '    """See docs/guide.md."""\n'
        ),
        "seltests/test_resource.py": "from selpkg.resource_loader import NAME\n",
        "config/settings.toml": "mode = 'default'\n",
        "seltests/test_settings.py": "SETTINGS = 'config/settings.toml'\n",
        ".importlinter": "[importlinter]\nroot_package = selpkg\n",
        "seltests/test_dependency_contract.py": "CONTRACT = '.importlinter'\n",
    }
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git = shutil.which("git")
    assert git is not None
    subprocess.run([git, "init", "-q", str(tmp_path)], check=True)
    subprocess.run([git, "-C", str(tmp_path), "add", "."], check=True)
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _select(root: Path, *changed: str) -> tuple[str, ...] | None:
    return select_test_files(
        changed, root=root, packages=("selpkg", "seltests"), test_package="seltests"
    )


def test_module_change_selects_importers_and_source_scanners(
    selection_repo: Path,
) -> None:
    assert _select(selection_repo, "selpkg/core.py") == (
        "seltests/test_consumer.py",
        "seltests/test_scanner.py",
    )


def test_test_file_change_selects_itself_importers_and_source_scanners(
    selection_repo: Path,
) -> None:
    assert _select(selection_repo, "seltests/test_unrelated.py") == (
        "seltests/test_reuses_unrelated.py",
        "seltests/test_scanner.py",
        "seltests/test_unrelated.py",
    )


def test_path_named_by_a_test_or_a_register_is_selected_without_an_import(
    selection_repo: Path,
) -> None:
    assert _select(selection_repo, "selpkg/cli.py") == (
        "seltests/test_cli_subprocess.py",
        "seltests/test_register.py",
        "seltests/test_scanner.py",
    )


@pytest.mark.parametrize(
    ("changed", "expected"),
    (
        ("docs/guide.md", ("seltests/test_docs.py",)),
        ("docs/other.md", ("seltests/test_docs.py",)),
        ("tables/joined.tsv", ("seltests/test_joined.py",)),
        ("seltests/fixtures/case.json", ("seltests/test_fixture.py",)),
        ("selpkg/data/preset.toml", ("seltests/test_loader.py",)),
        ("resources/presets/rna_case.toml", ("seltests/test_resource.py",)),
        ("config/settings.toml", ("seltests/test_settings.py",)),
        (".importlinter", ("seltests/test_dependency_contract.py",)),
    ),
)
def test_data_file_selects_tests_reading_it(
    selection_repo: Path, changed: str, expected: tuple[str, ...]
) -> None:
    assert _select(selection_repo, changed) == expected


def test_relevant_marker_selects_every_test_carrying_it(selection_repo: Path) -> None:
    assert _select(selection_repo, ".github/workflows/ci.yml") == (
        "seltests/test_marked.py",
        "seltests/test_workflow.py",
    )


@pytest.mark.parametrize(
    "changed",
    (
        "assets/unreferenced.bin",
        "pyproject.toml",
        "uv.lock",
        "seltests/conftest.py",
        "selpkg/deleted_module.py",
    ),
)
def test_change_the_derivation_cannot_place_runs_every_test(
    selection_repo: Path, changed: str
) -> None:
    assert _select(selection_repo, "selpkg/core.py", changed) is None


def test_selection_owner_change_runs_every_test() -> None:
    assert select_test_files(["tools/testing/test_selection.py"]) is None


def test_snapshot_marker_cannot_grow_without_a_recorded_decision() -> None:
    # The marker removes a test from CI, so marking a test without recording it
    # here fails.
    assert discover_snapshot_tests() == SNAPSHOT_TESTS


@pytest.mark.parametrize(
    "source",
    (
        "@pytest.mark.ledger_snapshot\nasync def test_async_snapshot():\n    pass\n",
        (
            "class TestSnapshots:\n"
            "    @pytest.mark.ledger_snapshot\n"
            "    def test_method_snapshot(self):\n"
            "        pass\n"
        ),
        (
            "@pytest.mark.ledger_snapshot\n"
            "class TestSnapshotClass:\n"
            "    def test_member(self):\n"
            "        pass\n"
        ),
        (
            "pytestmark = pytest.mark.ledger_snapshot\n\n"
            "def test_module_snapshot():\n"
            "    pass\n"
        ),
    ),
    ids=("async", "method", "class", "module"),
)
def test_snapshot_inventory_rejects_unsupported_marker_placements(
    tmp_path: Path,
    source: str,
) -> None:
    root = tmp_path / "tests"
    root.mkdir()
    (root / "test_marker_shape.py").write_text(
        "import pytest\n\n" + source,
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="ledger_snapshot.*top-level synchronous test function",
    ):
        discover_snapshot_tests(root)


def test_selection_prefers_uv_runner_when_pytest_is_not_importable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_selection, "find_spec", lambda name: None)
    monkeypatch.setattr(
        test_selection.shutil,
        "which",
        lambda name: "C:/tools/uv.exe" if name == "uv" else "C:/global/pytest.exe",
    )

    assert test_selection._pytest_command() == ["C:/tools/uv.exe", "run", "pytest"]


def _capture_calls(
    monkeypatch: pytest.MonkeyPatch, exit_code: int = 0
) -> list[list[str]]:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        test_selection, "_pytest_command", lambda: ["python", "-m", "pytest"]
    )
    monkeypatch.setattr(
        test_selection.subprocess,
        "call",
        lambda command: commands.append(command) or exit_code,
    )
    return commands


def test_snapshot_runner_intersects_caller_marker_with_snapshot_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        test_selection,
        "discover_test_files",
        lambda _root: (Path("tests/test_snapshot.py"),),
    )
    commands = _capture_calls(monkeypatch)

    assert test_selection._run_snapshots(("-m", "slow"), Path("tests")) == 0

    assert len(commands) == 1
    pytest_args = commands[0][3:]
    assert pytest_args.count("-m") == 1
    assert pytest_args[pytest_args.index("-m") + 1] == "(slow) and (ledger_snapshot)"


def test_selected_run_intersects_caller_marker_and_passes_selected_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _capture_calls(monkeypatch)
    plan = SelectionPlan(
        files=("tests/test_a.py", "tests/test_b.py"),
        marker_expression="not ledger_snapshot",
    )

    assert test_selection._run_plan(plan, ("-m", "slow")) == 0

    assert commands == [
        [
            "python",
            "-m",
            "pytest",
            "-m",
            "(slow) and (not ledger_snapshot)",
            "tests/test_a.py",
            "tests/test_b.py",
        ]
    ]


def test_selected_run_without_kept_tests_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _capture_calls(monkeypatch, exit_code=5)
    selected = SelectionPlan(files=("tests/test_a.py",), marker_expression="x")
    every = SelectionPlan(files=None, marker_expression="x")
    empty = SelectionPlan(files=(), marker_expression="x")

    assert test_selection._run_plan(selected, ()) == 0
    assert test_selection._run_plan(every, ()) == 5
    assert test_selection._run_plan(empty, ()) == 0
    assert len(commands) == 2


@pytest.mark.parametrize(
    ("pytest_args", "expected"),
    (
        (("-m", "slow"), ("-m", "(slow) and (owned)")),
        (("-mslow",), ("-m", "(slow) and (owned)")),
        (("-m=slow",), ("-m", "(slow) and (owned)")),
        (
            ("-m", "slow", "-mfast", "-m=unit"),
            ("-m", "(slow) and (fast) and (unit) and (owned)"),
        ),
        (("-q",), ("-m", "owned", "-q")),
    ),
)
def test_marker_intersection_handles_every_pytest_option_shape(
    pytest_args: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    assert (
        test_selection._intersect_marker_selection(
            pytest_args,
            required="owned",
        )
        == expected
    )


@pytest.mark.parametrize(
    "pytest_args",
    (
        ("-m=",),
        ("-m",),
        ("-m", ""),
        ("-m", "-q"),
    ),
)
def test_snapshot_runner_rejects_malformed_marker_before_pytest(
    monkeypatch: pytest.MonkeyPatch,
    pytest_args: tuple[str, ...],
) -> None:
    monkeypatch.setattr(
        test_selection,
        "discover_test_files",
        lambda _root: (Path("tests/test_snapshot.py"),),
    )
    monkeypatch.setattr(
        test_selection.subprocess,
        "call",
        lambda _command: pytest.fail("malformed marker reached pytest"),
    )

    assert test_selection._run_snapshots(pytest_args, Path("tests")) == 2


@pytest.mark.parametrize(
    ("path", "kept"),
    (
        ("pyproject.toml", {"packaging_build", "browser_e2e"}),
        ("uv.lock", {"packaging_build", "browser_e2e"}),
        ("xic_extractor.spec", {"packaging_build"}),
        (".github/workflows/build.yml", {"packaging_build"}),
        (".github/workflows/ci.yml", {"packaging_build", "browser_e2e"}),
        ("tests/test_test_selection.py", set()),
        ("tests/test_pyinstaller_spec.py", {"packaging_build"}),
        (
            "xic_extractor/discovery/data/default_background_library_15min.json.gz",
            {"packaging_build"},
        ),
        ("xic_inspector/api.py", {"packaging_build", "browser_e2e"}),
        ("xic_workbench/targeted.py", {"packaging_build", "browser_e2e"}),
        ("xic_workbench/assets/analysis.js", {"packaging_build", "browser_e2e"}),
        ("xic_workbench/server.py", {"packaging_build", "browser_e2e"}),
        ("xic_workbench/workspace.py", {"packaging_build", "browser_e2e"}),
        ("xic_workbench/jobs.py", {"packaging_build", "browser_e2e"}),
        ("tests/test_workbench_browser.py", {"browser_e2e"}),
        ("tests/test_xic_inspector_browser.py", {"browser_e2e"}),
        ("tests/test_xic_inspector_api.py", {"browser_e2e"}),
        ("tools/testing/test_selection.py", {"packaging_build", "browser_e2e"}),
        ("tools/diagnostics/targeted_istd_benchmark_loaders.py", {"packaging_build"}),
        ("xic_extractor/presets/data/dna_dr.toml", {"packaging_build"}),
        ("docs/product/backfill.md", {"product_governance"}),
        (
            "scripts/check_productization_state.py",
            {"product_governance", "packaging_build"},
        ),
        ("README.md", set()),
        ("pyproject.toml.example", set()),
        (".codex/skills/foo/SKILL.md", set()),
        ("xic_extractor/applications/untargeted/application.py", {"packaging_build"}),
    ),
)
def test_relevance_rules_keep_only_markers_whose_paths_changed(
    path: str, kept: set[str]
) -> None:
    skipped = set(test_selection.irrelevant_markers([path]))

    assert set(test_selection.RELEVANCE_RULES) - skipped == kept


def test_local_run_skips_markers_unrelated_to_the_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_selection, "_local_change_base", lambda: "base")
    monkeypatch.setattr(
        test_selection,
        "changed_paths",
        lambda base, head=None: ["docs/product/backfill.md"],
    )
    monkeypatch.setattr(
        test_selection, "select_test_files", lambda _changed: ("tests/test_a.py",)
    )

    plan = test_selection._local_plan(run_all=False, all_markers=False)
    assert plan == SelectionPlan(
        files=("tests/test_a.py",),
        marker_expression=(
            "not ledger_snapshot and not browser_e2e and not packaging_build"
        ),
    )
    assert test_selection._local_plan(run_all=False, all_markers=True) == (
        SelectionPlan(
            files=("tests/test_a.py",), marker_expression="not ledger_snapshot"
        )
    )
    assert test_selection._local_plan(run_all=True, all_markers=False).files is None


def test_local_run_runs_every_test_when_git_cannot_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_selection, "_local_change_base", lambda: None)

    assert test_selection._local_plan(run_all=False, all_markers=False) == (
        SelectionPlan(files=None, marker_expression="not ledger_snapshot")
    )


def test_ci_reads_the_plan_from_the_selection_owner() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "tools.testing.test_selection --ci-plan" in workflow
    for pattern in test_selection.RELEVANCE_RULES.values():
        assert pattern.pattern[:30] not in workflow


@pytest.mark.parametrize(
    ("event", "changed", "files", "failed_phase", "failed_code", "expected_exit"),
    (
        ("push", (), None, "", 0, 0),
        ("pull_request", (".github/workflows/ci.yml",), None, "", 0, 0),
        (
            "pull_request",
            ("tests/test_test_selection.py",),
            ("tests/test_test_selection.py",),
            "",
            0,
            0,
        ),
        (
            "pull_request",
            ("tests/test_workbench_browser.py",),
            ("tests/test_workbench_browser.py",),
            "",
            0,
            0,
        ),
        (
            "pull_request",
            ("xic_extractor/mz_search.py",),
            ("tests/test_a.py", "tests/test_b.py"),
            "",
            0,
            0,
        ),
        ("pull_request", ("README.md",), (), "", 0, 0),
        (
            "pull_request",
            ("README.md",),
            ("tests/test_readme.py",),
            "parallel",
            5,
            0,
        ),
        ("push", (), None, "parallel", 11, 11),
        ("push", (), None, "parallel", 5, 5),
        ("push", (), None, "browser", 12, 12),
        ("push", (), None, "serial", 13, 13),
    ),
)
def test_ci_runs_the_selected_plan_without_losing_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event: str,
    changed: tuple[str, ...],
    files: tuple[str, ...] | None,
    failed_phase: str,
    failed_code: int,
    expected_exit: int,
) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is required to exercise the Windows CI step")

    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text("utf-8"))
    step = next(
        step for step in workflow["jobs"]["test"]["steps"] if step.get("name") == "Test"
    )
    selection = (
        " and ".join(
            [
                test_selection.CI_BASE_MARKER_EXPRESSION,
                *(f"not {m}" for m in test_selection.irrelevant_markers(changed)),
            ]
        )
        if event == "pull_request"
        else test_selection.CI_BASE_MARKER_EXPRESSION
    )
    plan_lines = [selection, *(files if files is not None else ("ALL",))]
    for name, value in step["env"].items():
        monkeypatch.setenv(name, str(value))
    calls_path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("XIC_CI_TEST_CALLS", str(calls_path))
    monkeypatch.setenv("XIC_CI_TEST_PLAN", "\n".join(plan_lines))
    monkeypatch.setenv("XIC_CI_TEST_FAILED_PHASE", failed_phase)
    monkeypatch.setenv("XIC_CI_TEST_FAILED_CODE", str(failed_code))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.txt"))
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    # Run the actual workflow control flow; only the expensive child commands
    # are replaced. PowerShell still owns ordering, argument quoting and exit.
    script = r"""
$ErrorActionPreference = 'Stop'
function .\.venv\Scripts\python.exe {
    ConvertTo-Json -InputObject @($args | ForEach-Object { [string]$_ }) -Compress |
        Add-Content -LiteralPath $env:XIC_CI_TEST_CALLS
    $global:LASTEXITCODE = 0
    if ($args[1] -eq 'tools.testing.test_selection') {
        $env:XIC_CI_TEST_PLAN -split "`n" | Where-Object { $_ } | Write-Output
    } elseif ($args[1] -eq 'pytest') {
        $pytestArgs = $args | Select-Object -Skip 2
        $phase = if ($pytestArgs -contains '4') { 'parallel' }
            elseif ($pytestArgs -contains '-m') { 'browser' } else { 'serial' }
        if ($phase -eq $env:XIC_CI_TEST_FAILED_PHASE) {
            $global:LASTEXITCODE = [int]$env:XIC_CI_TEST_FAILED_CODE
        }
    }
}
""" + step["run"].replace("${{ github.event_name }}", event).replace(
        "${{ github.event.pull_request.base.sha }}", "base"
    ).replace("${{ github.event.pull_request.head.sha }}", "head")
    script_path = tmp_path / "ci-step.ps1"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr
    calls = [
        json.loads(line) for line in calls_path.read_text("utf-8-sig").splitlines()
    ]
    pytest_calls = [call for call in calls if call[:2] == ["-m", "pytest"]]
    runs_selection = files is None or len(files) > 0
    browser_selected = runs_selection and "not browser_e2e" not in selection
    packaging_selected = "not packaging_build" not in selection
    assert len(pytest_calls) == runs_selection + browser_selected + packaging_selected
    assert any(call[:2] == ["-m", "playwright"] for call in calls) == browser_selected
    if packaging_selected:
        assert "-n" not in pytest_calls[-1]
        assert (
            sum("tests/test_pyinstaller_spec.py::" in arg for arg in pytest_calls[-1])
            == 3
        )
    if not runs_selection:
        return

    parallel = pytest_calls[0]
    assert parallel[parallel.index("-n") + 1] == "4"
    selected_files = list(files or ())
    assert parallel[len(parallel) - len(selected_files) :] == selected_files
    parallel_expression = Expression.compile(parallel[parallel.index("-m", 2) + 1])
    browser_expression = None
    if browser_selected:
        browser = pytest_calls[1]
        assert browser[browser.index("-n") + 1] == "0"
        assert browser[len(browser) - len(selected_files) :] == selected_files
        browser_expression = Expression.compile(browser[browser.index("-m", 2) + 1])

    # The original selection must be partitioned exactly once, including the
    # browser/non-browser boundary and the existing relevance/snapshot gates.
    selected_expression = Expression.compile(selection)
    markers = (
        "browser_e2e",
        "packaging_build",
        "product_governance",
        "ledger_snapshot",
        "current_repository_census",
        "serial_ci",
    )
    for flags in itertools.product((False, True), repeat=len(markers)):
        marked = {marker for marker, enabled in zip(markers, flags) if enabled}
        in_parallel = parallel_expression.evaluate(marked.__contains__)
        in_browser = (
            browser_expression.evaluate(marked.__contains__)
            if browser_expression is not None
            else False
        )
        assert int(in_parallel) + int(in_browser) == int(
            selected_expression.evaluate(marked.__contains__)
        ), marked
        assert not (in_parallel and "browser_e2e" in marked)
        assert not (in_browser and "browser_e2e" not in marked)
