from dataclasses import replace
from pathlib import Path

from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.discovery.evidence_config import (
    DEFAULT_EVIDENCE_PROFILE,
    DiscoveryEvidenceProfile,
)
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.discovery.peak_anchor import assign_peak_anchors
from xic_extractor.discovery.settings import DiscoverySettings


def test_assign_peak_anchors_groups_candidates_sharing_ms1_peak() -> None:
    first = _candidate(
        candidate_id="Sample#10",
        precursor_mz=500.000,
        product_mz=383.9526,
        apex_rt=12.228,
        peak_start=12.104,
        peak_end=12.306,
    )
    second = _candidate(
        candidate_id="Sample#20",
        precursor_mz=500.005,
        product_mz=383.9576,
        apex_rt=12.228,
        peak_start=12.104,
        peak_end=12.306,
    )

    assigned = assign_peak_anchors(
        (second, first),
        settings=DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
        ),
    )

    assert {candidate.feature_id for candidate in assigned} == {"Sample@F0001"}
    assert [candidate.feature_family_size for candidate in assigned] == [2, 2]
    assert [candidate.candidate_id for candidate in assigned] == [
        "Sample#20",
        "Sample#10",
    ]


def test_assign_peak_anchors_keeps_same_apex_different_precursors_separate() -> None:
    first = _candidate(
        candidate_id="Sample#10",
        precursor_mz=250.0,
        product_mz=133.9526,
        apex_rt=12.228,
        peak_start=12.104,
        peak_end=12.306,
    )
    second = _candidate(
        candidate_id="Sample#20",
        precursor_mz=750.0,
        product_mz=633.9526,
        apex_rt=12.228,
        peak_start=12.104,
        peak_end=12.306,
    )
    settings = DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474),
        precursor_mz_tolerance_ppm=20.0,
    )

    assigned = assign_peak_anchors((first, second), settings=settings)

    assert [candidate.feature_id for candidate in assigned] == [
        "Sample@F0001",
        "Sample@F0002",
    ]
    assert [candidate.feature_family_size for candidate in assigned] == [1, 1]


def test_assign_peak_anchors_does_not_chain_precursor_tolerance_groups() -> None:
    low = _candidate(candidate_id="Sample#low", precursor_mz=500.000)
    middle = _candidate(candidate_id="Sample#middle", precursor_mz=500.009)
    high = _candidate(candidate_id="Sample#high", precursor_mz=500.018)
    settings = DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474),
        precursor_mz_tolerance_ppm=20.0,
    )

    assigned = assign_peak_anchors((middle, high, low), settings=settings)
    reversed_assigned = assign_peak_anchors((low, high, middle), settings=settings)
    by_id = {candidate.candidate_id: candidate for candidate in assigned}
    reversed_by_id = {
        candidate.candidate_id: candidate for candidate in reversed_assigned
    }

    assert by_id[low.candidate_id].feature_id == (by_id[middle.candidate_id].feature_id)
    assert by_id[high.candidate_id].feature_id != (
        by_id[middle.candidate_id].feature_id
    )
    assert {
        candidate_id: (candidate.feature_id, candidate.feature_family_size)
        for candidate_id, candidate in by_id.items()
    } == {
        candidate_id: (candidate.feature_id, candidate.feature_family_size)
        for candidate_id, candidate in reversed_by_id.items()
    }


def test_assign_peak_anchors_keeps_raw_sample_and_tag_boundaries_separate() -> None:
    assigned = assign_peak_anchors(
        (
            _candidate(
                candidate_id="Sample#10",
                raw_file=Path("C:/data/Sample.raw"),
                sample_stem="Sample",
                neutral_loss_tag="DNA_dR",
            ),
            _candidate(
                candidate_id="Sample#20",
                raw_file=Path("C:/data/Other.raw"),
                sample_stem="Sample",
                neutral_loss_tag="DNA_dR",
            ),
            _candidate(
                candidate_id="Sample#30",
                raw_file=Path("C:/data/Sample.raw"),
                sample_stem="OtherSample",
                neutral_loss_tag="DNA_dR",
            ),
            _candidate(
                candidate_id="Sample#40",
                raw_file=Path("C:/data/Sample.raw"),
                sample_stem="Sample",
                neutral_loss_tag="RNA_r",
            ),
        ),
        settings=DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
        ),
    )

    assert [candidate.feature_family_size for candidate in assigned] == [1, 1, 1, 1]
    assert len({candidate.feature_id for candidate in assigned}) == 4


