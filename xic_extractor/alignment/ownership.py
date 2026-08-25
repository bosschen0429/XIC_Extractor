from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupMemberSubject,
    group_member_subject_from_discovery_physical_selection,
)
from xic_extractor.alignment.matrix_handoff import integration_from_peak_trace
from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
    IdentityEvent,
    OwnerAssignment,
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.trace_context import alignment_trace_group
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.models import (
    MS1_FEATURE_BACKED_STATES,
    DiscoveryCandidate,
)
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryCandidatePhysicalBinding,
    validate_discovery_candidate_physical_binding,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.observations import mint_peak_observation_ref
from xic_extractor.peak_detection.region_audit import (
    PeakRegionAuditSummary,
    build_peak_region_audit_summary,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.signal_processing import find_peak_and_area
from xic_extractor.xic_models import XICRequest, XICTrace

_OWNER_QUANTITATION_CONTEXT_PADDING_MIN = 0.40


class OwnershipXICSource(Protocol):
    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]: ...


@dataclass(frozen=True)
class ResolvedPeak:
    rt: float
    peak_start: float
    peak_end: float
    area: float
    intensity: float
    region_audit: PeakRegionAuditSummary | None = None
    selected_integration: IntegrationResult | None = None


PeakResolver = Callable[
    [Any, NDArray[np.float64], NDArray[np.float64], ExtractionConfig, float],
    ResolvedPeak | None,
]


@dataclass(frozen=True)
class OwnershipBuildResult:
    owners: tuple[SampleLocalMS1Owner, ...]
    assignments: tuple[OwnerAssignment, ...]
    ambiguous_records: tuple[AmbiguousOwnerRecord, ...]


@dataclass(frozen=True)
class AlignmentCrossSampleIdentityContext:
    """Exact owner context that Alignment hands through to CrossSample."""

    processing_owners: ProcessingOwnerReferences
    samples_by_stem: Mapping[str, ResolvedCanonicalSampleIdentity]

    def __post_init__(self) -> None:
        if not isinstance(self.processing_owners, ProcessingOwnerReferences):
            raise TypeError("processing_owners must be owner-backed")
        if not isinstance(self.samples_by_stem, Mapping) or not self.samples_by_stem:
            raise TypeError("samples_by_stem must be a non-empty mapping")
        expected = {
            sample.lookup_token: sample
            for sample in self.processing_owners.sample_resolutions
        }
        if set(self.samples_by_stem) != set(expected) or any(
            self.samples_by_stem[stem] != expected[stem] for stem in expected
        ):
            raise ValueError("Alignment identity context crossed canonical samples")

    def sample_for(self, sample_stem: str) -> ResolvedCanonicalSampleIdentity:
        try:
            return self.samples_by_stem[sample_stem]
        except KeyError as exc:
            raise ValueError(
                "owner sample is outside Alignment identity context"
            ) from exc


@dataclass(frozen=True)
class _ResolvedCandidate:
    candidate: Any
    event: IdentityEvent
    apex_rt: float
    peak_start_rt: float
    peak_end_rt: float
    area: float
    height: float
    region_audit: PeakRegionAuditSummary | None = None
    selected_integration: IntegrationResult | None = None
    quantification_eligible: bool = True
    quantification_exclusion_reason: str = ""
    cross_sample_member_subject: CrossSampleGroupMemberSubject | None = None


@dataclass(frozen=True)
class _ResolutionOutcome:
    resolved: _ResolvedCandidate | None
    unresolved: OwnerAssignment | None


_DOUBLET_APEX_SEPARATION_WIDTH_FRACTION = 0.5
_ResolutionRequestItem = tuple[int, Any, float, XICRequest, bool]
_ResolutionRequestGroupKey = tuple[str, int | float, int | float]


def discovery_candidate_is_owner_build_eligible(
    candidate: DiscoveryCandidate,
) -> bool:
    """Return whether a final Discovery candidate may mint an Alignment owner."""

    return candidate.discovery_candidate_state in MS1_FEATURE_BACKED_STATES


