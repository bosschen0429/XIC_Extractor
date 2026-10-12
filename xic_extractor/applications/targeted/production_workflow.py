"""Complete Targeted acquisition into bound scientific and public result models."""

from __future__ import annotations

import hashlib
import pickle
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import InitVar, dataclass, replace
from pathlib import Path
from typing import Any, cast

from xic_extractor.alignment.completion import (
    AlignmentWorkflowContractError,
    CaptureWorkflowArguments,
    TargetWorkflowAdapter,
    TargetWorkflowRequest,
    WorkflowStage,
    WorkflowStageFailure,
    complete_analysis,
    validate_completion_inputs,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.result.application_result import (
    FinalizedTargetApplicationResult,
)
from xic_extractor.alignment.result.scientific_alignment import ObservedSearchSubject
from xic_extractor.applications.targeted.application import (
    FinalizedTargetProductionResult,
    TargetedApplicationError,
    TargetedApplicationRequest,
    TargetedOutputBoundaryError,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
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
from xic_extractor.extraction.targeted_capture_population import (
    build_targeted_capture_population,
    targeted_capture_candidate_id,
    targeted_signal_processing_profile,
)
from xic_extractor.extraction.targeted_cid_nl_assessment import (
    assess_targeted_cid_nl_samples,
)
from xic_extractor.extraction.targeted_inputs import (
    ResolvedTargetedInputs,
    bind_targeted_physical_capture_request,
    build_targeted_processing_request_identity,
    resolve_targeted_inputs,
    resolve_targeted_raw_paths,
)
from xic_extractor.extraction.targeted_istd_backfill import (
    resolve_targeted_istd_backfill,
)
from xic_extractor.extraction.targeted_product_projection import (
    build_targeted_product_projection,
)
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.output.targeted_product_model import (
    TargetedProductContext,
    TargetedProductProjection,
)
from xic_extractor.peak_detection.models import PeakResult
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
    canonical_identity_basis_bytes,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)

_IDENTITY_NAMESPACE = "targeted-production"
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
    """Admitted Targeted inputs and their exact acquisition identity bindings."""

    config: ExtractionConfig
    raw_paths: tuple[Path, ...]
    raw_file_states: tuple[tuple[int, int, int, int, int], ...]
    resolved_inputs: ResolvedTargetedInputs
    target_snapshot: TargetListSnapshot
    processing_request: ProcessingRequestIdentity

    def acquisition_sources_by_sample(self) -> dict[str, AcquisitionSourceExpectation]:
        """Project the admitted RAW identities for the acquisition runner."""

        request = self.processing_request
        sources_by_id = {
            source.acquisition_source_id: source
            for source in request.acquisition_sources
        }
        states_by_stem = {
            path.stem: state
            for path, state in zip(
                self.raw_paths,
                self.raw_file_states,
                strict=True,
            )
        }
        return {
            binding.adapter_input_key: AcquisitionSourceExpectation(
                source=sources_by_id[binding.acquisition_source_id],
                path_identity=states_by_stem[binding.adapter_input_key],
            )
            for binding in request.acquisition_bindings
        }


@dataclass(frozen=True, slots=True)
class _PreparedTargetedProductionWorkflow:
    """Owner-local acquisition population for per-Target scientific completion."""

    owners: ProcessingOwnerReferences
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    targets: tuple[ResolvedTargetDefinition, ...]
    results_by_sample: tuple[tuple[ResolvedCanonicalSampleIdentity, FileResult], ...]
    raw_stems: frozenset[str]
    acquisition_file_ids: tuple[int, ...]
    acquisition_snapshot: RunOutput


@dataclass(frozen=True, slots=True)
class CompletedTargetedProductionWorkflow:
    """Owner-completed science and the exact public result model it produced."""

    finalized_results: tuple[FinalizedTargetProductionResult, ...]
    _acquisition_file_ids: tuple[int, ...]
    _publication_snapshot: RunOutput
    _publication_snapshot_sha256: str
    _mint_token: InitVar[object] = None

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _COMPLETED_WORKFLOW_MINT_TOKEN:
            raise TypeError(
                "CompletedTargetedProductionWorkflow must be minted by its owner"
            )

    def publication_output(
        self,
        request: TargetedApplicationRequest,
        acquisition_evidence: RunOutput,
    ) -> RunOutput:
        """Bind a publication request and isolate its already-completed result view."""

        acquisition_file_ids = tuple(
            id(file_result) for file_result in acquisition_evidence.file_results
        )
        if acquisition_file_ids != self._acquisition_file_ids:
            raise TargetedOutputBoundaryError(
                "prepared Targeted workflow crossed the acquisition evidence"
            )
        if _run_output_sha256(self._publication_snapshot) != (
            self._publication_snapshot_sha256
        ):
            raise TargetedOutputBoundaryError(
                "completed Targeted publication snapshot was mutated"
            )
        _validate_target_population(request, self.finalized_results)
        return deepcopy(self._publication_snapshot)

    def __len__(self) -> int:
        return len(self.finalized_results)

    def __iter__(self) -> Iterator[FinalizedTargetProductionResult]:
        return iter(self.finalized_results)

    def __getitem__(self, index: int) -> FinalizedTargetProductionResult:
        return self.finalized_results[index]


def execute_targeted_production_workflow(
    request: TargetedApplicationRequest,
    acquisition_evidence: RunOutput,
    *,
    expected_inputs: TargetedProductionInputSnapshot | None = None,
) -> CompletedTargetedProductionWorkflow:
    """Complete the admitted Target population and seal its publication model."""

    prepared = _prepare_targeted_production_workflow(
        request, acquisition_evidence, expected_inputs=expected_inputs
    )
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
    _validate_finalized_result_population(
        request,
        finalized_results,
        expected_processing_request=prepared.owners.request,
        expected_samples=prepared.samples,
    )
    publication_snapshot = _build_publication_output(
        finalized_results, prepared.acquisition_snapshot
    )
    return CompletedTargetedProductionWorkflow(
        finalized_results=finalized_results,
        _acquisition_file_ids=prepared.acquisition_file_ids,
        _publication_snapshot=publication_snapshot,
        _publication_snapshot_sha256=_run_output_sha256(publication_snapshot),
        _mint_token=_COMPLETED_WORKFLOW_MINT_TOKEN,
    )


def _validate_finalized_result_population(
    request: TargetedApplicationRequest,
    results: tuple[FinalizedTargetProductionResult, ...],
    *,
    expected_processing_request: ProcessingRequestIdentity,
    expected_samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> None:
    if type(results) is not tuple or any(
        type(result) is not FinalizedTargetApplicationResult for result in results
    ):
        raise TargetedApplicationError(
            "finalized workflow must return an exact immutable Target result tuple"
        )
    _validate_target_population(request, results)
    request_ids = {
        result.processing_request.processing_request_id for result in results
    }
    sample_populations = {
        tuple(sample.identity.sample_identity_id for sample in result.canonical_samples)
        for result in results
    }
    if len(request_ids) != 1 or len(sample_populations) != 1:
        raise TargetedApplicationError(
            "finalized Target results crossed request or sample populations"
        )
    expected_sample_population = tuple(
        sorted(sample.identity.sample_identity_id for sample in expected_samples)
    )
    if any(
        result.processing_request != expected_processing_request
        or tuple(
            sorted(
                sample.identity.sample_identity_id
                for sample in result.canonical_samples
            )
        )
        != expected_sample_population
        for result in results
    ):
        raise TargetedApplicationError(
            "finalized Target results do not belong to the current acquisition"
        )


def _validate_target_population(
    request: TargetedApplicationRequest,
    results: tuple[FinalizedTargetProductionResult, ...],
) -> None:
    if len(results) != len(request.targets):
        raise TargetedApplicationError(
            "finalized workflow did not cover every declared Target"
        )
    for target, result in zip(request.targets, results, strict=True):
        finalized_target = (
            result.completed_projection.row_binding.target.definition.target
        )
        if finalized_target != target:
            raise TargetedApplicationError(
                "finalized Target result order differs from the declared Target list"
            )


def _build_publication_output(
    finalized_results: tuple[FinalizedTargetProductionResult, ...],
    acquisition_evidence: RunOutput,
) -> RunOutput:
    expected_samples = finalized_results[0].canonical_samples
    expected_sample_ids = tuple(
        sample.identity.sample_identity_id for sample in expected_samples
    )
    samples_by_raw_stem = {
        sample.metadata.raw_stem: sample for sample in expected_samples
    }
    if len(samples_by_raw_stem) != len(expected_samples):
        raise TargetedOutputBoundaryError(
            "finalized Targeted sample population contains duplicate RAW identities"
        )

    decisions: dict[tuple[str, str], Any] = {}
    target_ids_by_label: dict[str, str] = {}
    targets_by_label: dict[str, Target] = {}
    backfills = {}
    backfill_diagnostics = []
    for result in finalized_results:
        target = result.completed_projection.row_binding.target.definition
        target_label = target.target.label
        target_id = target.target_definition_id
        if target_label in target_ids_by_label:
            raise TargetedOutputBoundaryError(
                "finalized Targeted result population contains duplicate Targets"
            )
        target_ids_by_label[target_label] = target_id
        targets_by_label[target_label] = target.target
        for backfill in result.scientific_core.targeted_backfill_decisions:
            sample_id = backfill.target_cell.content.canonical_sample_identity_id
            backfills[(sample_id, target_id)] = backfill
        for projection_decision in result.completed_projection.decisions:
            subject = cast(Any, projection_decision.subject)
            sample_id = subject.sample.identity.sample_identity_id
            if sample_id not in expected_sample_ids:
                raise TargetedOutputBoundaryError(
                    "finalized Targeted decision crossed the canonical "
                    "sample population"
                )
            key = (sample_id, target_id)
            if key in decisions:
                raise TargetedOutputBoundaryError(
                    "finalized Targeted decision population contains duplicates"
                )
            decisions[key] = projection_decision

    files: list[FileResult] = []
    consumed: set[tuple[str, str]] = set()
    consumed_sample_ids: set[str] = set()
    for file_result in acquisition_evidence.file_results:
        sample = samples_by_raw_stem.get(file_result.sample_name)
        if sample is None:
            raise TargetedOutputBoundaryError(
                "acquisition evidence does not resolve to a canonical sample identity"
            )
        sample_id = sample.identity.sample_identity_id
        if sample_id in consumed_sample_ids:
            raise TargetedOutputBoundaryError(
                "acquisition evidence contains duplicate canonical samples"
            )
        consumed_sample_ids.add(sample_id)
        rendered: dict[str, ExtractionResult] = {}
        for target_label, extraction in file_result.results.items():
            if extraction.target_label != target_label:
                raise TargetedOutputBoundaryError(
                    "acquisition evidence crossed its declared Target identity"
                )
            resolved_target_id = target_ids_by_label.get(target_label)
            if resolved_target_id is None:
                raise TargetedOutputBoundaryError(
                    "acquisition evidence does not resolve to a finalized "
                    "Target identity"
                )
            key = (sample_id, resolved_target_id)
            render_decision = decisions.get(key)
            if render_decision is None:
                raise TargetedOutputBoundaryError(
                    "finalized Targeted decisions do not cover acquisition evidence"
                )
            consumed.add(key)
            backfill_decision = backfills.get(key)
            if backfill_decision is not None:
                backfill_diagnostics.append(
                    DiagnosticRecord(
                        file_result.sample_name,
                        target_label,
                        "BACKFILL_ACCEPTED"
                        if backfill_decision.matched_candidate is not None
                        else "BACKFILL_HOLD",
                        "Cross-sample confirmed peak localization: "
                        + "; ".join(backfill_decision.reason_codes)
                        + "; direct CID/NL remains HOLD; "
                        + (
                            "ISTD identity confirmed by Backfill."
                            if targets_by_label[target_label].is_istd
                            and backfill_decision.matched_candidate is not None
                            else "confirmed=False."
                        ),
                    )
                )
            no_write_reason = (
                ""
                if render_decision.variant == "numeric"
                else getattr(
                    render_decision,
                    "no_write_reason",
                    f"finalized_{render_decision.variant}",
                )
            )
            rendered[target_label] = _finalized_extraction_result(
                extraction,
                target=targets_by_label[target_label],
                value=render_decision.value,
                no_write_reason=no_write_reason,
                evidence=getattr(render_decision, "evidence", None),
                backfill_supported=(
                    backfill_decision is not None
                    and backfill_decision.matched_candidate is not None
                ),
            )
        files.append(replace(file_result, results=rendered))
    if consumed != set(decisions):
        raise TargetedOutputBoundaryError(
            "finalized Targeted decisions crossed the acquisition sample population"
        )
    return RunOutput(
        file_results=files,
        diagnostics=[*acquisition_evidence.diagnostics, *backfill_diagnostics],
    )


def _finalized_extraction_result(
    extraction: ExtractionResult,
    *,
    target: Target,
    value: float | None,
    no_write_reason: str,
    evidence: object,
    backfill_supported: bool = False,
) -> ExtractionResult:
    projection = _finalized_product_projection(
        extraction.targeted_product_projection,
        target=target,
        evidence=evidence,
        value=value,
        no_write_reason=no_write_reason,
        backfill_supported=backfill_supported,
    )
    if not no_write_reason:
        if (
            value is None
            or type(evidence) is not ObservedSearchSubject
            or _hypothesis_area(evidence.hypothesis) != value
        ):
            raise TargetedOutputBoundaryError(
                "finalized Targeted value does not match its public result model"
            )
        integration = evidence.hypothesis.integration
        physical_evaluation = evidence.hypothesis.peak_observation_refs[
            0
        ].physical_evaluation
        rendered = replace(
            extraction,
            peak_result=replace(
                extraction.peak_result,
                peak=PeakResult(
                    rt=integration.rt_apex_min,
                    intensity=integration.height_raw,
                    intensity_smoothed=integration.height_smoothed,
                    area=value,
                    peak_start=integration.rt_left_min,
                    peak_end=integration.rt_right_min,
                ),
                candidates=(),
                candidate_scores=(),
            ),
            selected_hypothesis=None,
            selection_decision=None,
            physical_evaluation=physical_evaluation,
            selected_cid_nl_candidate_index=next(
                (
                    index
                    for index, evaluation in enumerate(
                        extraction.candidate_physical_evaluations
                    )
                    if evaluation is physical_evaluation
                ),
                None,
            ),
            targeted_product_projection=projection,
        )
        if rendered.reported_peak_area != value:
            raise TargetedOutputBoundaryError(
                "finalized Targeted hypothesis did not produce its numeric value"
            )
        return rendered
    return replace(
        extraction,
        peak_result=replace(
            extraction.peak_result,
            peak=None,
            candidates=(),
            candidate_scores=(),
            confidence=None,
            reason=no_write_reason,
        ),
        selected_hypothesis=None,
        selection_decision=None,
        targeted_product_projection=projection,
    )


def _hypothesis_area(hypothesis: Any) -> float:
    integration = hypothesis.integration
    if integration.area_ms1_morphology is not None:
        return integration.area_ms1_morphology
    return integration.area_raw_counts_seconds


def _finalized_product_projection(
    projection: TargetedProductProjection | None,
    *,
    target: Target,
    evidence: object,
    value: float | None,
    no_write_reason: str,
    backfill_supported: bool = False,
) -> TargetedProductProjection | None:
    if projection is None:
        return None
    if no_write_reason:
        return replace(
            projection,
            product_state="not_counted",
            counted_detection=False,
            review_state="review_required",
            projection_reason=no_write_reason,
        )
    if type(evidence) is not ObservedSearchSubject:
        raise TargetedOutputBoundaryError(
            "numeric projection lost its finalized evidence"
        )
    return build_targeted_product_projection(
        TargetedProductContext(
            role="ISTD" if target.is_istd else "Analyte",
            expected_present=target.is_istd,
            target_label=target.label,
            istd_pair=target.istd_pair,
            neutral_loss_required=target.neutral_loss_da is not None,
        ),
        rt=evidence.hypothesis.integration.rt_apex_min,
        area=value,
        confidence=projection.legacy_evidence.get("confidence", ""),
        nl_status=projection.legacy_evidence.get("nl_status", ""),
        cid_nl_supported=(
            evidence.hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
        ),
        backfill_supported=backfill_supported,
        support_reasons=projection.support_reasons,
        review_reasons=projection.review_reasons,
        conflict_reasons=projection.conflict_reasons,
        not_counted_reasons=projection.not_counted_reasons,
        exclusion_reasons=projection.exclusion_reasons,
        legacy_evidence=projection.legacy_evidence,
    )


def _prepare_targeted_production_workflow(
    request: TargetedApplicationRequest,
    acquisition_evidence: RunOutput,
    *,
    expected_inputs: TargetedProductionInputSnapshot | None = None,
) -> _PreparedTargetedProductionWorkflow:
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
    profile = targeted_signal_processing_profile(request.config)
    physical_evaluations = {}
    for sample, (_, file_result) in zip(
        samples,
        results_by_raw_path,
        strict=True,
    ):
        for target in resolved_targets:
            result = file_result.results.get(target.definition.target.label)
            if result is not None and result.physical_evaluation is not None:
                candidate_id = targeted_capture_candidate_id(target, sample)
                physical_evaluations[candidate_id] = result.physical_evaluation
                for index, evaluation in enumerate(
                    result.candidate_physical_evaluations
                ):
                    physical_evaluations[f"{candidate_id}:universe:{index}"] = (
                        evaluation
                    )
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
    return _PreparedTargetedProductionWorkflow(
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
    )


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
    context: _PreparedTargetedProductionWorkflow,
    target_index: int,
) -> _PreparedTargetedProductionWorkflow:
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
    context: _PreparedTargetedProductionWorkflow,
    request: TargetedApplicationRequest,
    target: ResolvedTargetDefinition,
) -> FinalizedTargetProductionResult:
    population = build_targeted_capture_population(
        target=target,
        processing_owners=context.owners,
        samples=context.samples,
        results_by_sample=context.results_by_sample,
        config=request.config,
    )
    assessments = assess_targeted_cid_nl_samples(population)
    population = population.bind_assessments(assessments)
    alignment_config = AlignmentConfig()
    backfill = resolve_targeted_istd_backfill(
        population,
        assessments,
        config=request.config,
        alignment_config=alignment_config,
    )
    population = population.rebind_to_recovery(backfill.recovery_observations)
    assessment_tuple = tuple(
        sorted(
            assessments,
            key=lambda item: item[0].identity.sample_identity_id,
        )
    )
    capture_inputs = population.capture_inputs
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
                capture_inputs,
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
            scope=population.scope,
            processing_request=context.owners.request,
            processing_attempt=context.owners.attempt,
        ),
        TargetWorkflowAdapter(
            capture_inputs=capture_inputs,
            sample_assessments=assessment_tuple,
            alignment_config=alignment_config,
            predecessor_cell_observations=(),
            completed_capture_handoff=population.captures,
            targeted_backfill_decisions=backfill.decisions,
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
    except WorkflowStageFailure as failure:
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
