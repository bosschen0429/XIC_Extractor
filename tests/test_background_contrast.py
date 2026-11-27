"""A row enters the Matrix only when it stands above its channel's background."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from tests.alignment_pipeline_helpers import _owner
from xic_extractor.alignment.cross_sample.peak_groups import (
    _hypothesis_from_owner_group,
)
from xic_extractor.alignment.row_contrast import reject_low_contrast_groups
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.peak_detection.background_contrast import background_contrast

_RT = np.round(np.arange(0.0, 20.0, 0.02), 4)


def _bumps(height: float) -> np.ndarray:
    """Noise texture: narrow bumps every 0.3 min."""
    return sum(
        height * np.exp(-0.5 * ((_RT - center) / 0.03) ** 2)
        for center in np.arange(0.15, 20.0, 0.3)
    )


def _peak(height: float) -> np.ndarray:
    return height * np.exp(-0.5 * ((_RT - 10.0) / 0.06) ** 2)


def _contrast(trace: np.ndarray) -> float | None:
    return background_contrast(
        _RT,
        trace,
        left_rt_min=9.8,
        right_rt_min=10.2,
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )


def test_peak_far_above_noise_texture_has_high_contrast() -> None:
    assert _contrast(_bumps(1e4) + _peak(1e6)) > 20.0


def test_bump_as_tall_as_its_neighbours_has_low_contrast() -> None:
    assert _contrast(_bumps(1e4) + _peak(1.5e4)) < 5.0


def test_quiet_surroundings_have_infinite_contrast() -> None:
    assert _contrast(_peak(1e4)) == math.inf


def _group(*contrasts: float | None):
    owners = []
    for index, contrast in enumerate(contrasts):
        owner = _owner(f"S{index}", "m", apex_rt=10.0, precursor_mz=300.0)
        owners.append(
            replace(
                owner,
                hypothesis=replace(
                    owner.hypothesis,
                    peak=replace(owner.peak, background_contrast=contrast),
                ),
            )
        )
    return _hypothesis_from_owner_group(
        tuple(owners), disposition="established", evidence=""
    )


def test_row_below_contrast_floor_is_rejected_and_quiet_rows_are_kept() -> None:
    low, quiet, unmeasured = reject_low_contrast_groups(
        (_group(2.0, 3.0, 40.0), _group(math.inf, 3.0, math.inf), _group(None, None))
    )

    assert low.disposition == "rejected"
    assert low.evidence.endswith("low_contrast:3.00")
    assert quiet.disposition == "established"
    assert unmeasured.disposition == "established"
