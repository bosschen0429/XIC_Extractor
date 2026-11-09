"""Helpers shared by test_discovery_csv and other test modules."""

import csv
from pathlib import Path

from xic_extractor.discovery.models import (
    DiscoveryPeak,
)


def _candidate(
    *,
    review_priority: str = "MEDIUM",
    evidence_score: int = 50,
    evidence_tier: str = "C",
    ms2_support: str = "moderate",
    ms1_support: str = "moderate",
    rt_alignment: str = "aligned",
    candidate_id: str = "candidate-1",
    sample_stem: str = "Sample_1",
    reason: str = "neutral loss seed",
    raw_file: Path = Path("C:/data/Sample_1.raw"),
    best_seed_rt: float = 7.83,
    seed_event_count: int = 1,
    ms1_area: float | None = 88765.4,
    ms2_product_max_intensity: float = 12000.0,
    ms1_peak_found: bool = True,
    seed_scan_ids: tuple[int, ...] = (6095,),
    feature_id: str = "Sample_1@F0001",
    feature_family_size: int = 1,
    ms1_scan_support_score: float | None = None,
    discovery_candidate_state: str = "ms1_feature_nl_supported",
    ms1_feature_row_id: str = "Sample_1|DNA_dR|258.108512|7.841234",
) -> DiscoveryPeak:
    return DiscoveryPeak(
        review_priority=review_priority,  # type: ignore[arg-type]
        evidence_score=evidence_score,
        evidence_tier=evidence_tier,
        ms2_support=ms2_support,
        ms1_support=ms1_support,
        rt_alignment=rt_alignment,
        discovery_candidate_state=discovery_candidate_state,  # type: ignore[arg-type]
        ms1_feature_row_id=ms1_feature_row_id,
        candidate_id=candidate_id,
        feature_id=feature_id,
        feature_family_size=feature_family_size,
        precursor_mz=258.108512345,
        product_mz=142.061112345,
        observed_neutral_loss_da=116.0474,
        best_seed_rt=best_seed_rt,
        seed_event_count=seed_event_count,
        ms1_peak_found=ms1_peak_found,
        ms1_apex_rt=7.841234,
        ms1_area=ms1_area,
        ms2_product_max_intensity=ms2_product_max_intensity,
        reason=reason,
        raw_file=raw_file,
        sample_stem=sample_stem,
        best_ms2_scan_id=6095,
        seed_scan_ids=seed_scan_ids,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        neutral_loss_mass_error_ppm=2.584321,
        neutral_loss_error_basis="measured_scan_precursor_product",
        precursor_mz_basis="scan_precursor",
        scan_precursor_mz=258.108512345,
        scan_precursor_delta_da=0.0,
        max_scan_precursor_abs_delta_da=0.0,
        rt_seed_min=7.80,
        rt_seed_max=7.86,
        ms1_search_rt_min=7.60,
        ms1_search_rt_max=8.06,
        ms1_seed_delta_min=0.011234,
        ms1_peak_rt_start=7.701234,
        ms1_peak_rt_end=7.981234,
        ms1_height=4500.5,
        ms1_trace_quality="GOOD",
        ms1_scan_support_score=ms1_scan_support_score,
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
