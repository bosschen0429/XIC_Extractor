"""Cross-sample peak group construction.

This module groups SampleLocalMS1Owner instances into cross-sample chemical-
entity groups using m/z, RT, product m/z, observed neutral loss, and edge
evidence.  The output ``CrossSamplePeakGroupHypothesis`` is the primary
cross-sample identity unit.

``public_family_id`` is a *display label* preserved for output compatibility.
It is NOT an identity anchor; use ``group_hypothesis_id`` for identity
decisions within the pipeline.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import TargetedIstdIdentityAnchor
from xic_extractor.alignment.edge_scoring import (
    DriftLookupProtocol,
    EdgeDecision,
    OwnerEdgeEvidence,
    evaluate_owner_edge,
)
from xic_extractor.alignment.ownership_models import (
    SampleLocalMS1Owner,
)
from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    build_discovery_physical_selection_subject_id,
)
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryCandidatePhysicalBinding,
    validate_discovery_candidate_physical_binding,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    validate_peak_observation_binding,
)
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

if TYPE_CHECKING:
    from xic_extractor.configuration.models import Target

CrossSamplePeakGroupConstructionRole: TypeAlias = Literal[
    "owner_aligned_feature_shadow",
    "successor_constructor",
]

_PRECURSOR_BUCKET_WIDTH_DA = 1.0
_DUPLICATE_OWNER_GROUP_POLICY = "owner_duplicate_group_resolved"
_BOUNDED_CONTINUITY_RT_SPAN_SEC = 300.0
_GROUP_MEMBER_SUBJECT_PREFIX = "xic.group-member-subject.v1:"
_CROSS_SAMPLE_GROUP_PREFIX = "xic.cross-sample-group.v1:"
_DISCOVERY_PHYSICAL_SELECTION_SUBJECT_PREFIX = "discovery-physical-work.v1:"
_PRIMARY_DISPOSITION_PRODUCT_PRECURSOR_SHIFT_TOLERANCE_DA = 0.02

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
CrossSampleEdgeContext: TypeAlias = Literal[
    "initial_grouping",
    "claim_candidate",
    "repair_derived_observation",
]
CrossSampleNeighborhoodStatus: TypeAlias = Literal["assessable", "unassessable"]


class CrossSampleContractError(ValueError):
    """Raised when CrossSample identity, grouping, or lineage is not closed."""


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CrossSampleContractError(f"{field} must be non-empty text")
    return value


@dataclass(frozen=True, slots=True)
class CrossSampleGroupMemberSubject:
    """One owner-backed member in the canonical CrossSample identity domain."""

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
            raise CrossSampleContractError(
                "subject_kind is not a closed member variant"
            )
        if self.subject_kind == "peak_hypothesis" and not subject_id.startswith(
            "xic.peak-hypothesis.v1:"
        ):
            raise CrossSampleContractError(
                "peak_hypothesis member must use an owner-backed hypothesis ID"
            )
        if self.subject_kind == "physical_observation" and not subject_id.startswith(
            (
                "xic.peak-observation-content.v1:",
                _DISCOVERY_PHYSICAL_SELECTION_SUBJECT_PREFIX,
            )
        ):
            raise CrossSampleContractError(
                "physical_observation member must use an owner-backed physical ID"
            )
        physical_ids = tuple(
            _required_text(value, field="physical_observation_id")
            for value in self.physical_observation_ids
        )
        if not physical_ids or tuple(sorted(set(physical_ids))) != physical_ids:
            raise CrossSampleContractError(
                "physical_observation_ids must be unique and canonical-sorted"
            )
        if self.subject_kind == "physical_observation":
            if subject_id.startswith("xic.peak-observation-content.v1:"):
                if physical_ids != (subject_id,):
                    raise CrossSampleContractError(
                        "physical_observation member must bind its tagged observation"
                    )
            elif len(physical_ids) != 1 or not physical_ids[0].startswith(
                (
                    "xic.peak-observation-content.v1:",
                    "physical-facts.v1:",
                )
            ):
                raise CrossSampleContractError(
                    "Discovery physical member must retain one exact audit fact"
                )
        basis = {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": request_id,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": self.subject_kind, "id": subject_id},
        }
        expected_basis = canonical_identity_basis_bytes(basis)
        if self.basis_bytes != expected_basis:
            raise CrossSampleContractError("member subject basis bytes are not exact")
        try:
            validate_typed_identity_basis(
                self.member_subject_id,
                prefix=_GROUP_MEMBER_SUBJECT_PREFIX,
                basis_bytes=self.basis_bytes,
                field="member_subject_id",
            )
        except ValueError as exc:
            raise CrossSampleContractError(str(exc)) from exc
        object.__setattr__(self, "processing_request_id", request_id)

    @property
    def basis_mapping(self) -> Mapping[str, object]:
        """Reproduce the closed canonical member basis without parsing callers."""

        return {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": self.processing_request_id,
            "canonical_sample_identity_id": self.canonical_sample_identity_id,
            "subject": {"kind": self.subject_kind, "id": self.subject_id},
        }


def group_member_subject_from_peak_observation(
    *,
    processing_owners: ProcessingOwnerReferences | None = None,
    processing_request_id: str | None = None,
    sample: ResolvedCanonicalSampleIdentity,
    observation: PeakObservationRef,
) -> CrossSampleGroupMemberSubject:
    """Project an existing owner-backed physical observation into CrossSample."""

    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CrossSampleContractError("sample must be canonically resolved")
    if not isinstance(observation, PeakObservationRef):
        raise CrossSampleContractError("observation must be owner-backed")
    request_id = _owner_backed_request_id(
        processing_owners=processing_owners,
        processing_request_id=processing_request_id,
    )
    if observation.artifact.producer_processing_request_id != request_id:
        raise CrossSampleContractError(
            "physical observation crossed processing request"
        )
    sample_id = sample.identity.sample_identity_id
    if observation.content.canonical_sample_identity_id != sample_id:
        raise CrossSampleContractError("physical observation crossed canonical sample")
    subject_id = observation.content.peak_observation_content_id
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": request_id,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": "physical_observation", "id": subject_id},
        }
    )
    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(
            _GROUP_MEMBER_SUBJECT_PREFIX,
            basis,
        ),
        processing_request_id=request_id,
        canonical_sample_identity_id=sample_id,
        subject_kind="physical_observation",
        subject_id=subject_id,
        physical_observation_ids=(subject_id,),
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
    """Project one closed Discovery selection subject into CrossSample.

    Discovery owns the stable physical-selection identity.  The owner-closed
    receipt/raw wrapper validates an exact ``PeakObservationRef``; the
    in-memory binding wrapper validates Discovery's existing physical record
    directly and deliberately does not remint an observation.  Both wrappers
    share the same canonical membership basis.
    """

    if not isinstance(candidate, DiscoveryCandidate):
        raise CrossSampleContractError("Discovery physical member needs a candidate")
    if candidate.sample_stem != sample.lookup_token:
        raise CrossSampleContractError("Discovery candidate crossed canonical sample")
    selection_subject_id = build_discovery_physical_selection_subject_id(
        sample_stem=candidate.sample_stem,
        best_ms2_scan_id=candidate.best_ms2_scan_id,
        precursor_mz=candidate.precursor_mz,
        product_mz=candidate.product_mz,
    )
    audit_physical_id: str
    if physical_binding is not None:
        if observation is not None:
            raise CrossSampleContractError(
                "Discovery physical binding must not remint an observation"
            )
        try:
            validate_discovery_candidate_physical_binding(physical_binding)
        except ValueError as exc:
            raise CrossSampleContractError(str(exc)) from exc
        if (
            physical_binding.candidate != candidate
            or physical_binding.selection_subject_id != selection_subject_id
        ):
            raise CrossSampleContractError(
                "Discovery physical binding crossed candidate selection identity"
            )
        request_source = resolve_request_acquisition_source(
            processing_owners=processing_owners,
            sample=sample,
        )
        if (
            physical_binding.acquisition_source.acquisition_source_id
            != request_source.acquisition_source_id
        ):
            raise CrossSampleContractError(
                "Discovery physical binding crossed request acquisition"
            )
        facts = physical_binding.physical_evaluation.facts
        if facts is None:
            raise CrossSampleContractError(
                "available Discovery physical binding lost its exact audit facts"
            )
        audit_physical_id = facts.fact_content_id
    else:
        if observation is None:
            raise CrossSampleContractError(
                "Discovery receipt member requires an exact observation"
            )
        try:
            validate_peak_observation_binding(
                observation,
                processing_owners=processing_owners,
                sample=sample,
            )
        except ValueError as exc:
            raise CrossSampleContractError(str(exc)) from exc
        audit_physical_id = observation.content.peak_observation_content_id
    subject_id = selection_subject_id
    if not subject_id.startswith(_DISCOVERY_PHYSICAL_SELECTION_SUBJECT_PREFIX):
        raise CrossSampleContractError("Discovery selection subject is malformed")
    sample_id = sample.identity.sample_identity_id
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": processing_owners.request.processing_request_id,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": "physical_observation", "id": subject_id},
        }
    )
    return _mint_discovery_physical_member_subject(
        processing_request_id=processing_owners.request.processing_request_id,
        canonical_sample_identity_id=sample_id,
        selection_subject_id=subject_id,
        audit_physical_id=audit_physical_id,
        basis_bytes=basis,
    )


def _mint_discovery_physical_member_subject(
    *,
    processing_request_id: str,
    canonical_sample_identity_id: str,
    selection_subject_id: str,
    audit_physical_id: str,
    basis_bytes: bytes,
) -> CrossSampleGroupMemberSubject:
    """Mint the one shared basis after either Discovery wrapper is closed."""

    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(
            _GROUP_MEMBER_SUBJECT_PREFIX,
            basis_bytes,
        ),
        processing_request_id=processing_request_id,
        canonical_sample_identity_id=canonical_sample_identity_id,
        subject_kind="physical_observation",
        subject_id=selection_subject_id,
        physical_observation_ids=(audit_physical_id,),
        basis_bytes=basis_bytes,
    )


def group_member_subject_from_peak_hypothesis(
    *,
    processing_owners: ProcessingOwnerReferences | None = None,
    processing_request_id: str | None = None,
    sample: ResolvedCanonicalSampleIdentity,
    hypothesis: PeakHypothesis,
) -> CrossSampleGroupMemberSubject:
    """Project a real owner-backed PeakHypothesis into CrossSample exactly."""

    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CrossSampleContractError("sample must be canonically resolved")
    if not isinstance(hypothesis, PeakHypothesis) or not hypothesis.is_owner_backed:
        raise CrossSampleContractError("hypothesis must be owner-backed")
    if not hypothesis.hypothesis_id.startswith("xic.peak-hypothesis.v1:"):
        raise CrossSampleContractError("hypothesis must use an owner-backed identity")
    request_id = _owner_backed_request_id(
        processing_owners=processing_owners,
        processing_request_id=processing_request_id,
    )
    if (
        hypothesis.artifact is None
        or hypothesis.artifact.producer_processing_request_id != request_id
    ):
        raise CrossSampleContractError("peak hypothesis crossed processing request")
    if not hypothesis.peak_observation_refs:
        raise CrossSampleContractError("peak hypothesis has no physical observation")
    sample_id = sample.identity.sample_identity_id
    physical_ids = tuple(
        sorted(
            observation.content.peak_observation_content_id
            for observation in hypothesis.peak_observation_refs
        )
    )
    if len(set(physical_ids)) != len(physical_ids) or any(
        observation.content.canonical_sample_identity_id != sample_id
        or observation.artifact.producer_processing_request_id != request_id
        for observation in hypothesis.peak_observation_refs
    ):
        raise CrossSampleContractError("peak hypothesis crossed physical observation")
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": request_id,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": "peak_hypothesis", "id": hypothesis.hypothesis_id},
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


def _owner_backed_request_id(
    *,
    processing_owners: ProcessingOwnerReferences | None,
    processing_request_id: str | None,
) -> str:
    if processing_owners is not None:
        if not isinstance(processing_owners, ProcessingOwnerReferences):
            raise CrossSampleContractError("processing_owners must be owner-backed")
        owner_request_id = processing_owners.request.processing_request_id
        if (
            processing_request_id is not None
            and processing_request_id != owner_request_id
        ):
            raise CrossSampleContractError(
                "processing owner references crossed request"
            )
        return owner_request_id
    if processing_request_id is None:
        raise CrossSampleContractError("exact processing request identity is required")
    return validate_typed_identity_id(
        processing_request_id,
        prefix=PROCESSING_REQUEST_ID_PREFIX,
        field="processing_request_id",
    )


@dataclass(slots=True)
class CrossSampleIdentityBasisRegistry:
    """Local collision detector for retained canonical identity basis bytes."""

    _basis_by_identity_id: dict[str, bytes] = field(default_factory=dict)

    def register(self, identity_id: str, basis_bytes: bytes) -> None:
        _required_text(identity_id, field="identity_id")
        if not isinstance(basis_bytes, bytes) or not basis_bytes:
            raise CrossSampleContractError("identity basis bytes must be non-empty")
        prior = self._basis_by_identity_id.get(identity_id)
        if prior is not None and prior != basis_bytes:
            raise CrossSampleContractError(
                "identity digest collision: equal ID has unequal basis bytes"
            )
        self._basis_by_identity_id[identity_id] = basis_bytes


@dataclass(slots=True)
class CrossSamplePopulationRegistry:
    """Request-scoped rejection registry for a complete CrossSample population.

    A group validates its own samples and observations.  This registry owns the
    complementary invariant: a canonical member (or its physical evidence)
    cannot silently occur in more than one group in the same returned or
    delivered population.
    """

    _member_bases: CrossSampleIdentityBasisRegistry = field(
        default_factory=CrossSampleIdentityBasisRegistry
    )
    _member_groups: dict[str, str] = field(default_factory=dict)
    _physical_members: dict[str, str] = field(default_factory=dict)
    _groups: set[str] = field(default_factory=set)

    def register_group(self, group: "CrossSampleGroupIdentity") -> None:
        if not isinstance(group, CrossSampleGroupIdentity):
            raise CrossSampleContractError("population group must be canonical")
        if group.group_hypothesis_id in self._groups:
            raise CrossSampleContractError("population duplicates a group identity")
        for member in group.member_subjects:
            self._member_bases.register(member.member_subject_id, member.basis_bytes)
            prior_group = self._member_groups.get(member.member_subject_id)
            if prior_group is not None:
                raise CrossSampleContractError(
                    "population duplicates a canonical member across groups"
                )
            self._member_groups[member.member_subject_id] = group.group_hypothesis_id
            for physical_id in member.physical_observation_ids:
                prior = self._physical_members.get(physical_id)
                if prior is not None:
                    raise CrossSampleContractError(
                        "population duplicates a physical observation across groups"
                    )
                self._physical_members[physical_id] = member.member_subject_id
        self._groups.add(group.group_hypothesis_id)


def validate_cross_sample_group_population(
    groups: Sequence["CrossSampleGroupIdentity"],
) -> tuple["CrossSampleGroupIdentity", ...]:
    """Validate one exact request-scoped set before it crosses a boundary."""

    canonical = tuple(groups)
    if not canonical:
        return canonical
    request_ids = {group.processing_request_id for group in canonical}
    if len(request_ids) != 1:
        raise CrossSampleContractError("population crossed processing request")
    registry = CrossSamplePopulationRegistry()
    for group in sorted(canonical, key=lambda item: item.group_hypothesis_id):
        registry.register_group(group)
    return canonical


def _canonical_member_subjects(
    member_subjects: Sequence[CrossSampleGroupMemberSubject],
    *,
    processing_request_id: str,
) -> tuple[CrossSampleGroupMemberSubject, ...]:
    if not member_subjects:
        raise CrossSampleContractError(
            "cross-sample group requires at least one member"
        )
    registry = CrossSampleIdentityBasisRegistry()
    samples: set[str] = set()
    physical_observations: set[str] = set()
    canonical: list[CrossSampleGroupMemberSubject] = []
    for member in member_subjects:
        if not isinstance(member, CrossSampleGroupMemberSubject):
            raise CrossSampleContractError("group member is not a typed subject")
        if member.processing_request_id != processing_request_id:
            raise CrossSampleContractError("group member crossed processing request")
        if member.canonical_sample_identity_id in samples:
            raise CrossSampleContractError(
                "group has two identity-bearing members for one canonical sample"
            )
        if physical_observations.intersection(member.physical_observation_ids):
            raise CrossSampleContractError("group duplicates a physical observation")
        samples.add(member.canonical_sample_identity_id)
        physical_observations.update(member.physical_observation_ids)
        registry.register(member.member_subject_id, member.basis_bytes)
        canonical.append(member)
    ordered = tuple(sorted(canonical, key=lambda member: member.basis_bytes))
    if len({member.member_subject_id for member in ordered}) != len(ordered):
        raise CrossSampleContractError("group duplicates a member subject")
    return ordered


@dataclass(frozen=True, slots=True)
class CrossSampleGroupIdentity:
    """Canonical group identity and immutable membership basis."""

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
        members = _canonical_member_subjects(
            self.member_subjects,
            processing_request_id=request_id,
        )
        expected_basis = canonical_identity_basis_bytes(
            {
                "schema_version": "cross_sample_group_v1",
                "processing_request_id": request_id,
                "member_subjects": [member.basis_mapping for member in members],
            }
        )
        if self.basis_bytes != expected_basis:
            raise CrossSampleContractError(
                "cross-sample group basis bytes are not exact"
            )
        try:
            validate_typed_identity_basis(
                self.group_hypothesis_id,
                prefix=_CROSS_SAMPLE_GROUP_PREFIX,
                basis_bytes=self.basis_bytes,
                field="group_hypothesis_id",
            )
        except ValueError as exc:
            raise CrossSampleContractError(str(exc)) from exc
        expected_disposition: CrossSampleGroupDisposition = (
            "provisional" if len(members) == 1 else "established"
        )
        if self.disposition not in {
            "established",
            "provisional",
            "unresolved",
            "rejected",
        }:
            raise CrossSampleContractError("group disposition is not closed")
        if self.disposition == "provisional" and len(members) != 1:
            raise CrossSampleContractError("only singleton group may be provisional")
        if self.disposition == "established" and expected_disposition == "provisional":
            raise CrossSampleContractError("singleton group must remain provisional")
        object.__setattr__(self, "processing_request_id", request_id)
        object.__setattr__(self, "member_subjects", members)


def mint_cross_sample_group_identity(
    *,
    processing_request_id: str,
    member_subjects: Sequence[CrossSampleGroupMemberSubject],
    disposition: CrossSampleGroupDisposition | None = None,
) -> CrossSampleGroupIdentity:
    """Mint the one canonical group identity from exact request and members."""

    request_id = validate_typed_identity_id(
        processing_request_id,
        prefix=PROCESSING_REQUEST_ID_PREFIX,
        field="processing_request_id",
    )
    members = _canonical_member_subjects(
        member_subjects,
        processing_request_id=request_id,
    )
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_group_v1",
            "processing_request_id": request_id,
            "member_subjects": [member.basis_mapping for member in members],
        }
    )
    return CrossSampleGroupIdentity(
        group_hypothesis_id=typed_identity_digest(_CROSS_SAMPLE_GROUP_PREFIX, basis),
        processing_request_id=request_id,
        member_subjects=members,
        basis_bytes=basis,
        disposition=(
            disposition
            if disposition is not None
            else ("provisional" if len(members) == 1 else "established")
        ),
    )


@dataclass(frozen=True, slots=True)
class CrossSampleNeighborhoodBudget:
    """Caller-validated bound for one coupled successor neighborhood."""

    max_members: int
    max_eligible_edges: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_members, bool)
            or not isinstance(self.max_members, int)
            or self.max_members < 1
        ):
            raise CrossSampleContractError("max_members must be a positive integer")
        if (
            isinstance(self.max_eligible_edges, bool)
            or not isinstance(self.max_eligible_edges, int)
            or self.max_eligible_edges < 0
        ):
            raise CrossSampleContractError(
                "max_eligible_edges must be a non-negative integer"
            )


@dataclass(frozen=True, slots=True)
class CrossSampleNeighborhoodEdge:
    left_member_subject_id: str
    right_member_subject_id: str
    disposition: CrossSampleEdgeDisposition

    def __post_init__(self) -> None:
        left = _required_text(
            self.left_member_subject_id, field="left_member_subject_id"
        )
        right = _required_text(
            self.right_member_subject_id, field="right_member_subject_id"
        )
        if left == right:
            raise CrossSampleContractError("neighborhood edge cannot self-connect")
        if self.disposition not in {"eligible", "review_only", "excluded"}:
            raise CrossSampleContractError(
                "neighborhood edge disposition is not closed"
            )


@dataclass(frozen=True, slots=True)
class CrossSampleConnectedNeighborhood:
    """Self-contained, deterministic eligibility closure for one mutation."""

    status: CrossSampleNeighborhoodStatus
    member_subject_ids: tuple[str, ...]
    eligible_edges: tuple[tuple[str, str], ...]
    connected_components: tuple[tuple[str, ...], ...]
    reason: str = ""

    def __post_init__(self) -> None:
        member_ids = tuple(
            _required_text(member_id, field="neighborhood member_subject_id")
            for member_id in self.member_subject_ids
        )
        if tuple(sorted(set(member_ids))) != member_ids:
            raise CrossSampleContractError(
                "neighborhood member IDs must be canonical-sorted and unique"
            )
        normalized_edges: list[tuple[str, str]] = []
        for edge in self.eligible_edges:
            if not isinstance(edge, tuple) or len(edge) != 2:
                raise CrossSampleContractError(
                    "neighborhood eligible edge is malformed"
                )
            left, right = (
                _required_text(item, field="eligible edge member") for item in edge
            )
            if left == right or left not in member_ids or right not in member_ids:
                raise CrossSampleContractError("neighborhood eligible edge is invalid")
            normalized_edges.append(_canonical_neighborhood_edge_pair(left, right))
        if tuple(sorted(set(normalized_edges))) != tuple(normalized_edges):
            raise CrossSampleContractError(
                "neighborhood eligible edges must be canonical-sorted and unique"
            )
        components = tuple(tuple(component) for component in self.connected_components)
        if self.status not in {"assessable", "unassessable"}:
            raise CrossSampleContractError("neighborhood status is not closed")
        if self.status == "assessable":
            expected_components = _connected_components_from_eligible_edges(
                member_ids,
                tuple(normalized_edges),
            )
            if components != expected_components:
                raise CrossSampleContractError(
                    "assessable neighborhood components are not exact eligible closure"
                )
        elif components:
            raise CrossSampleContractError(
                "unassessable neighborhood cannot expose partial components"
            )
        object.__setattr__(self, "member_subject_ids", member_ids)
        object.__setattr__(self, "eligible_edges", tuple(normalized_edges))
        object.__setattr__(self, "connected_components", components)


def classify_cross_sample_edge(
    decision: EdgeDecision,
    *,
    context: CrossSampleEdgeContext,
    initial_full_batch_continuity: bool = False,
) -> CrossSampleEdgeDisposition:
    """Classify every CrossSample edge through one closed policy boundary."""

    if context not in {
        "initial_grouping",
        "claim_candidate",
        "repair_derived_observation",
    }:
        raise CrossSampleContractError("edge context is not closed")
    if decision == "blocked_edge":
        return "excluded"
    if decision == "strong_edge":
        return "eligible"
    if decision == "weak_edge":
        return (
            "eligible"
            if context == "initial_grouping" and initial_full_batch_continuity
            else "review_only"
        )
    raise CrossSampleContractError("edge decision is not closed")


@dataclass(frozen=True, slots=True)
class CrossSampleClaimEdgeFacts:
    """Owner-backed facts for one candidate MS1 claim comparison."""

    left_group: CrossSampleGroupIdentity
    right_group: CrossSampleGroupIdentity
    left_member_subject: CrossSampleGroupMemberSubject
    right_member_subject: CrossSampleGroupMemberSubject

    left_family_mz: float
    right_family_mz: float
    left_apex_rt: float
    right_apex_rt: float
    left_peak_start_rt: float
    left_peak_end_rt: float
    right_peak_start_rt: float
    right_peak_end_rt: float
    context: CrossSampleEdgeContext
    comparison_kind: Literal["numeric_candidate", "exact_candidate"] = (
        "numeric_candidate"
    )
    left_neutral_loss_tag: str = ""
    right_neutral_loss_tag: str = ""
    left_targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None
    right_targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None

    def __post_init__(self) -> None:
        if self.context not in {"claim_candidate", "repair_derived_observation"}:
            raise CrossSampleContractError("claim edge context is not closed")
        if self.comparison_kind not in {"numeric_candidate", "exact_candidate"}:
            raise CrossSampleContractError("claim comparison kind is not closed")
        if any(
            anchor is not None and not isinstance(anchor, TargetedIstdIdentityAnchor)
            for anchor in (
                self.left_targeted_istd_anchor,
                self.right_targeted_istd_anchor,
            )
        ):
            raise CrossSampleContractError("claim ISTD anchor is not typed")
        if (
            self.left_targeted_istd_anchor is not None
            or self.right_targeted_istd_anchor is not None
        ) and (not self.left_neutral_loss_tag or not self.right_neutral_loss_tag):
            raise CrossSampleContractError("claim ISTD edge requires neutral-loss tags")
        _validate_owner_backed_edge_members(
            left_group=self.left_group,
            right_group=self.right_group,
            left_member=self.left_member_subject,
            right_member=self.right_member_subject,
            field="claim edge",
        )
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in (
                self.left_family_mz,
                self.right_family_mz,
                self.left_apex_rt,
                self.right_apex_rt,
                self.left_peak_start_rt,
                self.left_peak_end_rt,
                self.right_peak_start_rt,
                self.right_peak_end_rt,
            )
        ):
            raise CrossSampleContractError("claim edge facts must be finite")


@dataclass(frozen=True, slots=True)
class CrossSamplePreConsolidationEdgeFacts:
    """Closed owner-backed facts for a pre-backfill group compatibility edge."""

    left_group: CrossSampleGroupIdentity
    right_group: CrossSampleGroupIdentity
    left_neutral_loss_tag: str
    right_neutral_loss_tag: str
    left_precursor_mz: float
    right_precursor_mz: float
    left_product_mz: float
    right_product_mz: float
    left_observed_loss_da: float
    right_observed_loss_da: float
    left_rt_min: float
    right_rt_min: float
    left_targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None
    right_targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.left_group, CrossSampleGroupIdentity) or not isinstance(
            self.right_group,
            CrossSampleGroupIdentity,
        ):
            raise CrossSampleContractError("pre consolidation groups must be canonical")
        if self.left_group == self.right_group:
            raise CrossSampleContractError("pre consolidation edge cannot self-connect")
        if (
            self.left_group.processing_request_id
            != self.right_group.processing_request_id
        ):
            raise CrossSampleContractError("pre consolidation edge crossed request")
        if not self.left_neutral_loss_tag or not self.right_neutral_loss_tag:
            raise CrossSampleContractError(
                "pre consolidation neutral-loss tag is required"
            )
        if any(
            anchor is not None and not isinstance(anchor, TargetedIstdIdentityAnchor)
            for anchor in (
                self.left_targeted_istd_anchor,
                self.right_targeted_istd_anchor,
            )
        ):
            raise CrossSampleContractError("pre consolidation ISTD anchor is not typed")
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in (
                self.left_precursor_mz,
                self.right_precursor_mz,
                self.left_product_mz,
                self.right_product_mz,
                self.left_observed_loss_da,
                self.right_observed_loss_da,
                self.left_rt_min,
                self.right_rt_min,
            )
        ):
            raise CrossSampleContractError("pre consolidation facts must be finite")


@dataclass(frozen=True, slots=True)
class CrossSamplePrimaryConsolidationEdgeFacts:
    """Owner-backed raw facts for a claim-derived primary consolidation edge."""

    left_group: CrossSampleGroupIdentity
    right_group: CrossSampleGroupIdentity
    left_member_subject: CrossSampleGroupMemberSubject
    right_member_subject: CrossSampleGroupMemberSubject
    left_neutral_loss_tag: str
    right_neutral_loss_tag: str
    left_precursor_mz: float
    right_precursor_mz: float
    left_product_mz: float
    right_product_mz: float
    left_observed_loss_da: float
    right_observed_loss_da: float
    left_rt_min: float
    right_rt_min: float
    claim_source_group_hypothesis_id: str = ""
    claim_winner_group_hypothesis_id: str = ""
    left_targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None
    right_targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None

    def __post_init__(self) -> None:
        _validate_owner_backed_edge_members(
            left_group=self.left_group,
            right_group=self.right_group,
            left_member=self.left_member_subject,
            right_member=self.right_member_subject,
            field="primary consolidation edge",
        )
        if bool(self.claim_source_group_hypothesis_id) != bool(
            self.claim_winner_group_hypothesis_id
        ):
            raise CrossSampleContractError(
                "primary consolidation claim closure is incomplete"
            )
        if self.claim_source_group_hypothesis_id and (
            self.claim_source_group_hypothesis_id
            != self.left_group.group_hypothesis_id
            or self.claim_winner_group_hypothesis_id
            != self.right_group.group_hypothesis_id
        ):
            raise CrossSampleContractError(
                "primary consolidation claim closure crossed canonical groups"
            )
        if not self.left_neutral_loss_tag or not self.right_neutral_loss_tag:
            raise CrossSampleContractError(
                "primary consolidation neutral-loss tag is required"
            )
        if any(
            anchor is not None and not isinstance(anchor, TargetedIstdIdentityAnchor)
            for anchor in (
                self.left_targeted_istd_anchor,
                self.right_targeted_istd_anchor,
            )
        ):
            raise CrossSampleContractError(
                "primary consolidation ISTD anchor is not typed"
            )
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in (
                self.left_precursor_mz,
                self.right_precursor_mz,
                self.left_product_mz,
                self.right_product_mz,
                self.left_observed_loss_da,
                self.right_observed_loss_da,
                self.left_rt_min,
                self.right_rt_min,
            )
        ):
            raise CrossSampleContractError(
                "primary consolidation facts must be finite"
            )


@dataclass(frozen=True, slots=True)
class _TargetedIstdAnchorObservation:
    """Minimal typed observation consumed by the existing ISTD anchor owner."""

    neutral_loss_tag: str
    family_center_mz: float
    family_center_rt: float


def _targeted_istd_facts_are_compatible(
    *,
    left_anchor: TargetedIstdIdentityAnchor | None,
    right_anchor: TargetedIstdIdentityAnchor | None,
    left_tag: str,
    right_tag: str,
    left_mz: float,
    right_mz: float,
    left_rt: float,
    right_rt: float,
) -> bool:
    """Keep a typed ISTD target identity closed across successor proposals."""

    if left_anchor is None and right_anchor is None:
        return True
    if (
        left_anchor is None
        or right_anchor is None
        or left_anchor.target_snapshot != right_anchor.target_snapshot
        or left_anchor.observed_labels != right_anchor.observed_labels
    ):
        return False
    left_target = left_anchor.matching_target(
        _TargetedIstdAnchorObservation(
            neutral_loss_tag=left_tag,
            family_center_mz=left_mz,
            family_center_rt=left_rt,
        )
    )
    right_target = right_anchor.matching_target(
        _TargetedIstdAnchorObservation(
            neutral_loss_tag=right_tag,
            family_center_mz=right_mz,
            family_center_rt=right_rt,
        )
    )
    if left_target is None and right_target is None:
        return True
    if left_target is None or right_target is None:
        return False
    return (
        left_target.label == right_target.label
        and left_anchor.target_config_identity == right_anchor.target_config_identity
    )


@dataclass(frozen=True, slots=True)
class CrossSamplePrimaryDispositionEdgeFacts:
    """Raw owner-backed evidence for a disposition-only primary comparison."""

    left_group: CrossSampleGroupIdentity
    right_group: CrossSampleGroupIdentity
    left_detected_members: tuple[CrossSampleGroupMemberSubject, ...]
    right_detected_members: tuple[CrossSampleGroupMemberSubject, ...]
    left_detected_count: int
    right_detected_count: int
    left_rescue_count: int
    right_rescue_count: int
    left_neutral_loss_tag: str
    right_neutral_loss_tag: str
    left_precursor_mz: float
    right_precursor_mz: float
    left_product_mz: float
    right_product_mz: float
    left_observed_loss_da: float
    right_observed_loss_da: float
    left_rt_min: float
    right_rt_min: float
    left_targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None
    right_targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.left_group, CrossSampleGroupIdentity) or not isinstance(
            self.right_group,
            CrossSampleGroupIdentity,
        ):
            raise CrossSampleContractError(
                "primary disposition groups must be canonical"
            )
        if self.left_group == self.right_group:
            raise CrossSampleContractError(
                "primary disposition edge cannot self-connect"
            )
        if (
            self.left_group.processing_request_id
            != self.right_group.processing_request_id
        ):
            raise CrossSampleContractError(
                "primary disposition edge crossed request"
            )
        for group, members, side in (
            (self.left_group, self.left_detected_members, "left"),
            (self.right_group, self.right_detected_members, "right"),
        ):
            member_ids = tuple(member.member_subject_id for member in members)
            if len(set(member_ids)) != len(member_ids) or any(
                member not in group.member_subjects for member in members
            ):
                raise CrossSampleContractError(
                    f"primary disposition {side} detected members lack closure"
                )
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (
                self.left_detected_count,
                self.right_detected_count,
                self.left_rescue_count,
                self.right_rescue_count,
            )
        ):
            raise CrossSampleContractError(
                "primary disposition counts must be non-negative integers"
            )
        if not self.left_neutral_loss_tag or not self.right_neutral_loss_tag:
            raise CrossSampleContractError(
                "primary disposition neutral-loss tag is required"
            )
        if any(
            anchor is not None and not isinstance(anchor, TargetedIstdIdentityAnchor)
            for anchor in (
                self.left_targeted_istd_anchor,
                self.right_targeted_istd_anchor,
            )
        ):
            raise CrossSampleContractError(
                "primary disposition ISTD anchor is not typed"
            )
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in (
                self.left_precursor_mz,
                self.right_precursor_mz,
                self.left_product_mz,
                self.right_product_mz,
                self.left_observed_loss_da,
                self.right_observed_loss_da,
                self.left_rt_min,
                self.right_rt_min,
            )
        ):
            raise CrossSampleContractError(
                "primary disposition facts must be finite"
            )


@dataclass(frozen=True, slots=True)
class CrossSampleBackfillScopeEdgeFacts:
    """Owner-backed raw facts for the legacy backfill-scope neighbor check."""

    left_group: CrossSampleGroupIdentity
    right_group: CrossSampleGroupIdentity
    left_neutral_loss_tag: str
    right_neutral_loss_tag: str
    left_precursor_mz: float
    right_precursor_mz: float
    left_product_mz: float
    right_product_mz: float
    left_observed_loss_da: float
    right_observed_loss_da: float
    left_rt_min: float
    right_rt_min: float
    left_targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None
    right_targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None

    def __post_init__(self) -> None:
        CrossSamplePreConsolidationEdgeFacts(
            left_group=self.left_group,
            right_group=self.right_group,
            left_neutral_loss_tag=self.left_neutral_loss_tag,
            right_neutral_loss_tag=self.right_neutral_loss_tag,
            left_precursor_mz=self.left_precursor_mz,
            right_precursor_mz=self.right_precursor_mz,
            left_product_mz=self.left_product_mz,
            right_product_mz=self.right_product_mz,
            left_observed_loss_da=self.left_observed_loss_da,
            right_observed_loss_da=self.right_observed_loss_da,
            left_rt_min=self.left_rt_min,
            right_rt_min=self.right_rt_min,
            left_targeted_istd_anchor=self.left_targeted_istd_anchor,
            right_targeted_istd_anchor=self.right_targeted_istd_anchor,
        )


def _validate_owner_backed_edge_members(
    *,
    left_group: CrossSampleGroupIdentity,
    right_group: CrossSampleGroupIdentity,
    left_member: CrossSampleGroupMemberSubject,
    right_member: CrossSampleGroupMemberSubject,
    field: str,
) -> None:
    if not isinstance(left_group, CrossSampleGroupIdentity) or not isinstance(
        right_group,
        CrossSampleGroupIdentity,
    ):
        raise CrossSampleContractError(f"{field} groups must be canonical")
    if not isinstance(left_member, CrossSampleGroupMemberSubject) or not isinstance(
        right_member,
        CrossSampleGroupMemberSubject,
    ):
        raise CrossSampleContractError(f"{field} members must be canonical")
    if left_group.processing_request_id != right_group.processing_request_id:
        raise CrossSampleContractError(f"{field} crossed processing request")
    if (
        left_member not in left_group.member_subjects
        or right_member not in right_group.member_subjects
    ):
        raise CrossSampleContractError(f"{field} member is outside its canonical group")
    if (
        left_member.processing_request_id != left_group.processing_request_id
        or right_member.processing_request_id != right_group.processing_request_id
    ):
        raise CrossSampleContractError(f"{field} member crossed processing request")


def classify_cross_sample_pre_consolidation_edge(
    facts: CrossSamplePreConsolidationEdgeFacts,
    *,
    config: AlignmentConfig,
) -> CrossSampleNeighborhoodEdge:
    """Classify raw pre-backfill facts without trusting caller verdicts."""

    if not isinstance(facts, CrossSamplePreConsolidationEdgeFacts):
        raise CrossSampleContractError("pre consolidation facts must be typed")
    left_samples = {
        member.canonical_sample_identity_id
        for member in facts.left_group.member_subjects
    }
    right_samples = {
        member.canonical_sample_identity_id
        for member in facts.right_group.member_subjects
    }
    same_sample = bool(left_samples.intersection(right_samples))
    compatible = (
        facts.left_group.disposition not in {"unresolved", "rejected"}
        and facts.right_group.disposition not in {"unresolved", "rejected"}
        and not same_sample
        and facts.left_neutral_loss_tag == facts.right_neutral_loss_tag
        and _ppm(facts.left_precursor_mz, facts.right_precursor_mz) <= config.max_ppm
        and abs(facts.left_rt_min - facts.right_rt_min) * 60.0
        <= config.identity_rt_candidate_window_sec
        and _ppm(facts.left_product_mz, facts.right_product_mz)
        <= config.product_mz_tolerance_ppm
        and _ppm(facts.left_observed_loss_da, facts.right_observed_loss_da)
        <= config.observed_loss_tolerance_ppm
        and _targeted_istd_facts_are_compatible(
            left_anchor=facts.left_targeted_istd_anchor,
            right_anchor=facts.right_targeted_istd_anchor,
            left_tag=facts.left_neutral_loss_tag,
            right_tag=facts.right_neutral_loss_tag,
            left_mz=facts.left_precursor_mz,
            right_mz=facts.right_precursor_mz,
            left_rt=facts.left_rt_min,
            right_rt=facts.right_rt_min,
        )
    )
    return CrossSampleNeighborhoodEdge(
        left_member_subject_id=facts.left_group.member_subjects[0].member_subject_id,
        right_member_subject_id=(
            facts.right_group.member_subjects[0].member_subject_id
        ),
        disposition=classify_cross_sample_edge(
            "strong_edge" if compatible else "blocked_edge",
            context="claim_candidate",
        ),
    )


def classify_cross_sample_primary_consolidation_edge(
    facts: CrossSamplePrimaryConsolidationEdgeFacts,
    *,
    config: AlignmentConfig,
) -> CrossSampleNeighborhoodEdge:
    """Classify primary claim facts from their raw owner-backed values."""

    if not isinstance(facts, CrossSamplePrimaryConsolidationEdgeFacts):
        raise CrossSampleContractError("primary consolidation facts must be typed")
    same_sample = (
        facts.left_member_subject.canonical_sample_identity_id
        == facts.right_member_subject.canonical_sample_identity_id
    )
    same_sample_exact_claim = (
        same_sample
        and facts.claim_source_group_hypothesis_id
        == facts.left_group.group_hypothesis_id
        and facts.claim_winner_group_hypothesis_id
        == facts.right_group.group_hypothesis_id
    )
    same_tag = facts.left_neutral_loss_tag == facts.right_neutral_loss_tag
    same_precursor_and_rt = (
        _ppm(facts.left_precursor_mz, facts.right_precursor_mz) <= config.max_ppm
        and abs(facts.left_rt_min - facts.right_rt_min) * 60.0
        <= config.identity_rt_candidate_window_sec
    )
    primary_compatible = (
        facts.left_group.disposition not in {"unresolved", "rejected"}
        and facts.right_group.disposition not in {"unresolved", "rejected"}
        and same_tag
        and same_precursor_and_rt
        and _ppm(facts.left_product_mz, facts.right_product_mz)
        <= config.product_mz_tolerance_ppm
        and _ppm(facts.left_observed_loss_da, facts.right_observed_loss_da)
        <= config.observed_loss_tolerance_ppm
        and _targeted_istd_facts_are_compatible(
            left_anchor=facts.left_targeted_istd_anchor,
            right_anchor=facts.right_targeted_istd_anchor,
            left_tag=facts.left_neutral_loss_tag,
            right_tag=facts.right_neutral_loss_tag,
            left_mz=facts.left_precursor_mz,
            right_mz=facts.right_precursor_mz,
            left_rt=facts.left_rt_min,
            right_rt=facts.right_rt_min,
        )
    )
    shared_ms1_compatible = (
        facts.left_group.disposition not in {"unresolved", "rejected"}
        and facts.right_group.disposition not in {"unresolved", "rejected"}
        and not same_tag
        and same_precursor_and_rt
        and _targeted_istd_facts_are_compatible(
            left_anchor=facts.left_targeted_istd_anchor,
            right_anchor=facts.right_targeted_istd_anchor,
            left_tag=facts.left_neutral_loss_tag,
            right_tag=facts.right_neutral_loss_tag,
            left_mz=facts.left_precursor_mz,
            right_mz=facts.right_precursor_mz,
            left_rt=facts.left_rt_min,
            right_rt=facts.right_rt_min,
        )
    )
    return CrossSampleNeighborhoodEdge(
        left_member_subject_id=facts.left_member_subject.member_subject_id,
        right_member_subject_id=facts.right_member_subject.member_subject_id,
        disposition=classify_cross_sample_edge(
            "strong_edge"
            if (not same_sample or same_sample_exact_claim)
            and (primary_compatible or shared_ms1_compatible)
            else "blocked_edge",
            context="claim_candidate",
        ),
    )


def classify_cross_sample_primary_disposition_edge(
    facts: CrossSamplePrimaryDispositionEdgeFacts,
    *,
    config: AlignmentConfig,
) -> CrossSampleNeighborhoodEdge:
    """Classify disposition-only primary evidence inside CrossSample."""

    if not isinstance(facts, CrossSamplePrimaryDispositionEdgeFacts):
        raise CrossSampleContractError("primary disposition facts must be typed")
    left_detected_samples = {
        member.canonical_sample_identity_id for member in facts.left_detected_members
    }
    right_detected_samples = {
        member.canonical_sample_identity_id for member in facts.right_detected_members
    }
    product_matches = (
        _ppm(facts.left_product_mz, facts.right_product_mz)
        <= config.product_mz_tolerance_ppm
    )
    precursor_delta = facts.right_precursor_mz - facts.left_precursor_mz
    product_delta = facts.right_product_mz - facts.left_product_mz
    compatible = (
        facts.left_group.disposition not in {"unresolved", "rejected"}
        and facts.right_group.disposition not in {"unresolved", "rejected"}
        and facts.left_neutral_loss_tag == facts.right_neutral_loss_tag
        and _ppm(facts.left_precursor_mz, facts.right_precursor_mz)
        <= config.max_ppm
        and abs(facts.left_rt_min - facts.right_rt_min) * 60.0
        <= config.identity_rt_candidate_window_sec
        and _ppm(facts.left_observed_loss_da, facts.right_observed_loss_da)
        <= config.observed_loss_tolerance_ppm
        and (
            product_matches
            or abs(product_delta - precursor_delta)
            <= _PRIMARY_DISPOSITION_PRODUCT_PRECURSOR_SHIFT_TOLERANCE_DA
        )
        and bool(left_detected_samples.intersection(right_detected_samples))
        and (
            facts.left_rescue_count > facts.left_detected_count > 0
            and facts.right_detected_count >= 2
            or facts.right_rescue_count > facts.right_detected_count > 0
            and facts.left_detected_count >= 2
        )
        and _targeted_istd_facts_are_compatible(
            left_anchor=facts.left_targeted_istd_anchor,
            right_anchor=facts.right_targeted_istd_anchor,
            left_tag=facts.left_neutral_loss_tag,
            right_tag=facts.right_neutral_loss_tag,
            left_mz=facts.left_precursor_mz,
            right_mz=facts.right_precursor_mz,
            left_rt=facts.left_rt_min,
            right_rt=facts.right_rt_min,
        )
    )
    return CrossSampleNeighborhoodEdge(
        left_member_subject_id=facts.left_group.member_subjects[0].member_subject_id,
        right_member_subject_id=(
            facts.right_group.member_subjects[0].member_subject_id
        ),
        disposition=classify_cross_sample_edge(
            "strong_edge" if compatible else "blocked_edge",
            context="claim_candidate",
        ),
    )


def classify_cross_sample_backfill_scope_edge(
    facts: CrossSampleBackfillScopeEdgeFacts,
    *,
    config: AlignmentConfig,
) -> CrossSampleNeighborhoodEdge:
    """Classify the Backfill scope's former loose compatibility predicate."""

    if not isinstance(facts, CrossSampleBackfillScopeEdgeFacts):
        raise CrossSampleContractError("backfill scope facts must be typed")
    left_samples = {
        member.canonical_sample_identity_id
        for member in facts.left_group.member_subjects
    }
    right_samples = {
        member.canonical_sample_identity_id
        for member in facts.right_group.member_subjects
    }
    same_sample = bool(left_samples.intersection(right_samples))
    product_matches = (
        _ppm(facts.left_product_mz, facts.right_product_mz)
        <= config.product_mz_tolerance_ppm
    )
    precursor_delta = facts.right_precursor_mz - facts.left_precursor_mz
    product_delta = facts.right_product_mz - facts.left_product_mz
    compatible = (
        facts.left_group.disposition not in {"unresolved", "rejected"}
        and facts.right_group.disposition not in {"unresolved", "rejected"}
        and not same_sample
        and facts.left_neutral_loss_tag == facts.right_neutral_loss_tag
        and _ppm(facts.left_precursor_mz, facts.right_precursor_mz) <= config.max_ppm
        and abs(facts.left_rt_min - facts.right_rt_min) * 60.0
        <= config.identity_rt_candidate_window_sec
        and _ppm(facts.left_observed_loss_da, facts.right_observed_loss_da)
        <= config.observed_loss_tolerance_ppm
        and (
            product_matches
            or abs(product_delta - precursor_delta)
            <= _PRIMARY_DISPOSITION_PRODUCT_PRECURSOR_SHIFT_TOLERANCE_DA
        )
        and _targeted_istd_facts_are_compatible(
            left_anchor=facts.left_targeted_istd_anchor,
            right_anchor=facts.right_targeted_istd_anchor,
            left_tag=facts.left_neutral_loss_tag,
            right_tag=facts.right_neutral_loss_tag,
            left_mz=facts.left_precursor_mz,
            right_mz=facts.right_precursor_mz,
            left_rt=facts.left_rt_min,
            right_rt=facts.right_rt_min,
        )
    )
    return CrossSampleNeighborhoodEdge(
        left_member_subject_id=facts.left_group.member_subjects[0].member_subject_id,
        right_member_subject_id=facts.right_group.member_subjects[0].member_subject_id,
        disposition=classify_cross_sample_edge(
            "strong_edge" if compatible else "blocked_edge",
            context="claim_candidate",
        ),
    )