def build_sample_local_owners(
    candidates: Sequence[Any],
    *,
    raw_sources: Mapping[str, OwnershipXICSource],
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
    peak_resolver: PeakResolver | None = None,
    raw_xic_batch_size: int = 1,
    emit_region_audit: bool = False,
    region_audit_family_ids: frozenset[str] | None = None,
    audit_evidence_mode: str = "full",
    cross_sample_identity_context: AlignmentCrossSampleIdentityContext | None = None,
) -> OwnershipBuildResult:
    if raw_xic_batch_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")
    active_peak_resolver = peak_resolver or _default_peak_resolver
    effective_emit_region_audit = emit_region_audit and audit_evidence_mode != "none"
    outcomes = _resolve_candidates(
        candidates,
        raw_sources,
        alignment_config,
        peak_config,
        active_peak_resolver,
        raw_xic_batch_size,
        effective_emit_region_audit,
        region_audit_family_ids,
        cross_sample_identity_context,
    )
    return _ownership_result_from_outcomes(
        outcomes,
        alignment_config=alignment_config,
        cross_sample_identity_context=cross_sample_identity_context,
    )


def build_sample_local_owners_from_discovery_bindings(
    bindings: Sequence[DiscoveryCandidatePhysicalBinding],
    *,
    alignment_config: AlignmentConfig,
    cross_sample_identity_context: AlignmentCrossSampleIdentityContext | None = None,
) -> OwnershipBuildResult:
    """Build owners from Discovery's canonical in-memory physical results."""

    outcomes: list[_ResolutionOutcome] = []
    for binding in bindings:
        if not isinstance(binding, DiscoveryCandidatePhysicalBinding):
            raise TypeError(
                "bindings must contain DiscoveryCandidatePhysicalBinding values"
            )
        validate_discovery_candidate_physical_binding(binding)
        candidate = binding.candidate
        if not discovery_candidate_is_owner_build_eligible(candidate):
            continue
        evaluation = binding.physical_evaluation
        if evaluation.state != "available":
            outcomes.append(
                _unresolved_outcome(
                    candidate,
                    f"physical_{evaluation.state}:{evaluation.reason}",
                )
            )
            continue
        integration = evaluation.integration
        if integration is None:
            raise ValueError("available Discovery physical binding lost integration")
        seed_rt = _candidate_seed_rt(candidate)
        member_subject = (
            _member_subject_from_discovery_binding(
                binding,
                context=cross_sample_identity_context,
            )
            if cross_sample_identity_context is not None
            else None
        )
        outcomes.append(
            _ResolutionOutcome(
                resolved=_ResolvedCandidate(
                    candidate=candidate,
                    event=_identity_event(candidate, seed_rt=seed_rt),
                    apex_rt=integration.rt_apex_min,
                    peak_start_rt=integration.rt_left_min,
                    peak_end_rt=integration.rt_right_min,
                    area=integration.area_raw_counts_seconds,
                    height=integration.height_raw,
                    selected_integration=integration,
                    quantification_eligible=_quantification_eligible(
                        integration.rt_apex_min,
                        integration.rt_left_min,
                        integration.rt_right_min,
                    ),
                    quantification_exclusion_reason=_quantification_exclusion_reason(
                        integration.rt_apex_min,
                        integration.rt_left_min,
                        integration.rt_right_min,
                    ),
                    cross_sample_member_subject=member_subject,
                ),
                unresolved=None,
            )
        )
    return _ownership_result_from_outcomes(
        tuple(outcomes),
        alignment_config=alignment_config,
        cross_sample_identity_context=cross_sample_identity_context,
    )


def _ownership_result_from_outcomes(
    outcomes: Sequence[_ResolutionOutcome],
    *,
    alignment_config: AlignmentConfig,
    cross_sample_identity_context: AlignmentCrossSampleIdentityContext | None = None,
) -> OwnershipBuildResult:
    resolved = tuple(
        outcome.resolved for outcome in outcomes if outcome.resolved is not None
    )
    unresolved_assignments = tuple(
        outcome.unresolved for outcome in outcomes if outcome.unresolved is not None
    )
    owners: list[SampleLocalMS1Owner] = []
    assignments: list[OwnerAssignment] = []
    ambiguous_records: list[AmbiguousOwnerRecord] = []
    by_sample: dict[str, list[_ResolvedCandidate]] = defaultdict(list)
    for item in resolved:
        by_sample[item.event.sample_stem].append(item)
    for sample_stem in sorted(by_sample):
        sample_owners, sample_assignments, sample_ambiguous = _owners_for_sample(
            sample_stem,
            by_sample[sample_stem],
            alignment_config=alignment_config,
            cross_sample_identity_context=cross_sample_identity_context,
        )
        owners.extend(sample_owners)
        assignments.extend(sample_assignments)
        ambiguous_records.extend(sample_ambiguous)
    return OwnershipBuildResult(
        owners=tuple(owners),
        assignments=(*unresolved_assignments, *assignments),
        ambiguous_records=tuple(ambiguous_records),
    )


