"""Helpers shared by test_discovery_csv and other test modules."""

import csv
from dataclasses import replace
from pathlib import Path

from xic_extractor.discovery.models import DiscoveryPeak


def _peak(**overrides: object) -> DiscoveryPeak:
    peak = DiscoveryPeak(
        peak_id="Sample_1@mz258.108512_rt7.8412",
        raw_file=Path("C:/data/Sample_1.raw"),
        sample_stem="Sample_1",
        xic_mz=258.108512345,
        best_event_precursor_mz=258.108512345,
        best_event_product_mz=142.061112345,
        best_event_neutral_loss_da=116.0474,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        neutral_loss_mass_error_ppm=2.584321,
        best_ms2_scan_id=6095,
        best_event_rt=7.83,
        event_scan_ids=(6095,),
        attached_event_count=1,
        event_rt_min=7.80,
        event_rt_max=7.86,
        ms2_product_max_intensity=12000.0,
        ms1_search_rt_min=7.60,
        ms1_search_rt_max=8.06,
        ms1_apex_rt=7.841234,
        ms1_area=88765.4,
        ms1_height=4500.5,
        ms1_peak_rt_start=7.701234,
        ms1_peak_rt_end=7.981234,
        ms1_event_delta_min=0.011234,
        ms1_trace_quality="clean",
        review_priority="MEDIUM",
        reason="single MS2 NL event; MS1 peak found",
        status="admitted",
        evidence_score=50,
        evidence_tier="C",
        ms2_support="moderate",
        ms1_support="moderate",
        rt_alignment="aligned",
        scan_precursor_mz=258.108512345,
        scan_precursor_delta_da=0.0,
        max_scan_precursor_abs_delta_da=0.0,
        matched_tag_names=("DNA_dR",),
        primary_tag_name="DNA_dR",
    )
    return replace(peak, **overrides)  # type: ignore[arg-type]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
