from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from xic_extractor.xic_models import XICRequest, XICTrace, validate_xic_window

AnalysisMode = Literal["targeted", "untargeted"]


@dataclass(frozen=True)
class Trace:
    raw_stem: str
    mz: float
    rt: NDArray[np.float64]
    intensity: NDArray[np.float64]
    rt_min: float
    rt_max: float
    ppm_tol: float
    source: str = ""
    acquisition_source_id: str = ""

    @classmethod
    def from_arrays(
        cls,
        *,
        raw_stem: str,
        mz: float,
        rt: object,
        intensity: object,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
        source: str = "",
        acquisition_source_id: str = "",
    ) -> Trace:
        rt_array = np.asarray(rt, dtype=float)
        intensity_array = np.asarray(intensity, dtype=float)
        if (
            rt_array.ndim != 1
            or intensity_array.ndim != 1
            or rt_array.shape != intensity_array.shape
        ):
            raise ValueError("Trace arrays must be matching 1D arrays")
        validate_xic_window(rt_min, rt_max, ppm_tol)
        return cls(
            raw_stem=raw_stem,
            mz=mz,
            rt=rt_array,
            intensity=intensity_array,
            rt_min=rt_min,
            rt_max=rt_max,
            ppm_tol=ppm_tol,
            source=source,
            acquisition_source_id=acquisition_source_id,
        )

    @property
    def scan_count(self) -> int:
        return int(self.rt.size)


@dataclass(frozen=True)
class TraceGroup:
    trace_group_id: str
    raw_stem: str
    analysis_mode: AnalysisMode
    context_id: str
    traces: tuple[Trace, ...]
    expected_rt_min: float | None = None
    precursor_mz: float | None = None
    product_mz: float | None = None
    neutral_loss_tag: str = ""
    observed_neutral_loss_da: float | None = None
    role: str = ""
    istd_pair: str = ""

    @property
    def primary_trace(self) -> Trace:
        return self.traces[0]


def trace_from_xic_request(
    *,
    raw_stem: str,
    request: XICRequest,
    xic_trace: XICTrace,
    source: str = "",
    acquisition_source_id: str = "",
) -> Trace:
    return Trace(
        raw_stem=raw_stem,
        mz=request.mz,
        rt=xic_trace.rt,
        intensity=xic_trace.intensity,
        rt_min=request.rt_min,
        rt_max=request.rt_max,
        ppm_tol=request.ppm_tol,
        source=source,
        acquisition_source_id=acquisition_source_id,
    )


def targeted_trace_group(
    trace: Trace,
    *,
    target_label: str,
    expected_rt_min: float | None = None,
    neutral_loss_tag: str = "",
    precursor_mz: float | None = None,
    product_mz: float | None = None,
    observed_neutral_loss_da: float | None = None,
    role: str = "",
    istd_pair: str = "",
) -> TraceGroup:
    return TraceGroup(
        trace_group_id="|".join((trace.raw_stem, target_label)),
        raw_stem=trace.raw_stem,
        analysis_mode="targeted",
        context_id=target_label,
        traces=(trace,),
        expected_rt_min=expected_rt_min,
        precursor_mz=trace.mz if precursor_mz is None else precursor_mz,
        product_mz=product_mz,
        neutral_loss_tag=neutral_loss_tag,
        observed_neutral_loss_da=observed_neutral_loss_da,
        role=role,
        istd_pair=istd_pair,
    )


def untargeted_trace_group(
    trace: Trace,
    *,
    family_id: str,
    expected_rt_min: float | None = None,
    neutral_loss_tag: str = "",
    precursor_mz: float | None = None,
    product_mz: float | None = None,
    observed_neutral_loss_da: float | None = None,
) -> TraceGroup:
    return TraceGroup(
        trace_group_id="|".join((trace.raw_stem, family_id, "untargeted")),
        raw_stem=trace.raw_stem,
        analysis_mode="untargeted",
        context_id=family_id,
        traces=(trace,),
        expected_rt_min=expected_rt_min,
        precursor_mz=trace.mz if precursor_mz is None else precursor_mz,
        product_mz=product_mz,
        neutral_loss_tag=neutral_loss_tag,
        observed_neutral_loss_da=observed_neutral_loss_da,
    )
