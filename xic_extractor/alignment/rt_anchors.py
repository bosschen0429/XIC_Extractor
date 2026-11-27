"""RT anchor observations: measured peaks that may place samples on one RT frame.

An anchor observation is a direct-supported, quantifiable peak and its apex RT.
Sample RT calibration fits its knots from the observations of agreed bootstrap
groups; the drift model reads each method channel's observations. Both read the
observations produced here for their respective bootstrap populations.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xic_extractor.alignment.cid_nl_grouping import has_cid_nl_activation_support
from xic_extractor.alignment.rt_drift import BatchRtModelAccumulator, RtDriftModel
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    BatchRtCalibrationAccumulator,
    ordinary_anchor_min_samples,
)
from xic_extractor.injection_timeline import InjectionSequence

if TYPE_CHECKING:
    from xic_extractor.alignment.grouping_bootstrap import (
        InitialGroupingScope,
    )
    from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
    from xic_extractor.alignment.rt_frame import SampleRtFrame


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


def places_sample_on_rt_frame(owner: SampleLocalMS1Owner) -> bool:
    """Whether a direct-supported owner's apex is an anchor observation."""
    return has_cid_nl_activation_support(owner) and owner.quantification_eligible


def reaches_ordinary_anchor_quorum(
    sample_stems: Iterable[str], frame: SampleRtFrame
) -> bool:
    """Whether anchors from these samples can form one calibrating correspondence.

    A bootstrap group calibrates a cohort only when it holds
    ``ordinary_anchor_min_samples`` of that cohort's samples, so a population
    below the quorum in every cohort calibrates nothing however it is grouped.
    """
    universe = frame.sample_universe or frame.injections.stems
    cohorts = frame.sample_cohorts
    sizes = Counter(cohorts[stem] for stem in universe if stem in cohorts)
    present = Counter(
        cohorts[stem]
        for stem in set(sample_stems)
        if stem in universe and stem in cohorts
    )
    return any(
        count >= ordinary_anchor_min_samples(sizes[cohort])
        for cohort, count in present.items()
    )


def rt_anchor_observations(
    scope: InitialGroupingScope,
) -> tuple[RtAnchorObservation, ...]:
    """Every anchor observation of one initial grouping, in group member order.

    Every direct, quantifiable owner is an observation: the drift model reads
    all of them. Only members of an established band with at least two anchors
    carry the band key that calibration correspondences read.

    Measured support comes from the retained assessment binding: an
    observation-only owner can carry nominal method-query masses, which do not
    establish a peak in the channel. A retained MS1-only or ambiguous member may
    still be grouped, but cannot establish other samples' RT coordinates.
    """
    observations: list[RtAnchorObservation] = []
    for group in scope.groups:
        anchors = [
            (owner, owner.assessment.method_subject)
            for owner in group.owners
            if owner.assessment is not None and places_sample_on_rt_frame(owner)
        ]
        band_key = (
            min(
                f"{subject.method_subject_id}|{owner.peak.physical_peak_key}"
                for owner, subject in anchors
            )
            if len(anchors) >= 2 and group.disposition == "established"
            else None
        )
        observations.extend(
            RtAnchorObservation(
                subject.method_subject_id,
                subject.precursor_mz,
                subject.neutral_loss_da,
                owner.sample_stem,
                owner.owner_id,
                owner.peak.rt_apex_min,
                band_key,
            )
            for owner, subject in anchors
        )
    return tuple(observations)


@dataclass(frozen=True, slots=True)
class BatchRtModelInputs:
    """Everything one batch RT model is fitted from, in partition order."""

    sample_stems: tuple[str, ...]
    sample_cohorts: Mapping[str, str]
    injections: InjectionSequence
    qc_samples: frozenset[str]
    measured_istds: tuple[tuple[str, str, float], ...]
    anchor_batches: tuple[tuple[RtAnchorObservation, ...], ...]


def fit_batch_rt_model(
    inputs: BatchRtModelInputs,
    shift_rows: Iterable[Mapping[str, float]] = (),
) -> tuple[BatchRtCalibration, RtDriftModel]:
    """Fit the published map, then the drift model against that map.

    ``shift_rows`` are the batch's direct-evidence rows in that map; they vote
    the drift model's sample shift.
    """
    calibration = BatchRtCalibrationAccumulator(
        sample_stems=inputs.sample_stems, sample_cohorts=inputs.sample_cohorts
    )
    drift = BatchRtModelAccumulator(
        sample_cohorts=inputs.sample_cohorts,
        injections=inputs.injections,
        qc_samples=inputs.qc_samples,
    )
    for batch in inputs.anchor_batches:
        calibration.add_observations(batch)
        drift.add_observations(batch)
    frozen = calibration.freeze(measured_istds=inputs.measured_istds)
    return frozen, drift.freeze(frozen, shift_rows)