def predecessor_membership_neighborhood_edges(
    predecessors: Sequence[CrossSampleGroupIdentity],
) -> tuple[CrossSampleNeighborhoodEdge, ...]:
    """Expose CrossSample's already-minted membership closure as eligible edges."""

    edges: list[CrossSampleNeighborhoodEdge] = []
    for predecessor in predecessors:
        members = predecessor.member_subjects
        for left, right in zip(members, members[1:], strict=False):
            edges.append(
                CrossSampleNeighborhoodEdge(
                    left_member_subject_id=left.member_subject_id,
                    right_member_subject_id=right.member_subject_id,
                    disposition=classify_cross_sample_edge(
                        "strong_edge",
                        context="initial_grouping",
                    ),
                )
            )
    return tuple(edges)


def classify_cross_sample_claim_edge(
    facts: CrossSampleClaimEdgeFacts,
    *,
    config: AlignmentConfig,
) -> CrossSampleEdgeDisposition:
    """Classify the historic duplicate-claim predicate through CrossSample.

    This deliberately retains the existing ppm/apex/window mathematics, but
    only this owner may translate it into downstream eligibility.
    """

    if not isinstance(facts, CrossSampleClaimEdgeFacts):
        raise CrossSampleContractError("claim edge facts must be typed")
    ppm = (
        abs(facts.left_family_mz - facts.right_family_mz)
        / max(abs(facts.left_family_mz), 1e-12)
        * 1_000_000.0
    )
    apex_delta_sec = abs(facts.left_apex_rt - facts.right_apex_rt) * 60.0
    left_width = facts.left_peak_end_rt - facts.left_peak_start_rt
    right_width = facts.right_peak_end_rt - facts.right_peak_start_rt
    overlap = min(facts.left_peak_end_rt, facts.right_peak_end_rt) - max(
        facts.left_peak_start_rt,
        facts.right_peak_start_rt,
    )
    overlap_fraction = (
        overlap / min(left_width, right_width)
        if left_width > 0 and right_width > 0 and overlap > 0
        else 0.0
    )
    shared_physical_ids = set(
        facts.left_member_subject.physical_observation_ids
    ).intersection(facts.right_member_subject.physical_observation_ids)
    numeric_compatible = (
        ppm <= config.duplicate_fold_ppm
        and apex_delta_sec <= config.owner_apex_close_sec
        and overlap_fraction >= config.owner_window_overlap_fraction
    )
    compatible = (
        bool(shared_physical_ids)
        if facts.comparison_kind == "exact_candidate"
        else bool(shared_physical_ids) or numeric_compatible
    )
    decision: EdgeDecision = (
        "strong_edge"
        if facts.left_group.disposition not in {"unresolved", "rejected"}
        and facts.right_group.disposition not in {"unresolved", "rejected"}
        and _targeted_istd_facts_are_compatible(
            left_anchor=facts.left_targeted_istd_anchor,
            right_anchor=facts.right_targeted_istd_anchor,
            left_tag=facts.left_neutral_loss_tag,
            right_tag=facts.right_neutral_loss_tag,
            left_mz=facts.left_family_mz,
            right_mz=facts.right_family_mz,
            left_rt=facts.left_apex_rt,
            right_rt=facts.right_apex_rt,
        )
        and compatible
        else "blocked_edge"
    )
    return classify_cross_sample_edge(
        decision,
        context=facts.context,
    )


