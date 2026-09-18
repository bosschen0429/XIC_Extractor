"""Backward-compatible facade for untargeted real-data runtime execution."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.extraction.untargeted_real_data_runtime import *  # noqa: F403
else:
    from xic_extractor.extraction import untargeted_real_data_runtime

    sys.modules[__name__] = untargeted_real_data_runtime
