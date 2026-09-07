"""Current Discovery-to-assessment adapter for one Untargeted CID-NL subject.

Discovery candidates are receipts.  This adapter reacquires their exact MS1 and
MS2 observations from one owner-bound RAW handle, then closes the existing
CellObservationRef assessment input without fabricating Targeted identity or
Matrix authority.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import numpy as np

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    BatchXICAdapter,
    TraceAcquisitionBatch,
    TraceCoordinateKind,
    acquire_trace_batch,
    mint_trace_acquisition_capability,
)
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    UntargetedCidNlAssessmentInput,
    UntargetedCidNlRecoveryInput,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.cid_nl_evidence_projection import (
    UntargetedCidNlProjectionRef,
    mint_untargeted_cid_nl_projection_ref,
)
from xic_extractor.discovery.models import (
    MS1_FEATURE_BACKED_STATES,
    DiscoveryCandidate,
    DiscoverySettings,
)
from xic_extractor.discovery.ms2_seeds import discovery_seeds_from_scan
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.discovery.strict_seed_receipts import (
    StrictSeedReceiptError,
    read_complete_strict_scan_seeds,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.peak_detection import capture as capture_owner
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelationRef,
    resolve_dda_cid_nl_candidate_set_relation_ref,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceRef,
    mint_cid_nl_evidence_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentContractError,
    CidNlMethodAssessmentRef,
    CidNlTechnicalState,
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    CidNlComposition,
    CidNlMethodSubject,
    CidNlMethodSubjectContractError,
    UntargetedCidNlMethodSubjectBatch,
    cid_nl_method_subjects_from_request,
    validate_cid_nl_method_subject_binding,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
    DdaCidNlPeakSupportRef,
    resolve_dda_cid_nl_peak_support_ref,
)
from xic_extractor.peak_detection.evidence_facts import (
    PhysicalPeakEvaluation,
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.hypotheses import build_peak_hypotheses
from xic_extractor.peak_detection.ms1_trace_detection import detect_ms1_trace_peak
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    mint_peak_observation_ref,
)
from xic_extractor.peak_detection.search_scope import (
    PHYSICAL_CAPTURE_INPUT_KEY,
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    SearchScopeContractError,
    UntargetedSearchScope,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.traces import trace_from_xic_request
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    canonical_current_attempt_artifact_closure,
    resolve_request_acquisition_source,
)
from xic_extractor.raw_reader import (
    Ms2ScanEvent,
    RawReaderError,
    require_owner_bound_raw_handle,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.signal_processing import find_peak_and_area
from xic_extractor.xic_models import XICRequest

VALIDATION_STATUS = "diagnostic_only"
_ADAPTER_NAMESPACE = "xic_extractor.untargeted_cid_nl_real_data_adapter"
UNTARGETED_CID_NL_CAPTURE_TRACE_SOURCE = (
    f"{_ADAPTER_NAMESPACE}:{TraceCoordinateKind.NATIVE_SCAN.value}"
)
_WRITER_ROUNDING_REL_TOL = 5e-6
_WRITER_ROUNDING_ABS_TOL = 5e-6


class UntargetedCidNlRealDataAdapterError(ValueError):
    """Raised when real observations cannot close one Untargeted subject."""


class UntargetedCidNlRealDataRaw(BatchXICAdapter, Protocol):
    def iter_ms2_scans(
        self,
        rt_min: float,
        rt_max: float,
    ) -> Iterator[Ms2ScanEvent]: ...


@dataclass(frozen=True, slots=True)
class UntargetedCidNlRealDataAdapterResult:
    assessment_input: UntargetedCidNlAssessmentInput
    acquisition: TraceAcquisitionBatch | None
    captures: tuple[capture_owner.SampleLocalCapture, ...]
    source_candidate_ids: tuple[str, ...]
    strict_scan_count: int
    lineage_artifacts: tuple[EvidenceArtifactRef, ...]
    validation_status: str = VALIDATION_STATUS


@dataclass(frozen=True, slots=True)
class UntargetedCidNlRecoveryAttemptResult:
    assessment: CidNlMethodAssessmentRef
    recovery_input: UntargetedCidNlRecoveryInput | None
    acquisition: TraceAcquisitionBatch
    lineage_artifacts: tuple[EvidenceArtifactRef, ...]
    validation_status: str = VALIDATION_STATUS


@dataclass(frozen=True, slots=True)
class UntargetedCidNlSampleTraceBatch:
    """One sample-local XIC batch keyed by exact Discovery candidate identity."""

    sample_identity_id: str
    acquisition_source_id: str
    candidate_ids: tuple[str, ...]
    acquisition: TraceAcquisitionBatch
    candidate_trace_indices: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if (
            not self.sample_identity_id.strip()
            or not self.acquisition_source_id.strip()
        ):
            raise UntargetedCidNlRealDataAdapterError(
                "sample trace batch requires owner identities"
            )
        if type(self.candidate_ids) is not tuple or not self.candidate_ids:
            raise UntargetedCidNlRealDataAdapterError(
                "sample trace batch requires exact candidate identities"
            )
        if self.candidate_ids != tuple(sorted(set(self.candidate_ids))):
            raise UntargetedCidNlRealDataAdapterError(
                "sample trace batch candidate identities must be canonical and unique"
            )
        if not isinstance(self.acquisition, TraceAcquisitionBatch):
            raise UntargetedCidNlRealDataAdapterError("invalid sample acquisition")
        indices = self.candidate_trace_indices
        if indices is None:
            indices = tuple(range(len(self.candidate_ids)))
            object.__setattr__(self, "candidate_trace_indices", indices)
        if (
            type(indices) is not tuple
            or len(indices) != len(self.candidate_ids)
            or any(type(index) is not int for index in indices)
            or set(indices) != set(range(len(self.acquisition.traces)))
            or tuple(dict.fromkeys(indices))
            != tuple(range(len(self.acquisition.traces)))
            or self.acquisition.capability.acquisition_source.acquisition_source_id
            != self.acquisition_source_id
        ):
            raise UntargetedCidNlRealDataAdapterError(
                "sample trace batch acquisition does not close its candidates"
            )

    def acquired_trace(self, candidate_id: str) -> AcquiredTrace:
        try:
            index = self.candidate_ids.index(candidate_id)
        except ValueError as exc:
            raise UntargetedCidNlRealDataAdapterError(
                f"sample trace batch has no candidate {candidate_id!r}"
            ) from exc
        assert self.candidate_trace_indices is not None
        return self.acquisition.traces[self.candidate_trace_indices[index]]


@dataclass(frozen=True, slots=True)
class UntargetedCidNlRecoveryTraceBatch:
    """One sample-local recovery phase keyed by exact method-subject identity."""

    sample_identity_id: str
    acquisition_source_id: str
    method_subject_ids: tuple[str, ...]
    group_center_rts: tuple[float, ...]
    requests: tuple[XICRequest, ...]
    acquisition: TraceAcquisitionBatch

    def __post_init__(self) -> None:
        if (
            not self.sample_identity_id.strip()
            or not self.acquisition_source_id.strip()
        ):
            raise UntargetedCidNlRealDataAdapterError(
                "recovery trace batch requires owner identities"
            )
        if (
            type(self.method_subject_ids) is not tuple
            or tuple(zip(self.method_subject_ids, self.group_center_rts))
            != tuple(sorted(set(zip(self.method_subject_ids, self.group_center_rts))))
            or not self.method_subject_ids
            or type(self.group_center_rts) is not tuple
            or type(self.requests) is not tuple
            or not (
                len(self.method_subject_ids)
                == len(self.group_center_rts)
                == len(self.requests)
            )
        ):
            raise UntargetedCidNlRealDataAdapterError(
                "recovery trace batch entries must be canonical and complete"
            )
        if not isinstance(self.acquisition, TraceAcquisitionBatch) or len(
            self.acquisition.traces
        ) != len(self.requests):
            raise UntargetedCidNlRealDataAdapterError(
                "recovery trace batch acquisition does not close its requests"
            )

    def acquired_trace(
        self,
        *,
        method_subject_id: str,
        group_center_rt: float,
        request: XICRequest,
    ) -> AcquiredTrace:
        try:
            index = tuple(zip(self.method_subject_ids, self.group_center_rts)).index(
                (method_subject_id, group_center_rt)
            )
        except ValueError as exc:
            raise UntargetedCidNlRealDataAdapterError(
                "recovery trace batch has no matching method subject"
            ) from exc
        if (
            self.group_center_rts[index] != group_center_rt
            or self.requests[index] != request
        ):
            raise UntargetedCidNlRealDataAdapterError(
                "recovery trace batch request crossed its owner decision"
            )
        return self.acquisition.traces[index]


class _UntargetedRealDataWorkObserver:
    """Retain already-minted facts so runtime failure can quarantine them."""

    __slots__ = ("_artifacts", "_processing_owners", "acquisition")

    def __init__(self, processing_owners: ProcessingOwnerReferences) -> None:
        self._processing_owners = processing_owners
        self.acquisition: TraceAcquisitionBatch | None = None
        self._artifacts: dict[str, EvidenceArtifactRef] = {}

    def record_acquisition(self, acquisition: TraceAcquisitionBatch) -> None:
        if self.acquisition is not None and self.acquisition is not acquisition:
            raise UntargetedCidNlRealDataAdapterError(
                "one bounded work unit cannot replace its observed acquisition"
            )
        self.acquisition = acquisition

    def record_artifact(self, artifact: EvidenceArtifactRef) -> None:
        attempt = self._processing_owners.attempt
        if (
            artifact.producer_processing_request_id != attempt.processing_request_id
            or artifact.producer_processing_attempt_id != attempt.processing_attempt_id
        ):
            raise UntargetedCidNlRealDataAdapterError(
                "observer artifact must bind the current processing attempt"
            )
        existing = self._artifacts.get(artifact.evidence_artifact_id)
        if existing is not None and existing != artifact:
            raise UntargetedCidNlRealDataAdapterError(
                "observer artifact identity was rebound"
            )
        self._artifacts[artifact.evidence_artifact_id] = artifact

    def artifact_closure(self) -> tuple[EvidenceArtifactRef, ...]:
        return canonical_current_attempt_artifact_closure(
            producer_attempt=self._processing_owners.attempt,
            artifacts=tuple(self._artifacts.values()),
        )

    def observed_artifacts(self) -> tuple[EvidenceArtifactRef, ...]:
        return tuple(
            sorted(
                self._artifacts.values(),
                key=lambda artifact: artifact.evidence_artifact_id,
            )
        )


def _sample_capture_requests(
    *,
    processing_owners: ProcessingOwnerReferences,
    candidates: tuple[DiscoveryCandidate, ...],
    settings: DiscoverySettings,
    profile: SignalProcessingProfile | None,
    method_subject: CidNlMethodSubject | None = None,
) -> tuple[tuple[XICRequest, ...], tuple[int, ...]]:
    requests = {
        item.candidate_id: XICRequest(
            mz=item.precursor_mz,
            rt_min=item.ms1_search_rt_min,
            rt_max=item.ms1_search_rt_max,
            ppm_tol=settings.precursor_mz_tolerance_ppm,
        )
        for item in candidates
    }
    if profile is not None:
        subjects = method_subject or cid_nl_method_subjects_from_request(
            processing_owners.request
        )
        if isinstance(subjects, UntargetedCidNlMethodSubjectBatch):
            memberships = dict(subjects.candidate_memberships)
            subject_values = {
                item.method_subject_id: item for item in subjects.subjects
            }
            grouped: dict[str, list[DiscoveryCandidate]] = {}
            for item in candidates:
                subject_id = memberships.get(item.candidate_id)
                if subject_id not in subject_values:
                    raise UntargetedCidNlRealDataAdapterError(
                        "capture candidate lacks method subject"
                    )
                grouped.setdefault(subject_id, []).append(item)
        else:
            subject_values = {subjects.method_subject_id: subjects}
            grouped = {subjects.method_subject_id: list(candidates)}
        for subject_id, members in grouped.items():
            subject = subject_values[subject_id]
            ordered = sorted(
                members, key=lambda item: (item.ms1_search_rt_min, item.candidate_id)
            )
            components: list[tuple[float, list[DiscoveryCandidate]]] = []
            for item in ordered:
                if not components or item.ms1_search_rt_min > components[-1][0]:
                    components.append((item.ms1_search_rt_max, [item]))
                else:
                    components[-1] = (
                        max(components[-1][0], item.ms1_search_rt_max),
                        components[-1][1],
                    )
                    components[-1][1].append(item)
            for right, members in components:
                if len(members) > 1:
                    request = XICRequest(
                        mz=subject.precursor_mz,
                        rt_min=members[0].ms1_search_rt_min,
                        rt_max=right,
                        ppm_tol=subject.precursor_tolerance_ppm,
                    )
                    for item in members:
                        requests[item.candidate_id] = request
    unique: dict[XICRequest, int] = {}
    indices = []
    if profile is None:
        return tuple(requests[item.candidate_id] for item in candidates), tuple(
            range(len(candidates))
        )
    for item in candidates:
        request = requests[item.candidate_id]
        indices.append(unique.setdefault(request, len(unique)))
    return tuple(unique), tuple(indices)


def acquire_untargeted_cid_nl_sample_trace_batch(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    candidates: tuple[DiscoveryCandidate, ...],
    settings: DiscoverySettings,
    raw: UntargetedCidNlRealDataRaw,
    signal_processing_profile: SignalProcessingProfile | None = None,
) -> UntargetedCidNlSampleTraceBatch | None:
    """Acquire all eligible candidate XICs for one sample in one adapter call."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise UntargetedCidNlRealDataAdapterError(
            "processing_owners has the wrong owner type"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise UntargetedCidNlRealDataAdapterError("sample has the wrong owner type")
    if not isinstance(settings, DiscoverySettings):
        raise UntargetedCidNlRealDataAdapterError("settings has the wrong owner type")
    if type(candidates) is not tuple or any(
        not isinstance(candidate, DiscoveryCandidate) for candidate in candidates
    ):
        raise UntargetedCidNlRealDataAdapterError(
            "candidates must be current DiscoveryCandidate receipts"
        )
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    if candidate_ids != tuple(sorted(set(candidate_ids))):
        raise UntargetedCidNlRealDataAdapterError(
            "sample trace batch candidates must be canonical and unique"
        )
    raw_stem = sample.metadata.raw_stem
    if any(
        candidate.sample_stem != raw_stem or candidate.raw_file.stem != raw_stem
        for candidate in candidates
    ):
        raise UntargetedCidNlRealDataAdapterError(
            "sample trace batch candidates crossed the resolved sample"
        )
    capture_candidates = tuple(
        candidate
        for candidate in candidates
        if candidate.ms1_peak_found
        and candidate.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
    )
    if not capture_candidates:
        return None
    _validate_raw_adapter(raw)
    source = resolve_request_acquisition_source(
        processing_owners=processing_owners,
        sample=sample,
    )
    try:
        bound_raw = require_owner_bound_raw_handle(raw, source)
    except RawReaderError as exc:
        raise UntargetedCidNlRealDataAdapterError(
            "raw handle is not owner-bound to the resolved acquisition source"
        ) from exc
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace=f"{_ADAPTER_NAMESPACE}.sample_batch",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    requests, indices = _sample_capture_requests(
        processing_owners=processing_owners,
        candidates=capture_candidates,
        settings=settings,
        profile=signal_processing_profile,
    )
    acquisition = _freeze_acquisition(
        acquire_trace_batch(capability, requests, bound_raw)
    )
    return UntargetedCidNlSampleTraceBatch(
        sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=source.acquisition_source_id,
        candidate_ids=tuple(candidate.candidate_id for candidate in capture_candidates),
        acquisition=acquisition,
        candidate_trace_indices=indices,
    )


