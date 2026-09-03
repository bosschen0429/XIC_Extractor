"""Production Targeted adapter from retained acquisition evidence to #312."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
)
from xic_extractor.alignment.mode_track import ModeTrackTrajectoryPolicy
from xic_extractor.alignment.workflow import (
    CaptureWorkflowArguments,
    CompletedTargetWorkflowResult,
    ModeTrackWorkflowArguments,
    TargetWorkflowAdapter,
    TargetWorkflowRequest,
    WorkflowRuntimeInvocation,
    execute_alignment_workflow,
)
from xic_extractor.config import Target
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    TargetListSnapshot,
    mint_target_list_snapshot,
    resolve_target_definition,
)
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
)
from xic_extractor.execution_runtime import WorkUnitTerminalOutcome
from xic_extractor.execution_runtime_observability import RuntimeMetricAvailability
from xic_extractor.execution_runtime_owner import (
    RuntimeArtifactIoSample,
    RuntimeArtifactRegistrationReceipt,
    RuntimeProcessTreePeakRssSample,
    RuntimeTelemetrySamples,
    RuntimeWorkerBatchReceipt,
    RuntimeWorkerPeakRssSample,
    mint_work_unit_settlement,
    runtime_observation_event_count,
    runtime_started_worker_keys,
    start_runtime_attempt,
)
from xic_extractor.extraction.targeted_inputs import (
    build_targeted_processing_request_identity,
    resolve_targeted_inputs,
    resolve_targeted_raw_paths,
)
from xic_extractor.extraction.targeted_service import (
    FinalizedTargetProductionResult,
    TargetedApplicationRequest,
)
from xic_extractor.extractor import ExtractionResult, FileResult, RunOutput
from xic_extractor.peak_detection.capture import (
    bind_owner_backed_capture_hypothesis,
    capture_peak_hypotheses,
    mint_captured_peak_observation,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceRef,
    mint_cid_nl_evidence_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentRef,
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    cid_nl_method_subject_from_target,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
    DdaCidNlPeakSupportRef,
    resolve_dda_cid_nl_peak_support_ref,
)
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    compose_ms1_observation_peak_hypothesis,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    TargetSearchScope,
    target_ms1_subject_id,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    compose_processing_owner_references,
    issue_processing_attempt,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)

_IDENTITY_NAMESPACE = "targeted-production-v1"
_TRACE_SOURCE = "xic_extractor.targeted_production_workflow:retained-acquisition"
_NO_OBSERVATION_ARTIFACT_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.extraction.targeted_production_workflow",
    semantic_role="retained_no_observation_capture_population",
    schema_version="targeted_no_observation_capture_v1",
    validation_status="production_input",
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf-v1",
)


@dataclass(frozen=True, slots=True)
class _ProductionContext:
    owners: ProcessingOwnerReferences
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    targets: tuple[ResolvedTargetDefinition, ...]
    results_by_sample: tuple[tuple[ResolvedCanonicalSampleIdentity, FileResult], ...]
    raw_stems: frozenset[str]


def execute_targeted_production_workflow(
    request: TargetedApplicationRequest,
    acquisition_evidence: RunOutput,
) -> tuple[FinalizedTargetProductionResult, ...]:
    """Produce one finalized #312 result per declared Target, in row order."""

    context = _build_context(request, acquisition_evidence)
    return tuple(
        _execute_target(_context_for_target(context, index), request, target)
        for index, target in enumerate(context.targets)
    )


