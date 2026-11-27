"""Exercise the CLI with real detector/matcher/writers and a fake RAW boundary."""

import csv
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.run_delta_mass_discovery import main
from tests.discovery_store_fixtures import (
    SampleRecords,
    attached,
    event,
    peak,
    write_discovery,
)
from xic_extractor.configuration.delta_mass import (
    DeltaMassPatternSettings,
    DeltaMassPosition,
)
from xic_extractor.configuration.mass_recalibration import (
    VENDOR_MASS_RECALIBRATION_RESULT,
    MassRecalibration,
    MassRecalibrationResult,
    deserialize_mass_recalibration_result,
)
from xic_extractor.discovery import delta_mass_pipeline
from xic_extractor.discovery.delta_mass_patterns import DeltaMassCandidate
from xic_extractor.discovery.delta_mass_publication import write_delta_mass_outputs
from xic_extractor.discovery.models import EventAttachment
from xic_extractor.discovery.store import DISCOVERY_ARTIFACT_NAME, DiscoverySample
from xic_extractor.ms1_scan_index import build_ms1_scan_index
from xic_extractor.xic_models import XICTrace


@pytest.fixture
def raw_cli_case(tmp_path, monkeypatch, composite, ratio_max, signal_gate, mass_policy):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "first.raw").touch()
    preset = tmp_path / "synthetic.toml"
    preset.write_text(
        """name = "Synthetic"
description = "A synthetic method"
combine_mode = "single"
[[tag]]
strategy = "delta_mass"
name = "Synthetic"
pattern_id = "synthetic"
[discovery]
method_label = "synthetic"
anchor_positions = ["base", "heavy"]
discriminating_positions = ["heavy"]
positions = [
 {label="base", delta_da=0, ratio_min=1, ratio_max=1},
 {label="heavy", delta_da=7, ratio_min=0.4, ratio_max=0.6}
]
[discovery.detector]
smoothing_window_sec = 1
""",
        encoding="utf-8",
    )
    preset.write_text(
        preset.read_text()
        .replace("ratio_max=0.6", f"ratio_max={ratio_max}")
        .replace("[discovery]", f'[discovery]\nmass_recalibration = "{mass_policy}"')
    )
    if signal_gate:
        preset.write_text(
            preset.read_text().replace(
                'discriminating_positions = ["heavy"]',
                'discriminating_positions = ["heavy"]\nlowest_position = "heavy"\n'
                'base_support_positions = ["heavy"]\nmin_base_support = 0.25',
            )
        )
    extra_args = []
    if composite:
        text = preset.read_text().replace(
            'combine_mode = "single"', 'combine_mode = "intersection"'
        )
        text = text.replace(
            "[discovery]",
            '[[tag]]\nstrategy = "neutral_loss"\nname = "DNA_dR"\n'
            "value = 116.0474\n[discovery]",
        )
        preset.write_text(text)
        first = peak("first", peak_id="cid-first", peak_mz=300, ms1_apex_rt=1 + 4 / 60)
        second = peak(
            "second",
            peak_id="cid-second",
            peak_mz=600,
            ms1_apex_rt=1 + 4 / 60,
            status="no_nl_event",
        )
        cid = write_discovery(
            tmp_path / "cid",
            [
                SampleRecords(
                    "first",
                    raw_dir / "first.raw",
                    peaks=[first],
                    attachments=attached(first, event("first", precursor_mz=300)),
                ),
                SampleRecords(
                    "second",
                    raw_dir / "second.raw",
                    peaks=[second],
                    attachments=[
                        EventAttachment(
                            event("second", precursor_mz=600),
                            peak_id=second.peak_id,
                            unattached_reason="rt_outside_peak",
                        )
                    ],
                ),
            ],
        )
        extra_args = ["--cid-nl-discovery", str(cid)]
    batches = []

    class Raw:
        RunHeaderEx = SimpleNamespace(FirstSpectrum=1, LastSpectrum=9)

        def __init__(self, path):
            self.base_mz = 300 if path.stem == "first" else 600
            self.height = 1e6 if path.stem == "first" else 2e6
            self.times = np.arange(9) / 60 + 1
            self.signal = np.array([0.03, 0.1, 0.3, 0.7, 1, 0.7, 0.3, 0.1, 0.03])
            self.mass_recalibration_result = MassRecalibrationResult(
                "applied" if mass_policy == "easy_ic_restore" else "none",
                policy=MassRecalibration(mass_policy),
                baseline_lm=(1.0 if path.stem == "first" else 2.0)
                if mass_policy != "none"
                else None,
                n_anchor=3 if mass_policy != "none" else 0,
            )

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def GetFilterForScanNumber(self, _):
            return SimpleNamespace(MSOrder="Ms")

        def RetentionTimeFromScanNumber(self, scan):
            return self.times[scan - 1]

        def GetCentroidStream(self, scan, _):
            return SimpleNamespace(
                Masses=[self.base_mz, self.base_mz + 7],
                Intensities=self.signal[scan - 1] * self.height * np.array([1, 0.5]),
            )

        def GetSimplifiedScan(self, scan):
            return SimpleNamespace(
                Masses=[
                    self.base_mz - 0.025,
                    self.base_mz,
                    self.base_mz + 0.025,
                    self.base_mz + 7 - 0.025,
                    self.base_mz + 7,
                    self.base_mz + 7 + 0.025,
                ],
                Intensities=self.signal[scan - 1]
                * self.height
                * np.array([0.25, 1, 0.25, 0.125, 0.5, 0.125]),
            )

        def read_ms1_scan_index(self, *, centroid):
            return build_ms1_scan_index(self, centroid=centroid)

        def extract_xic_many(self, requests):
            batches.append((self.base_mz, len(requests)))
            times = self.times
            signal = self.signal
            if signal_gate:
                seconds = np.arange(-30, 31, dtype=float)
                times = 1 + 4 / 60 + seconds / 60
                signal = np.exp(-0.5 * (seconds / 2) ** 2)
            return tuple(
                XICTrace(
                    times,
                    signal * self.height * (1 if r.mz == self.base_mz else 0.5),
                )
                for r in requests
            )

    opened = []

    def open_reader(path, dll_dir, *, mass_recalibration):
        opened.append(path)
        assert mass_recalibration == MassRecalibration(mass_policy)
        return Raw(path)

    monkeypatch.setattr(delta_mass_pipeline, "open_raw", open_reader)
    return raw_dir, preset, extra_args, batches, opened


