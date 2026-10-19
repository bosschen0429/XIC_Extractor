"""Compact, owner-minted runtime inputs for Backfill workers."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
)

_RUNTIME_FEATURE_ID_PREFIX = "xic.owner-backfill-runtime-feature:"


class BackfillRuntimeFeature(Protocol):
    """The Backfill-specific feature view consumed after process dispatch."""

    @property
    def feature_family_id(self) -> str: ...

    @property
    def neutral_loss_tag(self) -> str: ...

    @property
    def family_center_mz(self) -> float: ...

    @property
    def family_center_rt(self) -> float: ...

    @property
    def family_product_mz(self) -> float: ...

    @property
    def family_observed_neutral_loss_da(self) -> float: ...

    @property
    def review_only(self) -> bool: ...

    @property
    def group_hypothesis_id(self) -> str: ...

    @property
    def public_family_id(self) -> str: ...

    @property
    def group_construction_role(self) -> str: ...

    @property
    def group_delivery_role(self) -> str: ...

    @property
    def group_membership_source(self) -> str: ...


@dataclass(frozen=True, slots=True)
class BackfillOwnerEligibility:
    """Only the local owner facts needed to re-evaluate Backfill eligibility."""

    sample_stem: str
    owner_area: float | None
    quantification_eligible: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "owner_area",
            _validated_owner_area(self.owner_area, "owner eligibility owner_area"),
        )


@dataclass(frozen=True, slots=True)
class BackfillRuntimeFeatureProjection:
    """Sealed feature projection; never recreates owner or event domain objects."""

    runtime_feature_id: str
    feature_family_id: str
    neutral_loss_tag: str
    family_center_mz: float
    family_center_rt: float
    family_product_mz: float
    family_observed_neutral_loss_da: float
    review_only: bool
    confirm_local_owners_with_backfill: bool
    backfill_seed_centers: tuple[tuple[float, float], ...]
    owner_eligibility: tuple[BackfillOwnerEligibility, ...]
    group_hypothesis_id: str
    public_family_id: str
    group_construction_role: str
    group_delivery_role: str
    group_membership_source: str
    input_group_identity: CrossSampleGroupIdentity | None = None


BackfillRuntimeFeatures: TypeAlias = tuple[
    BackfillRuntimeFeatureProjection,
    ...,
]


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty owner-normalized text")
    return value


def _validated_owner_area(value: object, field: str) -> float | None:
    if value is None:
        return None
    normalized = _normalized_owner_area(value)
    if normalized is None:
        raise ValueError(f"{field} must be None or finite and positive")
    return normalized


def _normalized_owner_area(value: object) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        return None
    return float(value)


def _finite(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{field} must be finite")
    return float(value)


def _typed_id(prefix: str, basis: Mapping[str, object]) -> str:
    payload = json.dumps(
        basis,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return prefix + hashlib.sha256(payload).hexdigest()
