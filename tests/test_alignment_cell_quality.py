from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import pytest

from xic_extractor.alignment.cell_quality import (
    build_cell_quality_decisions,
    decide_cell_quality,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.promotion_policy import (
    ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
    BACKFILL_HYPOTHESIS_BLOCKED_REASON,
    LOW_MS1_COVERAGE_BLOCKED_REASON,
    NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
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


def test_rescue_requires_complete_peak_and_rt_inside_alignment_window() -> None:
    config = AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0)

    assert (
        decide_cell_quality(_cell("s1", "FAM001", "rescued", 100.0), config)
        .quality_status
        == "rescue_quantifiable"
    )
    assert (
        decide_cell_quality(
            _cell("s1", "FAM001", "rescued", 100.0, apex_rt=None),
            config,
        ).quality_status
        == "review_rescue"
    )
    outside_preferred = decide_cell_quality(
        _cell("s1", "FAM001", "rescued", 100.0, rt_delta_sec=20.0),
        config,
    )
    assert outside_preferred.quality_status == "review_rescue"
    assert outside_preferred.quality_reason == "rt_outside_preferred"
    outside = decide_cell_quality(
        _cell("s1", "FAM001", "rescued", 100.0, rt_delta_sec=31.0),
        config,
    )
    assert outside.quality_status == "review_rescue"
    assert outside.quality_reason == "rt_outside_max"


@pytest.mark.parametrize(
    ("cell_factory", "expected_reason"),
    (
        (
            lambda: _cell(
                "hard_negative",
                "FAM001",
                "rescued",
                100.0,
                group_hypothesis_id="HYP001",
                group_claim_state="duplicate_loser",
                claim_winner_group_hypothesis_id="HYP002",
            ),
            BACKFILL_HYPOTHESIS_BLOCKED_REASON,
        ),
        (
            lambda: _cell(
                "hard_negative",
                "FAM001",
                "rescued",
                100.0,
                region_local_mixture_diagnostic="ambiguous_adjacent_split",
            ),
            NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
        ),
        (
            lambda: _cell(
                "hard_negative",
                "FAM001",
                "rescued",
                100.0,
                region_local_mixture_diagnostic="two_peak_supported",
            ),
            NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
        ),
        (
            lambda: _cell(
                "hard_negative",
                "FAM001",
                "rescued",
                100.0,
                region_local_mixture_diagnostic="competing_boundary_model",
            ),
            NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
        ),
        (
            lambda: _cell(
                "hard_negative",
                "FAM001",
                "rescued",
                100.0,
                scan_support_score=0.2,
            ),
            LOW_MS1_COVERAGE_BLOCKED_REASON,
        ),
    ),
    ids=(
        "conflicting-claim",
        "ambiguous-adjacent-split",
        "two-peaks-supported",
        "competing-boundary-model",
        "low-ms1-coverage",
    ),
)
def test_owner_hard_negative_backfill_facts_hold_rescue_out_of_matrix(
    cell_factory: Callable[[], AlignedCell],
    expected_reason: str,
) -> None:
    config = AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0)
    decision = decide_cell_quality(cell_factory(), config)

    assert decision.quality_status == "review_rescue"
    assert decision.quality_reason == expected_reason
    assert decision.matrix_area is None


@pytest.mark.parametrize(
    "cell_factory",
    (
        lambda: _cell(
            "nonblocking",
            "FAM001",
            "rescued",
            100.0,
            scan_support_score=0.3,
        ),
        lambda: _cell(
            "nonblocking",
            "FAM001",
            "rescued",
            100.0,
            region_local_mixture_diagnostic=(
                "neighbor_interference_not_assessed;raw_trace_reread_v0_1"
            ),
        ),
        lambda: _cell(
            "nonblocking",
            "FAM001",
            "rescued",
            100.0,
            region_local_mixture_diagnostic="current_single_envelope",
        ),
        lambda: _cell(
            "nonblocking",
            "FAM001",
            "rescued",
            100.0,
            region_local_mixture_diagnostic="one_envelope_supported",
        ),
    ),
    ids=(
        "moderate-scan-support",
        "neighbor-interference-not-assessed",
        "current-single-envelope",
        "one-envelope-supported",
    ),
)
def test_nonblocking_owner_facts_keep_rescue_quantifiable(
    cell_factory: Callable[[], AlignedCell],
) -> None:
    decision = decide_cell_quality(
        cell_factory(),
        AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0),
    )

    assert decision.quality_status == "rescue_quantifiable"
    assert decision.matrix_area == 100.0


