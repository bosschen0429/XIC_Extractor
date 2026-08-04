"""Current Discovery-to-assessment adapter for one Untargeted CID-NL subject.

Discovery candidates are receipts.  This adapter reacquires their exact MS1 and
MS2 observations from one owner-bound RAW handle, then closes the existing M1
assessment input without fabricating Targeted identity or Matrix authority.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, replace
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
from xic_extractor.discovery.strict_seed_receipts import (
    StrictSeedReceiptError,
    read_complete_strict_scan_seeds,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
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
    CidNlMethodSubject,
    CidNlMethodSubjectContractError,
    validate_cid_nl_method_subject_binding,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
    DdaCidNlPeakSupportRef,
    resolve_dda_cid_nl_peak_support_ref,
)
from xic_extractor.peak_detection.hypotheses import build_peak_hypotheses
from xic_extractor.peak_detection.ms1_trace_detection import detect_ms1_trace_peak
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    mint_peak_observation_ref,
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
_WRITER_ROUNDING_REL_TOL = 5e-6
_WRITER_ROUNDING_ABS_TOL = 5e-6
_NON_DIRECT_PRECURSOR_BASIS_REASON = (
    "non_direct_precursor_basis_not_supported"
)


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
            artifact.producer_processing_request_id
            != attempt.processing_request_id
            or artifact.producer_processing_attempt_id
            != attempt.processing_attempt_id
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
) -> UntargetedCidNlRealDataAdapterResult:
    """Reacquire one sample's bounded subject and mint its complete M1 input."""

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
            source_candidate_ids=(),
            strict_scan_count=0,
            lineage_artifacts=lineage,
        )
    if not _supports_direct_precursor_assessment(bounded):
        assessment = _mint_assessment(
            processing_owners=processing_owners,
            sample=sample,
            method_subject=method_subject,
            settings=settings,
            support_policy=support_policy,
            technical_state="unsupported",
            technical_reason_code=_NON_DIRECT_PRECURSOR_BASIS_REASON,
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
            source_candidate_ids=tuple(item.candidate_id for item in bounded),
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
            candidates=bounded,
            settings=settings,
        )
    except StrictSeedReceiptError as exc:
        raise UntargetedCidNlRealDataAdapterError(str(exc)) from exc

    peak_candidates = tuple(
        candidate
        for candidate in bounded
        if candidate.ms1_peak_found
        and candidate.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
    )
    acquisition, observations = _reacquire_peak_observations(
        processing_owners=processing_owners,
        sample=sample,
        candidates=peak_candidates,
        settings=settings,
        peak_config=peak_config,
        raw=bound_raw,
        source=source,
    )
    if _observer is not None:
        if acquisition is not None:
            _observer.record_acquisition(acquisition)
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
        source_candidate_ids=tuple(item.candidate_id for item in bounded),
        strict_scan_count=len(strict_scan_seeds),
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
    raw: UntargetedCidNlRealDataRaw,
    _observer: _UntargetedRealDataWorkObserver | None = None,
) -> UntargetedCidNlRecoveryAttemptResult:
    """Revisit one eligible HOLD cell without granting direct SUPPORT."""

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
    query_rt_min = max(0.0, float(group_center_rt) - half_window)
    query_rt_max = float(group_center_rt) + half_window
    request = XICRequest(
        mz=method_subject.precursor_mz,
        rt_min=query_rt_min,
        rt_max=query_rt_max,
        ppm_tol=method_subject.precursor_tolerance_ppm,
    )
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace=f"{_ADAPTER_NAMESPACE}.recovery",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    acquisition = _freeze_acquisition(
        acquire_trace_batch(capability, (request,), bound_raw)
    )
    if _observer is not None:
        _observer.record_acquisition(acquisition)
    acquired = acquisition.traces[0]
    detection = detect_ms1_trace_peak(
        acquired.trace.rt,
        acquired.trace.intensity,
        peak_config=peak_config,
        preferred_rt=float(group_center_rt),
        peak_finder=find_peak_and_area,
    )
    observations: tuple[PeakObservationRef, ...] = ()
    if detection.status == "detected" and detection.result is not None:
        hypotheses = build_peak_hypotheses(
            sample_name=sample.metadata.sample_name,
            target_label=(
                f"untargeted:{method_subject.precursor_mz:.8g}:"
                f"NL{method_subject.neutral_loss_da:.8g}:recovery"
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
                f"{_ADAPTER_NAMESPACE}.recovery:"
                f"{TraceCoordinateKind.NATIVE_SCAN.value}"
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
                scanned_event_count=0,
                reason_code=None,
            ),
            candidate_peaks=observations,
        )
    except CidNlMethodAssessmentContractError as exc:
        raise UntargetedCidNlRealDataAdapterError(str(exc)) from exc
    if _observer is not None:
        _observer.record_artifact(assessment.artifact)
    lineage = canonical_current_attempt_artifact_closure(
        producer_attempt=processing_owners.attempt,
        artifacts=(
            *(observation.artifact for observation in observations),
            assessment.artifact,
        ),
    )
    recovery_input = (
        UntargetedCidNlRecoveryInput(
            sample=sample,
            candidate_assessment=assessment,
        )
        if observations
        else None
    )
    return UntargetedCidNlRecoveryAttemptResult(
        assessment=assessment,
        recovery_input=recovery_input,
        acquisition=acquisition,
        lineage_artifacts=lineage,
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
) -> tuple[TraceAcquisitionBatch | None, tuple[PeakObservationRef, ...]]:
    if not candidates:
        return None, ()
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace=_ADAPTER_NAMESPACE,
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    requests = tuple(
        XICRequest(
            mz=candidate.precursor_mz,
            rt_min=candidate.ms1_search_rt_min,
            rt_max=candidate.ms1_search_rt_max,
            ppm_tol=settings.precursor_mz_tolerance_ppm,
        )
        for candidate in candidates
    )
    acquisition = _freeze_acquisition(acquire_trace_batch(capability, requests, raw))
    observations: list[PeakObservationRef] = []
    for candidate, acquired in zip(candidates, acquisition.traces, strict=True):
        detection = detect_ms1_trace_peak(
            acquired.trace.rt,
            acquired.trace.intensity,
            peak_config=peak_config,
            preferred_rt=candidate.best_seed_rt,
            peak_finder=find_peak_and_area,
        )
        if detection.status != "detected" or detection.result is None:
            raise UntargetedCidNlRealDataAdapterError(
                f"{candidate.candidate_id}: current MS1 receipt was not reacquired"
            )
        hypotheses = build_peak_hypotheses(
            sample_name=sample.metadata.sample_name,
            target_label=method_label(candidate),
            role="Analyte",
            istd_pair="",
            resolver_mode=settings.resolver_mode,
            peak_result=detection.result,
            rt=detection.rt,
            intensity=detection.intensity,
            baseline_integration_method="asls",
            ms1_morphology_smoothing_window_points=(
                peak_config.ms1_morphology_smoothing_window_points
            ),
        )
        selected = tuple(item for item in hypotheses if item.audit.selected)
        if len(selected) != 1:
            raise UntargetedCidNlRealDataAdapterError(
                f"{candidate.candidate_id}: integration owner did not select one peak"
            )
        integration = selected[0].integration
        _validate_candidate_integration(candidate, integration)
        trace = trace_from_xic_request(
            sample_name=sample.metadata.sample_name,
            request=acquired.coordinate.request,
            xic_trace=acquired.trace,
            source=f"{_ADAPTER_NAMESPACE}:{TraceCoordinateKind.NATIVE_SCAN.value}",
            acquisition_source_id=source.acquisition_source_id,
        )
        observations.append(
            mint_peak_observation_ref(
                processing_owners=processing_owners,
                sample=sample,
                trace=trace,
                integration=integration,
                resolver_mode=settings.resolver_mode,
                proposal_sources=selected[0].audit.proposal_sources,
            )
        )
    return acquisition, tuple(observations)


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
                scanned_event_count=(len(evidence_refs) if search_completed else 0),
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


