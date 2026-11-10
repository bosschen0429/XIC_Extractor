"""Sample-by-group coverage facts retained through final cell assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from xic_extractor.alignment.backfill.completion_evidence import (
    BackfillCompletionProjection,
)
from xic_extractor.alignment.cross_sample.peak_groups import CrossSampleGroupIdentity
from xic_extractor.sample_metadata import SampleMetadata

if TYPE_CHECKING:
    from xic_extractor.alignment.backfill.conservative import (
        ConservativeBackfillCellDecision,
    )

BackfillPlanningReason = Literal[
    "retained_local_owner",
    "retained_unresolved_candidates",
    "local_owner_not_supersedable",
    "direct_support_threshold_not_met",
    "backfill_search_completed_empty",
    "no_discovery_input",
    "backfill_disabled",
]


@dataclass(frozen=True, slots=True)
class BackfillNoRequestDecision:
    """Retain the conservative answer and any completed-empty search evidence."""

    decision: ConservativeBackfillCellDecision
    completion: BackfillCompletionProjection | None = None

    @property
    def variant(self) -> Literal["threshold_not_met", "completed_backfill_empty"]:
        return (
            "threshold_not_met"
            if self.completion is None
            else "completed_backfill_empty"
        )

    @property
    def planning_reason(self) -> BackfillPlanningReason:
        return (
            "direct_support_threshold_not_met"
            if self.completion is None
            else "backfill_search_completed_empty"
        )


@dataclass(frozen=True, slots=True)
class BackfillPlanningPosition:
    group_identity: CrossSampleGroupIdentity
    sample: SampleMetadata
    planning_reason: BackfillPlanningReason
    no_request_decision: BackfillNoRequestDecision | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (
            self.group_identity.group_hypothesis_id,
            self.sample.sample_name,
        )


@dataclass(frozen=True, slots=True)
class BackfillEvaluationCoverageBatch:
    group_identities: tuple[CrossSampleGroupIdentity, ...]
    samples: tuple[SampleMetadata, ...]
    positions: tuple[BackfillPlanningPosition, ...]


__all__ = [
    "BackfillEvaluationCoverageBatch",
    "BackfillNoRequestDecision",
    "BackfillPlanningPosition",
    "BackfillPlanningReason",
]
