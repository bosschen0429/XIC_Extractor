"""Canonical scan receipts, exact replay and event enumeration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    _current_candidates,
    _FakeRaw,
    _settings,
)


@pytest.mark.parametrize("reverse_products", [False, True])
def test_scan_event_prefers_independent_direct_loss_over_stronger_inference(
    tmp_path: Path, reverse_products: bool
) -> None:
    from xic_extractor.discovery.ms2_seeds import discovery_seeds_from_scan
    from xic_extractor.discovery.strict_seed_receipts import (
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
    seeds = discovery_seeds_from_scan(
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


def test_unproven_inferred_members_keep_independent_scan_receipts(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.strict_seed_receipts import (
        read_complete_discovery_scan_seeds,
    )

    source_raw = _FakeRaw((7.98, 8.0, 8.02))
    source_raw.scans = tuple(
        replace(
            scan,
            precursor_mz=scan.precursor_mz + 0.5,
            masses=scan.masses + offset,
            intensities=np.asarray((800.0 + index * 100.0, 200.0)),
        )
        for index, (scan, offset) in enumerate(
            zip(source_raw.scans, (-0.012, -0.005, 0.002), strict=True)
        )
    )
    settings = replace(_settings(), neutral_loss_profile=None)
    candidates = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    # One measured peak combines the events, never their exact scan receipts.
    assert len(candidates) == 1
    assert {scan for c in candidates for scan in c.seed_scan_ids} == {
        scan.scan_number for scan in source_raw.scans
    }
    for candidate in candidates:
        assert candidate.admission_reason == "no_attached_event"
        assert candidate.precursor_mz_basis == "product_plus_neutral_loss"
        seeds = read_complete_discovery_scan_seeds(
            source_raw,
            candidates=(candidate,),
            settings=settings,
            include_inferred=True,
        )
        # Strict replay keeps each located scan only within its own receipt.
        replayed = {scan.scan_number for scan, _ in seeds}
        assert replayed <= set(candidate.seed_scan_ids)
        assert candidate.best_ms2_scan_id in replayed
        products_by_scan = {
            scan.scan_number: float(scan.masses[0]) for scan in source_raw.scans
        }
        assert all(
            seed.product_mz == products_by_scan[scan.scan_number]
            and seed.precursor_mz == seed.product_mz + 116.0474
            and not seed.supports_direct_nl
            for scan, seed in seeds
        )


@pytest.mark.parametrize(
    ("scan_rts", "shifts", "supported_scan_count"),
    [
        ((7.98, 8.0, 8.02, 8.04), (0.0, 0.0, 0.008, 0.008), 4),
        ((7.98, 8.0, 8.05, 8.07), (0.0, 0.0, 0.0085, 0.0112), 3),
    ],
    ids=["product-beyond-tolerance", "precursor-beyond-final-window"],
)
def test_shared_apex_merge_keeps_each_absorbed_scan_receipt(
    tmp_path: Path,
    scan_rts: tuple[float, ...],
    shifts: tuple[float, ...],
    supported_scan_count: int,
) -> None:
    from xic_extractor.discovery.strict_seed_receipts import (
        read_complete_discovery_scan_seeds,
    )

    source_raw = _FakeRaw(scan_rts)
    source_raw.intensity = 1000.0 * np.exp(
        -0.5 * ((source_raw.rt - sum(scan_rts) / len(scan_rts)) / 0.06) ** 2
    )
    # The later scans read the same MS1 peak with a precursor and product
    # shifted by scatter; their product lies beyond 20 ppm of the earlier one.
    source_raw.scans = tuple(
        replace(
            scan,
            precursor_mz=scan.precursor_mz + shift,
            masses=scan.masses + shift,
        )
        for scan, shift in zip(source_raw.scans, shifts, strict=True)
    )
    settings = replace(_settings(), neutral_loss_profile=None)
    candidates = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    # A scan beyond the precursor tolerance of the channel anchor measures its
    # own ion channel rather than being absorbed.
    candidate = max(candidates, key=lambda item: len(item.seed_scan_ids))
    assert len(candidate.seed_scan_ids) == supported_scan_count

    seeds = read_complete_discovery_scan_seeds(
        source_raw,
        candidates=(candidate,),
        settings=settings,
    )

    # An absorbed scan outside the final precursor window still reconstructs
    # against its own receipt but cannot support this candidate's mass.
    assert [scan.scan_number for scan, _ in seeds] == list(
        candidate.seed_scan_ids[:supported_scan_count]
    )


@pytest.mark.parametrize("variant", ["missing", "duplicate", "parse_error"])
def test_declared_strict_scan_subset_rejects_non_exact_events(variant: str) -> None:
    from types import SimpleNamespace

    from xic_extractor.discovery.strict_seed_receipts import (
        StrictSeedReceiptError,
        _read_exact_scans,
    )
    from xic_extractor.ms2_scan_models import Ms2ScanEvent

    candidate = SimpleNamespace(
        peak_id="peak-1",
        seed_scan_ids=(11, 12),
        rt_seed_min=8.0,
        rt_seed_max=8.2,
    )

    def scan(number):
        return SimpleNamespace(scan_number=number)

    if variant == "missing":
        events = (Ms2ScanEvent(scan(11), None, 11),)
    elif variant == "duplicate":
        events = (
            Ms2ScanEvent(scan(11), None, 11),
            Ms2ScanEvent(scan(11), None, 11),
            Ms2ScanEvent(scan(12), None, 12),
        )
    else:
        events = (
            Ms2ScanEvent(None, "malformed scan", 11),
            Ms2ScanEvent(scan(12), None, 12),
        )

    class _SubsetRaw:
        def iter_ms2_scan_subset(self, _rt_min, _rt_max, _scan_numbers):
            return iter(events)

    with pytest.raises(StrictSeedReceiptError, match="not exact"):
        _read_exact_scans(_SubsetRaw(), candidate)


def test_scan_seed_enumeration_runs_once_per_declared_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xic_extractor.discovery import strict_seed_receipts

    source_raw = _FakeRaw((7.98, 8.0, 8.02))
    settings = _settings()
    (candidate,) = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    assert len(candidate.seed_scan_ids) > 1

    enumerated: list[int] = []
    enumerate_seeds = strict_seed_receipts.discovery_seeds_from_scan

    def counted(scan, **kwargs):
        enumerated.append(scan.scan_number)
        return enumerate_seeds(scan, **kwargs)

    monkeypatch.setattr(strict_seed_receipts, "discovery_seeds_from_scan", counted)

    # Candidates declaring the same scan share one enumeration, and the
    # candidate-specific receipts stay identical to the single-candidate read.
    repeated = strict_seed_receipts.read_complete_discovery_scan_seeds(
        source_raw,
        candidates=(candidate, candidate),
        settings=settings,
    )
    assert sorted(enumerated) == sorted(set(candidate.seed_scan_ids))

    enumerated.clear()
    single = strict_seed_receipts.read_complete_discovery_scan_seeds(
        source_raw,
        candidates=(candidate,),
        settings=settings,
    )
    assert [scan.scan_number for scan, _ in repeated] == [
        scan.scan_number for scan, _ in single
    ]
    assert [seed for _, seed in repeated] == [seed for _, seed in single]


def test_strict_replay_retains_two_recorded_precursors_from_one_scan(tmp_path):
    from xic_extractor.discovery.strict_seed_receipts import (
        read_complete_discovery_scan_seeds,
    )
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata

    source_raw = _FakeRaw((7.98, 8.02))
    source_raw.scans = tuple(
        replace(
            scan,
            precursor_metadata=Ms2PrecursorMetadata(
                monoisotopic_mz=scan.precursor_mz - 1.0035,
                charge=1,
                isolation_width_da=1.6,
            ),
            masses=np.concatenate((scan.masses, scan.masses - 1.0035)),
            intensities=np.concatenate((scan.intensities, scan.intensities)),
        )
        for scan in source_raw.scans
    )
    settings = _settings()
    candidates = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    assert len(candidates) == 2
    seeds = read_complete_discovery_scan_seeds(
        source_raw, candidates=candidates, settings=settings
    )
    assert len(seeds) == 4
    assert {seed.precursor_mz_basis for _, seed in seeds} == {
        "scan_precursor",
        "scan_monoisotopic",
    }
    assert all(
        seed.precursor_metadata == scan.precursor_metadata for scan, seed in seeds
    )
    assert all(
        seed.supports_direct_nl == (seed.precursor_mz_basis == "scan_precursor")
        for _, seed in seeds
    )


@pytest.mark.parametrize("preserve_configured_losses", [False, True])
def test_same_peak_same_scan_products_keep_exact_canonical_seed(
    tmp_path: Path, preserve_configured_losses: bool
) -> None:
    import json

    from xic_extractor.discovery.strict_seed_receipts import (
        read_complete_discovery_scan_seeds,
    )

    source_raw = _FakeRaw((8.0,), scan_precursor_mz=255.15606689453125)
    canonical_product = 139.09368896484375
    absorbed_product = 139.09832763671875
    source_raw.scans = (
        replace(
            source_raw.scans[0],
            masses=np.asarray((canonical_product, absorbed_product)),
            intensities=np.asarray((800.0, 200.0)),
        ),
    )
    settings = _settings()
    candidates = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    # Spectral alternatives from one scan share a peak and one canonical event.
    assert len(candidates) == 1
    assert candidates[0].product_mz == canonical_product
    for candidate in candidates:
        assert candidate.admission_reason == "no_attached_event"
        evidence = json.loads(candidate.tag_evidence_json)["DNA_dR"]
        assert evidence["scan_ids"] == [candidate.best_ms2_scan_id]
        assert evidence["scan_count"] == 2
        seeds = read_complete_discovery_scan_seeds(
            source_raw,
            candidates=(candidate,),
            settings=settings,
            include_inferred=True,
            preserve_configured_losses=preserve_configured_losses,
        )
        expected_products = (
            {canonical_product, absorbed_product}
            if preserve_configured_losses
            else {canonical_product}
        )
        assert {seed.product_mz for _, seed in seeds} == expected_products
        assert all(seed.precursor_mz == seed.product_mz + 116.0474 for _, seed in seeds)


@pytest.mark.parametrize("preserve_configured_losses", [False, True])
def test_duplicate_exact_canonical_seed_is_rejected(
    tmp_path: Path, monkeypatch, preserve_configured_losses: bool
) -> None:
    from xic_extractor.discovery import strict_seed_receipts

    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    (candidate,) = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    enumerate_seeds = strict_seed_receipts.discovery_seeds_from_scan

    def duplicated(scan, **kwargs):
        seeds = enumerate_seeds(scan, **kwargs)
        return seeds + seeds

    monkeypatch.setattr(strict_seed_receipts, "discovery_seeds_from_scan", duplicated)
    with pytest.raises(strict_seed_receipts.StrictSeedReceiptError, match="not unique"):
        strict_seed_receipts.read_complete_discovery_scan_seeds(
            source_raw,
            candidates=(candidate,),
            settings=settings,
            preserve_configured_losses=preserve_configured_losses,
        )
