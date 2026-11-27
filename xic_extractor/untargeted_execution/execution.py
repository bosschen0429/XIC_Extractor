"""Complete CID-NL subjects in finite partitions."""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from copy import copy
from dataclasses import dataclass, fields, replace
from functools import partial
from pathlib import Path
from typing import cast

from xic_extractor.alignment.backfill.cid_nl_planning import CidNlBackfillQuestion
from xic_extractor.alignment.cid_nl_initial_grouping import (
    CidNlBatchGrouping,
    CidNlGrouping,
    direct_evidence_rows,
    regroup_cid_nl_grouping,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetBatchApplicationResult,
)
from xic_extractor.alignment.rt_anchors import RtAnchorObservation
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.execution.partitions import (
    PartitionAcquisitionOutput,
    PartitionCompositionOutput,
    PartitionPipelinePhase,
    PartitionPipelineScheduler,
    WorkPartition,
)
from xic_extractor.execution.result_store import (
    SealedPartition,
    StreamedResultStore,
    WrittenPartition,
    read_spooled_header_field,
    read_spooled_tuple,
    write_spooled_partition,
)
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration
from xic_extractor.raw_reader import ensure_assemblies_loaded
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.execution_result import (
    CompletedUntargetBatchWorkflowResult,
)
from xic_extractor.untargeted_execution.grouping_experiment import (
    PreparedGroupingExperiment,
    grouping_experiment_from_prepared,
)
from xic_extractor.untargeted_execution.input_planning import (
    PreparedUntargetedAlignmentWorksets,
    UntargetedInputPlan,
    pack_subject_partitions,
    prepare_untargeted_alignment_worksets,
)
from xic_extractor.untargeted_execution.partition.execution import execute_partition
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedInitialSampleResult,
    UntargetedPartitionJob,
    UntargetedPartitionResult,
    build_sample_jobs,
)
from xic_extractor.untargeted_execution.partition.workers import (
    plan_partition_backfills,
)
from xic_extractor.untargeted_execution.partition_composition import (
    ChildOutputProjection,
    ComposedUntargetedPartition,
    PersistedUntargetedPartition,
    UntargetedPartitionInputs,
    compose_and_persist_untargeted_partition,
    compose_untargeted_batch,
    group_acquired_partition,
)


@dataclass(frozen=True, slots=True)
class _GroupingInputs:
    batch: UntargetedPartitionInputs
    prepared: CidNlGrouping
    backfill_questions: tuple[CidNlBackfillQuestion, ...] = ()


@dataclass(frozen=True, slots=True)
class _SpooledGrouping:
    """One partition's initial grouping, which the worker reads from the spool."""

    path: Path
    shared_owners: Path

    def read(self) -> _GroupingInputs:
        return cast(
            _GroupingInputs,
            read_spooled_header_field(self.path, "grouping", self.shared_owners),
        )


def _direct_evidence_partition_rows(
    task: tuple[_SpooledGrouping, SampleRtFrame],
) -> tuple[dict[str, float], ...]:
    """One partition's direct-evidence rows under the frozen calibration."""
    spooled, rt_frame = task
    return direct_evidence_rows(spooled.read().prepared, rt_frame=rt_frame)


def _finalize_partition_grouping(
    task: tuple[_SpooledGrouping, SampleRtFrame, bool],
) -> tuple[WrittenPartition, tuple[Mapping[str, object], ...]]:
    """One partition's final grouping, with its Backfill questions when a
    Backfill follows, written into the spool; and the progress it reported."""
    spooled, rt_frame, plan_backfill = task
    initial = spooled.read()
    events: list[Mapping[str, object]] = []
    prepared = regroup_cid_nl_grouping(
        initial.prepared, rt_frame=rt_frame, progress_callback=events.append
    )
    questions = (
        plan_partition_backfills(initial.batch.jobs, prepared)
        if plan_backfill and prepared.alignment_config.backfill_enabled
        else ()
    )
    final = _GroupingPartition(_GroupingInputs(initial.batch, prepared, questions))
    written = write_spooled_partition(spooled.path.parent, final, spooled.shared_owners)
    return written, tuple(events)


