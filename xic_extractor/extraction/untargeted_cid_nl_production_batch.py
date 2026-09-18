"""Backward-compatible facade for untargeted production batch execution."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.extraction.untargeted_production_batch import *  # noqa: F403
else:
    from xic_extractor.extraction import untargeted_production_batch

    sys.modules[__name__] = untargeted_production_batch

