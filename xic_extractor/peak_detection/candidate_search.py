from __future__ import annotations

from collections.abc import Callable

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.local_minimum import (
    find_peak_candidates_local_minimum,
)
from xic_extractor.peak_detection.models import PeakCandidatesResult
from xic_extractor.peak_detection.savgol import (
    find_peak_candidates_savgol,
)
from xic_extractor.settings_schema import (
    ARBITRATED_RESOLVER_RETIRED_MESSAGE,
    CANONICAL_RESOLVER_MODE,
    RESOLVER_MODES,
)


def find_peak_candidates(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
    *,
    peak_min_prominence_ratio: float | None = None,
    local_minimum_finder: Callable[
        [np.ndarray, np.ndarray, ExtractionConfig],
        PeakCandidatesResult,
    ] = find_peak_candidates_local_minimum,
    savgol_finder: Callable[..., PeakCandidatesResult] = (
        find_peak_candidates_savgol
    ),
) -> PeakCandidatesResult:
    resolver_mode = getattr(config, "resolver_mode", CANONICAL_RESOLVER_MODE)
    if resolver_mode == "local_minimum":
        return local_minimum_finder(rt, intensity, config)
    if resolver_mode == "arbitrated":
        raise ValueError(ARBITRATED_RESOLVER_RETIRED_MESSAGE)
    if resolver_mode == "savgol":
        return savgol_finder(
            rt,
            intensity,
            config,
            peak_min_prominence_ratio=peak_min_prominence_ratio,
        )
    allowed = ", ".join(RESOLVER_MODES[:-1]) + f", or {RESOLVER_MODES[-1]}"
    raise ValueError(f"unsupported resolver mode {resolver_mode!r}; must be {allowed}")