def _resolve_candidates(
    candidates: Sequence[Any],
    raw_sources: Mapping[str, OwnershipXICSource],
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
    peak_resolver: PeakResolver,
    raw_xic_batch_size: int,
    emit_region_audit: bool,
    region_audit_family_ids: frozenset[str] | None,
    cross_sample_identity_context: AlignmentCrossSampleIdentityContext | None,
) -> tuple[_ResolutionOutcome, ...]:
    outcomes: list[_ResolutionOutcome | None] = [None] * len(candidates)
    requests_by_sample: dict[
        str,
        list[tuple[int, Any, float, XICRequest, bool]],
    ] = defaultdict(list)
    for index, candidate in enumerate(candidates):
        sample_stem = str(candidate.sample_stem)
        source = raw_sources.get(sample_stem)
        if source is None:
            outcomes[index] = _unresolved_outcome(candidate, "missing_raw_source")
            continue
        seed_rt = _candidate_seed_rt(candidate)
        rt_min, rt_max = _candidate_resolution_rt_window(
            candidate,
            seed_rt,
            alignment_config,
        )
        requests_by_sample[sample_stem].append(
            (
                index,
                candidate,
                seed_rt,
                XICRequest(
                    mz=float(candidate.precursor_mz),
                    rt_min=rt_min,
                    rt_max=rt_max,
                    ppm_tol=alignment_config.preferred_ppm,
                ),
                _emit_region_audit_for_candidate(
                    emit_region_audit,
                    candidate,
                    region_audit_family_ids,
                ),
            )
        )
    for sample_stem, sample_requests in requests_by_sample.items():
        source = raw_sources[sample_stem]
        for chunk in _request_chunks(
            source,
            tuple(sample_requests),
            raw_xic_batch_size,
        ):
            traces = _extract_many(source, tuple(item[3] for item in chunk))
            for (
                index,
                candidate,
                seed_rt,
                _request,
                item_emit_region_audit,
            ), trace in zip(
                chunk,
                traces,
                strict=True,
            ):
                outcomes[index] = _resolve_candidate_trace(
                    candidate,
                    seed_rt,
                    trace,
                    peak_config,
                    peak_resolver,
                    ppm_tol=_request.ppm_tol,
                    emit_region_audit=item_emit_region_audit,
                    request=_request,
                    cross_sample_identity_context=cross_sample_identity_context,
                )
    return tuple(outcome for outcome in outcomes if outcome is not None)


def _resolve_candidate_trace(
    candidate: Any,
    seed_rt: float,
    trace: XICTrace,
    peak_config: ExtractionConfig,
    peak_resolver: PeakResolver,
    *,
    ppm_tol: float,
    emit_region_audit: bool = False,
    request: XICRequest,
    cross_sample_identity_context: AlignmentCrossSampleIdentityContext | None,
) -> _ResolutionOutcome:
    rt_array, intensity_array = _validated_trace_arrays(trace.rt, trace.intensity)
    peak = _resolve_peak(
        candidate,
        rt_array,
        intensity_array,
        peak_config,
        seed_rt,
        peak_resolver,
        ppm_tol=ppm_tol,
        emit_region_audit=emit_region_audit,
    )
    if peak is None:
        return _unresolved_outcome(candidate, "peak_not_found")
    quantification_apex, quantification_left, quantification_right = (
        _resolved_peak_quantification_bounds(peak)
    )
    return _ResolutionOutcome(
        resolved=_ResolvedCandidate(
            candidate=candidate,
            event=_identity_event(candidate, seed_rt=seed_rt),
            apex_rt=peak.rt,
            peak_start_rt=peak.peak_start,
            peak_end_rt=peak.peak_end,
            area=peak.area,
            height=peak.intensity,
            region_audit=peak.region_audit,
            selected_integration=peak.selected_integration,
            quantification_eligible=_quantification_eligible(
                quantification_apex,
                quantification_left,
                quantification_right,
            ),
            quantification_exclusion_reason=_quantification_exclusion_reason(
                quantification_apex,
                quantification_left,
                quantification_right,
            ),
            cross_sample_member_subject=_member_subject_from_raw_trace(
                candidate=candidate,
                trace=trace,
                request=request,
                integration=peak.selected_integration,
                context=cross_sample_identity_context,
            ),
        ),
        unresolved=None,
    )


