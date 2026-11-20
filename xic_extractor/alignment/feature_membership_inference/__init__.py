"""Infer feature memberships through the stable Alignment import surface."""

from .inference import infer_feature_memberships
from .models import (
    BatchRtWarp,
    FeatureInferenceOperationCounts,
    FeatureMembershipGroup,
    FeatureMembershipInferenceResult,
    KnownIdentityMembershipConstraint,
    SparseFeatureDecision,
)
from .models import FeatureMembershipBasis as FeatureMembershipBasis
from .window_bands import rt_trend_supports_union

__all__ = [
    "BatchRtWarp",
    "FeatureInferenceOperationCounts",
    "FeatureMembershipGroup",
    "FeatureMembershipInferenceResult",
    "KnownIdentityMembershipConstraint",
    "SparseFeatureDecision",
    "infer_feature_memberships",
    "rt_trend_supports_union",
]
