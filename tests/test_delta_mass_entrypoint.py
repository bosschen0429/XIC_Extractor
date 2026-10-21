"""Exercise the CLI with real detector/matcher/writers and a fake RAW boundary."""

import csv
import json
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.run_delta_mass_discovery import main
from xic_extractor.discovery import delta_mass_pipeline
from xic_extractor.xic_models import XICTrace


@pytest.mark.parametrize("composite", [False, True])
def test_raw_cli_preserves_sample_attribution_and_batches_trace_acquisition(
    tmp_path, monkeypatch, composite
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
                    "candidate_id",
                    "ms1_feature_row_id",
                    "neutral_loss_tag",
                    "discovery_candidate_state",
                    "precursor_mz",
                    "ms1_apex_rt",
                ],
            )
            writer.writeheader()
            writer.writerow(
                dict(
                    candidate_id="cid-first",
                    ms1_feature_row_id="first|DNA_dR|300|1.0667",
                    neutral_loss_tag="DNA_dR",
                    discovery_candidate_state="ms1_feature_nl_supported",
                    precursor_mz=300,
                    ms1_apex_rt=1 + 4 / 60,
                )
            )
        extra_args = ["--cid-nl-candidates-csv", str(cid)]
    batches = []

    class Raw:
        RunHeaderEx = SimpleNamespace(FirstSpectrum=1, LastSpectrum=9)

        def __init__(self, path):
            self.base_mz = 300 if path.stem == "first" else 600
            self.height = 1e6 if path.stem == "first" else 2e6
            self.times = np.arange(9) / 60 + 1
            self.signal = np.array([0.03, 0.1, 0.3, 0.7, 1, 0.7, 0.3, 0.1, 0.03])

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def GetFilterForScanNumber(self, _):
            return SimpleNamespace(MSOrder="Ms")

        def RetentionTimeFromScanNumber(self, scan):
            return self.times[scan - 1]

        def GetSimplifiedScan(self, scan):
            return SimpleNamespace(
                Masses=[self.base_mz, self.base_mz + 7],
                Intensities=self.signal[scan - 1] * self.height * np.array([1, 0.5]),
            )

        def extract_xic_many(self, requests):
            batches.append((self.base_mz, len(requests)))
            return tuple(
                XICTrace(
                    self.times,
                    self.signal * self.height * (1 if r.mz == self.base_mz else 0.5),
                )
                for r in requests
            )

    monkeypatch.setattr(
        delta_mass_pipeline, "open_raw", lambda path, dll_dir: Raw(path)
    )
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
    assert batches == [(300, 2), (600, 2)]
    assert [r["cid_nl_base_status"] for r in rows] == (
        ["matched", "unmatched"] if composite else ["", ""]
    )
    assert [r["cid_nl_base_candidate_id"] for r in rows] == (
        ["cid-first", ""] if composite else ["", ""]
    )
    with (output / "delta_mass_positions.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        positions = list(csv.DictReader(handle))
    assert len(positions) == 4
    assert all(
        float(row["height_ratio_to_base"]) == 0.5
        for row in positions
        if row["position_label"] == "heavy"
    )
    sidecar = json.loads((output / "delta_mass_sidecar.json").read_text())
    assert sidecar["status"] == "diagnostic_only"
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
