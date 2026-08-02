"""Shadow-only cross-sample identity model and split-lineage settlement.

This module consumes existing Alignment, M1 cell, and M2 Backfill facts without
changing any of them.  It grants no Matrix, writer, selected-value, or product
authority.  Callers must supply preregistered identity and trajectory evidence;
this owner deliberately contains no universal scientific threshold.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Literal, final

from xic_extractor.alignment.cell_observations import CellObservationRef
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillCellDecision,
    ConservativeBackfillGroupFacts,
    ConservativeBackfillRecoveryCandidateUniverse,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.drift_evidence import LocalTrajectoryAssessment
from xic_extractor.peak_detection.observations import PeakObservationRef

VALIDATION_STATUS = "shadow_ready"
PRODUCT_AUTHORITY = "none"
_IDENTITY_RELATION_MINT_TOKEN = object()
_IDENTITY_SETTLEMENT_MINT_TOKEN = object()
_SPLIT_COMPONENT_MINT_TOKEN = object()
_SPLIT_COMPONENT_PARTITION_MINT_TOKEN = object()
_SPLIT_LINEAGE_MINT_TOKEN = object()
_SPLIT_CHILD_CENSUS_MINT_TOKEN = object()
_COMPONENT_AGGREGATION_MINT_TOKEN = object()

IdentityRelation = Literal[
    "same_identity",
    "different_identities",
    "artifact",
    "unresolved",
]
ResolvedIdentityRelation = Literal[
    "same_identity",
    "different_identities",
    "artifact",
]
ComponentPattern = Literal[
    "single_component",
    "repeated_multiple_components",
    "unreliable_feature",
]
IdentityModel = Literal["H1a", "H1b", "H2", "H3"]
IdentitySettlementState = Literal["resolved", "scientific_uncertainty"]
SplitLifecycleState = Literal[
    "split_watch",
    "split_candidate",
    "split_supported",
]


@dataclass(frozen=True, slots=True)
class IdentityRelationEvidence:
    """Owner-bound direct evidence for one resolved identity relation."""

    group: CrossSamplePeakGroupHypothesis
    relation: ResolvedIdentityRelation
    evidence_refs: tuple[str, ...]
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _IDENTITY_RELATION_MINT_TOKEN:
            raise ValueError("identity relation evidence must be minted by its owner")
        if not isinstance(self.group, CrossSamplePeakGroupHypothesis):
            raise ValueError("identity relation evidence requires a group owner")
        if self.relation not in {
            "same_identity",
            "different_identities",
            "artifact",
        }:
            raise ValueError("unsupported resolved identity relation")
        _require_canonical_text(self.evidence_refs, field="evidence_refs")
        _validate_shadow_authority(
            validation_status=self.validation_status,
            product_authority=self.product_authority,
        )


def mint_identity_relation_evidence(
    *,
    group: CrossSamplePeakGroupHypothesis,
    relation: ResolvedIdentityRelation,
    evidence_refs: tuple[str, ...],
) -> IdentityRelationEvidence:
    """Bind a preregistered scientific relation to one exact group owner."""

    return IdentityRelationEvidence(
        group=group,
        relation=relation,
        evidence_refs=evidence_refs,
        _mint_token=_IDENTITY_RELATION_MINT_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class IdentityModelEvidence:
    """Typed evidence for one preregistered H1a/H1b/H2/H3 comparison."""

    group: CrossSamplePeakGroupHypothesis
    identity_evidence: IdentityRelationEvidence | None
    component_pattern: ComponentPattern
    component_partition: SplitComponentPartitionEvidence | None
    trajectory: LocalTrajectoryAssessment
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.group, CrossSamplePeakGroupHypothesis):
            raise ValueError("identity evidence requires a cross-sample group owner")
        if self.identity_evidence is not None:
            if not isinstance(self.identity_evidence, IdentityRelationEvidence):
                raise ValueError("identity_evidence must be owner-bound")
            if self.identity_evidence.group is not self.group:
                raise ValueError("identity evidence references a different group")
        if self.component_pattern not in {
            "single_component",
            "repeated_multiple_components",
            "unreliable_feature",
        }:
            raise ValueError("unsupported component pattern")
        if self.component_partition is not None:
            if not isinstance(
                self.component_partition,
                SplitComponentPartitionEvidence,
            ):
                raise ValueError("component_partition must be owner-bound")
            if (
                self.component_partition.parent_group.group_context.group
                is not self.group
            ):
                raise ValueError("component partition references a different group")
            if self.component_partition.component_pattern != self.component_pattern:
                raise ValueError("component pattern disagrees with its exact partition")
        elif self.component_pattern == "repeated_multiple_components":
            raise ValueError(
                "repeated component pattern requires owner-bound partition evidence"
            )
        if not isinstance(self.trajectory, LocalTrajectoryAssessment):
            raise ValueError("identity evidence requires local trajectory assessment")
        if self.trajectory.sample_stems != _group_sample_stems(self.group):
            raise ValueError(
                "local trajectory samples must equal the group owner population"
            )
        _require_canonical_text(self.reason_codes, field="reason_codes")

    @property
    def identity_relation(self) -> IdentityRelation:
        if self.identity_evidence is None:
            return "unresolved"
        return self.identity_evidence.relation

    @property
    def direct_identity_evidence_refs(self) -> tuple[str, ...]:
        if self.identity_evidence is None:
            return ()
        return self.identity_evidence.evidence_refs


@dataclass(frozen=True, slots=True)
class IdentitySettlementDecision:
    """No-write model decision; numeric projection is permanently disabled."""

    evidence: IdentityModelEvidence
    model: IdentityModel | None
    state: IdentitySettlementState
    allow_component_support_or: bool
    allow_area_aggregation: bool
    reason_codes: tuple[str, ...]
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _IDENTITY_SETTLEMENT_MINT_TOKEN:
            raise ValueError("identity decisions must be minted by their owner")
        _validate_shadow_authority(
            validation_status=self.validation_status,
            product_authority=self.product_authority,
        )

    @property
    def numeric_projection_allowed(self) -> bool:
        return False


def settle_identity_model(
    evidence: IdentityModelEvidence,
) -> IdentitySettlementDecision:
    """Resolve only evidence-complete models; otherwise preserve uncertainty."""

    if not isinstance(evidence, IdentityModelEvidence):
        raise TypeError("identity settlement requires IdentityModelEvidence")
    reasons = set(evidence.reason_codes)
    reasons.update(evidence.trajectory.reason_codes)

    model: IdentityModel | None = None
    if evidence.identity_relation == "artifact":
        model = "H3"
        reasons.add("artifact_or_no_reliable_feature")
    elif evidence.identity_relation == "different_identities":
        model = "H2"
        reasons.add("direct_evidence_resolves_two_identities")
    elif evidence.identity_relation == "same_identity":
        if evidence.trajectory.status == "conflict":
            reasons.add("identity_resolution_blocked_by_trajectory_conflict")
        elif evidence.trajectory.status != "assessable":
            reasons.add("identity_resolution_requires_assessable_trajectory")
        elif evidence.component_pattern == "single_component":
            model = "H1a"
            reasons.add("same_identity_single_component_or_rt_drift")
        elif evidence.component_pattern == "repeated_multiple_components":
            model = "H1b"
            reasons.add("same_identity_multiple_components")
        else:
            reasons.add("same_identity_feature_unreliable")
    else:
        reasons.add("direct_identity_evidence_insufficient")
        if evidence.component_pattern == "repeated_multiple_components":
            reasons.add("one_component_model_disfavored")
            reasons.add("repeated_components_do_not_resolve_identity")

    state: IdentitySettlementState = (
        "resolved" if model is not None else "scientific_uncertainty"
    )
    if state == "scientific_uncertainty":
        reasons.add("scientific_uncertainty")
    allow_same_identity_aggregation = model == "H1b"
    return IdentitySettlementDecision(
        evidence=evidence,
        model=model,
        state=state,
        allow_component_support_or=allow_same_identity_aggregation,
        allow_area_aggregation=allow_same_identity_aggregation,
        reason_codes=tuple(sorted(reasons)),
        _mint_token=_IDENTITY_SETTLEMENT_MINT_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class SplitComponentEvidence:
    """One distinct component with exact local observations and direct cells."""

    parent_group: ConservativeBackfillGroupFacts
    component_id: str
    peak_observations: tuple[PeakObservationRef, ...]
    direct_support_cells: tuple[CellObservationRef, ...]
    membership_evidence_refs: tuple[str, ...]
    candidate_universe_artifact_ids: tuple[str, ...]
    candidate_mapping_decisions: tuple[ConservativeBackfillCellDecision, ...]
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _SPLIT_COMPONENT_MINT_TOKEN:
            raise ValueError("split component evidence must be minted by its owner")
        if not isinstance(self.parent_group, ConservativeBackfillGroupFacts):
            raise ValueError("split components require exact parent M2 facts")
        _required_text(self.component_id, field="component_id")
        if not self.peak_observations or any(
            not isinstance(observation, PeakObservationRef)
            for observation in self.peak_observations
        ):
            raise ValueError("components require physical peak observations")
        observation_ids = tuple(
            observation.content.peak_observation_content_id
            for observation in self.peak_observations
        )
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("component peak observations must be unique")
        if any(
            not isinstance(cell, CellObservationRef)
            for cell in self.direct_support_cells
        ):
            raise ValueError("direct_support_cells must be CellObservationRef values")
        if len({id(cell) for cell in self.direct_support_cells}) != len(
            self.direct_support_cells
        ):
            raise ValueError("direct support cells must be unique")
        for cell in self.direct_support_cells:
            if cell.content.method_result != "SUPPORT" or cell.peak_observation is None:
                raise ValueError("component direct evidence must be a SUPPORT cell")
            if not any(
                cell.peak_observation is observation
                for observation in self.peak_observations
            ):
                raise ValueError(
                    "component SUPPORT cell must retain its exact peak observation"
                )
        _require_canonical_text(
            self.membership_evidence_refs,
            field="membership_evidence_refs",
        )
        if self.candidate_universe_artifact_ids:
            _require_canonical_text(
                self.candidate_universe_artifact_ids,
                field="candidate_universe_artifact_ids",
            )
        if not isinstance(self.candidate_mapping_decisions, tuple) or any(
            not isinstance(decision, ConservativeBackfillCellDecision)
            for decision in self.candidate_mapping_decisions
        ):
            raise ValueError(
                "candidate_mapping_decisions must be owner-minted M2 decisions"
            )
        _validate_shadow_authority(
            validation_status=self.validation_status,
            product_authority=self.product_authority,
        )


def mint_split_component_evidence(
    *,
    parent_group: ConservativeBackfillGroupFacts,
    candidate_universes: tuple[
        ConservativeBackfillRecoveryCandidateUniverse,
        ...,
    ],
    candidate_mapping_decisions: tuple[ConservativeBackfillCellDecision, ...],
    component_id: str,
    peak_observations: tuple[PeakObservationRef, ...],
    direct_support_cells: tuple[CellObservationRef, ...],
    membership_evidence_refs: tuple[str, ...],
) -> SplitComponentEvidence:
    """Bind explicit component membership to one exact parent candidate scope."""

    universes = _validate_candidate_universes(parent_group, candidate_universes)
    mapping_decisions = _validate_candidate_mapping_decisions(
        universes,
        candidate_mapping_decisions,
    )
    parent_cells = {id(cell) for cell in parent_group.source_cells}
    if any(id(cell) not in parent_cells for cell in direct_support_cells):
        raise ValueError("component direct evidence is outside parent M1 cells")
    direct_observations = {
        id(cell.peak_observation)
        for cell in direct_support_cells
        if cell.peak_observation is not None
    }
    used_universe_ids: set[str] = set()
    for observation in peak_observations:
        if id(observation) in direct_observations:
            continue
        matching_universes = tuple(
            universe
            for universe in universes
            if any(observation is peak for peak in universe.candidate_peaks)
        )
        if len(matching_universes) != 1:
            raise ValueError(
                "component observation requires one exact parent candidate universe"
            )
        _require_candidate_allocation(
            parent_group,
            matching_universes[0],
            observation,
            mapping_decisions,
        )
        used_universe_ids.add(matching_universes[0].artifact.evidence_artifact_id)
    return SplitComponentEvidence(
        parent_group=parent_group,
        component_id=component_id,
        peak_observations=peak_observations,
        direct_support_cells=direct_support_cells,
        membership_evidence_refs=membership_evidence_refs,
        candidate_universe_artifact_ids=tuple(sorted(used_universe_ids)),
        candidate_mapping_decisions=mapping_decisions,
        _mint_token=_SPLIT_COMPONENT_MINT_TOKEN,
    )


@final
@dataclass(frozen=True, slots=True)
class SplitComponentPartitionEvidence:
    """Owner-minted exact component partition and M2 allocation closure."""

    parent_group: ConservativeBackfillGroupFacts
    components: tuple[SplitComponentEvidence, ...]
    candidate_universes: tuple[ConservativeBackfillRecoveryCandidateUniverse, ...]
    candidate_mapping_decisions: tuple[ConservativeBackfillCellDecision, ...]
    component_pattern: Literal["single_component", "repeated_multiple_components"]
    evidence_refs: tuple[str, ...]
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("SplitComponentPartitionEvidence is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _SPLIT_COMPONENT_PARTITION_MINT_TOKEN:
            raise ValueError("component partitions must be minted by their owner")
        _validate_shadow_authority(
            validation_status=self.validation_status,
            product_authority=self.product_authority,
        )


def mint_split_component_partition_evidence(
    *,
    parent_group: ConservativeBackfillGroupFacts,
    components: tuple[SplitComponentEvidence, ...],
    candidate_universes: tuple[
        ConservativeBackfillRecoveryCandidateUniverse,
        ...,
    ],
    candidate_mapping_decisions: tuple[ConservativeBackfillCellDecision, ...],
    component_pattern: Literal["single_component", "repeated_multiple_components"],
    evidence_refs: tuple[str, ...],
) -> SplitComponentPartitionEvidence:
    """Seal one exact partition before model settlement or split lifecycle use."""

    universes = _validate_candidate_universes(parent_group, candidate_universes)
    mapping_decisions = _validate_candidate_mapping_decisions(
        universes,
        candidate_mapping_decisions,
    )
    _validate_components(
        parent_group,
        components,
        universes,
        mapping_decisions,
    )
    _require_canonical_text(evidence_refs, field="partition_evidence_refs")
    if component_pattern == "single_component":
        if len(components) != 1:
            raise ValueError("single component pattern requires one exact component")
    elif component_pattern == "repeated_multiple_components":
        if len(components) < 2:
            raise ValueError(
                "repeated component pattern requires at least two exact components"
            )
    else:
        raise ValueError("unsupported partition component pattern")
    return SplitComponentPartitionEvidence(
        parent_group=parent_group,
        components=components,
        candidate_universes=universes,
        candidate_mapping_decisions=mapping_decisions,
        component_pattern=component_pattern,
        evidence_refs=evidence_refs,
        _mint_token=_SPLIT_COMPONENT_PARTITION_MINT_TOKEN,
    )


@final
@dataclass(frozen=True, slots=True)
class SplitChildSupportCensus:
    """Child-local S/K census; it never inherits the parent activation result."""

    parent_group_hypothesis_id: str
    child_group_hypothesis_id: str
    component_id: str
    peak_observations: tuple[PeakObservationRef, ...]
    direct_support_cells: tuple[CellObservationRef, ...]
    activation_eligible_sample_ids: tuple[str, ...]
    direct_support_sample_ids: tuple[str, ...]
    sample_count_n: int
    minimum_support_k: int
    direct_support_count_s: int
    meets_direct_support_threshold: bool
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("SplitChildSupportCensus is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _SPLIT_CHILD_CENSUS_MINT_TOKEN:
            raise ValueError("split child censuses must be minted by their owner")
        _required_text(
            self.parent_group_hypothesis_id,
            field="parent_group_hypothesis_id",
        )
        _required_text(
            self.child_group_hypothesis_id,
            field="child_group_hypothesis_id",
        )
        _required_text(self.component_id, field="component_id")
        if not isinstance(self.peak_observations, tuple) or any(
            not isinstance(observation, PeakObservationRef)
            for observation in self.peak_observations
        ):
            raise ValueError("child census requires typed peak observations")
        if not isinstance(self.direct_support_cells, tuple) or any(
            not isinstance(cell, CellObservationRef)
            for cell in self.direct_support_cells
        ):
            raise ValueError("child census requires typed direct SUPPORT cells")
        if self.activation_eligible_sample_ids != tuple(
            sorted(set(self.activation_eligible_sample_ids))
        ):
            raise ValueError("activation eligible sample IDs must be canonical")
        expected_support_ids = tuple(
            sorted(
                {
                    cell.content.canonical_sample_identity_id
                    for cell in self.direct_support_cells
                    if (
                        cell.content.method_result == "SUPPORT"
                        and cell.content.canonical_sample_identity_id
                        in self.activation_eligible_sample_ids
                    )
                }
            )
        )
        if self.direct_support_sample_ids != expected_support_ids:
            raise ValueError("child support IDs must match exact direct SUPPORT cells")
        if self.sample_count_n != len(self.activation_eligible_sample_ids):
            raise ValueError("child sample count must match its eligible population")
        if self.direct_support_count_s != len(self.direct_support_sample_ids):
            raise ValueError("child support count must match its support IDs")
        if self.minimum_support_k < 1:
            raise ValueError("child minimum support count must be positive")
        if self.meets_direct_support_threshold is not (
            self.direct_support_count_s >= self.minimum_support_k
        ):
            raise ValueError("child threshold result must be owner-derived")
        _validate_shadow_authority(
            validation_status=self.validation_status,
            product_authority=self.product_authority,
        )


@dataclass(frozen=True, slots=True)
class SplitLineageDecision:
    """Explicit lifecycle and parent/child lineage with no product authority."""

    lifecycle_state: SplitLifecycleState
    parent_group: ConservativeBackfillGroupFacts
    identity_settlement: IdentitySettlementDecision
    component_partition: SplitComponentPartitionEvidence
    components: tuple[SplitComponentEvidence, ...]
    candidate_universes: tuple[ConservativeBackfillRecoveryCandidateUniverse, ...]
    evidence_refs: tuple[str, ...]
    parent_superseded: bool
    blocks_parent_backfill: bool
    blocks_parent_numeric_projection: bool
    children: tuple[SplitChildSupportCensus, ...]
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _SPLIT_LINEAGE_MINT_TOKEN:
            raise ValueError("split lineage decisions must be minted by their owner")
        _validate_shadow_authority(
            validation_status=self.validation_status,
            product_authority=self.product_authority,
        )

    @property
    def numeric_projection_allowed(self) -> bool:
        return False


def assess_split_lineage(
    *,
    parent_group: ConservativeBackfillGroupFacts,
    component_partition: SplitComponentPartitionEvidence,
    lifecycle_state: SplitLifecycleState,
    identity_settlement: IdentitySettlementDecision,
    evidence_refs: tuple[str, ...],
) -> SplitLineageDecision:
    """Build one explicit split state; no time/retry input can advance it."""

    if not isinstance(parent_group, ConservativeBackfillGroupFacts):
        raise TypeError("split lineage requires frozen M2 parent facts")
    if not isinstance(identity_settlement, IdentitySettlementDecision):
        raise TypeError("split lineage requires a typed identity settlement")
    if identity_settlement.evidence.group is not parent_group.group_context.group:
        raise ValueError("identity settlement references a different parent group")
    if not isinstance(component_partition, SplitComponentPartitionEvidence):
        raise TypeError("split lineage requires an owner-minted component partition")
    if component_partition.parent_group is not parent_group:
        raise ValueError("component partition references a different parent group")
    if identity_settlement.evidence.component_partition is not component_partition:
        raise ValueError(
            "identity settlement and split lineage require the same exact partition"
        )
    components = component_partition.components
    candidate_universes = component_partition.candidate_universes
    _validate_components(
        parent_group,
        components,
        candidate_universes,
        component_partition.candidate_mapping_decisions,
    )
    _require_canonical_text(evidence_refs, field="evidence_refs")
    if lifecycle_state not in {
        "split_watch",
        "split_candidate",
        "split_supported",
    }:
        raise ValueError("unsupported split lifecycle state")
    if lifecycle_state != "split_watch" and len(components) < 2:
        raise ValueError(
            "candidate or supported split requires at least two components"
        )
    if lifecycle_state in {"split_candidate", "split_supported"}:
        _require_parent_support_preservation(parent_group, components)

    parent_superseded = lifecycle_state == "split_supported"
    blocks_parent = lifecycle_state in {"split_candidate", "split_supported"}
    children: tuple[SplitChildSupportCensus, ...] = ()
    if lifecycle_state == "split_supported":
        if identity_settlement.state != "resolved" or identity_settlement.model not in {
            "H1b",
            "H2",
        }:
            raise ValueError("split_supported requires resolved H1b or H2")
        children = tuple(
            _child_support_census(parent_group, component) for component in components
        )

    return SplitLineageDecision(
        lifecycle_state=lifecycle_state,
        parent_group=parent_group,
        identity_settlement=identity_settlement,
        component_partition=component_partition,
        components=components,
        candidate_universes=candidate_universes,
        evidence_refs=evidence_refs,
        parent_superseded=parent_superseded,
        blocks_parent_backfill=blocks_parent,
        blocks_parent_numeric_projection=blocks_parent,
        children=children,
        _mint_token=_SPLIT_LINEAGE_MINT_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class SameIdentityComponentAggregation:
    """Auditable H1b-only OR/area summary; not a public numeric projection."""

    split_lineage: SplitLineageDecision
    identity_evidence: IdentityRelationEvidence
    component_ids: tuple[str, ...]
    component_observations: tuple[PeakObservationRef, ...]
    direct_support_cells: tuple[CellObservationRef, ...]
    cid_nl_support: bool
    area_by_sample: tuple[tuple[str, float], ...]
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _COMPONENT_AGGREGATION_MINT_TOKEN:
            raise ValueError("component aggregations must be minted by their owner")
        _validate_shadow_authority(
            validation_status=self.validation_status,
            product_authority=self.product_authority,
        )

    @property
    def numeric_projection_allowed(self) -> bool:
        return False


def aggregate_same_identity_components(
    split_lineage: SplitLineageDecision,
) -> SameIdentityComponentAggregation | None:
    """Apply component OR/sum only after exact H1b split lineage resolves."""

    if not isinstance(split_lineage, SplitLineageDecision):
        raise TypeError("component aggregation requires owner-minted split lineage")
    settlement = split_lineage.identity_settlement
    identity_evidence = settlement.evidence.identity_evidence
    if (
        split_lineage.lifecycle_state != "split_supported"
        or settlement.state != "resolved"
        or settlement.model != "H1b"
        or settlement.evidence.component_partition
        is not split_lineage.component_partition
        or identity_evidence is None
        or identity_evidence.relation != "same_identity"
        or not settlement.allow_component_support_or
        or not settlement.allow_area_aggregation
    ):
        return None
    components = split_lineage.components
    _validate_component_collection(components)
    observations = tuple(
        observation
        for component in components
        for observation in component.peak_observations
    )
    support_cells = tuple(
        cell for component in components for cell in component.direct_support_cells
    )
    area_by_sample: dict[str, float] = {}
    for observation in observations:
        sample_id = observation.content.canonical_sample_identity_id
        area_by_sample[sample_id] = area_by_sample.get(sample_id, 0.0) + float(
            observation.content.integration.area_raw_counts_seconds
        )
    return SameIdentityComponentAggregation(
        split_lineage=split_lineage,
        identity_evidence=identity_evidence,
        component_ids=tuple(component.component_id for component in components),
        component_observations=observations,
        direct_support_cells=support_cells,
        cid_nl_support=bool(support_cells),
        area_by_sample=tuple(sorted(area_by_sample.items())),
        _mint_token=_COMPONENT_AGGREGATION_MINT_TOKEN,
    )


def _validate_components(
    parent_group: ConservativeBackfillGroupFacts,
    components: tuple[SplitComponentEvidence, ...],
    candidate_universes: tuple[
        ConservativeBackfillRecoveryCandidateUniverse,
        ...,
    ],
    candidate_mapping_decisions: tuple[ConservativeBackfillCellDecision, ...],
) -> None:
    _validate_component_collection(components)
    parent_cells = {id(cell) for cell in parent_group.source_cells}
    universes = _validate_candidate_universes(parent_group, candidate_universes)
    mapping_decisions = _validate_candidate_mapping_decisions(
        universes,
        candidate_mapping_decisions,
    )
    universes_by_id = {
        universe.artifact.evidence_artifact_id: universe for universe in universes
    }
    for component in components:
        if component.parent_group is not parent_group:
            raise ValueError("component evidence references a different parent group")
        if len(component.candidate_mapping_decisions) != len(mapping_decisions) or any(
            component_decision is not partition_decision
            for component_decision, partition_decision in zip(
                component.candidate_mapping_decisions,
                mapping_decisions,
                strict=True,
            )
        ):
            raise ValueError(
                "component evidence references a different M2 mapping closure"
            )
        if any(id(cell) not in parent_cells for cell in component.direct_support_cells):
            raise ValueError("component direct evidence is outside parent M1 cells")
        if any(
            universe_id not in universes_by_id
            for universe_id in component.candidate_universe_artifact_ids
        ):
            raise ValueError("component references an undeclared candidate universe")
        direct_observation_ids = {
            id(cell.peak_observation)
            for cell in component.direct_support_cells
            if cell.peak_observation is not None
        }
        used_universe_ids = {
            universe_id
            for observation in component.peak_observations
            if id(observation) not in direct_observation_ids
            for universe_id, universe in universes_by_id.items()
            if any(observation is peak for peak in universe.candidate_peaks)
        }
        if used_universe_ids != set(component.candidate_universe_artifact_ids):
            raise ValueError(
                "component observations do not close their exact candidate universes"
            )
        for observation in component.peak_observations:
            if id(observation) in direct_observation_ids:
                continue
            matches = tuple(
                universe_id
                for universe_id in component.candidate_universe_artifact_ids
                if any(
                    observation is peak
                    for peak in universes_by_id[universe_id].candidate_peaks
                )
            )
            if len(matches) != 1:
                raise ValueError(
                    "component observation is outside its exact membership scope"
                )
            _require_candidate_allocation(
                parent_group,
                universes_by_id[matches[0]],
                observation,
                mapping_decisions,
            )


def _validate_candidate_universes(
    parent_group: ConservativeBackfillGroupFacts,
    candidate_universes: tuple[
        ConservativeBackfillRecoveryCandidateUniverse,
        ...,
    ],
) -> tuple[ConservativeBackfillRecoveryCandidateUniverse, ...]:
    if not isinstance(candidate_universes, tuple) or any(
        not isinstance(universe, ConservativeBackfillRecoveryCandidateUniverse)
        for universe in candidate_universes
    ):
        raise ValueError("candidate universes must be owner-bound M2 receipts")
    universe_keys = tuple(
        universe.sample.identity.sample_identity_id for universe in candidate_universes
    )
    if universe_keys != tuple(sorted(set(universe_keys))):
        raise ValueError(
            "candidate universes must be unique by sample and canonical-sorted"
        )
    parent_target = (
        parent_group.group_context.target_key_id,
        parent_group.group_context.target_definition_id,
    )
    parent_attempt = parent_group.group_context.processing_attempt
    for universe in candidate_universes:
        if not any(facts is parent_group for facts in universe.group_facts):
            raise ValueError("candidate universe references a different parent group")
        if universe.target_key != parent_target:
            raise ValueError("candidate universe references a different target")
        if universe.processing_attempt is not parent_attempt:
            raise ValueError("candidate universe references a different attempt")
    return candidate_universes


def _validate_candidate_mapping_decisions(
    candidate_universes: tuple[
        ConservativeBackfillRecoveryCandidateUniverse,
        ...,
    ],
    candidate_mapping_decisions: tuple[ConservativeBackfillCellDecision, ...],
) -> tuple[ConservativeBackfillCellDecision, ...]:
    if not isinstance(candidate_mapping_decisions, tuple) or any(
        not isinstance(decision, ConservativeBackfillCellDecision)
        for decision in candidate_mapping_decisions
    ):
        raise ValueError("candidate mapping closure requires owner-minted M2 decisions")
    if not candidate_universes:
        if candidate_mapping_decisions:
            raise ValueError("candidate mapping decisions require declared universes")
        return ()
    for decision in candidate_mapping_decisions:
        if decision.candidate_universe is None or not any(
            decision.candidate_universe is universe for universe in candidate_universes
        ):
            raise ValueError(
                "candidate mapping decision references an undeclared universe"
            )
    decision_keys = tuple(
        (
            id(decision.candidate_universe),
            id(decision.group_facts),
        )
        for decision in candidate_mapping_decisions
    )
    if len(decision_keys) != len(set(decision_keys)):
        raise ValueError(
            "candidate mapping closure contains duplicate parent decisions"
        )
    for universe in candidate_universes:
        bound = tuple(
            decision
            for decision in candidate_mapping_decisions
            if decision.candidate_universe is universe
        )
        requires_m2_allocation = len(universe.group_facts) > 1
        if (requires_m2_allocation or bound) and (
            len(bound) != len(universe.group_facts)
            or any(
                not any(decision.group_facts is facts for decision in bound)
                for facts in universe.group_facts
            )
        ):
            raise ValueError(
                "candidate mapping closure must cover the universe's exact parent set"
            )
    return tuple(
        sorted(
            candidate_mapping_decisions,
            key=lambda decision: (
                (
                    decision.candidate_universe.artifact.evidence_artifact_id
                    if decision.candidate_universe is not None
                    else ""
                ),
                decision.group_facts.group_context.group.group_hypothesis_id,
            ),
        )
    )


def _require_candidate_allocation(
    parent_group: ConservativeBackfillGroupFacts,
    candidate_universe: ConservativeBackfillRecoveryCandidateUniverse,
    observation: PeakObservationRef,
    candidate_mapping_decisions: tuple[ConservativeBackfillCellDecision, ...],
) -> None:
    if len(candidate_universe.group_facts) == 1:
        return
    parent_decisions = tuple(
        decision
        for decision in candidate_mapping_decisions
        if (
            decision.candidate_universe is candidate_universe
            and decision.group_facts is parent_group
        )
    )
    if len(parent_decisions) != 1:
        raise ValueError(
            "multi-parent component membership requires one exact M2 parent decision"
        )
    decision = parent_decisions[0]
    if (
        decision.disposition != "candidate_mapped"
        or decision.matched_candidate is None
        or decision.matched_candidate.peak_observation is not observation
    ):
        raise ValueError(
            "multi-parent component membership requires the exact M2 mapped peak"
        )


def _validate_component_collection(
    components: tuple[SplitComponentEvidence, ...],
) -> None:
    if (
        not isinstance(components, tuple)
        or not components
        or any(
            not isinstance(component, SplitComponentEvidence)
            for component in components
        )
    ):
        raise ValueError("components must be a non-empty typed tuple")
    component_ids = tuple(component.component_id for component in components)
    if component_ids != tuple(sorted(set(component_ids))):
        raise ValueError("component IDs must be unique and canonical-sorted")
    observation_ids = [
        observation.content.peak_observation_content_id
        for component in components
        for observation in component.peak_observations
    ]
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError("a physical peak cannot be transferred across components")
    support_cell_ids = [
        id(cell) for component in components for cell in component.direct_support_cells
    ]
    if len(support_cell_ids) != len(set(support_cell_ids)):
        raise ValueError(
            "a direct SUPPORT cell cannot be transferred across components"
        )


def _require_parent_support_preservation(
    parent_group: ConservativeBackfillGroupFacts,
    components: tuple[SplitComponentEvidence, ...],
) -> None:
    parent_support_ids = {
        id(cell)
        for cell in parent_group.source_cells
        if cell.content.method_result == "SUPPORT"
    }
    component_support_ids = {
        id(cell) for component in components for cell in component.direct_support_cells
    }
    if component_support_ids != parent_support_ids:
        raise ValueError(
            "candidate and supported splits must preserve every parent direct "
            "SUPPORT cell"
        )


def _child_support_census(
    parent_group: ConservativeBackfillGroupFacts,
    component: SplitComponentEvidence,
) -> SplitChildSupportCensus:
    eligible_ids = parent_group.activation_eligible_sample_ids
    support_ids = tuple(
        sorted(
            {
                cell.content.canonical_sample_identity_id
                for cell in component.direct_support_cells
                if cell.content.canonical_sample_identity_id in eligible_ids
            }
        )
    )
    support_count = len(support_ids)
    return SplitChildSupportCensus(
        parent_group_hypothesis_id=(
            parent_group.group_context.group.group_hypothesis_id
        ),
        child_group_hypothesis_id=(
            f"{parent_group.group_context.group.group_hypothesis_id}"
            f"::shadow-component::{component.component_id}"
        ),
        component_id=component.component_id,
        peak_observations=component.peak_observations,
        direct_support_cells=component.direct_support_cells,
        activation_eligible_sample_ids=eligible_ids,
        direct_support_sample_ids=support_ids,
        sample_count_n=parent_group.sample_count_n,
        minimum_support_k=parent_group.minimum_support_k,
        direct_support_count_s=support_count,
        meets_direct_support_threshold=(
            support_count >= parent_group.minimum_support_k
        ),
        _mint_token=_SPLIT_CHILD_CENSUS_MINT_TOKEN,
    )


def _require_canonical_text(values: tuple[str, ...], *, field: str) -> None:
    if (
        not isinstance(values, tuple)
        or not values
        or any(not isinstance(value, str) or not value.strip() for value in values)
        or values != tuple(sorted(set(values)))
    ):
        raise ValueError(f"{field} must be non-empty, unique, and canonical-sorted")


def _group_sample_stems(
    group: CrossSamplePeakGroupHypothesis,
) -> tuple[str, ...]:
    sample_stems = tuple(sorted({owner.sample_stem for owner in group.owners}))
    _require_canonical_text(sample_stems, field="group_sample_stems")
    return sample_stems


def _validate_shadow_authority(
    *,
    validation_status: str,
    product_authority: str,
) -> None:
    if validation_status != VALIDATION_STATUS:
        raise ValueError(
            "identity settlement validation_status must remain shadow_ready"
        )
    if product_authority != PRODUCT_AUTHORITY:
        raise ValueError("identity settlement cannot grant product authority")


def _required_text(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()
