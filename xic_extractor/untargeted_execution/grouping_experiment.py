"""Project frozen formal groupings into the diagnostic replay contract."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from xic_extractor.alignment.cid_nl_initial_grouping import CidNlGrouping
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.grouping_experiment import (
    GroupingInputs,
    GroupingWorkset,
)
from xic_extractor.alignment.result.scientific_alignment import ObservedSearchSubject
from xic_extractor.alignment.rt_anchors import BatchRtModelInputs
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration


@dataclass(frozen=True, slots=True)
class PreparedGroupingExperiment:
    inputs: GroupingInputs
    groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    rt_calibration: BatchRtCalibration | None
    selected_partition_count: int
    total_partition_count: int
    initial_job_count: int


def grouping_experiment_from_prepared(
    prepared: Iterable[tuple[CidNlGrouping, CidNlGrouping]],
    *,
    sample_stems: tuple[str, ...],
    injection_order: Mapping[str, int],
    alignment_config: AlignmentConfig,
    initial_job_count: int,
    rt_calibration: BatchRtCalibration | None,
    rt_model_inputs: BatchRtModelInputs | None,
) -> PreparedGroupingExperiment:
    worksets = []
    groups: list[CrossSamplePeakGroupHypothesis] = []
    partition_count = 0
    candidates: dict[str, set[str]] = defaultdict(set)
    for initial, partition in prepared:
        partition_count += 1
        for workset, final in zip(initial.worksets, partition.worksets, strict=True):
            groups.extend(final.grouping_scope.groups)
            owners = {
                owner.owner_id: owner
                for group in workset.grouping_scope.groups
                for owner in group.owners
            }
            if any(
                owner.cid_nl_composite_assessment is not None
                for owner in owners.values()
            ):
                raise ValueError("Grouping replay requires scalar CID-NL observations")
            for terminal in workset.terminal_subjects:
                if (
                    isinstance(terminal, ObservedSearchSubject)
                    and terminal.hypothesis.hypothesis_id in owners
                ):
                    candidates[terminal.hypothesis.hypothesis_id].add(
                        terminal.candidate_id
                    )
            bindings = workset.grouping_result.rt_frame.known_identity_bindings
            worksets.append(
                GroupingWorkset(
                    key="|".join(
                        subject.method_subject_id for subject in workset.subjects
                    ),
                    owners=tuple(owners[key] for key in sorted(owners)),
                    known_identity_bindings=bindings,
                    rt_calibration=partition.rt_frame.calibration_for_snapshot,
                    rt_drift=partition.rt_frame.drift_model,
                    rt_model_inputs=rt_model_inputs,
                )
            )
    inputs = GroupingInputs(
        sample_stems=sample_stems,
        injection_order=dict(injection_order),
        alignment_config=alignment_config,
        worksets=tuple(worksets),
        candidate_ids_by_owner={
            key: tuple(sorted(value)) for key, value in candidates.items()
        },
    )
    return PreparedGroupingExperiment(
        inputs=inputs,
        groups=tuple(groups),
        rt_calibration=rt_calibration,
        selected_partition_count=partition_count,
        total_partition_count=partition_count,
        initial_job_count=initial_job_count,
    )
