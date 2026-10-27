"""Completed retained Untarget execution and its private-store lifetime."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from xic_extractor.alignment.completion import AlignmentWorkflowContractError
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleNeighborhoodEdge,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetBatchApplicationResult,
)
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.execution._lifecycle import AttemptTerminalOutcome
from xic_extractor.execution.attempt import RuntimeFinalizationReceipt
from xic_extractor.execution.result_store import StreamedResultStore
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.observations import RetainedCaptureEvidence

if TYPE_CHECKING:
    from xic_extractor.untargeted_execution.alignment import (
        PreparedUntargetedAlignmentInputs,
    )


@dataclass(slots=True)
class _CompletedResultResources:
    """Shared lifetime of a completed result and its immutable views."""

    store: StreamedResultStore | None = None
    retain_partition_spool: bool = False
    discovery_resources: ExitStack | None = None
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self.store is not None:
                if self.retain_partition_spool:
                    self.store.preserve_results()
                else:
                    self.store.cleanup()
        finally:
            if self.discovery_resources is not None:
                self.discovery_resources.close()


@dataclass(frozen=True, slots=True)
class UntargetedPublicationInputs:
    """The completed run's bound Matrix and explanation inputs, still lazy."""

    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]]
    ms1_apex_mz_by_candidate: Mapping[str, float]
    alignment_config: AlignmentConfig
    known_istd_reference_provenance: Mapping[str, object] | None


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
    _resources: _CompletedResultResources = field(
        default_factory=_CompletedResultResources, repr=False, compare=False
    )
    _publication_inputs: UntargetedPublicationInputs | None = field(
        default=None, repr=False, compare=False
    )

    @classmethod
    def from_runtime(
        cls,
        *,
        runtime_receipt: RuntimeFinalizationReceipt,
        application_result: FinalizedUntargetBatchApplicationResult,
        store: StreamedResultStore,
        retain_partition_spool: bool = False,
    ) -> CompletedUntargetBatchWorkflowResult:
        """Own the lazy result views and the store that keeps them readable.

        Retention publishes a readable record index on close; ordinary results
        remove their private spool. Acquisition cache and worker lifetimes stay
        with execution, not this result.
        """
        return cls(
            runtime_receipt=runtime_receipt,
            application_result=application_result,
            method_assessments=store.sequence("method_assessments"),
            initial_groups=store.sequence("initial_groups"),
            initial_edges=store.sequence("initial_edges"),
            capture_evidence=store.sequence("capture_evidence"),
            _resources=_CompletedResultResources(store, retain_partition_spool),
        )

    def with_discovery_inputs(
        self, inputs: PreparedUntargetedAlignmentInputs
    ) -> CompletedUntargetBatchWorkflowResult:
        """Adopt the prepared Discovery resources along with their lazy records."""
        completed = replace(
            self, discovery_physical_records=inputs.retained_physical_records()
        )
        self._resources.discovery_resources = inputs.take_result_resources()
        return completed

    def bind_publication_inputs(
        self,
        *,
        candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
        alignment_config: AlignmentConfig,
        known_istd_reference: KnownIstdReference | None = None,
    ) -> CompletedUntargetBatchWorkflowResult:
        """Close the Alignment handoff without loading its streamed children.

        The formal completion entry supplies the same inputs used by execution.
        Lower-level runtime results remain usable by explicit-input diagnostics.
        """
        if isinstance(candidates_by_sample, DiscoveryInputSource):
            candidates = candidates_by_sample.feature_view()
            apex_mz = candidates_by_sample.ms1_apex_mz_by_candidate
        else:
            candidates = candidates_by_sample
            apex_mz = {
                candidate.candidate_id: candidate.ms1_apex_mz
                for sample_candidates in candidates_by_sample.values()
                for candidate in sample_candidates
                if candidate.ms1_apex_mz is not None
            }
        return replace(
            self,
            _publication_inputs=UntargetedPublicationInputs(
                candidates_by_sample=candidates,
                ms1_apex_mz_by_candidate=MappingProxyType(apex_mz),
                alignment_config=alignment_config,
                known_istd_reference_provenance=(
                    None
                    if known_istd_reference is None
                    else known_istd_reference.explanation_provenance
                ),
            ),
        )

    @property
    def publication_inputs(self) -> UntargetedPublicationInputs:
        if self._publication_inputs is None:
            raise AlignmentWorkflowContractError(
                "publication requires a completed Alignment input binding"
            )
        return self._publication_inputs

    @property
    def processing_attempt_id(self) -> str:
        return self.application_result.processing_attempt.processing_attempt_id

    def close(self) -> None:
        """Release owned stores once, also when called through a replaced view."""
        self._resources.close()

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