def _supports_direct_precursor_assessment(
    candidates: tuple[DiscoveryCandidate, ...],
) -> bool:
    """Return whether M1 may evaluate the complete bounded receipt universe."""

    return bool(candidates) and all(
        candidate.precursor_mz_basis == "scan_precursor"
        for candidate in candidates
    )


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
    tag_name = settings.selected_tag_names[0]
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
                and math.isclose(
                    candidate.configured_neutral_loss_da,
                    method_subject.neutral_loss_da,
                    rel_tol=_WRITER_ROUNDING_REL_TOL,
                    abs_tol=_WRITER_ROUNDING_ABS_TOL,
                )
                and candidate.neutral_loss_tag == tag_name
            ),
            key=lambda item: item.candidate_id,
        )
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
    if (
        settings.tag_combine_mode != "single"
        or len(settings.selected_tag_names) != 1
        or len(settings.neutral_loss_profiles) != 1
    ):
        raise UntargetedCidNlRealDataAdapterError(
            "M7 accepts exactly one CID-NL Discovery tag"
        )
    if not math.isclose(
        settings.neutral_loss_profiles[0].neutral_loss_da,
        method_subject.neutral_loss_da,
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
    "UntargetedCidNlRealDataAdapterError",
    "UntargetedCidNlRealDataAdapterResult",
]
