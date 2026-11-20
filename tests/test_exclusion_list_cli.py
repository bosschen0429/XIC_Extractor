"""Instrument-facing CSV and recipe checks, substituting only the RAW reader."""

import csv
from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.run_exclusion_list import main
from xic_extractor.ms1_scan_index import MS1Scan
from xic_extractor.raw_reader import RawFileHandle


@pytest.fixture
def blank(monkeypatch):
    class Blank:
        ranges = [(100.0, 1000.0)]
        masses = np.full(100, 600.0)
        intensity = 26000.0
        _thermo = SimpleNamespace(ms1_order=1)

        @property
        def _raw_file(self):
            ranges = [SimpleNamespace(Low=a, High=b) for a, b in self.ranges]
            scan_filter = SimpleNamespace(
                MSOrder=1, MassRangeCount=len(ranges), GetMassRange=ranges.__getitem__
            )
            return SimpleNamespace(GetFilters=lambda: [scan_filter])

        ms1_scan_mass_ranges = RawFileHandle.ms1_scan_mass_ranges

        def acquisition_rt_bounds(self):
            return 0.0, 30.0

        def read_ms1_scan_index(self, *, centroid):
            return [
                MS1Scan(i + 1, i * 0.3, np.array([mz]), np.array([self.intensity]))
                for i, mz in enumerate(self.masses)
            ]

    source = Blank()
    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw", lambda *_: nullcontext(source)
    )
    return source


def recipe(
    folder,
    *,
    targets="target.csv",
    name="EX",
    rt=True,
    limit=10,
    threshold=25000,
    scan_range=(100, 1000),
):
    (folder / "blank.raw").touch()
    path = folder / "recipe.toml"
    path.write_text(
        f'blank="blank.raw"\ntargets=["{targets}"]\nnamed_exclusions=[{{file="named.csv"}}]\n'
        f"[method]\nmax_entries={limit}\nintensity_threshold={threshold}\n"
        f'[[lists]]\nname="{name}"\nrt={str(rt).lower()}\nscan_range={list(scan_range)}\n'
    )
    return str(path)


def inputs(folder, targets="m/z\n", named="m/z,name\n"):
    (folder / "target.csv").write_text(targets)
    (folder / "named.csv").write_text(named)


def rows(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def test_no_rt_list_protects_a_target_after_the_blank_ends(tmp_path, blank):
    inputs(tmp_path, "m/z,t start (min),t stop (min)\n600,40,41\n")
    assert main([recipe(tmp_path, rt=False)]) == 0
    assert rows(tmp_path / "EX.csv") == []


def test_protection_holds_at_import_precision(tmp_path, blank):
    inputs(
        tmp_path,
        "m/z,t start (min),t stop (min)\n600,10.1236,12.1234\n500,0,30\n",
        "m/z,name\n500.02000049,A\n",
    )
    assert main([recipe(tmp_path)]) == 0
    left, right = rows(tmp_path / "EX.csv")
    assert [float(r["m/z"]) for r in (left, right)] == [600.0, 600.0]
    assert float(left["t stop (min)"]) <= 10.1236
    assert float(right["t start (min)"]) >= 12.1234


def test_named_splits_cannot_exceed_capacity(tmp_path, blank, capsys):
    inputs(tmp_path, "m/z,t start (min),t stop (min)\n300,10,12\n", "m/z,name\n300,A\n")
    assert main([recipe(tmp_path, limit=1)]) == 2
    assert "max_entries" in capsys.readouterr().err
    assert not (tmp_path / "EX.csv").exists()


def test_only_emitted_named_rows_suppress_neighbors(tmp_path, blank):
    inputs(tmp_path, "m/z\n500\n", "m/z,name\n500.018,A\n500.024,B\n")
    assert main([recipe(tmp_path)]) == 0
    assert [float(r["m/z"]) for r in rows(tmp_path / "EX.csv")] == [500.024, 600.0]


@pytest.mark.parametrize(
    "targets,name", [("target.csv", "target"), ("target.csv", "named"), ("*.csv", "EX")]
)
def test_output_cannot_overwrite_or_feed_back_into_inputs(
    tmp_path, blank, targets, name
):
    inputs(tmp_path, "m/z\n301\n", "m/z,name\n300,A\n")
    before = {p: p.read_bytes() for p in tmp_path.glob("*.csv")}
    path = recipe(tmp_path, targets=targets, name=name)
    assert main([path]) == 2
    assert {p: p.read_bytes() for p in tmp_path.glob("*.csv")} == before
    assert not (tmp_path / "exclusion_report.txt").exists()
    out = tmp_path / "results"
    for _ in range(2):
        assert main([path, "--output-dir", str(out)]) == 0
        assert any(float(r["m/z"]) == 600.0 for r in rows(out / f"{name}.csv"))


def test_blank_without_recurrent_peaks_still_emits_named_rows(tmp_path, blank):
    blank.masses = np.arange(300.0, 400.0)
    inputs(tmp_path, named="m/z,name\n200,A\n")
    assert main([recipe(tmp_path)]) == 0
    assert [float(r["m/z"]) for r in rows(tmp_path / "EX.csv")] == [200.0]


def test_method_threshold_below_default_background_floor(tmp_path, blank):
    blank.intensity = 8000.0
    inputs(tmp_path)
    assert main([recipe(tmp_path, threshold=5000)]) == 0
    assert [float(r["m/z"]) for r in rows(tmp_path / "EX.csv")] == [600.0]


@pytest.mark.parametrize(
    "interval,status", [((250, 350), 2), ((150, 450), 2), ((400, 450), 0)]
)
def test_scan_range_uses_measured_intervals(tmp_path, blank, interval, status):
    blank.ranges = [(100.0, 200.0), (400.0, 500.0)]
    inputs(tmp_path)
    assert main([recipe(tmp_path, scan_range=interval)]) == status
    if status == 0:
        assert "100-200, 400-500" in (tmp_path / "exclusion_report.txt").read_text(
            encoding="utf-8"
        )
