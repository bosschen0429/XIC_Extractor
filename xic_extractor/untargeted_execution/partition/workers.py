"""Acquire and assess Untargeted samples in-process or through worker transport."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, replace

from xic_extractor.alignment.backfill.cid_nl_planning import (
    CidNlBackfillDecision,
    CidNlBackfillQuestion,
    plan_grouped_cid_nl_backfills,
    plan_subject_cid_nl_backfills,
)
from xic_extractor.alignment.cid_nl_initial_grouping import CidNlGrouping
from xic_extractor.alignment.cid_nl_inputs import (
    UntargetedCidNlBackfillInput,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    is_cid_nl_missing_ms1_rejection,
)
from xic_extractor.peak_detection.cid_nl_peak_support_models import (
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.raw_reader import RawFileHandle, ensure_assemblies_loaded, open_raw
from xic_extractor.untargeted_execution.partition import (
    cid_nl_backfill_acquisition,
)
from xic_extractor.untargeted_execution.partition.cid_nl_backfill_acquisition import (
    UntargetedCidNlBackfillAttemptResult,
    UntargetedCidNlBackfillTarget,
    UntargetedCidNlBackfillTraceBatch,
    backfill_untargeted_cid_nl_sample_subject,
    reuse_cid_nl_backfill_peak,
)
from xic_extractor.untargeted_execution.partition.cid_nl_region_acquisition import (
    CidNlSampleRegion,
    DiscoveryMassRegion,
    acquire_cid_nl_sample_regions,
)
from xic_extractor.untargeted_execution.partition.cid_nl_sample_acquisition import (
    CidNlAcquisitionResult,
    assess_sample_regions,
)
from xic_extractor.untargeted_execution.partition.models import (
    BACKFILL_HALF_WINDOW_MIN,
    UntargetedPartitionError,
    UntargetedPartitionJob,
    _request_subject_candidates,
)


@dataclass(slots=True)
class _RuntimeJobState:
    job: UntargetedPartitionJob
    error: Exception | None = None
    adapter_result: CidNlAcquisitionResult | None = None
    backfill_support: _BackfillSupportContext | None = None
    backfill_attempts: tuple[UntargetedCidNlBackfillAttemptResult, ...] = ()
    backfill_targets: tuple[UntargetedCidNlBackfillTarget, ...] = ()

    @property
    def backfill_input(self) -> UntargetedCidNlBackfillInput | None:
        if len(self.backfill_attempts) == 1:
            return self.backfill_attempts[0].backfill_input
        return None

    def apply_backfill_decision(self, decision: CidNlBackfillDecision) -> None:
        """Keep a measured revisit bound to the group which authorized it."""
        target = UntargetedCidNlBackfillTarget.from_question(decision.question)
        if decision.action == "reuse":
            assert self.adapter_result is not None
            attempt = reuse_cid_nl_backfill_peak(
                target=target,
                capture=self.adapter_result,
                source=self.job.acquisition_source_expectation.source,
                half_window_min=BACKFILL_HALF_WINDOW_MIN,
            )
            if attempt is not None:
                self.backfill_attempts += (attempt,)
        elif decision.action == "search":
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

    initial_assessment: CidNlMethodAssessment | None
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
        support_policy=assessment.evaluation_receipt.support_policy,
    )


@dataclass(slots=True)
class _BatchSamplePhaseState:
    states: tuple[_RuntimeJobState, ...]
    initial_resource_key: str | None
    backfill_resource_key: str | None
    regions: tuple[DiscoveryMassRegion, ...] = ()

    @property
    def job(self) -> UntargetedPartitionJob:
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


def _open_job_raw(job: UntargetedPartitionJob) -> RawFileHandle:
    expectation = job.acquisition_source_expectation
    return open_raw(
        job.raw_path,
        job.dll_dir,
        mass_recalibration=job.settings.mass_recalibration,
        expected_acquisition_source=expectation.source,
        _expected_path_identity=expectation.path_identity,
        prepared_mass_recalibration=job.prepared_mass_recalibration,
    )


def _execute_initial_assessment_batch(
    phase: _BatchSamplePhaseState,
) -> None:
    states = phase.states
    job = phase.job
    raw = None
    try:
        regions: tuple[CidNlSampleRegion, ...] = ()
        if phase.regions and any(state.error is None for state in states):
            if phase.initial_resource_key is None:
                raise UntargetedPartitionError(
                    "sample batch is missing its acquisition reservation"
                )
            raw = _open_job_raw(job)
            regions = acquire_cid_nl_sample_regions(
                raw,
                sample=job.sample,
                acquisition_source=job.acquisition_source_expectation.source,
                regions=phase.regions,
                events=(
                    event
                    for candidate in (*job.candidates, *job.support_candidates)
                    for event in candidate.located_events
                ),
                settings=job.settings,
                profile=job.signal_processing_profile,
            )
            for region in regions:
                for coverage in region.coverage:
                    if coverage.scan_bounds is None:
                        raise UntargetedPartitionError(
                            f"{job.sample.raw_stem}: MS1 acquisition at "
                            f"{coverage.request.mz}: {coverage.reason}"
                        )
        results = assess_sample_regions(
            regions=regions,
            sample=job.sample,
            acquisition_source=job.acquisition_source_expectation.source,
            scopes=tuple(
                (state.job.method_subject, _request_subject_candidates(state.job))
                for state in states
                if state.error is None
            ),
            processing_attempt_id=job.processing_attempt_id,
            settings=job.settings,
            raw=raw,
        )
        for state in states:
            if state.error is None:
                state.adapter_result = results[
                    state.job.method_subject.method_subject_id
                ]
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
    questions: tuple[CidNlBackfillQuestion, ...],
) -> None:
    by_key = {
        (
            state.job.sample.sample_name,
            state.job.method_subject.method_subject_id,
        ): state
        for state in states
    }
    for question in questions:
        key = (question.sample_key, question.method_subject.method_subject_id)
        state = by_key[key]
        if state.error is not None or state.adapter_result is None:
            continue
        state.apply_backfill_decision(
            question.resolve(state.adapter_result.assessment_input.assessment)
        )


def _prepare_group_authorized_backfills(
    states: list[_RuntimeJobState],
    *,
    prepared: CidNlGrouping,
) -> None:
    samples = {state.job.sample.sample_name: state.job.sample for state in states}
    questions = plan_grouped_cid_nl_backfills(
        prepared.worksets,
        sample_population=tuple(samples.values()),
        alignment_config=prepared.alignment_config,
        rt_frame=prepared.rt_frame,
        backfill_half_window_min=BACKFILL_HALF_WINDOW_MIN,
    )
    _apply_backfill_questions(states, questions)


def _prepare_owner_authorized_backfills(
    states: list[_RuntimeJobState],
    *,
    alignment_config: AlignmentConfig,
) -> None:
    by_subject: dict[str, list[_RuntimeJobState]] = {}
    for state in states:
        by_subject.setdefault(
            state.job.method_subject.method_subject_id,
            [],
        ).append(state)
    for method_subject_id in sorted(by_subject):
        _prepare_subject_authorized_backfills(
            tuple(by_subject[method_subject_id]), alignment_config=alignment_config
        )


def _prepare_subject_authorized_backfills(
    states: tuple[_RuntimeJobState, ...],
    *,
    alignment_config: AlignmentConfig,
) -> None:
    if not states or any(
        state.error is not None or state.adapter_result is None for state in states
    ):
        return
    samples = {state.job.sample.sample_name: state.job.sample for state in states}
    questions = plan_subject_cid_nl_backfills(
        tuple(
            (state.job.sample, state.adapter_result.assessment_input.assessment)
            for state in states
            if state.adapter_result is not None
        ),
        method_subject=states[0].job.method_subject,
        sample_population=tuple(samples.values()),
        alignment_config=alignment_config,
    )
    _apply_backfill_questions(list(states), questions)


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
            cid_nl_backfill_acquisition.acquire_untargeted_cid_nl_backfill_trace_batch
        )
        trace_batch = active_acquire(
            sample=first.sample,
            acquisition_source=first.acquisition_source_expectation.source,
            backfill_requests=tuple(
                target for state in pending for target in state.backfill_targets
            ),
            half_window_min=BACKFILL_HALF_WINDOW_MIN,
            raw=raw,
        )
        for state in pending:
            ctx = state.backfill_support
            if ctx is None and state.adapter_result is not None:
                ctx = _backfill_support_context(state.adapter_result)
            assert ctx is not None
            for target in state.backfill_targets:
                backfill = backfill_untargeted_cid_nl_sample_subject(
                    sample=state.job.sample,
                    acquisition_source=state.job.acquisition_source_expectation.source,
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
                    half_window_min=BACKFILL_HALF_WINDOW_MIN,
                    raw=raw if state.job.method_subject.composition else None,
                    settings=state.job.settings,
                    signal_processing_profile=state.job.signal_processing_profile,
                    _preacquired_traces=trace_batch,
                )
                state.backfill_attempts += (backfill,)
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
    regions: tuple[DiscoveryMassRegion, ...]


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


def _require_outcome_keys(expected: tuple[str, ...], actual: tuple[str, ...]) -> None:
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise UntargetedPartitionError(
            "worker outcome must cover its exact work-unit population"
        )


def _execute_initial_sample_phase(
    phase: _BatchSamplePhaseState,
) -> _SampleInitialWorkerResponse:
    local = phase.isolated_worker_state()
    _execute_initial_assessment_batch(local)
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
        )
        for job in request.jobs
    )
    phase = _BatchSamplePhaseState(
        states=states,
        initial_resource_key=request.initial_resource_key,
        backfill_resource_key=None,
        regions=request.regions,
    )
    return _execute_initial_sample_phase(phase)


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
            state.backfill_attempts += outcome.backfill_attempts
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
    for state in pending:
        state.backfill_attempts = ()
    _execute_backfill_batch(local)
    return _SampleBackfillWorkerResponse(
        job_outcomes=tuple(
            _BackfillJobOutcome(
                work_unit_key=state.job.work_unit_key,
                backfill_attempts=state.backfill_attempts,
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
    "_execute_initial_assessment_batch",
    "_execute_initial_sample_phase",
    "_execute_backfill_batch",
    "_execute_backfill_sample_phase",
    "_execute_sample_initial_worker",
    "_execute_sample_backfill_worker",
    "_open_job_raw",
    "_prepare_owner_authorized_backfills",
    "_prepare_subject_authorized_backfills",
    "_backfill_support_context",
]
