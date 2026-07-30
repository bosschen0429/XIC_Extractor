"""Executable Package 0 parity and frozen successor-comparison receipt checks."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import secrets
import stat
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import InitVar, dataclass, replace
from dataclasses import field as dataclass_field
from multiprocessing import get_context
from pathlib import Path, PurePosixPath
from typing import NoReturn, final

from xic_extractor.acquisition_contract import (
    TraceCoordinateKind,
    mint_trace_acquisition_capability,
)
from xic_extractor.alignment.runtime_adapter import (
    AlignmentRuntimeApplicationResult,
    AlignmentRuntimeProcessResult,
    execute_alignment_process_runtime,
    mint_alignment_process_result_artifact,
)
from xic_extractor.execution_runtime import (
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    canonical_coordinator_terminal_record_bytes,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeAttemptAggregate,
    RuntimeTelemetryProvider,
    mint_work_unit_settlement,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedProcessResourceReport,
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeLifecycleApplicationResult,
    TargetedRuntimeWorkerBatch,
    mint_targeted_process_result_ref,
)
from xic_extractor.extraction.targeted_service import (
    TargetedApplicationRequest,
    TargetedApplicationService,
)
from xic_extractor.peak_detection.observation_batch import (
    PeakObservationBatch,
    PeakObservationPlan,
    canonical_peak_observation_batch_bytes,
    produce_peak_observation_batch,
)
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.xic_models import XICRequest, XICTrace


class Package0ComparatorError(ValueError):
    """Raised when an adapter or successor receipt differs from the frozen gate."""


_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_PACKAGE1_DIFFERENCE_SCHEMA = "package1_runtime_observation_difference_v1"
_FROZEN_PACKAGE1_SURFACE_SHA256 = (
    "70099C0412D9301736CA479F05BEBF1D7F928C248407ED062D1EFBBA0D045B6B"
)
_PACKAGE1_OBSERVATION_MINT_TOKEN = object()
_PACKAGE1_PROBE_MINT_TOKEN = object()
_PACKAGE1_EXPERIMENT_DIR = "package1-runtime-observation-experiment"
_PACKAGE1_PRODUCT_OUTPUT_DIR = "product-output"
_PACKAGE1_ROOT_IDENTITY_MARKER = ".package1-root-identity"
_PACKAGE1_DIFFERENCE_FIELDS = (
    "schema_version",
    "stage",
    "stable_subject_or_cell",
    "old_value",
    "new_value",
    "reason",
    "preserved_invariant",
    "product_impact",
    "classification",
    "disposition",
)


@final
@dataclass(frozen=True, slots=True)
class Package1ObservationTraceFixture:
    """Exact no-RAW trace input for one Package 1 experiment request."""

    request: XICRequest
    rt: tuple[float, ...]
    intensity: tuple[float, ...]
    first_scan: int
    last_scan: int

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("Package1ObservationTraceFixture is final")

    def __post_init__(self) -> None:
        if not isinstance(self.request, XICRequest):
            raise Package0ComparatorError("trace fixture request must be an XICRequest")
        if not isinstance(self.rt, tuple) or not isinstance(self.intensity, tuple):
            raise Package0ComparatorError(
                "trace fixture rt and intensity must be exact tuples"
            )
        try:
            XICTrace.from_arrays(self.rt, self.intensity)
        except (TypeError, ValueError) as exc:
            raise Package0ComparatorError(
                "trace fixture must contain one valid finite XIC trace"
            ) from exc
        if (
            isinstance(self.first_scan, bool)
            or not isinstance(self.first_scan, int)
            or isinstance(self.last_scan, bool)
            or not isinstance(self.last_scan, int)
            or self.first_scan < 1
            or self.last_scan < self.first_scan
        ):
            raise Package0ComparatorError(
                "trace fixture scan window must be ordered positive integers"
            )


@final
@dataclass(frozen=True, slots=True)
class Package1ObservationExperimentJob:
    """Primitive, pickleable input to the fixed Package 1 experiment workers."""

    owners: ProcessingOwnerReferences
    sample: ResolvedCanonicalSampleIdentity
    work_unit_key: str
    resource_key: str
    coordinate_kind: TraceCoordinateKind
    plans: tuple[PeakObservationPlan, ...]
    trace_fixtures: tuple[Package1ObservationTraceFixture, ...]
    delay_sec: float = 0.0

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("Package1ObservationExperimentJob is final")

    def __post_init__(self) -> None:
        if not isinstance(self.owners, ProcessingOwnerReferences):
            raise Package0ComparatorError(
                "experiment job owners must be ProcessingOwnerReferences"
            )
        if not isinstance(self.sample, ResolvedCanonicalSampleIdentity):
            raise Package0ComparatorError(
                "experiment job sample must be a resolved canonical identity"
            )
        if not isinstance(self.work_unit_key, str) or not self.work_unit_key:
            raise Package0ComparatorError(
                "experiment job work_unit_key must be non-empty"
            )
        if not isinstance(self.resource_key, str) or not self.resource_key:
            raise Package0ComparatorError(
                "experiment job resource_key must be non-empty"
            )
        if not isinstance(self.coordinate_kind, TraceCoordinateKind):
            raise Package0ComparatorError(
                "experiment job coordinate_kind must be a TraceCoordinateKind"
            )
        if not isinstance(self.plans, tuple) or not self.plans or any(
            not isinstance(plan, PeakObservationPlan) for plan in self.plans
        ):
            raise Package0ComparatorError(
                "experiment job plans must be a non-empty PeakObservationPlan tuple"
            )
        if not isinstance(self.trace_fixtures, tuple) or any(
            type(fixture) is not Package1ObservationTraceFixture
            for fixture in self.trace_fixtures
        ):
            raise Package0ComparatorError(
                "experiment job trace_fixtures must be an exact fixture tuple"
            )
        if tuple(plan.request for plan in self.plans) != tuple(
            fixture.request for fixture in self.trace_fixtures
        ):
            raise Package0ComparatorError(
                "experiment trace fixtures must match plan requests in order"
            )
        if (
            isinstance(self.delay_sec, bool)
            or not isinstance(self.delay_sec, (int, float))
            or not math.isfinite(float(self.delay_sec))
            or self.delay_sec < 0
        ):
            raise Package0ComparatorError(
                "experiment job delay_sec must be finite and non-negative"
            )


@dataclass(frozen=True, slots=True)
class _Package1NoRawBatchAdapter:
    fixtures: tuple[Package1ObservationTraceFixture, ...]

    @property
    def raw_chromatogram_call_count(self) -> int:
        return 0

    def extract_xic_many(
        self,
        requests: Sequence[XICRequest],
    ) -> tuple[XICTrace, ...]:
        if tuple(requests) != tuple(fixture.request for fixture in self.fixtures):
            raise Package0ComparatorError(
                "experiment adapter requests must match the fixed trace fixtures"
            )
        return tuple(
            XICTrace.from_arrays(fixture.rt, fixture.intensity)
            for fixture in self.fixtures
        )

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        for fixture in self.fixtures:
            if fixture.request == request:
                return fixture.first_scan, fixture.last_scan
        raise Package0ComparatorError(
            "experiment adapter request is absent from the fixed trace fixtures"
        )


@dataclass(frozen=True, slots=True)
class _TargetedAdapterParityReceipt:
    adapter_name: str
    processing_request_id: str
    attempt_semantics_sha256: str
    lineage_sha256: str
    scientific_results_sha256: str
    delivery_projection_sha256: str
    baseline_stage_xic_results_sha256: str
    final_stage_xic_results_sha256: str

    def __post_init__(self) -> None:
        if self.adapter_name not in {"cli", "gui"}:
            raise Package0ComparatorError("adapter_name must be cli or gui")
        if not self.processing_request_id.startswith("xic.processing-request.v1:"):
            raise Package0ComparatorError(
                "processing_request_id must be owner-minted"
            )
        for field in (
            "attempt_semantics_sha256",
            "lineage_sha256",
            "scientific_results_sha256",
            "delivery_projection_sha256",
            "baseline_stage_xic_results_sha256",
            "final_stage_xic_results_sha256",
        ):
            _sha256(getattr(self, field), field)


@dataclass(frozen=True, slots=True)
class _Package0SuccessorObservation:
    baseline_id: str
    canonical_output_inventory_sha256: str
    canonical_artifact_path_sha256: str
    ordered_sample_sha256: str
    ordered_final_header_sha256: str
    baseline_stage_xic_results_sha256: str
    final_stage_xic_results_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.baseline_id, str) or not self.baseline_id.strip():
            raise Package0ComparatorError("baseline_id must be non-empty")
        for field in (
            "canonical_output_inventory_sha256",
            "canonical_artifact_path_sha256",
            "ordered_sample_sha256",
            "ordered_final_header_sha256",
            "baseline_stage_xic_results_sha256",
            "final_stage_xic_results_sha256",
        ):
            _sha256(getattr(self, field), field)


@final
@dataclass(frozen=True, slots=True)
class Package1DifferenceObservation:
    """Owner-derived final-state and runner receipt for one Package 1 run."""

    frozen_surface_sha256: str
    processing_request_id: str
    processing_attempt_id: str
    targeted_coordinator_sha256: str
    alignment_coordinator_sha256: str
    targeted_observation_batches_sha256: str
    alignment_observation_batches_sha256: str
    observation_batch_count: int
    targeted_terminal_dispositions_sha256: str
    alignment_terminal_dispositions_sha256: str
    targeted_offered_artifact_count: int
    alignment_offered_artifact_count: int
    terminal_non_offered_count: int
    targeted_legacy_runner_call_count: int
    successor_product_output_paths: tuple[str, ...]
    validation_status: str
    _mint_token: InitVar[object] = dataclass_field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("Package1DifferenceObservation is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _PACKAGE1_OBSERVATION_MINT_TOKEN:
            raise Package0ComparatorError(
                "Package1DifferenceObservation must be minted by the comparator"
            )
        for field in (
            "frozen_surface_sha256",
            "targeted_coordinator_sha256",
            "alignment_coordinator_sha256",
            "targeted_observation_batches_sha256",
            "alignment_observation_batches_sha256",
            "targeted_terminal_dispositions_sha256",
            "alignment_terminal_dispositions_sha256",
        ):
            _sha256(getattr(self, field), field)
        if not self.processing_request_id.startswith("xic.processing-request.v1:"):
            raise Package0ComparatorError(
                "Package 1 observation must bind an owner request"
            )
        if not self.processing_attempt_id.startswith("xic.processing-attempt.v1:"):
            raise Package0ComparatorError(
                "Package 1 observation must bind an owner attempt"
            )
        for field in (
            "observation_batch_count",
            "targeted_offered_artifact_count",
            "alignment_offered_artifact_count",
            "terminal_non_offered_count",
            "targeted_legacy_runner_call_count",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise Package0ComparatorError(f"{field} must be non-negative")
        if not isinstance(self.successor_product_output_paths, tuple) or any(
            not isinstance(path, str) or not path
            for path in self.successor_product_output_paths
        ):
            raise Package0ComparatorError(
                "successor_product_output_paths must be a text tuple"
            )


@final
class _Package1ProductOutputProbe:
    """Comparator-owned final-state parity window for product outputs."""

    __slots__ = (
        "_before_inventory",
        "_consumed",
        "_legacy_runner_call_count",
        "_output_root",
        "_root_identity",
    )

    def __init__(
        self,
        *,
        output_root: Path,
        before_inventory: tuple[tuple[str, str], ...],
        root_identity: tuple[int, int, int, int, int, str],
        _mint_token: object,
    ) -> None:
        if _mint_token is not _PACKAGE1_PROBE_MINT_TOKEN:
            raise Package0ComparatorError(
                "Package 1 product-output probe must be minted by the comparator"
            )
        self._output_root = output_root
        self._before_inventory = before_inventory
        self._root_identity = root_identity
        self._legacy_runner_call_count = 0
        self._consumed = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("Package 1 product-output probe is final")

    def _forbidden_legacy_runner(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> NoReturn:
        """Fail immediately if the Package 1 path enters the legacy runner."""

        self._legacy_runner_call_count += 1
        raise Package0ComparatorError(
            "Package 1 observation path invoked the forbidden legacy runner"
        )

    def _finish(self) -> tuple[int, tuple[str, ...]]:
        if self._consumed:
            raise Package0ComparatorError(
                "Package 1 product-output probe may finalize exactly one observation"
            )
        self._consumed = True
        if _package1_output_root_identity(self._output_root) != self._root_identity:
            raise Package0ComparatorError(
                "Package 1 comparator-owned output root identity changed"
            )
        after_inventory = _package1_product_output_inventory(self._output_root)
        before_by_path = dict(self._before_inventory)
        after_by_path = dict(after_inventory)
        changed_paths = tuple(
            sorted(
                path
                for path in before_by_path.keys() | after_by_path.keys()
                if before_by_path.get(path) != after_by_path.get(path)
            )
        )
        try:
            (self._output_root / _PACKAGE1_ROOT_IDENTITY_MARKER).unlink()
        except OSError as exc:
            raise Package0ComparatorError(
                "Package 1 comparator-owned output root identity marker changed"
            ) from exc
        return self._legacy_runner_call_count, changed_paths


def run_package1_observation_experiment(
    *,
    experiment_workspace: Path,
    targeted_request: TargetedApplicationRequest,
    runtime: AttemptRuntime,
    resource_reservations: Sequence[RuntimeResourceReservation],
    telemetry_provider: RuntimeTelemetryProvider,
    jobs: tuple[Package1ObservationExperimentJob, ...],
    max_workers: int,
) -> Package1DifferenceObservation:
    """Run fixed Package 1 consumers inside one comparator-owned parity window."""

    if not isinstance(experiment_workspace, Path) or not experiment_workspace.is_dir():
        raise Package0ComparatorError(
            "experiment_workspace must be an existing directory"
        )
    if not isinstance(targeted_request, TargetedApplicationRequest):
        raise Package0ComparatorError(
            "targeted_request must be a TargetedApplicationRequest"
        )
    _validate_package1_experiment_jobs(
        jobs,
        runtime=runtime,
        resource_reservations=resource_reservations,
        max_workers=max_workers,
    )
    experiment_root = experiment_workspace / _PACKAGE1_EXPERIMENT_DIR
    try:
        experiment_root.mkdir()
    except FileExistsError as exc:
        raise Package0ComparatorError(
            "Package 1 experiment root must not already exist"
        ) from exc
    product_output_root = experiment_root / _PACKAGE1_PRODUCT_OUTPUT_DIR
    product_output_root.mkdir()
    (product_output_root / _PACKAGE1_ROOT_IDENTITY_MARKER).write_bytes(
        secrets.token_bytes(32)
    )
    probe = _Package1ProductOutputProbe(
        output_root=product_output_root,
        before_inventory=_package1_product_output_inventory(product_output_root),
        root_identity=_package1_output_root_identity(product_output_root),
        _mint_token=_PACKAGE1_PROBE_MINT_TOKEN,
    )
    bound_targeted_request = _bind_package1_targeted_output_root(
        targeted_request,
        product_output_root=product_output_root,
    )

    def run_fixed_targeted_jobs(
        request: TargetedApplicationRequest,
        aggregate: RuntimeAttemptAggregate,
    ) -> TargetedRuntimeWorkerBatch:
        del request, aggregate
        return _execute_package1_targeted_jobs(jobs, max_workers=max_workers)

    targeted_service = TargetedApplicationService(
        probe._forbidden_legacy_runner,
        runtime_lifecycle_runner=run_fixed_targeted_jobs,
        runtime_recovery_marker=OwnerPathRuntimeMarker(
            experiment_root / "targeted.runtime.json",
            "package1-targeted-runtime-observation:v1",
        ),
    )
    targeted = targeted_service.execute_runtime_lifecycle(
        bound_targeted_request,
        runtime,
        resource_reservations=resource_reservations,
        telemetry_provider=telemetry_provider,
    )
    alignment = execute_alignment_process_runtime(
        runtime,
        jobs,
        worker=_run_package1_alignment_job,
        error_factory=_package1_alignment_error,
        max_workers=max_workers,
        resource_reservations=resource_reservations,
        telemetry_provider=telemetry_provider,
        recovery_marker=OwnerPathRuntimeMarker(
            experiment_root / "alignment.runtime.json",
            "package1-alignment-runtime-observation:v1",
        ),
    )
    return _mint_package1_difference_observation(
        probe=probe,
        targeted=targeted,
        alignment=alignment,
    )


def _validate_package1_experiment_jobs(
    jobs: object,
    *,
    runtime: AttemptRuntime,
    resource_reservations: Sequence[RuntimeResourceReservation],
    max_workers: int,
) -> None:
    if not isinstance(runtime, AttemptRuntime):
        raise Package0ComparatorError("runtime must be an AttemptRuntime")
    if not isinstance(jobs, tuple) or not jobs or any(
        type(job) is not Package1ObservationExperimentJob for job in jobs
    ):
        raise Package0ComparatorError(
            "jobs must be a non-empty Package1ObservationExperimentJob tuple"
        )
    if (
        isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        raise Package0ComparatorError("max_workers must be a positive integer")
    if tuple(job.work_unit_key for job in jobs) != runtime.manifest.work_unit_keys:
        raise Package0ComparatorError(
            "experiment jobs must match runtime manifest work in order"
        )
    if any(job.owners.attempt != runtime.manifest.attempt for job in jobs):
        raise Package0ComparatorError(
            "experiment jobs must bind the runtime processing attempt"
        )
    reservations = tuple(resource_reservations)
    if any(
        not isinstance(reservation, RuntimeResourceReservation)
        for reservation in reservations
    ):
        raise Package0ComparatorError(
            "resource_reservations must contain RuntimeResourceReservation values"
        )
    if tuple(reservation.resource_key for reservation in reservations) != tuple(
        job.resource_key for job in jobs
    ):
        raise Package0ComparatorError(
            "experiment resource reservations must match jobs in order"
        )


def _bind_package1_targeted_output_root(
    request: TargetedApplicationRequest,
    *,
    product_output_root: Path,
) -> TargetedApplicationRequest:
    output_csv = product_output_root / "xic_results.csv"
    diagnostics_csv = product_output_root / "xic_diagnostics.csv"
    bound = replace(
        request,
        config=replace(
            request.config,
            output_csv=output_csv,
            diagnostics_csv=diagnostics_csv,
        ),
    )
    if (
        bound.config.output_csv != output_csv
        or bound.config.diagnostics_csv != diagnostics_csv
    ):
        raise Package0ComparatorError(
            "Targeted product outputs must use the comparator-owned output root"
        )
    return bound


def _execute_package1_targeted_jobs(
    jobs: tuple[Package1ObservationExperimentJob, ...],
    *,
    max_workers: int,
) -> TargetedRuntimeWorkerBatch:
    worker_count = min(max_workers, len(jobs))
    with ProcessPoolExecutor(
        max_workers=worker_count,
        mp_context=get_context("spawn"),
    ) as executor:
        futures = tuple(
            executor.submit(_run_package1_targeted_job, job) for job in jobs
        )
        results = tuple(future.result() for future in futures)
    envelopes = tuple(envelope for envelope, _report in results)
    return TargetedRuntimeWorkerBatch(
        result_envelopes=envelopes,
        resource_reports=tuple(report for _envelope, report in results),
        runtime_artifact_refs=tuple(
            envelope.owner_result_ref
            for envelope in envelopes
            if envelope.owner_result_ref is not None
        ),
    )


def _run_package1_targeted_job(
    job: Package1ObservationExperimentJob,
) -> tuple[TargetedProcessWorkerResultEnvelope, TargetedProcessResourceReport]:
    batch = _produce_package1_observation_batch(job)
    payload = canonical_peak_observation_batch_bytes(batch)
    reference = mint_targeted_process_result_ref(
        processing_attempt=job.owners.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
        upstream_artifacts=batch.observation_artifacts,
    )
    return (
        TargetedProcessWorkerResultEnvelope(
            attempt=job.owners.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=reference,
            peak_observation_batch=batch,
        ),
        TargetedProcessResourceReport(
            attempt=job.owners.attempt,
            resource_key=job.resource_key,
            open_outcome=RuntimeResourceOpenOutcome.OPENED,
            cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
        ),
    )


def _run_package1_alignment_job(
    job: Package1ObservationExperimentJob,
) -> AlignmentRuntimeProcessResult:
    batch = _produce_package1_observation_batch(job)
    payload = canonical_peak_observation_batch_bytes(batch)
    artifact = mint_alignment_process_result_artifact(
        processing_attempt=job.owners.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
        upstream_artifacts=batch.observation_artifacts,
    )
    return AlignmentRuntimeProcessResult(
        settlement=mint_work_unit_settlement(
            attempt=job.owners.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=artifact,
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
        runtime_artifacts=(artifact, *batch.observation_artifacts),
        peak_observation_batch=batch,
    )


def _package1_alignment_error(
    job: Package1ObservationExperimentJob,
    error: Exception,
) -> AlignmentRuntimeProcessResult:
    return AlignmentRuntimeProcessResult(
        settlement=mint_work_unit_settlement(
            attempt=job.owners.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.FAILED,
            failure_detail=WorkUnitFailureDetail(
                category=WorkUnitFailureCategory.EXECUTION_ERROR,
                stage="package1-alignment-process-worker",
                source_reference=f"package1-no-raw:{job.work_unit_key}",
                message=str(error),
            ),
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )


def _produce_package1_observation_batch(
    job: Package1ObservationExperimentJob,
) -> PeakObservationBatch:
    if job.delay_sec:
        time.sleep(job.delay_sec)
    source = resolve_request_acquisition_source(
        processing_owners=job.owners,
        sample=job.sample,
    )
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="synthetic.package1.no_raw",
        coordinate_kind=job.coordinate_kind,
    )
    return produce_peak_observation_batch(
        processing_owners=job.owners,
        sample=job.sample,
        capability=capability,
        plans=job.plans,
        adapter=_Package1NoRawBatchAdapter(job.trace_fixtures),
    )


def _mint_package1_difference_observation(
    *,
    probe: _Package1ProductOutputProbe,
    targeted: TargetedRuntimeLifecycleApplicationResult,
    alignment: AlignmentRuntimeApplicationResult,
) -> Package1DifferenceObservation:
    """Derive one receipt from the comparator-owned experiment results."""

    targeted_attempt = targeted.settlement.runtime.manifest.attempt
    alignment_attempt = alignment.runtime.manifest.attempt
    if targeted_attempt != alignment_attempt:
        raise Package0ComparatorError(
            "Targeted and Alignment results must bind one request and attempt"
        )
    if (
        targeted.coordinator_record.proposed_outcome
        is not AttemptTerminalOutcome.SUCCEEDED
        or alignment.coordinator_record.proposed_outcome
        is not AttemptTerminalOutcome.SUCCEEDED
        or targeted.finalization_record.terminal_outcome
        is not AttemptTerminalOutcome.SUCCEEDED
        or alignment.finalization_record.terminal_outcome
        is not AttemptTerminalOutcome.SUCCEEDED
    ):
        raise Package0ComparatorError(
            "Package 1 difference receipt requires successful terminal results"
        )
    targeted_batches = tuple(
        (key, canonical_peak_observation_batch_bytes(batch))
        for key, batch in targeted.settlement.successful_observation_batches
    )
    alignment_batches = tuple(
        (key, canonical_peak_observation_batch_bytes(batch))
        for key, batch in alignment.successful_observation_batches
    )
    if not targeted_batches or targeted_batches != alignment_batches:
        raise Package0ComparatorError(
            "Targeted and Alignment observation batch receipts must preserve work keys "
            "and be canonical-equal"
        )
    targeted_dispositions = targeted.finalization_record.artifact_dispositions
    alignment_dispositions = alignment.finalization_record.artifact_dispositions
    expected_observation_ids = {
        artifact.evidence_artifact_id
        for _key, batch in targeted.settlement.successful_observation_batches
        for artifact in batch.observation_artifacts
    }
    if not expected_observation_ids.issubset(
        {artifact_id for artifact_id, _disposition in targeted_dispositions}
    ) or not expected_observation_ids.issubset(
        {artifact_id for artifact_id, _disposition in alignment_dispositions}
    ):
        raise Package0ComparatorError(
            "terminal results must disposition every observation artifact"
        )
    non_offered = sum(
        disposition is not RuntimeArtifactDisposition.OFFERED_TO_OWNER
        for dispositions in (targeted_dispositions, alignment_dispositions)
        for _artifact_id, disposition in dispositions
    )
    targeted_coordinator_bytes = canonical_coordinator_terminal_record_bytes(
        targeted.coordinator_record
    )
    alignment_coordinator_bytes = canonical_coordinator_terminal_record_bytes(
        alignment.coordinator_record
    )
    legacy_runner_call_count, product_output_paths = probe._finish()
    if legacy_runner_call_count != 0:
        raise Package0ComparatorError(
            "Package 1 observation path invoked the forbidden legacy runner"
        )
    if product_output_paths:
        raise Package0ComparatorError(
            "Package 1 observation path changed product output files: "
            + ", ".join(product_output_paths)
        )
    return Package1DifferenceObservation(
        frozen_surface_sha256=_FROZEN_PACKAGE1_SURFACE_SHA256,
        processing_request_id=targeted_attempt.processing_request_id,
        processing_attempt_id=targeted_attempt.processing_attempt_id,
        targeted_coordinator_sha256=hashlib.sha256(
            targeted_coordinator_bytes
        ).hexdigest().upper(),
        alignment_coordinator_sha256=hashlib.sha256(
            alignment_coordinator_bytes
        ).hexdigest().upper(),
        targeted_observation_batches_sha256=_canonical_sha256(
            {
                "items": [
                    {
                        "work_unit_key": key,
                        "sha256": hashlib.sha256(item).hexdigest(),
                    }
                    for key, item in targeted_batches
                ]
            }
        ),
        alignment_observation_batches_sha256=_canonical_sha256(
            {
                "items": [
                    {
                        "work_unit_key": key,
                        "sha256": hashlib.sha256(item).hexdigest(),
                    }
                    for key, item in alignment_batches
                ]
            }
        ),
        observation_batch_count=len(targeted_batches),
        targeted_terminal_dispositions_sha256=_canonical_sha256(
            {
                "items": [
                    (artifact_id, disposition.value)
                    for artifact_id, disposition in targeted_dispositions
                ]
            }
        ),
        alignment_terminal_dispositions_sha256=_canonical_sha256(
            {
                "items": [
                    (artifact_id, disposition.value)
                    for artifact_id, disposition in alignment_dispositions
                ]
            }
        ),
        targeted_offered_artifact_count=sum(
            disposition is RuntimeArtifactDisposition.OFFERED_TO_OWNER
            for _artifact_id, disposition in targeted_dispositions
        ),
        alignment_offered_artifact_count=sum(
            disposition is RuntimeArtifactDisposition.OFFERED_TO_OWNER
            for _artifact_id, disposition in alignment_dispositions
        ),
        terminal_non_offered_count=non_offered,
        targeted_legacy_runner_call_count=legacy_runner_call_count,
        successor_product_output_paths=product_output_paths,
        validation_status="diagnostic_only",
        _mint_token=_PACKAGE1_OBSERVATION_MINT_TOKEN,
    )


def mint_targeted_adapter_parity_receipt(
    *,
    adapter_name: str,
    processing_request: ProcessingRequestIdentity,
    attempt_semantics: bytes,
    lineage: bytes,
    scientific_results: bytes,
    delivery_projection: bytes,
    baseline_stage_xic_results: Path,
    final_stage_xic_results: Path,
) -> _TargetedAdapterParityReceipt:
    """Mint one parity receipt only from owner identity and actual bytes/files."""

    if not isinstance(processing_request, ProcessingRequestIdentity):
        raise Package0ComparatorError(
            "processing_request must be an owner-minted ProcessingRequestIdentity"
        )
    return _TargetedAdapterParityReceipt(
        adapter_name=adapter_name,
        processing_request_id=processing_request.processing_request_id,
        attempt_semantics_sha256=_bytes_sha256(
            attempt_semantics, field="attempt_semantics"
        ),
        lineage_sha256=_bytes_sha256(lineage, field="lineage"),
        scientific_results_sha256=_bytes_sha256(
            scientific_results, field="scientific_results"
        ),
        delivery_projection_sha256=_bytes_sha256(
            delivery_projection, field="delivery_projection"
        ),
        baseline_stage_xic_results_sha256=_file_sha256(
            baseline_stage_xic_results,
            field="baseline_stage_xic_results",
        ),
        final_stage_xic_results_sha256=_file_sha256(
            final_stage_xic_results,
            field="final_stage_xic_results",
        ),
    )


def assert_targeted_adapter_parity(
    cli: _TargetedAdapterParityReceipt,
    gui: _TargetedAdapterParityReceipt,
) -> str:
    if not isinstance(cli, _TargetedAdapterParityReceipt) or cli.adapter_name != "cli":
        raise Package0ComparatorError("cli must be a CLI parity receipt")
    if not isinstance(gui, _TargetedAdapterParityReceipt) or gui.adapter_name != "gui":
        raise Package0ComparatorError("gui must be a GUI parity receipt")
    cli_basis = _adapter_basis(cli)
    gui_basis = _adapter_basis(gui)
    if cli_basis != gui_basis:
        differences = sorted(
            key for key in cli_basis if cli_basis[key] != gui_basis[key]
        )
        raise Package0ComparatorError(
            f"CLI/GUI adapter parity differs: {', '.join(differences)}"
        )
    return _canonical_sha256(cli_basis)


def validate_package0_successor_output(
    contract: Mapping[str, object],
    membership_receipt: Mapping[str, object],
    *,
    output_root: Path,
    cli: _TargetedAdapterParityReceipt,
    gui: _TargetedAdapterParityReceipt,
) -> str:
    """Derive and validate the frozen comparator from actual successor files."""

    observation = _observe_package0_successor_output(
        contract,
        membership_receipt,
        output_root=output_root,
    )
    return _validate_package0_successor_observation(
        contract,
        observation,
        cli=cli,
        gui=gui,
    )


def _validate_package0_successor_observation(
    contract: Mapping[str, object],
    observation: _Package0SuccessorObservation,
    *,
    cli: _TargetedAdapterParityReceipt,
    gui: _TargetedAdapterParityReceipt,
) -> str:
    if contract.get("authority") != {
        "product_authority_granted": False,
        "validation_status": "diagnostic_only",
    }:
        raise Package0ComparatorError(
            "Package 0 comparator must remain diagnostic_only"
        )
    delivery_scope = contract.get("delivery_scope")
    if not isinstance(delivery_scope, Mapping) or (
        delivery_scope.get("issue_149_completion") != "complete"
        or delivery_scope.get("deferred") != []
    ):
        raise Package0ComparatorError(
            "Package 0 comparator contract is not formally complete"
        )
    comparator = contract.get("legacy_comparator")
    if not isinstance(comparator, Mapping):
        raise Package0ComparatorError("legacy_comparator contract is missing")
    expected = {
        "baseline_id": contract.get("baseline_id"),
        "canonical_output_inventory_sha256": contract.get(
            "canonical_output_inventory_sha256"
        ),
        "canonical_artifact_path_sha256": comparator.get(
            "canonical_artifact_path_sha256"
        ),
        "ordered_sample_sha256": comparator.get("ordered_sample_sha256"),
        "ordered_final_header_sha256": comparator.get(
            "ordered_final_header_sha256"
        ),
        "baseline_stage_xic_results_sha256": comparator.get(
            "baseline_stage_xic_results_sha256"
        ),
        "final_stage_xic_results_sha256": comparator.get(
            "final_stage_xic_results_sha256"
        ),
    }
    observed = {
        field: getattr(observation, field)
        for field in expected
    }
    differences = sorted(
        field for field, value in expected.items() if observed[field] != value
    )
    if differences:
        raise Package0ComparatorError(
            f"successor differs from frozen comparator: {', '.join(differences)}"
        )
    adapter_digest = assert_targeted_adapter_parity(cli, gui)
    if (
        cli.baseline_stage_xic_results_sha256
        != observation.baseline_stage_xic_results_sha256
        or cli.final_stage_xic_results_sha256
        != observation.final_stage_xic_results_sha256
    ):
        raise Package0ComparatorError(
            "adapter receipts do not bind the frozen Targeted CSV digests"
        )
    return _canonical_sha256(
        {"observation": observed, "adapter_parity_sha256": adapter_digest}
    )


def validate_package1_difference_manifest(
    contract: Mapping[str, object],
    rows: Sequence[Mapping[str, str]],
    *,
    observation: Package1DifferenceObservation,
) -> str:
    """Validate Package 1's internal diff and frozen exact-zero comparator row."""

    if contract.get("authority") != {
        "product_authority_granted": False,
        "validation_status": "diagnostic_only",
    }:
        raise Package0ComparatorError(
            "Package 1 difference gate must reuse the diagnostic-only comparator"
        )
    frozen_digest = _package1_frozen_surface_digest(contract)
    if frozen_digest != _FROZEN_PACKAGE1_SURFACE_SHA256:
        raise Package0ComparatorError(
            "Package 1 comparator contract differs from the immutable baseline anchor"
        )
    if not isinstance(observation, Package1DifferenceObservation) or (
        observation.frozen_surface_sha256 != _FROZEN_PACKAGE1_SURFACE_SHA256
        or observation.targeted_observation_batches_sha256
        != observation.alignment_observation_batches_sha256
        or observation.observation_batch_count < 1
        or observation.targeted_offered_artifact_count < 1
        or observation.targeted_offered_artifact_count
        != observation.alignment_offered_artifact_count
        or observation.terminal_non_offered_count != 0
        or observation.targeted_legacy_runner_call_count != 0
        or observation.successor_product_output_paths
        or observation.validation_status != "diagnostic_only"
    ):
        raise Package0ComparatorError(
            "Package 1 run emitted or invoked a frozen product surface"
        )
    expected = (
        {
            "schema_version": _PACKAGE1_DIFFERENCE_SCHEMA,
            "stage": "package1_runtime_observation",
            "stable_subject_or_cell": (
                "targeted_alignment_process_result_carrier"
            ),
            "old_value": "owner_result_ref_without_observation_batch",
            "new_value": "owner_result_ref_with_immutable_observation_batch",
            "reason": "compose_existing_acquisition_and_peak_observation_owners",
            "preserved_invariant": (
                "legacy_product_science_matrix_writer_authority_and_bytes_unchanged"
            ),
            "product_impact": "none",
            "classification": "explained_internal_runtime_difference",
            "disposition": "accept_diagnostic_only",
        },
        {
            "schema_version": _PACKAGE1_DIFFERENCE_SCHEMA,
            "stage": "package1_runtime_observation",
            "stable_subject_or_cell": (
                "frozen_legacy_product_scientific_matrix_writer"
            ),
            "old_value": f"sha256:{_FROZEN_PACKAGE1_SURFACE_SHA256}",
            "new_value": f"sha256:{_FROZEN_PACKAGE1_SURFACE_SHA256}",
            "reason": "package1_path_does_not_invoke_legacy_runner_or_writer",
            "preserved_invariant": (
                "frozen_package0_inventory_membership_and_csv_bytes_exact"
            ),
            "product_impact": "none",
            "classification": (
                "exact_zero_product_scientific_matrix_writer"
            ),
            "disposition": "pass",
        },
    )
    normalized = tuple(
        {field: row.get(field, "") for field in _PACKAGE1_DIFFERENCE_FIELDS}
        for row in rows
    )
    if any(set(row) != set(_PACKAGE1_DIFFERENCE_FIELDS) for row in rows):
        raise Package0ComparatorError(
            "Package 1 DifferenceManifest fields do not match the reviewed schema"
        )
    if normalized != expected:
        raise Package0ComparatorError(
            "Package 1 DifferenceManifest differs from the frozen exact-zero gate"
        )
    return _canonical_sha256(
        {
            "schema_version": _PACKAGE1_DIFFERENCE_SCHEMA,
            "observation": {
                "frozen_surface_sha256": observation.frozen_surface_sha256,
                "processing_request_id": observation.processing_request_id,
                "processing_attempt_id": observation.processing_attempt_id,
                "targeted_coordinator_sha256": (
                    observation.targeted_coordinator_sha256
                ),
                "alignment_coordinator_sha256": (
                    observation.alignment_coordinator_sha256
                ),
                "targeted_observation_batches_sha256": (
                    observation.targeted_observation_batches_sha256
                ),
                "alignment_observation_batches_sha256": (
                    observation.alignment_observation_batches_sha256
                ),
                "observation_batch_count": observation.observation_batch_count,
                "targeted_terminal_dispositions_sha256": (
                    observation.targeted_terminal_dispositions_sha256
                ),
                "alignment_terminal_dispositions_sha256": (
                    observation.alignment_terminal_dispositions_sha256
                ),
                "targeted_offered_artifact_count": (
                    observation.targeted_offered_artifact_count
                ),
                "alignment_offered_artifact_count": (
                    observation.alignment_offered_artifact_count
                ),
                "terminal_non_offered_count": observation.terminal_non_offered_count,
                "targeted_legacy_runner_call_count": (
                    observation.targeted_legacy_runner_call_count
                ),
                "successor_product_output_paths": (
                    observation.successor_product_output_paths
                ),
                "validation_status": observation.validation_status,
            },
            "rows": normalized,
        }
    )


