import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.integration import raw_apex_index
from xic_extractor.peak_detection.models import (
    LocalMinimumQualityFlag,
    LocalMinimumRegionQuality,
)

TRACE_CONTINUITY_MIN_SCORE: float = 0.70
TRACE_CONTINUITY_SIGNIFICANT_STEP_FRACTION: float = 0.05


def local_minimum_region_quality(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    *,
    left: int,
    right: int,
    max_intensity: float,
    config: ExtractionConfig,
) -> LocalMinimumRegionQuality | None:
    scan_count = right - left
    if scan_count < 2:
        return None
    duration = float(rt_values[right - 1] - rt_values[left])
    if duration <= 0:
        return None

    apex_idx = raw_apex_index(intensity_values, left, right)
    apex_intensity = float(intensity_values[apex_idx])
    if not passes_local_peak_height_filters(apex_intensity, max_intensity, config):
        return None

    edge_height = max(float(intensity_values[left]), float(intensity_values[right - 1]))
    edge_ratio = None if edge_height <= 0 else float(apex_intensity / edge_height)
    trace_continuity = trace_continuity_score(
        intensity_values,
        left=left,
        right=right,
    )
    flags: list[LocalMinimumQualityFlag] = []
    if left == 0 or right == len(intensity_values):
        flags.append("edge_clipped")
    if scan_count < config.resolver_min_scans:
        flags.append("low_scan_support")
    if duration < config.resolver_peak_duration_min:
        flags.append("too_short")
    if duration > config.resolver_peak_duration_max:
        flags.append("too_broad")
    if edge_ratio is not None and edge_ratio < config.resolver_min_ratio_top_edge:
        flags.append("poor_edge_recovery")
    if trace_continuity is not None and trace_continuity < TRACE_CONTINUITY_MIN_SCORE:
        flags.append("low_trace_continuity")
    return LocalMinimumRegionQuality(
        flags=tuple(flags),
        scan_count=scan_count,
        duration_min=duration,
        edge_ratio=edge_ratio,
        trace_continuity=trace_continuity,
    )


def trace_continuity_score(
    intensity_values: np.ndarray,
    *,
    left: int,
    right: int,
) -> float | None:
    region = np.asarray(intensity_values[left:right], dtype=float)
    if len(region) < 5:
        return None
    apex = float(np.max(region))
    edge = max(float(region[0]), float(region[-1]))
    dynamic_range = apex - edge
    if dynamic_range <= 0:
        return 0.0

    diffs = np.diff(region)
    significant = np.abs(diffs) >= (
        dynamic_range * TRACE_CONTINUITY_SIGNIFICANT_STEP_FRACTION
    )
    signs = np.sign(diffs[significant])
    if len(signs) <= 2:
        return 1.0
    sign_changes = int(np.count_nonzero(signs[1:] != signs[:-1]))
    return max(0.0, 1.0 - sign_changes / max(1, len(signs) - 1))


def passes_local_peak_height_filters(
    apex_intensity: float,
    max_intensity: float,
    config: ExtractionConfig,
) -> bool:
    return (
        apex_intensity >= config.resolver_min_absolute_height
        and apex_intensity >= max_intensity * config.resolver_min_relative_height
    )