def acquire_untargeted_cid_nl_recovery_trace_batch(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    recovery_requests: tuple[tuple[CidNlMethodSubject, float], ...],
    peak_config: ExtractionConfig,
    raw: UntargetedCidNlRealDataRaw,
) -> UntargetedCidNlRecoveryTraceBatch:
    """Acquire one sample's owner-authorized recovery requests in one phase."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise UntargetedCidNlRealDataAdapterError(
            "processing_owners has the wrong owner type"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise UntargetedCidNlRealDataAdapterError("sample has the wrong owner type")
    if not isinstance(peak_config, ExtractionConfig):
        raise UntargetedCidNlRealDataAdapterError(
            "peak_config has the wrong owner type"
        )
    if (
        type(recovery_requests) is not tuple
        or not recovery_requests
        or any(
            type(item) is not tuple
            or len(item) != 2
            or not isinstance(item[0], CidNlMethodSubject)
            or isinstance(item[1], bool)
            or not isinstance(item[1], (int, float))
            or not math.isfinite(float(item[1]))
            or float(item[1]) < 0.0
            for item in recovery_requests
        )
    ):
        raise UntargetedCidNlRealDataAdapterError(
            "recovery requests must be a non-empty typed tuple"
        )
    method_subject_ids = tuple(item[0].method_subject_id for item in recovery_requests)
    request_keys = tuple(
        (subject.method_subject_id, center) for subject, center in recovery_requests
    )
    if request_keys != tuple(sorted(set(request_keys))):
        raise UntargetedCidNlRealDataAdapterError(
            "recovery requests must be canonical and unique"
        )
    for method_subject, _group_center_rt in recovery_requests:
        try:
            validate_cid_nl_method_subject_binding(
                method_subject,
                processing_owners=processing_owners,
            )
        except CidNlMethodSubjectContractError as exc:
            raise UntargetedCidNlRealDataAdapterError(str(exc)) from exc
    _validate_raw_adapter(raw)
    source = resolve_request_acquisition_source(
        processing_owners=processing_owners,
        sample=sample,
    )
    try:
        bound_raw = require_owner_bound_raw_handle(raw, source)
    except RawReaderError as exc:
        raise UntargetedCidNlRealDataAdapterError(
            "raw handle is not owner-bound to the resolved acquisition source"
        ) from exc
    half_window = peak_config.nl_rt_anchor_half_window_min
    requests = tuple(
        XICRequest(
            mz=method_subject.precursor_mz,
            rt_min=max(0.0, float(group_center_rt) - half_window),
            rt_max=float(group_center_rt) + half_window,
            ppm_tol=method_subject.precursor_tolerance_ppm,
        )
        for method_subject, group_center_rt in recovery_requests
    )
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace=f"{_ADAPTER_NAMESPACE}.recovery_batch",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    acquisition = _freeze_acquisition(
        acquire_trace_batch(capability, requests, bound_raw)
    )
    return UntargetedCidNlRecoveryTraceBatch(
        sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=source.acquisition_source_id,
        method_subject_ids=method_subject_ids,
        group_center_rts=tuple(float(item[1]) for item in recovery_requests),
        requests=requests,
        acquisition=acquisition,
    )


def adapt_current_discovery_sample_subject(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    method_subject: CidNlMethodSubject,
    candidates: tuple[DiscoveryCandidate, ...],
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw: UntargetedCidNlRealDataRaw | None,
    _observer: _UntargetedRealDataWorkObserver | None = None,
    _preacquired_traces: UntargetedCidNlSampleTraceBatch | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
    physical_records: tuple[DiscoveryPhysicalFactRecord, ...] = (),
) -> UntargetedCidNlRealDataAdapterResult:
    """Reacquire a subject and mint its complete CellObservationRef input."""

    _validate_inputs(
        processing_owners=processing_owners,
        sample=sample,
        method_subject=method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
    )
    bounded = _bounded_subject_candidates(
        candidates,
        sample=sample,
        method_subject=method_subject,
        settings=settings,
    )
    support_policy = DdaCidNlPeakSupportPolicy(
        precursor_tolerance_da=max(
            method_subject.precursor_mz
            * method_subject.precursor_tolerance_ppm
            / 1_000_000.0,
            _WRITER_ROUNDING_ABS_TOL,
        ),
        neutral_loss_tolerance_ppm=settings.nl_tolerance_ppm,
    )
    if not bounded:
        assessment = _mint_assessment(
            processing_owners=processing_owners,
            sample=sample,
            method_subject=method_subject,
            settings=settings,
            support_policy=support_policy,
        )
        if _observer is not None:
            _observer.record_artifact(assessment.artifact)
        lineage = canonical_current_attempt_artifact_closure(
            producer_attempt=processing_owners.attempt,
            artifacts=(assessment.artifact,),
        )
        return UntargetedCidNlRealDataAdapterResult(
            assessment_input=UntargetedCidNlAssessmentInput(
                sample=sample,
                assessment=assessment,
            ),
            acquisition=None,
            captures=(),
            source_candidate_ids=(),
            strict_scan_count=0,
            lineage_artifacts=lineage,
        )
    if raw is None:
        raise UntargetedCidNlRealDataAdapterError(
            "bounded subject universe requires an owner-opened RAW adapter"
        )
    _validate_raw_adapter(raw)
    source = resolve_request_acquisition_source(
        processing_owners=processing_owners,
        sample=sample,
    )
    try:
        bound_raw = require_owner_bound_raw_handle(raw, source)
    except RawReaderError as exc:
        raise UntargetedCidNlRealDataAdapterError(
            "raw handle is not owner-bound to the resolved acquisition source"
        ) from exc
    try:
        strict_scan_seeds = read_complete_strict_scan_seeds(
            bound_raw,
            candidates=tuple(
                candidate
                for candidate in bounded
                if candidate.precursor_mz_basis == "scan_precursor"
            ),
            settings=settings,
            preserve_configured_losses=method_subject.composition is not None,
        )
        strict_scan_seeds = tuple(
            item
            for item in strict_scan_seeds
            if _within_ppm(
                item[1].precursor_mz,
                method_subject.precursor_mz,
                method_subject.precursor_tolerance_ppm,
            )
        )
    except StrictSeedReceiptError as exc:
        raise UntargetedCidNlRealDataAdapterError(str(exc)) from exc

    peak_candidates = tuple(
        candidate
        for candidate in bounded
        if candidate.ms1_peak_found
        and candidate.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
    )
    search_scope = _untargeted_search_scope(
        processing_owners=processing_owners,
        sample=sample,
        acquisition_source_id=source.acquisition_source_id,
        method_subject=method_subject,
        candidates=candidates,
        capture_candidates=peak_candidates,
        peak_config=peak_config,
        signal_processing_profile=signal_processing_profile,
        physical_records=physical_records,
    )
    acquisition, observations, captures = _reacquire_peak_observations(
        processing_owners=processing_owners,
        sample=sample,
        candidates=peak_candidates,
        settings=settings,
        peak_config=peak_config,
        raw=bound_raw,
        source=source,
        search_scope=search_scope,
        method_subject=method_subject,
        _observer=_observer,
        _preacquired_traces=_preacquired_traces,
    )
    if _observer is not None:
        for observation in observations:
            _observer.record_artifact(observation.artifact)
    evidence_refs: list[CidNlEvidenceRef] = []
    projections: list[UntargetedCidNlProjectionRef] = []
    individual_refs: list[DdaCidNlPeakSupportRef] = []
    set_refs: list[DdaCidNlCandidateSetRelationRef] = []
    candidate_universe = tuple(
        sorted(
            observations,
            key=lambda item: item.content.peak_observation_content_id,
        )
    )
    for _scan, raw_seed in strict_scan_seeds:
        seed = replace(raw_seed, acquisition_source_id=source.acquisition_source_id)
        evidence = mint_cid_nl_evidence_ref(
            processing_owners=processing_owners,
            sample=sample,
            scan_number=seed.scan_number,
            precursor_mz=seed.precursor_mz,
            product_mz=seed.product_mz,
            configured_neutral_loss_da=seed.configured_neutral_loss_da,
            observed_neutral_loss_da=seed.observed_neutral_loss_da,
            observed_loss_error_ppm=seed.observed_loss_error_ppm,
            scan_rt_min=seed.rt,
            product_intensity=seed.product_intensity,
        )
        projection = mint_untargeted_cid_nl_projection_ref(
            processing_owners=processing_owners,
            sample=sample,
            canonical_evidence=evidence,
            discovery_seed=seed,
        )
        evidence_refs.append(evidence)
        projections.append(projection)
        if _observer is not None:
            _observer.record_artifact(evidence.artifact)
            _observer.record_artifact(projection.artifact)
        relation_kind = _relation_kind(
            evidence=evidence,
            candidate_peaks=candidate_universe,
            policy=support_policy,
        )
        if relation_kind == "individual":
            support = resolve_dda_cid_nl_peak_support_ref(
                processing_owners=processing_owners,
                sample=sample,
                canonical_evidence=evidence,
                candidate_peaks=candidate_universe,
                policy=support_policy,
            )
            individual_refs.append(support)
            if _observer is not None:
                _observer.record_artifact(support.artifact)
        elif relation_kind == "set":
            relation = resolve_dda_cid_nl_candidate_set_relation_ref(
                processing_owners=processing_owners,
                sample=sample,
                canonical_evidence=evidence,
                candidate_peaks=candidate_universe,
                policy=support_policy,
            )
            set_refs.append(relation)
            if _observer is not None:
                _observer.record_artifact(relation.artifact)

    assessment = _mint_assessment(
        processing_owners=processing_owners,
        sample=sample,
        method_subject=method_subject,
        settings=settings,
        support_policy=support_policy,
        candidate_peaks=candidate_universe,
        evidence_refs=tuple(evidence_refs),
        individual_support_refs=tuple(individual_refs),
        set_relation_refs=tuple(set_refs),
    )
    if _observer is not None:
        _observer.record_artifact(assessment.artifact)
    lineage = canonical_current_attempt_artifact_closure(
        producer_attempt=processing_owners.attempt,
        artifacts=(
            *(observation.artifact for observation in candidate_universe),
            *(evidence.artifact for evidence in evidence_refs),
            *(projection.artifact for projection in projections),
            *(support.artifact for support in individual_refs),
            *(relation.artifact for relation in set_refs),
            assessment.artifact,
        ),
    )
    return UntargetedCidNlRealDataAdapterResult(
        assessment_input=UntargetedCidNlAssessmentInput(
            sample=sample,
            assessment=assessment,
            discovery_projections=tuple(projections),
        ),
        acquisition=acquisition,
        captures=captures,
        source_candidate_ids=tuple(item.candidate_id for item in bounded),
        strict_scan_count=len({scan.scan_number for scan, _ in strict_scan_seeds}),
        lineage_artifacts=lineage,
    )


def recover_untargeted_cid_nl_sample_subject(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    method_subject: CidNlMethodSubject,
    group_center_rt: float,
    support_policy: DdaCidNlPeakSupportPolicy,
    peak_config: ExtractionConfig,
    raw: UntargetedCidNlRealDataRaw | None,
    settings: DiscoverySettings | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
    _observer: _UntargetedRealDataWorkObserver | None = None,
    _preacquired_traces: UntargetedCidNlRecoveryTraceBatch | None = None,
) -> UntargetedCidNlRecoveryAttemptResult:
    """Revisit one eligible HOLD cell without granting direct SUPPORT.

    This finite ObservationRepair adapter remains until that owner receives an
    issued ``UntargetedSearchScope``. It is not a fallback for initial capture.
    """

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise UntargetedCidNlRealDataAdapterError(
            "processing_owners has the wrong owner type"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise UntargetedCidNlRealDataAdapterError("sample has the wrong owner type")
    if not isinstance(method_subject, CidNlMethodSubject):
        raise UntargetedCidNlRealDataAdapterError(
            "method_subject has the wrong owner type"
        )
    if not isinstance(peak_config, ExtractionConfig):
        raise UntargetedCidNlRealDataAdapterError(
            "peak_config has the wrong owner type"
        )
    if not isinstance(support_policy, DdaCidNlPeakSupportPolicy):
        raise UntargetedCidNlRealDataAdapterError(
            "support_policy has the wrong owner type"
        )
    if (
        isinstance(group_center_rt, bool)
        or not isinstance(group_center_rt, (int, float))
        or not math.isfinite(float(group_center_rt))
        or float(group_center_rt) < 0.0
    ):
        raise UntargetedCidNlRealDataAdapterError(
            "group_center_rt must be finite and non-negative"
        )
    try:
        validate_cid_nl_method_subject_binding(
            method_subject,
            processing_owners=processing_owners,
        )
    except CidNlMethodSubjectContractError as exc:
        raise UntargetedCidNlRealDataAdapterError(str(exc)) from exc
    if signal_processing_profile is None and PHYSICAL_CAPTURE_INPUT_KEY in json.loads(
        processing_owners.request.normalized_scientific_inputs_json
    ):
        raise UntargetedCidNlRealDataAdapterError(
            "physical recovery lost its request-bound profile"
        )
    source = resolve_request_acquisition_source(
        processing_owners=processing_owners,
        sample=sample,
    )
    half_window = peak_config.nl_rt_anchor_half_window_min
    query_rt_min = max(0.0, float(group_center_rt) - half_window)
    query_rt_max = float(group_center_rt) + half_window
    request = XICRequest(
        mz=method_subject.precursor_mz,
        rt_min=query_rt_min,
        rt_max=query_rt_max,
        ppm_tol=method_subject.precursor_tolerance_ppm,
    )
    if _preacquired_traces is None:
        if raw is None:
            raise UntargetedCidNlRealDataAdapterError(
                "recovery requires owner-opened RAW or preacquired traces"
            )
        _validate_raw_adapter(raw)
        try:
            bound_raw = require_owner_bound_raw_handle(raw, source)
        except RawReaderError as exc:
            raise UntargetedCidNlRealDataAdapterError(
                "raw handle is not owner-bound to the resolved acquisition source"
            ) from exc
        capability = mint_trace_acquisition_capability(
            source,
            adapter_namespace=f"{_ADAPTER_NAMESPACE}.recovery",
            coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
        )
        acquisition = _freeze_acquisition(
            acquire_trace_batch(capability, (request,), bound_raw)
        )
        acquired = acquisition.traces[0]
    else:
        if (
            _preacquired_traces.sample_identity_id != sample.identity.sample_identity_id
            or _preacquired_traces.acquisition_source_id != source.acquisition_source_id
        ):
            raise UntargetedCidNlRealDataAdapterError(
                "recovery trace batch crossed its sample or acquisition source"
            )
        acquisition = _preacquired_traces.acquisition
        acquired = _preacquired_traces.acquired_trace(
            method_subject_id=method_subject.method_subject_id,
            group_center_rt=float(group_center_rt),
            request=request,
        )
    if _observer is not None:
        _observer.record_acquisition(acquisition)
    observations: tuple[PeakObservationRef, ...] = ()
    if signal_processing_profile is not None:
        trace = trace_from_xic_request(
            sample_name=sample.metadata.sample_name,
            request=request,
            xic_trace=acquired.trace,
            source=f"{_ADAPTER_NAMESPACE}.recovery:{TraceCoordinateKind.NATIVE_SCAN.value}",
            acquisition_source_id=source.acquisition_source_id,
        )
        observation = capture_owner.capture_physical_recovery_observation(
            processing_owners=processing_owners,
            sample=sample,
            method_subject=method_subject,
            trace=trace,
            group_center_rt=float(group_center_rt),
            profile=signal_processing_profile,
            resolver_mode=peak_config.resolver_mode,
        )
        observations = (observation,) if observation is not None else ()
    else:
        detection = detect_ms1_trace_peak(
            acquired.trace.rt,
            acquired.trace.intensity,
            peak_config=peak_config,
            preferred_rt=float(group_center_rt),
            peak_finder=find_peak_and_area,
        )
        if detection.status == "detected" and detection.result is not None:
            hypotheses = build_peak_hypotheses(
                analysis_mode="untargeted",
                common_evidence_source_id=method_subject.method_subject_id,
                sample_name=sample.metadata.sample_name,
                target_label=(
                    f"untargeted:{method_subject.precursor_mz:.8g}:"
                    f"{method_subject.label}:recovery"
                ),
                role="Analyte",
                istd_pair="",
                resolver_mode=peak_config.resolver_mode,
                peak_result=detection.result,
                rt=detection.rt,
                intensity=detection.intensity,
                baseline_integration_method="asls",
                ms1_morphology_smoothing_window_points=(
                    peak_config.ms1_morphology_smoothing_window_points
                ),
            )
            trace = trace_from_xic_request(
                sample_name=sample.metadata.sample_name,
                request=request,
                xic_trace=acquired.trace,
                source=(
                    f"{_ADAPTER_NAMESPACE}.recovery:{TraceCoordinateKind.NATIVE_SCAN.value}"
                ),
                acquisition_source_id=source.acquisition_source_id,
            )
            observations = tuple(
                sorted(
                    (
                        mint_peak_observation_ref(
                            processing_owners=processing_owners,
                            sample=sample,
                            trace=trace,
                            integration=hypothesis.integration,
                            resolver_mode=peak_config.resolver_mode,
                            proposal_sources=hypothesis.audit.proposal_sources,
                        )
                        for hypothesis in hypotheses
                    ),
                    key=lambda item: item.content.peak_observation_content_id,
                )
            )
    if _observer is not None:
        for observation in observations:
            _observer.record_artifact(observation.artifact)
    recovery_evidence: tuple[CidNlEvidenceRef, ...] = ()
    recovery_support: tuple[DdaCidNlPeakSupportRef, ...] = ()
    recovery_sets: tuple[DdaCidNlCandidateSetRelationRef, ...] = ()
    scanned_count = 0
    if method_subject.composition is not None:
        if raw is None or settings is None:
            raise UntargetedCidNlRealDataAdapterError(
                "composite recovery requires full settings and owner-opened MS2 source"
            )
        if settings.tag_combine_mode == "single":
            raise UntargetedCidNlRealDataAdapterError(
                "Composite recovery requires union or intersection"
            )
        expected = CidNlComposition(
            tags=tuple(
                (profile.tag, profile.neutral_loss_da)
                for profile in settings.neutral_loss_profiles
            ),
            combine_mode=settings.tag_combine_mode,
        )
        if expected != method_subject.composition:
            raise UntargetedCidNlRealDataAdapterError(
                "recovery composition mismatches the method subject"
            )
        bound_raw = require_owner_bound_raw_handle(raw, source)
        recovery_evidence, recovery_support, recovery_sets, scanned_count = (
            _composite_recovery_evidence(
                processing_owners=processing_owners,
                sample=sample,
                method_subject=method_subject,
                raw=bound_raw,
                settings=settings,
                query_rt_min=query_rt_min,
                query_rt_max=query_rt_max,
                observations=observations,
                support_policy=support_policy,
            )
        )
        if _observer is not None:
            for artifact in (
                *(ref.artifact for ref in recovery_evidence),
                *(ref.artifact for ref in recovery_support),
                *(ref.artifact for ref in recovery_sets),
            ):
                _observer.record_artifact(artifact)
    try:
        assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=processing_owners,
            sample=sample,
            method_subject=method_subject,
            detector_invocation_id="untargeted-cid-nl-backfill-recovery:v1",
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=query_rt_min,
                query_rt_max=query_rt_max,
                support_policy=support_policy,
                acquisition_supported=True,
                search_completed=True,
                scanned_event_count=scanned_count,
                reason_code=None,
            ),
            candidate_peaks=observations,
            evidence_refs=recovery_evidence,
            individual_support_refs=recovery_support,
            set_relation_refs=recovery_sets,
        )
    except CidNlMethodAssessmentContractError as exc:
        raise UntargetedCidNlRealDataAdapterError(str(exc)) from exc
    if _observer is not None:
        _observer.record_artifact(assessment.artifact)
    lineage = canonical_current_attempt_artifact_closure(
        producer_attempt=processing_owners.attempt,
        artifacts=(
            *(observation.artifact for observation in observations),
            *(ref.artifact for ref in recovery_evidence),
            *(ref.artifact for ref in recovery_support),
            *(ref.artifact for ref in recovery_sets),
            assessment.artifact,
        ),
    )
    recovery_input = (
        UntargetedCidNlRecoveryInput(
            sample=sample,
            candidate_assessment=assessment,
        )
        if observations and assessment.content.method_result == "HOLD"
        else None
    )
    return UntargetedCidNlRecoveryAttemptResult(
        assessment=assessment,
        recovery_input=recovery_input,
        acquisition=acquisition,
        lineage_artifacts=lineage,
    )


def _composite_recovery_evidence(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    method_subject: CidNlMethodSubject,
    raw: UntargetedCidNlRealDataRaw,
    settings: DiscoverySettings,
    query_rt_min: float,
    query_rt_max: float,
    observations: tuple[PeakObservationRef, ...],
    support_policy: DdaCidNlPeakSupportPolicy,
) -> tuple[
    tuple[CidNlEvidenceRef, ...],
    tuple[DdaCidNlPeakSupportRef, ...],
    tuple[DdaCidNlCandidateSetRelationRef, ...],
    int,
]:
    """Evaluate the configured loss set on the recovered physical universe."""
    evidence_refs: list[CidNlEvidenceRef] = []
    individual_refs: list[DdaCidNlPeakSupportRef] = []
    set_refs: list[DdaCidNlCandidateSetRelationRef] = []
    scan_numbers: set[int] = set()
    for event in raw.iter_ms2_scans(query_rt_min, query_rt_max):
        if event.parse_error is not None or event.scan is None:
            raise UntargetedCidNlRealDataAdapterError(
                "composite recovery MS2 acquisition did not complete"
            )
        scan = event.scan
        if scan.scan_number in scan_numbers:
            raise UntargetedCidNlRealDataAdapterError(
                "duplicate acquisition scan during composite recovery"
            )
        scan_numbers.add(scan.scan_number)
        for seed in discovery_seeds_from_scan(
            scan, raw_file=Path(sample.metadata.raw_stem), settings=settings
        ):
            if seed.precursor_mz_basis != "scan_precursor" or not _within_ppm(
                seed.precursor_mz,
                method_subject.precursor_mz,
                method_subject.precursor_tolerance_ppm,
            ):
                continue
            evidence = mint_cid_nl_evidence_ref(
                processing_owners=processing_owners,
                sample=sample,
                scan_number=seed.scan_number,
                precursor_mz=seed.precursor_mz,
                product_mz=seed.product_mz,
                configured_neutral_loss_da=seed.configured_neutral_loss_da,
                observed_neutral_loss_da=seed.observed_neutral_loss_da,
                observed_loss_error_ppm=seed.observed_loss_error_ppm,
                scan_rt_min=seed.rt,
                product_intensity=seed.product_intensity,
            )
            evidence_refs.append(evidence)
            kind = _relation_kind(
                evidence=evidence, candidate_peaks=observations, policy=support_policy
            )
            if kind == "individual":
                individual_refs.append(
                    resolve_dda_cid_nl_peak_support_ref(
                        processing_owners=processing_owners,
                        sample=sample,
                        canonical_evidence=evidence,
                        candidate_peaks=observations,
                        policy=support_policy,
                    )
                )
            elif kind == "set":
                set_refs.append(
                    resolve_dda_cid_nl_candidate_set_relation_ref(
                        processing_owners=processing_owners,
                        sample=sample,
                        canonical_evidence=evidence,
                        candidate_peaks=observations,
                        policy=support_policy,
                    )
                )
    return (
        tuple(evidence_refs),
        tuple(individual_refs),
        tuple(set_refs),
        len(scan_numbers),
    )


def _reacquire_peak_observations(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    candidates: tuple[DiscoveryCandidate, ...],
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw: UntargetedCidNlRealDataRaw,
    source,
    search_scope: UntargetedSearchScope,
    method_subject: CidNlMethodSubject | None = None,
    _observer: _UntargetedRealDataWorkObserver | None = None,
    _preacquired_traces: UntargetedCidNlSampleTraceBatch | None = None,
) -> tuple[
    TraceAcquisitionBatch | None,
    tuple[PeakObservationRef, ...],
    tuple[capture_owner.SampleLocalCapture, ...],
]:
    if not candidates:
        return None, (), ()
    requests, indices = _sample_capture_requests(
        processing_owners=processing_owners,
        candidates=candidates,
        settings=settings,
        profile=search_scope.method_constraints.signal_processing_profile,
        method_subject=method_subject,
    )
    if _preacquired_traces is None:
        capability = mint_trace_acquisition_capability(
            source,
            adapter_namespace=_ADAPTER_NAMESPACE,
            coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
        )
        acquisition = _freeze_acquisition(
            acquire_trace_batch(capability, requests, raw)
        )
        acquired_traces = tuple(acquisition.traces[index] for index in indices)
    else:
        if (
            _preacquired_traces.sample_identity_id != sample.identity.sample_identity_id
            or _preacquired_traces.acquisition_source_id != source.acquisition_source_id
        ):
            raise UntargetedCidNlRealDataAdapterError(
                "preacquired traces crossed their sample or acquisition source"
            )
        acquired_traces = tuple(
            _preacquired_traces.acquired_trace(candidate.candidate_id)
            for candidate in candidates
        )
        acquisition = _preacquired_traces.acquisition
    if any(
        acquired.coordinate.request != requests[index]
        for acquired, index in zip(acquired_traces, indices, strict=True)
    ):
        raise UntargetedCidNlRealDataAdapterError(
            "preacquired trace does not match the candidate capture plan"
        )
    if _observer is not None:
        _observer.record_acquisition(acquisition)
    observations: list[PeakObservationRef] = []
    captures: list[capture_owner.SampleLocalCapture] = []
    for candidate, acquired in zip(candidates, acquired_traces, strict=True):
        trace = trace_from_xic_request(
            sample_name=sample.metadata.sample_name,
            request=acquired.coordinate.request,
            xic_trace=acquired.trace,
            source=UNTARGETED_CID_NL_CAPTURE_TRACE_SOURCE,
            acquisition_source_id=source.acquisition_source_id,
        )
        binding = next(
            item
            for item in search_scope.capture_bindings
            if item.candidate_id == candidate.candidate_id
        )
        capture_scope = search_scope
        if binding.physical_evaluation is not None and (
            trace.mz != binding.query_mz
            or trace.rt_min != binding.query_rt_min
            or trace.rt_max != binding.query_rt_max
            or trace.ppm_tol != binding.query_ppm_tol
        ):
            _validate_physical_candidate_receipt(candidate, binding.physical_evaluation)
            profile = search_scope.method_constraints.signal_processing_profile
            assert profile is not None
            prepared = prepare_physical_signal(
                trace.rt,
                trace.intensity,
                profile=profile,
            )
            fresh = evaluate_physical_peak(
                prepared,
                selection_subject_id=binding.physical_evaluation.question.selection_subject_id,
                query_rt_min=binding.preferred_rt,
                request_context_rt_start_min=trace.rt_min,
                request_context_rt_end_min=trace.rt_max,
            )
            binding = replace(
                binding,
                query_mz=trace.mz,
                query_rt_min=trace.rt_min,
                query_rt_max=trace.rt_max,
                query_ppm_tol=trace.ppm_tol,
                source_physical_evaluation=binding.physical_evaluation,
                physical_evaluation=fresh,
            )
            capture_scope = replace(
                search_scope,
                capture_bindings=tuple(
                    binding if item.candidate_id == candidate.candidate_id else item
                    for item in search_scope.capture_bindings
                ),
            )
        capture = capture_owner.capture_peak_hypotheses(
            scope=capture_scope,
            candidate_id=candidate.candidate_id,
            trace=trace,
        )
        captures.append(capture)
        if (
            binding.source_physical_evaluation is not None
            and capture.physical_evaluation is not None
            and capture.physical_evaluation.state == "unavailable"
        ):
            continue
        if (
            capture.detection.status != "detected"
            or capture.selected_hypothesis is None
        ):
            raise UntargetedCidNlRealDataAdapterError(
                f"{candidate.candidate_id}: current MS1 receipt was not reacquired"
            )
        integration = capture.selected_hypothesis.integration
        if capture.physical_evaluation is None:
            _validate_candidate_integration(candidate, integration)
        elif binding.source_physical_evaluation is None:
            _validate_physical_candidate_receipt(candidate, capture.physical_evaluation)
        observations.append(
            capture_owner.mint_captured_peak_observation(
                capture,
                processing_owners=processing_owners,
                sample=sample,
            )
        )
    if search_scope.method_constraints.signal_processing_profile is not None:
        observations = list(dict.fromkeys(observations))
    return acquisition, tuple(observations), tuple(captures)


def method_label(candidate: DiscoveryCandidate) -> str:
    """Return a diagnostic-only label; it is never configured Target identity."""

    return (
        f"untargeted:{candidate.precursor_mz:.8g}:"
        f"NL{candidate.configured_neutral_loss_da:.8g}"
    )


def _relation_kind(
    *,
    evidence: CidNlEvidenceRef,
    candidate_peaks: tuple[PeakObservationRef, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> str | None:
    observation = evidence.observation
    mz_compatible = tuple(
        peak
        for peak in candidate_peaks
        if abs(observation.precursor_mz - peak.content.mz)
        <= policy.precursor_tolerance_da
    )
    containing = tuple(
        peak
        for peak in mz_compatible
        if peak.content.integration.rt_left_min
        <= observation.scan_rt_min
        <= peak.content.integration.rt_right_min
    )
    if not containing:
        return None
    if len(containing) != 1:
        return "set"
    selected = containing[0]
    interval = selected.content.integration
    if not interval.rt_left_min < observation.scan_rt_min < interval.rt_right_min:
        return "set"
    if any(
        peak is not selected and _intervals_touch_or_overlap(selected, peak)
        for peak in mz_compatible
    ):
        return "set"
    return "individual"


def _intervals_touch_or_overlap(
    left: PeakObservationRef,
    right: PeakObservationRef,
) -> bool:
    left_interval = left.content.integration
    right_interval = right.content.integration
    return (
        left_interval.rt_left_min <= right_interval.rt_right_min
        and right_interval.rt_left_min <= left_interval.rt_right_min
    )


def _mint_assessment(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    method_subject: CidNlMethodSubject,
    settings: DiscoverySettings,
    support_policy: DdaCidNlPeakSupportPolicy,
    candidate_peaks: tuple[PeakObservationRef, ...] = (),
    evidence_refs: tuple[CidNlEvidenceRef, ...] = (),
    individual_support_refs: tuple[DdaCidNlPeakSupportRef, ...] = (),
    set_relation_refs: tuple[DdaCidNlCandidateSetRelationRef, ...] = (),
    technical_state: CidNlTechnicalState = "evaluated",
    technical_reason_code: str | None = None,
):
    search_completed = technical_state == "evaluated"
    candidate_query_rt_mins = tuple(
        peak.generation_provenance.query_rt_min for peak in candidate_peaks
    )
    candidate_query_rt_maxes = tuple(
        peak.generation_provenance.query_rt_max for peak in candidate_peaks
    )
    try:
        return mint_cid_nl_method_assessment_ref(
            processing_owners=processing_owners,
            sample=sample,
            method_subject=method_subject,
            detector_invocation_id=(
                f"untargeted-cid-nl-real-data:{settings.resolver_mode}:v1"
            ),
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=(
                    min(settings.rt_min, *candidate_query_rt_mins)
                    if candidate_query_rt_mins
                    else settings.rt_min
                ),
                query_rt_max=(
                    max(settings.rt_max, *candidate_query_rt_maxes)
                    if candidate_query_rt_maxes
                    else settings.rt_max
                ),
                support_policy=support_policy,
                acquisition_supported=technical_state != "unsupported",
                search_completed=search_completed,
                scanned_event_count=(
                    len({ref.observation.scan_number for ref in evidence_refs})
                    if search_completed
                    else 0
                ),
                reason_code=technical_reason_code,
            ),
            candidate_peaks=candidate_peaks,
            evidence_refs=evidence_refs,
            individual_support_refs=individual_support_refs,
            set_relation_refs=set_relation_refs,
            technical_state=technical_state,
        )
    except CidNlMethodAssessmentContractError as exc:
        raise UntargetedCidNlRealDataAdapterError(str(exc)) from exc


def _bounded_subject_candidates(
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    sample: ResolvedCanonicalSampleIdentity,
    method_subject: CidNlMethodSubject,
    settings: DiscoverySettings,
) -> tuple[DiscoveryCandidate, ...]:
    ids = tuple(candidate.candidate_id for candidate in candidates)
    if len(ids) != len(set(ids)):
        raise UntargetedCidNlRealDataAdapterError(
            "Discovery candidate IDs must be unique"
        )
    for candidate in candidates:
        if candidate.sample_stem != sample.metadata.raw_stem:
            raise UntargetedCidNlRealDataAdapterError(
                f"{candidate.candidate_id}: candidate references a different sample"
            )
        if candidate.raw_file.stem != sample.metadata.raw_stem:
            raise UntargetedCidNlRealDataAdapterError(
                f"{candidate.candidate_id}: raw receipt mismatches its sample"
            )
    return tuple(
        sorted(
            (
                candidate
                for candidate in candidates
                if _within_ppm(
                    candidate.precursor_mz,
                    method_subject.precursor_mz,
                    method_subject.precursor_tolerance_ppm,
                )
                and any(
                    math.isclose(
                        candidate.configured_neutral_loss_da,
                        loss,
                        rel_tol=_WRITER_ROUNDING_REL_TOL,
                        abs_tol=_WRITER_ROUNDING_ABS_TOL,
                    )
                    for loss in method_subject.configured_losses
                )
                and candidate.neutral_loss_tag in settings.selected_tag_names
            ),
            key=lambda item: item.candidate_id,
        )
    )


def _untargeted_search_scope(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    acquisition_source_id: str,
    method_subject: CidNlMethodSubject,
    candidates: tuple[DiscoveryCandidate, ...],
    capture_candidates: tuple[DiscoveryCandidate, ...],
    peak_config: ExtractionConfig,
    signal_processing_profile: SignalProcessingProfile | None = None,
    physical_records: tuple[DiscoveryPhysicalFactRecord, ...] = (),
) -> UntargetedSearchScope:
    records = {record.candidate_id: record for record in physical_records}
    try:
        return UntargetedSearchScope(
            schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
            request=processing_owners.request,
            samples=(sample,),
            method_constraints=CaptureMethodConstraints(
                boundary_policy="resolver_interval",
                peak_config=peak_config,
                signal_processing_profile=signal_processing_profile,
            ),
            evidence_providers=(
                EvidenceProviderBinding(
                    provider_id=CID_NL_PROVIDER_ID,
                    method_subject_id=method_subject.method_subject_id,
                ),
            ),
            declared_candidate_ids=tuple(item.candidate_id for item in candidates),
            capture_bindings=tuple(
                CaptureCandidateBinding(
                    candidate_id=item.candidate_id,
                    method_subject_id=method_subject.method_subject_id,
                    sample_identity_id=sample.identity.sample_identity_id,
                    sample_name=sample.metadata.sample_name,
                    acquisition_source_id=acquisition_source_id,
                    query_mz=item.precursor_mz,
                    query_rt_min=item.ms1_search_rt_min,
                    query_rt_max=item.ms1_search_rt_max,
                    query_ppm_tol=method_subject.precursor_tolerance_ppm,
                    preferred_rt=item.best_seed_rt,
                    physical_evaluation=(
                        records[item.candidate_id].evaluation
                        if signal_processing_profile is not None
                        else None
                    ),
                )
                for item in capture_candidates
            ),
        )
    except SearchScopeContractError as exc:
        raise UntargetedCidNlRealDataAdapterError(str(exc)) from exc


def _supports_direct_precursor_assessment(
    candidates: tuple[DiscoveryCandidate, ...],
) -> bool:
    """Return whether the universe contains exact scan-precursor evidence."""

    return any(
        candidate.precursor_mz_basis == "scan_precursor" for candidate in candidates
    )


def _validate_inputs(**values: object) -> None:
    expected = {
        "processing_owners": ProcessingOwnerReferences,
        "sample": ResolvedCanonicalSampleIdentity,
        "method_subject": CidNlMethodSubject,
        "candidates": tuple,
        "settings": DiscoverySettings,
        "peak_config": ExtractionConfig,
    }
    for name, kind in expected.items():
        if not isinstance(values[name], kind):
            raise UntargetedCidNlRealDataAdapterError(
                f"{name} has the wrong owner type"
            )
    candidates = values["candidates"]
    assert isinstance(candidates, tuple)
    if any(not isinstance(item, DiscoveryCandidate) for item in candidates):
        raise UntargetedCidNlRealDataAdapterError(
            "candidates must contain current DiscoveryCandidate values"
        )
    method_subject = values["method_subject"]
    settings = values["settings"]
    peak_config = values["peak_config"]
    processing_owners = values["processing_owners"]
    assert isinstance(method_subject, CidNlMethodSubject)
    assert isinstance(settings, DiscoverySettings)
    assert isinstance(peak_config, ExtractionConfig)
    assert isinstance(processing_owners, ProcessingOwnerReferences)
    if method_subject.subject_kind != "untargeted_precursor":
        raise UntargetedCidNlRealDataAdapterError(
            "adapter requires an Untargeted CID-NL method subject"
        )
    try:
        validate_cid_nl_method_subject_binding(
            method_subject,
            processing_owners=processing_owners,
        )
    except CidNlMethodSubjectContractError as exc:
        raise UntargetedCidNlRealDataAdapterError(str(exc)) from exc
    if method_subject.composition is not None:
        if settings.tag_combine_mode == "single":
            raise UntargetedCidNlRealDataAdapterError(
                "Composite assessment requires union or intersection"
            )
        expected_composition = CidNlComposition(
            tags=tuple(
                (profile.tag, profile.neutral_loss_da)
                for profile in settings.neutral_loss_profiles
            ),
            combine_mode=settings.tag_combine_mode,
        )
        if method_subject.composition != expected_composition:
            raise UntargetedCidNlRealDataAdapterError(
                "Discovery composition mismatches the method subject"
            )
    elif (
        settings.tag_combine_mode not in {"single", "union", "intersection"}
        or len(settings.selected_tag_names) != 1
        or len(settings.neutral_loss_profiles) != 1
    ):
        raise UntargetedCidNlRealDataAdapterError(
            "The real-data runtime accepts exactly one CID-NL Discovery tag"
        )
    if method_subject.composition is None and not math.isclose(
        settings.neutral_loss_profiles[0].neutral_loss_da,
        method_subject.configured_losses[0],
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise UntargetedCidNlRealDataAdapterError(
            "Discovery neutral loss mismatches the method subject"
        )
    if not math.isclose(
        settings.precursor_mz_tolerance_ppm,
        method_subject.precursor_tolerance_ppm,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise UntargetedCidNlRealDataAdapterError(
            "Discovery precursor tolerance mismatches the method subject"
        )
    if peak_config.baseline_integration_method != "asls":
        raise UntargetedCidNlRealDataAdapterError(
            "peak_config requires canonical asls baseline integration"
        )


def _validate_raw_adapter(raw: object) -> None:
    for method in ("extract_xic_many", "scan_window_for_request", "iter_ms2_scans"):
        if not callable(getattr(raw, method, None)):
            raise UntargetedCidNlRealDataAdapterError(f"raw adapter lacks {method}")


def _validate_physical_candidate_receipt(
    candidate: DiscoveryCandidate,
    evaluation: PhysicalPeakEvaluation,
) -> None:
    facts = evaluation.facts
    if evaluation.state != "available" or facts is None:
        raise UntargetedCidNlRealDataAdapterError("physical receipt was not reacquired")
    for field, expected in (
        ("apex_rt_min", candidate.ms1_apex_rt),
        ("left_rt_min", candidate.ms1_peak_rt_start),
        ("right_rt_min", candidate.ms1_peak_rt_end),
        ("area_counts_seconds", candidate.ms1_area),
        ("height", candidate.ms1_height),
    ):
        if expected is None or not math.isclose(
            getattr(facts, field),
            expected,
            rel_tol=_WRITER_ROUNDING_REL_TOL,
            abs_tol=_WRITER_ROUNDING_ABS_TOL,
        ):
            raise UntargetedCidNlRealDataAdapterError(
                f"{candidate.candidate_id}: physical {field} mismatches current receipt"
            )


def _validate_candidate_integration(
    candidate: DiscoveryCandidate,
    integration: object,
) -> None:
    for field, expected in (
        ("rt_apex_min", candidate.ms1_apex_rt),
        ("rt_left_min", candidate.ms1_peak_rt_start),
        ("rt_right_min", candidate.ms1_peak_rt_end),
        ("area_raw_counts_seconds", candidate.ms1_area),
        ("height_raw", candidate.ms1_height),
    ):
        actual = getattr(integration, field)
        if expected is None or not math.isclose(
            actual,
            expected,
            rel_tol=_WRITER_ROUNDING_REL_TOL,
            abs_tol=_WRITER_ROUNDING_ABS_TOL,
        ):
            if field in {"area_raw_counts_seconds", "height_raw"}:
                raise UntargetedCidNlRealDataAdapterError(
                    f"{candidate.candidate_id}: "
                    "morphology_receipt_compatibility_unresolved: "
                    f"{field} mismatches current receipt; "
                    "compatibility remains diagnostic_only"
                )
            raise UntargetedCidNlRealDataAdapterError(
                f"{candidate.candidate_id}: reacquired {field} mismatches "
                "current receipt"
            )


def _freeze_acquisition(acquisition: TraceAcquisitionBatch) -> TraceAcquisitionBatch:
    frozen_traces: list[AcquiredTrace] = []
    for acquired in acquisition.traces:
        rt = np.array(acquired.trace.rt, dtype=float, copy=True)
        intensity = np.array(acquired.trace.intensity, dtype=float, copy=True)
        rt.setflags(write=False)
        intensity.setflags(write=False)
        frozen_traces.append(
            replace(
                acquired,
                trace=type(acquired.trace)(rt=rt, intensity=intensity),
            )
        )
    return replace(acquisition, traces=tuple(frozen_traces))


def _within_ppm(observed: float, expected: float, tolerance_ppm: float) -> bool:
    return abs(observed - expected) / expected * 1_000_000.0 <= tolerance_ppm


__all__ = [
    "UNTARGETED_CID_NL_CAPTURE_TRACE_SOURCE",
    "UntargetedCidNlSampleTraceBatch",
    "UntargetedCidNlRecoveryTraceBatch",
    "UntargetedCidNlRealDataAdapterError",
    "UntargetedCidNlRealDataAdapterResult",
    "acquire_untargeted_cid_nl_sample_trace_batch",
    "acquire_untargeted_cid_nl_recovery_trace_batch",
]
