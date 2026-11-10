from __future__ import annotations


def delivery_feature_id(feature: object) -> str:
    return _string_attr(feature, "feature_id") or _string_attr(feature, "cluster_id")


def _string_attr(value: object, name: str) -> str:
    attr = getattr(value, name, "")
    if attr is None:
        return ""
    return str(attr)