@dataclass(frozen=True, slots=True)
class FrozenBackfillPartition:
    """A complete set of competing groups, its already acquired assessments and
    the Backfill questions planned with its final grouping."""

    batch: UntargetedPartitionInputs
    grouping: CidNlGrouping
    initial_results: tuple[UntargetedInitialSampleResult, ...]
    questions: tuple[CidNlBackfillQuestion, ...]

    def execute(
        self,
        partition_key: str,
        pool: WorkerAdmissionPool,
        *,
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> UntargetedPartitionResult:
        return execute_partition(
            self.batch.jobs,
            pool=pool,
            partition_key=partition_key,
            alignment_config=self.grouping.alignment_config,
            prepare_grouping=lambda _: self.grouping,
            initial_results=self.initial_results,
            initial_only=False,
            backfill_questions=self.questions,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )


@dataclass(frozen=True, slots=True)
class _BackfillPartitionTask:
    """One Backfill partition, which a worker runs whole from the spool.

    The worker reads the final grouping and the sealed initial assessments
    once, revisits each sample in turn and writes the composed partition
    beside the spool; the parent only adopts the file.
    """

    partition_key: str
    final: _SpooledGrouping
    initial_results: Path
    initial_records: tuple[tuple[int, int], ...]
    cancel_requested: Path
    alignment_config: AlignmentConfig
    project_child: ChildOutputProjection
    sample_rt_frame: SampleRtFrame | None
    known_istd_reference: KnownIstdReference | None


def _run_backfill_partition(
    task: _BackfillPartitionTask,
) -> tuple[WrittenPartition, ComposedUntargetedPartition]:
    """Revisit one partition's samples and compose it on this worker."""
    final = task.final.read()
    frozen = FrozenBackfillPartition(
        replace(final.batch, prepared_grouping=None),
        final.prepared,
        cast(
            tuple[UntargetedInitialSampleResult, ...],
            read_spooled_tuple(
                task.initial_results, task.initial_records, task.final.shared_owners
            ),
        ),
        final.backfill_questions,
    )
    with WorkerAdmissionPool(max_workers=1, backend="thread") as pool:
        leaf = frozen.execute(
            task.partition_key, pool, should_cancel=task.cancel_requested.exists
        )
    if leaf.failure is not None:
        raise leaf.failure
    return compose_and_persist_untargeted_partition(
        replace(frozen.batch, prepared_grouping=leaf.prepared_grouping),
        leaf.sample_results,
        write=lambda payload, children: write_spooled_partition(
            task.final.path.parent,
            payload,
            task.final.shared_owners,
            child_outputs=children,
        ),
        alignment_config=task.alignment_config,
        project_child=task.project_child,
        sample_rt_frame=task.sample_rt_frame,
        known_istd_reference=task.known_istd_reference,
    )


@dataclass(frozen=True, slots=True)
class _GroupingPartition:
    """Grouping checkpoint in the result table, with optional initial acquisition."""

    grouping: _GroupingInputs
    initial_results: tuple[UntargetedInitialSampleResult, ...] = ()
    child_outputs: tuple[()] = ()
    excluded_candidates: tuple[()] = ()
    method_assessments: tuple[()] = ()
    initial_groups: tuple[()] = ()


def _initialize_sample_process(dll_dir: Path) -> None:
    ensure_assemblies_loaded(dll_dir, importlib.import_module)


def materialize_untargeted_partition(
    plan: UntargetedInputPlan,
    subject_batch: UntargetedCidNlMethodSubjectBatch,
    *,
    mass_recalibration_by_sample: Mapping[str, PreparedRawMassRecalibration]
    | None = None,
) -> UntargetedPartitionInputs:
    """Bind a finite subject partition to shared, already-validated inputs."""

    candidates, support = plan.partition_input_loader(
        tuple(cid for cid, _ in subject_batch.candidate_memberships)
    )
    context = replace(plan.context, peaks_by_sample=candidates)
    templates: list[UntargetedPartitionJob] = []
    for sample in context.samples:
        stem = sample.raw_stem
        templates.extend(
            build_sample_jobs(
                processing_attempt_id=context.processing_attempt_id,
                method_subjects=context.method_subject,
                sample=sample,
                subject_work_units=tuple(
                    (
                        subject,
                        f"untargeted-cid-nl:{stem}:{subject.method_subject_id}",
                    )
                    for subject in subject_batch.subjects[:1]
                ),
                candidates=candidates[stem],
                discovery_receipt=context.discovery_receipts_by_sample[stem],
                settings=context.settings,
                raw_path=context.raw_paths_by_sample[stem],
                dll_dir=plan.dll_dir,
                signal_processing_profile=context.signal_processing_profile,
                acquisition_source_expectation=context.acquisition_sources_by_sample[
                    stem
                ],
                subject_partition=subject_batch,
                support_candidates=support[stem],
            )
        )
    jobs = []
    subjects_by_id = {
        subject.method_subject_id: subject for subject in subject_batch.subjects
    }
    for source_template in templates:
        template = copy(source_template)
        if mass_recalibration_by_sample:
            object.__setattr__(
                template,
                "prepared_mass_recalibration",
                mass_recalibration_by_sample[template.sample.raw_stem],
            )
        for subject_id in subjects_by_id:
            subject = subjects_by_id[subject_id]
            job = copy(template)
            subject_id = subject.method_subject_id
            object.__setattr__(job, "method_subject", subject)
            object.__setattr__(
                job,
                "work_unit_key",
                f"untargeted-cid-nl:{template.sample.raw_stem}:{subject_id}",
            )
            jobs.append(job)
    return UntargetedPartitionInputs(
        subject_batch=subject_batch,
        context=context,
        jobs=tuple(jobs),
    )


class UntargetedPartitionPipelineAdapter:
    """Adapts CID-NL partition acquisition, composition, and closure to the
    generic execution engine.
    """

    def __init__(
        self,
        plan: UntargetedInputPlan,
        *,
        alignment_config: AlignmentConfig,
        known_istd_reference: KnownIstdReference | None = None,
        execution: ExecutionProfile,
        max_partition_jobs: int = 100_000,
        grouping_only: bool = False,
        capture_grouping: Callable[[PreparedGroupingExperiment], None] | None = None,
        capture_backfill: Callable[[FrozenBackfillPartition], None] | None = None,
        mass_recalibration_by_sample: Mapping[str, PreparedRawMassRecalibration]
        | None = None,
        project_child: ChildOutputProjection | None = None,
    ) -> None:
        if project_child is None and not grouping_only:
            raise ValueError("composing children requires an output projection")
        self._grouping_only = grouping_only
        self._bootstrap_anchors: dict[int, tuple[RtAnchorObservation, ...]] = {}
        self._capture_grouping = capture_grouping
        self._capture_backfill = capture_backfill
        self._project_child = project_child
        self._plan = plan
        self._alignment_config = alignment_config
        sample_universe = frozenset(sample.raw_stem for sample in plan.context.samples)
        reference_frame = plan.context.rt_frame.for_sample_stems(sample_universe)
        self._known_istd_reference = (
            None
            if known_istd_reference is None
            else known_istd_reference.prepare_batch(
                sample_universe=sample_universe,
                injections=reference_frame.injections,
            )
        )
        self._max_partition_jobs = max_partition_jobs
        self._execution = execution
        self._mass_recalibration_by_sample = mass_recalibration_by_sample

        context = plan.context
        self._samples = context.samples
        self._prepared_worksets: PreparedUntargetedAlignmentWorksets = (
            prepare_untargeted_alignment_worksets(
                plan,
                known_istd_reference=self._known_istd_reference,
            )
        )
        subject_partitions = pack_subject_partitions(
            self._prepared_worksets,
            max_partition_jobs=max_partition_jobs,
            sample_count=len(context.samples),
        )
        self._acquisition_workers = (
            2
            if execution.overlap_partitions
            and len(subject_partitions) > 1
            and execution.alignment_workers >= 2
            else 1
        )
        self._partition_raw_workers = (
            execution.alignment_workers
            if execution.backend == "process"
            else execution.alignment_workers // self._acquisition_workers
        )
        partitions: list[WorkPartition[tuple[CidNlMethodSubject, ...]]] = []
        for phase in ("initial",) if grouping_only else ("initial", "backfill"):
            for index, subjects in enumerate(subject_partitions):
                partition_key = f"partition-{index:05d}-{phase}"
                partitions.append(
                    WorkPartition(
                        index=len(partitions),
                        partition_key=partition_key,
                        payload=subjects,
                    )
                )
        self._partitions = tuple(partitions)
        self._bootstrap_count = len(subject_partitions)
        self._initial_partitions: dict[int, SealedPartition] = {}
        self._final_groupings: dict[int, SealedPartition] = {}
        self._backfill_partitions: list[PersistedUntargetedPartition] = []
        self._initial_job_count = 0
        self._batch_preparation = CidNlBatchGrouping(context.rt_frame)
        self._partition_subject_batches = {
            partition.partition_key: self._prepared_worksets.subject_batch(
                partition.payload
            )
            for partition in self._partitions
        }
        self._process_pool_lock = threading.Lock()
        self._process_pools_closed = False
        self._process_pools: tuple[WorkerAdmissionPool, ...] = ()
        if execution.backend == "process":
            shared_objects = _sample_worker_shared_objects(
                self._samples,
                _sample_worker_subject_owners(
                    plan,
                    tuple(
                        self._partition_subject_batches[partition.partition_key]
                        for partition in self._partitions
                    ),
                ),
            )
            dll_dir = plan.dll_dir
            self._process_pools = (
                WorkerAdmissionPool(
                    max_workers=self._partition_raw_workers,
                    backend="process",
                    thread_name_prefix="cid-nl-sample",
                    initializer=partial(_initialize_sample_process, dll_dir),
                    shared_objects=shared_objects,
                ),
            )

    @property
    def partitions(
        self,
    ) -> Sequence[WorkPartition[tuple[CidNlMethodSubject, ...]]]:
        return self._partitions

    @property
    def bootstrap_partitions(
        self,
    ) -> Sequence[WorkPartition[tuple[CidNlMethodSubject, ...]]]:
        """Complete scientific worksets, each acquired once before calibration."""
        return self._partitions[: self._bootstrap_count]

    def execution_phases(
        self,
        *,
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> tuple[PartitionPipelinePhase[tuple[CidNlMethodSubject, ...]], ...]:
        """Operational chunks never close the batch's scientific evidence scope."""
        phases: tuple[PartitionPipelinePhase[tuple[CidNlMethodSubject, ...]], ...] = (
            PartitionPipelinePhase("initial-acquisition", self.bootstrap_partitions),
            PartitionPipelinePhase(
                "final-grouping",
                (),
                partial(self.prepare_batch_search, progress_callback=progress_callback),
            ),
        )
        if self._grouping_only:
            return phases
        return phases + (
            PartitionPipelinePhase(
                "backfill",
                (),
                partial(self.run_backfill, progress_callback=progress_callback),
            ),
        )

    @property
    def acquisition_workers(self) -> int:
        return self._acquisition_workers

    def close_process_worker_pools(self) -> None:
        """Close attempt-scoped process workers before terminal acknowledgement."""

        with self._process_pool_lock:
            if self._process_pools_closed:
                return
            self._process_pools_closed = True
        first_error: Exception | None = None
        for pool in self._process_pools:
            try:
                pool.__exit__(None, None, None)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def get_shared_objects(self) -> tuple[object, ...]:
        return _shared_objects(self._plan)

    def materialize_partition(
        self, partition: WorkPartition[tuple[CidNlMethodSubject, ...]]
    ) -> UntargetedPartitionInputs:
        """Load the same complete worksets for execution or initial-only replay."""
        return materialize_untargeted_partition(
            self._plan,
            self._partition_subject_batches[partition.partition_key],
            mass_recalibration_by_sample=self._mass_recalibration_by_sample,
        )

    def prepare_batch_search(
        self,
        store: StreamedResultStore,
        check_cancellation: Callable[[], None],
        *,
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        """Finish the frozen-calibration grouping phase before any Backfill.

        Partitions regroup independently on the RAW workers, so at most one
        grouping per worker is resident. Each worker reads its partition's
        initial grouping from the spool and writes the final one beside it, so
        the groupings never pass through this process. Both full execution and
        snapshots consume these answers; neither regroups in the Backfill lane.
        The same worker plans the partition's Backfill questions, which a
        Backfill partition would otherwise compute before it could dispatch.
        """
        if len(self._initial_partitions) != self._bootstrap_count:
            raise RuntimeError("initial acquisition phase is incomplete")
        check_cancellation()
        reference = CidNlBatchGrouping(self._plan.context.rt_frame)
        for index in range(self._bootstrap_count):
            reference.add_anchor_observations(self._bootstrap_anchors[index])
        self._batch_preparation.freeze_calibration(reference=reference)
        frozen = self._batch_preparation.frozen_rt_frame
        assert frozen is not None
        rt_frame: SampleRtFrame = frozen
        partitions = tuple(self.bootstrap_partitions)
        plan_backfill = not self._grouping_only or self._capture_backfill is not None
        shared_owners = store.shared_owners_path()

        def task(
            partition: WorkPartition[tuple[CidNlMethodSubject, ...]],
        ) -> tuple[_SpooledGrouping, SampleRtFrame]:
            check_cancellation()
            spooled = _SpooledGrouping(
                store.partition_path(self._initial_partitions[partition.index].index),
                shared_owners,
            )
            return spooled, rt_frame

        def finalize_task(
            partition: WorkPartition[tuple[CidNlMethodSubject, ...]],
        ) -> tuple[_SpooledGrouping, SampleRtFrame, bool]:
            return (*task(partition), plan_backfill)

        def seal(
            completed: int,
            total: int,
            partition: WorkPartition[tuple[CidNlMethodSubject, ...]],
            result: tuple[WrittenPartition, tuple[Mapping[str, object], ...]],
        ) -> None:
            check_cancellation()
            written, events = result
            self._final_groupings[partition.index] = store.adopt_written(
                f"partition-{partition.index:05d}-grouping", written
            )
            if progress_callback is not None:
                for event in events:
                    progress_callback(event)
                progress_callback(
                    {
                        "event": "partition_progress",
                        "stage": "final_grouping",
                        "completed": completed,
                        "total": total,
                    }
                )

        pool_context: nullcontext[WorkerAdmissionPool] | WorkerAdmissionPool
        if self._execution.backend == "process":
            if self._process_pools_closed:
                raise RuntimeError("process worker pools are already closed")
            pool_context = nullcontext(self._process_pools[0])
        else:
            pool_context = WorkerAdmissionPool(
                max_workers=self._partition_raw_workers,
                backend=self._execution.backend,
                thread_name_prefix="cid-nl-grouping",
            )
        with pool_context as pool:
            if self._batch_preparation.sample_shift_takes_rows:
                rows: list[dict[str, float]] = []
                pool.run_windowed(
                    partitions,
                    _direct_evidence_partition_rows,
                    task_arg_fn=task,
                    on_completed=lambda _done, _total, _partition, found: rows.extend(
                        found
                    ),
                )
                if pool.first_error is not None:
                    raise pool.first_error
                self._batch_preparation.vote_sample_shift(rows)
                refined = self._batch_preparation.frozen_rt_frame
                assert refined is not None
                rt_frame = refined
            pool.run_windowed(
                partitions,
                _finalize_partition_grouping,
                task_arg_fn=finalize_task,
                on_completed=seal,
            )
            if pool.first_error is not None:
                raise pool.first_error
        if self._capture_backfill is not None:
            for partition in partitions:
                final = cast(
                    _GroupingInputs,
                    store.read_header_field(
                        self._final_groupings[partition.index].index, "grouping"
                    ),
                )
                self._capture_backfill(
                    self._backfill_partition(store, partition.index, final)
                )

    def _backfill_partition(
        self, store: StreamedResultStore, index: int, final: _GroupingInputs
    ) -> FrozenBackfillPartition:
        return FrozenBackfillPartition(
            replace(final.batch, prepared_grouping=None),
            final.prepared,
            cast(
                tuple[UntargetedInitialSampleResult, ...],
                store.read_tuple(
                    self._initial_partitions[index].index, "initial_results"
                ),
            ),
            final.backfill_questions,
        )

    def execute_acquisition(
        self,
        partition: WorkPartition[tuple[CidNlMethodSubject, ...]],
        *,
        should_cancel: Callable[[], bool] | None = None,
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> PartitionAcquisitionOutput[
        tuple[
            UntargetedPartitionInputs,
            UntargetedPartitionResult,
        ]
    ]:
        batch = self.materialize_partition(partition)
        execute = partial(
            execute_partition,
            batch.jobs,
            partition_key=partition.partition_key,
            prepare_grouping=partial(
                group_acquired_partition,
                batch,
                alignment_config=self._alignment_config,
                known_istd_reference=self._known_istd_reference,
                progress_callback=progress_callback,
                bootstrap_only=True,
            ),
            alignment_config=self._alignment_config,
            initial_only=True,
            initial_results=None,
        )

        if self._execution.backend == "process":
            if self._process_pools_closed:
                raise RuntimeError("process worker pools are already closed")
            pool = self._process_pools[0]
            if pool.stopped:
                cause = pool.first_error
                raise RuntimeError(
                    "process worker pool failure stopped admission: "
                    f"{type(cause).__name__}: {cause}"
                ) from cause
            pool_context: nullcontext[WorkerAdmissionPool] | WorkerAdmissionPool = (
                nullcontext(pool)
            )
        else:
            pool_context = WorkerAdmissionPool(
                max_workers=self._partition_raw_workers,
                backend=self._execution.backend,
                thread_name_prefix="cid-nl-sample",
            )
        with pool_context as pool:
            leaf = execute(
                pool=pool,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            )
        if leaf.failure is None and leaf.prepared_grouping is not None:
            self._bootstrap_anchors[partition.index] = (
                leaf.prepared_grouping.rt_anchor_observations
            )
        return PartitionAcquisitionOutput(
            result=(replace(batch, prepared_grouping=leaf.prepared_grouping), leaf),
            failure=leaf.failure,
        )

    def execute_composition(
        self,
        partition: WorkPartition[tuple[CidNlMethodSubject, ...]],
        acquisition_output: PartitionAcquisitionOutput[
            tuple[
                UntargetedPartitionInputs,
                UntargetedPartitionResult,
            ]
        ],
        store: StreamedResultStore,
        *,
        should_cancel: Callable[[], bool] | Callable[[], None] | None = None,
    ) -> PartitionCompositionOutput[PersistedUntargetedPartition]:
        batch, leaf = acquisition_output.result
        assert leaf.prepared_grouping is not None
        prepared = leaf.prepared_grouping
        sealed = store.append(
            partition.partition_key,
            _GroupingPartition(
                grouping=_GroupingInputs(batch, prepared),
                initial_results=tuple(
                    UntargetedInitialSampleResult(result.job, result.adapter_result)
                    for result in leaf.sample_results
                ),
            ),
        )
        self._batch_preparation.add_initial(prepared)
        self._initial_job_count += len(batch.jobs)
        self._initial_partitions[partition.index] = sealed
        return PartitionCompositionOutput(
            result=PersistedUntargetedPartition(sealed), sealed=sealed
        )

    def run_backfill(
        self,
        store: StreamedResultStore,
        check_cancellation: Callable[[], None],
        *,
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        """Run each Backfill partition whole on a worker; adopt them in order.

        A worker reads its partition's final grouping and initial assessments
        from the spool once, revisits every sample and composes, so nothing of
        the partition passes through this process. Cancellation reaches the
        workers through a marker in the spool, checked between samples.
        """
        partitions = tuple(self._partitions[self._bootstrap_count :])
        shared_owners = store.shared_owners_path()
        cancel_requested = store.directory / "cancel-requested"
        cancelled = False
        started = 0
        finished: dict[int, tuple[WrittenPartition, ComposedUntargetedPartition]] = {}

        def report(stage: str, completed: int) -> None:
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "partition_progress",
                        "stage": stage,
                        "completed": completed,
                        "total": len(partitions),
                    }
                )

        def should_cancel() -> bool:
            nonlocal cancelled
            if not cancelled:
                try:
                    check_cancellation()
                except InterruptedError:
                    cancelled = True
                    cancel_requested.touch()
            return cancelled

        def task(
            partition: WorkPartition[tuple[CidNlMethodSubject, ...]],
        ) -> _BackfillPartitionTask:
            nonlocal started
            index = partition.index - self._bootstrap_count
            initial = self._initial_partitions[index].index
            report("partition_start", started)
            started += 1
            return _BackfillPartitionTask(
                partition_key=partition.partition_key,
                final=_SpooledGrouping(
                    store.partition_path(self._final_groupings[index].index),
                    shared_owners,
                ),
                initial_results=store.partition_path(initial),
                initial_records=store.field_records(initial, "initial_results"),
                cancel_requested=cancel_requested,
                alignment_config=self._alignment_config,
                project_child=cast(ChildOutputProjection, self._project_child),
                sample_rt_frame=self._batch_preparation.frozen_rt_frame,
                known_istd_reference=self._known_istd_reference,
            )

        def adopt(
            completed: int,
            total: int,
            partition: WorkPartition[tuple[CidNlMethodSubject, ...]],
            result: tuple[WrittenPartition, ComposedUntargetedPartition],
        ) -> None:
            finished[partition.index] = result
            while len(self._backfill_partitions) < len(partitions):
                following = partitions[len(self._backfill_partitions)]
                if following.index not in finished:
                    return
                written, composed = finished.pop(following.index)
                sealed = store.adopt_written(following.partition_key, written)
                self._backfill_partitions.append(
                    PersistedUntargetedPartition(sealed, composed)
                )
                report("partition_released", len(self._backfill_partitions))

        pool_context: nullcontext[WorkerAdmissionPool] | WorkerAdmissionPool
        if self._execution.backend == "process":
            if self._process_pools_closed:
                raise RuntimeError("process worker pools are already closed")
            pool_context = nullcontext(self._process_pools[0])
        else:
            pool_context = WorkerAdmissionPool(
                max_workers=self._partition_raw_workers,
                backend=self._execution.backend,
                thread_name_prefix="cid-nl-backfill",
            )
        with pool_context as pool:
            pool.run_windowed(
                partitions,
                _run_backfill_partition,
                task_arg_fn=task,
                on_completed=adopt,
                should_cancel=should_cancel,
            )
            if pool.first_error is not None:
                raise pool.first_error
        if len(self._backfill_partitions) != len(partitions):
            raise RuntimeError("Backfill left a partition unadopted")

    def finalize_application(
        self,
        store: StreamedResultStore,
        composition_outputs: Sequence[
            PartitionCompositionOutput[PersistedUntargetedPartition]
        ],
    ) -> FinalizedUntargetBatchApplicationResult | PreparedGroupingExperiment:
        if self._grouping_only or self._capture_grouping is not None:
            experiment = grouping_experiment_from_prepared(
                (
                    (
                        cast(
                            _GroupingInputs,
                            store.read_header_field(
                                self._initial_partitions[index].index, "grouping"
                            ),
                        ).prepared,
                        cast(
                            _GroupingInputs,
                            store.read_header_field(
                                self._final_groupings[index].index, "grouping"
                            ),
                        ).prepared,
                    )
                    for index in range(self._bootstrap_count)
                ),
                sample_stems=tuple(
                    sample.raw_stem for sample in self._plan.context.samples
                ),
                injection_order=self._plan.context.rt_frame.injections.snapshot_orders(),
                alignment_config=self._alignment_config,
                initial_job_count=self._initial_job_count,
                rt_calibration=(
                    frame.calibration_for_snapshot
                    if (frame := self._batch_preparation.frozen_rt_frame) is not None
                    else None
                ),
                rt_model_inputs=self._batch_preparation.rt_model_inputs,
            )
            if self._grouping_only:
                return experiment
            assert self._capture_grouping is not None
            self._capture_grouping(experiment)
        return compose_untargeted_batch(
            self._plan,
            store=store,
            partitions=(
                *(output.result for output in composition_outputs),
                *self._backfill_partitions,
            ),
        )

    def build_workflow_result(
        self,
        application_result: FinalizedUntargetBatchApplicationResult
        | PreparedGroupingExperiment,
        store: StreamedResultStore,
    ) -> CompletedUntargetBatchWorkflowResult | PreparedGroupingExperiment:
        if isinstance(application_result, PreparedGroupingExperiment):
            store.cleanup()
            return application_result
        return CompletedUntargetBatchWorkflowResult.from_runtime(
            application_result=application_result,
            store=store,
            sample_rt_frame=self._batch_preparation.frozen_rt_frame,
        )


def execute_untargeted_plan(
    plan: UntargetedInputPlan,
    *,
    output_dir: Path,
    alignment_config: AlignmentConfig,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    known_istd_reference: KnownIstdReference | None = None,
    execution: ExecutionProfile,
    max_partition_jobs: int = 100_000,
    should_cancel: Callable[[], bool] | None = None,
    grouping_only: bool = False,
    capture_grouping: Callable[[PreparedGroupingExperiment], None] | None = None,
    capture_backfill: Callable[[FrozenBackfillPartition], None] | None = None,
    mass_recalibration_by_sample: Mapping[str, PreparedRawMassRecalibration]
    | None = None,
    project_child: ChildOutputProjection | None = None,
) -> CompletedUntargetBatchWorkflowResult | PreparedGroupingExperiment:
    """Spill projected subject results, then close the exact global population.

    A partition contains every sample for its subjects, preserving grouping and
    backfill decisions. Operational work units describe partition/sample phases;
    each composed child is stored only as its ``project_child`` projection,
    which grouping-only runs do not need.
    """
    if (grouping_only or capture_grouping is not None) and any(
        subject.composition is not None for subject in plan.subject_batch.subjects
    ):
        raise ValueError("Grouping replay requires a scalar CID-NL preset")

    adapter = UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
        execution=execution,
        max_partition_jobs=max_partition_jobs,
        grouping_only=grouping_only,
        capture_grouping=capture_grouping,
        capture_backfill=capture_backfill,
        mass_recalibration_by_sample=mass_recalibration_by_sample,
        project_child=project_child,
    )
    try:
        return PartitionPipelineScheduler.execute(
            adapter,
            phases=adapter.execution_phases(progress_callback=progress_callback),
            output_dir=output_dir,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            pipeline_overlap=execution.overlap_partitions,
            acquisition_workers=adapter.acquisition_workers,
            store_factory=StreamedResultStore,
            acquisition_cleanup=adapter.close_process_worker_pools,
        )
    finally:
        adapter.close_process_worker_pools()


def _shared_objects(plan: UntargetedInputPlan) -> tuple[object, ...]:
    context = plan.context
    values = [
        context,
        context.samples,
        *context.samples,
        plan.subject_batch,
        *plan.subject_batch.subjects,
        context.settings,
        context.signal_processing_profile,
    ]
    for sample in context.samples:
        values.extend(getattr(sample, field.name) for field in fields(sample))
    return tuple({id(value): value for value in values if value is not None}.values())


def _sample_worker_shared_objects(
    samples: tuple[SampleMetadata, ...],
    subject_batches: tuple[object, ...],
) -> tuple[object, ...]:
    """Select immutable global values that process sample tasks reference."""

    values = [samples, *samples, *subject_batches]
    return tuple({id(value): value for value in values}.values())


def _sample_worker_subject_owners(
    plan: UntargetedInputPlan,
    partition_batches: tuple[object, ...],
) -> tuple[object, ...]:
    values = [plan.subject_batch, *partition_batches]
    return tuple({id(value): value for value in values}.values())
