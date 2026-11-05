from types import SimpleNamespace

from xic_extractor.peak_detection.scoring_quality import (
    candidate_quality_penalty,
    candidate_selection_quality_penalty,
    hard_quality_flags,
    has_adap_like_quality_flags,
    is_adap_like_quality_flag,
    trace_quality_cap_required,
    trace_quality_severities,
)


def test_adap_like_flags_are_soft_quality_policy() -> None:
    candidate = SimpleNamespace(
        quality_flags=("low_scan_support", "low_trace_continuity"),
    )

    assert candidate_quality_penalty(candidate) == (0, [])
    assert candidate_selection_quality_penalty(candidate) == 0.5
    assert trace_quality_severities(candidate) == (
        (1, "low scan support"),
        (1, "low trace continuity"),
        (0, "poor edge recovery"),
    )
    assert hard_quality_flags(candidate.quality_flags) == ()
    assert is_adap_like_quality_flag("low_scan_support") is True
    assert has_adap_like_quality_flags(candidate.quality_flags) is True


def test_hard_quality_flags_suppress_equivalent_legacy_flags() -> None:
    flags = (
        "low_scan_count",
        "low_scan_support",
        "low_top_edge_ratio",
        "poor_edge_recovery",
        "too_broad",
    )

    assert hard_quality_flags(flags) == ("too_broad",)


def test_trace_quality_cap_policy_distinguishes_blocking_and_soft_flags() -> None:
    assert trace_quality_cap_required(("low_scan_support",)) is True
    assert (
        trace_quality_cap_required(("low_trace_continuity", "poor_edge_recovery"))
        is True
    )
    assert trace_quality_cap_required(("low_trace_continuity",)) is False
