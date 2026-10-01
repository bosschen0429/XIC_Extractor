import numpy as np
import pytest

from xic_extractor.peak_detection.boundaries import (
    BoundaryCandidateContext,
    BoundaryHypothesis,
    boundary_audit_id,
    enumerate_boundary_hypotheses,
)
from xic_extractor.peak_detection.models import PeakCandidate, PeakResult


def test_candidate_interval_boundary_reproduces_current_candidate_interval() -> None:
    rt = np.asarray([0.0, 0.1, 0.2, 0.3, 0.4], dtype=float)
    intensity = np.asarray([1.0, 5.0, 10.0, 5.0, 1.0], dtype=float)
    candidate = _candidate(rt=0.2, left=0.1, right=0.3)

    boundaries = enumerate_boundary_hypotheses(
        rt,
        intensity,
        candidate,
        candidate_id="Sample|Target|candidate",
        sources=("candidate_interval",),
    )

    assert len(boundaries) == 1
    boundary = boundaries[0]
    assert boundary.sources == ("candidate_interval",)
    assert boundary.left_index == 1
    assert boundary.right_index == 4
    assert boundary.rt_left_min == pytest.approx(0.1)
    assert boundary.rt_right_min == pytest.approx(0.3)
    assert boundary.area_raw_counts_seconds == pytest.approx(90.0)
    assert (
        boundary.boundary_id
        == "Sample|Target|candidate|candidate_interval|0.10000|0.30000"
    )


def test_boundary_context_reproduces_interval_without_legacy_model() -> None:
    rt = np.asarray([0.0, 0.1, 0.2, 0.3, 0.4], dtype=float)
    intensity = np.asarray([1.0, 5.0, 10.0, 5.0, 1.0], dtype=float)
    candidate = BoundaryCandidateContext(
        selection_apex_rt=0.2,
        rt_left_min=0.1,
        rt_right_min=0.3,
        cwt_best_scale=3.0,
        proposal_sources=("hypothesis_source",),
    )

    boundaries = enumerate_boundary_hypotheses(
        rt,
        intensity,
        candidate,
        candidate_id="Sample|Target|hypothesis",
        sources=("candidate_interval", "cwt_width"),
    )

    by_source = {
        source: boundary
        for boundary in boundaries
        for source in boundary.sources
    }
    assert by_source["candidate_interval"].left_index == 1
    assert by_source["candidate_interval"].right_index == 4
    assert by_source["cwt_width"].left_index == 1
    assert by_source["cwt_width"].right_index == 4
    assert all(
        boundary.boundary_id.startswith("Sample|Target|hypothesis")
        for boundary in boundaries
    )


def test_boundary_enumerator_can_emit_distinct_audit_intervals() -> None:
    rt = np.asarray([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6], dtype=float)
    intensity = np.asarray([1.0, 2.0, 7.0, 10.0, 7.0, 2.0, 1.0], dtype=float)
    candidate = _candidate(rt=0.3, left=0.0, right=0.6)

    boundaries = enumerate_boundary_hypotheses(
        rt,
        intensity,
        candidate,
        sources=("candidate_interval", "half_height", "baseline_return"),
    )

    by_source = {
        source: boundary
        for boundary in boundaries
        for source in boundary.sources
    }
    assert by_source["candidate_interval"].left_index == 0
    assert by_source["candidate_interval"].right_index == 7
    assert by_source["half_height"].left_index == 1
    assert by_source["half_height"].right_index == 6
    assert by_source["baseline_return"].left_index == 0
    assert by_source["baseline_return"].right_index == 7


def test_derivative_zero_crossing_boundary_stops_at_neighboring_valleys() -> None:
    rt = np.asarray([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6], dtype=float)
    intensity = np.asarray([8.0, 3.0, 7.0, 10.0, 6.0, 2.0, 5.0], dtype=float)
    candidate = _candidate(rt=0.3, left=0.0, right=0.6)

    boundaries = enumerate_boundary_hypotheses(
        rt,
        intensity,
        candidate,
        sources=("derivative_zero_crossing",),
    )

    assert len(boundaries) == 1
    assert boundaries[0].sources == ("derivative_zero_crossing",)
    assert boundaries[0].left_index == 1
    assert boundaries[0].right_index == 6


