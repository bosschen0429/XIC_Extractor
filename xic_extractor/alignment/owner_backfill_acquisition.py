"""Owner Backfill acquisition and request-extraction helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from itertools import groupby
from typing import TypeAlias

from xic_extractor.alignment.ms1_index_source import OwnerBackfillXicBackend
from xic_extractor.alignment.owner_backfill_request_plan import (
    OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
    OwnerBackfillLogicalRequestMember,
    OwnerBackfillRequestEquivalenceClass,
    OwnerBackfillRequestEquivalenceContext,
    OwnerBackfillRequestIdentity,
    OwnerBackfillRequestItem,
    OwnerBackfillValidationMode,
    OwnerBackfillWindowStrategy,
    mint_owner_backfill_request_identity,
)
from xic_extractor.alignment.owner_backfill_result import (
    OwnerBackfillExtractionResult,
    OwnerBackfillLogicalExtractionResult,
    OwnerBackfillSource,
    _ExtractionRequestItem,
    _WindowedRequestItem,
    validate_owner_backfill_peak_fact_observation,  # noqa: F401
)
from xic_extractor.alignment.scan_retention_times import (
    ScanRetentionTimeCache,
    cached_retention_time_for_scan,
)
from xic_extractor.peak_detection.evidence_facts import (
    PeakTraceEvidenceFacts,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PreparedSignalResult,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
)
from xic_extractor.xic_models import XICRequest, XICTrace, crop_xic_trace_by_rt

_RequestItem = OwnerBackfillRequestItem
# One extraction chunk's prepared signals, keyed by the identity of the trace
# object request equivalence fanned out.  The cache holds the trace itself so a
# recycled id() can never alias a different trace.
_ChunkPreparedSignalCache: TypeAlias = dict[int, tuple[XICTrace, PreparedSignalResult]]
_RequestGroupKey = tuple[str, int | float, int | float]



def _request_equivalence_context(
    *,
    expected_acquisition_sources: Mapping[str, AcquisitionSourceIdentity] | None,
    backend: OwnerBackfillXicBackend,
    window_strategy: OwnerBackfillWindowStrategy,
    superwindow_span_factor: int,
    validation_mode: OwnerBackfillValidationMode,
) -> OwnerBackfillRequestEquivalenceContext | None:
    if expected_acquisition_sources is None:
        return None
    return OwnerBackfillRequestEquivalenceContext(
        acquisition_sources=expected_acquisition_sources,
        backend=backend,
        window_strategy=window_strategy,
        superwindow_span_factor=superwindow_span_factor,
        validation_mode=validation_mode,
        result_semantics=OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
    )


def _available_acquisition_sources(
    sources: Mapping[str, OwnerBackfillSource],
) -> dict[str, AcquisitionSourceIdentity] | None:
    bindings = {
        sample_stem: getattr(source, "acquisition_source", None)
        for sample_stem, source in sources.items()
    }
    if not bindings or any(
        not isinstance(source, AcquisitionSourceIdentity)
        for source in bindings.values()
    ):
        return None
    return {
        sample_stem: source
        for sample_stem, source in bindings.items()
        if isinstance(source, AcquisitionSourceIdentity)
    }


def _validate_owner_bound_sources(
    sources: Mapping[str, OwnerBackfillSource],
    *,
    expected_acquisition_sources: Mapping[str, AcquisitionSourceIdentity] | None,
    source_label: str,
) -> None:
    if expected_acquisition_sources is None:
        return
    for sample_stem, source in sources.items():
        expected = expected_acquisition_sources.get(sample_stem)
        if not isinstance(expected, AcquisitionSourceIdentity):
            raise ValueError(
                "exact request equivalence requires an owner-minted acquisition "
                f"identity for {sample_stem}"
            )
        actual = getattr(source, "acquisition_source", None)
        if not isinstance(actual, AcquisitionSourceIdentity):
            raise ValueError(
                f"{source_label} source for {sample_stem} is not owner-bound"
            )
        if actual != expected:
            raise ValueError(
                f"{source_label} source for {sample_stem} has a different "
                "acquisition identity"
            )


def _iter_extracted_request_traces(
    source: OwnerBackfillSource,
    items: tuple[_RequestItem, ...],
    chunk_size: int,
    *,
    window_strategy: OwnerBackfillWindowStrategy,
    superwindow_span_factor: int,
    equivalence_classes: tuple[OwnerBackfillRequestEquivalenceClass, ...] | None = None,
):
    extraction_items = _extraction_request_items(items, equivalence_classes)
    if window_strategy == "super-window":
        retention_time_by_scan: dict[int, float | None] = {}
        groups = _superwindow_groups(
            source,
            extraction_items,
            superwindow_span_factor=superwindow_span_factor,
            retention_time_by_scan=retention_time_by_scan,
        )
        if groups is not None:
            _validate_equivalence_grouping(
                tuple(tuple(item for item, _scan_window in group) for group in groups),
            )
            for group in groups:
                group_items = tuple(item for item, _scan_window in group)
                try:
                    yield (
                        tuple(item.item for item in group_items),
                        _extract_superwindow_group(
                            source,
                            group,
                            retention_time_by_scan=retention_time_by_scan,
                        ),
                    )
                except OSError:
                    exact_items = tuple(
                        _with_exact_window_strategy(item) for item in group_items
                    )
                    yield from _iter_exact_request_traces(
                        source,
                        exact_items,
                        chunk_size,
                    )
            return
    yield from _iter_exact_request_traces(
        source,
        extraction_items,
        chunk_size,
    )

def _iter_exact_request_traces(
    source: OwnerBackfillSource,
    items: tuple[_ExtractionRequestItem, ...],
    chunk_size: int,
):
    chunks = _scan_window_aware_extraction_chunks(source, items, chunk_size)
    _validate_equivalence_grouping(chunks)
    for chunk in chunks:
        try:
            traces = _extract_many(
                source,
                tuple(item.item[2] for item in chunk),
                items=chunk,
            )
        except OSError:
            continue
        yield tuple(item.item for item in chunk), traces


def _scan_window_aware_chunks(
    source: OwnerBackfillSource,
    items: tuple[_RequestItem, ...],
    chunk_size: int,
) -> tuple[tuple[_RequestItem, ...], ...]:
    """Preserve the diagnostics-facing raw request-item chunking contract."""
    extraction_chunks = _scan_window_aware_extraction_chunks(
        source,
        tuple(_ExtractionRequestItem(item) for item in items),
        chunk_size,
    )
    return tuple(tuple(item.item for item in chunk) for chunk in extraction_chunks)


def _scan_window_aware_extraction_chunks(
    source: OwnerBackfillSource,
    items: tuple[_ExtractionRequestItem, ...],
    chunk_size: int,
) -> tuple[tuple[_ExtractionRequestItem, ...], ...]:
    if chunk_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")
    keyed_items = tuple((_request_group_key(source, item), item) for item in items)
    ordered_items = tuple(sorted(keyed_items, key=_grouped_request_sort_key))
    chunks: list[tuple[_ExtractionRequestItem, ...]] = []
    current: list[_ExtractionRequestItem] = []
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


def _grouped_request_sort_key(
    keyed_item: tuple[_RequestGroupKey, _ExtractionRequestItem],
) -> tuple[str, int | float, int | float, float, str, str]:
    group_key, item = keyed_item
    feature, sample_stem, request, _preferred_rt = item.item
    return (
        *group_key,
        request.mz,
        feature.feature_family_id,
        sample_stem,
    )


def _request_group_key(
    source: OwnerBackfillSource,
    item: _ExtractionRequestItem,
) -> _RequestGroupKey:
    request = item.item[2]
    scan_window = _source_scan_window_for_request(source, request)
    if scan_window is not None:
        return ("scan", scan_window[0], scan_window[1])
    return ("rt", request.rt_min, request.rt_max)


def _superwindow_groups(
    source: OwnerBackfillSource,
    items: tuple[_ExtractionRequestItem, ...],
    *,
    superwindow_span_factor: int,
    retention_time_by_scan: ScanRetentionTimeCache,
) -> tuple[tuple[_WindowedRequestItem, ...], ...] | None:
    windowed_items: list[_WindowedRequestItem] = []
    for item in items:
        scan_window = _source_scan_window_for_request(source, item.item[2])
        if scan_window is None:
            return None
        if (
            _source_retention_time_for_scan(
                source,
                scan_window[0],
                retention_time_by_scan=retention_time_by_scan,
            )
            is None
        ):
            return None
        if (
            _source_retention_time_for_scan(
                source,
                scan_window[1],
                retention_time_by_scan=retention_time_by_scan,
            )
            is None
        ):
            return None
        windowed_items.append((item, scan_window))
    if not windowed_items:
        return ()

    ordered = tuple(sorted(windowed_items, key=_windowed_request_sort_key))
    groups: list[tuple[_WindowedRequestItem, ...]] = []
    current: list[_WindowedRequestItem] = []
    current_start = 0
    current_end = 0
    current_max_span = 1
    for windowed_item in ordered:
        scan_start, scan_end = windowed_item[1]
        item_span = _scan_span(scan_start, scan_end)
        if not current:
            current = [windowed_item]
            current_start = scan_start
            current_end = scan_end
            current_max_span = item_span
            continue

        proposed_start = min(current_start, scan_start)
        proposed_end = max(current_end, scan_end)
        proposed_max_span = max(current_max_span, item_span)
        overlaps_current = scan_start <= current_end
        within_span_limit = (
            _scan_span(proposed_start, proposed_end)
            <= proposed_max_span * superwindow_span_factor
        )
        if overlaps_current and within_span_limit:
            current.append(windowed_item)
            current_start = proposed_start
            current_end = proposed_end
            current_max_span = proposed_max_span
            continue

        groups.append(tuple(current))
        current = [windowed_item]
        current_start = scan_start
        current_end = scan_end
        current_max_span = item_span
    if current:
        groups.append(tuple(current))
    return tuple(groups)


def _windowed_request_sort_key(
    windowed_item: _WindowedRequestItem,
) -> tuple[int, int, float, str, str]:
    item, scan_window = windowed_item
    feature, sample_stem, request, _preferred_rt = item.item
    return (
        scan_window[0],
        scan_window[1],
        request.mz,
        feature.feature_family_id,
        sample_stem,
    )


def _extract_superwindow_group(
    source: OwnerBackfillSource,
    group: tuple[_WindowedRequestItem, ...],
    *,
    retention_time_by_scan: ScanRetentionTimeCache,
) -> tuple[XICTrace | OwnerBackfillLogicalExtractionResult, ...]:
    union_start = min(scan_window[0] for _item, scan_window in group)
    union_end = max(scan_window[1] for _item, scan_window in group)
    union_rt_min = _source_retention_time_for_scan(
        source,
        union_start,
        retention_time_by_scan=retention_time_by_scan,
    )
    union_rt_max = _source_retention_time_for_scan(
        source,
        union_end,
        retention_time_by_scan=retention_time_by_scan,
    )
    if union_rt_min is None or union_rt_max is None:
        raise AttributeError("scan RT lookup is unavailable")
    if union_rt_min > union_rt_max:
        union_rt_min, union_rt_max = union_rt_max, union_rt_min
    union_requests = tuple(
        XICRequest(
            mz=item.item[2].mz,
            rt_min=union_rt_min,
            rt_max=union_rt_max,
            ppm_tol=item.item[2].ppm_tol,
        )
        for item, _scan_window in group
    )
    group_items = tuple(item for item, _scan_window in group)
    if group_items and group_items[0].logical_request is not None:
        (
            unique_identities,
            unique_union_requests,
            _representative_by_identity,
        ) = _typed_extraction_inputs(
            group_items,
            union_requests,
        )
        unique_union_traces = _extract_unique_many(source, unique_union_requests)
        scan_window_by_identity = {
            item.request_identity: scan_window
            for item, scan_window in group
            if item.request_identity is not None
        }
        cropped_traces = tuple(
            _crop_trace_to_scan_window(
                source,
                trace,
                scan_window_by_identity[identity],
                retention_time_by_scan=retention_time_by_scan,
            )
            for identity, trace in zip(
                unique_identities,
                unique_union_traces,
                strict=True,
            )
        )
        return _fan_out_typed_results(
            group_items,
            unique_identities,
            cropped_traces,
        )
    union_traces = _require_untyped_traces(_extract_many(source, union_requests))
    return tuple(
        _crop_trace_to_scan_window(
            source,
            trace,
            scan_window,
            retention_time_by_scan=retention_time_by_scan,
        )
        for trace, (_item, scan_window) in zip(union_traces, group, strict=True)
    )


def _crop_trace_to_scan_window(
    source: OwnerBackfillSource,
    trace: XICTrace,
    scan_window: tuple[int, int],
    *,
    retention_time_by_scan: ScanRetentionTimeCache | None = None,
) -> XICTrace:
    rt_min = _source_retention_time_for_scan(
        source,
        scan_window[0],
        retention_time_by_scan=retention_time_by_scan,
    )
    rt_max = _source_retention_time_for_scan(
        source,
        scan_window[1],
        retention_time_by_scan=retention_time_by_scan,
    )
    if rt_min is None or rt_max is None:
        return trace
    return crop_xic_trace_by_rt(trace, rt_min, rt_max, assume_sorted_rt=True)


def _scan_span(start_scan: int, end_scan: int) -> int:
    return max(1, abs(end_scan - start_scan) + 1)


def _source_scan_window_for_request(
    source: OwnerBackfillSource,
    request: XICRequest,
) -> tuple[int, int] | None:
    resolver = getattr(source, "scan_window_for_request", None)
    if not callable(resolver):
        return None
    try:
        start_scan, end_scan = resolver(request)
    except (AttributeError, NotImplementedError):
        return None
    return int(start_scan), int(end_scan)


def _source_retention_time_for_scan(
    source: OwnerBackfillSource,
    scan_number: int,
    *,
    retention_time_by_scan: ScanRetentionTimeCache | None = None,
) -> float | None:
    return cached_retention_time_for_scan(
        source,
        scan_number,
        retention_time_by_scan=retention_time_by_scan,
    )

def _extract_many(
    source: OwnerBackfillSource,
    requests: tuple[XICRequest, ...],
    *,
    items: tuple[_ExtractionRequestItem, ...] | None = None,
) -> tuple[XICTrace | OwnerBackfillLogicalExtractionResult, ...]:
    if items is not None and items and items[0].logical_request is not None:
        if items is None or len(items) != len(requests):
            raise ValueError(
                "typed request extraction requires one logical item per request"
            )
        (
            unique_identities,
            unique_requests,
            _representative_by_identity,
        ) = _typed_extraction_inputs(
            items,
            requests,
        )
        unique_traces = _extract_unique_many(source, unique_requests)
        return _fan_out_typed_results(
            items,
            unique_identities,
            unique_traces,
        )
    unique_requests = tuple(dict.fromkeys(requests))
    if len(unique_requests) != len(requests):
        unique_traces = _extract_unique_many(source, unique_requests)
        traces_by_request = dict(zip(unique_requests, unique_traces, strict=True))
        return tuple(traces_by_request[request] for request in requests)
    return _extract_unique_many(source, requests)


def _typed_extraction_inputs(
    items: tuple[_ExtractionRequestItem, ...],
    requests: tuple[XICRequest, ...],
) -> tuple[
    tuple[OwnerBackfillRequestIdentity, ...],
    tuple[XICRequest, ...],
    Mapping[OwnerBackfillRequestIdentity, _ExtractionRequestItem],
]:
    if len(items) != len(requests):
        raise ValueError("typed extraction inputs must have equal cardinality")
    unique_identities: list[OwnerBackfillRequestIdentity] = []
    request_by_identity: dict[OwnerBackfillRequestIdentity, XICRequest] = {}
    representative_by_identity: dict[
        OwnerBackfillRequestIdentity,
        _ExtractionRequestItem,
    ] = {}
    for item, request in zip(items, requests, strict=True):
        identity = item.request_identity
        if identity is None:
            raise ValueError("logical request is missing owner equivalence identity")
        previous = request_by_identity.get(identity)
        if previous is not None and previous != request:
            raise ValueError(
                "one equivalence class produced incompatible extraction requests"
            )
        if previous is None:
            unique_identities.append(identity)
            request_by_identity[identity] = request
            representative_by_identity[identity] = item
    return (
        tuple(unique_identities),
        tuple(request_by_identity[identity] for identity in unique_identities),
        representative_by_identity,
    )


def _fan_out_typed_results(
    items: tuple[_ExtractionRequestItem, ...],
    unique_identities: tuple[OwnerBackfillRequestIdentity, ...],
    unique_traces: tuple[XICTrace, ...],
) -> tuple[OwnerBackfillLogicalExtractionResult, ...]:
    typed_results = tuple(
        OwnerBackfillExtractionResult(identity, trace)
        for identity, trace in zip(
            unique_identities,
            unique_traces,
            strict=True,
        )
    )
    result_by_identity = {result.request_identity: result for result in typed_results}
    if len(result_by_identity) != len(unique_identities):
        raise ValueError("typed extraction produced duplicate canonical results")
    logical_results: list[OwnerBackfillLogicalExtractionResult] = []
    for item in items:
        if item.logical_request is None or item.request_identity is None:
            raise ValueError("typed fan-out requires owner logical identity")
        logical_results.append(
            OwnerBackfillLogicalExtractionResult(
                logical_request=item.logical_request,
                canonical_result=result_by_identity[item.request_identity],
            )
        )
    return tuple(logical_results)


def _extraction_request_items(
    items: tuple[_RequestItem, ...],
    equivalence_classes: tuple[OwnerBackfillRequestEquivalenceClass, ...] | None,
) -> tuple[_ExtractionRequestItem, ...]:
    if equivalence_classes is None:
        return tuple(_ExtractionRequestItem(item) for item in items)
    binding_by_logical_index: dict[
        int,
        tuple[OwnerBackfillLogicalRequestMember, OwnerBackfillRequestIdentity],
    ] = {}
    for equivalence_class in equivalence_classes:
        for member in equivalence_class.members:
            if member.logical_index in binding_by_logical_index:
                raise ValueError(
                    "logical request belongs to multiple equivalence classes"
                )
            binding_by_logical_index[member.logical_index] = (
                member,
                equivalence_class.identity,
            )
    if set(binding_by_logical_index) != set(range(len(items))):
        raise ValueError("equivalence partition does not cover the logical population")
    extraction_items: list[_ExtractionRequestItem] = []
    for logical_index, item in enumerate(items):
        member, identity = binding_by_logical_index[logical_index]
        if member.item != item:
            raise ValueError("logical identity is rebound to another request item")
        extraction_items.append(_ExtractionRequestItem(item, member, identity))
    return tuple(extraction_items)


def _validate_equivalence_grouping(
    groups: Sequence[tuple[_ExtractionRequestItem, ...]],
) -> None:
    if not groups or not groups[0] or groups[0][0].request_identity is None:
        return
    group_by_identity: dict[OwnerBackfillRequestIdentity, int] = {}
    for group_index, group in enumerate(groups):
        for item in group:
            identity = item.request_identity
            if identity is None:
                raise ValueError(
                    "logical request is missing owner equivalence identity"
                )
            previous_group = group_by_identity.setdefault(identity, group_index)
            if previous_group != group_index:
                raise ValueError(
                    "one equivalence class was split across extraction groups"
                )


def _with_exact_window_strategy(
    item: _ExtractionRequestItem,
) -> _ExtractionRequestItem:
    identity = item.request_identity
    if identity is None:
        return item
    exact_identity = mint_owner_backfill_request_identity(
        sample_stem=identity.sample_stem,
        acquisition_source=identity.acquisition_source,
        request=identity.request,
        backend=identity.backend,
        window_strategy="exact",
        superwindow_span_factor=identity.superwindow_span_factor,
        validation_mode=identity.validation_mode,
        result_semantics=identity.result_semantics,
    )
    return _ExtractionRequestItem(item.item, item.logical_request, exact_identity)


def _trace_for_logical_request_result(
    item: _RequestItem,
    result: XICTrace | OwnerBackfillLogicalExtractionResult,
) -> XICTrace:
    if isinstance(result, XICTrace):
        return result
    if result.logical_request.item != item:
        raise ValueError("typed extraction result is rebound to another hypothesis")
    return result.trace


def _logical_binding_for_result(
    result: XICTrace | OwnerBackfillLogicalExtractionResult,
) -> tuple[
    OwnerBackfillLogicalRequestMember | None,
    OwnerBackfillRequestIdentity | None,
]:
    if isinstance(result, XICTrace):
        return None, None
    return result.logical_request, result.canonical_result.request_identity


def _positive_elapsed(value: float) -> float | None:
    return value if math.isfinite(value) and value > 0 else None


def _peak_facts_match_baseline(
    baseline: object,
    facts: PeakTraceEvidenceFacts | None,
) -> bool:
    if baseline is None or facts is None:
        return False
    candidate = facts.integration
    compatibility_fields = (
        "rt_left_min",
        "rt_apex_min",
        "rt_right_min",
        "raw_apex_rt_min",
        "rt_width_min",
        "height_raw",
        "height_smoothed",
        "area_raw_counts_seconds",
        "integration_method",
        "boundary_sources",
    )
    return (
        all(
            getattr(candidate, field) == getattr(baseline, field)
            for field in compatibility_fields
        )
        and facts.normalized_shape_trace.is_usable
        and facts.window.status == "ok"
        and facts.window.query_apex_rt_min == candidate.rt_apex_min
        and facts.window.query_apex_in_trace_window
        and candidate.area_ms1_morphology is not None
    )


def _require_untyped_traces(
    results: tuple[XICTrace | OwnerBackfillLogicalExtractionResult, ...],
) -> tuple[XICTrace, ...]:
    if any(not isinstance(result, XICTrace) for result in results):
        raise ValueError("untyped extraction unexpectedly produced logical results")
    return tuple(result for result in results if isinstance(result, XICTrace))


def _extract_unique_many(
    source: OwnerBackfillSource,
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

