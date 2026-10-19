"""Backfill request planning and identity contracts."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, NamedTuple

from xic_extractor.alignment.backfill.runtime_projection import (
    BackfillRuntimeFeature,
    BackfillRuntimeFeatureProjection,
    BackfillRuntimeFeatures,
    mint_backfill_runtime_feature,
)
from xic_extractor.alignment.backfill.scope import (
    backfill_request_sample_stems,
    backfill_seed_centers,
)
from xic_extractor.alignment.config import AlignmentConfig, BackfillXicBackend
from xic_extractor.alignment.owner_group_delivery import (
    OwnerGroupDeliveryFeatures,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.xic_models import XICRequest

BackfillWindowStrategy = Literal["exact", "super-window"]
BACKFILL_WINDOW_STRATEGIES: tuple[BackfillWindowStrategy, ...] = (
    "exact",
    "super-window",
)
BackfillValidationMode = Literal["primary", "prefilter", "validation"]
BACKFILL_VALIDATION_MODES: tuple[BackfillValidationMode, ...] = (
    "primary",
    "prefilter",
    "validation",
)
BACKFILL_XIC_BACKENDS: tuple[BackfillXicBackend, ...] = (
    "raw",
    "ms1_index",
    "ms1_index_hybrid",
)
BACKFILL_TRACE_RESULT_SEMANTICS = "owner_backfill_xic_trace"
_REQUEST_IDENTITY_PREFIX = "xic.owner-backfill-request:"
_LOGICAL_REQUEST_PREFIX = "xic.owner-backfill-logical-request:"
_RUNTIME_SAMPLE_PROJECTION_PREFIX = "xic.owner-backfill-runtime-sample:"


class BackfillRequestItem(NamedTuple):
    feature: BackfillRuntimeFeature
    sample_stem: str
    request: XICRequest
    preferred_rt: float


@dataclass(frozen=True, slots=True)
class BackfillSampleRequestProjection:
    sample_stem: str
    features: tuple[BackfillRuntimeFeature, ...]
    logical_request_count: int


@dataclass(frozen=True, slots=True)
class BackfillSampleRuntimeProjection:
    """Sample-bound compact feature payload minted by the request-plan owner."""

    runtime_projection_id: str
    sample_stem: str
    features: BackfillRuntimeFeatures
    logical_request_count: int
    logical_request_offset: int
    validation_request_offset: int
    canonical_sample: ResolvedCanonicalSampleIdentity | None = None


@dataclass(frozen=True, slots=True)
class BackfillRequestEquivalenceContext:
    acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    backend: BackfillXicBackend
    window_strategy: BackfillWindowStrategy
    superwindow_span_factor: int
    validation_mode: BackfillValidationMode
    result_semantics: str = BACKFILL_TRACE_RESULT_SEMANTICS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "acquisition_sources",
            MappingProxyType(dict(self.acquisition_sources)),
        )


@dataclass(frozen=True, slots=True)
class BackfillRequestIdentity:
    request_identity_id: str
    sample_stem: str
    acquisition_source: AcquisitionSourceIdentity
    request: XICRequest
    backend: BackfillXicBackend
    window_strategy: BackfillWindowStrategy
    superwindow_span_factor: int
    validation_mode: BackfillValidationMode
    result_semantics: str


@dataclass(frozen=True, slots=True)
class BackfillLogicalRequestMember:
    logical_request_id: str
    logical_index: int
    group_hypothesis_id: str
    item: BackfillRequestItem


@dataclass(frozen=True, slots=True)
class BackfillRequestEquivalenceClass:
    identity: BackfillRequestIdentity
    members: tuple[BackfillLogicalRequestMember, ...]

    @property
    def class_id(self) -> str:
        return self.identity.request_identity_id


@dataclass(frozen=True)
class BackfillRequestPlan:
    requests_by_sample: Mapping[str, tuple[BackfillRequestItem, ...]]
    equivalence_classes_by_sample: (
        Mapping[
            str,
            tuple[BackfillRequestEquivalenceClass, ...],
        ]
        | None
    ) = None
    equivalence_context: BackfillRequestEquivalenceContext | None = None
    equivalence_unavailable_reason: str = "owner_equivalence_context_not_supplied"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "requests_by_sample", MappingProxyType(dict(self.requests_by_sample))
        )
        if self.equivalence_classes_by_sample is not None:
            object.__setattr__(
                self,
                "equivalence_classes_by_sample",
                MappingProxyType(dict(self.equivalence_classes_by_sample)),
            )

    @property
    def exact_equivalence_available(self) -> bool:
        return self.equivalence_classes_by_sample is not None

    def requests_for_sample(
        self,
        sample_stem: str,
    ) -> tuple[BackfillRequestItem, ...]:
        return self.requests_by_sample.get(sample_stem, ())

    def request_count_for_sample(self, sample_stem: str) -> int:
        return len(self.requests_for_sample(sample_stem))

    def equivalence_classes_for_sample(
        self,
        sample_stem: str,
    ) -> tuple[BackfillRequestEquivalenceClass, ...]:
        if self.equivalence_classes_by_sample is None:
            raise ValueError(
                "exact request equivalence is unavailable: "
                f"{self.equivalence_unavailable_reason}"
            )
        return self.equivalence_classes_by_sample.get(sample_stem, ())

    def canonical_class_count_for_sample(self, sample_stem: str) -> int:
        return len(self.equivalence_classes_for_sample(sample_stem))

    def logical_bindings_for_sample(
        self,
        sample_stem: str,
    ) -> tuple[
        tuple[BackfillLogicalRequestMember, BackfillRequestIdentity],
        ...,
    ]:
        classes = self.equivalence_classes_for_sample(sample_stem)
        bindings = tuple(
            (member, equivalence_class.identity)
            for equivalence_class in classes
            for member in equivalence_class.members
        )
        return tuple(sorted(bindings, key=lambda item: item[0].logical_index))

    def logical_bindings_for_position(
        self,
        *,
        sample_stem: str,
        group_hypothesis_id: str,
    ) -> tuple[
        tuple[BackfillLogicalRequestMember, BackfillRequestIdentity],
        ...,
    ]:
        _required_text(group_hypothesis_id, "group_hypothesis_id")
        return tuple(
            (logical_request, request_identity)
            for logical_request, request_identity in self.logical_bindings_for_sample(
                sample_stem
            )
            if logical_request.group_hypothesis_id == group_hypothesis_id
        )


def build_backfill_request_plan(
    features: tuple[BackfillRuntimeFeature, ...],
    *,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
    equivalence_context: BackfillRequestEquivalenceContext | None = None,
) -> BackfillRequestPlan:
    pending: dict[str, list[BackfillRequestItem]] = defaultdict(list)
    rt_window_min = alignment_config.max_rt_sec / 60.0
    for feature, sample_stem, seed_centers in _iter_feature_sample_request_groups(
        features,
        sample_order=sample_order,
        raw_sample_stems=raw_sample_stems,
        alignment_config=alignment_config,
    ):
        for seed_mz, seed_rt in seed_centers:
            pending[sample_stem].append(
                BackfillRequestItem(
                    feature=feature,
                    sample_stem=sample_stem,
                    request=XICRequest(
                        mz=seed_mz,
                        rt_min=seed_rt - rt_window_min,
                        rt_max=seed_rt + rt_window_min,
                        ppm_tol=alignment_config.preferred_ppm,
                    ),
                    preferred_rt=seed_rt,
                )
            )
    requests_by_sample = {
        sample_stem: tuple(sample_requests)
        for sample_stem, sample_requests in pending.items()
    }
    if equivalence_context is None:
        return BackfillRequestPlan(requests_by_sample=requests_by_sample)
    classes_by_sample = {
        sample_stem: build_backfill_request_equivalence_classes(
            sample_requests,
            context=equivalence_context,
        )
        for sample_stem, sample_requests in requests_by_sample.items()
    }
    return BackfillRequestPlan(
        requests_by_sample=requests_by_sample,
        equivalence_classes_by_sample=classes_by_sample,
        equivalence_context=equivalence_context,
        equivalence_unavailable_reason="",
    )


def build_backfill_sample_request_projections(
    features: OwnerGroupDeliveryFeatures,
    *,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
) -> Mapping[str, BackfillSampleRequestProjection]:
    pending_features: dict[str, list[BackfillRuntimeFeature]] = defaultdict(list)
    request_counts: dict[str, int] = defaultdict(int)
    for feature, sample_stem, seed_centers in _iter_feature_sample_request_groups(
        features,
        sample_order=sample_order,
        raw_sample_stems=raw_sample_stems,
        alignment_config=alignment_config,
    ):
        request_counts[sample_stem] += len(seed_centers)
        pending_features[sample_stem].append(feature)

    projections: dict[str, BackfillSampleRequestProjection] = {}
    for sample_stem in tuple(pending_features):
        frozen_sample_features = tuple(pending_features.pop(sample_stem))
        projections[sample_stem] = BackfillSampleRequestProjection(
            sample_stem=sample_stem,
            features=frozen_sample_features,
            logical_request_count=request_counts[sample_stem],
        )
    return MappingProxyType(projections)


def build_backfill_sample_runtime_projections(
    features: OwnerGroupDeliveryFeatures,
    *,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
    canonical_samples_by_stem: Mapping[
        str,
        ResolvedCanonicalSampleIdentity,
    ]
    | None = None,
) -> Mapping[str, BackfillSampleRuntimeProjection]:
    """Mint compact, sample-bound worker inputs from the canonical request plan."""

    request_projections = build_backfill_sample_request_projections(
        features,
        sample_order=sample_order,
        raw_sample_stems=raw_sample_stems,
        alignment_config=alignment_config,
    )
    runtime_features_by_object_id: dict[
        int,
        BackfillRuntimeFeatureProjection,
    ] = {}
    runtime_projections: dict[str, BackfillSampleRuntimeProjection] = {}
    logical_request_counts = {
        sample_stem: projection.logical_request_count
        for sample_stem, projection in request_projections.items()
    }
    total_logical_request_count = sum(logical_request_counts.values())
    logical_request_offset = 0
    for sample_stem in sample_order:
        projection = request_projections.get(sample_stem)
        if projection is None:
            continue
        compact_features: list[BackfillRuntimeFeatureProjection] = []
        for feature in projection.features:
            feature_object_id = id(feature)
            compact_feature = runtime_features_by_object_id.get(feature_object_id)
            if compact_feature is None:
                compact_feature = mint_backfill_runtime_feature(feature)
                runtime_features_by_object_id[feature_object_id] = compact_feature
            compact_features.append(compact_feature)
        runtime_projections[sample_stem] = _mint_backfill_sample_runtime_projection(
            sample_stem=sample_stem,
            features=tuple(compact_features),
            logical_request_count=projection.logical_request_count,
            logical_request_offset=logical_request_offset,
            validation_request_offset=(
                total_logical_request_count + logical_request_offset
            ),
            canonical_sample=(
                canonical_samples_by_stem.get(sample_stem)
                if canonical_samples_by_stem is not None
                else None
            ),
        )
        logical_request_offset += projection.logical_request_count
    return MappingProxyType(runtime_projections)


def _mint_backfill_sample_runtime_projection(
    *,
    sample_stem: str,
    features: BackfillRuntimeFeatures,
    logical_request_count: int,
    logical_request_offset: int,
    validation_request_offset: int,
    canonical_sample: ResolvedCanonicalSampleIdentity | None = None,
) -> BackfillSampleRuntimeProjection:
    basis = _runtime_sample_projection_basis(
        sample_stem=sample_stem,
        features=features,
        logical_request_count=logical_request_count,
        logical_request_offset=logical_request_offset,
        validation_request_offset=validation_request_offset,
        canonical_sample=canonical_sample,
    )
    return BackfillSampleRuntimeProjection(
        runtime_projection_id=_typed_id(_RUNTIME_SAMPLE_PROJECTION_PREFIX, basis),
        sample_stem=sample_stem,
        features=features,
        logical_request_count=logical_request_count,
        logical_request_offset=logical_request_offset,
        validation_request_offset=validation_request_offset,
        canonical_sample=canonical_sample,
    )


def _runtime_sample_projection_basis(
    *,
    sample_stem: str,
    features: BackfillRuntimeFeatures,
    logical_request_count: int,
    logical_request_offset: int,
    validation_request_offset: int,
    canonical_sample: ResolvedCanonicalSampleIdentity | None = None,
) -> dict[str, object]:
    return {
        "schema": "owner_backfill_runtime_sample",
        "sample_stem": _required_text(sample_stem, "sample_stem"),
        "runtime_feature_ids": tuple(
            _required_text(feature.runtime_feature_id, "runtime_feature_id")
            for feature in features
        ),
        "logical_request_count": logical_request_count,
        "logical_request_offset": logical_request_offset,
        "validation_request_offset": validation_request_offset,
        "canonical_sample": (
            {
                "sample_identity_id": canonical_sample.identity.sample_identity_id,
                "snapshot_id": canonical_sample.snapshot.snapshot_id,
                "lookup_token": canonical_sample.lookup_token,
            }
            if canonical_sample is not None
            else None
        ),
    }


def _iter_feature_sample_request_groups(
    features: tuple[BackfillRuntimeFeature, ...],
    *,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
) -> Iterator[tuple[BackfillRuntimeFeature, str, tuple[tuple[float, float], ...]]]:
    for feature in features:
        seed_centers = backfill_seed_centers(feature)
        for sample_stem in backfill_request_sample_stems(
            feature,
            sample_order=sample_order,
            raw_sample_stems=raw_sample_stems,
            alignment_config=alignment_config,
        ):
            yield feature, sample_stem, seed_centers


def build_backfill_request_equivalence_classes(
    items: tuple[BackfillRequestItem, ...],
    *,
    context: BackfillRequestEquivalenceContext,
) -> tuple[BackfillRequestEquivalenceClass, ...]:
    if not isinstance(context, BackfillRequestEquivalenceContext):
        raise TypeError("context must be an BackfillRequestEquivalenceContext")
    if not isinstance(items, tuple) or any(
        not isinstance(item, BackfillRequestItem) for item in items
    ):
        raise TypeError("items must be an BackfillRequestItem tuple")
    pending: dict[
        BackfillRequestIdentity,
        list[BackfillLogicalRequestMember],
    ] = {}
    for logical_index, item in enumerate(items):
        source = context.acquisition_sources.get(item.sample_stem)
        if not isinstance(source, AcquisitionSourceIdentity):
            raise ValueError(
                "exact request equivalence requires one owner-minted acquisition "
                f"identity for {item.sample_stem}"
            )
        identity = mint_backfill_request_identity(
            sample_stem=item.sample_stem,
            acquisition_source=source,
            request=item.request,
            backend=context.backend,
            window_strategy=context.window_strategy,
            superwindow_span_factor=context.superwindow_span_factor,
            validation_mode=context.validation_mode,
            result_semantics=context.result_semantics,
        )
        group_hypothesis_id = _required_text(
            getattr(item.feature, "group_hypothesis_id", None),
            "feature.group_hypothesis_id",
        )
        member = mint_backfill_logical_request_member(
            logical_index=logical_index,
            group_hypothesis_id=group_hypothesis_id,
            item=item,
        )
        pending.setdefault(identity, []).append(member)
    return tuple(
        BackfillRequestEquivalenceClass(
            identity=identity,
            members=tuple(members),
        )
        for identity, members in pending.items()
    )


def mint_backfill_request_identity(
    *,
    sample_stem: str,
    acquisition_source: AcquisitionSourceIdentity,
    request: XICRequest,
    backend: BackfillXicBackend,
    window_strategy: BackfillWindowStrategy,
    superwindow_span_factor: int,
    validation_mode: BackfillValidationMode,
    result_semantics: str = BACKFILL_TRACE_RESULT_SEMANTICS,
) -> BackfillRequestIdentity:
    context = BackfillRequestEquivalenceContext(
        acquisition_sources={sample_stem: acquisition_source},
        backend=backend,
        window_strategy=window_strategy,
        superwindow_span_factor=superwindow_span_factor,
        validation_mode=validation_mode,
        result_semantics=result_semantics,
    )
    basis = _request_identity_basis(
        sample_stem=sample_stem,
        acquisition_source=acquisition_source,
        request=request,
        context=context,
    )
    return BackfillRequestIdentity(
        request_identity_id=_typed_id(_REQUEST_IDENTITY_PREFIX, basis),
        sample_stem=sample_stem,
        acquisition_source=acquisition_source,
        request=request,
        backend=backend,
        window_strategy=window_strategy,
        superwindow_span_factor=superwindow_span_factor,
        validation_mode=validation_mode,
        result_semantics=result_semantics,
    )


def mint_backfill_logical_request_member(
    *,
    logical_index: int,
    group_hypothesis_id: str,
    item: BackfillRequestItem,
) -> BackfillLogicalRequestMember:
    basis = {
        "schema": "owner_backfill_logical_request",
        "logical_index": logical_index,
        "group_hypothesis_id": group_hypothesis_id,
        "sample_stem": item.sample_stem,
        "request": _request_basis(item.request),
        "preferred_rt": item.preferred_rt,
    }
    return BackfillLogicalRequestMember(
        logical_request_id=_typed_id(_LOGICAL_REQUEST_PREFIX, basis),
        logical_index=logical_index,
        group_hypothesis_id=group_hypothesis_id,
        item=item,
    )


def _request_identity_basis(
    *,
    sample_stem: str,
    acquisition_source: AcquisitionSourceIdentity,
    request: XICRequest,
    context: BackfillRequestEquivalenceContext,
) -> dict[str, object]:
    return {
        "schema": "owner_backfill_request_identity",
        "sample_stem": sample_stem,
        "acquisition_source_id": acquisition_source.acquisition_source_id,
        "request": _request_basis(request),
        "backend": context.backend,
        "window_strategy": context.window_strategy,
        "superwindow_span_factor": context.superwindow_span_factor,
        "validation_mode": context.validation_mode,
        "result_semantics": context.result_semantics,
    }


def _request_basis(request: XICRequest) -> dict[str, float]:
    _validate_request_coordinates(request)
    return {
        "mz": request.mz,
        "rt_min": request.rt_min,
        "rt_max": request.rt_max,
        "ppm_tol": request.ppm_tol,
    }


def _validate_request_coordinates(request: XICRequest) -> None:
    if any(
        not math.isfinite(value)
        for value in (request.mz, request.rt_min, request.rt_max, request.ppm_tol)
    ):
        raise ValueError("exact request semantics require finite coordinates")


def _typed_id(prefix: str, basis: Mapping[str, object]) -> str:
    payload = json.dumps(
        basis,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}{hashlib.sha256(payload).hexdigest()}"


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty owner-normalized text")
    return value