def _build_context(
    request: TargetedApplicationRequest,
    acquisition_evidence: RunOutput,
) -> _ProductionContext:
    raw_paths = resolve_targeted_raw_paths(request.config)
    if len(raw_paths) != len(acquisition_evidence.file_results):
        raise RuntimeError("Targeted acquisition returned partial sample coverage")
    resolved_inputs = resolve_targeted_inputs(
        request.config,
        raw_paths=raw_paths,
        injection_order=request.injection_order,
        rt_prior_library=request.rt_prior_library,
        model_selection_expected_diff_approvals=(
            request.model_selection_expected_diff_approvals
        ),
    )
    target_snapshot = mint_target_list_snapshot(
        request.targets,
        identity_namespace=_IDENTITY_NAMESPACE,
    )
    rows = tuple(
        SampleMetadata(
            sample_name=file_result.sample_name,
            raw_stem=raw_path.stem,
            injection_order=resolved_inputs.injection_order[raw_path.stem],
            group=file_result.group or "",
        )
        for raw_path, file_result in zip(
            raw_paths,
            acquisition_evidence.file_results,
            strict=True,
        )
    )
    sample_snapshot = mint_sample_metadata_snapshot(
        rows,
        identity_namespace=_IDENTITY_NAMESPACE,
    )
    samples = tuple(
        resolve_canonical_sample_identity(sample_snapshot, row.raw_stem) for row in rows
    )
    processing_request = build_targeted_processing_request_identity(
        request.config,
        target_snapshot=target_snapshot,
        raw_paths=raw_paths,
        resolved_inputs=resolved_inputs,
    )
    attempt = issue_processing_attempt(processing_request)
    resolved_targets = tuple(
        resolve_target_definition(target_snapshot, target.label)
        for target in request.targets
    )
    owners = compose_processing_owner_references(
        request=processing_request,
        attempt=attempt,
        sample_snapshot=sample_snapshot,
        sample_resolutions=samples,
        target_snapshot=target_snapshot,
    )
    return _ProductionContext(
        owners=owners,
        samples=samples,
        targets=resolved_targets,
        results_by_sample=tuple(
            zip(samples, acquisition_evidence.file_results, strict=True)
        ),
        raw_stems=frozenset(path.stem for path in raw_paths),
    )


def _context_for_target(
    context: _ProductionContext,
    target_index: int,
) -> _ProductionContext:
    """Reuse the initial attempt, then issue one attempt per later Target."""

    if target_index == 0:
        return context
    owners = context.owners
    attempt = issue_processing_attempt(owners.request)
    return replace(
        context,
        owners=compose_processing_owner_references(
            request=owners.request,
            attempt=attempt,
            sample_snapshot=owners.sample_snapshot,
            sample_resolutions=owners.sample_resolutions,
            target_snapshot=owners.target_snapshot,
        ),
    )


