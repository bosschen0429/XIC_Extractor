import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from _pytest.mark.expression import Expression
from pre_commit.clientlib import load_config

from tools.testing import test_shards
from tools.testing.test_shards import (
    SHARD_PATTERNS,
    SNAPSHOT_TESTS,
    assign_shards,
    check_coverage,
    discover_snapshot_tests,
    discover_test_files,
    shard_for_path,
)

# Files whose path matches more than one shard glob. First match wins, so the
# left-hand shard is the owner and the file runs exactly once. Locking the set
# here means a new overlap has to be an explicit decision.
_ALLOWED_SHARD_OVERLAPS: dict[Path, tuple[str, ...]] = {
    Path("tests/test_config_hash.py"): ("docs-config", "targeted-core"),
    Path("tests/test_config_io.py"): ("docs-config", "targeted-core"),
    Path("tests/test_discovery_worker.py"): ("workbench", "alignment-core"),
    Path("tests/test_matrix_identity_blast_radius.py"): (
        "alignment-core",
        "product-gates-evidence",
    ),
    Path("tests/test_ms1_scan_index.py"): ("alignment-core", "product-gates-evidence"),
    Path("tests/test_pt_pattern_presets.py"): ("docs-config", "alignment-core"),
    Path("tests/test_targeted_manual_validation.py"): (
        "targeted-core",
        "product-gates-evidence",
    ),
    Path("tests/test_targeted_peak_selection.py"): (
        "targeted-core",
        "product-gates-evidence",
    ),
    Path("tests/test_targeted_service_contract.py"): (
        "targeted-core",
        "product-gates-evidence",
    ),
    Path("tests/test_validate_ms1_scan_index_xic.py"): (
        "alignment-core",
        "diagnostics-tools",
    ),
}


def test_test_shards_cover_every_test_file() -> None:
    assert check_coverage() == ()


def test_test_shard_coverage_hook_routes_staged_tests_to_owner(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(".pre-commit-config.yaml")
    hooks = {
        hook["id"]: hook
        for repository in config["repos"]
        for hook in repository["hooks"]
    }

    hook = hooks["test-shard-coverage"]
    assert hook["entry"] == (
        "uv run --cache-dir .uv-cache python -m tools.testing.test_shards --check"
    )
    assert hook["files"] == r"^(tests/.*\.py|tools/testing/test_shards\.py)$"
    assert hook["pass_filenames"] is False

    monkeypatch.chdir(tmp_path)
    test_root = Path("tests")
    test_root.mkdir()
    assigned = test_root / "test_finalized_application_result.py"
    assigned.write_text("def test_assigned(): pass\n", encoding="utf-8")

    assert test_shards.main(["--check"]) == 0
    capsys.readouterr()

    probe = test_root / "test_unassigned_shard_probe.py"
    probe.write_text("def test_probe(): pass\n", encoding="utf-8")

    assert test_shards.main(["--check"]) == 1
    assert probe.as_posix() in capsys.readouterr().err


def test_test_shards_do_not_run_empty_primary_shards() -> None:
    assigned = assign_shards(discover_test_files())

    for shard in SHARD_PATTERNS:
        assert assigned[shard], shard


def test_test_shards_keep_changed_shard_contracts() -> None:
    assigned = assign_shards(
        (
            Path("tests/test_discovery_worker.py"),
            Path("tests/test_pyinstaller_spec.py"),
            Path("tests/test_alignment_pipeline_outputs.py"),
            Path("tests/test_processing_run_spec.py"),
            Path("tests/test_processing_run_spec_architecture.py"),
            Path("tests/test_processing_run_spec_entrypoints.py"),
            Path("tests/test_physical_peak_evaluation_persistence.py"),
            Path("tests/test_run_discovery_run_spec_cli.py"),
            Path("tests/test_process_job_execution.py"),
            Path("tests/test_evidence_artifacts.py"),
        )
    )

    assert assigned["workbench"] == (Path("tests/test_discovery_worker.py"),)
    assert assigned["docs-config"] == (
        Path("tests/test_pyinstaller_spec.py"),
    )
    assert assigned["alignment-core"] == (
        Path("tests/test_alignment_pipeline_outputs.py"),
        Path("tests/test_processing_run_spec.py"),
        Path("tests/test_processing_run_spec_architecture.py"),
        Path("tests/test_processing_run_spec_entrypoints.py"),
        Path("tests/test_physical_peak_evaluation_persistence.py"),
        Path("tests/test_run_discovery_run_spec_cli.py"),
        Path("tests/test_process_job_execution.py"),
    )
    exact_alignment_paths = (
        Path("tests/test_processing_run_spec.py"),
        Path("tests/test_processing_run_spec_architecture.py"),
        Path("tests/test_processing_run_spec_entrypoints.py"),
        Path("tests/test_physical_peak_evaluation_persistence.py"),
        Path("tests/test_run_discovery_run_spec_cli.py"),
    )
    for path in exact_alignment_paths:
        assert _matching_shards(path) == ("alignment-core",)
        assert shard_for_path(path) == "alignment-core"
    assert assigned["product-gates-evidence"] == (
        Path("tests/test_evidence_artifacts.py"),
    )


def test_test_shards_lock_allowed_overlaps_and_first_match_owner() -> None:
    observed = {
        path: matches
        for path in discover_test_files()
        if len(matches := _matching_shards(path)) > 1
    }

    assert observed == _ALLOWED_SHARD_OVERLAPS
    for path, matches in observed.items():
        assert shard_for_path(path) == matches[0]


def test_test_shards_prefer_uv_runner_when_pytest_is_not_importable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(test_shards, "find_spec", lambda name: None)
    monkeypatch.setattr(
        test_shards.shutil,
        "which",
        lambda name: "C:/tools/uv.exe" if name == "uv" else "C:/global/pytest.exe",
    )

    assert test_shards._pytest_command() == ["C:/tools/uv.exe", "run", "pytest"]


def _matching_shards(path: Path) -> tuple[str, ...]:
    return tuple(
        shard
        for shard in SHARD_PATTERNS
        if test_shards._shard_matches_path(shard, path)
    )


def test_snapshot_marker_cannot_grow_without_a_recorded_decision() -> None:
    # The marker removes a test from CI, so it gets the ratchet the shard
    # budgets have. Marking a test without recording it here fails, which is
    # the gate the file-level exclusion never had.
    assert discover_snapshot_tests() == SNAPSHOT_TESTS


def test_recorded_snapshot_tests_still_exist() -> None:
    # A registry entry for a renamed or deleted test would silently stop
    # protecting anything.
    discovered = discover_snapshot_tests()
    for path, names in SNAPSHOT_TESTS.items():
        assert path in discovered, path
        for name in names:
            assert name in discovered[path], f"{path}::{name}"


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


def test_snapshot_runner_intersects_caller_marker_with_snapshot_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        test_shards,
        "discover_test_files",
        lambda _root: (Path("tests/test_snapshot.py"),),
    )
    monkeypatch.setattr(
        test_shards,
        "_pytest_command",
        lambda: ["python", "-m", "pytest"],
    )
    monkeypatch.setattr(
        test_shards.subprocess,
        "call",
        lambda command: commands.append(command) or 0,
    )

    assert test_shards._run_snapshots(("-m", "slow"), Path("tests")) == 0

    assert len(commands) == 1
    command = commands[0]
    pytest_args = command[3:]
    assert pytest_args.count("-m") == 1
    marker_index = pytest_args.index("-m")
    assert pytest_args[marker_index + 1] == "(slow) and (ledger_snapshot)"


