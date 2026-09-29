from __future__ import annotations

from typing import Protocol, TypeAlias, TypedDict

from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner


class OwnerGroupDeliveryFeature(Protocol):
    @property
    def feature_family_id(self) -> str: ...

    @property
    def cluster_id(self) -> str: ...

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
    def has_anchor(self) -> bool: ...

    @property
    def owners(self) -> tuple[SampleLocalMS1Owner, ...]: ...

    @property
    def members(self) -> tuple[SampleLocalMS1Owner, ...]: ...

    @property
    def event_cluster_ids(self) -> tuple[str, ...]: ...

    @property
    def event_member_count(self) -> int: ...

    @property
    def evidence(self) -> str: ...

    @property
    def identity_conflict(self) -> bool: ...

    @property
    def review_only(self) -> bool: ...

    @property
    def confirm_local_owners_with_backfill(self) -> bool: ...

    @property
    def backfill_seed_centers(self) -> tuple[tuple[float, float], ...]: ...

    @property
    def ambiguous_sample_stem(self) -> str | None: ...

    @property
    def ambiguous_candidate_ids(self) -> tuple[str, ...]: ...

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


OwnerGroupDeliveryFeatures: TypeAlias = tuple[OwnerGroupDeliveryFeature, ...]


class GroupProjection(TypedDict):
    group_hypothesis_id: str
    public_family_id: str
    group_construction_role: str
    group_delivery_role: str
    group_membership_source: str


class CellGroupProjection(GroupProjection):
    gap_fill_state: str
    gap_fill_reason: str
    missing_observation_state: str

GROUP_REVIEW_PROJECTION_COLUMNS = (
    "group_hypothesis_id",
    "public_family_id",
    "group_construction_role",
    "group_delivery_role",
    "group_membership_source",
)

CROSS_SAMPLE_GROUP_CELL_COLUMNS = (
    "group_hypothesis_id",
    "public_family_id",
    "group_construction_role",
    "group_delivery_role",
    "group_membership_source",
    "gap_fill_state",
    "gap_fill_reason",
    "missing_observation_state",
)

GROUP_BACKFILL_SEED_AUDIT_COLUMNS = (
    "group_hypothesis_id",
    "public_family_id",
    "group_construction_role",
    "group_delivery_role",
    "group_membership_source",
    "gap_fill_state",
    "gap_fill_reason",
    "missing_observation_state",
)


def delivery_group_hypothesis_id(feature: object) -> str:
    return (
        _string_attr(feature, "group_hypothesis_id")
        or _string_attr(feature, "feature_family_id")
        or _string_attr(feature, "cluster_id")
    )


def delivery_public_family_id(feature: object) -> str:
    return (
        _string_attr(feature, "public_family_id")
        or _string_attr(feature, "feature_family_id")
        or _string_attr(feature, "cluster_id")
    )


def delivery_group_construction_role(feature: object) -> str:
    return _string_attr(feature, "group_construction_role") or "successor_constructor"


def delivery_group_delivery_role(feature: object) -> str:
    return _string_attr(feature, "group_delivery_role") or (
        "owner_aligned_feature_compatibility_facade"
    )


def delivery_group_membership_source(feature: object) -> str:
    return _string_attr(feature, "group_membership_source") or (
        "owner_aligned_feature_successor_projection"
    )


def delivery_group_projection(feature: object) -> GroupProjection:
    return {
        "group_hypothesis_id": delivery_group_hypothesis_id(feature),
        "public_family_id": delivery_public_family_id(feature),
        "group_construction_role": delivery_group_construction_role(feature),
        "group_delivery_role": delivery_group_delivery_role(feature),
        "group_membership_source": delivery_group_membership_source(feature),
    }


def delivery_cell_projection(
    feature: object,
    *,
    gap_fill_state: str,
    gap_fill_reason: str,
    missing_observation_state: str,
) -> CellGroupProjection:
    return delivery_cell_projection_from_group(
        delivery_group_projection(feature),
        gap_fill_state=gap_fill_state,
        gap_fill_reason=gap_fill_reason,
        missing_observation_state=missing_observation_state,
    )


def delivery_cell_projection_from_group(
    group_projection: GroupProjection,
    *,
    gap_fill_state: str,
    gap_fill_reason: str,
    missing_observation_state: str,
) -> CellGroupProjection:
    return {
        **group_projection,
        "gap_fill_state": gap_fill_state,
        "gap_fill_reason": gap_fill_reason,
        "missing_observation_state": missing_observation_state,
    }


def _string_attr(value: object, name: str) -> str:
    attr = getattr(value, name, "")
    if attr is None:
        return ""
    return str(attr)
