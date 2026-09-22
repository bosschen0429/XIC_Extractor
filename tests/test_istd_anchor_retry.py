from xic_extractor.extraction.istd_anchor_retry import (
    _should_try_wider_istd_anchor_retry,
    _should_use_wider_istd_retry,
)
from xic_extractor.signal_processing import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)


def _candidate(
    *,
    rt: float,
    intensity: float,
    quality_flags: tuple[str, ...] = (),
) -> PeakCandidate:
    peak = PeakResult(
        rt=rt,
        intensity=intensity,
        intensity_smoothed=intensity,
        area=intensity * 10.0,
        peak_start=rt - 0.1,
        peak_end=rt + 0.1,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=intensity,
        selection_apex_index=10,
        raw_apex_rt=rt,
        raw_apex_intensity=intensity,
        raw_apex_index=10,
        prominence=intensity,
        quality_flags=quality_flags,
    )


def _result(
    candidate: PeakCandidate,
    *,
    severities: tuple[tuple[int, str], ...] = (),
) -> PeakDetectionResult:
    return PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=100,
        max_smoothed=candidate.selection_apex_intensity,
        n_prominent_peaks=1,
        candidates=(candidate,),
        confidence="HIGH",
        severities=severities,
    )


def test_wider_istd_retry_is_tried_for_borderline_local_sn_anchor() -> None:
    current = _result(
        _candidate(rt=26.26, intensity=35_000.0),
        severities=((1, "local_sn"),),
    )

    assert _should_try_wider_istd_anchor_retry(current)


def test_wider_istd_retry_allows_equal_penalty_when_signal_is_much_stronger(
) -> None:
    current = _result(_candidate(rt=26.26, intensity=35_000.0))
    retried = _result(_candidate(rt=24.18, intensity=30_000_000.0))

    assert _should_use_wider_istd_retry(current, retried)


def test_wider_istd_retry_rejects_equal_penalty_without_signal_gain() -> None:
    current = _result(_candidate(rt=26.26, intensity=35_000.0))
    retried = _result(_candidate(rt=24.18, intensity=50_000.0))

    assert not _should_use_wider_istd_retry(current, retried)
