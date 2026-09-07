"""Owner-closed production input assembly for pure Untargeted CID-NL."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from xic_extractor.alignment.backfill.evaluation_coverage import (
    OwnerBackfillPlanningPosition,
    build_owner_backfill_evaluation_coverage,
    mint_owner_backfill_conservative_no_request_proof,
    mint_owner_backfill_planning_epoch,
    mint_owner_backfill_position_id,
)
from xic_extractor.alignment.backfill.observation_repair import (
    ObservationRepairCandidate,
)
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.cell_observations import (
    compose_cid_nl_predecessor_cells,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillRecoveryCandidateUniverse,
    assess_conservative_backfill_group,
    map_conservative_backfill_candidates,
    mint_conservative_backfill_recovery_candidate_universe,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodEdge,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    apply_mode_track_refinement_run_for_workflow,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
)
from xic_extractor.alignment.mode_track import (
    ModeTrackTrajectoryPolicy,
    bootstrap_mode_track_refinement_from_cid_nl_support,
)
from xic_extractor.alignment.owner_backfill_request_plan import (
    OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
)
from xic_extractor.alignment.result.application_result import (
    UntargetedCandidateExclusion,
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
from xic_extractor.discovery.models import (
    MS1_FEATURE_BACKED_STATES,
    DiscoveryCandidate,
    DiscoverySettings,
)
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeTelemetryProvider,
    start_runtime_attempt,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    UntargetedCidNlRealDataContext,
    build_untargeted_cid_nl_real_data_context,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRealDataRuntimeApplicationResult,
    UntargetedCidNlRealDataRuntimeJob,
    _RuntimeResultContext,
    build_untargeted_cid_nl_sample_runtime_jobs,
    execute_untargeted_cid_nl_real_data_runtime,
    untargeted_cid_nl_runtime_call_cost,
)
from xic_extractor.peak_detection.capture import (
    SampleLocalCapture,
    bind_owner_backed_capture_hypothesis,
    capture_peak_hypotheses,
    mint_captured_peak_observation,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    CidNlComposition,
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    UntargetedSearchScope,
    require_capture_binding,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec
from xic_extractor.sample_metadata import (
    SampleMetadata,
)


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
    try:
        CidNlDiscoveryMethodSpec(settings=settings)
    except (TypeError, ValueError):
        return False
    return True


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
    signal_processing_profile: SignalProcessingProfile | None = None,
    physical_records_by_candidate: Mapping[str, DiscoveryPhysicalFactRecord]
    | None = None,
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

    combine_mode = settings.tag_combine_mode
    if len(settings.neutral_loss_profiles) > 1 and combine_mode == "single":
        raise ValueError("Multiple CID-NL tags require union or intersection")
    composition = (
        CidNlComposition(
            tags=tuple(
                (profile.tag, profile.neutral_loss_da)
                for profile in settings.neutral_loss_profiles
            ),
            combine_mode=combine_mode,
        )
        if len(settings.neutral_loss_profiles) > 1 and combine_mode != "single"
        else None
    )
    candidate_facts: list[tuple[float, float, str]] = []
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
        candidate_facts.append(
            (
                0.0 if composition else neutral_loss_da,
                precursor_mz,
                candidate.candidate_id,
            )
        )
    subject_by_key: dict[tuple[float, float | None], CidNlMethodSubject] = {}
    membership_keys: list[tuple[str, tuple[float, float | None]]] = []
    key_by_requirement: dict[float | None, tuple[float, float | None]] = {}
    for neutral_loss_da, precursor_mz, candidate_id in sorted(candidate_facts):
        requirement = None if composition else neutral_loss_da
        key = key_by_requirement.get(requirement)
        # A fixed first query center bounds the workset without chaining masses.
        if key is None or (
            abs(precursor_mz - key[0]) / key[0] * 1_000_000.0
            > settings.precursor_mz_tolerance_ppm
        ):
            key = (precursor_mz, requirement)
            key_by_requirement[requirement] = key
            subject_by_key[key] = mint_untargeted_cid_nl_method_subject(
                precursor_mz=precursor_mz,
                precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
                neutral_loss_da=requirement,
                composition=composition,
            )
        membership_keys.append((candidate_id, key))

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
        signal_processing_profile=signal_processing_profile,
        physical_records_by_candidate=physical_records_by_candidate,
    )
    ordered_physical_records = tuple(
        record
        for _candidate_id, record in sorted(
            context.physical_records_by_candidate.items()
        )
    )
    jobs = tuple(
        job
        for sample in context.samples
        for job in build_untargeted_cid_nl_sample_runtime_jobs(
            processing_owners=context.processing_owners,
            sample=sample,
            subject_work_units=tuple(
                (
                    subject,
                    "untargeted-cid-nl:"
                    f"{sample.metadata.raw_stem}:{subject.method_subject_id}",
                )
                for subject in subject_batch.subjects
            ),
            candidates=context.candidates_by_sample[sample.metadata.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[
                sample.metadata.raw_stem
            ],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
            dll_dir=dll_dir,
            signal_processing_profile=context.signal_processing_profile,
            physical_records=tuple(
                record
                for record in ordered_physical_records
                if record.work_item.sample_stem == sample.metadata.raw_stem
            ),
        )
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
    result_context = _RuntimeResultContext(batch.jobs)
    result_by_key = {
        (
            result.job.sample.metadata.raw_stem,
            result.job.method_subject.method_subject_id,
        ): result
        for result in runtime_result.sample_results
    }
    if len(result_by_key) != len(batch.jobs):
        raise RuntimeError("CID-NL production runtime lost sample/subject results")

    capture_by_candidate = _runtime_captures_by_candidate(batch, runtime_result)
    candidate_by_id = {
        candidate.candidate_id: candidate
        for candidates in batch.context.candidates_by_sample.values()
        for candidate in candidates
    }
    unsupported_subjects = {}
    for subject in batch.subject_batch.subjects:
        subject_assessments = tuple(
            assessment
            for sample in sorted(
                batch.context.samples, key=lambda item: item.identity.sample_identity_id
            )
            for assessment in result_by_key[
                (sample.metadata.raw_stem, subject.method_subject_id)
            ].effective_assessments
        )
        if all(
            ref.content.technical_state == "evaluated"
            and ref.content.method_result in {"HOLD", "REJECT"}
            for ref in subject_assessments
        ):
            unsupported_subjects[subject.method_subject_id] = subject_assessments
    bindings = tuple(
        require_capture_binding(
            capture_by_candidate[candidate_id].scope,
            candidate_id,
        )
        for candidate_id, method_subject_id in batch.subject_batch.candidate_memberships
        if candidate_by_id[candidate_id].discovery_candidate_state
        in MS1_FEATURE_BACKED_STATES
        and method_subject_id not in unsupported_subjects
    )
    exclusions = tuple(
        UntargetedCandidateExclusion(
            candidate_id=candidate_id,
            method_subject_id=method_subject_id,
            canonical_sample_identity_id=batch.context.sample(
                candidate_by_id[candidate_id].sample_stem
            ).identity.sample_identity_id,
            discovery_candidate_state=(
                candidate_by_id[candidate_id].discovery_candidate_state
            ),
            reason_code=(
                "composite_requirement_not_supported"
                if method_subject_id in unsupported_subjects
                and unsupported_subjects[method_subject_id][
                    0
                ].content.method_subject.composition
                is not None
                else "cid_nl_requirement_not_supported"
                if method_subject_id in unsupported_subjects
                else "discovery_candidate_not_search_eligible"
            ),
            composite_assessments=tuple(
                ref
                for ref in unsupported_subjects.get(method_subject_id, ())
                if ref.content.method_subject.composition is not None
            ),
            method_assessments=tuple(
                ref
                for ref in unsupported_subjects.get(method_subject_id, ())
                if ref.content.method_subject.composition is None
            ),
        )
        for candidate_id, method_subject_id in (
            batch.subject_batch.candidate_memberships
        )
        if candidate_by_id[candidate_id].discovery_candidate_state
        not in MS1_FEATURE_BACKED_STATES
        or method_subject_id in unsupported_subjects
    )
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=batch.context.processing_owners.request,
        samples=batch.context.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=batch.context.peak_config,
            signal_processing_profile=batch.context.signal_processing_profile,
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
        excluded_candidates=exclusions,
    )

    bundles = []
    initial_groups: list[CrossSamplePeakGroupHypothesis] = []
    initial_edges: list[CrossSampleNeighborhoodEdge] = []
    for subject in batch.subject_batch.subjects:
        child_bindings = tuple(
            binding
            for binding in bindings
            if binding.method_subject_id == subject.method_subject_id
        )
        if not child_bindings:
            continue
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
                assessment,
            )
            for sample in sorted(
                batch.context.samples,
                key=lambda item: item.identity.sample_identity_id,
            )
            for assessment in result_by_key[
                (sample.metadata.raw_stem, subject.method_subject_id)
            ].effective_assessments
        )
        trajectory_policy = ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=2 / 60,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        )
        capture_inputs_list = []
        captures_list = []
        assessment_by_sample_id = {
            sample.identity.sample_identity_id: result_by_key[
                (sample.metadata.raw_stem, subject.method_subject_id)
            ].adapter_result.assessment_input.assessment
            for sample in batch.context.samples
        }
        sample_by_id = {
            sample.identity.sample_identity_id: sample
            for sample in batch.context.samples
        }
        hypotheses_by_sample_id = {
            sample_id: compose_peak_hypotheses_from_assessment_universe(
                processing_owners=batch.context.processing_owners,
                sample=sample_by_id[sample_id],
                assessment=assessment_by_sample_id[sample_id],
            )
            for sample_id in dict.fromkeys(
                binding.sample_identity_id for binding in child_bindings
            )
        }
        for binding in child_bindings:
            sample = sample_by_id[binding.sample_identity_id]
            trace = capture_by_candidate[binding.candidate_id].trace
            captured = capture_peak_hypotheses(
                scope=child_scope,
                candidate_id=binding.candidate_id,
                trace=trace,
            )
            if captured.selected_hypothesis is None:
                captures_list.append(captured)
                capture_inputs_list.append(
                    CaptureWorkflowArguments(
                        candidate_id=binding.candidate_id,
                        trace=trace,
                    )
                )
                continue
            observation = mint_captured_peak_observation(
                captured,
                processing_owners=batch.context.processing_owners,
                sample=sample,
            )
            hypotheses = hypotheses_by_sample_id[binding.sample_identity_id]
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
            retain_observation_only_candidates=True,
        )
        initial_groups.extend(refinement.family_context.mode_tracks)
        initial_edges.extend(refinement.classified_edges)
        selected_ids = tuple(
            sorted(
                {
                    capture.selected_hypothesis.hypothesis_id
                    for capture in captures
                    if capture.selected_hypothesis is not None
                }
            )
        )
        retained_ids = tuple(
            sorted(
                candidate.peak_hypothesis_id
                for sample_source in refinement.fit_once_source.sample_sources
                for candidate in sample_source.candidates
            )
        )
        if not set(selected_ids).issubset(retained_ids):
            raise RuntimeError(
                "CID-NL capture/assessment owner mismatch: "
                f"captures={selected_ids!r}; assessments={retained_ids!r}"
            )
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=batch.context.processing_owners,
            captures=captures,
            run=refinement,
        )
        predecessor_cells = compose_cid_nl_predecessor_cells(
            processing_owners=batch.context.processing_owners,
            groups=refinement.family_context.mode_tracks,
            sample_assessments=assessments,
            alignment_config=alignment_config,
            recovery_assessments_by_group={
                (
                    group.group_hypothesis_id,
                    sample.identity.sample_identity_id,
                ): recovery.assessment
                for group in refinement.family_context.mode_tracks
                for sample in batch.context.samples
                if group.group_center_rt is not None
                and (
                    recovery := result_by_key[
                        (sample.metadata.raw_stem, subject.method_subject_id)
                    ].recovery_for_group(group.group_center_rt)
                )
                is not None
            },
        )
        cell_by_key = {
            (
                cell.content.group_hypothesis_id,
                cell.content.canonical_sample_identity_id,
            ): cell
            for cell in predecessor_cells
        }
        repair_groups = refinement.predecessors
        no_request_proofs = {}
        for group in repair_groups:
            facts = assess_conservative_backfill_group(
                tuple(
                    cell
                    for cell in predecessor_cells
                    if cell.content.group_hypothesis_id == group.group_hypothesis_id
                ),
                sample_population=batch.context.samples,
            )
            for sample in batch.context.samples:
                key = (group.group_hypothesis_id, sample.identity.sample_identity_id)
                cell = cell_by_key[key]
                if (
                    cell.content.technical_state != "evaluated"
                    or cell.content.method_result != "HOLD"
                    or cell.peak_observation is not None
                ):
                    continue
                completion = None
                universes: tuple[
                    ConservativeBackfillRecoveryCandidateUniverse, ...
                ] = ()
                if facts.may_attempt_recovery:
                    if cell.method_assessment.candidate_peaks:
                        continue
                    sample_result = result_by_key[
                        (sample.metadata.raw_stem, subject.method_subject_id)
                    ]
                    if (
                        facts.group_context.group.group_center_rt is None
                        or sample_result.recovery_for_group(
                            facts.group_context.group.group_center_rt
                        )
                        is None
                    ):
                        continue
                    completion = result_context.project_completed_recovery(
                        sample_result, group_context=facts.group_context
                    )
                    universes = (
                        mint_conservative_backfill_recovery_candidate_universe(
                            processing_owners=batch.context.processing_owners,
                            sample=sample,
                            group_facts=(facts,),
                            candidate_assessment=completion.recovery_assessment,
                        ),
                    )
                (decision,) = map_conservative_backfill_candidates(
                    (facts,), sample=sample, candidate_universes=universes
                )
                no_request_proofs[key] = (
                    mint_owner_backfill_conservative_no_request_proof(
                        processing_owners=batch.context.processing_owners,
                        group_identity=group,
                        sample=sample,
                        decision=decision,
                        completion=completion,
                    )
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
                planning_reason=(
                    no_request_proofs[
                        (group.group_hypothesis_id, sample.identity.sample_identity_id)
                    ].planning_reason
                    if (group.group_hypothesis_id, sample.identity.sample_identity_id)
                    in no_request_proofs
                    else "local_owner_not_supersedable"
                    if cell.content.outside_group is not None
                    else "retained_unresolved_candidates"
                    if (
                        cell.content.technical_state == "evaluated"
                        and cell.content.method_result == "HOLD"
                        and cell.peak_observation is None
                        and len(cell.method_assessment.candidate_peaks) >= 2
                    )
                    else "retained_local_owner"
                ),
                primary_logical_requests=(),
                conservative_no_request_proof=no_request_proofs.get(
                    (group.group_hypothesis_id, sample.identity.sample_identity_id)
                ),
            )
            for group in repair_groups
            for sample in batch.context.samples
            for cell in (
                cell_by_key[
                    (group.group_hypothesis_id, sample.identity.sample_identity_id)
                ],
            )
        )
        epoch = mint_owner_backfill_planning_epoch(
            processing_owners=batch.context.processing_owners,
            group_identities=repair_groups,
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
                        retain_observation_only_candidates=True,
                    ),
                    repair=ObservationRepairWorkflowArguments(
                        coverage=coverage,
                        cell_observations=predecessor_cells,
                        execute=_unexpected_repair,
                    ),
                    predecessor_cell_observations=predecessor_cells,
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
    return replace(
        terminal,
        method_assessments=tuple(
            assessment
            for result in runtime_result.sample_results
            for assessment in result.effective_assessments
        ),
        initial_groups=tuple(initial_groups),
        initial_edges=tuple(initial_edges),
    )


def _runtime_captures_by_candidate(
    batch: UntargetedCidNlProductionBatch,
    runtime_result: UntargetedCidNlRealDataRuntimeApplicationResult,
) -> dict[str, SampleLocalCapture]:
    captures: dict[str, SampleLocalCapture] = {}
    for result in runtime_result.sample_results:
        for capture in result.adapter_result.captures:
            if capture.candidate_id in captures:
                raise RuntimeError("runtime capture duplicated a candidate")
            captures[capture.candidate_id] = capture
    expected = {
        candidate.candidate_id
        for candidates in batch.context.candidates_by_sample.values()
        for candidate in candidates
        if candidate.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
    }
    if set(captures) != expected:
        raise RuntimeError("runtime capture does not close capture-eligible candidates")
    return captures


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
