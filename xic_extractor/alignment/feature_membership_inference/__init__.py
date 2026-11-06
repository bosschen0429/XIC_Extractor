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

# Existing assignment regression tests consume these private entry points.
from .sample_assignment import _Candidate as _Candidate
from .sample_assignment import _solve_sample_assignment as _solve_sample_assignment
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
