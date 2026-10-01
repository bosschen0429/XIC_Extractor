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

from xic_extractor.alignment.backfill_runtime_projection import (
    BackfillRuntimeFeature,
    BackfillRuntimeFeatureProjection,
    BackfillRuntimeFeatures,
    mint_backfill_runtime_feature,
    validate_backfill_runtime_feature,
)
from xic_extractor.alignment.backfill_scope import (
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

    def __post_init__(self) -> None:
        _required_text(self.sample_stem, "sample_stem")
        if not isinstance(self.features, tuple):
            raise TypeError("sample request projection features must be a tuple")
        if (
            isinstance(self.logical_request_count, bool)
            or not isinstance(self.logical_request_count, int)
            or self.logical_request_count < 1
        ):
            raise ValueError(
                "sample request projection count must be a positive integer"
            )


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

    def __post_init__(self) -> None:
        sample_stem = _required_text(self.sample_stem, "sample_stem")
        if not isinstance(self.features, tuple) or not self.features:
            raise ValueError(
                "runtime sample projection features must be a non-empty tuple"
            )
        if any(
            not isinstance(feature, BackfillRuntimeFeatureProjection)
            for feature in self.features
        ):
            raise TypeError(
                "runtime sample projection features must be compact owner projections"
            )
        if (
            isinstance(self.logical_request_count, bool)
            or not isinstance(self.logical_request_count, int)
            or self.logical_request_count < 1
        ):
            raise ValueError(
                "runtime sample projection count must be a positive integer"
            )
        if (
            isinstance(self.logical_request_offset, bool)
            or not isinstance(self.logical_request_offset, int)
            or self.logical_request_offset < 0
        ):
            raise ValueError(
                "runtime sample projection offset must be a non-negative integer"
            )
        if (
            isinstance(self.validation_request_offset, bool)
            or not isinstance(self.validation_request_offset, int)
            or self.validation_request_offset < 0
        ):
            raise ValueError(
                "runtime sample validation offset must be a non-negative integer"
            )
        if self.canonical_sample is not None:
            if not isinstance(
                self.canonical_sample,
                ResolvedCanonicalSampleIdentity,
            ):
                raise TypeError("canonical_sample must be owner-resolved")
            if self.canonical_sample.lookup_token != sample_stem:
                raise ValueError("runtime projection crossed canonical sample identity")
        expected = _typed_id(
            _RUNTIME_SAMPLE_PROJECTION_PREFIX,
            _runtime_sample_projection_basis(
                sample_stem=sample_stem,
                features=self.features,
                logical_request_count=self.logical_request_count,
                logical_request_offset=self.logical_request_offset,
                validation_request_offset=self.validation_request_offset,
                canonical_sample=self.canonical_sample,
            ),
        )
        if self.runtime_projection_id != expected:
            raise ValueError(
                "runtime_projection_id mismatches sample-bound Backfill semantics"
            )


@dataclass(frozen=True, slots=True)
class BackfillRequestEquivalenceContext:
    acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    backend: BackfillXicBackend
    window_strategy: BackfillWindowStrategy
    superwindow_span_factor: int
    validation_mode: BackfillValidationMode
    result_semantics: str = BACKFILL_TRACE_RESULT_SEMANTICS

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition_sources, Mapping):
            raise TypeError("acquisition_sources must be a mapping")
        acquisition_sources = dict(self.acquisition_sources)
        for sample_stem, source in acquisition_sources.items():
            _required_text(sample_stem, "sample_stem")
            if not isinstance(source, AcquisitionSourceIdentity):
                raise TypeError(
                    "acquisition_sources must contain owner-minted identities"
                )
        object.__setattr__(
            self,
            "acquisition_sources",
            MappingProxyType(acquisition_sources),
        )
        if self.backend not in BACKFILL_XIC_BACKENDS:
            raise ValueError("unsupported owner Backfill XIC backend")
        if self.window_strategy not in BACKFILL_WINDOW_STRATEGIES:
            raise ValueError("unsupported owner Backfill window strategy")
        if (
            isinstance(self.superwindow_span_factor, bool)
            or not isinstance(self.superwindow_span_factor, int)
            or self.superwindow_span_factor < 1
        ):
            raise ValueError("superwindow_span_factor must be a positive integer")
        if self.validation_mode not in BACKFILL_VALIDATION_MODES:
            raise ValueError("unsupported owner Backfill validation mode")
        if self.result_semantics != BACKFILL_TRACE_RESULT_SEMANTICS:
            raise ValueError("unsupported owner Backfill result semantics")


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

    def __post_init__(self) -> None:
        sample_stem = _required_text(self.sample_stem, "sample_stem")
        if not isinstance(self.acquisition_source, AcquisitionSourceIdentity):
            raise TypeError("acquisition_source must be an owner-minted identity")
        if not isinstance(self.request, XICRequest):
            raise TypeError("request must be an XICRequest")
        _validate_request_coordinates(self.request)
        context = BackfillRequestEquivalenceContext(
            acquisition_sources={sample_stem: self.acquisition_source},
            backend=self.backend,
            window_strategy=self.window_strategy,
            superwindow_span_factor=self.superwindow_span_factor,
            validation_mode=self.validation_mode,
            result_semantics=self.result_semantics,
        )
        expected = _typed_id(
            _REQUEST_IDENTITY_PREFIX,
            _request_identity_basis(
                sample_stem=sample_stem,
                acquisition_source=self.acquisition_source,
                request=self.request,
                context=context,
            ),
        )
        if self.request_identity_id != expected:
            raise ValueError(
                "request_identity_id mismatches complete owner request semantics"
            )


