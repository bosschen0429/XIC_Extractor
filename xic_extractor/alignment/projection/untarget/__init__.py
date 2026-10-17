"""Untargeted workflow projection contracts."""

from xic_extractor.alignment.projection.untarget.identity import (
    GroupCellSubject,
    UntargetProjectionContractError,
    UntargetRowKey,
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
    validate_untarget_matrix_projection,
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
    "UntargetRowKey",
    "UntargetRowRoute",
    "project_untarget_matrix",
    "validate_untarget_matrix_projection",
)
