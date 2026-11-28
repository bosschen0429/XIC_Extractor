import itertools
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml
from _pytest.mark.expression import Expression

from tools.testing import test_selection
from tools.testing.test_selection import CIPlan, SelectionPlan


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
        "seltests/test_marked.py": (
            "import pytest\n\npytestmark = pytest.mark.browser_e2e\n"
        ),
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
    return test_selection.plan_for_change(
        changed,
        base_expression="",
        root=root,
        packages=("selpkg", "seltests"),
        test_package="seltests",
    ).files


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


def test_document_reference_does_not_turn_a_settings_key_into_a_dependency(
    selection_repo: Path,
) -> None:
    guide = selection_repo / "docs/alignment.md"
    guide.parent.mkdir()
    guide.write_text("Implementation: selpkg/core.py\n", encoding="utf-8")
    (selection_repo / "selpkg/settings.py").write_text(
        "CONFIG = {'alignment': {}}\n", encoding="utf-8"
    )
    (selection_repo / "seltests/test_alignment_settings.py").write_text(
        "from selpkg.settings import CONFIG\n", encoding="utf-8"
    )
    (selection_repo / "selpkg/guide_reader.py").write_text(
        "def read_guide(folder):\n"
        "    name = 'alignment'\n"
        "    return (folder / f'{name}.md').read_text()\n",
        encoding="utf-8",
    )
    (selection_repo / "seltests/test_alignment_guide.py").write_text(
        "from selpkg.guide_reader import read_guide\n", encoding="utf-8"
    )
    with (selection_repo / "seltests/test_unrelated.py").open(
        "a", encoding="utf-8"
    ) as f:
        f.write("KEY = 'alignment'\n")
    subprocess.run(
        ["git", "-C", str(selection_repo), "add", "docs/alignment.md"], check=True
    )

    assert _select(selection_repo, "selpkg/core.py") == (
        "seltests/test_alignment_guide.py",
        "seltests/test_consumer.py",
        "seltests/test_docs.py",
        "seltests/test_scanner.py",
    )


@pytest.mark.parametrize(
    "reader",
    [
        "GUIDE = ROOT / ('alignment' + '.md')\n",
        "NAME = 'alignment'\nGUIDE = ROOT / f'{NAME}.md'\n",
    ],
)
def test_constructed_filename_selects_readers_without_selecting_an_unrelated_key(
    selection_repo: Path,
    reader: str,
) -> None:
    (selection_repo / "seltests/test_joined_resource.py").write_text(
        reader, encoding="utf-8"
    )
    with (selection_repo / "seltests/test_unrelated.py").open(
        "a", encoding="utf-8"
    ) as f:
        f.write("KEY = 'alignment'\n")

    assert _select(selection_repo, "resources/guides/alignment.md") == (
        "seltests/test_joined_resource.py",
    )


def test_resource_key_in_a_subprocess_still_selects_its_test(
    selection_repo: Path,
) -> None:
    (selection_repo / "seltests/test_preset_subprocess.py").write_text(
        'SCRIPT = "from selpkg.resource_loader import load_preset; '
        "load_preset('rna_case')\"\n",
        encoding="utf-8",
    )

    assert _select(selection_repo, "resources/presets/rna_case.toml") == (
        "seltests/test_preset_subprocess.py",
        "seltests/test_resource.py",
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
    [
        "tests/test_delta_mass_review_browser.py",
        "xic_extractor/diagnostics/delta_mass_review_data.py",
        "tests/test_workbench_browser.py",
        "tests/test_xic_inspector_browser.py",
        "tests/test_browser_diagnostics.py",
        "xic_extractor/presets/data/dna_dr.toml",
        "docs/user/README.md",
    ],
)
def test_browser_plan_keeps_selected_browser_consumers(changed: str) -> None:
    plan = test_selection.plan_for_change(
        [changed], base_expression=test_selection.CI_BASE_MARKER_EXPRESSION
    )

    assert "not browser_e2e" not in plan.marker_expression
    assert plan.files is not None
    assert {
        "tests/test_delta_mass_review_browser.py",
        "tests/test_workbench_browser.py",
        "tests/test_xic_inspector_browser.py",
    } <= set(plan.files)


