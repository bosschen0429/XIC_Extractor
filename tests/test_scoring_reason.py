from xic_extractor.peak_detection.peak_scoring_evidence import EvidenceScore
from xic_extractor.peak_detection.scoring_reason import (
    build_evidence_reason,
    build_reason,
    score_breakdown_fields,
)


def test_reason_all_pass() -> None:
    assert (
        build_reason(
            [(0, "symmetry"), (0, "local_sn")],
            istd_confidence_note=None,
        )
        == "all checks passed"
    )


def test_reason_lists_concerns_in_severity_order() -> None:
    reason = build_reason(
        [(0, "symmetry"), (1, "local_sn"), (2, "nl_support")],
        istd_confidence_note=None,
    )

    assert reason == "concerns: nl_support (major); local_sn (minor)"


def test_reason_appends_istd_note() -> None:
    reason = build_reason(
        [(0, "symmetry")], istd_confidence_note="ISTD anchor was LOW"
    )

    assert "ISTD anchor was LOW" in reason
    assert reason.startswith("ISTD anchor was LOW") or reason.endswith(
        "ISTD anchor was LOW"
    )


def test_reason_text_limits_default_evidence_labels() -> None:
    score = EvidenceScore(
        base_score=50,
        positive_points=50,
        negative_points=90,
        raw_score=10,
        score_confidence="VERY_LOW",
        confidence="VERY_LOW",
        support_labels=(
            "strict_nl_ok",
            "rt_prior_close",
            "local_sn_strong",
            "shape_clean",
        ),
        concern_labels=(
            "nl_fail",
            "rt_prior_far",
            "anchor_mismatch",
            "low_trace_continuity",
            "poor_edge_recovery",
        ),
        cap_labels=("nl_fail_cap",),
    )

    reason = build_evidence_reason(score, istd_confidence_note=None)

    assert reason.startswith("decision: review only, not counted; cap:")
    assert "support: strict NL OK; RT prior close; local S/N strong" in reason
    assert "shape clean" not in reason
    assert (
        "concerns: nl fail; rt prior far; anchor mismatch; low trace continuity"
        in reason
    )
    assert "poor edge recovery" not in reason


def test_score_breakdown_fields_preserves_public_projection_order() -> None:
    score = EvidenceScore(
        base_score=50,
        positive_points=40,
        negative_points=5,
        raw_score=85,
        score_confidence="HIGH",
        confidence="MEDIUM",
        support_labels=("strict_nl_ok", "local_sn_strong"),
        concern_labels=("trace_quality_review",),
        cap_labels=("trace_quality_cap",),
    )

    assert score_breakdown_fields(score) == (
        ("Final Confidence", "MEDIUM"),
        ("Caps", "trace_quality_cap"),
        ("Raw Score", "85"),
        ("Support", "strict_nl_ok; local_sn_strong"),
        ("Concerns", "trace_quality_review"),
        ("Base Score", "50"),
        ("Positive Points", "40"),
        ("Negative Points", "5"),
    )
