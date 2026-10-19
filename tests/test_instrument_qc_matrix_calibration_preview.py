import csv
import json
from pathlib import Path

import pytest

from xic_extractor.instrument_qc.calibration_product_preview import (
    build_level0_calibration_bundle,
    build_level1_calibration_preview,
)

TREND_COLUMNS = [
    "sample_name",
    "raw_path",
    "injection_order",
    "compound",
    "precursor_mz",
    "identity_evidence",
    "reference_rt_min",
    "rt_delta_to_reference_min",
    "apex_rt_min",
    "area",
    "base_width_min",
    "reference_base_width_min",
    "base_width_ratio_to_reference",
    "peak_start_rt_min",
    "peak_end_rt_min",
    "trend_confidence",
    "trend_flags",
    "status",
    "reason",
]

HCD_COLUMNS = [
    "sample_name",
    "raw_path",
    "injection_order",
    "compound",
    "precursor_mz",
    "ms1_apex_rt_min",
    "ms1_status",
    "instrument_method",
    "activation_method",
    "hcd_mapping_source",
    "hcd_product_group",
    "hcd_status",
    "best_ms2_scan_rt_min",
    "apex_ms2_delta_min",
    "trigger_scan_count",
    "expected_product_count",
    "matched_product_count",
    "best_product_ppm",
    "best_product_base_ratio",
    "matched_products",
    "review_flags",
    "review_reason",
]


def _write_tsv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _trend_row(**overrides: str) -> dict[str, str]:
    row = {
        "sample_name": "SDOLEK_1",
        "raw_path": "SDOLEK_1.raw",
        "injection_order": "4",
        "compound": "SDO",
        "precursor_mz": "311.0814",
        "identity_evidence": "MS1_ONLY",
        "reference_rt_min": "6.26",
        "rt_delta_to_reference_min": "0.01",
        "apex_rt_min": "6.27",
        "area": "1000",
        "base_width_min": "0.83",
        "reference_base_width_min": "0.83",
        "base_width_ratio_to_reference": "1",
        "peak_start_rt_min": "5.9",
        "peak_end_rt_min": "6.73",
        "trend_confidence": "clean",
        "trend_flags": "",
        "status": "detected",
        "reason": "OK",
    }
    row.update(overrides)
    return row


def _hcd_row(**overrides: str) -> dict[str, str]:
    row = {
        "sample_name": "SDOLEK_1",
        "raw_path": "SDOLEK_1.raw",
        "injection_order": "4",
        "compound": "SDO",
        "precursor_mz": "311.0814",
        "ms1_apex_rt_min": "6.27",
        "ms1_status": "detected",
        "instrument_method": "CID/wHCD switch",
        "activation_method": "wHCD",
        "hcd_mapping_source": "sdolek_builtin",
        "hcd_product_group": "SDO",
        "hcd_status": "hcd_supported",
        "best_ms2_scan_rt_min": "6.28",
        "apex_ms2_delta_min": "0.01",
        "trigger_scan_count": "1",
        "expected_product_count": "4",
        "matched_product_count": "2",
        "best_product_ppm": "3.2",
        "best_product_base_ratio": "0.5",
        "matched_products": "wHCD:156.070",
        "review_flags": "",
        "review_reason": "OK",
    }
    row.update(overrides)
    return row


def test_build_level0_bundle_writes_manifest_evidence_and_summary(
    tmp_path: Path,
) -> None:
    instrument_qc_dir = tmp_path / "instrument_qc"
    _write_tsv(
        instrument_qc_dir / "instrument_qc_sdolek_trend.tsv",
        TREND_COLUMNS,
        [_trend_row()],
    )
    _write_tsv(
        instrument_qc_dir / "instrument_qc_mixstds_trend.tsv",
        TREND_COLUMNS,
        [_trend_row(sample_name="Mix_1", compound="5-hmdC", precursor_mz="258.109")],
    )
    _write_tsv(
        instrument_qc_dir / "instrument_qc_hcd_audit.tsv",
        HCD_COLUMNS,
        [
            _hcd_row(hcd_status="no_ms2_trigger"),
            _hcd_row(
                sample_name="Mix_1",
                compound="5-hmdC",
                hcd_status="no_product_match",
            ),
        ],
    )

    result = build_level0_calibration_bundle(
        instrument_qc_dir=instrument_qc_dir,
        output_dir=tmp_path / "bundle",
        generation_command="test command",
    )

    assert result.manifest_json.exists()
    assert result.evidence_tsv.exists()
    assert result.evidence_summary_json.exists()
    assert result.rt_preview_tsv is None

    manifest = json.loads(result.manifest_json.read_text(encoding="utf-8"))
    assert manifest["product_maturity_level"] == "level_0"
    assert manifest["overall_verdict"] == "diagnostic_only"
    assert {item["artifact_id"] for item in manifest["artifact_inventory"]} == {
        "manifest",
        "evidence",
        "evidence_summary",
    }

    summary = json.loads(result.evidence_summary_json.read_text(encoding="utf-8"))
    assert summary["counts_by_source_type"] == {
        "hcd_audit": 2,
        "mixstds": 1,
        "sdolek": 1,
    }
    assert summary["counts_by_product_support_status"]["not_triggered"] == 1
    assert summary["counts_by_product_support_status"]["product_missing"] == 1

    with result.evidence_tsv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert {row["source_type"] for row in rows} == {"sdolek", "mixstds", "hcd_audit"}


