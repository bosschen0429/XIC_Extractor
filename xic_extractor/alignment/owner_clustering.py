from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupDisposition,
    CrossSampleGroupMemberSubject,
    CrossSampleLineageEdge,
    CrossSamplePeakGroupHypothesis,
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.drift_evidence import TargetedIstdIdentityAnchor
from xic_extractor.alignment.edge_scoring import (
    DriftLookupProtocol,
    OwnerEdgeEvidence,
    evaluate_owner_edge,
)
from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
    SampleLocalMS1Owner,
)

CrossSampleCarrierState = Literal["active", "render_only"]


@dataclass(frozen=True)
class OwnerAlignedFeature:
    """Compatibility facade for cross-sample owner grouping.

    ``feature_family_id`` here is a cross-sample group label, NOT the same
    concept as discovery's per-sample peak anchor.  Use ``group_hypothesis_id``
    for identity decisions.  ``feature_family_id`` is retained only because
    output surfaces (alignment_review.tsv, alignment_cells.tsv) use it as a
    column name.
    """

    feature_family_id: str
    neutral_loss_tag: str
    family_center_mz: float
    family_center_rt: float
    family_product_mz: float
    family_observed_neutral_loss_da: float
    has_anchor: bool
    owners: tuple[SampleLocalMS1Owner, ...]
    evidence: str
    targeted_istd_anchor: TargetedIstdIdentityAnchor | None = None
    identity_conflict: bool = False
    review_only: bool = False
    confirm_local_owners_with_backfill: bool = False
    backfill_seed_centers: tuple[tuple[float, float], ...] = ()
    ambiguous_sample_stem: str | None = None
    ambiguous_candidate_ids: tuple[str, ...] = ()
    group_hypothesis_id: str = ""
    public_family_id: str = ""
    processing_request_id: str = ""
    member_subjects: tuple[CrossSampleGroupMemberSubject, ...] = ()
    identity_basis_bytes: bytes = b""
    disposition: CrossSampleGroupDisposition = "established"
    cross_sample_lineage: tuple[CrossSampleLineageEdge, ...] = ()
    group_construction_role: str = "successor_constructor"
    group_delivery_role: str = "owner_aligned_feature_compatibility_facade"
    group_membership_source: str = "owner_aligned_feature_successor_projection"
    consolidation_state: str = "not_consolidated"
    consolidation_winner_group_hypothesis_id: str = ""
    consolidation_source_group_hypothesis_id: str = ""
    cross_sample_state: CrossSampleCarrierState = "active"

    def __post_init__(self) -> None:
        if self.cross_sample_state not in {"active", "render_only"}:
            raise ValueError("cross_sample_state is not closed")
        if not self.public_family_id:
            object.__setattr__(
                self,
                "public_family_id",
                self.feature_family_id,
            )

    @property
    def cluster_id(self) -> str:
        return self.feature_family_id

    @property
    def members(self) -> tuple[SampleLocalMS1Owner, ...]:
        return self.owners

    @property
    def event_cluster_ids(self) -> tuple[str, ...]:
        return tuple(owner.owner_id for owner in self.owners)

    @property
    def event_member_count(self) -> int:
        return sum(len(owner.all_events) for owner in self.owners)


def cluster_sample_local_owners(
    owners: tuple[SampleLocalMS1Owner, ...] | list[SampleLocalMS1Owner],
    *,
    config: AlignmentConfig,
    sample_universe: frozenset[str] | None = None,
    injection_order: Mapping[str, int] | None = None,
    drift_lookup: DriftLookupProtocol | None = None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None = None,
) -> tuple[OwnerAlignedFeature, ...]:
    hypotheses = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=config,
        sample_universe=sample_universe,
        injection_order=injection_order,
        drift_lookup=drift_lookup,
        edge_evidence_sink=edge_evidence_sink,
        edge_evaluator=evaluate_owner_edge,
    )
    return tuple(
        _feature_from_peak_group_hypothesis(hypothesis) for hypothesis in hypotheses
    )


def review_only_features_from_ambiguous_records(
    records: tuple[AmbiguousOwnerRecord, ...],
    *,
    start_index: int,
) -> tuple[OwnerAlignedFeature, ...]:
    features: list[OwnerAlignedFeature] = []
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
        features.append(
            OwnerAlignedFeature(
                feature_family_id=family_id,
                neutral_loss_tag=record.neutral_loss_tag,
                family_center_mz=record.precursor_mz,
                family_center_rt=record.apex_rt,
                family_product_mz=record.product_mz,
                family_observed_neutral_loss_da=record.observed_neutral_loss_da,
                has_anchor=False,
                owners=(),
                evidence="ambiguous_ms1_owner_review_only",
                review_only=True,
                ambiguous_sample_stem=record.sample_stem,
                ambiguous_candidate_ids=record.candidate_ids,
                group_construction_role="ambiguous_owner_display_adapter",
                group_membership_source="no_cross_sample_membership",
                cross_sample_state="render_only",
            )
        )
    return tuple(features)


def _feature_from_peak_group_hypothesis(
    hypothesis: CrossSamplePeakGroupHypothesis,
) -> OwnerAlignedFeature:
    if hypothesis.group_center_mz is None:
        raise ValueError("cross-sample peak group is missing center m/z")
    if hypothesis.group_center_rt is None:
        raise ValueError("cross-sample peak group is missing center RT")
    if hypothesis.group_product_mz is None:
        raise ValueError("cross-sample peak group is missing product m/z")
    if hypothesis.group_observed_neutral_loss_da is None:
        raise ValueError("cross-sample peak group is missing observed neutral loss")
    return OwnerAlignedFeature(
        feature_family_id=hypothesis.public_family_id,
        neutral_loss_tag=hypothesis.neutral_loss_tag,
        family_center_mz=hypothesis.group_center_mz,
        family_center_rt=hypothesis.group_center_rt,
        family_product_mz=hypothesis.group_product_mz,
        family_observed_neutral_loss_da=hypothesis.group_observed_neutral_loss_da,
        has_anchor=hypothesis.has_anchor,
        owners=hypothesis.owners,
        evidence=hypothesis.evidence,
        targeted_istd_anchor=hypothesis.targeted_istd_anchor,
        identity_conflict=hypothesis.identity_conflict,
        review_only=hypothesis.review_only,
        confirm_local_owners_with_backfill=(
            hypothesis.confirm_local_owners_with_backfill
        ),
        backfill_seed_centers=hypothesis.backfill_seed_centers,
        ambiguous_sample_stem=hypothesis.ambiguous_sample_stem,
        ambiguous_candidate_ids=hypothesis.ambiguous_candidate_ids,
        group_hypothesis_id=hypothesis.group_hypothesis_id,
        public_family_id=hypothesis.public_family_id,
        processing_request_id=hypothesis.processing_request_id,
        member_subjects=hypothesis.member_subjects,
        identity_basis_bytes=hypothesis.identity_basis_bytes,
        disposition=hypothesis.disposition,
        cross_sample_lineage=hypothesis.lineage,
        group_construction_role=hypothesis.group_construction_role,
        group_delivery_role="owner_aligned_feature_compatibility_facade",
        group_membership_source="owner_aligned_feature_successor_projection",
        consolidation_state=hypothesis.consolidation_state,
        consolidation_winner_group_hypothesis_id=(
            hypothesis.consolidation_winner_group_hypothesis_id
        ),
        consolidation_source_group_hypothesis_id=(
            hypothesis.consolidation_source_group_hypothesis_id
        ),
        cross_sample_state="active",
    )
