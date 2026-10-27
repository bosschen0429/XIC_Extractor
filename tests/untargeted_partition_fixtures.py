"""Dense partition replay fixtures for scientific output comparisons."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from xic_extractor.alignment.cid_nl_inputs import (
    CID_NL_ALIGNMENT_CONFIG,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.result.application_result import (
    UntargetBatchApplicationResultRequest,
    finalize_alignment_result,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.batch_contract import (
    DiscoveryCandidateUniverseReceipt,
)
from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    DiscoverySettings,
)
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.execution.attempt import (
    RuntimeTelemetryProvider,
    start_runtime_attempt,
)
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    UntargetedSearchScope,
)
from xic_extractor.sample_metadata import (
    SampleMetadata,
)
from xic_extractor.untargeted_execution.execution import (
    materialize_untargeted_partition,
)
from xic_extractor.untargeted_execution.execution_result import (
    CompletedUntargetBatchWorkflowResult,
)
from xic_extractor.untargeted_execution.input_planning import (
    prepare_eager_untargeted_plan,
)
from xic_extractor.untargeted_execution.partition.execution import (
    UntargetedAttemptResult,
    execute_attempt,
)
from xic_extractor.untargeted_execution.partition_composition import (
    UntargetedPartitionInputs,
    compose_untargeted_partition,
    prepare_untargeted_partition_grouping,
)


def build_dense_partition(
    *,
    raw_paths: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    discovery_receipts_by_sample: Mapping[str, DiscoveryCandidateUniverseReceipt],
    dll_dir: Path,
    sample_identity_namespace: str,
    signal_processing_profile: SignalProcessingProfile,
    physical_records_by_candidate: Mapping[str, DiscoveryPhysicalFactRecord],
) -> UntargetedPartitionInputs:
    """Prepare one dense partition through the formal eager plan owner."""

    plan = prepare_eager_untargeted_plan(
        raw_paths=raw_paths,
        sample_metadata=sample_metadata,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=discovery_receipts_by_sample,
        dll_dir=dll_dir,
        sample_identity_namespace=sample_identity_namespace,
        signal_processing_profile=signal_processing_profile,
        physical_records_by_candidate=physical_records_by_candidate,
    )
    return materialize_untargeted_partition(plan, plan.subject_batch)


def execute_dense_partition(
    batch: UntargetedPartitionInputs,
    *,
    telemetry_provider: RuntimeTelemetryProvider,
    recovery_marker: OwnerPathRuntimeMarker,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    alignment_config: AlignmentConfig = CID_NL_ALIGNMENT_CONFIG,
    known_istd_reference: KnownIstdReference | None = None,
) -> UntargetedAttemptResult:
    """Run the retained dense replay runtime for partition result oracles."""

    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation="cid-nl-production",
    )
    result = execute_attempt(
        runtime,
        batch.jobs,
        telemetry_provider=telemetry_provider,
        recovery_marker=recovery_marker,
        progress_callback=progress_callback,
        alignment_config=alignment_config,
        prepare_grouping=lambda results: prepare_untargeted_partition_grouping(
            batch,
            results,
            alignment_config=alignment_config,
            known_istd_reference=known_istd_reference,
            progress_callback=progress_callback,
        ),
    )
    return result


def complete_dense_partition(
    batch, runtime_result, *, alignment_config, known_istd_reference=None
):
    if not runtime_result.succeeded:
        raise RuntimeError("cannot compose a partial dense partition")
    batch = replace(batch, prepared_grouping=runtime_result.prepared_grouping)
    partition = compose_untargeted_partition(
        batch,
        runtime_result.sample_results,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
    )
    owners = batch.context.processing_owners
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=owners.request,
        samples=batch.context.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=batch.context.peak_config,
            signal_processing_profile=batch.context.signal_processing_profile,
        ),
        evidence_providers=tuple(
            EvidenceProviderBinding(CID_NL_PROVIDER_ID, subject.method_subject_id)
            for subject in batch.subject_batch.subjects
        ),
        declared_candidate_ids=tuple(
            cid for cid, _ in batch.subject_batch.candidate_memberships
        ),
        capture_bindings=partition.capture_bindings,
    )
    application_result = finalize_alignment_result(
        UntargetBatchApplicationResultRequest(
            scope=scope,
            processing_request=owners.request,
            processing_attempt=owners.attempt,
            children=partition.children,
            excluded_candidates=partition.excluded_candidates,
        )
    )
    return CompletedUntargetBatchWorkflowResult(
        runtime_receipt=runtime_result.finalization,
        application_result=application_result,
        method_assessments=partition.method_assessments,
        initial_groups=partition.initial_groups,
        initial_edges=partition.initial_edges,
        capture_evidence=partition.capture_evidence,
    )


def fixture_assessment(result):
    """Inspect retained sample facts for scientific fixture expectations."""
    if result.backfill_attempt is not None:
        return result.backfill_attempt.assessment
    return result.adapter_result.assessment_input.assessment
