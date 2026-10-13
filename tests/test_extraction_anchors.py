from tests.test_result_assembly import _target
from xic_extractor.extraction.anchors import paired_anchor_mismatch_diagnostic
from xic_extractor.peak_detection.models import PeakDetectionResult, PeakResult


def test_anchor_mismatch_is_diagnostic_without_changing_quantification() -> None:
    peak_result = PeakDetectionResult(
        status="OK",
        peak=PeakResult(
            rt=13.06,
            intensity=5000.0,
            intensity_smoothed=5000.0,
            area=8000.0,
            peak_start=12.9,
            peak_end=13.2,
        ),
        n_points=15,
        max_smoothed=5000.0,
        n_prominent_peaks=1,
        confidence="HIGH",
        reason="decision: accepted",
        score_breakdown=(
            ("Final Confidence", "HIGH"),
            ("Caps", ""),
            ("Raw Score", "90"),
            ("Support", "strict_nl_ok"),
            ("Concerns", ""),
            ("Negative Points", "0"),
        ),
    )

    diagnostic = paired_anchor_mismatch_diagnostic(
        "SampleA",
        _target(),
        peak_result,
        reference_rt=10.0,
        anchor_rt=10.0,
        strict_preferred_rt=True,
    )
    assert diagnostic is not None and diagnostic.issue == "ANCHOR_RT_MISMATCH"
    assert peak_result.confidence == "HIGH"
    assert peak_result.peak.area == 8000.0
    assert dict(peak_result.score_breakdown)["Raw Score"] == "90"
