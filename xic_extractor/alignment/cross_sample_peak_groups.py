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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from xic_extractor.alignment.config import AlignmentConfig
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

if TYPE_CHECKING:
    from xic_extractor.alignment.owner_clustering import OwnerAlignedFeature

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
      Identical to ``group_hypothesis_id`` in current implementation.
    - ``feature_family_id`` property: legacy output alias for ``public_family_id``.
    """

    group_hypothesis_id: str
    public_family_id: str
    owner_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    event_member_count: int
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


def construct_cross_sample_peak_group_hypotheses(
    owners: tuple[SampleLocalMS1Owner, ...] | list[SampleLocalMS1Owner],
    *,
    config: AlignmentConfig,
    sample_universe: frozenset[str] | None = None,
    injection_order: Mapping[str, int] | None = None,
    drift_lookup: DriftLookupProtocol | None = None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None = None,
    edge_evaluator: OwnerEdgeEvaluator = evaluate_owner_edge,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    clean = tuple(owner for owner in owners if not owner.identity_conflict)
    conflict_hypotheses = tuple(
        _hypothesis_from_owner_group(
            (owner,),
            group_hypothesis_id=f"FAM{index:06d}",
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
    resolved_duplicate_owner_groups = (
        _resolve_duplicate_owner_groups(groups, config=config)
        if sample_universe is not None
        else {}
    )
    group_ids = tuple(
        f"FAM{index + len(conflict_hypotheses):06d}"
        for index in range(1, len(groups) + 1)
    )
    resolved_group_ids_by_winner: dict[int, list[str]] = {}
    for subordinate, winner in sorted(resolved_duplicate_owner_groups.items()):
        resolved_group_ids_by_winner.setdefault(winner, []).append(
            group_ids[subordinate],
        )
    clean_hypotheses: list[CrossSamplePeakGroupHypothesis] = []
    for index, group in enumerate(groups):
        group_id = group_ids[index]
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
        )
        winner_index = resolved_duplicate_owner_groups.get(index)
        if winner_index is not None:
            winner_id = group_ids[winner_index]
            clean_hypotheses.append(
                _hypothesis_from_owner_group(
                    tuple(group),
                    group_hypothesis_id=group_id,
                    public_family_id=group_id,
                    evidence=(
                        f"{evidence};{_DUPLICATE_OWNER_GROUP_POLICY}_loser;"
                        f"winner={winner_id}"
                    ),
                    review_only=True,
                    consolidation_state="primary_loser",
                    consolidation_winner_group_hypothesis_id=winner_id,
                    consolidation_source_group_hypothesis_id=group_id,
                ),
            )
            continue
        resolved_ids = resolved_group_ids_by_winner.get(index)
        if resolved_ids is None:
            clean_hypotheses.append(
                _hypothesis_from_owner_group(
                    tuple(group),
                    group_hypothesis_id=group_id,
                    public_family_id=group_id,
                    evidence=evidence,
                ),
            )
            continue
        clean_hypotheses.append(
            _hypothesis_from_owner_group(
                tuple(group),
                group_hypothesis_id=group_id,
                public_family_id=group_id,
                evidence=(
                    f"{evidence};{_DUPLICATE_OWNER_GROUP_POLICY}_winner;"
                    f"resolved_group_count={len(resolved_ids)}"
                ),
                consolidation_state="primary_winner",
                consolidation_winner_group_hypothesis_id=group_id,
                consolidation_source_group_hypothesis_id=";".join(
                    (group_id, *resolved_ids),
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
            if edge.decision == "blocked_edge":
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
            _edge_for_pair(
                owner,
                existing,
                config=config,
                drift_lookup=drift_lookup,
                edge_evidence_sink=edge_evidence_sink,
                edge_cache=edge_cache,
                edge_evaluator=edge_evaluator,
            ).decision
            == "strong_edge"
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
    if edge.decision == "strong_edge":
        return True
    return (
        edge.decision == "weak_edge"
        and not edge.failure_reason
        and edge.drift_prior_source == "none"
        and edge.rt_raw_delta_sec <= config.preferred_rt_sec
        and edge.owner_quality == "clean"
        and edge.seed_support_level == "moderate"
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
            groups.append(ordered)
            full_batch_continuity_owner_sets.add(
                frozenset(owner.owner_id for owner in ordered),
            )
            continue
        if (
            len(complete_groups) > 1
            and len(group_samples) + 1 == len(sample_universe)
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


def _group_passes_bounded_continuity(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    config: AlignmentConfig,
) -> bool:
    """Keep a connected component whole on bounded evidence, not on coverage.

    The two coverage rescues above only fire at full batch coverage or one
    sample short of it, so a component that a real compound produced over any
    smaller share of the batch is re-split however continuous it is.  A
    component instead survives here when the whole of it stays inside the
    symmetric precursor/product/loss hard envelope and its apex RT span stays
    within the group span bound, which is what separates one compound drifting
    across the batch from two compounds the chromatography resolved.
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
) -> dict[int, int]:
    """Resolve groups that only compete inside samples another group owns.

    A group whose samples are all already owned by a larger, identity-compatible
    cross-sample group cannot add sample coverage; it holds duplicate owners of
    that group's samples.  Resolving it in this owner keeps it out of Backfill
    instead of paying a full-batch request set for a group that Matrix
    consolidation demotes afterwards.  Resolution is one level deep: a group
    that wins is never itself resolved away.
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
                if not _group_resolves_duplicate_owner_group(
                    facts[candidate],
                    facts[subordinate],
                    config=config,
                ):
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


def _group_resolves_duplicate_owner_group(
    winner: _GroupIdentityFacts,
    subordinate: _GroupIdentityFacts,
    *,
    config: AlignmentConfig,
) -> bool:
    if len(winner.owners) < 2 or len(winner.owners) <= len(subordinate.owners):
        return False
    if winner.neutral_loss_tag != subordinate.neutral_loss_tag:
        return False
    if not subordinate.sample_stems <= winner.sample_stems:
        return False
    if _ppm(subordinate.center_mz, winner.center_mz) > config.max_ppm:
        return False
    if (
        abs(subordinate.center_rt - winner.center_rt) * 60.0
        > config.identity_rt_candidate_window_sec
    ):
        return False
    if (
        _ppm(subordinate.center_product_mz, winner.center_product_mz)
        > config.product_mz_tolerance_ppm
    ):
        return False
    if (
        _ppm(subordinate.center_observed_loss, winner.center_observed_loss)
        > config.observed_loss_tolerance_ppm
    ):
        return False
    return all(
        _owner_within_group_envelope(owner, winner.envelope, config)
        for owner in subordinate.owners
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
    if not math.isfinite(envelope.precursor_mz_min):
        return
    key = (envelope.neutral_loss_tag, _precursor_bucket(envelope.precursor_mz_min))
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
    group_hypothesis_id: str,
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
        group_hypothesis_id=group_hypothesis_id,
        public_family_id=public_family_id,
        owner_ids=tuple(owner.owner_id for owner in group),
        event_ids=event_ids,
        event_member_count=sum(len(owner.all_events) for owner in group),
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


def _group_evidence(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    full_batch_continuity: bool,
    injection_trajectory: bool,
    bounded_continuity: bool = False,
) -> str:
    if len(group) == 1:
        return "single_sample_local_owner"
    if full_batch_continuity:
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
