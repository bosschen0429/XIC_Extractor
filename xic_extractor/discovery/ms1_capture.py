from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

import numpy as np

from xic_extractor.acquisition.physical_capture import (
    PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS,
    PHYSICAL_CONTEXT_HALF_WINDOW_MIN,
    CapturedPhysicalPeak,
    PhysicalPeakCapture,
    PhysicalPeakSearch,
)
from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    BatchXICAdapter,
    TraceAcquisitionBatch,
)
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
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlComposition,
    CidNlPhysicalCandidateFact,
    CidNlPhysicalProof,
    group_cid_nl_physical_subject_units,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    mint_physical_peak_question,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
    PhysicalSegment,
)
from xic_extractor.precursor_evidence import MS1MassWindowSource
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.xic_models import XICRequest

_DISCOVERY_MS1_PREDICTION_MAX_REQUESTS = 256
# The apex scan and one MS1 scan on each side read the ion's m/z; readings
# spread wider than a few ppm come from more than one ion.
_APEX_ION_SCAN_RADIUS = 1
_APEX_ION_SPREAD_PPM = 5.0
# 13C spacing of a doubly charged ion. Product + loss assumes a singly charged
# precursor, so an inferred ion read beside this isotope is not one.
_DOUBLY_CHARGED_ISOTOPE_SPACING_MZ = 1.0033548 / 2


