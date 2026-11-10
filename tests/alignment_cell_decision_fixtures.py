"""Helpers shared by test_alignment_cell_decision and other test modules."""

from __future__ import annotations

from typing import Any

from tests.cid_nl_peak_support_no_raw_fixture import PRECURSOR_MZ
from xic_extractor.extraction.targeted_peak_hypotheses import (
    build_targeted_peak_hypotheses,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.observations import (
    mint_peak_observation_ref,
)
from xic_extractor.peak_detection.traces import Trace


def _numeric_peak(
    fixture: Any,
    sample_stem: str,
    *,
    mz: float = PRECURSOR_MZ,
    apex_rt: float = 8.0,
    rt_left_min: float = 7.9,
    rt_right_min: float = 8.1,
    height: float = 500.0,
) -> Any:
    sample = fixture.sample(sample_stem)
    source = fixture.source(sample)
    trace = Trace.from_arrays(
        sample_name=sample_stem,
        mz=mz,
        rt=(
            rt_left_min - 0.1,
            rt_left_min,
            apex_rt,
            rt_right_min,
            rt_right_min + 0.1,
        ),
        intensity=(0.0, height / 10.0, height, height / 10.0, 0.0),
        rt_min=rt_left_min - 0.1,
        rt_max=rt_right_min + 0.1,
        ppm_tol=10.0,
        source="cell_decision_no_raw_fixture",
        acquisition_source_id=source.acquisition_source_id,
    )
    peak = PeakResult(
        rt=apex_rt,
        intensity=height,
        intensity_smoothed=height,
        area=height * 6.6,
        peak_start=rt_left_min,
        peak_end=rt_right_min,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=apex_rt,
        selection_apex_intensity=height,
        selection_apex_index=2,
        raw_apex_rt=apex_rt,
        raw_apex_intensity=height,
        raw_apex_index=2,
        prominence=height * 0.9,
        proposal_sources=("local_minimum",),
    )
    result = PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=trace.scan_count,
        max_smoothed=height,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    integration = build_targeted_peak_hypotheses(
        sample_name=trace.sample_name,
        target_label="cell-decision-no-raw",
        role="",
        istd_pair="",
        resolver_mode="local_minimum",
        peak_result=result,
        rt=trace.rt,
        intensity=trace.intensity,
        baseline_integration_method="asls",
    )[0].integration
    return mint_peak_observation_ref(
        sample=sample,
        trace=trace,
        integration=integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )
