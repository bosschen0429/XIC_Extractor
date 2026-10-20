import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

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
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery import candidate_merge
from xic_extractor.discovery.grouping import (
    build_discovery_seed_group,
    merge_seed_evidence,
    select_representative_seed,
)
from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    DiscoveryPhysicalWorkItem,
    DiscoverySeed,
    DiscoverySeedGroup,
    DiscoverySettings,
    assign_discovery_candidate_state,
    build_discovery_physical_selection_subject_id,
)
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryCandidatePhysicalBinding,
    DiscoveryPhysicalFactRecord,
    bind_discovery_physical_record_evidence,
)
from xic_extractor.discovery.priority import (
    assign_review_priority,
    build_candidate_reason,
)
from xic_extractor.discovery.strict_seed_receipts import read_parent_ms1_observations
from xic_extractor.mass_accuracy import weighted_ion_mz, within_ppm
from xic_extractor.peak_detection.background_shape import is_provisional_peak_shape
from xic_extractor.peak_detection.models import PeakResult
from xic_extractor.peak_detection.ms1_morphology import DEFAULT_GAUSSIAN15_WINDOW_POINTS
from xic_extractor.peak_detection.ms1_trace_detection import detect_ms1_trace_peak
from xic_extractor.peak_detection.peak_integration import find_peak_and_area
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    mint_physical_peak_question,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_identity import (
    mint_physical_trace_content_id,
)
from xic_extractor.peak_detection.physical_peak_models import (
    DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    PHYSICAL_PEAK_POINT_POLICY,
    PhysicalPeakEvaluation,
    PreparedSignalResult,
)
from xic_extractor.peak_detection.scoring_metrics import short_scale_noise_mad
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.raw_superwindow_source import RawSuperWindowSource
from xic_extractor.xic_models import XICRequest

_DISCOVERY_MS1_BATCH_MAX_REQUESTS = 256
_DISCOVERY_MS1_PREDICTION_MAX_REQUESTS = 256
_DISCOVERY_PHYSICAL_CONTEXT_HALF_WINDOW_MIN = 0.60
# The apex scan and one MS1 scan on each side read the ion's m/z; readings
# spread wider than a few ppm come from more than one ion.
_APEX_ION_SCAN_RADIUS = 1
_APEX_ION_SPREAD_PPM = 5.0
# 13C spacing of a doubly charged ion. Product + loss assumes a singly charged
# precursor, so an inferred ion read beside this isotope is not one.
_DOUBLY_CHARGED_ISOTOPE_SPACING_MZ = 1.0033548 / 2


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
    initial_request_count: int = 0
    reacquisition_request_count: int = 0

    def __post_init__(self) -> None:
        count_values = (
            self.input_group_count,
            self.empty_group_count,
            self.exact_scan_window_count,
            self.predicted_chunked_call_count,
            self.initial_request_count,
            self.reacquisition_request_count,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (*count_values, self.batch_max_requests)
        ):
            raise ValueError("Discovery MS1 measurements must be integral")
        if any(value < 0 for value in count_values) or self.batch_max_requests < 1:
            raise ValueError("Discovery MS1 measurements must be non-negative")
        if any(
            not isinstance(batch, TraceAcquisitionBatch)
            for batch in self.acquisition_batches
        ):
            raise ValueError("Discovery MS1 acquisition batches must be owner-minted")
        nonempty_group_count = self.input_group_count - self.empty_group_count
        if nonempty_group_count < 0:
            raise ValueError("Discovery MS1 input population is incomplete")
        logical_request_count = sum(
            batch.logical_xic_request_count for batch in self.acquisition_batches
        )
        if self.initial_request_count > nonempty_group_count:
            raise ValueError("Discovery MS1 acquisition request population is invalid")
        if self.reacquisition_request_count > self.initial_request_count:
            raise ValueError(
                "Discovery MS1 reacquisition exceeds its initial population"
            )
        if logical_request_count != (
            self.initial_request_count + self.reacquisition_request_count
        ):
            raise ValueError("Discovery MS1 acquisition receipts are incomplete")
        if self.exact_scan_window_count > logical_request_count:
            raise ValueError("exact scan-window count exceeds logical requests")
        if self.predicted_chunked_call_count > logical_request_count:
            raise ValueError("predicted call count exceeds logical requests")


@dataclass(frozen=True, slots=True)
class _DiscoveryMs1AcquisitionResult:
    candidates: tuple[DiscoveryCandidate, ...]
    measurements: _DiscoveryMs1Measurements
    physical_work_manifest: tuple[DiscoveryPhysicalWorkItem, ...] = ()
    physical_evaluations: tuple[DiscoveryPhysicalFactRecord, ...] = ()
    physical_bindings: tuple["DiscoveryCandidatePhysicalBinding", ...] = ()


def capture_ms1_candidates(
    raw: BatchXICAdapter,
    groups: Iterable[DiscoverySeedGroup],
    *,
    acquisition_source: AcquisitionSourceIdentity,
    run_spec_id: str | None = None,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None] | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
    ms1_centroid_mz: candidate_merge.MS1CentroidMz | None = None,
) -> _DiscoveryMs1AcquisitionResult:
    """Acquire canonical Discovery requests in bounded owner-minted batches."""

    if signal_processing_profile is not None and not run_spec_id:
        raise ValueError("canonical physical bindings require run_spec_id")
    group_tuple = tuple(groups)
    plans = _discovery_ms1_request_plan(
        group_tuple,
        settings=settings,
        minimum_padding_min=(
            _DISCOVERY_PHYSICAL_CONTEXT_HALF_WINDOW_MIN
            if signal_processing_profile is not None
            else 0.0
        ),
    )
    capability = mint_trace_acquisition_capability(
        acquisition_source,
        adapter_namespace="xic_extractor.discovery.ms1_capture",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    physical_plans: list[_DiscoveryMs1RequestPlan] = []
    physical_index_by_question: dict[tuple[str, str, XICRequest], int] = {}
    physical_indices: list[int] = []
    for plan in plans:
        question = _physical_request_question(plan, acquisition_source)
        physical_index = physical_index_by_question.get(question)
        if physical_index is None:
            physical_index = len(physical_plans)
            physical_index_by_question[question] = physical_index
            physical_plans.append(plan)
        physical_indices.append(physical_index)

    superwindow_raw = RawSuperWindowSource(raw)
    batches: list[TraceAcquisitionBatch] = []
    acquired_traces: list[AcquiredTrace] = []
    prepared_signal_cache: dict[tuple[str, str], PreparedSignalResult] = {}
    for plan_chunk in _chunks(physical_plans, _DISCOVERY_MS1_BATCH_MAX_REQUESTS):
        batch = acquire_trace_batch(
            capability,
            tuple(plan.request for plan in plan_chunk),
            superwindow_raw,
        )
        _validate_acquired_plan_chunk(plan_chunk, batch)
        batches.append(batch)
        acquired_traces.extend(batch.traces)

    initial_physical_facts: dict[int, DiscoveryPhysicalFactRecord] = {}
    physical_trace_qualities: dict[int, str] = {}
    apex_readings: dict[int, tuple[float | None, bool]] = {}
    if signal_processing_profile is not None:
        for physical_index, (plan, acquired) in enumerate(
            zip(physical_plans, acquired_traces, strict=True)
        ):
            records: list[DiscoveryPhysicalFactRecord] = []
            initial_candidate = _candidate_from_trace(
                plan.group,
                acquired.trace.rt,
                acquired.trace.intensity,
                settings=settings,
                peak_config=peak_config,
                signal_processing_profile=signal_processing_profile,
                prepared_signal_cache=prepared_signal_cache,
                physical_work_ordinal=physical_index,
                physical_acquisition_source_id=acquisition_source.acquisition_source_id,
                physical_acquired_trace=acquired,
                physical_evaluation_observer=records.append,
            )
            (initial_physical_facts[physical_index],) = records
            physical_trace_qualities[physical_index] = (
                initial_candidate.ms1_trace_quality
            )
        reacquisition: list[tuple[int, _DiscoveryMs1RequestPlan]] = []
        for physical_index, (plan, acquired) in enumerate(
            zip(physical_plans, acquired_traces, strict=True)
        ):
            evaluation = initial_physical_facts[physical_index].evaluation
            expanded = _expanded_physical_request_plan(
                plan,
                acquired,
                profile=signal_processing_profile,
                settings=settings,
                physical_evaluation=evaluation,
            )
            if expanded is not None:
                reacquisition.append((physical_index, expanded))
        reacquisition_plans = tuple(reacquisition)
        for indexed_chunk in _chunks(
            reacquisition_plans,
            _DISCOVERY_MS1_BATCH_MAX_REQUESTS,
        ):
            plan_chunk = tuple(plan for _physical_index, plan in indexed_chunk)
            batch = acquire_trace_batch(
                capability,
                tuple(plan.request for plan in plan_chunk),
                superwindow_raw,
            )
            _validate_acquired_plan_chunk(plan_chunk, batch)
            batches.append(batch)
            for (physical_index, expanded_plan), acquired in zip(
                indexed_chunk,
                batch.traces,
                strict=True,
            ):
                del physical_index_by_question[
                    _physical_request_question(
                        physical_plans[physical_index], acquisition_source
                    )
                ]
                physical_index_by_question[
                    _physical_request_question(expanded_plan, acquisition_source)
                ] = physical_index
                physical_plans[physical_index] = expanded_plan
                acquired_traces[physical_index] = acquired
                del initial_physical_facts[physical_index]
                del physical_trace_qualities[physical_index]

    scan_windows_by_batch = tuple(
        tuple(
            _native_scan_window(
                acquired.coordinate.first_scan,
                acquired.coordinate.last_scan,
            )
            for acquired in batch.traces
        )
        for batch in batches
    )
    scan_windows = tuple(
        scan_window
        for batch_scan_windows in scan_windows_by_batch
        for scan_window in batch_scan_windows
    )
    measurements = _DiscoveryMs1Measurements(
        input_group_count=len(group_tuple),
        empty_group_count=sum(not group.seeds for group in group_tuple),
        exact_scan_window_count=len(set(scan_windows)),
        predicted_chunked_call_count=sum(
            _predicted_chunked_call_count(batch_scan_windows)
            for batch_scan_windows in scan_windows_by_batch
        ),
        batch_max_requests=_DISCOVERY_MS1_BATCH_MAX_REQUESTS,
        acquisition_batches=tuple(batches),
        initial_request_count=len(physical_plans),
        reacquisition_request_count=len(reacquisition_plans)
        if signal_processing_profile is not None
        else 0,
    )
    if measurement_observer is not None:
        measurement_observer(measurements)

    if len(acquired_traces) != len(physical_plans):
        raise ValueError("Discovery MS1 acquisition lost a physical request")
    physical_work_manifest: list[DiscoveryPhysicalWorkItem] = []
    physical_evaluations: list[DiscoveryPhysicalFactRecord] = []
    physical_fact_by_index: dict[int, DiscoveryPhysicalFactRecord] = {}
    candidates: list[DiscoveryCandidate] = []
    candidate_plans: list[_DiscoveryMs1RequestPlan] = []
    candidate_physical_indices: list[int] = []
    for plan, physical_index in zip(plans, physical_indices, strict=True):
        acquired = acquired_traces[physical_index]
        existing_fact = physical_fact_by_index.get(physical_index)
        if existing_fact is None:
            existing_fact = initial_physical_facts.get(physical_index)
            if existing_fact is not None:
                physical_fact_by_index[physical_index] = existing_fact
                physical_work_manifest.append(existing_fact.work_item)
                physical_evaluations.append(existing_fact)
        if signal_processing_profile is not None and existing_fact is None:
            initial_candidate = _candidate_from_trace(
                plan.group,
                acquired.trace.rt,
                acquired.trace.intensity,
                settings=settings,
                peak_config=peak_config,
                signal_processing_profile=signal_processing_profile,
                prepared_signal_cache=prepared_signal_cache,
                physical_work_ordinal=physical_index,
                physical_acquisition_source_id=acquisition_source.acquisition_source_id,
                physical_acquired_trace=acquired,
                physical_work_observer=physical_work_manifest.append,
                physical_evaluation_observer=physical_evaluations.append,
            )
            existing_fact = physical_evaluations[-1]
            physical_fact_by_index[physical_index] = existing_fact
            physical_trace_qualities[physical_index] = (
                initial_candidate.ms1_trace_quality
            )
        groups_for_trace = (
            (plan.group,)
            if existing_fact is None
            else _groups_for_physical_peaks(
                plan.group, existing_fact.evaluation, settings
            )
        )
        for group in groups_for_trace:
            candidate_plan = replace(
                plan, group=group, request=acquired.coordinate.request
            )
            question = _physical_request_question(candidate_plan, acquisition_source)
            index = physical_index_by_question.get(question)
            if index is None:
                # A new physical question reuses the group's acquired signal.
                # It is not another RAW acquisition or another integrated vote.
                index = len(physical_plans)
                physical_index_by_question[question] = index
                physical_plans.append(candidate_plan)
                acquired_traces.append(acquired)
            candidate_trace = acquired_traces[index]
            fact = physical_fact_by_index.get(index)
            candidate = _candidate_from_trace(
                group,
                candidate_trace.trace.rt,
                candidate_trace.trace.intensity,
                settings=settings,
                peak_config=peak_config,
                signal_processing_profile=signal_processing_profile,
                prepared_signal_cache=prepared_signal_cache,
                physical_work_ordinal=index,
                physical_acquisition_source_id=acquisition_source.acquisition_source_id,
                physical_acquired_trace=candidate_trace,
                physical_fact_record=fact,
                physical_trace_quality=physical_trace_qualities.get(index),
                physical_work_observer=physical_work_manifest.append,
                physical_evaluation_observer=physical_evaluations.append,
            )
            if signal_processing_profile is not None:
                if fact is None:
                    fact = physical_evaluations[-1]
                    physical_fact_by_index[index] = fact
                    physical_trace_qualities[index] = candidate.ms1_trace_quality
                if index not in apex_readings:
                    # Read the retained peak after reacquisition and seed
                    # partitioning; shared physical questions reuse the reading.
                    apex_readings[index] = _apex_ion_reading(
                        candidate_plan,
                        candidate_trace,
                        fact.evaluation,
                        ms1_centroid_mz=ms1_centroid_mz,
                    )
                ion_mz, _doubly_charged = apex_readings[index]
                if ion_mz is not None:
                    candidate = replace(candidate, ms1_apex_mz=ion_mz)
            candidates.append(candidate)
            candidate_plans.append(candidate_plan)
            candidate_physical_indices.append(index)
    physical_indices = candidate_physical_indices
    merge_result = candidate_merge.merge_candidates_by_ms1_peak_with_lineage(
        candidates,
        settings=settings,
        ms1_centroid_mz=ms1_centroid_mz,
    )
    merge_result = _verify_precursor_evidence(
        raw, merge_result, candidate_plans, candidates, settings=settings
    )
    doubly_charged_by_source = tuple(
        apex_readings.get(index, (None, False))[1] for index in physical_indices
    )
    if any(doubly_charged_by_source):
        merge_result = _hold_doubly_charged_candidates(
            merge_result, doubly_charged_by_source
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
                candidate=replace(
                    merge_result.candidates[binding.final_ordinal],
                    ms1_search_rt_min=physical_fact_by_index[
                        physical_indices[binding.representative_source_ordinal]
                    ].work_item.xic_rt_min,
                    ms1_search_rt_max=physical_fact_by_index[
                        physical_indices[binding.representative_source_ordinal]
                    ].work_item.xic_rt_max,
                ),
                representative_source_ordinal=(binding.representative_source_ordinal),
                member_source_ordinals=binding.member_source_ordinals,
                member_work_ordinals=tuple(
                    physical_indices[index] for index in binding.member_source_ordinals
                ),
                representative_work_ordinal=physical_indices[
                    binding.representative_source_ordinal
                ],
                selection_subject_id=physical_fact_by_index[
                    physical_indices[binding.representative_source_ordinal]
                ].selection_subject_id,
                physical_fact_record=physical_fact_by_index[
                    physical_indices[binding.representative_source_ordinal]
                ],
            )
            for binding in merge_result.bindings
        )
    return _DiscoveryMs1AcquisitionResult(
        candidates=(
            tuple(binding.candidate for binding in physical_bindings)
            if signal_processing_profile is not None
            else merge_result.candidates
        ),
        measurements=measurements,
        physical_work_manifest=tuple(
            sorted(physical_work_manifest, key=lambda work: work.work_ordinal)
        ),
        physical_evaluations=bind_discovery_physical_record_evidence(
            tuple(
                sorted(
                    physical_evaluations, key=lambda fact: fact.work_item.work_ordinal
                )
            ),
            physical_bindings,
        ),
        physical_bindings=physical_bindings,
    )


