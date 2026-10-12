"""Production Targeted adapter from retained acquisition evidence to #312."""

from __future__ import annotations

import hashlib
import pickle
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import InitVar, dataclass, replace
from pathlib import Path
from typing import Any, cast

from xic_extractor.alignment.cell_observations import (
    CidNlCellGroupContext,
    CidNlCellGroupSupport,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.completion import (
    AlignmentWorkflowContractError,
    CaptureWorkflowArguments,
    TargetWorkflowAdapter,
    TargetWorkflowRequest,
    WorkflowStage,
    _WorkflowStageFailure,
    complete_analysis,
    validate_completion_inputs,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillCellDecision,
    assess_conservative_backfill_group,
    map_conservative_backfill_candidates,
    mint_conservative_backfill_candidate_universe,
    plan_conservative_backfill_targets,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.peak_trajectories import PeakTrajectory
from xic_extractor.applications.targeted.application import (
    FinalizedTargetProductionResult,
    TargetedApplicationRequest,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
    resolve_signal_processing_profile,
)
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    TargetListSnapshot,
    mint_target_list_snapshot,
    resolve_target_definition,
)
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    mint_artifact_semantic_contract_ref,
    mint_evidence_artifact_ref,
)
from xic_extractor.execution._lifecycle import (
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution._observability import RuntimeMetricAvailability
from xic_extractor.execution.attempt import (
    RuntimeArtifactRegistrationReceipt,
    RuntimeProcessTreePeakRssSample,
    RuntimeTelemetrySamples,
    RuntimeWorkerBatchReceipt,
    RuntimeWorkerPeakRssSample,
    execute_runtime_attempt,
    mint_work_unit_settlement,
    runtime_started_worker_keys,
    start_runtime_attempt,
)
from xic_extractor.extraction.result_models import (
    ExtractionResult,
    FileResult,
    RunOutput,
)
from xic_extractor.extraction.target_extraction import credible_istd_anchor_rt
from xic_extractor.extraction.targeted_inputs import (
    ResolvedTargetedInputs,
    bind_targeted_physical_capture_request,
    build_targeted_processing_request_identity,
    resolve_targeted_inputs,
    resolve_targeted_raw_paths,
)
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
    CidNlMethodAssessmentRef,
    mint_cid_nl_method_assessment_ref,
    resolve_cid_nl_method_relations,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    cid_nl_method_subject_from_target,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportRef,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    mint_peak_observation_ref,
    physical_peak_observation_integration,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
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
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)

_IDENTITY_NAMESPACE = "targeted-production"
_TRACE_SOURCE = "xic_extractor.targeted_production_workflow:retained-acquisition"
_NO_OBSERVATION_ARTIFACT_CONTRACT = mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.applications.targeted.production_workflow",
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
    metadata_by_stem = {row.raw_stem: row for row in resolved_inputs.sample_metadata}
    rows = tuple(
        replace(
            metadata_by_stem[raw_path.stem],
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
    resolved_targets = tuple(
        resolve_target_definition(target_snapshot, target.label)
        for target in request.targets
    )
    profile = _targeted_signal_processing_profile(request.config)
    physical_evaluations = {}
    for sample, (_, file_result) in zip(
        samples,
        results_by_raw_path,
        strict=True,
    ):
        for target in resolved_targets:
            result = file_result.results.get(target.definition.target.label)
            if result is not None and result.physical_evaluation is not None:
                physical_evaluations[_candidate_id(target, sample)] = (
                    result.physical_evaluation
                )
                for index, evaluation in enumerate(
                    result.candidate_physical_evaluations
                ):
                    physical_evaluations[
                        f"{_candidate_id(target, sample)}:universe:{index}"
                    ] = evaluation
    if any(
        evaluation.prepared.profile_id != profile.profile_id
        for evaluation in physical_evaluations.values()
    ):
        raise RuntimeError("Targeted acquisition crossed its configured signal profile")
    processing_request = bind_targeted_physical_capture_request(
        current_inputs.processing_request,
        profile=profile,
        evaluations=physical_evaluations,
    )
    attempt = issue_processing_attempt(processing_request)
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
        if extraction.physical_evaluation is not None:
            candidates.append((sample, extraction, _candidate_id(target, sample)))
    bindings = tuple(
        _capture_binding(
            context.owners,
            target,
            sample,
            result,
            candidate_id,
            method_subject_id=method_subject_id,
        )
        for sample, result, candidate_id in candidates
    )
    scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=context.owners.request,
        samples=context.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=request.config,
            signal_processing_profile=_targeted_signal_processing_profile(
                request.config
            ),
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
    for sample, file_result in context.results_by_sample:
        result = file_result.results[target.definition.target.label]
        candidate = candidate_by_sample.get(sample.identity.sample_identity_id)
        observations: tuple[PeakObservationRef, ...] = ()
        captured = None
        selected_observation = None
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
            selected_observation = observations[0] if observations else None
            all_observations = []
            for evaluation in result.candidate_physical_evaluations:
                if (
                    evaluation is result.physical_evaluation
                    and selected_observation is not None
                ):
                    all_observations.append(selected_observation)
                else:
                    all_observations.append(
                        mint_peak_observation_ref(
                            processing_owners=context.owners,
                            sample=sample,
                            trace=trace,
                            integration=physical_peak_observation_integration(
                                evaluation
                            ),
                            selection_intensity=(
                                evaluation.retained_prepared_signal.positive_signal
                                if evaluation.retained_prepared_signal is not None
                                else None
                            ),
                            resolver_mode=request.config.resolver_mode,
                            proposal_sources=("canonical_physical_segment",),
                            physical_evaluation=evaluation,
                            signal_processing_profile=scope.method_constraints.signal_processing_profile,
                        )
                    )
            observations = tuple(all_observations)
        if ms1_only:
            raise RuntimeError(
                "MS1-only Target lost its retained acquisition candidate"
            )
        if result.cid_nl_evaluation_receipt is None or result.cid_nl_relations is None:
            raise RuntimeError("Targeted acquisition lost its complete CID-NL universe")
        acquisition_source = resolve_request_acquisition_source(
            processing_owners=context.owners, sample=sample
        )
        evidence = []
        for product in result.cid_nl_products:
            if (
                product.acquisition_source_id
                != acquisition_source.acquisition_source_id
            ):
                raise RuntimeError(
                    "Targeted MS2 evidence crossed the resolved acquisition source"
                )
            if product.scan_number is None:
                raise RuntimeError(
                    "Targeted MS2 evidence lost its acquired scan identity"
                )
            evidence.append(
                mint_cid_nl_evidence_ref(
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
                    precursor_mz_basis=product.precursor_mz_basis,
                    scan_precursor_mz=product.scan_precursor_mz,
                    precursor_metadata=product.precursor_metadata,
                )
            )
        evidence_refs = tuple(evidence)
        support_refs, set_refs = resolve_cid_nl_method_relations(
            processing_owners=context.owners,
            sample=sample,
            evidence_refs=evidence_refs,
            candidate_peaks=observations,
            policy=result.cid_nl_evaluation_receipt.support_policy,
            relations=result.cid_nl_relations,
        )
        assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=context.owners,
            sample=sample,
            target=target,
            detector_invocation_id="targeted-production-retained-trace",
            evaluation_receipt=result.cid_nl_evaluation_receipt,
            candidate_peaks=observations,
            evidence_refs=evidence_refs,
            individual_support_refs=support_refs,
            set_relation_refs=set_refs,
        )
        assessments.append((sample, assessment))
        if captured is not None:
            if selected_observation is None:
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
                if selected_observation in hypothesis.peak_observation_refs
            )
            if len(matches) != 1:
                raise RuntimeError("Targeted assessment lost its captured observation")
            bound = bind_owner_backed_capture_hypothesis(
                captured,
                observation=selected_observation,
                hypothesis=matches[0],
            )
            completed_captures.append(bound)
            capture_inputs.append(
                CaptureWorkflowArguments(
                    candidate_id=bound.candidate_id,
                    trace=bound.trace,
                    owner_observation=selected_observation,
                    owner_hypothesis=matches[0],
                )
            )
    # Direct CID/NL observations freeze the reference group. Missing-sample
    # observations never vote for their own Backfill eligibility.
    alignment_config = AlignmentConfig()
    backfill_decisions: list[ConservativeBackfillCellDecision] = []
    if (
        target.definition.target.is_istd or target.definition.target.istd_pair
    ) and assessments:
        assessment_by_sample = {
            s.identity.sample_identity_id: a for s, a in assessments
        }
        sample_by_id = {s.identity.sample_identity_id: s for s in context.samples}
        direct = tuple(
            CidNlCellGroupSupport(
                sample=sample_by_id[
                    item.owner_observation.content.canonical_sample_identity_id
                ],
                assessment=assessment_by_sample[
                    item.owner_observation.content.canonical_sample_identity_id
                ],
                hypothesis=item.owner_hypothesis,
            )
            for item in capture_inputs
            if item.owner_hypothesis is not None
            and item.owner_observation is not None
            and item.owner_hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
        )
        groups = (
            construct_cross_sample_peak_group_hypotheses(
                tuple(
                    owner_from_cid_nl_assessment_support(
                        sample=support.sample,
                        assessment=support.assessment,
                        hypothesis=support.hypothesis,
                    )
                    for support in direct
                ),
                config=alignment_config,
            )
            if direct
            else ()
        )
        # A fixed Target with competing confirmed groups cannot acquire an
        # identity merely by choosing one of their recovery windows.
        plans = (
            plan_conservative_backfill_targets(
                groups, sample_population=context.samples
            )
            if len(groups) == 1
            else ()
        )
        paired_observations = []
        if plans:
            for sample, file_result in context.results_by_sample:
                pair = file_result.results.get(target.definition.target.istd_pair)
                if pair is None or credible_istd_anchor_rt(pair) is None:
                    continue
                pair_evaluation = pair.physical_evaluation
                if (
                    pair_evaluation is None
                    or pair_evaluation.retained_prepared_signal is None
                ):
                    continue
                paired_observations.append(
                    mint_peak_observation_ref(
                        processing_owners=context.owners,
                        sample=sample,
                        trace=_owner_bound_trace(context.owners, sample, pair),
                        integration=physical_peak_observation_integration(
                            pair_evaluation
                        ),
                        selection_intensity=pair_evaluation.retained_prepared_signal.positive_signal,
                        resolver_mode=request.config.resolver_mode,
                        proposal_sources=("canonical_physical_segment",),
                        physical_evaluation=pair_evaluation,
                        signal_processing_profile=scope.method_constraints.signal_processing_profile,
                    )
                )
        pair_rts = {
            sample_by_id[
                peak.content.canonical_sample_identity_id
            ].metadata.raw_stem: peak.content.integration.rt_apex_min
            for peak in paired_observations
        }
        for group, missing_ids in plans:
            group_context = CidNlCellGroupContext(
                group=group,
                supports=tuple(
                    sorted(
                        direct,
                        key=lambda support: support.sample.identity.sample_identity_id,
                    )
                ),
                config=alignment_config,
                processing_attempt=context.owners.attempt,
            )
            direct_by_sample = {
                support.sample.identity.sample_identity_id: support
                for support in direct
            }
            cells = tuple(
                mint_cell_observation_ref(
                    processing_owners=context.owners,
                    sample=sample,
                    target=target,
                    group_context=group_context,
                    method_assessment=assessment,
                    peak_observation=(
                        direct_by_sample[
                            sample.identity.sample_identity_id
                        ].hypothesis.peak_observation_refs[0]
                        if sample.identity.sample_identity_id in direct_by_sample
                        else None
                    ),
                    peak_hypothesis=(
                        direct_by_sample[sample.identity.sample_identity_id].hypothesis
                        if sample.identity.sample_identity_id in direct_by_sample
                        else None
                    ),
                    unassigned_support=(
                        assessment.content.method_result == "SUPPORT"
                        and sample.identity.sample_identity_id not in direct_by_sample
                    ),
                )
                for sample, assessment in assessments
            )
            facts = assess_conservative_backfill_group(
                cells, sample_population=context.samples
            )
            trajectory = PeakTrajectory(
                group.owner_ids,
                tuple(
                    (
                        support.sample.metadata.raw_stem,
                        support.hypothesis.integration.rt_apex_min,
                    )
                    for support in direct
                ),
            )
            locations = trajectory.backfill_locations(
                tuple(
                    sample_by_id[sample_id].metadata.raw_stem
                    for sample_id in sorted(missing_ids)
                ),
                reference_samples=frozenset(
                    support.sample.metadata.raw_stem for support in direct
                ),
                injection_order={
                    s.metadata.raw_stem: s.metadata.injection_order
                    for s in context.samples
                    if s.metadata.injection_order is not None
                },
                sample_cohorts={
                    s.metadata.raw_stem: s.metadata.batch_id for s in context.samples
                },
                rt_warp=None,
                backfill_half_window_min=request.config.nl_rt_anchor_half_window_min,
                paired_istd_rts=pair_rts,
            )
            for sample_id in sorted(missing_ids):
                sample = sample_by_id[sample_id]
                if assessment_by_sample[sample_id].content.method_result != "HOLD":
                    continue
                location = locations[sample.metadata.raw_stem]
                if (
                    not target.definition.target.is_istd
                    and location.location.basis != "paired_istd"
                ):
                    continue
                universe = mint_conservative_backfill_candidate_universe(
                    processing_owners=context.owners,
                    sample=sample,
                    group_facts=(facts,),
                    candidate_assessment=assessment_by_sample[sample_id],
                    localization=location,
                    localization_sources=tuple(paired_observations),
                )
                backfill_decisions.extend(
                    map_conservative_backfill_candidates(
                        (facts,),
                        sample=sample,
                        candidate_universes=(universe,),
                    )
                )
        mapped = {
            decision.target_cell.content.canonical_sample_identity_id: (
                decision.matched_candidate.peak_observation
            )
            for decision in backfill_decisions
            if decision.matched_candidate is not None
        }
        replacements = {}
        for sample, result, candidate_id in candidates:
            recovery_observation = mapped.get(sample.identity.sample_identity_id)
            if (
                recovery_observation is None
                or recovery_observation.physical_evaluation
                is result.physical_evaluation
            ):
                continue
            index = next(
                i
                for i, evaluation in enumerate(result.candidate_physical_evaluations)
                if evaluation is recovery_observation.physical_evaluation
            )
            replacements[candidate_id] = _capture_binding(
                context.owners,
                target,
                sample,
                replace(
                    result, physical_evaluation=recovery_observation.physical_evaluation
                ),
                f"{candidate_id}:universe:{index}",
                method_subject_id=method_subject_id,
            )
        if replacements:
            bindings = tuple(
                replacements.get(binding.candidate_id, binding) for binding in bindings
            )
            scope = replace(
                scope,
                capture_bindings=bindings,
                declared_candidate_ids=tuple(
                    binding.candidate_id for binding in bindings
                ),
            )
            new_inputs = []
            new_captures = []
            for old in capture_inputs:
                binding = replacements.get(old.candidate_id)
                candidate_id = (
                    old.candidate_id if binding is None else binding.candidate_id
                )
                captured = capture_peak_hypotheses(
                    scope=scope, candidate_id=candidate_id, trace=old.trace
                )
                recovery_observation = old.owner_observation
                hypothesis = old.owner_hypothesis
                if binding is not None:
                    recovery_observation = mapped[binding.sample_identity_id]
                    hypothesis = compose_ms1_observation_peak_hypothesis(
                        processing_owners=context.owners,
                        sample=sample_by_id[binding.sample_identity_id],
                        peak_observation=recovery_observation,
                    )
                if recovery_observation is not None and hypothesis is not None:
                    captured = bind_owner_backed_capture_hypothesis(
                        captured,
                        observation=recovery_observation,
                        hypothesis=hypothesis,
                    )
                new_inputs.append(
                    CaptureWorkflowArguments(
                        candidate_id=candidate_id,
                        trace=old.trace,
                        owner_observation=recovery_observation,
                        owner_hypothesis=hypothesis,
                    )
                )
                new_captures.append(captured)
            capture_inputs, completed_captures = new_inputs, new_captures
    assessment_tuple = tuple(
        sorted(
            assessments,
            key=lambda item: item[0].identity.sample_identity_id,
        )
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
    return _complete_retained_target(
        TargetWorkflowRequest(
            scope=scope,
            processing_request=context.owners.request,
            processing_attempt=context.owners.attempt,
        ),
        TargetWorkflowAdapter(
            capture_inputs=tuple(capture_inputs),
            sample_assessments=assessment_tuple,
            alignment_config=alignment_config,
            predecessor_cell_observations=(),
            completed_capture_handoff=tuple(completed_captures),
            targeted_backfill_decisions=tuple(backfill_decisions),
        ),
        runtime=runtime,
        workflow_artifact=workflow_artifact,
        work_unit_key=work_unit_key,
    )


def _complete_retained_target(
    request: TargetWorkflowRequest,
    adapter: TargetWorkflowAdapter,
    *,
    runtime: AttemptRuntime,
    workflow_artifact: EvidenceArtifactRef,
    work_unit_key: str,
) -> FinalizedTargetProductionResult:
    """Acknowledge retained acquisition, then complete its exact owner evidence."""
    owners = validate_completion_inputs(request, adapter)
    if (
        runtime.manifest.attempt is not request.processing_attempt
        or runtime.history.attempt is not request.processing_attempt
    ):
        raise AlignmentWorkflowContractError(
            "workflow runtime crossed the exact processing attempt"
        )
    receipt = execute_runtime_attempt(
        runtime,
        (),
        worker_batch_provider=lambda _aggregate: RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=request.processing_attempt,
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
    )
    outcome = receipt.supervisor.terminal_outcome
    if outcome is not AttemptTerminalOutcome.SUCCEEDED:
        stage = (
            WorkflowStage.RUNTIME if outcome is AttemptTerminalOutcome.FAILED else None
        )
        raise RuntimeError(
            f"Targeted workflow failed before publication: stage={stage}; reason=None"
        )
    try:
        result = complete_analysis(request, adapter, processing_owners=owners)
    except _WorkflowStageFailure as failure:
        raise RuntimeError(
            "Targeted workflow failed before publication: "
            f"stage={failure.stage}; reason={failure.error}"
        ) from failure.error
    if type(result) is not FinalizedTargetProductionResult:
        raise RuntimeError("Targeted completion returned a crossed result")
    return result


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
    return mint_evidence_artifact_ref(
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
) -> CaptureCandidateBinding:
    trace = _target_capture_trace(
        owners,
        sample,
        result,
        target.definition.target,
    )
    physical_evaluation = result.physical_evaluation
    if physical_evaluation is None:
        raise RuntimeError("Targeted capture lost its acquisition physical evaluation")
    preferred_rt = physical_evaluation.question.query_rt_min
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
        retained_selected_integration=None,
        selection_policy="capture_default",
        physical_evaluation=physical_evaluation,
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
    if result.physical_evaluation is not None:
        return trace
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


def _matches_target_rt(observed_rt: float, target: Target) -> bool:
    return target.rt_min <= observed_rt <= target.rt_max


def _targeted_signal_processing_profile(
    config: ExtractionConfig,
) -> SignalProcessingProfile:
    return resolve_signal_processing_profile(
        signal_processing_method=config.signal_processing_method,
        signal_processing_window_points=config.signal_processing_window_points,
    )


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
