from __future__ import annotations

from typing import Protocol, TypeAlias, TypedDict

from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner


class OwnerGroupDeliveryFeature(Protocol):
    @property
    def feature_id(self) -> str: ...

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
    def review_only(self) -> bool: ...

    @property
    def group_hypothesis_id(self) -> str: ...

    @property
    def group_construction_role(self) -> str: ...

    @property
    def group_delivery_role(self) -> str: ...

    @property
    def group_membership_source(self) -> str: ...


OwnerGroupDeliveryFeatures: TypeAlias = tuple[OwnerGroupDeliveryFeature, ...]


class GroupProjection(TypedDict):
    group_construction_role: str
    group_delivery_role: str
    group_membership_source: str


GROUP_REVIEW_PROJECTION_COLUMNS = (
    "group_construction_role",
    "group_delivery_role",
    "group_membership_source",
)

CROSS_SAMPLE_GROUP_CELL_COLUMNS = (
    "group_construction_role",
    "group_delivery_role",
    "group_membership_source",
    "gap_fill_state",
    "gap_fill_reason",
    "missing_observation_state",
)

GROUP_BACKFILL_SEED_AUDIT_COLUMNS = (
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
        or _string_attr(feature, "feature_id")
        or _string_attr(feature, "cluster_id")
    )


def delivery_feature_id(feature: object) -> str:
    return _string_attr(feature, "feature_id") or _string_attr(feature, "cluster_id")


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
        "group_construction_role": delivery_group_construction_role(feature),
        "group_delivery_role": delivery_group_delivery_role(feature),
        "group_membership_source": delivery_group_membership_source(feature),
    }


def _string_attr(value: object, name: str) -> str:
    attr = getattr(value, name, "")
    if attr is None:
        return ""
    return str(attr)
