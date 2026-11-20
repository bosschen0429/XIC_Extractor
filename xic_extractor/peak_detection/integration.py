import warnings

import numpy as np
from scipy.signal import peak_widths


def raw_apex_index(intensity_values: np.ndarray, left: int, right: int) -> int:
    if right <= left:
        return left
    local_offset = int(np.argmax(intensity_values[left:right]))
    return left + local_offset


def integrate_area_counts_seconds(
    intensity_values: np.ndarray,
    rt_values: np.ndarray,
    left: int,
    right: int,
) -> float:
    area_counts_minutes = float(
        np.trapezoid(intensity_values[left:right], rt_values[left:right])
    )
    return area_counts_minutes * 60.0


def peak_bounds(
    smoothed: np.ndarray, best_idx: int, peak_rel_height: float, n_points: int
) -> tuple[int, int]:
    if best_idx < 0 or best_idx >= n_points:
        return 0, min(n_points, 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        widths = peak_widths(smoothed, [best_idx], rel_height=peak_rel_height)
    if len(widths[0]) == 0 or not np.isfinite(widths[0][0]) or widths[0][0] <= 0:
        return best_idx, min(n_points, best_idx + 1)
    left = max(0, int(np.floor(widths[2][0])))
    right = min(n_points, int(np.ceil(widths[3][0])) + 1)
    return left, right