def test_ci_shard_intersects_caller_marker_with_snapshot_exclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        test_shards,
        "assign_shards",
        lambda _files: {"docs-config": (Path("tests/test_regular.py"),)},
    )
    monkeypatch.setattr(test_shards, "discover_test_files", lambda _root: ())
    monkeypatch.setattr(
        test_shards,
        "_pytest_command",
        lambda: ["python", "-m", "pytest"],
    )
    monkeypatch.setattr(
        test_shards.subprocess,
        "call",
        lambda command: commands.append(command) or 0,
    )

    assert (
        test_shards._run_shard(
            "docs-config", ("-m", "slow"), Path("tests"), all_markers=True
        )
        == 0
    )

    assert len(commands) == 1
    command = commands[0]
    pytest_args = command[3:]
    assert pytest_args.count("-m") == 1
    marker_index = pytest_args.index("-m")
    assert pytest_args[marker_index + 1] == "(slow) and (not ledger_snapshot)"


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
        test_shards._intersect_marker_selection(
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
        test_shards,
        "discover_test_files",
        lambda _root: (Path("tests/test_snapshot.py"),),
    )
    monkeypatch.setattr(
        test_shards.subprocess,
        "call",
        lambda _command: pytest.fail("malformed marker reached pytest"),
    )

    assert test_shards._run_snapshots(pytest_args, Path("tests")) == 2


