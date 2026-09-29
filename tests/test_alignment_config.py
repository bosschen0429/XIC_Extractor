import ast
import importlib
import math
import sys
from pathlib import Path
from typing import get_type_hints

import pytest


def test_alignment_public_import_avoids_pipeline_and_raw_reader():
    before = set(sys.modules)

    importlib.import_module("xic_extractor.alignment")

    newly_imported = set(sys.modules) - before
    assert "xic_extractor.discovery.pipeline" not in newly_imported
    assert "xic_extractor.extraction" not in newly_imported
    assert "xic_extractor.extractor" not in newly_imported
    assert "xic_extractor.raw_reader" not in newly_imported


def test_alignment_public_type_hints_resolve_without_raw_reader_import():
    before = set(sys.modules)

    from xic_extractor.alignment import AlignedCell

    hints = get_type_hints(AlignedCell)

    assert "selected_integration" in hints
    assert "xic_extractor.raw_reader" not in set(sys.modules) - before


def test_alignment_modules_do_not_import_pipeline_or_io_boundaries():
    alignment_dir = Path(__file__).parents[1] / "xic_extractor" / "alignment"
    allowed_composition_imports = {
        (
            "pipeline.py",
            "xic_extractor.extraction.untargeted_alignment",
        ),
        (
            "pipeline.py",
            "xic_extractor.extraction.untargeted_alignment."
            "complete_untargeted_alignment",
        ),
        (
            "pipeline.py",
            "xic_extractor.extraction.untargeted_alignment."
            "complete_untargeted_cid_nl_alignment",
        ),
        (
            "pipeline.py",
            "xic_extractor.extraction.untargeted_production_batch",
        ),
        (
            "pipeline.py",
            "xic_extractor.extraction.untargeted_production_batch."
            "is_supported_alignment_discovery",
        ),
        (
            "pipeline.py",
            "xic_extractor.extraction.untargeted_cid_nl_alignment",
        ),
        (
            "pipeline.py",
            "xic_extractor.extraction.untargeted_cid_nl_alignment."
            "complete_untargeted_cid_nl_alignment",
        ),
        (
            "pipeline.py",
            "xic_extractor.extraction.untargeted_cid_nl_production_batch",
        ),
        (
            "pipeline.py",
            "xic_extractor.extraction.untargeted_cid_nl_production_batch."
            "is_supported_alignment_discovery",
        ),
    }
    banned_roots = (
        "gui",
        "scripts",
        "xic_extractor.discovery.pipeline",
        "xic_extractor.discovery.csv_writer",
        "xic_extractor.diagnostics.diagnostic_io",
        "xic_extractor.extraction",
        "xic_extractor.extractor",
        "xic_extractor.output",
    )

    violations = []
    for path in alignment_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for imported_name in _imported_module_names(node):
                raw_reader_allowed = path.name in {
                    "pipeline.py",
                    "process_backend.py",
                }
                if (
                    imported_name.startswith("xic_extractor.raw_reader")
                    and not raw_reader_allowed
                ):
                    violations.append((path.name, imported_name))
                elif (
                    imported_name.startswith(banned_roots)
                    and (
                        path.name,
                        imported_name,
                    )
                    not in allowed_composition_imports
                ):
                    violations.append((path.name, imported_name))

    assert violations == []


def test_import_boundary_detects_package_attribute_imports():
    tree = ast.parse(
        "from xic_extractor import extraction, extractor\n"
        "from xic_extractor import raw_reader\n"
    )
    imported_names = [
        imported_name
        for node in ast.walk(tree)
        for imported_name in _imported_module_names(node)
    ]

    assert "xic_extractor.extraction" in imported_names
    assert "xic_extractor.extractor" in imported_names
    assert "xic_extractor.raw_reader" in imported_names