def test_assign_peak_anchors_keeps_distinct_ms1_peaks_separate() -> None:
    assigned = assign_peak_anchors(
        (
            _candidate(
                candidate_id="Sample#10",
                apex_rt=7.80,
                peak_start=7.70,
                peak_end=7.88,
            ),
            _candidate(
                candidate_id="Sample#20",
                apex_rt=8.04,
                peak_start=7.96,
                peak_end=8.14,
            ),
        ),
        settings=DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
        ),
    )

    assert [candidate.feature_id for candidate in assigned] == [
        "Sample@F0001",
        "Sample@F0002",
    ]
    assert [candidate.feature_family_size for candidate in assigned] == [1, 1]


def test_assign_peak_anchors_does_not_group_missing_ms1_peaks() -> None:
    assigned = assign_peak_anchors(
        (
            _candidate(candidate_id="Sample#10", ms1_peak_found=False),
            _candidate(candidate_id="Sample#20", ms1_peak_found=False),
        ),
        settings=DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
        ),
    )

    assert [candidate.feature_id for candidate in assigned] == [
        "Sample@F0001",
        "Sample@F0002",
    ]
    assert [candidate.feature_family_size for candidate in assigned] == [1, 1]


def test_assign_peak_anchors_assigns_evidence_score_and_tier() -> None:
    assigned = assign_peak_anchors(
        (
            _candidate(
                candidate_id="Sample#10",
                review_priority="HIGH",
                seed_event_count=3,
                ms2_product_max_intensity=150000.0,
                apex_rt=13.100,
                peak_start=12.90,
                peak_end=13.30,
                ms1_area=20_000_000.0,
            ),
            _candidate(
                candidate_id="Sample#20",
                review_priority="MEDIUM",
                seed_event_count=1,
                ms2_product_max_intensity=5000.0,
                apex_rt=13.500,
                peak_start=13.40,
                peak_end=13.60,
                ms1_area=50_000.0,
            ),
            _candidate(
                candidate_id="Sample#30",
                review_priority="LOW",
                ms1_peak_found=False,
                seed_event_count=1,
                ms2_product_max_intensity=500.0,
                ms1_area=None,
            ),
        ),
        settings=DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
        ),
    )

    by_id = {candidate.candidate_id: candidate for candidate in assigned}
    assert by_id["Sample#10"].evidence_tier == "A"
    assert by_id["Sample#10"].evidence_score >= 80
    assert by_id["Sample#10"].ms2_support == "strong"
    assert by_id["Sample#10"].ms1_support == "strong"
    assert by_id["Sample#10"].rt_alignment == "aligned"
    assert by_id["Sample#20"].evidence_tier in {"B", "C"}
    assert by_id["Sample#20"].ms2_support == "weak"
    assert by_id["Sample#20"].ms1_support == "moderate"
    assert by_id["Sample#30"].evidence_tier == "E"
    assert by_id["Sample#30"].ms1_support == "missing"
    assert by_id["Sample#30"].rt_alignment == "missing"


def test_assign_peak_anchors_threads_custom_evidence_settings() -> None:
    default_assigned = assign_peak_anchors(
        (_candidate(candidate_id="Sample#10"),),
        settings=DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
        ),
    )
    custom_settings = DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474),
        evidence_profile=DiscoveryEvidenceProfile(
            name="default",
            weights=replace(
                DEFAULT_EVIDENCE_PROFILE.weights,
                ms1_peak_present=30,
            ),
            thresholds=DEFAULT_EVIDENCE_PROFILE.thresholds,
        ),
    )

    custom_assigned = assign_peak_anchors(
        (_candidate(candidate_id="Sample#10"),),
        settings=custom_settings,
    )

    assert custom_assigned[0].evidence_score == default_assigned[0].evidence_score + 5