def test_current_claim_winner_resolves_historical_duplicate_loser() -> None:
    decision = decide_cell_quality(
        _cell(
            "resolved_claim",
            "FAM001",
            "rescued",
            100.0,
            group_hypothesis_id="HYP001",
            group_claim_state="duplicate_loser",
            claim_winner_group_hypothesis_id="HYP001",
            consolidation_state="moved_to_primary_winner",
        ),
        AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0),
    )

    assert decision.quality_status == "rescue_quantifiable"
    assert decision.matrix_area == 100.0


def test_only_owner_authorized_drift_correction_can_override_preferred_rt() -> None:
    config = AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0)

    same_peak_without_drift = decide_cell_quality(
        _cell(
            "s1",
            "FAM001",
            "rescued",
            100.0,
            rt_delta_sec=120.0,
            backfill_evidence=True,
        ),
        config,
    )
    drift_supported_inside_max = decide_cell_quality(
        _cell(
            "s1_drift",
            "FAM001",
            "rescued",
            100.0,
            rt_delta_sec=20.0,
            backfill_evidence=True,
            backfill_drift_supported=True,
        ),
        config,
    )
    ms1_only = decide_cell_quality(
        _cell(
            "s1_ms1_only",
            "FAM001",
            "rescued",
            100.0,
            rt_delta_sec=120.0,
            backfill_evidence=True,
            candidate_ms2_evidence=False,
        ),
        config,
    )
    unsupported = decide_cell_quality(
        _cell(
            "s2",
            "FAM001",
            "rescued",
            100.0,
            rt_delta_sec=120.0,
            backfill_evidence=False,
        ),
        config,
    )
    incomplete = decide_cell_quality(
        _cell(
            "s3",
            "FAM001",
            "rescued",
            100.0,
            apex_rt=None,
            rt_delta_sec=120.0,
            backfill_evidence=True,
        ),
        config,
    )
    drift_supported = decide_cell_quality(
        _cell(
            "s4",
            "FAM001",
            "rescued",
            100.0,
            rt_delta_sec=240.0,
            backfill_evidence=True,
            backfill_drift_supported=True,
        ),
        config,
    )
    missing_rt = decide_cell_quality(
        _cell(
            "s5",
            "FAM001",
            "rescued",
            100.0,
            rt_delta_sec=None,
            backfill_evidence=True,
            backfill_drift_supported=True,
        ),
        config,
    )

    assert same_peak_without_drift.quality_status == "review_rescue"
    assert same_peak_without_drift.quality_reason == "rt_outside_max"
    assert ms1_only.quality_status == "review_rescue"
    assert ms1_only.quality_reason == "rt_outside_max"
    assert drift_supported_inside_max.quality_status == "rescue_quantifiable"
    assert drift_supported_inside_max.matrix_area == 100.0
    assert drift_supported.quality_status == "rescue_quantifiable"
    assert drift_supported.matrix_area == 100.0
    assert unsupported.quality_status == "review_rescue"
    assert unsupported.quality_reason == "rt_outside_max"
    assert incomplete.quality_status == "review_rescue"
    assert incomplete.quality_reason == "incomplete_peak"
    assert missing_rt.quality_status == "review_rescue"
    assert missing_rt.quality_reason == "rt_outside_max"


