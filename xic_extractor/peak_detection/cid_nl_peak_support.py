"""Resolve one exact DDA/ddMS2 CID-NL event to one physical MS1 peak."""

from __future__ import annotations

from xic_extractor.evidence_artifacts import _mint_evidence_artifact_ref
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceContractError,
    CidNlEvidenceRef,
    validate_cid_nl_evidence_binding,
)
from xic_extractor.peak_detection.cid_nl_peak_support_models import (
    _ARTIFACT_SEMANTIC_CONTRACT,
    ARTIFACT_SEMANTIC_ROLE,
    RELATIONSHIP,
    SCHEMA_VERSION,
    DdaCidNlPeakSupportContent,
    DdaCidNlPeakSupportContractError,
    DdaCidNlPeakSupportPolicy,
    DdaCidNlPeakSupportRef,
    _support_bytes,
    canonical_dda_cid_nl_peak_support_bytes,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationContractError,
    PeakObservationRef,
    validate_peak_observation_binding,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


def resolve_dda_cid_nl_peak_support_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    canonical_evidence: CidNlEvidenceRef,
    candidate_peaks: tuple[PeakObservationRef, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> DdaCidNlPeakSupportRef:
    """Resolve one DDA event to exactly one supplied physical MS1 observation."""

    _validate_owner_sample(processing_owners, sample)
    _validate_evidence_binding(
        canonical_evidence,
        processing_owners=processing_owners,
        sample=sample,
    )
    if not isinstance(policy, DdaCidNlPeakSupportPolicy):
        raise DdaCidNlPeakSupportContractError(
            "policy must be owner-normalized DdaCidNlPeakSupportPolicy"
        )
    _validate_candidate_peaks(
        candidate_peaks,
        processing_owners=processing_owners,
        sample=sample,
    )

    observation = canonical_evidence.observation
    if observation.observed_loss_error_ppm > policy.neutral_loss_tolerance_ppm:
        raise DdaCidNlPeakSupportContractError(
            "CID-NL evidence does not satisfy the strict neutral-loss tolerance"
        )
    matching_peaks = tuple(
        peak
        for peak in candidate_peaks
        if _matches_observation(
            canonical_evidence,
            peak,
            precursor_tolerance_da=policy.precursor_tolerance_da,
        )
    )
    if not matching_peaks:
        raise DdaCidNlPeakSupportContractError(
            "missing physical MS1 peak matching the DDA CID-NL event"
        )
    if len(matching_peaks) != 1:
        raise DdaCidNlPeakSupportContractError(
            "DDA CID-NL event matches ambiguous physical MS1 peak observations"
        )

    peak = matching_peaks[0]
    content = _support_content(
        sample=sample,
        canonical_evidence=canonical_evidence,
        peak_observation=peak,
        candidate_peaks=candidate_peaks,
        policy=policy,
    )
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=_support_bytes(content),
        upstream_artifacts=(
            canonical_evidence.artifact,
            *(candidate.artifact for candidate in candidate_peaks),
        ),
    )
    return DdaCidNlPeakSupportRef(content=content, artifact=artifact)


def validate_dda_cid_nl_peak_support_binding(
    reference: DdaCidNlPeakSupportRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    canonical_evidence: CidNlEvidenceRef,
    candidate_peaks: tuple[PeakObservationRef, ...],
) -> None:
    """Reject request, attempt, sample, evidence, or candidate substitution."""

    if not isinstance(reference, DdaCidNlPeakSupportRef):
        raise DdaCidNlPeakSupportContractError(
            "reference must be an owner-minted DdaCidNlPeakSupportRef"
        )
    _validate_owner_sample(processing_owners, sample)
    _validate_evidence_binding(
        canonical_evidence,
        processing_owners=processing_owners,
        sample=sample,
    )
    _validate_candidate_peaks(
        candidate_peaks,
        processing_owners=processing_owners,
        sample=sample,
    )
    matching_peaks = _matching_peaks(
        canonical_evidence,
        candidate_peaks,
        policy=reference.content.policy,
    )
    if len(matching_peaks) != 1:
        raise DdaCidNlPeakSupportContractError(
            "DDA CID-NL peak support candidate universe no longer has one match"
        )
    peak_observation = matching_peaks[0]
    expected_content = _support_content(
        sample=sample,
        canonical_evidence=canonical_evidence,
        peak_observation=peak_observation,
        candidate_peaks=candidate_peaks,
        policy=reference.content.policy,
    )
    if reference.content != expected_content:
        if (
            reference.content.cid_nl_evidence_content_id
            != expected_content.cid_nl_evidence_content_id
            or reference.content.dda_scan_number != expected_content.dda_scan_number
        ):
            raise DdaCidNlPeakSupportContractError(
                "DDA CID-NL peak support references different evidence"
            )
        raise DdaCidNlPeakSupportContractError(
            "DDA CID-NL peak support references a different peak observation or "
            "candidate universe"
        )
    expected_upstream_ids = tuple(
        sorted(
            (
                canonical_evidence.artifact.evidence_artifact_id,
                *(peak.artifact.evidence_artifact_id for peak in candidate_peaks),
            )
        )
    )
    if reference.artifact.upstream_artifact_ids != expected_upstream_ids:
        raise DdaCidNlPeakSupportContractError(
            "DDA CID-NL peak support upstream references were substituted"
        )
    if (
        reference.artifact.producer_processing_request_id
        != processing_owners.request.processing_request_id
    ):
        raise DdaCidNlPeakSupportContractError(
            "DDA CID-NL peak support references a different processing request"
        )
    if (
        reference.artifact.producer_processing_attempt_id
        != processing_owners.attempt.processing_attempt_id
    ):
        raise DdaCidNlPeakSupportContractError(
            "DDA CID-NL peak support references a different processing attempt"
        )


def _support_content(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    canonical_evidence: CidNlEvidenceRef,
    peak_observation: PeakObservationRef,
    candidate_peaks: tuple[PeakObservationRef, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> DdaCidNlPeakSupportContent:
    observation = canonical_evidence.observation
    peak_content = peak_observation.content
    return DdaCidNlPeakSupportContent(
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        cid_nl_evidence_content_id=(
            canonical_evidence.artifact.content_identity.artifact_content_id
        ),
        peak_observation_content_id=peak_content.peak_observation_content_id,
        candidate_peak_content_ids=tuple(
            sorted(peak.content.peak_observation_content_id for peak in candidate_peaks)
        ),
        acquisition_mode=observation.acquisition_mode,
        dda_scan_number=observation.scan_number,
        scan_rt_min=observation.scan_rt_min,
        peak_rt_min=peak_content.rt[0],
        peak_rt_max=peak_content.rt[-1],
        precursor_mz=observation.precursor_mz,
        peak_mz=peak_content.mz,
        precursor_delta_da=observation.precursor_mz - peak_content.mz,
        observed_loss_error_ppm=observation.observed_loss_error_ppm,
        policy=policy,
        relationship=RELATIONSHIP,
    )


def _matches_observation(
    canonical_evidence: CidNlEvidenceRef,
    peak_observation: PeakObservationRef,
    *,
    precursor_tolerance_da: float,
) -> bool:
    observation = canonical_evidence.observation
    peak_content = peak_observation.content
    return (
        peak_content.rt[0] <= observation.scan_rt_min <= peak_content.rt[-1]
        and abs(observation.precursor_mz - peak_content.mz) <= precursor_tolerance_da
    )


def _matching_peaks(
    canonical_evidence: CidNlEvidenceRef,
    candidate_peaks: tuple[PeakObservationRef, ...],
    *,
    policy: DdaCidNlPeakSupportPolicy,
) -> tuple[PeakObservationRef, ...]:
    return tuple(
        peak
        for peak in candidate_peaks
        if _matches_observation(
            canonical_evidence,
            peak,
            precursor_tolerance_da=policy.precursor_tolerance_da,
        )
    )


def _validate_candidate_peaks(
    candidate_peaks: object,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> tuple[PeakObservationRef, ...]:
    if not isinstance(candidate_peaks, tuple):
        raise DdaCidNlPeakSupportContractError(
            "candidate_peaks must be a tuple of owner-backed observations"
        )
    if not candidate_peaks:
        raise DdaCidNlPeakSupportContractError(
            "missing candidate physical MS1 peak observations"
        )
    if any(not isinstance(peak, PeakObservationRef) for peak in candidate_peaks):
        raise DdaCidNlPeakSupportContractError(
            "candidate_peaks must contain owner-backed PeakObservationRef values"
        )
    typed_peaks = candidate_peaks
    artifact_ids = tuple(peak.artifact.evidence_artifact_id for peak in typed_peaks)
    content_ids = tuple(
        peak.content.peak_observation_content_id for peak in typed_peaks
    )
    if len(set(artifact_ids)) != len(artifact_ids) or len(set(content_ids)) != len(
        content_ids
    ):
        raise DdaCidNlPeakSupportContractError(
            "candidate physical MS1 peak observations are ambiguous"
        )
    for peak in typed_peaks:
        _validate_peak_binding(
            peak,
            processing_owners=processing_owners,
            sample=sample,
        )
    return typed_peaks


def _validate_owner_sample(
    processing_owners: object,
    sample: object,
) -> None:
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise DdaCidNlPeakSupportContractError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise DdaCidNlPeakSupportContractError(
            "sample must be an owner-resolved canonical sample"
        )
    if sample not in processing_owners.sample_resolutions:
        raise DdaCidNlPeakSupportContractError(
            "DDA CID-NL peak support references a different sample"
        )


def _validate_evidence_binding(
    reference: CidNlEvidenceRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> None:
    try:
        validate_cid_nl_evidence_binding(
            reference,
            processing_owners=processing_owners,
            sample=sample,
        )
    except CidNlEvidenceContractError as exc:
        raise DdaCidNlPeakSupportContractError(str(exc)) from exc


def _validate_peak_binding(
    reference: PeakObservationRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> None:
    try:
        validate_peak_observation_binding(
            reference,
            processing_owners=processing_owners,
            sample=sample,
        )
    except PeakObservationContractError as exc:
        raise DdaCidNlPeakSupportContractError(str(exc)) from exc


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "DdaCidNlPeakSupportContent",
    "DdaCidNlPeakSupportContractError",
    "DdaCidNlPeakSupportPolicy",
    "DdaCidNlPeakSupportRef",
    "RELATIONSHIP",
    "SCHEMA_VERSION",
    "canonical_dda_cid_nl_peak_support_bytes",
    "resolve_dda_cid_nl_peak_support_ref",
    "validate_dda_cid_nl_peak_support_binding",
]