def _physical_request_question(
    plan: _DiscoveryMs1RequestPlan,
    source: AcquisitionSourceIdentity,
) -> tuple[str, str, XICRequest]:
    return (
        source.acquisition_source_id,
        build_discovery_physical_selection_subject_id(
            sample_stem=plan.group.sample_stem,
            best_ms2_scan_id=select_representative_seed(plan.group.seeds).scan_number,
            precursor_mz=plan.group.precursor_mz,
            product_mz=plan.group.product_mz,
        ),
        plan.request,
    )


def _groups_for_physical_peaks(
    group: DiscoverySeedGroup,
    evaluation: PhysicalPeakEvaluation,
    settings: DiscoverySettings,
) -> tuple[DiscoverySeedGroup, ...]:
    """Partition a seed group by owner-selected physical segments, not RT gaps."""
    primary = evaluation.question.admissible_segment_ids
    by_segment: dict[tuple[str, ...], list[DiscoverySeed]] = {primary: []}
    for seed in group.seeds:
        question = mint_physical_peak_question(
            evaluation.prepared,
            evaluation.segment_universe,
            selection_subject_id=evaluation.question.selection_subject_id,
            query_rt_min=seed.rt,
            selection_policy_version=evaluation.question.selection_policy_version,
        )
        key = question.admissible_segment_ids
        # Unresolved/outside scans remain with the original representative;
        # they cannot create or lend support to a different physical peak.
        if len(key) != 1:
            key = primary
        by_segment.setdefault(key, []).append(seed)
    if len(by_segment) == 1:
        return (group,)
    return tuple(
        build_discovery_seed_group(seeds, settings=settings)
        for seeds in by_segment.values()
        if seeds
    )


