"""Whether a group's members stand above the background bumps around them.

Each member carries its peak's background contrast: its height over the median
nearby bump of its channel. A row whose median member contrast is below the
floor is a slice of noise texture, however consistent its shapes, and does not
enter the Matrix. Members without a measured contrast take no part; quiet
surroundings count as infinite contrast. The floor is product policy chosen on
the 8RAW background rows and true peaks reviewed in #1283.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import numpy as np

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)

MIN_ROW_CONTRAST = 5.0


def row_contrast(group: CrossSamplePeakGroupHypothesis) -> float | None:
    """Median member contrast; ``None`` when no member carries one."""
    contrasts = [
        contrast
        for owner in group.owners
        if (contrast := owner.peak.background_contrast) is not None
    ]
    return float(np.median(contrasts)) if contrasts else None


def reject_low_contrast_groups(
    groups: Sequence[CrossSamplePeakGroupHypothesis],
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Reject established groups that do not stand above their background."""
    result = []
    for group in groups:
        contrast = row_contrast(group) if group.disposition == "established" else None
        if contrast is not None and contrast < MIN_ROW_CONTRAST:
            group = replace(
                group,
                disposition="rejected",
                evidence=f"{group.evidence};low_contrast:{contrast:.2f}",
            )
        result.append(group)
    return tuple(result)