def _member_subject_from_raw_trace(
    *,
    candidate: Any,
    trace: XICTrace,
    request: XICRequest,
    integration: IntegrationResult | None,
    context: AlignmentCrossSampleIdentityContext | None,
) -> CrossSampleGroupMemberSubject | None:
    if context is None:
        return None
    if integration is None:
        raise ValueError("exact CrossSample identity requires measured integration")
    sample_stem = str(candidate.sample_stem)
    sample = context.sample_for(sample_stem)
    source = resolve_request_acquisition_source(
        processing_owners=context.processing_owners,
        sample=sample,
    )
    owner_trace = Trace.from_arrays(
        sample_name=sample.metadata.raw_stem,
        mz=request.mz,
        rt=trace.rt,
        intensity=trace.intensity,
        rt_min=request.rt_min,
        rt_max=request.rt_max,
        ppm_tol=request.ppm_tol,
        source="alignment_owner_identity",
        acquisition_source_id=source.acquisition_source_id,
    )
    observation = mint_peak_observation_ref(
        processing_owners=context.processing_owners,
        sample=sample,
        trace=owner_trace,
        integration=integration,
        resolver_mode="alignment_owner",
        proposal_sources=("alignment_owner",),
    )
    return group_member_subject_from_discovery_physical_selection(
        processing_owners=context.processing_owners,
        sample=sample,
        candidate=candidate,
        observation=observation,
    )


def _member_subject_from_discovery_binding(
    binding: DiscoveryCandidatePhysicalBinding,
    *,
    context: AlignmentCrossSampleIdentityContext,
) -> CrossSampleGroupMemberSubject:
    sample = context.sample_for(binding.candidate.sample_stem)
    return group_member_subject_from_discovery_physical_selection(
        processing_owners=context.processing_owners,
        sample=sample,
        candidate=binding.candidate,
        physical_binding=binding,
    )


def _resolve_peak(
    candidate: Any,
    rt_array: NDArray[np.float64],
    intensity_array: NDArray[np.float64],
    peak_config: ExtractionConfig,
    seed_rt: float,
    peak_resolver: PeakResolver,
    *,
    ppm_tol: float,
    emit_region_audit: bool,
) -> ResolvedPeak | None:
    if peak_resolver is _default_peak_resolver:
        return _default_peak_resolver(
            candidate,
            rt_array,
            intensity_array,
            peak_config,
            seed_rt,
            ppm_tol=ppm_tol,
            emit_region_audit=emit_region_audit,
        )
    return peak_resolver(candidate, rt_array, intensity_array, peak_config, seed_rt)


def _extract_many(
    source: OwnershipXICSource,
    requests: tuple[XICRequest, ...],
) -> tuple[XICTrace, ...]:
    unique_requests = tuple(dict.fromkeys(requests))
    if len(unique_requests) != len(requests):
        unique_traces = _extract_unique_many(source, unique_requests)
        traces_by_request = dict(zip(unique_requests, unique_traces, strict=True))
        return tuple(traces_by_request[request] for request in requests)
    return _extract_unique_many(source, requests)


def _extract_unique_many(
    source: OwnershipXICSource,
    requests: tuple[XICRequest, ...],
) -> tuple[XICTrace, ...]:
    if hasattr(source, "extract_xic_many"):
        return tuple(source.extract_xic_many(requests))  # type: ignore[attr-defined]
    traces: list[XICTrace] = []
    for request in requests:
        rt, intensity = source.extract_xic(
            request.mz,
            request.rt_min,
            request.rt_max,
            request.ppm_tol,
        )
        traces.append(XICTrace.from_arrays(rt, intensity))
    return tuple(traces)


