"""Complete, owner-backed settlement for one CID-NL method attempt.

Positive measured evidence and one-to-one support keep their existing owners.
This module closes the complete attempt so technical state, completed negative
searches, HOLD, and REJECT do not need fake ``PeakHypothesis`` objects.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Literal, TypeAlias

from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelationContractError,
    DdaCidNlCandidateSetRelationRef,
    validate_dda_cid_nl_candidate_set_relation_binding,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceContractError,
    CidNlEvidenceRef,
    validate_cid_nl_evidence_binding,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_METHOD_ID,
    CID_NL_METHOD_VERSION,
    CID_NL_PROVIDER_ID,
    CidNlMethodSubject,
    CidNlMethodSubjectContractError,
    cid_nl_method_subject_from_target,
    validate_cid_nl_method_subject_binding,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportContractError,
    DdaCidNlPeakSupportPolicy,
    DdaCidNlPeakSupportRef,
    validate_dda_cid_nl_peak_support_binding,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationContractError,
    PeakObservationRef,
    validate_peak_observation_binding,
)
from xic_extractor.processing_identity import (
    IdentityContractError,
    ProcessingOwnerReferences,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "cid_nl_method_assessment"
ARTIFACT_SEMANTIC_ROLE = "cid_nl_method_assessment"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf"

CidNlTechnicalState: TypeAlias = Literal[
    "evaluated",
    "unsupported",
    "failed",
    "not_evaluated",
]
CidNlMethodResult: TypeAlias = Literal["SUPPORT", "HOLD", "REJECT"]

_ARTIFACT_CONTENT_ID_PREFIX = "xic.artifact-content:"
_ACQUISITION_SOURCE_ID_PREFIX = "xic.acquisition-source:"
_PEAK_CONTENT_ID_PREFIX = "xic.peak-observation-content:"
_SAMPLE_ID_PREFIX = "xic.sample:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TECHNICAL_STATES = frozenset({"evaluated", "unsupported", "failed", "not_evaluated"})
_METHOD_RESULTS = frozenset({"SUPPORT", "HOLD", "REJECT"})
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.peak_detection.cid_nl_method_assessment",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)
class CidNlMethodAssessmentContractError(ValueError):
    """Raised when one complete CID-NL method attempt cannot be closed."""


@dataclass(frozen=True, slots=True)
class CidNlEvaluationReceipt:
    """Evidence that one acquisition/search attempt did or did not complete."""

    query_rt_min: float
    query_rt_max: float
    support_policy: DdaCidNlPeakSupportPolicy
    acquisition_supported: bool
    search_completed: bool
    scanned_event_count: int
    reason_code: str | None

    def __post_init__(self) -> None:
        query_min = _finite_float(self.query_rt_min, field="query_rt_min")
        query_max = _finite_float(self.query_rt_max, field="query_rt_max")
        if query_min < 0.0 or query_min >= query_max:
            raise CidNlMethodAssessmentContractError(
                "evaluation query must have a non-negative increasing RT window"
            )
        if not isinstance(self.support_policy, DdaCidNlPeakSupportPolicy):
            raise CidNlMethodAssessmentContractError(
                "support_policy must be owner-normalized DDA CID-NL policy"
            )
        if not isinstance(self.acquisition_supported, bool):
            raise CidNlMethodAssessmentContractError(
                "acquisition_supported must be a boolean"
            )
        if not isinstance(self.search_completed, bool):
            raise CidNlMethodAssessmentContractError(
                "search_completed must be a boolean"
            )
        if (
            isinstance(self.scanned_event_count, bool)
            or not isinstance(self.scanned_event_count, int)
            or self.scanned_event_count < 0
        ):
            raise CidNlMethodAssessmentContractError(
                "scanned_event_count must be a non-negative integer"
            )
        if self.reason_code is not None:
            _required_text(self.reason_code, field="reason_code")
        object.__setattr__(self, "query_rt_min", query_min)
        object.__setattr__(self, "query_rt_max", query_max)


@dataclass(frozen=True, slots=True)
class CidNlMethodAssessmentContent:
    """Orthogonal technical and method-result axes for one attempt."""

    canonical_sample_identity_id: str
    acquisition_source_id: str
    provider_id: str
    method_id: str
    method_version: str
    method_subject: CidNlMethodSubject
    detector_invocation_id: str
    candidate_peak_content_ids: tuple[str, ...]
    evaluation_receipt: CidNlEvaluationReceipt
    evidence_artifact_content_ids: tuple[str, ...]
    individual_relation_artifact_content_ids: tuple[str, ...]
    set_relation_artifact_content_ids: tuple[str, ...]
    technical_state: CidNlTechnicalState
    method_result: CidNlMethodResult | None
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, prefix, field in (
            (
                self.canonical_sample_identity_id,
                _SAMPLE_ID_PREFIX,
                "canonical_sample_identity_id",
            ),
            (
                self.acquisition_source_id,
                _ACQUISITION_SOURCE_ID_PREFIX,
                "acquisition_source_id",
            ),
        ):
            _validate_typed_digest_id(value, prefix=prefix, field=field)
        if not isinstance(self.method_subject, CidNlMethodSubject):
            raise CidNlMethodAssessmentContractError(
                "method_subject must be owner-normalized"
            )
        if self.provider_id != CID_NL_PROVIDER_ID:
            raise CidNlMethodAssessmentContractError(
                f"provider_id must be {CID_NL_PROVIDER_ID!r}"
            )
        if self.method_id != CID_NL_METHOD_ID:
            raise CidNlMethodAssessmentContractError(
                f"method_id must be {CID_NL_METHOD_ID!r}"
            )
        if self.method_version != CID_NL_METHOD_VERSION:
            raise CidNlMethodAssessmentContractError(
                f"method_version must be {CID_NL_METHOD_VERSION!r}"
            )
        _required_text(self.detector_invocation_id, field="detector_invocation_id")
        _canonical_digest_ids(
            self.candidate_peak_content_ids,
            prefix=_PEAK_CONTENT_ID_PREFIX,
            field="candidate_peak_content_ids",
            allow_empty=True,
        )
        if not isinstance(self.evaluation_receipt, CidNlEvaluationReceipt):
            raise CidNlMethodAssessmentContractError(
                "evaluation_receipt must be owner-normalized"
            )
        for values, field in (
            (self.evidence_artifact_content_ids, "evidence_artifact_content_ids"),
            (
                self.individual_relation_artifact_content_ids,
                "individual_relation_artifact_content_ids",
            ),
            (
                self.set_relation_artifact_content_ids,
                "set_relation_artifact_content_ids",
            ),
        ):
            _canonical_digest_ids(
                values,
                prefix=_ARTIFACT_CONTENT_ID_PREFIX,
                field=field,
                allow_empty=True,
            )
        if self.technical_state not in _TECHNICAL_STATES:
            raise CidNlMethodAssessmentContractError(
                "technical_state must be evaluated, unsupported, failed, or "
                "not_evaluated"
            )
        if self.method_result is not None and self.method_result not in _METHOD_RESULTS:
            raise CidNlMethodAssessmentContractError(
                "method_result must be SUPPORT, HOLD, REJECT, or None"
            )
        if self.technical_state != "evaluated" and self.method_result is not None:
            raise CidNlMethodAssessmentContractError(
                "non-evaluated technical state requires method_result=None"
            )
        if self.technical_state == "evaluated" and self.method_result is None:
            raise CidNlMethodAssessmentContractError(
                "completed evaluation requires a named method result"
            )
        if (
            not isinstance(self.reason_codes, tuple)
            or not self.reason_codes
            or any(
                not isinstance(item, str) or not item.strip()
                for item in self.reason_codes
            )
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
        ):
            raise CidNlMethodAssessmentContractError(
                "reason_codes must be non-empty, unique, and canonical-sorted"
            )

    @property
    def target_snapshot_id(self) -> str | None:
        """Targeted compatibility projection; not workflow-neutral identity."""

        return self.method_subject.target_snapshot_id

    @property
    def target_key_id(self) -> str | None:
        """Targeted compatibility projection; not workflow-neutral identity."""

        return self.method_subject.target_key_id

    @property
    def target_definition_id(self) -> str | None:
        """Targeted compatibility projection; not workflow-neutral identity."""

        return self.method_subject.target_definition_id


@dataclass(frozen=True, slots=True)
class CidNlMethodAssessmentRef:
    """One complete method attempt plus its exact typed upstream references."""

    content: CidNlMethodAssessmentContent
    candidate_peaks: tuple[PeakObservationRef, ...]
    evidence_refs: tuple[CidNlEvidenceRef, ...]
    individual_support_refs: tuple[DdaCidNlPeakSupportRef, ...]
    set_relation_refs: tuple[DdaCidNlCandidateSetRelationRef, ...]
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        if not isinstance(self.content, CidNlMethodAssessmentContent):
            raise CidNlMethodAssessmentContractError(
                "content must be an owner-normalized method assessment"
            )
        candidates = _canonical_candidates(self.candidate_peaks, allow_empty=True)
        evidence = _canonical_evidence_refs(self.evidence_refs)
        individual = _canonical_individual_refs(self.individual_support_refs)
        set_relations = _canonical_set_refs(self.set_relation_refs)
        if candidates != self.candidate_peaks:
            raise CidNlMethodAssessmentContractError(
                "candidate_peaks must be canonical-sorted"
            )
        if evidence != self.evidence_refs:
            raise CidNlMethodAssessmentContractError(
                "evidence_refs must be canonical-sorted"
            )
        if individual != self.individual_support_refs:
            raise CidNlMethodAssessmentContractError(
                "individual_support_refs must be canonical-sorted"
            )
        if set_relations != self.set_relation_refs:
            raise CidNlMethodAssessmentContractError(
                "set_relation_refs must be canonical-sorted"
            )
        if self.content.candidate_peak_content_ids != tuple(
            peak.content.peak_observation_content_id for peak in candidates
        ):
            raise CidNlMethodAssessmentContractError(
                "assessment candidate universe does not match its exact refs"
            )
        if self.content.evidence_artifact_content_ids != tuple(
            ref.artifact.content_identity.artifact_content_id for ref in evidence
        ):
            raise CidNlMethodAssessmentContractError(
                "assessment evidence content does not match its exact refs"
            )
        if self.content.individual_relation_artifact_content_ids != tuple(
            ref.artifact.content_identity.artifact_content_id for ref in individual
        ):
            raise CidNlMethodAssessmentContractError(
                "assessment individual relations do not match their exact refs"
            )
        if self.content.set_relation_artifact_content_ids != tuple(
            ref.artifact.content_identity.artifact_content_id for ref in set_relations
        ):
            raise CidNlMethodAssessmentContractError(
                "assessment set relations do not match their exact refs"
            )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise CidNlMethodAssessmentContractError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise CidNlMethodAssessmentContractError(
                "artifact semantic contract is not owned by CID-NL assessment"
            )
        upstreams = _assessment_upstream_artifacts(
            candidate_peaks=candidates,
            evidence_refs=evidence,
            individual_support_refs=individual,
            set_relation_refs=set_relations,
        )
        expected_upstream_ids = tuple(
            artifact.evidence_artifact_id for artifact in upstreams
        )
        if self.artifact.upstream_artifact_ids != expected_upstream_ids:
            raise CidNlMethodAssessmentContractError(
                "assessment upstream artifact closure was substituted"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_cid_nl_method_assessment_bytes(self.content),
            )
        except ValueError as exc:
            raise CidNlMethodAssessmentContractError(
                "assessment mismatches its immutable artifact"
            ) from exc


def mint_cid_nl_method_assessment_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition | None = None,
    method_subject: CidNlMethodSubject | None = None,
    detector_invocation_id: str,
    evaluation_receipt: CidNlEvaluationReceipt,
    candidate_peaks: tuple[PeakObservationRef, ...] = (),
    evidence_refs: tuple[CidNlEvidenceRef, ...] = (),
    individual_support_refs: tuple[DdaCidNlPeakSupportRef, ...] = (),
    set_relation_refs: tuple[DdaCidNlCandidateSetRelationRef, ...] = (),
    technical_state: CidNlTechnicalState = "evaluated",
) -> CidNlMethodAssessmentRef:
    """Close one complete CID-NL attempt without granting product authority."""

    subject = _resolve_method_subject(
        target=target,
        method_subject=method_subject,
    )
    _validate_owner_sample_subject(processing_owners, sample, subject, target=target)
    if not isinstance(evaluation_receipt, CidNlEvaluationReceipt):
        raise CidNlMethodAssessmentContractError(
            "evaluation_receipt must be owner-normalized"
        )
    _required_text(detector_invocation_id, field="detector_invocation_id")
    candidates = _canonical_candidates(candidate_peaks, allow_empty=True)
    evidence = _canonical_evidence_refs(evidence_refs)
    individual = _canonical_individual_refs(individual_support_refs)
    set_relations = _canonical_set_refs(set_relation_refs)
    _validate_attempt_state(
        technical_state=technical_state,
        evaluation_receipt=evaluation_receipt,
        candidate_peaks=candidates,
        evidence_refs=evidence,
        individual_support_refs=individual,
        set_relation_refs=set_relations,
    )
    _validate_upstream_bindings(
        processing_owners=processing_owners,
        sample=sample,
        method_subject=subject,
        evaluation_receipt=evaluation_receipt,
        candidate_peaks=candidates,
        evidence_refs=evidence,
        individual_support_refs=individual,
        set_relation_refs=set_relations,
    )
    method_result, reason_codes = _method_result(
        method_subject=subject,
        technical_state=technical_state,
        evaluation_receipt=evaluation_receipt,
        candidate_peaks=candidates,
        evidence_refs=evidence,
        individual_support_refs=individual,
        set_relation_refs=set_relations,
    )
    acquisition_source = resolve_request_acquisition_source(
        processing_owners=processing_owners,
        sample=sample,
    )
    content = CidNlMethodAssessmentContent(
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=acquisition_source.acquisition_source_id,
        provider_id=CID_NL_PROVIDER_ID,
        method_id=CID_NL_METHOD_ID,
        method_version=CID_NL_METHOD_VERSION,
        method_subject=subject,
        detector_invocation_id=detector_invocation_id,
        candidate_peak_content_ids=tuple(
            peak.content.peak_observation_content_id for peak in candidates
        ),
        evaluation_receipt=evaluation_receipt,
        evidence_artifact_content_ids=tuple(
            ref.artifact.content_identity.artifact_content_id for ref in evidence
        ),
        individual_relation_artifact_content_ids=tuple(
            ref.artifact.content_identity.artifact_content_id for ref in individual
        ),
        set_relation_artifact_content_ids=tuple(
            ref.artifact.content_identity.artifact_content_id for ref in set_relations
        ),
        technical_state=technical_state,
        method_result=method_result,
        reason_codes=reason_codes,
    )
    upstreams = _assessment_upstream_artifacts(
        candidate_peaks=candidates,
        evidence_refs=evidence,
        individual_support_refs=individual,
        set_relation_refs=set_relations,
    )
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=canonical_cid_nl_method_assessment_bytes(content),
        upstream_artifacts=upstreams,
    )
    return CidNlMethodAssessmentRef(
        content=content,
        candidate_peaks=candidates,
        evidence_refs=evidence,
        individual_support_refs=individual,
        set_relation_refs=set_relations,
        artifact=artifact,
    )


def validate_cid_nl_method_assessment_binding(
    reference: CidNlMethodAssessmentRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition | None = None,
    method_subject: CidNlMethodSubject | None = None,
) -> None:
    """Bind an already-validated assessment to its consuming request and sample."""

    if not isinstance(reference, CidNlMethodAssessmentRef):
        raise CidNlMethodAssessmentContractError(
            "reference must be an owner-minted CID-NL method assessment"
        )
    subject = _resolve_method_subject(
        target=target,
        method_subject=method_subject,
    )
    try:
        source = resolve_request_acquisition_source(
            processing_owners=processing_owners,
            sample=sample,
        )
    except IdentityContractError as exc:
        raise CidNlMethodAssessmentContractError(str(exc)) from exc
    # Construction pins content and upstream refs; minting settles the method.
    # A handoff checks scope without re-running that scientific decision.
    if (
        reference.artifact.producer_processing_request_id
        != processing_owners.request.processing_request_id
    ):
        raise CidNlMethodAssessmentContractError(
            "CID-NL method assessment references a different processing request"
        )
    if (
        reference.artifact.producer_processing_attempt_id
        != processing_owners.attempt.processing_attempt_id
    ):
        raise CidNlMethodAssessmentContractError(
            "CID-NL method assessment references a different processing attempt"
        )
    if (
        reference.content.canonical_sample_identity_id
        != sample.identity.sample_identity_id
        or reference.content.acquisition_source_id != source.acquisition_source_id
        or reference.content.method_subject != subject
    ):
        raise CidNlMethodAssessmentContractError(
            "CID-NL method assessment sample or subject binding was substituted"
        )


def canonical_cid_nl_method_assessment_bytes(
    content: CidNlMethodAssessmentContent,
) -> bytes:
    if not isinstance(content, CidNlMethodAssessmentContent):
        raise CidNlMethodAssessmentContractError(
            "canonical serialization requires method assessment content"
        )
    content_payload = asdict(content)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "content": content_payload,
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
        raise CidNlMethodAssessmentContractError(
            "method assessment payload must be canonical JSON"
        ) from exc
    return (serialized + "\n").encode("utf-8")


def _resolve_method_subject(
    *,
    target: ResolvedTargetDefinition | None,
    method_subject: CidNlMethodSubject | None,
) -> CidNlMethodSubject:
    if (target is None) == (method_subject is None):
        raise CidNlMethodAssessmentContractError(
            "assessment requires exactly one Targeted adapter or method_subject"
        )
    if target is not None:
        try:
            return cid_nl_method_subject_from_target(target)
        except CidNlMethodSubjectContractError as exc:
            raise CidNlMethodAssessmentContractError(str(exc)) from exc
    assert method_subject is not None
    return method_subject


def _validate_owner_sample_subject(
    processing_owners: object,
    sample: object,
    method_subject: object,
    *,
    target: ResolvedTargetDefinition | None,
) -> None:
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise CidNlMethodAssessmentContractError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CidNlMethodAssessmentContractError(
            "sample must be an owner-resolved canonical sample"
        )
    if not isinstance(method_subject, CidNlMethodSubject):
        raise CidNlMethodAssessmentContractError(
            "method_subject must be owner-normalized"
        )
    if sample not in processing_owners.sample_resolutions:
        raise CidNlMethodAssessmentContractError(
            "assessment sample is outside the processing request"
        )
    try:
        validate_cid_nl_method_subject_binding(
            method_subject,
            processing_owners=processing_owners,
            target=target,
        )
    except CidNlMethodSubjectContractError as exc:
        raise CidNlMethodAssessmentContractError(str(exc)) from exc
    try:
        source = resolve_request_acquisition_source(
            processing_owners=processing_owners,
            sample=sample,
        )
    except IdentityContractError as exc:
        raise CidNlMethodAssessmentContractError(str(exc)) from exc
    if not source.acquisition_source_id:
        raise CidNlMethodAssessmentContractError(
            "assessment requires an acquisition-source binding"
        )


def _validate_attempt_state(
    *,
    technical_state: object,
    evaluation_receipt: CidNlEvaluationReceipt,
    candidate_peaks: tuple[PeakObservationRef, ...],
    evidence_refs: tuple[CidNlEvidenceRef, ...],
    individual_support_refs: tuple[DdaCidNlPeakSupportRef, ...],
    set_relation_refs: tuple[DdaCidNlCandidateSetRelationRef, ...],
) -> None:
    if technical_state not in _TECHNICAL_STATES:
        raise CidNlMethodAssessmentContractError(
            "technical_state must be evaluated, unsupported, failed, or not_evaluated"
        )
    if technical_state == "evaluated":
        if not (
            evaluation_receipt.acquisition_supported
            and evaluation_receipt.search_completed
        ):
            raise CidNlMethodAssessmentContractError(
                "evaluated state requires a completed supported search receipt"
            )
        if evaluation_receipt.reason_code is not None:
            raise CidNlMethodAssessmentContractError(
                "completed evaluation cannot carry a technical failure reason"
            )
        if evaluation_receipt.scanned_event_count < len(
            {
                (ref.observation.acquisition_source_id, ref.observation.scan_number)
                for ref in evidence_refs
            }
        ):
            raise CidNlMethodAssessmentContractError(
                "scanned_event_count cannot be smaller than measured evidence"
            )
        return
    if candidate_peaks or evidence_refs or individual_support_refs or set_relation_refs:
        raise CidNlMethodAssessmentContractError(
            "non-evaluated technical state may not carry scientific result refs"
        )
    if evaluation_receipt.search_completed or evaluation_receipt.scanned_event_count:
        raise CidNlMethodAssessmentContractError(
            "non-evaluated technical state cannot claim a completed search"
        )
    if evaluation_receipt.reason_code is None:
        raise CidNlMethodAssessmentContractError(
            "non-evaluated technical state requires a typed reason"
        )
    if technical_state == "unsupported" and evaluation_receipt.acquisition_supported:
        raise CidNlMethodAssessmentContractError(
            "unsupported state requires acquisition_supported=False"
        )
    if technical_state in {"failed", "not_evaluated"} and not (
        evaluation_receipt.acquisition_supported
    ):
        raise CidNlMethodAssessmentContractError(
            f"{technical_state} requires a supported acquisition capability"
        )


def _validate_upstream_bindings(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    method_subject: CidNlMethodSubject,
    evaluation_receipt: CidNlEvaluationReceipt,
    candidate_peaks: tuple[PeakObservationRef, ...],
    evidence_refs: tuple[CidNlEvidenceRef, ...],
    individual_support_refs: tuple[DdaCidNlPeakSupportRef, ...],
    set_relation_refs: tuple[DdaCidNlCandidateSetRelationRef, ...],
) -> None:
    for peak in candidate_peaks:
        try:
            validate_peak_observation_binding(
                peak,
                processing_owners=processing_owners,
                sample=sample,
            )
        except PeakObservationContractError as exc:
            raise CidNlMethodAssessmentContractError(str(exc)) from exc
        _validate_target_precursor(
            observed_mz=peak.content.mz,
            target_mz=method_subject.precursor_mz,
            target_tolerance_ppm=method_subject.precursor_tolerance_ppm,
            subject="candidate peak",
        )
        _validate_query_rt(
            observed_rt_min=peak.content.integration.rt_apex_min,
            evaluation_receipt=evaluation_receipt,
            subject="candidate peak apex",
        )
    evidence_by_content_id = {}
    for evidence in evidence_refs:
        try:
            validate_cid_nl_evidence_binding(
                evidence,
                processing_owners=processing_owners,
                sample=sample,
            )
        except CidNlEvidenceContractError as exc:
            raise CidNlMethodAssessmentContractError(str(exc)) from exc
        _validate_target_precursor(
            observed_mz=evidence.observation.precursor_mz,
            target_mz=method_subject.precursor_mz,
            target_tolerance_ppm=method_subject.precursor_tolerance_ppm,
            subject="CID-NL evidence",
        )
        _validate_query_rt(
            observed_rt_min=evidence.observation.scan_rt_min,
            evaluation_receipt=evaluation_receipt,
            subject="CID-NL evidence scan",
        )
        if not any(
            math.isclose(
                evidence.observation.configured_neutral_loss_da,
                configured_loss,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            for configured_loss in method_subject.configured_losses
        ):
            raise CidNlMethodAssessmentContractError(
                "CID-NL evidence references a different method-subject neutral loss"
            )
        evidence_by_content_id[
            evidence.artifact.content_identity.artifact_content_id
        ] = evidence
    acquisition_scan_keys = tuple(
        (
            evidence.observation.acquisition_source_id,
            evidence.observation.scan_number,
            evidence.observation.configured_neutral_loss_da
            if method_subject.composition
            else None,
        )
        for evidence in evidence_refs
    )
    if len(set(acquisition_scan_keys)) != len(acquisition_scan_keys):
        raise CidNlMethodAssessmentContractError(
            "duplicate acquisition scan evidence cannot multiply one method event"
        )
    candidate_ids = tuple(
        peak.content.peak_observation_content_id for peak in candidate_peaks
    )
    support_policy = evaluation_receipt.support_policy
    individual_evidence_ids: set[str] = set()
    for support in individual_support_refs:
        canonical_evidence = evidence_by_content_id.get(
            support.content.cid_nl_evidence_content_id
        )
        if canonical_evidence is None:
            raise CidNlMethodAssessmentContractError(
                "individual support evidence is missing from the assessment"
            )
        if support.content.candidate_peak_content_ids != candidate_ids:
            raise CidNlMethodAssessmentContractError(
                "individual support references a different candidate universe"
            )
        if support.content.policy != support_policy:
            raise CidNlMethodAssessmentContractError(
                "individual support references a different method support policy"
            )
        try:
            validate_dda_cid_nl_peak_support_binding(
                support,
                processing_owners=processing_owners,
                sample=sample,
                canonical_evidence=canonical_evidence,
                candidate_peaks=candidate_peaks,
            )
        except DdaCidNlPeakSupportContractError as exc:
            raise CidNlMethodAssessmentContractError(str(exc)) from exc
        if not _is_strict_unique_support(
            support=support,
            candidate_peaks=candidate_peaks,
        ):
            raise CidNlMethodAssessmentContractError(
                "boundary or positive-width overlapping support requires a set-level "
                "relation"
            )
        individual_evidence_ids.add(support.content.cid_nl_evidence_content_id)
    set_relation_evidence_ids: set[str] = set()
    for relation in set_relation_refs:
        if relation.content.candidate_peak_content_ids != candidate_ids:
            raise CidNlMethodAssessmentContractError(
                "set relation references a different candidate universe"
            )
        if relation.content.cid_nl_evidence_content_id not in evidence_by_content_id:
            raise CidNlMethodAssessmentContractError(
                "set relation evidence is missing from the assessment"
            )
        if relation.content.policy != support_policy:
            raise CidNlMethodAssessmentContractError(
                "set relation references a different method support policy"
            )
        try:
            validate_dda_cid_nl_candidate_set_relation_binding(
                relation,
                processing_owners=processing_owners,
                sample=sample,
            )
        except DdaCidNlCandidateSetRelationContractError as exc:
            raise CidNlMethodAssessmentContractError(str(exc)) from exc
        set_relation_evidence_ids.add(relation.content.cid_nl_evidence_content_id)
    if individual_evidence_ids & set_relation_evidence_ids:
        raise CidNlMethodAssessmentContractError(
            "one CID-NL evidence ref cannot carry both individual and set relations"
        )
    settled_evidence_ids = individual_evidence_ids | set_relation_evidence_ids
    for evidence_content_id, evidence in evidence_by_content_id.items():
        if evidence.observation.observed_loss_error_ppm > (
            support_policy.neutral_loss_tolerance_ppm
        ):
            continue
        if (
            _evidence_matches_candidate_universe(
                evidence=evidence,
                candidate_peaks=candidate_peaks,
                support_policy=support_policy,
            )
            and evidence_content_id not in settled_evidence_ids
        ):
            raise CidNlMethodAssessmentContractError(
                "qualifying CID-NL evidence matching the candidate universe "
                "requires an individual or set-level relation"
            )


def _validate_target_precursor(
    *,
    observed_mz: float,
    target_mz: float,
    target_tolerance_ppm: float,
    subject: str,
) -> None:
    precursor_error_ppm = abs(observed_mz - target_mz) / target_mz * 1_000_000.0
    if precursor_error_ppm > target_tolerance_ppm:
        raise CidNlMethodAssessmentContractError(
            f"{subject} is outside the exact target precursor tolerance"
        )


def _validate_query_rt(
    *,
    observed_rt_min: float,
    evaluation_receipt: CidNlEvaluationReceipt,
    subject: str,
) -> None:
    if not (
        evaluation_receipt.query_rt_min
        <= observed_rt_min
        <= evaluation_receipt.query_rt_max
    ):
        raise CidNlMethodAssessmentContractError(
            f"{subject} is outside the evaluation query RT window"
        )


def _evidence_matches_candidate_universe(
    *,
    evidence: CidNlEvidenceRef,
    candidate_peaks: tuple[PeakObservationRef, ...],
    support_policy: DdaCidNlPeakSupportPolicy,
) -> bool:
    observation = evidence.observation
    return any(
        peak.content.integration.rt_left_min
        <= observation.scan_rt_min
        <= peak.content.integration.rt_right_min
        and abs(observation.precursor_mz - peak.content.mz)
        <= support_policy.precursor_tolerance_da
        for peak in candidate_peaks
    )


def _is_strict_unique_support(
    *,
    support: DdaCidNlPeakSupportRef,
    candidate_peaks: tuple[PeakObservationRef, ...],
) -> bool:
    selected = next(
        peak
        for peak in candidate_peaks
        if peak.content.peak_observation_content_id
        == support.content.peak_observation_content_id
    )
    selected_interval = selected.content.integration
    if not (
        selected_interval.rt_left_min
        < support.content.scan_rt_min
        < selected_interval.rt_right_min
    ):
        return False
    for candidate in candidate_peaks:
        if candidate is selected:
            continue
        if abs(support.content.precursor_mz - candidate.content.mz) > (
            support.content.policy.precursor_tolerance_da
        ):
            continue
        interval = candidate.content.integration
        if (
            selected_interval.rt_left_min < interval.rt_right_min
            and interval.rt_left_min < selected_interval.rt_right_min
        ):
            return False
    return True


def _method_result(
    *,
    method_subject: CidNlMethodSubject,
    technical_state: CidNlTechnicalState,
    evaluation_receipt: CidNlEvaluationReceipt,
    candidate_peaks: tuple[PeakObservationRef, ...],
    evidence_refs: tuple[CidNlEvidenceRef, ...],
    individual_support_refs: tuple[DdaCidNlPeakSupportRef, ...],
    set_relation_refs: tuple[DdaCidNlCandidateSetRelationRef, ...],
) -> tuple[CidNlMethodResult | None, tuple[str, ...]]:
    if technical_state != "evaluated":
        assert evaluation_receipt.reason_code is not None
        return None, (evaluation_receipt.reason_code,)
    if _supported_peak_ids(method_subject, evidence_refs, individual_support_refs):
        return "SUPPORT", ("unique_support_within_declared_candidate_universe",)
    if method_subject.composition is not None and individual_support_refs:
        return "HOLD", ("incomplete_composite_requirement",)
    if set_relation_refs:
        return "HOLD", ("set_level_relation",)
    if not candidate_peaks and any(
        evidence.observation.observed_loss_error_ppm
        <= evaluation_receipt.support_policy.neutral_loss_tolerance_ppm
        for evidence in evidence_refs
    ):
        return "REJECT", ("qualifying_cid_nl_without_usable_ms1_peak",)
    if candidate_peaks:
        return "HOLD", ("no_unique_qualifying_relation",)
    return "HOLD", ("insufficient_method_evidence",)


def is_cid_nl_missing_ms1_rejection(assessment: CidNlMethodAssessmentRef) -> bool:
    """Recognize a completed rejection; group owners still authorize a revisit."""
    return (
        assessment.content.technical_state == "evaluated"
        and assessment.content.method_result == "REJECT"
        and assessment.content.reason_codes
        == ("qualifying_cid_nl_without_usable_ms1_peak",)
        and not assessment.candidate_peaks
    )


def is_unsearched_cid_nl_discovery_assessment(
    assessment: CidNlMethodAssessmentRef,
) -> bool:
    """Recognize missing eligible input, without granting recovery authority."""
    return (
        assessment.content.technical_state == "not_evaluated"
        and assessment.content.reason_codes == ("no_search_eligible_discovery_input",)
    )


def supported_cid_nl_peak_ids(assessment: CidNlMethodAssessmentRef) -> frozenset[str]:
    """Physical peaks that satisfy the complete owner-bound requirement."""
    if assessment.content.technical_state != "evaluated":
        return frozenset()
    return _supported_peak_ids(
        assessment.content.method_subject,
        assessment.evidence_refs,
        assessment.individual_support_refs,
    )


def _supported_peak_ids(
    subject: CidNlMethodSubject,
    evidence_refs: tuple[CidNlEvidenceRef, ...],
    individual_support_refs: tuple[DdaCidNlPeakSupportRef, ...],
) -> frozenset[str]:
    loss_by_evidence = {
        ref.artifact.content_identity.artifact_content_id: (
            ref.observation.configured_neutral_loss_da
        )
        for ref in evidence_refs
    }
    losses_by_peak: dict[str, set[float]] = {}
    for support in individual_support_refs:
        losses_by_peak.setdefault(
            support.content.peak_observation_content_id, set()
        ).add(loss_by_evidence[support.content.cid_nl_evidence_content_id])
    required = set(subject.configured_losses)
    return frozenset(
        peak_id
        for peak_id, losses in losses_by_peak.items()
        if subject.composition is None
        or subject.composition.combine_mode == "union"
        or required.issubset(losses)
    )


def _assessment_upstream_artifacts(
    *,
    candidate_peaks: tuple[PeakObservationRef, ...],
    evidence_refs: tuple[CidNlEvidenceRef, ...],
    individual_support_refs: tuple[DdaCidNlPeakSupportRef, ...],
    set_relation_refs: tuple[DdaCidNlCandidateSetRelationRef, ...],
) -> tuple[EvidenceArtifactRef, ...]:
    artifacts = (
        *(peak.artifact for peak in candidate_peaks),
        *(ref.artifact for ref in evidence_refs),
        *(ref.artifact for ref in individual_support_refs),
        *(ref.artifact for ref in set_relation_refs),
    )
    artifact_ids = tuple(artifact.evidence_artifact_id for artifact in artifacts)
    if len(set(artifact_ids)) != len(artifact_ids):
        raise CidNlMethodAssessmentContractError(
            "assessment upstream artifacts must be unique"
        )
    return tuple(sorted(artifacts, key=lambda item: item.evidence_artifact_id))


def _canonical_candidates(
    value: object,
    *,
    allow_empty: bool,
) -> tuple[PeakObservationRef, ...]:
    if not isinstance(value, tuple) or (not value and not allow_empty):
        raise CidNlMethodAssessmentContractError(
            "candidate_peaks must be an immutable tuple"
        )
    if any(not isinstance(item, PeakObservationRef) for item in value):
        raise CidNlMethodAssessmentContractError(
            "candidate_peaks must contain owner-backed observations"
        )
    refs = tuple(
        sorted(
            value,
            key=lambda item: (
                item.content.peak_observation_content_id,
                item.artifact.evidence_artifact_id,
            ),
        )
    )
    _require_unique_refs(
        tuple(item.content.peak_observation_content_id for item in refs),
        field="candidate peak content",
    )
    _require_unique_refs(
        tuple(item.artifact.evidence_artifact_id for item in refs),
        field="candidate peak artifact",
    )
    return refs


def _canonical_evidence_refs(value: object) -> tuple[CidNlEvidenceRef, ...]:
    return _canonical_refs(
        value,
        expected_type=CidNlEvidenceRef,
        field="evidence_refs",
    )


def _canonical_individual_refs(value: object) -> tuple[DdaCidNlPeakSupportRef, ...]:
    return _canonical_refs(
        value,
        expected_type=DdaCidNlPeakSupportRef,
        field="individual_support_refs",
    )


def _canonical_set_refs(
    value: object,
) -> tuple[DdaCidNlCandidateSetRelationRef, ...]:
    return _canonical_refs(
        value,
        expected_type=DdaCidNlCandidateSetRelationRef,
        field="set_relation_refs",
    )


def _canonical_refs(value: object, *, expected_type: type, field: str) -> tuple:
    if not isinstance(value, tuple):
        raise CidNlMethodAssessmentContractError(f"{field} must be a tuple")
    if any(not isinstance(item, expected_type) for item in value):
        raise CidNlMethodAssessmentContractError(
            f"{field} contains a non-owner reference"
        )
    refs = tuple(
        sorted(
            value,
            key=lambda item: (
                item.artifact.content_identity.artifact_content_id,
                item.artifact.evidence_artifact_id,
            ),
        )
    )
    _require_unique_refs(
        tuple(item.artifact.evidence_artifact_id for item in refs),
        field=field,
    )
    return refs


def _require_unique_refs(values: tuple[str, ...], *, field: str) -> None:
    if len(set(values)) != len(values):
        raise CidNlMethodAssessmentContractError(f"duplicate {field} refs")


def _canonical_digest_ids(
    value: object,
    *,
    prefix: str,
    field: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, tuple) or (not value and not allow_empty):
        raise CidNlMethodAssessmentContractError(f"{field} must be a tuple")
    ids = tuple(
        _validate_typed_digest_id(item, prefix=prefix, field=field) for item in value
    )
    if ids != tuple(sorted(set(ids))):
        raise CidNlMethodAssessmentContractError(
            f"{field} must be unique and canonical-sorted"
        )
    return ids


def _validate_typed_digest_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise CidNlMethodAssessmentContractError(
            f"{field} must use the {prefix!r} prefix"
        )
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise CidNlMethodAssessmentContractError(
            f"{field} must contain a SHA-256 digest"
        )
    return value


def _finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CidNlMethodAssessmentContractError(f"{field} must be finite")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise CidNlMethodAssessmentContractError(f"{field} must be finite")
    return 0.0 if normalized == 0.0 else normalized


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CidNlMethodAssessmentContractError(f"{field} must be non-empty")
    if value != value.strip():
        raise CidNlMethodAssessmentContractError(
            f"{field} must not contain surrounding whitespace"
        )
    return value


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "CID_NL_METHOD_ID",
    "CID_NL_METHOD_VERSION",
    "CID_NL_PROVIDER_ID",
    "CidNlEvaluationReceipt",
    "CidNlMethodAssessmentContent",
    "CidNlMethodAssessmentContractError",
    "CidNlMethodAssessmentRef",
    "CidNlMethodResult",
    "CidNlTechnicalState",
    "SCHEMA_VERSION",
    "canonical_cid_nl_method_assessment_bytes",
    "mint_cid_nl_method_assessment_ref",
    "validate_cid_nl_method_assessment_binding",
]
