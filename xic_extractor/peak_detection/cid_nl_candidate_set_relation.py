"""Set-level CID-NL scan relations that cannot grant individual SUPPORT.

This owner is deliberately separate from ``DdaCidNlPeakSupportRef``.  It
captures boundary and positive-width overlapping cases without selecting
one peak or minting one vote per candidate.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass

from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    ACQUISITION_MODE,
    CidNlEvidenceContractError,
    CidNlEvidenceRef,
    validate_cid_nl_evidence_binding,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationContractError,
    PeakObservationRef,
    validate_peak_observation_binding,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "dda_cid_nl_candidate_set_relation"
ARTIFACT_SEMANTIC_ROLE = "dda_cid_nl_candidate_set_relation"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf"
RELATIONSHIP = "supports_one_or_more_of"

_ARTIFACT_CONTENT_ID_PREFIX = "xic.artifact-content:"
_PEAK_CONTENT_ID_PREFIX = "xic.peak-observation-content:"
_SAMPLE_ID_PREFIX = "xic.sample:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.peak_detection.cid_nl_candidate_set_relation",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)


class DdaCidNlCandidateSetRelationContractError(ValueError):
    """Raised when one scan cannot close a conservative candidate-set relation."""


@dataclass(frozen=True, slots=True)
class DdaCidNlCandidateSetRelationContent:
    """Immutable set-level relation; it carries no individual vote."""

    canonical_sample_identity_id: str
    cid_nl_evidence_content_id: str
    candidate_peak_content_ids: tuple[str, ...]
    matched_candidate_content_ids: tuple[str, ...]
    acquisition_mode: str
    dda_scan_number: int
    scan_rt_min: float
    policy: DdaCidNlPeakSupportPolicy
    relationship: str = RELATIONSHIP

    def __post_init__(self) -> None:
        _validate_typed_digest_id(
            self.canonical_sample_identity_id,
            prefix=_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
        _validate_typed_digest_id(
            self.cid_nl_evidence_content_id,
            prefix=_ARTIFACT_CONTENT_ID_PREFIX,
            field="cid_nl_evidence_content_id",
        )
        candidate_ids = _canonical_peak_ids(
            self.candidate_peak_content_ids,
            field="candidate_peak_content_ids",
        )
        matched_ids = _canonical_peak_ids(
            self.matched_candidate_content_ids,
            field="matched_candidate_content_ids",
        )
        if not set(matched_ids).issubset(candidate_ids):
            raise DdaCidNlCandidateSetRelationContractError(
                "matched candidate IDs must be a subset of the declared universe"
            )
        if self.acquisition_mode != ACQUISITION_MODE:
            raise DdaCidNlCandidateSetRelationContractError(
                f"acquisition_mode must be {ACQUISITION_MODE!r}"
            )
        if (
            isinstance(self.dda_scan_number, bool)
            or not isinstance(self.dda_scan_number, int)
            or self.dda_scan_number <= 0
        ):
            raise DdaCidNlCandidateSetRelationContractError(
                "dda_scan_number must be a positive integer"
            )
        if isinstance(self.scan_rt_min, bool) or not isinstance(
            self.scan_rt_min, (int, float)
        ):
            raise DdaCidNlCandidateSetRelationContractError(
                "scan_rt_min must be finite and non-negative"
            )
        scan_rt = float(self.scan_rt_min)
        if scan_rt < 0.0 or not math.isfinite(scan_rt):
            raise DdaCidNlCandidateSetRelationContractError(
                "scan_rt_min must be finite and non-negative"
            )
        if not isinstance(self.policy, DdaCidNlPeakSupportPolicy):
            raise DdaCidNlCandidateSetRelationContractError(
                "policy must be owner-normalized DDA CID-NL support policy"
            )
        if self.relationship != RELATIONSHIP:
            raise DdaCidNlCandidateSetRelationContractError(
                f"relationship must be {RELATIONSHIP!r}"
            )
        object.__setattr__(self, "scan_rt_min", 0.0 if scan_rt == 0.0 else scan_rt)


@dataclass(frozen=True, slots=True)
class DdaCidNlCandidateSetRelationRef:
    """Set-level relation plus the exact evidence and candidate references."""

    content: DdaCidNlCandidateSetRelationContent
    canonical_evidence: CidNlEvidenceRef
    candidate_peaks: tuple[PeakObservationRef, ...]
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        if not isinstance(self.content, DdaCidNlCandidateSetRelationContent):
            raise DdaCidNlCandidateSetRelationContractError(
                "content must be an owner-normalized candidate-set relation"
            )
        if not isinstance(self.canonical_evidence, CidNlEvidenceRef):
            raise DdaCidNlCandidateSetRelationContractError(
                "canonical_evidence must be an owner-minted CID-NL reference"
            )
        candidates = _canonical_candidates(self.candidate_peaks)
        if candidates != self.candidate_peaks:
            raise DdaCidNlCandidateSetRelationContractError(
                "candidate_peaks must be canonical-sorted"
            )
        if self.content.canonical_sample_identity_id != (
            self.canonical_evidence.observation.canonical_sample_identity_id
        ):
            raise DdaCidNlCandidateSetRelationContractError(
                "candidate-set relation references a different sample"
            )
        if self.content.cid_nl_evidence_content_id != (
            self.canonical_evidence.artifact.content_identity.artifact_content_id
        ):
            raise DdaCidNlCandidateSetRelationContractError(
                "candidate-set relation references different CID-NL evidence"
            )
        if self.content.candidate_peak_content_ids != tuple(
            peak.content.peak_observation_content_id for peak in candidates
        ):
            raise DdaCidNlCandidateSetRelationContractError(
                "candidate-set relation does not close its declared universe"
            )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise DdaCidNlCandidateSetRelationContractError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise DdaCidNlCandidateSetRelationContractError(
                "artifact semantic contract is not owned by candidate-set relation"
            )
        expected_upstream_ids = tuple(
            sorted(
                (
                    self.canonical_evidence.artifact.evidence_artifact_id,
                    *(peak.artifact.evidence_artifact_id for peak in candidates),
                )
            )
        )
        if self.artifact.upstream_artifact_ids != expected_upstream_ids:
            raise DdaCidNlCandidateSetRelationContractError(
                "candidate-set relation upstream references were substituted"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_dda_cid_nl_candidate_set_relation_bytes(self.content),
            )
        except ValueError as exc:
            raise DdaCidNlCandidateSetRelationContractError(
                "candidate-set relation mismatches its immutable artifact"
            ) from exc


def resolve_dda_cid_nl_candidate_set_relation_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    canonical_evidence: CidNlEvidenceRef,
    candidate_peaks: tuple[PeakObservationRef, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> DdaCidNlCandidateSetRelationRef:
    """Resolve a non-unique zero-buffer case without selecting one candidate."""

    candidates = _validate_inputs(
        processing_owners=processing_owners,
        sample=sample,
        canonical_evidence=canonical_evidence,
        candidate_peaks=candidate_peaks,
        policy=policy,
    )
    matched = _set_level_members(
        canonical_evidence=canonical_evidence,
        candidate_peaks=candidates,
        policy=policy,
    )
    content = DdaCidNlCandidateSetRelationContent(
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        cid_nl_evidence_content_id=(
            canonical_evidence.artifact.content_identity.artifact_content_id
        ),
        candidate_peak_content_ids=tuple(
            peak.content.peak_observation_content_id for peak in candidates
        ),
        matched_candidate_content_ids=tuple(
            peak.content.peak_observation_content_id for peak in matched
        ),
        acquisition_mode=canonical_evidence.observation.acquisition_mode,
        dda_scan_number=canonical_evidence.observation.scan_number,
        scan_rt_min=canonical_evidence.observation.scan_rt_min,
        policy=policy,
    )
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=canonical_dda_cid_nl_candidate_set_relation_bytes(content),
        upstream_artifacts=(
            canonical_evidence.artifact,
            *(peak.artifact for peak in candidates),
        ),
    )
    return DdaCidNlCandidateSetRelationRef(
        content=content,
        canonical_evidence=canonical_evidence,
        candidate_peaks=candidates,
        artifact=artifact,
    )


def validate_dda_cid_nl_candidate_set_relation_binding(
    reference: DdaCidNlCandidateSetRelationRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> None:
    """Reject request, attempt, sample, evidence, or universe substitution."""

    if not isinstance(reference, DdaCidNlCandidateSetRelationRef):
        raise DdaCidNlCandidateSetRelationContractError(
            "reference must be an owner-minted candidate-set relation"
        )
    expected = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=processing_owners,
        sample=sample,
        canonical_evidence=reference.canonical_evidence,
        candidate_peaks=reference.candidate_peaks,
        policy=reference.content.policy,
    )
    if reference != expected:
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate-set relation binding was substituted"
        )


def canonical_dda_cid_nl_candidate_set_relation_bytes(
    content: DdaCidNlCandidateSetRelationContent,
) -> bytes:
    if not isinstance(content, DdaCidNlCandidateSetRelationContent):
        raise DdaCidNlCandidateSetRelationContractError(
            "canonical serialization requires candidate-set relation content"
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "content": asdict(content),
    }
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate-set relation payload must be canonical JSON"
        ) from exc
    return (serialized + "\n").encode("utf-8")


def _validate_inputs(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    canonical_evidence: CidNlEvidenceRef,
    candidate_peaks: object,
    policy: DdaCidNlPeakSupportPolicy,
) -> tuple[PeakObservationRef, ...]:
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise DdaCidNlCandidateSetRelationContractError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise DdaCidNlCandidateSetRelationContractError(
            "sample must be an owner-resolved canonical sample"
        )
    if sample not in processing_owners.sample_resolutions:
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate-set relation references a sample outside the request"
        )
    if not isinstance(policy, DdaCidNlPeakSupportPolicy):
        raise DdaCidNlCandidateSetRelationContractError(
            "policy must be owner-normalized DDA CID-NL support policy"
        )
    try:
        validate_cid_nl_evidence_binding(
            canonical_evidence,
            processing_owners=processing_owners,
            sample=sample,
        )
    except CidNlEvidenceContractError as exc:
        raise DdaCidNlCandidateSetRelationContractError(str(exc)) from exc
    candidates = _canonical_candidates(candidate_peaks)
    for peak in candidates:
        try:
            validate_peak_observation_binding(
                peak,
                processing_owners=processing_owners,
                sample=sample,
            )
        except PeakObservationContractError as exc:
            raise DdaCidNlCandidateSetRelationContractError(str(exc)) from exc
    observation = canonical_evidence.observation
    if not observation.assess_precursor_loss(
        policy.neutral_loss_tolerance_ppm
    ).supports_nl:
        raise DdaCidNlCandidateSetRelationContractError(
            "CID-NL evidence does not satisfy the strict neutral-loss tolerance"
        )
    return candidates


def _set_level_members(
    *,
    canonical_evidence: CidNlEvidenceRef,
    candidate_peaks: tuple[PeakObservationRef, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> tuple[PeakObservationRef, ...]:
    observation = canonical_evidence.observation
    precursor = observation.assess_precursor_loss(policy.neutral_loss_tolerance_ppm)
    if not precursor.supports_nl or precursor.reference_mz is None:
        raise DdaCidNlCandidateSetRelationContractError(
            "CID-NL evidence lacks independent precursor isolation support"
        )
    mz_compatible = tuple(
        peak
        for peak in candidate_peaks
        if abs(precursor.reference_mz - peak.content.mz)
        <= policy.precursor_tolerance_da
    )
    containing = tuple(
        peak
        for peak in mz_compatible
        if peak.content.integration.rt_left_min <= observation.support_rt_min
        and observation.scan_rt_min <= peak.content.integration.rt_right_min
    )
    if not containing:
        raise DdaCidNlCandidateSetRelationContractError(
            "CID-NL scan does not relate to the declared candidate universe"
        )

    if len(containing) == 1:
        selected = containing[0]
        interval = selected.content.integration
        if (
            interval.rt_left_min < observation.scan_rt_min < interval.rt_right_min
            and not any(
                candidate is not selected
                and interval.rt_left_min < candidate.content.integration.rt_right_min
                and candidate.content.integration.rt_left_min < interval.rt_right_min
                for candidate in mz_compatible
            )
        ):
            raise DdaCidNlCandidateSetRelationContractError(
                "strictly unique relation belongs to individual support"
            )

    # Boundary sets retain touching neighbors after individual eligibility closes.
    component = set(containing)
    changed = True
    while changed:
        changed = False
        for candidate in mz_compatible:
            if candidate in component:
                continue
            if any(
                _intervals_touch_or_overlap(candidate, member) for member in component
            ):
                component.add(candidate)
                changed = True
    matched = tuple(sorted(component, key=_candidate_sort_key))
    return matched


def _intervals_touch_or_overlap(
    left: PeakObservationRef,
    right: PeakObservationRef,
) -> bool:
    left_integration = left.content.integration
    right_integration = right.content.integration
    return (
        left_integration.rt_left_min <= right_integration.rt_right_min
        and right_integration.rt_left_min <= left_integration.rt_right_min
    )


def _canonical_candidates(value: object) -> tuple[PeakObservationRef, ...]:
    if not isinstance(value, tuple) or not value:
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate_peaks must be a non-empty immutable tuple"
        )
    if any(not isinstance(peak, PeakObservationRef) for peak in value):
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate_peaks must contain owner-backed observations"
        )
    candidates = tuple(sorted(value, key=_candidate_sort_key))
    content_ids = tuple(peak.content.peak_observation_content_id for peak in candidates)
    artifact_ids = tuple(peak.artifact.evidence_artifact_id for peak in candidates)
    if len(set(content_ids)) != len(content_ids) or len(set(artifact_ids)) != len(
        artifact_ids
    ):
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate_peaks must be unique"
        )
    return candidates


def _candidate_sort_key(peak: PeakObservationRef) -> tuple[str, str]:
    return (
        peak.content.peak_observation_content_id,
        peak.artifact.evidence_artifact_id,
    )


def _canonical_peak_ids(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value:
        raise DdaCidNlCandidateSetRelationContractError(
            f"{field} must be a non-empty tuple"
        )
    ids = tuple(
        _validate_typed_digest_id(item, prefix=_PEAK_CONTENT_ID_PREFIX, field=field)
        for item in value
    )
    if ids != tuple(sorted(set(ids))):
        raise DdaCidNlCandidateSetRelationContractError(
            f"{field} must be unique and canonical-sorted"
        )
    return ids


def _validate_typed_digest_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise DdaCidNlCandidateSetRelationContractError(
            f"{field} must use the {prefix!r} prefix"
        )
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise DdaCidNlCandidateSetRelationContractError(
            f"{field} must contain a SHA-256 digest"
        )
    return value


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "DdaCidNlCandidateSetRelationContent",
    "DdaCidNlCandidateSetRelationContractError",
    "DdaCidNlCandidateSetRelationRef",
    "RELATIONSHIP",
    "SCHEMA_VERSION",
    "canonical_dda_cid_nl_candidate_set_relation_bytes",
    "resolve_dda_cid_nl_candidate_set_relation_ref",
    "validate_dda_cid_nl_candidate_set_relation_binding",
]