def _request_chunks(
    source: OwnershipXICSource,
    items: tuple[_ResolutionRequestItem, ...],
    chunk_size: int,
) -> tuple[tuple[_ResolutionRequestItem, ...], ...]:
    if chunk_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")
    keyed_items = tuple((_request_group_key(source, item), item) for item in items)
    ordered_items = tuple(sorted(keyed_items, key=_grouped_request_sort_key))
    chunks: list[tuple[_ResolutionRequestItem, ...]] = []
    current: list[_ResolutionRequestItem] = []
    for _group_key, group_iter in groupby(ordered_items, key=lambda pair: pair[0]):
        group = [item for _key, item in group_iter]
        if current and len(current) + len(group) > chunk_size:
            chunks.append(tuple(current))
            current = []
        current.extend(group)
        if len(current) >= chunk_size:
            chunks.append(tuple(current))
            current = []
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def _request_group_key(
    source: OwnershipXICSource,
    item: _ResolutionRequestItem,
) -> _ResolutionRequestGroupKey:
    request = item[3]
    resolver = getattr(source, "scan_window_for_request", None)
    if callable(resolver):
        try:
            start_scan, end_scan = resolver(request)
        except (AttributeError, NotImplementedError):
            pass
        else:
            return ("scan", int(start_scan), int(end_scan))
    return ("rt", request.rt_min, request.rt_max)


def _grouped_request_sort_key(
    keyed_item: tuple[_ResolutionRequestGroupKey, _ResolutionRequestItem],
) -> tuple[str, int | float, int | float, float, int]:
    group_key, item = keyed_item
    original_index, _candidate, _seed_rt, request, _emit_region_audit = item
    return (*group_key, request.mz, original_index)


def _unresolved_outcome(candidate: Any, reason: str) -> _ResolutionOutcome:
    return _ResolutionOutcome(
        resolved=None,
        unresolved=OwnerAssignment(
            str(candidate.candidate_id),
            None,
            "unresolved",
            reason,
        ),
    )


def _quantification_eligible(apex: float, left: float, right: float) -> bool:
    return left < apex < right


def _resolved_peak_quantification_bounds(
    peak: ResolvedPeak,
) -> tuple[float, float, float]:
    integration = peak.selected_integration
    if integration is not None:
        return (
            integration.rt_apex_min,
            integration.rt_left_min,
            integration.rt_right_min,
        )
    return peak.rt, peak.peak_start, peak.peak_end


def _quantification_exclusion_reason(apex: float, left: float, right: float) -> str:
    if _quantification_eligible(apex, left, right):
        return ""
    return "owner_apex_on_integration_boundary"


def _emit_region_audit_for_candidate(
    emit_region_audit: bool,
    candidate: Any,
    region_audit_family_ids: frozenset[str] | None,
) -> bool:
    if not emit_region_audit:
        return False
    if region_audit_family_ids is None:
        return True
    candidate_ids = {
        str(getattr(candidate, "feature_family_id", "")),
        str(getattr(candidate, "candidate_id", "")),
    }
    return bool(region_audit_family_ids.intersection(candidate_ids))


def _default_peak_resolver(
    candidate: Any,
    rt_array: NDArray[np.float64],
    intensity_array: NDArray[np.float64],
    peak_config: ExtractionConfig,
    seed_rt: float,
    *,
    ppm_tol: float = 20.0,
    emit_region_audit: bool = False,
) -> ResolvedPeak | None:
    result = find_peak_and_area(
        rt_array,
        intensity_array,
        peak_config,
        preferred_rt=seed_rt,
        strict_preferred_rt=True,
    )
    if result.status != "OK" or result.peak is None:
        return None
    peak = result.peak
    rt_min = float(rt_array[0]) if rt_array.size else seed_rt
    rt_max = float(rt_array[-1]) if rt_array.size else seed_rt
    trace_group = (
        alignment_trace_group(
            sample_stem=str(candidate.sample_stem),
            family_id=str(candidate.candidate_id),
            mz=float(candidate.precursor_mz),
            rt_values=rt_array,
            intensity_values=intensity_array,
            rt_min=rt_min,
            rt_max=rt_max,
            ppm_tol=ppm_tol,
            expected_rt_min=seed_rt,
            neutral_loss_tag=str(candidate.neutral_loss_tag),
            product_mz=float(candidate.product_mz),
            observed_neutral_loss_da=float(candidate.observed_neutral_loss_da),
            source="alignment_detected_owner",
        )
        if emit_region_audit
        else None
    )
    region_audit = (
        build_peak_region_audit_summary(
            rt_array,
            intensity_array,
            result,
            peak_config,
            trace_group=trace_group,
        )
        if emit_region_audit
        else None
    )
    return ResolvedPeak(
        rt=peak.rt,
        peak_start=peak.peak_start,
        peak_end=peak.peak_end,
        area=peak.area,
        intensity=peak.intensity,
        region_audit=region_audit,
        selected_integration=integration_from_peak_trace(
            peak,
            rt_array,
            intensity_array,
            boundary_sources=("alignment_owner",),
            baseline_integration_method=getattr(
                peak_config,
                "baseline_integration_method",
                "asls",
            ),
        ),
    )


