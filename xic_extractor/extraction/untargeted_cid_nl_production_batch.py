"""Owner-closed production input assembly for pure Untargeted CID-NL."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.backfill.evaluation_coverage import (
    OwnerBackfillPlanningPosition,
    build_owner_backfill_evaluation_coverage,
    mint_owner_backfill_planning_epoch,
    mint_owner_backfill_position_id,
)
from xic_extractor.alignment.backfill.observation_repair import (
    ObservationRepairCandidate,
)
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_reconciliation import (
    apply_mode_track_refinement_run_for_workflow,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
)
from xic_extractor.alignment.mode_track import (
    ModeTrackTrajectoryPolicy,
    bootstrap_mode_track_refinement_from_cid_nl_support,
)
from xic_extractor.alignment.owner_backfill_request_plan import (
    OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
)
from xic_extractor.alignment.workflow import (
    CaptureWorkflowArguments,
    CompletedUntargetBatchWorkflowResult,
    ModeTrackWorkflowArguments,
    ObservationRepairWorkflowArguments,
    UntargetWorkflowAdapter,
    UntargetWorkflowBundle,
    UntargetWorkflowCompositionRequest,
    compose_untarget_batch_after_runtime,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.batch_contract import (
    DiscoveryCandidateUniverseReceipt,
)
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeTelemetryProvider,
    start_runtime_attempt,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_adapter import (
    UNTARGETED_CID_NL_CAPTURE_TRACE_SOURCE,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    UntargetedCidNlRealDataContext,
    build_untargeted_cid_nl_real_data_context,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRealDataRuntimeApplicationResult,
    UntargetedCidNlRealDataRuntimeJob,
    execute_untargeted_cid_nl_real_data_runtime,
    untargeted_cid_nl_runtime_call_cost,
)
from xic_extractor.peak_detection.capture import (
    bind_owner_backed_capture_hypothesis,
    capture_peak_hypotheses,
    mint_captured_peak_observation,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    UntargetedSearchScope,
)
from xic_extractor.peak_detection.traces import Trace, trace_from_xic_request
from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True, slots=True)
class UntargetedCidNlProductionBatch:
    """One complete request context and its canonical sample/subject jobs."""

    subject_batch: UntargetedCidNlMethodSubjectBatch
    context: UntargetedCidNlRealDataContext
    jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...]


def is_pure_single_provider_cid_nl(settings: DiscoverySettings) -> bool:
    """Return whether settings select the production-owned pure CID-NL route."""

    if not isinstance(settings, DiscoverySettings):
        return False
    return (
        settings.tag_combine_mode == "single"
        and len(settings.neutral_loss_profiles) == 1
        and len(settings.selected_tag_names) == 1
        and settings.selected_tag_names[0] == settings.neutral_loss_profiles[0].tag
    )


def build_untargeted_cid_nl_production_batch(
    *,
    raw_paths: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    discovery_receipts_by_sample: Mapping[
        str,
        DiscoveryCandidateUniverseReceipt,
    ],
    dll_dir: Path,
    sample_identity_namespace: str,
) -> UntargetedCidNlProductionBatch:
    """Seal exact candidate membership before any subject-local runtime work."""

    if not is_pure_single_provider_cid_nl(settings):
        raise ValueError(
            "Untargeted CID-NL production requires a pure single-provider route"
        )
    if not isinstance(dll_dir, Path):
        raise TypeError("dll_dir must be a Path")
    candidates = tuple(
        candidate
        for sample_candidates in candidates_by_sample.values()
        for candidate in sample_candidates
    )
    if not candidates:
        raise ValueError("Untargeted CID-NL production requires candidates")
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Discovery candidate identity must be globally unique")

    subject_by_key: dict[tuple[float, float], CidNlMethodSubject] = {}
    membership_keys: list[tuple[str, tuple[float, float]]] = []
    for candidate in candidates:
        precursor_mz = float(candidate.precursor_mz)
        neutral_loss_da = float(candidate.configured_neutral_loss_da)
        if (
            not math.isfinite(precursor_mz)
            or precursor_mz <= 0.0
            or not math.isfinite(neutral_loss_da)
            or neutral_loss_da <= 0.0
        ):
            raise ValueError("Discovery candidate lacks finite CID-NL owner facts")
        key = (precursor_mz, neutral_loss_da)
        subject_by_key.setdefault(
            key,
            mint_untargeted_cid_nl_method_subject(
                precursor_mz=precursor_mz,
                precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
                neutral_loss_da=neutral_loss_da,
            ),
        )
        membership_keys.append((candidate.candidate_id, key))

    subjects = tuple(
        sorted(subject_by_key.values(), key=lambda subject: subject.method_subject_id)
    )
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=tuple(
            sorted(
                (
                    candidate_id,
                    subject_by_key[key].method_subject_id,
                )
                for candidate_id, key in membership_keys
            )
        ),
    )
    context = build_untargeted_cid_nl_real_data_context(
        raw_paths=raw_paths,
        sample_metadata=sample_metadata,
        method_subject=subject_batch,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=discovery_receipts_by_sample,
        sample_identity_namespace=sample_identity_namespace,
    )
    jobs = tuple(
        UntargetedCidNlRealDataRuntimeJob(
            processing_owners=context.processing_owners,
            sample=sample,
            method_subject=subject,
            candidates=context.candidates_by_sample[sample.metadata.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[
                sample.metadata.raw_stem
            ],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
            dll_dir=dll_dir,
            work_unit_key=(
                "untargeted-cid-nl:"
                f"{sample.metadata.raw_stem}:{subject.method_subject_id}"
            ),
        )
        for sample in context.samples
        for subject in subject_batch.subjects
    )
    return UntargetedCidNlProductionBatch(
        subject_batch=subject_batch,
        context=context,
        jobs=jobs,
    )


def execute_untargeted_cid_nl_production_batch(
    batch: UntargetedCidNlProductionBatch,
    *,
    telemetry_provider: RuntimeTelemetryProvider,
    recovery_marker: OwnerPathRuntimeMarker,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> UntargetedCidNlRealDataRuntimeApplicationResult:
    """Execute the sealed universe once and enforce its per-sample phase bound."""

    if not isinstance(batch, UntargetedCidNlProductionBatch):
        raise TypeError("batch must be an UntargetedCidNlProductionBatch")
    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation=(
            f"{batch.context.processing_owners.request.processing_request_id}:"
            "cid-nl-production-v1"
        ),
    )
    result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        batch.jobs,
        telemetry_provider=telemetry_provider,
        recovery_marker=recovery_marker,
        progress_callback=progress_callback,
    )
    if not result.succeeded or len(result.sample_results) != len(batch.jobs):
        raise RuntimeError("Untargeted CID-NL production did not complete atomically")
    call_cost = untargeted_cid_nl_runtime_call_cost(result.finalization)
    sample_count = len(batch.context.samples)
    if (
        call_cost["raw_open_count"] > sample_count * 2
        or call_cost["xic_batch_count"] > sample_count * 2
    ):
        raise RuntimeError(
            "Untargeted CID-NL production exceeded initial/recovery sample phases"
        )
    return result


def compose_untargeted_cid_nl_production_result(
    batch: UntargetedCidNlProductionBatch,
    runtime_result: UntargetedCidNlRealDataRuntimeApplicationResult,
    *,
    alignment_config: AlignmentConfig,
) -> CompletedUntargetBatchWorkflowResult:
    """Close exact subject-local workflows from the one completed runtime."""

    if not runtime_result.succeeded:
        raise RuntimeError("CID-NL production cannot compose a partial runtime")
    if not isinstance(alignment_config, AlignmentConfig):
        raise TypeError("alignment_config must be an AlignmentConfig")
    result_by_key = {
        (
            result.job.sample.metadata.raw_stem,
            result.job.method_subject.method_subject_id,
        ): result
        for result in runtime_result.sample_results
    }
    if len(result_by_key) != len(batch.jobs):
        raise RuntimeError("CID-NL production runtime lost sample/subject results")

    trace_by_candidate = _runtime_candidate_traces(batch, runtime_result)
    candidate_by_id = {
        candidate.candidate_id: candidate
        for candidates in batch.context.candidates_by_sample.values()
        for candidate in candidates
    }
    bindings = tuple(
        _capture_binding(
            batch=batch,
            candidate=candidate_by_id[candidate_id],
            method_subject_id=method_subject_id,
        )
        for candidate_id, method_subject_id in batch.subject_batch.candidate_memberships
    )
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=batch.context.processing_owners.request,
        samples=batch.context.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=batch.context.peak_config,
        ),
        evidence_providers=tuple(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=subject.method_subject_id,
            )
            for subject in batch.subject_batch.subjects
        ),
        declared_candidate_ids=tuple(
            candidate_id
            for candidate_id, _method_subject_id in (
                batch.subject_batch.candidate_memberships
            )
        ),
        capture_bindings=bindings,
    )
    parent_request = UntargetWorkflowCompositionRequest(
        scope=scope,
        processing_request=batch.context.processing_owners.request,
        processing_attempt=batch.context.processing_owners.attempt,
    )

    bundles = []
    for subject in batch.subject_batch.subjects:
        child_bindings = tuple(
            binding
            for binding in bindings
            if binding.method_subject_id == subject.method_subject_id
        )
        child_scope = UntargetedSearchScope(
            schema_version=scope.schema_version,
            request=scope.request,
            samples=scope.samples,
            method_constraints=scope.method_constraints,
            evidence_providers=(
                EvidenceProviderBinding(
                    provider_id=CID_NL_PROVIDER_ID,
                    method_subject_id=subject.method_subject_id,
                ),
            ),
            declared_candidate_ids=tuple(
                binding.candidate_id for binding in child_bindings
            ),
            capture_bindings=child_bindings,
        )
        assessments = tuple(
            (
                sample,
                result_by_key[
                    (sample.metadata.raw_stem, subject.method_subject_id)
                ].adapter_result.assessment_input.assessment,
            )
            for sample in sorted(
                batch.context.samples,
                key=lambda item: item.identity.sample_identity_id,
            )
        )
        trajectory_policy = ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=2 / 60,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        )
        spine = build_cid_nl_reference_spine(
            processing_owners=batch.context.processing_owners,
            method_subject=subject,
            sample_assessments=assessments,
            config=alignment_config,
        )
        capture_inputs_list = []
        captures_list = []
        assessment_by_sample_id = {
            sample.identity.sample_identity_id: assessment
            for sample, assessment in assessments
        }
        sample_by_id = {
            sample.identity.sample_identity_id: sample
            for sample in batch.context.samples
        }
        for binding in child_bindings:
            sample = sample_by_id[binding.sample_identity_id]
            trace = trace_by_candidate[binding.candidate_id]
            captured = capture_peak_hypotheses(
                scope=child_scope,
                candidate_id=binding.candidate_id,
                trace=trace,
            )
            observation = mint_captured_peak_observation(
                captured,
                processing_owners=batch.context.processing_owners,
                sample=sample,
            )
            hypotheses = compose_cid_nl_peak_hypotheses_from_assessment(
                processing_owners=batch.context.processing_owners,
                sample=sample,
                assessment=assessment_by_sample_id[binding.sample_identity_id],
            )
            matches = tuple(
                hypothesis
                for hypothesis in hypotheses
                if observation in hypothesis.peak_observation_refs
            )
            if len(matches) != 1:
                raise RuntimeError(
                    "CID-NL runtime assessment lost its exact captured observation"
                )
            owner_capture = bind_owner_backed_capture_hypothesis(
                captured,
                observation=observation,
                hypothesis=matches[0],
            )
            captures_list.append(owner_capture)
            capture_inputs_list.append(
                CaptureWorkflowArguments(
                    candidate_id=binding.candidate_id,
                    trace=trace,
                    owner_observation=observation,
                    owner_hypothesis=matches[0],
                )
            )
        captures = tuple(captures_list)
        capture_inputs = tuple(capture_inputs_list)
        refinement = bootstrap_mode_track_refinement_from_cid_nl_support(
            processing_owners=batch.context.processing_owners,
            sample_assessments=assessments,
            alignment_config=alignment_config,
            trajectory_policy=trajectory_policy,
        )
        selected_ids = tuple(
            sorted(
                capture.selected_hypothesis.hypothesis_id
                for capture in captures
                if capture.selected_hypothesis is not None
            )
        )
        owner_ids = tuple(sorted(owner.owner_id for owner in refinement.owners))
        if selected_ids != owner_ids:
            raise RuntimeError(
                "CID-NL capture/assessment owner mismatch: "
                f"captures={selected_ids!r}; assessments={owner_ids!r}"
            )
        cross_sample = apply_mode_track_refinement_run_for_workflow(
            processing_owners=batch.context.processing_owners,
            captures=captures,
            run=refinement,
        )
        positions = tuple(
            OwnerBackfillPlanningPosition(
                position_id=mint_owner_backfill_position_id(
                    processing_request_id=group.processing_request_id,
                    group_hypothesis_id=group.group_hypothesis_id,
                    sample_identity_id=sample.identity.sample_identity_id,
                ),
                group_identity=group,
                sample=sample,
                planning_state="not_planned",
                planning_reason="retained_local_owner",
                primary_logical_requests=(),
            )
            for group in cross_sample.population
            for sample in batch.context.samples
        )
        epoch = mint_owner_backfill_planning_epoch(
            processing_owners=batch.context.processing_owners,
            group_identities=cross_sample.population,
            samples=batch.context.samples,
            raw_sample_stems=frozenset(batch.context.raw_paths_by_sample),
            alignment_config=alignment_config,
            backend="raw",
            window_strategy="exact",
            superwindow_span_factor=2,
            validation_mode="primary",
            result_semantics=OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
            scope="full-audit",
            selected_family_ids=frozenset(),
            positions=positions,
        )
        coverage = build_owner_backfill_evaluation_coverage(epoch, ())
        bundles.append(
            UntargetWorkflowBundle(
                request=UntargetWorkflowCompositionRequest(
                    scope=child_scope,
                    processing_request=parent_request.processing_request,
                    processing_attempt=parent_request.processing_attempt,
                ),
                adapter=UntargetWorkflowAdapter(
                    capture_inputs=capture_inputs,
                    mode_track=ModeTrackWorkflowArguments(
                        sample_assessments=assessments,
                        alignment_config=alignment_config,
                        trajectory_policy=trajectory_policy,
                    ),
                    repair=ObservationRepairWorkflowArguments(
                        coverage=coverage,
                        cell_observations=spine.cells,
                        execute=_unexpected_repair,
                    ),
                    predecessor_cell_observations=spine.cells,
                ),
            )
        )
    terminal = compose_untarget_batch_after_runtime(
        parent_request,
        lambda: tuple(bundles),
        runtime_result.finalization,
    )
    if type(terminal) is not CompletedUntargetBatchWorkflowResult:
        raise RuntimeError(
            "CID-NL workflow failed atomically: "
            f"stage={getattr(terminal, 'stage', None)}; "
            f"reason={getattr(terminal, 'failure_message', None)}"
        )
    return terminal


def _runtime_candidate_traces(
    batch: UntargetedCidNlProductionBatch,
    runtime_result: UntargetedCidNlRealDataRuntimeApplicationResult,
) -> dict[str, Trace]:
    traces: dict[str, Trace] = {}
    for sample in batch.context.samples:
        candidates = tuple(
            sorted(
                batch.context.candidates_by_sample[sample.metadata.raw_stem],
                key=lambda candidate: candidate.candidate_id,
            )
        )
        result = next(
            result
            for result in runtime_result.sample_results
            if result.job.sample is sample
            and result.adapter_result.acquisition is not None
        )
        acquisition = result.adapter_result.acquisition
        assert acquisition is not None
        if len(acquisition.traces) != len(candidates):
            raise RuntimeError("sample acquisition does not close candidate universe")
        for candidate, acquired in zip(candidates, acquisition.traces, strict=True):
            traces[candidate.candidate_id] = trace_from_xic_request(
                sample_name=sample.metadata.sample_name,
                request=acquired.coordinate.request,
                xic_trace=acquired.trace,
                source=UNTARGETED_CID_NL_CAPTURE_TRACE_SOURCE,
                acquisition_source_id=(
                    acquired.coordinate.acquisition_source_id
                ),
            )
    return traces


def _capture_binding(
    *,
    batch: UntargetedCidNlProductionBatch,
    candidate: DiscoveryCandidate,
    method_subject_id: str,
) -> CaptureCandidateBinding:
    sample = batch.context.sample(candidate.sample_stem)
    source = batch.context.discovery_receipts_by_sample[
        candidate.sample_stem
    ].acquisition_source
    return CaptureCandidateBinding(
        candidate_id=candidate.candidate_id,
        method_subject_id=method_subject_id,
        sample_identity_id=sample.identity.sample_identity_id,
        sample_name=sample.metadata.sample_name,
        acquisition_source_id=source.acquisition_source_id,
        query_mz=candidate.precursor_mz,
        query_rt_min=candidate.ms1_search_rt_min,
        query_rt_max=candidate.ms1_search_rt_max,
        query_ppm_tol=batch.context.settings.precursor_mz_tolerance_ppm,
        preferred_rt=candidate.best_seed_rt,
    )


def _unexpected_repair(
    _candidate: ObservationRepairCandidate,
) -> OwnerBackfillSampleResult:
    raise RuntimeError("direct CID-NL production cannot request legacy Backfill")


__all__ = [
    "UntargetedCidNlProductionBatch",
    "build_untargeted_cid_nl_production_batch",
    "compose_untargeted_cid_nl_production_result",
    "execute_untargeted_cid_nl_production_batch",
    "is_pure_single_provider_cid_nl",
]
