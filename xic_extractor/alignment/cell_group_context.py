"""CID-NL cell-group context contracts and validation primitives."""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Literal, TypeAlias

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
    construct_cross_sample_peak_group_hypotheses,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.edge_scoring import DriftLookupProtocol
from xic_extractor.alignment.hypothesis_owner_adapter import (
    HypothesisOwnerAdapterError,
    owner_from_cid_nl_assessment_support,
    validate_cid_nl_assessment_hypothesis_closure,
)
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    CidNlMethodSubjectContractError,
    validate_cid_nl_alignment_subject_compatibility,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.processing_identity import (
    ProcessingAttemptIdentity,
    retry_predecessor_attempt_ids,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "cell_observation_v4"
OUTSIDE_GROUP_SCHEMA_VERSION = "cell_observation_v5"
ARTIFACT_SEMANTIC_ROLE = "diagnostic_cell_observation"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf-v1"

_ARTIFACT_CONTENT_ID_PREFIX = "xic.artifact-content.v1:"
_EVIDENCE_ARTIFACT_ID_PREFIX = "xic.evidence-artifact.v1:"
_PEAK_CONTENT_ID_PREFIX = "xic.peak-observation-content.v1:"
_PEAK_HYPOTHESIS_ID_PREFIX = "xic.peak-hypothesis.v1:"
_SAMPLE_ID_PREFIX = "xic.sample.v1:"
_METHOD_SUBJECT_ID_PREFIX = "xic.cid-nl-method-subject.v1:"
_TARGET_KEY_ID_PREFIX = "xic.target-key.v1:"
_TARGET_DEFINITION_ID_PREFIX = "xic.target-definition.v1:"
_FINAL_CELL_OBSERVATION_ID_PREFIX = "xic.final-cell-observation.v1:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FINAL_CELL_MINT_TOKEN = object()
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.alignment.cell_observations",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)
_OUTSIDE_GROUP_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.alignment.cell_observations",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=OUTSIDE_GROUP_SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)

TerminalObservationVariant: TypeAlias = Literal[
    "observed",
    "searched_no_observation",
]



class CellObservationContractError(ValueError):
    """Raised when one assessment cannot enter an existing group cell exactly."""


@dataclass(frozen=True, slots=True)
class CidNlCellGroupSupport:
    """One exact SUPPORT closure that owns membership in a provisional group."""

    sample: ResolvedCanonicalSampleIdentity
    assessment: CidNlMethodAssessmentRef
    hypothesis: PeakHypothesis

    def __post_init__(self) -> None:
        try:
            validate_cid_nl_assessment_hypothesis_closure(
                sample=self.sample,
                assessment=self.assessment,
                hypothesis=self.hypothesis,
            )
        except HypothesisOwnerAdapterError as exc:
            raise CellObservationContractError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class CidNlCellGroupContext:
    """Method-subject proof for one group and every SUPPORT owner that formed it."""

    group: CrossSamplePeakGroupHypothesis
    supports: tuple[CidNlCellGroupSupport, ...]
    config: AlignmentConfig
    processing_attempt: ProcessingAttemptIdentity
    sample_universe: frozenset[str] | None = None
    injection_order: tuple[tuple[str, int], ...] | None = None
    drift_lookup: DriftLookupProtocol | None = None

    def __post_init__(self) -> None:
        if self.sample_universe is not None and not isinstance(
            self.sample_universe, frozenset
        ):
            raise CellObservationContractError(
                "group sample universe must be immutable"
            )
        if self.injection_order is not None and (
            not isinstance(self.injection_order, tuple)
            or any(
                not isinstance(item, tuple) or len(item) != 2
                for item in self.injection_order
            )
            or len(dict(self.injection_order)) != len(self.injection_order)
        ):
            raise CellObservationContractError(
                "group injection order must be immutable and unique"
            )
        if not isinstance(self.group, CrossSamplePeakGroupHypothesis) or not (
            self.group.owner_ids
        ):
            raise CellObservationContractError(
                "group context requires an existing provisional group"
            )
        if (
            not isinstance(self.supports, tuple)
            or not self.supports
            or any(
                not isinstance(item, CidNlCellGroupSupport) for item in self.supports
            )
        ):
            raise CellObservationContractError(
                "group context requires exact SUPPORT closures"
            )
        if not isinstance(self.config, AlignmentConfig):
            raise CellObservationContractError(
                "group context requires the exact AlignmentConfig"
            )
        if not isinstance(self.processing_attempt, ProcessingAttemptIdentity):
            raise CellObservationContractError(
                "group context requires an owner-issued processing attempt"
            )
        sample_ids = tuple(
            support.sample.identity.sample_identity_id for support in self.supports
        )
        if sample_ids != tuple(sorted(set(sample_ids))):
            raise CellObservationContractError(
                "group SUPPORT closures must be unique and canonical-sorted by sample"
            )
        try:
            validate_cid_nl_alignment_subject_compatibility(self.method_subjects)
        except CidNlMethodSubjectContractError as exc:
            raise CellObservationContractError(str(exc)) from exc
        expected_owners = tuple(
            owner_from_cid_nl_assessment_support(
                sample=support.sample,
                assessment=support.assessment,
                hypothesis=support.hypothesis,
            )
            for support in self.supports
        )
        expected_owner_ids = tuple(owner.owner_id for owner in expected_owners)
        if len(set(expected_owner_ids)) != len(self.supports):
            raise CellObservationContractError(
                "group SUPPORT closures contain duplicate hypotheses"
            )
        _validate_group_support_attempts(
            self.supports,
            producer_attempt=self.processing_attempt,
        )
        expected_groups = construct_cross_sample_peak_group_hypotheses(
            expected_owners,
            config=self.config,
            drift_lookup=self.drift_lookup,
            sample_universe=self.sample_universe,
            injection_order=(
                None if self.injection_order is None else dict(self.injection_order)
            ),
        )
        validate_cross_sample_peak_group_identity(self.group, require_canonical=True)
        # Public family ordinals belong to the complete population's presentation.
        if (
            len(expected_groups) != 1
            or replace(expected_groups[0], public_family_id=self.group.public_family_id)
            != self.group
        ):
            raise CellObservationContractError(
                "group context does not close its exact SUPPORT owners and config"
            )

    @property
    def method_subjects(self) -> tuple[CidNlMethodSubject, ...]:
        return tuple(
            sorted(
                {
                    support.assessment.content.method_subject.method_subject_id: (
                        support.assessment.content.method_subject
                    )
                    for support in self.supports
                }.values(),
                key=lambda subject: subject.method_subject_id,
            )
        )

    @property
    def method_subject(self) -> CidNlMethodSubject:
        center = self.group.group_center_mz
        if center is None:
            raise CellObservationContractError(
                "group recovery subject requires a finite group center m/z"
            )
        return min(
            self.method_subjects,
            key=lambda subject: (
                abs(subject.precursor_mz - center) / max(abs(center), 1e-12),
                subject.method_subject_id,
            ),
        )

    @property
    def method_subject_id(self) -> str:
        return self.method_subject.method_subject_id

    @property
    def target_key_id(self) -> str | None:
        """Targeted compatibility projection; not group identity."""

        return self.method_subject.target_key_id

    @property
    def target_definition_id(self) -> str | None:
        """Targeted compatibility projection; not group identity."""

        return self.method_subject.target_definition_id

    @property
    def support_assessment_artifact_content_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                support.assessment.artifact.content_identity.artifact_content_id
                for support in self.supports
            )
        )

