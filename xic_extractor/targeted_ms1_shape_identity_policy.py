"""Targeted MS1 shape identity activation policy constants."""

from __future__ import annotations

EXPLICIT_SUPPORT_TSV_POLICY = "explicit_support_tsv"
LIMITED_HMDC_MEDC_POLICY = "limited_5hmdc_5medc"
TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES = (
    EXPLICIT_SUPPORT_TSV_POLICY,
    LIMITED_HMDC_MEDC_POLICY,
)
LIMITED_HMDC_MEDC_TARGETS = frozenset({"5-hmdC", "5-medC"})


def require_supported_activation_policy(policy: str) -> str:
    if policy not in TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES:
        allowed = ", ".join(TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES)
        raise ValueError(
            "unsupported targeted MS1 shape identity activation policy "
            f"{policy!r}; must be {allowed}",
        )
    return policy


def target_allowed_by_activation_policy(policy: str, target_name: str) -> bool:
    policy = require_supported_activation_policy(policy)
    if policy == EXPLICIT_SUPPORT_TSV_POLICY:
        return True
    return target_name in LIMITED_HMDC_MEDC_TARGETS
