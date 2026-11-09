"""Per-scan resolution of strict Discovery events."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    _FakeRaw,
    _settings,
)


@pytest.mark.parametrize("reverse_products", [False, True])
def test_scan_event_prefers_independent_direct_loss_over_stronger_inference(
    tmp_path: Path, reverse_products: bool
) -> None:
    from xic_extractor.discovery.ms2_events import discovery_events_from_scan
    from xic_extractor.discovery.strict_event_receipts import (
        resolve_discovery_scan_events,
    )

    source_raw = _FakeRaw((8.0,))
    products = (383.9526, 383.9826)
    intensities = (11900.0, 12000.0)
    if reverse_products:
        products = products[::-1]
        intensities = intensities[::-1]
    scan = replace(
        source_raw.scans[0],
        masses=np.asarray(products),
        intensities=np.asarray(intensities),
        base_peak=12000.0,
    )
    settings = _settings()
    seeds = discovery_events_from_scan(
        scan, raw_file=tmp_path / "S1.RAW", settings=settings
    )
    assert len(seeds) == 2
    assert {seed.precursor_mz_basis for seed in seeds} == {
        "scan_precursor",
        "product_plus_neutral_loss",
    }
    forward = resolve_discovery_scan_events(seeds, settings=settings)
    reverse = resolve_discovery_scan_events(seeds[::-1], settings=settings)
    assert forward == reverse
    (event,) = forward
    assert event.precursor_mz_basis == "scan_precursor"
    assert event.product_mz == 383.9526
    assert event.product_intensity == 11900.0
    assert event.assess_precursor_loss(settings.nl_tolerance_ppm).supports_nl
