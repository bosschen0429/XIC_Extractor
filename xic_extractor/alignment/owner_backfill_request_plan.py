from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, NamedTuple

from xic_extractor.alignment.backfill_scope import (
    backfill_request_sample_stems,
    backfill_seed_centers,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.ms1_index_source import OwnerBackfillXicBackend
from xic_extractor.alignment.owner_group_delivery import (
    OwnerGroupDeliveryFeature,
    OwnerGroupDeliveryFeatures,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.xic_models import XICRequest

OwnerBackfillWindowStrategy = Literal["exact", "super-window"]
OWNER_BACKFILL_WINDOW_STRATEGIES: tuple[OwnerBackfillWindowStrategy, ...] = (
    "exact",
    "super-window",
)
OwnerBackfillValidationMode = Literal["primary", "prefilter", "validation"]
OWNER_BACKFILL_VALIDATION_MODES: tuple[OwnerBackfillValidationMode, ...] = (
    "primary",
    "prefilter",
    "validation",
)
OWNER_BACKFILL_XIC_BACKENDS: tuple[OwnerBackfillXicBackend, ...] = (
    "raw",
    "ms1_index",
    "ms1_index_hybrid",
)
OWNER_BACKFILL_TRACE_RESULT_SEMANTICS = "owner_backfill_xic_trace_v1"
_REQUEST_IDENTITY_PREFIX = "xic.owner-backfill-request.v1:"
_LOGICAL_REQUEST_PREFIX = "xic.owner-backfill-logical-request.v1:"


class OwnerBackfillRequestItem(NamedTuple):
    feature: OwnerGroupDeliveryFeature
    sample_stem: str
    request: XICRequest
    preferred_rt: float


@dataclass(frozen=True, slots=True)
class OwnerBackfillRequestEquivalenceContext:
    acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    backend: OwnerBackfillXicBackend
    window_strategy: OwnerBackfillWindowStrategy
    superwindow_span_factor: int
    validation_mode: OwnerBackfillValidationMode
    result_semantics: str = OWNER_BACKFILL_TRACE_RESULT_SEMANTICS

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
        if self.backend not in OWNER_BACKFILL_XIC_BACKENDS:
            raise ValueError("unsupported owner Backfill XIC backend")
        if self.window_strategy not in OWNER_BACKFILL_WINDOW_STRATEGIES:
            raise ValueError("unsupported owner Backfill window strategy")
        if (
            isinstance(self.superwindow_span_factor, bool)
            or not isinstance(self.superwindow_span_factor, int)
            or self.superwindow_span_factor < 1
        ):
            raise ValueError("superwindow_span_factor must be a positive integer")
        if self.validation_mode not in OWNER_BACKFILL_VALIDATION_MODES:
            raise ValueError("unsupported owner Backfill validation mode")
        if self.result_semantics != OWNER_BACKFILL_TRACE_RESULT_SEMANTICS:
            raise ValueError("unsupported owner Backfill result semantics")


@dataclass(frozen=True, slots=True)
class OwnerBackfillRequestIdentity:
    request_identity_id: str
    sample_stem: str
    acquisition_source: AcquisitionSourceIdentity
    request: XICRequest
    backend: OwnerBackfillXicBackend
    window_strategy: OwnerBackfillWindowStrategy
    superwindow_span_factor: int
    validation_mode: OwnerBackfillValidationMode
    result_semantics: str

    def __post_init__(self) -> None:
        sample_stem = _required_text(self.sample_stem, "sample_stem")
        if not isinstance(self.acquisition_source, AcquisitionSourceIdentity):
            raise TypeError("acquisition_source must be an owner-minted identity")
        if not isinstance(self.request, XICRequest):
            raise TypeError("request must be an XICRequest")
        _validate_request_coordinates(self.request)
        context = OwnerBackfillRequestEquivalenceContext(
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
class OwnerBackfillLogicalRequestMember:
    logical_request_id: str
    logical_index: int
    group_hypothesis_id: str
    item: OwnerBackfillRequestItem

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
        if not isinstance(self.item, OwnerBackfillRequestItem):
            raise TypeError("item must be an OwnerBackfillRequestItem")
        if group_hypothesis_id != _required_text(
            getattr(self.item.feature, "group_hypothesis_id", None),
            "feature.group_hypothesis_id",
        ):
            raise ValueError("group_hypothesis_id mismatches the owner feature")
        expected = _typed_id(
            _LOGICAL_REQUEST_PREFIX,
            {
                "schema": "owner_backfill_logical_request_v1",
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
class OwnerBackfillRequestEquivalenceClass:
    identity: OwnerBackfillRequestIdentity
    members: tuple[OwnerBackfillLogicalRequestMember, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.identity, OwnerBackfillRequestIdentity):
            raise TypeError("identity must be an OwnerBackfillRequestIdentity")
        if not isinstance(self.members, tuple) or not self.members:
            raise ValueError("equivalence class members must be a non-empty tuple")
        indices = tuple(member.logical_index for member in self.members)
        if indices != tuple(sorted(indices)) or len(set(indices)) != len(indices):
            raise ValueError(
                "equivalence class logical indices must be unique and ordered"
            )
        for member in self.members:
            if not isinstance(member, OwnerBackfillLogicalRequestMember):
                raise TypeError("equivalence class contains an invalid member")
            if member.item.sample_stem != self.identity.sample_stem:
                raise ValueError("equivalence class crosses canonical samples")
            if member.item.request != self.identity.request:
                raise ValueError("equivalence class crosses XIC request semantics")

    @property
    def class_id(self) -> str:
        return self.identity.request_identity_id


@dataclass(frozen=True)
class OwnerBackfillRequestPlan:
    requests_by_sample: Mapping[str, tuple[OwnerBackfillRequestItem, ...]]
    equivalence_classes_by_sample: Mapping[
        str,
        tuple[OwnerBackfillRequestEquivalenceClass, ...],
    ] | None = None
    equivalence_context: OwnerBackfillRequestEquivalenceContext | None = None
    equivalence_unavailable_reason: str = "owner_equivalence_context_not_supplied"

    def __post_init__(self) -> None:
        if not isinstance(self.requests_by_sample, Mapping):
            raise TypeError("requests_by_sample must be a mapping")
        requests_by_sample = dict(self.requests_by_sample)
        for sample_stem, requests in requests_by_sample.items():
            _required_text(sample_stem, "sample_stem")
            if not isinstance(requests, tuple) or any(
                not isinstance(item, OwnerBackfillRequestItem) for item in requests
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
            OwnerBackfillRequestEquivalenceContext,
        ):
            raise ValueError(
                "available equivalence partition requires its owner context"
            )
        if not isinstance(self.equivalence_classes_by_sample, Mapping):
            raise TypeError("equivalence_classes_by_sample must be a mapping")
        equivalence_classes_by_sample = dict(
            self.equivalence_classes_by_sample
        )
        for classes in equivalence_classes_by_sample.values():
            if not isinstance(classes, tuple) or any(
                not isinstance(item, OwnerBackfillRequestEquivalenceClass)
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
                source = self.equivalence_context.acquisition_sources.get(
                    sample_stem
                )
                if not isinstance(source, AcquisitionSourceIdentity):
                    raise ValueError(
                        "equivalence context does not bind every request sample"
                    )
                expected_identity = mint_owner_backfill_request_identity(
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
    ) -> tuple[OwnerBackfillRequestItem, ...]:
        return self.requests_by_sample.get(sample_stem, ())

    def features_for_sample(
        self,
        sample_stem: str,
    ) -> OwnerGroupDeliveryFeatures:
        features: list[OwnerGroupDeliveryFeature] = []
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
    ) -> tuple[OwnerBackfillRequestEquivalenceClass, ...]:
        if self.equivalence_classes_by_sample is None:
            raise ValueError(
                "exact request equivalence is unavailable: "
                f"{self.equivalence_unavailable_reason}"
            )
        return self.equivalence_classes_by_sample.get(sample_stem, ())

    def canonical_class_count_for_sample(self, sample_stem: str) -> int:
        return len(self.equivalence_classes_for_sample(sample_stem))


def build_owner_backfill_request_plan(
    features: OwnerGroupDeliveryFeatures,
    *,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
    equivalence_context: OwnerBackfillRequestEquivalenceContext | None = None,
) -> OwnerBackfillRequestPlan:
    pending: dict[str, list[OwnerBackfillRequestItem]] = defaultdict(list)
    rt_window_min = alignment_config.max_rt_sec / 60.0
    for feature in features:
        seed_centers = backfill_seed_centers(feature)
        for sample_stem in backfill_request_sample_stems(
            feature,
            sample_order=sample_order,
            raw_sample_stems=raw_sample_stems,
            alignment_config=alignment_config,
        ):
            for seed_mz, seed_rt in seed_centers:
                pending[sample_stem].append(
                    OwnerBackfillRequestItem(
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
        return OwnerBackfillRequestPlan(requests_by_sample=requests_by_sample)
    classes_by_sample = {
        sample_stem: build_owner_backfill_request_equivalence_classes(
            sample_requests,
            context=equivalence_context,
        )
        for sample_stem, sample_requests in requests_by_sample.items()
    }
    return OwnerBackfillRequestPlan(
        requests_by_sample=requests_by_sample,
        equivalence_classes_by_sample=classes_by_sample,
        equivalence_context=equivalence_context,
        equivalence_unavailable_reason="",
    )


def build_owner_backfill_request_equivalence_classes(
    items: tuple[OwnerBackfillRequestItem, ...],
    *,
    context: OwnerBackfillRequestEquivalenceContext,
) -> tuple[OwnerBackfillRequestEquivalenceClass, ...]:
    if not isinstance(context, OwnerBackfillRequestEquivalenceContext):
        raise TypeError("context must be an OwnerBackfillRequestEquivalenceContext")
    if not isinstance(items, tuple) or any(
        not isinstance(item, OwnerBackfillRequestItem) for item in items
    ):
        raise TypeError("items must be an OwnerBackfillRequestItem tuple")
    pending: dict[
        OwnerBackfillRequestIdentity,
        list[OwnerBackfillLogicalRequestMember],
    ] = {}
    for logical_index, item in enumerate(items):
        source = context.acquisition_sources.get(item.sample_stem)
        if not isinstance(source, AcquisitionSourceIdentity):
            raise ValueError(
                "exact request equivalence requires one owner-minted acquisition "
                f"identity for {item.sample_stem}"
            )
        identity = mint_owner_backfill_request_identity(
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
        member = mint_owner_backfill_logical_request_member(
            logical_index=logical_index,
            group_hypothesis_id=group_hypothesis_id,
            item=item,
        )
        pending.setdefault(identity, []).append(member)
    return tuple(
        OwnerBackfillRequestEquivalenceClass(
            identity=identity,
            members=tuple(members),
        )
        for identity, members in pending.items()
    )


def mint_owner_backfill_request_identity(
    *,
    sample_stem: str,
    acquisition_source: AcquisitionSourceIdentity,
    request: XICRequest,
    backend: OwnerBackfillXicBackend,
    window_strategy: OwnerBackfillWindowStrategy,
    superwindow_span_factor: int,
    validation_mode: OwnerBackfillValidationMode,
    result_semantics: str = OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
) -> OwnerBackfillRequestIdentity:
    context = OwnerBackfillRequestEquivalenceContext(
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
    return OwnerBackfillRequestIdentity(
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


def mint_owner_backfill_logical_request_member(
    *,
    logical_index: int,
    group_hypothesis_id: str,
    item: OwnerBackfillRequestItem,
) -> OwnerBackfillLogicalRequestMember:
    basis = {
        "schema": "owner_backfill_logical_request_v1",
        "logical_index": logical_index,
        "group_hypothesis_id": group_hypothesis_id,
        "sample_stem": item.sample_stem,
        "request": _request_basis(item.request),
        "preferred_rt": item.preferred_rt,
    }
    return OwnerBackfillLogicalRequestMember(
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
    context: OwnerBackfillRequestEquivalenceContext,
) -> dict[str, object]:
    return {
        "schema": "owner_backfill_request_identity_v1",
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
