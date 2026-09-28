"""Plain-language presentation of owner-issued product decisions and evidence."""

from __future__ import annotations

from collections.abc import Mapping

_REASON_TEXT = {
    "ms1_peak_present": "MS1 peak present",
    "ms1_coherent": "Coherent MS1 peak",
    # This owner token does not certify identity or physical peak-local MS2.
    "candidate_aligned_ms2_nl": "MS2/NL support associated with the candidate",
    "role_aware_rt_support": "RT supported by reference/ISTD",
    "paired_istd_anchor_support": "RT agrees with paired ISTD",
    "paired_area_ratio_support": "Plausible area ratio to paired ISTD",
    "chrom_peak_segment_context": "Chromatographic peak-segment support",
    "cwt_boundary_morphology_context": "Peak-shape support for boundaries",
    "trace_coherent": "No major XIC quality flags",
    "role_aware_istd_expected_present": "ISTD expected in this sample",
    "targeted_rt_conflict": "peak position conflicts with the RT reference/window",
    "targeted_rt_review": "RT position needs review",
    "anchor_conflict": "peak RT disagrees with the ISTD anchor",
    "candidate_aligned_ms2_nl_conflict": (
        "expected candidate MS2/NL support was not observed"
    ),
    "hard_nl_conflict": "expected NL support was not observed",
    "hard_local_quality_conflict": "local peak quality needs review",
    "trace_morphology_conflict": "XIC shape/continuity needs review",
    "hard_quality_flag_conflict": "major XIC quality flags",
    "trace_morphology_review": "XIC shape/continuity needs review",
    "chrom_peak_segment_shoulder_review": "shoulder near the selected peak",
    "legacy_confidence_review": "low confidence needs review",
    "selected_envelope_split_supported": "multiple peak segments need boundary review",
    "selected_envelope_boundary_externalize": (
        "peak/integration boundaries are unresolved"
    ),
    "missing_positive_ms1_peak": "no positive MS1 peak available",
    "plausible_dda_nl_dropout": "MS2/NL may be missing from DDA sampling",
    "analyte_nl_fail_requires_policy": (
        "configured neutral loss was not observed within tolerance"
    ),
    "analyte_missing_ms2_requires_policy": "no MS2 spectrum for the selected peak",
    "local_sn_minor": "weak local signal-to-noise",
    "file_error": "RAW file could not be processed",
}

_RT_CONCERN_TEXT = {
    "rt_prior_far": "peak RT is too far from the reference",
    "rt_prior_borderline": "peak RT is borderline relative to the reference",
    "rt_centrality_poor": "peak is far from the RT-window centre",
    "rt_centrality_borderline": "peak is off-centre in the RT window (borderline)",
}


def display_measurement_reason(
    *,
    has_quantifiable_peak: bool,
    review_reasons: tuple[str, ...] = (),
    conflict_reasons: tuple[str, ...] = (),
    not_counted_reasons: tuple[str, ...] = (),
    exclusion_reasons: tuple[str, ...] = (),
    concerns: str = "",
) -> str:
    """Describe retained measurements and review evidence without denying a value."""
    if not has_quantifiable_peak:
        return "No quantifiable MS1 peak was retained in the searched window."
    reasons = (
        conflict_reasons or not_counted_reasons or exclusion_reasons or review_reasons
    )
    if reasons:
        if reasons[0] == "targeted_rt_conflict":
            tokens = frozenset(part.strip() for part in concerns.split(";"))
            for token, text in _RT_CONCERN_TEXT.items():
                if token in tokens:
                    return f"MS1 peak measured; review: {text}."
        return f"MS1 peak measured; review: {_reason_text(reasons[0])}."
    return "MS1 peak measured."


def display_nl_evidence(token: str | None, *, configured: bool) -> str:
    """Render the configured observation; lack of support is not chemical absence."""
    if not configured:
        return "Not requested"
    if token and token.startswith("WARN_"):
        return "Observed with tolerance warning"
    return {
        "OK": "Observed",
        "WARN": "Observed with tolerance warning",
        "NL_FAIL": "Not observed within tolerance",
        "NO_MS2": "No MS2 spectrum",
        "ERROR": "Processing error",
    }.get(token or "", "Unavailable")


def display_supporting_evidence(row: Mapping[str, str]) -> str:
    reasons = _row_reasons(row, "Projection Support Reasons")
    # Peak presence is redundant once the owner reports a coherent MS1 peak.
    if "ms1_coherent" in reasons:
        reasons = tuple(reason for reason in reasons if reason != "ms1_peak_present")
    return "; ".join(dict.fromkeys(_reason_text(reason) for reason in reasons))


def display_conflicts_and_concerns(row: Mapping[str, str]) -> str:
    reasons = (
        _reason_text(reason)
        for field in (
            "Projection Conflict Reasons",
            "Projection Not Counted Reasons",
            "Projection Exclusion Reasons",
            "Projection Review Reasons",
        )
        for reason in _row_reasons(row, field)
    )
    return "; ".join(dict.fromkeys(reasons))


def _row_reasons(row: Mapping[str, str], field: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in row.get(field, "").split(";") if part.strip())


def _reason_text(reason: str) -> str:
    return _REASON_TEXT.get(reason, reason.replace("_", " "))
