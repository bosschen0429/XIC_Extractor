"""In-process and pool harnesses for untargeted real-data extraction."""

from __future__ import annotations

import importlib
from copy import copy
from dataclasses import dataclass
from typing import Literal

from xic_extractor.acquisition_contract import TraceAcquisitionBatch
from xic_extractor.alignment.cell_group_context import (
    select_cid_nl_group_backfill_subject,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    plan_conservative_backfill_targets,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    UntargetedCidNlBackfillInput,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceCleanupOutcome,
)
from xic_extractor.extraction.untargeted_acquisition_cache import (
    CachedUntargetedRaw,
    bind_untargeted_acquisition_cache,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_adapter import (
    UntargetedCidNlBackfillAttemptResult,
    UntargetedCidNlBackfillTarget,
    UntargetedCidNlBackfillTraceBatch,
    UntargetedCidNlRealDataAdapterResult,
    UntargetedCidNlSampleTraceBatch,
    _UntargetedRealDataWorkObserver,
    acquire_untargeted_cid_nl_backfill_trace_batch,
    acquire_untargeted_cid_nl_sample_trace_batch,
    adapt_current_discovery_sample_subject,
    backfill_untargeted_cid_nl_sample_subject,
)
from xic_extractor.extraction.untargeted_real_data_models import (
    UntargetedCidNlPreparedGrouping,
    UntargetedCidNlRealDataRuntimeError,
    UntargetedCidNlRealDataRuntimeJob,
    _request_subject_candidates,
    _request_subject_physical_records,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    is_cid_nl_missing_ms1_rejection,
    is_unsearched_cid_nl_discovery_assessment,
)
from xic_extractor.peak_detection.cid_nl_peak_support_models import (
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.processing_identity import resolve_request_acquisition_source
from xic_extractor.raw_reader import (
    RawFileHandle,
    RawReaderOpenError,
    _bind_expected_path_identity,
    _ensure_assemblies_loaded,
    open_raw,
)


@dataclass(slots=True)
class _RuntimeJobState:
    job: UntargetedCidNlRealDataRuntimeJob
    resource_key: str | None
    initial_requires_raw: bool
    observer: _UntargetedRealDataWorkObserver
    error: Exception | None = None
    adapter_result: UntargetedCidNlRealDataAdapterResult | None = None
    backfill_support: _BackfillSupportContext | None = None
    backfill_input: UntargetedCidNlBackfillInput | None = None
    backfill_attempts: tuple[UntargetedCidNlBackfillAttemptResult, ...] = ()
    backfill_targets: tuple[UntargetedCidNlBackfillTarget, ...] = ()
    open_attempted: bool = False
    opened: bool = False
    cleanup_outcome: RuntimeResourceCleanupOutcome = (
        RuntimeResourceCleanupOutcome.SUCCEEDED
    )


@dataclass(frozen=True, slots=True)
class _BackfillSupportContext:
    """Narrow backfill-only projection of initial assessment facts.

    Backfill execution consumes only the support policy and, when the
    initial attempt was a missing-MS1 rejection, that initial assessment.
    The full ``UntargetedCidNlRealDataAdapterResult`` — including
    acquisition, captures, lineage artifacts, and capture evidence — is
    not needed by the backfill worker and must not be transported across
    the process boundary.  ``initial_assessment`` stays ``None`` whenever
    backfill would not consume it, so the assessment reference graph is
    not transported either.
    """

    initial_assessment: CidNlMethodAssessmentRef | None
    support_policy: DdaCidNlPeakSupportPolicy


def _backfill_support_context(
    result: UntargetedCidNlRealDataAdapterResult,
) -> _BackfillSupportContext:
    """Project the backfill-consumed facts out of one initial result."""

    assessment = result.assessment_input.assessment
    return _BackfillSupportContext(
        initial_assessment=(
            assessment if is_cid_nl_missing_ms1_rejection(assessment) else None
        ),
        support_policy=assessment.content.evaluation_receipt.support_policy,
    )


@dataclass(slots=True)
class _BatchSamplePhaseState:
    states: tuple[_RuntimeJobState, ...]
    initial_resource_key: str | None
    backfill_resource_key: str | None
    initial_open_attempted: bool = False
    initial_opened: bool = False
    initial_cleanup_outcome: RuntimeResourceCleanupOutcome = (
        RuntimeResourceCleanupOutcome.SUCCEEDED
    )
    backfill_open_attempted: bool = False
    backfill_opened: bool = False
    backfill_cleanup_outcome: RuntimeResourceCleanupOutcome = (
        RuntimeResourceCleanupOutcome.SUCCEEDED
    )
    initial_acquisition: TraceAcquisitionBatch | None = None
    backfill_acquisition: TraceAcquisitionBatch | None = None
    sample_template: UntargetedCidNlRealDataRuntimeJob | None = None

    @property
    def job(self) -> UntargetedCidNlRealDataRuntimeJob:
        if self.sample_template is not None:
            return self.sample_template
        return self.states[0].job


def _runtime_delegate(name: str, fallback):
    import sys

    rt = sys.modules.get("xic_extractor.extraction.untargeted_real_data_runtime")
    if rt is not None and hasattr(rt, name):
        return getattr(rt, name)
    return fallback


def _open_job_raw(
    job: UntargetedCidNlRealDataRuntimeJob,
) -> RawFileHandle | CachedUntargetedRaw:
    active_open = _runtime_delegate("open_raw", open_raw)
    expectation = job.acquisition_source_expectation
    source = (
        expectation.source
        if expectation is not None
        else resolve_request_acquisition_source(
            processing_owners=job.processing_owners, sample=job.sample
        )
    )

    def open_native():
        if expectation is not None:
            return active_open(
                job.raw_path,
                job.dll_dir,
                expected_acquisition_source=source,
                _expected_path_identity=expectation.path_identity,
            )
        return active_open(
            job.raw_path, job.dll_dir, expected_acquisition_source=source
        )

    if expectation is not None and job.acquisition_cache_dir is not None:
        # The input owner already verified the content. Cache replay still checks
        # that this path has that exact identity before returning any facts.
        _bind_expected_path_identity(job.raw_path, source, expectation.path_identity)
        return CachedUntargetedRaw(
            open_native, source=source, cache_dir=job.acquisition_cache_dir
        )
    raw = open_native()
    return bind_untargeted_acquisition_cache(
        raw,
        source=source,
        cache_dir=job.acquisition_cache_dir,
    )


def _execute_initial_assessment(state: _RuntimeJobState) -> None:
    job = state.job
    raw = None
    try:
        if state.error is None and state.initial_requires_raw:
            if state.resource_key is None:
                raise UntargetedCidNlRealDataRuntimeError(
                    "bounded subject is missing its acquisition reservation"
                )
            state.open_attempted = True
            raw = _open_job_raw(job)
            state.opened = True
        if state.error is None:
            state.adapter_result = adapt_current_discovery_sample_subject(
                processing_owners=job.processing_owners,
                sample=job.sample,
                method_subject=job.method_subject,
                candidates=_request_subject_candidates(job),
                support_candidates=job.support_candidates,
                settings=job.settings,
                peak_config=job.peak_config,
                raw=raw,
                _observer=state.observer,
                signal_processing_profile=job.signal_processing_profile,
                physical_records=_request_subject_physical_records(job),
            )
    except RawReaderOpenError as exc:
        state.cleanup_outcome = (
            RuntimeResourceCleanupOutcome.FAILED
            if exc.cleanup_failed
            else RuntimeResourceCleanupOutcome.SUCCEEDED
        )
        state.error = exc
    except Exception as exc:
        state.error = exc
    finally:
        if raw is not None:
            if isinstance(raw, CachedUntargetedRaw):
                state.open_attempted = raw.native_open_attempted
                state.opened = raw.native_opened
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
                state.cleanup_outcome = RuntimeResourceCleanupOutcome.FAILED
                if state.error is None:
                    state.error = exc


def _execute_initial_assessment_batch(
    phase: _BatchSamplePhaseState,
    *,
    partition_candidates: bool = False,
) -> None:
    states = phase.states
    leader = states[0]
    job = leader.job
    raw = None
    trace_batch: UntargetedCidNlSampleTraceBatch | None = None
    try:
        if any(state.initial_requires_raw and state.error is None for state in states):
            if phase.initial_resource_key is None:
                raise UntargetedCidNlRealDataRuntimeError(
                    "sample batch is missing its acquisition reservation"
                )
            phase.initial_open_attempted = True
            raw = _open_job_raw(job)
            phase.initial_opened = True
            candidates = job.candidates
            if partition_candidates:
                selected_candidates = {
                    candidate.candidate_id: candidate
                    for state in states
                    if state.error is None
                    for candidate in _request_subject_candidates(state.job)
                }
                candidates = tuple(
                    sorted(
                        selected_candidates.values(),
                        key=lambda candidate: job._candidate_order[
                            candidate.candidate_id
                        ],
                    )
                )
            active_acquire = _runtime_delegate(
                "acquire_untargeted_cid_nl_sample_trace_batch",
                acquire_untargeted_cid_nl_sample_trace_batch,
            )
            trace_batch = active_acquire(
                processing_owners=job.processing_owners,
                sample=job.sample,
                candidates=candidates,
                settings=job.settings,
                raw=raw,
                signal_processing_profile=job.signal_processing_profile,
            )
            phase.initial_acquisition = (
                trace_batch.acquisition if trace_batch is not None else None
            )
        for state in states:
            if state.error is not None:
                continue
            state.adapter_result = adapt_current_discovery_sample_subject(
                processing_owners=state.job.processing_owners,
                sample=state.job.sample,
                method_subject=state.job.method_subject,
                candidates=_request_subject_candidates(state.job),
                support_candidates=state.job.support_candidates,
                settings=state.job.settings,
                peak_config=state.job.peak_config,
                raw=raw,
                _observer=state.observer,
                _preacquired_traces=trace_batch,
                signal_processing_profile=state.job.signal_processing_profile,
                physical_records=_request_subject_physical_records(state.job),
            )
    except RawReaderOpenError as exc:
        phase.initial_cleanup_outcome = (
            RuntimeResourceCleanupOutcome.FAILED
            if exc.cleanup_failed
            else RuntimeResourceCleanupOutcome.SUCCEEDED
        )
        for state in states:
            if state.error is None:
                state.error = exc
    except Exception as exc:
        for state in states:
            if state.error is None:
                state.error = exc
    finally:
        if raw is not None:
            if isinstance(raw, CachedUntargetedRaw):
                phase.initial_open_attempted = raw.native_open_attempted
                phase.initial_opened = raw.native_opened
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
                phase.initial_cleanup_outcome = RuntimeResourceCleanupOutcome.FAILED
                for state in states:
                    if state.error is None:
                        state.error = exc


def _prepare_group_authorized_backfills(
    states: list[_RuntimeJobState],
    *,
    prepared: UntargetedCidNlPreparedGrouping,
    sample_templates: tuple[UntargetedCidNlRealDataRuntimeJob, ...] = (),
) -> None:
    """Schedule searches from frozen workset membership, without regrouping."""
    templates = {
        job.sample.identity.sample_identity_id: job
        for job in sample_templates or tuple(state.job for state in states)
    }
    by_key = {
        (
            state.job.sample.identity.sample_identity_id,
            state.job.method_subject.method_subject_id,
        ): state
        for state in states
    }
    population = tuple(job.sample for job in templates.values())
    for workset in prepared.worksets:
        direct = {
            candidate.peak_hypothesis_id: candidate.direct_support_assessment
            for candidate in workset.retained_candidates
            if candidate.direct_support_assessment is not None
        }
        groups = tuple(
            group
            for group in workset.assignment_scope.groups
            if all(owner_id in direct for owner_id in group.owner_ids)
        )
        for group, target_ids in plan_conservative_backfill_targets(
            groups, sample_population=population
        ):
            subject = select_cid_nl_group_backfill_subject(
                group,
                tuple(
                    {
                        direct[owner_id].content.method_subject
                        for owner_id in group.owner_ids
                    }
                ),
            )
            assert group.group_center_rt is not None
            for sample_id in sorted(target_ids):
                key = (sample_id, subject.method_subject_id)
                if key not in by_key:
                    job = copy(templates[sample_id])
                    object.__setattr__(job, "method_subject", subject)
                    object.__setattr__(
                        job,
                        "work_unit_key",
                        f"untargeted-cid-nl:{job.sample.metadata.raw_stem}:"
                        f"{subject.method_subject_id}",
                    )
                    state = _RuntimeJobState(
                        job=job,
                        resource_key=None,
                        initial_requires_raw=False,
                        observer=_UntargetedRealDataWorkObserver(job.processing_owners),
                    )
                    _execute_initial_assessment(state)
                    states.append(state)
                    by_key[key] = state
                state = by_key[key]
                if state.error is not None or state.adapter_result is None:
                    continue
                assessment = state.adapter_result.assessment_input.assessment
                revisit = _group_revisit(assessment)
                if revisit is None:
                    continue
                if revisit == "reuse":
                    state.backfill_input = UntargetedCidNlBackfillInput(
                        sample=state.job.sample,
                        candidate_assessment=assessment,
                    )
                else:
                    state.backfill_targets = tuple(
                        sorted(
                            (
                                *state.backfill_targets,
                                UntargetedCidNlBackfillTarget(
                                    group_hypothesis_id=group.group_hypothesis_id,
                                    method_subject=subject,
                                    physical_mz=group.family_center_mz,
                                    center_rt=group.group_center_rt,
                                ),
                            ),
                            key=lambda target: (
                                target.center_rt,
                                target.group_hypothesis_id,
                            ),
                        )
                    )


def _group_revisit(
    assessment: CidNlMethodAssessmentRef,
) -> Literal["reuse", "search"] | None:
    """How a sample outside one group revisits that group's window.

    A HOLD with candidate peaks is reused; otherwise the window is searched.
    A SUPPORT belongs to another peak of the sample and does not answer the
    group's window.
    """
    if assessment.content.method_result == "SUPPORT":
        return "search"
    if not (
        assessment.content.method_result == "HOLD"
        or is_unsearched_cid_nl_discovery_assessment(assessment)
        or is_cid_nl_missing_ms1_rejection(assessment)
    ):
        return None
    return "reuse" if assessment.candidate_peaks else "search"


def _prepare_owner_authorized_backfills(
    states: list[_RuntimeJobState],
    *,
    alignment_config: AlignmentConfig,
    sample_templates: tuple[UntargetedCidNlRealDataRuntimeJob, ...] = (),
) -> None:
    by_subject: dict[str, list[_RuntimeJobState]] = {}
    for state in states:
        by_subject.setdefault(
            state.job.method_subject.method_subject_id,
            [],
        ).append(state)
    for method_subject_id in sorted(by_subject):
        states.extend(
            _prepare_subject_authorized_backfills(
                tuple(by_subject[method_subject_id]),
                alignment_config=alignment_config,
                sample_templates=sample_templates,
            )
        )


def _prepare_subject_authorized_backfills(
    states: tuple[_RuntimeJobState, ...],
    *,
    alignment_config: AlignmentConfig,
    sample_templates: tuple[UntargetedCidNlRealDataRuntimeJob, ...] = (),
) -> tuple[_RuntimeJobState, ...]:
    if any(state.error is not None or state.adapter_result is None for state in states):
        return ()
    adapter_results = tuple(
        state.adapter_result for state in states if state.adapter_result is not None
    )
    if not any(
        result.assessment_input.assessment.content.method_result == "SUPPORT"
        for result in adapter_results
    ):
        return ()
    support_owners: list[SampleLocalMS1Owner] = []
    for state in states:
        result = state.adapter_result
        if result is None:
            continue
        assessment = result.assessment_input.assessment
        if assessment.content.method_result != "SUPPORT":
            continue
        hypotheses = compose_cid_nl_peak_hypotheses_from_assessment(
            processing_owners=state.job.processing_owners,
            sample=state.job.sample,
            assessment=assessment,
        )
        support_owners.extend(
            owner_from_cid_nl_assessment_support(
                sample=state.job.sample, assessment=assessment, hypothesis=hypothesis
            )
            for hypothesis in hypotheses
        )
    groups = construct_cross_sample_peak_group_hypotheses(
        tuple(support_owners),
        config=alignment_config,
    )
    states_by_sample = {
        state.job.sample.identity.sample_identity_id: state for state in states
    }
    templates_by_sample = {
        job.sample.identity.sample_identity_id: job for job in sample_templates
    }
    population = (
        tuple(job.sample for job in sample_templates)
        if sample_templates
        else tuple(state.job.sample for state in states)
    )
    added_states: list[_RuntimeJobState] = []
    for group, target_sample_ids in plan_conservative_backfill_targets(
        groups, sample_population=population
    ):
        center = group.group_center_rt
        if center is None:
            raise UntargetedCidNlRealDataRuntimeError(
                "authorized backfill requires a frozen RT group"
            )
        for sample_id in sorted(target_sample_ids):
            if sample_id not in states_by_sample:
                job = copy(templates_by_sample[sample_id])
                subject = states[0].job.method_subject
                object.__setattr__(job, "method_subject", subject)
                object.__setattr__(
                    job,
                    "work_unit_key",
                    f"untargeted-cid-nl:{job.sample.metadata.raw_stem}:"
                    f"{subject.method_subject_id}",
                )
                state = _RuntimeJobState(
                    job=job,
                    resource_key=None,
                    initial_requires_raw=False,
                    observer=_UntargetedRealDataWorkObserver(job.processing_owners),
                )
                _execute_initial_assessment(state)
                states_by_sample[sample_id] = state
                added_states.append(state)
            state = states_by_sample[sample_id]
            if state.error is not None:
                continue
            result = state.adapter_result
            assert result is not None
            assessment = result.assessment_input.assessment
            revisit = _group_revisit(assessment)
            if revisit is None:
                continue
            if revisit == "reuse":
                state.backfill_input = UntargetedCidNlBackfillInput(
                    sample=state.job.sample,
                    candidate_assessment=assessment,
                )
            else:
                state.backfill_targets = tuple(
                    sorted(
                        (
                            *state.backfill_targets,
                            UntargetedCidNlBackfillTarget(
                                group_hypothesis_id=group.group_hypothesis_id,
                                method_subject=state.job.method_subject,
                                physical_mz=group.family_center_mz,
                                center_rt=center,
                            ),
                        ),
                        key=lambda target: (
                            target.center_rt,
                            target.group_hypothesis_id,
                        ),
                    )
                )
    return tuple(added_states)


def _execute_backfill(
    state: _RuntimeJobState,
) -> None:
    job = state.job
    ctx = state.backfill_support
    if ctx is None and state.adapter_result is not None:
        ctx = _backfill_support_context(state.adapter_result)
    if ctx is None:
        state.error = UntargetedCidNlRealDataRuntimeError(
            "backfill requires a completed initial assessment"
        )
        return
    if state.resource_key is None:
        state.error = UntargetedCidNlRealDataRuntimeError(
            "eligible backfill is missing its acquisition reservation"
        )
        return
    raw = None
    initial_open_attempted = state.open_attempted
    initial_opened = state.opened
    try:
        state.open_attempted = True
        raw = _open_job_raw(job)
        state.opened = True
        active_acquire = _runtime_delegate(
            "acquire_untargeted_cid_nl_backfill_trace_batch",
            acquire_untargeted_cid_nl_backfill_trace_batch,
        )
        trace_batch = active_acquire(
            processing_owners=job.processing_owners,
            sample=job.sample,
            backfill_requests=state.backfill_targets,
            peak_config=job.peak_config,
            raw=raw,
        )
        for target in state.backfill_targets:
            backfill = backfill_untargeted_cid_nl_sample_subject(
                processing_owners=job.processing_owners,
                sample=job.sample,
                method_subject=job.method_subject,
                group_center_rt=target.center_rt,
                group_center_mz=target.physical_mz,
                group_hypothesis_id=target.group_hypothesis_id,
                initial_assessment=ctx.initial_assessment,
                support_policy=ctx.support_policy,
                peak_config=job.peak_config,
                raw=raw,
                _observer=state.observer,
                settings=job.settings,
                signal_processing_profile=job.signal_processing_profile,
                _preacquired_traces=trace_batch,
            )
            state.backfill_attempts += (backfill,)
        if len(state.backfill_attempts) == 1:
            state.backfill_input = state.backfill_attempts[0].backfill_input
    except RawReaderOpenError as exc:
        state.cleanup_outcome = (
            RuntimeResourceCleanupOutcome.FAILED
            if exc.cleanup_failed
            else RuntimeResourceCleanupOutcome.SUCCEEDED
        )
        state.error = exc
    except Exception as exc:
        state.error = exc
    finally:
        if raw is not None:
            if isinstance(raw, CachedUntargetedRaw):
                state.open_attempted = (
                    initial_open_attempted or raw.native_open_attempted
                )
                state.opened = initial_opened or raw.native_opened
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
                state.cleanup_outcome = RuntimeResourceCleanupOutcome.FAILED
                if state.error is None:
                    state.error = exc


def _execute_backfill_batch(phase: _BatchSamplePhaseState) -> None:
    pending = tuple(
        sorted(
            (
                state
                for state in phase.states
                if state.error is None and state.backfill_targets
            ),
            key=lambda state: state.job.method_subject.method_subject_id,
        )
    )
    if not pending:
        return
    if phase.backfill_resource_key is None:
        error = UntargetedCidNlRealDataRuntimeError(
            "eligible backfill batch is missing its phase reservation"
        )
        for state in pending:
            state.error = error
        return
    first = pending[0].job
    raw = None
    trace_batch: UntargetedCidNlBackfillTraceBatch | None = None
    try:
        phase.backfill_open_attempted = True
        raw = _open_job_raw(first)
        phase.backfill_opened = True
        active_acquire = _runtime_delegate(
            "acquire_untargeted_cid_nl_backfill_trace_batch",
            acquire_untargeted_cid_nl_backfill_trace_batch,
        )
        trace_batch = active_acquire(
            processing_owners=first.processing_owners,
            sample=first.sample,
            backfill_requests=tuple(
                target for state in pending for target in state.backfill_targets
            ),
            peak_config=first.peak_config,
            raw=raw,
        )
        phase.backfill_acquisition = trace_batch.acquisition
        for state in pending:
            ctx = state.backfill_support
            if ctx is None and state.adapter_result is not None:
                ctx = _backfill_support_context(state.adapter_result)
            assert ctx is not None
            for target in state.backfill_targets:
                backfill = backfill_untargeted_cid_nl_sample_subject(
                    processing_owners=state.job.processing_owners,
                    sample=state.job.sample,
                    method_subject=state.job.method_subject,
                    group_center_rt=target.center_rt,
                    group_center_mz=target.physical_mz,
                    group_hypothesis_id=target.group_hypothesis_id,
                    initial_assessment=ctx.initial_assessment,
                    support_policy=ctx.support_policy,
                    peak_config=state.job.peak_config,
                    raw=raw if state.job.method_subject.composition else None,
                    settings=state.job.settings,
                    signal_processing_profile=state.job.signal_processing_profile,
                    _observer=state.observer,
                    _preacquired_traces=trace_batch,
                )
                state.backfill_attempts += (backfill,)
            if len(state.backfill_attempts) == 1:
                state.backfill_input = state.backfill_attempts[0].backfill_input
    except RawReaderOpenError as exc:
        phase.backfill_cleanup_outcome = (
            RuntimeResourceCleanupOutcome.FAILED
            if exc.cleanup_failed
            else RuntimeResourceCleanupOutcome.SUCCEEDED
        )
        for state in pending:
            state.error = exc
    except Exception as exc:
        for state in pending:
            state.error = exc
    finally:
        if raw is not None:
            if isinstance(raw, CachedUntargetedRaw):
                phase.backfill_open_attempted = raw.native_open_attempted
                phase.backfill_opened = raw.native_opened
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
                phase.backfill_cleanup_outcome = RuntimeResourceCleanupOutcome.FAILED
                for state in pending:
                    if state.error is None:
                        state.error = exc


@dataclass(slots=True)
class _SampleInitialWorkerRequest:
    jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...]
    initial_resource_key: str | None
    partition_candidates: bool


@dataclass(slots=True)
class _SampleInitialWorkerResponse:
    adapter_results: tuple[UntargetedCidNlRealDataAdapterResult | None, ...]
    observer_artifacts: tuple[tuple[EvidenceArtifactRef, ...], ...]
    observer_has_acquisitions: tuple[bool, ...]
    initial_open_attempted: bool
    initial_opened: bool
    initial_cleanup_outcome: RuntimeResourceCleanupOutcome
    initial_acquisition: TraceAcquisitionBatch | None
    errors: tuple[Exception | None, ...]


def _execute_sample_initial_worker(
    request: _SampleInitialWorkerRequest,
) -> _SampleInitialWorkerResponse:
    first_job = request.jobs[0]
    _ensure_assemblies_loaded(first_job.dll_dir, importlib.import_module)
    states = tuple(
        _RuntimeJobState(
            job=job,
            resource_key=None,
            initial_requires_raw=bool(_request_subject_candidates(job)),
            observer=_UntargetedRealDataWorkObserver(job.processing_owners),
        )
        for job in request.jobs
    )
    phase = _BatchSamplePhaseState(
        states=states,
        initial_resource_key=request.initial_resource_key,
        backfill_resource_key=None,
    )
    _execute_initial_assessment_batch(
        phase,
        partition_candidates=request.partition_candidates,
    )
    return _SampleInitialWorkerResponse(
        adapter_results=tuple(s.adapter_result for s in states),
        observer_artifacts=tuple(s.observer.observed_artifacts() for s in states),
        observer_has_acquisitions=tuple(
            s.observer.acquisition is not None for s in states
        ),
        initial_open_attempted=phase.initial_open_attempted,
        initial_opened=phase.initial_opened,
        initial_cleanup_outcome=phase.initial_cleanup_outcome,
        initial_acquisition=phase.initial_acquisition,
        errors=tuple(s.error for s in states),
    )


@dataclass(slots=True)
class _SampleBackfillWorkerRequest:
    jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...]
    backfill_targets_by_key: dict[str, tuple[UntargetedCidNlBackfillTarget, ...]]
    backfill_inputs_by_key: dict[str, UntargetedCidNlBackfillInput | None]
    backfill_support_by_key: dict[str, _BackfillSupportContext]
    backfill_resource_key: str | None


