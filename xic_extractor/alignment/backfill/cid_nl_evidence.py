"""CID-NL interpretation for revisits and conservative physical recovery."""

from __future__ import annotations

from typing import Literal

from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    is_cid_nl_missing_ms1_rejection,
    is_unsearched_cid_nl_discovery_assessment,
)


def decide_cid_nl_backfill_revisit(
    assessment: CidNlMethodAssessmentRef,
    *,
    observed_rt: float | None = None,
    apex_tolerance_sec: float = 0.0,
) -> Literal["reuse", "search", "skip"]:
    """Reuse HOLD evidence unless an observed pattern locates a different peak.

    SUPPORT outside this group belongs to another peak and requires a search.
    A forecast alone does not establish a different observed peak.
    """
    if assessment.content.method_result == "SUPPORT":
        return "search"
    if not (
        assessment.content.method_result == "HOLD"
        or is_unsearched_cid_nl_discovery_assessment(assessment)
        or is_cid_nl_missing_ms1_rejection(assessment)
    ):
        return "skip"
    if not assessment.candidate_peaks:
        return "search"
    if observed_rt is not None and not any(
        abs(peak.content.integration.rt_apex_min - observed_rt) * 60.0
        <= apex_tolerance_sec
        for peak in assessment.candidate_peaks
    ):
        return "search"
    return "reuse"


def cid_nl_backfill_block_reason(assessment: CidNlMethodAssessmentRef) -> str | None:
    """Existing direct support and method rejection are not absent observations."""
    if assessment.content.method_result == "SUPPORT":
        return "cell_already_has_direct_support"
    if assessment.content.method_result == "REJECT":
        return "method_reject_is_not_backfill_eligible"
    return None


def cid_nl_allows_physical_localization(assessment: CidNlMethodAssessmentRef) -> bool:
    """Set-level CID/NL is competing attribution, not missing chemical evidence."""
    return not assessment.set_relation_refs