def build_bounded_connected_neighborhood(
    *,
    member_subjects: Sequence[CrossSampleGroupMemberSubject],
    edges: Sequence[CrossSampleNeighborhoodEdge],
    budget: CrossSampleNeighborhoodBudget,
) -> CrossSampleConnectedNeighborhood:
    """Return complete eligible-edge components, or typed unassessable state."""

    if not isinstance(budget, CrossSampleNeighborhoodBudget):
        raise CrossSampleContractError("neighborhood budget must be typed")
    members = tuple(member_subjects)
    member_ids = tuple(member.member_subject_id for member in members)
    if len(set(member_ids)) != len(member_ids):
        raise CrossSampleContractError("neighborhood duplicates a member subject")
    canonical_member_ids = tuple(sorted(member_ids))
    eligible_edges = tuple(edge for edge in edges if edge.disposition == "eligible")
    canonical_eligible_edges = tuple(
        sorted(
            _canonical_neighborhood_edge_pair(
                edge.left_member_subject_id,
                edge.right_member_subject_id,
            )
            for edge in eligible_edges
        )
    )
    if len(set(canonical_eligible_edges)) != len(canonical_eligible_edges):
        raise CrossSampleContractError("neighborhood duplicates an eligible edge")
    member_set = set(member_ids)
    for edge in edges:
        if (
            edge.left_member_subject_id not in member_set
            or edge.right_member_subject_id not in member_set
        ):
            raise CrossSampleContractError(
                "neighborhood edge references unknown member"
            )
    if len(members) > budget.max_members:
        return CrossSampleConnectedNeighborhood(
            status="unassessable",
            member_subject_ids=canonical_member_ids,
            eligible_edges=canonical_eligible_edges,
            connected_components=(),
            reason="member_budget_exceeded",
        )
    if len(eligible_edges) > budget.max_eligible_edges:
        return CrossSampleConnectedNeighborhood(
            status="unassessable",
            member_subject_ids=canonical_member_ids,
            eligible_edges=canonical_eligible_edges,
            connected_components=(),
            reason="eligible_edge_budget_exceeded",
        )
    return CrossSampleConnectedNeighborhood(
        status="assessable",
        member_subject_ids=canonical_member_ids,
        eligible_edges=canonical_eligible_edges,
        connected_components=_connected_components_from_eligible_edges(
            canonical_member_ids,
            canonical_eligible_edges,
        ),
    )


