"""Helpers shared by test_discovery_csv and other test modules."""

import csv
from dataclasses import replace
from pathlib import Path

from xic_extractor.discovery.models import DiscoveryEvent, DiscoveryPeak


def _peak(**overrides: object) -> DiscoveryPeak:
    peak = DiscoveryPeak(
        peak_id="Sample_1@mz258.108512_rt7.8412",
        sample_stem="Sample_1",
        peak_mz=258.108512345,
        neutral_loss_tag="DNA_dR",
        best_ms2_scan_id=6095,
        attached_event_count=1,
        ms1_apex_rt=7.841234,
        ms1_search_rt_min=7.241234,
        ms1_search_rt_max=8.441234,
        query_rt_min=7.831234,
        status="admitted",
        evidence_score=50,
        matched_tag_names=("DNA_dR",),
    )
    return replace(peak, **overrides)  # type: ignore[arg-type]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def located_scan_ids(peak: DiscoveryPeak) -> tuple[int, ...]:
    """MS2 scans of every event locating the peak, as the event table lists them."""
    return tuple(sorted({event.scan_number for event in peak.located_events}))


def representative_event(peak: DiscoveryPeak) -> DiscoveryEvent:
    """The located event ``best_ms2_scan_id`` names, for a peak with one there."""
    (event,) = {
        event
        for event in peak.located_events
        if event.scan_number == peak.best_ms2_scan_id
        and event.neutral_loss_tag == peak.neutral_loss_tag
    }
    return event
