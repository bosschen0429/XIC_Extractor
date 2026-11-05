"""Numeric evidence scores and diagnostic labels; not product decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class EvidenceSignal:
    label: str
    points: int


@dataclass(frozen=True)
class EvidenceScore:
    base_score: int
    positive_points: int
    negative_points: int
    raw_score: int
    support_labels: tuple[str, ...]
    concern_labels: tuple[str, ...]
    cap_labels: tuple[str, ...]


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
    caps: Sequence[str] = (),
) -> EvidenceScore:
    _validate_non_negative_points(positive, "positive")
    _validate_non_negative_points(negative, "negative")

    positive_points = sum(signal.points for signal in positive)
    negative_points = sum(signal.points for signal in negative)
    raw_score = base_score + positive_points - negative_points

    return EvidenceScore(
        base_score=base_score,
        positive_points=positive_points,
        negative_points=negative_points,
        raw_score=raw_score,
        support_labels=tuple(signal.label for signal in positive),
        concern_labels=tuple(signal.label for signal in negative),
        cap_labels=tuple(caps),
    )
