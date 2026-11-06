"""Helpers shared by test_target_extraction and other test modules."""

from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)


def _candidate(
    *,
    rt: float = 10.0,
    area: float = 1000.0,
    peak_start: float | None = None,
    peak_end: float | None = None,
    quality_flags: tuple[str, ...] = (),
    merge_note: str = "",
) -> PeakCandidate:
    return PeakCandidate(
        peak=PeakResult(
            rt=rt,
            intensity=100.0,
            intensity_smoothed=100.0,
            area=area,
            peak_start=rt - 0.1 if peak_start is None else peak_start,
            peak_end=rt + 0.1 if peak_end is None else peak_end,
        ),
        selection_apex_rt=rt,
        selection_apex_intensity=100.0,
        selection_apex_index=1,
        raw_apex_rt=rt,
        raw_apex_intensity=100.0,
        raw_apex_index=1,
        prominence=50.0,
        quality_flags=quality_flags,
        merge_note=merge_note,
    )


def _peak_result(candidate: PeakCandidate) -> PeakDetectionResult:
    return PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=5,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
