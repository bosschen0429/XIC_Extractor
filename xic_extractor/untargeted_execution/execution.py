"""Complete CID-NL subjects in finite partitions."""

from __future__ import annotations

import importlib
import shutil
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from copy import copy
from dataclasses import dataclass, fields, replace
from functools import partial
from pathlib import Path
from typing import cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetBatchApplicationResult,
)
from xic_extractor.configuration.execution_profile import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.execution.partitions import (
    PartitionAcquisitionOutput,
    PartitionCompositionOutput,
    PartitionPipelinePhase,
    PartitionPipelineScheduler,
    ProcessingOwners,
    WorkPartition,
)
from xic_extractor.execution.result_store import SealedPartition, StreamedResultStore
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration
from xic_extractor.raw_reader import ensure_assemblies_loaded
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
    prepare_untargeted_alignment_worksets,
)
from xic_extractor.untargeted_execution.partition.execution import execute_partition
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedInitialSampleResult,
    UntargetedPartitionJob,
    UntargetedPartitionResult,
    UntargetedPreparedGrouping,
    build_sample_jobs,
)
from xic_extractor.untargeted_execution.partition_composition import (
    PersistedUntargetedPartition,
    UntargetedBatchGroupingPreparation,
    UntargetedPartitionInputs,
    compose_and_persist_untargeted_partition,
    compose_untargeted_batch,
    prepare_untargeted_partition_grouping,
)


@dataclass(frozen=True, slots=True)
class _GroupingInputs:
    batch: UntargetedPartitionInputs
    prepared: UntargetedPreparedGrouping


@dataclass(frozen=True, slots=True)
class _GroupingPartition:
    """Grouping checkpoint in the result table, with optional initial acquisition."""

    grouping: _GroupingInputs
    initial_results: tuple[UntargetedInitialSampleResult, ...] = ()
    children: tuple[()] = ()
    excluded_candidates: tuple[()] = ()
    method_assessments: tuple[()] = ()
    initial_groups: tuple[()] = ()
    initial_edges: tuple[()] = ()
    capture_evidence: tuple[()] = ()


def _initialize_sample_process(dll_dir: Path) -> None:
    ensure_assemblies_loaded(dll_dir, importlib.import_module)


