"""Bounded acquisition, exact saved answers, and the real offline CLI handoff."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict

import numpy as np
import pytest

from tests.delta_mass_review_fixtures import (
    review_fixture,
    write_discovery,
    write_review,
)
from tools.diagnostics.delta_mass_review import main
from xic_extractor.diagnostics import delta_mass_review_acquisition as acquisition
from xic_extractor.diagnostics.delta_mass_review_data import (
    REVIEW_SCHEMA,
    decode_delta_mass_review,
)
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
from xic_extractor.xic_models import XICTrace
from xic_inspector.api import (
    InspectorError,
    SelectionRequest,
    prepare_report,
    resolve_source,
)


def test_selected_raws_are_batched_and_saved_evidence_is_not_reclassified(
    tmp_path, monkeypatch
):
    discovery = tmp_path / "discovery"
    candidate_ids = write_discovery(discovery)
    before = {p.name: p.read_bytes() for p in discovery.iterdir() if p.is_file()}
    raw = tmp_path / "raw"
    raw.mkdir()
    for name in ("sample-a", "sample-b", "unused"):
        (raw / f"{name}.raw").touch()
    calls = []

    class Reader:
        def __init__(self, path):
            self.name = path.stem

        def __enter__(self):
            calls.append(("open", self.name))
            return self

        def __exit__(self, *args):
            pass

        def acquisition_rt_bounds(self):
            return 0.0, 5.0

        def extract_xic_many(self, requests):
            calls.append(("xic", self.name, len(requests)))
            return tuple(
                XICTrace.from_arrays([r.rt_min, 2.0, r.rt_max], [0.0, r.mz, 0.0])
                for r in requests
            )

        def iter_ms2_scans(self, lo, hi):
            calls.append(("ms2", self.name, lo, hi))
            # The out-of-precursor spectrum must remain available as RT context.
            yield Ms2ScanEvent(
                Ms2Scan(
                    11,
                    2.0,
                    999.0,
                    np.array([50.0, 80.0]),
                    np.array([20.0, 100.0]),
                    100.0,
                ),
                None,
                11,
            )
            yield Ms2ScanEvent(None, "scan unreadable", 12)
            yield Ms2ScanEvent(
                Ms2Scan(13, hi + 0.01, 100.0, np.array([50.0]), np.array([20.0]), 20.0),
                None,
                13,
            )

    monkeypatch.setattr(acquisition, "open_raw", lambda path, **kwargs: Reader(path))
    output = tmp_path / "review.json"
    args = [
        "--discovery-dir",
        str(discovery),
        "--raw-dir",
        str(raw),
        "--dll-dir",
        str(tmp_path),
        "--padding-min",
        "0.5",
        "--output",
        str(output),
        *(arg for cid in candidate_ids for arg in ("--candidate-id", cid)),
    ]
    assert main(args) == 0
    review = decode_delta_mass_review(json.loads(output.read_text()))
    assert [c[1] for c in calls if c[0] == "open"] == ["sample-a", "sample-b"]
    assert [c for c in calls if c[0] == "xic"] == [
        ("xic", "sample-a", 6),
        ("xic", "sample-b", 3),
    ]
    assert [c for c in calls if c[0] == "ms2"] == [
        ("ms2", "sample-a", 1.5, 2.7),
        ("ms2", "sample-b", 1.5, 2.5),
    ]
    for candidate, peak in zip(review.candidates, (100.0, 110.0, 200.0), strict=True):
        assert candidate.positions[0].raw_intensity == (0.0, peak, 0.0)
        assert candidate.positions[0].height_ratio_to_base == 2.5
        assert candidate.positions[0].ratio_status == "outside"
        assert candidate.status == "candidate"
        assert candidate.ratio_reference_label == "partner"
        assert (
            len(candidate.spectra) == 1 and candidate.spectra[0].precursor_mz == 999.0
        )
        assert candidate.ms2_read_errors[0].scan_number == 12
    assert before == {
        p.name: p.read_bytes() for p in discovery.iterdir() if p.is_file()
    }


def test_unknown_candidate_fails_before_raw_acquisition(tmp_path, monkeypatch):
    directory = tmp_path / "discovery"
    write_discovery(directory)

    def forbidden(*args, **kwargs):
        pytest.fail("RAW was opened for an invalid selection")

    monkeypatch.setattr(acquisition, "open_raw", forbidden)
    with pytest.raises(ValueError, match="exactly one"):
        acquisition.acquire_delta_mass_review(
            directory,
            candidate_ids=["missing"],
            raw_directory=tmp_path,
            dll_directory=tmp_path,
        )


def test_discovery_directory_points_to_review_export_not_alignment(tmp_path):
    write_discovery(tmp_path)
    with pytest.raises(
        FileNotFoundError, match="delta_mass_review.*不需執行 Alignment"
    ):
        resolve_source(tmp_path)


def test_position_query_selects_the_whole_candidate_and_raw_only_curves(tmp_path):
    source = write_review(tmp_path)
    result = prepare_report(
        source, SelectionRequest(strategy="query", mz_min=103, mz_max=103)
    )
    assert result.selected_count == 1
    item = result.payload["items"][0]
    assert item["kind"] == "candidate"
    assert item["delta_mass"]["candidate_id"] == "sample-a|100.0"
    assert [c["position_label"] for c in item["curves"]] == ["M", "partner", "third"]
    assert item["curves"][1]["raw_intensities"] == (0.0, 40.0, 200.0, 60.0, 0.0)
    assert all(c["processed_curves"] == [] for c in item["curves"])
    assert "quantitative_value" not in item["samples"][0]
    assert "raw_rt" not in item["delta_mass"]["positions"][0]
    assert item["delta_mass"]["spectra"][0]["mz"] == (50.0, 80.0, 140.0)


def test_real_inspector_cli_accepts_review_without_importing_raw(tmp_path):
    source = write_review(tmp_path)
    output = tmp_path / "report.html"
    code = (
        "import sys; from xic_inspector.cli import main; "
        f"result=main([{str(source)!r}, '--no-open', '--output', {str(output)!r}]); "
        "assert 'xic_extractor.raw_reader' not in sys.modules; raise SystemExit(result)"
    )
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    text = output.read_text(encoding="utf-8")
    assert "window.XicInspectorDeltaMass" in text
    assert "__INSPECTOR_" not in text
    assert "https://" not in text


def test_mismatched_spectrum_arrays_are_an_explicit_source_error(tmp_path):
    payload = {"schema_version": REVIEW_SCHEMA, **asdict(review_fixture())}
    payload = json.loads(json.dumps(payload))
    payload["candidates"][0]["spectra"][0]["intensity"].pop()
    path = tmp_path / "incomplete.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InspectorError, match="MS2 arrays must pair"):
        prepare_report(path, SelectionRequest())


def test_expected_source_rejects_changed_review_at_same_path(tmp_path):
    path = write_review(tmp_path)
    prepared = prepare_report(path, SelectionRequest())
    body = json.loads(path.read_text())
    body["candidates"][0]["status"] = "rejected"
    path.write_text(json.dumps(body))
    with pytest.raises(InspectorError, match="expected_source"):
        prepare_report(path, SelectionRequest(), expected_source=prepared.source)
