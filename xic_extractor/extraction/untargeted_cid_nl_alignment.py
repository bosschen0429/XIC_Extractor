"""Backward-compatible facade for untargeted alignment execution."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.extraction.untargeted_alignment import *  # noqa: F403
else:
    from xic_extractor.extraction import untargeted_alignment

    sys.modules[__name__] = untargeted_alignment

