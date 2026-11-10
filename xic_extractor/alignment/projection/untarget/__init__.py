"""Untargeted workflow projection contracts."""

from xic_extractor.alignment.projection.untarget.identity import (
    GroupCellSubject,
    UntargetProjectionContractError,
)
from xic_extractor.alignment.projection.untarget.matrix import (
    GroupCells,
    project_untarget_matrix,
)

__all__ = (
    "GroupCellSubject",
    "GroupCells",
    "UntargetProjectionContractError",
    "project_untarget_matrix",
)
