"""Identity, membership, and repaired observation models for CrossSample."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    build_discovery_physical_selection_subject_id,
)
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryCandidatePhysicalBinding,
    validate_discovery_candidate_physical_binding,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    resolve_request_acquisition_source,
    typed_identity_digest,
    validate_typed_identity_basis,
    validate_typed_identity_id,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.validation_primitives import require_owner_normalized_text

_GROUP_MEMBER_SUBJECT_PREFIX = "xic.group-member-subject:"
_CROSS_SAMPLE_GROUP_PREFIX = "xic.cross-sample-group:"
_CROSS_SAMPLE_REPAIRED_OBSERVATION_PREFIX = "xic.cross-sample-repaired-observation:"
_CROSS_SAMPLE_REPAIRED_OBSERVATION_MINT_TOKEN = object()

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
    subject_kind: Literal["peak_hypothesis", "physical_observation"]
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
        if self.subject_kind not in {"peak_hypothesis", "physical_observation"}:
            raise CrossSampleContractError("subject_kind is not a closed variant")
        expected_prefixes = (
            ("xic.peak-hypothesis:",)
            if self.subject_kind == "peak_hypothesis"
            else (
                "xic.peak-observation-content:",
                "physical-facts:",
            )
        )
        if not subject_id.startswith(expected_prefixes):
            raise CrossSampleContractError("member subject is not owner-backed")
        physical_ids = tuple(self.physical_observation_ids)
        if not physical_ids or tuple(sorted(set(physical_ids))) != physical_ids:
            raise CrossSampleContractError(
                "physical_observation_ids must be unique and canonical-sorted"
            )
        if self.subject_kind == "physical_observation":
            if subject_id.startswith("xic.peak-observation-content:"):
                if physical_ids != (subject_id,):
                    raise CrossSampleContractError(
                        "physical observation member must bind its tagged observation"
                    )
            elif physical_ids != (subject_id,):
                raise CrossSampleContractError(
                    "physical observation member must bind its tagged observation"
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


def group_member_subject_from_peak_observation(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    observation: PeakObservationRef,
    processing_owners: ProcessingOwnerReferences | None = None,
    processing_request_id: str | None = None,
) -> CrossSampleGroupMemberSubject:
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CrossSampleContractError("sample must be canonically resolved")
    if not isinstance(observation, PeakObservationRef):
        raise CrossSampleContractError("observation must be owner-backed")
    request_id = _request_id(
        processing_owners=processing_owners,
        processing_request_id=processing_request_id,
    )
    sample_id = sample.identity.sample_identity_id
    if (
        observation.artifact.producer_processing_request_id != request_id
        or observation.content.canonical_sample_identity_id != sample_id
    ):
        raise CrossSampleContractError("physical observation crossed owner binding")
    subject_id = observation.content.peak_observation_content_id
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject",
            "processing_request_id": request_id,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": "physical_observation", "id": subject_id},
            "physical_observation_ids": [subject_id],
        }
    )
    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(_GROUP_MEMBER_SUBJECT_PREFIX, basis),
        processing_request_id=request_id,
        canonical_sample_identity_id=sample_id,
        subject_kind="physical_observation",
        subject_id=subject_id,
        physical_observation_ids=(subject_id,),
        basis_bytes=basis,
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


def group_member_subject_from_discovery_physical_selection(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    candidate: DiscoveryCandidate,
    observation: PeakObservationRef | None = None,
    physical_binding: DiscoveryCandidatePhysicalBinding | None = None,
) -> CrossSampleGroupMemberSubject:
    """Project an owner-closed Discovery selection into CrossSample."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise CrossSampleContractError("processing_owners must be owner-backed")
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CrossSampleContractError("sample must be canonically resolved")
    if not isinstance(candidate, DiscoveryCandidate):
        raise CrossSampleContractError("candidate must be Discovery-owned")
    if candidate.sample_stem != sample.lookup_token:
        raise CrossSampleContractError("Discovery candidate crossed sample")
    selection_id = build_discovery_physical_selection_subject_id(
        sample_stem=candidate.sample_stem,
        best_ms2_scan_id=candidate.best_ms2_scan_id,
        precursor_mz=candidate.precursor_mz,
        product_mz=candidate.product_mz,
    )
    if physical_binding is not None:
        if observation is not None:
            raise CrossSampleContractError(
                "Discovery binding must not remint an observation"
            )
        try:
            validate_discovery_candidate_physical_binding(physical_binding)
        except ValueError as exc:
            raise CrossSampleContractError(str(exc)) from exc
        source = resolve_request_acquisition_source(
            processing_owners=processing_owners,
            sample=sample,
        )
        if (
            physical_binding.candidate != candidate
            or physical_binding.selection_subject_id != selection_id
            or physical_binding.acquisition_source.acquisition_source_id
            != source.acquisition_source_id
        ):
            raise CrossSampleContractError("Discovery binding crossed owner facts")
        facts = physical_binding.physical_evaluation.facts
        if facts is None:
            raise CrossSampleContractError("Discovery binding lost physical facts")
        physical_id = facts.fact_content_id
    else:
        if observation is None:
            raise CrossSampleContractError(
                "Discovery selection requires an observation or physical binding"
            )
        if (
            observation.artifact.producer_processing_request_id
            != processing_owners.request.processing_request_id
            or observation.content.canonical_sample_identity_id
            != sample.identity.sample_identity_id
        ):
            raise CrossSampleContractError("Discovery observation crossed owner facts")
        physical_id = observation.content.peak_observation_content_id
    request_id = processing_owners.request.processing_request_id
    sample_id = sample.identity.sample_identity_id
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject",
            "processing_request_id": request_id,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": "physical_observation", "id": physical_id},
            "physical_observation_ids": [physical_id],
        }
    )
    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(_GROUP_MEMBER_SUBJECT_PREFIX, basis),
        processing_request_id=request_id,
        canonical_sample_identity_id=sample_id,
        subject_kind="physical_observation",
        subject_id=physical_id,
        physical_observation_ids=(physical_id,),
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