def _verify_precursor_evidence(
    raw: object,
    merged: candidate_merge.DiscoveryCandidateMergeResult,
    plans: Sequence[_DiscoveryMs1RequestPlan],
    original_candidates: Sequence[DiscoveryCandidate],
    *,
    settings: DiscoverySettings,
) -> candidate_merge.DiscoveryCandidateMergeResult:
    """Verify member evidence against the final retained physical peak."""
    seed_groups = tuple(
        tuple(
            seed
            for source in binding.member_source_ordinals
            for seed in plans[source].group.seeds
        )
        for binding in merged.bindings
    )
    retained_seeds = tuple(
        seed
        for candidate, seeds in zip(merged.candidates, seed_groups, strict=True)
        if candidate.ms1_peak_found
        for seed in seeds
    )
    measured = read_parent_ms1_observations(
        raw, retained_seeds, tolerance_ppm=settings.precursor_mz_tolerance_ppm
    )
    by_seed = dict(zip(retained_seeds, measured, strict=True))
    verified: list[DiscoveryCandidate] = []
    bindings: list[candidate_merge.DiscoveryCandidateMergeBinding] = []
    for candidate, original_seeds, binding in zip(
        merged.candidates, seed_groups, merged.bindings, strict=True
    ):
        bindings.append(binding)
        if not candidate.ms1_peak_found:
            verified.append(candidate)
            continue
        seeds = [by_seed[seed] for seed in original_seeds]
        support_events = []
        for seed in seeds:
            assessment = seed.assess_precursor_loss(settings.nl_tolerance_ppm)
            if assessment.supports_nl and assessment.reference_mz is not None:
                parent = seed.ms1_precursor_observation
                support_events.append(
                    (
                        assessment.reference_mz,
                        seed.rt if parent is None else parent.rt_min,
                        seed.rt,
                    )
                )
        has_support = _candidate_has_precursor_support(
            candidate, support_events, settings=settings
        )
        if not has_support:
            for (
                rebuilt,
                replacement_binding,
            ) in candidate_merge.same_apex_representative_alternatives(
                original_candidates, binding, settings=settings
            ):
                if _candidate_has_precursor_support(
                    rebuilt, support_events, settings=settings
                ):
                    candidate = rebuilt
                    bindings[-1] = replacement_binding
                    has_support = True
                    break
        verified.append(
            replace(
                candidate,
                tag_evidence_json=merge_seed_evidence(seeds, settings=settings),
                discovery_candidate_state=assign_discovery_candidate_state(
                    ms1_peak_found=True, has_nl_support=has_support
                ),
            )
        )
    return replace(merged, candidates=tuple(verified), bindings=tuple(bindings))


