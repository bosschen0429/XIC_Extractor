"""Exercise the CLI with real detector/matcher/writers and a fake RAW boundary."""

import csv
import json
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.run_delta_mass_discovery import main
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    MassRecalibrationResult,
    deserialize_mass_recalibration_result,
)
from xic_extractor.discovery import delta_mass_pipeline
from xic_extractor.ms1_scan_index import build_ms1_scan_index
from xic_extractor.xic_models import XICTrace


@pytest.mark.parametrize("signal_gate", [False, True])
@pytest.mark.parametrize("mass_policy", ["none", "easy_ic_restore"])
@pytest.mark.parametrize("composite", [False, True])
@pytest.mark.parametrize(
    "ratio_max, expected_ratio_status", [(0.6, "within_range"), (0.45, "above_range")]
)
def test_raw_cli_preserves_sample_attribution_and_batches_trace_acquisition(
    tmp_path,
    monkeypatch,
    composite,
    ratio_max,
    expected_ratio_status,
    signal_gate,
    mass_policy,
):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for sample in ("first", "second"):
        (raw_dir / f"{sample}.raw").touch()
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
        cid = tmp_path / "cid.csv"
        with cid.open("w", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "peak_id",
                    "sample_stem",
                    "matched_tag_names",
                    "status",
                    "peak_mz",
                    "precursor_mz",
                    "ms1_apex_rt",
                ],
            )
            writer.writeheader()
            # The join reads peak_mz, the peak's mass; the representative event's
            # precursor can sit outside the join window on a dense peak.
            writer.writerow(
                dict(
                    peak_id="cid-first",
                    sample_stem="first",
                    matched_tag_names="DNA_dR",
                    status="admitted",
                    peak_mz=300,
                    precursor_mz=300 * (1 + 30e-6),
                    ms1_apex_rt=1 + 4 / 60,
                )
            )
            # A measured second-sample peak without an attached event cannot
            # supply dR support, even when its mass and RT match exactly.
            writer.writerow(
                dict(
                    peak_id="cid-second",
                    sample_stem="second",
                    matched_tag_names="DNA_dR",
                    status="no_nl_event",
                    peak_mz=600,
                    precursor_mz=600,
                    ms1_apex_rt=1 + 4 / 60,
                )
            )
        extra_args = ["--cid-nl-peaks-csv", str(cid)]
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
            # Each mass peak has two above-noise profile flanks. They are not
            # additional ions, even though seeded m/z bins can separate them.
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

    def open_reader(path, dll_dir, *, mass_recalibration):
        assert mass_recalibration == MassRecalibration(mass_policy)
        return Raw(path)

    monkeypatch.setattr(delta_mass_pipeline, "open_raw", open_reader)
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
    assert [
        (r["sample_stem"], float(r["base_mz"]), float(r["base_height"]), r["status"])
        for r in rows
    ] == [("first", 300, 1e6, "candidate"), ("second", 600, 2e6, "candidate")]
    assert batches == [(300, 4 if signal_gate else 2), (600, 4 if signal_gate else 2)]
    assert [r["cid_nl_base_status"] for r in rows] == (
        ["matched", "unmatched"] if composite else ["", ""]
    )
    assert [r["cid_nl_base_peak_id"] for r in rows] == (
        ["cid-first", ""] if composite else ["", ""]
    )
    with (output / "delta_mass_positions.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        positions = list(csv.DictReader(handle))
    assert len(positions) == 4
    if signal_gate:
        heavy = [r for r in positions if r["position_label"] == "heavy"]
        assert [float(r["height_above_background"]) for r in heavy] == [5e5, 1e6]
        assert all(r["signal_status"] == r["lowest_status"] == "pass" for r in heavy)
        assert all(r["base_support_status"] == "pass" for r in rows)
        assert [float(r["base_expected_net_height"]) for r in rows] == pytest.approx(
            [5e5 / np.sqrt(0.4 * ratio_max), 1e6 / np.sqrt(0.4 * ratio_max)],
            rel=1e-5,
        )
        assert [float(r["base_support_score"]) for r in rows] == pytest.approx(
            [2 * np.sqrt(0.4 * ratio_max)] * 2, rel=1e-5
        )
    else:
        assert all(r["base_support_status"] == "not_required" for r in rows)
        assert all(r["base_support_score"] == "" for r in rows)

    assert all(
        row["ratio_status"] == expected_ratio_status
        and row["position_status"] == "pass"
        for row in positions
        if row["position_label"] == "heavy"
    )
    assert all(
        float(row["height_ratio_to_base"]) == 0.5
        for row in positions
        if row["position_label"] == "heavy"
    )
    sidecar = json.loads((output / "delta_mass_sidecar.json").read_text())
    assert sidecar["method"]["mass_recalibration"] == mass_policy
    results = [
        deserialize_mass_recalibration_result(s["mass_recalibration"])
        for s in sidecar["samples"]
    ]
    assert [r.baseline_lm for r in results] == (
        [1.0, 2.0] if mass_policy != "none" else [None, None]
    )
    assert sidecar["status"] == "diagnostic_only"
    assert sidecar["method"]["height_ratio_policy"] == "evidence_only"
    assert sidecar["method"]["base_support_positions"] == (
        ["heavy"] if signal_gate else []
    )
    assert sidecar["method"]["min_base_support"] == 0.25
    assert sidecar["method"]["detector"]["spectrum_mode"] == "vendor_centroid"
    assert [s["candidate_count"] for s in sidecar["samples"]] == [1, 1]
    assert {p.name for p in output.glob("*.*")} == {
        "delta_mass_candidates.csv",
        "delta_mass_positions.csv",
        "delta_mass_sidecar.json",
    }
    assert all(
        (output / name / "detected_features.csv").is_file()
        for name in ("first", "second")
    )
