from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pytest

from scripts import check_architecture_census as census

ROOT = Path(__file__).resolve().parents[1]


def test_current_architecture_census_is_complete_and_valid() -> None:
    observed = census.build_architecture_census(ROOT)

    assert census.check_architecture_census(ROOT, census=observed) == []
    assert set(observed.serializable()) == set(census.CENSUS_OWNER_REFS)
    assert all(observed.serializable().values())
    assert not {
        "productization_lanes",
        "authority_scopes",
        "retained_artifacts",
    } & set(observed.serializable())


def test_import_scanner_resolves_static_and_literal_dynamic_imports() -> None:
    source = """
import tools.diagnostics.example
from xic_extractor import output
from . import models
from ..peak_detection import hypotheses
import importlib as il
from importlib import import_module as im
il.import_module("tools.diagnostics.dynamic")
im("scripts.run_alignment")
__import__("gui.main")
"""

    imports = census.scan_imports_from_source(
        source,
        source_module="xic_extractor.alignment.example",
        source_path=Path("xic_extractor/alignment/example.py"),
    )

    assert {
        "tools.diagnostics.example",
        "xic_extractor.output",
        "xic_extractor.alignment.models",
        "xic_extractor.peak_detection.hypotheses",
        "tools.diagnostics.dynamic",
        "scripts.run_alignment",
        "gui.main",
    } <= {reference.imported_module for reference in imports}


def test_dependency_gate_rejects_aliased_literal_dynamic_import(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "xic_extractor" / "peak_detection" / "bad.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        'import importlib as il\nil.import_module("tools.diagnostics.helper")\n',
        encoding="utf-8",
    )

    problems = census.check_dependency_rules(
        tmp_path,
        python_paths=(Path("xic_extractor/peak_detection/bad.py"),),
    )

    assert any("domain_no_outer_layer" in problem for problem in problems)
    assert any("forbidden dependency tools" in problem for problem in problems)


def test_dependency_gate_rejects_forbidden_outer_layer_import(tmp_path: Path) -> None:
    source_path = tmp_path / "xic_extractor" / "peak_detection" / "bad.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("from tools.diagnostics import helper\n", encoding="utf-8")

    problems = census.check_dependency_rules(
        tmp_path,
        python_paths=(Path("xic_extractor/peak_detection/bad.py"),),
    )

    assert any("domain_no_outer_layer" in problem for problem in problems)
    assert any("tools" in problem for problem in problems)


def test_dependency_exception_requires_executable_removal_gate() -> None:
    exception = census.DependencyException(
        rule_id="domain_no_outer_layer",
        source_path="xic_extractor/example.py",
        imported_prefix="tools.diagnostics",
        owner_ref="",
        rationale="",
        removal_gate="issue_closed",
    )

    problems = census.validate_dependency_exceptions((exception,), ROOT)

    assert any("owner_ref" in problem for problem in problems)
    assert any("rationale" in problem for problem in problems)
    assert any("must be executable" in problem for problem in problems)


