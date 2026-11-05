import importlib
import math
import sys
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

    from xic_extractor.alignment.matrix import AlignedCell

    hints = get_type_hints(AlignedCell)

    assert "selected_integration" in hints
    assert "xic_extractor.raw_reader" not in set(sys.modules) - before


def test_default_config_matches_alignment_contract():
    from xic_extractor.alignment import AlignmentConfig

    config = AlignmentConfig()

    assert config.preferred_ppm == 20.0
    assert config.max_ppm == 50.0
    assert config.preferred_rt_sec == 60.0
    assert config.max_rt_sec == 180.0
    assert config.product_mz_tolerance_ppm == 20.0
    assert config.observed_loss_tolerance_ppm == 20.0
    assert config.min_peak_shape_similarity == 0.80
    assert config.anchor_min_evidence_score == 60
    assert config.anchor_min_seed_events == 2
    assert config.rt_unit == "min"
    assert config.fragmentation_model == "cid_nl"


def test_alignment_config_owner_gate_defaults_are_explicit():
    from xic_extractor.alignment import AlignmentConfig

    config = AlignmentConfig()

    assert config.owner_apex_close_sec == 2.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_apex_close_sec", 0.0),
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
        {"min_peak_shape_similarity": -0.1},
        {"min_peak_shape_similarity": 1.1},
        {"min_peak_shape_similarity": math.nan},
        {"min_peak_shape_similarity": True},
    ],
)
def test_invalid_tolerance_windows_are_rejected(kwargs):
    from xic_extractor.alignment import AlignmentConfig

    with pytest.raises(ValueError):
        AlignmentConfig(**kwargs)


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
        {"rt_unit": "sec"},
        {"fragmentation_model": "hcd"},
    ],
)
def test_invalid_anchor_and_fixed_fields_are_rejected(kwargs):
    from xic_extractor.alignment import AlignmentConfig

    with pytest.raises(ValueError):
        AlignmentConfig(**kwargs)