@dataclass(slots=True)
class _SampleBackfillWorkerResponse:
    work_unit_keys: tuple[str, ...]
    backfill_attempts_by_key: dict[
        str, tuple[UntargetedCidNlBackfillAttemptResult, ...]
    ]
    backfill_inputs_by_key: dict[str, UntargetedCidNlBackfillInput | None]
    observer_artifacts: tuple[tuple[EvidenceArtifactRef, ...], ...]
    observer_has_acquisitions: tuple[bool, ...]
    backfill_open_attempted: bool
    backfill_opened: bool
    backfill_cleanup_outcome: RuntimeResourceCleanupOutcome
    backfill_acquisition: TraceAcquisitionBatch | None
    errors: tuple[Exception | None, ...]


def _execute_sample_backfill_worker(
    request: _SampleBackfillWorkerRequest,
) -> _SampleBackfillWorkerResponse:
    first_job = request.jobs[0]
    _ensure_assemblies_loaded(first_job.dll_dir, importlib.import_module)
    states = tuple(
        _RuntimeJobState(
            job=job,
            resource_key=None,
            initial_requires_raw=False,
            observer=_UntargetedRealDataWorkObserver(job.processing_owners),
            backfill_support=request.backfill_support_by_key.get(job.work_unit_key),
            backfill_input=request.backfill_inputs_by_key.get(job.work_unit_key),
            backfill_targets=request.backfill_targets_by_key.get(job.work_unit_key, ()),
        )
        for job in request.jobs
    )
    phase = _BatchSamplePhaseState(
        states=states,
        initial_resource_key=None,
        backfill_resource_key=request.backfill_resource_key,
    )
    _execute_backfill_batch(phase)
    return _SampleBackfillWorkerResponse(
        work_unit_keys=tuple(state.job.work_unit_key for state in states),
        backfill_attempts_by_key={
            s.job.work_unit_key: s.backfill_attempts
            for s in states
            if s.backfill_attempts
        },
        backfill_inputs_by_key={
            s.job.work_unit_key: s.backfill_input
            for s in states
            if s.backfill_input is not None
        },
        observer_artifacts=tuple(s.observer.observed_artifacts() for s in states),
        observer_has_acquisitions=tuple(
            s.observer.acquisition is not None for s in states
        ),
        backfill_open_attempted=phase.backfill_open_attempted,
        backfill_opened=phase.backfill_opened,
        backfill_cleanup_outcome=phase.backfill_cleanup_outcome,
        backfill_acquisition=phase.backfill_acquisition,
        errors=tuple(s.error for s in states),
    )


__all__ = [
    "_BatchSamplePhaseState",
    "_RuntimeJobState",
    "_SampleInitialWorkerRequest",
    "_SampleInitialWorkerResponse",
    "_SampleBackfillWorkerRequest",
    "_SampleBackfillWorkerResponse",
    "_execute_initial_assessment",
    "_execute_initial_assessment_batch",
    "_execute_backfill",
    "_execute_backfill_batch",
    "_execute_sample_initial_worker",
    "_execute_sample_backfill_worker",
    "_open_job_raw",
    "_prepare_owner_authorized_backfills",
    "_prepare_subject_authorized_backfills",
    "_backfill_support_context",
]
