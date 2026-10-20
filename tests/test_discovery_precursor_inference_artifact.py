import csv
from pathlib import Path

from scripts.check_discovery_precursor_inference_artifact import (
    EXPECTED_ROWS,
    REQUIRED_COLUMNS,
    check_discovery_precursor_inference_artifact,
)


def test_precursor_inference_checker_accepts_expected_rows(tmp_path: Path) -> None:
    candidates_csv = tmp_path / "discovery_candidates.csv"
    _write_candidates(candidates_csv, [_row(EXPECTED_ROWS[0]), _row(EXPECTED_ROWS[1])])

    problems = check_discovery_precursor_inference_artifact(
        candidates_csv=candidates_csv,
        expected_row_count=2,
    )

    assert problems == []


def test_precursor_inference_checker_rejects_stale_scan_only_ids(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "discovery_candidates.csv"
    rows = [_row(EXPECTED_ROWS[0]), _row(EXPECTED_ROWS[1])]
    rows[0]["candidate_id"] = "TumorBC2312_DNA#19561"
    _write_candidates(candidates_csv, rows)

    problems = check_discovery_precursor_inference_artifact(
        candidates_csv=candidates_csv,
        expected_row_count=2,
    )

    assert any("lacks row identity suffix" in problem for problem in problems)
    assert any("monoisotopic_300" in problem for problem in problems)


def test_precursor_inference_checker_rejects_row_identity_mismatch(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "discovery_candidates.csv"
    rows = [_row(EXPECTED_ROWS[0]), _row(EXPECTED_ROWS[1])]
    rows[0]["precursor_mz"] = "301.160635"
    rows[1]["best_ms2_scan_id"] = "19562"
    _write_candidates(candidates_csv, rows)

    problems = check_discovery_precursor_inference_artifact(
        candidates_csv=candidates_csv,
        expected_row_count=2,
    )

    assert any(
        "candidate_id precursor_mz does not match" in problem for problem in problems
    )
    assert any("candidate_id scan id does not match" in problem for problem in problems)


def test_precursor_inference_checker_rejects_invalid_basis_values(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "discovery_candidates.csv"
    rows = [_row(EXPECTED_ROWS[0]), _row(EXPECTED_ROWS[1])]
    rows[0]["precursor_mz_basis"] = "target_lookup"
    rows[1]["neutral_loss_error_basis"] = "measured_truth"
    _write_candidates(candidates_csv, rows)

    problems = check_discovery_precursor_inference_artifact(
        candidates_csv=candidates_csv,
        expected_row_count=2,
    )

    assert any("invalid precursor_mz_basis" in problem for problem in problems)
    assert any("invalid neutral_loss_error_basis" in problem for problem in problems)


def _write_candidates(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _row(expected: dict[str, object]) -> dict[str, str]:
    precursor_mz = float(expected["precursor_mz"])
    product_mz = float(expected["product_mz"])
    if expected["label"] == "monoisotopic_300":
        precursor_mz_basis = "product_plus_neutral_loss"
        neutral_loss_error_basis = "configured_loss_inferred_precursor"
    else:
        precursor_mz_basis = "scan_precursor"
        neutral_loss_error_basis = "measured_scan_precursor_product"
    scan_delta = (
        "1.004343" if precursor_mz_basis == "product_plus_neutral_loss" else "0.0"
    )
    return {
        "candidate_id": str(expected["candidate_id"]),
        "sample_stem": "TumorBC2312_DNA",
        "best_ms2_scan_id": "19561",
        "precursor_mz": f"{precursor_mz:.6f}",
        "product_mz": f"{product_mz:.6f}",
        "neutral_loss_mass_error_ppm": "0.0",
        "neutral_loss_error_basis": neutral_loss_error_basis,
        "precursor_mz_basis": precursor_mz_basis,
        "scan_precursor_mz": f"{precursor_mz + float(scan_delta):.6f}",
        "scan_precursor_delta_da": scan_delta,
        "max_scan_precursor_abs_delta_da": scan_delta,
        "tag_evidence_json": (
            '{"DNA_dR":{"precursor_mz_basis":"'
            + precursor_mz_basis
            + '","scan_count":1}}'
        ),
    }