def materialize_untargeted_partition(
    plan: UntargetedInputPlan,
    subject_batch: UntargetedCidNlMethodSubjectBatch,
    *,
    prepared_worksets: PreparedUntargetedAlignmentWorksets | None = None,
    acquisition_cache_dir: Path | None = None,
    sparse: bool = False,
    mass_recalibration_by_sample: Mapping[str, PreparedRawMassRecalibration]
    | None = None,
) -> UntargetedPartitionInputs:
    """Bind a finite subject partition to shared, already-validated inputs."""

    prepared = (
        prepare_untargeted_alignment_worksets(plan)
        if prepared_worksets is None
        else prepared_worksets
    )
    if prepared.plan is not plan:
        raise ValueError("prepared alignment worksets crossed the production plan")
    expected_batch = (
        plan.subject_batch
        if subject_batch == plan.subject_batch
        else prepared.subject_batch(subject_batch.subjects)
    )
    if subject_batch.candidate_memberships != expected_batch.candidate_memberships:
        raise ValueError("CID-NL partition crossed prepared candidate memberships")

    if plan.partition_input_loader is not None:
        candidates, records, support = plan.partition_input_loader(
            tuple(cid for cid, _ in subject_batch.candidate_memberships)
        )
        context = replace(
            plan.context,
            candidates_by_sample=candidates,
            physical_records_by_candidate=records,
        )
        assert plan.dll_dir is not None
        templates: list[UntargetedPartitionJob] = []
        for sample in context.samples:
            stem = sample.metadata.raw_stem
            templates.extend(
                build_sample_jobs(
                    processing_owners=context.processing_owners,
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
                    peak_config=context.peak_config,
                    raw_path=context.raw_paths_by_sample[stem],
                    dll_dir=plan.dll_dir,
                    signal_processing_profile=context.signal_processing_profile,
                    physical_records=tuple(
                        records[item.candidate_id] for item in candidates[stem]
                    ),
                    acquisition_source_expectation=context.acquisition_sources_by_sample[
                        stem
                    ],
                    subject_partition=subject_batch,
                    support_candidates=support[stem],
                    acquisition_cache_dir=acquisition_cache_dir,
                )
            )
    else:
        context = plan.context
        templates = list(plan.sample_job_templates)
    jobs = []
    subjects_by_id = {
        subject.method_subject_id: subject for subject in subject_batch.subjects
    }
    bound_templates = []
    for source_template in templates:
        template = copy(source_template)
        object.__setattr__(template, "acquisition_cache_dir", acquisition_cache_dir)
        if mass_recalibration_by_sample:
            object.__setattr__(
                template,
                "prepared_mass_recalibration",
                mass_recalibration_by_sample[template.sample.metadata.raw_stem],
            )
        bound_templates.append(template)
        subject_ids = (
            tuple(
                subject_id
                for subject_id in subjects_by_id
                if template._request_candidates_by_subject.get(subject_id)
            )
            if sparse
            else tuple(subjects_by_id)
        )
        for subject_id in subject_ids:
            subject = subjects_by_id[subject_id]
            job = copy(template)
            subject_id = subject.method_subject_id
            object.__setattr__(job, "method_subject", subject)
            object.__setattr__(
                job,
                "work_unit_key",
                f"untargeted-cid-nl:{template.sample.metadata.raw_stem}:{subject_id}",
            )
            object.__setattr__(job, "acquisition_cache_dir", acquisition_cache_dir)
            jobs.append(job)
    return UntargetedPartitionInputs(
        subject_batch=subject_batch,
        context=context,
        jobs=tuple(jobs),
        sample_job_templates=tuple(bound_templates) if sparse else (),
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
        max_partition_jobs: int = 100_000,
        raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
        raw_worker_backend: str = "thread",
        acquisition_workers: int = 1,
        acquisition_cache_dir: Path | None = None,
        retain_partition_spool: bool = False,
        grouping_only: bool = False,
        mass_recalibration_by_sample: Mapping[str, PreparedRawMassRecalibration]
        | None = None,
    ) -> None:
        if type(acquisition_workers) is not int or acquisition_workers not in (1, 2):
            raise ValueError("acquisition_workers must be 1 or 2")
        self._retain_partition_spool = bool(retain_partition_spool)
        self._grouping_only = grouping_only
        self._plan = plan
        self._alignment_config = alignment_config
        sample_universe = frozenset(
            sample.metadata.raw_stem for sample in plan.context.samples
        )
        reference_frame = plan.context.rt_frame.for_sample_stems(sample_universe)
        self._known_istd_reference = (
            None
            if known_istd_reference is None
            else known_istd_reference.prepare_batch(
                sample_universe=sample_universe,
                injection_order=reference_frame.injection_order,
            )
        )
        self._max_partition_jobs = max_partition_jobs
        self._raw_workers = raw_workers
        self._raw_worker_backend = raw_worker_backend
        self._acquisition_cache_dir = acquisition_cache_dir
        self._mass_recalibration_by_sample = mass_recalibration_by_sample

        context = plan.context
        self._owners = context.processing_owners
        self._prepared_worksets: PreparedUntargetedAlignmentWorksets = (
            prepare_untargeted_alignment_worksets(
                plan,
                known_istd_reference=self._known_istd_reference,
            )
        )
        subjects_per_partition = max(1, max_partition_jobs // len(context.samples))
        subject_partitions_list: list[tuple[CidNlMethodSubject, ...]] = []
        current_subjects: list[CidNlMethodSubject] = []
        current_candidate_count = 0
        current_mass_bin: int | None = None
        worksets = self._prepared_worksets.worksets
        if plan.partition_input_loader is not None:
            worksets = tuple(
                sorted(
                    worksets,
                    key=lambda workset: (
                        min(subject.precursor_mz for subject in workset),
                        min(subject.method_subject_id for subject in workset),
                    ),
                )
            )
        for workset in worksets:
            mass_bin = int(min(subject.precursor_mz for subject in workset) // 100.0)
            candidate_count = sum(
                len(
                    self._prepared_worksets.memberships_by_subject.get(
                        subject.method_subject_id, ()
                    )
                )
                for subject in workset
            )
            if len(workset) > subjects_per_partition:
                raise ValueError(
                    "max_partition_jobs cannot contain one alignment subject workset"
                )
            if current_subjects and (
                len(current_subjects) + len(workset) > subjects_per_partition
                or (
                    plan.partition_input_loader is not None
                    and (
                        current_mass_bin != mass_bin
                        or current_candidate_count + candidate_count > 1024
                    )
                )
            ):
                subject_partitions_list.append(
                    tuple(
                        sorted(
                            current_subjects,
                            key=lambda subject: subject.method_subject_id,
                        )
                    )
                )
                current_subjects = []
                current_candidate_count = 0
            current_subjects.extend(workset)
            current_candidate_count += candidate_count
            current_mass_bin = mass_bin
        if current_subjects:
            subject_partitions_list.append(
                tuple(
                    sorted(
                        current_subjects,
                        key=lambda subject: subject.method_subject_id,
                    )
                )
            )
        subject_partitions = tuple(subject_partitions_list)
        self._acquisition_workers = (
            2
            if acquisition_workers == 2
            and len(subject_partitions) > 1
            and raw_workers >= 2
            else 1
        )
        self._partition_raw_workers = (
            raw_workers
            if raw_worker_backend == "process"
            else raw_workers // self._acquisition_workers
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
        self._initial_job_count = 0
        self._batch_preparation = UntargetedBatchGroupingPreparation(context)
        self._initial_store: StreamedResultStore | None = None
        self._partition_subject_batches = {
            partition.partition_key: self._prepared_worksets.subject_batch(
                partition.payload
            )
            for partition in self._partitions
        }
        self._process_pool_lock = threading.Lock()
        self._process_pools_closed = False
        self._process_pools: tuple[WorkerAdmissionPool, ...] = ()
        if raw_worker_backend == "process":
            shared_objects = _sample_worker_shared_objects(
                self._owners,
                _sample_worker_subject_owners(
                    plan,
                    tuple(
                        self._partition_subject_batches[partition.partition_key]
                        for partition in self._partitions
                    ),
                ),
            )
            dll_dir = _plan_dll_dir(plan)
            # Both acquisition lanes share the full process budget. A lane
            # waiting on grouping/composition does not strand half the workers.
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
                "backfill", self._partitions[self._bootstrap_count :]
            ),
        )

    def _is_bootstrap(
        self, partition: WorkPartition[tuple[CidNlMethodSubject, ...]]
    ) -> bool:
        return partition.index < self._bootstrap_count

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
            prepared_worksets=self._prepared_worksets,
            acquisition_cache_dir=self._acquisition_cache_dir,
            sparse=True,
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

        Keep only one partition's grouping resident. Both full execution and
        snapshots consume these answers; neither regroups in the Backfill lane.
        """
        if len(self._initial_partitions) != self._bootstrap_count:
            raise RuntimeError("initial acquisition phase is incomplete")
        check_cancellation()
        self._batch_preparation.freeze_calibration()
        self._initial_store = store
        for partition in self.bootstrap_partitions:
            check_cancellation()
            initial = cast(
                _GroupingInputs,
                store.read_header_field(
                    self._initial_partitions[partition.index].index, "grouping"
                ),
            )
            prepared = self._batch_preparation.finalize_grouping(
                initial.batch, initial.prepared, progress_callback=progress_callback
            )
            check_cancellation()
            self._final_groupings[partition.index] = store.append(
                f"partition-{partition.index:05d}-grouping",
                _GroupingPartition(_GroupingInputs(initial.batch, prepared)),
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "partition_progress",
                        "stage": "final_grouping",
                        "completed": partition.index + 1,
                        "total": self._bootstrap_count,
                    }
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
        initial_only = self._is_bootstrap(partition)
        initial_results = None
        grouping_callback: Callable[
            [tuple[UntargetedInitialSampleResult, ...]], UntargetedPreparedGrouping
        ]
        if initial_only:
            batch = self.materialize_partition(partition)
            grouping_callback = partial(
                prepare_untargeted_partition_grouping,
                batch,
                alignment_config=self._alignment_config,
                known_istd_reference=self._known_istd_reference,
                progress_callback=progress_callback,
                bootstrap_only=True,
            )
        else:
            store = self._initial_store
            if store is None:
                raise RuntimeError("Backfill requires frozen batch calibration")
            initial_partition = self._initial_partitions[
                partition.index - self._bootstrap_count
            ]
            # Reuse the final phase answer and the original acquisition records.
            final_grouping_inputs = cast(
                _GroupingInputs,
                store.read_header_field(
                    self._final_groupings[
                        partition.index - self._bootstrap_count
                    ].index,
                    "grouping",
                ),
            )
            batch = replace(final_grouping_inputs.batch, prepared_grouping=None)
            initial_results = cast(
                tuple[UntargetedInitialSampleResult, ...],
                store.read_tuple(initial_partition.index, "initial_results"),
            )

            def final_grouping(
                _: tuple[UntargetedInitialSampleResult, ...],
            ) -> UntargetedPreparedGrouping:
                return final_grouping_inputs.prepared

            grouping_callback = final_grouping

        def run(pool: WorkerAdmissionPool) -> UntargetedPartitionResult:
            return execute_partition(
                batch.jobs,
                pool=pool,
                prepare_grouping=grouping_callback,
                backfill_templates=batch.sample_job_templates,
                partition_key=partition.partition_key,
                alignment_config=self._alignment_config,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
                raw_workers=self._partition_raw_workers,
                raw_worker_backend=self._raw_worker_backend,
                initial_only=initial_only,
                initial_results=initial_results,
            )

        if self._raw_worker_backend == "process":
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
                backend=self._raw_worker_backend,
                thread_name_prefix="cid-nl-sample",
            )
        with pool_context as pool:
            leaf = run(pool=pool)
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
        retain_memory: bool = False,
        should_cancel: Callable[[], bool] | Callable[[], None] | None = None,
    ) -> PartitionCompositionOutput[PersistedUntargetedPartition]:
        batch, leaf = acquisition_output.result
        if self._is_bootstrap(partition):
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
            persisted = PersistedUntargetedPartition(sealed, 0, ())
            return PartitionCompositionOutput(result=persisted, sealed=sealed)

        def cancel_check() -> None:
            if should_cancel is not None and should_cancel():
                raise InterruptedError("CID-NL batch cancelled")

        persisted = compose_and_persist_untargeted_partition(
            batch,
            leaf.sample_results,
            store=store,
            partition_key=partition.partition_key,
            alignment_config=self._alignment_config,
            known_istd_reference=self._known_istd_reference,
            should_cancel=cancel_check,
            retain_memory=retain_memory,
            raw_workers=self._raw_workers,
        )
        return PartitionCompositionOutput(result=persisted, sealed=persisted.sealed)

    def finalize_application(
        self,
        store: StreamedResultStore,
        composition_outputs: Sequence[
            PartitionCompositionOutput[PersistedUntargetedPartition]
        ],
    ) -> FinalizedUntargetBatchApplicationResult | PreparedGroupingExperiment:
        if self._grouping_only:
            sample_stems = tuple(
                sample.metadata.raw_stem for sample in self._plan.context.samples
            )
            return grouping_experiment_from_prepared(
                (
                    cast(
                        _GroupingInputs,
                        store.read_header_field(
                            self._final_groupings[index].index, "grouping"
                        ),
                    ).prepared
                    for index in range(self._bootstrap_count)
                ),
                sample_stems=sample_stems,
                injection_order=self._plan.context.rt_frame.injection_order,
                alignment_config=self._alignment_config,
                initial_job_count=self._initial_job_count,
            )
        return compose_untargeted_batch(
            self._plan,
            store=store,
            partitions=tuple(output.result for output in composition_outputs),
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
            retain_partition_spool=self._retain_partition_spool,
        )


def execute_untargeted_plan(
    plan: UntargetedInputPlan,
    *,
    output_dir: Path,
    alignment_config: AlignmentConfig,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    known_istd_reference: KnownIstdReference | None = None,
    max_partition_jobs: int = 100_000,
    raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
    raw_worker_backend: str = "thread",
    should_cancel: Callable[[], bool] | None = None,
    retain_partition_memory: bool = False,
    retain_partition_spool: bool = False,
    retain_acquisition_cache: bool = False,
    acquisition_cache_dir: Path | None = None,
    pipeline_overlap: bool = False,
    grouping_only: bool = False,
    mass_recalibration_by_sample: Mapping[str, PreparedRawMassRecalibration]
    | None = None,
) -> CompletedUntargetBatchWorkflowResult | PreparedGroupingExperiment:
    """Spill complete subject results, then close the exact global population.

    A partition contains every sample for its subjects, preserving grouping and
    backfill decisions. Operational work units describe partition/sample phases;
    scientific per-subject receipts live in the owner-controlled result store.
    """
    if type(plan) is not UntargetedInputPlan:
        raise TypeError("bounded execution requires a production plan")
    if type(max_partition_jobs) is not int or max_partition_jobs < 1:
        raise ValueError("partition job budget must be a positive integer")
    if type(raw_workers) is not int or raw_workers < 1:
        raise ValueError("raw_workers must be a positive integer")
    if raw_worker_backend not in {"process", "thread"}:
        raise ValueError("raw_worker_backend must be 'process' or 'thread'")
    if grouping_only and any(
        subject.composition is not None for subject in plan.subject_batch.subjects
    ):
        raise ValueError("Grouping replay requires a scalar CID-NL preset")

    requested_acquisition_workers = 2 if pipeline_overlap and raw_workers >= 2 else 1
    # A shared cache directory outlives the run; the default one is the run's own.
    cache_dir = (
        Path(acquisition_cache_dir)
        if acquisition_cache_dir is not None
        else Path(output_dir) / ".cid_nl_acquisition_cache"
    )
    adapter = UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
        max_partition_jobs=max_partition_jobs,
        raw_workers=raw_workers,
        raw_worker_backend=raw_worker_backend,
        acquisition_workers=requested_acquisition_workers,
        acquisition_cache_dir=cache_dir,
        retain_partition_spool=retain_partition_spool,
        grouping_only=grouping_only,
        mass_recalibration_by_sample=mass_recalibration_by_sample,
    )
    completed_once = False
    try:
        result = PartitionPipelineScheduler.execute(
            adapter,
            phases=adapter.execution_phases(progress_callback=progress_callback),
            output_dir=output_dir,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            retain_partition_memory=retain_partition_memory,
            pipeline_overlap=pipeline_overlap,
            acquisition_workers=adapter.acquisition_workers,
            store_factory=StreamedResultStore,
            acquisition_cleanup=adapter.close_process_worker_pools,
        )
        completed_once = True
        return result
    finally:
        adapter.close_process_worker_pools()
        # The cache only accelerates acquisition, which is finished. A failed
        # attempt keeps it because the recovery marker allows a retry that
        # would otherwise reread every RAW file.
        if (
            completed_once
            and acquisition_cache_dir is None
            and not retain_acquisition_cache
        ):
            shutil.rmtree(cache_dir, ignore_errors=True)


def _shared_objects(plan: UntargetedInputPlan) -> tuple[object, ...]:
    context = plan.context
    owners = context.processing_owners
    values = [
        context,
        owners,
        context.samples,
        *context.samples,
        plan.subject_batch,
        *plan.subject_batch.subjects,
        context.settings,
        context.peak_config,
        context.signal_processing_profile,
    ]
    values.extend(getattr(owners, field.name) for field in fields(owners))
    for sample in context.samples:
        values.extend(getattr(sample, field.name) for field in fields(sample))
    return tuple({id(value): value for value in values if value is not None}.values())


def _sample_worker_shared_objects(
    owners: ProcessingOwners,
    subject_batches: tuple[object, ...],
) -> tuple[object, ...]:
    """Select immutable global owners that process sample tasks reference."""

    values = [
        owners,
        *tuple(getattr(owners, field.name) for field in fields(owners)),
        *subject_batches,
    ]
    return tuple({id(value): value for value in values}.values())


def _sample_worker_subject_owners(
    plan: UntargetedInputPlan,
    partition_batches: tuple[object, ...],
) -> tuple[object, ...]:
    values = [plan.subject_batch, *partition_batches]
    values.extend(template._request_subjects for template in plan.sample_job_templates)
    return tuple({id(value): value for value in values}.values())


def _plan_dll_dir(plan: UntargetedInputPlan) -> Path:
    if plan.dll_dir is not None:
        return plan.dll_dir
    if plan.sample_job_templates:
        return plan.sample_job_templates[0].dll_dir
    raise RuntimeError("process worker pool requires a planned DLL directory")
