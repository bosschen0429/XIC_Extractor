from types import SimpleNamespace

from xic_extractor.alignment.identity_gates import (
    EXTREME_BACKFILL_REASON,
    WEAK_SEED_BACKFILL_REASON,
    WEAK_SEED_TOLERATED_REASON,
    DetectedSeedRef,
    classify_single_dr_backfill_dependency,
    lookup_seed_candidate,
    summarize_detected_seed_quality,
)


def test_extreme_single_dr_backfill_gate_does_not_need_seed_enrichment() -> None:
    reason = classify_single_dr_backfill_dependency(
        neutral_loss_tag="DNA_dR",
        q_detected=2,
        q_backfill=83,
        cell_count=85,
        seed_quality=None,
    )

    assert reason == EXTREME_BACKFILL_REASON


def test_weak_seed_backfill_gate_reuses_seed_quality_summary() -> None:
    summary = summarize_detected_seed_quality(
        (
            DetectedSeedRef(sample_stem="S1", source_candidate_id="S1#C1"),
            DetectedSeedRef(sample_stem="S2", source_candidate_id="S2#C2"),
            DetectedSeedRef(sample_stem="S3", source_candidate_id="S3#C3"),
        ),
        {
            ("S1", "S1#C1"): _candidate(evidence_score=55),
            ("S2", "S2#C2"): _candidate(seed_event_count=1),
            ("S3", "S3#C3"): _candidate(nl_ppm=12.0),
        },
        enrichment_available=True,
    )

    reason = classify_single_dr_backfill_dependency(
        neutral_loss_tag="dR",
        q_detected=3,
        q_backfill=6,
        cell_count=10,
        seed_quality=summary,
    )

    assert summary.status == "weak"
    assert summary.min_evidence_score == 55.0
    assert summary.min_seed_event_count == 1.0
    assert summary.max_abs_nl_ppm == 12.0
    assert reason == WEAK_SEED_BACKFILL_REASON


def test_adequate_seed_backfill_is_not_a_gate_candidate() -> None:
    summary = summarize_detected_seed_quality(
        (
            DetectedSeedRef(sample_stem="S1", source_candidate_id="S1#C1"),
            DetectedSeedRef(sample_stem="S2", source_candidate_id="S2#C2"),
            DetectedSeedRef(sample_stem="S3", source_candidate_id="S3#C3"),
        ),
        {
            "S1#C1": _candidate(),
            "S2#C2": _candidate(),
            "S3#C3": _candidate(),
        },
        enrichment_available=True,
    )

    reason = classify_single_dr_backfill_dependency(
        neutral_loss_tag="dR",
        q_detected=3,
        q_backfill=6,
        cell_count=10,
        seed_quality=summary,
    )

    assert summary.status == "adequate"
    assert reason is None


def test_two_trusted_seeds_tolerate_one_weak_seed_with_warning_reason() -> None:
    summary = summarize_detected_seed_quality(
        (
            DetectedSeedRef(sample_stem="S1", source_candidate_id="S1#C1"),
            DetectedSeedRef(sample_stem="S2", source_candidate_id="S2#C2"),
            DetectedSeedRef(sample_stem="S3", source_candidate_id="S3#C3"),
        ),
        {
            "S1#C1": _candidate(),
            "S2#C2": _candidate(),
            "S3#C3": _candidate(evidence_score=55, nl_ppm=12.0),
        },
        enrichment_available=True,
    )

    reason = classify_single_dr_backfill_dependency(
        neutral_loss_tag="dR",
        q_detected=3,
        q_backfill=6,
        cell_count=10,
        seed_quality=summary,
    )

    assert summary.status == "adequate"
    assert summary.weak_seed_signal is True
    assert summary.trusted_detected_candidate_count == 2
    assert reason == WEAK_SEED_TOLERATED_REASON


