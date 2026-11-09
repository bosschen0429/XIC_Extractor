"""Analytical NL evidence is scoped to saved peaks, not the display window."""

import csv
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from tests.delta_mass_review_fixtures import review_fixture, write_discovery
from tools.diagnostics.delta_mass_review import main
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.diagnostics import delta_mass_review_acquisition as acquisition
from xic_extractor.diagnostics.delta_mass_nl_review import assess_review_neutral_losses
from xic_extractor.diagnostics.delta_mass_review_data import (
    ReviewReadError,
    ReviewSpectrum,
    decode_delta_mass_review,
    write_delta_mass_nl_summary,
)
from xic_extractor.discovery.detected_features import DetectedFeature
from xic_extractor.discovery.ms2_events import discovery_events_from_scan
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata, Ms2Scan, Ms2ScanEvent
from xic_extractor.precursor_evidence import Ms1PrecursorObservation
from xic_extractor.xic_models import XICTrace


def test_peak_nl_support_uses_shared_matching_and_retains_competing_peak_ambiguity():
    settings = DiscoverySettings(
        neutral_loss_profiles=(NeutralLossProfile("test", 20.0),)
    )
    c = review_fixture().candidates[0]
    position = replace(c.positions[0], feature_id="peak", peak_rt_window=(1.8, 2.2))
    feature = DetectedFeature(
        "sample-a", "peak", None, 100, 2, 1.8, 2.2, 100, None, "OK", None, "external"
    )
    # One measured 100 -> 80 loss inside, another outside the saved peak,
    # and an unrelated 999 -> 979 loss at the same RT.
    scans = (
        Ms2Scan(11, 2.0, 100, np.array([80.0, 55.0]), np.array([20.0, 100.0]), 100.0),
        Ms2Scan(22, 2.5, 100, np.array([80.0]), np.array([100.0]), 100.0),
        Ms2Scan(33, 2.0, 999, np.array([979.0]), np.array([100.0]), 100.0),
    )
    spectra = tuple(
        ReviewSpectrum(
            s.scan_number,
            s.rt,
            s.precursor_mz,
            None,
            tuple(s.masses),
            tuple(s.intensities),
        )
        for s in scans
    )
    candidate = replace(c, positions=(position,), spectra=spectra)
    seeds = tuple(
        seed
        for scan in scans
        for seed in discovery_events_from_scan(
            scan, raw_file=Path("sample-a.raw"), settings=settings
        )
    )
    result = assess_review_neutral_losses(
        candidate, features=(feature,), seeds=seeds, settings=settings
    )[0]
    assert result.status == "supported"
    assert result.related_scan_numbers == (11,)
    assert [
        (m.scan_number, m.product_mz, m.observed_loss_da, m.product_base_ratio)
        for m in result.matches
    ] == [(11, 80.0, 20.0, 0.2)]
    # A second saved detector interval overlaps: do not invent unique ownership.
    overlap = replace(feature, feature_id="other", rt_start_min=1.9, rt_end_min=2.3)
    ambiguous = assess_review_neutral_losses(
        candidate, features=(feature, overlap), seeds=seeds, settings=settings
    )[0]
    assert ambiguous.status == "ambiguous"
    assert ambiguous.matches[0].status == "ambiguous"


def test_inferred_loss_without_parent_ms1_is_not_self_confirming_support():
    settings = DiscoverySettings(
        neutral_loss_profiles=(NeutralLossProfile("test", 20.0),)
    )
    c = review_fixture().candidates[0]
    position = replace(c.positions[0], feature_id="peak", peak_rt_window=(1.8, 2.2))
    feature = DetectedFeature(
        "sample-a", "peak", None, 100, 2, 1.8, 2.2, 100, None, "OK", None, "external"
    )
    meta = Ms2PrecursorMetadata(
        isolation_width_da=1.6, isolation_offset_da=0, master_scan_number=9
    )
    scan = Ms2Scan(11, 2, 100.3, np.array([80.0]), np.array([100.0]), 100.0, meta)
    candidate = replace(
        c,
        positions=(position,),
        spectra=(ReviewSpectrum(11, 2, 100.3, meta, (80.0,), (100.0,)),),
    )
    seeds = discovery_events_from_scan(
        scan, raw_file=Path("sample-a.raw"), settings=settings
    )
    result = assess_review_neutral_losses(
        candidate, features=(feature,), seeds=seeds, settings=settings
    )[0]
    assert result.status == "unresolved"
    assert result.matches[0].status == "parent_ms1_unavailable"
    assert result.matches[0].observed_loss_da is None


def test_missing_peak_and_no_ms2_are_not_negative_nl_findings():
    settings = DiscoverySettings(
        neutral_loss_profiles=(NeutralLossProfile("test", 20.0),)
    )
    c = review_fixture().candidates[1]
    result = assess_review_neutral_losses(c, features=(), seeds=(), settings=settings)
    assert {p.status for p in result} == {"no_peak_bounds"}
    feature = DetectedFeature(
        "sample-b", "peak", None, 200, 2, 1.8, 2.2, 100, None, "OK", None, "external"
    )
    c = replace(
        c,
        positions=(
            replace(c.positions[0], feature_id="peak", peak_rt_window=(1.8, 2.2)),
        ),
    )
    result = assess_review_neutral_losses(
        c, features=(feature,), seeds=(), settings=settings
    )
    assert result[0].status == "no_ms2"
    partial = replace(
        c, ms2_read_errors=(ReviewReadError(99, (1.8, 2.2), "read failure"),)
    )
    result = assess_review_neutral_losses(
        partial, features=(feature,), seeds=(), settings=settings
    )
    assert result[0].status == "incomplete"