class DiscoveryMs1Source(BatchXICAdapter, MS1MassWindowSource, Protocol):
    """RAW reader surface used by Discovery MS1 capture."""

    def batch_xic_source(self) -> BatchXICAdapter:
        """XIC adapter for physical capture; the reader chooses its read merging."""


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
    preparation_request_count: int = 0

    def __post_init__(self) -> None:
        count_values = (
            self.input_group_count,
            self.empty_group_count,
            self.exact_scan_window_count,
            self.predicted_chunked_call_count,
            self.initial_request_count,
            self.reacquisition_request_count,
            self.preparation_request_count,
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
        if self.reacquisition_request_count > 2 * self.initial_request_count:
            raise ValueError(
                "Discovery MS1 reacquisition exceeds its initial population"
            )
        if logical_request_count != (
            self.initial_request_count
            + self.reacquisition_request_count
            + self.preparation_request_count
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
    physical_work_manifest: tuple[DiscoveryPhysicalWorkItem, ...]
    physical_evaluations: tuple[DiscoveryPhysicalFactRecord, ...]
    physical_bindings: tuple["DiscoveryCandidatePhysicalBinding", ...]


def capture_ms1_candidates(
    raw: DiscoveryMs1Source,
    groups: Iterable[DiscoverySeedGroup],
    *,
    acquisition_source: AcquisitionSourceIdentity,
    settings: DiscoverySettings,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None] | None = None,
    signal_processing_profile: SignalProcessingProfile,
    ms1_centroid_mz: candidate_merge.MS1CentroidMz,
) -> _DiscoveryMs1AcquisitionResult:
    """Resolve CID-NL hits against shared physical capture, then project evidence."""

    group_tuple = tuple(groups)
    plans = _discovery_ms1_request_plan(
        group_tuple,
        settings=settings,
        minimum_padding_min=PHYSICAL_CONTEXT_HALF_WINDOW_MIN,
    )
    physical_capture = PhysicalPeakCapture(
        raw.batch_xic_source(),
        acquisition_source=acquisition_source,
        # Keep persisted acquisition coordinates compatible with current sidecars.
        adapter_namespace="xic_extractor.discovery.ms1_capture",
        profile=signal_processing_profile,
    )
    # Every seed may expose an incomplete edge, even when the representative
    # lands on a complete peak. Resolve context for the whole population before
    # attaching any seed; all questions retain the original group's mass query.
    searches = tuple(
        replace(_physical_search(plan, settings=settings), query_rt_min=seed.rt)
        for plan in plans
        for seed in plan.group.seeds
    )
    captured_by_question = {
        (search.selection_subject_id, search.query_rt_min, search.request): captured
        for search, captured in zip(
            searches, physical_capture.capture(searches), strict=True
        )
    }
    captured_peaks = tuple(
        captured_by_question[
            (
                _selection_subject_id(plan.group),
                select_representative_seed(plan.group.seeds).rt,
                plan.request,
            )
        ]
        for plan in plans
    )
    batches = physical_capture.acquisition_batches

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
        batch_max_requests=PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS,
        acquisition_batches=tuple(batches),
        initial_request_count=physical_capture.initial_request_count,
        reacquisition_request_count=physical_capture.reacquisition_request_count,
        preparation_request_count=physical_capture.preparation_request_count,
    )
    if measurement_observer is not None:
        measurement_observer(measurements)

    physical_fact_by_index: dict[int, DiscoveryPhysicalFactRecord] = {}
    apex_readings: dict[int, tuple[float | None, bool]] = {}
    candidates: list[DiscoveryCandidate] = []
    candidate_plans: list[_DiscoveryMs1RequestPlan] = []
    physical_indices: list[int] = []
    for plan, captured in zip(plans, captured_peaks, strict=True):
        if captured.work_ordinal not in physical_fact_by_index:
            physical_fact_by_index[captured.work_ordinal] = _cid_nl_physical_record(
                plan.group, captured
            )
        for group in _groups_for_physical_peaks(
            plan.group, captured.evaluation, settings
        ):
            selected = physical_capture.reselect(
                captured,
                selection_subject_id=_selection_subject_id(group),
                query_rt_min=select_representative_seed(group.seeds).rt,
            )
            index = selected.work_ordinal
            fact = physical_fact_by_index.get(index)
            if fact is None:
                fact = _cid_nl_physical_record(group, selected)
                physical_fact_by_index[index] = fact
            candidate = _candidate_from_physical_record(
                group,
                physical_fact_record=fact,
                trace_quality=selected.trace_quality,
                settings=settings,
            )
            candidate_plan = replace(plan, group=group, request=selected.search.request)
            if index not in apex_readings:
                # Read the retained peak after reacquisition and seed
                # partitioning; shared physical questions reuse the reading.
                apex_readings[index] = _apex_ion_reading(
                    candidate_plan,
                    selected.acquired_trace,
                    selected.evaluation,
                    ms1_centroid_mz=ms1_centroid_mz,
                )
            ion_mz, _doubly_charged = apex_readings[index]
            if ion_mz is not None:
                candidate = replace(candidate, ms1_apex_mz=ion_mz)
            candidates.append(candidate)
            candidate_plans.append(candidate_plan)
            physical_indices.append(index)
    # Complete each source's method admission before merging. A valid neighbor
    # cannot lend NL or isotope eligibility to a rejected source wrapper.
    method_seeds = tuple(
        seed
        for candidate, plan in zip(candidates, candidate_plans, strict=True)
        for seed in plan.group.seeds
    )
    measured = read_parent_ms1_observations(
        raw, method_seeds, tolerance_ppm=settings.precursor_mz_tolerance_ppm
    )
    measured_by_seed = dict(zip(method_seeds, measured, strict=True))
    source_result = candidate_merge.DiscoveryCandidateMergeResult(
        candidates=tuple(candidates),
        bindings=tuple(
            candidate_merge.DiscoveryCandidateMergeBinding(i, i, (i,))
            for i in range(len(candidates))
        ),
    )
    source_result = _verify_precursor_evidence(
        source_result,
        candidate_plans,
        candidates,
        measured_by_seed=measured_by_seed,
        settings=settings,
    )
    doubly_charged_by_source = tuple(
        apex_readings.get(index, (None, False))[1] for index in physical_indices
    )
    source_result = _hold_doubly_charged_candidates(
        source_result, doubly_charged_by_source
    )
    candidates = list(source_result.candidates)
    merge_result = candidate_merge.merge_candidates_by_ms1_peak_with_lineage(
        candidates,
        settings=settings,
        ms1_centroid_mz=ms1_centroid_mz,
    )
    merge_result = _verify_precursor_evidence(
        merge_result,
        candidate_plans,
        candidates,
        measured_by_seed=measured_by_seed,
        settings=settings,
    )
    closed = _close_duplicate_physical_views(
        raw,
        merge_result,
        tuple(physical_fact_by_index[index] for index in physical_indices),
        settings=settings,
    )
    if len(closed.candidates) < len(merge_result.candidates):
        merge_result = _verify_precursor_evidence(
            closed,
            candidate_plans,
            candidates,
            measured_by_seed=measured_by_seed,
            settings=settings,
        )
    # Only retained questions enter the sidecar. Per-seed context probes above
    # are not independent persisted work items.
    retained_ordinals = {
        ordinal: index for index, ordinal in enumerate(sorted(physical_fact_by_index))
    }
    physical_fact_by_index = {
        retained_ordinals[ordinal]: replace(
            fact,
            work_item=replace(fact.work_item, work_ordinal=retained_ordinals[ordinal]),
        )
        for ordinal, fact in physical_fact_by_index.items()
    }
    physical_indices = [retained_ordinals[index] for index in physical_indices]
    physical_bindings = tuple(
        DiscoveryCandidatePhysicalBinding(
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
        candidates=tuple(binding.candidate for binding in physical_bindings),
        measurements=measurements,
        physical_work_manifest=tuple(
            fact.work_item for _, fact in sorted(physical_fact_by_index.items())
        ),
        physical_evaluations=bind_discovery_physical_record_evidence(
            tuple(
                sorted(
                    physical_fact_by_index.values(),
                    key=lambda fact: fact.work_item.work_ordinal,
                )
            ),
            physical_bindings,
        ),
        physical_bindings=physical_bindings,
    )


def _selection_subject_id(group: DiscoverySeedGroup) -> str:
    return build_discovery_physical_selection_subject_id(
        sample_stem=group.sample_stem,
        best_ms2_scan_id=select_representative_seed(group.seeds).scan_number,
        precursor_mz=group.precursor_mz,
        product_mz=group.product_mz,
    )


def _close_duplicate_physical_views(
    raw: DiscoveryMs1Source,
    merged: candidate_merge.DiscoveryCandidateMergeResult,
    records: Sequence[DiscoveryPhysicalFactRecord],
    *,
    settings: DiscoverySettings,
) -> candidate_merge.DiscoveryCandidateMergeResult:
    """Acquire bounded ion evidence for representatives left by greedy merging.

    A later member can move a group's representative into another retained
    group's peak. Only same-apex, overlapping windows need this reconciliation;
    centroid acquisition stays here, while the physical owner decides identity.
    """
    proofs: dict[str, CidNlPhysicalProof] = {}
    buckets: dict[float, list[tuple[float, float, str]]] = {}
    for candidate, binding in zip(merged.candidates, merged.bindings, strict=True):
        if candidate.discovery_candidate_state != "ms1_feature_nl_supported":
            continue
        record = records[binding.representative_source_ordinal]
        peak = record.evaluation.facts
        if peak is None:
            continue
        work = record.work_item
        proof = CidNlPhysicalProof(
            acquisition_source_id=work.acquisition_source_id,
            apex_rt_min=peak.apex_rt_min,
            left_rt_min=peak.left_rt_min,
            right_rt_min=peak.right_rt_min,
            xic_mz=work.xic_mz,
            xic_ppm_tol=work.xic_ppm_tol,
            rt=record.acquired_trace.trace.rt,
            raw=record.acquired_trace.trace.intensity,
        )
        proofs[candidate.candidate_id] = proof
        radius = proof.xic_mz * proof.xic_ppm_tol * 1e-6
        buckets.setdefault(proof.apex_rt_min, []).append(
            (proof.xic_mz - radius, proof.xic_mz + radius, candidate.candidate_id)
        )
    eligible: set[str] = set()
    for intervals in buckets.values():
        upper = float("-inf")
        previous = ""
        for low, high, candidate_id in sorted(intervals):
            if low <= upper:
                eligible.update((previous, candidate_id))
            if high > upper:
                upper, previous = high, candidate_id
    windows_by_id: dict[str, tuple[tuple[int, float, float], ...]] = {}
    for candidate_id in sorted(eligible):
        proof = proofs[candidate_id]
        rt = np.asarray(proof.rt)
        apex = int(np.searchsorted(rt, proof.apex_rt_min))
        if not 0 < apex < len(rt) - 1:
            continue
        if not (
            proof.left_rt_min <= rt[apex - 1] and rt[apex + 1] <= proof.right_rt_min
        ):
            continue
        radius = proof.xic_mz * proof.xic_ppm_tol * 1e-6
        windows_by_id[candidate_id] = tuple(
            (
                raw.scan_window_for_request(
                    XICRequest(
                        proof.xic_mz, float(point), float(point), proof.xic_ppm_tol
                    )
                )[0],
                proof.xic_mz - radius,
                proof.xic_mz + radius,
            )
            for point in rt[apex - 1 : apex + 2]
        )
    requests = tuple(
        dict.fromkeys(
            window for windows in windows_by_id.values() for window in windows
        )
    )
    if not requests:
        return merged
    observations = dict(zip(requests, raw.read_ms1_mass_windows(requests), strict=True))
    acquired_proofs = {}
    for candidate_id, windows in windows_by_id.items():
        scans = tuple(
            scan for window in windows if (scan := observations[window]) is not None
        )
        if len(scans) == len(windows):
            acquired_proofs[candidate_id] = replace(
                proofs[candidate_id], apex_centroids=scans
            )
    composition = (
        CidNlComposition(
            tags=tuple(
                (p.tag, p.neutral_loss_da) for p in settings.neutral_loss_profiles
            ),
            combine_mode=settings.tag_combine_mode,
        )
        if len(settings.neutral_loss_profiles) > 1
        and settings.tag_combine_mode != "single"
        else None
    )
    units = group_cid_nl_physical_subject_units(
        tuple(
            CidNlPhysicalCandidateFact(
                candidate.candidate_id,
                candidate.physical_mz,
                candidate.configured_neutral_loss_da,
                acquired_proofs.get(candidate.candidate_id),
            )
            for candidate in merged.candidates
        ),
        composition=composition,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
    )
    return candidate_merge.close_physical_candidate_units(
        merged, units, settings=settings
    )


def _physical_search(
    plan: _DiscoveryMs1RequestPlan, *, settings: DiscoverySettings
) -> PhysicalPeakSearch:
    return PhysicalPeakSearch(
        selection_subject_id=_selection_subject_id(plan.group),
        query_rt_min=select_representative_seed(plan.group.seeds).rt,
        request=plan.request,
        allow_sparse_reacquisition=_allow_sparse_reacquisition(
            plan.group, settings=settings
        ),
    )


def _groups_for_physical_peaks(
    group: DiscoverySeedGroup,
    evaluation: PhysicalPeakEvaluation,
    settings: DiscoverySettings,
) -> tuple[DiscoverySeedGroup, ...]:
    """Partition a seed group by owner-selected physical segments, not RT gaps."""
    by_segment: dict[tuple[PhysicalSegment, ...], list[DiscoverySeed]] = {}
    for seed in group.seeds:
        question = mint_physical_peak_question(
            evaluation.prepared,
            evaluation.segment_universe,
            query_rt_min=seed.rt,
            selection_policy_version=evaluation.question.selection_policy_version,
        )
        key = question.admissible_segments
        # Unmapped or ambiguous seeds retain their own diagnostic lineage;
        # they are not members of the representative's resolved peak.
        by_segment.setdefault(key, []).append(seed)
    if len(by_segment) == 1:
        return (group,)
    return tuple(
        build_discovery_seed_group(seeds, settings=settings)
        for seeds in by_segment.values()
        if seeds
    )


def _verify_precursor_evidence(
    merged: candidate_merge.DiscoveryCandidateMergeResult,
    plans: Sequence[_DiscoveryMs1RequestPlan],
    original_candidates: Sequence[DiscoveryCandidate],
    *,
    measured_by_seed: dict[DiscoverySeed, DiscoverySeed],
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
    verified: list[DiscoveryCandidate] = []
    bindings: list[candidate_merge.DiscoveryCandidateMergeBinding] = []
    for candidate, original_seeds, binding in zip(
        merged.candidates, seed_groups, merged.bindings, strict=True
    ):
        bindings.append(binding)
        seeds = [measured_by_seed[seed] for seed in original_seeds]
        candidate = replace(
            candidate,
            has_precursor_nl_evidence=any(
                seed.assess_precursor_loss(settings.nl_tolerance_ppm).supports_nl
                for seed in seeds
            ),
        )
        if not candidate.ms1_peak_found:
            verified.append(
                replace(
                    candidate,
                    tag_evidence_json=merge_seed_evidence(seeds, settings=settings),
                )
            )
            continue
        if candidate.discovery_candidate_state in {
            "review_only_unresolved_peak_attribution",
            "review_only_ambiguous_coisolation",
        }:
            verified.append(
                replace(
                    candidate,
                    tag_evidence_json=merge_seed_evidence(seeds, settings=settings),
                )
            )
            continue
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
        and candidate.ms1_peak_rt_start <= support_rt <= candidate.ms1_peak_rt_end
        and candidate.ms1_peak_rt_start <= scan_rt <= candidate.ms1_peak_rt_end
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


def _allow_sparse_reacquisition(
    group: DiscoverySeedGroup, *, settings: DiscoverySettings
) -> bool:
    if group.precursor_mz_basis == "product_plus_neutral_loss":
        return (
            len(group.seeds) >= 2
            and max(seed.product_intensity for seed in group.seeds)
            >= settings.evidence_profile.thresholds.product_intensity_high_min
        )
    return group.precursor_mz_basis in {"scan_precursor", "scan_monoisotopic", "mixed"}


def _apex_ion_reading(
    plan: _DiscoveryMs1RequestPlan,
    acquired: AcquiredTrace,
    evaluation: PhysicalPeakEvaluation,
    *,
    ms1_centroid_mz: candidate_merge.MS1CentroidMz,
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
    """Hold inferred isotope sources before they can borrow a merge's support."""
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


def _cid_nl_physical_record(
    group: DiscoverySeedGroup, captured: CapturedPhysicalPeak
) -> DiscoveryPhysicalFactRecord:
    """Project completed physical work into the retained CID-NL sidecar schema."""
    acquired = captured.acquired_trace
    coordinate = acquired.coordinate
    request = coordinate.request
    question = captured.evaluation.question
    return DiscoveryPhysicalFactRecord(
        work_item=DiscoveryPhysicalWorkItem(
            work_ordinal=captured.work_ordinal,
            acquisition_source_id=coordinate.capability.acquisition_source.acquisition_source_id,
            sample_stem=group.sample_stem,
            best_ms2_scan_id=select_representative_seed(group.seeds).scan_number,
            precursor_mz=group.precursor_mz,
            product_mz=group.product_mz,
            query_rt_min=captured.search.query_rt_min,
            context_rt_start_min=question.context_rt_start_min,
            context_rt_end_min=question.context_rt_end_min,
            xic_mz=request.mz,
            xic_rt_min=request.rt_min,
            xic_rt_max=request.rt_max,
            xic_ppm_tol=request.ppm_tol,
        ),
        acquired_trace=acquired,
        evaluation=captured.evaluation,
        preparation_acquired_trace=captured.preparation_acquired_trace,
    )


def _candidate_from_physical_record(
    group: DiscoverySeedGroup,
    *,
    physical_fact_record: DiscoveryPhysicalFactRecord,
    trace_quality: str,
    settings: DiscoverySettings,
) -> DiscoveryCandidate:
    """Combine CID-NL evidence with completed physical facts, without recomputing."""
    best_seed = select_representative_seed(group.seeds)
    physical_work = physical_fact_record.work_item
    physical_evaluation = physical_fact_record.evaluation
    rt_min, rt_max = physical_work.xic_rt_min, physical_work.xic_rt_max
    ms1_fields = _ms1_fields_from_physical_evaluation(
        physical_evaluation,
        work_item=physical_work,
        trace_quality=trace_quality,
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
        if physical_evaluation.state in {"unavailable", "failed"}
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
    if physical_evaluation.requires_overlap_review:
        candidate = replace(
            candidate,
            discovery_candidate_state="review_only_unresolved_peak_attribution",
        )
    if candidate.candidate_id != physical_work.candidate_id:
        raise ValueError("physical work candidate identity diverged")
    return candidate


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


def _ms1_fields_from_physical_evaluation(
    evaluation: PhysicalPeakEvaluation,
    *,
    work_item: DiscoveryPhysicalWorkItem,
    trace_quality: str,
    settings: DiscoverySettings,
) -> _Ms1Fields:
    facts = evaluation.facts
    if facts is None:
        return _missing_ms1_fields(trace_quality=trace_quality)
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
        trace_quality=trace_quality,
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