def _candidate_has_precursor_support(
    candidate: DiscoveryCandidate,
    support_events: Sequence[tuple[float, float, float]],
    *,
    settings: DiscoverySettings,
) -> bool:
    return any(
        within_ppm(
            reference_mz, candidate.physical_mz, settings.precursor_mz_tolerance_ppm
        )
        and candidate.ms1_peak_rt_start is not None
        and candidate.ms1_peak_rt_end is not None
        and candidate.ms1_peak_rt_start <= support_rt
        and scan_rt <= candidate.ms1_peak_rt_end
        for reference_mz, support_rt, scan_rt in support_events
    )


def _discovery_ms1_request_plan(
    groups: Iterable[DiscoverySeedGroup],
    *,
    settings: DiscoverySettings,
    minimum_padding_min: float = 0.0,
) -> tuple[_DiscoveryMs1RequestPlan, ...]:
    plans: list[_DiscoveryMs1RequestPlan] = []
    for input_ordinal, group in enumerate(groups):
        if not group.seeds:
            continue
        padding_min = max(settings.ms1_search_padding_min, minimum_padding_min)
        plans.append(
            _DiscoveryMs1RequestPlan(
                input_ordinal=input_ordinal,
                group=group,
                request=XICRequest(
                    mz=group.precursor_mz,
                    rt_min=max(
                        0.0,
                        group.rt_seed_min - padding_min,
                    ),
                    rt_max=group.rt_seed_max + padding_min,
                    ppm_tol=settings.precursor_mz_tolerance_ppm,
                ),
            )
        )
    return tuple(plans)


