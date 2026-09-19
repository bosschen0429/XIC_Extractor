"""Production Targeted adapter from retained acquisition evidence to #312."""

from __future__ import annotations

import hashlib
import pickle
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import InitVar, dataclass, replace
from pathlib import Path
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
from xic_extractor.config import ConfigError, ExtractionConfig, Target
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
    RuntimeArtifactRegistrationReceipt,
    RuntimeProcessTreePeakRssSample,
    RuntimeTelemetrySamples,
    RuntimeWorkerBatchReceipt,
    RuntimeWorkerPeakRssSample,
    mint_work_unit_settlement,
    runtime_started_worker_keys,
    start_runtime_attempt,
)
from xic_extractor.extraction.targeted_inputs import (
    ResolvedTargetedInputs,
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
    SampleLocalCapture,
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
    ProcessingRequestIdentity,
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

_IDENTITY_NAMESPACE = "targeted-production"
_TRACE_SOURCE = "xic_extractor.targeted_production_workflow:retained-acquisition"
_NO_OBSERVATION_ARTIFACT_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.extraction.targeted_production_workflow",
    semantic_role="retained_no_observation_capture_population",
    schema_version="targeted_no_observation_capture",
    validation_status="production_input",
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf",
)
_COMPLETED_WORKFLOW_MINT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class TargetedProductionInputSnapshot:
    """Exact mutable input state observed at one production boundary."""

    config: ExtractionConfig
    raw_paths: tuple[Path, ...]
    raw_file_states: tuple[tuple[int, int, int, int, int], ...]
    resolved_inputs: ResolvedTargetedInputs
    target_snapshot: TargetListSnapshot
    processing_request: ProcessingRequestIdentity


@dataclass(frozen=True, slots=True)
class PreparedTargetedProductionWorkflow:
    """Exact owner-bound inputs reused by service validation and execution."""

    owners: ProcessingOwnerReferences
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    targets: tuple[ResolvedTargetDefinition, ...]
    results_by_sample: tuple[tuple[ResolvedCanonicalSampleIdentity, FileResult], ...]
    raw_stems: frozenset[str]
    acquisition_file_ids: tuple[int, ...]
    acquisition_snapshot: RunOutput
    acquisition_snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class CompletedTargetedProductionWorkflow:
    """One exact prepared workflow and the results produced by its attempts."""

    prepared: PreparedTargetedProductionWorkflow
    target_contexts: tuple[PreparedTargetedProductionWorkflow, ...]
    finalized_results: tuple[FinalizedTargetProductionResult, ...]
    _mint_token: InitVar[object] = None

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _COMPLETED_WORKFLOW_MINT_TOKEN:
            raise TypeError(
                "CompletedTargetedProductionWorkflow must be minted by its owner"
            )
        if len(self.target_contexts) != len(self.finalized_results):
            raise RuntimeError(
                "completed Targeted workflow crossed its execution population"
            )

    def __len__(self) -> int:
        return len(self.finalized_results)

    def __iter__(self) -> Iterator[FinalizedTargetProductionResult]:
        return iter(self.finalized_results)

    def __getitem__(self, index: int) -> FinalizedTargetProductionResult:
        return self.finalized_results[index]


def execute_targeted_production_workflow(
    request: TargetedApplicationRequest,
    acquisition_evidence: RunOutput,
) -> CompletedTargetedProductionWorkflow:
    """Produce one finalized #312 result per declared Target, in row order."""

    return complete_targeted_production_workflow(request, acquisition_evidence)


def complete_targeted_production_workflow(
    request: TargetedApplicationRequest,
    acquisition_evidence: RunOutput,
) -> CompletedTargetedProductionWorkflow:
    """Execute and retain the exact attempt-bound publication receipt."""

    prepared = prepare_targeted_production_workflow(request, acquisition_evidence)
    return complete_prepared_targeted_production_workflow(request, prepared)


def execute_prepared_targeted_production_workflow(
    request: TargetedApplicationRequest,
    prepared: PreparedTargetedProductionWorkflow,
) -> tuple[FinalizedTargetProductionResult, ...]:
    """Execute an already owner-bound production population."""

    return complete_prepared_targeted_production_workflow(
        request,
        prepared,
    ).finalized_results