@dataclass(frozen=True, slots=True)
class CrossSampleNeighborhoodBudget:
    max_members: int
    max_eligible_edges: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_members, bool)
            or not isinstance(self.max_members, int)
            or self.max_members < 1
            or isinstance(self.max_eligible_edges, bool)
            or not isinstance(self.max_eligible_edges, int)
            or self.max_eligible_edges < 0
        ):
            raise CrossSampleContractError("neighborhood budget is invalid")


@dataclass(frozen=True, slots=True)
class CrossSampleRepairedObservation:
    """CrossSample projection of repaired physical evidence without identity support."""

    observation_id: str
    repair_receipt_id: str
    authorization_id: str
    candidate_id: str
    sample_stem: str
    acquisition_source_id: str
    precursor_mz: float
    rt_left_min: float
    rt_apex_min: float
    rt_right_min: float
    height: float
    scan_support_count: int
    area_counts_seconds: float
    member_subject: CrossSampleGroupMemberSubject
    basis_bytes: bytes
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _CROSS_SAMPLE_REPAIRED_OBSERVATION_MINT_TOKEN:
            raise CrossSampleContractError(
                "repaired observation must be minted by CrossSample"
            )
        for text_field_name, text_value in (
            ("repair_receipt_id", self.repair_receipt_id),
            ("authorization_id", self.authorization_id),
            ("candidate_id", self.candidate_id),
            ("sample_stem", self.sample_stem),
            ("acquisition_source_id", self.acquisition_source_id),
        ):
            _required_text(text_value, field=text_field_name)
        if type(self.member_subject) is not CrossSampleGroupMemberSubject:
            raise CrossSampleContractError(
                "repaired observation requires an exact CrossSample member"
            )
        if (
            self.member_subject.subject_kind != "physical_observation"
            or self.member_subject.subject_id
            not in self.member_subject.physical_observation_ids
        ):
            raise CrossSampleContractError(
                "repaired observation member is not physical evidence"
            )
        for numeric_field_name, numeric_value in (
            ("precursor_mz", self.precursor_mz),
            ("rt_left_min", self.rt_left_min),
            ("rt_apex_min", self.rt_apex_min),
            ("rt_right_min", self.rt_right_min),
            ("height", self.height),
            ("area_counts_seconds", self.area_counts_seconds),
        ):
            if not isinstance(numeric_value, float) or not math.isfinite(numeric_value):
                raise CrossSampleContractError(
                    f"{numeric_field_name} must be a finite float"
                )
        if not self.rt_left_min < self.rt_apex_min < self.rt_right_min:
            raise CrossSampleContractError(
                "repaired observation apex must lie inside its physical bounds"
            )
        if (
            self.precursor_mz <= 0.0
            or self.height < 0.0
            or self.area_counts_seconds < 0.0
        ):
            raise CrossSampleContractError(
                "repaired observation physical values are invalid"
            )
        if (
            isinstance(self.scan_support_count, bool)
            or not isinstance(self.scan_support_count, int)
            or self.scan_support_count < 1
        ):
            raise CrossSampleContractError(
                "repaired observation scan support must be positive"
            )
        basis = _repaired_observation_basis(
            repair_receipt_id=self.repair_receipt_id,
            authorization_id=self.authorization_id,
            candidate_id=self.candidate_id,
            sample_stem=self.sample_stem,
            acquisition_source_id=self.acquisition_source_id,
            precursor_mz=self.precursor_mz,
            rt_left_min=self.rt_left_min,
            rt_apex_min=self.rt_apex_min,
            rt_right_min=self.rt_right_min,
            height=self.height,
            scan_support_count=self.scan_support_count,
            area_counts_seconds=self.area_counts_seconds,
            member_subject=self.member_subject,
        )
        if self.basis_bytes != basis:
            raise CrossSampleContractError(
                "repaired observation basis bytes are not exact"
            )
        try:
            validate_typed_identity_basis(
                self.observation_id,
                prefix=_CROSS_SAMPLE_REPAIRED_OBSERVATION_PREFIX,
                basis_bytes=basis,
                field="repaired observation_id",
            )
        except ValueError as exc:
            raise CrossSampleContractError(str(exc)) from exc

    @property
    def owner_id(self) -> str:
        return self.observation_id

    @property
    def owner_apex_rt(self) -> float:
        return self.rt_apex_min

    @property
    def cross_sample_member_subject(self) -> CrossSampleGroupMemberSubject:
        return self.member_subject

    @property
    def identity_evidence_authority(self) -> Literal["none"]:
        return "none"


