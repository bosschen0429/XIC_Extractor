"""AST boundary tests for Pt pattern Discovery modules (Task 1 RED phase).

The Pt pattern (Delta Mass) Discovery lane must sit beside the existing
neutral-loss Discovery lane without touching Alignment, Backfill,
ProductWriter, or the workbook Matrix. This file proves (via static AST
parsing, not import) that the Pt pattern modules never import
``xic_extractor.alignment``, ``xic_extractor.output``, ``scripts.csv_to_excel``,
or ``scripts.run_alignment``.

These tests read source files directly with ``Path.read_text`` rather than
importing the modules, so they fail with ``FileNotFoundError`` (not
``ImportError``) until Tasks 2, 3, and 5 create the real module files. That is
the expected RED-phase failure for this file.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_IMPORT_ROOTS = (
    "xic_extractor.alignment",
    "xic_extractor.output",
    "scripts.csv_to_excel",
    "scripts.run_alignment",
)

PT_PATTERN_MODULE_PATHS = (
    REPO_ROOT / "xic_extractor" / "discovery" / "detected_features.py",
    REPO_ROOT / "xic_extractor" / "discovery" / "delta_mass_patterns.py",
    REPO_ROOT / "xic_extractor" / "discovery" / "delta_mass_csv.py",
    REPO_ROOT / "xic_extractor" / "discovery" / "delta_mass_pipeline.py",
    REPO_ROOT / "scripts" / "run_pt_pattern_discovery.py",
)


def _imported_module_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.module is None:
            return []
        names = [node.module]
        names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        return names
    return []


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        imported_name
        for node in ast.walk(tree)
        for imported_name in _imported_module_names(node)
        if imported_name.startswith(FORBIDDEN_IMPORT_ROOTS)
    ]


def test_detected_features_module_does_not_import_forbidden_roots() -> None:
    path = REPO_ROOT / "xic_extractor" / "discovery" / "detected_features.py"
    assert _violations(path) == []


def test_delta_mass_patterns_module_does_not_import_forbidden_roots() -> None:
    path = REPO_ROOT / "xic_extractor" / "discovery" / "delta_mass_patterns.py"
    assert _violations(path) == []


def test_delta_mass_csv_module_does_not_import_forbidden_roots() -> None:
    path = REPO_ROOT / "xic_extractor" / "discovery" / "delta_mass_csv.py"
    assert _violations(path) == []


def test_delta_mass_pipeline_module_does_not_import_forbidden_roots() -> None:
    path = REPO_ROOT / "xic_extractor" / "discovery" / "delta_mass_pipeline.py"
    assert _violations(path) == []


def test_run_pt_pattern_discovery_cli_does_not_import_forbidden_roots() -> None:
    path = REPO_ROOT / "scripts" / "run_pt_pattern_discovery.py"
    assert _violations(path) == []


def test_all_pt_pattern_modules_are_free_of_forbidden_imports() -> None:
    all_violations = {
        str(path): _violations(path) for path in PT_PATTERN_MODULE_PATHS
    }
    assert all(not violations for violations in all_violations.values()), (
        all_violations
    )
