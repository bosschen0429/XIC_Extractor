import pytest

from xic_extractor.peak_detection.peak_scoring_evidence import (
    EvidenceSignal,
    score_evidence,
)


def test_score_evidence_adds_support_and_subtracts_concerns() -> None:
    result = score_evidence(
        positive=[
            EvidenceSignal("strict_nl_ok", 30),
            EvidenceSignal("local_sn_strong", 10),
        ],
        negative=[
            EvidenceSignal("rt_prior_borderline", 15),
        ],
    )

    assert result.base_score == 50
    assert result.positive_points == 40
    assert result.negative_points == 15
    assert result.raw_score == 75
    assert result.support_labels == ("strict_nl_ok", "local_sn_strong")
    assert result.concern_labels == ("rt_prior_borderline",)


def test_evidence_labels_do_not_change_raw_score() -> None:
    scored = score_evidence(
        positive=[
            EvidenceSignal("strict_nl_ok", 30),
            EvidenceSignal("shape_clean", 10),
        ],
        negative=[],
        caps=["anchor_mismatch_cap"],
    )

    assert scored.raw_score == 90
    assert scored.cap_labels == ("anchor_mismatch_cap",)


def test_score_evidence_rejects_negative_evidence_points() -> None:
    with pytest.raises(
        ValueError,
        match="negative evidence points must be non-negative.*nl_fail.*-45",
    ):
        score_evidence(
            positive=[],
            negative=[EvidenceSignal("nl_fail", -45)],
        )


def test_score_evidence_rejects_negative_support_points() -> None:
    with pytest.raises(
        ValueError,
        match="positive evidence points must be non-negative.*strict_nl_ok.*-30",
    ):
        score_evidence(
            positive=[EvidenceSignal("strict_nl_ok", -30)],
            negative=[],
        )