def test_peak_anchors_preserve_distinct_feature_backed_inferred_candidates() -> None:
    weaker = replace(
        _candidate(
            candidate_id="Sample#10-weaker",
            seed_event_count=1,
            ms2_product_max_intensity=5_000.0,
            ms1_area=50_000.0,
        ),
        precursor_mz_basis="product_plus_neutral_loss",
        discovery_candidate_state="ms1_feature_nl_rescued",
        ms1_feature_row_id="Sample|DNA_dR|258.1085|7.84",
    )
    stronger = replace(
        _candidate(
            candidate_id="Sample#10-stronger",
            precursor_mz=258.6085,
            product_mz=142.5611,
            seed_event_count=3,
            ms2_product_max_intensity=150_000.0,
            ms1_area=20_000_000.0,
        ),
        precursor_mz_basis="product_plus_neutral_loss",
        discovery_candidate_state="ms1_feature_nl_rescued",
        ms1_feature_row_id="Sample|DNA_dR|258.6085|7.84",
    )
    direct = replace(
        _candidate(
            candidate_id="Sample#10-direct",
            precursor_mz=301.1652,
            product_mz=185.1178,
        ),
        precursor_mz_basis="scan_precursor",
        discovery_candidate_state="ms1_feature_nl_supported",
        ms1_feature_row_id="Sample|DNA_dR|301.1652|7.84",
    )
    other_scan = replace(
        _candidate(
            candidate_id="Sample#11-inferred",
            precursor_mz=420.2,
            product_mz=304.1526,
        ),
        best_ms2_scan_id=11,
        seed_scan_ids=(11,),
        precursor_mz_basis="product_plus_neutral_loss",
        discovery_candidate_state="ms1_feature_nl_rescued",
        ms1_feature_row_id="Sample|DNA_dR|420.2|7.84",
    )

    assigned = assign_peak_anchors(
        (weaker, direct, other_scan, stronger),
        settings=DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
        ),
    )
    reversed_assigned = assign_peak_anchors(
        tuple(reversed((weaker, direct, other_scan, stronger))),
        settings=DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
        ),
    )
    by_id = {candidate.candidate_id: candidate for candidate in assigned}
    reversed_by_id = {
        candidate.candidate_id: candidate for candidate in reversed_assigned
    }

    assert set(by_id) == {
        candidate.candidate_id for candidate in (weaker, stronger, direct, other_scan)
    }
    for original in (weaker, stronger):
        candidate = by_id[original.candidate_id]
        assert candidate.discovery_candidate_state == "ms1_feature_nl_rescued"
        assert candidate.ms1_feature_row_id == original.ms1_feature_row_id
        assert candidate.ms1_area == original.ms1_area
        assert candidate.best_ms2_scan_id == original.best_ms2_scan_id
    assert by_id[weaker.candidate_id].feature_id != (
        by_id[stronger.candidate_id].feature_id
    )
    assert by_id[stronger.candidate_id].evidence_score > (
        by_id[weaker.candidate_id].evidence_score
    )
    assert by_id[direct.candidate_id].discovery_candidate_state == (
        "ms1_feature_nl_supported"
    )
    assert by_id[other_scan.candidate_id].discovery_candidate_state == (
        "ms1_feature_nl_rescued"
    )
    assert {
        candidate_id: (
            candidate.discovery_candidate_state,
            candidate.ms1_feature_row_id,
            candidate.ms1_area,
            candidate.feature_id,
            candidate.evidence_score,
        )
        for candidate_id, candidate in by_id.items()
    } == {
        candidate_id: (
            candidate.discovery_candidate_state,
            candidate.ms1_feature_row_id,
            candidate.ms1_area,
            candidate.feature_id,
            candidate.evidence_score,
        )
        for candidate_id, candidate in reversed_by_id.items()
    }


def _candidate(
    *,
    candidate_id: str,
    review_priority: str = "MEDIUM",
    sample_stem: str = "Sample",
    raw_file: Path | None = None,
    neutral_loss_tag: str = "DNA_dR",
    precursor_mz: float = 258.1085,
    product_mz: float = 142.0611,
    apex_rt: float | None = 7.84,
    peak_start: float | None = 7.70,
    peak_end: float | None = 7.98,
    ms1_peak_found: bool = True,
    seed_event_count: int = 1,
    ms2_product_max_intensity: float = 5000.0,
    ms1_area: float | None = None,
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        review_priority=review_priority,  # type: ignore[arg-type]
        evidence_score=0,
        evidence_tier="E",
        ms2_support="weak",
        ms1_support="missing",
        rt_alignment="missing",
        candidate_id=candidate_id,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        observed_neutral_loss_da=116.0474,
        best_seed_rt=apex_rt or 7.84,
        seed_event_count=seed_event_count,
        ms1_peak_found=ms1_peak_found,
        ms1_apex_rt=apex_rt,
        ms1_area=(
            (1000.0 if ms1_peak_found else None) if ms1_area is None else ms1_area
        ),
        ms2_product_max_intensity=ms2_product_max_intensity,
        reason="single MS2 NL seed; MS1 peak found",
        raw_file=raw_file or Path("C:/data/Sample.raw"),
        sample_stem=sample_stem,
        best_ms2_scan_id=10,
        seed_scan_ids=(10,),
        neutral_loss_tag=neutral_loss_tag,
        configured_neutral_loss_da=116.0474,
        neutral_loss_mass_error_ppm=0.0,
        rt_seed_min=apex_rt or 7.84,
        rt_seed_max=apex_rt or 7.84,
        ms1_search_rt_min=7.60,
        ms1_search_rt_max=8.10,
        ms1_seed_delta_min=0.0 if ms1_peak_found else None,
        ms1_peak_rt_start=peak_start if ms1_peak_found else None,
        ms1_peak_rt_end=peak_end if ms1_peak_found else None,
        ms1_height=100.0 if ms1_peak_found else None,
        ms1_trace_quality="clean" if ms1_peak_found else "missing",
    )
