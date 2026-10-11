from types import SimpleNamespace
from typing import Literal

import pytest

from xic_extractor.configuration.models import Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.extraction.paired_area_ratio_projection import (
    PAIRED_AREA_RATIO_BASIS,
    PAIRED_AREA_RATIO_REFERENCE_BASIS,
    PAIRED_AREA_RATIO_ROBUST_BASIS,
    PairedAreaRatioReferencePoint,
    apply_paired_area_ratio_projection,
    assess_paired_area_ratio,
)
from xic_extractor.extraction.result_assembly import build_extraction_result
from xic_extractor.extraction.result_models import FileResult, RunOutput
from xic_extractor.output.targeted_product_model import (
    OWN_MAX_SAME_PEAK_SUPPORT_REASON,
)
from xic_extractor.peak_detection.evidence_semantics import EvidenceDecisionSemantics
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)

_NLStatus = Literal["OK", "WARN", "NL_FAIL", "NO_MS2"]


def test_run_level_area_ratio_support_without_own_max_keeps_nl_fail_not_counted() -> (
    None
):
    output = RunOutput(
        file_results=[
            _file_result("SampleA", target_area=50_000.0, nl_status="NL_FAIL"),
            _file_result("RefA", target_area=40_000.0, nl_status="OK"),
            _file_result("RefB", target_area=50_000.0, nl_status="OK"),
            _file_result("RefC", target_area=60_000.0, nl_status="OK"),
        ],
        diagnostics=[],
    )

    result = output.file_results[0].results["Analyte"]
    assert result.targeted_product_projection is not None
    assert result.targeted_product_projection.counted_detection is False
    assert "analyte_nl_fail_requires_policy" in (
        result.targeted_product_projection.not_counted_reasons
    )

    apply_paired_area_ratio_projection(output, targets=[_target(), _istd()])

    projected = output.file_results[0].results["Analyte"].targeted_product_projection
    decision = output.file_results[0].results["Analyte"].selection_decision
    assert projected is not None
    assert decision is not None
    assert projected.product_state == "ambiguous"
    assert projected.counted_detection is False
    assert "paired_area_ratio_support" in projected.support_reasons
    assert "paired_istd_rt_within_1min_support" in projected.support_reasons
    assert OWN_MAX_SAME_PEAK_SUPPORT_REASON not in projected.support_reasons
    assert "analyte_nl_fail_requires_policy" in projected.not_counted_reasons
    assert "run_level_paired_area_ratio" in decision.evidence_sources
    assert "run_level_paired_istd_rt" in decision.evidence_sources


def test_run_level_area_ratio_support_can_count_own_max_supported_nl_fail() -> None:
    output = RunOutput(
        file_results=[
            _file_result(
                "SampleA",
                target_area=50_000.0,
                nl_status="NL_FAIL",
                support_reasons=(OWN_MAX_SAME_PEAK_SUPPORT_REASON,),
            ),
            _file_result("RefA", target_area=40_000.0, nl_status="OK"),
            _file_result("RefB", target_area=50_000.0, nl_status="OK"),
            _file_result("RefC", target_area=60_000.0, nl_status="OK"),
        ],
        diagnostics=[],
    )

    apply_paired_area_ratio_projection(output, targets=[_target(), _istd()])

    projected = output.file_results[0].results["Analyte"].targeted_product_projection
    decision = output.file_results[0].results["Analyte"].selection_decision
    assert projected is not None
    assert decision is not None
    assert projected.product_state == "detected_flagged"
    assert projected.counted_detection is True
    assert "paired_area_ratio_support" in projected.support_reasons
    assert "paired_istd_rt_within_1min_support" in projected.support_reasons
    assert OWN_MAX_SAME_PEAK_SUPPORT_REASON in projected.support_reasons
    assert "analyte_nl_fail_requires_policy" not in projected.not_counted_reasons


def test_run_level_area_ratio_outside_reference_stays_not_counted() -> None:
    output = RunOutput(
        file_results=[
            _file_result("SampleA", target_area=2_000.0, nl_status="NL_FAIL"),
            _file_result("RefA", target_area=40_000.0, nl_status="OK"),
            _file_result("RefB", target_area=50_000.0, nl_status="OK"),
            _file_result("RefC", target_area=60_000.0, nl_status="OK"),
        ],
        diagnostics=[],
    )

    apply_paired_area_ratio_projection(output, targets=[_target(), _istd()])

    projected = output.file_results[0].results["Analyte"].targeted_product_projection
    assert projected is not None
    assert projected.counted_detection is False
    assert "paired_area_ratio_support" not in projected.support_reasons
    assert "analyte_nl_fail_requires_policy" in projected.not_counted_reasons