def _expanded_physical_request_plan(
    plan: _DiscoveryMs1RequestPlan,
    acquired: AcquiredTrace,
    *,
    profile: SignalProcessingProfile,
    settings: DiscoverySettings,
    physical_evaluation: PhysicalPeakEvaluation,
) -> _DiscoveryMs1RequestPlan | None:
    rt = acquired.trace.rt
    intensity = acquired.trace.intensity
    point_count = len(rt)
    if point_count < 2:
        return None
    if (
        rt.ndim != 1
        or intensity.ndim != 1
        or rt.shape != intensity.shape
        or not np.all(np.isfinite(rt))
        or not np.all(np.isfinite(intensity))
    ):
        return None
    deltas = np.diff(rt)
    if not np.all(deltas > 0.0):
        return None
    cadence_min = float(np.median(deltas))
    if not math.isfinite(cadence_min) or cadence_min <= 0.0:
        return None

    if point_count >= profile.window_points:
        if physical_evaluation.reason != "incomplete_peak_context":
            return None
        extra_context = max(
            _DISCOVERY_PHYSICAL_CONTEXT_HALF_WINDOW_MIN,
            cadence_min * (profile.window_points // 2),
        )
        return replace(
            plan,
            request=replace(
                plan.request,
                rt_min=max(0.0, plan.request.rt_min - extra_context),
                rt_max=plan.request.rt_max + extra_context,
            ),
        )

    precursor_mz_basis = plan.group.precursor_mz_basis
    if precursor_mz_basis == "product_plus_neutral_loss":
        if not _has_strong_inferred_precursor_support(plan.group, settings=settings):
            return None
    elif precursor_mz_basis not in {"scan_precursor", "scan_monoisotopic", "mixed"}:
        return None

    extra_point_count = profile.window_points - point_count + 2
    extra_span_min = cadence_min * extra_point_count
    left_span_min = cadence_min * (extra_point_count // 2)
    expanded_rt_min = max(0.0, plan.request.rt_min - left_span_min)
    actual_left_span_min = plan.request.rt_min - expanded_rt_min
    expanded_rt_max = plan.request.rt_max + (extra_span_min - actual_left_span_min)
    return replace(
        plan,
        request=XICRequest(
            mz=plan.request.mz,
            rt_min=expanded_rt_min,
            rt_max=expanded_rt_max,
            ppm_tol=plan.request.ppm_tol,
        ),
    )


def _has_strong_inferred_precursor_support(
    group: DiscoverySeedGroup,
    *,
    settings: DiscoverySettings,
) -> bool:
    return (
        len(group.seeds) >= 2
        and max(seed.product_intensity for seed in group.seeds)
        >= settings.evidence_profile.thresholds.product_intensity_high_min
    )


def _apex_ion_reading(
    plan: _DiscoveryMs1RequestPlan,
    acquired: AcquiredTrace,
    evaluation: PhysicalPeakEvaluation,
    *,
    ms1_centroid_mz: candidate_merge.MS1CentroidMz | None,
) -> tuple[float | None, bool]:
    """The MS1 ion read at the resolved peak of an inferred precursor.

    MS1 is the method's accurate-mass scan, while ddMS2 runs at a quarter of
    its resolution, so product + loss only points at the ion. The ion is read
    inside the request at the apex scans; readings spread wider than a few ppm
    come from two ions and report none. A selected precursor is not read,
    since a reading near it can belong to a neighbouring ion or isotope. The
    m/z is reported only: the precursor, its trace and its MS2 evidence keep
    product + loss. The flag is true when the ion's doubly charged isotope is
    read in most of the scans where the ion is read.
    """
    facts = evaluation.facts
    if (
        plan.group.precursor_mz_basis != "product_plus_neutral_loss"
        or ms1_centroid_mz is None
        or evaluation.state != "available"
        or facts is None
    ):
        return None, False
    rt = acquired.trace.rt
    intensity = acquired.trace.intensity
    if len(rt) == 0:
        return None, False
    apex = int(np.argmin(np.abs(rt - facts.apex_rt_min)))
    request = plan.request
    half_width = request.mz * request.ppm_tol * 1e-6
    readings = []
    read_rts = []
    for index in range(
        max(0, apex - _APEX_ION_SCAN_RADIUS),
        min(len(rt), apex + _APEX_ION_SCAN_RADIUS + 1),
    ):
        mz = ms1_centroid_mz(
            float(rt[index]), request.mz - half_width, request.mz + half_width
        )
        if mz is not None:
            readings.append((mz, float(intensity[index])))
            read_rts.append(float(rt[index]))
    measured = weighted_ion_mz(readings, max_spread_ppm=_APEX_ION_SPREAD_PPM)
    if measured is None:
        return None, False
    return measured, _reads_doubly_charged_isotope(
        measured, read_rts, ms1_centroid_mz=ms1_centroid_mz
    )


def _reads_doubly_charged_isotope(
    ion_mz: float,
    read_rts: Sequence[float],
    *,
    ms1_centroid_mz: candidate_merge.MS1CentroidMz,
) -> bool:
    isotope_mz = ion_mz + _DOUBLY_CHARGED_ISOTOPE_SPACING_MZ
    half_width = isotope_mz * _APEX_ION_SPREAD_PPM * 1e-6
    isotope_reads = sum(
        ms1_centroid_mz(rt, isotope_mz - half_width, isotope_mz + half_width)
        is not None
        for rt in read_rts
    )
    return 2 * isotope_reads > len(read_rts)


def _hold_doubly_charged_candidates(
    merge_result: candidate_merge.DiscoveryCandidateMergeResult,
    doubly_charged_by_source: tuple[bool, ...],
) -> candidate_merge.DiscoveryCandidateMergeResult:
    """Hold for review a candidate whose every seed read a doubly charged ion.

    Any singly charged member carries the candidate, so the hold is applied
    after the merge, which reassigns the state.
    """
    return replace(
        merge_result,
        candidates=tuple(
            replace(
                candidate,
                discovery_candidate_state="review_only_ambiguous_coisolation",
                reason=(
                    "inferred precursor reads a doubly charged isotope at the "
                    "peak apex; retained for review"
                ),
            )
            if all(doubly_charged_by_source[s] for s in binding.member_source_ordinals)
            else candidate
            for candidate, binding in zip(
                merge_result.candidates, merge_result.bindings, strict=True
            )
        ),
    )


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
    physical_trace_quality: str | None = None,
    physical_work_observer: Callable[[DiscoveryPhysicalWorkItem], None] | None = None,
    physical_evaluation_observer: (
        Callable[[DiscoveryPhysicalFactRecord], None] | None
    ) = None,
) -> DiscoveryCandidate:
    best_seed = select_representative_seed(group.seeds)
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
        rt_min = request.rt_min
        rt_max = request.rt_max
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
            if physical_trace_quality is None:
                raise ValueError(
                    "reused Discovery physical fact lost trace qualification"
                )
            ms1_fields = _ms1_fields_from_physical_evaluation(
                physical_evaluation,
                work_item=physical_work,
                settings=settings,
            )
            ms1_fields.trace_quality = physical_trace_quality
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
    physical_overlap = ms1_fields.trace_quality == "physical_shoulder_overlap"
    if physical_overlap:
        priority = "MEDIUM"
        reason += "; physical_shoulder_overlap"
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
        is_provisional=(
            physical_overlap
            or (len(group.seeds) < 2 and ms1_fields.trace_quality == "provisional")
        ),
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


def _with_tag_fields(
    candidate: DiscoveryCandidate,
    *,
    group: DiscoverySeedGroup,
    settings: DiscoverySettings,
) -> DiscoveryCandidate:
    matched_tag_names = candidate_merge.ordered_tag_names(
        group.matched_tag_names, settings=settings
    )
    return replace(
        candidate,
        selected_tag_count=len(settings.selected_tag_names),
        matched_tag_count=len(matched_tag_names),
        matched_tag_names=matched_tag_names,
        primary_tag_name=group.neutral_loss_tag,
        tag_combine_mode=settings.tag_combine_mode,
        tag_intersection_status=candidate_merge.tag_intersection_status(
            matched_tag_names,
            settings=settings,
        ),
        tag_evidence_json=group.tag_evidence_json,
    )


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
        selection_policy_version=(
            DISCOVERY_PHYSICAL_PEAK_POINT_POLICY
            if profile.window_points == DEFAULT_GAUSSIAN15_WINDOW_POINTS
            else PHYSICAL_PEAK_POINT_POLICY
        ),
    )
    fields = _ms1_fields_from_physical_evaluation(
        evaluation,
        work_item=work_item,
        settings=settings,
    )
    if (
        evaluation.state == "available"
        and evaluation.facts is not None
        and evaluation.answer.physical_segment_id is not None
    ):
        selected_id = evaluation.answer.physical_segment_id
        for seg in evaluation.segment_universe.segments:
            if seg.physical_segment_id == selected_id:
                raw_seg = np.asarray(
                    intensity[seg.start_index : seg.end_index], dtype=float
                )
                rt_seg = np.asarray(rt[seg.start_index : seg.end_index], dtype=float)
                raw_noise_mad = short_scale_noise_mad(rt_seg, raw_seg)
                if fields.trace_quality == "clean" and is_provisional_peak_shape(
                    rt_seg,
                    raw_seg,
                    apex_index=seg.apex_index - seg.start_index,
                    raw_noise_mad=raw_noise_mad,
                ):
                    fields.trace_quality = "provisional"
                break
    return fields, evaluation


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
        trace_quality=(
            "physical_shoulder_overlap"
            if evaluation.requires_overlap_review
            else "clean"
        ),
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
