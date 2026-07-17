import csv
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tools.diagnostics import ms1_index_backfill_audit as audit
from xic_extractor.config import ExtractionConfig
from xic_extractor.ms1_scan_index import read_ms1_scan_index_provenance
from xic_extractor.xic_models import XICRequest, XICTrace


def test_audit_requests_compares_max_and_sum_against_vendor() -> None:
    summary, examples = audit.audit_requests_for_sample(
        sample_stem="sample-a",
        raw=FakeRawHandle(),
        requests=(
            XICRequest(mz=100.0, rt_min=1.0, rt_max=7.0, ppm_tol=10000.0),
        ),
        peak_config=_peak_config(),
        example_limit=5,
    )

    assert summary["request_count"] == 1
    assert summary["modes"]["max"]["peak_status_match_count"] == 1
    assert summary["modes"]["sum"]["peak_status_match_count"] == 1
    assert summary["modes"]["max"]["area_relative_delta_median"] > 0.0
    assert summary["modes"]["sum"]["area_relative_delta_median"] == 0.0
    assert examples[0]["sample_stem"] == "sample-a"
    assert examples[0]["mode"] == "max"


def test_audit_requests_reports_warm_cache_economics(tmp_path: Path) -> None:
    raw_path = tmp_path / "sample-a.raw"
    raw_path.write_bytes(b"sample-a-raw")
    summary, _examples = audit.audit_requests_for_sample(
        sample_stem="sample-a",
        raw=FakeRawHandle(),
        requests=(
            XICRequest(mz=100.0, rt_min=1.0, rt_max=7.0, ppm_tol=10000.0),
        ),
        peak_config=_peak_config(),
        example_limit=0,
        index_cache_path=tmp_path / "sample-a.ms1_index.npz",
        raw_path=raw_path,
        cache_warm_repeats=2,
    )

    assert summary["cache_write_sec"] is not None
    assert summary["cache_path"].endswith("sample-a.ms1_index.npz")
    assert summary["cache_manifest"]["ms1_scan_count"] == 7
    assert (tmp_path / "sample-a.ms1_index.npz").is_file()
    assert (tmp_path / "sample-a.ms1_index.manifest.json").is_file()
    assert summary["modes"]["sum"]["warm_cache_load_repeat_count"] == 2
    assert summary["modes"]["sum"]["warm_cache_matches_cold_index"] is True
    assert summary["modes"]["sum"]["warm_cache_total_sec"] >= 0.0
    provenance = read_ms1_scan_index_provenance(
        tmp_path / "sample-a.ms1_index.npz"
    )
    assert provenance is not None
    assert provenance.sample_stem == "sample-a"
    assert provenance.source_path == str(raw_path.resolve())


def test_write_outputs_preserves_dynamic_tsv_contract(tmp_path: Path) -> None:
    outputs = audit.AuditOutputs(
        summary_tsv=tmp_path / "nested" / "summary.tsv",
        examples_tsv=tmp_path / "nested" / "examples.tsv",
        json_path=tmp_path / "nested" / "audit.json",
    )
    result = {
        "aggregate": {
            "modes": {
                "max": {
                    "request_count": 2,
                    "area_relative_delta_median": 0.0,
                },
            },
        },
        "samples": [
            {
                "sample_stem": "sample-a",
                "vendor_extract_sec": 1.25,
                "index_build_sec": 2.5,
                "ms1_scan_count": 7,
                "modes": {
                    "max": {
                        "request_count": 1,
                        "area_relative_delta_median": 0.0,
                        "sample_only_metric": None,
                    },
                },
            },
        ],
        "examples": [
            {
                "sample_stem": "sample-a",
                "mode": "max",
                "area_relative_delta": 0.0,
            },
        ],
    }

    audit._write_outputs(outputs, result)

    assert outputs.json_path.read_text(encoding="utf-8").endswith("\n")
    assert json.loads(outputs.json_path.read_text(encoding="utf-8")) == result
    assert b"\r\n" in outputs.summary_tsv.read_bytes()
    with outputs.summary_tsv.open(encoding="utf-8", newline="") as handle:
        summary_rows = list(csv.DictReader(handle, delimiter="\t"))
    assert tuple(summary_rows[0]) == (
        "scope",
        "sample_stem",
        "mode",
        "request_count",
        "area_relative_delta_median",
        "vendor_extract_sec",
        "vendor_extraction_mode",
        "index_build_sec",
        "cache_write_sec",
        "cache_path",
        "ms1_scan_count",
        "sample_only_metric",
    )
    assert summary_rows[0]["area_relative_delta_median"] == "0.0"
    assert summary_rows[1]["sample_only_metric"] == ""

    assert b"\r\n" in outputs.examples_tsv.read_bytes()
    with outputs.examples_tsv.open(encoding="utf-8", newline="") as handle:
        example_rows = list(csv.DictReader(handle, delimiter="\t"))
    assert tuple(example_rows[0]) == (
        "sample_stem",
        "mode",
        "area_relative_delta",
    )
    assert example_rows[0]["area_relative_delta"] == "0.0"


def test_write_examples_tsv_empty_examples_is_bare_newline(tmp_path: Path) -> None:
    examples_tsv = tmp_path / "examples.tsv"

    audit._write_examples_tsv(examples_tsv, [])

    assert examples_tsv.read_bytes() == b"\n"


class FakeRawHandle:
    def __init__(self) -> None:
        self._raw_file = self
        self.RunHeaderEx = SimpleNamespace(FirstSpectrum=1, LastSpectrum=7)

    def extract_xic_many(self, requests):
        traces = []
        for request in requests:
            assert request.mz == 100.0
            traces.append(
                XICTrace.from_arrays(
                    [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
                    [0.0, 10.0, 60.0, 100.0, 60.0, 10.0, 0.0],
                )
            )
        return tuple(traces)

    def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
        return self.extract_xic_many(
            (XICRequest(mz=mz, rt_min=rt_min, rt_max=rt_max, ppm_tol=ppm_tol),)
        )[0]

    def GetFilterForScanNumber(self, scan_number: int) -> SimpleNamespace:
        return SimpleNamespace(MSOrder="Ms")

    def GetSegmentedScanFromScanNumber(
        self,
        scan_number: int,
        _scan_filter,
    ) -> SimpleNamespace:
        profile = {
            1: 0.0,
            2: 10.0,
            3: 60.0,
            4: 100.0,
            5: 60.0,
            6: 10.0,
            7: 0.0,
        }
        height = profile[scan_number]
        masses = [99.0, 100.0, 101.0]
        intensities = [height * 0.2, height * 0.6, height * 0.2]
        return SimpleNamespace(
            Positions=np.asarray(masses, dtype=float),
            Intensities=np.asarray(intensities, dtype=float),
        )

    def RetentionTimeFromScanNumber(self, scan_number: int) -> float:
        return float(scan_number)

    def ScanNumberFromRetentionTime(self, rt: float) -> int:
        return int(round(rt))


def _peak_config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("."),
        dll_dir=Path("."),
        output_csv=Path("out.csv"),
        diagnostics_csv=Path("diag.csv"),
        smooth_window=3,
        smooth_polyorder=1,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
    )
