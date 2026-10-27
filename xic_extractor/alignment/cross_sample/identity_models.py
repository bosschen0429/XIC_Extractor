"""Identity, membership, and repaired observation models for CrossSample."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    typed_identity_digest,
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
    sample_key: str
    subject_kind: Literal["peak_hypothesis"]
    subject_id: str
    physical_observation_ids: tuple[str, ...]


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
    sample_id = sample.identity.sample_key
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
            observation.content.sample_key != sample_id
            for observation in hypothesis.peak_observation_refs
        )
    ):
        raise CrossSampleContractError("peak hypothesis crossed physical observation")
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject",
            "processing_request_id": request_id,
            "sample_key": sample_id,
            "subject": {"kind": "peak_hypothesis", "id": hypothesis.hypothesis_id},
            "physical_observation_ids": list(physical_ids),
        }
    )
    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(_GROUP_MEMBER_SUBJECT_PREFIX, basis),
        processing_request_id=request_id,
        sample_key=sample_id,
        subject_kind="peak_hypothesis",
        subject_id=hypothesis.hypothesis_id,
        physical_observation_ids=physical_ids,
    )


def _canonical_members(
    member_subjects: Sequence[CrossSampleGroupMemberSubject],
    *,
    processing_request_id: str,
) -> tuple[CrossSampleGroupMemberSubject, ...]:
    if not member_subjects:
        raise CrossSampleContractError("cross-sample group requires a member")
    samples: set[str] = set()
    physical: set[str] = set()
    members: list[CrossSampleGroupMemberSubject] = []
    for member in member_subjects:
        if not isinstance(member, CrossSampleGroupMemberSubject):
            raise CrossSampleContractError("group member is not a typed subject")
        if member.processing_request_id != processing_request_id:
            raise CrossSampleContractError("group member crossed processing request")
        if member.sample_key in samples:
            raise CrossSampleContractError(
                "group has two identity-bearing members for one canonical sample"
            )
        if physical.intersection(member.physical_observation_ids):
            raise CrossSampleContractError("group duplicates a physical observation")
        samples.add(member.sample_key)
        physical.update(member.physical_observation_ids)
        members.append(member)
    return tuple(sorted(members, key=lambda item: item.member_subject_id))


@dataclass(frozen=True, slots=True)
class CrossSampleGroupIdentity:
    group_hypothesis_id: str
    processing_request_id: str
    member_subjects: tuple[CrossSampleGroupMemberSubject, ...]
    disposition: CrossSampleGroupDisposition


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
        disposition=resolved_disposition,
    )


__all__ = (
    "CrossSampleContractError",
    "CrossSampleEdgeDisposition",
    "CrossSampleGroupDisposition",
    "CrossSampleGroupIdentity",
    "CrossSampleGroupMemberSubject",
    "CrossSampleInitialEdgeContext",
    "CrossSampleNeighborhoodStatus",
    "_CROSS_SAMPLE_GROUP_PREFIX",
    "_GROUP_MEMBER_SUBJECT_PREFIX",
    "_canonical_members",
    "_request_id",
    "_required_text",
    "group_member_subject_from_peak_hypothesis",
    "mint_cross_sample_group_identity",
)
