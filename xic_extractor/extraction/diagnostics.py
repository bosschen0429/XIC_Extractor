from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xic_extractor.acquisition.ms2_windows import acquire_ms2_window
from xic_extractor.config import ExtractionConfig, Target
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
from xic_extractor.extraction.anchors import ANCHOR_PEAK_DELTA_WARN_MIN
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.signal_processing import PeakCandidate, PeakDetectionResult


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
) -> NLResult | None:
    """NL quality uses the original target window for full matched scan counts."""
    if target.neutral_loss_da is None:
        return None
    if target.nl_ppm_warn is None or target.nl_ppm_max is None:
        return None
    return check_nl(
        raw,
        sample_key=sample_key,
        precursor_mz=target.mz,
        rt_min=target.rt_min,
        rt_max=target.rt_max,
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
) -> Callable[[PeakCandidate], Any] | None:
    if target.neutral_loss_da is None:
        return None
    if target.nl_ppm_warn is None or target.nl_ppm_max is None:
        return None
    neutral_loss_da = target.neutral_loss_da
    nl_ppm_warn = target.nl_ppm_warn
    nl_ppm_max = target.nl_ppm_max

    def _builder(candidate: PeakCandidate) -> Any:
        peak_group_scope = _candidate_ms1_peak_group_scope(candidate)
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


def anchor_rt_mismatch_diagnostic(
    sample_name: str,
    target: Target,
    peak_result: PeakDetectionResult,
    *,
    anchor_rt: float | None,
    paired_rejection: DiagnosticRecord | None,
) -> DiagnosticRecord | None:
    if (
        paired_rejection is not None
        or anchor_rt is None
        or peak_result.peak is None
    ):
        return None
    delta = abs(peak_result.peak.rt - anchor_rt)
    if delta <= ANCHOR_PEAK_DELTA_WARN_MIN:
        return None
    return DiagnosticRecord(
        sample_name=sample_name,
        target_label=target.label,
        issue="ANCHOR_RT_MISMATCH",
        reason=(
            f"Peak RT {peak_result.peak.rt:.3f} min deviates "
            f"{delta:.2f} min "
            f"from NL anchor at {anchor_rt:.3f} min "
            f"(threshold {ANCHOR_PEAK_DELTA_WARN_MIN} min); "
            f"anchor scan may be noise — verify manually"
        ),
    )


def nl_anchor_fallback_diagnostic(
    sample_name: str,
    target: Target,
    config: ExtractionConfig,
    *,
    anchor_used: bool,
    rt_min: float,
    rt_max: float,
    nl_result: NLResult | None,
) -> DiagnosticRecord | None:
    if anchor_used or target.neutral_loss_da is None:
        return None
    rt_center = (target.rt_min + target.rt_max) / 2.0
    nl_note = ""
    if nl_result is not None and nl_result.status in {"NL_FAIL", "NO_MS2"}:
        nl_note = f"; NL check also {nl_result.status} within fallback window"
    return DiagnosticRecord(
        sample_name=sample_name,
        target_label=target.label,
        issue="NL_ANCHOR_FALLBACK",
        reason=(
            f"No NL-confirmed MS2 within RT center "
            f"{rt_center:.2f} ± {config.nl_rt_anchor_search_margin_min} min; "
            f"fallback window [{rt_min:.2f}, {rt_max:.2f}]{nl_note}"
        ),
    )


def append_anchor_window_diagnostics(
    diagnostics: list[DiagnosticRecord],
    sample_name: str,
    target: Target,
    config: ExtractionConfig,
    peak_result: PeakDetectionResult,
    *,
    anchor_used: bool,
    anchor_rt: float | None,
    rt_min: float,
    rt_max: float,
    nl_result: NLResult | None,
    paired_rejection: DiagnosticRecord | None,
) -> None:
    anchor_diagnostic = anchor_rt_mismatch_diagnostic(
        sample_name,
        target,
        peak_result,
        anchor_rt=anchor_rt,
        paired_rejection=paired_rejection,
    )
    if anchor_diagnostic is not None:
        diagnostics.append(anchor_diagnostic)
    fallback_diagnostic = nl_anchor_fallback_diagnostic(
        sample_name,
        target,
        config,
        anchor_used=anchor_used,
        rt_min=rt_min,
        rt_max=rt_max,
        nl_result=nl_result,
    )
    if fallback_diagnostic is not None:
        diagnostics.append(fallback_diagnostic)


def istd_anchor_missing_diagnostic(
    sample_name: str, target: Target
) -> DiagnosticRecord:
    return DiagnosticRecord(
        sample_name=sample_name,
        target_label=target.label,
        issue="ISTD_ANCHOR_MISSING",
        reason=(
            f"ISTD '{target.istd_pair}' yielded no NL-confirmed anchor in this "
            "sample; falling back to highest base_peak — isobar discrimination "
            "disabled"
        ),
    )