def test_dependency_gate_rejects_exception_after_import_is_removed(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "xic_extractor" / "example.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("VALUE = 1\n", encoding="utf-8")
    owner = tmp_path / "docs" / "architecture-contract.md"
    owner.parent.mkdir(parents=True)
    owner.write_text("# Architecture Contract\n", encoding="utf-8")
    exception = census.DependencyException(
        rule_id="domain_no_outer_layer",
        source_path="xic_extractor/example.py",
        imported_prefix="tools.diagnostics",
        owner_ref="docs/architecture-contract.md",
        rationale="Temporary migration edge.",
    )

    problems = census.check_dependency_rules(
        tmp_path,
        python_paths=(Path("xic_extractor/example.py"),),
        exceptions=(exception,),
    )

    assert any("stale dependency exception" in problem for problem in problems)


def test_project_layout_package_include_snapshot_matches_pyproject() -> None:
    assert census.check_project_layout_package_snapshot(ROOT) == []


def test_census_owner_refs_use_exact_tracked_path_spelling() -> None:
    assert set(census.CENSUS_OWNER_REFS.values()) <= census.repository_tracked_paths(
        ROOT
    )


def test_pyinstaller_census_includes_globs_and_exe_resources(tmp_path: Path) -> None:
    spec = tmp_path / "app.spec"
    spec.write_text(
        """
preset_datas = collect_data_files(
    "example.presets",
    includes=["data/*.toml"],
)
analysis = Analysis(
    ["gui/main.py"],
    datas=[("assets", "assets")],
    hiddenimports=["example.hidden"],
)
exe = EXE(
    analysis.scripts,
    name="Example",
    icon="assets/app_icon.png",
)
""",
        encoding="utf-8",
    )

    inputs = census._pyinstaller_inputs(spec)

    assert {
        "collect_data_files:example.presets",
        "collect_data_files:data/*.toml",
        "Analysis:gui/main.py",
        "Analysis:assets",
        "Analysis:example.hidden",
        "EXE:Example",
        "EXE:assets/app_icon.png",
    } <= set(inputs)


def test_project_entrypoint_accepts_facades_and_dotted_attrs_statically(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "example"
version = "0"

[project.scripts]
reexported = "reexported:main"
assigned = "assigned:main"
dotted = "dotted:cli.main"
""",
        encoding="utf-8",
    )
    (tmp_path / "reexported.py").write_text(
        "from implementation import main as main\n",
        encoding="utf-8",
    )
    (tmp_path / "assigned.py").write_text(
        "from implementation import main as _main\nmain = _main\n",
        encoding="utf-8",
    )
    (tmp_path / "dotted.py").write_text(
        "from implementation import cli as cli\n",
        encoding="utf-8",
    )
    (tmp_path / "implementation.py").write_text(
        """
def main() -> None:
    return None

class CLI:
    def main(self) -> None:
        return None

cli = CLI()
""",
        encoding="utf-8",
    )

    assert census._check_project_entrypoint_declarations(tmp_path) == []


def test_project_entrypoint_validation_does_not_import_runtime_module(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "example"
version = "0"

[project.scripts]
headless = "headless:main"
""",
        encoding="utf-8",
    )
    (tmp_path / "headless.py").write_text(
        """
raise RuntimeError("optional runtime dependency is unavailable")

def main() -> None:
    return None
""",
        encoding="utf-8",
    )

    assert census._check_project_entrypoint_declarations(tmp_path) == []


def test_project_entrypoint_rejects_missing_top_level_export(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "example"
version = "0"

[project.scripts]
broken = "facade:missing.main"
""",
        encoding="utf-8",
    )
    (tmp_path / "facade.py").write_text("cli = object()\n", encoding="utf-8")

    assert census._check_project_entrypoint_declarations(tmp_path) == [
        "project entrypoint declaration is not statically valid: "
        "broken=facade:missing.main"
    ]


def test_installed_project_entrypoints_load_full_target_chain_and_are_callable(
) -> None:
    scripts = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["scripts"]
    probe = """
import json
import sys
from importlib.metadata import distribution
from pathlib import Path

expected = json.loads(sys.argv[1])
repo_root = Path(sys.argv[2]).resolve()
if Path.cwd().resolve().is_relative_to(repo_root):
    raise RuntimeError("entrypoint probe must run outside the source checkout")
installed = {
    entrypoint.name: entrypoint
    for entrypoint in distribution("xic-extractor").entry_points
    if entrypoint.group == "console_scripts"
}
observed = {name: entrypoint.value for name, entrypoint in installed.items()}
if observed != expected:
    raise RuntimeError(
        f"installed console scripts do not match pyproject.toml: "
        f"expected={expected!r}, observed={observed!r}"
    )

failures = []
for name, entrypoint in installed.items():
    try:
        loaded = entrypoint.load()
        if not callable(loaded):
            failures.append(f"{name}={entrypoint.value}: target is not callable")
    except Exception as exc:
        failures.append(f"{name}={entrypoint.value}: {type(exc).__name__}: {exc}")
if failures:
    raise RuntimeError("\\n".join(failures))
"""

    uv = shutil.which("uv")
    assert uv is not None
    with tempfile.TemporaryDirectory(prefix="xic-installed-entrypoints-") as temp_dir:
        result = subprocess.run(
            [
                uv,
                "run",
                "--no-sync",
                "--project",
                str(ROOT),
                "python",
                "-c",
                probe,
                json.dumps(scripts, sort_keys=True),
                str(ROOT),
            ],
            cwd=temp_dir,
            text=True,
            capture_output=True,
            check=False,
        )

    assert result.returncode == 0, result.stderr or result.stdout


def test_optional_json_census_has_content_identity_and_no_authority_claims(
    tmp_path: Path,
) -> None:
    observed = census.build_architecture_census(ROOT)
    output = tmp_path / "architecture_census.json"

    census.write_architecture_census_json(output, observed, ROOT)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "architecture_census_v1"
    assert payload["claim"] == "derived_disposable_not_authority"
    assert payload["source_identity"]["head"]
    assert len(payload["source_identity"]["census_sha256"]) == 64
    assert isinstance(payload["source_identity"]["worktree_dirty"], bool)
    assert set(payload["census"]) == set(census.CENSUS_OWNER_REFS)


def test_json_census_rejects_nonignored_repo_path() -> None:
    observed = census.build_architecture_census(ROOT)

    with pytest.raises(ValueError, match="outside the repo or git-ignored"):
        census.write_architecture_census_json(
            ROOT / "docs" / "architecture_census.json",
            observed,
            ROOT,
        )
