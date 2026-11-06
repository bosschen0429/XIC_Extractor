import csv
import json
from pathlib import Path

import numpy as np

from xic_extractor.instrument_qc.pipeline import DEFAULT_DLL_DIR, run_sdolek_pipeline
from xic_extractor.instrument_qc.pipeline_inputs import discover_sdolek_raws
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent


class FakeRaw:
    def __init__(
        self,
        traces: dict[float, tuple[np.ndarray, np.ndarray]],
        ms2_events: tuple[Ms2ScanEvent, ...] = (),
    ) -> None:
        self.traces = traces
        self.ms2_events = ms2_events
        self.requests: list[tuple[float, float, float, float]] = []

    def __enter__(self) -> "FakeRaw":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        self.requests.append((mz, rt_min, rt_max, ppm_tol))
        return self.traces[mz]

    def iter_ms2_scans(self, _rt_min: float, _rt_max: float):
        yield from self.ms2_events


def _write_raw(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _trace(apex_rt: float) -> tuple[np.ndarray, np.ndarray]:
    rt = np.array([apex_rt - 0.2, apex_rt, apex_rt + 0.2])
    intensity = np.array([0.0, 1000.0, 0.0])
    return rt, intensity


def _long_trace(apex_rt: float) -> tuple[np.ndarray, np.ndarray]:
    rt = np.linspace(apex_rt - 0.4, apex_rt + 0.4, 9)
    intensity = np.array([0.0, 10.0, 100.0, 400.0, 1000.0, 400.0, 100.0, 10.0, 0.0])
    return rt, intensity


def _ms2_scan(
    *,
    precursor_mz: float = 311.0814,
    product_mz: float = 156.0768,
) -> Ms2ScanEvent:
    return Ms2ScanEvent(
        scan=Ms2Scan(
            scan_number=1,
            rt=6.26,
            precursor_mz=precursor_mz,
            masses=np.array([product_mz]),
            intensities=np.array([1000.0]),
            base_peak=1000.0,
        ),
        parse_error=None,
        scan_number=1,
    )


def test_sdolek_pipeline_extracts_two_compounds_and_writes_outputs(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    raw_path = raw_root / "SDOLEK" / "SDO LEK - 1.raw"
    _write_raw(raw_path)
    output_dir = tmp_path / "out"
    injection_order = tmp_path / "order.csv"
    injection_order.write_text(
        "Sample_Name,Injection_Order\nSDO LEK - 1,4\n",
        encoding="utf-8",
    )

    fake_raw = FakeRaw(
        {
            311.0814: _trace(6.26),
            556.2771: _trace(6.40),
        }
    )

    output = run_sdolek_pipeline(
        raw_dir=raw_root,
        output_dir=output_dir,
        injection_order_source=injection_order,
        raw_opener=lambda _path: fake_raw,
    )

    assert len(output.trend_rows) == 2
    assert {row.compound for row in output.trend_rows} == {"SDO", "LEK"}
    assert {row.injection_order for row in output.trend_rows} == {4}
    assert all(row.identity_evidence == "MS1_ONLY" for row in output.trend_rows)
    assert output.trend_tsv.exists()
    assert output.trend_json.exists()
    assert output.diagnostics_tsv.exists()
    assert output.workbook.exists()
    payload = json.loads(output.trend_json.read_text(encoding="utf-8"))
    assert payload["metadata_source_status"]["injection_order_status"] == "provided"


def test_sdolek_pipeline_reports_missing_injection_order_without_failing(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    raw_path = raw_root / "SDOLEK" / "SDOLEK-pretest.raw"
    _write_raw(raw_path)

    output = run_sdolek_pipeline(
        raw_dir=raw_root,
        output_dir=tmp_path / "out",
        raw_opener=lambda _path: FakeRaw(
            {
                311.0814: _trace(6.26),
                556.2771: _trace(6.40),
            }
        ),
    )

    assert all(row.injection_order is None for row in output.trend_rows)
    assert any(diag.issue == "INJECTION_ORDER_MISSING" for diag in output.diagnostics)
    payload = json.loads(output.trend_json.read_text(encoding="utf-8"))
    assert payload["metadata_source_status"] == {
        "injection_order_source": "",
        "injection_order_status": "missing",
    }


def test_sdolek_pipeline_ignores_biological_root_raws(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    _write_raw(raw_root / "TumorBC2257_DNA.raw")
    _write_raw(raw_root / "SDOLEK" / "SDO-posttest.raw")

    output = run_sdolek_pipeline(
        raw_dir=raw_root,
        output_dir=tmp_path / "out",
        raw_opener=lambda _path: FakeRaw(
            {
                311.0814: _trace(6.26),
                556.2771: _trace(6.40),
            }
        ),
    )

    assert {row.sample_name for row in output.trend_rows} == {"SDO-posttest"}


def test_sdolek_pipeline_uses_xcalibur_default_dll_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_root = tmp_path / "raw"
    _write_raw(raw_root / "SDOLEK" / "SDO-posttest.raw")
    calls: dict[str, Path] = {}

    def fake_open_raw(raw_path: Path, dll_dir: Path) -> FakeRaw:
        calls["raw_path"] = raw_path
        calls["dll_dir"] = dll_dir
        return FakeRaw(
            {
                311.0814: _trace(6.26),
                556.2771: _trace(6.40),
            }
        )

    monkeypatch.setattr("xic_extractor.instrument_qc.pipeline.open_raw", fake_open_raw)

    output = run_sdolek_pipeline(
        raw_dir=raw_root,
        output_dir=tmp_path / "out",
    )

    assert len(output.trend_rows) == 2
    assert calls["dll_dir"] == DEFAULT_DLL_DIR


def test_sdolek_discovery_finds_flat_folder_raw(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    sdolek = raw_root / "SDOLEK-pretest.raw"
    _write_raw(sdolek)
    _write_raw(raw_root / "Blank_1.raw")
    _write_raw(raw_root / "TumorBC2257_DNA.raw")

    assert discover_sdolek_raws(raw_root, []) == (sdolek,)


def test_sdolek_pipeline_fails_clearly_when_no_sdolek_raw(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    _write_raw(raw_root / "TumorBC2257_DNA.raw")

    try:
        run_sdolek_pipeline(
            raw_dir=raw_root,
            output_dir=tmp_path / "out",
            raw_opener=lambda _path: FakeRaw({}),
        )
    except FileNotFoundError as exc:
        assert "SDOLEK" in str(exc)
    else:
        raise AssertionError("Expected missing SDOLEK RAW to fail clearly")


def test_pipeline_can_emit_mixstds_outputs_from_explicit_registry(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    _write_raw(raw_root / "SDOLEK" / "SDOLEK-pretest.raw")
    _write_raw(raw_root / "STDs" / "Mix_STDs_01.raw")
    registry = tmp_path / "mixstds.csv"
    registry.write_text(
        "compound,precursor_mz,rt_min,rt_max,ppm_tol\nSTD-A,123.4567,1.0,2.0,10\n",
        encoding="utf-8",
    )

    output = run_sdolek_pipeline(
        raw_dir=raw_root,
        output_dir=tmp_path / "out",
        raw_opener=lambda _path: FakeRaw(
            {
                311.0814: _trace(6.26),
                556.2771: _trace(6.40),
                123.4567: _trace(1.50),
            }
        ),
        emit_mixstds=True,
        mixstds_target_registry=registry,
    )

    assert output.mixstds_trend_tsv is not None
    assert output.mixstds_trend_json is not None
    assert output.mixstds_diagnostics_tsv is not None
    with output.mixstds_trend_tsv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert rows[0]["sample_name"] == "Mix_STDs_01"
    assert rows[0]["compound"] == "STD-A"
    assert rows[0]["identity_evidence"] == "MS1_ONLY"


def test_pipeline_can_emit_hcd_audit_sidecar_without_changing_trend_schema(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    _write_raw(raw_root / "SDOLEK" / "SDOLEK-pretest.raw")
    injection_order = tmp_path / "order.csv"
    injection_order.write_text(
        "Sample_Name,Injection_Order\nSDOLEK-pretest,4\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "source_doc\tsource_section\tdoc_display_name\traw_stem\t"
        "injection_order\tinstrument_qc_class\tmatch_status\tmatch_confidence\t"
        "match_reason\tinstrument_method\tactivation_method\n"
        "doc\ttable:1\tSDOLEK-pretest\tSDOLEK-pretest\t4\tSDOLEK\tmatched\t"
        "high\tok\t20260105 SDOLEK CID\tCID\n",
        encoding="utf-8",
    )

    output = run_sdolek_pipeline(
        raw_dir=raw_root,
        output_dir=tmp_path / "out",
        raw_opener=lambda _path: FakeRaw(
            {
                311.0814: _long_trace(6.26),
                556.2771: _long_trace(6.40),
            },
            ms2_events=(_ms2_scan(),),
        ),
        injection_order_source=injection_order,
        emit_hcd_audit=True,
        sequence_manifest_source=manifest,
    )

    assert output.hcd_audit_tsv is not None
    assert output.hcd_audit_json is not None
    assert output.hcd_audit_tsv.exists()
    assert output.hcd_audit_rows[0].hcd_status == "hcd_supported"
    with output.trend_tsv.open(encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle, delimiter="\t"))
    assert "hcd_status" not in header


def test_pipeline_ignores_hcd_registry_when_audit_is_disabled(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    _write_raw(raw_root / "SDOLEK" / "SDOLEK-pretest.raw")
    invalid_registry = tmp_path / "bad_hcd_registry.csv"
    invalid_registry.write_text("not,a,registry\n", encoding="utf-8")

    output = run_sdolek_pipeline(
        raw_dir=raw_root,
        output_dir=tmp_path / "out",
        raw_opener=lambda _path: FakeRaw(
            {
                311.0814: _long_trace(6.26),
                556.2771: _long_trace(6.40),
            }
        ),
        hcd_product_registry=invalid_registry,
    )

    assert output.hcd_audit_tsv is None
    assert output.hcd_audit_rows == ()


def test_pipeline_reports_mixstds_missing_registry_without_extraction(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    _write_raw(raw_root / "SDOLEK" / "SDOLEK-pretest.raw")
    _write_raw(raw_root / "STDs" / "Mix_STDs_01.raw")

    output = run_sdolek_pipeline(
        raw_dir=raw_root,
        output_dir=tmp_path / "out",
        raw_opener=lambda _path: FakeRaw(
            {
                311.0814: _trace(6.26),
                556.2771: _trace(6.40),
            }
        ),
        emit_mixstds=True,
    )

    assert output.mixstds_trend_tsv is not None
    assert output.mixstds_trend_tsv.exists()
    assert any(
        diag.issue == "MIXSTDS_TARGET_REGISTRY_MISSING" for diag in output.diagnostics
    )
