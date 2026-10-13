from dataclasses import dataclass
from typing import Literal, Protocol, cast

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.peak_detection.models import PeakDetectionResult, PeakResult

DiagnosticIssue = Literal[
    "BACKFILL_ACCEPTED",
    "BACKFILL_HOLD",
    "PEAK_NOT_FOUND",
    "NO_SIGNAL",
    "WINDOW_TOO_SHORT",
    "NL_FAIL",
    "NO_MS2",
    "FILE_ERROR",
    "MULTI_PEAK",
    "TAILING",
    "NL_ANCHOR_FALLBACK",
    "ISTD_ANCHOR_MISSING",
    "ANCHOR_RT_MISMATCH",
    "ISTD_CONFIDENCE",
    "TARGET_PAIR_RT_AUTO_RESELECTION_SKIPPED",
    "OFF_ISTD_POSITION",
]

_TAILING_THRESHOLD: float = 2.0


@dataclass(frozen=True)
class DiagnosticRecord:
    sample_name: str
    target_label: str
    issue: DiagnosticIssue
    reason: str


class ExtractionResultLike(Protocol):
    @property
    def peak_result(self) -> PeakDetectionResult: ...

    @property
    def nl(self) -> NLResult | None: ...

    @property
    def candidate_ms2_evidence(self) -> CandidateMS2Evidence | None: ...

    @property
    def reported_rt(self) -> float | None: ...

    @property
    def reason(self) -> str: ...


def build_diagnostic_records(
    sample_name: str,
    target: Target,
    result: ExtractionResultLike,
    config: ExtractionConfig,
) -> list[DiagnosticRecord]:
    records: list[DiagnosticRecord] = []
    if result.peak_result.status != "OK":
        physical_evaluation = getattr(result, "physical_evaluation", None)
        peak_reason = (
            f"Physical peak {physical_evaluation.state}: {physical_evaluation.reason}"
            if physical_evaluation is not None
            and physical_evaluation.state != "available"
            else result.peak_result.reason
            or _peak_reason(target, result.peak_result, config)
        )
        records.append(
            DiagnosticRecord(
                sample_name=sample_name,
                target_label=target.label,
                issue=result.peak_result.status,
                reason=peak_reason,
            )
        )

    records.extend(build_ms2_diagnostic_records(sample_name, target, result, config))

    if result.peak_result.status == "OK" and result.peak_result.peak is not None:
        peak = result.peak_result.peak
        if result.peak_result.n_prominent_peaks > 1:
            records.append(
                DiagnosticRecord(
                    sample_name=sample_name,
                    target_label=target.label,
                    issue="MULTI_PEAK",
                    reason=_multi_peak_reason(result),
                )
            )
        left_half = peak.rt - peak.peak_start
        right_half = peak.peak_end - peak.rt
        if left_half > 0 and (right_half / left_half) > _TAILING_THRESHOLD:
            records.append(
                DiagnosticRecord(
                    sample_name=sample_name,
                    target_label=target.label,
                    issue="TAILING",
                    reason=_tailing_reason(peak),
                )
            )

    return records


def build_ms2_diagnostic_records(
    sample_name: str,
    target: Target,
    result: ExtractionResultLike,
    config: ExtractionConfig,
) -> list[DiagnosticRecord]:
    """Render the current selected peak's evidence, including after Backfill."""
    records = []
    issue = _selected_or_window_nl_issue(result)
    if issue is not None:
        records.append(
            DiagnosticRecord(
                sample_name=sample_name,
                target_label=target.label,
                issue=cast(DiagnosticIssue, issue),
                reason=_nl_reason(target, result, config),
            )
        )
    if result.reported_rt is not None:
        istd_confidence = _istd_confidence_diagnostic(sample_name, target, result)
        if istd_confidence is not None:
            records.append(istd_confidence)
    return records