def _owners_for_sample(
    sample_stem: str,
    resolved: list[_ResolvedCandidate],
    *,
    alignment_config: AlignmentConfig,
    cross_sample_identity_context: AlignmentCrossSampleIdentityContext | None = None,
) -> tuple[
    list[SampleLocalMS1Owner],
    list[OwnerAssignment],
    list[AmbiguousOwnerRecord],
]:
    pending = sorted(resolved, key=_resolved_sort_key)
    groups = _candidate_components(pending, alignment_config)
    owners: list[SampleLocalMS1Owner] = []
    assignments: list[OwnerAssignment] = []
    ambiguous: list[AmbiguousOwnerRecord] = []
    for index, group in enumerate(groups, start=1):
        if _component_is_ambiguous(group, alignment_config):
            ambiguity_id = f"AMB-{sample_stem}-{len(ambiguous) + 1:06d}"
            candidate_ids = tuple(item.event.candidate_id for item in group)
            ambiguous.append(
                AmbiguousOwnerRecord(
                    ambiguity_id=ambiguity_id,
                    sample_stem=sample_stem,
                    candidate_ids=candidate_ids,
                    reason="owner_multiplet_ambiguity",
                    neutral_loss_tag=_shared_neutral_loss_tag(group),
                    precursor_mz=sum(item.event.precursor_mz for item in group)
                    / len(group),
                    apex_rt=sum(item.apex_rt for item in group) / len(group),
                    product_mz=sum(item.event.product_mz for item in group)
                    / len(group),
                    observed_neutral_loss_da=sum(
                        item.event.observed_neutral_loss_da for item in group
                    )
                    / len(group),
                ),
            )
            assignments.extend(
                OwnerAssignment(
                    candidate_id,
                    None,
                    "ambiguous",
                    "owner_multiplet_ambiguity",
                )
                for candidate_id in candidate_ids
            )
            continue
        primary, supporting = _primary_and_supporting(group)
        owner_id = f"OWN-{sample_stem}-{index:06d}"
        owners.append(
            SampleLocalMS1Owner(
                owner_id=owner_id,
                sample_stem=sample_stem,
                raw_file=primary.event.raw_file,
                precursor_mz=primary.event.precursor_mz,
                owner_apex_rt=primary.apex_rt,
                owner_peak_start_rt=primary.peak_start_rt,
                owner_peak_end_rt=primary.peak_end_rt,
                owner_area=primary.area,
                owner_height=primary.height,
                primary_identity_event=primary.event,
                supporting_events=tuple(item.event for item in supporting),
                identity_conflict=_identity_conflict(group),
                assignment_reason="owner_exact_apex_match",
                region_audit=primary.region_audit,
                selected_integration=primary.selected_integration,
                quantification_eligible=primary.quantification_eligible,
                quantification_exclusion_reason=(
                    primary.quantification_exclusion_reason
                ),
                cross_sample_member_subject=primary.cross_sample_member_subject,
            ),
        )
        assignments.append(
            OwnerAssignment(
                primary.event.candidate_id,
                owner_id,
                "primary",
                "primary_identity_event",
            ),
        )
        assignments.extend(
            OwnerAssignment(
                item.event.candidate_id,
                owner_id,
                "supporting",
                _assignment_reason(primary, item, alignment_config),
            )
            for item in supporting
        )
    return owners, assignments, ambiguous


def _candidate_components(
    items: list[_ResolvedCandidate],
    config: AlignmentConfig,
) -> list[list[_ResolvedCandidate]]:
    remaining = set(range(len(items)))
    components: list[list[_ResolvedCandidate]] = []
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        component = {seed}
        while stack:
            current = stack.pop()
            for candidate_index in tuple(remaining):
                relation = _owner_relation(
                    items[current],
                    items[candidate_index],
                    config,
                )
                if relation is not None:
                    remaining.remove(candidate_index)
                    stack.append(candidate_index)
                    component.add(candidate_index)
        components.append([items[index] for index in sorted(component)])
    return components


def _same_owner(
    left: _ResolvedCandidate,
    right: _ResolvedCandidate,
    config: AlignmentConfig,
) -> bool:
    return _owner_relation(left, right, config) in {
        "owner_exact_apex_match",
        "owner_tail_assignment",
    }


