from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.peak_detection.evidence_semantics import (
    DecisionClass,
    EvidenceDecisionSemantics,
)

DECISION_CLASS_RANK: dict[DecisionClass, int] = {
    "accepted": 0,
    "review": 1,
    "not_counted": 2,
    "ambiguous": 3,
    "excluded": 4,
}

DecisionTerm = tuple[str, float]


@dataclass(frozen=True)
class DecisionRecord:
    workflow: str
    unit_id: str
    required_evidence: tuple[str, ...]
    decision_class: DecisionClass
    blockers: tuple[str, ...]
    support: tuple[str, ...]
    gate: tuple[DecisionTerm, ...]
    tie_break: tuple[DecisionTerm, ...]
    projection_authority: str


def decision_blockers(
    semantics: EvidenceDecisionSemantics,
) -> tuple[str, ...]:
    return (
        *semantics.conflict_reasons,
        *semantics.review_reasons,
        *semantics.not_counted_reasons,
        *semantics.exclusion_reasons,
        *semantics.ambiguity_reasons,
    )


def decision_gate_terms(
    semantics: EvidenceDecisionSemantics,
) -> tuple[DecisionTerm, DecisionTerm]:
    blockers = decision_blockers(semantics)
    return (
        ("decision_class_rank", float(DECISION_CLASS_RANK[semantics.decision_class])),
        ("blocker_count", float(len(blockers))),
    )
