import csv
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.discovery_csv_fixtures import _peak, _read_csv
from tests.discovery_ms1_capture_fixtures import _seed
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.discovery.csv_reader import (
    read_discovery_events_csv,
    read_discovery_peaks_csv,
)
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
from xic_extractor.discovery.priority import order_discovery_peak_bindings
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.precursor_evidence import Ms1PrecursorObservation


def test_peak_table_writes_each_peak_fact_once() -> None:
    # Event values live in the event table; the peak's measurement is
    # Alignment's, from these capture conditions.
    assert DISCOVERY_PEAK_COLUMNS == (
        "status",
        "evidence_score",
        "attached_event_count",
        "peak_id",
        "sample_stem",
        "peak_mz",
        "precursor_mz_basis",
        "ms1_apex_mz",
        "ms1_apex_rt",
        "ms1_search_rt_min",
        "ms1_search_rt_max",
        "query_rt_min",
        "best_ms2_scan_id",
        "neutral_loss_tag",
        "matched_tag_names",
    )


def test_event_table_ends_with_the_located_peak_and_attachment() -> None:
    assert DISCOVERY_EVENT_COLUMNS[-3:] == (
        "xic_mz",
        "peak_id",
        "unattached_reason",
    )


def test_peak_csv_round_trips_through_the_reader(tmp_path: Path) -> None:
    peak = _peak(
        matched_tag_names=("DNA_dR", "R"),
        status="overlap",
        ms1_apex_mz=258.1083,
    )
    path = write_discovery_peaks_csv(tmp_path / "nested" / "peaks.csv", [peak])

    (row,) = _read_csv(path)
    assert row["status"] == "overlap"
    assert row["matched_tag_names"] == "DNA_dR;R"
    (restored,) = read_discovery_peaks_csv(path)
    assert restored == peak


def test_peak_csv_writes_a_header_without_peaks(tmp_path: Path) -> None:
    path = write_discovery_peaks_csv(tmp_path / "peaks.csv", [])

    with path.open(newline="", encoding="utf-8") as handle:
        assert list(csv.reader(handle)) == [list(DISCOVERY_PEAK_COLUMNS)]


def test_peak_rows_keep_the_order_discovery_settled(tmp_path: Path) -> None:
    peaks = [_peak(peak_id=peak_id) for peak_id in ("b", "c", "a")]

    path = write_discovery_peaks_csv(tmp_path / "peaks.csv", peaks)

    assert [row["peak_id"] for row in _read_csv(path)] == ["b", "c", "a"]


def test_peak_rows_follow_the_review_queue() -> None:
    settings = DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
    )

    def binding(
        peak_id: str,
        *,
        status: str = "admitted",
        attached: int = 2,
        score: int = 50,
        intensity: float = 1000.0,
        event_rt: float = 7.84,
        shoulder: bool = False,
    ) -> _OrderedBinding:
        event = _seed(scan_number=6095, rt=event_rt, product_intensity=intensity)
        return _OrderedBinding(
            peak=_peak(
                peak_id=peak_id,
                status=status,
                attached_event_count=attached,
                evidence_score=score,
                located_events=(event,),
            ),
            physical_evaluation=SimpleNamespace(
                state="available",
                requires_overlap_review=shoulder,
                facts=SimpleNamespace(area_counts_seconds=1000.0),
            ),
            final_ordinal=0,
        )

    ordered = order_discovery_peak_bindings(
        (
            binding("blank", status="blank", score=99),
            binding("single", attached=1, score=90),
            binding("far", event_rt=7.0, score=95),
            binding("shoulder", shoulder=True, score=95),
            binding("lead-weak", score=40),
            binding("lead-strong", score=80),
            binding("lead-strong-intense", score=80, intensity=5000.0),
        ),
        settings=settings,  # type: ignore[arg-type]
    )

    # Two attached events at the event RT on an unshouldered peak lead; then
    # stronger evidence; blank background last.
    assert [item.peak.peak_id for item in ordered] == [
        "lead-strong-intense",
        "lead-strong",
        "lead-weak",
        "far",
        "shoulder",
        "single",
        "blank",
    ]
    assert [item.final_ordinal for item in ordered] == list(range(7))


@dataclass(frozen=True)
class _OrderedBinding:
    peak: object
    physical_evaluation: object
    final_ordinal: int


def test_peak_table_from_an_older_discovery_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "peaks.csv"
    path.write_text(
        ",".join(("review_priority", *DISCOVERY_PEAK_COLUMNS)) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="regenerate Discovery"):
        read_discovery_peaks_csv(path)


def test_peak_csv_escapes_text_for_csv_and_spreadsheets(tmp_path: Path) -> None:
    peak = _peak(
        sample_stem='Sample, "quoted"',
        neutral_loss_tag="@review",
    )

    (row,) = _read_csv(write_discovery_peaks_csv(tmp_path / "peaks.csv", [peak]))

    assert row["sample_stem"] == 'Sample, "quoted"'
    assert row["neutral_loss_tag"] == "'@review"
    assert format_discovery_csv_value("raw_file", Path("=cmd.raw")) == "'=cmd.raw"


