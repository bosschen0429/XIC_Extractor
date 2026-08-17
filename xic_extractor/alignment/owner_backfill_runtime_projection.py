"""Compact, owner-minted runtime inputs for Alignment Backfill workers."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from statistics import median
from typing import Protocol, TypeAlias, cast

_RUNTIME_FEATURE_ID_PREFIX = "xic.owner-backfill-runtime-feature.v1:"


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
    def confirm_local_owners_with_backfill(self) -> bool: ...

    @property
    def backfill_seed_centers(self) -> tuple[tuple[float, float], ...]: ...

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

    @property
    def consolidation_state(self) -> str: ...

    @property
    def consolidation_winner_group_hypothesis_id(self) -> str: ...

    @property
    def consolidation_source_group_hypothesis_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class OwnerBackfillOwnerEligibility:
    """Only the local owner facts needed to re-evaluate Backfill eligibility."""

    sample_stem: str
    owner_area: float | None

    def __post_init__(self) -> None:
        _required_text(self.sample_stem, "owner eligibility sample_stem")
        object.__setattr__(
            self,
            "owner_area",
            _validated_owner_area(
                self.owner_area,
                "owner eligibility owner_area",
            ),
        )


@dataclass(frozen=True, slots=True)
class OwnerBackfillRuntimeFeatureProjection:
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
    owner_eligibility: tuple[OwnerBackfillOwnerEligibility, ...]
    group_hypothesis_id: str
    public_family_id: str
    group_construction_role: str
    group_delivery_role: str
    group_membership_source: str
    consolidation_state: str
    consolidation_winner_group_hypothesis_id: str
    consolidation_source_group_hypothesis_id: str

    def __post_init__(self) -> None:
        _validate_runtime_feature_projection(self)


OwnerBackfillRuntimeFeatures: TypeAlias = tuple[
    OwnerBackfillRuntimeFeatureProjection,
    ...,
]


def mint_owner_backfill_runtime_feature(
    feature: BackfillRuntimeFeature,
) -> OwnerBackfillRuntimeFeatureProjection:
    """Mint the only worker-safe form of an Alignment Backfill feature."""

    owner_eligibility = tuple(
        OwnerBackfillOwnerEligibility(
            sample_stem=cast(str, getattr(owner, "sample_stem", None)),
            owner_area=_normalized_owner_area(getattr(owner, "owner_area", None)),
        )
        for owner in _full_feature_owners(feature)
    )
    basis = _runtime_feature_basis(
        feature=feature,
        owner_eligibility=owner_eligibility,
    )
    return OwnerBackfillRuntimeFeatureProjection(
        runtime_feature_id=_typed_id(_RUNTIME_FEATURE_ID_PREFIX, basis),
        feature_family_id=_required_text(
            feature.feature_family_id,
            "feature_family_id",
        ),
        neutral_loss_tag=_required_text(feature.neutral_loss_tag, "neutral_loss_tag"),
        family_center_mz=_positive_finite(
            feature.family_center_mz,
            "family_center_mz",
        ),
        family_center_rt=_finite(feature.family_center_rt, "family_center_rt"),
        family_product_mz=_positive_finite(
            feature.family_product_mz,
            "family_product_mz",
        ),
        family_observed_neutral_loss_da=_finite(
            feature.family_observed_neutral_loss_da,
            "family_observed_neutral_loss_da",
        ),
        review_only=_required_bool(feature.review_only, "review_only"),
        confirm_local_owners_with_backfill=_required_bool(
            feature.confirm_local_owners_with_backfill,
            "confirm_local_owners_with_backfill",
        ),
        backfill_seed_centers=_seed_centers(feature.backfill_seed_centers),
        owner_eligibility=owner_eligibility,
        group_hypothesis_id=_required_text(
            feature.group_hypothesis_id,
            "group_hypothesis_id",
        ),
        public_family_id=_required_text(feature.public_family_id, "public_family_id"),
        group_construction_role=_required_text(
            feature.group_construction_role,
            "group_construction_role",
        ),
        group_delivery_role=_required_text(
            feature.group_delivery_role,
            "group_delivery_role",
        ),
        group_membership_source=_required_text(
            feature.group_membership_source,
            "group_membership_source",
        ),
        consolidation_state=_required_text(
            feature.consolidation_state,
            "consolidation_state",
        ),
        consolidation_winner_group_hypothesis_id=_optional_text(
            feature.consolidation_winner_group_hypothesis_id,
            "consolidation_winner_group_hypothesis_id",
        ),
        consolidation_source_group_hypothesis_id=_optional_text(
            feature.consolidation_source_group_hypothesis_id,
            "consolidation_source_group_hypothesis_id",
        ),
    )


def validate_owner_backfill_runtime_feature(
    feature: OwnerBackfillRuntimeFeatureProjection,
) -> None:
    if not isinstance(feature, OwnerBackfillRuntimeFeatureProjection):
        raise TypeError("Backfill runtime feature must be owner-minted projection")
    _validate_runtime_feature_projection(feature)


def backfill_runtime_owner_eligibility(
    feature: BackfillRuntimeFeature,
) -> tuple[OwnerBackfillOwnerEligibility, ...]:
    """Expose eligibility without synthesizing domain owners in a worker."""

    if isinstance(feature, OwnerBackfillRuntimeFeatureProjection):
        validate_owner_backfill_runtime_feature(feature)
        return feature.owner_eligibility
    return tuple(
        OwnerBackfillOwnerEligibility(
            sample_stem=cast(str, getattr(owner, "sample_stem", None)),
            owner_area=_normalized_owner_area(getattr(owner, "owner_area", None)),
        )
        for owner in _full_feature_owners(feature)
    )


def median_backfill_runtime_owner_area(feature: BackfillRuntimeFeature) -> float | None:
    areas = tuple(
        item.owner_area
        for item in backfill_runtime_owner_eligibility(feature)
        if item.owner_area is not None
    )
    return float(median(areas)) if areas else None


def _validate_runtime_feature_projection(
    feature: OwnerBackfillRuntimeFeatureProjection,
) -> None:
    feature_family_id = _required_text(feature.feature_family_id, "feature_family_id")
    _required_text(feature.neutral_loss_tag, "neutral_loss_tag")
    _positive_finite(feature.family_center_mz, "family_center_mz")
    _finite(feature.family_center_rt, "family_center_rt")
    _positive_finite(feature.family_product_mz, "family_product_mz")
    _finite(feature.family_observed_neutral_loss_da, "family_observed_neutral_loss_da")
    _required_bool(feature.review_only, "review_only")
    _required_bool(
        feature.confirm_local_owners_with_backfill,
        "confirm_local_owners_with_backfill",
    )
    _seed_centers(feature.backfill_seed_centers)
    if (
        not isinstance(feature.owner_eligibility, tuple)
        or not feature.owner_eligibility
    ):
        raise ValueError("owner eligibility must be a non-empty tuple")
    if any(
        not isinstance(item, OwnerBackfillOwnerEligibility)
        for item in feature.owner_eligibility
    ):
        raise TypeError("owner eligibility must contain compact owner facts")
    for item in feature.owner_eligibility:
        _required_text(item.sample_stem, "owner eligibility sample_stem")
        _validated_owner_area(item.owner_area, "owner eligibility owner_area")
    _required_text(feature.group_hypothesis_id, "group_hypothesis_id")
    _required_text(feature.public_family_id, "public_family_id")
    _required_text(feature.group_construction_role, "group_construction_role")
    _required_text(feature.group_delivery_role, "group_delivery_role")
    _required_text(feature.group_membership_source, "group_membership_source")
    _required_text(feature.consolidation_state, "consolidation_state")
    _optional_text(
        feature.consolidation_winner_group_hypothesis_id,
        "consolidation_winner_group_hypothesis_id",
    )
    _optional_text(
        feature.consolidation_source_group_hypothesis_id,
        "consolidation_source_group_hypothesis_id",
    )
    expected = _typed_id(
        _RUNTIME_FEATURE_ID_PREFIX,
        _runtime_feature_basis(
            feature=feature,
            owner_eligibility=feature.owner_eligibility,
        ),
    )
    if feature.runtime_feature_id != expected:
        raise ValueError(
            "runtime_feature_id mismatches complete Backfill feature semantics"
        )
    if feature_family_id != feature.feature_family_id:
        raise AssertionError("required text validation changed feature identity")


def _runtime_feature_basis(
    *,
    feature: BackfillRuntimeFeature,
    owner_eligibility: Iterable[OwnerBackfillOwnerEligibility],
) -> Mapping[str, object]:
    return {
        "schema": "owner_backfill_runtime_feature_v1",
        "feature_family_id": _required_text(
            feature.feature_family_id, "feature_family_id"
        ),
        "neutral_loss_tag": _required_text(
            feature.neutral_loss_tag, "neutral_loss_tag"
        ),
        "family_center_mz": _positive_finite(
            feature.family_center_mz, "family_center_mz"
        ),
        "family_center_rt": _finite(feature.family_center_rt, "family_center_rt"),
        "family_product_mz": _positive_finite(
            feature.family_product_mz,
            "family_product_mz",
        ),
        "family_observed_neutral_loss_da": _finite(
            feature.family_observed_neutral_loss_da,
            "family_observed_neutral_loss_da",
        ),
        "review_only": _required_bool(feature.review_only, "review_only"),
        "confirm_local_owners_with_backfill": _required_bool(
            feature.confirm_local_owners_with_backfill,
            "confirm_local_owners_with_backfill",
        ),
        "backfill_seed_centers": _seed_centers(feature.backfill_seed_centers),
        "owner_eligibility": tuple(
            {
                "sample_stem": _required_text(
                    item.sample_stem,
                    "owner eligibility sample_stem",
                ),
                "owner_area": _validated_owner_area(
                    item.owner_area,
                    "owner eligibility owner_area",
                ),
            }
            for item in owner_eligibility
        ),
        "group_hypothesis_id": _required_text(
            feature.group_hypothesis_id,
            "group_hypothesis_id",
        ),
        "public_family_id": _required_text(
            feature.public_family_id, "public_family_id"
        ),
        "group_construction_role": _required_text(
            feature.group_construction_role,
            "group_construction_role",
        ),
        "group_delivery_role": _required_text(
            feature.group_delivery_role,
            "group_delivery_role",
        ),
        "group_membership_source": _required_text(
            feature.group_membership_source,
            "group_membership_source",
        ),
        "consolidation_state": _required_text(
            feature.consolidation_state,
            "consolidation_state",
        ),
        "consolidation_winner_group_hypothesis_id": _optional_text(
            feature.consolidation_winner_group_hypothesis_id,
            "consolidation_winner_group_hypothesis_id",
        ),
        "consolidation_source_group_hypothesis_id": _optional_text(
            feature.consolidation_source_group_hypothesis_id,
            "consolidation_source_group_hypothesis_id",
        ),
    }


def _full_feature_owners(feature: object) -> tuple[object, ...]:
    owners = getattr(feature, "owners")
    if not isinstance(owners, tuple) or not owners:
        raise TypeError("Backfill runtime projection requires non-empty owner tuple")
    return owners


def _seed_centers(value: object) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, tuple):
        raise TypeError("backfill_seed_centers must be a tuple")
    centers: list[tuple[float, float]] = []
    for index, center in enumerate(value):
        if not isinstance(center, tuple) or len(center) != 2:
            raise TypeError("backfill seed center must be an (mz, rt) tuple")
        centers.append(
            (
                _positive_finite(center[0], f"backfill_seed_centers[{index}].mz"),
                _finite(center[1], f"backfill_seed_centers[{index}].rt"),
            )
        )
    return tuple(centers)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty owner-normalized text")
    return value


def _optional_text(value: object, field: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError(f"{field} must be owner-normalized text")
    return value


def _required_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be bool")
    return value


def _positive_finite(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field} must be finite and positive")
    return float(value)


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
