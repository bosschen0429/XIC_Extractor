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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import TargetedIstdIdentityAnchor
from xic_extractor.alignment.edge_scoring import (
    DriftLookupProtocol,
    DriftPriorSource,
    EdgeDecision,
    HardGateFailureReason,
    OwnerEdgeEvidence,
    evaluate_owner_edge,
)
from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
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

if TYPE_CHECKING:
    from xic_extractor.alignment.owner_clustering import OwnerAlignedFeature
    from xic_extractor.configuration.models import Target

CrossSamplePeakGroupEdgeRole: TypeAlias = Literal[
    "membership_support",
    "membership_challenge",
]
CrossSamplePeakGroupConstructionPolicy: TypeAlias = Literal[
    "none",
    "construction_time_hard_gate_observed",
]
CrossSamplePeakGroupReviewReason: TypeAlias = Literal[
    "identity_conflict",
    "ambiguous_owner",
    "review_only",
]
CrossSamplePeakGroupReviewRole: TypeAlias = Literal["review_only_challenge"]
CrossSamplePeakGroupHardGateRole: TypeAlias = Literal["split_gate_challenge"]
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
    if not isinstance(value, str) or not value.strip():
        raise CrossSampleContractError(f"{field} must be non-empty text")
    return value


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
            ("xic.peak-hypothesis.v1:",)
            if self.subject_kind == "peak_hypothesis"
            else (
                "xic.peak-observation-content.v1:",
                _DISCOVERY_PHYSICAL_SELECTION_SUBJECT_PREFIX,
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
            if subject_id.startswith("xic.peak-observation-content.v1:"):
                if physical_ids != (subject_id,):
                    raise CrossSampleContractError(
                        "physical observation member must bind its tagged observation"
                    )
            elif len(physical_ids) != 1 or not physical_ids[0].startswith(
                ("xic.peak-observation-content.v1:", "physical-facts.v1:")
            ):
                raise CrossSampleContractError(
                    "Discovery member must retain one exact physical audit fact"
                )
        basis = canonical_identity_basis_bytes(
            {
                "schema_version": "group_member_subject_v1",
                "processing_request_id": request_id,
                "canonical_sample_identity_id": sample_id,
                "subject": {"kind": self.subject_kind, "id": subject_id},
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
            "schema_version": "group_member_subject_v1",
            "processing_request_id": request_id,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": "physical_observation", "id": subject_id},
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
    if not physical_ids or len(set(physical_ids)) != len(physical_ids) or any(
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
            "schema_version": "group_member_subject_v1",
            "processing_request_id": request_id,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": "physical_observation", "id": selection_id},
        }
    )
    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(_GROUP_MEMBER_SUBJECT_PREFIX, basis),
        processing_request_id=request_id,
        canonical_sample_identity_id=sample_id,
        subject_kind="physical_observation",
        subject_id=selection_id,
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
                "schema_version": "cross_sample_group_v1",
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
            "schema_version": "cross_sample_group_v1",
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
class CrossSampleNeighborhoodEdge:
    left_member_subject_id: str
    right_member_subject_id: str
    disposition: CrossSampleEdgeDisposition

    def __post_init__(self) -> None:
        left = _required_text(self.left_member_subject_id, field="left member")
        right = _required_text(self.right_member_subject_id, field="right member")
        if left == right:
            raise CrossSampleContractError("neighborhood edge cannot be a self-edge")
        if self.disposition not in {"eligible", "review_only", "excluded"}:
            raise CrossSampleContractError("edge disposition is not closed")


def classify_cross_sample_initial_edge(
    edge: OwnerEdgeEvidence,
    *,
    context: CrossSampleInitialEdgeContext,
    config: AlignmentConfig,
) -> CrossSampleEdgeDisposition:
    """Classify raw owner evidence without caller-authored compatibility flags."""

    if not isinstance(edge, OwnerEdgeEvidence):
        raise CrossSampleContractError("initial edge evidence must be owner-minted")
    if not isinstance(config, AlignmentConfig):
        raise CrossSampleContractError("initial edge config must be typed")
    if context not in {
        "hard_gate",
        "complete_link",
        "full_batch_continuity",
        "targeted_istd_continuity",
    }:
        raise CrossSampleContractError("initial edge context is not closed")
    if edge.decision == "blocked_edge":
        if context == "targeted_istd_continuity" and edge.failure_reason in {
            "product_mz_out_of_tolerance",
            "observed_loss_out_of_tolerance",
        }:
            return _targeted_istd_rt_disposition(edge, config=config)
        return "excluded"
    if context == "hard_gate":
        return "eligible"
    if edge.decision == "strong_edge":
        return "eligible"
    if context == "complete_link":
        return "review_only"
    if _full_batch_weak_edge_is_eligible(edge, config=config):
        return "eligible"
    if context == "targeted_istd_continuity":
        return _targeted_istd_rt_disposition(edge, config=config)
    return "review_only"


def _full_batch_weak_edge_is_eligible(
    edge: OwnerEdgeEvidence,
    *,
    config: AlignmentConfig,
) -> bool:
    return (
        edge.decision == "weak_edge"
        and not edge.failure_reason
        and edge.drift_prior_source == "none"
        and edge.rt_raw_delta_sec <= config.preferred_rt_sec
        and edge.owner_quality == "clean"
        and edge.seed_support_level == "moderate"
    )


def _targeted_istd_rt_disposition(
    edge: OwnerEdgeEvidence,
    *,
    config: AlignmentConfig,
) -> CrossSampleEdgeDisposition:
    if (
        edge.owner_quality in {"weak", "ambiguous_nearby"}
        or edge.seed_support_level == "weak"
    ):
        return "review_only"
    rt_delta_sec = edge.rt_raw_delta_sec
    if (
        edge.drift_prior_source != "none"
        and edge.rt_drift_corrected_delta_sec is not None
    ):
        rt_delta_sec = edge.rt_drift_corrected_delta_sec
    return "eligible" if rt_delta_sec <= config.preferred_rt_sec else "review_only"


def _components(
    member_ids: tuple[str, ...],
    eligible_edges: tuple[CrossSampleNeighborhoodEdge, ...],
) -> tuple[tuple[str, ...], ...]:
    adjacent: dict[str, set[str]] = {
        member_id: set() for member_id in member_ids
    }
    for edge in eligible_edges:
        adjacent[edge.left_member_subject_id].add(edge.right_member_subject_id)
        adjacent[edge.right_member_subject_id].add(edge.left_member_subject_id)
    remaining = set(member_ids)
    components: list[tuple[str, ...]] = []
    while remaining:
        pending = [min(remaining)]
        found: set[str] = set()
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            pending.extend(sorted(adjacent[current] - found, reverse=True))
        remaining.difference_update(found)
        components.append(tuple(sorted(found)))
    return tuple(sorted(components))


@dataclass(frozen=True, slots=True)
class CrossSampleConnectedNeighborhood:
    status: CrossSampleNeighborhoodStatus
    member_subject_ids: tuple[str, ...]
    eligible_edges: tuple[CrossSampleNeighborhoodEdge, ...]
    connected_components: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        if self.status not in {"assessable", "unassessable"}:
            raise CrossSampleContractError("neighborhood status is not closed")
        if tuple(sorted(set(self.member_subject_ids))) != self.member_subject_ids:
            raise CrossSampleContractError("neighborhood members are not canonical")
        if any(edge.disposition != "eligible" for edge in self.eligible_edges):
            raise CrossSampleContractError("neighborhood retained a non-eligible edge")
        member_ids = set(self.member_subject_ids)
        if any(
            edge.left_member_subject_id not in member_ids
            or edge.right_member_subject_id not in member_ids
            for edge in self.eligible_edges
        ):
            raise CrossSampleContractError("neighborhood edge references an outsider")
        if self.connected_components != _components(
            self.member_subject_ids,
            self.eligible_edges,
        ):
            raise CrossSampleContractError("neighborhood components are not exact")


def build_bounded_connected_neighborhood(
    *,
    member_subjects: Sequence[CrossSampleGroupMemberSubject],
    edges: Sequence[CrossSampleNeighborhoodEdge],
    budget: CrossSampleNeighborhoodBudget,
) -> CrossSampleConnectedNeighborhood:
    if not isinstance(budget, CrossSampleNeighborhoodBudget):
        raise CrossSampleContractError("neighborhood budget must be typed")
    members = tuple(member_subjects)
    member_ids = tuple(sorted(member.member_subject_id for member in members))
    if len(set(member_ids)) != len(member_ids):
        raise CrossSampleContractError("neighborhood duplicates a member")
    eligible = tuple(
        sorted(
            (edge for edge in edges if edge.disposition == "eligible"),
            key=lambda edge: (
                min(edge.left_member_subject_id, edge.right_member_subject_id),
                max(edge.left_member_subject_id, edge.right_member_subject_id),
            ),
        )
    )
    status: CrossSampleNeighborhoodStatus = (
        "assessable"
        if len(member_ids) <= budget.max_members
        and len(eligible) <= budget.max_eligible_edges
        else "unassessable"
    )
    return CrossSampleConnectedNeighborhood(
        status=status,
        member_subject_ids=member_ids,
        eligible_edges=eligible,
        connected_components=_components(member_ids, eligible),
    )


@dataclass(frozen=True, slots=True)
class CrossSampleLineageEdge:
    predecessor_group_hypothesis_id: str
    successor_group_hypothesis_id: str
    member_subject_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        predecessor = _required_text(
            self.predecessor_group_hypothesis_id,
            field="lineage predecessor",
        )
        successor = _required_text(
            self.successor_group_hypothesis_id,
            field="lineage successor",
        )
        if predecessor == successor:
            raise CrossSampleContractError("lineage cannot be a self-edge")
        if (
            not self.member_subject_ids
            or tuple(sorted(set(self.member_subject_ids)))
            != self.member_subject_ids
        ):
            raise CrossSampleContractError(
                "lineage member closure must be non-empty and canonical"
            )


@dataclass(frozen=True, slots=True)
class CrossSampleSuccessorApplication:
    status: CrossSampleNeighborhoodStatus
    successors: tuple[CrossSampleGroupIdentity, ...]
    lineage: tuple[CrossSampleLineageEdge, ...]
    reason: str


def apply_coupled_successor_proposal(
    *,
    predecessors: Sequence[CrossSampleGroupIdentity],
    successor_member_sets: Sequence[Sequence[CrossSampleGroupMemberSubject]],
    edges: Sequence[CrossSampleNeighborhoodEdge],
    budget: CrossSampleNeighborhoodBudget,
    population: Sequence[CrossSampleGroupIdentity],
) -> CrossSampleSuccessorApplication:
    predecessor_groups = tuple(predecessors)
    if not predecessor_groups:
        raise CrossSampleContractError("successor requires a predecessor")
    validate_cross_sample_group_population(population)
    request_ids = {group.processing_request_id for group in predecessor_groups}
    if len(request_ids) != 1:
        raise CrossSampleContractError("successor predecessors crossed request")
    request_id = next(iter(request_ids))
    predecessor_members = tuple(
        member for group in predecessor_groups for member in group.member_subjects
    )
    neighborhood = build_bounded_connected_neighborhood(
        member_subjects=predecessor_members,
        edges=edges,
        budget=budget,
    )
    if neighborhood.status == "unassessable":
        return CrossSampleSuccessorApplication(
            status="unassessable",
            successors=(),
            lineage=(),
            reason="eligible_neighborhood_exceeds_budget",
        )
    proposed = tuple(tuple(member_set) for member_set in successor_member_sets)
    proposed_ids = tuple(
        member.member_subject_id for member_set in proposed for member in member_set
    )
    predecessor_ids = tuple(member.member_subject_id for member in predecessor_members)
    if sorted(proposed_ids) != sorted(predecessor_ids) or len(set(proposed_ids)) != len(
        proposed_ids
    ):
        raise CrossSampleContractError("successor proposal is not a complete partition")
    component_by_member = {
        member_id: index
        for index, component in enumerate(neighborhood.connected_components)
        for member_id in component
    }
    if any(
        len({component_by_member[member.member_subject_id] for member in member_set})
        != 1
        for member_set in proposed
    ):
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
                for member_set in proposed
            ),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    unaffected = tuple(group for group in population if group not in predecessor_groups)
    validate_cross_sample_group_population((*unaffected, *successors))
    predecessor_ids_set = {
        group.group_hypothesis_id for group in predecessor_groups
    }
    successor_ids_set = {group.group_hypothesis_id for group in successors}
    if predecessor_ids_set == successor_ids_set:
        return CrossSampleSuccessorApplication(
            status="assessable",
            successors=successors,
            lineage=(),
            reason="unchanged_membership_no_lineage",
        )
    lineage = tuple(
        CrossSampleLineageEdge(
            predecessor_group_hypothesis_id=predecessor.group_hypothesis_id,
            successor_group_hypothesis_id=successor.group_hypothesis_id,
            member_subject_ids=tuple(
                member.member_subject_id for member in successor.member_subjects
            ),
        )
        for predecessor in sorted(
            predecessor_groups, key=lambda group: group.group_hypothesis_id
        )
        for successor in successors
        if set(predecessor.member_subjects).intersection(successor.member_subjects)
        and predecessor.group_hypothesis_id != successor.group_hypothesis_id
    )
    return CrossSampleSuccessorApplication(
        status="assessable",
        successors=successors,
        lineage=lineage,
        reason="membership_changed",
    )


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
class CrossSamplePeakGroupEdgeFact:
    left_owner_id: str
    right_owner_id: str
    owner_pair_ids: tuple[str, str]
    decision: EdgeDecision
    role: CrossSamplePeakGroupEdgeRole
    failure_reason: HardGateFailureReason | Literal[""]
    rt_raw_delta_sec: float
    rt_drift_corrected_delta_sec: float | None
    drift_prior_source: DriftPriorSource
    injection_order_gap: int | None
    score: int
    reason: str
    construction_policy: CrossSamplePeakGroupConstructionPolicy = "none"
    source: str = "owner_edge_evidence_shadow"


@dataclass(frozen=True)
class CrossSamplePeakGroupReviewFact:
    feature_family_id: str
    public_family_id: str
    review_only: bool
    identity_conflict: bool
    ambiguous_sample_stem: str | None
    ambiguous_candidate_ids: tuple[str, ...]
    evidence: str
    reason: CrossSamplePeakGroupReviewReason
    role: CrossSamplePeakGroupReviewRole = "review_only_challenge"
    source: str = "owner_aligned_feature_review_shadow"


@dataclass(frozen=True)
class CrossSamplePeakGroupHardGateChallengeFact:
    left_owner_id: str
    right_owner_id: str
    owner_pair_ids: tuple[str, str]
    decision: EdgeDecision
    role: CrossSamplePeakGroupHardGateRole
    failure_reason: HardGateFailureReason | Literal[""]
    reason: str
    construction_policy: CrossSamplePeakGroupConstructionPolicy = (
        "construction_time_hard_gate_observed"
    )
    source: str = "owner_edge_hard_gate_shadow"


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
    edge_facts: tuple[CrossSamplePeakGroupEdgeFact, ...] = ()
    review_facts: tuple[CrossSamplePeakGroupReviewFact, ...] = ()
    hard_gate_challenge_facts: tuple[
        CrossSamplePeakGroupHardGateChallengeFact, ...
    ] = ()

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
    """Replay the canonical identity retained by a scientific group carrier."""

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
                "scientific CrossSample carrier is missing canonical identity"
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
    member_ids = {member.member_subject_id for member in identity.member_subjects}
    for edge in group.lineage:
        if edge.successor_group_hypothesis_id != group.group_hypothesis_id:
            raise CrossSampleContractError("lineage crossed its successor identity")
        if not set(edge.member_subject_ids).issubset(member_ids):
            raise CrossSampleContractError("lineage contains a forged member closure")


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
    subjects = tuple(owner.cross_sample_member_subject for owner in owners)
    if any(subject is None for subject in subjects):
        raise CrossSampleContractError(
            "every owner requires an exact member subject before grouping"
        )
    request_ids = {
        subject.processing_request_id for subject in subjects if subject is not None
    }
    if len(request_ids) != 1:
        raise CrossSampleContractError("owners crossed processing requests")
    request_id = next(iter(request_ids))
    if processing_request_id is not None and processing_request_id != request_id:
        raise CrossSampleContractError("explicit request crossed owner subjects")
    clean = tuple(owner for owner in owners if not owner.identity_conflict)
    conflict_hypotheses = tuple(
        _hypothesis_from_owner_group(
            (owner,),
            group_identity=_identity_from_owners(
                (owner,),
                processing_request_id=request_id,
                disposition="unresolved",
            ),
            public_family_id=f"FAM{index:06d}",
            evidence="identity_conflict_review_only",
            identity_conflict=True,
            review_only=True,
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
        ) = (
            _bind_targeted_istd_trajectory_provenance(
                groups,
                injection_trajectory_owner_sets=injection_trajectory_owner_sets,
                injection_order=injection_order,
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            )
        )
    targeted_istd_anchor = getattr(drift_lookup, "istd_identity_anchor", None)
    if not isinstance(targeted_istd_anchor, TargetedIstdIdentityAnchor):
        targeted_istd_anchor = None
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
        _identity_from_owners(
            tuple(group),
            processing_request_id=request_id,
            disposition=(
                "unresolved" if index in resolved_duplicate_owner_groups else None
            ),
        )
        for index, group in enumerate(groups)
    )
    validate_cross_sample_group_population(
        tuple(
            CrossSampleGroupIdentity(
                group_hypothesis_id=hypothesis.group_hypothesis_id,
                processing_request_id=hypothesis.processing_request_id,
                member_subjects=hypothesis.member_subjects,
                basis_bytes=hypothesis.identity_basis_bytes,
                disposition=hypothesis.disposition,
            )
            for hypothesis in conflict_hypotheses
        )
        + group_identities
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
            full_batch_continuity=(
                owner_id_set in full_batch_continuity_owner_sets
            ),
            injection_trajectory=(
                owner_id_set in injection_trajectory_owner_sets
            ),
            bounded_continuity=(owner_id_set in bounded_continuity_owner_sets),
            targeted_istd_trajectory=(
                owner_id_set in targeted_istd_trajectory_owner_sets
            ),
        )
        winner_index = resolved_duplicate_owner_groups.get(index)
        if winner_index is not None:
            winner_id = group_identities[winner_index].group_hypothesis_id
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
                    consolidation_winner_group_hypothesis_id=winner_id,
                    consolidation_source_group_hypothesis_id=(
                        group_identity.group_hypothesis_id
                    ),
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
            ),
        )
    return (*conflict_hypotheses, *clean_hypotheses)


def review_only_peak_group_hypotheses_from_ambiguous_records(
    records: tuple[AmbiguousOwnerRecord, ...],
    *,
    start_index: int,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    hypotheses: list[CrossSamplePeakGroupHypothesis] = []
    for offset, record in enumerate(records):
        if (
            record.neutral_loss_tag is None
            or record.precursor_mz is None
            or record.apex_rt is None
            or record.product_mz is None
            or record.observed_neutral_loss_da is None
        ):
            continue
        family_id = f"FAM{start_index + offset:06d}"
        hypotheses.append(
            CrossSamplePeakGroupHypothesis(
                group_hypothesis_id=family_id,
                public_family_id=family_id,
                owner_ids=(),
                event_ids=(),
                event_member_count=0,
                source="cross_sample_peak_group_successor_constructor",
                neutral_loss_tag=record.neutral_loss_tag,
                group_center_mz=record.precursor_mz,
                group_center_rt=record.apex_rt,
                group_product_mz=record.product_mz,
                group_observed_neutral_loss_da=record.observed_neutral_loss_da,
                has_anchor=False,
                owners=(),
                evidence="ambiguous_ms1_owner_review_only",
                review_only=True,
                ambiguous_sample_stem=record.sample_stem,
                ambiguous_candidate_ids=record.candidate_ids,
                construction_role="successor_constructor",
            ),
        )
    return tuple(hypotheses)


def cross_sample_peak_group_hypothesis_from_owner_feature(
    feature: OwnerAlignedFeature,
    *,
    edge_evidence: Sequence[OwnerEdgeEvidence] = (),
) -> CrossSamplePeakGroupHypothesis:
    event_ids = tuple(
        event_id for owner in feature.owners for event_id in owner.event_candidate_ids
    )
    edge_facts = cross_sample_peak_group_edge_facts_from_owner_edges(
        edge_evidence,
        owner_ids=feature.event_cluster_ids,
    )
    review_fact = cross_sample_peak_group_review_fact_from_owner_feature(feature)
    review_facts = () if review_fact is None else (review_fact,)
    hard_gate_challenge_facts = (
        cross_sample_peak_group_hard_gate_challenge_facts_from_owner_edges(
            edge_evidence,
            owner_ids=feature.event_cluster_ids,
        )
    )
    return CrossSamplePeakGroupHypothesis(
        group_hypothesis_id=feature.feature_family_id,
        public_family_id=feature.feature_family_id,
        owner_ids=feature.event_cluster_ids,
        event_ids=event_ids,
        event_member_count=feature.event_member_count,
        processing_request_id=feature.processing_request_id,
        member_subjects=feature.member_subjects,
        identity_basis_bytes=feature.identity_basis_bytes,
        disposition=feature.disposition,
        lineage=feature.cross_sample_lineage,
        neutral_loss_tag=feature.neutral_loss_tag,
        group_center_mz=feature.family_center_mz,
        group_center_rt=feature.family_center_rt,
        group_product_mz=feature.family_product_mz,
        group_observed_neutral_loss_da=feature.family_observed_neutral_loss_da,
        has_anchor=feature.has_anchor,
        owners=feature.owners,
        evidence=feature.evidence,
        identity_conflict=feature.identity_conflict,
        review_only=feature.review_only,
        confirm_local_owners_with_backfill=(feature.confirm_local_owners_with_backfill),
        backfill_seed_centers=feature.backfill_seed_centers,
        ambiguous_sample_stem=feature.ambiguous_sample_stem,
        ambiguous_candidate_ids=feature.ambiguous_candidate_ids,
        edge_facts=edge_facts,
        review_facts=review_facts,
        hard_gate_challenge_facts=hard_gate_challenge_facts,
    )


def cross_sample_peak_group_edge_fact_from_owner_edge(
    edge: OwnerEdgeEvidence,
) -> CrossSamplePeakGroupEdgeFact:
    owner_pair_ids = (
        min(edge.left_owner_id, edge.right_owner_id),
        max(edge.left_owner_id, edge.right_owner_id),
    )
    return CrossSamplePeakGroupEdgeFact(
        left_owner_id=edge.left_owner_id,
        right_owner_id=edge.right_owner_id,
        owner_pair_ids=owner_pair_ids,
        decision=edge.decision,
        role=(
            "membership_support"
            if edge.decision == "strong_edge"
            else "membership_challenge"
        ),
        failure_reason=edge.failure_reason,
        rt_raw_delta_sec=edge.rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=edge.rt_drift_corrected_delta_sec,
        drift_prior_source=edge.drift_prior_source,
        injection_order_gap=edge.injection_order_gap,
        score=edge.score,
        reason=edge.reason,
        construction_policy=(
            "construction_time_hard_gate_observed"
            if edge.decision == "blocked_edge"
            else "none"
        ),
    )


def cross_sample_peak_group_review_fact_from_owner_feature(
    feature: OwnerAlignedFeature,
) -> CrossSamplePeakGroupReviewFact | None:
    if not feature.review_only:
        return None

    reason: CrossSamplePeakGroupReviewReason
    if feature.identity_conflict:
        reason = "identity_conflict"
    elif feature.ambiguous_sample_stem is not None or feature.ambiguous_candidate_ids:
        reason = "ambiguous_owner"
    else:
        reason = "review_only"

    return CrossSamplePeakGroupReviewFact(
        feature_family_id=feature.feature_family_id,
        public_family_id=feature.feature_family_id,
        review_only=feature.review_only,
        identity_conflict=feature.identity_conflict,
        ambiguous_sample_stem=feature.ambiguous_sample_stem,
        ambiguous_candidate_ids=feature.ambiguous_candidate_ids,
        evidence=feature.evidence,
        reason=reason,
    )


def cross_sample_peak_group_hard_gate_challenge_fact_from_owner_edge(
    edge: OwnerEdgeEvidence,
) -> CrossSamplePeakGroupHardGateChallengeFact | None:
    if edge.decision != "blocked_edge":
        return None

    owner_pair_ids = (
        min(edge.left_owner_id, edge.right_owner_id),
        max(edge.left_owner_id, edge.right_owner_id),
    )
    return CrossSamplePeakGroupHardGateChallengeFact(
        left_owner_id=edge.left_owner_id,
        right_owner_id=edge.right_owner_id,
        owner_pair_ids=owner_pair_ids,
        decision=edge.decision,
        role="split_gate_challenge",
        failure_reason=edge.failure_reason,
        reason=edge.reason,
    )


def cross_sample_peak_group_edge_facts_from_owner_edges(
    edges: Sequence[OwnerEdgeEvidence],
    *,
    owner_ids: Iterable[str] | None = None,
) -> tuple[CrossSamplePeakGroupEdgeFact, ...]:
    owner_id_set = frozenset(owner_ids) if owner_ids is not None else None
    return tuple(
        cross_sample_peak_group_edge_fact_from_owner_edge(edge)
        for edge in edges
        if _edge_matches_owner_ids(edge, owner_id_set)
    )


def cross_sample_peak_group_hard_gate_challenge_facts_from_owner_edges(
    edges: Sequence[OwnerEdgeEvidence],
    *,
    owner_ids: Iterable[str] | None = None,
) -> tuple[CrossSamplePeakGroupHardGateChallengeFact, ...]:
    owner_id_set = frozenset(owner_ids) if owner_ids is not None else None
    facts: list[CrossSamplePeakGroupHardGateChallengeFact] = []
    for edge in edges:
        if not _edge_matches_owner_ids(edge, owner_id_set):
            continue
        fact = cross_sample_peak_group_hard_gate_challenge_fact_from_owner_edge(
            edge,
        )
        if fact is not None:
            facts.append(fact)
    return tuple(facts)


def _edge_matches_owner_ids(
    edge: OwnerEdgeEvidence,
    owner_id_set: frozenset[str] | None,
) -> bool:
    return owner_id_set is None or (
        edge.left_owner_id in owner_id_set and edge.right_owner_id in owner_id_set
    )


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
                classify_cross_sample_initial_edge(
                    edge,
                    context="hard_gate",
                    config=config,
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
            classify_cross_sample_initial_edge(
                _edge_for_pair(
                    owner,
                    existing,
                    config=config,
                    drift_lookup=drift_lookup,
                    edge_evidence_sink=edge_evidence_sink,
                    edge_cache=edge_cache,
                    edge_evaluator=edge_evaluator,
                ),
                context="complete_link",
                config=config,
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
    return (
        classify_cross_sample_initial_edge(
            edge,
            context="full_batch_continuity",
            config=config,
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
        if (
            len(complete_groups) > 1
            and _is_injection_trajectory(
                connected_group,
                injection_order=injection_order,
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            )
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
    if (
        injection_order is None
        or not isinstance(anchor, TargetedIstdIdentityAnchor)
    ):
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
    return _owners_in_injection_order(
        candidate,
        injection_order=injection_order,
    ) is not None


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
    return (
        classify_cross_sample_initial_edge(
            edge,
            context="targeted_istd_continuity",
            config=config,
        )
        == "eligible"
    )


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
                    and frozenset(
                        owner.owner_id for owner in facts[candidate].owners
                    )
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
            owner.neutral_loss_tag != group[0].neutral_loss_tag
            for owner in group[1:]
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


def _identity_from_owners(
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
