"""Weighted evidence scoring primitives for peak confidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

ConfidenceValue = Literal["HIGH", "MEDIUM", "LOW", "VERY_LOW"]

_CONFIDENCE_RANK: dict[ConfidenceValue, int] = {
    "HIGH": 0,
    "MEDIUM": 1,
    "LOW": 2,
    "VERY_LOW": 3,
}


@dataclass(frozen=True)
class EvidenceSignal:
    label: str
    points: int


@dataclass(frozen=True)
class ConfidenceCap:
    label: str
    max_confidence: ConfidenceValue


@dataclass(frozen=True)
class EvidenceScore:
    base_score: int
    positive_points: int
    negative_points: int
    raw_score: int
    score_confidence: ConfidenceValue
    confidence: ConfidenceValue
    support_labels: tuple[str, ...]
    concern_labels: tuple[str, ...]
    cap_labels: tuple[str, ...]


def confidence_from_score(score: int) -> ConfidenceValue:
    if score >= 80:
        return "HIGH"
    if score >= 60:
        return "MEDIUM"
    if score >= 40:
        return "LOW"
    return "VERY_LOW"


def apply_confidence_caps(
    confidence: ConfidenceValue,
    caps: Sequence[ConfidenceCap],
) -> ConfidenceValue:
    capped_confidence = confidence
    for cap in caps:
        if _CONFIDENCE_RANK[cap.max_confidence] > _CONFIDENCE_RANK[capped_confidence]:
            capped_confidence = cap.max_confidence
    return capped_confidence


def _validate_non_negative_points(
    signals: Sequence[EvidenceSignal],
    evidence_kind: str,
) -> None:
    for signal in signals:
        if signal.points < 0:
            raise ValueError(
                f"{evidence_kind} evidence points must be non-negative: "
                f"{signal.label}={signal.points}"
            )


def score_evidence(
    *,
    positive: Sequence[EvidenceSignal],
    negative: Sequence[EvidenceSignal],
    base_score: int = 50,
    caps: Sequence[ConfidenceCap] = (),
) -> EvidenceScore:
    _validate_non_negative_points(positive, "positive")
    _validate_non_negative_points(negative, "negative")

    positive_points = sum(signal.points for signal in positive)
    negative_points = sum(signal.points for signal in negative)
    raw_score = base_score + positive_points - negative_points
    score_confidence = confidence_from_score(raw_score)
    confidence = apply_confidence_caps(score_confidence, caps)

    return EvidenceScore(
        base_score=base_score,
        positive_points=positive_points,
        negative_points=negative_points,
        raw_score=raw_score,
        score_confidence=score_confidence,
        confidence=confidence,
        support_labels=tuple(signal.label for signal in positive),
        concern_labels=tuple(signal.label for signal in negative),
        cap_labels=tuple(cap.label for cap in caps),
    )
