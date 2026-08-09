from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from xic_extractor.alignment.backfill_scope import (
    any_detected_owner_can_be_superseded,
    backfill_seed_centers,
)
from xic_extractor.alignment.cell_region_audit import with_region_audit
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.matrix_handoff import integration_from_peak_trace
from xic_extractor.alignment.ms1_index_source import OwnerBackfillXicBackend
from xic_extractor.alignment.owner_area import median_owner_area, positive_finite
from xic_extractor.alignment.owner_backfill_request_plan import (
    OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
    OWNER_BACKFILL_VALIDATION_MODES,
    OWNER_BACKFILL_WINDOW_STRATEGIES,
    OWNER_BACKFILL_XIC_BACKENDS,
    OwnerBackfillLogicalRequestMember,
    OwnerBackfillRequestEquivalenceClass,
    OwnerBackfillRequestEquivalenceContext,
    OwnerBackfillRequestIdentity,
    OwnerBackfillRequestItem,
    OwnerBackfillValidationMode,
    OwnerBackfillWindowStrategy,
    build_owner_backfill_request_equivalence_classes,
    build_owner_backfill_request_plan,
    mint_owner_backfill_request_identity,
)
from xic_extractor.alignment.owner_group_delivery import (
    OwnerGroupDeliveryFeature,
    OwnerGroupDeliveryFeatures,
    delivery_cell_projection,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.scan_retention_times import (
    ScanRetentionTimeCache,
    cached_retention_time_for_scan,
)
from xic_extractor.alignment.trace_context import alignment_trace_group
from xic_extractor.config import ExtractionConfig
from xic_extractor.peak_detection.ms1_trace_detection import detect_ms1_trace_peak
from xic_extractor.peak_detection.region_audit import build_peak_region_audit_summary
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.signal_processing import find_peak_and_area
from xic_extractor.xic_models import XICRequest, XICTrace, crop_xic_trace_by_rt

_RequestItem = OwnerBackfillRequestItem
_RequestGroupKey = tuple[str, int | float, int | float]


@dataclass(frozen=True, slots=True)
class _ExtractionRequestItem:
    item: _RequestItem
    logical_request: OwnerBackfillLogicalRequestMember | None = None
    request_identity: OwnerBackfillRequestIdentity | None = None

    def __post_init__(self) -> None:
        if (self.logical_request is None) != (self.request_identity is None):
            raise ValueError(
                "logical request and canonical identity must be supplied together"
            )
        if self.logical_request is not None and self.logical_request.item != self.item:
            raise ValueError("logical request is not bound to its extraction item")


_WindowedRequestItem = tuple[_ExtractionRequestItem, tuple[int, int]]


class OwnerBackfillSource(Protocol):
    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]: ...


@dataclass(frozen=True)
class OwnerBackfillCandidateAuditRow:
    feature_family_id: str
    group_hypothesis_id: str
    public_family_id: str
    sample_stem: str
    candidate_index: int
    candidate_phase: str
    selected_for_output: bool
    candidate_status: str
    candidate_outcome: str
    trace_quality: str
    area: float | None
    apex_rt: float | None
    peak_start_rt: float | None
    peak_end_rt: float | None
    rt_delta_sec: float | None
    backfill_seed_mz: float | None
    backfill_seed_rt: float | None
    backfill_request_rt_min: float | None
    backfill_request_rt_max: float | None
    backfill_request_ppm: float | None
    reason: str
    selection_note: str


@dataclass(frozen=True)
class OwnerBackfillResult:
    cells: tuple[AlignedCell, ...]
    candidate_audit_rows: tuple[OwnerBackfillCandidateAuditRow, ...]
    request_reuse_observations: tuple[
        OwnerBackfillRequestReuseObservation,
        ...,
    ] = ()