@pytest.mark.parametrize(
    "changed, browser_required",
    [
        ("seltests/test_plot.py", True),
        ("selpkg/consumer.py", True),
        ("selpkg/core.py", True),
        ("selpkg/data/preset.toml", True),
        ("seltests/fixtures/case.json", True),
        ("seltests/test_unrelated.py", False),
    ],
)
def test_browser_lane_follows_consumers_without_a_test_filename_registry(
    selection_repo: Path, changed: str, browser_required: bool
) -> None:
    (selection_repo / "seltests/test_plot.py").write_text(
        "import pytest\nfrom selpkg.consumer import VALUE\n"
        "from selpkg.loader import PACKAGE\n"
        "CASE = 'seltests/fixtures/case.json'\n"
        "pytestmark = [pytest.mark.browser_e2e]\n"
        "def test_plot(): assert VALUE\n",
        encoding="utf-8",
    )
    (selection_repo / "seltests/test_unrelated.py").write_text(
        "# pytest.mark.browser_e2e is mentioned, not applied\n"
        'EXAMPLE = "pytestmark = pytest.mark.browser_e2e"\n'
        "def test_unrelated(): pass\n",
        encoding="utf-8",
    )
    plan = test_selection.plan_for_change(
        [changed],
        base_expression="",
        root=selection_repo,
        packages=("selpkg", "seltests"),
        test_package="seltests",
    )

    assert ("not browser_e2e" not in plan.marker_expression) == browser_required
    assert plan.files is not None
    assert ("seltests/test_plot.py" in plan.files) == browser_required
    assert ("seltests/test_marked.py" in plan.files) == browser_required


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
    plan = test_selection.plan_for_change(
        ["selpkg/core.py", changed],
        base_expression="",
        root=selection_repo,
        packages=("selpkg", "seltests"),
        test_package="seltests",
    )
    assert plan.files is None
    assert "not browser_e2e" not in plan.marker_expression


@pytest.mark.parametrize(
    "owner",
    [
        "tools/testing/test_selection.py",
        "tools/testing/test_sharding.py",
        "tools/testing/pytest_temp.py",
    ],
)
def test_selection_owner_change_runs_every_test(owner: str) -> None:
    plan = test_selection.plan_for_change([owner], base_expression="")
    assert plan.files is None
    assert "not browser_e2e" not in plan.marker_expression


def test_ci_browser_file_plan_keeps_actual_pytest_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    sources = {
        "test_new_view.py": (
            "import pytest\npytestmark = pytest.mark.browser_e2e\n"
            "def test_view(): pass\n"
        ),
        "test_mixed.py": (
            "import pytest\n@pytest.mark.browser_e2e\n"
            "@pytest.mark.parametrize('value', [1, 2])\n"
            "def test_chart(value): assert value\n"
            "def test_ordinary(): pass\n"
        ),
        "test_fixture_string.py": (
            "EXAMPLE = 'pytest.mark.browser_e2e'\ndef test_plain(): pass\n"
        ),
        "test_unrelated.py": "def test_unrelated(): pass\n",
    }
    for name, source in sources.items():
        (tests / name).write_text(source, encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\nmarkers =\n    browser_e2e: browser case\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        test_selection, "changed_paths", lambda *_args: ["pyproject.toml"]
    )

    assert (
        test_selection.main(["--ci-plan", "--base", "before", "--head", "after"]) == 0
    )
    plan = json.loads(capsys.readouterr().out)
    assert plan["tests"]["files"] is None
    assert set(plan["browser"]["files"]) == {
        "tests/test_new_view.py",
        "tests/test_mixed.py",
        "tests/test_fixture_string.py",
    }
    nodes = []
    for targets in ((), plan["browser"]["files"]):
        collected = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "--noconftest",
                "-c",
                str(config),
                "-m",
                f"({plan['browser']['marker_expression']}) and browser_e2e",
                *targets,
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert collected.returncode == 0, collected.stdout + collected.stderr
        nodes.append(
            {
                line
                for line in collected.stdout.splitlines()
                if line.startswith("tests/")
            }
        )
    assert (
        nodes[0]
        == nodes[1]
        == {
            "tests/test_new_view.py::test_view",
            "tests/test_mixed.py::test_chart[1]",
            "tests/test_mixed.py::test_chart[2]",
        }
    )


def test_ci_plan_git_failure_has_no_success_payload(monkeypatch, capsys) -> None:
    monkeypatch.setattr(test_selection, "changed_paths", lambda *_args: None)
    assert (
        test_selection.main(["--ci-plan", "--base", "before", "--head", "after"]) == 2
    )
    output = capsys.readouterr()
    assert output.out == ""
    assert "git diff failed" in output.err


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
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int = 0,
    *,
    xdist: bool = False,
) -> list[list[str]]:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        test_selection, "_pytest_command", lambda: ["python", "-m", "pytest"]
    )
    monkeypatch.setattr(
        test_selection, "find_spec", lambda name: object() if xdist else None
    )
    monkeypatch.setattr(
        test_selection.subprocess,
        "call",
        lambda command, **_kwargs: commands.append(command) or exit_code,
    )
    return commands


