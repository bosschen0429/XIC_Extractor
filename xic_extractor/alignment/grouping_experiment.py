"""Replay the formal grouping decisions over retained, curve-free observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from xic_extractor.alignment.band_grouping import band_shift_rows, group_peak_bands
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.rt_anchors import BatchRtModelInputs, fit_batch_rt_model
from xic_extractor.alignment.rt_drift import RtDriftModel
from xic_extractor.alignment.rt_frame import RtFrame, SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
from xic_extractor.injection_timeline import InjectionSequence


@dataclass(frozen=True, slots=True)
class GroupingWorkset:
    """One replay unit; worksets of one batch share its RT model and inputs."""

    key: str
    owners: tuple[SampleLocalMS1Owner, ...]
    known_identity_bindings: tuple[tuple[str, str], ...]
    rt_calibration: BatchRtCalibration | None = None
    rt_drift: RtDriftModel | None = None
    rt_model_inputs: BatchRtModelInputs | None = None


@dataclass(frozen=True, slots=True)
class GroupingInputs:
    sample_stems: tuple[str, ...]
    injection_order: Mapping[str, int]
    alignment_config: AlignmentConfig
    worksets: tuple[GroupingWorkset, ...]
    candidate_ids_by_owner: Mapping[str, tuple[str, ...]]


def combine_grouping_inputs(batches: Sequence[GroupingInputs]) -> GroupingInputs:
    """Independently grouped batches side by side; each workset keeps its batch."""
    return GroupingInputs(
        sample_stems=tuple(stem for item in batches for stem in item.sample_stems),
        injection_order={
            stem: order
            for item in batches
            for stem, order in item.injection_order.items()
        },
        alignment_config=batches[0].alignment_config,
        worksets=tuple(workset for item in batches for workset in item.worksets),
        candidate_ids_by_owner={
            owner: ids
            for item in batches
            for owner, ids in item.candidate_ids_by_owner.items()
        },
    )


def replay_grouping(
    inputs: GroupingInputs,
    *,
    config: AlignmentConfig | None = None,
    rt_coordinates: RtFrame | None = None,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Replay band membership before envelope/shape checks or Backfill."""
    sample_universe = frozenset(inputs.sample_stems)
    base_frame = SampleRtFrame(
        injections=InjectionSequence(inputs.injection_order),
        sample_universe=sample_universe,
    )
    if isinstance(rt_coordinates, SampleRtFrame):
        override = base_frame.with_calibration(rt_coordinates)
    elif rt_coordinates is not None:
        override = base_frame.with_coordinate_override(rt_coordinates.corrected_rt_min)
    else:
        override = None
    groups: list[CrossSamplePeakGroupHypothesis] = []
    for workset in inputs.worksets:
        frame = override or base_frame
        if override is None and workset.rt_calibration is not None:
            frame = frame.with_sample_calibration(
                workset.rt_calibration, workset.rt_drift
            )
        groups.extend(
            group_peak_bands(
                workset.owners,
                config=config or inputs.alignment_config,
                rt_frame=frame,
                known_identity_bindings=workset.known_identity_bindings,
            )
        )
    return tuple(groups)


def refit_rt_models(inputs: GroupingInputs) -> GroupingInputs:
    """Fit each batch RT model again from its retained anchors with current code.

    Worksets without retained anchors keep their frozen model. The sample
    shift is then voted by the batch's direct-evidence rows, as a full run does.
    """
    sample_universe = frozenset(inputs.sample_stems)
    base_frame = SampleRtFrame(
        injections=InjectionSequence(inputs.injection_order),
        sample_universe=sample_universe,
    )
    fitted: dict[int, tuple[BatchRtCalibration, RtDriftModel]] = {}
    for workset in inputs.worksets:
        model_inputs = workset.rt_model_inputs
        if model_inputs is None or id(model_inputs) in fitted:
            continue
        calibration, drift = fit_batch_rt_model(model_inputs)
        rows = [
            row
            for other in inputs.worksets
            if other.rt_model_inputs is model_inputs
            for row in band_shift_rows(
                other.owners,
                config=inputs.alignment_config,
                rt_frame=base_frame.with_sample_calibration(calibration, drift),
            )
        ]
        fitted[id(model_inputs)] = fit_batch_rt_model(model_inputs, shift_rows=rows)
    return replace(
        inputs,
        worksets=tuple(
            workset
            if workset.rt_model_inputs is None
            else replace(
                workset,
                rt_calibration=fitted[id(workset.rt_model_inputs)][0],
                rt_drift=fitted[id(workset.rt_model_inputs)][1],
            )
            for workset in inputs.worksets
        ),
    )