@dataclass(frozen=True, slots=True)
class BackfillLogicalRequestMember:
    logical_request_id: str
    logical_index: int
    group_hypothesis_id: str
    item: BackfillRequestItem

    def __post_init__(self) -> None:
        if (
            isinstance(self.logical_index, bool)
            or not isinstance(self.logical_index, int)
            or self.logical_index < 0
        ):
            raise ValueError("logical_index must be a non-negative integer")
        group_hypothesis_id = _required_text(
            self.group_hypothesis_id,
            "group_hypothesis_id",
        )
        if not isinstance(self.item, BackfillRequestItem):
            raise TypeError("item must be an BackfillRequestItem")
        if group_hypothesis_id != _required_text(
            getattr(self.item.feature, "group_hypothesis_id", None),
            "feature.group_hypothesis_id",
        ):
            raise ValueError("group_hypothesis_id mismatches the owner feature")
        expected = _typed_id(
            _LOGICAL_REQUEST_PREFIX,
            {
                "schema": "owner_backfill_logical_request",
                "logical_index": self.logical_index,
                "group_hypothesis_id": group_hypothesis_id,
                "sample_stem": self.item.sample_stem,
                "request": _request_basis(self.item.request),
                "preferred_rt": self.item.preferred_rt,
            },
        )
        if self.logical_request_id != expected:
            raise ValueError(
                "logical_request_id mismatches the hypothesis-bound request"
            )


