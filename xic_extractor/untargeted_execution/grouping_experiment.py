"""Project frozen formal groupings into the diagnostic replay contract."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.grouping_experiment import (
    GroupingInputs,
    GroupingWorkset,
)
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedPreparedGrouping,
)


@dataclass(frozen=True, slots=True)
class PreparedGroupingExperiment:
    inputs: GroupingInputs
    groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    selected_partition_count: int
    total_partition_count: int
    initial_job_count: int


def grouping_experiment_from_prepared(
    prepared: Iterable[UntargetedPreparedGrouping],
    *,
    sample_stems: tuple[str, ...],
    injection_order: Mapping[str, int],
    alignment_config: AlignmentConfig,
    initial_job_count: int,
) -> PreparedGroupingExperiment:
    worksets = []
    groups: list[CrossSamplePeakGroupHypothesis] = []
    partition_count = 0
    candidates: dict[str, set[str]] = defaultdict(set)
    for partition in prepared:
        partition_count += 1
        for workset in partition.worksets:
            groups.extend(workset.grouping_scope.groups)
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
            for capture in workset.captures:
                hypothesis = capture.measured_hypothesis
                if hypothesis is not None and hypothesis.hypothesis_id in owners:
                    candidates[hypothesis.hypothesis_id].add(capture.candidate_id)
            bindings = tuple(
                sorted(
                    (owner_id, group.inference_basis.known_identity_label)
                    for group in workset.grouping_scope.groups
                    if group.inference_basis is not None
                    and group.inference_basis.known_identity_label is not None
                    for owner_id in group.owner_ids
                )
            )
            worksets.append(
                GroupingWorkset(
                    key="|".join(
                        subject.method_subject_id for subject in workset.subjects
                    ),
                    owners=tuple(owners[key] for key in sorted(owners)),
                    known_identity_bindings=bindings,
                    rt_warp=partition.rt_frame.warp_for_snapshot,
                    rt_calibration=partition.rt_frame.calibration_for_snapshot,
                    elution_contexts=workset.grouping_scope.peak_trajectories.source_contexts,
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
        selected_partition_count=partition_count,
        total_partition_count=partition_count,
        initial_job_count=initial_job_count,
    )
