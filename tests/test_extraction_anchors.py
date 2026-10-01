from xic_extractor.extraction.anchors import apply_anchor_mismatch_penalty
from xic_extractor.peak_detection.models import PeakDetectionResult, PeakResult


def test_anchor_mismatch_penalty_syncs_score_breakdown_confidence_and_caps() -> None:
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

    downgraded = apply_anchor_mismatch_penalty(
        peak_result,
        "Paired analyte peak RT 13.060 min deviates from anchor",
    )

    breakdown = dict(downgraded.score_breakdown)
    assert downgraded.confidence == "VERY_LOW"
    assert breakdown["Final Confidence"] == "VERY_LOW"
    assert breakdown["Caps"] == "anchor_mismatch_cap"
    assert breakdown["Concerns"] == "anchor_mismatch"
    assert breakdown["Raw Score"] == "45"
    assert breakdown["Negative Points"] == "45"
    assert downgraded.reason.startswith("decision: review only, not counted")
    assert "decision: accepted" not in downgraded.reason
