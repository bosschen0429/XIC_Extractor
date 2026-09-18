"""Backward-compatible facade for alignment explanation diagnostics."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.diagnostics.alignment_explanation import *  # noqa: F403
else:
    from xic_extractor.diagnostics import alignment_explanation

    sys.modules[__name__] = alignment_explanation

