from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

import numpy as np

from xic_extractor.acquisition.ms2_windows import MS2ScanSource


class XICSource(Protocol):
    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[np.ndarray, np.ndarray]: ...


class InstrumentQcRaw(XICSource, MS2ScanSource, Protocol):
    """RAW reader surface for trend rows and their HCD audit."""


RawOpener = Callable[[Path], AbstractContextManager[InstrumentQcRaw]]