def _repaired_observation_basis(
    *,
    repair_receipt_id: str,
    authorization_id: str,
    candidate_id: str,
    sample_stem: str,
    acquisition_source_id: str,
    precursor_mz: float,
    rt_left_min: float,
    rt_apex_min: float,
    rt_right_min: float,
    height: float,
    scan_support_count: int,
    area_counts_seconds: float,
    member_subject: CrossSampleGroupMemberSubject,
) -> bytes:
    return canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_repaired_observation",
            "repair_receipt_id": repair_receipt_id,
            "authorization_id": authorization_id,
            "candidate_id": candidate_id,
            "sample_stem": sample_stem,
            "acquisition_source_id": acquisition_source_id,
            "precursor_mz": precursor_mz,
            "rt_left_min": rt_left_min,
            "rt_apex_min": rt_apex_min,
            "rt_right_min": rt_right_min,
            "height": height,
            "scan_support_count": scan_support_count,
            "area_counts_seconds": area_counts_seconds,
            "member_subject_id": member_subject.member_subject_id,
            "physical_observation_ids": list(member_subject.physical_observation_ids),
        }
    )


def _mint_cross_sample_repaired_observation(
    *,
    repair_receipt_id: str,
    authorization_id: str,
    candidate_id: str,
    sample_stem: str,
    acquisition_source_id: str,
    precursor_mz: float,
    rt_left_min: float,
    rt_apex_min: float,
    rt_right_min: float,
    height: float,
    scan_support_count: int,
    area_counts_seconds: float,
    member_subject: CrossSampleGroupMemberSubject,
) -> CrossSampleRepairedObservation:
    basis = _repaired_observation_basis(
        repair_receipt_id=repair_receipt_id,
        authorization_id=authorization_id,
        candidate_id=candidate_id,
        sample_stem=sample_stem,
        acquisition_source_id=acquisition_source_id,
        precursor_mz=precursor_mz,
        rt_left_min=rt_left_min,
        rt_apex_min=rt_apex_min,
        rt_right_min=rt_right_min,
        height=height,
        scan_support_count=scan_support_count,
        area_counts_seconds=area_counts_seconds,
        member_subject=member_subject,
    )
    return CrossSampleRepairedObservation(
        observation_id=typed_identity_digest(
            _CROSS_SAMPLE_REPAIRED_OBSERVATION_PREFIX,
            basis,
        ),
        repair_receipt_id=repair_receipt_id,
        authorization_id=authorization_id,
        candidate_id=candidate_id,
        sample_stem=sample_stem,
        acquisition_source_id=acquisition_source_id,
        precursor_mz=precursor_mz,
        rt_left_min=rt_left_min,
        rt_apex_min=rt_apex_min,
        rt_right_min=rt_right_min,
        height=height,
        scan_support_count=scan_support_count,
        area_counts_seconds=area_counts_seconds,
        member_subject=member_subject,
        basis_bytes=basis,
        _mint_token=_CROSS_SAMPLE_REPAIRED_OBSERVATION_MINT_TOKEN,
    )


__all__ = (
    "CrossSampleContractError",
    "CrossSampleEdgeDisposition",
    "CrossSampleGroupDisposition",
    "CrossSampleGroupIdentity",
    "CrossSampleGroupMemberSubject",
    "CrossSampleIdentityBasisRegistry",
    "CrossSampleInitialEdgeContext",
    "CrossSampleNeighborhoodBudget",
    "CrossSampleNeighborhoodStatus",
    "CrossSampleRepairedObservation",
    "_CROSS_SAMPLE_GROUP_PREFIX",
    "_CROSS_SAMPLE_REPAIRED_OBSERVATION_MINT_TOKEN",
    "_CROSS_SAMPLE_REPAIRED_OBSERVATION_PREFIX",
    "_GROUP_MEMBER_SUBJECT_PREFIX",
    "_canonical_members",
    "_mint_cross_sample_repaired_observation",
    "_repaired_observation_basis",
    "_request_id",
    "_required_text",
    "group_member_subject_from_discovery_physical_selection",
    "group_member_subject_from_peak_hypothesis",
    "group_member_subject_from_peak_observation",
    "mint_cross_sample_group_identity",
    "validate_cross_sample_group_population",
)