def istd_confidence_note(istd_confidence: str | None) -> str | None:
    if istd_confidence in (None, "HIGH", "MEDIUM"):
        return None
    return f"ISTD anchor was {istd_confidence}"


def _peak_reason(
    target: Target, peak_result: PeakDetectionResult, config: ExtractionConfig
) -> str:
    if peak_result.status == "NO_SIGNAL":
        return (
            f"XIC empty in window [{target.rt_min}, {target.rt_max}] for "
            f"m/z {target.mz} +/- {target.ppm_tol:g} ppm"
        )
    if peak_result.status == "WINDOW_TOO_SHORT":
        return (
            f"Only {peak_result.n_points} scans in window; "
            f"savgol requires >= {config.smooth_window}"
        )
    max_value = _format_optional_number(peak_result.max_smoothed)
    prominence_pct = config.peak_min_prominence_ratio * 100
    return (
        "No peak met prominence >= "
        f"{prominence_pct:g}% of max smoothed (max={max_value})"
    )


def _selected_or_window_nl_issue(result: ExtractionResultLike) -> str | None:
    candidate_ms2 = result.candidate_ms2_evidence
    if candidate_ms2 is not None and candidate_ms2.nl_status in {"NL_FAIL", "NO_MS2"}:
        return candidate_ms2.nl_status
    if (
        candidate_ms2 is None
        and result.nl is not None
        and result.nl.status
        in {
            "NL_FAIL",
            "NO_MS2",
        }
    ):
        return result.nl.status
    return None


def _nl_reason(
    target: Target, result: ExtractionResultLike, config: ExtractionConfig
) -> str:
    candidate_ms2 = result.candidate_ms2_evidence
    if candidate_ms2 is not None:
        return _candidate_ms2_reason(target, candidate_ms2)
    assert result.nl is not None
    nl = result.nl
    if nl.status == "NO_MS2":
        return (
            f"No MS2 scan targeting precursor {target.mz} +/- "
            f"{config.ms2_precursor_tol_da:g} Da within RT "
            f"[{target.rt_min}, {target.rt_max}]; "
            f"{nl.valid_ms2_scan_count} valid MS2 scans in window "
            f"({nl.parse_error_count} parse errors)"
        )

    limit = target.nl_ppm_max if target.nl_ppm_max is not None else 0.0
    assert (
        target.neutral_loss_da is not None
    )  # NL is only checked when neutral_loss_da is set
    nl_da = target.neutral_loss_da
    expected_product = target.mz - nl_da

    if nl.best_ppm is not None:
        rt_info = (
            f" at scan RT {nl.best_scan_rt:.3f} min"
            if nl.best_scan_rt is not None
            else ""
        )
        return (
            f"Precursor {target.mz} triggered {nl.matched_scan_count} MS2 scans; "
            f"NL {nl_da:g} Da → expected product m/z {expected_product:.4f}; "
            f"best observed-loss error {nl.best_ppm:.1f} ppm "
            f"(quadrature window at {limit:g} ppm per ion){rt_info}"
        )

    return (
        f"Precursor {target.mz} triggered {nl.matched_scan_count} MS2 scans; "
        f"NL {nl_da:g} Da → expected product m/z {expected_product:.4f}; "
        f"not detected in any matched scan"
    )


