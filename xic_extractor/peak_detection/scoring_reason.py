from __future__ import annotations

from xic_extractor.peak_detection.peak_scoring_evidence import EvidenceScore


def score_breakdown_fields(
    evidence_score: EvidenceScore | None,
) -> tuple[tuple[str, str], ...]:
    if evidence_score is None:
        return ()
    return (
        ("Caps", "; ".join(evidence_score.cap_labels)),
        ("Raw Score", str(evidence_score.raw_score)),
        ("Support", "; ".join(evidence_score.support_labels)),
        ("Concerns", "; ".join(evidence_score.concern_labels)),
        ("Base Score", str(evidence_score.base_score)),
        ("Positive Points", str(evidence_score.positive_points)),
        ("Negative Points", str(evidence_score.negative_points)),
    )