def test_area_ratio_inside_minmax_outside_robust_stays_not_counted() -> None:
    output = RunOutput(
        file_results=[
            _file_result("SampleA", target_area=600_000.0, nl_status="NL_FAIL"),
            _file_result("RefA", target_area=40_000.0, nl_status="OK"),
            _file_result("RefB", target_area=50_000.0, nl_status="OK"),
            _file_result("RefC", target_area=1_000_000.0, nl_status="OK"),
        ],
        diagnostics=[],
    )

    apply_paired_area_ratio_projection(output, targets=[_target(), _istd()])

    projected = output.file_results[0].results["Analyte"].targeted_product_projection
    assert projected is not None
    assert projected.counted_detection is False
    assert "paired_area_ratio_support" not in projected.support_reasons
    assert "analyte_nl_fail_requires_policy" in projected.not_counted_reasons


def test_run_level_area_ratio_reference_requires_three_other_samples() -> None:
    output = RunOutput(
        file_results=[
            _file_result("SampleA", target_area=50_000.0, nl_status="NL_FAIL"),
            _file_result("RefA", target_area=40_000.0, nl_status="OK"),
            _file_result("RefB", target_area=50_000.0, nl_status="OK"),
        ],
        diagnostics=[],
    )

    apply_paired_area_ratio_projection(output, targets=[_target(), _istd()])

    projected = output.file_results[0].results["Analyte"].targeted_product_projection
    assert projected is not None
    assert projected.counted_detection is False
    assert "paired_area_ratio_support" not in projected.support_reasons
    assert "analyte_nl_fail_requires_policy" in projected.not_counted_reasons


def test_run_level_area_ratio_does_not_override_selected_envelope_guard() -> None:
    output = RunOutput(
        file_results=[
            _file_result(
                "SampleA",
                target_area=50_000.0,
                nl_status="NL_FAIL",
                selected_envelope_not_counted=True,
            ),
            _file_result("RefA", target_area=40_000.0, nl_status="OK"),
            _file_result("RefB", target_area=50_000.0, nl_status="OK"),
            _file_result("RefC", target_area=60_000.0, nl_status="OK"),
        ],
        diagnostics=[],
    )

    apply_paired_area_ratio_projection(output, targets=[_target(), _istd()])

    projected = output.file_results[0].results["Analyte"].targeted_product_projection
    assert projected is not None
    assert projected.counted_detection is False
    assert "selected_envelope_boundary_defer" in projected.not_counted_reasons
    assert "paired_area_ratio_support" not in projected.support_reasons


def test_area_ratio_basis_documents_counted_leave_one_out_robust_gate() -> None:
    assert PAIRED_AREA_RATIO_REFERENCE_BASIS == (
        "leave_one_sample_out_counted_area_over_istd_area"
    )
    assert PAIRED_AREA_RATIO_BASIS == (
        "leave_one_sample_out_median_plus_minus_3_scaled_mad_area_over_istd_area"
    )
    assert PAIRED_AREA_RATIO_ROBUST_BASIS == (
        "leave_one_sample_out_median_plus_minus_3_scaled_mad_area_over_istd_area"
    )


def test_area_ratio_assessment_reports_robust_shadow_conflict() -> None:
    assessment = assess_paired_area_ratio(
        SimpleNamespace(reported_peak_area=600_000.0),
        target=_target(),
        sample_name="SampleA",
        paired_istd_result=SimpleNamespace(reported_peak_area=100_000.0),
        references={
            ("Analyte", "ISTD"): (
                PairedAreaRatioReferencePoint("RefA", 0.4),
                PairedAreaRatioReferencePoint("RefB", 0.5),
                PairedAreaRatioReferencePoint("RefC", 10.0),
            )
        },
    )

    assert assessment.status == "outside_robust_range"
    assert assessment.within_reference is False
    assert assessment.robust_status == "outside_robust_range"
    assert assessment.robust_reference_median == pytest.approx(0.5)
    assert assessment.robust_reference_mad == pytest.approx(0.1)
    assert assessment.robust_reference_min == pytest.approx(0.05522, abs=1e-5)
    assert assessment.robust_reference_max == pytest.approx(0.94478, abs=1e-5)
    assert assessment.robust_basis == PAIRED_AREA_RATIO_ROBUST_BASIS


def _target() -> Target:
    return Target(
        label="Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=9.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=False,
        istd_pair="ISTD",
    )


def _istd() -> Target:
    return Target(
        label="ISTD",
        mz=263.1085,
        rt_min=8.0,
        rt_max=9.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="ISTD",
    )


def _file_result(
    sample_name: str,
    *,
    target_area: float,
    nl_status: _NLStatus,
    selected_envelope_not_counted: bool = False,
    support_reasons: tuple[str, ...] = (),
) -> FileResult:
    target = _target()
    istd = _istd()
    return FileResult(
        sample_name=sample_name,
        results={
            "Analyte": _result(
                target,
                sample_name=sample_name,
                area=target_area,
                nl_status=nl_status,
                selected_envelope_not_counted=selected_envelope_not_counted,
                support_reasons=support_reasons,
            ),
            "ISTD": _result(
                istd,
                sample_name=sample_name,
                area=100_000.0,
                nl_status="OK",
            ),
        },
    )


