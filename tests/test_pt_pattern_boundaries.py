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

Task 6 adds, alongside the Task 1 tests above (not duplicating them):

- a static ``.xlsx``/``openpyxl`` write-boundary check (still AST/text based,
  no code execution);
- a ProductWriter / quant-matrix builder import boundary check. There is no
  literal ``class ProductWriter`` or ``ProductWriter`` module anywhere in this
  repo (confirmed by repo-wide search) -- "ProductWriter" is used only as a
  documentation/comment term for the workbook write-authority boundary. The
  real xlsx-writing code lives in ``xic_extractor.output.excel_pipeline`` /
  ``xic_extractor.output.workbook_builder``, and the real quant-matrix
  builders live in ``xic_extractor.alignment.quant_matrix_*``, both already
  covered by the existing ``xic_extractor.output`` / ``xic_extractor.alignment``
  forbidden roots. No new forbidden root was needed.
- a Backfill-terminology-as-detection content scan over the Pt Candidate / Pt
  Review CSV schemas and the fixed enum values used by the matcher/pipeline;
- two matcher regression tests (base-only input, and partial pattern plus a
  synthetic Backfill-flavored row) proving zero candidates are produced.
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
    REPO_ROOT / "xic_extractor" / "discovery" / "pt_pattern_evidence_rule.py",
    REPO_ROOT / "xic_extractor" / "discovery" / "pt_pattern_trace_evidence.py",
    REPO_ROOT / "xic_extractor" / "discovery" / "pt_pattern_review_packet.py",
    REPO_ROOT / "xic_extractor" / "discovery" / "pt_pattern_blind_overlay.py",
    REPO_ROOT / "xic_extractor" / "discovery" / "pt_pattern_oracle_promotion.py",
    REPO_ROOT / "xic_extractor" / "discovery" / "pt_pattern_oracle_refresh.py",
    REPO_ROOT / "xic_extractor" / "discovery" / "pt_pattern_oracle_thresholds.py",
    REPO_ROOT / "xic_extractor" / "discovery" / "pt_pattern_diagnostic_gate.py",
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


def test_pt_pattern_evidence_rule_module_does_not_import_forbidden_roots() -> None:
    path = (
        REPO_ROOT
        / "xic_extractor"
        / "discovery"
        / "pt_pattern_evidence_rule.py"
    )
    assert _violations(path) == []


def test_pt_pattern_trace_evidence_module_does_not_import_forbidden_roots() -> None:
    path = (
        REPO_ROOT
        / "xic_extractor"
        / "discovery"
        / "pt_pattern_trace_evidence.py"
    )
    assert _violations(path) == []


def test_pt_pattern_review_packet_module_does_not_import_forbidden_roots() -> None:
    path = (
        REPO_ROOT
        / "xic_extractor"
        / "discovery"
        / "pt_pattern_review_packet.py"
    )
    assert _violations(path) == []


def test_pt_pattern_blind_overlay_module_does_not_import_forbidden_roots() -> None:
    path = (
        REPO_ROOT
        / "xic_extractor"
        / "discovery"
        / "pt_pattern_blind_overlay.py"
    )
    assert _violations(path) == []


def test_pt_pattern_oracle_promotion_module_does_not_import_forbidden_roots() -> None:
    path = (
        REPO_ROOT
        / "xic_extractor"
        / "discovery"
        / "pt_pattern_oracle_promotion.py"
    )
    assert _violations(path) == []


def test_pt_pattern_oracle_refresh_module_does_not_import_forbidden_roots() -> None:
    path = (
        REPO_ROOT
        / "xic_extractor"
        / "discovery"
        / "pt_pattern_oracle_refresh.py"
    )
    assert _violations(path) == []


def test_pt_pattern_oracle_thresholds_module_does_not_import_forbidden_roots() -> None:
    path = (
        REPO_ROOT
        / "xic_extractor"
        / "discovery"
        / "pt_pattern_oracle_thresholds.py"
    )
    assert _violations(path) == []


def test_pt_pattern_diagnostic_gate_module_does_not_import_forbidden_roots() -> None:
    path = (
        REPO_ROOT
        / "xic_extractor"
        / "discovery"
        / "pt_pattern_diagnostic_gate.py"
    )
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


# ---------------------------------------------------------------------------
# Task 6: no .xlsx writing anywhere in the Pt pattern lane.
# ---------------------------------------------------------------------------

_XLSX_MARKERS = (".xlsx", "openpyxl")


def _xlsx_markers_found(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [marker for marker in _XLSX_MARKERS if marker in text]


def test_no_pt_pattern_module_references_xlsx_or_openpyxl() -> None:
    all_hits = {
        str(path): _xlsx_markers_found(path) for path in PT_PATTERN_MODULE_PATHS
    }
    assert all(not hits for hits in all_hits.values()), all_hits


# ---------------------------------------------------------------------------
# Task 6: no ProductWriter / quant-matrix builder import or call.
#
# There is no literal ``class ProductWriter`` or standalone ``ProductWriter``
# module in this repo (confirmed by a repo-wide search): "ProductWriter" is
# used only as prose in comments/docstrings elsewhere in the codebase to name
# the workbook write-authority boundary. The actual xlsx-writing code is
# ``xic_extractor.output.excel_pipeline`` / ``xic_extractor.output.workbook_builder``,
# and the actual quant-matrix builders are ``xic_extractor.alignment.quant_matrix_*``
# -- both already covered by ``FORBIDDEN_IMPORT_ROOTS`` above. This section
# adds an explicit, separately-named check (rather than silently relying on
# the existing test) so a future rename/relocation of either module is still
# caught under a clearly-labeled ProductWriter/quant-matrix guard.
# ---------------------------------------------------------------------------

PRODUCT_WRITER_AND_QUANT_MATRIX_IMPORT_ROOTS = (
    "xic_extractor.output",
    "xic_extractor.alignment",
)


def _product_writer_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        imported_name
        for node in ast.walk(tree)
        for imported_name in _imported_module_names(node)
        if imported_name.startswith(PRODUCT_WRITER_AND_QUANT_MATRIX_IMPORT_ROOTS)
    ]


