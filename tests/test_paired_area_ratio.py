from types import SimpleNamespace
from typing import Literal

import pytest

from tests.test_result_assembly import _method_relations
from xic_extractor.configuration.models import Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.extraction.paired_area_ratio import (
    PAIRED_AREA_RATIO_BASIS,
    PAIRED_AREA_RATIO_REFERENCE_BASIS,
    PairedAreaRatioReferencePoint,
    assess_paired_area_ratio,
    paired_area_ratio_references,
)
from xic_extractor.extraction.result_assembly import build_extraction_result
from xic_extractor.extraction.result_models import FileResult, RunOutput
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


def test_area_ratio_basis_documents_counted_leave_one_out_robust_gate() -> None:
    assert PAIRED_AREA_RATIO_REFERENCE_BASIS == (
        "leave_one_sample_out_counted_area_over_istd_area"
    )
    assert PAIRED_AREA_RATIO_BASIS == (
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
    assert (assessment.status == "within_robust_range") is False
    assert assessment.robust_status == "outside_robust_range"
    assert assessment.robust_reference_median == pytest.approx(0.5)
    assert assessment.robust_reference_mad == pytest.approx(0.1)
    assert assessment.robust_reference_min == pytest.approx(0.05522, abs=1e-5)
    assert assessment.robust_reference_max == pytest.approx(0.94478, abs=1e-5)


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
        cid_nl_relations=_method_relations(target, candidate, nl_status),
        selected_cid_nl_candidate_index=0,
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


@pytest.mark.parametrize("area", [50000.0, 600000.0])
def test_ratio_observation_does_not_change_scientific_results(area: float) -> None:
    output = RunOutput(
        file_results=[
            _file_result("SampleA", target_area=area, nl_status="NL_FAIL"),
            _file_result("RefA", target_area=40000.0, nl_status="OK"),
            _file_result("RefB", target_area=50000.0, nl_status="OK"),
            _file_result("RefC", target_area=60000.0, nl_status="OK"),
        ],
        diagnostics=[],
    )
    result = output.file_results[0].results["Analyte"]
    projection = result.targeted_product_projection
    decision = result.selection_decision
    references = paired_area_ratio_references(
        output.file_results, targets=[_target(), _istd()]
    )
    observation = assess_paired_area_ratio(
        result,
        target=_target(),
        sample_name="SampleA",
        paired_istd_result=output.file_results[0].results["ISTD"],
        references=references,
    )
    assert observation.observed_ratio == area / 100000.0
    assert (observation.status == "within_robust_range") is (area == 50000.0)
    assert result.targeted_product_projection is projection
    assert result.selection_decision is decision
    assert projection is not None and not projection.counted_detection
    assert "paired_area_ratio_support" not in projection.support_reasons
    assert result.reported_peak_area == area
