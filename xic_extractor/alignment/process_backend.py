from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from types import TracebackType
from typing import Any

from xic_extractor.acquisition_contract import (
    RawAcquisitionSessionCapability,
    RawAcquisitionSessionReceipt,
    mint_raw_acquisition_session_capability,
)
from xic_extractor.alignment.backfill_scope import (
    REQUEST_PLAN_VERSION,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.identity_coherence.models import (
    IdentityCoherenceTraceRequest,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceProcessOutput as IdentityTraceProcessOutput,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceSampleJob as IdentityTraceSampleJob,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceSampleResult as IdentityTraceSampleResult,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceTimingStats as IdentityTraceTimingStats,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceWorkerError as IdentityTraceWorkerError,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceWorkerResult as IdentityTraceWorkerResult,
)
from xic_extractor.alignment.identity_trace_process import (
    collect_identity_trace_results as collect_identity_trace_results,
)
from xic_extractor.alignment.identity_trace_process import (
    extract_identity_trace_sample_from_raw as _extract_identity_trace_sample_from_raw,
)
from xic_extractor.alignment.identity_trace_process import (
    identity_trace_blocked_sample_result as _identity_trace_blocked_sample_result,
)
from xic_extractor.alignment.identity_trace_process import (
    run_identity_trace_jobs as _run_identity_trace_jobs,
)
from xic_extractor.alignment.identity_trace_process import (
    run_identity_trace_process as _run_identity_trace_process,
)
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.ms1_index_source import (
    OwnerBackfillXicBackend,
    OwnerBuildXicBackend,
    source_for_owner_backfill_backend,
    source_for_owner_build_backend,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillCandidateAuditRow,
    OwnerBackfillPeakFactObservation,
    OwnerBackfillRequestReuseObservation,
    OwnerBackfillResult,
    OwnerBackfillWindowStrategy,
    build_owner_backfill_result,
    validate_owner_backfill_peak_fact_observation,
)
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    PreparedOwnerBackfillPeakFact,
    prepare_owner_backfill_peak_fact,
)
from xic_extractor.alignment.owner_backfill_request_plan import (
    build_owner_backfill_sample_request_projections,
)
from xic_extractor.alignment.owner_group_delivery import OwnerGroupDeliveryFeatures
from xic_extractor.alignment.ownership import (
    OwnershipBuildResult,
    build_sample_local_owners,
)
from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
    OwnerAssignment,
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.process_execution import (
    AlignmentProcessExecutionError,
    ProcessProgressCallback,
    process_job_payload_size_bytes,
    validate_process_job_payload,
)
from xic_extractor.alignment.process_execution import (
    TimedProcessRawSource as _TimedProcessRawSource,
)
from xic_extractor.alignment.process_execution import (
    TimedProcessStats as _TimedProcessStats,
)
from xic_extractor.alignment.process_execution import (
    run_process_jobs as _run_process_jobs,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.execution_runtime_owner import (
    RuntimeArtifactIoSample,
    RuntimePeakFactCostMetrics,
    RuntimePeakFactIdentityStatus,
    RuntimePeakFactOperation,
    RuntimePeakFactWorkReceipt,
    derive_runtime_peak_fact_cost_metrics,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity


@dataclass(frozen=True)
class OwnerBuildSampleJob:
    sample_index: int
    sample_stem: str
    raw_path: Path
    dll_dir: Path
    candidates: tuple[Any, ...]
    alignment_config: AlignmentConfig
    peak_config: ExtractionConfig
    expected_acquisition_source: AcquisitionSourceIdentity | None = None
    raw_xic_batch_size: int = 1
    owner_build_xic_backend: OwnerBuildXicBackend = "raw"
    emit_region_audit: bool = False
    region_audit_family_ids: frozenset[str] | None = None
    audit_evidence_mode: str = "full"


@dataclass(frozen=True)
class OwnerBuildTimingStats:
    sample_stem: str
    elapsed_sec: float
    extract_xic_count: int
    point_count: int
    extract_xic_batch_count: int = 0
    raw_chromatogram_call_count: int = 0


@dataclass(frozen=True)
class OwnerBuildSampleResult:
    sample_index: int
    sample_stem: str
    owners: tuple[SampleLocalMS1Owner, ...]
    assignments: tuple[OwnerAssignment, ...]
    ambiguous_records: tuple[AmbiguousOwnerRecord, ...]
    timing_stats: tuple[OwnerBuildTimingStats, ...] = ()


@dataclass(frozen=True)
class OwnerBuildProcessOutput:
    ownership: OwnershipBuildResult
    timing_stats: tuple[OwnerBuildTimingStats, ...]


@dataclass(frozen=True)
class OwnerBuildWorkerError:
    sample_index: int
    sample_stem: str
    raw_name: str
    message: str


@dataclass(frozen=True)
class OwnerBackfillSampleJob:
    sample_index: int
    sample_stem: str
    raw_path: Path
    dll_dir: Path
    features: OwnerGroupDeliveryFeatures
    alignment_config: AlignmentConfig
    peak_config: ExtractionConfig
    signal_processing_profile: SignalProcessingProfile
    expected_acquisition_source: AcquisitionSourceIdentity | None = None
    raw_session_capability: RawAcquisitionSessionCapability | None = None
    raw_xic_batch_size: int = 1
    owner_backfill_xic_backend: OwnerBackfillXicBackend = "raw"
    owner_backfill_window_strategy: OwnerBackfillWindowStrategy = "exact"
    owner_backfill_superwindow_span_factor: int = 2
    emit_region_audit: bool = False
    region_audit_family_ids: frozenset[str] | None = None
    audit_evidence_mode: str = "full"
    request_plan_id: str = REQUEST_PLAN_VERSION
    backfill_scope: str = "full-audit"
    feature_payload_count: int = 0
    request_payload_count: int = 0
    peak_fact_feature_order_indices: tuple[int, ...] = ()
    peak_fact_sample_order_index: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.signal_processing_profile, SignalProcessingProfile):
            raise TypeError("signal_processing_profile must be owner-resolved")
        if self.peak_fact_sample_order_index is None:
            if self.peak_fact_feature_order_indices:
                raise ValueError("peak-fact feature order requires sample order")
        elif len(self.peak_fact_feature_order_indices) != len(self.features):
            raise ValueError("peak-fact feature order must cover every job feature")


@dataclass(frozen=True)
class OwnerBackfillJobPayloadMetrics:
    sample_index: int
    sample_stem: str
    feature_payload_count: int
    request_payload_count: int
    requests_per_feature: float | None
    pickle_payload_bytes: int


@dataclass(frozen=True)
class OwnerBackfillTimingStats:
    sample_stem: str
    elapsed_sec: float
    extract_xic_count: int
    point_count: int
    extract_xic_batch_count: int = 0
    raw_chromatogram_call_count: int = 0


@dataclass(frozen=True)
class OwnerBackfillSampleResult:
    sample_index: int
    sample_stem: str
    cells: tuple[AlignedCell, ...]
    candidate_audit_rows: tuple[OwnerBackfillCandidateAuditRow, ...] = ()
    timing_stats: tuple[OwnerBackfillTimingStats, ...] = ()
    request_reuse_observations: tuple[
        OwnerBackfillRequestReuseObservation,
        ...,
    ] = ()
    peak_fact_observations: tuple[
        OwnerBackfillPeakFactObservation,
        ...,
    ] = ()
    prepared_peak_facts: tuple[PreparedOwnerBackfillPeakFact, ...] = ()
    peak_fact_cost: RuntimePeakFactCostMetrics | None = None

    def __post_init__(self) -> None:
        if self.peak_fact_observations and self.prepared_peak_facts:
            raise ValueError(
                "Backfill result cannot carry live and prepared peak facts"
            )


@dataclass(frozen=True)
class OwnerBackfillProcessOutput:
    cells: tuple[AlignedCell, ...]
    timing_stats: tuple[OwnerBackfillTimingStats, ...]
    candidate_audit_rows: tuple[OwnerBackfillCandidateAuditRow, ...] = ()
    request_reuse_observations: tuple[
        OwnerBackfillRequestReuseObservation,
        ...,
    ] = ()
    peak_fact_observations: tuple[
        OwnerBackfillPeakFactObservation,
        ...,
    ] = ()
    peak_fact_cost_receipts: tuple[RuntimePeakFactCostMetrics, ...] = ()


@dataclass(frozen=True)
class OwnerBackfillWorkerError:
    sample_index: int
    sample_stem: str
    raw_name: str
    message: str


OwnerBuildWorkerResult = OwnerBuildSampleResult | OwnerBuildWorkerError
OwnerBackfillWorkerResult = OwnerBackfillSampleResult | OwnerBackfillWorkerError


def _validate_expected_acquisition_sources(
    *,
    sample_order: tuple[str, ...],
    raw_paths: Mapping[str, Path],
    expected_acquisition_sources: Mapping[str, AcquisitionSourceIdentity] | None,
) -> None:
    if expected_acquisition_sources is None:
        return
    for sample_stem in sample_order:
        if sample_stem not in raw_paths:
            raise ValueError(
                "owner-bound process execution requires RAW for every sample: "
                f"{sample_stem}"
            )
        _expected_acquisition_source(expected_acquisition_sources, sample_stem)


def _expected_acquisition_source(
    expected_acquisition_sources: Mapping[str, AcquisitionSourceIdentity] | None,
    sample_stem: str,
) -> AcquisitionSourceIdentity | None:
    if expected_acquisition_sources is None:
        return None
    source = expected_acquisition_sources.get(sample_stem)
    if not isinstance(source, AcquisitionSourceIdentity):
        raise ValueError(
            "owner-bound process execution requires an acquisition source for "
            f"{sample_stem}"
        )
    return source


def run_identity_trace_process(
    requests: Sequence[IdentityCoherenceTraceRequest],
    *,
    raw_paths: Mapping[str, Path],
    dll_dir: Path,
    max_workers: int,
    raw_xic_batch_size: int = 1,
    runner: Callable[..., list[IdentityTraceWorkerResult]] | None = None,
) -> IdentityTraceProcessOutput:
    active_runner = runner or run_identity_trace_jobs
    return _run_identity_trace_process(
        requests,
        raw_paths=raw_paths,
        dll_dir=dll_dir,
        max_workers=max_workers,
        raw_xic_batch_size=raw_xic_batch_size,
        runner=active_runner,
    )


def run_identity_trace_jobs(
    jobs: Iterable[IdentityTraceSampleJob],
    *,
    max_workers: int,
    executor_factory: Callable[..., Any] | None = None,
) -> list[IdentityTraceWorkerResult]:
    return _run_identity_trace_jobs(
        jobs,
        worker=extract_identity_trace_sample_job,
        max_workers=max_workers,
        executor_factory=executor_factory,
    )


def extract_identity_trace_sample_job(
    job: IdentityTraceSampleJob,
) -> IdentityTraceWorkerResult:
    from xic_extractor.raw_reader import open_raw

    try:
        raw_context = open_raw(job.raw_path, job.dll_dir)
        raw = raw_context.__enter__()
    except Exception:
        return _identity_trace_blocked_sample_result(job)

    exc_info: tuple[
        type[BaseException] | None,
        BaseException | None,
        TracebackType | None,
    ] = (None, None, None)
    try:
        return _extract_identity_trace_sample_from_raw(job, raw)
    except BaseException as error:
        exc_info = (type(error), error, error.__traceback__)
        raise
    finally:
        raw_context.__exit__(*exc_info)


def run_owner_build_process(
    candidates: Sequence[Any],
    *,
    sample_order: tuple[str, ...],
    raw_paths: Mapping[str, Path],
    dll_dir: Path,
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
    max_workers: int,
    raw_xic_batch_size: int = 1,
    owner_build_xic_backend: OwnerBuildXicBackend = "raw",
    expected_acquisition_sources: Mapping[
        str,
        AcquisitionSourceIdentity,
    ]
    | None = None,
    emit_region_audit: bool = False,
    region_audit_family_ids: frozenset[str] | None = None,
    audit_evidence_mode: str = "full",
    runner: Callable[..., list[OwnerBuildWorkerResult]] | None = None,
    progress_callback: ProcessProgressCallback | None = None,
) -> OwnerBuildProcessOutput:
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    if raw_xic_batch_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")
    _validate_expected_acquisition_sources(
        sample_order=sample_order,
        raw_paths=raw_paths,
        expected_acquisition_sources=expected_acquisition_sources,
    )
    candidates_by_sample: dict[str, list[Any]] = {}
    for candidate in candidates:
        candidates_by_sample.setdefault(
            str(candidate.sample_stem),
            [],
        ).append(candidate)

    jobs: list[OwnerBuildSampleJob] = []
    parent_results: list[OwnerBuildSampleResult] = []
    for index, sample_stem in enumerate(sample_order, start=1):
        sample_candidates = tuple(candidates_by_sample.get(sample_stem, ()))
        if not sample_candidates:
            continue
        raw_path = raw_paths.get(sample_stem)
        if raw_path is None:
            if expected_acquisition_sources is not None:
                raise ValueError(
                    "owner-bound owner build requires RAW for every sample: "
                    f"{sample_stem}"
                )
            ownership = build_sample_local_owners(
                sample_candidates,
                raw_sources={},
                alignment_config=alignment_config,
                peak_config=peak_config,
                raw_xic_batch_size=raw_xic_batch_size,
                emit_region_audit=emit_region_audit,
                region_audit_family_ids=region_audit_family_ids,
                audit_evidence_mode=audit_evidence_mode,
            )
            result = _owner_build_sample_result(
                sample_index=index,
                sample_stem=sample_stem,
                ownership=ownership,
                timing_stats=(),
            )
            if progress_callback is not None:
                progress_callback(result)
            parent_results.append(result)
            continue
        jobs.append(
            OwnerBuildSampleJob(
                sample_index=index,
                sample_stem=sample_stem,
                raw_path=raw_path,
                dll_dir=dll_dir,
                candidates=sample_candidates,
                alignment_config=alignment_config,
                peak_config=peak_config,
                expected_acquisition_source=_expected_acquisition_source(
                    expected_acquisition_sources,
                    sample_stem,
                ),
                raw_xic_batch_size=raw_xic_batch_size,
                owner_build_xic_backend=owner_build_xic_backend,
                emit_region_audit=emit_region_audit,
                region_audit_family_ids=region_audit_family_ids,
                audit_evidence_mode=audit_evidence_mode,
            )
        )
    active_runner = runner or run_owner_build_jobs
    if not jobs:
        worker_results = []
    elif progress_callback is None:
        worker_results = active_runner(jobs, max_workers=max_workers)
    else:
        worker_results = active_runner(
            jobs,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )
    return collect_owner_build_results((*parent_results, *worker_results))


def collect_owner_build_results(
    results: Iterable[OwnerBuildWorkerResult],
) -> OwnerBuildProcessOutput:
    successes: list[OwnerBuildSampleResult] = []
    errors: list[OwnerBuildWorkerError] = []
    for result in results:
        if isinstance(result, OwnerBuildWorkerError):
            errors.append(result)
        else:
            successes.append(result)
    if errors:
        messages = "; ".join(
            f"{error.raw_name}: {error.message}"
            for error in sorted(errors, key=lambda item: item.sample_index)
        )
        raise AlignmentProcessExecutionError(messages)

    unresolved_assignments = tuple(
        assignment
        for result in sorted(successes, key=lambda item: item.sample_index)
        for assignment in result.assignments
        if assignment.assignment_status == "unresolved"
    )
    sample_sorted = sorted(successes, key=lambda item: item.sample_stem)
    resolved_assignments = tuple(
        assignment
        for result in sample_sorted
        for assignment in result.assignments
        if assignment.assignment_status != "unresolved"
    )
    ownership = OwnershipBuildResult(
        owners=tuple(owner for result in sample_sorted for owner in result.owners),
        assignments=(*unresolved_assignments, *resolved_assignments),
        ambiguous_records=tuple(
            record for result in sample_sorted for record in result.ambiguous_records
        ),
    )
    timing_stats = tuple(
        stat
        for result in sorted(successes, key=lambda item: item.sample_index)
        for stat in result.timing_stats
    )
    return OwnerBuildProcessOutput(ownership=ownership, timing_stats=timing_stats)


def _owner_build_sample_result(
    *,
    sample_index: int,
    sample_stem: str,
    ownership: OwnershipBuildResult,
    timing_stats: tuple[OwnerBuildTimingStats, ...],
) -> OwnerBuildSampleResult:
    return OwnerBuildSampleResult(
        sample_index=sample_index,
        sample_stem=sample_stem,
        owners=ownership.owners,
        assignments=ownership.assignments,
        ambiguous_records=ownership.ambiguous_records,
        timing_stats=timing_stats,
    )


def run_owner_build_jobs(
    jobs: Iterable[OwnerBuildSampleJob],
    *,
    max_workers: int,
    executor_factory: Callable[..., Any] | None = None,
    progress_callback: ProcessProgressCallback | None = None,
) -> list[OwnerBuildWorkerResult]:
    return _run_process_jobs(
        jobs,
        worker=extract_owner_build_sample_job,
        error_factory=_owner_build_worker_error,
        max_workers=max_workers,
        executor_factory=executor_factory,
        progress_callback=progress_callback,
        is_terminal_failure=lambda result: isinstance(result, OwnerBuildWorkerError),
    )


def extract_owner_build_sample_job(
    job: OwnerBuildSampleJob,
) -> OwnerBuildWorkerResult:
    from xic_extractor.raw_reader import open_raw

    try:
        with open_raw(
            job.raw_path,
            job.dll_dir,
            expected_acquisition_source=job.expected_acquisition_source,
        ) as raw:
            stats = _TimedProcessStats(sample_stem=job.sample_stem)
            owner_build_source = source_for_owner_build_backend(
                raw,
                job.owner_build_xic_backend,
            )
            timed_raw = _TimedProcessRawSource(owner_build_source, stats=stats)
            ownership = build_sample_local_owners(
                job.candidates,
                raw_sources={job.sample_stem: timed_raw},
                alignment_config=job.alignment_config,
                peak_config=job.peak_config,
                raw_xic_batch_size=job.raw_xic_batch_size,
                emit_region_audit=job.emit_region_audit,
                region_audit_family_ids=job.region_audit_family_ids,
                audit_evidence_mode=job.audit_evidence_mode,
            )
        return _owner_build_sample_result(
            sample_index=job.sample_index,
            sample_stem=job.sample_stem,
            ownership=ownership,
            timing_stats=(
                OwnerBuildTimingStats(
                    sample_stem=job.sample_stem,
                    elapsed_sec=stats.elapsed_sec,
                    extract_xic_count=stats.extract_xic_count,
                    point_count=stats.point_count,
                    extract_xic_batch_count=stats.extract_xic_batch_count,
                    raw_chromatogram_call_count=(stats.raw_chromatogram_call_count),
                ),
            ),
        )
    except Exception as exc:
        return OwnerBuildWorkerError(
            sample_index=job.sample_index,
            sample_stem=job.sample_stem,
            raw_name=job.raw_path.name,
            message=f"{type(exc).__name__}: {exc}",
        )


def run_owner_backfill_process(
    features: OwnerGroupDeliveryFeatures,
    *,
    matrix_features: OwnerGroupDeliveryFeatures | None = None,
    sample_order: tuple[str, ...],
    raw_paths: Mapping[str, Path],
    dll_dir: Path,
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
    signal_processing_profile: SignalProcessingProfile,
    max_workers: int,
    raw_xic_batch_size: int = 1,
    owner_backfill_xic_backend: OwnerBackfillXicBackend = "raw",
    expected_acquisition_sources: Mapping[
        str,
        AcquisitionSourceIdentity,
    ]
    | None = None,
    owner_backfill_window_strategy: OwnerBackfillWindowStrategy = "exact",
    owner_backfill_superwindow_span_factor: int = 2,
    backfill_scope: str = "full-audit",
    emit_region_audit: bool = False,
    region_audit_family_ids: frozenset[str] | None = None,
    audit_evidence_mode: str = "full",
    runner: Callable[..., list[OwnerBackfillWorkerResult]] | None = None,
    progress_callback: ProcessProgressCallback | None = None,
    matrix_sample_order: tuple[str, ...] | None = None,
) -> Any:
    if not isinstance(signal_processing_profile, SignalProcessingProfile):
        raise TypeError("signal_processing_profile must be owner-resolved")
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    if raw_xic_batch_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")
    if owner_backfill_superwindow_span_factor < 1:
        raise ValueError("owner_backfill_superwindow_span_factor must be >= 1")
    _validate_expected_acquisition_sources(
        sample_order=sample_order,
        raw_paths=raw_paths,
        expected_acquisition_sources=expected_acquisition_sources,
    )
    jobs: list[OwnerBackfillSampleJob] = []
    raw_sample_stems = frozenset(raw_paths)
    sample_request_projections = build_owner_backfill_sample_request_projections(
        features,
        sample_order=sample_order,
        raw_sample_stems=raw_sample_stems,
        alignment_config=alignment_config,
    )
    peak_fact_feature_rank: dict[str, int] = {}
    peak_fact_sample_rank: dict[str, int] = {}
    if matrix_sample_order is not None:
        peak_fact_feature_rank = {
            feature.feature_family_id: index
            for index, feature in enumerate(
                matrix_features if matrix_features is not None else features
            )
        }
        peak_fact_sample_rank = {
            sample_stem: index
            for index, sample_stem in enumerate(matrix_sample_order)
        }
    for index, sample_stem in enumerate(sample_order, start=1):
        raw_path = raw_paths.get(sample_stem)
        if raw_path is None:
            continue
        sample_projection = sample_request_projections.get(sample_stem)
        if sample_projection is None:
            continue
        job_features = sample_projection.features
        expected_acquisition_source = _expected_acquisition_source(
            expected_acquisition_sources,
            sample_stem,
        )
        jobs.append(
            OwnerBackfillSampleJob(
                sample_index=index,
                sample_stem=sample_stem,
                raw_path=raw_path,
                dll_dir=dll_dir,
                features=job_features,
                alignment_config=alignment_config,
                peak_config=peak_config,
                signal_processing_profile=signal_processing_profile,
                expected_acquisition_source=expected_acquisition_source,
                raw_session_capability=(
                    mint_raw_acquisition_session_capability(expected_acquisition_source)
                    if expected_acquisition_source is not None
                    else None
                ),
                raw_xic_batch_size=raw_xic_batch_size,
                owner_backfill_xic_backend=owner_backfill_xic_backend,
                owner_backfill_window_strategy=owner_backfill_window_strategy,
                owner_backfill_superwindow_span_factor=(
                    owner_backfill_superwindow_span_factor
                ),
                emit_region_audit=emit_region_audit,
                region_audit_family_ids=region_audit_family_ids,
                audit_evidence_mode=audit_evidence_mode,
                request_plan_id=REQUEST_PLAN_VERSION,
                backfill_scope=backfill_scope,
                feature_payload_count=len(job_features),
                request_payload_count=sample_projection.logical_request_count,
                peak_fact_feature_order_indices=(
                    tuple(
                        peak_fact_feature_rank[feature.feature_family_id]
                        for feature in job_features
                    )
                    if matrix_sample_order is not None
                    else ()
                ),
                peak_fact_sample_order_index=(
                    peak_fact_sample_rank[sample_stem]
                    if matrix_sample_order is not None
                    else None
                ),
            )
        )
    if not jobs and matrix_sample_order is None:
        return OwnerBackfillProcessOutput(cells=(), timing_stats=())
    active_runner = runner or run_owner_backfill_jobs
    if matrix_sample_order is not None:
        from xic_extractor.alignment.backfill_transaction import (
            OwnerBackfillSampleTransaction,
        )

        transaction = OwnerBackfillSampleTransaction(
            matrix_features if matrix_features is not None else features,
            matrix_sample_order=matrix_sample_order,
            expected_samples=tuple(
                (job.sample_index, job.sample_stem) for job in jobs
            ),
            signal_processing_profile=signal_processing_profile,
        )
        results = active_runner(
            jobs,
            max_workers=max_workers,
            progress_callback=progress_callback,
            success_result_delivery=transaction.deliver,
        )
        errors = tuple(
            result for result in results if isinstance(result, OwnerBackfillWorkerError)
        )
        if errors:
            messages = "; ".join(
                f"{error.raw_name}: {error.message}"
                for error in sorted(errors, key=lambda item: item.sample_index)
            )
            raise AlignmentProcessExecutionError(messages)
        return transaction.finish()
    if progress_callback is None:
        results = active_runner(jobs, max_workers=max_workers)
    else:
        results = active_runner(
            jobs,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )
    return collect_owner_backfill_results(
        results,
        feature_order=tuple(feature.feature_family_id for feature in features),
        sample_order=sample_order,
    )


def collect_owner_backfill_results(
    results: Iterable[OwnerBackfillWorkerResult],
    *,
    feature_order: tuple[str, ...],
    sample_order: tuple[str, ...],
) -> OwnerBackfillProcessOutput:
    successes: list[OwnerBackfillSampleResult] = []
    errors: list[OwnerBackfillWorkerError] = []
    for result in results:
        if isinstance(result, OwnerBackfillWorkerError):
            errors.append(result)
        else:
            successes.append(result)
    if errors:
        messages = "; ".join(
            f"{error.raw_name}: {error.message}"
            for error in sorted(errors, key=lambda item: item.sample_index)
        )
        raise AlignmentProcessExecutionError(messages)

    feature_rank = {feature_id: index for index, feature_id in enumerate(feature_order)}
    sample_rank = {sample_stem: index for index, sample_stem in enumerate(sample_order)}
    cells = tuple(
        sorted(
            (cell for result in successes for cell in result.cells),
            key=lambda cell: (
                feature_rank.get(cell.cluster_id, len(feature_rank)),
                sample_rank.get(cell.sample_stem, len(sample_rank)),
            ),
        )
    )
    timing_stats = tuple(
        stat
        for result in sorted(successes, key=lambda item: item.sample_index)
        for stat in result.timing_stats
    )
    candidate_audit_rows = tuple(
        sorted(
            (row for result in successes for row in result.candidate_audit_rows),
            key=lambda row: (
                feature_rank.get(row.feature_family_id, len(feature_rank)),
                sample_rank.get(row.sample_stem, len(sample_rank)),
                row.candidate_index,
            ),
        )
    )
    request_reuse_observations = tuple(
        observation
        for result in sorted(successes, key=lambda item: item.sample_index)
        for observation in result.request_reuse_observations
    )
    peak_fact_cost_receipts = tuple(
        result.peak_fact_cost
        for result in sorted(successes, key=lambda item: item.sample_index)
        if result.peak_fact_cost is not None
    )
    peak_fact_observations = tuple(
        observation
        for result in sorted(successes, key=lambda item: item.sample_index)
        for observation in result.peak_fact_observations
    )
    return OwnerBackfillProcessOutput(
        cells=cells,
        timing_stats=timing_stats,
        candidate_audit_rows=candidate_audit_rows,
        request_reuse_observations=request_reuse_observations,
        peak_fact_observations=peak_fact_observations,
        peak_fact_cost_receipts=peak_fact_cost_receipts,
    )


def run_owner_backfill_jobs(
    jobs: Iterable[OwnerBackfillSampleJob],
    *,
    max_workers: int,
    executor_factory: Callable[..., Any] | None = None,
    progress_callback: ProcessProgressCallback | None = None,
    success_result_delivery: Callable[[int, Any], Any] | None = None,
) -> list[Any]:
    return _run_process_jobs(
        jobs,
        worker=extract_owner_backfill_sample_job,
        error_factory=_owner_backfill_worker_error,
        max_workers=max_workers,
        executor_factory=executor_factory,
        progress_callback=progress_callback,
        is_terminal_failure=lambda result: isinstance(result, OwnerBackfillWorkerError),
        success_result_delivery=success_result_delivery,
    )


def _owner_build_worker_error(
    job: OwnerBuildSampleJob,
    exc: Exception,
) -> OwnerBuildWorkerError:
    return OwnerBuildWorkerError(
        sample_index=job.sample_index,
        sample_stem=job.sample_stem,
        raw_name=job.raw_path.name,
        message=f"{type(exc).__name__}: {exc}",
    )


def _owner_backfill_worker_error(
    job: OwnerBackfillSampleJob,
    exc: Exception,
) -> OwnerBackfillWorkerError:
    return OwnerBackfillWorkerError(
        sample_index=job.sample_index,
        sample_stem=job.sample_stem,
        raw_name=job.raw_path.name,
        message=f"{type(exc).__name__}: {exc}",
    )


def extract_owner_backfill_sample_job(
    job: OwnerBackfillSampleJob,
) -> OwnerBackfillWorkerResult:
    from xic_extractor.raw_reader import open_raw

    try:
        with open_raw(
            job.raw_path,
            job.dll_dir,
            expected_acquisition_source=job.expected_acquisition_source,
            _raw_session_capability=job.raw_session_capability,
        ) as raw:
            raw_open_receipt = getattr(raw, "raw_open_receipt", None)
            stats = _TimedProcessStats(sample_stem=job.sample_stem)
            source = source_for_owner_backfill_backend(
                raw,
                job.owner_backfill_xic_backend,
            )
            timed_raw = _TimedProcessRawSource(source, stats=stats)
            validation_raw_sources = (
                {job.sample_stem: _TimedProcessRawSource(raw, stats=stats)}
                if job.owner_backfill_xic_backend == "ms1_index_hybrid"
                else None
            )
            validation_kwargs = (
                {"validation_raw_sources": validation_raw_sources}
                if validation_raw_sources is not None
                else {}
            )
            backfill_result = build_owner_backfill_result(
                job.features,
                sample_order=(job.sample_stem,),
                raw_sources={job.sample_stem: timed_raw},
                **validation_kwargs,
                expected_acquisition_sources=(
                    {job.sample_stem: job.expected_acquisition_source}
                    if job.expected_acquisition_source is not None
                    else None
                ),
                alignment_config=job.alignment_config,
                peak_config=job.peak_config,
                signal_processing_profile=job.signal_processing_profile,
                raw_xic_batch_size=job.raw_xic_batch_size,
                owner_backfill_xic_backend=job.owner_backfill_xic_backend,
                owner_backfill_window_strategy=job.owner_backfill_window_strategy,
                owner_backfill_superwindow_span_factor=(
                    job.owner_backfill_superwindow_span_factor
                ),
                emit_region_audit=job.emit_region_audit,
                region_audit_family_ids=job.region_audit_family_ids,
                audit_evidence_mode=job.audit_evidence_mode,
            )
            peak_fact_cost = _runtime_peak_fact_cost_metrics(
                backfill_result,
                sample_stem=job.sample_stem,
                raw_session_capability=job.raw_session_capability,
                raw_open_receipt=raw_open_receipt,
            )
        peak_fact_observations = backfill_result.peak_fact_observations
        prepared_peak_facts: tuple[PreparedOwnerBackfillPeakFact, ...] = ()
        if job.peak_fact_sample_order_index is not None:
            feature_order_indices = {
                feature.feature_family_id: feature_order_index
                for feature, feature_order_index in zip(
                    job.features,
                    job.peak_fact_feature_order_indices,
                    strict=True,
                )
            }
            prepared_peak_facts = tuple(
                prepare_owner_backfill_peak_fact(
                    observation,
                    feature_order_index=feature_order_indices[
                        observation.cell.cluster_id
                    ],
                    sample_order_index=job.peak_fact_sample_order_index,
                    profile=job.signal_processing_profile,
                )
                for observation in peak_fact_observations
            )
            peak_fact_observations = ()
        return OwnerBackfillSampleResult(
            sample_index=job.sample_index,
            sample_stem=job.sample_stem,
            cells=backfill_result.cells,
            candidate_audit_rows=backfill_result.candidate_audit_rows,
            request_reuse_observations=(backfill_result.request_reuse_observations),
            peak_fact_observations=peak_fact_observations,
            prepared_peak_facts=prepared_peak_facts,
            peak_fact_cost=peak_fact_cost,
            timing_stats=(
                OwnerBackfillTimingStats(
                    sample_stem=job.sample_stem,
                    elapsed_sec=stats.elapsed_sec,
                    extract_xic_count=stats.extract_xic_count,
                    point_count=stats.point_count,
                    extract_xic_batch_count=stats.extract_xic_batch_count,
                    raw_chromatogram_call_count=(stats.raw_chromatogram_call_count),
                ),
            ),
        )
    except Exception as exc:
        return OwnerBackfillWorkerError(
            sample_index=job.sample_index,
            sample_stem=job.sample_stem,
            raw_name=job.raw_path.name,
            message=f"{type(exc).__name__}: {exc}",
        )


def _runtime_peak_fact_cost_metrics(
    result: OwnerBackfillResult,
    *,
    sample_stem: str,
    raw_session_capability: RawAcquisitionSessionCapability | None,
    raw_open_receipt: RawAcquisitionSessionReceipt | None,
) -> RuntimePeakFactCostMetrics:
    work_receipts = chain(
        (
            _runtime_peak_fact_work_receipt(observation)
            for observation in result.peak_fact_observations
        ),
        (
            RuntimePeakFactWorkReceipt(
                work_unit_key=work_unit_key,
                sample_stem=sample_stem,
                operations=(RuntimePeakFactOperation.TRACE_EVALUATION,),
                identity_status=RuntimePeakFactIdentityStatus.UNAVAILABLE,
            )
            for work_unit_key in result.peak_fact_unsealed_evaluation_keys
        ),
    )
    return derive_runtime_peak_fact_cost_metrics(
        raw_session_capability=raw_session_capability,
        raw_open_receipts=(() if raw_open_receipt is None else (raw_open_receipt,)),
        work_receipts=work_receipts,
        artifact_io=RuntimeArtifactIoSample(
            read_count=0,
            read_bytes=0,
            write_count=0,
            write_bytes=0,
        ),
    )


def _runtime_peak_fact_work_receipt(
    observation: OwnerBackfillPeakFactObservation,
) -> RuntimePeakFactWorkReceipt:
    operations = _runtime_peak_fact_operations(observation)
    return RuntimePeakFactWorkReceipt(
        work_unit_key=observation.work_unit_key,
        sample_stem=observation.cell.sample_stem,
        operations=operations,
        identity_status=RuntimePeakFactIdentityStatus.SEALED,
        owner_request_identity=observation.request_identity.request_identity_id,
        group_hypothesis_id=observation.logical_request.group_hypothesis_id,
        acquisition_source=observation.request_identity.acquisition_source,
        prepared_signal_id=observation.prepared_signal_id,
        elapsed_seconds=(
            observation.fact_elapsed_seconds if len(operations) > 1 else None
        ),
    )


def _runtime_peak_fact_operations(
    observation: OwnerBackfillPeakFactObservation,
) -> tuple[RuntimePeakFactOperation, ...]:
    validate_owner_backfill_peak_fact_observation(observation)
    if observation.physical_evaluation is not None:
        return tuple(
            RuntimePeakFactOperation(operation)
            for operation in observation.physical_evaluation.operations
        )
    operations: tuple[RuntimePeakFactOperation, ...] = (
        (RuntimePeakFactOperation.TRACE_EVALUATION,)
        if observation.trace_evaluated
        else ()
    )
    if observation.facts is not None:
        if observation.facts.smoothing_count:
            operations += (RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,)
        if observation.facts.integration_count:
            operations += (RuntimePeakFactOperation.INTEGRATION,)
    return operations


def validate_owner_backfill_job_payload(job: OwnerBackfillSampleJob) -> None:
    validate_process_job_payload(job)


def owner_backfill_job_payload_metrics(
    job: OwnerBackfillSampleJob,
) -> OwnerBackfillJobPayloadMetrics:
    requests_per_feature = (
        job.request_payload_count / job.feature_payload_count
        if job.feature_payload_count
        else None
    )
    return OwnerBackfillJobPayloadMetrics(
        sample_index=job.sample_index,
        sample_stem=job.sample_stem,
        feature_payload_count=job.feature_payload_count,
        request_payload_count=job.request_payload_count,
        requests_per_feature=requests_per_feature,
        pickle_payload_bytes=process_job_payload_size_bytes(job),
    )
