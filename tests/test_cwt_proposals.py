from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.extraction.targeted_peak_hypotheses import (
    build_targeted_peak_hypotheses as build_peak_hypotheses,
)
from xic_extractor.peak_detection.cwt import (
    add_cwt_proposals_for_audit,
    find_peak_candidates_centwave_cwt,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)


def test_centwave_cwt_finds_audit_candidate_on_synthetic_trace() -> None:
    rt, intensity = _two_peak_trace()

    result = find_peak_candidates_centwave_cwt(rt, intensity, _config())

    assert result.status == "OK"
    assert result.n_prominent_peaks == 2
    assert all(
        candidate.proposal_sources == ("centwave_cwt",)
        for candidate in result.candidates
    )
    assert any(
        abs(candidate.selection_apex_rt - 4.0) < 0.08
        for candidate in result.candidates
    )
    assert any(
        abs(candidate.selection_apex_rt - 7.0) < 0.08
        for candidate in result.candidates
    )
    assert all(
        candidate.cwt_best_scale is not None and candidate.cwt_best_scale > 0
        for candidate in result.candidates
    )
    assert all(
        candidate.cwt_ridge_persistence is not None
        and candidate.cwt_ridge_persistence > 0
        for candidate in result.candidates
    )


def test_cwt_audit_proposals_do_not_change_selected_peak() -> None:
    rt, intensity = _two_peak_trace()
    selected = _candidate(
        4.0,
        left=3.85,
        right=4.15,
        proposal_sources=("legacy_savgol",),
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=len(rt),
        max_smoothed=float(np.max(intensity)),
        n_prominent_peaks=1,
        candidates=(selected,),
        selection_reference_rt=4.0,
    )

    audited = add_cwt_proposals_for_audit(
        peak_result,
        rt,
        intensity,
        _config(),
    )

    assert audited.peak == selected.peak
    assert audited.selection_reference_rt == 4.0
    selected_candidate = next(
        candidate for candidate in audited.candidates if candidate.peak == selected.peak
    )
    assert selected_candidate.proposal_sources == ("legacy_savgol", "centwave_cwt")
    assert any(
        abs(candidate.selection_apex_rt - 7.0) < 0.08
        and candidate.proposal_sources == ("centwave_cwt",)
        for candidate in audited.candidates
    )


def test_cwt_audit_merge_prefers_selected_near_apex_candidate(monkeypatch) -> None:
    rt, intensity = _two_peak_trace()
    shadow = _candidate(
        4.0,
        left=3.80,
        right=4.20,
        proposal_sources=("legacy_savgol",),
    )
    selected = _candidate(
        4.0,
        left=3.86,
        right=4.14,
        proposal_sources=("local_minimum",),
    )
    cwt_proposal = replace(
        _candidate(
            4.0,
            left=3.82,
            right=4.18,
            proposal_sources=("centwave_cwt",),
        ),
        cwt_best_scale=5.0,
        cwt_ridge_persistence=0.6,
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=len(rt),
        max_smoothed=float(np.max(intensity)),
        n_prominent_peaks=2,
        candidates=(shadow, selected),
        selection_reference_rt=4.0,
    )

    def _fake_cwt(
        *_args: object,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        return PeakDetectionResult(
            status="OK",
            peak=cwt_proposal.peak,
            candidates=(cwt_proposal,),
            n_points=len(rt),
            max_smoothed=float(np.max(intensity)),
            n_prominent_peaks=1,
        )

    monkeypatch.setattr(
        "xic_extractor.peak_detection.cwt.find_peak_candidates_centwave_cwt",
        _fake_cwt,
    )

    audited = add_cwt_proposals_for_audit(peak_result, rt, intensity, _config())

    shadow_row = next(
        candidate for candidate in audited.candidates if candidate.peak == shadow.peak
    )
    selected_row = next(
        candidate for candidate in audited.candidates if candidate.peak == selected.peak
    )
    assert shadow_row.proposal_sources == ("legacy_savgol",)
    assert selected_row.proposal_sources == ("local_minimum", "centwave_cwt")
    assert audited.peak == selected.peak


def test_cwt_evidence_reaches_peak_hypothesis_without_selection_authority() -> None:
    rt, intensity = _two_peak_trace()
    selected = _candidate(
        4.0,
        left=3.85,
        right=4.15,
        proposal_sources=("legacy_savgol",),
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=len(rt),
        max_smoothed=float(np.max(intensity)),
        n_prominent_peaks=1,
        candidates=(selected,),
        selection_reference_rt=4.0,
    )
    audited = add_cwt_proposals_for_audit(peak_result, rt, intensity, _config())

    hypotheses = build_peak_hypotheses(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="local_minimum",
        peak_result=audited,
    )

    selected_hypotheses = [item for item in hypotheses if item.audit.selected]
    cwt_only = [
        item
        for item in hypotheses
        if item.audit.proposal_sources == ("centwave_cwt",)
    ]
    assert len(selected_hypotheses) == 1
    assert selected_hypotheses[0].integration.rt_apex_min == selected.peak.rt
    assert selected_hypotheses[0].evidence.cwt_best_scale == 11.0
    assert cwt_only
    assert all(not item.audit.selected for item in cwt_only)
    assert all(item.evidence.cwt_ridge_persistence is not None for item in cwt_only)


def _two_peak_trace() -> tuple[np.ndarray, np.ndarray]:
    rt = np.linspace(0.0, 10.0, 201)
    first = np.exp(-0.5 * ((rt - 4.0) / 0.10) ** 2) * 1200.0
    second = np.exp(-0.5 * ((rt - 7.0) / 0.14) ** 2) * 900.0
    baseline = np.full_like(rt, 20.0)
    return rt, first + second + baseline


def _candidate(
    rt: float,
    *,
    left: float,
    right: float,
    proposal_sources: tuple[str, ...],
) -> PeakCandidate:
    peak = PeakResult(
        rt=rt,
        intensity=1200.0,
        intensity_smoothed=1100.0,
        area=1000.0,
        peak_start=left,
        peak_end=right,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=1100.0,
        selection_apex_index=80,
        raw_apex_rt=rt,
        raw_apex_intensity=1200.0,
        raw_apex_index=80,
        prominence=900.0,
        proposal_sources=proposal_sources,
        source_apex_rank=1,
    )


def _config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("."),
        dll_dir=Path("."),
        output_csv=Path("out.csv"),
        diagnostics_csv=Path("diag.csv"),
        smooth_window=7,
        smooth_polyorder=2,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.05,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
        resolver_min_scans=3,
        resolver_min_absolute_height=10.0,
        resolver_min_relative_height=0.01,
        resolver_peak_duration_max=1.5,
    )
