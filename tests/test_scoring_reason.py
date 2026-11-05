from xic_extractor.peak_detection.peak_scoring_evidence import EvidenceScore
from xic_extractor.peak_detection.scoring_reason import (
    score_breakdown_fields,
)


def test_score_breakdown_fields_preserves_public_projection_order() -> None:
    score = EvidenceScore(
        base_score=50,
        positive_points=40,
        negative_points=5,
        raw_score=85,
        support_labels=("strict_nl_ok", "local_sn_strong"),
        concern_labels=("trace_quality_review",),
        cap_labels=("trace_quality_cap",),
    )

    assert score_breakdown_fields(score) == (
        ("Caps", "trace_quality_cap"),
        ("Raw Score", "85"),
        ("Support", "strict_nl_ok; local_sn_strong"),
        ("Concerns", "trace_quality_review"),
        ("Base Score", "50"),
        ("Positive Points", "40"),
        ("Negative Points", "5"),
    )
