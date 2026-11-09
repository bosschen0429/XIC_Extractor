import csv
from pathlib import Path

import pytest

from tests.discovery_csv_fixtures import _peak, _read_csv
from tests.discovery_ms1_capture_fixtures import _seed
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv
from xic_extractor.discovery.csv_writer import (
    format_discovery_csv_value,
    write_discovery_events_csv,
    write_discovery_peaks_csv,
)
from xic_extractor.discovery.evidence_config import DEFAULT_EVIDENCE_PROFILE
from xic_extractor.discovery.models import (
    DISCOVERY_EVENT_COLUMNS,
    DISCOVERY_PEAK_COLUMNS,
    EventAttachment,
)
from xic_extractor.discovery.settings import DiscoverySettings


def test_peak_table_leads_with_review_and_status_fields() -> None:
    assert DISCOVERY_PEAK_COLUMNS[:8] == (
        "review_priority",
        "status",
        "evidence_tier",
        "evidence_score",
        "ms2_support",
        "ms1_support",
        "rt_alignment",
        "peak_id",
    )
    assert DISCOVERY_PEAK_COLUMNS[-1] == "is_provisional"


def test_event_table_records_attachment_or_reason() -> None:
    assert DISCOVERY_EVENT_COLUMNS[-3:] == (
        "xic_mz",
        "peak_id",
        "unattached_reason",
    )


def test_peak_csv_round_trips_through_the_reader(tmp_path: Path) -> None:
    peak = _peak(
        event_scan_ids=(6095, 6100, 6112),
        matched_tag_names=("DNA_dR", "R"),
        status="overlap",
        ms1_apex_mz=258.1083,
    )
    path = write_discovery_peaks_csv(tmp_path / "nested" / "peaks.csv", [peak])

    (row,) = _read_csv(path)
    assert row["status"] == "overlap"
    assert row["event_scan_ids"] == "6095;6100;6112"
    assert row["raw_file"] == "C:\\data\\Sample_1.raw"
    (restored,) = read_discovery_peaks_csv(path)
    assert restored.peak_id == peak.peak_id
    assert restored.event_scan_ids == peak.event_scan_ids
    assert restored.matched_tag_names == peak.matched_tag_names
    assert restored.status == "overlap"
    assert restored.ms1_apex_mz == pytest.approx(258.1083)


def test_peak_csv_writes_a_header_without_peaks(tmp_path: Path) -> None:
    path = write_discovery_peaks_csv(tmp_path / "peaks.csv", [])

    with path.open(newline="", encoding="utf-8") as handle:
        assert list(csv.reader(handle)) == [list(DISCOVERY_PEAK_COLUMNS)]


def test_peak_rows_follow_the_review_queue(tmp_path: Path) -> None:
    peaks = [
        _peak(peak_id="low", review_priority="LOW", attached_event_count=9),
        _peak(peak_id="medium-b", evidence_score=75, ms2_product_max_intensity=1.0),
        _peak(peak_id="medium-c", evidence_score=40, attached_event_count=9),
        _peak(peak_id="high-1", review_priority="HIGH", attached_event_count=2),
        _peak(peak_id="high-3", review_priority="HIGH", attached_event_count=3),
    ]

    path = write_discovery_peaks_csv(tmp_path / "peaks.csv", peaks)

    assert [row["peak_id"] for row in _read_csv(path)] == [
        "high-3",
        "high-1",
        "medium-b",
        "medium-c",
        "low",
    ]


def test_peak_csv_escapes_text_for_csv_and_spreadsheets(tmp_path: Path) -> None:
    peak = _peak(
        raw_file=Path("=cmd.raw"),
        sample_stem='Sample, "quoted"',
        reason="@review",
    )

    (row,) = _read_csv(write_discovery_peaks_csv(tmp_path / "peaks.csv", [peak]))

    assert row["raw_file"] == "'=cmd.raw"
    assert row["sample_stem"] == 'Sample, "quoted"'
    assert row["reason"] == "'@review"


def test_seed_scan_ids_formatter_does_not_join_string_characters() -> None:
    assert format_discovery_csv_value("event_scan_ids", (6095, 6100)) == "6095;6100"
    assert format_discovery_csv_value("event_scan_ids", "6095;6100") == "6095;6100"


def test_event_csv_writes_one_row_per_event_in_rt_order(tmp_path: Path) -> None:
    events = [
        EventAttachment(
            event=_seed(scan_number=20, rt=8.2),
            xic_mz=500.0,
            precursor_verified=True,
            unattached_reason="rt_outside_peak",
        ),
        EventAttachment(
            event=_seed(scan_number=10, rt=7.6),
            xic_mz=500.0,
            precursor_verified=True,
            peak_id="Sample@mz500_rt7.6000",
        ),
    ]

    rows = _read_csv(write_discovery_events_csv(tmp_path / "events.csv", events))

    assert [row["ms2_scan_number"] for row in rows] == ["10", "20"]
    assert rows[0]["peak_id"] == "Sample@mz500_rt7.6000"
    assert rows[1]["unattached_reason"] == "rt_outside_peak"
    assert rows[1]["peak_id"] == ""


def test_an_event_cannot_both_attach_and_record_a_reason() -> None:
    with pytest.raises(ValueError, match="one peak or records one reason"):
        EventAttachment(
            event=_seed(scan_number=10, rt=7.6),
            xic_mz=500.0,
            precursor_verified=True,
            peak_id="peak",
            unattached_reason="rt_outside_peak",
        )


def test_discovery_settings_require_neutral_loss_profile() -> None:
    profile = NeutralLossProfile("DNA_dR", 116.0474)

    settings = DiscoverySettings(neutral_loss_profile=profile)

    assert settings.neutral_loss_profile == profile
    assert settings.nl_tolerance_ppm == 20.0
    assert settings.precursor_mz_tolerance_ppm == 20.0
    assert settings.product_mz_tolerance_ppm == 20.0
    assert settings.product_search_ppm == 50.0
    assert settings.nl_min_intensity_ratio == 0.01
    assert settings.ms1_search_padding_min == 0.35
    assert settings.rt_min is None
    assert settings.rt_max is None
    assert settings.resolver_mode == "local_minimum"
    assert settings.evidence_profile == DEFAULT_EVIDENCE_PROFILE
