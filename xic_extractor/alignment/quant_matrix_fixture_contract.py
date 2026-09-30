"""Summary/hash contracts for bulky QuantMatrix validation TSVs.

These helpers keep validation replay contracts in git without requiring full
generated result tables to stay tracked. They only read and summarize existing
TSVs; they do not run extraction, scoring, ProductWriter, workbook output, or
matrix generation.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xic_extractor.alignment.quant_matrix_report import (
    QUANT_MATRIX_REVIEW_ROW_COLUMNS,
)
from xic_extractor.diagnostics.quant_matrix.version import CELL_PROVENANCE_COLUMNS
from xic_extractor.tabular_io import (
    file_sha256,
    read_tsv_with_header,
    write_tsv,
)

FIXTURE_CONTRACT_SCHEMA = "quant_matrix_fixture_contract"
ROW_UNIVERSE_KEY_COLUMNS = (
    "peak_hypothesis_id",
    "sample_stem",
    "source_feature_family_ids",
    "cell_status",
)


def write_cell_provenance_contract(
    full_tsv: Path,
    summary_json: Path,
    fixture_tsv: Path,
    *,
    source_relpath: str,
) -> None:
    _write_contract(
        full_tsv,
        summary_json,
        fixture_tsv,
        source_relpath=source_relpath,
        required_columns=CELL_PROVENANCE_COLUMNS,
        count_columns=("cell_status", "write_authority", "value_source"),
        artifact_kind="cell_provenance",
    )


def write_review_rows_contract(
    full_tsv: Path,
    summary_json: Path,
    fixture_tsv: Path,
    *,
    source_relpath: str,
) -> None:
    _write_contract(
        full_tsv,
        summary_json,
        fixture_tsv,
        source_relpath=source_relpath,
        required_columns=QUANT_MATRIX_REVIEW_ROW_COLUMNS,
        count_columns=(
            "cell_status",
            "report_authority",
            "truth_status",
            "next_evidence_needed",
        ),
        artifact_kind="review_rows",
    )


def validate_fixture_contract(summary_json: Path, fixture_tsv: Path) -> list[str]:
    problems: list[str] = []
    try:
        payload = json.loads(summary_json.read_text(encoding="utf-8"))
    except OSError as exc:
        return [f"{summary_json}: {exc}"]
    except json.JSONDecodeError as exc:
        return [f"{summary_json}: invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return [f"{summary_json}: expected JSON object"]
    if payload.get("schema_version") != FIXTURE_CONTRACT_SCHEMA:
        problems.append(f"{summary_json}: schema_version mismatch")
    if payload.get("may_grant_product_authority") is not False:
        problems.append(f"{summary_json}: may_grant_product_authority must be false")

    column_names = _string_list(payload.get("column_names"))
    if not column_names:
        problems.append(f"{summary_json}: column_names missing")
    source_row_count = _int_value(payload.get("source_row_count"))
    if source_row_count is None or source_row_count < 0:
        problems.append(f"{summary_json}: source_row_count invalid")
    source_sha = str(payload.get("source_sha256", ""))
    if not _is_sha256(source_sha):
        problems.append(f"{summary_json}: source_sha256 invalid")

    minimal = payload.get("minimal_fixture")
    if not isinstance(minimal, dict):
        problems.append(f"{summary_json}: minimal_fixture missing")
        return problems
    expected_rows = _int_value(minimal.get("row_count"))
    expected_sha = str(minimal.get("sha256", ""))
    if expected_rows is None:
        problems.append(f"{summary_json}: minimal fixture row_count invalid")
    if not _is_sha256(expected_sha):
        problems.append(f"{summary_json}: minimal fixture sha256 invalid")
    row_universe = payload.get("row_universe")
    if isinstance(row_universe, dict):
        row_universe_count = _int_value(row_universe.get("row_count"))
        if (
            source_row_count is not None
            and row_universe_count is not None
            and source_row_count != row_universe_count
        ):
            problems.append(f"{summary_json}: source_row_count mismatch")
        if (
            source_row_count is not None
            and expected_rows is not None
            and expected_rows > source_row_count
        ):
            problems.append(f"{summary_json}: minimal fixture row_count too large")
    if not fixture_tsv.is_file():
        problems.append(f"{fixture_tsv}: minimal fixture missing")
        return problems
    try:
        header, rows = read_tsv_with_header(fixture_tsv, required_columns=column_names)
    except (OSError, ValueError) as exc:
        problems.append(f"{fixture_tsv}: {exc}")
        return problems
    if tuple(header) != tuple(column_names):
        problems.append(f"{fixture_tsv}: minimal fixture schema mismatch")
    if expected_rows is not None and len(rows) != expected_rows:
        problems.append(f"{fixture_tsv}: minimal fixture row count mismatch")
    if expected_sha and file_sha256(fixture_tsv) != expected_sha.upper():
        problems.append(f"{fixture_tsv}: minimal fixture sha256 mismatch")
    _append_count_shape_problems(payload, problems)
    _append_row_universe_shape_problems(payload, problems)
    return problems


def _write_contract(
    full_tsv: Path,
    summary_json: Path,
    fixture_tsv: Path,
    *,
    source_relpath: str,
    required_columns: Sequence[str],
    count_columns: Sequence[str],
    artifact_kind: str,
) -> None:
    header, rows = read_tsv_with_header(full_tsv, required_columns=required_columns)
    if tuple(header) != tuple(required_columns):
        raise ValueError(f"{full_tsv}: unexpected column order")
    fixture_rows, selection_warnings = _minimal_fixture_rows(rows)
    fixture_tsv.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        fixture_tsv,
        fixture_rows,
        tuple(header),
        extrasaction="raise",
        lineterminator="\n",
    )
    payload = {
        "schema_version": FIXTURE_CONTRACT_SCHEMA,
        "artifact_kind": artifact_kind,
        "source_relpath": source_relpath,
        "source_row_count": len(rows),
        "source_sha256": file_sha256(full_tsv),
        "column_names": list(header),
        "counts": {
            column: _counts(rows, column)
            for column in count_columns
        },
        "row_universe": _row_universe(rows),
        "minimal_fixture": {
            "path": fixture_tsv.name,
            "row_count": len(fixture_rows),
            "sha256": file_sha256(fixture_tsv),
            "selection_rule": "first detected row plus first accepted_backfill row",
            "selection_warnings": selection_warnings,
        },
        "may_grant_product_authority": False,
    }
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _minimal_fixture_rows(
    rows: Sequence[Mapping[str, str]],
) -> tuple[list[Mapping[str, str]], list[str]]:
    selected: list[Mapping[str, str]] = []
    warnings: list[str] = []
    for status in ("detected", "accepted_backfill"):
        match = next((row for row in rows if row.get("cell_status") == status), None)
        if match is None:
            warnings.append(f"missing_{status}_row")
        else:
            selected.append(match)
    if not selected and rows:
        selected.append(rows[0])
        warnings.append("fallback_first_row")
    return selected, warnings


def _counts(rows: Sequence[Mapping[str, str]], column: str) -> dict[str, int]:
    return dict(sorted(Counter(row.get(column, "") for row in rows).items()))


def _row_universe(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    keys = [
        {column: row.get(column, "") for column in ROW_UNIVERSE_KEY_COLUMNS}
        for row in rows
    ]
    encoded = json.dumps(keys, separators=(",", ":"), sort_keys=True).encode("utf-8")
    import hashlib

    return {
        "key_columns": list(ROW_UNIVERSE_KEY_COLUMNS),
        "row_count": len(keys),
        "sha256": hashlib.sha256(encoded).hexdigest().upper(),
    }


def _append_count_shape_problems(
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        problems.append("counts missing")
        return
    for column, values in counts.items():
        if not isinstance(column, str) or not isinstance(values, dict):
            problems.append("counts shape invalid")
            return
        for value in values.values():
            if not isinstance(value, int) or value < 0:
                problems.append("counts contain invalid value")
                return


def _append_row_universe_shape_problems(
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    row_universe = payload.get("row_universe")
    if not isinstance(row_universe, dict):
        problems.append("row_universe missing")
        return
    if row_universe.get("key_columns") != list(ROW_UNIVERSE_KEY_COLUMNS):
        problems.append("row_universe key_columns mismatch")
    if _int_value(row_universe.get("row_count")) is None:
        problems.append("row_universe row_count invalid")
    if not _is_sha256(str(row_universe.get("sha256", ""))):
        problems.append("row_universe sha256 invalid")


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result = [item for item in value if isinstance(item, str)]
    return result if len(result) == len(value) else []


def _int_value(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)
