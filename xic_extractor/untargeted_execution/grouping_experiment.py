"""Prepare bounded grouping inputs through the formal initial assessment owner."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.grouping_experiment import GroupingInputs, GroupingWorkset
from xic_extractor.peak_detection.cid_nl_method_subject import (
    cid_nl_method_subject_resolver_interval,
)
from xic_extractor.untargeted_execution.execution import (
    UntargetedPartitionPipelineAdapter,
)
from xic_extractor.untargeted_execution.input_planning import UntargetedInputPlan
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedInitialSampleResult,
    UntargetedPartitionJob,
    UntargetedPreparedGrouping,
)
from xic_extractor.untargeted_execution.partition.workers import (
    assess_initial_sample_jobs,
)
from xic_extractor.untargeted_execution.partition_composition import (
    prepare_untargeted_partition_grouping,
)


@dataclass(frozen=True, slots=True)
class PreparedGroupingExperiment:
    inputs: GroupingInputs
    groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    selected_partition_count: int
    total_partition_count: int
    initial_job_count: int


def grouping_inputs_from_prepared(
    prepared: Sequence[UntargetedPreparedGrouping],
    *,
    sample_stems: tuple[str, ...],
    injection_order: Mapping[str, int],
    alignment_config: AlignmentConfig,
    backfill_half_window_min: float,
) -> GroupingInputs:
    worksets = []
    candidates: dict[str, set[str]] = defaultdict(set)
    for partition in prepared:
        for workset in partition.worksets:
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
                hypothesis = capture.selected_hypothesis
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
                    rt_warp=partition.rt_warp,
                    elution_contexts=workset.grouping_scope.elution_correspondence.source_contexts,
                )
            )
    return GroupingInputs(
        sample_stems=sample_stems,
        injection_order=dict(injection_order),
        alignment_config=alignment_config,
        backfill_half_window_min=backfill_half_window_min,
        worksets=tuple(worksets),
        candidate_ids_by_owner={
            key: tuple(sorted(value)) for key, value in candidates.items()
        },
    )


def prepare_grouping_experiment(
    plan: UntargetedInputPlan,
    *,
    mz_ranges: Sequence[tuple[float, float]],
    alignment_config: AlignmentConfig,
    known_istd_reference: KnownIstdReference | None = None,
    acquisition_cache_dir: Path | None = None,
    workers: int = 4,
    progress: Callable[[str], None] | None = None,
) -> PreparedGroupingExperiment:
    """Assess complete selected partitions, then stop before Backfill planning."""
    if workers < 1:
        raise ValueError("workers must be positive")
    if any(subject.composition is not None for subject in plan.subject_batch.subjects):
        raise ValueError("Grouping replay requires a scalar CID-NL preset")
    adapter = UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
        raw_workers=workers,
        raw_worker_backend="thread",
        acquisition_cache_dir=acquisition_cache_dir,
    )
    # The original partition owns its batch-warp anchors. Keep that whole
    # partition so scoping does not silently change an unchanged baseline.
    selected = tuple(
        partition
        for partition in adapter.partitions
        if any(
            left <= upper and lower <= right
            for subject in partition.payload
            for lower, upper in (cid_nl_method_subject_resolver_interval(subject),)
            for left, right in mz_ranges
        )
    )
    compact_inputs: list[GroupingInputs] = []
    groups: list[CrossSamplePeakGroupHypothesis] = []
    job_count = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for index, partition in enumerate(selected, 1):
                if progress:
                    progress(f"Initial assessment: partition {index}/{len(selected)}")
                batch = adapter.materialize_partition(partition)
                by_sample: dict[str, list[UntargetedPartitionJob]] = defaultdict(list)
                for job in batch.jobs:
                    by_sample[job.sample.metadata.raw_stem].append(job)
                jobs_by_sample = tuple(
                    tuple(by_sample[key]) for key in sorted(by_sample)
                )
                sample_results: list[UntargetedInitialSampleResult] = []
                for jobs, results in zip(
                    jobs_by_sample,
                    pool.map(assess_initial_sample_jobs, jobs_by_sample),
                    strict=True,
                ):
                    sample_results.extend(
                        UntargetedInitialSampleResult(job, result)
                        for job, result in zip(jobs, results, strict=True)
                    )
                job_count += len(batch.jobs)
                if progress:
                    progress(f"Grouping: partition {index}/{len(selected)}")
                prepared = prepare_untargeted_partition_grouping(
                    batch,
                    tuple(sample_results),
                    alignment_config=alignment_config,
                    known_istd_reference=known_istd_reference,
                )
                compact_inputs.append(
                    grouping_inputs_from_prepared(
                        (prepared,),
                        sample_stems=tuple(
                            sample.metadata.raw_stem for sample in plan.context.samples
                        ),
                        injection_order={
                            sample.metadata.raw_stem: sample.metadata.injection_order
                            for sample in plan.context.samples
                            if sample.metadata.injection_order is not None
                        },
                        alignment_config=alignment_config,
                        backfill_half_window_min=plan.context.peak_config.nl_rt_anchor_half_window_min,
                    )
                )
                groups.extend(
                    group
                    for workset in prepared.worksets
                    for group in workset.grouping_scope.groups
                )
                del prepared, sample_results, batch
    finally:
        adapter.close_process_worker_pools()
    inputs = GroupingInputs(
        sample_stems=tuple(sample.metadata.raw_stem for sample in plan.context.samples),
        injection_order={
            sample.metadata.raw_stem: sample.metadata.injection_order
            for sample in plan.context.samples
            if sample.metadata.injection_order is not None
        },
        alignment_config=alignment_config,
        backfill_half_window_min=plan.context.peak_config.nl_rt_anchor_half_window_min,
        worksets=tuple(
            workset for compact in compact_inputs for workset in compact.worksets
        ),
        candidate_ids_by_owner={
            key: ids
            for compact in compact_inputs
            for key, ids in compact.candidate_ids_by_owner.items()
        },
    )
    return PreparedGroupingExperiment(
        inputs=inputs,
        groups=tuple(groups),
        selected_partition_count=len(selected),
        total_partition_count=len(adapter.partitions),
        initial_job_count=job_count,
    )
