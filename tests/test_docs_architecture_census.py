from __future__ import annotations

import json
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
import importlib
from importlib import import_module
importlib.import_module("tools.diagnostics.dynamic")
import_module("scripts.run_alignment")
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
