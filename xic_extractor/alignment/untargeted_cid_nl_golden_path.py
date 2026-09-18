"""Backward-compatible facade for untargeted golden path."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.alignment.untargeted_golden_path import *  # noqa: F403
else:
    from xic_extractor.alignment import untargeted_golden_path

    sys.modules[__name__] = untargeted_golden_path

