from __future__ import annotations

import math

from xic_extractor.alignment.cell_quality import (
    build_cell_quality_decisions,
    decide_cell_quality,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.promotion_policy import (
    LOW_MS1_COVERAGE_BLOCKED_REASON,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult


def test_detected_cell_requires_positive_finite_area() -> None:
    config = AlignmentConfig()

    assert (
        decide_cell_quality(_cell("s1", "FAM001", "detected", 100.0), config)
        .quality_status
        == "detected_quantifiable"
    )
    assert (
        decide_cell_quality(_cell("s1", "FAM001", "detected", 0.0), config)
        .quality_status
        == "invalid"
    )
    assert (
        decide_cell_quality(_cell("s1", "FAM001", "detected", math.nan), config)
        .quality_status
        == "invalid"
    )


def test_detected_cell_uses_ms1_morphology_area_when_present() -> None:
    config = AlignmentConfig()
    cell = _cell(
        "s1",
        "FAM001",
        "detected",
        100.0,
        selected_integration=_integration(
            raw_area=250.0,
            asls_area=200.0,
            morphology_area=210.0,
        ),
    )

    decision = decide_cell_quality(cell, config)

    assert decision.quality_status == "detected_quantifiable"
    assert decision.matrix_area == 210.0


def test_missing_ms1_morphology_does_not_fall_back_to_asls_or_raw() -> None:
    config = AlignmentConfig()
    cell = _cell(
        "s1",
        "FAM001",
        "detected",
        100.0,
        selected_integration=_integration(raw_area=250.0, asls_area=None),
    )

    decision = decide_cell_quality(cell, config)

    assert decision.quality_status == "invalid"
    assert decision.quality_reason == "missing_ms1_morphology_area"


def test_backfill_requires_complete_peak_and_rt_inside_alignment_window() -> None:
    config = AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0)

    assert (
        decide_cell_quality(_cell("s1", "FAM001", "backfilled", 100.0), config)
        .quality_status
        == "rescue_quantifiable"
    )
    assert (
        decide_cell_quality(
            _cell("s1", "FAM001", "backfilled", 100.0, apex_rt=None),
            config,
        ).quality_status
        == "review_backfill"
    )
    outside_preferred = decide_cell_quality(
        _cell("s1", "FAM001", "backfilled", 100.0, rt_delta_sec=20.0),
        config,
    )
    assert outside_preferred.quality_status == "review_backfill"
    assert outside_preferred.quality_reason == "rt_outside_preferred"
    outside = decide_cell_quality(
        _cell("s1", "FAM001", "backfilled", 100.0, rt_delta_sec=31.0),
        config,
    )
    assert outside.quality_status == "review_backfill"
    assert outside.quality_reason == "rt_outside_max"


def test_low_scan_support_holds_backfill_out_of_matrix() -> None:
    config = AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0)
    cell = _cell("hard_negative", "FAM001", "backfilled", 100.0, scan_support_score=0.2)

    decision = decide_cell_quality(cell, config)

    assert decision.quality_status == "review_backfill"
    assert decision.quality_reason == LOW_MS1_COVERAGE_BLOCKED_REASON
    assert decision.matrix_area is None


def test_moderate_scan_support_keeps_backfill_quantifiable() -> None:
    cell = _cell("nonblocking", "FAM001", "backfilled", 100.0, scan_support_score=0.3)
    decision = decide_cell_quality(
        cell, AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0)
    )

    assert decision.quality_status == "rescue_quantifiable"
    assert decision.matrix_area == 100.0


def test_backfill_rt_outside_preferred_is_review_only() -> None:
    decision = decide_cell_quality(
        _cell("s1", "FAM001", "backfilled", 100.0, rt_delta_sec=20.0),
        AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0),
    )

    assert decision.quality_status == "review_backfill"
    assert decision.quality_reason == "rt_outside_preferred"


def test_duplicate_and_ambiguous_cells_do_not_support_identity() -> None:
    config = AlignmentConfig()
    decisions = build_cell_quality_decisions(
        (
            _cell("s1", "FAM001", "duplicate_assigned", 100.0),
            _cell("s2", "FAM001", "ambiguous_ms1_owner", None),
        ),
        config,
    )

    assert decisions[("FAM001", "s1")].quality_status == "duplicate_loser"
    assert decisions[("FAM001", "s2")].quality_status == "ambiguous_owner"
    assert not decisions[("FAM001", "s1")].is_quantifiable_cell
    assert not decisions[("FAM001", "s2")].is_detected_identity_support


def _cell(
    sample_stem: str,
    cluster_id: str,
    status: str,
    area: float | None,
    *,
    apex_rt: float | None = 8.5,
    height: float | None = 100.0,
    peak_start_rt: float | None = 8.45,
    peak_end_rt: float | None = 8.55,
    rt_delta_sec: float | None = 0.0,
    selected_integration: IntegrationResult | None = None,
    scan_support_score: float | None = 0.8,
    group_hypothesis_id: str = "",
) -> AlignedCell:
    if (
        selected_integration is None
        and status in {"detected", "backfilled"}
        and _positive_area(area)
    ):
        selected_integration = _integration(
            raw_area=area,
            asls_area=area,
            morphology_area=area,
        )
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=cluster_id,
        status=status,  # type: ignore[arg-type]
        area=area,
        apex_rt=apex_rt,
        height=height,
        peak_start_rt=peak_start_rt,
        peak_end_rt=peak_end_rt,
        rt_delta_sec=rt_delta_sec,
        trace_quality="clean",
        scan_support_score=scan_support_score,
        source_candidate_id=f"{sample_stem}#{cluster_id}",
        reason=status,
        group_hypothesis_id=group_hypothesis_id,
        selected_integration=selected_integration,
    )


def _integration(
    *,
    raw_area: float,
    asls_area: float | None,
    baseline_type: str = "asls",
    morphology_area: float | None = None,
) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=8.45,
        rt_apex_min=8.5,
        rt_right_min=8.55,
        raw_apex_rt_min=8.5,
        rt_width_min=0.1,
        height_raw=100.0,
        height_smoothed=100.0,
        area_raw_counts_seconds=raw_area,
        area_baseline_corrected=asls_area,
        baseline_type=baseline_type,
        boundary_sources=("test",),
        area_ms1_morphology=morphology_area,
        ms1_morphology_area_source=(
            "gaussian15_positive_asls_residual"
            if morphology_area is not None
            else ""
        ),
    )


def _positive_area(value: float | None) -> bool:
    return (
        value is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )
