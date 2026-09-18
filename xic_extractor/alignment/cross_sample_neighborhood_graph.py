"""Neighborhood graphs, edge evaluations, and successor applications for CrossSample."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field
from itertools import combinations
from typing import TypeAlias

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_identity_models import (
    _CROSS_SAMPLE_GROUP_PREFIX,
    CrossSampleContractError,
    CrossSampleEdgeDisposition,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleInitialEdgeContext,
    CrossSampleNeighborhoodBudget,
    CrossSampleNeighborhoodStatus,
    CrossSampleRepairedObservation,
    _required_text,
    mint_cross_sample_group_identity,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.edge_scoring import (
    DriftLookupProtocol,
    DriftPriorSource,
    OwnerEdgeEvidence,
    evaluate_owner_edge,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.mass_accuracy import absolute_ppm_error
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    canonical_identity_basis_bytes,
    typed_identity_digest,
    validate_typed_identity_basis,
    validate_typed_identity_id,
)

_CROSS_SAMPLE_LINEAGE_EDGE_PREFIX = "xic.cross-sample-lineage-edge.v1:"
_CROSS_SAMPLE_REPAIR_APPLICATION_PREFIX = (
    "xic.cross-sample-repair-admission-application.v1:"
)
_CROSS_SAMPLE_REPAIR_APPLICATION_MINT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class CrossSampleNeighborhoodEdge:
    left_member_subject_id: str
    right_member_subject_id: str
    disposition: CrossSampleEdgeDisposition
    initial_evidence: OwnerEdgeEvidence
    initial_context: CrossSampleInitialEdgeContext
    config: AlignmentConfig
    left_owner: SampleLocalMS1Owner
    right_owner: SampleLocalMS1Owner
    drift_lookup: DriftLookupProtocol | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        left = _required_text(self.left_member_subject_id, field="left member")
        right = _required_text(self.right_member_subject_id, field="right member")
        if left == right:
            raise CrossSampleContractError("neighborhood edge cannot be a self-edge")
        if self.disposition not in {"eligible", "review_only", "excluded"}:
            raise CrossSampleContractError("edge disposition is not closed")
        if not isinstance(self.initial_evidence, OwnerEdgeEvidence):
            raise CrossSampleContractError(
                "neighborhood edge must be classified by CrossSample"
            )
        if not isinstance(self.left_owner, SampleLocalMS1Owner) or not isinstance(
            self.right_owner, SampleLocalMS1Owner
        ):
            raise CrossSampleContractError("neighborhood edge owners must be typed")
        left_member = self.left_owner.cross_sample_member_subject
        right_member = self.right_owner.cross_sample_member_subject
        if (
            left_member is None
            or right_member is None
            or left_member.member_subject_id != left
            or right_member.member_subject_id != right
            or self.initial_evidence.left_owner_id != self.left_owner.owner_id
            or self.initial_evidence.right_owner_id != self.right_owner.owner_id
            or self.initial_evidence.left_sample_stem != self.left_owner.sample_stem
            or self.initial_evidence.right_sample_stem != self.right_owner.sample_stem
        ):
            raise CrossSampleContractError(
                "neighborhood evidence crossed its exact owner-member pair"
            )
        exact_evidence = evaluate_owner_edge(
            self.left_owner,
            self.right_owner,
            config=self.config,
            drift_lookup=self.drift_lookup,
        )
        if self.initial_evidence != exact_evidence:
            raise CrossSampleContractError(
                "neighborhood edge does not retain exact owner evidence"
            )
        expected = classify_cross_sample_initial_edge(
            exact_evidence,
            context=self.initial_context,
            config=self.config,
        )
        if self.disposition != expected:
            raise CrossSampleContractError(
                "neighborhood edge disposition contradicts owner classification"
            )


def classify_cross_sample_initial_neighborhood_edge(
    *,
    left_owner: SampleLocalMS1Owner,
    right_owner: SampleLocalMS1Owner,
    context: CrossSampleInitialEdgeContext,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None = None,
) -> CrossSampleNeighborhoodEdge:
    """Mint a sealed edge by evaluating the exact owner pair inside CrossSample."""

    edge = evaluate_owner_edge(
        left_owner,
        right_owner,
        config=config,
        drift_lookup=drift_lookup,
    )
    disposition = classify_cross_sample_initial_edge(
        edge,
        context=context,
        config=config,
    )
    left_member = left_owner.cross_sample_member_subject
    right_member = right_owner.cross_sample_member_subject
    if left_member is None or right_member is None:
        raise CrossSampleContractError(
            "initial neighborhood edge requires exact owner members"
        )
    return CrossSampleNeighborhoodEdge(
        left_member_subject_id=left_member.member_subject_id,
        right_member_subject_id=right_member.member_subject_id,
        disposition=disposition,
        initial_evidence=edge,
        initial_context=context,
        config=config,
        left_owner=left_owner,
        right_owner=right_owner,
        drift_lookup=drift_lookup,
    )


CrossSampleEdgeOwner: TypeAlias = SampleLocalMS1Owner | CrossSampleRepairedObservation


@dataclass(frozen=True, slots=True)
class CrossSampleRepairAdmissionEdge:
    """Physical-compatibility edge into an already-identified group."""

    left_member_subject_id: str
    right_member_subject_id: str
    disposition: CrossSampleEdgeDisposition
    predecessor_owner: SampleLocalMS1Owner
    repaired_observation: CrossSampleRepairedObservation
    rt_raw_delta_sec: float
    rt_drift_corrected_delta_sec: float | None
    drift_prior_source: DriftPriorSource
    injection_order_gap: int | None
    reason: str
    config: AlignmentConfig
    drift_lookup: DriftLookupProtocol | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        exact = _repair_admission_edge_facts(
            predecessor_owner=self.predecessor_owner,
            repaired_observation=self.repaired_observation,
            config=self.config,
            drift_lookup=self.drift_lookup,
        )
        if (
            self.left_member_subject_id,
            self.right_member_subject_id,
            self.disposition,
            self.rt_raw_delta_sec,
            self.rt_drift_corrected_delta_sec,
            self.drift_prior_source,
            self.injection_order_gap,
            self.reason,
        ) != exact:
            raise CrossSampleContractError(
                "repair admission edge does not retain exact physical evidence"
            )

    @property
    def left_owner(self) -> CrossSampleEdgeOwner:
        predecessor_member = self.predecessor_owner.cross_sample_member_subject
        assert predecessor_member is not None
        if predecessor_member.member_subject_id == self.left_member_subject_id:
            return self.predecessor_owner
        return self.repaired_observation

    @property
    def right_owner(self) -> CrossSampleEdgeOwner:
        predecessor_member = self.predecessor_owner.cross_sample_member_subject
        assert predecessor_member is not None
        if predecessor_member.member_subject_id == self.right_member_subject_id:
            return self.predecessor_owner
        return self.repaired_observation


CrossSampleClassifiedEdge: TypeAlias = (
    CrossSampleNeighborhoodEdge | CrossSampleRepairAdmissionEdge
)


def classify_cross_sample_repair_admission_edge(
    *,
    predecessor_owner: SampleLocalMS1Owner,
    repaired_observation: CrossSampleRepairedObservation,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None = None,
) -> CrossSampleRepairAdmissionEdge:
    """Classify repaired physical evidence without inventing local identity evidence."""

    facts = _repair_admission_edge_facts(
        predecessor_owner=predecessor_owner,
        repaired_observation=repaired_observation,
        config=config,
        drift_lookup=drift_lookup,
    )
    return CrossSampleRepairAdmissionEdge(
        left_member_subject_id=facts[0],
        right_member_subject_id=facts[1],
        disposition=facts[2],
        predecessor_owner=predecessor_owner,
        repaired_observation=repaired_observation,
        rt_raw_delta_sec=facts[3],
        rt_drift_corrected_delta_sec=facts[4],
        drift_prior_source=facts[5],
        injection_order_gap=facts[6],
        reason=facts[7],
        config=config,
        drift_lookup=drift_lookup,
    )


def _repair_admission_edge_facts(
    *,
    predecessor_owner: SampleLocalMS1Owner,
    repaired_observation: CrossSampleRepairedObservation,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
) -> tuple[
    str,
    str,
    CrossSampleEdgeDisposition,
    float,
    float | None,
    DriftPriorSource,
    int | None,
    str,
]:
    if type(predecessor_owner) is not SampleLocalMS1Owner:
        raise CrossSampleContractError(
            "repair admission predecessor must be an exact identity owner"
        )
    if type(repaired_observation) is not CrossSampleRepairedObservation:
        raise CrossSampleContractError(
            "repair admission candidate must be an exact physical observation"
        )
    if type(config) is not AlignmentConfig:
        raise CrossSampleContractError("repair admission config must be exact")
    predecessor_member = predecessor_owner.cross_sample_member_subject
    if predecessor_member is None:
        raise CrossSampleContractError(
            "repair admission predecessor is missing its canonical member"
        )
    member_ids = tuple(
        sorted(
            (
                predecessor_member.member_subject_id,
                repaired_observation.member_subject.member_subject_id,
            )
        )
    )
    if member_ids[0] == member_ids[1]:
        raise CrossSampleContractError("repair admission edge cannot be a self-edge")
    raw_delta = (
        abs(predecessor_owner.owner_apex_rt - repaired_observation.rt_apex_min) * 60.0
    )
    corrected_delta: float | None = None
    drift_source: DriftPriorSource = "none"
    injection_gap: int | None = None
    if drift_lookup is not None:
        left_delta = drift_lookup.sample_delta_min(predecessor_owner.sample_stem)
        right_delta = drift_lookup.sample_delta_min(repaired_observation.sample_stem)
        if left_delta is not None and right_delta is not None:
            corrected_delta = (
                abs(
                    (predecessor_owner.owner_apex_rt - left_delta)
                    - (repaired_observation.rt_apex_min - right_delta)
                )
                * 60.0
            )
            drift_source = drift_lookup.source
            if drift_source not in {
                "targeted_istd_trend",
                "batch_istd_trend",
            }:
                raise CrossSampleContractError(
                    "repair admission drift source is unsupported"
                )
        left_order = drift_lookup.injection_order(predecessor_owner.sample_stem)
        right_order = drift_lookup.injection_order(repaired_observation.sample_stem)
        if left_order is not None and right_order is not None:
            injection_gap = abs(left_order - right_order)
    effective_rt_delta = corrected_delta if corrected_delta is not None else raw_delta
    if predecessor_owner.sample_stem == repaired_observation.sample_stem:
        disposition: CrossSampleEdgeDisposition = "excluded"
        reason = "same_sample"
    elif (
        absolute_ppm_error(
            observed_mz=repaired_observation.precursor_mz,
            reference_mz=predecessor_owner.precursor_mz,
        )
        > config.max_ppm
    ):
        disposition = "excluded"
        reason = "precursor_mz_out_of_tolerance"
    elif effective_rt_delta > config.max_rt_sec:
        disposition = "excluded"
        reason = "rt_out_of_tolerance"
    elif effective_rt_delta <= config.preferred_rt_sec:
        disposition = "eligible"
        reason = "preferred_rt_match"
    else:
        disposition = "review_only"
        reason = "rt_review_band"
    return (
        member_ids[0],
        member_ids[1],
        disposition,
        raw_delta,
        corrected_delta,
        drift_source,
        injection_gap,
        reason,
    )


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
        and edge.rt_basis == "raw"
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
        edge.rt_basis == "drift_corrected"
        and edge.rt_drift_corrected_delta_sec is not None
    ):
        rt_delta_sec = edge.rt_drift_corrected_delta_sec
    return "eligible" if rt_delta_sec <= config.preferred_rt_sec else "review_only"


def _components(
    member_ids: tuple[str, ...],
    eligible_edges: tuple[CrossSampleClassifiedEdge, ...],
) -> tuple[tuple[str, ...], ...]:
    adjacent: dict[str, set[str]] = {member_id: set() for member_id in member_ids}
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
    eligible_edges: tuple[CrossSampleClassifiedEdge, ...]
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
    edges: Sequence[CrossSampleClassifiedEdge],
    budget: CrossSampleNeighborhoodBudget,
) -> CrossSampleConnectedNeighborhood:
    if not isinstance(budget, CrossSampleNeighborhoodBudget):
        raise CrossSampleContractError("neighborhood budget must be typed")
    members = tuple(member_subjects)
    if any(
        type(edge) not in {CrossSampleNeighborhoodEdge, CrossSampleRepairAdmissionEdge}
        for edge in edges
    ):
        raise CrossSampleContractError(
            "neighborhood edges must be exact CrossSample classifications"
        )
    edge_pairs = tuple(
        tuple(sorted((edge.left_member_subject_id, edge.right_member_subject_id)))
        for edge in edges
    )
    if len(set(edge_pairs)) != len(edge_pairs):
        raise CrossSampleContractError("neighborhood repeats an undirected member edge")
    owner_by_member: dict[str, str] = {}
    for edge in edges:
        for member_id, owner_id in (
            (edge.left_member_subject_id, edge.left_owner.owner_id),
            (edge.right_member_subject_id, edge.right_owner.owner_id),
        ):
            prior = owner_by_member.setdefault(member_id, owner_id)
            if prior != owner_id:
                raise CrossSampleContractError(
                    "neighborhood member crossed owner bindings"
                )
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
class CrossSampleCoupledProposalContext:
    """Rich CrossSample-owned context kept outside a ModeTrack snapshot."""

    predecessors: tuple[CrossSampleGroupIdentity, ...]
    classified_edges: tuple[CrossSampleNeighborhoodEdge, ...]
    budget: CrossSampleNeighborhoodBudget

    def __post_init__(self) -> None:
        if self.predecessors != tuple(
            sorted(
                self.predecessors,
                key=lambda group: group.group_hypothesis_id,
            )
        ):
            raise CrossSampleContractError(
                "coupled proposal predecessors are not canonical-sorted"
            )
        validate_cross_sample_group_population(self.predecessors)
        build_bounded_connected_neighborhood(
            member_subjects=tuple(
                member
                for predecessor in self.predecessors
                for member in predecessor.member_subjects
            ),
            edges=self.classified_edges,
            budget=self.budget,
        )


@dataclass(frozen=True, slots=True)
class CrossSampleLineageEdge:
    lineage_edge_id: str
    predecessor_group_hypothesis_id: str
    successor_group_hypothesis_id: str
    processing_request_id: str
    predecessor_identity_basis_bytes: bytes
    predecessor_group_hypothesis_ids: tuple[str, ...]
    member_subject_ids: tuple[str, ...]
    basis_bytes: bytes

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
        request_id = validate_typed_identity_id(
            self.processing_request_id,
            prefix=PROCESSING_REQUEST_ID_PREFIX,
            field="lineage processing_request_id",
        )
        try:
            validate_typed_identity_basis(
                predecessor,
                prefix=_CROSS_SAMPLE_GROUP_PREFIX,
                basis_bytes=self.predecessor_identity_basis_bytes,
                field="lineage predecessor_group_hypothesis_id",
            )
            validate_typed_identity_id(
                successor,
                prefix=_CROSS_SAMPLE_GROUP_PREFIX,
                field="lineage successor_group_hypothesis_id",
            )
            predecessor_basis = json.loads(
                self.predecessor_identity_basis_bytes.decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise CrossSampleContractError(str(exc)) from exc
        if (
            not isinstance(predecessor_basis, dict)
            or set(predecessor_basis)
            != {
                "schema_version",
                "processing_request_id",
                "member_subject_ids",
            }
            or predecessor_basis["schema_version"] != "cross_sample_group_v1"
            or predecessor_basis["processing_request_id"] != request_id
        ):
            raise CrossSampleContractError("lineage predecessor crossed request")
        predecessor_member_ids = predecessor_basis["member_subject_ids"]
        if (
            not isinstance(predecessor_member_ids, list)
            or not predecessor_member_ids
            or any(
                not isinstance(item, str) or not item for item in predecessor_member_ids
            )
            or sorted(set(predecessor_member_ids)) != predecessor_member_ids
        ):
            raise CrossSampleContractError("lineage predecessor basis is malformed")
        predecessor_ids = self.predecessor_group_hypothesis_ids
        if (
            not predecessor_ids
            or tuple(sorted(set(predecessor_ids))) != predecessor_ids
            or predecessor not in predecessor_ids
        ):
            raise CrossSampleContractError(
                "lineage predecessor closure must be non-empty and canonical"
            )
        if (
            not self.member_subject_ids
            or tuple(sorted(set(self.member_subject_ids))) != self.member_subject_ids
        ):
            raise CrossSampleContractError(
                "lineage member closure must be non-empty and canonical"
            )
        if not set(predecessor_member_ids).intersection(self.member_subject_ids):
            raise CrossSampleContractError(
                "lineage predecessor does not overlap successor membership"
            )
        basis = canonical_identity_basis_bytes(
            {
                "schema_version": "cross_sample_lineage_edge_v1",
                "processing_request_id": request_id,
                "predecessor_group_hypothesis_id": predecessor,
                "predecessor_identity_basis": predecessor_basis,
                "predecessor_group_hypothesis_ids": list(predecessor_ids),
                "successor_group_hypothesis_id": successor,
                "member_subject_ids": list(self.member_subject_ids),
            }
        )
        if self.basis_bytes != basis:
            raise CrossSampleContractError("lineage edge basis bytes are not exact")
        try:
            validate_typed_identity_basis(
                self.lineage_edge_id,
                prefix=_CROSS_SAMPLE_LINEAGE_EDGE_PREFIX,
                basis_bytes=basis,
                field="lineage_edge_id",
            )
        except ValueError as exc:
            raise CrossSampleContractError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class CrossSampleSuccessorApplication:
    status: CrossSampleNeighborhoodStatus
    predecessors: tuple[CrossSampleGroupIdentity, ...]
    successors: tuple[CrossSampleGroupIdentity, ...]
    lineage: tuple[CrossSampleLineageEdge, ...]
    reason: str

    def __post_init__(self) -> None:
        if (
            type(self.predecessors) is not tuple
            or type(self.successors) is not tuple
            or type(self.lineage) is not tuple
        ):
            raise CrossSampleContractError(
                "successor application closures must be immutable tuples"
            )
        if any(
            not isinstance(group, CrossSampleGroupIdentity)
            for group in self.predecessors
        ):
            raise CrossSampleContractError(
                "successor application requires typed predecessors"
            )
        predecessor_ids = tuple(
            group.group_hypothesis_id for group in self.predecessors
        )
        if predecessor_ids != tuple(sorted(predecessor_ids)):
            raise CrossSampleContractError(
                "successor application predecessors are not canonical-sorted"
            )
        validate_cross_sample_group_population(self.predecessors)
        if self.status == "unassessable":
            if (
                not self.predecessors
                or self.successors
                or self.lineage
                or self.reason != ("eligible_neighborhood_exceeds_budget")
            ):
                raise CrossSampleContractError(
                    "unassessable successor application is not atomic"
                )
            return
        if self.status != "assessable" or bool(self.successors) != bool(
            self.predecessors
        ):
            raise CrossSampleContractError("successor application status is invalid")
        successor_ids = tuple(group.group_hypothesis_id for group in self.successors)
        if successor_ids != tuple(sorted(successor_ids)):
            raise CrossSampleContractError(
                "successor application successors are not canonical-sorted"
            )
        predecessor_members = sorted(
            member.member_subject_id
            for group in self.predecessors
            for member in group.member_subjects
        )
        successor_members = sorted(
            member.member_subject_id
            for group in self.successors
            for member in group.member_subjects
        )
        if predecessor_members != successor_members:
            raise CrossSampleContractError(
                "successor application is not a complete partition"
            )
        expected_lineage = _expected_cross_sample_lineage(
            predecessors=self.predecessors,
            successors=self.successors,
        )
        if self.lineage != expected_lineage:
            raise CrossSampleContractError(
                "successor application lineage is not the complete atomic closure"
            )
        unchanged = set(predecessor_ids) == set(successor_ids)
        expected_reason = (
            "unchanged_membership_no_lineage" if unchanged else "membership_changed"
        )
        if self.reason != expected_reason:
            raise CrossSampleContractError(
                "successor application reason contradicts its transition"
            )


@dataclass(frozen=True, slots=True)
class CrossSampleRepairAdmissionApplication:
    """One CrossSample-owned atomic extension by an exact repaired member."""

    application_id: str
    repair_receipt_id: str
    authorization_id: str
    candidate_id: str
    predecessor: CrossSampleGroupIdentity
    successor: CrossSampleGroupIdentity
    admitted_member_subject_id: str
    lineage: tuple[CrossSampleLineageEdge, ...]
    basis_bytes: bytes
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _CROSS_SAMPLE_REPAIR_APPLICATION_MINT_TOKEN:
            raise CrossSampleContractError(
                "repair admission application must be minted by CrossSample"
            )
        if (
            type(self.predecessor) is not CrossSampleGroupIdentity
            or type(self.successor) is not CrossSampleGroupIdentity
        ):
            raise CrossSampleContractError(
                "repair admission application requires exact group identities"
            )
        predecessor = self.predecessor
        successor = self.successor
        validate_cross_sample_group_population((predecessor,))
        validate_cross_sample_group_population((successor,))
        if predecessor.processing_request_id != successor.processing_request_id:
            raise CrossSampleContractError(
                "repair admission application crossed processing request"
            )
        predecessor_by_id = {
            member.member_subject_id: member for member in predecessor.member_subjects
        }
        successor_by_id = {
            member.member_subject_id: member for member in successor.member_subjects
        }
        added_ids = set(successor_by_id) - set(predecessor_by_id)
        if (
            len(added_ids) != 1
            or added_ids != {self.admitted_member_subject_id}
            or any(
                successor_by_id[member_id] != member
                for member_id, member in predecessor_by_id.items()
            )
        ):
            raise CrossSampleContractError(
                "repair admission application is not one complete member extension"
            )
        admitted = successor_by_id[self.admitted_member_subject_id]
        if any(
            member.canonical_sample_identity_id == admitted.canonical_sample_identity_id
            for member in predecessor.member_subjects
        ):
            raise CrossSampleContractError(
                "repair admission application duplicates a canonical sample"
            )
        expected_lineage = _expected_cross_sample_lineage(
            predecessors=(predecessor,),
            successors=(successor,),
        )
        if self.lineage != expected_lineage:
            raise CrossSampleContractError(
                "repair admission lineage is not the complete atomic closure"
            )
        basis = _repair_admission_application_basis(
            repair_receipt_id=self.repair_receipt_id,
            authorization_id=self.authorization_id,
            candidate_id=self.candidate_id,
            predecessor=predecessor,
            successor=successor,
            admitted_member_subject_id=self.admitted_member_subject_id,
            lineage=self.lineage,
        )
        if self.basis_bytes != basis:
            raise CrossSampleContractError(
                "repair admission application basis bytes are not exact"
            )
        try:
            validate_typed_identity_basis(
                self.application_id,
                prefix=_CROSS_SAMPLE_REPAIR_APPLICATION_PREFIX,
                basis_bytes=basis,
                field="repair admission application_id",
            )
        except ValueError as exc:
            raise CrossSampleContractError(str(exc)) from exc


def _mint_cross_sample_lineage_edge(
    *,
    predecessor: CrossSampleGroupIdentity,
    successor: CrossSampleGroupIdentity,
    predecessor_group_hypothesis_ids: tuple[str, ...],
) -> CrossSampleLineageEdge:
    member_subject_ids = tuple(
        member.member_subject_id for member in successor.member_subjects
    )
    predecessor_basis = json.loads(predecessor.basis_bytes.decode("utf-8"))
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_lineage_edge_v1",
            "processing_request_id": predecessor.processing_request_id,
            "predecessor_group_hypothesis_id": predecessor.group_hypothesis_id,
            "predecessor_identity_basis": predecessor_basis,
            "predecessor_group_hypothesis_ids": list(predecessor_group_hypothesis_ids),
            "successor_group_hypothesis_id": successor.group_hypothesis_id,
            "member_subject_ids": list(member_subject_ids),
        }
    )
    return CrossSampleLineageEdge(
        lineage_edge_id=typed_identity_digest(
            _CROSS_SAMPLE_LINEAGE_EDGE_PREFIX,
            basis,
        ),
        predecessor_group_hypothesis_id=predecessor.group_hypothesis_id,
        successor_group_hypothesis_id=successor.group_hypothesis_id,
        processing_request_id=predecessor.processing_request_id,
        predecessor_identity_basis_bytes=predecessor.basis_bytes,
        predecessor_group_hypothesis_ids=predecessor_group_hypothesis_ids,
        member_subject_ids=member_subject_ids,
        basis_bytes=basis,
    )


def _expected_cross_sample_lineage(
    *,
    predecessors: tuple[CrossSampleGroupIdentity, ...],
    successors: tuple[CrossSampleGroupIdentity, ...],
) -> tuple[CrossSampleLineageEdge, ...]:
    lineage_edges: list[CrossSampleLineageEdge] = []
    for successor in successors:
        relevant_predecessors = tuple(
            predecessor
            for predecessor in predecessors
            if set(predecessor.member_subjects).intersection(successor.member_subjects)
            and predecessor.group_hypothesis_id != successor.group_hypothesis_id
        )
        predecessor_closure = tuple(
            predecessor.group_hypothesis_id for predecessor in relevant_predecessors
        )
        lineage_edges.extend(
            _mint_cross_sample_lineage_edge(
                predecessor=predecessor,
                successor=successor,
                predecessor_group_hypothesis_ids=predecessor_closure,
            )
            for predecessor in relevant_predecessors
        )
    return tuple(lineage_edges)


def _repair_admission_application_basis(
    *,
    repair_receipt_id: str,
    authorization_id: str,
    candidate_id: str,
    predecessor: CrossSampleGroupIdentity,
    successor: CrossSampleGroupIdentity,
    admitted_member_subject_id: str,
    lineage: tuple[CrossSampleLineageEdge, ...],
) -> bytes:
    return canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_repair_admission_application_v1",
            "repair_receipt_id": _required_text(
                repair_receipt_id,
                field="repair_receipt_id",
            ),
            "authorization_id": _required_text(
                authorization_id,
                field="authorization_id",
            ),
            "candidate_id": _required_text(candidate_id, field="candidate_id"),
            "predecessor_group_hypothesis_id": (predecessor.group_hypothesis_id),
            "successor_group_hypothesis_id": successor.group_hypothesis_id,
            "admitted_member_subject_id": _required_text(
                admitted_member_subject_id,
                field="admitted_member_subject_id",
            ),
            "lineage_edge_ids": [edge.lineage_edge_id for edge in lineage],
        }
    )


def _apply_repaired_nonmember_admission(
    *,
    predecessor: CrossSampleGroupIdentity,
    candidate_member: CrossSampleGroupMemberSubject,
    population: Sequence[CrossSampleGroupIdentity],
    repair_receipt_id: str,
    authorization_id: str,
    candidate_id: str,
) -> CrossSampleRepairAdmissionApplication:
    """Atomically extend one exact current group for the reconciliation owner."""

    if (
        type(predecessor) is not CrossSampleGroupIdentity
        or type(candidate_member) is not CrossSampleGroupMemberSubject
    ):
        raise CrossSampleContractError(
            "repair admission requires exact CrossSample identities"
        )
    canonical_population = validate_cross_sample_group_population(population)
    current_by_id = {group.group_hypothesis_id: group for group in canonical_population}
    if current_by_id.get(predecessor.group_hypothesis_id) is not predecessor:
        raise CrossSampleContractError(
            "repair admission predecessor is not the exact current group"
        )
    if candidate_member.processing_request_id != predecessor.processing_request_id:
        raise CrossSampleContractError(
            "repair admission candidate crossed processing request"
        )
    if any(
        member.canonical_sample_identity_id
        == candidate_member.canonical_sample_identity_id
        for member in predecessor.member_subjects
    ):
        raise CrossSampleContractError(
            "repair admission candidate duplicates a canonical sample"
        )
    successor = mint_cross_sample_group_identity(
        processing_request_id=predecessor.processing_request_id,
        member_subjects=(*predecessor.member_subjects, candidate_member),
    )
    lineage = _expected_cross_sample_lineage(
        predecessors=(predecessor,),
        successors=(successor,),
    )
    basis = _repair_admission_application_basis(
        repair_receipt_id=repair_receipt_id,
        authorization_id=authorization_id,
        candidate_id=candidate_id,
        predecessor=predecessor,
        successor=successor,
        admitted_member_subject_id=candidate_member.member_subject_id,
        lineage=lineage,
    )
    return CrossSampleRepairAdmissionApplication(
        application_id=typed_identity_digest(
            _CROSS_SAMPLE_REPAIR_APPLICATION_PREFIX,
            basis,
        ),
        repair_receipt_id=repair_receipt_id,
        authorization_id=authorization_id,
        candidate_id=candidate_id,
        predecessor=predecessor,
        successor=successor,
        admitted_member_subject_id=candidate_member.member_subject_id,
        lineage=lineage,
        basis_bytes=basis,
        _mint_token=_CROSS_SAMPLE_REPAIR_APPLICATION_MINT_TOKEN,
    )


def apply_coupled_successor_proposal(
    *,
    predecessors: Sequence[CrossSampleGroupIdentity],
    successor_member_sets: Sequence[Sequence[CrossSampleGroupMemberSubject]],
    edges: Sequence[CrossSampleNeighborhoodEdge],
    budget: CrossSampleNeighborhoodBudget,
    population: Sequence[CrossSampleGroupIdentity],
) -> CrossSampleSuccessorApplication:
    predecessor_groups = tuple(
        sorted(predecessors, key=lambda group: group.group_hypothesis_id)
    )
    if not predecessor_groups:
        raise CrossSampleContractError("successor requires a predecessor")
    validate_cross_sample_group_population(population)
    population_by_id = {group.group_hypothesis_id: group for group in population}
    if any(
        population_by_id.get(group.group_hypothesis_id) != group
        for group in predecessor_groups
    ):
        raise CrossSampleContractError(
            "successor predecessors are not an exact current population subset"
        )
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
            predecessors=predecessor_groups,
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
    excluded_pairs = {
        frozenset((edge.left_member_subject_id, edge.right_member_subject_id))
        for edge in edges
        if edge.disposition == "excluded"
    }
    if any(
        frozenset((left.member_subject_id, right.member_subject_id)) in excluded_pairs
        for member_set in proposed
        for left, right in combinations(member_set, 2)
    ):
        raise CrossSampleContractError(
            "successor proposal reopens an excluded member edge"
        )
    predecessor_by_members = {
        frozenset(
            member.member_subject_id for member in predecessor.member_subjects
        ): predecessor
        for predecessor in predecessor_groups
    }
    successors = tuple(
        sorted(
            (
                predecessor_by_members.get(
                    frozenset(member.member_subject_id for member in member_set)
                )
                or mint_cross_sample_group_identity(
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
    predecessor_ids_set = {group.group_hypothesis_id for group in predecessor_groups}
    successor_ids_set = {group.group_hypothesis_id for group in successors}
    if predecessor_ids_set == successor_ids_set:
        return CrossSampleSuccessorApplication(
            status="assessable",
            predecessors=predecessor_groups,
            successors=successors,
            lineage=(),
            reason="unchanged_membership_no_lineage",
        )
    lineage = _expected_cross_sample_lineage(
        predecessors=predecessor_groups,
        successors=successors,
    )
    return CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=predecessor_groups,
        successors=successors,
        lineage=lineage,
        reason="membership_changed",
    )


__all__ = (
    "CrossSampleClassifiedEdge",
    "CrossSampleConnectedNeighborhood",
    "CrossSampleCoupledProposalContext",
    "CrossSampleEdgeOwner",
    "CrossSampleLineageEdge",
    "CrossSampleNeighborhoodEdge",
    "CrossSampleRepairAdmissionApplication",
    "CrossSampleRepairAdmissionEdge",
    "CrossSampleSuccessorApplication",
    "_CROSS_SAMPLE_LINEAGE_EDGE_PREFIX",
    "_CROSS_SAMPLE_REPAIR_APPLICATION_MINT_TOKEN",
    "_CROSS_SAMPLE_REPAIR_APPLICATION_PREFIX",
    "_apply_repaired_nonmember_admission",
    "_components",
    "_expected_cross_sample_lineage",
    "_full_batch_weak_edge_is_eligible",
    "_mint_cross_sample_lineage_edge",
    "_repair_admission_application_basis",
    "_repair_admission_edge_facts",
    "_targeted_istd_rt_disposition",
    "apply_coupled_successor_proposal",
    "build_bounded_connected_neighborhood",
    "classify_cross_sample_initial_edge",
    "classify_cross_sample_initial_neighborhood_edge",
    "classify_cross_sample_repair_admission_edge",
)