def _execute_target(
    context: _ProductionContext,
    request: TargetedApplicationRequest,
    target: ResolvedTargetDefinition,
) -> FinalizedTargetProductionResult:
    ms1_only = target.definition.target.neutral_loss_da is None
    method_subject = None if ms1_only else cid_nl_method_subject_from_target(target)
    method_subject_id = (
        target_ms1_subject_id(target)
        if method_subject is None
        else method_subject.method_subject_id
    )
    candidates = []
    for sample, file_result in context.results_by_sample:
        extraction = file_result.results[target.definition.target.label]
        if extraction.acquisition_trace is None and not ms1_only:
            if extraction.selected_hypothesis is not None:
                raise RuntimeError(
                    "selected Targeted result lost its acquisition trace"
                )
            continue
        if extraction.acquisition_trace is None:
            if extraction.selected_hypothesis is not None:
                raise RuntimeError(
                    "selected Targeted result lost its acquisition trace"
                )
            raise RuntimeError(
                "MS1-only Target is missing retained acquisition evidence"
            )
        candidates.append((sample, extraction, _candidate_id(target, sample)))
    bindings = tuple(
        _capture_binding(
            context.owners,
            target,
            sample,
            result,
            candidate_id,
            method_subject_id=method_subject_id,
            retained_only=ms1_only,
        )
        for sample, result, candidate_id in candidates
    )
    scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=context.owners.request,
        samples=context.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="targeted_gaussian15",
            peak_config=request.config,
        ),
        evidence_providers=(
            ()
            if method_subject is None
            else (
                EvidenceProviderBinding(
                    provider_id=CID_NL_PROVIDER_ID,
                    method_subject_id=method_subject.method_subject_id,
                ),
            )
        ),
        declared_candidate_ids=tuple(binding.candidate_id for binding in bindings),
        capture_bindings=bindings,
        target_snapshot=_target_snapshot(context.owners),
        target=target,
    )
    assessments: list[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef]
    ] = []
    capture_inputs: list[CaptureWorkflowArguments] = []
    candidate_by_sample = {
        sample.identity.sample_identity_id: (result, candidate_id)
        for sample, result, candidate_id in candidates
    }
    for sample in context.samples:
        candidate = candidate_by_sample.get(sample.identity.sample_identity_id)
        observations: tuple[PeakObservationRef, ...] = ()
        captured = None
        evidence_refs: tuple[CidNlEvidenceRef, ...] = ()
        support_refs: tuple[DdaCidNlPeakSupportRef, ...] = ()
        if candidate is not None:
            result, candidate_id = candidate
            trace = _target_capture_trace(
                context.owners,
                sample,
                result,
                target.definition.target,
            )
            captured = capture_peak_hypotheses(
                scope=scope,
                candidate_id=candidate_id,
                trace=trace,
            )
            if captured.selected_hypothesis is not None:
                observation = mint_captured_peak_observation(
                    captured,
                    processing_owners=context.owners,
                    sample=sample,
                )
                observations = (observation,)
            if ms1_only:
                owner_hypothesis = (
                    None
                    if not observations
                    else compose_ms1_observation_peak_hypothesis(
                        processing_owners=context.owners,
                        sample=sample,
                        peak_observation=observations[0],
                    )
                )
                capture_inputs.append(
                    CaptureWorkflowArguments(
                        candidate_id=captured.candidate_id,
                        trace=captured.trace,
                        owner_observation=(
                            None if not observations else observations[0]
                        ),
                        owner_hypothesis=owner_hypothesis,
                    )
                )
                continue
            candidate_ms2_evidence = result.candidate_ms2_evidence
            product = (
                None
                if candidate_ms2_evidence is None
                else candidate_ms2_evidence.best_product_evidence
            )
            if (
                product is not None
                and product.scan_number is not None
                and _matches_target_precursor(
                    product.precursor_mz,
                    target.definition.target,
                )
            ):
                evidence = mint_cid_nl_evidence_ref(
                    processing_owners=context.owners,
                    sample=sample,
                    scan_number=product.scan_number,
                    precursor_mz=product.precursor_mz,
                    product_mz=product.product_mz,
                    configured_neutral_loss_da=_neutral_loss(target.definition.target),
                    observed_neutral_loss_da=product.observed_loss_da,
                    observed_loss_error_ppm=product.observed_loss_error_ppm,
                    scan_rt_min=product.scan_rt,
                    product_intensity=product.product_intensity,
                )
                evidence_refs = (evidence,)
                if (
                    observations
                    and candidate_ms2_evidence is not None
                    and candidate_ms2_evidence.nl_match
                ):
                    support = resolve_dda_cid_nl_peak_support_ref(
                        processing_owners=context.owners,
                        sample=sample,
                        canonical_evidence=evidence,
                        candidate_peaks=observations,
                        policy=_support_policy(target.definition.target),
                    )
                    support_refs = (support,)
        if ms1_only:
            raise RuntimeError(
                "MS1-only Target lost its retained acquisition candidate"
            )
        assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=context.owners,
            sample=sample,
            target=target,
            detector_invocation_id="targeted-production-retained-trace:v1",
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=target.definition.target.rt_min,
                query_rt_max=target.definition.target.rt_max,
                support_policy=_support_policy(target.definition.target),
                acquisition_supported=True,
                search_completed=True,
                scanned_event_count=len(evidence_refs),
                reason_code=None,
            ),
            candidate_peaks=observations,
            evidence_refs=evidence_refs,
            individual_support_refs=support_refs,
        )
        assessments.append((sample, assessment))
        if captured is not None:
            if not observations:
                capture_inputs.append(
                    CaptureWorkflowArguments(
                        candidate_id=captured.candidate_id,
                        trace=captured.trace,
                    )
                )
                continue
            hypotheses = compose_peak_hypotheses_from_assessment_universe(
                processing_owners=context.owners,
                sample=sample,
                assessment=assessment,
            )
            matches = tuple(
                hypothesis
                for hypothesis in hypotheses
                if observations[0] in hypothesis.peak_observation_refs
            )
            if len(matches) != 1:
                raise RuntimeError("Targeted assessment lost its captured observation")
            bound = bind_owner_backed_capture_hypothesis(
                captured,
                observation=observations[0],
                hypothesis=matches[0],
            )
            capture_inputs.append(
                CaptureWorkflowArguments(
                    candidate_id=bound.candidate_id,
                    trace=bound.trace,
                    owner_observation=observations[0],
                    owner_hypothesis=matches[0],
                )
            )
    assessment_tuple = tuple(
        sorted(
            assessments,
            key=lambda item: item[0].identity.sample_identity_id,
        )
    )
    alignment_config = AlignmentConfig()
    trajectory_policy = ModeTrackTrajectoryPolicy(
        trajectory_policy_version="anchor-trajectory-v1",
        band_policy_version="fixed-band-v1",
        prediction_half_width_min=2 / 60,
        max_interpolation_gap=10,
        max_extrapolation_gap=10,
    )
    workflow_artifact: EvidenceArtifactRef
    if assessment_tuple:
        workflow_artifact = assessment_tuple[0][1].artifact
    else:
        captured_artifact = next(
            (
                item.owner_observation.artifact
                for item in capture_inputs
                if item.owner_observation is not None
            ),
            None,
        )
        workflow_artifact = (
            captured_artifact
            if captured_artifact is not None
            else _mint_no_observation_capture_artifact(
                context.owners,
                target,
                tuple(capture_inputs),
            )
        )
    work_unit_key = f"targeted-workflow:{target.definition.target_definition_id}"
    runtime = start_runtime_attempt(
        attempt=context.owners.attempt,
        work_unit_keys=(work_unit_key,),
        owner_completeness_attestation="targeted-production-acquisition:v1",
    )
    terminal = execute_alignment_workflow(
        TargetWorkflowRequest(
            scope=scope,
            processing_request=context.owners.request,
            processing_attempt=context.owners.attempt,
            runtime=WorkflowRuntimeInvocation(
                runtime=runtime,
                reservations=(),
                worker_batch_provider=lambda _aggregate: RuntimeWorkerBatchReceipt(
                    settlements=(
                        mint_work_unit_settlement(
                            attempt=context.owners.attempt,
                            work_unit_key=work_unit_key,
                            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                            owner_result_ref=workflow_artifact,
                        ),
                    ),
                    artifact_registrations=(
                        RuntimeArtifactRegistrationReceipt(
                            work_unit_key=work_unit_key,
                            artifact=workflow_artifact,
                        ),
                    ),
                ),
                telemetry_provider=_runtime_telemetry,
                persister=lambda receipt: receipt,
            ),
        ),
        TargetWorkflowAdapter(
            capture_inputs=tuple(capture_inputs),
            mode_track=ModeTrackWorkflowArguments(
                sample_assessments=assessment_tuple,
                alignment_config=alignment_config,
                trajectory_policy=trajectory_policy,
                retain_observation_only_candidates=True,
            ),
            repair=None,
            predecessor_cell_observations=(),
        ),
    )
    if type(terminal) is not CompletedTargetWorkflowResult:
        raise RuntimeError(
            "Targeted workflow failed before publication: "
            f"stage={getattr(terminal, 'stage', None)}; "
            f"reason={getattr(terminal, 'failure_message', None)}"
        )
    return terminal.application_result


