from __future__ import annotations

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.peak_detection.traces import Trace, TraceGroup, targeted_trace_group


def targeted_extraction_trace_group(
    *,
    sample_name: str,
    target: Target,
    config: ExtractionConfig,
    rt: object,
    intensity: object,
    rt_min: float,
    rt_max: float,
    expected_rt_min: float | None,
) -> TraceGroup:
    trace = Trace.from_arrays(
        sample_name=sample_name,
        mz=target.mz,
        rt=rt,
        intensity=intensity,
        rt_min=rt_min,
        rt_max=rt_max,
        ppm_tol=target.ppm_tol,
        source="targeted_extraction",
    )
    return targeted_trace_group(
        trace,
        target_label=target.label,
        resolver_mode=config.resolver_mode,
        expected_rt_min=expected_rt_min,
        neutral_loss_tag=_neutral_loss_tag(target),
        precursor_mz=target.mz,
        observed_neutral_loss_da=target.neutral_loss_da,
        role="ISTD" if target.is_istd else "Analyte",
        istd_pair=target.istd_pair,
    )


def _neutral_loss_tag(target: Target) -> str:
    if target.neutral_loss_da is None:
        return ""
    return f"NL:{target.neutral_loss_da:.4f}"