def _owner_relation(
    left: _ResolvedCandidate,
    right: _ResolvedCandidate,
    config: AlignmentConfig,
) -> str | None:
    if _ppm(left.event.precursor_mz, right.event.precursor_mz) > config.max_ppm:
        return None
    apex_delta_sec = abs(left.apex_rt - right.apex_rt) * 60.0
    overlap = _window_overlap_fraction(left, right)
    if _has_window_overlap(left, right) and _looks_like_unresolved_doublet(
        left,
        right,
        config,
    ):
        return "owner_multiplet_ambiguity"
    if (
        apex_delta_sec <= config.owner_apex_close_sec
        and overlap >= config.owner_window_overlap_fraction
    ):
        return "owner_exact_apex_match"
    if _seed_on_peak_tail(left, right, config) or _seed_on_peak_tail(
        right,
        left,
        config,
    ):
        return "owner_tail_assignment"
    return None


def _looks_like_unresolved_doublet(
    left: _ResolvedCandidate,
    right: _ResolvedCandidate,
    config: AlignmentConfig,
) -> bool:
    """Two peaks, or one peak seeded by more than one MS2 scan?

    One MS1 peak spans many scans and its neutral loss can be seeded from
    several of them.  Each seed opens its own resolution window, so the same
    peak comes back with apexes a few seconds apart while the seeds themselves
    sit tens of seconds apart.  An apex separation measured only against
    ``owner_apex_close_sec`` cannot tell that from a real doublet, because the
    bound carries no sense of how wide the peaks are.  Require the separation to
    also be chromatographically meaningful against the narrower peak: below
    roughly half a peak width the trace does not show two apexes at all, so
    calling it an unresolved doublet is not supportable.
    """
    apex_delta_sec = abs(left.apex_rt - right.apex_rt) * 60.0
    if apex_delta_sec <= config.owner_apex_close_sec:
        return False
    narrower_width_sec = min(
        (left.peak_end_rt - left.peak_start_rt) * 60.0,
        (right.peak_end_rt - right.peak_start_rt) * 60.0,
    )
    if (
        narrower_width_sec > 0.0
        and apex_delta_sec
        < narrower_width_sec * _DOUBLET_APEX_SEPARATION_WIDTH_FRACTION
    ):
        return False
    lower_height = min(left.height, right.height)
    higher_height = max(left.height, right.height)
    return (
        lower_height / max(higher_height, 1e-12) > config.owner_tail_max_secondary_ratio
    )


def _seed_on_peak_tail(
    owner: _ResolvedCandidate,
    event: _ResolvedCandidate,
    config: AlignmentConfig,
) -> bool:
    seed_delta_sec = abs(event.event.seed_rt - owner.apex_rt) * 60.0
    return (
        owner.peak_start_rt <= event.event.seed_rt <= owner.peak_end_rt
        and seed_delta_sec <= config.owner_tail_seed_guard_sec
    )


def _component_is_ambiguous(
    group: list[_ResolvedCandidate],
    config: AlignmentConfig,
) -> bool:
    for left_index, left in enumerate(group):
        for right in group[left_index + 1 :]:
            if _owner_relation(left, right, config) == "owner_multiplet_ambiguity":
                return True
    return False


def _assignment_reason(
    primary: _ResolvedCandidate,
    supporting: _ResolvedCandidate,
    config: AlignmentConfig,
) -> str:
    seed_delta_sec = abs(supporting.event.seed_rt - primary.apex_rt) * 60.0
    if (
        seed_delta_sec > config.owner_apex_close_sec
        and seed_delta_sec <= config.owner_tail_seed_guard_sec
    ):
        return "owner_tail_assignment"
    relation = _owner_relation(primary, supporting, config)
    if relation == "owner_tail_assignment":
        return relation
    return "owner_exact_apex_match"


def _primary_and_supporting(
    group: list[_ResolvedCandidate],
) -> tuple[_ResolvedCandidate, list[_ResolvedCandidate]]:
    return group[0], group[1:]


def _resolved_sort_key(item: _ResolvedCandidate) -> tuple[object, ...]:
    return (
        -item.event.evidence_score,
        -item.event.seed_event_count,
        -item.area,
        abs(item.event.seed_rt - item.apex_rt),
        item.event.candidate_id,
    )


