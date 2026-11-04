"""Sample-local condition answers on one physical quantitation candidate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from xic_extractor.discovery.delta_mass_patterns import DeltaMassCandidate


@dataclass(frozen=True)
class IdentificationConditionEvidence:
    condition_index: int
    kind: str
    label: str
    status: Literal["matched", "not_matched", "not_acquired"]
    delta_mass_candidates: tuple[DeltaMassCandidate, ...] = ()


@dataclass(frozen=True)
class MethodCandidateEvidence:
    candidate_index: int
    rt_apex_min: float
    rt_start_min: float
    rt_end_min: float
    accepted: bool
    conditions: tuple[IdentificationConditionEvidence, ...]