@pytest.mark.parametrize(
    ("path", "kept"),
    (
        ("pyproject.toml", {"packaging_build", "browser_e2e"}),
        ("uv.lock", {"packaging_build", "browser_e2e"}),
        ("xic_extractor.spec", {"packaging_build"}),
        (".github/workflows/build.yml", {"packaging_build"}),
        (".github/workflows/ci.yml", {"packaging_build", "browser_e2e"}),
        ("tests/test_test_shards.py", {"packaging_build", "browser_e2e"}),
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
        ("tools/testing/test_shards.py", {"packaging_build", "browser_e2e"}),
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
    skipped = set(test_shards.irrelevant_markers([path]))

    assert set(test_shards.RELEVANCE_RULES) - skipped == kept


def _capture_shard_marker(monkeypatch: pytest.MonkeyPatch, **run_kwargs) -> str:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        test_shards,
        "assign_shards",
        lambda _files: {"docs-config": (Path("tests/test_regular.py"),)},
    )
    monkeypatch.setattr(test_shards, "discover_test_files", lambda _root: ())
    monkeypatch.setattr(
        test_shards, "_pytest_command", lambda: ["python", "-m", "pytest"]
    )
    monkeypatch.setattr(
        test_shards.subprocess, "call", lambda command: commands.append(command) or 0
    )

    assert test_shards._run_shard("docs-config", (), Path("tests"), **run_kwargs) == 0

    pytest_args = commands[0][3:]
    return pytest_args[pytest_args.index("-m") + 1]


def test_local_shard_skips_markers_unrelated_to_the_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_shards, "_local_change_base", lambda: "base")
    monkeypatch.setattr(
        test_shards,
        "changed_paths",
        lambda base, head=None: ["docs/product/backfill.md"],
    )

    expression = _capture_shard_marker(monkeypatch)

    assert expression == (
        "not ledger_snapshot and not browser_e2e and not packaging_build"
    )
    assert _capture_shard_marker(monkeypatch, all_markers=True) == "not ledger_snapshot"


def test_local_shard_runs_every_marker_when_git_cannot_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_shards, "_local_change_base", lambda: None)

    assert _capture_shard_marker(monkeypatch) == "not ledger_snapshot"


def test_ci_reads_relevance_rules_from_the_shard_owner() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "tools.testing.test_shards --marker-expression" in workflow
    for pattern in test_shards.RELEVANCE_RULES.values():
        assert pattern.pattern[:30] not in workflow


@pytest.mark.parametrize(
    ("event", "changed", "failed_phase", "expected_exit"),
    (
        ("push", (), "", 0),
        ("pull_request", (".github/workflows/ci.yml",), "", 0),
        ("pull_request", ("tests/test_workbench_browser.py",), "", 0),
        ("pull_request", ("xic_extractor/mz_search.py",), "", 0),
        ("pull_request", ("README.md",), "", 0),
        ("push", (), "parallel", 11),
        ("push", (), "browser", 12),
        ("push", (), "serial", 13),
    ),
)
def test_ci_isolates_browser_work_without_losing_selection_or_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event: str,
    changed: tuple[str, ...],
    failed_phase: str,
    expected_exit: int,
) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is required to exercise the Windows CI step")

    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text("utf-8"))
    step = next(
        step for step in workflow["jobs"]["test"]["steps"] if step.get("name") == "Test"
    )
    monkeypatch.setattr(test_shards, "changed_paths", lambda *_: changed)
    selection = (
        test_shards.ci_marker_expression("base", "head")
        if event == "pull_request"
        else test_shards.CI_BASE_MARKER_EXPRESSION
    )
    assert selection is not None
    for name, value in step["env"].items():
        monkeypatch.setenv(name, str(value))
    calls_path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("XIC_CI_TEST_CALLS", str(calls_path))
    monkeypatch.setenv("XIC_CI_TEST_SELECTION", selection)
    monkeypatch.setenv("XIC_CI_TEST_FAILED_PHASE", failed_phase)
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
    if ($args[1] -eq 'tools.testing.test_shards') {
        Write-Output $env:XIC_CI_TEST_SELECTION
    } elseif ($args[1] -eq 'pytest') {
        $pytestArgs = $args | Select-Object -Skip 2
        $phase = if ($pytestArgs -contains '4') { 'parallel' }
            elseif ($pytestArgs -contains '-m') { 'browser' } else { 'serial' }
        if ($phase -eq $env:XIC_CI_TEST_FAILED_PHASE) {
            $global:LASTEXITCODE = @{ parallel = 11; browser = 12; serial = 13 }[$phase]
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
    browser_selected = "not browser_e2e" not in selection
    packaging_selected = "not packaging_build" not in selection
    assert len(pytest_calls) == 1 + browser_selected + packaging_selected
    parallel = pytest_calls[0]
    assert parallel[parallel.index("-n") + 1] == "4"
    parallel_expression = Expression.compile(parallel[parallel.index("-m", 2) + 1])
    browser_expression = None
    if browser_selected:
        browser = pytest_calls[1]
        assert browser[browser.index("-n") + 1] == "0"
        browser_expression = Expression.compile(browser[browser.index("-m", 2) + 1])
    assert any(call[:2] == ["-m", "playwright"] for call in calls) == browser_selected
    if packaging_selected:
        assert "-n" not in pytest_calls[-1]
        assert (
            sum("tests/test_pyinstaller_spec.py::" in arg for arg in pytest_calls[-1])
            == 3
        )

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
