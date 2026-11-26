"""Cross-sample peak group construction.

This module groups SampleLocalMS1Owner instances using shared physical rules
and explicitly supplied method evidence. ``CrossSamplePeakGroupHypothesis`` is
the one representation of a group from grouping to the written outputs.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.alignment.cross_sample.identity_models import (
    CrossSampleContractError,
    CrossSampleGroupDisposition,
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodStatus,
    canonical_group_members,
    group_member_subject_from_peak_hypothesis,
)
from xic_extractor.alignment.cross_sample.neighborhood_graph import (
    CrossSampleLineageEdge,
    CrossSamplePartitionTransition,
    _expected_cross_sample_lineage,
)
from xic_extractor.alignment.membership_risk import AcceptedMembershipRisk
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.rt_drift import DriftCoefficients

GroupDestination: TypeAlias = Literal["matrix", "archive"]


@dataclass(frozen=True, slots=True)
class BandInference:
    """The frozen drift fit and any independently known identity of a band."""

    known_identity_label: str | None = None
    rt_drift: DriftCoefficients = DriftCoefficients()


@dataclass(frozen=True)
class CrossSamplePeakGroupHypothesis:
    """Peaks across samples treated as one feature.

    Equality and hash are the membership: the sample-ordered member subjects and
    the disposition. Every other field is a fact about that membership.
    ``group_center_mz``/``group_center_rt`` are decided when the group is
    constructed; a group extended by Backfill keeps the centre its searches used.
    """

    member_subjects: tuple[CrossSampleGroupMemberSubject, ...]
    disposition: CrossSampleGroupDisposition
    owners: tuple[SampleLocalMS1Owner, ...] = field(compare=False)
    group_center_mz: float = field(compare=False)
    group_center_rt: float = field(compare=False)
    neutral_loss_tag: str = field(default="", compare=False)
    group_product_mz: float | None = field(default=None, compare=False)
    group_observed_neutral_loss_da: float | None = field(default=None, compare=False)
    has_anchor: bool = field(default=False, compare=False)
    evidence: str = field(default="", compare=False)
    inference_basis: BandInference | None = field(default=None, compare=False)
    member_risks: tuple[tuple[str, AcceptedMembershipRisk], ...] = field(
        default=(), compare=False
    )
    drift_reference: tuple[tuple[str, ...], float] | None = field(
        default=None, compare=False
    )
    one_band: bool = field(default=False, compare=False)
    """Members are pieces of one chromatographic band; each sample's value is
    the whole envelope holding its member (see ``band_identity``)."""

    @property
    def group_hypothesis_id(self) -> str:
        """In-process key: the sample-ordered member hypothesis keys."""

        return " + ".join(member.subject_id for member in self.member_subjects)

    @property
    def owner_ids(self) -> tuple[str, ...]:
        return tuple(owner.owner_id for owner in self.owners)

    @property
    def destination(self) -> GroupDestination:
        """Only an established group is delivered to the Matrix."""

        return "matrix" if self.disposition == "established" else "archive"


def _hypothesis_from_owner_group(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    disposition: CrossSampleGroupDisposition | None,
    evidence: str,
    center: tuple[float, float] | None = None,
    inference_basis: BandInference | None = None,
    member_risks: tuple[tuple[str, AcceptedMembershipRisk], ...] = (),
    drift_reference: tuple[tuple[str, ...], float] | None = None,
    one_band: bool = False,
) -> CrossSamplePeakGroupHypothesis:
    """Build one group from its owners; ``center`` keeps an earlier decision."""

    members = canonical_group_members(
        tuple(owner.cross_sample_member_subject for owner in group)
    )
    if center is None:
        center = (
            sum(owner.peak.mz for owner in group) / len(group),
            sum(owner.peak.rt_apex_min for owner in group) / len(group),
        )
    events = tuple(
        owner.primary_identity_event
        for owner in group
        if owner.primary_identity_event is not None
    )
    center_product_mz = (
        None if not events else sum(event.product_mz for event in events) / len(events)
    )
    center_loss = (
        None
        if not events
        else sum(event.observed_neutral_loss_da for event in events) / len(events)
    )
    settled = _settled_members(group)
    return CrossSamplePeakGroupHypothesis(
        member_subjects=members,
        disposition=disposition or ("established" if settled else "provisional"),
        owners=group,
        group_center_mz=center[0],
        group_center_rt=center[1],
        neutral_loss_tag=events[0].neutral_loss_tag if events else "",
        group_product_mz=center_product_mz,
        group_observed_neutral_loss_da=center_loss,
        has_anchor=True,
        evidence=evidence,
        inference_basis=inference_basis,
        member_risks=member_risks,
        drift_reference=drift_reference,
        one_band=one_band,
    )


def _settled_members(group: Sequence[SampleLocalMS1Owner]) -> bool:
    return len(group) >= 2


def accepted_membership_risks(
    groups: Iterable[CrossSamplePeakGroupHypothesis],
) -> dict[str, AcceptedMembershipRisk]:
    """The accepted membership risk of each owner, keyed by owner."""

    return {owner: risk for group in groups for owner, risk in group.member_risks}


__all__ = (
    "accepted_membership_risks",
    "CrossSampleContractError",
    "CrossSampleGroupDisposition",
    "CrossSampleGroupMemberSubject",
    "CrossSampleLineageEdge",
    "CrossSampleNeighborhoodStatus",
    "_expected_cross_sample_lineage",
    "GroupDestination",
    "CrossSamplePeakGroupHypothesis",
    "CrossSamplePartitionTransition",
    "group_member_subject_from_peak_hypothesis",
)