def test_build_level0_bundle_records_missing_optional_inputs(tmp_path: Path) -> None:
    instrument_qc_dir = tmp_path / "instrument_qc"
    _write_tsv(
        instrument_qc_dir / "instrument_qc_sdolek_trend.tsv",
        TREND_COLUMNS,
        [_trend_row()],
    )

    result = build_level0_calibration_bundle(
        instrument_qc_dir=instrument_qc_dir,
        output_dir=tmp_path / "bundle",
        generation_command="test command",
    )

    summary = json.loads(result.evidence_summary_json.read_text(encoding="utf-8"))
    assert summary["missing_artifacts"] == [
        "instrument_qc_mixstds_trend.tsv",
        "instrument_qc_hcd_audit.tsv",
    ]


def test_build_level0_bundle_fails_on_missing_required_columns(tmp_path: Path) -> None:
    instrument_qc_dir = tmp_path / "instrument_qc"
    _write_tsv(
        instrument_qc_dir / "instrument_qc_sdolek_trend.tsv",
        ["sample_name", "compound"],
        [{"sample_name": "S1", "compound": "SDO"}],
    )

    with pytest.raises(ValueError, match="missing required columns"):
        build_level0_calibration_bundle(
            instrument_qc_dir=instrument_qc_dir,
            output_dir=tmp_path / "bundle",
            generation_command="test command",
        )


def test_build_level0_bundle_fails_on_bad_numeric_value(tmp_path: Path) -> None:
    instrument_qc_dir = tmp_path / "instrument_qc"
    _write_tsv(
        instrument_qc_dir / "instrument_qc_sdolek_trend.tsv",
        TREND_COLUMNS,
        [_trend_row(area="not-a-number")],
    )

    with pytest.raises(ValueError, match="instrument_qc_sdolek_trend.tsv.*area.*row 2"):
        build_level0_calibration_bundle(
            instrument_qc_dir=instrument_qc_dir,
            output_dir=tmp_path / "bundle",
            generation_command="test command",
        )


