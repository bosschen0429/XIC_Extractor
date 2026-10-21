"""Evidence answers consumed by cell closure and conservative recovery.

The retained method adapter binds its own assessment. Shared consumers never
interpret that assessment's result enum or relation schema.
"""

from __future__ import annotations

from typing import Literal, Protocol, TypeAlias

from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import PeakObservationRef

FinalCellEvidenceState: TypeAlias = Literal[
    "direct_observation",
    "ms1_only_backfill_observation",
    "unresolved",
    "rejected_observation",
    "searched_no_observation",
    "unavailable",
    "not_searched",
]


class CellMethodEvidence(Protocol):
    @property
    def supported_observation_ids(self) -> frozenset[str]: ...

    @property
    def backfill_block_reason(self) -> str | None: ...

    @property
    def allows_physical_localization(self) -> bool: ...

    def local_state(
        self, observation: PeakObservationRef | None, hypothesis: PeakHypothesis | None
    ) -> FinalCellEvidenceState: ...

    def recovered_state(
        self, hypothesis: PeakHypothesis | None
    ) -> FinalCellEvidenceState: ...
