"""The representative event's precursor, read back from the event table."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.cid_nl_runtime_fixtures import build_discovery_receipts
from tests.discovery_csv_fixtures import _peak
from tests.discovery_ms1_capture_fixtures import _seed
from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
from xic_extractor.discovery.csv_writer import write_discovery_events_csv
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import EventAttachment


@pytest.mark.parametrize(
    ("stronger_attaches", "expected"),
    [(False, 300.1000), (True, 300.1100)],
)
def test_representative_precursor_follows_discovery_choice_within_one_scan(
    tmp_path: Path, stronger_attaches: bool, expected: float
) -> None:
    # One MS2 scan holds two inferred events of the peak's tag. Discovery
    # chooses among attached events when any attaches, then by abundance; the
    # stronger event here does not attach unless the case says so.
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"representative-precursor")
    peak = _peak(
        peak_id="TumorBC2312_DNA@mz300.1_rt7.8000",
        sample_stem="TumorBC2312_DNA",
        peak_mz=300.1,
        best_ms2_scan_id=10,
    )
    weaker = _seed(
        scan_number=10,
        rt=7.8,
        precursor_mz=300.1000,
        product_mz=184.0526,
        product_intensity=500.0,
    )
    stronger = replace(
        weaker, precursor_mz=300.1100, product_mz=184.0626, product_intensity=900.0
    )
    write_discovery_events_csv(
        tmp_path / "events.csv",
        (
            EventAttachment(
                event=weaker,
                xic_mz=300.1,
                precursor_verified=True,
                peak_id=peak.peak_id,
            ),
            EventAttachment(
                event=stronger,
                xic_mz=300.1,
                precursor_verified=True,
                peak_id=peak.peak_id,
                unattached_reason="" if stronger_attaches else "rt_outside_peak",
            ),
        ),
    )
    receipts = build_discovery_receipts(tmp_path, (raw_path,), {raw_path.stem: (peak,)})
    source = DiscoveryInputSource(
        DiscoveryBatchInput(
            sample_order=(raw_path.stem,),
            peak_csvs={
                raw_path.stem: tmp_path / f"{raw_path.stem}.owner-candidates.csv"
            },
            raw_files={raw_path.stem: raw_path},
            event_csvs={raw_path.stem: tmp_path / "events.csv"},
            peak_receipts=receipts,
        )
    )

    assert source.precursor_mz_by_candidate == {peak.peak_id: expected}
