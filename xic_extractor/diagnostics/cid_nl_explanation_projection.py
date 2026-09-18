"""Backward-compatible facade for alignment explanation projection."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.diagnostics.alignment_explanation_projection import *  # noqa: F403
else:
    from xic_extractor.diagnostics import alignment_explanation_projection

    sys.modules[__name__] = alignment_explanation_projection

