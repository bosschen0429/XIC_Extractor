"""Untargeted workflow projection contracts."""

from xic_extractor.alignment.projection.untarget.identity import (
    GroupCellSubject,
    UntargetProjectionContractError,
)
from xic_extractor.alignment.projection.untarget.matrix import (
    UntargetDecisionVariant,
    UntargetDecisionVariantCount,
    UntargetMatrixProjection,
    UntargetMatrixRow,
    UntargetRowDecisionCounts,
    UntargetRowDestination,
    UntargetRowRoute,
    project_untarget_matrix,
)

__all__ = (
    "GroupCellSubject",
    "UntargetDecisionVariant",
    "UntargetDecisionVariantCount",
    "UntargetMatrixProjection",
    "UntargetMatrixRow",
    "UntargetProjectionContractError",
    "UntargetRowDecisionCounts",
    "UntargetRowDestination",
    "UntargetRowRoute",
    "project_untarget_matrix",
)
