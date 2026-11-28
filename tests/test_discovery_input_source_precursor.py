"""The representative event's precursor, read back from the event table."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.discovery_ms1_capture_fixtures import _seed, with_scan
from tests.discovery_peak_fixtures import _peak
from tests.discovery_store_fixtures import (
    SampleRecords,
    acquisition_source,
    write_discovery,
)
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import EventAttachment
from xic_extractor.discovery.store import read_discovery_batch


@pytest.mark.parametrize(
    ("stronger_attaches", "expected"),
    [(False, 300.1000), (True, 300.1100)],
)
def test_representative_precursor_follows_discovery_choice_within_one_scan(
    tmp_path: Path, stronger_attaches: bool, expected: float
) -> None:
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
    stronger = with_scan(
        weaker, precursor_mz=300.1100, product_mz=184.0626, product_intensity=900.0
    )
    source = DiscoveryInputSource(
        read_discovery_batch(
            write_discovery(
                tmp_path,
                [
                    SampleRecords(
                        raw_path.stem,
                        raw_path,
                        peaks=(peak,),
                        acquisition_source=acquisition_source(
                            tmp_path / "sources", raw_path.stem
                        ),
                        attachments=(
                            EventAttachment(event=weaker, peak_id=peak.peak_id),
                            EventAttachment(
                                event=stronger,
                                peak_id=peak.peak_id,
                                unattached_reason=""
                                if stronger_attaches
                                else "rt_outside_peak",
                            ),
                        ),
                    )
                ],
            )
        )
    )

    assert source.precursor_mz_by_candidate == {peak.peak_id: expected}
