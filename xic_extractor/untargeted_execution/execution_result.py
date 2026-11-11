"""Completed retained Untarget execution and its private-store lifetime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from xic_extractor.alignment.completion import AlignmentWorkflowContractError
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetBatchApplicationResult,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.execution.result_store import StreamedResultStore
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
)
from xic_extractor.peak_detection.observations import RetainedCaptureEvidence


@dataclass(slots=True)
class _CompletedResultResources:
    """Shared lifetime of a completed result and its immutable views."""

    store: StreamedResultStore | None = None
    retain_partition_spool: bool = False
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.store is not None:
            if self.retain_partition_spool:
                self.store.preserve_results()
            else:
                self.store.cleanup()


@dataclass(frozen=True, slots=True)
class UntargetedPublicationInputs:
    """The completed run's bound Matrix and explanation inputs, still lazy."""

    peaks_by_sample: Mapping[str, tuple[DiscoveryPeak, ...]]
    ms1_apex_mz_by_candidate: Mapping[str, float]
    precursor_mz_by_candidate: Mapping[str, float]
    neutral_loss_by_tag: Mapping[str, float]
    alignment_config: AlignmentConfig


@dataclass(frozen=True, slots=True)
class CompletedUntargetBatchWorkflowResult:
    application_result: FinalizedUntargetBatchApplicationResult
    # Diagnostic handoff only; these retained owner facts grant no completion
    # or Matrix authority and do not change scientific result equality.
    method_assessments: (
        tuple[CidNlMethodAssessment, ...] | ImmutableSequence[CidNlMethodAssessment]
    ) = field(default=(), repr=False, compare=False)
    initial_groups: (
        tuple[CrossSamplePeakGroupHypothesis, ...]
        | ImmutableSequence[CrossSamplePeakGroupHypothesis]
    ) = field(default=(), repr=False, compare=False)
    capture_evidence: (
        tuple[RetainedCaptureEvidence, ...] | ImmutableSequence[RetainedCaptureEvidence]
    ) = field(default=(), repr=False, compare=False)
    sample_rt_frame: SampleRtFrame | None = field(
        default=None, repr=False, compare=False
    )
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
        application_result: FinalizedUntargetBatchApplicationResult,
        store: StreamedResultStore,
        retain_partition_spool: bool = False,
        sample_rt_frame: SampleRtFrame | None = None,
    ) -> CompletedUntargetBatchWorkflowResult:
        """Own the lazy result views and the store that keeps them readable.

        Retention publishes a readable record index on close; ordinary results
        remove their private spool. Acquisition cache and worker lifetimes stay
        with execution, not this result.
        """
        return cls(
            application_result=application_result,
            sample_rt_frame=sample_rt_frame,
            method_assessments=store.sequence("method_assessments"),
            initial_groups=store.sequence("initial_groups"),
            capture_evidence=store.sequence("capture_evidence"),
            _resources=_CompletedResultResources(store, retain_partition_spool),
        )

    def bind_publication_inputs(
        self,
        *,
        peaks_by_sample: Mapping[str, tuple[DiscoveryPeak, ...]],
        precursor_mz_by_candidate: Mapping[str, float],
        neutral_loss_by_tag: Mapping[str, float],
        alignment_config: AlignmentConfig,
    ) -> CompletedUntargetBatchWorkflowResult:
        """Close the Alignment handoff without loading its streamed children.

        The formal completion entry supplies the same inputs used by execution.
        Lower-level runtime results remain usable by explicit-input diagnostics.
        """
        if isinstance(peaks_by_sample, DiscoveryInputSource):
            candidates = peaks_by_sample.feature_view()
            apex_mz = peaks_by_sample.ms1_apex_mz_by_candidate
        else:
            candidates = peaks_by_sample
            apex_mz = {
                candidate.peak_id: candidate.ms1_apex_mz
                for sample_candidates in peaks_by_sample.values()
                for candidate in sample_candidates
                if candidate.ms1_apex_mz is not None
            }
        return replace(
            self,
            _publication_inputs=UntargetedPublicationInputs(
                peaks_by_sample=candidates,
                ms1_apex_mz_by_candidate=MappingProxyType(apex_mz),
                precursor_mz_by_candidate=MappingProxyType(
                    dict(precursor_mz_by_candidate)
                ),
                neutral_loss_by_tag=MappingProxyType(dict(neutral_loss_by_tag)),
                alignment_config=alignment_config,
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
        return self.application_result.processing_attempt_id

    def close(self) -> None:
        """Release owned stores once, also when called through a replaced view."""
        self._resources.close()


__all__ = ("CompletedUntargetBatchWorkflowResult",)
