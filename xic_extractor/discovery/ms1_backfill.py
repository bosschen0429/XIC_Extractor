import json
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, cast

import numpy as np

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    BatchXICAdapter,
    TraceAcquisitionBatch,
    TraceCoordinateKind,
    acquire_trace_batch,
    acquired_trace_content_id,
    mint_trace_acquisition_capability,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    DiscoveryPhysicalWorkItem,
    DiscoverySeed,
    DiscoverySeedGroup,
    DiscoverySettings,
    GroupPrecursorMzBasis,
    NeutralLossErrorBasis,
    assign_discovery_candidate_state,
    build_discovery_physical_selection_subject_id,
    build_ms1_feature_row_id,
)
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryCandidatePhysicalBinding,
    DiscoveryPhysicalFactRecord,
)
from xic_extractor.discovery.priority import (
    assign_review_priority,
    build_candidate_reason,
)
from xic_extractor.peak_detection.evidence_facts import (
    PhysicalPeakEvaluation,
    PreparedSignalResult,
    evaluate_physical_peak,
    mint_physical_trace_content_id,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.ms1_trace_detection import detect_ms1_trace_peak
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.signal_processing import PeakResult, find_peak_and_area
from xic_extractor.xic_models import XICRequest

_TagIntersectionStatus = Literal["not_required", "complete", "incomplete"]
_DISCOVERY_MS1_BATCH_MAX_REQUESTS = 256
_DISCOVERY_MS1_PREDICTION_MAX_REQUESTS = 256


class MS1XicSource(Protocol):
    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[np.ndarray, np.ndarray]: ...


@dataclass(frozen=True, slots=True)
class _DiscoveryMs1RequestPlan:
    input_ordinal: int
    group: DiscoverySeedGroup
    request: XICRequest


@dataclass(frozen=True, slots=True)
class _DiscoveryMs1Measurements:
    input_group_count: int
    empty_group_count: int
    exact_scan_window_count: int
    predicted_chunked_call_count: int
    batch_max_requests: int
    acquisition_batches: tuple[TraceAcquisitionBatch, ...]

    def __post_init__(self) -> None:
        values = (
            self.input_group_count,
            self.empty_group_count,
            self.exact_scan_window_count,
            self.predicted_chunked_call_count,
            self.batch_max_requests,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) for value in values
        ):
            raise ValueError("Discovery MS1 measurements must be integral")
        if any(value < 0 for value in values[:-1]) or self.batch_max_requests < 1:
            raise ValueError("Discovery MS1 measurements must be non-negative")
        if any(
            not isinstance(batch, TraceAcquisitionBatch)
            for batch in self.acquisition_batches
        ):
            raise ValueError("Discovery MS1 acquisition batches must be owner-minted")
        nonempty_group_count = self.input_group_count - self.empty_group_count
        if nonempty_group_count < 0:
            raise ValueError("Discovery MS1 input population is incomplete")
        if self.exact_scan_window_count > nonempty_group_count:
            raise ValueError("exact scan-window count exceeds logical requests")
        if self.predicted_chunked_call_count > nonempty_group_count:
            raise ValueError("predicted call count exceeds logical requests")


