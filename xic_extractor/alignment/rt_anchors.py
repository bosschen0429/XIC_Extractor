"""RT anchor observations: measured peaks that may place samples on one RT frame.

An anchor observation is a direct-supported, quantifiable peak and its apex RT.
Sample RT calibration fits its knots from the observations of agreed bootstrap
groups; the drift model reads each method channel's observations. Both read the
observations produced here for their respective bootstrap populations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.alignment.feature_membership_inference import (
        FeatureMembershipBasis,
    )
    from xic_extractor.alignment.grouping_bootstrap import (
        InitialGroupingScope,
        RetainedGroupingCandidate,
    )
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        CidNlMethodSubject,
    )

_CALIBRATION_GROUP_BASES: frozenset[FeatureMembershipBasis] = frozenset(
    {
        "bidirectional_membership_agreement",
        "known_identity_constraint",
        "peak_trajectory",
    }
)


@dataclass(frozen=True, slots=True)
class RtAnchorObservation:
    """One sample's direct-supported, quantifiable peak in a method channel.

    ``bootstrap_group_id`` names the agreed bootstrap group the peak belongs
    to; None when its group is not agreed, so it cannot calibrate.
    """

    method_subject: CidNlMethodSubject
    sample_stem: str
    owner_id: str
    rt_min: float
    bootstrap_group_id: str | None

    @property
    def channel(self) -> str:
        return self.method_subject.method_subject_id


def rt_anchor_observations(
    scope: InitialGroupingScope,
    candidates: Sequence[RetainedGroupingCandidate],
) -> tuple[RtAnchorObservation, ...]:
    """Every anchor observation of one initial grouping, in group member order.

    Measured support comes from the retained assessment binding: an
    observation-only owner can carry nominal method-query masses, which do not
    establish a peak in the channel. A retained MS1-only or ambiguous member may
    still be grouped, but cannot establish other samples' RT coordinates.
    """
    subject_by_owner = {
        candidate.peak_hypothesis_id: candidate.direct_support_assessment.method_subject
        for candidate in candidates
        if candidate.direct_support_assessment is not None
    }
    return tuple(
        RtAnchorObservation(
            subject_by_owner[owner.owner_id],
            owner.sample_stem,
            owner.owner_id,
            owner.peak.integration.rt_apex_min,
            group.group_hypothesis_id
            if group.inference_basis is not None
            and group.inference_basis.basis in _CALIBRATION_GROUP_BASES
            else None,
        )
        for group in scope.groups
        for owner in group.owners
        if owner.owner_id in subject_by_owner
        and owner.primary_identity_event is not None
        and owner.quantification_eligible
    )
