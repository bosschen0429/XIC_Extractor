"""Complete CID-NL subjects in finite partitions under one runtime attempt."""

from __future__ import annotations

import importlib
import json
import shutil
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields, replace
from functools import partial
from pathlib import Path
from typing import cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetBatchApplicationResult,
    UntargetBatchApplicationResultRequest,
    UntargetedCandidateExclusion,
    finalize_alignment_result,
)
from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
from xic_extractor.evidence_artifacts import (
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
)
from xic_extractor.execution_engine import (
    PartitionAcquisitionOutput,
    PartitionCompositionOutput,
    PartitionPipelineScheduler,
    ProcessingOwners,
    StreamedResultStore,
    WorkerAdmissionPool,
    WorkPartition,
)
from xic_extractor.execution_engine.worker_pool import WorkerTransportMetrics
from xic_extractor.execution_runtime import (
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceKind,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeArtifactRegistrationReceipt,
    RuntimeFinalizationReceipt,
    RuntimeTelemetryProvider,
    execute_runtime_attempt,
    mint_work_unit_settlement,
)
from xic_extractor.execution_settings import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    PreparedCidNlAlignmentSubjectWorksets,
    UntargetedCidNlPersistedPartition,
    UntargetedCidNlProductionBatch,
    UntargetedCidNlProductionPlan,
    compose_and_persist_untargeted_cid_nl_partition,
    materialize_untargeted_cid_nl_partition,
    prepare_untargeted_cid_nl_alignment_subject_worksets,
    prepare_untargeted_cid_nl_grouping,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRuntimePartitionResult,
    execute_untargeted_cid_nl_runtime_partition,
)
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    CidNlMethodSubject,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import resolve_request_acquisition_source
from xic_extractor.raw_reader import _ensure_assemblies_loaded

PartitionResultStore = StreamedResultStore

_PARTITION_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.untargeted_cid_nl_bounded_runtime",
    semantic_role="closed_scientific_partition",
    schema_version="cid_nl_scientific_partition",
    validation_status="diagnostic_only",
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf",
)

_GLOBAL_CLOSURE_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.untargeted_cid_nl_bounded_runtime",
    semantic_role="closed_application_result",
    schema_version="cid_nl_global_closure",
    validation_status="diagnostic_only",
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf",
)


def _initialize_sample_process(dll_dir: Path) -> None:
    _ensure_assemblies_loaded(dll_dir, importlib.import_module)


