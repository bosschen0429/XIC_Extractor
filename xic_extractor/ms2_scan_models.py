"""Pure MS2 scan data shared by acquisition and domain processing."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Ms2Scan:
    scan_number: int
    rt: float
    precursor_mz: float
    masses: np.ndarray
    intensities: np.ndarray
    base_peak: float


@dataclass(frozen=True)
class Ms2ScanEvent:
    scan: Ms2Scan | None
    parse_error: str | None
    scan_number: int