def test_tag_names_formatter_does_not_join_string_characters() -> None:
    assert format_discovery_csv_value("matched_tag_names", ("A", "B")) == "A;B"
    assert format_discovery_csv_value("matched_tag_names", "A;B") == "A;B"


def test_event_table_keeps_the_located_peak_of_an_unattached_event(
    tmp_path: Path,
) -> None:
    located = "Sample@mz500_rt7.6000"
    events = [
        EventAttachment(
            event=_seed(scan_number=20, rt=8.2),
            xic_mz=500.0,
            precursor_verified=True,
            peak_id=located,
            unattached_reason="rt_outside_peak",
        ),
        EventAttachment(
            event=_seed(scan_number=10, rt=7.6),
            xic_mz=500.0,
            precursor_verified=True,
            peak_id=located,
        ),
        EventAttachment(
            event=_seed(scan_number=30, rt=9.4),
            xic_mz=500.0,
            precursor_verified=True,
            unattached_reason="no_peak_at_rt",
        ),
    ]

    rows = _read_csv(write_discovery_events_csv(tmp_path / "events.csv", events))

    assert [
        (row["ms2_scan_number"], row["peak_id"], row["unattached_reason"])
        for row in rows
    ] == [
        ("10", located, ""),
        ("20", located, "rt_outside_peak"),
        ("30", "", "no_peak_at_rt"),
    ]


def test_an_event_without_a_located_peak_records_why() -> None:
    with pytest.raises(ValueError, match="without a located peak needs a reason"):
        EventAttachment(
            event=_seed(scan_number=10, rt=7.6),
            xic_mz=500.0,
            precursor_verified=True,
        )


def test_event_table_round_trips_the_evidence_alignment_assesses(
    tmp_path: Path,
) -> None:
    inferred = replace(
        _seed(scan_number=33623, rt=38.75, precursor_mz=961.4289),
        observed_loss_error_ppm=None,
        precursor_mz_basis="product_plus_neutral_loss",
        scan_precursor_mz=961.4482,
        precursor_metadata=Ms2PrecursorMetadata(
            monoisotopic_mz=961.4478,
            charge=1,
            isolation_width_da=1.0,
            isolation_offset_da=-0.25,
            master_scan_number=33620,
        ),
        # Two centroids is the ambiguous parent ion the old table left blank.
        ms1_precursor_observation=Ms1PrecursorObservation(
            33620, 38.7412, (961.4281, 961.4302)
        ),
    )
    empty_parent = replace(
        inferred,
        scan_number=33700,
        ms1_precursor_observation=Ms1PrecursorObservation(33698, 38.8, ()),
    )
    recorded = _seed(scan_number=33706, rt=38.9)
    written = [
        EventAttachment(
            event=inferred,
            xic_mz=961.4478,
            precursor_verified=False,
            peak_id="S@mz961.4478_rt38.7477",
            unattached_reason="parent_ms1_ion_ambiguous",
        ),
        EventAttachment(
            event=empty_parent,
            xic_mz=961.4478,
            precursor_verified=False,
            peak_id="S@mz961.4478_rt38.7477",
            unattached_reason="parent_ms1_ion_missing",
        ),
        EventAttachment(
            event=recorded,
            xic_mz=500.0,
            precursor_verified=True,
            peak_id="S@mz500_rt38.9",
        ),
    ]
    path = write_discovery_events_csv(tmp_path / "events.csv", written)

    restored = read_discovery_events_csv(
        path,
        raw_file=inferred.raw_file,
        sample_stem=inferred.sample_stem,
        neutral_loss_by_tag={"DNA_dR": inferred.configured_neutral_loss_da},
    )

    by_scan = {item.event.scan_number: item for item in restored}
    for item in written:
        back = by_scan[item.event.scan_number]
        assert back.peak_id == item.peak_id
        assert back.unattached_reason == item.unattached_reason
        assert back.precursor_verified == item.precursor_verified
        assert back.event == item.event
    assert by_scan[33623].event.ms1_precursor_observation.centroid_mzs == (
        961.4281,
        961.4302,
    )
    assert by_scan[33700].event.ms1_precursor_observation.centroid_mzs == ()
    assert by_scan[33706].event.ms1_precursor_observation is None
    assert by_scan[33706].event.precursor_metadata is None


def test_event_table_from_an_older_discovery_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    path.write_text(
        ",".join(
            column
            for column in DISCOVERY_EVENT_COLUMNS
            if column != "parent_ms1_centroid_mzs"
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="regenerate Discovery"):
        read_discovery_events_csv(
            path,
            raw_file=Path("S.raw"),
            sample_stem="S",
            neutral_loss_by_tag={"DNA_dR": 116.0474},
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
