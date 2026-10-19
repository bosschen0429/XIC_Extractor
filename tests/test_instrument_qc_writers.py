import csv
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from xic_extractor.instrument_qc.models import (
    HCDAuditRow,
    InstrumentQCDiagnostic,
    SDOLEKTrendRow,
)
from xic_extractor.instrument_qc.writers import (
    DIAGNOSTIC_TSV_COLUMNS,
    HCD_AUDIT_TSV_COLUMNS,
    TREND_TSV_COLUMNS,
    write_diagnostics_tsv,
    write_hcd_audit_tsv,
    write_sdolek_json,
    write_trend_tsv,
)

Writer = Callable[[Path, Any], None]


def _row(raw_path: Path) -> SDOLEKTrendRow:
    return SDOLEKTrendRow(
        sample_name="SDOLEK-pretest",
        raw_path=raw_path,
        injection_order=4,
        compound="SDO",
        precursor_mz=311.0814,
        identity_evidence="MS1_ONLY",
        reference_rt_min=6.26,
        rt_delta_to_reference_min=0.01,
        apex_rt_min=6.27,
        area=123.4,
        base_width_min=0.83,
        reference_base_width_min=0.83,
        base_width_ratio_to_reference=1.0,
        peak_start_rt_min=5.90,
        peak_end_rt_min=6.73,
        trend_confidence="clean",
        trend_flags=(),
        status="detected",
        reason="OK",
    )


def test_write_trend_tsv_uses_contract_columns(tmp_path: Path) -> None:
    path = tmp_path / "trend.tsv"
    write_trend_tsv(path, [_row(tmp_path / "a.raw")])

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)

    assert reader.fieldnames == TREND_TSV_COLUMNS
    assert rows[0]["compound"] == "SDO"
    assert rows[0]["identity_evidence"] == "MS1_ONLY"
    assert rows[0]["base_width_ratio_to_reference"] == "1.0"
    assert rows[0]["trend_flags"] == ""


def test_write_diagnostics_tsv_uses_contract_columns(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics.tsv"
    write_diagnostics_tsv(
        path,
        [
            InstrumentQCDiagnostic(
                sample_name="S1",
                raw_path=tmp_path / "S1.raw",
                issue="INJECTION_ORDER_MISSING",
                detail="No injection-order file supplied.",
            )
        ],
    )

    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "sample_name\traw_path\tissue\tdetail"


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("=1+1", "'=1+1"),
        ("+1+1", "'+1+1"),
        ("-1+1", "'-1+1"),
        ("@SUM(1,1)", "'@SUM(1,1)"),
        ('Sample "α"\tβ\nγ', 'Sample "α"\tβ\nγ'),
        ("", ""),
    ),
)
def test_diagnostic_report_escapes_text_without_mutating_json(
    tmp_path: Path, text: str, expected: str
) -> None:
    diagnostic = InstrumentQCDiagnostic(
        sample_name=text, raw_path=Path("=1+1.raw"), issue="@issue", detail=text
    )
    path = tmp_path / "diagnostics.tsv"
    write_diagnostics_tsv(path, [diagnostic])
    with path.open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle, delimiter="\t")) == [
            {
                "sample_name": expected,
                "raw_path": "'=1+1.raw",
                "issue": "'@issue",
                "detail": expected,
            }
        ]

    json_path = tmp_path / "trend.json"
    write_sdolek_json(json_path, [], [diagnostic])
    saved = json.loads(json_path.read_text(encoding="utf-8"))["diagnostics"][0]
    assert saved == {
        "sample_name": text,
        "raw_path": "=1+1.raw",
        "issue": "@issue",
        "detail": text,
    }


def test_write_hcd_audit_tsv_uses_contract_columns_and_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hcd.tsv"

    write_hcd_audit_tsv(path, [_hcd_row(tmp_path / "a.raw")])

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)

    assert reader.fieldnames == HCD_AUDIT_TSV_COLUMNS
    assert rows[0]["matched_products"] == "m/z 182.1;m/z 164.1"
    assert rows[0]["review_flags"] == "LOW_BASE_RATIO"
    assert rows[0]["best_product_ppm"] == ""
    assert rows[0]["best_product_base_ratio"] == "1.0"


def test_instrument_qc_tsv_writers_write_header_without_rows(
    tmp_path: Path,
) -> None:
    cases: list[tuple[str, list[str], Writer]] = [
        ("trend.tsv", TREND_TSV_COLUMNS, write_trend_tsv),
        ("diagnostics.tsv", DIAGNOSTIC_TSV_COLUMNS, write_diagnostics_tsv),
        ("hcd.tsv", HCD_AUDIT_TSV_COLUMNS, write_hcd_audit_tsv),
    ]

    for filename, columns, writer in cases:
        path = tmp_path / "empty" / filename

        writer(path, [])

        assert path.read_text(encoding="utf-8").splitlines() == [
            "\t".join(columns)
        ]


def test_write_sdolek_json_contains_summary_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "trend.json"
    write_sdolek_json(
        path,
        [_row(tmp_path / "a.raw")],
        [
            InstrumentQCDiagnostic(
                sample_name="S1",
                raw_path=tmp_path / "S1.raw",
                issue="INJECTION_ORDER_MISSING",
                detail="No injection-order file supplied.",
            )
        ],
        metadata_source_status={
            "injection_order_source": "",
            "injection_order_status": "missing",
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["summary"]["total_rows"] == 1
    assert payload["summary"]["status_counts"] == {"detected": 1}
    assert payload["rows"][0]["compound"] == "SDO"
    assert payload["metadata_source_status"] == {
        "injection_order_source": "",
        "injection_order_status": "missing",
    }


def _hcd_row(raw_path: Path) -> HCDAuditRow:
    return HCDAuditRow(
        sample_name="SDOLEK-pretest",
        raw_path=raw_path,
        injection_order=4,
        compound="SDO",
        precursor_mz=311.0814,
        ms1_apex_rt_min=6.27,
        ms1_status="detected",
        instrument_method="MethodA",
        activation_method="wHCD",
        hcd_mapping_source="compound",
        hcd_product_group="SDO",
        hcd_status="hcd_supported",
        best_ms2_scan_rt_min=6.28,
        apex_ms2_delta_min=0.01,
        trigger_scan_count=2,
        expected_product_count=2,
        matched_product_count=2,
        best_product_ppm=None,
        best_product_base_ratio=1.0,
        matched_products=("m/z 182.1", "m/z 164.1"),
        review_flags=("LOW_BASE_RATIO",),
        review_reason="Review low base ratio.",
    )