@pytest.mark.parametrize("signal_gate", [False, True])
@pytest.mark.parametrize("mass_policy", ["none", "easy_ic_restore"])
@pytest.mark.parametrize("composite", [False, True])
@pytest.mark.parametrize(
    "ratio_max, expected_ratio_status", [(0.6, "within_range"), (0.45, "above_range")]
)
def test_raw_cli_preserves_sample_attribution_and_batches_trace_acquisition(
    tmp_path,
    raw_cli_case,
    composite,
    ratio_max,
    expected_ratio_status,
    signal_gate,
    mass_policy,
):
    raw_dir, preset, extra_args, batches, _ = raw_cli_case
    (raw_dir / "second.raw").touch()
    output = tmp_path / "result"
    assert (
        main(
            [
                "--raw-dir",
                str(raw_dir),
                "--dll-dir",
                str(tmp_path),
                "--preset",
                str(preset),
                "--output-dir",
                str(output),
                *extra_args,
            ]
        )
        == 0
    )
    with (output / "delta_mass_candidates.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["sample"], float(r["mz"]), float(r["height"])) for r in rows] == [
        ("first", 300, 1e6),
        ("second", 600, 2e6),
    ]
    assert batches == [(300, 4 if signal_gate else 2), (600, 4 if signal_gate else 2)]
    assert [r["cid_nl_peak_id"] for r in rows] == (
        ["cid-first", ""] if composite else ["", ""]
    )
    assert [(r["positions_found"], r["positions"]) for r in rows] == [
        ("2", "base;heavy")
    ] * 2
    with closing(sqlite3.connect(output / DISCOVERY_ARTIFACT_NAME)) as db:
        heavy = db.execute(
            "SELECT position_status,height_ratio_to_base FROM delta_mass_positions "
            "WHERE position_label='heavy' ORDER BY candidate"
        ).fetchall()
        (method,) = db.execute("SELECT method FROM delta_mass_run").fetchone()
        saved = db.execute(
            "SELECT mass_recalibration_result FROM samples ORDER BY id"
        ).fetchall()
    assert heavy == [("pass", 0.5)] * 2
    method = json.loads(method)
    assert method["mass_recalibration"] == mass_policy
    assert [deserialize_mass_recalibration_result(r).baseline_lm for (r,) in saved] == (
        [1.0, 2.0] if mass_policy != "none" else [None, None]
    )
    assert method["height_ratio_policy"] == "evidence_only"
    assert method["base_support_positions"] == (["heavy"] if signal_gate else [])
    assert method["min_base_support"] == 0.25
    assert method["detector"]["spectrum_mode"] == "vendor_centroid"
    assert {p.name for p in output.iterdir()} == {
        DISCOVERY_ARTIFACT_NAME,
        "delta_mass_candidates.csv",
    }