def test_cwt_width_boundary_uses_candidate_cwt_scale_when_available() -> None:
    rt = np.asarray([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6], dtype=float)
    intensity = np.asarray([1.0, 2.0, 7.0, 10.0, 7.0, 2.0, 1.0], dtype=float)
    candidate = _candidate(rt=0.3, left=0.0, right=0.6, cwt_best_scale=3.0)

    boundaries = enumerate_boundary_hypotheses(
        rt,
        intensity,
        candidate,
        sources=("cwt_width",),
    )

    assert len(boundaries) == 1
    assert boundaries[0].sources == ("cwt_width",)
    assert boundaries[0].left_index == 2
    assert boundaries[0].right_index == 5


def test_cwt_width_boundary_is_absent_without_cwt_scale() -> None:
    rt = np.asarray([0.0, 0.1, 0.2, 0.3, 0.4], dtype=float)
    intensity = np.asarray([1.0, 2.0, 10.0, 2.0, 1.0], dtype=float)
    candidate = _candidate(rt=0.2, left=0.0, right=0.4)

    assert (
        enumerate_boundary_hypotheses(
            rt,
            intensity,
            candidate,
            sources=("cwt_width",),
        )
        == ()
    )


@pytest.mark.parametrize("bad_scale", [0.0, -1.0, float("nan"), float("inf")])
def test_cwt_width_boundary_is_absent_with_invalid_cwt_scale(
    bad_scale: float,
) -> None:
    rt = np.asarray([0.0, 0.1, 0.2, 0.3, 0.4], dtype=float)
    intensity = np.asarray([1.0, 2.0, 10.0, 2.0, 1.0], dtype=float)
    candidate = _candidate(rt=0.2, left=0.0, right=0.4, cwt_best_scale=bad_scale)

    assert (
        enumerate_boundary_hypotheses(
            rt,
            intensity,
            candidate,
            sources=("cwt_width",),
        )
        == ()
    )


def test_duplicate_boundary_intervals_merge_sources() -> None:
    rt = np.asarray([0.0, 0.1, 0.2, 0.3, 0.4], dtype=float)
    intensity = np.asarray([1.0, 2.0, 10.0, 2.0, 1.0], dtype=float)
    candidate = _candidate(rt=0.2, left=0.0, right=0.4)

    boundaries = enumerate_boundary_hypotheses(
        rt,
        intensity,
        candidate,
        sources=("candidate_interval", "baseline_return"),
    )

    assert len(boundaries) == 1
    assert boundaries[0].sources == ("candidate_interval", "baseline_return")


def test_boundary_audit_id_is_deterministic() -> None:
    boundary = BoundaryHypothesis(
        boundary_id="",
        sources=("candidate_interval",),
        left_index=1,
        right_index=4,
        rt_left_min=8.1,
        rt_apex_min=8.2,
        rt_right_min=8.3,
        width_min=0.2,
        area_raw_counts_seconds=123.4,
        scan_count=3,
    )

    first = boundary_audit_id(
        candidate_id="Sample|Target|candidate",
        boundary=boundary,
    )
    second = boundary_audit_id(
        candidate_id="Sample|Target|candidate",
        boundary=boundary,
    )

    assert first == second
    assert first == "Sample|Target|candidate|candidate_interval|8.10000|8.30000"


def test_boundary_enumerator_rejects_mismatched_arrays() -> None:
    candidate = _candidate(rt=0.2, left=0.1, right=0.3)

    with pytest.raises(ValueError, match="same length"):
        enumerate_boundary_hypotheses(
            np.asarray([0.0, 0.1], dtype=float),
            np.asarray([1.0], dtype=float),
            candidate,
        )


def _candidate(
    rt: float,
    *,
    left: float,
    right: float,
    cwt_best_scale: float | None = None,
) -> PeakCandidate:
    peak = PeakResult(
        rt=rt,
        intensity=10.0,
        intensity_smoothed=9.0,
        area=100.0,
        peak_start=left,
        peak_end=right,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=9.0,
        selection_apex_index=2,
        raw_apex_rt=rt,
        raw_apex_intensity=10.0,
        raw_apex_index=2,
        prominence=8.0,
        proposal_sources=("legacy_savgol",),
        source_apex_rank=1,
        cwt_best_scale=cwt_best_scale,
    )
