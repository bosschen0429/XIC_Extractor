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
    "candidate_aligned_ms2_nl_conflict": "candidate MS2/NL support failed",
    "hard_local_quality_conflict": "local peak quality failed",
    "trace_morphology_conflict": "XIC shape/continuity failed quality checks",
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
    "local_sn_minor": "weak local signal-to-noise",
    "file_error": "RAW file could not be processed",
}

_RT_CONCERN_TEXT = {
    "rt_prior_far": "peak RT is too far from the reference",
    "rt_prior_borderline": "peak RT is borderline relative to the reference",
    "rt_centrality_poor": "peak is far from the RT-window centre",
    "rt_centrality_borderline": "peak is off-centre in the RT window (borderline)",
}


def display_product_reason(
    product_state: str,
    *,
    review_reasons: tuple[str, ...] = (),
    conflict_reasons: tuple[str, ...] = (),
    not_counted_reasons: tuple[str, ...] = (),
    exclusion_reasons: tuple[str, ...] = (),
    concerns: str = "",
) -> str:
    """Summarize the decisive owner category; never mix support into the reason."""
    if product_state == "detected_clean":
        return "Detected."
    if product_state == "detected_flagged":
        if review_reasons:
            return f"Detected; review: {_reason_text(review_reasons[0])}."
        return "Detected; review required."
    reasons = exclusion_reasons or conflict_reasons or not_counted_reasons
    if reasons:
        if reasons[0] == "targeted_rt_conflict":
            tokens = frozenset(part.strip() for part in concerns.split(";"))
            for token, text in _RT_CONCERN_TEXT.items():
                if token in tokens:
                    return f"Not counted: {text}."
        return f"Not counted: {_reason_text(reasons[0])}."
    return "Not counted: decision unresolved; review required."


def display_row_reason(row: Mapping[str, str]) -> str:
    reason = row.get("Reason", "")
    state = row.get("Product State", "")
    if not state or row.get("Counted Detection", "").upper() not in {"TRUE", "FALSE"}:
        return reason
    # Current CSV/in-memory rows already carry the owner's presentation; reuse it.
    # Older canonical rows carry the long serialized projection instead.
    if reason.startswith(("Detected.", "Detected; review:", "Not counted:")):
        return reason
    return display_product_reason(
        state,
        review_reasons=_row_reasons(row, "Projection Review Reasons"),
        conflict_reasons=_row_reasons(row, "Projection Conflict Reasons"),
        not_counted_reasons=_row_reasons(row, "Projection Not Counted Reasons"),
        exclusion_reasons=_row_reasons(row, "Projection Exclusion Reasons"),
    )


def display_supporting_evidence(row: Mapping[str, str]) -> str:
    reasons = _row_reasons(row, "Projection Support Reasons")
    # Peak presence is redundant once the owner reports a coherent MS1 peak.
    if "ms1_coherent" in reasons:
        reasons = tuple(reason for reason in reasons if reason != "ms1_peak_present")
    return "; ".join(dict.fromkeys(_reason_text(reason) for reason in reasons))


def display_conflicts_and_concerns(row: Mapping[str, str]) -> str:
    sections = []
    for label, field in (
        ("Conflict", "Projection Conflict Reasons"),
        ("Not counted", "Projection Not Counted Reasons"),
        ("Excluded", "Projection Exclusion Reasons"),
        ("Review", "Projection Review Reasons"),
    ):
        reasons = _row_reasons(row, field)
        if reasons:
            text = "; ".join(dict.fromkeys(_reason_text(reason) for reason in reasons))
            sections.append(f"{label}: {text}")
    return "\n".join(sections)


def _row_reasons(row: Mapping[str, str], field: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in row.get(field, "").split(";") if part.strip())


def _reason_text(reason: str) -> str:
    return _REASON_TEXT.get(reason, reason.replace("_", " "))
