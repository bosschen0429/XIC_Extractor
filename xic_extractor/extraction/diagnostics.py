from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xic_extractor.acquisition.ms2_windows import acquire_ms2_window
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import (
    CandidateMS2Evidence,
    NLResult,
    candidate_ms2_geometry,
)
from xic_extractor.evidence.neutral_loss import (
    check_nl as evaluate_nl,
)
from xic_extractor.evidence.neutral_loss import (
    collect_candidate_ms2_evidence as evaluate_candidate_ms2_evidence,
)
from xic_extractor.ms2_scan_models import Ms2Window
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.peak_detection.models import PeakCandidate


def check_nl(
    raw: Any,
    *,
    sample_key: str,
    rt_min: float,
    rt_max: float,
    **question: Any,
) -> NLResult:
    window = acquire_ms2_window(
        raw, sample_key=sample_key, rt_min=rt_min, rt_max=rt_max
    )
    return evaluate_nl(window, **question)


def collect_candidate_ms2_evidence(
    raw: Any,
    *,
    sample_key: str,
    candidate: object,
    precursor_mz: float,
    neutral_loss_da: float,
    nl_ppm_warn: float,
    nl_ppm_max: float,
    ms2_precursor_tol_da: float,
    nl_min_intensity_ratio: float,
    ms1_peak_group_rt_min: float | None = None,
    ms1_peak_group_rt_max: float | None = None,
    ms1_peak_group_source: str = "",
) -> CandidateMS2Evidence:
    geometry = candidate_ms2_geometry(
        candidate,
        ms1_peak_group_rt_min=ms1_peak_group_rt_min,
        ms1_peak_group_rt_max=ms1_peak_group_rt_max,
    )
    window = acquire_ms2_window(
        raw, sample_key=sample_key, rt_min=geometry.rt_min, rt_max=geometry.rt_max
    )
    return evaluate_candidate_ms2_evidence(
        window,
        geometry=geometry,
        precursor_mz=precursor_mz,
        neutral_loss_da=neutral_loss_da,
        nl_ppm_warn=nl_ppm_warn,
        nl_ppm_max=nl_ppm_max,
        ms2_precursor_tol_da=ms2_precursor_tol_da,
        nl_min_intensity_ratio=nl_min_intensity_ratio,
        ms1_peak_group_source=ms1_peak_group_source,
    )


def check_target_nl(
    raw: Any,
    target: Target,
    config: ExtractionConfig,
    *,
    sample_key: str,
    window: Ms2Window | None = None,
) -> NLResult | None:
    """Diagnostic summary; candidate-local shared method evidence owns support."""
    if target.neutral_loss_da is None:
        return None
    assert target.nl_ppm_warn is not None and target.nl_ppm_max is not None
    if window is None:
        low, high = raw.acquisition_rt_bounds()
        window = acquire_ms2_window(raw, sample_key=sample_key, rt_min=low, rt_max=high)
    return evaluate_nl(
        window,
        precursor_mz=target.mz,
        neutral_loss_da=target.neutral_loss_da,
        nl_ppm_warn=target.nl_ppm_warn,
        nl_ppm_max=target.nl_ppm_max,
        ms2_precursor_tol_da=config.ms2_precursor_tol_da,
        nl_min_intensity_ratio=config.nl_min_intensity_ratio,
    )


def candidate_ms2_evidence_builder(
    raw: Any,
    target: Target,
    config: ExtractionConfig,
    *,
    sample_key: str,
    window: Ms2Window | None = None,
) -> Callable[[PeakCandidate], Any] | None:
    if target.neutral_loss_da is None:
        return None
    assert target.nl_ppm_warn is not None and target.nl_ppm_max is not None
    if target.nl_ppm_warn is None or target.nl_ppm_max is None:
        return None
    neutral_loss_da = target.neutral_loss_da
    nl_ppm_warn = target.nl_ppm_warn
    nl_ppm_max = target.nl_ppm_max

    def _builder(candidate: PeakCandidate) -> Any:
        peak_group_scope = _candidate_ms1_peak_group_scope(candidate)
        if window is not None:
            return evaluate_candidate_ms2_evidence(
                window,
                geometry=candidate_ms2_geometry(
                    candidate,
                    ms1_peak_group_rt_min=peak_group_scope[0],
                    ms1_peak_group_rt_max=peak_group_scope[1],
                ),
                precursor_mz=target.mz,
                neutral_loss_da=neutral_loss_da,
                nl_ppm_warn=nl_ppm_warn,
                nl_ppm_max=nl_ppm_max,
                ms2_precursor_tol_da=config.ms2_precursor_tol_da,
                nl_min_intensity_ratio=config.nl_min_intensity_ratio,
                ms1_peak_group_source=peak_group_scope[2],
            )
        return collect_candidate_ms2_evidence(
            raw,
            sample_key=sample_key,
            candidate=candidate,
            precursor_mz=target.mz,
            neutral_loss_da=neutral_loss_da,
            nl_ppm_warn=nl_ppm_warn,
            nl_ppm_max=nl_ppm_max,
            ms2_precursor_tol_da=config.ms2_precursor_tol_da,
            nl_min_intensity_ratio=config.nl_min_intensity_ratio,
            ms1_peak_group_rt_min=peak_group_scope[0],
            ms1_peak_group_rt_max=peak_group_scope[1],
            ms1_peak_group_source=peak_group_scope[2],
        )

    return _builder


def _candidate_ms1_peak_group_scope(
    candidate: PeakCandidate,
) -> tuple[float | None, float | None, str]:
    proposal_sources = {str(source) for source in candidate.proposal_sources}
    if "chrom_peak_segment" not in proposal_sources:
        return None, None, ""
    peak = candidate.peak
    return (
        float(peak.peak_start),
        float(peak.peak_end),
        "gaussian15_ms1_peak_group",
    )


def istd_anchor_missing_diagnostic(
    sample_name: str, target: Target
) -> DiagnosticRecord:
    return DiagnosticRecord(
        sample_name=sample_name,
        target_label=target.label,
        issue="ISTD_ANCHOR_MISSING",
        reason=(
            f"ISTD '{target.istd_pair}' yielded no credible current-sample anchor; "
            "use the target expected RT with available sample drift as identity "
            "reference while retaining complete acquisition coverage"
        ),
    )
