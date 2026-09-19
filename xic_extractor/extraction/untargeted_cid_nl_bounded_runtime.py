"""Complete CID-NL subjects in finite partitions under one runtime attempt."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields
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
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    UntargetedCidNlPersistedPartition,
    UntargetedCidNlProductionBatch,
    UntargetedCidNlProductionPlan,
    compose_and_persist_untargeted_cid_nl_partition,
    materialize_untargeted_cid_nl_partition,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRuntimePartitionResult,
    execute_untargeted_cid_nl_runtime_partition,
)
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
    cid_nl_alignment_subject_worksets,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import resolve_request_acquisition_source

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
        raw_workers: int = 1,
        raw_worker_backend: str = "thread",
    ) -> None:
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

        context = plan.context
        self._owners = context.processing_owners
        subjects_per_partition = max(1, max_partition_jobs // len(context.samples))
        subject_partitions_list: list[tuple[CidNlMethodSubject, ...]] = []
        current_subjects: list[CidNlMethodSubject] = []
        for workset in cid_nl_alignment_subject_worksets(plan.runtime_subjects):
            if len(workset) > subjects_per_partition:
                raise ValueError(
                    "max_partition_jobs cannot contain one alignment subject workset"
                )
            if current_subjects and (
                len(current_subjects) + len(workset) > subjects_per_partition
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
            current_subjects.extend(workset)
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
        self._partitions = tuple(
            WorkPartition(
                index=index,
                partition_key=f"partition-{index:05d}",
                payload=subjects,
            )
            for index, subjects in enumerate(subject_partitions)
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
        self._memberships_by_subject: dict[str, list[tuple[str, str]]] = {}
        for membership in plan.subject_batch.candidate_memberships:
            self._memberships_by_subject.setdefault(membership[1], []).append(
                membership
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
        subjects = partition.payload
        subject_batch = UntargetedCidNlMethodSubjectBatch(
            subjects=subjects,
            candidate_memberships=tuple(
                sorted(
                    membership
                    for subject in subjects
                    for membership in self._memberships_by_subject[
                        subject.method_subject_id
                    ]
                )
            ),
        )
        batch = materialize_untargeted_cid_nl_partition(self._plan, subject_batch)
        with WorkerAdmissionPool(
            max_workers=self._raw_workers,
            backend=self._raw_worker_backend,
            thread_name_prefix="cid-nl-sample",
        ) as pool:
            leaf = execute_untargeted_cid_nl_runtime_partition(
                batch.jobs,
                partition_key=partition.partition_key,
                alignment_config=self._alignment_config,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
                raw_workers=self._raw_workers,
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
            result=(batch, leaf),
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
        bindings: list[CaptureCandidateBinding] = []
        exclusion_positions: list[tuple[str, int, int]] = []
        for index, comp_out in enumerate(composition_outputs):
            persisted = comp_out.result
            bindings.extend(persisted.capture_bindings)
            exclusion_positions.extend(
                (exclusion.candidate_id, index, offset)
                for offset, exclusion in enumerate(persisted.excluded_candidates)
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
            capture_bindings=tuple(
                sorted(bindings, key=lambda binding: binding.candidate_id)
            ),
        )
        return cast(
            FinalizedUntargetBatchApplicationResult,
            finalize_alignment_result(
                UntargetBatchApplicationResultRequest(
                    scope=scope,
                    processing_request=self._owners.request,
                    processing_attempt=self._owners.attempt,
                    children=store.sequence("children"),
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
            cleanup_action=store.cleanup,
        )


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
    raw_workers: int = 1,
    raw_worker_backend: str = "thread",
    should_cancel: Callable[[], bool] | None = None,
    retain_partition_memory: bool = False,
    pipeline_overlap: bool = False,
) -> CompletedUntargetBatchWorkflowResult:
    """Spill complete subject results, then close the exact global population.

    A partition contains every sample for its subjects, preserving grouping and
    recovery decisions. Operational work units describe partition/sample phases;
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

    adapter = CidNlPartitionPipelineAdapter(
        plan,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
        max_partition_jobs=max_partition_jobs,
        raw_workers=raw_workers,
        raw_worker_backend=raw_worker_backend,
    )
    return PartitionPipelineScheduler.execute(
        adapter,
        output_dir=output_dir,
        telemetry_provider=telemetry_provider,
        recovery_marker=recovery_marker,
        progress_callback=progress_callback,
        should_cancel=should_cancel,
        retain_partition_memory=retain_partition_memory,
        pipeline_overlap=pipeline_overlap,
        execute_runtime_attempt=execute_runtime_attempt,
        store_factory=PartitionResultStore,
    )


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
