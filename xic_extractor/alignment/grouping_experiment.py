"""Replay the formal grouping decisions over retained, curve-free observations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.feature_membership_inference import (
    BatchRtWarp,
)
from xic_extractor.alignment.grouping_workflow import group_cid_nl_owners
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import (
    resolve_peak_trajectories,
)
from xic_extractor.alignment.rt_frame import RtFrame, SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    RtCalibrationPolicy,
    build_batch_rt_calibration,
)
from xic_extractor.peak_detection.elution_context import SampleElutionContext


@dataclass(frozen=True, slots=True)
class GroupingWorkset:
    key: str
    owners: tuple[SampleLocalMS1Owner, ...]
    known_identity_bindings: tuple[tuple[str, str], ...]
    rt_warp: BatchRtWarp | None
    elution_contexts: tuple[SampleElutionContext, ...] = ()
    rt_calibration: BatchRtCalibration | None = None


@dataclass(frozen=True, slots=True)
class GroupingInputs:
    sample_stems: tuple[str, ...]
    injection_order: Mapping[str, int]
    alignment_config: AlignmentConfig
    worksets: tuple[GroupingWorkset, ...]
    candidate_ids_by_owner: Mapping[str, tuple[str, ...]]


def replay_grouping(
    inputs: GroupingInputs,
    *,
    config: AlignmentConfig | None = None,
    rt_coordinates: RtFrame | None = None,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Reuse membership and post-grouping rules without acquisition or Backfill."""
    sample_universe = frozenset(inputs.sample_stems)
    base_frame = SampleRtFrame(
        injection_order=inputs.injection_order, sample_universe=sample_universe
    )
    if isinstance(rt_coordinates, SampleRtFrame):
        override = base_frame.with_calibration(rt_coordinates)
    elif rt_coordinates is not None:
        # Legacy owner-only diagnostics do not imply measured landmark support.
        override = base_frame.with_coordinate_override(rt_coordinates.corrected_rt_min)
    else:
        override = None
    groups: list[CrossSamplePeakGroupHypothesis] = []
    for workset in inputs.worksets:
        frame = override or base_frame.with_warp(workset.rt_warp)
        if override is None and workset.rt_calibration is not None:
            frame = frame.with_sample_calibration(workset.rt_calibration)
        trajectories = resolve_peak_trajectories(
            workset.owners,
            workset.elution_contexts,
            config=config or inputs.alignment_config,
            injection_order=inputs.injection_order,
            rt_frame=frame,
        )
        groups.extend(
            group_cid_nl_owners(
                workset.owners,
                config=config or inputs.alignment_config,
                sample_universe=sample_universe,
                rt_frame=frame,
                known_identity_bindings=workset.known_identity_bindings,
                peak_trajectories=trajectories,
            )
        )
    return tuple(groups)


def calibrate_grouping(
    inputs: GroupingInputs,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    *,
    policy: RtCalibrationPolicy,
) -> BatchRtCalibration:
    """Fit the existing RT model from directly observed stable initial groups."""
    observations = {
        group.group_hypothesis_id: {
            owner.sample_stem: owner.peak.integration.rt_apex_min
            for owner in group.owners
        }
        for group in groups
        if group.disposition == "established"
    }
    return build_batch_rt_calibration(
        observations,
        sample_stems=inputs.sample_stems,
        prior_injections={},
        policy=policy,
    )
