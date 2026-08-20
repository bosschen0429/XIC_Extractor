import csv
import json
from pathlib import Path

from tools.diagnostics import instrument_qc_sdolek_calibration


def test_calibration_cli_writes_outputs_from_synthetic_inputs(
    tmp_path: Path,
    capsys,
) -> None:
    trend_tsv = _write_trend_tsv(tmp_path)
    trend_json = _write_trend_json(tmp_path)
    injection_order = _write_injection_order(tmp_path)
    output_dir = tmp_path / "out"

    rc = instrument_qc_sdolek_calibration.main(
        [
            "--trend-tsv",
            str(trend_tsv),
            "--trend-json",
            str(trend_json),
            "--injection-order-source",
            str(injection_order),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert rc == 0
    assert (output_dir / "instrument_qc_sdolek_calibrated_trend.tsv").exists()
    assert (output_dir / "instrument_qc_sdolek_calibration_summary.json").exists()
    assert (output_dir / "instrument_qc_sdolek_review.md").exists()
    payload = json.loads(
        (output_dir / "instrument_qc_sdolek_calibration_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["calibration_metadata_status"]["injection_order_status"] == (
        "provided"
    )
    assert payload["calibration_metadata_status"]["matched_injection_order_rows"] == 3
    assert "instrument_qc_sdolek_calibrated_trend.tsv" in capsys.readouterr().out


def test_calibration_cli_rejects_missing_trend_tsv(
    tmp_path: Path,
    capsys,
) -> None:
    trend_json = _write_trend_json(tmp_path)

    rc = instrument_qc_sdolek_calibration.main(
        [
            "--trend-tsv",
            str(tmp_path / "missing.tsv"),
            "--trend-json",
            str(trend_json),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert rc == 2
    assert "trend TSV not found" in capsys.readouterr().err


def test_calibration_cli_rejects_missing_required_column(
    tmp_path: Path,
    capsys,
) -> None:
    trend_tsv = tmp_path / "trend.tsv"
    trend_tsv.write_text("sample_name\tcompound\nS1\tSDO\n", encoding="utf-8")
    trend_json = _write_trend_json(tmp_path)

    rc = instrument_qc_sdolek_calibration.main(
        [
            "--trend-tsv",
            str(trend_tsv),
            "--trend-json",
            str(trend_json),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert rc == 2
    err = capsys.readouterr().err
    assert "Missing required SDOLEK trend columns" in err
    assert "apex_rt_min" in err


def test_calibration_cli_allows_missing_injection_order_for_exploration(
    tmp_path: Path,
) -> None:
    trend_tsv = _write_trend_tsv(tmp_path)
    trend_json = _write_trend_json(tmp_path)
    output_dir = tmp_path / "out"

    rc = instrument_qc_sdolek_calibration.main(
        [
            "--trend-tsv",
            str(trend_tsv),
            "--trend-json",
            str(trend_json),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert rc == 0
    payload = json.loads(
        (output_dir / "instrument_qc_sdolek_calibration_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["calibration_metadata_status"]["injection_order_status"] == (
        "missing"
    )


def test_calibration_cli_rejects_sampleinfo_as_injection_order_source(
    tmp_path: Path,
    capsys,
) -> None:
    trend_tsv = _write_trend_tsv(tmp_path)
    trend_json = _write_trend_json(tmp_path)
    sample_info = tmp_path / "SampleInfo.xlsx"
    sample_info.write_text("downstream", encoding="utf-8")

    rc = instrument_qc_sdolek_calibration.main(
        [
            "--trend-tsv",
            str(trend_tsv),
            "--trend-json",
            str(trend_json),
            "--injection-order-source",
            str(sample_info),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert rc == 2
    assert "SampleInfo" in capsys.readouterr().err


def test_calibration_cli_reports_output_write_error(
    tmp_path: Path,
    capsys,
) -> None:
    trend_tsv = _write_trend_tsv(tmp_path)
    trend_json = _write_trend_json(tmp_path)
    output_dir = tmp_path / "out-as-file"
    output_dir.write_text("not a directory", encoding="utf-8")

    rc = instrument_qc_sdolek_calibration.main(
        [
            "--trend-tsv",
            str(trend_tsv),
            "--trend-json",
            str(trend_json),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert rc == 2
    assert "out-as-file" in capsys.readouterr().err


def _write_trend_tsv(tmp_path: Path) -> Path:
    path = tmp_path / "trend.tsv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sample_name",
                "compound",
                "identity_evidence",
                "status",
                "apex_rt_min",
                "area",
                "base_width_min",
                "reference_rt_min",
                "rt_delta_to_reference_min",
                "reference_base_width_min",
                "base_width_ratio_to_reference",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        for sample, area in [("S1", "100"), ("S2", "105"), ("S3", "95")]:
            writer.writerow(
                {
                    "sample_name": sample,
                    "compound": "SDO",
                    "identity_evidence": "MS1_ONLY",
                    "status": "detected",
                    "apex_rt_min": "6.2",
                    "area": area,
                    "base_width_min": "0.2",
                    "reference_rt_min": "6.2",
                    "rt_delta_to_reference_min": "0",
                    "reference_base_width_min": "0.8",
                    "base_width_ratio_to_reference": "0.25",
                }
            )
    return path


def _write_trend_json(tmp_path: Path) -> Path:
    path = tmp_path / "trend.json"
    path.write_text(
        json.dumps({"metadata_source_status": {"source": "synthetic"}}),
        encoding="utf-8",
    )
    return path


def _write_injection_order(tmp_path: Path) -> Path:
    path = tmp_path / "instrument_qc_injection_order.csv"
    path.write_text(
        "Sample_Name,Injection_Order\nS1,1\nS2,2\nS3,3\n",
        encoding="utf-8",
    )
    return path
