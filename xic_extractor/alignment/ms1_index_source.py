from __future__ import annotations

from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from xic_extractor.ms1_scan_index import (
    MS1_INDEX_CACHE_SCHEMA_VERSION as MS1_INDEX_CACHE_SCHEMA_VERSION,
)
from xic_extractor.ms1_scan_index import (
    MS1IndexIntensityMode as MS1IndexIntensityMode,
)
from xic_extractor.ms1_scan_index import (
    MS1Scan,
    _raw_file,
    build_ms1_scan_index,
    extract_index_xic,
)
from xic_extractor.ms1_scan_index import (
    read_ms1_scan_index_npz as read_ms1_scan_index_npz,
)
from xic_extractor.ms1_scan_index import (
    write_ms1_scan_index_npz as write_ms1_scan_index_npz,
)
from xic_extractor.raw_superwindow_source import RawSuperWindowSource
from xic_extractor.xic_models import XICRequest, XICTrace

OwnerBuildXicBackend = Literal["raw", "raw_superwindow", "ms1_index"]
BackfillXicBackend = Literal["raw", "ms1_index", "ms1_index_hybrid"]


class MS1IndexedRawSource:
    def __init__(self, source: Any) -> None:
        self._source = source
        self._index: tuple[MS1Scan, ...] | None = None

    @property
    def raw_chromatogram_call_count(self) -> int:
        value = getattr(self._source, "raw_chromatogram_call_count", 0)
        return value if isinstance(value, int) else 0

    @property
    def acquisition_source(self) -> Any:
        return getattr(self._source, "acquisition_source", None)

    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        trace = self.extract_xic_many(
            (XICRequest(mz=mz, rt_min=rt_min, rt_max=rt_max, ppm_tol=ppm_tol),)
        )[0]
        return trace.rt, trace.intensity

    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ) -> tuple[XICTrace, ...]:
        if not requests:
            return ()
        index = self._ms1_index()
        return tuple(
            extract_index_xic(self._source, index, request)
            for request in requests
        )

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        resolver = getattr(self._source, "scan_window_for_request", None)
        if callable(resolver):
            start_scan, end_scan = resolver(request)
            return int(start_scan), int(end_scan)
        raw_file = _raw_file(self._source)
        return (
            int(raw_file.ScanNumberFromRetentionTime(request.rt_min)),
            int(raw_file.ScanNumberFromRetentionTime(request.rt_max)),
        )

    def _ms1_index(self) -> tuple[MS1Scan, ...]:
        if self._index is None:
            self._index = build_ms1_scan_index(self._source)
        return self._index


def source_for_backfill_backend(
    source: Any,
    backend: BackfillXicBackend,
) -> Any:
    if backend == "raw":
        return source
    if backend in {"ms1_index", "ms1_index_hybrid"}:
        return MS1IndexedRawSource(source)
    raise ValueError(f"unsupported owner backfill XIC backend: {backend}")


def source_for_owner_build_backend(
    source: Any,
    backend: OwnerBuildXicBackend,
) -> Any:
    if backend == "raw":
        return source
    if backend == "raw_superwindow":
        return RawSuperWindowSource(source)
    if backend == "ms1_index":
        return MS1IndexedRawSource(source)
    raise ValueError(f"unsupported owner build XIC backend: {backend}")