def _package1_frozen_surface_digest(contract: Mapping[str, object]) -> str:
    comparator = contract.get("legacy_comparator")
    if not isinstance(comparator, Mapping):
        raise Package0ComparatorError("legacy_comparator contract is missing")
    fields = (
        "baseline_stage_xic_results_sha256",
        "final_stage_xic_results_sha256",
        "ordered_sample_sha256",
        "ordered_final_header_sha256",
        "sample_row_count_per_stage",
        "column_count_per_stage",
        "sample_identity_cell_count_per_stage",
        "analytical_cell_count_per_stage",
        "target_count",
        "fields_per_target",
        "required_exact_globs",
    )
    basis = {
        "canonical_output_inventory_sha256": contract.get(
            "canonical_output_inventory_sha256"
        ),
        **{field: comparator.get(field) for field in fields},
    }
    if any(value is None for value in basis.values()):
        raise Package0ComparatorError(
            "frozen comparator lacks Package 1 exact-zero surface membership"
        )
    return _canonical_sha256(basis)


def _observe_package0_successor_output(
    contract: Mapping[str, object],
    membership_receipt: Mapping[str, object],
    *,
    output_root: Path,
) -> _Package0SuccessorObservation:
    if not isinstance(membership_receipt, Mapping) or (
        membership_receipt.get("schema_version")
        != "package0_legacy_membership_receipt_v1"
    ):
        raise Package0ComparatorError("membership receipt schema is invalid")
    paths = _membership_text_sequence(
        membership_receipt,
        "canonical_artifact_paths",
    )
    samples = _membership_text_sequence(
        membership_receipt,
        "ordered_sample_identities",
    )
    header = _membership_text_sequence(
        membership_receipt,
        "ordered_final_header",
    )
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise Package0ComparatorError(
            "canonical_artifact_paths must be unique and ordered"
        )
    normalized_paths = tuple(_normalized_relative_path(path) for path in paths)
    if not isinstance(output_root, Path) or not output_root.is_dir():
        raise Package0ComparatorError("output_root must be an existing directory")
    resolved_root = output_root.resolve()
    actual_paths = tuple(
        sorted(
            path.relative_to(output_root).as_posix()
            for path in output_root.rglob("*")
            if path.is_file()
        )
    )
    if actual_paths != paths:
        raise Package0ComparatorError(
            "successor differs from frozen comparator: canonical_artifact_paths"
        )
    output_artifacts: list[dict[str, object]] = []
    bytes_by_path: dict[str, bytes] = {}
    for relative_path in normalized_paths:
        path = output_root / relative_path
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(resolved_root) or not path.is_file():
            raise Package0ComparatorError(
                f"successor artifact escapes or is missing: {relative_path}"
            )
        exact_bytes = path.read_bytes()
        bytes_by_path[relative_path] = exact_bytes
        output_artifacts.append(
            {
                "path": relative_path,
                "sha256": hashlib.sha256(exact_bytes).hexdigest().upper(),
                "size_bytes": len(exact_bytes),
            }
        )
    baseline_path = "baseline/output/xic_results.csv"
    final_path = "final/output/xic_results.csv"
    try:
        baseline_bytes = bytes_by_path[baseline_path]
        final_bytes = bytes_by_path[final_path]
    except KeyError as exc:
        raise Package0ComparatorError(
            "membership receipt must include both Targeted xic_results.csv files"
        ) from exc
    baseline_header, baseline_samples = _csv_membership(
        baseline_bytes,
        field=baseline_path,
    )
    final_header, final_samples = _csv_membership(
        final_bytes,
        field=final_path,
    )
    differences: list[str] = []
    if baseline_header != header or final_header != header:
        differences.append("ordered_final_header")
    if baseline_samples != samples or final_samples != samples:
        differences.append("ordered_sample")
    if differences:
        raise Package0ComparatorError(
            "successor differs from frozen comparator: " + ", ".join(differences)
        )
    baseline_id = contract.get("baseline_id")
    if not isinstance(baseline_id, str) or not baseline_id:
        raise Package0ComparatorError("baseline_id must be non-empty")
    return _Package0SuccessorObservation(
        baseline_id=baseline_id,
        canonical_output_inventory_sha256=_canonical_value_sha256(
            output_artifacts
        ),
        canonical_artifact_path_sha256=_ordered_sha256(paths),
        ordered_sample_sha256=_ordered_sha256(baseline_samples),
        ordered_final_header_sha256=_ordered_sha256(baseline_header),
        baseline_stage_xic_results_sha256=(
            hashlib.sha256(baseline_bytes).hexdigest().upper()
        ),
        final_stage_xic_results_sha256=(
            hashlib.sha256(final_bytes).hexdigest().upper()
        ),
    )


