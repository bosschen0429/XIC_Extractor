"""Minimal sample-by-group observation closure for diagnostic evidence flow."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    HypothesisOwnerAdapterError,
    validate_cid_nl_assessment_hypothesis_closure,
)
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    CidNlMethodResult,
    CidNlTechnicalState,
    validate_cid_nl_method_assessment_binding,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    PeakHypothesisContractError,
    validate_cid_nl_peak_hypothesis_binding,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.review_records import ResolvedReviewRecordRef
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "cell_observation_v2"
ARTIFACT_SEMANTIC_ROLE = "diagnostic_cell_observation"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf-v1"

_ARTIFACT_CONTENT_ID_PREFIX = "xic.artifact-content.v1:"
_EVIDENCE_ARTIFACT_ID_PREFIX = "xic.evidence-artifact.v1:"
_PEAK_CONTENT_ID_PREFIX = "xic.peak-observation-content.v1:"
_PEAK_HYPOTHESIS_ID_PREFIX = "xic.peak-hypothesis.v1:"
_SAMPLE_ID_PREFIX = "xic.sample.v1:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.alignment.cell_observations",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)


class CellObservationContractError(ValueError):
    """Raised when one assessment cannot enter an existing group cell exactly."""


@dataclass(frozen=True, slots=True)
class CellObservationContent:
    """Orthogonal axes only; no value, writer, fill, or product authority."""

    canonical_sample_identity_id: str
    group_hypothesis_id: str
    group_owner_ids: tuple[str, ...]
    technical_state: CidNlTechnicalState
    method_result: CidNlMethodResult | None
    peak_observation_content_id: str | None
    peak_hypothesis_id: str | None
    method_assessment_artifact_content_id: str
    evidence_artifact_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    human_disposition_ref_id: str | None

    def __post_init__(self) -> None:
        _validate_typed_digest_id(
            self.canonical_sample_identity_id,
            prefix=_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
        _required_text(self.group_hypothesis_id, field="group_hypothesis_id")
        if not self.group_owner_ids:
            raise CellObservationContractError("group_owner_ids must be non-empty")
        _canonical_typed_ids(
            self.group_owner_ids,
            prefix=_PEAK_HYPOTHESIS_ID_PREFIX,
            field="group_owner_ids",
        )
        if self.peak_observation_content_id is not None:
            _validate_typed_digest_id(
                self.peak_observation_content_id,
                prefix=_PEAK_CONTENT_ID_PREFIX,
                field="peak_observation_content_id",
            )
        if self.peak_hypothesis_id is not None:
            _validate_typed_digest_id(
                self.peak_hypothesis_id,
                prefix=_PEAK_HYPOTHESIS_ID_PREFIX,
                field="peak_hypothesis_id",
            )
        _validate_typed_digest_id(
            self.method_assessment_artifact_content_id,
            prefix=_ARTIFACT_CONTENT_ID_PREFIX,
            field="method_assessment_artifact_content_id",
        )
        _canonical_typed_ids(
            self.evidence_artifact_ids,
            prefix=_EVIDENCE_ARTIFACT_ID_PREFIX,
            field="evidence_artifact_ids",
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
            raise CellObservationContractError(
                "reason_codes must be non-empty, unique, and canonical-sorted"
            )
        if self.human_disposition_ref_id is not None:
            _required_text(
                self.human_disposition_ref_id,
                field="human_disposition_ref_id",
            )
        if self.technical_state not in {
            "evaluated",
            "unsupported",
            "failed",
            "not_evaluated",
        }:
            raise CellObservationContractError(
                "technical_state must be evaluated, unsupported, failed, or "
                "not_evaluated"
            )
        if self.method_result is not None and self.method_result not in {
            "SUPPORT",
            "HOLD",
            "REJECT",
        }:
            raise CellObservationContractError(
                "method_result must be SUPPORT, HOLD, REJECT, or None"
            )
        if self.technical_state != "evaluated" and self.method_result is not None:
            raise CellObservationContractError(
                "non-evaluated technical state requires method_result=None"
            )
        if self.technical_state == "evaluated" and self.method_result is None:
            raise CellObservationContractError(
                "evaluated technical state requires a named method result"
            )
        if self.method_result == "SUPPORT":
            if (
                self.peak_observation_content_id is None
                or self.peak_hypothesis_id is None
            ):
                raise CellObservationContractError(
                    "SUPPORT requires an observation and owner-backed hypothesis"
                )
        elif self.peak_hypothesis_id is not None:
            raise CellObservationContractError(
                "only SUPPORT may carry an individual PeakHypothesis"
            )


@dataclass(frozen=True, slots=True)
class CellObservationRef:
    """One exact cell closure under an already-existing provisional group."""

    content: CellObservationContent
    method_assessment: CidNlMethodAssessmentRef
    peak_observation: PeakObservationRef | None
    peak_hypothesis: PeakHypothesis | None
    human_disposition: ResolvedReviewRecordRef | None
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        if not isinstance(self.content, CellObservationContent):
            raise CellObservationContractError(
                "content must be an owner-normalized CellObservation"
            )
        if not isinstance(self.method_assessment, CidNlMethodAssessmentRef):
            raise CellObservationContractError(
                "method_assessment must be an owner-backed reference"
            )
        if self.content.canonical_sample_identity_id != (
            self.method_assessment.content.canonical_sample_identity_id
        ):
            raise CellObservationContractError(
                "cell and method assessment reference different samples"
            )
        if (
            self.content.technical_state
            != (self.method_assessment.content.technical_state)
            or self.content.method_result
            != self.method_assessment.content.method_result
        ):
            raise CellObservationContractError(
                "cell rewrites technical or method-result axes"
            )
        if self.content.reason_codes != self.method_assessment.content.reason_codes:
            raise CellObservationContractError(
                "cell rewrites method-assessment reason codes"
            )
        expected_evidence_ids = tuple(
            sorted(
                evidence.artifact.evidence_artifact_id
                for evidence in self.method_assessment.evidence_refs
            )
        )
        if self.content.evidence_artifact_ids != expected_evidence_ids:
            raise CellObservationContractError(
                "cell evidence refs do not match the method assessment"
            )
        if self.content.method_assessment_artifact_content_id != (
            self.method_assessment.artifact.content_identity.artifact_content_id
        ):
            raise CellObservationContractError(
                "cell references a different method assessment"
            )
        observation_content_id = (
            None
            if self.peak_observation is None
            else self.peak_observation.content.peak_observation_content_id
        )
        if self.content.peak_observation_content_id != observation_content_id:
            raise CellObservationContractError(
                "cell peak observation reference was substituted"
            )
        hypothesis_id = (
            None if self.peak_hypothesis is None else self.peak_hypothesis.hypothesis_id
        )
        if self.content.peak_hypothesis_id != hypothesis_id:
            raise CellObservationContractError(
                "cell PeakHypothesis reference was substituted"
            )
        human_id = (
            None
            if self.human_disposition is None
            else self.human_disposition.reference.review_record_id
        )
        if self.content.human_disposition_ref_id != human_id:
            raise CellObservationContractError(
                "cell human disposition reference was substituted"
            )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise CellObservationContractError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise CellObservationContractError(
                "artifact semantic contract is not owned by CellObservation"
            )
        expected_upstream_ids = tuple(
            artifact.evidence_artifact_id
            for artifact in _cell_upstream_artifacts(
                method_assessment=self.method_assessment,
                peak_observation=self.peak_observation,
                peak_hypothesis=self.peak_hypothesis,
            )
        )
        if self.artifact.upstream_artifact_ids != expected_upstream_ids:
            raise CellObservationContractError(
                "cell upstream artifact closure was substituted"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_cell_observation_bytes(self.content),
            )
        except ValueError as exc:
            raise CellObservationContractError(
                "cell observation mismatches its immutable artifact"
            ) from exc


def mint_cell_observation_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
    group: CrossSamplePeakGroupHypothesis,
    method_assessment: CidNlMethodAssessmentRef,
    peak_observation: PeakObservationRef | None = None,
    peak_hypothesis: PeakHypothesis | None = None,
    human_disposition: ResolvedReviewRecordRef | None = None,
) -> CellObservationRef:
    """Attach a completed assessment to an existing group without creating it."""

    if not isinstance(group, CrossSamplePeakGroupHypothesis) or not group.owner_ids:
        raise CellObservationContractError(
            "CellObservation requires an existing/provisional group context"
        )
    try:
        validate_cid_nl_method_assessment_binding(
            method_assessment,
            processing_owners=processing_owners,
            sample=sample,
            target=target,
        )
    except ValueError as exc:
        raise CellObservationContractError(str(exc)) from exc
    method_result = method_assessment.content.method_result
    if method_result == "SUPPORT":
        if peak_observation is None or peak_hypothesis is None:
            raise CellObservationContractError(
                "SUPPORT cell requires the exact observation and hypothesis"
            )
        support = peak_hypothesis.evidence.cid_nl_chromatographic_support_ref
        if support is None:
            raise CellObservationContractError(
                "SUPPORT hypothesis is missing chromatographic support"
            )
        try:
            validate_cid_nl_assessment_hypothesis_closure(
                sample=sample,
                assessment=method_assessment,
                hypothesis=peak_hypothesis,
            )
            validate_cid_nl_peak_hypothesis_binding(
                peak_hypothesis,
                processing_owners=processing_owners,
                sample=sample,
                peak_observation=peak_observation,
                chromatographic_support=support,
            )
        except (HypothesisOwnerAdapterError, PeakHypothesisContractError) as exc:
            raise CellObservationContractError(str(exc)) from exc
        if peak_hypothesis.hypothesis_id not in group.owner_ids:
            raise CellObservationContractError(
                "SUPPORT hypothesis is not a member of the existing group"
            )
    else:
        if peak_hypothesis is not None:
            raise CellObservationContractError(
                "HOLD, REJECT, and technical states cannot carry a fake hypothesis"
            )
        if peak_observation is not None and peak_observation not in (
            method_assessment.candidate_peaks
        ):
            raise CellObservationContractError(
                "cell observation is outside the method candidate universe"
            )
    if human_disposition is not None:
        if not isinstance(human_disposition, ResolvedReviewRecordRef):
            raise CellObservationContractError(
                "human_disposition must be an owner-resolved review record"
            )
        if processing_owners.review_record != human_disposition:
            raise CellObservationContractError(
                "human disposition is outside the processing owner closure"
            )
        review_reference = human_disposition.reference
        if (
            review_reference.processing_request_id
            != processing_owners.request.processing_request_id
        ):
            raise CellObservationContractError(
                "human disposition references a different processing request"
            )
        if (
            review_reference.canonical_sample_identity_id
            != sample.identity.sample_identity_id
        ):
            raise CellObservationContractError(
                "human disposition references a different sample"
            )
        if (
            review_reference.target_key_id
            != target.definition.target_key.target_key_id
            or review_reference.target_definition_id
            != target.definition.target_definition_id
        ):
            raise CellObservationContractError(
                "human disposition references a different target definition"
            )
        if method_assessment.artifact.evidence_artifact_id not in (
            review_reference.evidence_artifact_ids
        ):
            raise CellObservationContractError(
                "human disposition does not reference the current method assessment"
            )
    evidence_artifact_ids = tuple(
        sorted(
            evidence.artifact.evidence_artifact_id
            for evidence in method_assessment.evidence_refs
        )
    )
    content = CellObservationContent(
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        group_hypothesis_id=group.group_hypothesis_id,
        group_owner_ids=tuple(sorted(group.owner_ids)),
        technical_state=method_assessment.content.technical_state,
        method_result=method_result,
        peak_observation_content_id=(
            None
            if peak_observation is None
            else peak_observation.content.peak_observation_content_id
        ),
        peak_hypothesis_id=(
            None if peak_hypothesis is None else peak_hypothesis.hypothesis_id
        ),
        method_assessment_artifact_content_id=(
            method_assessment.artifact.content_identity.artifact_content_id
        ),
        evidence_artifact_ids=evidence_artifact_ids,
        reason_codes=method_assessment.content.reason_codes,
        human_disposition_ref_id=(
            None
            if human_disposition is None
            else human_disposition.reference.review_record_id
        ),
    )
    upstreams = _cell_upstream_artifacts(
        method_assessment=method_assessment,
        peak_observation=peak_observation,
        peak_hypothesis=peak_hypothesis,
    )
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=canonical_cell_observation_bytes(content),
        upstream_artifacts=upstreams,
    )
    return CellObservationRef(
        content=content,
        method_assessment=method_assessment,
        peak_observation=peak_observation,
        peak_hypothesis=peak_hypothesis,
        human_disposition=human_disposition,
        artifact=artifact,
    )


def canonical_cell_observation_bytes(content: CellObservationContent) -> bytes:
    if not isinstance(content, CellObservationContent):
        raise CellObservationContractError(
            "canonical serialization requires CellObservation content"
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
        raise CellObservationContractError(
            "CellObservation payload must be canonical JSON"
        ) from exc
    return (serialized + "\n").encode("utf-8")


def _cell_upstream_artifacts(
    *,
    method_assessment: CidNlMethodAssessmentRef,
    peak_observation: PeakObservationRef | None,
    peak_hypothesis: PeakHypothesis | None,
) -> tuple[EvidenceArtifactRef, ...]:
    artifacts = [method_assessment.artifact]
    if peak_observation is not None:
        artifacts.append(peak_observation.artifact)
    if peak_hypothesis is not None:
        if peak_hypothesis.artifact is None:
            raise CellObservationContractError(
                "owner-backed hypothesis is missing its immutable artifact"
            )
        artifacts.append(peak_hypothesis.artifact)
    if len({item.evidence_artifact_id for item in artifacts}) != len(artifacts):
        raise CellObservationContractError("cell upstream artifacts must be unique")
    return tuple(sorted(artifacts, key=lambda item: item.evidence_artifact_id))


def _canonical_typed_ids(value: object, *, prefix: str, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise CellObservationContractError(f"{field} must be a tuple")
    ids = tuple(
        _validate_typed_digest_id(item, prefix=prefix, field=field) for item in value
    )
    if ids != tuple(sorted(set(ids))):
        raise CellObservationContractError(
            f"{field} must be unique and canonical-sorted"
        )
    return ids


def _validate_typed_digest_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise CellObservationContractError(f"{field} must use the {prefix!r} prefix")
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise CellObservationContractError(f"{field} must contain a SHA-256 digest")
    return value


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CellObservationContractError(f"{field} must be non-empty")
    if value != value.strip():
        raise CellObservationContractError(
            f"{field} must not contain surrounding whitespace"
        )
    return value


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "CellObservationContent",
    "CellObservationContractError",
    "CellObservationRef",
    "SCHEMA_VERSION",
    "canonical_cell_observation_bytes",
    "mint_cell_observation_ref",
]