def _mint_no_observation_capture_artifact(
    owners: ProcessingOwnerReferences,
    target: ResolvedTargetDefinition,
    captures: tuple[CaptureWorkflowArguments, ...],
) -> EvidenceArtifactRef:
    if not captures or any(
        item.owner_observation is not None or item.owner_hypothesis is not None
        for item in captures
    ):
        raise RuntimeError(
            "no-observation artifact requires a non-empty unselected capture population"
        )
    payload = canonical_identity_basis_bytes(
        {
            "schema_version": "targeted_no_observation_capture_v1",
            "processing_request_id": owners.request.processing_request_id,
            "processing_attempt_id": owners.attempt.processing_attempt_id,
            "target_definition_id": target.definition.target_definition_id,
            "captures": [
                {
                    "candidate_id": item.candidate_id,
                    "sample_name": item.trace.sample_name,
                    "acquisition_source_id": item.trace.acquisition_source_id,
                    "scan_count": item.trace.scan_count,
                }
                for item in captures
            ],
        }
    )
    return _mint_evidence_artifact_ref(
        producer_processing_request_id=owners.request.processing_request_id,
        producer_processing_attempt_id=owners.attempt.processing_attempt_id,
        semantic_contract=_NO_OBSERVATION_ARTIFACT_CONTRACT,
        payload_bytes=payload,
    )


def _capture_binding(
    owners: ProcessingOwnerReferences,
    target: ResolvedTargetDefinition,
    sample: ResolvedCanonicalSampleIdentity,
    result: ExtractionResult,
    candidate_id: str,
    *,
    method_subject_id: str,
    retained_only: bool,
) -> CaptureCandidateBinding:
    trace = _target_capture_trace(
        owners,
        sample,
        result,
        target.definition.target,
    )
    target_value = target.definition.target
    retained_integration = (
        None
        if result.selected_hypothesis is None
        else result.selected_hypothesis.integration
    )
    if retained_integration is not None and not _matches_target_rt(
        retained_integration.rt_apex_min,
        target_value,
    ):
        retained_integration = None
    preferred_rt = result.reported_rt
    if preferred_rt is None or not _matches_target_rt(preferred_rt, target_value):
        preferred_rt = (target_value.rt_min + target_value.rt_max) / 2
    source = resolve_request_acquisition_source(
        processing_owners=owners,
        sample=sample,
    )
    return CaptureCandidateBinding(
        candidate_id=candidate_id,
        method_subject_id=method_subject_id,
        sample_identity_id=sample.identity.sample_identity_id,
        sample_name=sample.metadata.sample_name,
        acquisition_source_id=source.acquisition_source_id,
        query_mz=trace.mz,
        query_rt_min=trace.rt_min,
        query_rt_max=trace.rt_max,
        query_ppm_tol=trace.ppm_tol,
        preferred_rt=preferred_rt,
        retained_selected_integration=retained_integration,
        selection_policy="retained_only" if retained_only else "capture_default",
    )


