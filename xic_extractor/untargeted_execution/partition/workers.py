"""Acquire and assess Untargeted samples in-process or through worker transport."""

from __future__ import annotations

import importlib
from copy import copy
from dataclasses import dataclass, replace

from xic_extractor.alignment.backfill.cid_nl_planning import (
    CidNlBackfillDecision,
    CidNlBackfillQuestion,
    plan_grouped_cid_nl_backfills,
    plan_subject_cid_nl_backfills,
)
from xic_extractor.alignment.cid_nl_inputs import (
    UntargetedCidNlBackfillInput,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    is_cid_nl_missing_ms1_rejection,
)
from xic_extractor.peak_detection.cid_nl_peak_support_models import (
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.processing_identity import resolve_request_acquisition_source
from xic_extractor.raw_reader import (
    RawFileHandle,
    bind_expected_path_identity,
    ensure_assemblies_loaded,
    open_raw,
)
from xic_extractor.untargeted_execution.acquisition_cache import (
    CachedUntargetedRaw,
    bind_untargeted_acquisition_cache,
)
from xic_extractor.untargeted_execution.partition import cid_nl_acquisition
from xic_extractor.untargeted_execution.partition.cid_nl_acquisition import (
    CidNlAcquisitionResult,
    UntargetedCidNlBackfillAttemptResult,
    UntargetedCidNlBackfillTarget,
    UntargetedCidNlBackfillTraceBatch,
    UntargetedCidNlSampleTraceBatch,
    adapt_current_discovery_sample_subject,
    backfill_untargeted_cid_nl_sample_subject,
)
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedPartitionError,
    UntargetedPartitionJob,
    UntargetedPreparedGrouping,
    _request_subject_candidates,
    _request_subject_physical_records,
)


@dataclass(slots=True)
class _RuntimeJobState:
    job: UntargetedPartitionJob
    resource_key: str | None
    initial_requires_raw: bool
    error: Exception | None = None
    adapter_result: CidNlAcquisitionResult | None = None
    backfill_support: _BackfillSupportContext | None = None
    backfill_input: UntargetedCidNlBackfillInput | None = None
    backfill_attempts: tuple[UntargetedCidNlBackfillAttemptResult, ...] = ()
    backfill_targets: tuple[UntargetedCidNlBackfillTarget, ...] = ()

    def apply_backfill_decision(self, decision: CidNlBackfillDecision) -> None:
        """Translate a resolved scientific decision into runtime work only.

        Initial assessments already carry reused HOLD candidates. Once any group
        searches, the scalar reuse carrier yields to the per-group attempts.
        """
        if decision.action == "reuse" and not self.backfill_targets:
            self.backfill_input = UntargetedCidNlBackfillInput(
                sample=self.job.sample, candidate_assessment=decision.assessment
            )
        elif decision.action == "search":
            self.backfill_input = None
            target = UntargetedCidNlBackfillTarget.from_question(decision.question)
            self.backfill_targets = tuple(
                sorted(
                    (*self.backfill_targets, target),
                    key=lambda item: (item.center_rt, item.group_hypothesis_id),
                )
            )


@dataclass(frozen=True, slots=True)
class _BackfillSupportContext:
    """Narrow backfill-only projection of initial assessment facts.

    Backfill execution consumes only the support policy and, when the
    initial attempt was a missing-MS1 rejection, that initial assessment.
    The full ``UntargetedCidNlRealDataAdapterResult`` — including
    acquisition, captures, and capture evidence — is
    not needed by the backfill worker and must not be transported across
    the process boundary.  ``initial_assessment`` stays ``None`` whenever
    backfill would not consume it, so the assessment reference graph is
    not transported either.
    """

    initial_assessment: CidNlMethodAssessmentRef | None
    support_policy: DdaCidNlPeakSupportPolicy