def _canonical_neighborhood_edge_pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def _connected_components_from_eligible_edges(
    member_ids: tuple[str, ...],
    eligible_edges: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, ...], ...]:
    adjacency: dict[str, set[str]] = {member_id: set() for member_id in member_ids}
    for left, right in eligible_edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    remaining = set(member_ids)
    components: list[tuple[str, ...]] = []
    while remaining:
        root = min(remaining)
        pending = [root]
        component: set[str] = set()
        while pending:
            member_id = pending.pop()
            if member_id in component:
                continue
            component.add(member_id)
            pending.extend(sorted(adjacency[member_id] - component, reverse=True))
        remaining.difference_update(component)
        components.append(tuple(sorted(component)))
    return tuple(sorted(components))


@dataclass(frozen=True, slots=True)
class CrossSampleSuccessorProposal:
    processing_request_id: str
    predecessor_group_ids: tuple[str, ...]
    successor_member_sets: tuple[tuple[CrossSampleGroupMemberSubject, ...], ...]
    membership_closure: "CrossSampleMembershipClosure | None" = None


@dataclass(frozen=True, slots=True)
class CrossSampleMembershipClosure:
    """Exact added/removed member closure for an atomic attached mutation."""

    added_members: tuple[CrossSampleGroupMemberSubject, ...] = ()
    removed_members: tuple[CrossSampleGroupMemberSubject, ...] = ()

    def __post_init__(self) -> None:
        for member in (*self.added_members, *self.removed_members):
            if not isinstance(member, CrossSampleGroupMemberSubject):
                raise CrossSampleContractError("membership closure member is not typed")
        added_ids = tuple(member.member_subject_id for member in self.added_members)
        removed_ids = tuple(member.member_subject_id for member in self.removed_members)
        if len(set(added_ids)) != len(added_ids) or len(set(removed_ids)) != len(
            removed_ids
        ):
            raise CrossSampleContractError("membership closure duplicates a member")
        if set(added_ids).intersection(removed_ids):
            raise CrossSampleContractError(
                "membership closure crosses added and removed"
            )


@dataclass(frozen=True, slots=True)
class CrossSampleLineageEdge:
    processing_request_id: str
    predecessor_group_hypothesis_id: str
    successor_group_hypothesis_id: str
    predecessor_member_subjects: tuple[CrossSampleGroupMemberSubject, ...]
    member_subject_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CrossSampleSuccessorApplication:
    successors: tuple[CrossSampleGroupIdentity, ...]
    lineage: tuple[CrossSampleLineageEdge, ...]
    status: Literal["applied", "unassessable"] = "applied"
    reason: str = ""


def validate_cross_sample_lineage_edges(
    lineage: Sequence[CrossSampleLineageEdge],
    *,
    predecessors: Sequence[CrossSampleGroupIdentity],
    successors: Sequence[CrossSampleGroupIdentity],
) -> tuple[CrossSampleLineageEdge, ...]:
    """Replay immutable lineage against exact predecessor and successor bases."""

    predecessor_by_id = {
        group.group_hypothesis_id: group for group in predecessors
    }
    successor_by_id = {group.group_hypothesis_id: group for group in successors}
    if not predecessor_by_id or not successor_by_id:
        raise CrossSampleContractError("lineage requires predecessors and successors")
    request_ids = {
        *(group.processing_request_id for group in predecessor_by_id.values()),
        *(group.processing_request_id for group in successor_by_id.values()),
    }
    if len(request_ids) != 1:
        raise CrossSampleContractError("lineage crossed processing request")
    request_id = next(iter(request_ids))
    expected: dict[tuple[str, str], tuple[str, ...]] = {}
    for predecessor in predecessor_by_id.values():
        predecessor_ids = {
            member.member_subject_id for member in predecessor.member_subjects
        }
        for successor in successor_by_id.values():
            shared = tuple(
                member.member_subject_id
                for member in successor.member_subjects
                if member.member_subject_id in predecessor_ids
            )
            if shared and (
                predecessor.group_hypothesis_id
                != successor.group_hypothesis_id
            ):
                expected[(
                    predecessor.group_hypothesis_id,
                    successor.group_hypothesis_id,
                )] = shared
    validated: list[CrossSampleLineageEdge] = []
    observed: set[tuple[str, str]] = set()
    for edge in lineage:
        if not isinstance(edge, CrossSampleLineageEdge):
            raise CrossSampleContractError("lineage edge is not typed")
        if edge.processing_request_id != request_id:
            raise CrossSampleContractError("lineage edge crossed processing request")
        predecessor_identity = predecessor_by_id.get(
            edge.predecessor_group_hypothesis_id
        )
        successor_identity = successor_by_id.get(
            edge.successor_group_hypothesis_id
        )
        if predecessor_identity is None or successor_identity is None:
            raise CrossSampleContractError("lineage edge references an unknown group")
        if edge.predecessor_member_subjects != predecessor_identity.member_subjects:
            raise CrossSampleContractError("lineage edge lost predecessor member basis")
        key = (
            edge.predecessor_group_hypothesis_id,
            edge.successor_group_hypothesis_id,
        )
        if key in observed or edge.member_subject_ids != expected.get(key):
            raise CrossSampleContractError("lineage edge does not close exact members")
        observed.add(key)
        validated.append(edge)
    if observed != set(expected):
        raise CrossSampleContractError("lineage is incomplete")
    return tuple(
        sorted(
            validated,
            key=lambda edge: (
                edge.predecessor_group_hypothesis_id,
                edge.successor_group_hypothesis_id,
            ),
        )
    )


def validate_cross_sample_lineage_carrier(
    lineage: Sequence[CrossSampleLineageEdge],
    *,
    current_successor: CrossSampleGroupIdentity,
) -> tuple[CrossSampleLineageEdge, ...]:
    """Replay a retained lineage graph from exact predecessor member bases."""

    identities: dict[str, CrossSampleGroupIdentity] = {
        current_successor.group_hypothesis_id: current_successor
    }
    for edge in lineage:
        if not isinstance(edge, CrossSampleLineageEdge):
            raise CrossSampleContractError("lineage carrier edge is not typed")
        if edge.processing_request_id != current_successor.processing_request_id:
            raise CrossSampleContractError("lineage carrier crossed processing request")
        if (
            edge.predecessor_group_hypothesis_id
            == edge.successor_group_hypothesis_id
        ):
            raise CrossSampleContractError("lineage carrier contains a self-cycle")
        predecessor = mint_cross_sample_group_identity(
            processing_request_id=edge.processing_request_id,
            member_subjects=edge.predecessor_member_subjects,
        )
        if predecessor.group_hypothesis_id != edge.predecessor_group_hypothesis_id:
            raise CrossSampleContractError(
                "lineage carrier predecessor basis is forged"
            )
        identities[predecessor.group_hypothesis_id] = predecessor
    canonical: list[CrossSampleLineageEdge] = []
    seen: set[tuple[str, str]] = set()
    for edge in lineage:
        predecessor = identities[edge.predecessor_group_hypothesis_id]
        successor = identities.get(edge.successor_group_hypothesis_id)
        if successor is None:
            raise CrossSampleContractError("lineage carrier successor basis is absent")
        key = (
            edge.predecessor_group_hypothesis_id,
            edge.successor_group_hypothesis_id,
        )
        expected_member_ids = tuple(
            member.member_subject_id
            for member in successor.member_subjects
            if member.member_subject_id
            in {item.member_subject_id for item in predecessor.member_subjects}
        )
        if key in seen or edge.member_subject_ids != expected_member_ids:
            raise CrossSampleContractError("lineage carrier member closure is forged")
        seen.add(key)
        canonical.append(edge)
    return tuple(
        sorted(
            canonical,
            key=lambda edge: (
                edge.predecessor_group_hypothesis_id,
                edge.successor_group_hypothesis_id,
            ),
        )
    )