_population_group_context_validation: ContextVar[
    tuple[dict[int, CidNlCellGroupContext], set[int]] | None
] = ContextVar("population_group_context_validation", default=None)


def _group_support_artifacts(
    supports: tuple[CidNlCellGroupSupport, ...],
) -> tuple[EvidenceArtifactRef, ...]:
    artifacts: list[EvidenceArtifactRef] = []
    for support in supports:
        artifacts.append(support.assessment.artifact)
        if support.hypothesis.artifact is None:
            raise CellObservationContractError(
                "group SUPPORT hypothesis is missing its immutable artifact"
            )
        artifacts.append(support.hypothesis.artifact)
    artifacts_by_id = {item.evidence_artifact_id: item for item in artifacts}
    return tuple(
        artifacts_by_id[artifact_id] for artifact_id in sorted(artifacts_by_id)
    )


def _validate_group_support_attempts(
    supports: tuple[CidNlCellGroupSupport, ...],
    *,
    producer_attempt: ProcessingAttemptIdentity,
) -> None:
    allowed_attempt_ids = {
        producer_attempt.processing_attempt_id,
        *retry_predecessor_attempt_ids(producer_attempt),
    }
    for artifact in _group_support_artifacts(supports):
        if (
            artifact.producer_processing_request_id
            != producer_attempt.processing_request_id
            or artifact.producer_processing_attempt_id not in allowed_attempt_ids
        ):
            raise CellObservationContractError(
                "group SUPPORT artifacts are outside the current attempt or retry "
                "lineage"
            )


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
