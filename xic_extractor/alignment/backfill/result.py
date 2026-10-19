"""Backfill extraction result and peak fact contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias

import numpy as np
from numpy.typing import NDArray

from xic_extractor.alignment.backfill.request_plan import (
    BackfillLogicalRequestMember,
    BackfillRequestIdentity,
    BackfillRequestItem,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PreparedSignalResult,
)
from xic_extractor.xic_models import XICTrace

_RequestItem = BackfillRequestItem
# One extraction chunk's prepared signals, keyed by the identity of the trace
# object request equivalence fanned out.  The cache holds the trace itself so a
# recycled id() can never alias a different trace.
_ChunkPreparedSignalCache: TypeAlias = dict[int, tuple[XICTrace, PreparedSignalResult]]
_RequestGroupKey = tuple[str, int | float, int | float]
_BACKFILL_PEAK_FACT_OBSERVATION_MINT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class _ExtractionRequestItem:
    item: _RequestItem
    logical_request: BackfillLogicalRequestMember | None = None
    request_identity: BackfillRequestIdentity | None = None


_WindowedRequestItem = tuple[_ExtractionRequestItem, tuple[int, int]]


class BackfillSource(Protocol):
    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]: ...
