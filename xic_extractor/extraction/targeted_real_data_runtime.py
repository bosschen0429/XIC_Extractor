"""Atomic runtime owner for bounded Package 3A RAW-backed Targeted work."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from xic_extractor.acquisition_contract import TraceAcquisitionBatch
from xic_extractor.config import ExtractionConfig
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.discovery.targeted_candidate_set import (
    canonical_targeted_candidate_set_bytes,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution_runtime import (
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    CoordinatorTerminalRecord,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    SupervisorFinalizationRecord,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_observability import (
    RuntimeObservabilityContractError,
    validate_runtime_acquisition_metrics,
)
from xic_extractor.execution_runtime_owner import (
    RuntimeAcquisitionReceipt,
    RuntimeArtifactRegistrationReceipt,
    RuntimeFinalizationReceipt,
    RuntimeResourceSettlementReceipt,
    RuntimeTelemetryProvider,
    RuntimeWorkerBatchReceipt,
    execute_runtime_attempt,
    mint_work_unit_settlement,
)
from xic_extractor.extraction.targeted_inputs import (
    TARGETED_PROCESSING_ALGORITHM_VERSION,
    TARGETED_PROCESSING_CONTRACT_VERSION,
    TARGETED_PROCESSING_INPUT_SCHEMA_VERSION,
    TARGETED_REAL_DATA_CANDIDATE_UNIVERSE_INPUT_KEY,
    TARGETED_REAL_DATA_DISCOVERY_SETTINGS_INPUT_KEY,
    TARGETED_SCIENTIFIC_SETTING_FIELDS,
    targeted_real_data_candidate_universe_entry_payload,
    targeted_real_data_discovery_settings_payload,
    validate_targeted_real_data_candidate_universe_payload,
)
from xic_extractor.extraction.targeted_real_data_adapter import (
    TargetedRealDataAdapterResult,
    _adapt_current_discovery_sample_target,
    _bounded_current_discovery_candidates,
    _TargetedRealDataWorkObserver,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedProcessResultRef,
    mint_targeted_process_result_ref,
)
from xic_extractor.peak_detection.selection_decision import (
    canonical_diagnostic_peak_hypothesis_selection_bytes,
)
from xic_extractor.processing_identity import (
    IdentityContractError,
    ProcessingOwnerReferences,
    canonical_current_attempt_artifact_closure,
    resolve_request_acquisition_source,
)
from xic_extractor.raw_reader import RawReaderOpenError, open_raw
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

_RESULT_SCHEMA_VERSION = "targeted_real_data_runtime_result_v2"
_ACQUISITION_SCHEMA_VERSION = "targeted_real_data_acquisition_v1"
_RUNTIME_COMPATIBILITY_TOKEN = "targeted-real-data-runtime:v1"


class TargetedRealDataRuntimeError(ValueError):
    """Raised when bounded RAW-backed Targeted work cannot enter runtime."""


@dataclass(frozen=True, slots=True)
class TargetedRealDataRuntimeJob:
    """One exact sample-target unit owned by one processing attempt."""

    processing_owners: ProcessingOwnerReferences
    sample: ResolvedCanonicalSampleIdentity
    target: ResolvedTargetDefinition
    candidates: tuple[DiscoveryCandidate, ...]
    settings: DiscoverySettings
    peak_config: ExtractionConfig
    raw_path: Path
    dll_dir: Path
    work_unit_key: str

    def __post_init__(self) -> None:
        expected = (
            ("processing_owners", ProcessingOwnerReferences),
            ("sample", ResolvedCanonicalSampleIdentity),
            ("target", ResolvedTargetDefinition),
            ("settings", DiscoverySettings),
            ("peak_config", ExtractionConfig),
            ("raw_path", Path),
            ("dll_dir", Path),
        )
        for field, kind in expected:
            if not isinstance(getattr(self, field), kind):
                raise TargetedRealDataRuntimeError(f"{field} has the wrong owner type")
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, DiscoveryCandidate) for item in self.candidates
        ):
            raise TargetedRealDataRuntimeError(
                "candidates must contain current DiscoveryCandidate values"
            )
        _validate_request_bound_scientific_settings(self)
        _required_text(self.work_unit_key, field="work_unit_key")


def _validate_request_bound_scientific_settings(
    job: TargetedRealDataRuntimeJob,
) -> None:
    request = job.processing_owners.request
    if (
        request.scientific_input_schema_version
        != TARGETED_PROCESSING_INPUT_SCHEMA_VERSION
        or request.processing_algorithm_version
        != TARGETED_PROCESSING_ALGORITHM_VERSION
        or request.processing_contract_version
        != TARGETED_PROCESSING_CONTRACT_VERSION
    ):
        raise TargetedRealDataRuntimeError(
            "processing request does not use the supported Targeted scientific "
            "contract"
        )
    request_inputs = json.loads(request.normalized_scientific_inputs_json)
    expected_peak_settings = {
        field: getattr(job.peak_config, field)
        for field in TARGETED_SCIENTIFIC_SETTING_FIELDS
    }
    if request_inputs.get("settings") != expected_peak_settings:
        raise TargetedRealDataRuntimeError(
            "peak_config scientific settings do not match processing request"
        )
    expected_discovery_settings = (
        targeted_real_data_discovery_settings_payload(job.settings)
    )
    if (
        request_inputs.get(TARGETED_REAL_DATA_DISCOVERY_SETTINGS_INPUT_KEY)
        != expected_discovery_settings
    ):
        raise TargetedRealDataRuntimeError(
            "Discovery scientific settings do not match processing request"
        )
    universe = request_inputs.get(TARGETED_REAL_DATA_CANDIDATE_UNIVERSE_INPUT_KEY)
    try:
        expected_universe_entry = (
            targeted_real_data_candidate_universe_entry_payload(
                job.sample.metadata.raw_stem,
                job.candidates,
            )
        )
    except IdentityContractError as exc:
        raise TargetedRealDataRuntimeError(str(exc)) from exc
    try:
        universe_entries = validate_targeted_real_data_candidate_universe_payload(
            universe,
            expected_sample_stems=tuple(
                binding.adapter_input_key
                for binding in request.acquisition_bindings
            ),
        )
    except IdentityContractError as exc:
        raise TargetedRealDataRuntimeError(
            "processing request does not bind a complete Discovery candidate universe"
        ) from exc
    matches = [
        item
        for item in universe_entries
        if item.get("sample_stem") == job.sample.metadata.raw_stem
    ]
    if len(matches) != 1 or matches[0] != expected_universe_entry:
        raise TargetedRealDataRuntimeError(
            "Discovery candidate universe does not match processing request"
        )


@dataclass(frozen=True, slots=True)
class TargetedRealDataRuntimeApplicationResult:
    """One atomic closeout plus an exact successful scientific result."""

    work_unit_key: str
    expected_acquisition_source_id: str
    finalization: RuntimeFinalizationReceipt
    result_ref: TargetedProcessResultRef | None
    adapter_result: TargetedRealDataAdapterResult | None
    acquisition: TraceAcquisitionBatch | None = None

    def __post_init__(self) -> None:
        key = _required_text(self.work_unit_key, field="work_unit_key")
        expected_source_id = _required_text(
            self.expected_acquisition_source_id,
            field="expected_acquisition_source_id",
        )
        if not isinstance(self.finalization, RuntimeFinalizationReceipt):
            raise TargetedRealDataRuntimeError(
                "finalization must be an owner-minted RuntimeFinalizationReceipt"
            )
        if self.acquisition is not None and not isinstance(
            self.acquisition,
            TraceAcquisitionBatch,
        ):
            raise TargetedRealDataRuntimeError(
                "acquisition must be an owner-observed TraceAcquisitionBatch"
            )
        if self.acquisition is not None and (
            self.acquisition.capability.acquisition_source.acquisition_source_id
            != expected_source_id
        ):
            raise TargetedRealDataRuntimeError(
                "application acquisition references a different sample source"
            )
        runtime = self.finalization.coordinator.state.runtime
        settlement = next(
            (item for item in runtime.settlements if item.work_unit_key == key),
            None,
        )
        terminal = self.finalization.supervisor.terminal_outcome
        if terminal is not AttemptTerminalOutcome.SUCCEEDED:
            if self.result_ref is not None or self.adapter_result is not None:
                raise TargetedRealDataRuntimeError(
                    "unsuccessful runtime cannot expose scientific success"
                )
            _validate_finalized_acquisition_metrics(
                self.finalization,
                self.acquisition,
            )
            return
        if (
            settlement is None
            or settlement.outcome is not WorkUnitTerminalOutcome.SUCCEEDED
        ):
            raise TargetedRealDataRuntimeError(
                "successful runtime requires its successful work settlement"
            )
        if not isinstance(self.result_ref, TargetedProcessResultRef):
            raise TargetedRealDataRuntimeError(
                "successful runtime requires its Targeted result reference"
            )
        if not isinstance(self.adapter_result, TargetedRealDataAdapterResult):
            raise TargetedRealDataRuntimeError(
                "successful runtime requires its real-data adapter result"
            )
        if settlement.owner_result_ref != self.result_ref.artifact:
            raise TargetedRealDataRuntimeError(
                "runtime settlement does not bind the real-data result reference"
            )
        acquisition = self.adapter_result.acquisition
        if self.acquisition is not acquisition:
            raise TargetedRealDataRuntimeError(
                "successful runtime requires its exact adapter acquisition"
            )
        if acquisition is not None and (
            acquisition.capability.acquisition_source.acquisition_source_id
            != expected_source_id
        ):
            raise TargetedRealDataRuntimeError(
                "successful acquisition references a different sample source"
            )
        candidate_set = self.adapter_result.alignment_input.candidate_set
        produced_artifacts = _result_artifact_closure(self.adapter_result)
        expected_upstreams = tuple(
            artifact.evidence_artifact_id for artifact in produced_artifacts
        )
        if self.result_ref.artifact.upstream_artifact_ids != expected_upstreams:
            raise TargetedRealDataRuntimeError(
                "result reference does not close the exact scientific artifacts"
            )
        expected_registered = canonical_current_attempt_artifact_closure(
            producer_attempt=candidate_set.processing_attempt,
            artifacts=(*produced_artifacts, self.result_ref.artifact),
        )
        registered = tuple(
            record.artifact
            for record in self.finalization.coordinator.state.artifacts
            if record.work_unit_key == key
        )
        if registered != expected_registered:
            raise TargetedRealDataRuntimeError(
                "runtime did not register the exact scientific artifact closure"
            )
        payload = _canonical_adapter_result_bytes(self.adapter_result)
        if (
            self.result_ref.owner_result_content_sha256
            != hashlib.sha256(payload).hexdigest()
            or self.result_ref.owner_result_byte_size != len(payload)
        ):
            raise TargetedRealDataRuntimeError(
                "runtime result reference does not bind the full adapter result"
            )
        _validate_finalized_acquisition_metrics(self.finalization, acquisition)

    @property
    def coordinator_record(self) -> CoordinatorTerminalRecord:
        return self.finalization.coordinator

    @property
    def finalization_record(self) -> SupervisorFinalizationRecord:
        return self.finalization.supervisor

    @property
    def lifecycle(self) -> _TargetedRealDataRuntimeLifecycleProjection:
        """Compatibility projection without reopening a settlement bypass."""

        runtime = self.finalization.coordinator.state.runtime
        settlement = next(
            (
                item
                for item in runtime.settlements
                if item.work_unit_key == self.work_unit_key
            ),
            None,
        )
        acquisitions: tuple[tuple[str, TraceAcquisitionBatch], ...] = ()
        if (
            settlement is not None
            and settlement.outcome is WorkUnitTerminalOutcome.SUCCEEDED
            and self.acquisition is not None
        ):
            acquisitions = ((self.work_unit_key, self.acquisition),)
        return _TargetedRealDataRuntimeLifecycleProjection(
            settlement=_TargetedRealDataRuntimeSettlementProjection(
                runtime=runtime,
                successful_acquisition_batches=acquisitions,
            ),
            finalization=self.finalization,
        )


@dataclass(frozen=True, slots=True)
class _TargetedRealDataRuntimeSettlementProjection:
    runtime: AttemptRuntime
    successful_acquisition_batches: tuple[tuple[str, TraceAcquisitionBatch], ...]


@dataclass(frozen=True, slots=True)
class _TargetedRealDataRuntimeLifecycleProjection:
    settlement: _TargetedRealDataRuntimeSettlementProjection
    finalization: RuntimeFinalizationReceipt

    @property
    def finalization_record(self) -> SupervisorFinalizationRecord:
        return self.finalization.supervisor


def execute_current_discovery_sample_target_runtime(
    runtime: AttemptRuntime,
    job: TargetedRealDataRuntimeJob,
    *,
    telemetry_provider: RuntimeTelemetryProvider,
    recovery_marker: OwnerPathRuntimeMarker,
) -> TargetedRealDataRuntimeApplicationResult:
    """Open RAW, project evidence, and finalize through one atomic owner entry."""

    if not isinstance(runtime, AttemptRuntime):
        raise TargetedRealDataRuntimeError("runtime must be an AttemptRuntime")
    if not isinstance(job, TargetedRealDataRuntimeJob):
        raise TargetedRealDataRuntimeError("job must be TargetedRealDataRuntimeJob")
    if not isinstance(recovery_marker, OwnerPathRuntimeMarker):
        raise TargetedRealDataRuntimeError(
            "recovery_marker must be OwnerPathRuntimeMarker"
        )
    if runtime.manifest.attempt != job.processing_owners.attempt:
        raise TargetedRealDataRuntimeError(
            "job processing attempt must match the runtime manifest"
        )
    if (
        not runtime.manifest_preflight_failed
        and runtime.manifest.work_unit_keys != (job.work_unit_key,)
    ):
        raise TargetedRealDataRuntimeError(
            "bounded real-data runtime requires exactly its declared work unit"
        )

    expected_source = resolve_request_acquisition_source(
        processing_owners=job.processing_owners,
        sample=job.sample,
    )
    planning_error: Exception | None = None
    try:
        bounded = _bounded_current_discovery_candidates(
            candidates=job.candidates,
            sample=job.sample,
            target=job.target,
        )
    except Exception as exc:
        bounded = ()
        planning_error = exc
    resource_key = (
        f"acquisition-session:{job.work_unit_key}" if bounded else None
    )
    reservations = (
        (
            RuntimeResourceReservation(
                attempt=runtime.manifest.attempt,
                kind=RuntimeResourceKind.ACQUISITION_SESSION,
                resource_key=resource_key,
                worker_key=f"inline-acquisition:{job.work_unit_key}",
                source_reference=expected_source.acquisition_source_id,
                compatibility_token=_RUNTIME_COMPATIBILITY_TOKEN,
            ),
        )
        if resource_key is not None
        else ()
    )
    recovery_marker.record_launch(runtime)
    observed_result: TargetedRealDataAdapterResult | None = None
    observed_reference: TargetedProcessResultRef | None = None
    observed_acquisition: TraceAcquisitionBatch | None = None

    def provide_worker_batch(_aggregate: object) -> RuntimeWorkerBatchReceipt:
        nonlocal observed_result, observed_reference, observed_acquisition
        batch, result, reference, acquisition = _execute_job(
            job,
            resource_key=resource_key,
            planning_error=planning_error,
            expected_acquisition_source_id=expected_source.acquisition_source_id,
        )
        observed_result = result
        observed_reference = reference
        observed_acquisition = acquisition
        return batch

    finalization = execute_runtime_attempt(
        runtime,
        reservations,
        worker_batch_provider=provide_worker_batch,
        telemetry_provider=telemetry_provider,
        persister=recovery_marker,
    )
    _validate_finalized_acquisition_metrics(finalization, observed_acquisition)
    succeeded = (
        finalization.supervisor.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED
    )
    return TargetedRealDataRuntimeApplicationResult(
        work_unit_key=job.work_unit_key,
        expected_acquisition_source_id=expected_source.acquisition_source_id,
        finalization=finalization,
        result_ref=observed_reference if succeeded else None,
        adapter_result=observed_result if succeeded else None,
        acquisition=observed_acquisition,
    )


def _execute_job(
    job: TargetedRealDataRuntimeJob,
    *,
    resource_key: str | None,
    planning_error: Exception | None,
    expected_acquisition_source_id: str,
) -> tuple[
    RuntimeWorkerBatchReceipt,
    TargetedRealDataAdapterResult | None,
    TargetedProcessResultRef | None,
    TraceAcquisitionBatch | None,
]:
    observer = _TargetedRealDataWorkObserver(job.processing_owners)
    raw = None
    open_attempted = False
    opened = False
    cleanup_outcome = RuntimeResourceCleanupOutcome.SUCCEEDED
    error = planning_error
    result: TargetedRealDataAdapterResult | None = None
    try:
        if error is None and resource_key is not None:
            source = resolve_request_acquisition_source(
                processing_owners=job.processing_owners,
                sample=job.sample,
            )
            open_attempted = True
            raw = open_raw(
                job.raw_path,
                job.dll_dir,
                expected_acquisition_source=source,
            )
            opened = True
        if error is None:
            result = _adapt_current_discovery_sample_target(
                processing_owners=job.processing_owners,
                sample=job.sample,
                target=job.target,
                candidates=job.candidates,
                settings=job.settings,
                peak_config=job.peak_config,
                raw=raw,
                _observer=observer,
            )
    except RawReaderOpenError as exc:
        cleanup_outcome = (
            RuntimeResourceCleanupOutcome.FAILED
            if exc.cleanup_failed
            else RuntimeResourceCleanupOutcome.SUCCEEDED
        )
        error = exc
    except Exception as exc:
        error = exc
    finally:
        if raw is not None:
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
                cleanup_outcome = RuntimeResourceCleanupOutcome.FAILED
                if error is None:
                    error = exc

    acquisition = observer.acquisition
    if acquisition is not None and (
        acquisition.capability.acquisition_source.acquisition_source_id
        != expected_acquisition_source_id
    ):
        error = TargetedRealDataRuntimeError(
            "observed acquisition references a different sample source"
        )

    try:
        observed_artifacts = observer.artifact_closure()
    except Exception as exc:
        # Closure failure must not erase valid artifacts already minted by the
        # worker; the owner will quarantine them on the failed terminal path.
        observed_artifacts = observer.observed_artifacts()
        if error is None:
            error = exc

    reference: TargetedProcessResultRef | None = None
    registered_artifacts = observed_artifacts
    if result is not None:
        try:
            result_artifacts = _result_artifact_closure(result)
            if observed_artifacts and observed_artifacts != result_artifacts:
                raise TargetedRealDataRuntimeError(
                    "worker observer does not close the exact successful result"
                )
            payload = _canonical_adapter_result_bytes(result)
            reference = mint_targeted_process_result_ref(
                processing_attempt=job.processing_owners.attempt,
                work_unit_key=job.work_unit_key,
                owner_result_payload=payload,
                upstream_artifacts=result_artifacts,
            )
            registered_artifacts = canonical_current_attempt_artifact_closure(
                producer_attempt=job.processing_owners.attempt,
                artifacts=(*result_artifacts, reference.artifact),
            )
        except Exception as exc:
            if error is None:
                error = exc

    success = error is None and result is not None and reference is not None
    if not success and error is None:
        error = TargetedRealDataRuntimeError(
            "adapter returned no terminal scientific result"
        )
    settlement = mint_work_unit_settlement(
        attempt=job.processing_owners.attempt,
        work_unit_key=job.work_unit_key,
        outcome=(
            WorkUnitTerminalOutcome.SUCCEEDED
            if success
            else WorkUnitTerminalOutcome.FAILED
        ),
        owner_result_ref=(
            reference.artifact if success and reference is not None else None
        ),
        failure_detail=None if success else _failure_detail(job, error),
    )
    resource_receipts = (
        (
            RuntimeResourceSettlementReceipt(
                attempt=job.processing_owners.attempt,
                resource_key=resource_key,
                open_outcome=(
                    RuntimeResourceOpenOutcome.OPENED
                    if opened
                    else (
                        RuntimeResourceOpenOutcome.OPEN_FAILED
                        if open_attempted
                        else RuntimeResourceOpenOutcome.NOT_OPENED
                    )
                ),
                cleanup_outcome=cleanup_outcome,
            ),
        )
        if resource_key is not None
        else ()
    )
    batch = RuntimeWorkerBatchReceipt(
        settlements=(settlement,),
        resource_settlements=resource_receipts,
        artifact_registrations=tuple(
            RuntimeArtifactRegistrationReceipt(
                work_unit_key=job.work_unit_key,
                artifact=artifact,
            )
            for artifact in registered_artifacts
        ),
        acquisitions=(
            (
                RuntimeAcquisitionReceipt(
                    work_unit_key=job.work_unit_key,
                    acquisition=acquisition,
                ),
            )
            if acquisition is not None
            else ()
        ),
    )
    return (
        batch,
        result if success else None,
        reference if success else None,
        acquisition,
    )


def _failure_detail(
    job: TargetedRealDataRuntimeJob,
    error: Exception | None,
) -> WorkUnitFailureDetail:
    observed = error or TargetedRealDataRuntimeError("unknown worker failure")
    message = " ".join(str(observed).split()) or type(observed).__name__
    return WorkUnitFailureDetail(
        category=WorkUnitFailureCategory.EXECUTION_ERROR,
        stage="targeted-real-data-adapter",
        source_reference=f"targeted-real-data:{job.work_unit_key}",
        message=f"{type(observed).__name__}: {message}",
    )


def _result_artifact_closure(
    result: TargetedRealDataAdapterResult,
) -> tuple[EvidenceArtifactRef, ...]:
    candidate_set = result.alignment_input.candidate_set
    selection = result.alignment_input.selection
    return canonical_current_attempt_artifact_closure(
        producer_attempt=candidate_set.processing_attempt,
        artifacts=(
            *candidate_set.lineage_artifacts,
            selection.artifact,
        ),
    )


def _canonical_adapter_result_bytes(
    result: TargetedRealDataAdapterResult,
) -> bytes:
    candidate_set = result.alignment_input.candidate_set
    selection = result.alignment_input.selection
    acquisition_bytes = (
        None
        if result.acquisition is None
        else _canonical_acquisition_bytes(result.acquisition)
    )
    payload = {
        "schema_version": _RESULT_SCHEMA_VERSION,
        "processing_request_id": candidate_set.processing_request_id,
        "processing_attempt_id": candidate_set.processing_attempt_id,
        "canonical_sample_identity_id": candidate_set.canonical_sample_identity_id,
        "target_snapshot_id": candidate_set.target_snapshot_id,
        "target_key_id": candidate_set.target_key_id,
        "target_definition_id": candidate_set.target_definition_id,
        "candidate_set_artifact_id": candidate_set.artifact.evidence_artifact_id,
        "candidate_set_sha256": hashlib.sha256(
            canonical_targeted_candidate_set_bytes(candidate_set)
        ).hexdigest(),
        "selection_artifact_id": selection.artifact.evidence_artifact_id,
        "selection_sha256": hashlib.sha256(
            canonical_diagnostic_peak_hypothesis_selection_bytes(selection)
        ).hexdigest(),
        "result_artifact_closure": [
            _canonical_artifact_ref_payload(artifact)
            for artifact in _result_artifact_closure(result)
        ],
        "source_candidate_ids": result.source_candidate_ids,
        "acquisition_sha256": (
            None
            if acquisition_bytes is None
            else hashlib.sha256(acquisition_bytes).hexdigest()
        ),
        "acquisition_byte_size": (
            None if acquisition_bytes is None else len(acquisition_bytes)
        ),
        "validation_status": result.validation_status,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_artifact_ref_payload(artifact: EvidenceArtifactRef) -> dict[str, object]:
    semantic = artifact.semantic_contract
    content = artifact.content_identity
    return {
        "evidence_artifact_id": artifact.evidence_artifact_id,
        "producer_processing_request_id": artifact.producer_processing_request_id,
        "producer_processing_attempt_id": artifact.producer_processing_attempt_id,
        "semantic_contract": {
            "semantic_contract_id": semantic.semantic_contract_id,
            "owner_namespace": semantic.owner_namespace,
            "semantic_role": semantic.semantic_role,
            "schema_version": semantic.schema_version,
            "validation_status": semantic.validation_status,
            "media_type": semantic.media_type,
            "serialization_format": semantic.serialization_format,
        },
        "content_identity": {
            "artifact_content_id": content.artifact_content_id,
            "content_sha256": content.content_sha256,
            "byte_size": content.byte_size,
            "media_type": content.media_type,
            "serialization_format": content.serialization_format,
        },
        "upstream_artifact_ids": artifact.upstream_artifact_ids,
    }


def _canonical_acquisition_bytes(acquisition: TraceAcquisitionBatch) -> bytes:
    source = acquisition.capability.acquisition_source
    payload = {
        "schema_version": _ACQUISITION_SCHEMA_VERSION,
        "capability_id": acquisition.capability.capability_id,
        "acquisition_source": {
            "acquisition_source_id": source.acquisition_source_id,
            "content_sha256": source.content_sha256,
            "byte_size": source.byte_size,
            "acquisition_format": source.acquisition_format,
            "observation_namespace": source.observation_namespace,
        },
        "adapter_namespace": acquisition.capability.adapter_namespace,
        "coordinate_kind": acquisition.capability.coordinate_kind.value,
        "logical_xic_request_count": acquisition.logical_xic_request_count,
        "xic_batch_count": acquisition.xic_batch_count,
        "adapter_chromatogram_call_count": (
            acquisition.adapter_chromatogram_call_count
        ),
        "trace_point_count": acquisition.trace_point_count,
        "traces": [
            {
                "trace_coordinate_id": item.coordinate.trace_coordinate_id,
                "request": {
                    "mz": item.coordinate.request.mz,
                    "rt_min": item.coordinate.request.rt_min,
                    "rt_max": item.coordinate.request.rt_max,
                    "ppm_tol": item.coordinate.request.ppm_tol,
                },
                "first_scan": item.coordinate.first_scan,
                "last_scan": item.coordinate.last_scan,
                "coordinate_kind": item.coordinate.coordinate_kind.value,
                "trace_point_count": item.coordinate.trace_point_count,
                "rt": _canonical_array_values(item.trace.rt),
                "intensity": _canonical_array_values(item.trace.intensity),
                "rt_sha256": _canonical_array_sha256(item.trace.rt),
                "intensity_sha256": _canonical_array_sha256(
                    item.trace.intensity
                ),
            }
            for item in acquisition.traces
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_array_values(values: np.ndarray) -> list[float]:
    normalized = np.asarray(values, dtype=np.float64)
    return normalized.tolist()


def _canonical_array_sha256(values: np.ndarray) -> str:
    payload = json.dumps(
        _canonical_array_values(values),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_finalized_acquisition_metrics(
    finalization: RuntimeFinalizationReceipt,
    acquisition: TraceAcquisitionBatch | None,
) -> None:
    try:
        validate_runtime_acquisition_metrics(
            finalization.observability_ledger,
            () if acquisition is None else (acquisition,),
        )
    except RuntimeObservabilityContractError as exc:
        raise TargetedRealDataRuntimeError(
            "runtime acquisition metrics do not match the observed RAW work"
        ) from exc


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TargetedRealDataRuntimeError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


__all__ = (
    "TargetedRealDataRuntimeApplicationResult",
    "TargetedRealDataRuntimeError",
    "TargetedRealDataRuntimeJob",
    "execute_current_discovery_sample_target_runtime",
)