@dataclass(frozen=True, slots=True)
class OwnerBackfillRequestReuseObservation:
    sample_stem: str
    validation_mode: OwnerBackfillValidationMode
    logical_request_count: int
    canonical_request_class_count: int

    def __post_init__(self) -> None:
        if not self.sample_stem or self.sample_stem != self.sample_stem.strip():
            raise ValueError("sample_stem must be owner-normalized text")
        if self.validation_mode not in OWNER_BACKFILL_VALIDATION_MODES:
            raise ValueError("unsupported owner Backfill validation mode")
        for field in ("logical_request_count", "canonical_request_class_count"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if self.canonical_request_class_count > self.logical_request_count:
            raise ValueError("canonical classes cannot exceed logical requests")


@dataclass(frozen=True, slots=True)
class OwnerBackfillExtractionResult:
    request_identity: OwnerBackfillRequestIdentity
    trace: XICTrace

    def __post_init__(self) -> None:
        if not isinstance(self.request_identity, OwnerBackfillRequestIdentity):
            raise TypeError("request_identity must be owner-minted")
        if not isinstance(self.trace, XICTrace):
            raise TypeError("trace must be an XICTrace")


@dataclass(frozen=True, slots=True)
class OwnerBackfillLogicalExtractionResult:
    logical_request: OwnerBackfillLogicalRequestMember
    canonical_result: OwnerBackfillExtractionResult

    def __post_init__(self) -> None:
        if not isinstance(
            self.logical_request,
            OwnerBackfillLogicalRequestMember,
        ):
            raise TypeError("logical_request must be owner-minted")
        if not isinstance(self.canonical_result, OwnerBackfillExtractionResult):
            raise TypeError("canonical_result must be an owner extraction result")
        identity = self.canonical_result.request_identity
        if self.logical_request.item.sample_stem != identity.sample_stem:
            raise ValueError("logical result crosses canonical samples")
        if self.logical_request.item.request != identity.request:
            raise ValueError("logical result crosses canonical request semantics")

    @property
    def trace(self) -> XICTrace:
        return self.canonical_result.trace


@dataclass(frozen=True)
class _OwnerBackfillCandidateRecord:
    cell: AlignedCell
    candidate_index: int
    candidate_phase: str


def build_owner_backfill_cells(
    features: OwnerGroupDeliveryFeatures,
    *,
    sample_order: tuple[str, ...],
    raw_sources: Mapping[str, OwnerBackfillSource],
    validation_raw_sources: Mapping[str, OwnerBackfillSource] | None = None,
    expected_acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    | None = None,
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
    raw_xic_batch_size: int = 1,
    owner_backfill_xic_backend: OwnerBackfillXicBackend = "raw",
    owner_backfill_window_strategy: OwnerBackfillWindowStrategy = "exact",
    owner_backfill_superwindow_span_factor: int = 2,
    emit_region_audit: bool = False,
    region_audit_family_ids: frozenset[str] | None = None,
    audit_evidence_mode: str = "full",
) -> tuple[AlignedCell, ...]:
    return build_owner_backfill_result(
        features,
        sample_order=sample_order,
        raw_sources=raw_sources,
        validation_raw_sources=validation_raw_sources,
        expected_acquisition_sources=expected_acquisition_sources,
        alignment_config=alignment_config,
        peak_config=peak_config,
        raw_xic_batch_size=raw_xic_batch_size,
        owner_backfill_xic_backend=owner_backfill_xic_backend,
        owner_backfill_window_strategy=owner_backfill_window_strategy,
        owner_backfill_superwindow_span_factor=owner_backfill_superwindow_span_factor,
        emit_region_audit=emit_region_audit,
        region_audit_family_ids=region_audit_family_ids,
        audit_evidence_mode=audit_evidence_mode,
    ).cells


def build_owner_backfill_result(
    features: OwnerGroupDeliveryFeatures,
    *,
    sample_order: tuple[str, ...],
    raw_sources: Mapping[str, OwnerBackfillSource],
    validation_raw_sources: Mapping[str, OwnerBackfillSource] | None = None,
    expected_acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    | None = None,
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
    raw_xic_batch_size: int = 1,
    owner_backfill_xic_backend: OwnerBackfillXicBackend = "raw",
    owner_backfill_window_strategy: OwnerBackfillWindowStrategy = "exact",
    owner_backfill_superwindow_span_factor: int = 2,
    emit_region_audit: bool = False,
    region_audit_family_ids: frozenset[str] | None = None,
    audit_evidence_mode: str = "full",
) -> OwnerBackfillResult:
    if raw_xic_batch_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")
    if owner_backfill_superwindow_span_factor < 1:
        raise ValueError("owner_backfill_superwindow_span_factor must be >= 1")
    if owner_backfill_window_strategy not in OWNER_BACKFILL_WINDOW_STRATEGIES:
        raise ValueError(
            "owner_backfill_window_strategy must be exact or super-window",
        )
    if owner_backfill_xic_backend not in OWNER_BACKFILL_XIC_BACKENDS:
        raise ValueError("unsupported owner Backfill XIC backend")
    _validate_owner_bound_sources(
        raw_sources,
        expected_acquisition_sources=expected_acquisition_sources,
        source_label="primary",
    )
    if validation_raw_sources is not None:
        _validate_owner_bound_sources(
            validation_raw_sources,
            expected_acquisition_sources=expected_acquisition_sources,
            source_label="validation",
        )
    effective_emit_region_audit = emit_region_audit and audit_evidence_mode != "none"
    cells: list[AlignedCell] = []
    candidate_records: list[_OwnerBackfillCandidateRecord] = []
    request_reuse_observations: list[OwnerBackfillRequestReuseObservation] = []
    primary_validation_mode: OwnerBackfillValidationMode = (
        "prefilter" if validation_raw_sources is not None else "primary"
    )
    primary_equivalence_context = _request_equivalence_context(
        expected_acquisition_sources=expected_acquisition_sources,
        backend=owner_backfill_xic_backend,
        window_strategy=owner_backfill_window_strategy,
        superwindow_span_factor=owner_backfill_superwindow_span_factor,
        validation_mode=primary_validation_mode,
    )
    request_plan = build_owner_backfill_request_plan(
        features,
        sample_order=sample_order,
        raw_sample_stems=frozenset(raw_sources),
        alignment_config=alignment_config,
        equivalence_context=primary_equivalence_context,
    )
    backfill_by_feature_sample: dict[tuple[str, str], AlignedCell] = {}
    validation_pending: dict[str, list[_RequestItem]] = defaultdict(list)
    validation_pending_keys: set[tuple[str, str]] = set()
    for sample_stem in sample_order:
        sample_requests = request_plan.requests_for_sample(sample_stem)
        if not sample_requests:
            continue
        equivalence_classes = (
            request_plan.equivalence_classes_for_sample(sample_stem)
            if request_plan.exact_equivalence_available
            else None
        )
        if equivalence_classes is not None:
            request_reuse_observations.append(
                OwnerBackfillRequestReuseObservation(
                    sample_stem=sample_stem,
                    validation_mode=primary_validation_mode,
                    logical_request_count=len(sample_requests),
                    canonical_request_class_count=len(equivalence_classes),
                )
            )
        source = raw_sources[sample_stem]
        for chunk, traces in _iter_extracted_request_traces(
            source,
            tuple(sample_requests),
            raw_xic_batch_size,
            window_strategy=owner_backfill_window_strategy,
            superwindow_span_factor=owner_backfill_superwindow_span_factor,
            equivalence_classes=equivalence_classes,
        ):
            for (feature, requested_sample, request, preferred_rt), result in zip(
                chunk,
                traces,
                strict=True,
            ):
                trace = _trace_for_logical_request_result(
                    OwnerBackfillRequestItem(
                        feature,
                        requested_sample,
                        request,
                        preferred_rt,
                    ),
                    result,
                )
                cell = _backfill_feature_sample_trace(
                    feature,
                    requested_sample,
                    trace,
                    request=request,
                    preferred_rt=preferred_rt,
                    peak_config=peak_config,
                    emit_region_audit=_emit_region_audit_for_family(
                        effective_emit_region_audit,
                        feature.feature_family_id,
                        region_audit_family_ids,
                    ),
                )
                if cell is not None:
                    _append_candidate_record(
                        candidate_records,
                        cell,
                        phase=(
                            "prefilter_query"
                            if validation_raw_sources is not None
                            else "primary_query"
                        ),
                    )
                    if validation_raw_sources is None or cell.status != "rescued":
                        _keep_best_backfill_outcome_cell(
                            backfill_by_feature_sample,
                            cell,
                        )
                    else:
                        validation_pending[requested_sample].append(
                            OwnerBackfillRequestItem(
                                feature,
                                requested_sample,
                                request,
                                preferred_rt,
                            )
                        )
                        validation_pending_keys.add(
                            (feature.feature_family_id, requested_sample)
                        )
        for feature, requested_sample, request, preferred_rt in sample_requests:
            key = (feature.feature_family_id, requested_sample)
            if (
                key not in backfill_by_feature_sample
                and key not in validation_pending_keys
            ):
                cell = _backfill_unchecked_cell(
                    feature,
                    requested_sample,
                    request=request,
                    preferred_rt=preferred_rt,
                    trace_quality="owner_backfill_unassessable",
                    reason="owner-centered MS1 backfill query was not assessable",
                )
                _append_candidate_record(
                    candidate_records,
                    cell,
                    phase="primary_fallback",
                )
                _keep_best_backfill_outcome_cell(
                    backfill_by_feature_sample,
                    cell,
    )
    if validation_raw_sources is not None:
        for sample_stem in sample_order:
            validation_requests = validation_pending.get(sample_stem, [])
            if not validation_requests:
                continue
            if sample_stem not in validation_raw_sources:
                for (
                    feature,
                    requested_sample,
                    request,
                    preferred_rt,
                ) in validation_requests:
                    cell = _backfill_unchecked_cell(
                        feature,
                        requested_sample,
                        request=request,
                        preferred_rt=preferred_rt,
                        trace_quality="owner_backfill_unassessable",
                        reason=(
                            "owner-centered MS1 backfill validation "
                            "source was not available"
                        ),
                    )
                    _append_candidate_record(
                        candidate_records,
                        cell,
                        phase="validation_unavailable",
                    )
                    _keep_best_backfill_outcome_cell(
                        backfill_by_feature_sample,
                        cell,
                    )
                continue
            source = validation_raw_sources[sample_stem]
            validation_items = tuple(validation_requests)
            validation_context = _request_equivalence_context(
                expected_acquisition_sources=expected_acquisition_sources,
                backend="raw",
                window_strategy="exact",
                superwindow_span_factor=owner_backfill_superwindow_span_factor,
                validation_mode="validation",
            )
            validation_classes = (
                build_owner_backfill_request_equivalence_classes(
                    validation_items,
                    context=validation_context,
                )
                if validation_context is not None
                else None
            )
            if validation_classes is not None:
                request_reuse_observations.append(
                    OwnerBackfillRequestReuseObservation(
                        sample_stem=sample_stem,
                        validation_mode="validation",
                        logical_request_count=len(validation_items),
                        canonical_request_class_count=len(validation_classes),
                    )
                )
            for chunk, traces in _iter_extracted_request_traces(
                source,
                validation_items,
                raw_xic_batch_size,
                window_strategy="exact",
                superwindow_span_factor=owner_backfill_superwindow_span_factor,
                equivalence_classes=validation_classes,
            ):
                for (
                    feature,
                    requested_sample,
                    _request,
                    preferred_rt,
                ), result in zip(
                    chunk,
                    traces,
                    strict=True,
                ):
                    trace = _trace_for_logical_request_result(
                        OwnerBackfillRequestItem(
                            feature,
                            requested_sample,
                            _request,
                            preferred_rt,
                        ),
                        result,
                    )
                    cell = _backfill_feature_sample_trace(
                        feature,
                        requested_sample,
                        trace,
                        request=_request,
                        preferred_rt=preferred_rt,
                        peak_config=peak_config,
                        emit_region_audit=_emit_region_audit_for_family(
                            effective_emit_region_audit,
                            feature.feature_family_id,
                            region_audit_family_ids,
                        ),
                    )
                    if cell is not None:
                        _append_candidate_record(
                            candidate_records,
                            cell,
                            phase="validation_query",
                        )
                        _keep_best_backfill_outcome_cell(
                            backfill_by_feature_sample,
                            cell,
                        )
            for (
                feature,
                requested_sample,
                request,
                preferred_rt,
            ) in validation_requests:
                key = (feature.feature_family_id, requested_sample)
                if key not in backfill_by_feature_sample:
                    cell = _backfill_unchecked_cell(
                        feature,
                        requested_sample,
                        request=request,
                        preferred_rt=preferred_rt,
                        trace_quality="owner_backfill_unassessable",
                        reason=(
                            "owner-centered MS1 backfill validation "
                            "query was not assessable"
                        ),
                    )
                    _append_candidate_record(
                        candidate_records,
                        cell,
                        phase="validation_fallback",
                    )
                    _keep_best_backfill_outcome_cell(
                        backfill_by_feature_sample,
                        cell,
                    )
    for feature in features:
        if feature.review_only:
            continue
        for sample_stem in sample_order:
            cell = backfill_by_feature_sample.get(
                (feature.feature_family_id, sample_stem)
            )
            if cell is not None:
                cells.append(cell)
    selected_cell_ids = {id(cell) for cell in backfill_by_feature_sample.values()}
    return OwnerBackfillResult(
        cells=tuple(cells),
        candidate_audit_rows=_candidate_audit_rows(
            candidate_records,
            selected_cell_ids=selected_cell_ids,
            feature_order=tuple(feature.feature_family_id for feature in features),
            sample_order=sample_order,
        ),
        request_reuse_observations=tuple(request_reuse_observations),
    )


def _request_equivalence_context(
    *,
    expected_acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    | None,
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


def _validate_owner_bound_sources(
    sources: Mapping[str, OwnerBackfillSource],
    *,
    expected_acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    | None,
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
    equivalence_classes: tuple[OwnerBackfillRequestEquivalenceClass, ...]
    | None = None,
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


def _append_candidate_record(
    records: list[_OwnerBackfillCandidateRecord],
    cell: AlignedCell,
    *,
    phase: str,
) -> None:
    records.append(
        _OwnerBackfillCandidateRecord(
            cell=cell,
            candidate_index=len(records) + 1,
            candidate_phase=phase,
        )
    )


def _candidate_audit_rows(
    records: Sequence[_OwnerBackfillCandidateRecord],
    *,
    selected_cell_ids: set[int],
    feature_order: tuple[str, ...],
    sample_order: tuple[str, ...],
) -> tuple[OwnerBackfillCandidateAuditRow, ...]:
    feature_rank = {feature_id: index for index, feature_id in enumerate(feature_order)}
    sample_rank = {sample: index for index, sample in enumerate(sample_order)}
    rows = [
        _candidate_audit_row(record, id(record.cell) in selected_cell_ids)
        for record in records
    ]
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                feature_rank.get(row.feature_family_id, len(feature_rank)),
                sample_rank.get(row.sample_stem, len(sample_rank)),
                row.candidate_index,
            ),
        )
    )


