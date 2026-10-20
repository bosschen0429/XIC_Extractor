import csv
import json
from pathlib import Path

from xic_extractor.instrument_qc.calibration import (
    CalibrationMetadataStatus,
    SDOLEKCalibrationResult,
    SDOLEKTrendRow,
    calibrate_sdolek_rows,
)
from xic_extractor.instrument_qc.calibration_writers import (
    CALIBRATED_TREND_TSV_COLUMNS,
    calibration_verdict,
    top_review_rows,
    write_calibrated_trend_tsv,
    write_calibration_review_markdown,
    write_calibration_summary_json,
)


def _row(
    sample: str,
    *,
    area: float | None = 100.0,
    rt_ref_delta: float | None = 0.0,
) -> SDOLEKTrendRow:
    return SDOLEKTrendRow(
        sample_name=sample,
        compound="SDO",
        identity_evidence="MS1_ONLY",
        status="detected",
        apex_rt_min=6.2,
        area=area,
        base_width_min=0.2,
        reference_rt_min=6.2,
        rt_delta_to_reference_min=rt_ref_delta,
        reference_base_width_min=0.8,
        base_width_ratio_to_reference=1.0,
    )


def _result() -> SDOLEKCalibrationResult:
    return calibrate_sdolek_rows(
        [
            _row("S1", area=100),
            _row("S2", area=100),
            _row("S3", area=100),
            _row("S4", area=300, rt_ref_delta=-0.55),
        ],
        metadata_source_status={"injection_order_status": "missing"},
    )


def test_write_calibrated_trend_tsv_header_and_rows(tmp_path: Path) -> None:
    result = _result()
    path = tmp_path / "instrument_qc_sdolek_calibrated_trend.tsv"

    write_calibrated_trend_tsv(path, result.rows)

    with path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert reader.fieldnames == CALIBRATED_TREND_TSV_COLUMNS
        rows = list(reader)
    assert rows[0]["identity_evidence"] == "MS1_ONLY"
    assert rows[0]["area"] == "100"
    assert rows[0]["rt_delta_to_reference_min"] == "0.0"
    assert rows[0]["compound_batch_median_area"] == "100.0"
    assert rows[-1]["review_bucket"] == "sensitivity_trend_review"
    assert "PRIOR_RT_SHIFT" in rows[-1]["prior_conflict_flags"]


def test_write_calibrated_trend_tsv_writes_header_without_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "empty.tsv"

    write_calibrated_trend_tsv(path, [])

    assert path.read_text(encoding="utf-8").splitlines() == [
        "\t".join(CALIBRATED_TREND_TSV_COLUMNS)
    ]


def test_write_calibration_summary_json_includes_metadata_and_counts(
    tmp_path: Path,
) -> None:
    result = _result()
    path = tmp_path / "instrument_qc_sdolek_calibration_summary.json"

    write_calibration_summary_json(path, result)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["verdict"] == "review_ready"
    assert payload["metadata_source_status"] == {"injection_order_status": "missing"}
    assert payload["calibration_metadata_status"]["source_contract"] == (
        "method_docs_only"
    )
    assert payload["summary"]["review_bucket_counts"]["sensitivity_trend_review"] == 1
    assert payload["rows"][-1]["batch_trend_flags"] == "BATCH_AREA_RISE"


def test_write_calibration_review_markdown_first_screen(tmp_path: Path) -> None:
    result = _result()
    path = tmp_path / "instrument_qc_sdolek_review.md"

    write_calibration_review_markdown(path, result, top_n=2)

    text = path.read_text(encoding="utf-8")
    assert "verdict: `review_ready`" in text
    assert "identity evidence: `MS1_ONLY`" in text
    assert "metadata note:" in text
    assert "top RT observation" in text
    assert "top area observation" in text
    assert "| sample | compound | order | bucket |" in text
    assert "sensitivity_trend_review" in text


def test_top_review_rows_excludes_stable_rows() -> None:
    result = calibrate_sdolek_rows(
        [
            _row("S1", area=100),
            _row("S2", area=100),
            _row("S3", area=100),
        ]
    )

    assert top_review_rows(result.rows, top_n=5) == []


def test_calibration_verdict_reports_metadata_incomplete_for_invalid_source() -> None:
    result = SDOLEKCalibrationResult(
        rows=_result().rows,
        metadata_source_status={},
        calibration_metadata_status=CalibrationMetadataStatus(
            injection_order_source="SampleInfo.xlsx",
            injection_order_status="invalid",
            source_contract="method_docs_only",
            matched_injection_order_rows=0,
            unmatched_injection_order_rows=4,
            reason="SampleInfo is downstream evidence.",
        ),
    )

    assert calibration_verdict(result) == "metadata_incomplete"


def test_calibration_verdict_reports_insufficient_evidence_for_empty_result() -> None:
    result = SDOLEKCalibrationResult(
        rows=(),
        metadata_source_status={},
        calibration_metadata_status=CalibrationMetadataStatus(
            injection_order_source="",
            injection_order_status="missing",
            source_contract="method_docs_only",
            matched_injection_order_rows=0,
            unmatched_injection_order_rows=0,
            reason="No rows.",
        ),
    )

    assert calibration_verdict(result) == "insufficient_evidence"
