"""Method answers required by the existing physical grouping algorithms.

Composition supplies one rule set explicitly. This is not a method registry or
a replacement for method subjects, assessments, or persisted evidence schemas.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner

SeedSupportLevel = Literal["strong", "moderate", "weak"]
DuplicateContext = Literal["none", "same_owner_events", "tail_assignment"]
IndexKey = tuple[str, str]
IndexQuery = tuple[IndexKey, float, float]


@dataclass(frozen=True, slots=True)
class MethodPairEvidence:
    """Method gates surround the physical gates; reason text is not policy."""

    identity_failure: str | None
    measurement_failure: str | None
    seed_support_level: SeedSupportLevel
    duplicate_context: DuplicateContext
    measurement_failure_allows_rt_continuity: bool = False


class MethodFeatureEnvelope(Protocol):
    def add(self, owner: SampleLocalMS1Owner) -> None: ...

    def failures(
        self, owner: SampleLocalMS1Owner, config: AlignmentConfig
    ) -> tuple[str, str]: ...

    def index_entries(
        self, precursor_min: float
    ) -> tuple[tuple[IndexKey, float], ...]: ...


@dataclass(frozen=True, slots=True)
class GroupingRules:
    """Only the decisions consumed by current directional/window inference.

    Index alternatives are a union of intersections. Keys and method distance
    coordinates are opaque to the shared algorithm; exact envelopes still gate
    membership after candidate lookup.
    """

    pair_evidence: Callable[
        [SampleLocalMS1Owner, SampleLocalMS1Owner, AlignmentConfig], MethodPairEvidence
    ]
    support_level: Callable[
        [SampleLocalMS1Owner, SampleLocalMS1Owner, AlignmentConfig], SeedSupportLevel
    ]
    feature_envelope: Callable[[SampleLocalMS1Owner], MethodFeatureEnvelope]
    index_query_alternatives: Callable[
        [SampleLocalMS1Owner, AlignmentConfig], tuple[tuple[IndexQuery, ...], ...]
    ]
    requires_directional_membership: Callable[[Sequence[SampleLocalMS1Owner]], bool]
    trajectory_witness_eligible: Callable[[SampleLocalMS1Owner], bool]
    chemistry_key: Callable[[SampleLocalMS1Owner], tuple[str, str]]
    mass_center: Callable[[Sequence[SampleLocalMS1Owner]], float | None]
    mass_fraction: Callable[[float | None, float | None, AlignmentConfig], float]
    pair_mass_fraction: Callable[
        [SampleLocalMS1Owner, SampleLocalMS1Owner, AlignmentConfig], float
    ]
