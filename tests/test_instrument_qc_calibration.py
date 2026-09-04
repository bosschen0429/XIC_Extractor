import csv
import json
from pathlib import Path

import pytest

from xic_extractor.instrument_qc.calibration import (
    SDOLEKTrendRow,
    calibrate_sdolek_rows,
    load_metadata_source_status,
    load_sdolek_trend_rows,
)


def _row(
    sample: str,
    *,
    compound: str = "SDO",
    status: str = "detected",
    rt: float | None = 6.2,
    area: float | None = 100.0,
    width: float | None = 0.2,
    rt_ref_delta: float | None = 0.0,
    width_ref_ratio: float | None = 1.0,
) -> SDOLEKTrendRow:
    return SDOLEKTrendRow(
        sample_name=sample,
        compound=compound,
        identity_evidence="MS1_ONLY",
        status=status,
        apex_rt_min=rt,
        area=area,
        base_width_min=width,
        reference_rt_min=6.2,
        rt_delta_to_reference_min=rt_ref_delta,
        reference_base_width_min=0.8,
        base_width_ratio_to_reference=width_ref_ratio,
    )


def test_stable_detected_rows_become_stable_ms1_trend() -> None:
    result = calibrate_sdolek_rows(
        [
            _row("S1", area=100),
            _row("S2", area=110),
            _row("S3", area=90),
        ]
    )

    assert {row.review_bucket for row in result.rows} == {"stable_ms1_trend"}
    assert {row.identity_evidence for row in result.rows} == {"MS1_ONLY"}
    assert result.rows[0].compound_batch_median_area == 100.0


def test_prior_rt_mismatch_with_stable_batch_is_reference_mismatch() -> None:
    result = calibrate_sdolek_rows(
        [
            _row("S1", rt=5.7, rt_ref_delta=-0.55),
            _row("S2", rt=5.71, rt_ref_delta=-0.54),
            _row("S3", rt=5.69, rt_ref_delta=-0.56),
        ]
    )

    assert {row.review_bucket for row in result.rows} == {"prior_reference_mismatch"}
    assert all("PRIOR_RT_SHIFT" in row.prior_conflict_flags for row in result.rows)


def test_ms1_only_alone_does_not_become_insufficient() -> None:
    result = calibrate_sdolek_rows(
        [
            _row("S1"),
            _row("S2"),
            _row("S3"),
        ]
    )

    assert result.rows[0].identity_evidence == "MS1_ONLY"
    assert result.rows[0].review_bucket != "identity_evidence_insufficient"


def test_missing_injection_order_does_not_block_batch_medians() -> None:
    result = calibrate_sdolek_rows(
        [
            _row("S1"),
            _row("S2"),
            _row("S3"),
        ]
    )

    assert result.calibration_metadata_status.injection_order_status == "missing"
    assert all(row.injection_order is None for row in result.rows)
    assert all(row.compound_batch_median_rt_min is not None for row in result.rows)


def test_injection_order_partial_match_is_reported(tmp_path: Path) -> None:
    order = tmp_path / "instrument_qc_injection_order.csv"
    order.write_text("Sample_Name,Injection_Order\nS1,1\nS2,2\n", encoding="utf-8")

    result = calibrate_sdolek_rows(
        [
            _row("S1"),
            _row("S2"),
            _row("S3"),
        ],
        injection_order_source=order,
    )

    assert result.calibration_metadata_status.injection_order_status == "partial_match"
    assert result.calibration_metadata_status.matched_injection_order_rows == 2
    assert result.calibration_metadata_status.unmatched_injection_order_rows == 1
    assert [row.injection_order for row in result.rows] == [1, 2, None]


def test_sampleinfo_is_invalid_source_contract(tmp_path: Path) -> None:
    sample_info = tmp_path / "SampleInfo.xlsx"
    sample_info.write_text("not actually xlsx", encoding="utf-8")

    result = calibrate_sdolek_rows(
        [
            _row("S1"),
            _row("S2"),
            _row("S3"),
        ],
        injection_order_source=sample_info,
    )

    assert result.calibration_metadata_status.injection_order_status == "invalid"
    assert "SampleInfo" in result.calibration_metadata_status.reason


def test_fewer_than_three_detected_rows_leave_batch_fields_blank() -> None:
    result = calibrate_sdolek_rows([_row("S1"), _row("S2")])

    assert result.rows[0].compound_batch_median_rt_min is None
    assert {row.review_bucket for row in result.rows} == {
        "identity_evidence_insufficient"
    }


def test_non_detected_rows_become_not_detected_or_error() -> None:
    result = calibrate_sdolek_rows(
        [
            _row("S1", status="not_detected", rt=None, area=None, width=None),
            _row("S2"),
            _row("S3"),
            _row("S4"),
        ]
    )

    assert result.rows[0].review_bucket == "not_detected_or_error"


def test_batch_area_outlier_becomes_sensitivity_review() -> None:
    result = calibrate_sdolek_rows(
        [
            _row("S1", area=100),
            _row("S2", area=100),
            _row("S3", area=100),
            _row("S4", area=300),
        ]
    )

    outlier = [row for row in result.rows if row.sample_name == "S4"][0]
    assert outlier.review_bucket == "sensitivity_trend_review"
    assert "BATCH_AREA_RISE" in outlier.batch_trend_flags


def test_batch_outlier_takes_precedence_over_prior_mismatch() -> None:
    result = calibrate_sdolek_rows(
        [
            _row("S1", area=100, rt_ref_delta=-0.55),
            _row("S2", area=100, rt_ref_delta=-0.55),
            _row("S3", area=100, rt_ref_delta=-0.55),
            _row("S4", area=300, rt_ref_delta=-0.55),
        ]
    )

    outlier = [row for row in result.rows if row.sample_name == "S4"][0]
    assert outlier.review_bucket == "sensitivity_trend_review"
    assert "PRIOR_RT_SHIFT" in outlier.prior_conflict_flags


def test_load_instrument_trend_rows_and_metadata(tmp_path: Path) -> None:
    trend = tmp_path / "trend.tsv"
    with trend.open("w", encoding="utf-8", newline="") as handle:
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
        writer.writerow(
            {
                "sample_name": "S1",
                "compound": "SDO",
                "identity_evidence": "MS1_ONLY",
                "status": "detected",
                "apex_rt_min": "6.2",
                "area": "100",
                "base_width_min": "0.2",
                "reference_rt_min": "6.2",
                "rt_delta_to_reference_min": "0",
                "reference_base_width_min": "0.8",
                "base_width_ratio_to_reference": "0.25",
            }
        )
    rows = load_sdolek_trend_rows(trend)

    assert rows[0].sample_name == "S1"
    assert rows[0].base_width_ratio_to_reference == 0.25

    summary = tmp_path / "trend.json"
    summary.write_text(
        json.dumps({"metadata_source_status": {"injection_order_status": "missing"}}),
        encoding="utf-8",
    )
    assert load_metadata_source_status(summary) == {
        "injection_order_status": "missing"
    }


def test_load_instrument_trend_rows_fails_on_missing_columns(tmp_path: Path) -> None:
    trend = tmp_path / "trend.tsv"
    trend.write_text("sample_name\tcompound\nS1\tSDO\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Missing required SDOLEK trend columns"):
        load_sdolek_trend_rows(trend)