def _owner_bound_trace(
    owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    result: ExtractionResult,
) -> Trace:
    trace = result.acquisition_trace
    if trace is None:
        raise RuntimeError("Targeted result has no retained acquisition trace")
    source = resolve_request_acquisition_source(
        processing_owners=owners,
        sample=sample,
    )
    return replace(
        trace,
        sample_name=sample.metadata.sample_name,
        source=_TRACE_SOURCE,
        acquisition_source_id=source.acquisition_source_id,
    )


def _target_capture_trace(
    owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    result: ExtractionResult,
    target: Target,
) -> Trace:
    trace = _owner_bound_trace(owners, sample, result)
    retained = result.selected_hypothesis
    if retained is not None and _matches_target_rt(
        retained.integration.rt_apex_min,
        target,
    ):
        return trace
    mask = (trace.rt >= target.rt_min) & (trace.rt <= target.rt_max)
    return Trace.from_arrays(
        sample_name=trace.sample_name,
        mz=trace.mz,
        rt=trace.rt[mask],
        intensity=trace.intensity[mask],
        rt_min=target.rt_min,
        rt_max=target.rt_max,
        ppm_tol=trace.ppm_tol,
        source=trace.source,
        acquisition_source_id=trace.acquisition_source_id,
    )


def _candidate_id(
    target: ResolvedTargetDefinition,
    sample: ResolvedCanonicalSampleIdentity,
) -> str:
    return ":".join(
        (
            "targeted-capture-v1",
            target.definition.target_definition_id,
            sample.identity.sample_identity_id,
        )
    )


def _support_policy(target: Target) -> DdaCidNlPeakSupportPolicy:
    return DdaCidNlPeakSupportPolicy(
        precursor_tolerance_da=max(target.mz * target.ppm_tol / 1_000_000, 1e-9),
        neutral_loss_tolerance_ppm=target.nl_ppm_max or 20.0,
    )


def _matches_target_precursor(observed_mz: float, target: Target) -> bool:
    return abs(observed_mz - target.mz) <= max(
        target.mz * target.ppm_tol / 1_000_000,
        1e-9,
    )


def _matches_target_rt(observed_rt: float, target: Target) -> bool:
    return target.rt_min <= observed_rt <= target.rt_max


def _neutral_loss(target: Target) -> float:
    if target.neutral_loss_da is None:
        raise RuntimeError("Targeted production requires a neutral-loss definition")
    return target.neutral_loss_da


def _target_snapshot(owners: ProcessingOwnerReferences) -> TargetListSnapshot:
    snapshot = owners.target_snapshot
    if snapshot is None:
        raise RuntimeError("Targeted production lost its target snapshot")
    return snapshot


def _runtime_telemetry(
    coordinator: object,
) -> RuntimeTelemetrySamples:
    terminal = cast(Any, coordinator)
    event_count = runtime_observation_event_count(terminal)
    worker_keys = runtime_started_worker_keys(terminal)
    reason = "not_collected_by_targeted_foreground_runner"
    return RuntimeTelemetrySamples(
        event_monotonic_offsets_ns=tuple(range(event_count)),
        artifact_io=RuntimeArtifactIoSample(0, 0, 0, 0),
        process_tree_peak_rss=RuntimeProcessTreePeakRssSample(
            availability=RuntimeMetricAvailability.UNSUPPORTED,
            reason=reason,
        ),
        worker_peak_rss=tuple(
            RuntimeWorkerPeakRssSample(
                worker_key=worker_key,
                availability=RuntimeMetricAvailability.UNSUPPORTED,
                reason=reason,
            )
            for worker_key in worker_keys
        ),
    )


__all__ = ["execute_targeted_production_workflow"]
