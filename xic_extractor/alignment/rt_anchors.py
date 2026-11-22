"""RT anchor observations: measured peaks that may place samples on one RT frame.

An anchor observation is a direct-supported, quantifiable peak and its apex RT.
Sample RT calibration fits its knots from the observations of agreed bootstrap
groups; the drift model reads each method channel's observations. Both read the
observations produced here for their respective bootstrap populations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xic_extractor.alignment.cid_nl_grouping import has_cid_nl_activation_support
from xic_extractor.alignment.rt_drift import BatchRtModelAccumulator, RtDriftModel
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    BatchRtCalibrationAccumulator,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.feature_membership_inference import (
        FeatureMembershipBasis,
    )
    from xic_extractor.alignment.grouping_bootstrap import (
        InitialGroupingScope,
        RetainedGroupingCandidate,
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

    ``channel`` is the method subject ID. ``bootstrap_group_id`` names the
    agreed bootstrap group the peak belongs to; None when its group is not
    agreed, so it cannot calibrate.
    """

    channel: str
    precursor_mz: float
    neutral_loss_da: float | None
    sample_stem: str
    owner_id: str
    rt_min: float
    bootstrap_group_id: str | None


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
            subject.method_subject_id,
            subject.precursor_mz,
            subject.neutral_loss_da,
            owner.sample_stem,
            owner.owner_id,
            owner.peak.rt_apex_min,
            group.group_hypothesis_id
            if group.inference_basis is not None
            and group.inference_basis.basis in _CALIBRATION_GROUP_BASES
            else None,
        )
        for group in scope.groups
        for owner in group.owners
        if (subject := subject_by_owner.get(owner.owner_id)) is not None
        and has_cid_nl_activation_support(owner)
        and owner.quantification_eligible
    )


@dataclass(frozen=True, slots=True)
class BatchRtModelInputs:
    """Everything one batch RT model is fitted from, in partition order."""

    sample_stems: tuple[str, ...]
    sample_cohorts: Mapping[str, str]
    injection_order: Mapping[str, int]
    qc_samples: frozenset[str]
    measured_istds: tuple[tuple[str, str, float], ...]
    anchor_batches: tuple[tuple[RtAnchorObservation, ...], ...]


def fit_batch_rt_model(
    inputs: BatchRtModelInputs,
) -> tuple[BatchRtCalibration, RtDriftModel]:
    """Fit the published map, then the drift model against that map."""
    calibration = BatchRtCalibrationAccumulator(
        sample_stems=inputs.sample_stems, sample_cohorts=inputs.sample_cohorts
    )
    drift = BatchRtModelAccumulator(
        sample_cohorts=inputs.sample_cohorts,
        injection_order=inputs.injection_order,
        qc_samples=inputs.qc_samples,
    )
    for batch in inputs.anchor_batches:
        calibration.add_observations(batch)
        drift.add_observations(batch)
    frozen = calibration.freeze(measured_istds=inputs.measured_istds)
    return frozen, drift.freeze(frozen)