def complete_prepared_targeted_production_workflow(
    request: TargetedApplicationRequest,
    prepared: PreparedTargetedProductionWorkflow,
) -> CompletedTargetedProductionWorkflow:
    """Execute one prepared population and seal its exact per-Target attempts."""

    target_contexts = tuple(
        _context_for_target(prepared, index)
        for index, _target in enumerate(prepared.targets)
    )
    finalized_results = tuple(
        _execute_target(context, request, target)
        for context, target in zip(
            target_contexts,
            prepared.targets,
            strict=True,
        )
    )
    return CompletedTargetedProductionWorkflow(
        prepared=prepared,
        target_contexts=target_contexts,
        finalized_results=finalized_results,
        _mint_token=_COMPLETED_WORKFLOW_MINT_TOKEN,
    )


def prepare_targeted_production_workflow(
    request: TargetedApplicationRequest,
    acquisition_evidence: RunOutput,
    *,
    expected_inputs: TargetedProductionInputSnapshot | None = None,
) -> PreparedTargetedProductionWorkflow:
    """Bind acquisition rows to exact RAW, request, sample, and Target owners."""

    current_inputs = (
        snapshot_targeted_production_inputs(request)
        if expected_inputs is None
        else _validate_targeted_production_input_snapshot(request, expected_inputs)
    )
    raw_paths = list(current_inputs.raw_paths)
    if len(raw_paths) != len(acquisition_evidence.file_results):
        raise RuntimeError("Targeted acquisition returned partial sample coverage")
    acquisition_file_ids = tuple(id(item) for item in acquisition_evidence.file_results)
    acquisition_snapshot = deepcopy(acquisition_evidence)
    acquisition_snapshot_sha256 = _run_output_sha256(acquisition_snapshot)
    results_by_raw_path = _bind_results_to_raw_paths(
        raw_paths,
        acquisition_snapshot.file_results,
    )
    resolved_inputs = current_inputs.resolved_inputs
    target_snapshot = current_inputs.target_snapshot
    processing_request = current_inputs.processing_request
    rows = tuple(
        SampleMetadata(
            sample_name=file_result.sample_name,
            raw_stem=raw_path.stem,
            injection_order=resolved_inputs.injection_order[raw_path.stem],
            group=file_result.group or "",
        )
        for raw_path, file_result in results_by_raw_path
    )
    sample_snapshot = mint_sample_metadata_snapshot(
        rows,
        identity_namespace=_IDENTITY_NAMESPACE,
    )
    samples = tuple(
        resolve_canonical_sample_identity(sample_snapshot, row.raw_stem) for row in rows
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
    return PreparedTargetedProductionWorkflow(
        owners=owners,
        samples=samples,
        targets=resolved_targets,
        results_by_sample=tuple(
            (sample, file_result)
            for sample, (_, file_result) in zip(
                samples,
                results_by_raw_path,
                strict=True,
            )
        ),
        raw_stems=frozenset(path.stem for path in raw_paths),
        acquisition_file_ids=acquisition_file_ids,
        acquisition_snapshot=acquisition_snapshot,
        acquisition_snapshot_sha256=acquisition_snapshot_sha256,
    )


def acquisition_snapshot_for_publication(
    prepared: PreparedTargetedProductionWorkflow,
) -> RunOutput:
    """Return an isolated snapshot only while the owner-sealed bytes remain exact."""

    if _run_output_sha256(prepared.acquisition_snapshot) != (
        prepared.acquisition_snapshot_sha256
    ):
        raise RuntimeError("prepared Targeted acquisition snapshot was mutated")
    return deepcopy(prepared.acquisition_snapshot)


def _run_output_sha256(output: RunOutput) -> str:
    return hashlib.sha256(pickle.dumps(output, protocol=5)).hexdigest()


def snapshot_targeted_production_inputs(
    request: TargetedApplicationRequest,
) -> TargetedProductionInputSnapshot:
    """Fingerprint every mutable scientific input for a production phase."""

    raw_paths = resolve_targeted_raw_paths(request.config)
    if not raw_paths:
        raise ConfigError("No RAW files were found for Targeted extraction.")
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
    raw_file_states = _raw_file_states(raw_paths)
    processing_request = build_targeted_processing_request_identity(
        request.config,
        target_snapshot=target_snapshot,
        raw_paths=raw_paths,
        resolved_inputs=resolved_inputs,
        should_cancel=request.should_stop,
    )
    if _raw_file_states(raw_paths) != raw_file_states:
        raise RuntimeError("Targeted production inputs changed during fingerprinting")
    return TargetedProductionInputSnapshot(
        config=deepcopy(request.config),
        raw_paths=tuple(raw_paths),
        raw_file_states=raw_file_states,
        resolved_inputs=resolved_inputs,
        target_snapshot=target_snapshot,
        processing_request=processing_request,
    )


def _validate_targeted_production_input_snapshot(
    request: TargetedApplicationRequest,
    expected: TargetedProductionInputSnapshot,
) -> TargetedProductionInputSnapshot:
    raw_paths = resolve_targeted_raw_paths(request.config)
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
    if (
        request.config != expected.config
        or tuple(raw_paths) != expected.raw_paths
        or _raw_file_states(raw_paths) != expected.raw_file_states
        or resolved_inputs != expected.resolved_inputs
        or target_snapshot != expected.target_snapshot
    ):
        raise RuntimeError("Targeted production inputs changed during acquisition")
    return expected


def _raw_file_states(
    raw_paths: list[Path],
) -> tuple[tuple[int, int, int, int, int], ...]:
    return tuple(
        (
            state.st_dev,
            state.st_ino,
            state.st_size,
            state.st_mtime_ns,
            state.st_ctime_ns,
        )
        for state in (path.stat() for path in raw_paths)
    )


def _bind_results_to_raw_paths(
    raw_paths: list[Path],
    file_results: list[FileResult],
) -> tuple[tuple[Path, FileResult], ...]:
    results_by_sample: dict[str, FileResult] = {}
    for file_result in file_results:
        if file_result.error:
            raise RuntimeError(
                "Targeted RAW acquisition failed for "
                f"{file_result.sample_name}: {file_result.error}"
            )
        if file_result.sample_name in results_by_sample:
            raise RuntimeError(
                "Targeted acquisition returned duplicate sample identities"
            )
        results_by_sample[file_result.sample_name] = file_result
    raw_stems = {path.stem for path in raw_paths}
    result_names = set(results_by_sample)
    if result_names != raw_stems:
        raise RuntimeError(
            "Targeted acquisition sample identities do not match resolved RAW inputs"
        )
    return tuple((path, results_by_sample[path.stem]) for path in raw_paths)


def _context_for_target(
    context: PreparedTargetedProductionWorkflow,
    target_index: int,
) -> PreparedTargetedProductionWorkflow:
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
    context: PreparedTargetedProductionWorkflow,
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
        if extraction.target_label != target.definition.target.label:
            raise RuntimeError(
                "Targeted acquisition result crossed its declared Target identity"
            )
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
    completed_captures: list[SampleLocalCapture] = []
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
                if owner_hypothesis is not None:
                    captured = bind_owner_backed_capture_hypothesis(
                        captured,
                        observation=observations[0],
                        hypothesis=owner_hypothesis,
                    )
                completed_captures.append(captured)
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
            if product is not None:
                acquisition_source = resolve_request_acquisition_source(
                    processing_owners=context.owners,
                    sample=sample,
                )
                if (
                    product.acquisition_source_id
                    != acquisition_source.acquisition_source_id
                ):
                    raise RuntimeError(
                        "Targeted MS2 evidence crossed the resolved acquisition source"
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
                    and observations[0].content.integration.rt_left_min
                    <= product.scan_rt
                    <= observations[0].content.integration.rt_right_min
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
            detector_invocation_id="targeted-production-retained-trace",
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
                completed_captures.append(captured)
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
            completed_captures.append(bound)
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
        trajectory_policy_version="anchor-trajectory",
        band_policy_version="fixed-band",
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
    # RAW acquisition has already completed; this attempt acknowledges its
    # retained artifact handoff before scientific composition begins.
    runtime = start_runtime_attempt(
        attempt=context.owners.attempt,
        work_unit_keys=(work_unit_key,),
        owner_completeness_attestation="targeted-retained-acquisition-handoff",
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
            predecessor_cell_observations=(),
            completed_capture_handoff=tuple(completed_captures),
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
            "schema_version": "targeted_no_observation_capture",
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
    if trace.sample_name != sample.metadata.sample_name:
        raise RuntimeError(
            "Targeted acquisition trace crossed the canonical sample identity"
        )
    if trace.acquisition_source_id not in {"", source.acquisition_source_id}:
        raise RuntimeError(
            "Targeted acquisition trace crossed the resolved acquisition source"
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
            "targeted-capture",
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
    worker_keys = runtime_started_worker_keys(terminal)
    reason = "not_collected_by_targeted_foreground_runner"
    return RuntimeTelemetrySamples(
        event_monotonic_offsets_ns=None,
        artifact_io=None,
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
        unavailable_reason=reason,
    )


__all__ = ["execute_targeted_production_workflow"]