def test_drift_correction_uses_the_live_alignment_preferred_rt() -> None:
    config = AlignmentConfig(preferred_rt_sec=120.0, max_rt_sec=180.0)

    decision = decide_cell_quality(
        _cell(
            "s1",
            "FAM001",
            "rescued",
            100.0,
            rt_delta_sec=240.0,
            backfill_evidence=True,
            backfill_drift_supported=True,
            backfill_drift_corrected_delta_sec=100.0,
        ),
        config,
    )

    assert decision.quality_status == "rescue_quantifiable"
    assert decision.matrix_area == 100.0


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
    backfill_evidence: bool = False,
    backfill_drift_supported: bool = False,
    backfill_drift_corrected_delta_sec: float = 4.0,
    candidate_ms2_evidence: bool = True,
    scan_support_score: float | None = 0.8,
    region_local_mixture_diagnostic: str = "",
    group_hypothesis_id: str = "",
    group_claim_state: str = "",
    claim_winner_group_hypothesis_id: str = "",
    consolidation_state: str = "",
) -> AlignedCell:
    if (
        selected_integration is None
        and status in {"detected", "rescued"}
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
        source_raw_file=Path(f"{sample_stem}.raw"),
        reason=status,
        region_local_mixture_diagnostic=region_local_mixture_diagnostic,
        group_hypothesis_id=group_hypothesis_id,
        group_claim_state=group_claim_state,
        claim_winner_group_hypothesis_id=claim_winner_group_hypothesis_id,
        consolidation_state=consolidation_state,
        selected_integration=selected_integration,
        **_backfill_evidence_fields(
            status=status,
            enabled=backfill_evidence,
            drift_supported=backfill_drift_supported,
            drift_corrected_delta_sec=backfill_drift_corrected_delta_sec,
            candidate_ms2_evidence=candidate_ms2_evidence,
        ),
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


def _backfill_evidence_fields(
    *,
    status: str,
    enabled: bool,
    drift_supported: bool,
    drift_corrected_delta_sec: float,
    candidate_ms2_evidence: bool,
) -> dict[str, object]:
    if status != "rescued" or not enabled:
        return {}
    fields: dict[str, object] = {
        "backfill_ms1_pattern_status": "supportive",
        "backfill_ms1_pattern_evidence_level": "trace_constellation",
        "backfill_ms2_trigger_scan_count": 3,
        "backfill_strict_nl_scan_count": 1,
        "backfill_ms2_trace_strength": "moderate",
        "backfill_evidence_reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
        "backfill_ms1_product_authority_status": "product_authorized",
        "backfill_ms1_product_authority_scope": "feature_family_sample",
        "backfill_ms1_product_authority_source": "unit_test_reviewed_allowlist",
        "backfill_ms1_product_authority_reason": "unit_test_authorized",
        "backfill_ms1_product_authority_evidence_sha256": "unit-test-ms1-sha256",
    }
    if candidate_ms2_evidence:
        fields.update(
            {
                "backfill_candidate_ms2_pattern_status": "partial_support",
                "backfill_candidate_ms2_evidence_level": "sample_candidate_aligned",
                "backfill_candidate_ms2_product_authority_status": (
                    "product_authorized"
                ),
                "backfill_candidate_ms2_product_authority_scope": (
                    "feature_family_sample"
                ),
                "backfill_candidate_ms2_product_authority_source": (
                    "unit_test_reviewed_allowlist"
                ),
                "backfill_candidate_ms2_product_authority_reason": (
                    "unit_test_authorized"
                ),
                "backfill_candidate_ms2_product_authority_evidence_sha256": (
                    "unit-test-ms2-sha256"
                ),
            },
        )
    if drift_supported:
        fields.update(
            {
                "backfill_matrix_rt_drift_status": "drift_supported",
                "backfill_drift_evidence_level": "sample_istd_aligned",
                "backfill_drift_compatible_status": "compatible",
                "backfill_drift_corrected_delta_sec": drift_corrected_delta_sec,
            },
        )
    return fields


def _positive_area(value: float | None) -> bool:
    return (
        value is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )
