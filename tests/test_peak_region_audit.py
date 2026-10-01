from pathlib import Path

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.region_audit import (
    build_peak_region_audit_summary,
)
from xic_extractor.peak_detection.traces import Trace, untargeted_trace_group


def test_peak_region_audit_summarizes_current_envelope() -> None:
    rt = np.array([4.8, 4.9, 5.0, 5.1, 5.2], dtype=float)
    intensity = np.array([1.0, 3.0, 9.0, 3.0, 1.0], dtype=float)
    peak = PeakResult(
        rt=5.0,
        intensity=9.0,
        intensity_smoothed=9.0,
        area=60.0,
        peak_start=4.9,
        peak_end=5.1,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=5.0,
        selection_apex_intensity=9.0,
        selection_apex_index=2,
        raw_apex_rt=5.0,
        raw_apex_intensity=9.0,
        raw_apex_index=2,
        prominence=8.0,
        proposal_sources=("local_minimum",),
    )
    result = PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=5,
        max_smoothed=9.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )

    summary = build_peak_region_audit_summary(
        rt,
        intensity,
        result,
        _config(),
        include_cwt=False,
    )

    assert summary.candidate_count == 1
    assert summary.selected_proposal_sources == ("local_minimum",)
    assert summary.shadow_status == "evaluated"
    assert summary.decision_status == "evaluated"
    assert summary.decision_class == "current_supported"
    assert summary.product_action == "no_change"
    assert summary.promotion_reason == ""
    assert summary.baseline_method == "asls"
    assert summary.local_mixture_diagnostic == "current_single_envelope"
    assert summary.integration_audit is not None
    assert summary.integration_audit.area_baseline_corrected is not None
    assert summary.integration_audit.integration_scan_count == 3


def test_peak_region_audit_returns_empty_for_no_peak() -> None:
    summary = build_peak_region_audit_summary(
        np.array([1.0, 2.0]),
        np.array([0.0, 0.0]),
        PeakDetectionResult(
            status="PEAK_NOT_FOUND",
            peak=None,
            n_points=2,
            max_smoothed=0.0,
            n_prominent_peaks=0,
        ),
        _config(),
    )

    assert summary.candidate_count is None
    assert summary.shadow_status == ""
    assert summary.integration_audit is None


def test_peak_region_audit_prefers_trace_group_arrays() -> None:
    raw_rt = np.array([4.8, 4.9, 5.0], dtype=float)
    raw_intensity = np.array([1.0], dtype=float)
    trace = Trace.from_arrays(
        sample_name="sample-a",
        mz=269.1388,
        rt=[4.8, 4.9, 5.0, 5.1, 5.2],
        intensity=[1.0, 3.0, 9.0, 3.0, 1.0],
        rt_min=4.8,
        rt_max=5.2,
        ppm_tol=20.0,
    )
    peak = PeakResult(
        rt=5.0,
        intensity=9.0,
        intensity_smoothed=9.0,
        area=60.0,
        peak_start=4.9,
        peak_end=5.1,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=5.0,
        selection_apex_intensity=9.0,
        selection_apex_index=2,
        raw_apex_rt=5.0,
        raw_apex_intensity=9.0,
        raw_apex_index=2,
        prominence=8.0,
        proposal_sources=("local_minimum",),
    )

    summary = build_peak_region_audit_summary(
        raw_rt,
        raw_intensity,
        PeakDetectionResult(
            status="OK",
            peak=peak,
            n_points=5,
            max_smoothed=9.0,
            n_prominent_peaks=1,
            candidates=(candidate,),
        ),
        _config(),
        include_cwt=False,
        trace_group=untargeted_trace_group(trace, family_id="FAM000001"),
    )

    assert summary.shadow_status == "evaluated"
    assert summary.review_reason != "Trace arrays must have matching lengths"


def _config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("."),
        dll_dir=Path("."),
        output_csv=Path("output.csv"),
        diagnostics_csv=Path("diagnostics.csv"),
        smooth_window=5,
        smooth_polyorder=2,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
    )