def _candidate_audit_row(
    record: _OwnerBackfillCandidateRecord,
    selected_for_output: bool,
) -> OwnerBackfillCandidateAuditRow:
    cell = record.cell
    return OwnerBackfillCandidateAuditRow(
        feature_family_id=cell.cluster_id,
        group_hypothesis_id=cell.group_hypothesis_id,
        public_family_id=cell.public_family_id,
        sample_stem=cell.sample_stem,
        candidate_index=record.candidate_index,
        candidate_phase=record.candidate_phase,
        selected_for_output=selected_for_output,
        candidate_status=cell.status,
        candidate_outcome=_candidate_outcome(cell),
        trace_quality=cell.trace_quality,
        area=cell.area,
        apex_rt=cell.apex_rt,
        peak_start_rt=cell.peak_start_rt,
        peak_end_rt=cell.peak_end_rt,
        rt_delta_sec=cell.rt_delta_sec,
        backfill_seed_mz=cell.backfill_seed_mz,
        backfill_seed_rt=cell.backfill_seed_rt,
        backfill_request_rt_min=cell.backfill_request_rt_min,
        backfill_request_rt_max=cell.backfill_request_rt_max,
        backfill_request_ppm=cell.backfill_request_ppm,
        reason=cell.reason,
        selection_note="selected" if selected_for_output else "not_selected",
    )