@pytest.fixture
def serial_split_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """One ordinary test file and one that marks a serial test."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests").mkdir()
    Path("tests/test_plain.py").write_text("def test_plain(): pass\n", encoding="utf-8")
    Path("tests/test_build.py").write_text(
        "import pytest\n\n@pytest.mark.serial_ci\ndef test_build(): pass\n",
        encoding="utf-8",
    )
    return ("tests/test_plain.py", "tests/test_build.py")


def _record_sessions(
    monkeypatch: pytest.MonkeyPatch, exit_codes: tuple[int, ...] = (0, 0)
) -> list[tuple[list[str], dict[str, str] | None]]:
    sessions: list[tuple[list[str], dict[str, str] | None]] = []
    remaining = list(exit_codes)
    monkeypatch.setattr(
        test_selection, "_pytest_command", lambda: ["python", "-m", "pytest"]
    )
    monkeypatch.setattr(test_selection, "find_spec", lambda name: object())

    def call(command: list[str], env: dict[str, str] | None = None) -> int:
        sessions.append((command, env))
        return remaining.pop(0)

    monkeypatch.setattr(test_selection.subprocess, "call", call)
    return sessions


def test_local_run_parallelizes_then_runs_serial_tests_outside_xdist(
    monkeypatch: pytest.MonkeyPatch, serial_split_files: tuple[str, str]
) -> None:
    monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)
    sessions = _record_sessions(monkeypatch)
    plan = SelectionPlan(files=serial_split_files, marker_expression="not browser_e2e")

    assert test_selection._run_plan(plan, ("-q",)) == 0

    (parallel, parallel_env), (serial, serial_env) = sessions
    assert parallel == [
        "python",
        "-m",
        "pytest",
        "-m",
        "(not browser_e2e) and (not serial_ci)",
        "-n",
        "auto",
        "--dist",
        "worksteal",
        "-q",
        *serial_split_files,
    ]
    assert parallel_env is not None and parallel_env["OPENBLAS_NUM_THREADS"] == "1"
    assert serial == [
        "python",
        "-m",
        "pytest",
        "-m",
        "(not browser_e2e) and (serial_ci)",
        "-q",
        "tests/test_build.py",
    ]
    assert serial_env is None


def test_local_run_keeps_caller_worker_count_and_blas_setting(
    monkeypatch: pytest.MonkeyPatch, serial_split_files: tuple[str, str]
) -> None:
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "4")
    sessions = _record_sessions(monkeypatch)
    plan = SelectionPlan(files=serial_split_files, marker_expression="")

    assert test_selection._run_plan(plan, ("--numprocesses=3", "-x")) == 0

    (parallel, parallel_env), (serial, _env) = sessions
    assert parallel[3:6] == ["-m", "not serial_ci", "--numprocesses=3"]
    assert parallel_env is not None and parallel_env["OPENBLAS_NUM_THREADS"] == "4"
    assert serial[3:] == ["-m", "serial_ci", "-x", "tests/test_build.py"]


@pytest.mark.parametrize("pytest_args", (("-n", "0"), ("-n0",), ("-p", "no:xdist")))
def test_local_run_without_parallelism_is_one_session(
    monkeypatch: pytest.MonkeyPatch,
    serial_split_files: tuple[str, str],
    pytest_args: tuple[str, ...],
) -> None:
    sessions = _record_sessions(monkeypatch, exit_codes=(0,))
    plan = SelectionPlan(files=serial_split_files, marker_expression="owned")

    assert test_selection._run_plan(plan, pytest_args) == 0

    assert [command for command, _env in sessions] == [
        ["python", "-m", "pytest", "-m", "owned", *pytest_args, *serial_split_files]
    ]


def test_local_run_skips_serial_session_when_no_file_marks_serial_tests(
    monkeypatch: pytest.MonkeyPatch, serial_split_files: tuple[str, str]
) -> None:
    sessions = _record_sessions(monkeypatch, exit_codes=(0,))
    plan = SelectionPlan(files=serial_split_files[:1], marker_expression="")

    assert test_selection._run_plan(plan, ()) == 0
    assert len(sessions) == 1


@pytest.mark.parametrize(
    ("files", "exit_codes", "expected"),
    (
        ("selected", (1, 0), 1),
        ("selected", (0, 1), 1),
        ("selected", (2, 1), 2),
        ("selected", (0, 5), 0),
        ("selected", (5, 0), 0),
        ("selected", (5, 5), 0),
        ("every", (5, 5), 5),
    ),
)
def test_local_run_reports_failure_from_either_session(
    monkeypatch: pytest.MonkeyPatch,
    serial_split_files: tuple[str, str],
    files: str,
    exit_codes: tuple[int, int],
    expected: int,
) -> None:
    sessions = _record_sessions(monkeypatch, exit_codes=exit_codes)
    plan = SelectionPlan(
        files=serial_split_files if files == "selected" else None,
        marker_expression="",
    )

    assert test_selection._run_plan(plan, ()) == expected
    assert len(sessions) == 2


def test_selected_run_intersects_caller_marker_and_passes_selected_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _capture_calls(monkeypatch)
    plan = SelectionPlan(
        files=("tests/test_a.py", "tests/test_b.py"),
        marker_expression="not browser_e2e",
    )

    assert test_selection._run_plan(plan, ("-m", "slow")) == 0

    assert commands == [
        [
            "python",
            "-m",
            "pytest",
            "-m",
            "(slow) and (not browser_e2e)",
            "tests/test_a.py",
            "tests/test_b.py",
        ]
    ]


def test_selected_run_without_kept_tests_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commands = _capture_calls(monkeypatch, exit_code=5)
    selected = SelectionPlan(files=("tests/test_a.py",), marker_expression="x")
    every = SelectionPlan(files=None, marker_expression="x")
    empty = SelectionPlan(files=(), marker_expression="x")

    assert test_selection._run_plan(selected, ()) == 0
    selected_output = capsys.readouterr().out
    assert "Markers: x" in selected_output
    assert (
        "No tests ran after filtering; no verification was performed."
        in selected_output
    )
    assert test_selection._run_plan(every, ()) == 5
    assert test_selection._run_plan(empty, ()) == 0
    assert len(commands) == 2


@pytest.mark.parametrize(
    ("pytest_args", "expected_exit", "expected_output"),
    (
        ((), 0, "not browser_e2e\ntests/test_a.py\n"),
        (("-m", "slow"), 0, "(slow) and (not browser_e2e)\ntests/test_a.py\n"),
        (("-m",), 2, ""),
    ),
)
def test_print_selection_applies_caller_markers_without_running_tests(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pytest_args: tuple[str, ...],
    expected_exit: int,
    expected_output: str,
) -> None:
    monkeypatch.setattr(
        test_selection,
        "_local_plan",
        lambda **_kwargs: SelectionPlan(
            files=("tests/test_a.py",), marker_expression="not browser_e2e"
        ),
    )
    monkeypatch.setattr(
        test_selection.subprocess,
        "call",
        lambda _command: pytest.fail("preview must not run pytest"),
    )

    assert test_selection.main(["--print", "--", *pytest_args]) == expected_exit
    captured = capsys.readouterr()
    assert captured.out == expected_output
    assert captured.err == (
        "Invalid pytest marker selection\n" if expected_exit else ""
    )


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
def test_run_rejects_malformed_marker_before_pytest(
    monkeypatch: pytest.MonkeyPatch,
    pytest_args: tuple[str, ...],
) -> None:
    monkeypatch.setattr(
        test_selection.subprocess,
        "call",
        lambda _command: pytest.fail("malformed marker reached pytest"),
    )
    plan = SelectionPlan(files=("tests/test_a.py",), marker_expression="owned")

    assert test_selection._run_plan(plan, pytest_args) == 2


@pytest.mark.parametrize(
    ("pytest_args", "expected"),
    (
        (("-q",), ("-q",)),
        (("-m", "slow", "-q"), ("-m", "(slow)", "-q")),
    ),
)
def test_marker_intersection_without_owned_expression_keeps_caller_markers(
    pytest_args: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    assert test_selection._intersect_marker_selection(pytest_args, required="") == (
        expected
    )


@pytest.mark.parametrize(
    ("path", "kept"),
    (
        ("pyproject.toml", {"packaging_build", "browser_e2e"}),
        ("uv.lock", {"packaging_build", "browser_e2e"}),
        ("packaging/xic_extractor.spec", {"packaging_build"}),
        (".github/workflows/build.yml", {"packaging_build"}),
        (".github/workflows/ci.yml", {"packaging_build", "browser_e2e", "ci_workflow"}),
        ("tests/test_test_selection.py", {"ci_workflow"}),
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
        ("tests/browser_diagnostics.py", {"browser_e2e"}),
        ("tests/test_xic_inspector_api.py", set()),
        ("tests/inspector_fixtures.py", {"browser_e2e"}),
        ("tests/workbench_http.py", {"browser_e2e"}),
        (
            "tools/testing/test_selection.py",
            {"packaging_build", "browser_e2e", "ci_workflow"},
        ),
        (
            "tools/testing/test_sharding.py",
            {"packaging_build", "browser_e2e", "ci_workflow"},
        ),
        (
            "tools/testing/pytest_temp.py",
            {"packaging_build", "browser_e2e", "ci_workflow"},
        ),
        ("tools/diagnostics/duplicate_quantification.py", {"packaging_build"}),
        ("xic_extractor/presets/data/dna_dr.toml", {"packaging_build"}),
        ("docs/product/backfill.md", set()),
        ("MANIFEST.in", {"packaging_build"}),
        ("docs/development/command-line.md", set()),
        (
            "docs/user/assets/targeted-workspace.png",
            {"packaging_build", "browser_e2e"},
        ),
        ("docs/architecture/README.md", set()),
        ("README.md", set()),
        ("pyproject.toml.example", set()),
        (".codex/skills/foo/SKILL.md", set()),
        ("xic_extractor/applications/untargeted/application.py", {"packaging_build"}),
    ),
)
def test_relevance_rules_keep_only_markers_whose_paths_changed(
    path: str, kept: set[str]
) -> None:
    assert set(test_selection.relevant_markers([path])) == kept


def test_local_run_skips_markers_unrelated_to_the_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_selection, "_local_change_base", lambda: "base")
    monkeypatch.setattr(
        test_selection,
        "changed_paths",
        lambda base, head=None: ["docs/architecture/README.md"],
    )
    monkeypatch.setattr(
        test_selection,
        "plan_for_change",
        lambda _changed, **kwargs: SelectionPlan(
            files=("tests/test_a.py",),
            marker_expression=(
                "not browser_e2e and not packaging_build and not ci_workflow"
            ),
        ),
    )

    plan = test_selection._local_plan(run_all=False, all_markers=False)
    assert plan == SelectionPlan(
        files=("tests/test_a.py",),
        marker_expression="not browser_e2e and not packaging_build and not ci_workflow",
    )
    assert test_selection._local_plan(run_all=False, all_markers=True) == (
        SelectionPlan(files=("tests/test_a.py",), marker_expression="")
    )
    assert test_selection._local_plan(run_all=True, all_markers=False).files is None


def test_local_run_leaves_packaging_builds_to_ci_unless_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_selection, "_local_change_base", lambda: "base")
    monkeypatch.setattr(
        test_selection,
        "changed_paths",
        lambda base, head=None: [
            "xic_extractor/applications/untargeted/application.py"
        ],
    )
    monkeypatch.setattr(
        test_selection,
        "plan_for_change",
        lambda _changed, **kwargs: SelectionPlan(
            files=("tests/test_a.py",),
            marker_expression="not browser_e2e and not ci_workflow",
        ),
    )

    assert test_selection._local_plan(run_all=False, all_markers=False) == (
        SelectionPlan(
            files=("tests/test_a.py",),
            marker_expression=(
                "not browser_e2e and not ci_workflow and not packaging_build"
            ),
        )
    )
    assert test_selection._local_plan(run_all=False, all_markers=True) == (
        SelectionPlan(files=("tests/test_a.py",), marker_expression="")
    )


def test_local_run_runs_every_test_when_git_cannot_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_selection, "_local_change_base", lambda: None)

    assert test_selection._local_plan(run_all=False, all_markers=False) == (
        SelectionPlan(files=None, marker_expression="not packaging_build")
    )


def test_renamed_module_reports_its_old_path_and_runs_every_test(
    selection_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = [shutil.which("git"), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "commit", "-qm", "base"], cwd=selection_repo, check=True)
    subprocess.run(
        [*git, "mv", "selpkg/core.py", "selpkg/kernel.py"],
        cwd=selection_repo,
        check=True,
    )
    subprocess.run([*git, "commit", "-qm", "rename"], cwd=selection_repo, check=True)
    monkeypatch.chdir(selection_repo)

    changed = test_selection.changed_paths("HEAD~1", "HEAD")

    assert changed is not None and "selpkg/core.py" in changed
    assert _select(selection_repo, *changed) is None


def test_ci_reads_the_plan_from_the_selection_owner() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert workflow.count("tools.testing.test_selection --ci-plan") == 1
    for pattern in test_selection.RELEVANCE_RULES.values():
        assert pattern.pattern[:30] not in workflow
    jobs = yaml.safe_load(workflow)["jobs"]
    plan = jobs["plan"]
    coverage = next(step for step in plan["steps"] if step.get("id") == "coverage")
    assert coverage["env"]["XIC_CI_BASE"] == (
        "${{ github.event.pull_request.base.sha || github.event.before }}"
    )
    assert coverage["env"]["XIC_CI_HEAD"] == (
        "${{ github.event.pull_request.head.sha || github.sha }}"
    )
    assert coverage["env"]["XIC_CI_EVENT"] == "${{ github.event_name }}"
    assert plan["outputs"]["coverage"] == "${{ steps.coverage.outputs.coverage }}"
    assert plan["outputs"]["browser_required"] == (
        "${{ steps.coverage.outputs.browser_required }}"
    )
    for lane, step_name in (
        ("windows-integration", "Test"),
        ("python-tests", "Test"),
        ("browser", "Browser test"),
    ):
        step = next(s for s in jobs[lane]["steps"] if s.get("name") == step_name)
        environment = {**jobs[lane].get("env", {}), **step.get("env", {})}
        assert environment["XIC_CI_PLAN"] == "${{ needs.plan.outputs.coverage }}"


def test_ci_windows_integration_uses_hosted_runner() -> None:
    jobs = yaml.safe_load(Path(".github/workflows/ci.yml").read_text("utf-8"))["jobs"]

    assert jobs["windows-integration"]["runs-on"] == "windows-latest"


_CI_COMMAND_FAKES = r"""
$ErrorActionPreference = 'Stop'
function .\.venv\Scripts\python.exe {
    ConvertTo-Json -InputObject @($args | ForEach-Object { [string]$_ }) -Compress |
        Add-Content -LiteralPath $env:XIC_CI_TEST_CALLS
    $global:LASTEXITCODE = 0
    if ($args[1] -eq 'tools.testing.test_selection') {
        $env:XIC_CI_TEST_PLAN -split "`n" | Where-Object { $_ } | Write-Output
        if ($env:XIC_CI_TEST_FAILED_PHASE -eq 'plan') {
            $global:LASTEXITCODE = [int]$env:XIC_CI_TEST_FAILED_CODE
        }
    } elseif ($args[1] -eq 'playwright') {
        if ($env:XIC_CI_TEST_FAILED_PHASE -eq 'browser_install') {
            $global:LASTEXITCODE = [int]$env:XIC_CI_TEST_FAILED_CODE
        }
    } elseif ($args[1] -eq 'pytest') {
        $pytestArgs = $args | Select-Object -Skip 2
        $phase = if ($pytestArgs -match 'and windows_only') { 'windows' }
            elseif ($pytestArgs -match 'and browser_e2e') { 'browser' }
            elseif ($pytestArgs -contains '-n') { 'parallel' } else { 'serial' }
        if ($phase -eq $env:XIC_CI_TEST_FAILED_PHASE) {
            $global:LASTEXITCODE = [int]$env:XIC_CI_TEST_FAILED_CODE
        }
    }
}
function uv {
    if (($args[0..2] -join ' ') -ne 'run --no-sync python') {
        throw 'Unexpected uv invocation'
    }
    $pythonArgs = @($args | Select-Object -Skip 3)
    & .\.venv\Scripts\python.exe @pythonArgs
}
"""


def _render_ci_step(run: str) -> str:
    expressions = {
        "${{ matrix.shard }}": "1",
        "${{ strategy.job-total }}": "2",
    }
    for expression, value in expressions.items():
        run = run.replace(expression, value)
    assert "${{" not in run, run
    return run


@pytest.mark.ci_workflow
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
        ("push", (), None, "windows", 11, 11),
        ("push", (), None, "windows", 5, 5),
        (
            "pull_request",
            ("tests/test_readme.py",),
            ("tests/test_readme.py",),
            "windows",
            5,
            0,
        ),
        ("push", (), None, "browser", 12, 12),
        ("push", (), None, "browser", 5, 5),
        ("push", (), None, "browser_install", 14, 14),
        (
            "pull_request",
            ("tests/test_workbench_browser.py",),
            ("tests/test_workbench_browser.py",),
            "browser",
            5,
            5,
        ),
        ("push", (), None, "serial", 13, 13),
        ("push", (), None, "plan", 17, 17),
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
        pytest.skip("PowerShell is required to exercise the CI steps")

    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text("utf-8"))
    steps = [
        next(
            step for step in workflow["jobs"][job]["steps"] if step.get("name") == name
        )
        for job, name in (
            ("plan", "Plan test coverage"),
            ("windows-integration", "Test"),
            ("python-tests", "Test"),
            ("browser", "Browser test"),
        )
    ]
    monkeypatch.setattr(test_selection, "changed_paths", lambda *_args: list(changed))
    base = "0" * 40 if event == "push" else "base"
    coverage = test_selection.ci_plan(base, "head", event=event)
    assert coverage is not None
    selection = coverage.tests.marker_expression
    coverage = CIPlan(
        tests=SelectionPlan(files=files, marker_expression=selection),
        browser=coverage.browser,
    )
    for step in steps:
        for name, value in step.get("env", {}).items():
            monkeypatch.setenv(name, str(value))
    calls_path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("XIC_CI_TEST_CALLS", str(calls_path))
    monkeypatch.setenv("XIC_CI_TEST_PLAN", json.dumps(asdict(coverage)))
    monkeypatch.setenv("XIC_CI_BASE", base)
    monkeypatch.setenv("XIC_CI_HEAD", "head")
    monkeypatch.setenv("XIC_CI_EVENT", event)
    monkeypatch.setenv("XIC_CI_TEST_FAILED_PHASE", failed_phase)
    monkeypatch.setenv("XIC_CI_TEST_FAILED_CODE", str(failed_code))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.txt"))
    output_path = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    results = []
    for index, step in enumerate(steps):
        if index:
            outputs = dict(
                line.split("=", 1)
                for line in output_path.read_text("utf-8-sig").splitlines()
            )
            monkeypatch.setenv("XIC_CI_PLAN", outputs["coverage"])
        if step.get("name") == "Browser test":
            assert workflow["jobs"]["browser"]["if"] == (
                "${{ needs.plan.outputs.browser_required == 'true' }}"
            )
            if outputs["browser_required"] != "true":
                assert "NOT RUN" in (tmp_path / "summary.txt").read_text("utf-8-sig")
                continue

        actual_script = _CI_COMMAND_FAKES + _render_ci_step(step["run"])
        script_path = tmp_path / f"ci-step-{index}.ps1"
        script_path.write_text(actual_script, encoding="utf-8")
        results.append(
            subprocess.run(
                [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script_path)],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
        )
        if index == 0 and results[-1].returncode:
            break
    assert next((r.returncode for r in results if r.returncode), 0) == expected_exit, (
        "\n".join(r.stdout + r.stderr for r in results)
    )
    calls = [
        json.loads(line) for line in calls_path.read_text("utf-8-sig").splitlines()
    ]
    pytest_calls = [call for call in calls if call[:2] == ["-m", "pytest"]]
    assert (
        sum(call[:2] == ["-m", "tools.testing.test_selection"] for call in calls) == 1
    )
    if failed_phase == "plan":
        assert not pytest_calls
        return
    runs_selection = files is None or len(files) > 0
    browser_selected = coverage.browser is not None
    packaging_selected = "not packaging_build" not in selection
    browser_ran = browser_selected and failed_phase != "browser_install"
    assert len(pytest_calls) == 2 * runs_selection + browser_ran + packaging_selected
    assert any(call[:2] == ["-m", "playwright"] for call in calls) == browser_selected
    if packaging_selected:
        serial = next(call for call in pytest_calls if "-n" not in call)
        assert sum("tests/test_pyinstaller_spec.py::" in arg for arg in serial) == 3
    if not runs_selection:
        return

    ordinary_calls = [
        call
        for call in pytest_calls
        if "-m" in call[2:] and "and browser_e2e" not in call[call.index("-m", 2) + 1]
    ]
    assert len(ordinary_calls) == 2
    selected_files = list(files or ())
    for call, workers in zip(ordinary_calls, ("0", "4"), strict=True):
        assert call[call.index("-n") + 1] == workers
        assert call[len(call) - len(selected_files) :] == selected_files
    linux_call = ordinary_calls[1]
    assert linux_call[linux_call.index("-p") + 1] == "tools.testing.test_sharding"
    assert linux_call[linux_call.index("--test-shard") + 1] == "1/2"
    assert linux_call[linux_call.index("--dist") + 1] == "worksteal"
    windows_expression, linux_expression = (
        Expression.compile(call[call.index("-m", 2) + 1]) for call in ordinary_calls
    )
    browser_expression = None
    if browser_ran:
        browser = next(
            call
            for call in pytest_calls
            if "-m" in call[2:] and "and browser_e2e" in call[call.index("-m", 2) + 1]
        )
        assert browser[browser.index("-p") + 1] == "tools.testing.test_sharding"
        assert browser[browser.index("--test-shard") + 1] == "1/2"
        assert coverage.browser is not None and coverage.browser.files
        browser_files = list(coverage.browser.files)
        assert browser[-len(browser_files) :] == browser_files
        browser_expression = Expression.compile(browser[browser.index("-m", 2) + 1])

    selected_expression = Expression.compile(selection)
    markers = (
        "browser_e2e",
        "packaging_build",
        "current_repository_census",
        "serial_ci",
        "windows_only",
        "requires_dotnet",
    )
    for flags in itertools.product((False, True), repeat=len(markers)):
        marked = {
            marker for marker, enabled in zip(markers, flags, strict=True) if enabled
        }
        in_windows = windows_expression.evaluate(marked.__contains__)
        in_linux = linux_expression.evaluate(marked.__contains__)
        in_browser = (
            browser_expression.evaluate(marked.__contains__)
            if browser_expression is not None
            else False
        )
        if failed_phase != "browser_install":
            assert int(in_windows) + int(in_linux) + int(in_browser) == int(
                selected_expression.evaluate(marked.__contains__)
            ), marked
        assert not (in_linux and "windows_only" in marked)
        assert not (in_windows and "windows_only" not in marked)
        assert not ((in_windows or in_linux) and "browser_e2e" in marked)
        assert not (in_browser and "browser_e2e" not in marked)


@pytest.mark.ci_workflow
@pytest.mark.parametrize(
    ("before", "changed", "required"),
    [
        ("before", ("README.md",), "false"),
        ("before", ("xic_workbench/server.py",), "true"),
        ("before", ("tests/test_delta_mass_review_browser.py",), "true"),
        ("0" * 40, ("README.md",), "true"),
    ],
)
def test_push_browser_lane_follows_the_pushed_range(
    tmp_path, monkeypatch, before, changed, required
):
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is required to exercise the CI steps")
    jobs = yaml.safe_load(Path(".github/workflows/ci.yml").read_text("utf-8"))["jobs"]
    step = next(
        s for s in jobs["plan"]["steps"] if s.get("name") == "Plan test coverage"
    )
    monkeypatch.setattr(test_selection, "changed_paths", lambda *_args: list(changed))
    coverage = test_selection.ci_plan(before, "after", event="push")
    assert coverage is not None
    assert coverage.tests.files is None
    assert coverage.tests.marker_expression == test_selection.CI_BASE_MARKER_EXPRESSION
    calls_path = tmp_path / "calls.jsonl"
    output_path = tmp_path / "outputs.txt"
    monkeypatch.setenv("XIC_CI_TEST_CALLS", str(calls_path))
    monkeypatch.setenv("XIC_CI_TEST_PLAN", json.dumps(asdict(coverage)))
    monkeypatch.setenv("XIC_CI_BASE", before)
    monkeypatch.setenv("XIC_CI_HEAD", "after")
    monkeypatch.setenv("XIC_CI_EVENT", "push")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.txt"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    script_path = tmp_path / "plan.ps1"
    script_path.write_text(
        _CI_COMMAND_FAKES + _render_ci_step(step["run"]),
        encoding="utf-8",
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = dict(
        line.split("=", 1) for line in output_path.read_text("utf-8-sig").splitlines()
    )
    assert outputs["browser_required"] == required
    assert json.loads(outputs["coverage"]) == json.loads(json.dumps(asdict(coverage)))
    calls = calls_path.read_text("utf-8-sig").splitlines()
    assert len(calls) == 1
    call = json.loads(calls[0])
    assert call[call.index("--base") + 1 : call.index("--base") + 4] == [
        before,
        "--head",
        "after",
    ]
    assert call[call.index("--ci-event") + 1] == "push"


@pytest.mark.ci_workflow
@pytest.mark.parametrize("browser_required", [True, False])
@pytest.mark.parametrize(
    "failed_lane,result",
    [(None, "success")]
    + [
        (lane, result)
        for lane in ("lint", "plan", "python-tests", "browser", "windows-integration")
        for result in ("failure", "cancelled", "skipped")
    ],
)
def test_ci_required_check_accepts_only_success_or_planned_browser_skip(
    tmp_path, monkeypatch, failed_lane, result, browser_required
):
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is required to exercise the Windows CI gate")
    jobs = yaml.safe_load(Path(".github/workflows/ci.yml").read_text("utf-8"))["jobs"]
    gate = jobs["test"]
    assert gate["name"] == "test gate"
    assert set(gate["needs"]) == {
        "lint",
        "plan",
        "python-tests",
        "browser",
        "windows-integration",
    }
    assert gate["if"] == "${{ always() }}"
    assert "needs" not in jobs["plan"]
    assert "needs" not in jobs["lint"]
    assert jobs["browser"]["runs-on"] == "windows-latest"
    for lane in ("python-tests", "browser", "windows-integration"):
        assert jobs[lane]["needs"] == "plan"
    assert (
        jobs["browser"]["if"] == "${{ needs.plan.outputs.browser_required == 'true' }}"
    )
    assert jobs["browser"]["strategy"]["matrix"]["shard"] == [1, 2, 3, 4, 5]
    assert jobs["browser"]["strategy"]["fail-fast"] is False
    lane_results = {
        lane: {"result": result if lane == failed_lane else "success"}
        for lane in gate["needs"]
    }
    lane_results["plan"]["outputs"] = {
        "browser_required": str(browser_required).lower()
    }
    if not browser_required and failed_lane != "browser":
        lane_results["browser"]["result"] = "skipped"
    monkeypatch.setenv(
        "TEST_LANE_RESULTS",
        json.dumps(lane_results),
    )
    step = gate["steps"][0]
    assert step["env"]["TEST_LANE_RESULTS"] == "${{ toJSON(needs) }}"
    script_path = tmp_path / "gate.ps1"
    script_path.write_text(step["run"], encoding="utf-8")
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    expected_success = failed_lane is None or (
        not browser_required and failed_lane == "browser" and result == "skipped"
    )
    assert (completed.returncode == 0) == expected_success, (
        completed.stdout + completed.stderr
    )


def test_known_empty_diff_does_not_scan_a_repository(tmp_path):
    plan = test_selection.plan_for_change(
        [], root=tmp_path, packages=("not_a_package",), base_expression=""
    )
    assert plan.files == ()