def _result(
    target: Target,
    *,
    sample_name: str,
    area: float,
    nl_status: _NLStatus,
    selected_envelope_not_counted: bool = False,
    support_reasons: tuple[str, ...] = (),
):
    candidate = _candidate(area=area)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=12,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        paired_istd_anchor_rt=8.5 if not target.is_istd else None,
    )
    selected = _hypothesis(
        target,
        sample_name=sample_name,
        area=area,
        nl_status=nl_status,
        support_reasons=support_reasons,
    )
    selection_decision = None
    if selected_envelope_not_counted:
        selection_decision = PeakHypothesisSelectionDecision(
            selected_candidate_id=selected.hypothesis_id,
            trace_group_id=selected.trace_group_id,
            decision_class="not_counted",
            projected_confidence="VERY_LOW",
            projected_reason=(
                "decision: not_counted; not_counted: selected_envelope_boundary_defer"
            ),
            support_reasons=("ms1_coherent",),
            not_counted_reasons=("selected_envelope_boundary_defer",),
        )
    return build_extraction_result(
        peak_result=peak_result,
        nl_result=NLResult(
            nl_status,
            2.5 if nl_status == "OK" else None,
            8.5 if nl_status != "NO_MS2" else None,
            1 if nl_status != "NO_MS2" else 0,
            0,
            1 if nl_status == "OK" else 0,
        ),
        candidate_ms2_evidence=_ms2(nl_status),
        target=target,
        candidate=candidate,
        scoring_context_builder=None,
        selected_hypothesis=selected,
        selection_decision=selection_decision,
        sample_name=sample_name,
    )


def _hypothesis(
    target: Target,
    *,
    sample_name: str,
    area: float,
    nl_status: _NLStatus,
    support_reasons: tuple[str, ...] = (),
) -> PeakHypothesis:
    support = ["ms1_coherent"]
    support.extend(support_reasons)
    conflicts: tuple[str, ...] = ()
    if nl_status == "OK":
        support.append("candidate_aligned_ms2_nl")
    elif not target.is_istd:
        conflicts = ("candidate_aligned_ms2_nl_conflict",)
    return PeakHypothesis(
        hypothesis_id=f"{sample_name}|{target.label}|selected",
        trace_group_id=f"{sample_name}|{target.label}|targeted",
        target_label=target.label,
        role="ISTD" if target.is_istd else "Analyte",
        istd_pair=target.istd_pair,
        analysis_mode="targeted",
        resolver_mode="local_minimum",
        integration=IntegrationResult(
            rt_left_min=8.4,
            rt_apex_min=8.5,
            rt_right_min=8.6,
            raw_apex_rt_min=8.5,
            rt_width_min=0.2,
            height_raw=1200.0,
            height_smoothed=1100.0,
            area_raw_counts_seconds=area,
            area_ms1_morphology=area,
            ms1_morphology_area_source="gaussian15_positive_asls_residual",
        ),
        evidence=EvidenceVector(
            confidence="HIGH",
            reason="decision: accepted",
            decision_semantics=EvidenceDecisionSemantics(
                decision_class="ambiguous",
                support_reasons=tuple(support),
                conflict_reasons=conflicts,
            ),
        ),
        audit=AuditTrail(selected=True),
    )


def _candidate(*, area: float) -> PeakCandidate:
    peak = PeakResult(
        rt=8.5,
        intensity=1200.0,
        intensity_smoothed=1100.0,
        area=area,
        peak_start=8.4,
        peak_end=8.6,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=8.5,
        selection_apex_intensity=1100.0,
        selection_apex_index=1,
        raw_apex_rt=8.5,
        raw_apex_intensity=1200.0,
        raw_apex_index=1,
        prominence=700.0,
    )


def _ms2(nl_status: _NLStatus) -> CandidateMS2Evidence:
    if nl_status not in {"OK", "NL_FAIL", "NO_MS2"}:
        raise ValueError(nl_status)
    return CandidateMS2Evidence(
        ms2_present=nl_status != "NO_MS2",
        nl_match=nl_status == "OK",
        nl_status=nl_status,
        trigger_scan_count=0 if nl_status == "NO_MS2" else 1,
        strict_nl_scan_count=1 if nl_status == "OK" else 0,
        best_loss_ppm=2.5 if nl_status == "OK" else None,
        best_scan_rt=8.5 if nl_status != "NO_MS2" else None,
        best_product_base_ratio=0.8 if nl_status == "OK" else None,
        alignment_source="region" if nl_status != "NO_MS2" else "none",
    )