def _candidate_outcome(cell: AlignedCell) -> str:
    if cell.status == "rescued":
        return "detected"
    if cell.trace_quality == "owner_backfill_not_detected":
        return "not_detected"
    if cell.trace_quality == "owner_backfill_unassessable":
        return "unassessable"
    return cell.trace_quality or cell.status


def _iter_exact_request_traces(
    source: OwnerBackfillSource,
    items: tuple[_ExtractionRequestItem, ...],
    chunk_size: int,
):
    chunks = _scan_window_aware_chunks(source, items, chunk_size)
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


def _backfill_feature_sample_trace(
    feature: OwnerGroupDeliveryFeature,
    sample_stem: str,
    trace: XICTrace,
    *,
    request: XICRequest,
    preferred_rt: float | None = None,
    peak_config: ExtractionConfig,
    emit_region_audit: bool = False,
) -> AlignedCell | None:
    peak_preferred_rt = (
        feature.family_center_rt if preferred_rt is None else preferred_rt
    )
    detection = detect_ms1_trace_peak(
        trace.rt,
        trace.intensity,
        peak_config=peak_config,
        preferred_rt=peak_preferred_rt,
        strict_preferred_rt=False,
        peak_finder=find_peak_and_area,
    )
    if detection.status == "unassessable_trace":
        return _backfill_unchecked_cell(
            feature,
            sample_stem,
            request=request,
            preferred_rt=preferred_rt,
            trace_quality="owner_backfill_unassessable",
            reason="owner-centered MS1 backfill query was not assessable",
        )
    if (
        detection.status != "detected"
        or detection.result is None
        or detection.peak is None
    ):
        return _backfill_unchecked_cell(
            feature,
            sample_stem,
            request=request,
            preferred_rt=preferred_rt,
            trace_quality="owner_backfill_not_detected",
            reason="owner-centered MS1 backfill query found no accepted peak",
        )
    rt_array = detection.rt
    intensity_array = detection.intensity
    result = detection.result
    peak = detection.peak
    trace_group = (
        alignment_trace_group(
            sample_stem=sample_stem,
            family_id=feature.feature_family_id,
            mz=request.mz,
            rt_values=rt_array,
            intensity_values=intensity_array,
            rt_min=request.rt_min,
            rt_max=request.rt_max,
            ppm_tol=request.ppm_tol,
            expected_rt_min=feature.family_center_rt,
            neutral_loss_tag=feature.neutral_loss_tag,
            product_mz=feature.family_product_mz,
            observed_neutral_loss_da=feature.family_observed_neutral_loss_da,
            source="owner_backfill_batch",
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
    cell = AlignedCell(
        sample_stem=sample_stem,
        cluster_id=feature.feature_family_id,
        status="rescued",
        area=peak.area,
        apex_rt=peak.rt,
        height=peak.intensity,
        peak_start_rt=peak.peak_start,
        peak_end_rt=peak.peak_end,
        rt_delta_sec=(peak.rt - feature.family_center_rt) * 60.0,
        trace_quality="owner_backfill",
        scan_support_score=_scan_support_score(
            rt_array,
            peak_start=peak.peak_start,
            peak_end=peak.peak_end,
            scans_target=peak_config.resolver_min_scans,
        ),
        source_candidate_id=None,
        source_raw_file=None,
        reason="owner-centered MS1 backfill",
        selected_integration=integration_from_peak_trace(
            peak,
            rt_array,
            intensity_array,
            boundary_sources=("owner_backfill_batch",),
            baseline_integration_method=getattr(
                peak_config,
                "baseline_integration_method",
                "asls",
            ),
        ),
        backfill_seed_mz=request.mz,
        backfill_seed_rt=preferred_rt,
        backfill_request_rt_min=request.rt_min,
        backfill_request_rt_max=request.rt_max,
        backfill_request_ppm=request.ppm_tol,
        **delivery_cell_projection(
            feature,
            gap_fill_state="gap_fill_rescued",
            gap_fill_reason="group_centered_query_detected",
            missing_observation_state="queried_and_detected",
        ),
    )
    return with_region_audit(cell, region_audit)


def _backfill_unchecked_cell(
    feature: OwnerGroupDeliveryFeature,
    sample_stem: str,
    *,
    request: XICRequest,
    preferred_rt: float | None,
    trace_quality: str,
    reason: str,
) -> AlignedCell:
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=feature.feature_family_id,
        status="unchecked",
        area=None,
        apex_rt=None,
        height=None,
        peak_start_rt=None,
        peak_end_rt=None,
        rt_delta_sec=None,
        trace_quality=trace_quality,
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
        reason=reason,
        backfill_seed_mz=request.mz,
        backfill_seed_rt=preferred_rt,
        backfill_request_rt_min=request.rt_min,
        backfill_request_rt_max=request.rt_max,
        backfill_request_ppm=request.ppm_tol,
        **delivery_cell_projection(
            feature,
            gap_fill_state="not_filled",
            gap_fill_reason="query_attempt_not_detected",
            missing_observation_state="missing_unchecked",
        ),
    )


def _backfill_seed_centers(
    feature: OwnerGroupDeliveryFeature,
) -> tuple[tuple[float, float], ...]:
    return backfill_seed_centers(feature)


def _any_detected_owner_can_be_superseded(
    feature: OwnerGroupDeliveryFeature,
    owners: Sequence[SampleLocalMS1Owner] | None,
) -> bool:
    return any_detected_owner_can_be_superseded(feature, owners)


def _detected_owner_can_be_superseded(
    feature: OwnerGroupDeliveryFeature,
    owner: SampleLocalMS1Owner,
) -> bool:
    detected_area = positive_finite(owner.owner_area)
    if detected_area is None:
        return False
    family_area = median_owner_area(feature)
    if family_area is None:
        return False
    return detected_area <= family_area * 0.25


def _emit_region_audit_for_family(
    emit_region_audit: bool,
    family_id: str,
    region_audit_family_ids: frozenset[str] | None,
) -> bool:
    if not emit_region_audit:
        return False
    if region_audit_family_ids is None:
        return True
    return family_id in region_audit_family_ids


def _keep_best_backfill_outcome_cell(
    cells: dict[tuple[str, str], AlignedCell],
    candidate: AlignedCell,
) -> None:
    key = (candidate.cluster_id, candidate.sample_stem)
    current = cells.get(key)
    if current is None:
        cells[key] = candidate
        return
    if current.status == "rescued" and candidate.status != "rescued":
        return
    if current.status != "rescued" and candidate.status == "rescued":
        cells[key] = candidate
        return
    if candidate.status != "rescued":
        return
    if _rescued_cell_sort_key(candidate) < _rescued_cell_sort_key(current):
        cells[key] = candidate


def _rescued_cell_sort_key(cell: AlignedCell) -> tuple[float, float, float]:
    area = float(cell.area or 0.0)
    rt_delta = abs(cell.rt_delta_sec) if cell.rt_delta_sec is not None else np.inf
    apex_rt = float(cell.apex_rt or 0.0)
    return (-area, rt_delta, apex_rt)


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
    result_by_identity = {
        result.request_identity: result for result in typed_results
    }
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


def _scan_support_score(
    rt: NDArray[np.float64],
    *,
    peak_start: float,
    peak_end: float,
    scans_target: int,
) -> float:
    if scans_target <= 0:
        return 0.0
    scan_count = int(np.count_nonzero((rt >= peak_start) & (rt <= peak_end)))
    return min(1.0, scan_count / scans_target)