def _package1_output_root_identity(
    output_root: Path,
) -> tuple[int, int, int, int, int, str]:
    if not isinstance(output_root, Path):
        raise Package0ComparatorError(
            "Package 1 probe output_root must be a Path"
        )
    try:
        observed = os.lstat(output_root)
    except OSError as exc:
        raise Package0ComparatorError(
            "Package 1 comparator-owned output root is unavailable"
        ) from exc
    if not stat.S_ISDIR(observed.st_mode):
        raise Package0ComparatorError(
            "Package 1 comparator-owned output root must remain one directory"
        )
    marker_path = output_root / _PACKAGE1_ROOT_IDENTITY_MARKER
    try:
        marker = os.lstat(marker_path)
        marker_sha256 = hashlib.sha256(marker_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise Package0ComparatorError(
            "Package 1 comparator-owned output root identity marker changed"
        ) from exc
    if not stat.S_ISREG(marker.st_mode):
        raise Package0ComparatorError(
            "Package 1 comparator-owned output root identity marker changed"
        )
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        marker.st_dev,
        marker.st_ino,
        marker_sha256,
    )


def _package1_product_output_inventory(
    output_root: Path,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(output_root, Path) or not output_root.is_dir():
        raise Package0ComparatorError(
            "Package 1 probe output_root must be an existing directory"
        )
    resolved_root = output_root.resolve()
    inventory: list[tuple[str, str]] = []
    for path in output_root.rglob("*"):
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(resolved_root):
            raise Package0ComparatorError(
                "Package 1 product output path escapes the observed root"
            )
        if path.is_symlink():
            content_identity = "symlink"
        elif path.is_file():
            content_identity = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        elif path.is_dir():
            content_identity = "directory"
        else:
            content_identity = "other"
        inventory.append(
            (
                path.relative_to(output_root).as_posix(),
                content_identity,
            )
        )
    return tuple(sorted(inventory))


def _adapter_basis(receipt: _TargetedAdapterParityReceipt) -> dict[str, str]:
    return {
        "processing_request_id": receipt.processing_request_id,
        "attempt_semantics_sha256": receipt.attempt_semantics_sha256,
        "lineage_sha256": receipt.lineage_sha256,
        "scientific_results_sha256": receipt.scientific_results_sha256,
        "delivery_projection_sha256": receipt.delivery_projection_sha256,
        "baseline_stage_xic_results_sha256": (
            receipt.baseline_stage_xic_results_sha256
        ),
        "final_stage_xic_results_sha256": receipt.final_stage_xic_results_sha256,
    }


def _canonical_sha256(value: Mapping[str, object]) -> str:
    return _canonical_value_sha256(value)


def _canonical_value_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest().upper()


def _ordered_sha256(values: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest().upper()


def _bytes_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, bytes):
        raise Package0ComparatorError(f"{field} must be exact bytes")
    return hashlib.sha256(value).hexdigest().upper()


def _file_sha256(path: object, *, field: str) -> str:
    if not isinstance(path, Path) or not path.is_file():
        raise Package0ComparatorError(f"{field} must be an existing file")
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _membership_text_sequence(
    membership_receipt: Mapping[str, object],
    field: str,
) -> tuple[str, ...]:
    value = membership_receipt.get(field)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise Package0ComparatorError(f"{field} must be a non-empty text list")
    return tuple(value)


def _normalized_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise Package0ComparatorError(
            "canonical artifact paths must be normalized relative POSIX paths"
        )
    return value


def _csv_membership(
    exact_bytes: bytes,
    *,
    field: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        rows = tuple(
            tuple(row)
            for row in csv.reader(
                io.StringIO(exact_bytes.decode("utf-8-sig"), newline="")
            )
        )
    except (UnicodeDecodeError, csv.Error) as exc:
        raise Package0ComparatorError(f"{field} must be a UTF-8 CSV") from exc
    if len(rows) < 2 or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise Package0ComparatorError(f"{field} must be a rectangular non-empty CSV")
    return rows[0], tuple(row[0] for row in rows[1:])


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise Package0ComparatorError(
            f"{field} must be an upper-case SHA-256 digest"
        )
    return value


__all__ = [
    "Package0ComparatorError",
    "Package1DifferenceObservation",
    "Package1ObservationExperimentJob",
    "Package1ObservationTraceFixture",
    "assert_targeted_adapter_parity",
    "mint_targeted_adapter_parity_receipt",
    "run_package1_observation_experiment",
    "validate_package1_difference_manifest",
    "validate_package0_successor_output",
]