def _candidate_ms2_reason(target: Target, evidence: CandidateMS2Evidence) -> str:
    assert target.neutral_loss_da is not None
    nl_da = target.neutral_loss_da
    if evidence.nl_status == "NO_MS2":
        return (
            "selected candidate has no candidate-aligned MS2 trigger "
            f"for precursor {target.mz}; strict observed neutral loss "
            f"{nl_da:g} Da could not be evaluated"
        )
    rt_info = (
        f" at scan RT {evidence.best_scan_rt:.3f} min"
        if evidence.best_scan_rt is not None
        else ""
    )
    if evidence.best_loss_ppm is not None:
        limit = target.nl_ppm_max if target.nl_ppm_max is not None else 0.0
        return (
            f"selected candidate has {evidence.trigger_scan_count} "
            f"candidate-aligned MS2 trigger scans; strict observed neutral loss "
            f"{nl_da:g} Da not matched; best observed-loss error "
            f"{evidence.best_loss_ppm:.1f} ppm "
            f"(quadrature window at {limit:g} ppm per ion){rt_info}; "
            f"alignment={evidence.alignment_source}"
        )
    probe_info = _candidate_product_probe_info(evidence)
    return (
        f"selected candidate has {evidence.trigger_scan_count} "
        f"target-matched MS2 trigger scans; target-peak CID-NL support "
        f"for {nl_da:g} Da was not established; "
        f"alignment={evidence.alignment_source}{probe_info}"
    )


def _candidate_product_probe_info(evidence: CandidateMS2Evidence) -> str:
    parts: list[str] = []
    if evidence.diagnostic_product_absence_reason:
        parts.append(f"subcause={evidence.diagnostic_product_absence_reason}")
    if evidence.nearest_product_mz is not None:
        parts.append(f"nearest product m/z {evidence.nearest_product_mz:.4f}")
    if evidence.nearest_product_loss_ppm is not None:
        parts.append(
            f"nearest observed-loss error {evidence.nearest_product_loss_ppm:.1f} ppm"
        )
    if evidence.nearest_product_base_ratio is not None:
        parts.append(
            f"nearest product/base ratio {evidence.nearest_product_base_ratio:.3g}"
        )
    if not parts:
        return ""
    return "; " + "; ".join(parts)


def _multi_peak_reason(result: ExtractionResultLike) -> str:
    base = (
        f"{result.peak_result.n_prominent_peaks} qualified physical candidates "
        "in retained trace context"
    )
    if result.reported_rt is not None:
        base += f"; selected RT {result.reported_rt:.4f} min"
    reason = result.reason or result.peak_result.reason
    if reason:
        base += f"; result reason: {reason}"
    evidence = result.candidate_ms2_evidence
    if evidence is not None and evidence.nl_match and evidence.best_scan_rt is not None:
        base += (
            f"; selected candidate NL-matched MS2 at RT {evidence.best_scan_rt:.3f} min"
        )
    return base


def _tailing_reason(peak: PeakResult) -> str:
    left_half = peak.rt - peak.peak_start
    right_half = peak.peak_end - peak.rt
    ratio = right_half / left_half if left_half > 0 else float("inf")
    return (
        f"Asymmetry ratio {ratio:.2f} (right/left half-width at 5% peak height, "
        f"USP limit 2.0); apex RT {peak.rt:.4f} min, "
        f"peak [{peak.peak_start:.4f}, {peak.peak_end:.4f}]"
    )


def _istd_confidence_diagnostic(
    sample_name: str, target: Target, result: ExtractionResultLike
) -> DiagnosticRecord | None:
    if not target.is_istd:
        return None

    flags: list[str] = []
    confidence = "HIGH"
    evidence = result.candidate_ms2_evidence
    status = (
        evidence.nl_status
        if evidence is not None
        else (result.nl.status if result.nl is not None else None)
    )
    if status is not None:
        if status == "NO_MS2":
            flags.append("NO_MS2")
            confidence = "MEDIUM"
        elif status == "NL_FAIL":
            flags.append("NL_FAIL")
            confidence = "LOW"
        elif status == "WARN":
            flags.append("NL_WARN")
            confidence = "MEDIUM"

    if not flags:
        return None

    return DiagnosticRecord(
        sample_name=sample_name,
        target_label=target.label,
        issue="ISTD_CONFIDENCE",
        reason=(
            f"ISTD confidence={confidence}; flags={','.join(flags)}; "
            "MS1 peak retained because ISTD NL evidence is diagnostic support, "
            "not a hard detection gate"
        ),
    )


def _format_optional_number(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:g}"