@dataclass(frozen=True, slots=True)
class _DiscoveryMs1AcquisitionResult:
    candidates: tuple[DiscoveryCandidate, ...]
    measurements: _DiscoveryMs1Measurements
    physical_work_manifest: tuple[DiscoveryPhysicalWorkItem, ...] = ()
    physical_evaluations: tuple[DiscoveryPhysicalFactRecord, ...] = ()
    physical_bindings: tuple["DiscoveryCandidatePhysicalBinding", ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveryCandidateMergeBinding:
    final_ordinal: int
    representative_source_ordinal: int
    member_source_ordinals: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryCandidateMergeResult:
    candidates: tuple[DiscoveryCandidate, ...]
    bindings: tuple[DiscoveryCandidateMergeBinding, ...]


def backfill_ms1_candidates(
    raw: MS1XicSource,
    groups: Iterable[DiscoverySeedGroup],
    *,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
) -> tuple[DiscoveryCandidate, ...]:
    """Preserve the scalar compatibility path for non-owner-bound callers."""

    candidates: list[DiscoveryCandidate] = []
    for plan in _discovery_ms1_request_plan(groups, settings=settings):
        request = plan.request
        rt, intensity = raw.extract_xic(
            request.mz,
            request.rt_min,
            request.rt_max,
            request.ppm_tol,
        )
        candidates.append(
            _candidate_from_trace(
                plan.group,
                rt,
                intensity,
                settings=settings,
                peak_config=peak_config,
            )
        )
    return merge_candidates_by_ms1_peak(candidates, settings=settings)


def _backfill_ms1_candidates_with_acquisition(
    raw: BatchXICAdapter,
    groups: Iterable[DiscoverySeedGroup],
    *,
    acquisition_source: AcquisitionSourceIdentity,
    run_spec_id: str | None = None,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None] | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
) -> _DiscoveryMs1AcquisitionResult:
    """Acquire canonical Discovery requests in bounded owner-minted batches."""

    if signal_processing_profile is not None and not run_spec_id:
        raise ValueError("canonical physical bindings require run_spec_id")
    group_tuple = tuple(groups)
    plans = _discovery_ms1_request_plan(group_tuple, settings=settings)
    capability = mint_trace_acquisition_capability(
        acquisition_source,
        adapter_namespace="xic_extractor.discovery.ms1_backfill",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    physical_plans: list[_DiscoveryMs1RequestPlan] = []
    physical_index_by_question: dict[tuple[str, str, XICRequest], int] = {}
    physical_indices: list[int] = []
    for plan in plans:
        best_seed = _best_seed(plan.group.seeds)
        question = (
            acquisition_source.acquisition_source_id,
            build_discovery_physical_selection_subject_id(
                sample_stem=plan.group.sample_stem,
                best_ms2_scan_id=best_seed.scan_number,
                precursor_mz=plan.group.precursor_mz,
                product_mz=plan.group.product_mz,
            ),
            plan.request,
        )
        physical_index = physical_index_by_question.get(question)
        if physical_index is None:
            physical_index = len(physical_plans)
            physical_index_by_question[question] = physical_index
            physical_plans.append(plan)
        physical_indices.append(physical_index)

    batches: list[TraceAcquisitionBatch] = []
    acquired_traces: list[AcquiredTrace] = []
    for plan_chunk in _chunks(physical_plans, _DISCOVERY_MS1_BATCH_MAX_REQUESTS):
        batch = acquire_trace_batch(
            capability,
            tuple(plan.request for plan in plan_chunk),
            raw,
        )
        _validate_acquired_plan_chunk(plan_chunk, batch)
        batches.append(batch)
        acquired_traces.extend(batch.traces)

    scan_windows = tuple(
        _native_scan_window(
            acquired.coordinate.first_scan,
            acquired.coordinate.last_scan,
        )
        for acquired in acquired_traces
    )
    measurements = _DiscoveryMs1Measurements(
        input_group_count=len(group_tuple),
        empty_group_count=sum(not group.seeds for group in group_tuple),
        exact_scan_window_count=len(set(scan_windows)),
        predicted_chunked_call_count=_predicted_chunked_call_count(scan_windows),
        batch_max_requests=_DISCOVERY_MS1_BATCH_MAX_REQUESTS,
        acquisition_batches=tuple(batches),
    )
    if measurement_observer is not None:
        measurement_observer(measurements)

    if len(acquired_traces) != len(physical_plans):
        raise ValueError("Discovery MS1 acquisition lost a physical request")
    prepared_signal_cache: dict[tuple[str, str], PreparedSignalResult] = {}
    physical_work_manifest: list[DiscoveryPhysicalWorkItem] = []
    physical_evaluations: list[DiscoveryPhysicalFactRecord] = []
    physical_fact_by_index: dict[int, DiscoveryPhysicalFactRecord] = {}
    candidates: list[DiscoveryCandidate] = []
    for plan, physical_index in zip(plans, physical_indices, strict=True):
        acquired = acquired_traces[physical_index]
        existing_fact = physical_fact_by_index.get(physical_index)
        candidates.append(
            _candidate_from_trace(
                plan.group,
                acquired.trace.rt,
                acquired.trace.intensity,
                settings=settings,
                peak_config=peak_config,
                signal_processing_profile=signal_processing_profile,
                prepared_signal_cache=prepared_signal_cache,
                physical_work_ordinal=physical_index,
                physical_acquisition_source_id=(
                    acquisition_source.acquisition_source_id
                ),
                physical_acquired_trace=acquired,
                physical_fact_record=existing_fact,
                physical_work_observer=physical_work_manifest.append,
                physical_evaluation_observer=physical_evaluations.append,
            )
        )
        if signal_processing_profile is not None and existing_fact is None:
            if len(physical_evaluations) != len(physical_fact_by_index) + 1:
                raise ValueError("Discovery physical evaluation was not recorded once")
            physical_fact_by_index[physical_index] = physical_evaluations[-1]
    merge_result = merge_candidates_by_ms1_peak_with_lineage(
        candidates,
        settings=settings,
    )
    physical_bindings: tuple[DiscoveryCandidatePhysicalBinding, ...] = ()
    if signal_processing_profile is not None:
        assert run_spec_id is not None
        if len(physical_fact_by_index) != len(physical_plans):
            raise ValueError("Discovery physical binding population is incomplete")
        physical_bindings = tuple(
            DiscoveryCandidatePhysicalBinding(
                run_spec_id=run_spec_id,
                acquisition_source=acquisition_source,
                final_ordinal=binding.final_ordinal,
                candidate=merge_result.candidates[binding.final_ordinal],
                representative_source_ordinal=(binding.representative_source_ordinal),
                member_source_ordinals=binding.member_source_ordinals,
                representative_work_ordinal=physical_indices[
                    binding.representative_source_ordinal
                ],
                selection_subject_id=physical_fact_by_index[
                    physical_indices[binding.representative_source_ordinal]
                ].selection_subject_id,
                physical_evaluation=physical_fact_by_index[
                    physical_indices[binding.representative_source_ordinal]
                ].evaluation,
            )
            for binding in merge_result.bindings
        )
    return _DiscoveryMs1AcquisitionResult(
        candidates=merge_result.candidates,
        measurements=measurements,
        physical_work_manifest=tuple(physical_work_manifest),
        physical_evaluations=tuple(physical_evaluations),
        physical_bindings=physical_bindings,
    )


def _discovery_ms1_request_plan(
    groups: Iterable[DiscoverySeedGroup],
    *,
    settings: DiscoverySettings,
) -> tuple[_DiscoveryMs1RequestPlan, ...]:
    plans: list[_DiscoveryMs1RequestPlan] = []
    for input_ordinal, group in enumerate(groups):
        if not group.seeds:
            continue
        plans.append(
            _DiscoveryMs1RequestPlan(
                input_ordinal=input_ordinal,
                group=group,
                request=XICRequest(
                    mz=group.precursor_mz,
                    rt_min=max(
                        0.0,
                        group.rt_seed_min - settings.ms1_search_padding_min,
                    ),
                    rt_max=group.rt_seed_max + settings.ms1_search_padding_min,
                    ppm_tol=settings.precursor_mz_tolerance_ppm,
                ),
            )
        )
    return tuple(plans)


def _validate_acquired_plan_chunk(
    plans: Sequence[_DiscoveryMs1RequestPlan],
    batch: TraceAcquisitionBatch,
) -> None:
    if len(plans) != len(batch.traces):
        raise ValueError("Discovery MS1 acquisition cardinality changed")
    for plan, acquired in zip(plans, batch.traces, strict=True):
        if acquired.coordinate.request != plan.request:
            raise ValueError("Discovery MS1 acquisition request order changed")


def _native_scan_window(
    first_scan: int | None,
    last_scan: int | None,
) -> tuple[int, int]:
    if first_scan is None or last_scan is None:
        raise ValueError("Discovery MS1 request lacks a native scan window")
    if first_scan < 0 or last_scan < first_scan:
        raise ValueError("Discovery MS1 request has an invalid native scan window")
    return first_scan, last_scan


def _predicted_chunked_call_count(
    scan_windows: Sequence[tuple[int, int]],
) -> int:
    return sum(
        len(set(chunk))
        for chunk in _chunks(scan_windows, _DISCOVERY_MS1_PREDICTION_MAX_REQUESTS)
    )


def _chunks(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _candidate_from_trace(
    group: DiscoverySeedGroup,
    rt: np.ndarray,
    intensity: np.ndarray,
    *,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    signal_processing_profile: SignalProcessingProfile | None = None,
    prepared_signal_cache: dict[tuple[str, str], PreparedSignalResult] | None = None,
    physical_work_ordinal: int | None = None,
    physical_acquisition_source_id: str | None = None,
    physical_acquired_trace: AcquiredTrace | None = None,
    physical_fact_record: DiscoveryPhysicalFactRecord | None = None,
    physical_work_observer: Callable[[DiscoveryPhysicalWorkItem], None] | None = None,
    physical_evaluation_observer: (
        Callable[[DiscoveryPhysicalFactRecord], None] | None
    ) = None,
) -> DiscoveryCandidate:
    best_seed = _best_seed(group.seeds)
    rt_min = max(0.0, group.rt_seed_min - settings.ms1_search_padding_min)
    rt_max = group.rt_seed_max + settings.ms1_search_padding_min
    physical_evaluation: PhysicalPeakEvaluation | None = None
    physical_work: DiscoveryPhysicalWorkItem | None = None
    if signal_processing_profile is None:
        ms1_fields = _detect_ms1_peak(
            rt,
            intensity,
            peak_config=peak_config,
            best_seed=best_seed,
            settings=settings,
        )
    else:
        if (
            physical_work_ordinal is None
            or not physical_acquisition_source_id
            or physical_acquired_trace is None
        ):
            raise ValueError("physical Discovery work identity is incomplete")
        coordinate = physical_acquired_trace.coordinate
        request = coordinate.request
        if coordinate.acquisition_source_id != physical_acquisition_source_id:
            raise ValueError("physical Discovery trace crosses acquisition source")
        trace_rt_matches = np.array_equal(rt, physical_acquired_trace.trace.rt)
        trace_intensity_matches = np.array_equal(
            intensity,
            physical_acquired_trace.trace.intensity,
        )
        if not trace_rt_matches or not trace_intensity_matches:
            raise ValueError("physical Discovery trace arrays lost acquisition binding")
        if physical_fact_record is None:
            physical_work = DiscoveryPhysicalWorkItem(
                work_ordinal=physical_work_ordinal,
                acquisition_source_id=physical_acquisition_source_id,
                sample_stem=group.sample_stem,
                best_ms2_scan_id=best_seed.scan_number,
                precursor_mz=group.precursor_mz,
                product_mz=group.product_mz,
                query_rt_min=best_seed.rt,
                context_rt_start_min=rt_min,
                context_rt_end_min=rt_max,
                trace_coordinate_id=coordinate.trace_coordinate_id,
                acquired_trace_content_id=acquired_trace_content_id(
                    physical_acquired_trace
                ),
                xic_mz=request.mz,
                xic_rt_min=request.rt_min,
                xic_rt_max=request.rt_max,
                xic_ppm_tol=request.ppm_tol,
            )
            ms1_fields, physical_evaluation = _physical_ms1_peak_fields(
                rt,
                intensity,
                profile=signal_processing_profile,
                prepared_signal_cache=(
                    prepared_signal_cache if prepared_signal_cache is not None else {}
                ),
                work_item=physical_work,
                settings=settings,
            )
            physical_work = replace(
                physical_work,
                context_rt_start_min=(
                    physical_evaluation.question.context_rt_start_min
                ),
                context_rt_end_min=physical_evaluation.question.context_rt_end_min,
            )
        else:
            physical_work = physical_fact_record.work_item
            physical_evaluation = physical_fact_record.evaluation
            expected_selection_subject_id = (
                build_discovery_physical_selection_subject_id(
                    sample_stem=group.sample_stem,
                    best_ms2_scan_id=best_seed.scan_number,
                    precursor_mz=group.precursor_mz,
                    product_mz=group.product_mz,
                )
            )
            if (
                physical_work.work_ordinal != physical_work_ordinal
                or physical_work.acquisition_source_id != physical_acquisition_source_id
                or physical_work.selection_subject_id != expected_selection_subject_id
                or physical_fact_record.acquired_trace is not physical_acquired_trace
                or (
                    physical_work.xic_mz,
                    physical_work.xic_rt_min,
                    physical_work.xic_rt_max,
                    physical_work.xic_ppm_tol,
                )
                != (request.mz, request.rt_min, request.rt_max, request.ppm_tol)
            ):
                raise ValueError("reused Discovery physical fact crosses its question")
            ms1_fields = _ms1_fields_from_physical_evaluation(
                physical_evaluation,
                work_item=physical_work,
                settings=settings,
            )
    priority = assign_review_priority(
        seed_event_count=len(group.seeds),
        ms1_peak_found=ms1_fields.peak_found,
        ms1_seed_delta_min=ms1_fields.seed_delta_min,
        settings=settings,
    )
    reason = (
        _physical_terminal_candidate_reason(physical_evaluation)
        if physical_evaluation is not None
        and physical_evaluation.state in {"unavailable", "failed"}
        else build_candidate_reason(
            seed_event_count=len(group.seeds),
            ms1_peak_found=ms1_fields.peak_found,
            ms1_seed_delta_min=ms1_fields.seed_delta_min,
            settings=settings,
        )
    )
    candidate = DiscoveryCandidate.from_values(
        raw_file=group.raw_file,
        sample_stem=group.sample_stem,
        precursor_mz=group.precursor_mz,
        product_mz=group.product_mz,
        observed_neutral_loss_da=group.observed_neutral_loss_da,
        best_seed=best_seed,
        seed_scan_ids=tuple(seed.scan_number for seed in group.seeds),
        neutral_loss_tag=group.neutral_loss_tag,
        configured_neutral_loss_da=group.configured_neutral_loss_da,
        neutral_loss_mass_error_ppm=group.neutral_loss_mass_error_ppm,
        rt_seed_min=group.rt_seed_min,
        rt_seed_max=group.rt_seed_max,
        ms1_search_rt_min=rt_min,
        ms1_search_rt_max=rt_max,
        ms1_seed_delta_min=ms1_fields.seed_delta_min,
        ms1_peak_rt_start=ms1_fields.peak_rt_start,
        ms1_peak_rt_end=ms1_fields.peak_rt_end,
        ms1_height=ms1_fields.height,
        ms1_trace_quality=ms1_fields.trace_quality,
        ms1_scan_support_score=ms1_fields.scan_support_score,
        neutral_loss_error_basis=group.neutral_loss_error_basis,
        precursor_mz_basis=group.precursor_mz_basis,
        scan_precursor_mz=group.scan_precursor_mz,
        scan_precursor_delta_da=group.scan_precursor_delta_da,
        max_scan_precursor_abs_delta_da=group.max_scan_precursor_abs_delta_da,
        seed_event_count=len(group.seeds),
        ms1_peak_found=ms1_fields.peak_found,
        ms1_apex_rt=ms1_fields.apex_rt,
        ms1_area=ms1_fields.area,
        ms2_product_max_intensity=max(seed.product_intensity for seed in group.seeds),
        review_priority=priority,
        reason=reason,
    )
    candidate = _with_tag_fields(candidate, group=group, settings=settings)
    if physical_evaluation is not None:
        if physical_work is None:
            raise ValueError("physical evaluation lost its Discovery work identity")
        if candidate.candidate_id != physical_work.candidate_id:
            raise ValueError("physical work candidate identity diverged")
        if physical_fact_record is None and physical_work_observer is not None:
            physical_work_observer(physical_work)
    if (
        physical_evaluation is not None
        and physical_fact_record is None
        and physical_evaluation_observer is not None
    ):
        assert physical_work is not None
        assert physical_acquired_trace is not None
        physical_evaluation_observer(
            DiscoveryPhysicalFactRecord(
                work_item=physical_work,
                acquired_trace=physical_acquired_trace,
                evaluation=physical_evaluation,
            )
        )
    return candidate


def compute_ms1_scan_support_score(
    rt: np.ndarray,
    peak: PeakResult,
    *,
    scans_target: int,
) -> float:
    if scans_target <= 0:
        raise ValueError("scans_target must be greater than 0")
    if rt.size == 0:
        return 0.0
    mask = (rt >= peak.peak_start) & (rt <= peak.peak_end)
    return min(1.0, float(np.count_nonzero(mask)) / float(scans_target))


def merge_candidates_by_ms1_peak(
    candidates: Iterable[DiscoveryCandidate],
    *,
    settings: DiscoverySettings,
) -> tuple[DiscoveryCandidate, ...]:
    return merge_candidates_by_ms1_peak_with_lineage(
        candidates,
        settings=settings,
    ).candidates


def merge_candidates_by_ms1_peak_with_lineage(
    candidates: Iterable[DiscoveryCandidate],
    *,
    settings: DiscoverySettings,
) -> DiscoveryCandidateMergeResult:
    merged: list[DiscoveryCandidate] = []
    member_source_ordinals: list[list[int]] = []
    representative_source_ordinals: list[int] = []
    for source_ordinal, candidate in enumerate(candidates):
        merge_index = _matching_ms1_peak_candidate_index(
            candidate, merged, settings=settings
        )
        if merge_index is None:
            merged.append(candidate)
            member_source_ordinals.append([source_ordinal])
            representative_source_ordinals.append(source_ordinal)
            continue
        current = merged[merge_index]
        representative = _representative_candidate(
            current,
            candidate,
            settings=settings,
        )
        merged[merge_index] = _merge_candidate_pair(
            current,
            candidate,
            settings=settings,
        )
        member_source_ordinals[merge_index].append(source_ordinal)
        if representative is candidate:
            representative_source_ordinals[merge_index] = source_ordinal
    return DiscoveryCandidateMergeResult(
        candidates=tuple(merged),
        bindings=tuple(
            DiscoveryCandidateMergeBinding(
                final_ordinal=final_ordinal,
                representative_source_ordinal=representative_source_ordinals[
                    final_ordinal
                ],
                member_source_ordinals=tuple(member_source_ordinals[final_ordinal]),
            )
            for final_ordinal in range(len(merged))
        ),
    )


_merge_candidates_by_ms1_peak = merge_candidates_by_ms1_peak


def _matching_ms1_peak_candidate_index(
    candidate: DiscoveryCandidate,
    merged: list[DiscoveryCandidate],
    *,
    settings: DiscoverySettings,
) -> int | None:
    for index, existing in enumerate(merged):
        if _can_merge_by_ms1_peak(existing, candidate, settings=settings):
            return index
    return None


def _can_merge_by_ms1_peak(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
) -> bool:
    same_ms1_feature_row = _same_ms1_feature_row_identity(first, second)
    if not (
        first.raw_file == second.raw_file
        and first.sample_stem == second.sample_stem
        and first.ms1_peak_found
        and second.ms1_peak_found
        and _candidate_peak_bounds_present(first)
        and _candidate_peak_bounds_present(second)
        and _within_ppm(
            first.precursor_mz,
            second.precursor_mz,
            settings.precursor_mz_tolerance_ppm,
        )
        and _peak_intervals_overlap(first, second)
        and (_seed_ranges_touch_shared_peak(first, second) or same_ms1_feature_row)
    ):
        return False
    if first.neutral_loss_tag == second.neutral_loss_tag:
        if not _within_ppm(
            first.product_mz,
            second.product_mz,
            settings.product_mz_tolerance_ppm,
        ):
            return False
        if same_ms1_feature_row:
            return _configured_neutral_loss_compatible(
                first,
                settings=settings,
            ) and _configured_neutral_loss_compatible(second, settings=settings)
        return _within_ppm(
            first.observed_neutral_loss_da,
            second.observed_neutral_loss_da,
            settings.nl_tolerance_ppm,
        )
    return settings.tag_combine_mode == "union"


def _same_ms1_feature_row_identity(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> bool:
    return (
        bool(first.ms1_feature_row_id)
        and first.ms1_feature_row_id == second.ms1_feature_row_id
    )


def _configured_neutral_loss_compatible(
    candidate: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
) -> bool:
    return _within_ppm(
        candidate.observed_neutral_loss_da,
        candidate.configured_neutral_loss_da,
        settings.nl_tolerance_ppm,
    )


def _merge_candidate_pair(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
) -> DiscoveryCandidate:
    representative = _representative_candidate(first, second, settings=settings)
    seed_scan_ids = tuple(sorted(set(first.seed_scan_ids + second.seed_scan_ids)))
    seed_event_count = first.seed_event_count + second.seed_event_count
    rt_seed_min = min(first.rt_seed_min, second.rt_seed_min)
    rt_seed_max = max(first.rt_seed_max, second.rt_seed_max)
    seed_delta = (
        representative.ms1_apex_rt - representative.best_seed_rt
        if representative.ms1_apex_rt is not None
        else None
    )
    priority = assign_review_priority(
        seed_event_count=seed_event_count,
        ms1_peak_found=True,
        ms1_seed_delta_min=seed_delta,
        settings=settings,
    )
    reason = "MS2 NL seeds merged by shared MS1 peak; MS1 peak found near seed RT"
    matched_tag_names = _ordered_tag_names(
        first.matched_tag_names + second.matched_tag_names,
        settings=settings,
    )
    precursor_mz_basis = _combined_precursor_mz_basis(first, second)
    return replace(
        representative,
        seed_event_count=seed_event_count,
        seed_scan_ids=seed_scan_ids,
        rt_seed_min=rt_seed_min,
        rt_seed_max=rt_seed_max,
        ms1_search_rt_min=min(first.ms1_search_rt_min, second.ms1_search_rt_min),
        ms1_search_rt_max=max(first.ms1_search_rt_max, second.ms1_search_rt_max),
        ms1_seed_delta_min=seed_delta,
        ms2_product_max_intensity=max(
            first.ms2_product_max_intensity,
            second.ms2_product_max_intensity,
        ),
        neutral_loss_error_basis=_combined_neutral_loss_error_basis(first, second),
        precursor_mz_basis=precursor_mz_basis,
        max_scan_precursor_abs_delta_da=_combined_max_scan_precursor_abs_delta_da(
            first,
            second,
        ),
        discovery_candidate_state=assign_discovery_candidate_state(
            ms1_peak_found=True,
            precursor_mz_basis=precursor_mz_basis,
        ),
        ms1_feature_row_id=build_ms1_feature_row_id(
            sample_stem=representative.sample_stem,
            neutral_loss_tag=representative.neutral_loss_tag,
            precursor_mz=representative.precursor_mz,
            best_seed_rt=representative.best_seed_rt,
            ms1_peak_found=True,
            ms1_apex_rt=representative.ms1_apex_rt,
            ms1_peak_rt_start=representative.ms1_peak_rt_start,
            ms1_peak_rt_end=representative.ms1_peak_rt_end,
        ),
        review_priority=priority,
        reason=reason,
        selected_tag_count=len(settings.selected_tag_names),
        matched_tag_count=len(matched_tag_names),
        matched_tag_names=matched_tag_names,
        primary_tag_name=representative.neutral_loss_tag,
        tag_combine_mode=settings.tag_combine_mode,
        tag_intersection_status=_tag_intersection_status(
            matched_tag_names,
            settings=settings,
        ),
        tag_evidence_json=_merge_tag_evidence_json(first, second),
    )


def _representative_candidate(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
) -> DiscoveryCandidate:
    return min(
        (first, second),
        key=lambda candidate: (
            _tag_rank(candidate.neutral_loss_tag, settings=settings),
            -candidate.ms2_product_max_intensity,
            abs(candidate.neutral_loss_mass_error_ppm),
            candidate.best_seed_rt,
            candidate.best_ms2_scan_id,
        ),
    )


def _candidate_peak_bounds_present(candidate: DiscoveryCandidate) -> bool:
    return (
        candidate.ms1_peak_rt_start is not None
        and candidate.ms1_peak_rt_end is not None
        and candidate.ms1_peak_rt_start <= candidate.ms1_peak_rt_end
    )


def _combined_precursor_mz_basis(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> GroupPrecursorMzBasis:
    if first.precursor_mz_basis == second.precursor_mz_basis:
        return first.precursor_mz_basis
    return "mixed"


def _combined_neutral_loss_error_basis(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> NeutralLossErrorBasis:
    if first.neutral_loss_error_basis == second.neutral_loss_error_basis:
        return first.neutral_loss_error_basis
    return "mixed"


def _combined_max_scan_precursor_abs_delta_da(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> float | None:
    values = [
        value
        for value in (
            first.max_scan_precursor_abs_delta_da,
            second.max_scan_precursor_abs_delta_da,
        )
        if value is not None
    ]
    if not values:
        return None
    return max(values)


def _peak_intervals_overlap(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> bool:
    first_start = first.ms1_peak_rt_start
    first_end = first.ms1_peak_rt_end
    second_start = second.ms1_peak_rt_start
    second_end = second.ms1_peak_rt_end
    if (
        first_start is None
        or first_end is None
        or second_start is None
        or second_end is None
    ):
        return False
    return max(first_start, second_start) <= min(first_end, second_end)


def _seed_ranges_touch_shared_peak(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> bool:
    return _seed_range_touches_peak(first, second) and _seed_range_touches_peak(
        second, first
    )


def _seed_range_touches_peak(
    seed_candidate: DiscoveryCandidate,
    peak_candidate: DiscoveryCandidate,
) -> bool:
    peak_start = peak_candidate.ms1_peak_rt_start
    peak_end = peak_candidate.ms1_peak_rt_end
    if peak_start is None or peak_end is None:
        return False
    return (
        seed_candidate.rt_seed_min <= peak_end
        and seed_candidate.rt_seed_max >= peak_start
    )


def _with_tag_fields(
    candidate: DiscoveryCandidate,
    *,
    group: DiscoverySeedGroup,
    settings: DiscoverySettings,
) -> DiscoveryCandidate:
    matched_tag_names = _ordered_tag_names(group.matched_tag_names, settings=settings)
    return replace(
        candidate,
        selected_tag_count=len(settings.selected_tag_names),
        matched_tag_count=len(matched_tag_names),
        matched_tag_names=matched_tag_names,
        primary_tag_name=group.neutral_loss_tag,
        tag_combine_mode=settings.tag_combine_mode,
        tag_intersection_status=_tag_intersection_status(
            matched_tag_names,
            settings=settings,
        ),
        tag_evidence_json=group.tag_evidence_json,
    )


def _ordered_tag_names(
    values: Iterable[str],
    *,
    settings: DiscoverySettings,
) -> tuple[str, ...]:
    unique = {value for value in values if value}
    return tuple(
        sorted(unique, key=lambda tag: (_tag_rank(tag, settings=settings), tag))
    )


def _tag_rank(tag: str, *, settings: DiscoverySettings) -> int:
    try:
        return settings.selected_tag_names.index(tag)
    except ValueError:
        return len(settings.selected_tag_names)


def _tag_intersection_status(
    matched_tag_names: tuple[str, ...],
    *,
    settings: DiscoverySettings,
) -> _TagIntersectionStatus:
    if settings.tag_combine_mode != "intersection":
        return "not_required"
    return (
        "complete"
        if set(settings.selected_tag_names).issubset(set(matched_tag_names))
        else "incomplete"
    )


def _merge_tag_evidence_json(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> str:
    payload: dict[str, Any] = {}
    for candidate in (first, second):
        try:
            parsed = json.loads(candidate.tag_evidence_json or "{}")
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            for tag_name, evidence in parsed.items():
                tag_key = str(tag_name)
                if not isinstance(evidence, dict):
                    payload.setdefault(tag_key, evidence)
                    continue
                if isinstance(payload.get(tag_key), dict):
                    payload[tag_key] = _merge_tag_evidence_entry(
                        cast(dict[str, Any], payload[tag_key]),
                        cast(dict[str, Any], evidence),
                    )
                else:
                    payload[tag_key] = dict(evidence)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _merge_tag_evidence_entry(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(first)
    for key, value in second.items():
        merged.setdefault(key, value)

    scan_ids = sorted(
        set(_int_values(first.get("scan_ids")))
        | set(_int_values(second.get("scan_ids")))
    )
    if scan_ids:
        merged["scan_ids"] = scan_ids

    scan_count = _optional_int(first.get("scan_count")) + _optional_int(
        second.get("scan_count")
    )
    if scan_count:
        merged["scan_count"] = scan_count
    elif scan_ids:
        merged["scan_count"] = len(scan_ids)

    _merge_float_min(merged, first, second, "rt_min")
    _merge_float_max(merged, first, second, "rt_max")
    _merge_max_intensity_fields(merged, first, second)
    return merged


def _merge_float_min(
    merged: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
    key: str,
) -> None:
    values = [
        value
        for value in (_optional_float(first.get(key)), _optional_float(second.get(key)))
        if value is not None
    ]
    if values:
        merged[key] = min(values)


def _merge_float_max(
    merged: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
    key: str,
) -> None:
    values = [
        value
        for value in (_optional_float(first.get(key)), _optional_float(second.get(key)))
        if value is not None
    ]
    if values:
        merged[key] = max(values)


def _merge_max_intensity_fields(
    merged: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
) -> None:
    first_intensity = _optional_float(first.get("max_intensity"))
    second_intensity = _optional_float(second.get("max_intensity"))
    if first_intensity is None and second_intensity is None:
        return
    winner = (
        second
        if first_intensity is None
        or (second_intensity is not None and second_intensity > first_intensity)
        else first
    )
    merged["max_intensity"] = _optional_float(winner.get("max_intensity"))
    for key in ("product_mz", "neutral_loss_error_ppm"):
        if key in winner:
            merged[key] = winner[key]


def _int_values(value: object) -> tuple[int, ...]:
    if not isinstance(value, list | tuple):
        return ()
    parsed: list[int] = []
    for item in value:
        integer = _optional_int(item)
        if integer:
            parsed.append(integer)
    return tuple(parsed)


def _optional_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _within_ppm(a: float, b: float, tolerance_ppm: float) -> bool:
    if (
        not math.isfinite(a)
        or not math.isfinite(b)
        or not math.isfinite(tolerance_ppm)
        or a <= 0
        or b <= 0
        or tolerance_ppm < 0
    ):
        return False
    return abs(a - b) / abs(b) * 1_000_000.0 <= tolerance_ppm


class _Ms1Fields:
    def __init__(
        self,
        *,
        peak_found: bool,
        apex_rt: float | None,
        area: float | None,
        height: float | None,
        seed_delta_min: float | None,
        peak_rt_start: float | None,
        peak_rt_end: float | None,
        trace_quality: str,
        scan_support_score: float | None,
    ) -> None:
        self.peak_found = peak_found
        self.apex_rt = apex_rt
        self.area = area
        self.height = height
        self.seed_delta_min = seed_delta_min
        self.peak_rt_start = peak_rt_start
        self.peak_rt_end = peak_rt_end
        self.trace_quality = trace_quality
        self.scan_support_score = scan_support_score


def _physical_ms1_peak_fields(
    rt: np.ndarray,
    intensity: np.ndarray,
    *,
    profile: SignalProcessingProfile,
    prepared_signal_cache: dict[tuple[str, str], PreparedSignalResult],
    work_item: DiscoveryPhysicalWorkItem,
    settings: DiscoverySettings,
) -> tuple[_Ms1Fields, PhysicalPeakEvaluation]:
    trace_content_id = mint_physical_trace_content_id(rt, intensity)
    cache_key = (trace_content_id, profile.profile_id)
    prepared = prepared_signal_cache.get(cache_key)
    if prepared is None:
        prepared = prepare_physical_signal(
            rt,
            intensity,
            trace_content_id=trace_content_id,
            profile=profile,
        )
        prepared_signal_cache[cache_key] = prepared
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id=work_item.selection_subject_id,
        query_rt_min=work_item.query_rt_min,
        request_context_rt_start_min=work_item.context_rt_start_min,
        request_context_rt_end_min=work_item.context_rt_end_min,
    )
    return (
        _ms1_fields_from_physical_evaluation(
            evaluation,
            work_item=work_item,
            settings=settings,
        ),
        evaluation,
    )


def _ms1_fields_from_physical_evaluation(
    evaluation: PhysicalPeakEvaluation,
    *,
    work_item: DiscoveryPhysicalWorkItem,
    settings: DiscoverySettings,
) -> _Ms1Fields:
    facts = evaluation.facts
    if evaluation.state in {"unavailable", "failed"}:
        return _missing_ms1_fields(trace_quality=f"physical_{evaluation.state}")
    if facts is None:
        return _missing_ms1_fields()
    scan_support_score = min(
        1.0,
        float(facts.scan_support_count)
        / float(settings.evidence_profile.thresholds.scan_support_target),
    )
    return _Ms1Fields(
        peak_found=True,
        apex_rt=facts.apex_rt_min,
        area=facts.area_counts_seconds,
        height=facts.height,
        seed_delta_min=facts.apex_rt_min - work_item.query_rt_min,
        peak_rt_start=facts.left_rt_min,
        peak_rt_end=facts.right_rt_min,
        trace_quality="clean",
        scan_support_score=scan_support_score,
    )


def _detect_ms1_peak(
    rt: np.ndarray,
    intensity: np.ndarray,
    *,
    peak_config: ExtractionConfig,
    best_seed: DiscoverySeed,
    settings: DiscoverySettings,
) -> _Ms1Fields:
    detection = detect_ms1_trace_peak(
        rt,
        intensity,
        peak_config=peak_config,
        preferred_rt=best_seed.rt,
        strict_preferred_rt=False,
        peak_finder=find_peak_and_area,
    )
    if detection.status != "detected" or detection.peak is None:
        return _missing_ms1_fields()
    peak = detection.peak
    scan_support_score = compute_ms1_scan_support_score(
        detection.rt,
        peak,
        scans_target=settings.evidence_profile.thresholds.scan_support_target,
    )
    return _Ms1Fields(
        peak_found=True,
        apex_rt=peak.rt,
        area=peak.area,
        height=peak.intensity,
        seed_delta_min=peak.rt - best_seed.rt,
        peak_rt_start=peak.peak_start,
        peak_rt_end=peak.peak_end,
        trace_quality="clean",
        scan_support_score=scan_support_score,
    )


def _missing_ms1_fields(*, trace_quality: str = "missing") -> _Ms1Fields:
    return _Ms1Fields(
        peak_found=False,
        apex_rt=None,
        area=None,
        height=None,
        seed_delta_min=None,
        peak_rt_start=None,
        peak_rt_end=None,
        trace_quality=trace_quality,
        scan_support_score=None,
    )


def _physical_terminal_candidate_reason(
    evaluation: PhysicalPeakEvaluation,
) -> str:
    if evaluation.state not in {"unavailable", "failed"}:
        raise ValueError("physical terminal reason requires a terminal evaluation")
    if not evaluation.reason.strip():
        raise ValueError("physical terminal evaluation requires a nonempty reason")
    return f"physical MS1 evaluation {evaluation.state}: {evaluation.reason}"


def _best_seed(seeds: Iterable[DiscoverySeed]) -> DiscoverySeed:
    return min(
        seeds,
        key=lambda seed: (
            -seed.product_intensity,
            abs(seed.observed_loss_error_ppm),
            seed.rt,
            seed.scan_number,
        ),
    )
