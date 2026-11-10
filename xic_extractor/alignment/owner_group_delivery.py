from __future__ import annotations

GROUP_BACKFILL_SEED_AUDIT_COLUMNS = (
    "group_construction_role",
    "group_delivery_role",
    "group_membership_source",
    "gap_fill_state",
    "gap_fill_reason",
    "missing_observation_state",
)


def delivery_feature_id(feature: object) -> str:
    return _string_attr(feature, "feature_id") or _string_attr(feature, "cluster_id")


def _string_attr(value: object, name: str) -> str:
    attr = getattr(value, name, "")
    if attr is None:
        return ""
    return str(attr)