def test_high_score_seeds_are_not_trusted_without_seed_event_support() -> None:
    summary = summarize_detected_seed_quality(
        (
            DetectedSeedRef(sample_stem="S1", source_candidate_id="S1#C1"),
            DetectedSeedRef(sample_stem="S2", source_candidate_id="S2#C2"),
            DetectedSeedRef(sample_stem="S3", source_candidate_id="S3#C3"),
        ),
        {
            "S1#C1": _candidate(seed_event_count=1),
            "S2#C2": _candidate(seed_event_count=1),
            "S3#C3": _candidate(evidence_score=55),
        },
        enrichment_available=True,
    )

    reason = classify_single_dr_backfill_dependency(
        neutral_loss_tag="dR",
        q_detected=3,
        q_backfill=6,
        cell_count=10,
        seed_quality=summary,
    )

    assert summary.status == "weak"
    assert summary.trusted_detected_candidate_count == 0
    assert reason == WEAK_SEED_BACKFILL_REASON


def test_low_scan_support_seed_is_weak_and_not_trusted() -> None:
    summary = summarize_detected_seed_quality(
        (
            DetectedSeedRef(sample_stem="S1", source_candidate_id="S1#C1"),
            DetectedSeedRef(sample_stem="S2", source_candidate_id="S2#C2"),
            DetectedSeedRef(sample_stem="S3", source_candidate_id="S3#C3"),
        ),
        {
            "S1#C1": _candidate(scan_support=0.4),
            "S2#C2": _candidate(),
            "S3#C3": _candidate(),
        },
        enrichment_available=True,
    )

    reason = classify_single_dr_backfill_dependency(
        neutral_loss_tag="dR",
        q_detected=3,
        q_backfill=6,
        cell_count=10,
        seed_quality=summary,
    )

    assert summary.status == "adequate"
    assert summary.weak_seed_signal is True
    assert summary.trusted_detected_candidate_count == 2
    assert reason == WEAK_SEED_TOLERATED_REASON


def test_non_dr_backfill_is_out_of_scope_even_when_extreme() -> None:
    reason = classify_single_dr_backfill_dependency(
        neutral_loss_tag="DNA_R",
        q_detected=1,
        q_backfill=9,
        cell_count=10,
        seed_quality=None,
    )

    assert reason is None


def test_missing_detected_seed_join_is_reported_separately_but_still_weak() -> None:
    summary = summarize_detected_seed_quality(
        (DetectedSeedRef(sample_stem="S1", source_candidate_id="S1#missing"),),
        {},
        enrichment_available=True,
    )

    assert summary.status == "missing_lookup"
    assert summary.weak is True
    assert summary.looked_up_candidate_count == 0
    assert summary.missing_detected_candidate_count == 1


def test_missing_enrichment_is_unavailable_not_weak() -> None:
    summary = summarize_detected_seed_quality(
        (DetectedSeedRef(sample_stem="S1", source_candidate_id="S1#missing"),),
        None,
        enrichment_available=False,
    )

    assert summary.status == "unavailable"
    assert summary.weak is False


def test_lookup_seed_candidate_uses_sample_specific_and_fallback_keys() -> None:
    candidates = {
        ("S1", "S1#C1"): _candidate(evidence_score=61),
        ("", "S9#C2"): _candidate(evidence_score=62),
    }

    exact = lookup_seed_candidate(
        DetectedSeedRef(sample_stem="S1", source_candidate_id="S1#C1"),
        candidates,
    )
    fallback = lookup_seed_candidate(
        DetectedSeedRef(sample_stem="S9", source_candidate_id="S9#C2"),
        candidates,
    )

    assert exact is candidates[("S1", "S1#C1")]
    assert fallback is candidates[("", "S9#C2")]


def test_lookup_seed_candidate_does_not_match_bare_suffix_keys() -> None:
    candidates = {
        "C2": _candidate(evidence_score=10),
    }

    result = lookup_seed_candidate(
        DetectedSeedRef(sample_stem="S9", source_candidate_id="S9#C2"),
        candidates,
    )

    assert result is None


def _candidate(
    *,
    evidence_score: int = 80,
    seed_event_count: int = 3,
    nl_ppm: float = 3.0,
    scan_support: float = 0.8,
) -> SimpleNamespace:
    return SimpleNamespace(
        evidence_score=evidence_score,
        seed_event_count=seed_event_count,
        neutral_loss_mass_error_ppm=nl_ppm,
        ms1_scan_support_score=scan_support,
    )
