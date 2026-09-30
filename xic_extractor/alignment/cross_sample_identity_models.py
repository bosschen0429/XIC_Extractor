"""Identity, membership, and repaired observation models for CrossSample."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    typed_identity_digest,
    validate_typed_identity_basis,
    validate_typed_identity_id,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.validation_primitives import require_owner_normalized_text

_GROUP_MEMBER_SUBJECT_PREFIX = "xic.group-member-subject:"
_CROSS_SAMPLE_GROUP_PREFIX = "xic.cross-sample-group:"
CrossSampleGroupDisposition: TypeAlias = Literal[
    "established",
    "provisional",
    "unresolved",
    "rejected",
]
CrossSampleEdgeDisposition: TypeAlias = Literal[
    "eligible",
    "review_only",
    "excluded",
]
CrossSampleNeighborhoodStatus: TypeAlias = Literal["assessable", "unassessable"]
CrossSampleInitialEdgeContext: TypeAlias = Literal[
    "hard_gate",
    "complete_link",
    "full_batch_continuity",
    "targeted_istd_continuity",
]


class CrossSampleContractError(ValueError):
    """Raised when canonical CrossSample state cannot close exactly."""


def _required_text(value: object, *, field: str) -> str:
    return require_owner_normalized_text(
        value,
        field=field,
        error_factory=CrossSampleContractError,
    )


@dataclass(frozen=True, slots=True)
class CrossSampleGroupMemberSubject:
    member_subject_id: str
    processing_request_id: str
    canonical_sample_identity_id: str
    subject_kind: Literal["peak_hypothesis"]
    subject_id: str
    physical_observation_ids: tuple[str, ...]
    basis_bytes: bytes

    def __post_init__(self) -> None:
        request_id = validate_typed_identity_id(
            self.processing_request_id,
            prefix=PROCESSING_REQUEST_ID_PREFIX,
            field="processing_request_id",
        )
        sample_id = _required_text(
            self.canonical_sample_identity_id,
            field="canonical_sample_identity_id",
        )
        subject_id = _required_text(self.subject_id, field="subject_id")
        if self.subject_kind != "peak_hypothesis":
            raise CrossSampleContractError("subject_kind is not a closed variant")
        if not subject_id.startswith("xic.peak-hypothesis:"):
            raise CrossSampleContractError("member subject is not owner-backed")
        physical_ids = tuple(self.physical_observation_ids)
        if not physical_ids or tuple(sorted(set(physical_ids))) != physical_ids:
            raise CrossSampleContractError(
                "physical_observation_ids must be unique and canonical-sorted"
            )
        basis = canonical_identity_basis_bytes(
            {
                "schema_version": "group_member_subject",
                "processing_request_id": request_id,
                "canonical_sample_identity_id": sample_id,
                "subject": {"kind": self.subject_kind, "id": subject_id},
                "physical_observation_ids": list(physical_ids),
            }
        )
        if self.basis_bytes != basis:
            raise CrossSampleContractError("member subject basis bytes are not exact")
        try:
            validate_typed_identity_basis(
                self.member_subject_id,
                prefix=_GROUP_MEMBER_SUBJECT_PREFIX,
                basis_bytes=basis,
                field="member_subject_id",
            )
        except ValueError as exc:
            raise CrossSampleContractError(str(exc)) from exc


def _request_id(
    *,
    processing_owners: ProcessingOwnerReferences | None,
    processing_request_id: str | None,
) -> str:
    if processing_owners is not None:
        if not isinstance(processing_owners, ProcessingOwnerReferences):
            raise CrossSampleContractError("processing_owners must be owner-backed")
        owner_request = processing_owners.request.processing_request_id
        if processing_request_id is not None and processing_request_id != owner_request:
            raise CrossSampleContractError(
                "processing owner references crossed request"
            )
        return owner_request
    if processing_request_id is None:
        raise CrossSampleContractError("exact processing request identity is required")
    return validate_typed_identity_id(
        processing_request_id,
        prefix=PROCESSING_REQUEST_ID_PREFIX,
        field="processing_request_id",
    )


def group_member_subject_from_peak_hypothesis(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    hypothesis: PeakHypothesis,
    processing_owners: ProcessingOwnerReferences | None = None,
    processing_request_id: str | None = None,
) -> CrossSampleGroupMemberSubject:
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CrossSampleContractError("sample must be canonically resolved")
    if not isinstance(hypothesis, PeakHypothesis) or not hypothesis.is_owner_backed:
        raise CrossSampleContractError("hypothesis must be owner-backed")
    request_id = _request_id(
        processing_owners=processing_owners,
        processing_request_id=processing_request_id,
    )
    if (
        hypothesis.artifact is None
        or hypothesis.artifact.producer_processing_request_id != request_id
    ):
        raise CrossSampleContractError("peak hypothesis crossed processing request")
    sample_id = sample.identity.sample_identity_id
    physical_ids = tuple(
        sorted(
            observation.content.peak_observation_content_id
            for observation in hypothesis.peak_observation_refs
        )
    )
    if (
        not physical_ids
        or len(set(physical_ids)) != len(physical_ids)
        or any(
            observation.content.canonical_sample_identity_id != sample_id
            or observation.artifact.producer_processing_request_id != request_id
            for observation in hypothesis.peak_observation_refs
        )
    ):
        raise CrossSampleContractError("peak hypothesis crossed physical observation")
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject",
            "processing_request_id": request_id,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": "peak_hypothesis", "id": hypothesis.hypothesis_id},
            "physical_observation_ids": list(physical_ids),
        }
    )
    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(_GROUP_MEMBER_SUBJECT_PREFIX, basis),
        processing_request_id=request_id,
        canonical_sample_identity_id=sample_id,
        subject_kind="peak_hypothesis",
        subject_id=hypothesis.hypothesis_id,
        physical_observation_ids=physical_ids,
        basis_bytes=basis,
    )


@dataclass(slots=True)
class CrossSampleIdentityBasisRegistry:
    _basis_by_id: dict[str, bytes] = field(default_factory=dict)

    def register(self, identity_id: str, basis_bytes: bytes) -> None:
        _required_text(identity_id, field="identity_id")
        if not isinstance(basis_bytes, bytes) or not basis_bytes:
            raise CrossSampleContractError("identity basis bytes must be non-empty")
        prior = self._basis_by_id.get(identity_id)
        if prior is not None and prior != basis_bytes:
            raise CrossSampleContractError(
                "identity digest collision: equal ID has unequal basis bytes"
            )
        self._basis_by_id[identity_id] = basis_bytes


def _canonical_members(
    member_subjects: Sequence[CrossSampleGroupMemberSubject],
    *,
    processing_request_id: str,
) -> tuple[CrossSampleGroupMemberSubject, ...]:
    if not member_subjects:
        raise CrossSampleContractError("cross-sample group requires a member")
    registry = CrossSampleIdentityBasisRegistry()
    samples: set[str] = set()
    physical: set[str] = set()
    members: list[CrossSampleGroupMemberSubject] = []
    for member in member_subjects:
        if not isinstance(member, CrossSampleGroupMemberSubject):
            raise CrossSampleContractError("group member is not a typed subject")
        if member.processing_request_id != processing_request_id:
            raise CrossSampleContractError("group member crossed processing request")
        if member.canonical_sample_identity_id in samples:
            raise CrossSampleContractError(
                "group has two identity-bearing members for one canonical sample"
            )
        if physical.intersection(member.physical_observation_ids):
            raise CrossSampleContractError("group duplicates a physical observation")
        registry.register(member.member_subject_id, member.basis_bytes)
        samples.add(member.canonical_sample_identity_id)
        physical.update(member.physical_observation_ids)
        members.append(member)
    return tuple(sorted(members, key=lambda item: item.member_subject_id))


@dataclass(frozen=True, slots=True)
class CrossSampleGroupIdentity:
    group_hypothesis_id: str
    processing_request_id: str
    member_subjects: tuple[CrossSampleGroupMemberSubject, ...]
    basis_bytes: bytes
    disposition: CrossSampleGroupDisposition

    def __post_init__(self) -> None:
        request_id = validate_typed_identity_id(
            self.processing_request_id,
            prefix=PROCESSING_REQUEST_ID_PREFIX,
            field="processing_request_id",
        )
        members = _canonical_members(
            self.member_subjects,
            processing_request_id=request_id,
        )
        if members != self.member_subjects:
            raise CrossSampleContractError("group members are not canonical-sorted")
        if self.disposition not in {
            "established",
            "provisional",
            "unresolved",
            "rejected",
        }:
            raise CrossSampleContractError("group disposition is not closed")
        if len(members) == 1 and self.disposition == "established":
            raise CrossSampleContractError("singleton group cannot be established")
        basis = canonical_identity_basis_bytes(
            {
                "schema_version": "cross_sample_group",
                "processing_request_id": request_id,
                "member_subject_ids": [item.member_subject_id for item in members],
            }
        )
        if self.basis_bytes != basis:
            raise CrossSampleContractError("group identity basis bytes are not exact")
        try:
            validate_typed_identity_basis(
                self.group_hypothesis_id,
                prefix=_CROSS_SAMPLE_GROUP_PREFIX,
                basis_bytes=basis,
                field="group_hypothesis_id",
            )
        except ValueError as exc:
            raise CrossSampleContractError(str(exc)) from exc


def mint_cross_sample_group_identity(
    *,
    processing_request_id: str,
    member_subjects: Sequence[CrossSampleGroupMemberSubject],
    disposition: CrossSampleGroupDisposition | None = None,
) -> CrossSampleGroupIdentity:
    request_id = validate_typed_identity_id(
        processing_request_id,
        prefix=PROCESSING_REQUEST_ID_PREFIX,
        field="processing_request_id",
    )
    members = _canonical_members(member_subjects, processing_request_id=request_id)
    resolved_disposition = disposition or (
        "established" if len(members) >= 2 else "provisional"
    )
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_group",
            "processing_request_id": request_id,
            "member_subject_ids": [item.member_subject_id for item in members],
        }
    )
    return CrossSampleGroupIdentity(
        group_hypothesis_id=typed_identity_digest(_CROSS_SAMPLE_GROUP_PREFIX, basis),
        processing_request_id=request_id,
        member_subjects=members,
        basis_bytes=basis,
        disposition=resolved_disposition,
    )


def validate_cross_sample_group_population(
    groups: Sequence[CrossSampleGroupIdentity],
) -> tuple[CrossSampleGroupIdentity, ...]:
    canonical = tuple(groups)
    if not canonical:
        return canonical
    if len({group.processing_request_id for group in canonical}) != 1:
        raise CrossSampleContractError("population crossed processing request")
    group_ids: set[str] = set()
    members: set[str] = set()
    physical: set[str] = set()
    for group in canonical:
        if group.group_hypothesis_id in group_ids:
            raise CrossSampleContractError("population duplicates a group identity")
        for member in group.member_subjects:
            if member.member_subject_id in members:
                raise CrossSampleContractError(
                    "population duplicates a canonical member across groups"
                )
            if physical.intersection(member.physical_observation_ids):
                raise CrossSampleContractError(
                    "population duplicates a physical observation across groups"
                )
            members.add(member.member_subject_id)
            physical.update(member.physical_observation_ids)
        group_ids.add(group.group_hypothesis_id)
    return canonical


__all__ = (
    "CrossSampleContractError",
    "CrossSampleEdgeDisposition",
    "CrossSampleGroupDisposition",
    "CrossSampleGroupIdentity",
    "CrossSampleGroupMemberSubject",
    "CrossSampleIdentityBasisRegistry",
    "CrossSampleInitialEdgeContext",
    "CrossSampleNeighborhoodStatus",
    "_CROSS_SAMPLE_GROUP_PREFIX",
    "_GROUP_MEMBER_SUBJECT_PREFIX",
    "_canonical_members",
    "_request_id",
    "_required_text",
    "group_member_subject_from_peak_hypothesis",
    "mint_cross_sample_group_identity",
    "validate_cross_sample_group_population",
)