class CidNlPartitionPipelineAdapter:
    """Adapts CID-NL partition acquisition, composition, and closure to the
    generic execution engine.
    """

    def __init__(
        self,
        plan: UntargetedCidNlProductionPlan,
        *,
        alignment_config: AlignmentConfig,
        known_istd_reference: KnownIstdReference | None = None,
        max_partition_jobs: int = 100_000,
        raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
        raw_worker_backend: str = "thread",
        acquisition_workers: int = 1,
        acquisition_cache_dir: Path | None = None,
        retain_partition_spool: bool = False,
    ) -> None:
        if type(acquisition_workers) is not int or acquisition_workers not in (1, 2):
            raise ValueError("acquisition_workers must be 1 or 2")
        self._retain_partition_spool = bool(retain_partition_spool)
        self._plan = plan
        self._alignment_config = alignment_config
        self._known_istd_reference = (
            None
            if known_istd_reference is None
            else known_istd_reference.prepare_batch(
                sample_universe=frozenset(
                    sample.metadata.raw_stem for sample in plan.context.samples
                ),
                injection_order={
                    sample.metadata.raw_stem: sample.metadata.injection_order
                    for sample in plan.context.samples
                    if sample.metadata.injection_order is not None
                },
            )
        )
        self._max_partition_jobs = max_partition_jobs
        self._raw_workers = raw_workers
        self._raw_worker_backend = raw_worker_backend
        self._acquisition_cache_dir = acquisition_cache_dir

        context = plan.context
        self._owners = context.processing_owners
        self._prepared_worksets: PreparedCidNlAlignmentSubjectWorksets = (
            prepare_untargeted_cid_nl_alignment_subject_worksets(
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
        self._partitions = tuple(
            WorkPartition(
                index=index,
                partition_key=f"partition-{index:05d}",
                payload=subjects,
            )
            for index, subjects in enumerate(subject_partitions)
        )
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
        self._global_closure_work_unit_key = (
            f"global-closure:{self._owners.request.processing_request_id}"
        )
        self._work_unit_keys = (
            *(
                f"{partition.partition_key}:{sample.identity.sample_identity_id}:{phase}"
                for partition in self._partitions
                for sample in context.samples
                for phase in ("initial", "recovery")
            ),
            self._global_closure_work_unit_key,
        )
        self._resource_reservations = tuple(
            RuntimeResourceReservation(
                attempt=self._owners.attempt,
                kind=RuntimeResourceKind.ACQUISITION_SESSION,
                resource_key=(
                    f"acquisition-session:{partition.partition_key}:"
                    f"{sample.identity.sample_identity_id}:{phase}"
                ),
                worker_key=(
                    f"inline-acquisition:{partition.partition_key}:"
                    f"{sample.identity.sample_identity_id}:{phase}"
                ),
                source_reference=resolve_request_acquisition_source(
                    processing_owners=self._owners, sample=sample
                ).acquisition_source_id,
                compatibility_token="untargeted-cid-nl-real-data-runtime",
                owner_conditional=True,
            )
            for partition in self._partitions
            for sample in context.samples
            for phase in ("initial", "recovery")
        )
        self._owner_completeness_attestation = (
            f"{self._owners.request.processing_request_id}:cid-nl-partitioned"
        )

    @property
    def partitions(
        self,
    ) -> Sequence[WorkPartition[tuple[CidNlMethodSubject, ...]]]:
        return self._partitions

    @property
    def processing_owners(self) -> ProcessingOwners:
        return self._owners

    @property
    def work_unit_keys(self) -> Sequence[str]:
        return self._work_unit_keys

    @property
    def resource_reservations(self) -> Sequence[RuntimeResourceReservation]:
        return self._resource_reservations

    @property
    def owner_completeness_attestation(self) -> str:
        return self._owner_completeness_attestation

    @property
    def acquisition_workers(self) -> int:
        return self._acquisition_workers

    @property
    def partition_raw_workers(self) -> int:
        return self._partition_raw_workers

    @property
    def process_pool_transport_metrics(
        self,
    ) -> tuple[WorkerTransportMetrics | None, ...]:
        return tuple(pool.transport_metrics for pool in self._process_pools)

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

    def execute_acquisition(
        self,
        partition: WorkPartition[tuple[CidNlMethodSubject, ...]],
        *,
        should_cancel: Callable[[], bool] | None = None,
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> PartitionAcquisitionOutput[
        tuple[
            UntargetedCidNlProductionBatch,
            UntargetedCidNlRuntimePartitionResult,
        ]
    ]:
        subject_batch = self._partition_subject_batches[partition.partition_key]
        batch = materialize_untargeted_cid_nl_partition(
            self._plan,
            subject_batch,
            prepared_worksets=self._prepared_worksets,
            acquisition_cache_dir=self._acquisition_cache_dir,
            sparse=True,
        )
        if self._raw_worker_backend == "process":
            if self._process_pools_closed:
                raise RuntimeError("process worker pools are already closed")
            pool = self._process_pools[0]
            if pool.stopped:
                raise RuntimeError("process worker pool failure stopped admission")
            leaf = execute_untargeted_cid_nl_runtime_partition(
                batch.jobs,
                prepare_grouping=partial(
                    prepare_untargeted_cid_nl_grouping,
                    batch,
                    alignment_config=self._alignment_config,
                    known_istd_reference=self._known_istd_reference,
                    progress_callback=progress_callback,
                ),
                backfill_templates=batch.sample_job_templates,
                partition_key=partition.partition_key,
                alignment_config=self._alignment_config,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
                raw_workers=self._partition_raw_workers,
                raw_worker_backend=self._raw_worker_backend,
                pool=pool,
            )
        else:
            with WorkerAdmissionPool(
                max_workers=self._partition_raw_workers,
                backend=self._raw_worker_backend,
                thread_name_prefix="cid-nl-sample",
            ) as pool:
                leaf = execute_untargeted_cid_nl_runtime_partition(
                    batch.jobs,
                    prepare_grouping=partial(
                        prepare_untargeted_cid_nl_grouping,
                        batch,
                        alignment_config=self._alignment_config,
                        known_istd_reference=self._known_istd_reference,
                        progress_callback=progress_callback,
                    ),
                    backfill_templates=batch.sample_job_templates,
                    partition_key=partition.partition_key,
                    alignment_config=self._alignment_config,
                    progress_callback=progress_callback,
                    should_cancel=should_cancel,
                    raw_workers=self._partition_raw_workers,
                    raw_worker_backend=self._raw_worker_backend,
                    pool=pool,
                )
        failure_detail = None
        if not leaf.succeeded:
            failed = next(
                receipt
                for receipt in leaf.worker_batch.settlements
                if receipt.outcome is not WorkUnitTerminalOutcome.SUCCEEDED
            )
            failure_detail = failed.failure_detail
        return PartitionAcquisitionOutput(
            result=(replace(batch, prepared_grouping=leaf.prepared_grouping), leaf),
            resources=leaf.worker_batch.resource_settlements,
            acquisitions=leaf.acquisitions,
            succeeded=leaf.succeeded,
            failure_detail=failure_detail,
        )

    def execute_composition(
        self,
        partition: WorkPartition[tuple[CidNlMethodSubject, ...]],
        acquisition_output: PartitionAcquisitionOutput[
            tuple[
                UntargetedCidNlProductionBatch,
                UntargetedCidNlRuntimePartitionResult,
            ]
        ],
        store: StreamedResultStore,
        *,
        retain_memory: bool = False,
        should_cancel: Callable[[], bool] | Callable[[], None] | None = None,
    ) -> PartitionCompositionOutput[UntargetedCidNlPersistedPartition]:
        batch, leaf = acquisition_output.result

        def cancel_check() -> None:
            if should_cancel is not None and should_cancel():
                raise InterruptedError("CID-NL batch cancelled")

        persisted = compose_and_persist_untargeted_cid_nl_partition(
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

    def create_partition_artifacts(
        self,
        partition: WorkPartition[tuple[CidNlMethodSubject, ...]],
        composition_output: PartitionCompositionOutput[
            UntargetedCidNlPersistedPartition
        ],
    ) -> tuple[
        Sequence[RuntimeArtifactRegistrationReceipt],
        Sequence[WorkUnitSettlement],
    ]:
        sealed = composition_output.sealed
        context = self._plan.context
        artifacts: list[RuntimeArtifactRegistrationReceipt] = []
        settlements: list[WorkUnitSettlement] = []
        for sample in context.samples:
            for phase in ("initial", "recovery"):
                key = (
                    f"{partition.partition_key}:"
                    f"{sample.identity.sample_identity_id}:{phase}"
                )
                artifact = _mint_evidence_artifact_ref(
                    producer_processing_request_id=(
                        self._owners.request.processing_request_id
                    ),
                    producer_processing_attempt_id=(
                        self._owners.attempt.processing_attempt_id
                    ),
                    semantic_contract=_PARTITION_CONTRACT,
                    payload_bytes=(
                        json.dumps(
                            {
                                "work_unit_key": key,
                                "partition_sha256": sealed.sha256,
                                "partition_bytes": sealed.size_bytes,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8"),
                    upstream_artifacts=(),
                )
                artifacts.append(RuntimeArtifactRegistrationReceipt(key, artifact))
                settlements.append(
                    mint_work_unit_settlement(
                        attempt=self._owners.attempt,
                        work_unit_key=key,
                        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                        owner_result_ref=artifact,
                    )
                )
        return tuple(artifacts), tuple(settlements)

    def finalize_application(
        self,
        store: StreamedResultStore,
        composition_outputs: Sequence[
            PartitionCompositionOutput[UntargetedCidNlPersistedPartition]
        ],
    ) -> FinalizedUntargetBatchApplicationResult:
        context = self._plan.context
        exclusion_positions: list[tuple[str, int, int]] = []
        child_positions: list[tuple[str, int, int]] = []
        for comp_out in composition_outputs:
            persisted = comp_out.result
            child_positions.extend(
                (key, persisted.sealed.index, offset)
                for offset, key in enumerate(persisted.child_order_keys)
            )
            exclusion_positions.extend(
                (candidate_id, persisted.sealed.index, offset)
                for offset, candidate_id in enumerate(persisted.excluded_candidate_ids)
            )
        scope = UntargetedSearchScope(
            schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
            request=self._owners.request,
            samples=context.samples,
            method_constraints=CaptureMethodConstraints(
                boundary_policy="resolver_interval",
                peak_config=context.peak_config,
                signal_processing_profile=context.signal_processing_profile,
            ),
            evidence_providers=tuple(
                EvidenceProviderBinding(CID_NL_PROVIDER_ID, subject.method_subject_id)
                for subject in self._plan.subject_batch.subjects
            ),
            declared_candidate_ids=tuple(
                candidate_id
                for candidate_id, _ in self._plan.subject_batch.candidate_memberships
            ),
            # Batch closure checks request membership and exact child owners.
            # Capture authorization and physical evidence belong to each child.
            capture_bindings=(),
        )
        children = store.sequence("children")
        if child_positions:
            ordered_children = tuple(sorted(child_positions))
            if len(ordered_children) != len(children):
                raise ValueError("partition child order lost a scientific result")
            children = ImmutableSequence(
                (1,) * len(ordered_children),
                lambda index: store.read_selected(
                    ordered_children[index][1],
                    "children",
                    (ordered_children[index][2],),
                ),
            )
        return cast(
            FinalizedUntargetBatchApplicationResult,
            finalize_alignment_result(
                UntargetBatchApplicationResultRequest(
                    scope=scope,
                    processing_request=self._owners.request,
                    processing_attempt=self._owners.attempt,
                    children=children,
                    excluded_candidates=_ordered_exclusions(
                        store, exclusion_positions, self._plan.excluded_candidates
                    ),
                )
            ),
        )

    @property
    def global_closure_work_unit_key(self) -> str:
        return self._global_closure_work_unit_key

    def create_global_closure_artifacts(
        self,
        application_result: FinalizedUntargetBatchApplicationResult,
    ) -> tuple[
        Sequence[RuntimeArtifactRegistrationReceipt],
        Sequence[WorkUnitSettlement],
    ]:
        key = self._global_closure_work_unit_key
        artifact = _mint_evidence_artifact_ref(
            producer_processing_request_id=(self._owners.request.processing_request_id),
            producer_processing_attempt_id=(self._owners.attempt.processing_attempt_id),
            semantic_contract=_GLOBAL_CLOSURE_CONTRACT,
            payload_bytes=(
                json.dumps(
                    {
                        "work_unit_key": key,
                        "completed_children_count": len(application_result.children),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8"),
            upstream_artifacts=(),
        )
        receipt = RuntimeArtifactRegistrationReceipt(key, artifact)
        settlement = mint_work_unit_settlement(
            attempt=self._owners.attempt,
            work_unit_key=key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=artifact,
        )
        return (receipt,), (settlement,)

    def build_workflow_result(
        self,
        runtime_receipt: RuntimeFinalizationReceipt,
        application_result: FinalizedUntargetBatchApplicationResult,
        store: StreamedResultStore,
    ) -> CompletedUntargetBatchWorkflowResult:
        return CompletedUntargetBatchWorkflowResult(
            runtime_receipt=runtime_receipt,
            application_result=application_result,
            method_assessments=store.sequence("method_assessments"),
            initial_groups=store.sequence("initial_groups"),
            initial_edges=store.sequence("initial_edges"),
            capture_evidence=store.sequence("capture_evidence"),
            cleanup_action=self._release_store(store),
        )

    def _release_store(self, store: StreamedResultStore) -> Callable[[], None]:
        """Decide what closing the workflow result does to the spool.

        Retaining publishes the record index and keeps the partition files, so
        a later reader can restore one record instead of receiving a copy of
        every record. The default still deletes the spool.
        """
        if not self._retain_partition_spool:
            return store.cleanup

        def retain() -> None:
            store.preserve_results()

        return retain


def execute_bounded_untargeted_cid_nl(
    plan: UntargetedCidNlProductionPlan,
    *,
    output_dir: Path,
    alignment_config: AlignmentConfig,
    telemetry_provider: RuntimeTelemetryProvider,
    recovery_marker: OwnerPathRuntimeMarker,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    known_istd_reference: KnownIstdReference | None = None,
    max_partition_jobs: int = 100_000,
    raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
    raw_worker_backend: str = "thread",
    should_cancel: Callable[[], bool] | None = None,
    retain_partition_memory: bool = False,
    retain_partition_spool: bool = False,
    retain_acquisition_cache: bool = False,
    pipeline_overlap: bool = False,
) -> CompletedUntargetBatchWorkflowResult:
    """Spill complete subject results, then close the exact global population.

    A partition contains every sample for its subjects, preserving grouping and
    backfill decisions. Operational work units describe partition/sample phases;
    scientific per-subject receipts live in the owner-controlled result store.
    """
    if type(plan) is not UntargetedCidNlProductionPlan:
        raise TypeError("bounded execution requires a production plan")
    if type(max_partition_jobs) is not int or max_partition_jobs < 1:
        raise ValueError("partition job budget must be a positive integer")
    if type(raw_workers) is not int or raw_workers < 1:
        raise ValueError("raw_workers must be a positive integer")
    if raw_worker_backend not in {"process", "thread"}:
        raise ValueError("raw_worker_backend must be 'process' or 'thread'")

    requested_acquisition_workers = 2 if pipeline_overlap and raw_workers >= 2 else 1
    acquisition_cache_dir = Path(output_dir) / ".cid_nl_acquisition_cache"
    adapter = CidNlPartitionPipelineAdapter(
        plan,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
        max_partition_jobs=max_partition_jobs,
        raw_workers=raw_workers,
        raw_worker_backend=raw_worker_backend,
        acquisition_workers=requested_acquisition_workers,
        acquisition_cache_dir=acquisition_cache_dir,
        retain_partition_spool=retain_partition_spool,
    )
    completed_once = False
    try:
        result = PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=telemetry_provider,
            recovery_marker=recovery_marker,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            retain_partition_memory=retain_partition_memory,
            pipeline_overlap=pipeline_overlap,
            acquisition_workers=adapter.acquisition_workers,
            execute_runtime_attempt=execute_runtime_attempt,
            store_factory=PartitionResultStore,
            acquisition_cleanup=adapter.close_process_worker_pools,
        )
        completed_once = True
        return result
    finally:
        adapter.close_process_worker_pools()
        # The cache only accelerates acquisition, which is finished. A failed
        # attempt keeps it because the recovery marker allows a retry that
        # would otherwise reread every RAW file.
        if completed_once and not retain_acquisition_cache:
            shutil.rmtree(acquisition_cache_dir, ignore_errors=True)


def _shared_objects(plan: UntargetedCidNlProductionPlan) -> tuple[object, ...]:
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
    plan: UntargetedCidNlProductionPlan,
    partition_batches: tuple[object, ...],
) -> tuple[object, ...]:
    values = [plan.subject_batch, *partition_batches]
    values.extend(template._request_subjects for template in plan.sample_job_templates)
    return tuple({id(value): value for value in values}.values())


def _plan_dll_dir(plan: UntargetedCidNlProductionPlan) -> Path:
    if plan.dll_dir is not None:
        return plan.dll_dir
    if plan.sample_job_templates:
        return plan.sample_job_templates[0].dll_dir
    raise RuntimeError("process worker pool requires a planned DLL directory")


def _ordered_exclusions(
    store: StreamedResultStore,
    positions: list[tuple[str, int, int]],
    planning_exclusions: tuple[UntargetedCandidateExclusion, ...],
) -> ImmutableSequence:
    """Restore canonical candidate order with finite result windows."""
    all_positions = list(positions)
    # Planning exclusions have no acquisition partition or assessment payload.
    all_positions.extend(
        (item.candidate_id, -1, offset)
        for offset, item in enumerate(planning_exclusions)
    )
    ordered = tuple(sorted(all_positions))
    windows = tuple(
        ordered[start : start + 2048] for start in range(0, len(ordered), 2048)
    )

    def load_window(index: int) -> tuple:
        window = windows[index]
        by_partition: dict[int, list[tuple[int, int]]] = {}
        for position, (_, partition, offset) in enumerate(window):
            by_partition.setdefault(partition, []).append((position, offset))
        values: dict[int, UntargetedCandidateExclusion] = {}
        for partition, selected in by_partition.items():
            exclusions = (
                tuple(planning_exclusions[offset] for _, offset in selected)
                if partition == -1
                else store.read_selected(
                    partition,
                    "excluded_candidates",
                    tuple(offset for _, offset in selected),
                )
            )
            for (position, _), exclusion in zip(selected, exclusions, strict=True):
                values[position] = exclusion
        return tuple(values[position] for position in range(len(window)))

    return ImmutableSequence(tuple(map(len, windows)), load_window)
