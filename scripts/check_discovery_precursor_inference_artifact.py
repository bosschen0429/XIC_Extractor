"""Check the Discovery precursor-inference artifact used by product gates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES_CSV = (
    ROOT
    / "docs/superpowers/validation/discovery_precursor_inference_v1"
    / "TumorBC2312_DNA/discovery_candidates.csv"
)

DEFAULT_EXPECTED_ROW_COUNT = 157
ROW_ID_PATTERN = re.compile(
    r"^(?P<sample>.+)#(?P<scan>\d+)@mz(?P<precursor>\d+(?:\.\d+)?)"
    r"_p(?P<product>\d+(?:\.\d+)?)$"
)
REQUIRED_COLUMNS = (
    "candidate_id",
    "sample_stem",
    "best_ms2_scan_id",
    "precursor_mz",
    "product_mz",
    "neutral_loss_mass_error_ppm",
    "neutral_loss_error_basis",
    "precursor_mz_basis",
    "scan_precursor_mz",
    "scan_precursor_delta_da",
    "max_scan_precursor_abs_delta_da",
    "tag_evidence_json",
)
ALLOWED_PRECURSOR_MZ_BASIS = {
    "scan_precursor",
    "product_plus_neutral_loss",
    "mixed",
}
ALLOWED_NEUTRAL_LOSS_ERROR_BASIS = {
    "measured_scan_precursor_product",
    "configured_loss_inferred_precursor",
    "mixed",
}
ROW_ID_MZ_TOLERANCE_DA = 0.001

EXPECTED_ROWS = (
    {
        "label": "monoisotopic_300",
        "candidate_id": "TumorBC2312_DNA#19561@mz300.160635_p184.113235",
        "precursor_mz": 300.160635,
        "product_mz": 184.113235,
        "precursor_mz_basis": {"product_plus_neutral_loss"},
        "neutral_loss_error_basis": {"configured_loss_inferred_precursor"},
        "min_abs_scan_precursor_delta_da": 0.5,
    },
    {
        "label": "isotope_301",
        "candidate_id": "TumorBC2312_DNA#19561@mz301.164978_p185.115845",
        "precursor_mz": 301.164978,
        "product_mz": 185.115845,
        "precursor_mz_basis": {"scan_precursor", "mixed"},
        "neutral_loss_error_basis": {"measured_scan_precursor_product", "mixed"},
        "min_abs_scan_precursor_delta_da": 0.0,
    },
)


def check_discovery_precursor_inference_artifact(
    *,
    candidates_csv: Path = DEFAULT_CANDIDATES_CSV,
    expected_row_count: int = DEFAULT_EXPECTED_ROW_COUNT,
) -> list[str]:
    problems: list[str] = []
    rows, fieldnames = _read_rows(candidates_csv, problems)
    if problems:
        return problems

    missing_columns = [
        column for column in REQUIRED_COLUMNS if column not in fieldnames
    ]
    if missing_columns:
        problems.append(
            "discovery candidate artifact missing required columns: "
            + ", ".join(missing_columns)
        )
        return problems

    if len(rows) != expected_row_count:
        problems.append(
            "discovery candidate row count mismatch: "
            f"expected {expected_row_count}, observed {len(rows)}"
        )

    candidate_ids = [row["candidate_id"] for row in rows]
    duplicate_ids = sorted(
        candidate_id
        for candidate_id in set(candidate_ids)
        if candidate_ids.count(candidate_id) > 1
    )
    if duplicate_ids:
        problems.append(
            "duplicate candidate_id values found: " + ", ".join(duplicate_ids[:5])
        )

    for row_number, row in enumerate(rows, start=2):
        _append_row_contract_problems(row_number, row, problems)

    rows_by_id = {row["candidate_id"]: row for row in rows}
    for expected in EXPECTED_ROWS:
        _append_expected_row_problems(rows_by_id, expected, problems)
    return problems


def _read_rows(
    candidates_csv: Path,
    problems: list[str],
) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    try:
        with candidates_csv.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            return list(reader), tuple(reader.fieldnames or ())
    except OSError as exc:
        problems.append(f"{candidates_csv}: could not read CSV: {exc}")
    return [], ()


def _append_row_contract_problems(
    row_number: int,
    row: dict[str, str],
    problems: list[str],
) -> None:
    candidate_id = row["candidate_id"]
    match = ROW_ID_PATTERN.match(candidate_id)
    if match is None:
        problems.append(
            f"row {row_number}: candidate_id lacks row identity suffix; "
            f"regenerate stale discovery artifacts: {candidate_id}"
        )
        return

    if match.group("sample") != row.get("sample_stem", ""):
        problems.append(
            f"row {row_number}: candidate_id sample stem does not match "
            f"sample_stem: {candidate_id}"
        )

    scan_id = _optional_int(row.get("best_ms2_scan_id", ""))
    if scan_id is None or int(match.group("scan")) != scan_id:
        problems.append(
            f"row {row_number}: candidate_id scan id does not match "
            f"best_ms2_scan_id: {candidate_id}"
        )

    _append_row_id_mz_problem(
        row_number,
        row,
        problems,
        candidate_id=candidate_id,
        column="precursor_mz",
        id_value=float(match.group("precursor")),
    )
    _append_row_id_mz_problem(
        row_number,
        row,
        problems,
        candidate_id=candidate_id,
        column="product_mz",
        id_value=float(match.group("product")),
    )

    if row.get("precursor_mz_basis", "") not in ALLOWED_PRECURSOR_MZ_BASIS:
        problems.append(
            f"row {row_number}: invalid precursor_mz_basis "
            f"{row.get('precursor_mz_basis', '')!r}"
        )
    if (
        row.get("neutral_loss_error_basis", "")
        not in ALLOWED_NEUTRAL_LOSS_ERROR_BASIS
    ):
        problems.append(
            f"row {row_number}: invalid neutral_loss_error_basis "
            f"{row.get('neutral_loss_error_basis', '')!r}"
        )


def _append_row_id_mz_problem(
    row_number: int,
    row: dict[str, str],
    problems: list[str],
    *,
    candidate_id: str,
    column: str,
    id_value: float,
) -> None:
    observed = _optional_float(row.get(column, ""))
    if observed is not None and abs(observed - id_value) <= ROW_ID_MZ_TOLERANCE_DA:
        return
    problems.append(
        f"row {row_number}: candidate_id {column} does not match {column}: "
        f"{candidate_id}"
    )


def _append_expected_row_problems(
    rows_by_id: dict[str, dict[str, str]],
    expected: dict[str, Any],
    problems: list[str],
) -> None:
    candidate_id = str(expected["candidate_id"])
    row = rows_by_id.get(candidate_id)
    label = str(expected["label"])
    if row is None:
        problems.append(f"{label}: expected candidate_id not found: {candidate_id}")
        return

    for column in ("precursor_mz", "product_mz"):
        observed = _optional_float(row.get(column, ""))
        expected_value = float(expected[column])
        if observed is None or abs(observed - expected_value) > 0.001:
            problems.append(
                f"{label}: {column} mismatch: expected {expected_value}, "
                f"observed {row.get(column, '')}"
            )

    basis = row.get("precursor_mz_basis", "")
    allowed_basis = expected["precursor_mz_basis"]
    if basis not in allowed_basis:
        problems.append(
            f"{label}: precursor_mz_basis {basis!r} not in "
            f"{sorted(allowed_basis)!r}"
        )

    error_basis = row.get("neutral_loss_error_basis", "")
    allowed_error_basis = expected["neutral_loss_error_basis"]
    if error_basis not in allowed_error_basis:
        problems.append(
            f"{label}: neutral_loss_error_basis {error_basis!r} not in "
            f"{sorted(allowed_error_basis)!r}"
        )

    min_abs_delta = float(expected["min_abs_scan_precursor_delta_da"])
    delta = abs(_optional_float(row.get("scan_precursor_delta_da", "")) or 0.0)
    max_delta = abs(
        _optional_float(row.get("max_scan_precursor_abs_delta_da", "")) or 0.0
    )
    if delta < min_abs_delta and max_delta < min_abs_delta:
        problems.append(
            f"{label}: scan precursor delta is too small for expected basis; "
            f"delta={delta}, max_delta={max_delta}"
        )

    _append_tag_evidence_problems(row, label, problems)


def _append_tag_evidence_problems(
    row: dict[str, str],
    label: str,
    problems: list[str],
) -> None:
    try:
        payload = json.loads(row.get("tag_evidence_json", "{}") or "{}")
    except json.JSONDecodeError as exc:
        problems.append(f"{label}: tag_evidence_json is not valid JSON: {exc}")
        return
    if not isinstance(payload, dict) or "DNA_dR" not in payload:
        problems.append(f"{label}: tag_evidence_json lacks DNA_dR evidence")
        return
    evidence = payload["DNA_dR"]
    if not isinstance(evidence, dict):
        problems.append(f"{label}: DNA_dR evidence is not an object")
        return
    basis = evidence.get("precursor_mz_basis")
    if basis not in {"scan_precursor", "product_plus_neutral_loss", "mixed"}:
        problems.append(f"{label}: invalid tag_evidence precursor_mz_basis {basis!r}")


def _optional_float(value: str) -> float | None:
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _optional_int(value: str) -> int | None:
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _relative_or_absolute(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check the Discovery precursor-inference artifact for row identity, "
            "inferred 300 row recovery, 301 row preservation, and provenance basis."
        )
    )
    parser.add_argument("--candidates-csv", type=Path, default=DEFAULT_CANDIDATES_CSV)
    parser.add_argument(
        "--expected-row-count",
        type=int,
        default=DEFAULT_EXPECTED_ROW_COUNT,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    candidates_csv = args.candidates_csv
    problems = check_discovery_precursor_inference_artifact(
        candidates_csv=candidates_csv,
        expected_row_count=args.expected_row_count,
    )
    if problems:
        for problem in problems:
            print(f"discovery_precursor_inference_problem: {problem}")
        return 1
    print(f"discovery_precursor_inference_candidates_csv: {candidates_csv}")
    print(f"discovery_precursor_inference_candidates_sha256: {_sha256(candidates_csv)}")
    print("discovery_precursor_inference_status: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