def test_batch_cli_answers_each_candidate_without_manual_scan_selection(
    tmp_path, monkeypatch
):
    discovery = tmp_path / "discovery"
    ids = write_discovery(discovery)
    before = {
        p.relative_to(discovery): p.read_bytes()
        for p in discovery.rglob("*")
        if p.is_file()
    }
    preset = tmp_path / "test.toml"
    preset.write_text(
        'name="Test NL"\ndescription="Analytical test"\ncombine_mode="single"\n'
        '[[tag]]\nstrategy="neutral_loss"\nname="test"\nvalue=20.0\n',
        encoding="utf-8",
    )
    raw = tmp_path / "raw"
    raw.mkdir()
    for name in ("sample-a", "sample-b"):
        (raw / f"{name}.raw").touch()
    parent_reads = []

    class Reader:
        def __init__(self, path):
            self.name = path.stem

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def acquisition_rt_bounds(self):
            return 0.0, 5.0

        def extract_xic_many(self, requests):
            return tuple(
                XICTrace.from_arrays([r.rt_min, 2.0, r.rt_max], [0.0, 100.0, 0.0])
                for r in requests
            )

        def iter_ms2_scans(self, lo, hi):
            assert (
                lo <= 1.8 and hi >= 2.2
            )  # complete peak, not 0.05-min display padding
            precursor = 100.0 if self.name == "sample-a" else 200.0
            product = 80.0 if self.name == "sample-a" else 180.03
            yield Ms2ScanEvent(
                Ms2Scan(99, 2.0, 999.0, np.array([979.0]), np.array([100.0]), 100.0),
                None,
                99,
            )
            yield Ms2ScanEvent(
                Ms2Scan(
                    11,
                    2.0,
                    precursor,
                    np.array([product, 50.0]),
                    np.array([20.0, 100.0]),
                    100.0,
                ),
                None,
                11,
            )
            if self.name == "sample-a":
                meta = Ms2PrecursorMetadata(
                    isolation_width_da=1.6,
                    isolation_offset_da=0.0,
                    master_scan_number=10,
                )
                yield Ms2ScanEvent(
                    Ms2Scan(
                        12,
                        2.0,
                        103.3,
                        np.array([83.0, 50.0]),
                        np.array([30.0, 100.0]),
                        100.0,
                        meta,
                    ),
                    None,
                    12,
                )

        def read_ms1_mass_windows(self, requests):
            parent_reads.append((self.name, len(requests)))
            return tuple(
                Ms1PrecursorObservation(scan, 1.99, (103.0,))
                for scan, _lo, _hi in requests
            )

    monkeypatch.setattr(acquisition, "open_raw", lambda path, **kwargs: Reader(path))
    output = tmp_path / "batch.json"
    assert (
        main(
            [
                "--discovery-dir",
                str(discovery),
                "--raw-dir",
                str(raw),
                "--dll-dir",
                str(tmp_path),
                "--all-candidates",
                "--nl-preset",
                str(preset),
                "--padding-min",
                ".05",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    review = decode_delta_mass_review(json.loads(output.read_text()))
    assert parent_reads == [("sample-a", 1)]
    candidates = {c.candidate_id: c for c in review.candidates}
    assert len(candidates) == 3
    a = candidates[ids[0]]
    b = candidates[ids[2]]
    assert {s.scan_number for s in a.spectra} == {11, 12}
    assert {s.scan_number for s in b.spectra} == {11}
    assert [(n.position_label, n.status) for n in a.neutral_losses] == [
        ("M", "supported"),
        ("partner", "supported"),
        ("third", "no_ms2"),
    ]
    assert a.neutral_losses[1].matches[0].reference_source == "parent_ms1"
    assert b.neutral_losses[0].status == "no_match"
    rows = list(
        csv.DictReader(output.with_suffix(".nl.csv").open(encoding="utf-8-sig"))
    )
    assert [
        (r["candidate_id"], r["intersection_status"], r["supported_positions"])
        for r in rows
    ] == [
        (ids[0], "supported", "M;partner"),
        (ids[1], "not_established", ""),
        (ids[2], "not_established", ""),
    ]
    # NL support cannot promote a saved Delta Mass rejection into the intersection.
    rejected = replace(review, candidates=(replace(a, status="rejected"),))
    write_delta_mass_nl_summary(rejected, output.with_suffix(".nl.csv"))
    row = next(csv.DictReader(output.with_suffix(".nl.csv").open(encoding="utf-8-sig")))
    assert row["intersection_status"] == "delta_mass_rejected"
    assert row["supported_positions"] == "M;partner"
    assert before == {
        p.relative_to(discovery): p.read_bytes()
        for p in discovery.rglob("*")
        if p.is_file()
    }