def _imported_module_names(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        imported_names = [node.module]
        imported_names.extend(
            f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*"
        )
        return imported_names
    return []


def test_default_config_matches_alignment_contract():
    from xic_extractor.alignment import AlignmentConfig

    config = AlignmentConfig()

    assert config.preferred_ppm == 20.0
    assert config.max_ppm == 50.0
    assert config.preferred_rt_sec == 60.0
    assert config.max_rt_sec == 180.0
    assert config.product_mz_tolerance_ppm == 20.0
    assert config.observed_loss_tolerance_ppm == 20.0
    assert config.mz_bucket_neighbor_radius == 2
    assert config.anchor_priorities == ("HIGH",)
    assert config.anchor_min_evidence_score == 60
    assert config.anchor_min_seed_events == 2
    assert config.anchor_min_scan_support_score == 0.5
    assert config.rt_unit == "min"
    assert config.fragmentation_model == "cid_nl"


def test_alignment_config_duplicate_fold_defaults_are_conservative():
    from xic_extractor.alignment import AlignmentConfig

    config = AlignmentConfig()

    assert config.duplicate_fold_ppm == 5.0
    assert config.duplicate_fold_rt_sec == 2.0
    assert config.duplicate_fold_product_ppm == 10.0
    assert config.duplicate_fold_observed_loss_ppm == 10.0
    assert config.duplicate_fold_min_detected_overlap == 0.80
    assert config.duplicate_fold_min_shared_detected_count == 3
    assert config.duplicate_fold_min_detected_jaccard == 0.60
    assert config.duplicate_fold_min_present_overlap == 0.80


def test_alignment_config_owner_gate_defaults_are_explicit():
    from xic_extractor.alignment import AlignmentConfig

    config = AlignmentConfig()

    assert config.owner_window_overlap_fraction == 0.50
    assert config.owner_apex_close_sec == 2.0
    assert config.owner_tail_seed_guard_sec == 30.0
    assert config.owner_tail_max_secondary_ratio == 0.30
    assert config.owner_backfill_min_detected_samples == 1
    assert config.identity_rt_candidate_window_sec == 180.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_window_overlap_fraction", -0.01),
        ("owner_window_overlap_fraction", 1.01),
        ("owner_apex_close_sec", 0.0),
        ("owner_tail_seed_guard_sec", 0.0),
        ("owner_tail_max_secondary_ratio", -0.01),
        ("owner_tail_max_secondary_ratio", 1.01),
        ("owner_backfill_min_detected_samples", 0),
        ("owner_backfill_min_detected_samples", 1.5),
        ("owner_backfill_min_detected_samples", True),
        ("identity_rt_candidate_window_sec", 0.0),
    ],
)
def test_alignment_config_owner_gate_validation(field, value):
    from xic_extractor.alignment import AlignmentConfig

    with pytest.raises(ValueError, match=field):
        AlignmentConfig(**{field: value})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"preferred_ppm": 0.0},
        {"max_ppm": -1.0},
        {"preferred_ppm": 51.0, "max_ppm": 50.0},
        {"preferred_rt_sec": 0.0},
        {"max_rt_sec": -1.0},
        {"preferred_rt_sec": 181.0, "max_rt_sec": 180.0},
        {"product_mz_tolerance_ppm": 0.0},
        {"observed_loss_tolerance_ppm": -1.0},
    ],
)
def test_invalid_tolerance_windows_are_rejected(kwargs):
    from xic_extractor.alignment import AlignmentConfig

    with pytest.raises(ValueError):
        AlignmentConfig(**kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duplicate_fold_ppm", 0.0),
        ("duplicate_fold_rt_sec", 0.0),
        ("duplicate_fold_product_ppm", 0.0),
        ("duplicate_fold_observed_loss_ppm", 0.0),
        ("duplicate_fold_min_detected_overlap", -0.1),
        ("duplicate_fold_min_detected_overlap", 1.1),
        ("duplicate_fold_min_shared_detected_count", 0),
        ("duplicate_fold_min_detected_jaccard", -0.1),
        ("duplicate_fold_min_detected_jaccard", 1.1),
        ("duplicate_fold_min_present_overlap", -0.1),
        ("duplicate_fold_min_present_overlap", 1.1),
    ],
)
def test_alignment_config_rejects_invalid_duplicate_fold_values(field, value):
    from xic_extractor.alignment import AlignmentConfig

    with pytest.raises(ValueError, match=field):
        AlignmentConfig(**{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "preferred_ppm",
        "max_ppm",
        "preferred_rt_sec",
        "max_rt_sec",
        "product_mz_tolerance_ppm",
        "observed_loss_tolerance_ppm",
    ],
)
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_tolerance_windows_are_rejected(field, value):
    from xic_extractor.alignment import AlignmentConfig

    with pytest.raises(ValueError):
        AlignmentConfig(**{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "preferred_ppm",
        "max_ppm",
        "preferred_rt_sec",
        "max_rt_sec",
        "product_mz_tolerance_ppm",
        "observed_loss_tolerance_ppm",
    ],
)
def test_bool_tolerance_windows_are_rejected(field):
    from xic_extractor.alignment import AlignmentConfig

    with pytest.raises(ValueError):
        AlignmentConfig(**{field: True})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"anchor_priorities": ()},
        {"anchor_priorities": ["HIGH"]},
        {"anchor_priorities": "HIGH"},
        {"anchor_priorities": ("BAD",)},
        {"anchor_priorities": ("HIGH", "BAD")},
        {"anchor_priorities": ([],)},
        {"anchor_min_evidence_score": -1},
        {"anchor_min_evidence_score": 101},
        {"anchor_min_evidence_score": True},
        {"anchor_min_evidence_score": 60.5},
        {"anchor_min_evidence_score": math.nan},
        {"anchor_min_evidence_score": math.inf},
        {"anchor_min_seed_events": 0},
        {"anchor_min_seed_events": math.nan},
        {"anchor_min_seed_events": math.inf},
        {"anchor_min_seed_events": 1.5},
        {"anchor_min_seed_events": True},
        {"anchor_min_scan_support_score": -0.1},
        {"anchor_min_scan_support_score": 1.1},
        {"anchor_min_scan_support_score": True},
        {"anchor_min_scan_support_score": math.nan},
        {"anchor_min_scan_support_score": math.inf},
        {"anchor_min_scan_support_score": "0.5"},
        {"mz_bucket_neighbor_radius": 0},
        {"mz_bucket_neighbor_radius": -1},
        {"mz_bucket_neighbor_radius": math.nan},
        {"mz_bucket_neighbor_radius": math.inf},
        {"mz_bucket_neighbor_radius": 1.5},
        {"mz_bucket_neighbor_radius": True},
        {"rt_unit": "sec"},
        {"fragmentation_model": "hcd"},
    ],
)
def test_invalid_anchor_and_fixed_fields_are_rejected(kwargs):
    from xic_extractor.alignment import AlignmentConfig

    with pytest.raises(ValueError):
        AlignmentConfig(**kwargs)
