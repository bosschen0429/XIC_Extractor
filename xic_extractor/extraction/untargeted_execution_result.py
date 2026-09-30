"""Completed retained Untarget execution and its private-store lifetime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from xic_extractor.alignment.completion import AlignmentWorkflowContractError
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodEdge,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetBatchApplicationResult,
)
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.execution._lifecycle import AttemptTerminalOutcome
from xic_extractor.execution.attempt import RuntimeFinalizationReceipt
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.observations import RetainedCaptureEvidence


@dataclass(frozen=True, slots=True)
class CompletedUntargetBatchWorkflowResult:
    runtime_receipt: RuntimeFinalizationReceipt
    application_result: FinalizedUntargetBatchApplicationResult
    # Diagnostic handoff only; these retained owner facts grant no completion
    # or Matrix authority and do not change scientific result equality.
    method_assessments: (
        tuple[CidNlMethodAssessmentRef, ...]
        | ImmutableSequence[CidNlMethodAssessmentRef]
    ) = field(default=(), repr=False, compare=False)
    initial_groups: (
        tuple[CrossSamplePeakGroupHypothesis, ...]
        | ImmutableSequence[CrossSamplePeakGroupHypothesis]
    ) = field(default=(), repr=False, compare=False)
    initial_edges: (
        tuple[CrossSampleNeighborhoodEdge, ...]
        | ImmutableSequence[CrossSampleNeighborhoodEdge]
    ) = field(default=(), repr=False, compare=False)
    discovery_physical_records: (
        tuple[DiscoveryPhysicalFactRecord, ...]
        | ImmutableSequence[DiscoveryPhysicalFactRecord]
    ) = field(default=(), repr=False, compare=False)
    capture_evidence: (
        tuple[RetainedCaptureEvidence, ...] | ImmutableSequence[RetainedCaptureEvidence]
    ) = field(default=(), repr=False, compare=False)
    cleanup_action: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )

    def close(self) -> None:
        """Release underlying transient partition storage and resources."""
        if self.cleanup_action is not None:
            self.cleanup_action()

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_receipt, RuntimeFinalizationReceipt):
            raise AlignmentWorkflowContractError(
                "workflow terminal result requires an owner-minted runtime receipt"
            )
        if (
            self.runtime_receipt.supervisor.terminal_outcome
            is not AttemptTerminalOutcome.SUCCEEDED
        ):
            raise AlignmentWorkflowContractError(
                "workflow terminal result requires runtime outcome succeeded"
            )
        if type(self.application_result) is not FinalizedUntargetBatchApplicationResult:
            raise AlignmentWorkflowContractError(
                "completed Untarget batch requires an exact batch application result"
            )
        if (
            self.runtime_receipt.supervisor.attempt
            is not self.application_result.processing_attempt
        ):
            raise AlignmentWorkflowContractError(
                "completed Untarget batch crossed its runtime attempt"
            )


__all__ = ("CompletedUntargetBatchWorkflowResult",)