@pytest.mark.parametrize(
    ("composite", "ratio_max", "signal_gate", "mass_policy"),
    [
        (True, 0.6, False, "none"),
    ],
)
def test_selected_raw_matches_the_single_file_baseline(tmp_path, raw_cli_case):
    raw_dir, preset, evidence, _, opened = raw_cli_case
    first = raw_dir / "first.raw"
    settings = ["--dll-dir", str(tmp_path), "--preset", str(preset)]
    baseline, selected = tmp_path / "baseline", tmp_path / "selected"
    assert (
        main(
            [
                "--raw-dir",
                str(raw_dir),
                "--output-dir",
                str(baseline),
                *settings,
                *evidence,
            ]
        )
        == 0
    )
    (raw_dir / "second.raw").touch()
    opened.clear()
    args = ["--raw", str(first), "--output-dir", str(selected), *settings]
    assert main(args) == 2
    assert not opened
    assert main([*args, *evidence]) == 0
    assert opened == [first]
    assert (selected / "delta_mass_candidates.csv").read_bytes() == (
        baseline / "delta_mass_candidates.csv"
    ).read_bytes()
    assert _dump(selected) == _dump(baseline)


def _dump(directory):
    with closing(sqlite3.connect(directory / DISCOVERY_ARTIFACT_NAME)) as db:
        return list(db.iterdump())


def test_cid_nl_join_records_unmatched_reason_and_reproduces_from_published_table(
    tmp_path,
):
    """Each unmatched base says why; the written table re-joins identically.

    The base m/z sits 19.99999 ppm from its peak, so a ``.6g`` display value
    (379.123) would fall outside 20 ppm and flip the match on recomputation.
    """
    base_mz = 379.1234
    edge_peak_mz = base_mz * (1 + 19.99999e-6)
    cid_rows = tuple(
        peak(
            sample,
            peak_id=peak_id,
            status=status,
            peak_mz=mz,
            ms1_apex_rt=rt,
            matched_tag_names=(
                ("Other",) if peak_id == "unrelated-tag" else ("DNA_dR",)
            ),
        )
        for sample, peak_id, status, mz, rt in (
            ("edge", "matched", "admitted", edge_peak_mz, 2.0),
            ("crowded", "overlapping", "no_nl_event", 500.0, 3.0),
            ("crowded", "split", "doubly_charged", 500.0, 3.1),
            ("late", "late-peak", "admitted", 600.0, 5.0),
            ("alone", "other-ion", "admitted", 700.0, 2.0),
            ("alone", "unrelated-tag", "admitted", 800.0, 2.0),
        )
    )
    settings = DeltaMassPatternSettings(
        pattern_id="synthetic",
        method_label="synthetic",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("heavy", 7, 0.4, 0.6),
        ),
        anchor_positions=("base",),
        discriminating_positions=(),
    )

    def candidate(sample, mz, rt):
        return DeltaMassCandidate(
            sample_stem=sample,
            method_label="synthetic",
            pattern_id="synthetic",
            candidate_id=sample,
            anchor_position="base",
            anchor_feature_id=sample,
            base_mz=mz,
            base_rt_apex_min=rt,
            base_height=1.0,
            positions=(),
        )

    joined = delta_mass_pipeline._join_cid_nl(
        (
            candidate("edge", base_mz, 2.0 + 29.999 / 60),
            candidate("crowded", 500.0, 3.0),
            candidate("late", 600.0, 3.0),
            candidate("alone", 800.0, 2.0),
        ),
        cid_rows,
    )
    assert [
        (
            c.cid_nl_base_status,
            c.cid_nl_base_peak_id,
            c.cid_nl_base_unmatched_reason,
            c.cid_nl_base_peak_status,
        )
        for c in joined
    ] == [
        ("matched", "matched", "", ""),
        ("unmatched", "", "not_admitted", "doubly_charged;no_nl_event"),
        ("unmatched", "", "outside_rt_tolerance", ""),
        ("unmatched", "", "no_cid_nl_peak", ""),
    ]
    outputs = write_delta_mass_outputs(
        tmp_path / "dm",
        method_label=settings.method_label,
        method={},
        samples=[
            DiscoverySample(
                c.sample_stem,
                tmp_path / f"{c.sample_stem}.raw",
                None,
                VENDOR_MASS_RECALIBRATION_RESULT,
            )
            for c in joined
        ],
        candidates=joined,
        features={},
    )
    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        published = list(csv.DictReader(handle))
    assert [r["cid_nl_peak_id"] for r in published] == ["matched", "", "", ""]
    rejoined = delta_mass_pipeline._join_cid_nl(
        tuple(
            candidate(r["sample"], float(r["mz"]), float(r["rt_min"]))
            for r in published
        ),
        cid_rows,
    )
    assert [
        (c.cid_nl_base_status, c.cid_nl_base_peak_id, c.cid_nl_base_unmatched_reason)
        for c in rejoined
    ] == [
        (c.cid_nl_base_status, c.cid_nl_base_peak_id, c.cid_nl_base_unmatched_reason)
        for c in joined
    ]