def test_build_level1_rt_preview_writes_rejoinable_sidecar(tmp_path: Path) -> None:
    instrument_qc_dir = tmp_path / "instrument_qc"
    _write_tsv(
        instrument_qc_dir / "instrument_qc_sdolek_trend.tsv",
        TREND_COLUMNS,
        [
            _trend_row(
                sample_name="Anchor_1",
                raw_path="Anchor_1.raw",
                injection_order="1",
                compound="SDO",
                reference_rt_min="10.0",
                apex_rt_min="10.05",
                rt_delta_to_reference_min="0.05",
            ),
            _trend_row(
                sample_name="Anchor_2",
                raw_path="Anchor_2.raw",
                injection_order="10",
                compound="LEK",
                reference_rt_min="12.0",
                apex_rt_min="12.10",
                rt_delta_to_reference_min="0.10",
            ),
            _trend_row(
                sample_name="Anchor_3",
                raw_path="Anchor_3.raw",
                injection_order="20",
                compound="SDO",
                reference_rt_min="14.0",
                apex_rt_min="14.15",
                rt_delta_to_reference_min="0.15",
            ),
        ],
    )
    (instrument_qc_dir / "instrument_qc_injection_order.csv").write_text(
        "Sample_Name,Injection_Order\nSampleA,10\n",
        encoding="utf-8",
    )
    matrix_input = tmp_path / "alignment_cells.tsv"
    matrix_input.write_text(
        "\t".join(
            [
                "feature_family_id",
                "sample_stem",
                "status",
                "area",
                "apex_rt",
                "source_raw_file",
                "family_center_mz",
                "family_center_rt",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "FAM001",
                "SampleA",
                "detected",
                "1000",
                "12.10",
                "SampleA.raw",
                "300.0",
                "12.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    before = matrix_input.read_bytes()

    result = build_level1_calibration_preview(
        preview_kind="rt",
        instrument_qc_dir=instrument_qc_dir,
        matrix_input=matrix_input,
        matrix_input_role="untargeted_cell_table",
        output_dir=tmp_path / "bundle",
        generation_command="test command",
    )

    assert matrix_input.read_bytes() == before
    assert result.rt_preview_tsv is not None
    assert result.rt_preview_summary_json is not None
    assert result.rt_model_tsv is not None
    assert result.rt_model_summary_json is not None
    assert result.rt_leave_one_anchor_out_tsv is not None

    manifest = json.loads(result.manifest_json.read_text(encoding="utf-8"))
    assert manifest["product_maturity_level"] == "level_1"
    assert manifest["overall_verdict"] == "preview_ready"
    assert {item["artifact_id"] for item in manifest["artifact_inventory"]} == {
        "manifest",
        "evidence",
        "evidence_summary",
        "rt_model",
        "rt_model_summary",
        "rt_leave_one_anchor_out",
        "rt_preview",
        "rt_preview_summary",
    }

    with result.rt_preview_tsv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert rows[0]["source_row_id"] == "2"
    assert rows[0]["source_cell_key"] == "FAM001|SampleA"
    assert rows[0]["injection_order"] == "10"
    assert rows[0]["rt_if_standard_corrected_min"]
    assert rows[0]["coverage_status"] == "covered"
    assert rows[0]["rt_alignment_support_status"] == "local_rt_supported"
    assert rows[0]["local_anchor_count"] == "3"
    assert rows[0]["irt_anchor_scope"] == "inside_anchor_range"

    with result.rt_leave_one_anchor_out_tsv.open(
        encoding="utf-8",
        newline="",
    ) as handle:
        loo_rows = list(csv.DictReader(handle, delimiter="\t"))
    assert {row["status"] for row in loo_rows} <= {"PASS", "WARN", "FAIL"}
    assert {row["compound"] for row in loo_rows} == {"SDO", "LEK"}

    summary = json.loads(result.rt_preview_summary_json.read_text(encoding="utf-8"))
    assert summary["counts_by_correction_status"] == {"shadow_only": 1}


def test_build_level1_rt_preview_blocks_without_injection_order(
    tmp_path: Path,
) -> None:
    instrument_qc_dir = tmp_path / "instrument_qc"
    _write_tsv(
        instrument_qc_dir / "instrument_qc_sdolek_trend.tsv",
        TREND_COLUMNS,
        [_trend_row(rt_delta_to_reference_min="0.05")],
    )
    matrix_input = tmp_path / "alignment_cells.tsv"
    matrix_input.write_text(
        "\t".join(
            [
                "feature_family_id",
                "sample_stem",
                "status",
                "area",
                "apex_rt",
                "source_raw_file",
                "family_center_mz",
                "family_center_rt",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "FAM001",
                "SampleA",
                "detected",
                "1000",
                "12.05",
                "SampleA.raw",
                "300.0",
                "12.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_level1_calibration_preview(
        preview_kind="rt",
        instrument_qc_dir=instrument_qc_dir,
        matrix_input=matrix_input,
        matrix_input_role="untargeted_cell_table",
        output_dir=tmp_path / "bundle",
        generation_command="test command",
    )

    assert result.rt_preview_tsv is not None
    with result.rt_preview_tsv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert rows[0]["coverage_status"] == "incomplete"
    assert rows[0]["rt_alignment_support_status"] == (
        "incomplete_missing_injection_order"
    )
    assert rows[0]["correction_status"] == "blocked_not_covered"


def test_build_level1_rt_preview_rejects_unsupported_matrix_role(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unsupported matrix input role"):
        build_level1_calibration_preview(
            preview_kind="rt",
            instrument_qc_dir=tmp_path,
            matrix_input=tmp_path / "alignment_cells.tsv",
            matrix_input_role="external_matrix",
            output_dir=tmp_path / "bundle",
            generation_command="test command",
        )
