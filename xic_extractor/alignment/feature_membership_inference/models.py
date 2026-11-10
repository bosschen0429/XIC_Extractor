"""Membership result records and the retained read-only RT warp."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Literal

from xic_extractor.alignment.edge_scoring import RTEvidenceBasis
from xic_extractor.alignment.membership_selection import AcceptedMembershipRisk
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner

FeatureMembershipDisposition = Literal[
    "assigned",
    "undecided",
    "known_identity",
    "unresolved_conflict",
]


FeatureMembershipBasis = Literal[
    "window_level_rt_band",
    "peak_trajectory",
    "bidirectional_membership_agreement",
    "directional_membership_disagreement",
    "insufficient_cross_sample_evidence",
    "known_identity_constraint",
    "known_identity_input_conflict",
]


KnownIdentityConstraintStatus = Literal["satisfied", "unresolved_conflict"]


DecisionDirection = Literal["forward", "reverse", "window"]


SparseDecisionState = Literal[
    "assigned",
    "birth",
    "competition_unassigned",
    "evaluated_undecided",
    "proven_incompatible",
    "supported_not_selected",
]


_ALGORITHM = "finite_bidirectional_sparse_feature_assignment"


_OBJECTIVE = (
    "per_direction_per_sample_minimum_supported_assignment_cost_with_private_"
    "unassigned_options;final_membership_requires_directional_agreement"
)


_TERMINATION = "completed_two_finite_directional_passes"


@dataclass(frozen=True, slots=True)
class FeatureMembershipGroup:
    owner_ids: tuple[str, ...]
    disposition: FeatureMembershipDisposition
    basis: FeatureMembershipBasis
    reason: str
    known_identity_label: str | None = None


@dataclass(frozen=True, slots=True)
class KnownIdentityMembershipConstraint:
    label: str
    owner_ids: tuple[str, ...]
    status: KnownIdentityConstraintStatus
    reason: str


@dataclass(frozen=True, slots=True)
class SparseFeatureDecision:
    direction: DecisionDirection
    sample_stem: str
    owner_id: str
    feature_key: str | None
    state: SparseDecisionState
    reason: str
    cost: float | None = None
    witness_owner_ids: tuple[str, ...] = ()
    # Which witness carried the decision and on which RT coordinate. The
    # witness list alone cannot answer either, so an audit cannot tell whether
    # a membership stood on raw or drift-corrected RT without these.
    selected_witness_owner_id: str | None = None
    rt_basis: RTEvidenceBasis | None = None
    rt_raw_delta_sec: float | None = None
    rt_drift_corrected_delta_sec: float | None = None
    accepted_risk: AcceptedMembershipRisk | None = None


@dataclass(frozen=True, slots=True)
class FeatureInferenceOperationCounts:
    input_owner_rows: int
    unique_owners: int
    duplicate_owner_rows_ignored: int
    known_identity_bindings: int
    directional_passes: int
    samples_processed: int
    index_queries: int
    index_entries_scanned: int
    candidate_features_considered: int
    feature_summary_comparisons: int
    owner_edge_evaluations: int
    supported_candidate_arcs: int
    competition_components: int
    assignment_solver_calls: int
    alternative_solver_calls: int
    births: int
    ambiguous_assignments: int
    consensus_disagreements: int
    rt_candidate_features_pruned: int = 0
    index_axis_updates: int = 0
    index_axis_updates_avoided: int = 0
    trivial_assignment_components: int = 0
    largest_component_owners: int = 0
    largest_component_features: int = 0
    largest_component_arcs: int = 0


@dataclass(frozen=True, slots=True)
class FeatureMembershipInferenceResult:
    groups: tuple[FeatureMembershipGroup, ...]
    known_identity_constraints: tuple[KnownIdentityMembershipConstraint, ...]
    sparse_decisions: tuple[SparseFeatureDecision, ...]
    operation_counts: FeatureInferenceOperationCounts
    algorithm: str = _ALGORITHM
    objective: str = _OBJECTIVE
    termination: str = _TERMINATION
    pair_coverage: str = "not_materialized;absence_means_not_evaluated"


@dataclass(frozen=True, slots=True)
class BatchRtWarp:
    """Read-only historical offset map for retained grouping snapshots."""

    knots_by_sample: tuple[tuple[str, tuple[tuple[float, float], ...]], ...]
    anchor_group_count: int

    def has_support(self, sample_stem: str) -> bool:
        """Distinguish an unsupported identity transform from measured zero."""
        return bool(self._knots(sample_stem))

    def offset_min(self, sample_stem: str, rt_min: float) -> float:
        return _interpolated_offset(self._knots(sample_stem), rt_min)

    def corrected_rt_min(self, owner: SampleLocalMS1Owner) -> float:
        return self.common_rt_min(owner.sample_stem, owner.peak.integration.rt_apex_min)

    def common_rt_min(self, sample_stem: str, observed_rt: float) -> float:
        # Knots are keyed by common RT; re-key them by observed RT to invert.
        knots = self._knots(sample_stem)
        return observed_rt - _interpolated_offset(
            tuple((rt + offset, offset) for rt, offset in knots), observed_rt
        )

    def _knots(self, sample_stem: str) -> tuple[tuple[float, float], ...]:
        return dict(self.knots_by_sample).get(sample_stem, ())


def _interpolated_offset(
    knots: tuple[tuple[float, float], ...], rt_min: float
) -> float:
    if not knots:
        return 0.0
    if rt_min <= knots[0][0]:
        return knots[0][1]
    if rt_min >= knots[-1][0]:
        return knots[-1][1]
    right = bisect_right(knots, rt_min, key=lambda knot: knot[0])
    left_rt, left_offset = knots[right - 1]
    right_rt, right_offset = knots[right]
    fraction = (rt_min - left_rt) / (right_rt - left_rt)
    return left_offset + fraction * (right_offset - left_offset)