def _identity_conflict(group: list[_ResolvedCandidate]) -> bool:
    tags = {item.event.neutral_loss_tag for item in group}
    return len(tags) > 1


def _shared_neutral_loss_tag(group: list[_ResolvedCandidate]) -> str | None:
    tags = {item.event.neutral_loss_tag for item in group}
    if len(tags) == 1:
        return next(iter(tags))
    return None


def _identity_event(candidate: Any, *, seed_rt: float) -> IdentityEvent:
    return IdentityEvent(
        candidate_id=str(candidate.candidate_id),
        sample_stem=str(candidate.sample_stem),
        raw_file=str(candidate.raw_file),
        neutral_loss_tag=str(candidate.neutral_loss_tag),
        precursor_mz=float(candidate.precursor_mz),
        product_mz=float(candidate.product_mz),
        observed_neutral_loss_da=float(candidate.observed_neutral_loss_da),
        seed_rt=seed_rt,
        evidence_score=int(getattr(candidate, "evidence_score", 0)),
        seed_event_count=int(getattr(candidate, "seed_event_count", 0)),
    )


def _candidate_seed_rt(candidate: Any) -> float:
    for field in ("best_seed_rt", "ms1_apex_rt"):
        value = getattr(candidate, field, None)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return float(value)
    raise ValueError("ownership candidate requires finite best_seed_rt or ms1_apex_rt")


def _candidate_resolution_rt_window(
    candidate: Any,
    seed_rt: float,
    config: AlignmentConfig,
) -> tuple[float, float]:
    ms1_apex = _finite_attr(candidate, "ms1_apex_rt")
    peak_start = _finite_attr(candidate, "ms1_peak_rt_start")
    peak_end = _finite_attr(candidate, "ms1_peak_rt_end")
    if (
        peak_start is not None
        and peak_end is not None
        and peak_start < peak_end
        and (
            _contains_rt(peak_start, peak_end, seed_rt)
            or (ms1_apex is not None and _contains_rt(peak_start, peak_end, ms1_apex))
        )
    ):
        padding_min = min(
            _OWNER_QUANTITATION_CONTEXT_PADDING_MIN,
            config.max_rt_sec / 60.0,
        )
        return (
            max(0.0, peak_start - padding_min),
            peak_end + padding_min,
        )

    search_start = _finite_attr(candidate, "ms1_search_rt_min")
    search_end = _finite_attr(candidate, "ms1_search_rt_max")
    if (
        search_start is not None
        and search_end is not None
        and search_start < search_end
        and _contains_rt(search_start, search_end, seed_rt)
    ):
        return search_start, search_end

    margin_min = config.max_rt_sec / 60.0
    return seed_rt - margin_min, seed_rt + margin_min


def _finite_attr(candidate: Any, field: str) -> float | None:
    value = getattr(candidate, field, None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _contains_rt(left: float, right: float, rt: float) -> bool:
    return left <= rt <= right


def _window_overlap_fraction(
    left: _ResolvedCandidate,
    right: _ResolvedCandidate,
) -> float:
    intersection = min(left.peak_end_rt, right.peak_end_rt) - max(
        left.peak_start_rt,
        right.peak_start_rt,
    )
    if intersection <= 0:
        return 0.0
    left_width = left.peak_end_rt - left.peak_start_rt
    right_width = right.peak_end_rt - right.peak_start_rt
    denominator = min(left_width, right_width)
    if denominator <= 0:
        return 0.0
    return intersection / denominator


def _has_window_overlap(left: _ResolvedCandidate, right: _ResolvedCandidate) -> bool:
    return min(left.peak_end_rt, right.peak_end_rt) > max(
        left.peak_start_rt,
        right.peak_start_rt,
    )


def _validated_trace_arrays(
    rt: object,
    intensity: object,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    rt_array = np.asarray(rt, dtype=float)
    intensity_array = np.asarray(intensity, dtype=float)
    if (
        rt_array.ndim != 1
        or intensity_array.ndim != 1
        or rt_array.shape != intensity_array.shape
        or not np.all(np.isfinite(rt_array))
        or not np.all(np.isfinite(intensity_array))
    ):
        raise ValueError("ownership trace arrays must be finite one-dimensional pairs")
    return rt_array, intensity_array


def _ppm(left: float, right: float) -> float:
    denominator = max(abs(left), 1e-12)
    return abs(left - right) / denominator * 1_000_000.0