@dataclass(frozen=True, slots=True)
class BackfillRequestEquivalenceClass:
    identity: BackfillRequestIdentity
    members: tuple[BackfillLogicalRequestMember, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.identity, BackfillRequestIdentity):
            raise TypeError("identity must be an BackfillRequestIdentity")
        if not isinstance(self.members, tuple) or not self.members:
            raise ValueError("equivalence class members must be a non-empty tuple")
        indices = tuple(member.logical_index for member in self.members)
        if indices != tuple(sorted(indices)) or len(set(indices)) != len(indices):
            raise ValueError(
                "equivalence class logical indices must be unique and ordered"
            )
        for member in self.members:
            if not isinstance(member, BackfillLogicalRequestMember):
                raise TypeError("equivalence class contains an invalid member")
            if member.item.sample_stem != self.identity.sample_stem:
                raise ValueError("equivalence class crosses canonical samples")
            if member.item.request != self.identity.request:
                raise ValueError("equivalence class crosses XIC request semantics")

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
        if not isinstance(self.requests_by_sample, Mapping):
            raise TypeError("requests_by_sample must be a mapping")
        requests_by_sample = dict(self.requests_by_sample)
        for sample_stem, requests in requests_by_sample.items():
            _required_text(sample_stem, "sample_stem")
            if not isinstance(requests, tuple) or any(
                not isinstance(item, BackfillRequestItem) for item in requests
            ):
                raise TypeError("requests_by_sample must contain request tuples")
            if any(item.sample_stem != sample_stem for item in requests):
                raise ValueError("request plan sample bucket is inconsistent")
        object.__setattr__(
            self,
            "requests_by_sample",
            MappingProxyType(requests_by_sample),
        )
        if self.equivalence_classes_by_sample is None:
            if self.equivalence_context is not None:
                raise ValueError(
                    "equivalence context requires a complete request partition"
                )
            _required_text(
                self.equivalence_unavailable_reason,
                "equivalence_unavailable_reason",
            )
            return
        if not isinstance(
            self.equivalence_context,
            BackfillRequestEquivalenceContext,
        ):
            raise ValueError(
                "available equivalence partition requires its owner context"
            )
        if not isinstance(self.equivalence_classes_by_sample, Mapping):
            raise TypeError("equivalence_classes_by_sample must be a mapping")
        equivalence_classes_by_sample = dict(self.equivalence_classes_by_sample)
        for classes in equivalence_classes_by_sample.values():
            if not isinstance(classes, tuple) or any(
                not isinstance(item, BackfillRequestEquivalenceClass)
                for item in classes
            ):
                raise TypeError(
                    "equivalence_classes_by_sample must contain class tuples"
                )
        object.__setattr__(
            self,
            "equivalence_classes_by_sample",
            MappingProxyType(equivalence_classes_by_sample),
        )
        if self.equivalence_unavailable_reason:
            raise ValueError(
                "available equivalence partition cannot have an unavailable reason"
            )
        if set(self.equivalence_classes_by_sample) != set(self.requests_by_sample):
            raise ValueError("equivalence partition must cover every request sample")
        for sample_stem, requests in self.requests_by_sample.items():
            classes = self.equivalence_classes_by_sample[sample_stem]
            first_indices = tuple(item.members[0].logical_index for item in classes)
            if first_indices != tuple(sorted(first_indices)):
                raise ValueError(
                    "equivalence classes must follow first logical occurrence"
                )
            members = tuple(member for item in classes for member in item.members)
            indices = tuple(member.logical_index for member in members)
            if tuple(sorted(indices)) != tuple(range(len(requests))):
                raise ValueError(
                    "equivalence partition must cover each logical request once"
                )
            for member in members:
                if member.item != requests[member.logical_index]:
                    raise ValueError(
                        "equivalence member is not bound to its logical request"
                    )
            for equivalence_class in classes:
                source = self.equivalence_context.acquisition_sources.get(sample_stem)
                if not isinstance(source, AcquisitionSourceIdentity):
                    raise ValueError(
                        "equivalence context does not bind every request sample"
                    )
                expected_identity = mint_backfill_request_identity(
                    sample_stem=sample_stem,
                    acquisition_source=source,
                    request=equivalence_class.identity.request,
                    backend=self.equivalence_context.backend,
                    window_strategy=self.equivalence_context.window_strategy,
                    superwindow_span_factor=(
                        self.equivalence_context.superwindow_span_factor
                    ),
                    validation_mode=self.equivalence_context.validation_mode,
                    result_semantics=self.equivalence_context.result_semantics,
                )
                if equivalence_class.identity != expected_identity:
                    raise ValueError(
                        "equivalence class identity mismatches its owner context"
                    )

    @property
    def exact_equivalence_available(self) -> bool:
        return self.equivalence_classes_by_sample is not None

    def requests_for_sample(
        self,
        sample_stem: str,
    ) -> tuple[BackfillRequestItem, ...]:
        return self.requests_by_sample.get(sample_stem, ())

    def features_for_sample(
        self,
        sample_stem: str,
    ) -> tuple[BackfillRuntimeFeature, ...]:
        features: list[BackfillRuntimeFeature] = []
        seen_feature_ids: set[int] = set()
        for item in self.requests_for_sample(sample_stem):
            feature_id = id(item.feature)
            if feature_id in seen_feature_ids:
                continue
            seen_feature_ids.add(feature_id)
            features.append(item.feature)
        return tuple(features)

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
        runtime_projections[sample_stem] = (
            _mint_backfill_sample_runtime_projection(
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
        )
        logical_request_offset += projection.logical_request_count
    return MappingProxyType(runtime_projections)


def validate_backfill_sample_runtime_projection(
    projection: BackfillSampleRuntimeProjection,
) -> None:
    validate_backfill_sample_runtime_projection_shape(projection)
    for feature in projection.features:
        validate_backfill_runtime_feature(feature)


def validate_backfill_sample_runtime_projection_shape(
    projection: BackfillSampleRuntimeProjection,
) -> None:
    """Validate the parent-safe envelope without rehashing every feature."""

    if not isinstance(projection, BackfillSampleRuntimeProjection):
        raise TypeError("Backfill sample runtime projection must be owner-minted")
    projection.__post_init__()


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
