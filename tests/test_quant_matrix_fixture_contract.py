import json
from pathlib import Path

from xic_extractor.alignment.quant_matrix_fixture_contract import (
    ROW_UNIVERSE_KEY_COLUMNS,
    validate_fixture_contract,
    write_cell_provenance_contract,
    write_review_rows_contract,
)
from xic_extractor.alignment.quant_matrix_report import (
    QUANT_MATRIX_REVIEW_ROW_COLUMNS,
)
from xic_extractor.diagnostics.quant_matrix.version import CELL_PROVENANCE_COLUMNS
from xic_extractor.tabular_io import file_sha256, write_tsv


def test_cell_provenance_contract_counts_and_validates_fixture(
    tmp_path: Path,
) -> None:
    full_tsv = tmp_path / "cell_provenance.tsv"
    summary_json = tmp_path / "cell_provenance_summary.json"
    fixture_tsv = tmp_path / "cell_provenance_minimal_fixture.tsv"
    rows = [_cell_row("detected"), _cell_row("accepted_backfill")]
    write_tsv(full_tsv, rows, CELL_PROVENANCE_COLUMNS, lineterminator="\n")

    write_cell_provenance_contract(
        full_tsv,
        summary_json,
        fixture_tsv,
        source_relpath="quant_matrix_version/cell_provenance.tsv",
    )

    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "quant_matrix_fixture_contract"
    assert payload["source_relpath"] == "quant_matrix_version/cell_provenance.tsv"
    assert payload["source_row_count"] == 2
    assert payload["source_sha256"] == file_sha256(full_tsv)
    assert payload["column_names"] == list(CELL_PROVENANCE_COLUMNS)
    assert payload["counts"]["cell_status"] == {
        "accepted_backfill": 1,
        "detected": 1,
    }
    assert payload["counts"]["write_authority"] == {"FALSE": 1, "TRUE": 1}
    assert payload["counts"]["value_source"] == {
        "ProductionAcceptanceManifest": 1,
        "input_quant_matrix": 1,
    }
    assert payload["minimal_fixture"]["row_count"] == 2
    assert payload["minimal_fixture"]["sha256"] == file_sha256(fixture_tsv)
    assert validate_fixture_contract(summary_json, fixture_tsv) == []

    fixture_tsv.unlink()

    assert any(
        "minimal fixture missing" in problem
        for problem in validate_fixture_contract(summary_json, fixture_tsv)
    )


def test_review_rows_contract_preserves_review_row_universe(
    tmp_path: Path,
) -> None:
    full_tsv = tmp_path / "quant_matrix_review_rows.tsv"
    summary_json = tmp_path / "quant_matrix_review_rows_summary.json"
    fixture_tsv = tmp_path / "quant_matrix_review_rows_minimal_fixture.tsv"
    rows = [
        _review_row("detected", truth_status=""),
        _review_row("accepted_backfill", truth_status="not_truth_claimed"),
    ]
    write_tsv(full_tsv, rows, QUANT_MATRIX_REVIEW_ROW_COLUMNS, lineterminator="\n")

    write_review_rows_contract(
        full_tsv,
        summary_json,
        fixture_tsv,
        source_relpath="review/quant_matrix_review_rows.tsv",
    )

    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    assert payload["source_relpath"] == "review/quant_matrix_review_rows.tsv"
    assert payload["counts"]["cell_status"] == {
        "accepted_backfill": 1,
        "detected": 1,
    }
    assert payload["counts"]["report_authority"] == {"review_only": 2}
    assert payload["counts"]["truth_status"] == {
        "": 1,
        "not_truth_claimed": 1,
    }
    assert payload["counts"]["next_evidence_needed"] == {"": 2}
    assert payload["row_universe"]["key_columns"] == list(ROW_UNIVERSE_KEY_COLUMNS)
    assert payload["row_universe"]["row_count"] == 2
    assert payload["row_universe"]["sha256"]
    assert payload["minimal_fixture"]["row_count"] == 2
    assert validate_fixture_contract(summary_json, fixture_tsv) == []


def _cell_row(status: str) -> dict[str, str]:
    is_backfill = status == "accepted_backfill"
    return {
        "schema_version": "quant_matrix_cell_provenance",
        "peak_hypothesis_id": "PH001",
        "sample_stem": "SampleB" if is_backfill else "SampleA",
        "source_feature_family_ids": "FAM001",
        "matrix_value": "222.2" if is_backfill else "100",
        "cell_status": status,
        "value_source": "ProductionAcceptanceManifest"
        if is_backfill
        else "input_quant_matrix",
        "write_authority": "TRUE" if is_backfill else "FALSE",
        "acceptance_decision": "accept_basic_backfill" if is_backfill else "",
        "acceptance_basis": "standard_peak" if is_backfill else "",
        "truth_status": "not_truth_claimed" if is_backfill else "",
        "quant_value_source": "standard_peak_shadow_projection" if is_backfill else "",
        "matrix_area_source": "gaussian_smoothed" if is_backfill else "",
        "source_artifact_relpath": "source.tsv" if is_backfill else "",
        "source_artifact_sha256": "A" * 64 if is_backfill else "",
        "source_row_sha256": "B" * 64 if is_backfill else "",
        "manifest_sha256": "C" * 64 if is_backfill else "",
    }


def _review_row(status: str, *, truth_status: str) -> dict[str, str]:
    row = {
        column: ""
        for column in QUANT_MATRIX_REVIEW_ROW_COLUMNS
    }
    row.update(
        {
            "schema_version": "quant_matrix_review_report",
            "peak_hypothesis_id": "PH001",
            "source_feature_family_ids": "FAM001",
            "sample_stem": "SampleB" if status == "accepted_backfill" else "SampleA",
            "cell_status": status,
            "matrix_value": "222.2" if status == "accepted_backfill" else "100",
            "report_authority": "review_only",
            "write_authority": "TRUE" if status == "accepted_backfill" else "FALSE",
            "truth_status": truth_status,
        }
    )
    return row