def apply_coupled_successor_proposal(
    *,
    predecessors: Sequence[CrossSampleGroupIdentity],
    proposal: CrossSampleSuccessorProposal,
    neighborhood: CrossSampleConnectedNeighborhood,
    population: Sequence[CrossSampleGroupIdentity],
) -> CrossSampleSuccessorApplication:
    """Atomically mint a complete split/merge successor set and lineage."""

    if not isinstance(proposal, CrossSampleSuccessorProposal):
        raise CrossSampleContractError("successor proposal must be typed")
    if not isinstance(neighborhood, CrossSampleConnectedNeighborhood):
        raise CrossSampleContractError("successor proposal needs a typed neighborhood")
    if neighborhood.status != "assessable":
        raise CrossSampleContractError(
            "unassessable neighborhood cannot apply a successor proposal"
        )
    request_id = validate_typed_identity_id(
        proposal.processing_request_id,
        prefix=PROCESSING_REQUEST_ID_PREFIX,
        field="proposal processing_request_id",
    )
    ordered_predecessors = tuple(
        sorted(predecessors, key=lambda group: group.group_hypothesis_id)
    )
    if not ordered_predecessors:
        raise CrossSampleContractError("successor proposal has no predecessor groups")
    predecessor_ids = tuple(group.group_hypothesis_id for group in ordered_predecessors)
    if (
        len(set(predecessor_ids)) != len(predecessor_ids)
        or tuple(sorted(proposal.predecessor_group_ids)) != predecessor_ids
    ):
        raise CrossSampleContractError(
            "successor proposal does not consume predecessors"
        )
    if any(group.processing_request_id != request_id for group in ordered_predecessors):
        raise CrossSampleContractError("successor proposal crossed processing request")
    complete_population = tuple(population)
    validate_cross_sample_group_population(complete_population)
    population_by_id = {
        group.group_hypothesis_id: group for group in complete_population
    }
    if any(
        population_by_id.get(group.group_hypothesis_id) != group
        for group in ordered_predecessors
    ):
        raise CrossSampleContractError(
            "successor proposal population does not contain exact predecessors"
        )
    predecessor_id_set = set(predecessor_ids)
    unaffected_population = tuple(
        group
        for group in complete_population
        if group.group_hypothesis_id not in predecessor_id_set
    )
    predecessor_members = tuple(
        member for group in ordered_predecessors for member in group.member_subjects
    )
    predecessor_member_ids = tuple(
        member.member_subject_id for member in predecessor_members
    )
    predecessor_physical_ids = tuple(
        physical_observation_id
        for member in predecessor_members
        for physical_observation_id in member.physical_observation_ids
    )
    if len(set(predecessor_member_ids)) != len(predecessor_member_ids) or len(
        set(predecessor_physical_ids)
    ) != len(predecessor_physical_ids):
        raise CrossSampleContractError(
            "successor predecessors duplicate member or physical observation"
        )
    successor_member_sets = tuple(proposal.successor_member_sets)
    if not successor_member_sets or any(not item for item in successor_member_sets):
        raise CrossSampleContractError("successor proposal has an empty successor set")
    successor_members = tuple(
        member for member_set in successor_member_sets for member in member_set
    )
    successor_member_ids = tuple(
        member.member_subject_id for member in successor_members
    )
    closure = proposal.membership_closure
    if closure is not None and not isinstance(closure, CrossSampleMembershipClosure):
        raise CrossSampleContractError("successor membership closure must be typed")
    added_members = () if closure is None else closure.added_members
    removed_members = () if closure is None else closure.removed_members
    added_ids = {member.member_subject_id for member in added_members}
    removed_ids = {member.member_subject_id for member in removed_members}
    predecessor_by_id = {
        member.member_subject_id: member for member in predecessor_members
    }
    successor_by_id = {member.member_subject_id: member for member in successor_members}
    if any(member.processing_request_id != request_id for member in added_members):
        raise CrossSampleContractError(
            "successor added member crossed processing request"
        )
    if any(
        member.member_subject_id not in predecessor_by_id
        for member in removed_members
    ):
        raise CrossSampleContractError(
            "successor removed member was not a predecessor"
        )
    if tuple(sorted((*predecessor_member_ids, *added_ids))) != (
        neighborhood.member_subject_ids
    ):
        raise CrossSampleContractError(
            "successor neighborhood does not close exact predecessor and added members"
        )
    expected_successor_ids = (
        set(predecessor_member_ids).difference(removed_ids).union(added_ids)
    )
    actual_added_ids = set(successor_member_ids).difference(predecessor_member_ids)
    actual_removed_ids = set(predecessor_member_ids).difference(successor_member_ids)
    if (
        len(set(successor_member_ids)) != len(successor_member_ids)
        or set(successor_member_ids) != expected_successor_ids
        or actual_added_ids != added_ids
        or actual_removed_ids != removed_ids
        or (actual_added_ids or actual_removed_ids) and closure is None
    ):
        raise CrossSampleContractError(
            "successor proposal does not form a complete typed member partition"
        )
    for member_id in set(successor_member_ids).intersection(predecessor_by_id):
        if successor_by_id[member_id] != predecessor_by_id[member_id]:
            raise CrossSampleContractError(
                "successor proposal changed an existing member basis"
            )
    predecessor_partitions = tuple(
        sorted(
            tuple(sorted(member.member_subject_id for member in group.member_subjects))
            for group in ordered_predecessors
        )
    )
    successor_partitions = tuple(
        sorted(
            tuple(sorted(member.member_subject_id for member in member_set))
            for member_set in successor_member_sets
        )
    )
    if (
        successor_partitions == predecessor_partitions
        and not (added_ids or removed_ids)
    ):
        return CrossSampleSuccessorApplication(
            successors=ordered_predecessors,
            lineage=(),
            reason="unchanged_membership_no_lineage",
        )
    component_by_member = {
        member_id: index
        for index, component in enumerate(neighborhood.connected_components)
        for member_id in component
    }
    for member_set in successor_member_sets:
        component_ids = {
            component_by_member[member.member_subject_id] for member in member_set
        }
        if len(component_ids) != 1:
            raise CrossSampleContractError(
                "successor proposal spans disconnected eligible components"
            )
    successors = tuple(
        sorted(
            (
                mint_cross_sample_group_identity(
                    processing_request_id=request_id,
                    member_subjects=member_set,
                )
                for member_set in successor_member_sets
            ),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    lineage: list[CrossSampleLineageEdge] = []
    unchanged_predecessor_ids = {
        predecessor.group_hypothesis_id
        for predecessor in ordered_predecessors
        if any(
            successor.group_hypothesis_id == predecessor.group_hypothesis_id
            and successor.member_subjects == predecessor.member_subjects
            for successor in successors
        )
    }
    unchanged_member_ids = {
        member.member_subject_id
        for predecessor in ordered_predecessors
        if predecessor.group_hypothesis_id in unchanged_predecessor_ids
        for member in predecessor.member_subjects
    }
    for predecessor in ordered_predecessors:
        predecessor_member_id_set = {
            member.member_subject_id for member in predecessor.member_subjects
        }
        for successor in successors:
            shared = tuple(
                member.member_subject_id
                for member in successor.member_subjects
                if member.member_subject_id in predecessor_member_id_set
            )
            if shared and (
                predecessor.group_hypothesis_id
                != successor.group_hypothesis_id
            ):
                lineage.append(
                    CrossSampleLineageEdge(
                        processing_request_id=request_id,
                        predecessor_group_hypothesis_id=(
                            predecessor.group_hypothesis_id
                        ),
                        successor_group_hypothesis_id=successor.group_hypothesis_id,
                        predecessor_member_subjects=predecessor.member_subjects,
                        member_subject_ids=shared,
                    )
                )
    if {member_id for edge in lineage for member_id in edge.member_subject_ids} != set(
        member.member_subject_id
        for member in predecessor_members
        if member.member_subject_id not in removed_ids
        and member.member_subject_id not in unchanged_member_ids
    ):
        raise CrossSampleContractError("successor lineage is incomplete")
    validated_lineage = validate_cross_sample_lineage_edges(
        lineage,
        predecessors=ordered_predecessors,
        successors=successors,
    )
    try:
        validate_cross_sample_group_population((*unaffected_population, *successors))
    except CrossSampleContractError as exc:
        raise CrossSampleContractError(
            "successor population collides with an unaffected group"
        ) from exc
    return CrossSampleSuccessorApplication(
        successors=successors,
        lineage=validated_lineage,
    )


def apply_group_disposition(
    group: CrossSampleGroupIdentity,
    *,
    disposition: CrossSampleGroupDisposition,
) -> CrossSampleGroupIdentity:
    """Keep canonical identity and lineage unchanged for a disposition-only update."""

    if not isinstance(group, CrossSampleGroupIdentity):
        raise CrossSampleContractError("group must be a canonical CrossSample group")
    return replace(group, disposition=disposition)


def mint_cross_sample_consolidation_successor(
    *,
    predecessors: Sequence[CrossSampleGroupIdentity],
    successor_members: Sequence[CrossSampleGroupMemberSubject],
    edges: Sequence[CrossSampleNeighborhoodEdge],
    budget: CrossSampleNeighborhoodBudget,
    population: Sequence[CrossSampleGroupIdentity],
) -> CrossSampleSuccessorApplication:
    """Apply one complete owner-selected consolidation membership projection.

    Consolidators may select the already-existing winning observation per
    sample, but cannot retain a predecessor digest once that membership changes.
    """

    canonical_predecessors = tuple(
        sorted(predecessors, key=lambda group: group.group_hypothesis_id)
    )
    complete_population = tuple(population)
    validate_cross_sample_group_population(complete_population)
    if not canonical_predecessors:
        raise CrossSampleContractError("consolidation needs predecessor groups")
    request_ids = {group.processing_request_id for group in canonical_predecessors}
    if len(request_ids) != 1:
        raise CrossSampleContractError("consolidation predecessors crossed request")
    request_id = next(iter(request_ids))
    predecessor_members = tuple(
        member for group in canonical_predecessors for member in group.member_subjects
    )
    successor_ids = {member.member_subject_id for member in successor_members}
    removed_members = tuple(
        member
        for member in predecessor_members
        if member.member_subject_id not in successor_ids
    )
    added_members = tuple(
        member
        for member in successor_members
        if member.member_subject_id
        not in {predecessor.member_subject_id for predecessor in predecessor_members}
    )
    neighborhood = build_bounded_connected_neighborhood(
        member_subjects=(*predecessor_members, *added_members),
        edges=edges,
        budget=budget,
    )
    if neighborhood.status == "unassessable":
        return CrossSampleSuccessorApplication(
            successors=(),
            lineage=(),
            status="unassessable",
            reason=neighborhood.reason,
        )
    component_by_member = {
        member_id: index
        for index, component in enumerate(neighborhood.connected_components)
        for member_id in component
    }
    successor_component_ids = {
        component_by_member[member.member_subject_id] for member in successor_members
    }
    if len(successor_component_ids) != 1:
        return CrossSampleSuccessorApplication(
            successors=(),
            lineage=(),
            status="unassessable",
            reason="successor_spans_disconnected_eligible_components",
        )
    return apply_coupled_successor_proposal(
        predecessors=canonical_predecessors,
        proposal=CrossSampleSuccessorProposal(
            processing_request_id=request_id,
            predecessor_group_ids=tuple(
                group.group_hypothesis_id for group in canonical_predecessors
            ),
            successor_member_sets=(tuple(successor_members),),
            membership_closure=CrossSampleMembershipClosure(
                added_members=added_members,
                removed_members=removed_members,
            ),
        ),
        neighborhood=neighborhood,
        population=complete_population,
    )


def mint_cross_sample_consolidation_partition(
    *,
    predecessors: Sequence[CrossSampleGroupIdentity],
    successor_member_sets: Sequence[Sequence[CrossSampleGroupMemberSubject]],
    edges: Sequence[CrossSampleNeighborhoodEdge],
    budget: CrossSampleNeighborhoodBudget,
    population: Sequence[CrossSampleGroupIdentity],
) -> CrossSampleSuccessorApplication:
    """Atomically apply one complete successor partition of a full component."""

    canonical_predecessors = tuple(
        sorted(predecessors, key=lambda group: group.group_hypothesis_id)
    )
    complete_population = tuple(population)
    validate_cross_sample_group_population(complete_population)
    if not canonical_predecessors:
        raise CrossSampleContractError("consolidation partition needs predecessors")
    request_ids = {group.processing_request_id for group in canonical_predecessors}
    if len(request_ids) != 1:
        raise CrossSampleContractError("consolidation partition crossed request")
    request_id = next(iter(request_ids))
    canonical_successor_sets = tuple(
        tuple(member_set) for member_set in successor_member_sets
    )
    predecessor_members = tuple(
        member for group in canonical_predecessors for member in group.member_subjects
    )
    successor_members = tuple(
        member for member_set in canonical_successor_sets for member in member_set
    )
    predecessor_by_id = {
        member.member_subject_id: member for member in predecessor_members
    }
    successor_ids = {member.member_subject_id for member in successor_members}
    added_members = tuple(
        member
        for member in successor_members
        if member.member_subject_id not in predecessor_by_id
    )
    removed_members = tuple(
        member
        for member in predecessor_members
        if member.member_subject_id not in successor_ids
    )
    neighborhood = build_bounded_connected_neighborhood(
        member_subjects=(*predecessor_members, *added_members),
        edges=edges,
        budget=budget,
    )
    if neighborhood.status == "unassessable":
        return CrossSampleSuccessorApplication(
            successors=(),
            lineage=(),
            status="unassessable",
            reason=neighborhood.reason,
        )
    return apply_coupled_successor_proposal(
        predecessors=canonical_predecessors,
        proposal=CrossSampleSuccessorProposal(
            processing_request_id=request_id,
            predecessor_group_ids=tuple(
                group.group_hypothesis_id for group in canonical_predecessors
            ),
            successor_member_sets=canonical_successor_sets,
            membership_closure=CrossSampleMembershipClosure(
                added_members=added_members,
                removed_members=removed_members,
            ),
        ),
        neighborhood=neighborhood,
        population=complete_population,
    )


def validate_cross_sample_successor_owner_projection(
    *,
    successor: CrossSampleGroupIdentity,
    owners: Sequence[SampleLocalMS1Owner],
) -> tuple[SampleLocalMS1Owner, ...]:
    """Allow a facade to flatten owners only when they exactly project a successor."""

    if not isinstance(successor, CrossSampleGroupIdentity):
        raise CrossSampleContractError("successor owner projection needs identity")
    projected = tuple(owners)
    subjects = tuple(owner.cross_sample_member_subject for owner in projected)
    if any(subject is None for subject in subjects):
        raise CrossSampleContractError(
            "successor owner projection lost a canonical member subject"
        )
    typed_subjects = cast(tuple[CrossSampleGroupMemberSubject, ...], subjects)
    if tuple(sorted(member.member_subject_id for member in typed_subjects)) != tuple(
        sorted(member.member_subject_id for member in successor.member_subjects)
    ):
        raise CrossSampleContractError(
            "successor owner projection does not match exact member closure"
        )
    if any(
        member.processing_request_id != successor.processing_request_id
        for member in typed_subjects
    ):
        raise CrossSampleContractError(
            "successor owner projection crossed processing request"
        )
    return projected


class OwnerEdgeEvaluator(Protocol):
    def __call__(
        self,
        left: SampleLocalMS1Owner,
        right: SampleLocalMS1Owner,
        *,
        config: AlignmentConfig,
        drift_lookup: DriftLookupProtocol | None = None,
    ) -> OwnerEdgeEvidence: ...


@dataclass(frozen=True)
class CrossSamplePeakGroupHypothesis:
    """Cross-sample chemical-entity group hypothesis.

    Identity semantics:
    - ``group_hypothesis_id``: primary identity key for pipeline decisions.
    - ``public_family_id``: display label for output compatibility.
      It is intentionally independent of ``group_hypothesis_id``.
    - ``feature_family_id`` property: legacy output alias for ``public_family_id``.
    """

    group_hypothesis_id: str
    public_family_id: str
    owner_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    event_member_count: int
    processing_request_id: str = ""
    member_subjects: tuple[CrossSampleGroupMemberSubject, ...] = ()
    identity_basis_bytes: bytes = b""
    disposition: CrossSampleGroupDisposition = "established"
    lineage: tuple[CrossSampleLineageEdge, ...] = ()
    source: str = "owner_aligned_feature_shadow"
    neutral_loss_tag: str = ""
    group_center_mz: float | None = None
    group_center_rt: float | None = None
    group_product_mz: float | None = None
    group_observed_neutral_loss_da: float | None = None
    targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None
    has_anchor: bool = False
    owners: tuple[SampleLocalMS1Owner, ...] = ()
    evidence: str = ""
    identity_conflict: bool = False
    review_only: bool = False
    confirm_local_owners_with_backfill: bool = False
    backfill_seed_centers: tuple[tuple[float, float], ...] = ()
    ambiguous_sample_stem: str | None = None
    ambiguous_candidate_ids: tuple[str, ...] = ()
    construction_role: CrossSamplePeakGroupConstructionRole = (
        "owner_aligned_feature_shadow"
    )
    consolidation_state: str = "not_consolidated"
    consolidation_winner_group_hypothesis_id: str = ""
    consolidation_source_group_hypothesis_id: str = ""
    @property
    def feature_family_id(self) -> str:
        return self.public_family_id

    @property
    def cluster_id(self) -> str:
        return self.public_family_id

    @property
    def family_center_mz(self) -> float:
        if self.group_center_mz is None:
            raise ValueError("cross-sample peak group is missing center m/z")
        return self.group_center_mz

    @property
    def family_center_rt(self) -> float:
        if self.group_center_rt is None:
            raise ValueError("cross-sample peak group is missing center RT")
        return self.group_center_rt

    @property
    def family_product_mz(self) -> float:
        if self.group_product_mz is None:
            raise ValueError("cross-sample peak group is missing product m/z")
        return self.group_product_mz

    @property
    def family_observed_neutral_loss_da(self) -> float:
        if self.group_observed_neutral_loss_da is None:
            raise ValueError("cross-sample peak group is missing observed neutral loss")
        return self.group_observed_neutral_loss_da

    @property
    def members(self) -> tuple[SampleLocalMS1Owner, ...]:
        return self.owners

    @property
    def event_cluster_ids(self) -> tuple[str, ...]:
        return self.owner_ids

    @property
    def group_construction_role(self) -> str:
        if self.construction_role == "successor_constructor":
            return "successor_constructor"
        return "successor_projection_adapter"

    @property
    def group_delivery_role(self) -> str:
        return "successor_delivery_protocol"

    @property
    def group_membership_source(self) -> str:
        return "cross_sample_peak_group_hypothesis"


def validate_cross_sample_peak_group_identity(
    group: CrossSamplePeakGroupHypothesis,
    *,
    require_canonical: bool = False,
) -> None:
    """Replay a retained canonical CrossSample identity without projection data.

    Display-only carriers are accepted only by explicit render adapters.  Any
    scientific carrier must require a replayable canonical identity.
    """

    if not isinstance(group, CrossSamplePeakGroupHypothesis):
        raise CrossSampleContractError("group must be a CrossSample hypothesis")
    has_identity = bool(
        group.processing_request_id
        or group.member_subjects
        or group.identity_basis_bytes
    )
    if not has_identity:
        if require_canonical:
            raise CrossSampleContractError(
                "scientific CrossSample carrier is missing canonical identity fields"
            )
        return
    if not (
        group.processing_request_id
        and group.member_subjects
        and group.identity_basis_bytes
    ):
        raise CrossSampleContractError(
            "canonical CrossSample carrier has incomplete identity fields"
        )
    identity = CrossSampleGroupIdentity(
        group_hypothesis_id=group.group_hypothesis_id,
        processing_request_id=group.processing_request_id,
        member_subjects=group.member_subjects,
        basis_bytes=group.identity_basis_bytes,
        disposition=group.disposition,
    )
    if (
        identity.group_hypothesis_id != group.group_hypothesis_id
        or identity.member_subjects != group.member_subjects
        or identity.basis_bytes != group.identity_basis_bytes
        or identity.disposition != group.disposition
    ):
        raise CrossSampleContractError(
            "canonical CrossSample carrier identity replay changed"
        )
    validate_cross_sample_lineage_carrier(
        group.lineage,
        current_successor=identity,
    )


def cross_sample_group_identity_from_carrier(
    carrier: object,
) -> CrossSampleGroupIdentity:
    """Recover the exact canonical identity retained by an owner facade.

    Consolidation may project the result into several legacy row types, but it
    must never invent identity from display labels or numeric centers.
    """

    if getattr(carrier, "cross_sample_state", "active") != "active":
        raise CrossSampleContractError(
            "render-only carrier cannot re-enter CrossSample identity decisions"
        )
    try:
        identity = CrossSampleGroupIdentity(
            group_hypothesis_id=getattr(carrier, "group_hypothesis_id"),
            processing_request_id=getattr(carrier, "processing_request_id"),
            member_subjects=getattr(carrier, "member_subjects"),
            basis_bytes=getattr(carrier, "identity_basis_bytes"),
            disposition=getattr(carrier, "disposition"),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise CrossSampleContractError(
            "CrossSample consolidation requires a retained canonical identity"
        ) from exc
    return identity


@dataclass(frozen=True)
class _GroupHardGateEnvelope:
    neutral_loss_tag: str
    sample_stems: frozenset[str]
    precursor_mz_min: float
    precursor_mz_max: float
    product_mz_min: float
    product_mz_max: float
    observed_loss_min: float
    observed_loss_max: float


@dataclass(frozen=True, slots=True)
class _TargetedIstdFamilyView:
    neutral_loss_tag: str
    family_center_mz: float
    family_center_rt: float


def construct_cross_sample_peak_group_hypotheses(
    owners: tuple[SampleLocalMS1Owner, ...] | list[SampleLocalMS1Owner],
    *,
    config: AlignmentConfig,
    sample_universe: frozenset[str] | None = None,
    injection_order: Mapping[str, int] | None = None,
    drift_lookup: DriftLookupProtocol | None = None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None = None,
    edge_evaluator: OwnerEdgeEvaluator = evaluate_owner_edge,
    processing_request_id: str | None = None,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    if not owners:
        return ()
    request_id = _cross_sample_request_id_for_owners(
        owners,
        processing_request_id=processing_request_id,
    )
    targeted_istd_anchor = getattr(drift_lookup, "istd_identity_anchor", None)
    if not isinstance(targeted_istd_anchor, TargetedIstdIdentityAnchor):
        targeted_istd_anchor = None
    clean = tuple(owner for owner in owners if not owner.identity_conflict)
    conflict_hypotheses = tuple(
        _hypothesis_from_owner_group(
            (owner,),
            group_identity=_group_identity_from_owners(
                (owner,),
                processing_request_id=request_id,
                disposition="unresolved",
            ),
            public_family_id=f"FAM{index:06d}",
            evidence="identity_conflict_review_only",
            identity_conflict=True,
            review_only=True,
            targeted_istd_anchor=targeted_istd_anchor,
        )
        for index, owner in enumerate(
            sorted(
                (owner for owner in owners if owner.identity_conflict),
                key=_owner_sort_key,
            ),
            start=1,
        )
    )
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence] = {}
    full_batch_continuity_owner_sets: frozenset[frozenset[str]] = frozenset()
    injection_trajectory_owner_sets: frozenset[frozenset[str]] = frozenset()
    bounded_continuity_owner_sets: frozenset[frozenset[str]] = frozenset()
    targeted_istd_trajectory_owner_sets: frozenset[frozenset[str]] = frozenset()
    validated_targeted_istd_owner_sets: frozenset[frozenset[str]] = frozenset()
    if sample_universe is None:
        groups = _complete_link_groups(
            sorted(clean, key=_owner_sort_key),
            config,
            drift_lookup=drift_lookup,
            edge_evidence_sink=edge_evidence_sink,
            edge_cache=edge_cache,
            edge_evaluator=edge_evaluator,
        )
    else:
        if any(owner.sample_stem not in sample_universe for owner in clean):
            raise ValueError("sample_universe omits an owner sample")
        groups = _connected_link_groups(
            sorted(clean, key=_owner_rt_sort_key),
            config,
            drift_lookup=drift_lookup,
            edge_evidence_sink=edge_evidence_sink,
            edge_cache=edge_cache,
            edge_evaluator=edge_evaluator,
        )
        (
            groups,
            full_batch_continuity_owner_sets,
            injection_trajectory_owner_sets,
            bounded_continuity_owner_sets,
        ) = _restore_sparse_complete_link_groups(
            groups,
            sample_universe=sample_universe,
            injection_order=injection_order,
            config=config,
            drift_lookup=drift_lookup,
            edge_evidence_sink=edge_evidence_sink,
            edge_cache=edge_cache,
            edge_evaluator=edge_evaluator,
        )
        (
            groups,
            targeted_istd_trajectory_owner_sets,
            validated_targeted_istd_owner_sets,
        ) = _bind_targeted_istd_trajectory_provenance(
            groups,
            injection_trajectory_owner_sets=injection_trajectory_owner_sets,
            injection_order=injection_order,
            config=config,
            drift_lookup=drift_lookup,
            edge_evidence_sink=edge_evidence_sink,
            edge_cache=edge_cache,
            edge_evaluator=edge_evaluator,
        )
    resolved_duplicate_owner_groups = (
        _resolve_duplicate_owner_groups(
            groups,
            config=config,
            targeted_istd_anchor=targeted_istd_anchor,
            validated_targeted_istd_owner_sets=validated_targeted_istd_owner_sets,
        )
        if sample_universe is not None
        else {}
    )
    public_group_ids = tuple(
        f"FAM{index + len(conflict_hypotheses):06d}"
        for index in range(1, len(groups) + 1)
    )
    group_identities = tuple(
        _group_identity_from_owners(
            tuple(group),
            processing_request_id=request_id,
            disposition="unresolved"
            if index in resolved_duplicate_owner_groups
            else None,
        )
        for index, group in enumerate(groups)
    )
    validate_cross_sample_group_population(
        (*(
            _group_identity_from_owners(
                (owner,),
                processing_request_id=request_id,
                disposition="unresolved",
            )
            for owner in owners
            if owner.identity_conflict
        ), *group_identities)
    )
    resolved_group_ids_by_winner: dict[int, list[str]] = {}
    for subordinate, winner in sorted(resolved_duplicate_owner_groups.items()):
        resolved_group_ids_by_winner.setdefault(winner, []).append(
            group_identities[subordinate].group_hypothesis_id,
        )
    clean_hypotheses: list[CrossSamplePeakGroupHypothesis] = []
    for index, group in enumerate(groups):
        group_identity = group_identities[index]
        public_group_id = public_group_ids[index]
        owner_id_set = frozenset(owner.owner_id for owner in group)
        evidence = _group_evidence(
            group,
            full_batch_continuity=(owner_id_set in full_batch_continuity_owner_sets),
            injection_trajectory=(owner_id_set in injection_trajectory_owner_sets),
            bounded_continuity=(owner_id_set in bounded_continuity_owner_sets),
            targeted_istd_trajectory=(
                owner_id_set in targeted_istd_trajectory_owner_sets
            ),
        )
        winner_index = resolved_duplicate_owner_groups.get(index)
        if winner_index is not None:
            winner_id = public_group_ids[winner_index]
            winner_group_id = group_identities[winner_index].group_hypothesis_id
            clean_hypotheses.append(
                _hypothesis_from_owner_group(
                    tuple(group),
                    group_identity=group_identity,
                    public_family_id=public_group_id,
                    evidence=(
                        f"{evidence};{_DUPLICATE_OWNER_GROUP_POLICY}_loser;"
                        f"winner={winner_id}"
                    ),
                    review_only=True,
                    consolidation_state="primary_loser",
                    consolidation_winner_group_hypothesis_id=winner_group_id,
                    consolidation_source_group_hypothesis_id=(
                        group_identity.group_hypothesis_id
                    ),
                    targeted_istd_anchor=targeted_istd_anchor,
                ),
            )
            continue
        resolved_ids = resolved_group_ids_by_winner.get(index)
        if resolved_ids is None:
            clean_hypotheses.append(
                _hypothesis_from_owner_group(
                    tuple(group),
                    group_identity=group_identity,
                    public_family_id=public_group_id,
                    evidence=evidence,
                    targeted_istd_anchor=targeted_istd_anchor,
                ),
            )
            continue
        clean_hypotheses.append(
            _hypothesis_from_owner_group(
                tuple(group),
                group_identity=group_identity,
                public_family_id=public_group_id,
                evidence=(
                    f"{evidence};{_DUPLICATE_OWNER_GROUP_POLICY}_winner;"
                    f"resolved_group_count={len(resolved_ids)}"
                ),
                consolidation_state="primary_winner",
                consolidation_winner_group_hypothesis_id=(
                    group_identity.group_hypothesis_id
                ),
                consolidation_source_group_hypothesis_id=";".join(
                    (group_identity.group_hypothesis_id, *resolved_ids),
                ),
                targeted_istd_anchor=targeted_istd_anchor,
            ),
        )
    return (*conflict_hypotheses, *clean_hypotheses)


def _connected_link_groups(
    owners: list[SampleLocalMS1Owner],
    config: AlignmentConfig,
    *,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
) -> tuple[tuple[SampleLocalMS1Owner, ...], ...]:
    return _link_groups(
        owners,
        config,
        drift_lookup=drift_lookup,
        edge_evidence_sink=edge_evidence_sink,
        edge_cache=edge_cache,
        edge_evaluator=edge_evaluator,
        require_complete_link=False,
    )


def _complete_link_groups(
    owners: list[SampleLocalMS1Owner],
    config: AlignmentConfig,
    *,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
) -> tuple[tuple[SampleLocalMS1Owner, ...], ...]:
    return _link_groups(
        owners,
        config,
        drift_lookup=drift_lookup,
        edge_evidence_sink=edge_evidence_sink,
        edge_cache=edge_cache,
        edge_evaluator=edge_evaluator,
        require_complete_link=True,
    )


def _link_groups(
    owners: list[SampleLocalMS1Owner],
    config: AlignmentConfig,
    *,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
    require_complete_link: bool,
) -> tuple[tuple[SampleLocalMS1Owner, ...], ...]:
    groups: list[list[SampleLocalMS1Owner]] = []
    envelopes: list[_GroupHardGateEnvelope] = []
    group_indexes_by_tag: dict[str, list[int]] = {}
    group_indexes_by_precursor_bucket: dict[tuple[str, int], list[int]] = {}
    for owner in owners:
        candidate_group_indexes = _candidate_group_indexes_for_owner(
            owner,
            group_indexes_by_tag=group_indexes_by_tag,
            group_indexes_by_precursor_bucket=group_indexes_by_precursor_bucket,
            config=config,
        )
        compatible_group_indexes = [
            index
            for index in candidate_group_indexes
            for group in (groups[index],)
            if _group_accepts_owner(
                owner,
                group,
                envelope=envelopes[index],
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
                require_complete_link=require_complete_link,
            )
        ]
        if not compatible_group_indexes:
            group_index = len(groups)
            envelope = _group_envelope_for_owner(owner)
            groups.append([owner])
            envelopes.append(envelope)
            group_indexes_by_tag.setdefault(envelope.neutral_loss_tag, []).append(
                group_index,
            )
            _add_group_precursor_bucket_index(
                group_index,
                envelope,
                group_indexes_by_precursor_bucket,
            )
            continue
        # Only the connected-link pass reconciles multiple compatible groups.
        # Complete-link remains the downstream hard partition and must not
        # inherit union-find chaining from this pass.
        if not require_complete_link and len(compatible_group_indexes) > 1:
            merged_group_indexes = tuple(sorted(set(compatible_group_indexes)))
            merged_group = [owner]
            for group_index in merged_group_indexes:
                merged_group.extend(groups[group_index])
            merged_envelope = _group_envelope_for_owner(merged_group[0])
            for merged_owner in merged_group[1:]:
                merged_envelope = _extend_group_envelope(
                    merged_envelope,
                    merged_owner,
                )
            if _merged_group_passes_hard_gates(
                tuple(merged_group),
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            ):
                keep_index = merged_group_indexes[0]
                groups[keep_index] = merged_group
                envelopes[keep_index] = merged_envelope
                for group_index in reversed(merged_group_indexes[1:]):
                    groups.pop(group_index)
                    envelopes.pop(group_index)
                _rebuild_group_indexes(
                    envelopes,
                    group_indexes_by_tag=group_indexes_by_tag,
                    group_indexes_by_precursor_bucket=group_indexes_by_precursor_bucket,
                )
                continue
        best_index = min(
            compatible_group_indexes,
            key=lambda index: _group_match_score(owner, groups[index], config),
        )
        groups[best_index].append(owner)
        envelopes[best_index] = _extend_group_envelope(envelopes[best_index], owner)
        _add_group_precursor_bucket_index(
            best_index,
            envelopes[best_index],
            group_indexes_by_precursor_bucket,
        )
    return tuple(tuple(group) for group in groups)


def _rebuild_group_indexes(
    envelopes: list[_GroupHardGateEnvelope],
    *,
    group_indexes_by_tag: dict[str, list[int]],
    group_indexes_by_precursor_bucket: dict[tuple[str, int], list[int]],
) -> None:
    group_indexes_by_tag.clear()
    group_indexes_by_precursor_bucket.clear()
    for group_index, envelope in enumerate(envelopes):
        group_indexes_by_tag.setdefault(envelope.neutral_loss_tag, []).append(
            group_index,
        )
        _add_group_precursor_bucket_index(
            group_index,
            envelope,
            group_indexes_by_precursor_bucket,
        )


def _merged_group_passes_hard_gates(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
) -> bool:
    if not _group_passes_hard_envelope(group, config=config):
        return False
    for left_index, left in enumerate(group):
        for right in group[left_index + 1 :]:
            edge = _edge_for_pair(
                left,
                right,
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            )
            if (
                classify_cross_sample_edge(
                    edge.decision,
                    context="initial_grouping",
                )
                == "excluded"
            ):
                return False
    return True


def _group_accepts_owner(
    owner: SampleLocalMS1Owner,
    group: list[SampleLocalMS1Owner],
    *,
    envelope: _GroupHardGateEnvelope,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
    require_complete_link: bool,
) -> bool:
    if require_complete_link:
        if not _group_can_pass_hard_gates(owner, envelope, config):
            return False
        return all(
            classify_cross_sample_edge(
                _edge_for_pair(
                    owner,
                    existing,
                    config=config,
                    drift_lookup=drift_lookup,
                    edge_evidence_sink=edge_evidence_sink,
                    edge_cache=edge_cache,
                    edge_evaluator=edge_evaluator,
                ).decision,
                context="initial_grouping",
            )
            == "eligible"
            for existing in group
        )
    if owner.sample_stem in envelope.sample_stems:
        return False
    return any(
        _is_full_batch_continuity_edge(
            _edge_for_pair(
                owner,
                existing,
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            ),
            config=config,
        )
        for existing in group
    )


def _is_full_batch_continuity_edge(
    edge: OwnerEdgeEvidence,
    *,
    config: AlignmentConfig,
) -> bool:
    continuity_eligible = (
        edge.decision == "weak_edge"
        and not edge.failure_reason
        and edge.drift_prior_source == "none"
        and edge.rt_raw_delta_sec <= config.preferred_rt_sec
        and edge.owner_quality == "clean"
        and edge.seed_support_level == "moderate"
    )
    return (
        classify_cross_sample_edge(
            edge.decision,
            context="initial_grouping",
            initial_full_batch_continuity=continuity_eligible,
        )
        == "eligible"
    )


def _restore_sparse_complete_link_groups(
    connected_groups: tuple[tuple[SampleLocalMS1Owner, ...], ...],
    *,
    sample_universe: frozenset[str],
    injection_order: Mapping[str, int] | None,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
) -> tuple[
    tuple[tuple[SampleLocalMS1Owner, ...], ...],
    frozenset[frozenset[str]],
    frozenset[frozenset[str]],
    frozenset[frozenset[str]],
]:
    groups: list[tuple[SampleLocalMS1Owner, ...]] = []
    full_batch_continuity_owner_sets: set[frozenset[str]] = set()
    injection_trajectory_owner_sets: set[frozenset[str]] = set()
    bounded_continuity_owner_sets: set[frozenset[str]] = set()
    for connected_group in connected_groups:
        group_samples = frozenset(owner.sample_stem for owner in connected_group)
        complete_groups = _complete_link_groups(
            sorted(connected_group, key=_owner_sort_key),
            config,
            drift_lookup=drift_lookup,
            edge_evidence_sink=edge_evidence_sink,
            edge_cache=edge_cache,
            edge_evaluator=edge_evaluator,
        )
        if group_samples == sample_universe and len(complete_groups) > 1:
            ordered = tuple(sorted(connected_group, key=_owner_sort_key))
            if not _merged_group_passes_hard_gates(
                ordered,
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            ):
                groups.extend(complete_groups)
                continue
            groups.append(ordered)
            full_batch_continuity_owner_sets.add(
                frozenset(owner.owner_id for owner in ordered),
            )
            continue
        if len(complete_groups) > 1 and _is_injection_trajectory(
            connected_group,
            injection_order=injection_order,
            config=config,
            drift_lookup=drift_lookup,
            edge_evidence_sink=edge_evidence_sink,
            edge_cache=edge_cache,
            edge_evaluator=edge_evaluator,
        ):
            ordered = tuple(sorted(connected_group, key=_owner_sort_key))
            groups.append(ordered)
            injection_trajectory_owner_sets.add(
                frozenset(owner.owner_id for owner in ordered),
            )
            continue
        if len(complete_groups) > 1:
            ordered = tuple(sorted(connected_group, key=_owner_sort_key))
            if _group_passes_bounded_continuity(ordered, config=config):
                groups.append(ordered)
                bounded_continuity_owner_sets.add(
                    frozenset(owner.owner_id for owner in ordered),
                )
                continue
        groups.extend(complete_groups)
    groups.sort(key=lambda group: _owner_sort_key(group[0]))
    return (
        tuple(groups),
        frozenset(full_batch_continuity_owner_sets),
        frozenset(injection_trajectory_owner_sets),
        frozenset(bounded_continuity_owner_sets),
    )


def _bind_targeted_istd_trajectory_provenance(
    groups: tuple[tuple[SampleLocalMS1Owner, ...], ...],
    *,
    injection_trajectory_owner_sets: frozenset[frozenset[str]],
    injection_order: Mapping[str, int] | None,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
) -> tuple[
    tuple[tuple[SampleLocalMS1Owner, ...], ...],
    frozenset[frozenset[str]],
    frozenset[frozenset[str]],
]:
    """Bind typed ISTD provenance and reconcile bounded anchor trajectories."""

    anchor = getattr(drift_lookup, "istd_identity_anchor", None)
    if injection_order is None or not isinstance(anchor, TargetedIstdIdentityAnchor):
        return groups, frozenset(), frozenset()

    passthrough: list[tuple[SampleLocalMS1Owner, ...]] = []
    groups_by_target: dict[
        str,
        list[tuple[tuple[SampleLocalMS1Owner, ...], Target]],
    ] = {}
    for group in groups:
        target = _targeted_istd_target_for_group(group, anchor=anchor)
        if target is None:
            passthrough.append(group)
            continue
        groups_by_target.setdefault(str(getattr(target, "label")), []).append(
            (group, target)
        )

    targeted_owner_sets: set[frozenset[str]] = set()
    validated_owner_sets: set[frozenset[str]] = set()
    reconciled = list(passthrough)
    for target_groups in groups_by_target.values():
        tracks: list[tuple[tuple[SampleLocalMS1Owner, ...], Target, int]] = []
        ordered_target_groups = sorted(
            target_groups,
            key=lambda item: (-len(item[0]), _owner_sort_key(item[0][0])),
        )
        sample_sets = [
            frozenset(owner.sample_stem for owner in group)
            for group, _target in ordered_target_groups
        ]
        subordinate_indexes = {
            index
            for index, samples in enumerate(sample_sets)
            if any(samples < other_samples for other_samples in sample_sets)
        }
        adjacency: list[set[int]] = [set() for _ in ordered_target_groups]
        for left_index, (left_group, target) in enumerate(ordered_target_groups):
            for right_index in range(left_index + 1, len(ordered_target_groups)):
                if (left_index in subordinate_indexes) != (
                    right_index in subordinate_indexes
                ):
                    continue
                right_group, _right_target = ordered_target_groups[right_index]
                candidate = tuple(
                    sorted((*left_group, *right_group), key=_owner_sort_key)
                )
                if _targeted_istd_hard_union_is_valid(
                    candidate,
                    target=target,
                    anchor=anchor,
                    injection_order=injection_order,
                    config=config,
                ) and _targeted_istd_groups_share_local_continuity(
                    left_group,
                    right_group,
                    config=config,
                    drift_lookup=drift_lookup,
                    edge_evidence_sink=edge_evidence_sink,
                    edge_cache=edge_cache,
                    edge_evaluator=edge_evaluator,
                ):
                    adjacency[left_index].add(right_index)
                    adjacency[right_index].add(left_index)

        remaining = set(range(len(ordered_target_groups)))
        while remaining:
            start = min(remaining)
            component: set[int] = set()
            pending = [start]
            while pending:
                index = pending.pop()
                if index in component:
                    continue
                component.add(index)
                pending.extend(sorted(adjacency[index] - component, reverse=True))
            remaining.difference_update(component)
            members = [ordered_target_groups[index] for index in sorted(component)]
            candidate = tuple(
                sorted(
                    (owner for group, _target in members for owner in group),
                    key=_owner_sort_key,
                )
            )
            target = members[0][1]
            if len(members) > 1 and _targeted_istd_reconciliation_is_valid(
                candidate,
                target=target,
                anchor=anchor,
                injection_order=injection_order,
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            ):
                tracks.append((candidate, target, len(members)))
                continue
            tracks.extend((group, group_target, 1) for group, group_target in members)

        for track, _target, source_count in tracks:
            reconciled.append(track)
            owner_set = frozenset(owner.owner_id for owner in track)
            if _targeted_istd_reconciliation_is_valid(
                track,
                target=_target,
                anchor=anchor,
                injection_order=injection_order,
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            ):
                validated_owner_sets.add(owner_set)
            if source_count > 1 or (
                owner_set in injection_trajectory_owner_sets
                and _targeted_istd_candidate_is_valid(
                    track,
                    injection_order=injection_order,
                    config=config,
                    drift_lookup=drift_lookup,
                    edge_evidence_sink=edge_evidence_sink,
                    edge_cache=edge_cache,
                    edge_evaluator=edge_evaluator,
                )
            ):
                targeted_owner_sets.add(owner_set)
    reconciled.sort(key=lambda group: _owner_sort_key(group[0]))
    return (
        tuple(reconciled),
        frozenset(targeted_owner_sets),
        frozenset(validated_owner_sets),
    )


def _targeted_istd_target_for_group(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    anchor: TargetedIstdIdentityAnchor,
) -> Target | None:
    if not group:
        return None
    view = _TargetedIstdFamilyView(
        neutral_loss_tag=group[0].neutral_loss_tag,
        family_center_mz=sum(owner.precursor_mz for owner in group) / len(group),
        family_center_rt=sum(owner.owner_apex_rt for owner in group) / len(group),
    )
    target = anchor.matching_target(view)
    if target is None or not all(
        anchor.matches_observation(
            target,
            neutral_loss_tag=owner.neutral_loss_tag,
            precursor_mz=owner.precursor_mz,
            apex_rt=owner.owner_apex_rt,
        )
        for owner in group
    ):
        return None
    return target


def _targeted_istd_reconciliation_is_valid(
    candidate: tuple[SampleLocalMS1Owner, ...],
    *,
    target: Target,
    anchor: TargetedIstdIdentityAnchor,
    injection_order: Mapping[str, int],
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
) -> bool:
    if not _targeted_istd_hard_union_is_valid(
        candidate,
        target=target,
        anchor=anchor,
        injection_order=injection_order,
        config=config,
    ):
        return False
    ordered = _owners_in_injection_order(
        candidate,
        injection_order=injection_order,
    )
    assert ordered is not None
    return all(
        _targeted_istd_continuity_edge(
            _edge_for_pair(
                left,
                right,
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            ),
            config=config,
        )
        for left, right in zip(ordered, ordered[1:])
    )


def _targeted_istd_hard_union_is_valid(
    candidate: tuple[SampleLocalMS1Owner, ...],
    *,
    target: Target,
    anchor: TargetedIstdIdentityAnchor,
    injection_order: Mapping[str, int],
    config: AlignmentConfig,
) -> bool:
    if (
        len({owner.sample_stem for owner in candidate}) != len(candidate)
        or not candidate
        or not candidate[0].neutral_loss_tag
        or any(
            owner.neutral_loss_tag != candidate[0].neutral_loss_tag
            for owner in candidate[1:]
        )
        or any(
            not anchor.matches_observation(
                target,
                neutral_loss_tag=owner.neutral_loss_tag,
                precursor_mz=owner.precursor_mz,
                apex_rt=owner.owner_apex_rt,
            )
            for owner in candidate
        )
    ):
        return False
    if not _targeted_istd_precursor_envelope_is_valid(candidate, config=config):
        return False
    return (
        _owners_in_injection_order(
            candidate,
            injection_order=injection_order,
        )
        is not None
    )


def _targeted_istd_groups_share_local_continuity(
    left_group: tuple[SampleLocalMS1Owner, ...],
    right_group: tuple[SampleLocalMS1Owner, ...],
    *,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
) -> bool:
    return any(
        _targeted_istd_continuity_edge(
            _edge_for_pair(
                left,
                right,
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            ),
            config=config,
        )
        for left in left_group
        for right in right_group
    )


def _targeted_istd_precursor_envelope_is_valid(
    owners: tuple[SampleLocalMS1Owner, ...],
    *,
    config: AlignmentConfig,
) -> bool:
    precursor_min = min(owner.precursor_mz for owner in owners)
    precursor_max = max(owner.precursor_mz for owner in owners)
    return all(
        not _range_exceeds_ppm(
            owner.precursor_mz,
            precursor_min,
            precursor_max,
            config.max_ppm,
        )
        for owner in owners
    )


def _targeted_istd_continuity_edge(
    edge: OwnerEdgeEvidence,
    *,
    config: AlignmentConfig,
) -> bool:
    if _is_full_batch_continuity_edge(edge, config=config):
        return True
    if edge.failure_reason and edge.failure_reason not in {
        "product_mz_out_of_tolerance",
        "observed_loss_out_of_tolerance",
    }:
        return False
    if (
        edge.owner_quality in {"weak", "ambiguous_nearby"}
        or edge.seed_support_level == "weak"
    ):
        return False
    rt_delta_sec = edge.rt_raw_delta_sec
    if (
        edge.drift_prior_source != "none"
        and edge.rt_drift_corrected_delta_sec is not None
    ):
        rt_delta_sec = edge.rt_drift_corrected_delta_sec
    return rt_delta_sec <= config.preferred_rt_sec


def _targeted_istd_candidate_is_valid(
    candidate: tuple[SampleLocalMS1Owner, ...],
    *,
    injection_order: Mapping[str, int],
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
) -> bool:
    if len({owner.sample_stem for owner in candidate}) != len(candidate):
        return False
    return _merged_group_passes_hard_gates(
        candidate,
        config=config,
        drift_lookup=drift_lookup,
        edge_evidence_sink=edge_evidence_sink,
        edge_cache=edge_cache,
        edge_evaluator=edge_evaluator,
    ) and _is_injection_trajectory(
        candidate,
        injection_order=injection_order,
        config=config,
        drift_lookup=drift_lookup,
        edge_evidence_sink=edge_evidence_sink,
        edge_cache=edge_cache,
        edge_evaluator=edge_evaluator,
    )


def _group_passes_bounded_continuity(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    config: AlignmentConfig,
) -> bool:
    """Keep a component whole only after higher-priority trajectory rescues fail.

    Full-batch and arbitrary partial injection trajectories run first. This
    fallback instead requires the symmetric precursor/product/loss hard envelope
    and the bounded apex-RT span that separates one drifting compound from two
    chromatography-resolved compounds.
    """
    if not _group_passes_hard_envelope(group, config=config):
        return False
    apex_rts = [owner.owner_apex_rt for owner in group]
    span_sec = (max(apex_rts) - min(apex_rts)) * 60.0
    return span_sec <= _BOUNDED_CONTINUITY_RT_SPAN_SEC


@dataclass(frozen=True)
class _GroupIdentityFacts:
    """Group-level identity facts used to resolve duplicate-owner groups."""

    neutral_loss_tag: str
    center_mz: float
    center_rt: float
    center_product_mz: float
    center_observed_loss: float
    sample_stems: frozenset[str]
    owners: tuple[SampleLocalMS1Owner, ...]
    envelope: _GroupHardGateEnvelope


def _group_identity_facts(
    group: tuple[SampleLocalMS1Owner, ...],
) -> _GroupIdentityFacts:
    envelope = _group_envelope_for_owner(group[0])
    for owner in group[1:]:
        envelope = _extend_group_envelope(envelope, owner)
    return _GroupIdentityFacts(
        neutral_loss_tag=group[0].neutral_loss_tag,
        center_mz=sum(owner.precursor_mz for owner in group) / len(group),
        center_rt=sum(owner.owner_apex_rt for owner in group) / len(group),
        center_product_mz=sum(
            owner.primary_identity_event.product_mz for owner in group
        )
        / len(group),
        center_observed_loss=sum(
            owner.primary_identity_event.observed_neutral_loss_da for owner in group
        )
        / len(group),
        sample_stems=frozenset(owner.sample_stem for owner in group),
        owners=group,
        envelope=envelope,
    )


def _resolve_duplicate_owner_groups(
    groups: tuple[tuple[SampleLocalMS1Owner, ...], ...],
    *,
    config: AlignmentConfig,
    targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None,
    validated_targeted_istd_owner_sets: frozenset[frozenset[str]] = frozenset(),
) -> dict[int, int]:
    """Resolve groups that only compete inside samples another group owns.

    A group whose samples are all already owned by a larger, typed same-target
    trajectory cannot add sample coverage. Resolving that bounded duplicate in
    this owner keeps it out of Backfill instead of paying a full-batch request
    set for a group that Matrix consolidation demotes afterwards. Aggregate
    group-center geometry is not same-peak evidence. Resolution is one level
    deep: a group that wins is never itself resolved away.
    """
    if len(groups) < 2:
        return {}
    facts = tuple(_group_identity_facts(group) for group in groups)
    indexes_by_tag: dict[str, list[int]] = {}
    for index, fact in enumerate(facts):
        if not fact.neutral_loss_tag:
            continue
        indexes_by_tag.setdefault(fact.neutral_loss_tag, []).append(index)

    rt_window_min = config.identity_rt_candidate_window_sec / 60.0
    resolved: dict[int, int] = {}
    winners: set[int] = set()
    for tag_indexes in indexes_by_tag.values():
        ordered = sorted(
            tag_indexes,
            key=lambda index: (facts[index].center_rt, index),
        )
        position_by_index = {index: position for position, index in enumerate(ordered)}
        for subordinate in sorted(
            tag_indexes,
            key=lambda index: (
                len(facts[index].owners),
                facts[index].center_rt,
                index,
            ),
        ):
            if subordinate in winners:
                continue
            best_score: tuple[float, float, int] | None = None
            best_winner: int | None = None
            for candidate in _rt_window_group_indexes(
                ordered,
                facts=facts,
                position=position_by_index[subordinate],
                center_rt=facts[subordinate].center_rt,
                rt_window_min=rt_window_min,
            ):
                if candidate == subordinate or candidate in resolved:
                    continue
                targeted_match = (
                    targeted_istd_anchor is not None
                    and frozenset(owner.owner_id for owner in facts[candidate].owners)
                    in validated_targeted_istd_owner_sets
                    and _targeted_istd_group_resolves_duplicate_owner_group(
                        facts[candidate],
                        facts[subordinate],
                        anchor=targeted_istd_anchor,
                        config=config,
                    )
                )
                if not targeted_match:
                    continue
                score = (
                    _ppm(facts[subordinate].center_mz, facts[candidate].center_mz),
                    abs(facts[subordinate].center_rt - facts[candidate].center_rt),
                    candidate,
                )
                if best_score is None or score < best_score:
                    best_score = score
                    best_winner = candidate
            if best_winner is not None:
                resolved[subordinate] = best_winner
                winners.add(best_winner)
    return resolved


def _rt_window_group_indexes(
    ordered: list[int],
    *,
    facts: tuple[_GroupIdentityFacts, ...],
    position: int,
    center_rt: float,
    rt_window_min: float,
) -> Iterable[int]:
    left = position - 1
    while left >= 0 and center_rt - facts[ordered[left]].center_rt <= rt_window_min:
        yield ordered[left]
        left -= 1
    right = position + 1
    while (
        right < len(ordered)
        and facts[ordered[right]].center_rt - center_rt <= rt_window_min
    ):
        yield ordered[right]
        right += 1


def _targeted_istd_group_resolves_duplicate_owner_group(
    winner: _GroupIdentityFacts,
    subordinate: _GroupIdentityFacts,
    *,
    anchor: TargetedIstdIdentityAnchor,
    config: AlignmentConfig,
) -> bool:
    if len(winner.owners) < 2 or len(winner.owners) <= len(subordinate.owners):
        return False
    if winner.neutral_loss_tag != subordinate.neutral_loss_tag:
        return False
    if not subordinate.sample_stems <= winner.sample_stems:
        return False
    if (
        abs(subordinate.center_rt - winner.center_rt) * 60.0
        > config.identity_rt_candidate_window_sec
    ):
        return False
    winner_target = _targeted_istd_target_for_group(winner.owners, anchor=anchor)
    subordinate_target = _targeted_istd_target_for_group(
        subordinate.owners,
        anchor=anchor,
    )
    if (
        winner_target is None
        or subordinate_target is None
        or winner_target.label != subordinate_target.label
    ):
        return False
    return _targeted_istd_precursor_envelope_is_valid(
        (*winner.owners, *subordinate.owners),
        config=config,
    )


def _is_injection_trajectory(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    injection_order: Mapping[str, int] | None,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
) -> bool:
    if not _group_passes_hard_envelope(group, config=config):
        return False
    ordered = _owners_in_injection_order(
        group,
        injection_order=injection_order,
    )
    if ordered is None:
        return False
    return all(
        _is_full_batch_continuity_edge(
            _edge_for_pair(
                left,
                right,
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            ),
            config=config,
        )
        for left, right in zip(ordered, ordered[1:])
    )


def _group_passes_hard_envelope(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    config: AlignmentConfig,
) -> bool:
    if (
        len({owner.sample_stem for owner in group}) != len(group)
        or not group[0].neutral_loss_tag
        or any(
            owner.neutral_loss_tag != group[0].neutral_loss_tag for owner in group[1:]
        )
    ):
        return False
    envelope = _group_envelope_for_owner(group[0])
    for owner in group[1:]:
        envelope = _extend_group_envelope(envelope, owner)
    return all(
        not _range_exceeds_ppm(
            owner.precursor_mz,
            envelope.precursor_mz_min,
            envelope.precursor_mz_max,
            config.max_ppm,
        )
        and not _range_exceeds_ppm(
            owner.primary_identity_event.product_mz,
            envelope.product_mz_min,
            envelope.product_mz_max,
            config.product_mz_tolerance_ppm,
        )
        and not _range_exceeds_ppm(
            owner.primary_identity_event.observed_neutral_loss_da,
            envelope.observed_loss_min,
            envelope.observed_loss_max,
            config.observed_loss_tolerance_ppm,
        )
        for owner in group
    )


def _owners_in_injection_order(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    injection_order: Mapping[str, int] | None,
) -> tuple[SampleLocalMS1Owner, ...] | None:
    if injection_order is None:
        return None
    orders: list[tuple[int, SampleLocalMS1Owner]] = []
    for owner in group:
        order = injection_order.get(owner.sample_stem)
        if order is None:
            return None
        orders.append((order, owner))
    if len({order for order, _ in orders}) != len(orders):
        return None
    return tuple(owner for _, owner in sorted(orders, key=lambda item: item[0]))


def _candidate_group_indexes_for_owner(
    owner: SampleLocalMS1Owner,
    *,
    group_indexes_by_tag: dict[str, list[int]],
    group_indexes_by_precursor_bucket: dict[tuple[str, int], list[int]],
    config: AlignmentConfig,
) -> tuple[int, ...]:
    if not owner.neutral_loss_tag:
        return ()
    tag_group_indexes = group_indexes_by_tag.get(owner.neutral_loss_tag, [])
    if (
        not tag_group_indexes
        or not math.isfinite(owner.precursor_mz)
        or owner.precursor_mz <= 0.0
        or config.max_ppm < 0.0
    ):
        return tuple(tag_group_indexes)

    tolerance_fraction = config.max_ppm / 1_000_000.0
    lower = owner.precursor_mz * (1.0 - tolerance_fraction)
    upper = owner.precursor_mz * (1.0 + tolerance_fraction)
    if lower > upper:
        lower, upper = upper, lower

    candidates: set[int] = set()
    tag = owner.neutral_loss_tag
    for bucket in range(_precursor_bucket(lower), _precursor_bucket(upper) + 1):
        candidates.update(group_indexes_by_precursor_bucket.get((tag, bucket), ()))
    return tuple(sorted(candidates))


def _add_group_precursor_bucket_index(
    group_index: int,
    envelope: _GroupHardGateEnvelope,
    group_indexes_by_precursor_bucket: dict[tuple[str, int], list[int]],
) -> None:
    if not (
        math.isfinite(envelope.precursor_mz_min)
        and math.isfinite(envelope.precursor_mz_max)
    ):
        return
    # Owners arrive in ascending precursor-m/z order. Indexing both envelope
    # endpoints keeps the next locally compatible owner discoverable across a
    # bucket boundary without expanding a long connected chain into every
    # interior integer-Da bucket.
    buckets = {
        _precursor_bucket(envelope.precursor_mz_min),
        _precursor_bucket(envelope.precursor_mz_max),
    }
    for bucket in sorted(buckets):
        key = (envelope.neutral_loss_tag, bucket)
        bucket_indexes = group_indexes_by_precursor_bucket.setdefault(key, [])
        if group_index not in bucket_indexes:
            bucket_indexes.append(group_index)


def _precursor_bucket(value: float) -> int:
    return math.floor(value / _PRECURSOR_BUCKET_WIDTH_DA)


def _group_can_pass_hard_gates(
    owner: SampleLocalMS1Owner,
    envelope: _GroupHardGateEnvelope,
    config: AlignmentConfig,
) -> bool:
    if owner.sample_stem in envelope.sample_stems:
        return False
    return _owner_within_group_envelope(owner, envelope, config)


def _owner_within_group_envelope(
    owner: SampleLocalMS1Owner,
    envelope: _GroupHardGateEnvelope,
    config: AlignmentConfig,
) -> bool:
    if (
        not owner.neutral_loss_tag
        or owner.neutral_loss_tag != envelope.neutral_loss_tag
    ):
        return False
    precursor_denominator = max(abs(owner.precursor_mz), 1e-12)
    if (
        abs(owner.precursor_mz - envelope.precursor_mz_min)
        / precursor_denominator
        * 1_000_000.0
        > config.max_ppm
        or abs(owner.precursor_mz - envelope.precursor_mz_max)
        / precursor_denominator
        * 1_000_000.0
        > config.max_ppm
    ):
        return False

    event = owner.primary_identity_event
    product_denominator = max(abs(event.product_mz), 1e-12)
    if (
        abs(event.product_mz - envelope.product_mz_min)
        / product_denominator
        * 1_000_000.0
        > config.product_mz_tolerance_ppm
        or abs(event.product_mz - envelope.product_mz_max)
        / product_denominator
        * 1_000_000.0
        > config.product_mz_tolerance_ppm
    ):
        return False
    observed_loss_denominator = max(abs(event.observed_neutral_loss_da), 1e-12)
    return (
        abs(event.observed_neutral_loss_da - envelope.observed_loss_min)
        / observed_loss_denominator
        * 1_000_000.0
        <= config.observed_loss_tolerance_ppm
        and abs(event.observed_neutral_loss_da - envelope.observed_loss_max)
        / observed_loss_denominator
        * 1_000_000.0
        <= config.observed_loss_tolerance_ppm
    )


def _range_exceeds_ppm(
    reference: float,
    lower: float,
    upper: float,
    max_ppm: float,
) -> bool:
    denominator = max(abs(reference), 1e-12)
    if abs(reference - lower) / denominator * 1_000_000.0 > max_ppm:
        return True
    return abs(reference - upper) / denominator * 1_000_000.0 > max_ppm


def _group_envelope_for_owner(
    owner: SampleLocalMS1Owner,
) -> _GroupHardGateEnvelope:
    event = owner.primary_identity_event
    return _GroupHardGateEnvelope(
        neutral_loss_tag=owner.neutral_loss_tag,
        sample_stems=frozenset((owner.sample_stem,)),
        precursor_mz_min=owner.precursor_mz,
        precursor_mz_max=owner.precursor_mz,
        product_mz_min=event.product_mz,
        product_mz_max=event.product_mz,
        observed_loss_min=event.observed_neutral_loss_da,
        observed_loss_max=event.observed_neutral_loss_da,
    )


def _extend_group_envelope(
    envelope: _GroupHardGateEnvelope,
    owner: SampleLocalMS1Owner,
) -> _GroupHardGateEnvelope:
    event = owner.primary_identity_event
    return _GroupHardGateEnvelope(
        neutral_loss_tag=envelope.neutral_loss_tag,
        sample_stems=envelope.sample_stems | frozenset((owner.sample_stem,)),
        precursor_mz_min=min(envelope.precursor_mz_min, owner.precursor_mz),
        precursor_mz_max=max(envelope.precursor_mz_max, owner.precursor_mz),
        product_mz_min=min(envelope.product_mz_min, event.product_mz),
        product_mz_max=max(envelope.product_mz_max, event.product_mz),
        observed_loss_min=min(
            envelope.observed_loss_min,
            event.observed_neutral_loss_da,
        ),
        observed_loss_max=max(
            envelope.observed_loss_max,
            event.observed_neutral_loss_da,
        ),
    )


def _edge_for_pair(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    *,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None,
    edge_cache: dict[tuple[str, str], OwnerEdgeEvidence],
    edge_evaluator: OwnerEdgeEvaluator,
) -> OwnerEdgeEvidence:
    key = cast(tuple[str, str], tuple(sorted((left.owner_id, right.owner_id))))
    edge = edge_cache.get(key)
    if edge is not None:
        return edge

    edge = edge_evaluator(
        left,
        right,
        config=config,
        drift_lookup=drift_lookup,
    )
    edge_cache[key] = edge
    if edge_evidence_sink is not None:
        edge_evidence_sink.append(edge)
    return edge


def _group_match_score(
    owner: SampleLocalMS1Owner,
    group: list[SampleLocalMS1Owner],
    config: AlignmentConfig,
) -> tuple[float, float, float]:
    return max(_owner_match_score(owner, existing, config) for existing in group)


def _owner_match_score(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    config: AlignmentConfig,
) -> tuple[float, float, float]:
    mz_score = _ppm(left.precursor_mz, right.precursor_mz) / config.max_ppm
    rt_score = (
        abs(left.owner_apex_rt - right.owner_apex_rt)
        * 60.0
        / config.identity_rt_candidate_window_sec
    )
    product_ppm = _ppm(
        left.primary_identity_event.product_mz,
        right.primary_identity_event.product_mz,
    )
    product_score = product_ppm / config.product_mz_tolerance_ppm
    return (mz_score + rt_score + product_score, mz_score, rt_score)


def _hypothesis_from_owner_group(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    group_identity: CrossSampleGroupIdentity,
    public_family_id: str,
    evidence: str,
    identity_conflict: bool = False,
    review_only: bool = False,
    consolidation_state: str = "not_consolidated",
    consolidation_winner_group_hypothesis_id: str = "",
    consolidation_source_group_hypothesis_id: str = "",
    targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None,
) -> CrossSamplePeakGroupHypothesis:
    center_mz = sum(owner.precursor_mz for owner in group) / len(group)
    center_rt = sum(owner.owner_apex_rt for owner in group) / len(group)
    center_product_mz = sum(
        owner.primary_identity_event.product_mz for owner in group
    ) / len(group)
    center_loss = sum(
        owner.primary_identity_event.observed_neutral_loss_da for owner in group
    ) / len(group)
    event_ids = tuple(
        event_id for owner in group for event_id in owner.event_candidate_ids
    )
    return CrossSamplePeakGroupHypothesis(
        group_hypothesis_id=group_identity.group_hypothesis_id,
        public_family_id=public_family_id,
        owner_ids=tuple(owner.owner_id for owner in group),
        event_ids=event_ids,
        event_member_count=sum(len(owner.all_events) for owner in group),
        processing_request_id=group_identity.processing_request_id,
        member_subjects=group_identity.member_subjects,
        identity_basis_bytes=group_identity.basis_bytes,
        disposition=group_identity.disposition,
        source="cross_sample_peak_group_successor_constructor",
        neutral_loss_tag=group[0].neutral_loss_tag,
        group_center_mz=center_mz,
        group_center_rt=center_rt,
        group_product_mz=center_product_mz,
        group_observed_neutral_loss_da=center_loss,
        targeted_istd_anchor=targeted_istd_anchor,
        has_anchor=True,
        owners=group,
        evidence=evidence,
        identity_conflict=identity_conflict,
        review_only=review_only,
        construction_role="successor_constructor",
        consolidation_state=consolidation_state,
        consolidation_winner_group_hypothesis_id=(
            consolidation_winner_group_hypothesis_id
        ),
        consolidation_source_group_hypothesis_id=(
            consolidation_source_group_hypothesis_id
        ),
    )


def _cross_sample_request_id_for_owners(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    processing_request_id: str | None,
) -> str:
    subjects = tuple(owner.cross_sample_member_subject for owner in owners)
    if not subjects or any(subject is None for subject in subjects):
        raise CrossSampleContractError(
            "every owner requires an exact member subject before grouping"
        )
    request_ids = {
        subject.processing_request_id for subject in subjects if subject is not None
    }
    if len(request_ids) != 1:
        raise CrossSampleContractError("owners crossed processing requests")
    inferred = next(iter(request_ids))
    if processing_request_id is not None and processing_request_id != inferred:
        raise CrossSampleContractError(
            "explicit processing request crossed owner subject"
        )
    return inferred


def _group_identity_from_owners(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    processing_request_id: str,
    disposition: CrossSampleGroupDisposition | None = None,
) -> CrossSampleGroupIdentity:
    subjects = tuple(owner.cross_sample_member_subject for owner in owners)
    if any(subject is None for subject in subjects):
        raise CrossSampleContractError("owner is missing a CrossSample member subject")
    return mint_cross_sample_group_identity(
        processing_request_id=processing_request_id,
        member_subjects=cast(tuple[CrossSampleGroupMemberSubject, ...], subjects),
        disposition=disposition,
    )


def _group_evidence(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    full_batch_continuity: bool,
    injection_trajectory: bool,
    bounded_continuity: bool = False,
    targeted_istd_trajectory: bool = False,
) -> str:
    if len(group) == 1:
        return "single_sample_local_owner"
    if targeted_istd_trajectory:
        policy = "owner_targeted_istd_trajectory"
    elif full_batch_continuity:
        policy = "owner_full_batch_continuity"
    elif injection_trajectory:
        policy = "owner_injection_trajectory"
    elif bounded_continuity:
        policy = "owner_bounded_continuity"
    else:
        policy = "owner_complete_link"
    return f"{policy};owner_count={len(group)}"


def _owner_sort_key(owner: SampleLocalMS1Owner) -> tuple[object, ...]:
    event = owner.primary_identity_event
    return (
        owner.neutral_loss_tag,
        owner.precursor_mz,
        owner.owner_apex_rt,
        owner.sample_stem,
        -event.evidence_score,
        -event.seed_event_count,
        owner.owner_id,
    )


def _owner_rt_sort_key(owner: SampleLocalMS1Owner) -> tuple[object, ...]:
    event = owner.primary_identity_event
    return (
        owner.neutral_loss_tag,
        owner.owner_apex_rt,
        owner.precursor_mz,
        owner.sample_stem,
        -event.evidence_score,
        -event.seed_event_count,
        owner.owner_id,
    )


def _ppm(left: float, right: float) -> float:
    denominator = max(abs(left), 1e-12)
    return abs(left - right) / denominator * 1_000_000.0