def test_no_pt_pattern_module_imports_product_writer_or_quant_matrix_builders() -> (
    None
):
    all_violations = {
        str(path): _product_writer_violations(path)
        for path in PT_PATTERN_MODULE_PATHS
    }
    assert all(not violations for violations in all_violations.values()), (
        all_violations
    )


def test_no_pt_pattern_module_source_mentions_product_writer_call_site() -> None:
    # Belt-and-suspenders text scan: even a dynamically-constructed reference
    # (e.g. getattr/importlib string) to "ProductWriter" would show up as a
    # literal substring in source text, which the AST-only checks above would
    # miss.
    all_hits = {
        str(path): "ProductWriter" in path.read_text(encoding="utf-8")
        for path in PT_PATTERN_MODULE_PATHS
    }
    assert not any(all_hits.values()), all_hits


# ---------------------------------------------------------------------------
# Task 6: no Backfill terminology used as detection (output content check).
# ---------------------------------------------------------------------------


def test_no_pt_pattern_module_source_contains_backfill_terminology() -> None:
    """Backfill is a separate lane; Pt pattern CSV schemas/enums must not

    reference it. A case-insensitive substring scan across the Pt
    pattern module files (which is where ``PT_CANDIDATE_COLUMNS``,
    ``PT_REVIEW_COLUMNS``, and every fixed ``candidate_state`` /
    ``cid_nl_base_status`` / ``local_signal_support_status`` enum value used
    by the matcher/pipeline live) is a simple, sufficient implementation.
    """

    all_hits = {
        str(path): "backfill" in path.read_text(encoding="utf-8").lower()
        for path in PT_PATTERN_MODULE_PATHS
    }
    assert not any(all_hits.values()), all_hits


# ---------------------------------------------------------------------------
# Task 6: regression tests on the matcher itself.
# ---------------------------------------------------------------------------


def _boundary_feature(
    *,
    feature_id: str,
    mz: float,
    rt_apex_min: float,
    sample_stem: str = "Sample_1",
    height: float = 1000.0,
    area: float | None = 500.0,
    trace_quality: str = "GOOD",
    shape_score: float | None = 0.90,
    source: str = "targeted",
    source_feature_row_id: str | None = None,
):
    from xic_extractor.discovery.detected_features import DetectedFeature

    return DetectedFeature(
        sample_stem=sample_stem,
        feature_id=feature_id,
        source_feature_row_id=source_feature_row_id,
        mz=mz,
        rt_apex_min=rt_apex_min,
        rt_start_min=rt_apex_min - 0.05,
        rt_end_min=rt_apex_min + 0.05,
        height=height,
        area=area,
        trace_quality=trace_quality,
        shape_score=shape_score,
        source=source,
    )


def test_base_only_input_creates_zero_candidates() -> None:
    """A Detected Features list with only the base position (no plus_1,

    plus_2, or plus_4 at all -- the extreme 1-of-4 case, more extreme than
    the existing 3-of-4 ``test_missing_plus_4_position_yields_no_candidate``
    in ``tests/test_delta_mass_patterns.py``) must produce zero candidates.
    """

    from xic_extractor.discovery.delta_mass_patterns import match_delta_mass_pattern

    base_mz = 500.0
    features = [
        _boundary_feature(feature_id="base", mz=base_mz, rt_apex_min=10.000),
    ]

    candidates = match_delta_mass_pattern(features)

    assert candidates == ()


def test_partial_pattern_with_synthetic_backfill_like_row_yields_zero_candidates() -> (
    None
):
    """A partial pattern (base + pt_plus_1 + pt_plus_2, missing pt_plus_4)

    plus one extra synthetic feature whose ``source``/``feature_id`` looks
    Backfill-derived must still yield zero candidates: the matcher has no
    special-case logic that lets a Backfill-flavored row count toward pattern
    completion, and this synthetic row's m/z does not fall in the pt_plus_4
    expected window, so it cannot accidentally complete the pattern anyway.
    """

    from xic_extractor.discovery.delta_mass_patterns import match_delta_mass_pattern

    base_mz = 500.0
    features = [
        _boundary_feature(feature_id="base", mz=base_mz, rt_apex_min=10.000),
        _boundary_feature(
            feature_id="plus1", mz=base_mz + 1.0021, rt_apex_min=10.000
        ),
        _boundary_feature(
            feature_id="plus2", mz=base_mz + 2.0023, rt_apex_min=10.000
        ),
        # Synthetic Backfill-like row: far from the pt_plus_4 expected m/z
        # window and explicitly tagged as Backfill-sourced, so it must not be
        # treated as a legitimate pt_plus_4 position match.
        _boundary_feature(
            feature_id="backfill_owner_row_3015",
            mz=base_mz + 10.0,
            rt_apex_min=10.000,
            source="backfill",
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert candidates == ()