def _backfill_support_context(
    result: CidNlAcquisitionResult,
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
    sample_template: UntargetedPartitionJob | None = None

    @property
    def job(self) -> UntargetedPartitionJob:
        if self.sample_template is not None:
            return self.sample_template
        return self.states[0].job

    def isolated_worker_state(self) -> _BatchSamplePhaseState:
        """Keep in-process work behind the same outcome boundary as a process.

        Domain inputs stay shared; only mutable phase/job state is local to this
        phase's execution.
        """
        return replace(
            self,
            states=tuple(replace(state) for state in self.states),
        )


def _open_job_raw(
    job: UntargetedPartitionJob,
) -> RawFileHandle | CachedUntargetedRaw:
    active_open = open_raw
    expectation = job.acquisition_source_expectation
    source = (
        expectation.source
        if expectation is not None
        else resolve_request_acquisition_source(
            processing_owners=job.processing_owners, sample=job.sample
        )
    )

    mass_recalibration = job.settings.mass_recalibration

    def open_native():
        if expectation is not None:
            return active_open(
                job.raw_path,
                job.dll_dir,
                mass_recalibration=mass_recalibration,
                expected_acquisition_source=source,
                _expected_path_identity=expectation.path_identity,
                prepared_mass_recalibration=job.prepared_mass_recalibration,
            )
        return active_open(
            job.raw_path,
            job.dll_dir,
            mass_recalibration=mass_recalibration,
            expected_acquisition_source=source,
            prepared_mass_recalibration=job.prepared_mass_recalibration,
        )

    if expectation is not None and job.acquisition_cache_dir is not None:
        # The input owner already verified the content. Cache replay still checks
        # that this path has that exact identity before returning any facts.
        bind_expected_path_identity(job.raw_path, source, expectation.path_identity)
        return CachedUntargetedRaw(
            open_native,
            source=source,
            cache_dir=job.acquisition_cache_dir,
            mass_recalibration=mass_recalibration,
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
                raise UntargetedPartitionError(
                    "bounded subject is missing its acquisition reservation"
                )
            raw = _open_job_raw(job)
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
                signal_processing_profile=job.signal_processing_profile,
                physical_records=_request_subject_physical_records(job),
            )
    except Exception as exc:
        state.error = exc
    finally:
        if raw is not None:
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
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
                raise UntargetedPartitionError(
                    "sample batch is missing its acquisition reservation"
                )
            raw = _open_job_raw(job)
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
            active_acquire = (
                cid_nl_acquisition.acquire_untargeted_cid_nl_sample_trace_batch
            )
            trace_batch = active_acquire(
                processing_owners=job.processing_owners,
                sample=job.sample,
                candidates=candidates,
                settings=job.settings,
                raw=raw,
                signal_processing_profile=job.signal_processing_profile,
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
                _preacquired_traces=trace_batch,
                signal_processing_profile=state.job.signal_processing_profile,
                physical_records=_request_subject_physical_records(state.job),
            )
    except Exception as exc:
        for state in states:
            if state.error is None:
                state.error = exc
    finally:
        if raw is not None:
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
                for state in states:
                    if state.error is None:
                        state.error = exc


def _apply_backfill_questions(
    states: list[_RuntimeJobState],
    templates: dict[str, UntargetedPartitionJob],
    questions: tuple[CidNlBackfillQuestion, ...],
) -> None:
    by_key = {
        (
            state.job.sample.identity.sample_key,
            state.job.method_subject.method_subject_id,
        ): state
        for state in states
    }
    for question in questions:
        key = (question.sample_key, question.method_subject.method_subject_id)
        if key not in by_key:
            job = copy(templates[question.sample_key])
            object.__setattr__(job, "method_subject", question.method_subject)
            object.__setattr__(
                job,
                "work_unit_key",
                f"untargeted-cid-nl:{job.sample.metadata.raw_stem}:"
                f"{question.method_subject.method_subject_id}",
            )
            state = _RuntimeJobState(
                job=job,
                resource_key=None,
                initial_requires_raw=False,
            )
            _execute_initial_assessment(state)
            states.append(state)
            by_key[key] = state
        state = by_key[key]
        if state.error is not None or state.adapter_result is None:
            continue
        state.apply_backfill_decision(
            question.resolve(state.adapter_result.assessment_input.assessment)
        )


def _prepare_group_authorized_backfills(
    states: list[_RuntimeJobState],
    *,
    prepared: UntargetedPreparedGrouping,
    sample_templates: tuple[UntargetedPartitionJob, ...] = (),
) -> None:
    templates = {
        job.sample.identity.sample_key: job
        for job in sample_templates or tuple(state.job for state in states)
    }
    questions = plan_grouped_cid_nl_backfills(
        prepared.worksets,
        sample_population=tuple(job.sample for job in templates.values()),
        alignment_config=prepared.alignment_config,
        rt_frame=prepared.rt_frame,
        half_windows_by_sample={
            sample_id: job.peak_config.nl_rt_anchor_half_window_min
            for sample_id, job in templates.items()
        },
    )
    _apply_backfill_questions(states, templates, questions)


def _prepare_owner_authorized_backfills(
    states: list[_RuntimeJobState],
    *,
    alignment_config: AlignmentConfig,
    sample_templates: tuple[UntargetedPartitionJob, ...] = (),
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
    sample_templates: tuple[UntargetedPartitionJob, ...] = (),
) -> tuple[_RuntimeJobState, ...]:
    if not states or any(
        state.error is not None or state.adapter_result is None for state in states
    ):
        return ()
    templates = {
        job.sample.identity.sample_key: job
        for job in sample_templates or tuple(state.job for state in states)
    }
    questions = plan_subject_cid_nl_backfills(
        tuple(
            (state.job.sample, state.adapter_result.assessment_input.assessment)
            for state in states
            if state.adapter_result is not None
        ),
        method_subject=states[0].job.method_subject,
        processing_owners=states[0].job.processing_owners,
        sample_population=tuple(job.sample for job in templates.values()),
        alignment_config=alignment_config,
    )
    prepared_states = list(states)
    _apply_backfill_questions(prepared_states, templates, questions)
    return tuple(prepared_states[len(states) :])


def _execute_backfill(
    state: _RuntimeJobState,
) -> None:
    job = state.job
    ctx = state.backfill_support
    if ctx is None and state.adapter_result is not None:
        ctx = _backfill_support_context(state.adapter_result)
    if ctx is None:
        state.error = UntargetedPartitionError(
            "backfill requires a completed initial assessment"
        )
        return
    if state.resource_key is None:
        state.error = UntargetedPartitionError(
            "eligible backfill is missing its acquisition reservation"
        )
        return
    raw = None
    try:
        raw = _open_job_raw(job)
        active_acquire = (
            cid_nl_acquisition.acquire_untargeted_cid_nl_backfill_trace_batch
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
                query_rt=target.sample_query_rt,
                query_rt_basis=target.query_rt_basis,
                query_rt_tolerance_sec=target.query_rt_tolerance_sec,
                query_rt_reliable=target.query_rt_reliable,
                group_center_mz=target.physical_mz,
                group_hypothesis_id=target.group_hypothesis_id,
                initial_assessment=ctx.initial_assessment,
                support_policy=ctx.support_policy,
                peak_config=job.peak_config,
                raw=raw,
                settings=job.settings,
                signal_processing_profile=job.signal_processing_profile,
                _preacquired_traces=trace_batch,
            )
            state.backfill_attempts += (backfill,)
        if len(state.backfill_attempts) == 1:
            state.backfill_input = state.backfill_attempts[0].backfill_input
    except Exception as exc:
        state.error = exc
    finally:
        if raw is not None:
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
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
        error = UntargetedPartitionError(
            "eligible backfill batch is missing its phase reservation"
        )
        for state in pending:
            state.error = error
        return
    first = pending[0].job
    raw = None
    trace_batch: UntargetedCidNlBackfillTraceBatch | None = None
    try:
        raw = _open_job_raw(first)
        active_acquire = (
            cid_nl_acquisition.acquire_untargeted_cid_nl_backfill_trace_batch
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
                    query_rt=target.sample_query_rt,
                    query_rt_basis=target.query_rt_basis,
                    query_rt_tolerance_sec=target.query_rt_tolerance_sec,
                    query_rt_reliable=target.query_rt_reliable,
                    group_center_mz=target.physical_mz,
                    group_hypothesis_id=target.group_hypothesis_id,
                    initial_assessment=ctx.initial_assessment,
                    support_policy=ctx.support_policy,
                    peak_config=state.job.peak_config,
                    raw=raw if state.job.method_subject.composition else None,
                    settings=state.job.settings,
                    signal_processing_profile=state.job.signal_processing_profile,
                    _preacquired_traces=trace_batch,
                )
                state.backfill_attempts += (backfill,)
            if len(state.backfill_attempts) == 1:
                state.backfill_input = state.backfill_attempts[0].backfill_input
    except Exception as exc:
        for state in pending:
            state.error = exc
    finally:
        if raw is not None:
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
                for state in pending:
                    if state.error is None:
                        state.error = exc


@dataclass(slots=True)
class _SampleInitialWorkerRequest:
    jobs: tuple[UntargetedPartitionJob, ...]
    initial_resource_key: str | None
    partition_candidates: bool


@dataclass(frozen=True, slots=True)
class _InitialJobOutcome:
    work_unit_key: str
    adapter_result: CidNlAcquisitionResult | None
    error: Exception | None


@dataclass(frozen=True, slots=True)
class _SampleInitialWorkerResponse:
    job_outcomes: tuple[_InitialJobOutcome, ...]

    def _outcomes_by_key(
        self, work_unit_keys: tuple[str, ...]
    ) -> dict[str, _InitialJobOutcome]:
        _require_outcome_keys(
            work_unit_keys, tuple(item.work_unit_key for item in self.job_outcomes)
        )
        return {item.work_unit_key: item for item in self.job_outcomes}

    def apply_to(self, phase: _BatchSamplePhaseState) -> None:
        outcomes = self._outcomes_by_key(
            tuple(state.job.work_unit_key for state in phase.states)
        )
        for state in phase.states:
            outcome = outcomes[state.job.work_unit_key]
            state.adapter_result = outcome.adapter_result
            if state.error is None:
                state.error = outcome.error

    def results_for(
        self, jobs: tuple[UntargetedPartitionJob, ...]
    ) -> tuple[CidNlAcquisitionResult, ...]:
        outcomes = self._outcomes_by_key(tuple(job.work_unit_key for job in jobs))
        results = []
        for job in jobs:
            outcome = outcomes[job.work_unit_key]
            if outcome.error is not None:
                raise outcome.error
            if outcome.adapter_result is None:
                raise UntargetedPartitionError("initial assessment returned no result")
            results.append(outcome.adapter_result)
        return tuple(results)


def _require_outcome_keys(expected: tuple[str, ...], actual: tuple[str, ...]) -> None:
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise UntargetedPartitionError(
            "worker outcome must cover its exact work-unit population"
        )


def _execute_initial_sample_phase(
    phase: _BatchSamplePhaseState, *, partition_candidates: bool = False
) -> _SampleInitialWorkerResponse:
    local = phase.isolated_worker_state()
    _execute_initial_assessment_batch(local, partition_candidates=partition_candidates)
    return _SampleInitialWorkerResponse(
        job_outcomes=tuple(
            _InitialJobOutcome(
                work_unit_key=state.job.work_unit_key,
                adapter_result=state.adapter_result,
                error=state.error,
            )
            for state in local.states
        ),
    )


def _execute_sample_initial_worker(
    request: _SampleInitialWorkerRequest,
) -> _SampleInitialWorkerResponse:
    first_job = request.jobs[0]
    ensure_assemblies_loaded(first_job.dll_dir, importlib.import_module)
    states = tuple(
        _RuntimeJobState(
            job=job,
            resource_key=None,
            initial_requires_raw=bool(_request_subject_candidates(job)),
        )
        for job in request.jobs
    )
    phase = _BatchSamplePhaseState(
        states=states,
        initial_resource_key=request.initial_resource_key,
        backfill_resource_key=None,
    )
    return _execute_initial_sample_phase(
        phase,
        partition_candidates=request.partition_candidates,
    )


def assess_initial_sample_jobs(
    jobs: tuple[UntargetedPartitionJob, ...],
) -> tuple[CidNlAcquisitionResult, ...]:
    """Use the production initial worker without scheduling missing-cell work."""
    response = _execute_sample_initial_worker(
        _SampleInitialWorkerRequest(
            jobs=jobs,
            initial_resource_key=(
                f"grouping-experiment:{jobs[0].sample.identity.sample_key}"
            ),
            partition_candidates=True,
        )
    )
    return response.results_for(jobs)


@dataclass(slots=True)
class _SampleBackfillWorkerRequest:
    jobs: tuple[UntargetedPartitionJob, ...]
    backfill_targets_by_key: dict[str, tuple[UntargetedCidNlBackfillTarget, ...]]
    backfill_support_by_key: dict[str, _BackfillSupportContext]
    backfill_resource_key: str | None


@dataclass(frozen=True, slots=True)
class _BackfillJobOutcome:
    work_unit_key: str
    backfill_attempts: tuple[UntargetedCidNlBackfillAttemptResult, ...]
    backfill_input: UntargetedCidNlBackfillInput | None
    error: Exception | None


@dataclass(frozen=True, slots=True)
class _SampleBackfillWorkerResponse:
    job_outcomes: tuple[_BackfillJobOutcome, ...]

    def apply_to(self, phase: _BatchSamplePhaseState) -> None:
        states = {
            state.job.work_unit_key: state
            for state in phase.states
            if state.error is None and state.backfill_targets
        }
        _require_outcome_keys(
            tuple(states), tuple(item.work_unit_key for item in self.job_outcomes)
        )
        for outcome in self.job_outcomes:
            state = states[outcome.work_unit_key]
            if outcome.backfill_attempts:
                state.backfill_attempts = outcome.backfill_attempts
            if outcome.backfill_input is not None:
                state.backfill_input = outcome.backfill_input
            if state.error is None:
                state.error = outcome.error


def _execute_backfill_sample_phase(
    phase: _BatchSamplePhaseState,
) -> _SampleBackfillWorkerResponse:
    local = phase.isolated_worker_state()
    pending = tuple(
        state
        for state in local.states
        if state.error is None and state.backfill_targets
    )
    _execute_backfill_batch(local)
    return _SampleBackfillWorkerResponse(
        job_outcomes=tuple(
            _BackfillJobOutcome(
                work_unit_key=state.job.work_unit_key,
                backfill_attempts=state.backfill_attempts,
                backfill_input=state.backfill_input,
                error=state.error,
            )
            for state in pending
        ),
    )


def _execute_sample_backfill_worker(
    request: _SampleBackfillWorkerRequest,
) -> _SampleBackfillWorkerResponse:
    first_job = request.jobs[0]
    ensure_assemblies_loaded(first_job.dll_dir, importlib.import_module)
    states = tuple(
        _RuntimeJobState(
            job=job,
            resource_key=None,
            initial_requires_raw=False,
            backfill_support=request.backfill_support_by_key.get(job.work_unit_key),
            backfill_targets=request.backfill_targets_by_key.get(job.work_unit_key, ()),
        )
        for job in request.jobs
    )
    phase = _BatchSamplePhaseState(
        states=states,
        initial_resource_key=None,
        backfill_resource_key=request.backfill_resource_key,
    )
    return _execute_backfill_sample_phase(phase)


__all__ = [
    "_BatchSamplePhaseState",
    "_RuntimeJobState",
    "_SampleInitialWorkerRequest",
    "_SampleInitialWorkerResponse",
    "_SampleBackfillWorkerRequest",
    "_SampleBackfillWorkerResponse",
    "_execute_initial_assessment",
    "_execute_initial_assessment_batch",
    "_execute_initial_sample_phase",
    "_execute_backfill",
    "_execute_backfill_batch",
    "_execute_backfill_sample_phase",
    "_execute_sample_initial_worker",
    "_execute_sample_backfill_worker",
    "_open_job_raw",
    "_prepare_owner_authorized_backfills",
    "_prepare_subject_authorized_backfills",
    "_backfill_support_context",
]
