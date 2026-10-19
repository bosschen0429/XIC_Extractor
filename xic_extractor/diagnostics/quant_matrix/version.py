from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from xic_extractor.tabular_io import (
    numeric_equal,
    optional_float,
    render_delimited_rows,
)

QUANT_MATRIX_VERSION_SCHEMA = "quant_matrix_version"
CELL_PROVENANCE_SCHEMA = "quant_matrix_cell_provenance"
ROW_SUMMARY_SCHEMA = "quant_matrix_row_summary"
EXPECTED_DIFF_SCHEMA = "quant_matrix_version_expected_diff"
EXPECTED_DIFF_SUMMARY_SCHEMA = "quant_matrix_version_expected_diff_summary"

PRODUCTION_ACCEPTANCE_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "sample_stem",
    "feature_family_id",
    "acceptance_decision",
    "acceptance_basis",
    "truth_status",
    "shadow_only",
    "write_authority",
    "matrix_write_allowed",
    "quant_value",
    "quant_value_source",
    "matrix_area_source",
    "detected_count",
    "backfilled_count",
    "quant_available_count",
    "missing_count",
    "backfill_fraction",
    "prevalence_flags",
    "hard_blocker_rule_ids",
    "triggered_risk_rule_ids",
    "closure_rule_ids",
    "decision_reason",
    "next_evidence_needed",
    "doublet_status",
    "reference_side",
    "doublet_allowed",
    "doublet_source_relpath",
    "doublet_source_sha256",
    "source_artifact_relpath",
    "source_artifact_sha256",
    "source_row_sha256",
    "manifest_sha256",
    "acceptance_contract_version",
)

EXPECTED_DIFF_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "sample_stem",
    "baseline_value",
    "activated_value",
    "expected_matrix_effect",
    "expected_reason",
)

CELL_PROVENANCE_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "sample_stem",
    "source_feature_family_ids",
    "matrix_value",
    "cell_status",
    "value_source",
    "write_authority",
    "acceptance_decision",
    "acceptance_basis",
    "truth_status",
    "quant_value_source",
    "matrix_area_source",
    "source_artifact_relpath",
    "source_artifact_sha256",
    "source_row_sha256",
    "manifest_sha256",
)

ROW_SUMMARY_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "source_feature_family_ids",
    "detected_count",
    "accepted_backfilled_count",
    "quant_available_count",
    "missing_count",
    "backfill_fraction",
    "prevalence_flags",
)

EXPECTED_DIFF_SUMMARY_COLUMNS = (
    "schema_version",
    "acceptance_status",
    "expected_diff_count",
    "written_backfill_count",
    "unused_expected_diff_count",
    "blocking_reasons",
)

SOURCE_SUMMARY_COLUMNS = (
    "schema_version",
    "input_quant_matrix_tsv",
    "input_quant_matrix_sha256",
    "input_matrix_identity_tsv",
    "input_matrix_identity_sha256",
    "production_acceptance_manifest_tsv",
    "production_acceptance_manifest_sha256",
    "expected_diff_tsv",
    "expected_diff_sha256",
)


@dataclass(frozen=True)
class QuantMatrixVersionRows:
    quant_matrix_rows: tuple[Mapping[str, str], ...]
    cell_provenance_rows: tuple[Mapping[str, str], ...]
    row_summary_rows: tuple[Mapping[str, str], ...]
    expected_diff_summary_rows: tuple[Mapping[str, str], ...]


def production_acceptance_manifest_sha256(
    rows: Sequence[Mapping[str, str]],
) -> str:
    canonical_rows = []
    for row in rows:
        canonical = {
            column: row.get(column, "") for column in PRODUCTION_ACCEPTANCE_COLUMNS
        }
        canonical["manifest_sha256"] = ""
        canonical_rows.append(canonical)
    rendered = render_delimited_rows(
        canonical_rows,
        PRODUCTION_ACCEPTANCE_COLUMNS,
        delimiter="\t",
        extrasaction="raise",
        lineterminator="\n",
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest().upper()


def build_quant_matrix_version_rows(
    *,
    matrix_header: Sequence[str],
    input_quant_matrix_rows: Sequence[Mapping[str, str]],
    input_matrix_identity_rows: Sequence[Mapping[str, str]],
    production_acceptance_rows: Sequence[Mapping[str, str]],
    expected_diff_rows: Sequence[Mapping[str, str]],
) -> QuantMatrixVersionRows:
    """Pure row projection; file-backed writers require a checked manifest receipt."""

    sample_columns = _sample_columns(matrix_header)
    matrix_rows = [
        {column: row.get(column, "") for column in matrix_header}
        for row in input_quant_matrix_rows
    ]
    identity_by_peak = _identity_by_peak(input_matrix_identity_rows, len(matrix_rows))
    row_identity = _row_identity_by_matrix_index(input_matrix_identity_rows)
    accepted_by_key = accepted_production_acceptance_rows_by_key(
        production_acceptance_rows
    )
    expected_by_key = _expected_diff_by_key(expected_diff_rows)
    written_keys: set[tuple[str, str]] = set()

    for accepted_key, accepted in accepted_by_key.items():
        peak_hypothesis_id, sample_stem = accepted_key
        if sample_stem not in sample_columns:
            raise ValueError(
                f"{peak_hypothesis_id}/{sample_stem}: sample missing from quant matrix",
            )
        row_index = identity_by_peak.get(peak_hypothesis_id)
        if row_index is None:
            raise ValueError(
                f"{peak_hypothesis_id}/{sample_stem}: peak_hypothesis_id missing "
                "from matrix identity",
            )
        matrix_row = matrix_rows[row_index - 1]
        baseline_value = matrix_row.get(sample_stem, "")
        if baseline_value:
            raise ValueError(
                f"{peak_hypothesis_id}/{sample_stem}: cannot overwrite existing "
                "quant value",
            )
        expected = expected_by_key.pop(accepted_key, None)
        if expected is None:
            raise ValueError(
                f"{peak_hypothesis_id}/{sample_stem}: missing expected-diff row",
            )
        _validate_expected_diff(
            expected,
            baseline_value=baseline_value,
            activated_value=accepted.get("quant_value", ""),
        )
        matrix_row[sample_stem] = _format_numeric(accepted.get("quant_value", ""))
        written_keys.add(accepted_key)

    if expected_by_key:
        unused = ";".join(
            f"{peak_hypothesis_id}/{sample_stem}"
            for peak_hypothesis_id, sample_stem in sorted(expected_by_key)
        )
        raise ValueError(f"unused expected-diff row(s): {unused}")

    provenance_rows = _cell_provenance_rows(
        matrix_rows=matrix_rows,
        sample_columns=sample_columns,
        row_identity=row_identity,
        accepted_by_key=accepted_by_key,
        written_keys=written_keys,
    )
    row_summary_rows = _row_summary_rows(
        matrix_rows=matrix_rows,
        sample_columns=sample_columns,
        row_identity=row_identity,
        accepted_by_key=accepted_by_key,
        written_keys=written_keys,
    )
    expected_diff_summary_rows = [
        {
            "schema_version": EXPECTED_DIFF_SUMMARY_SCHEMA,
            "acceptance_status": "pass",
            "expected_diff_count": str(len(expected_diff_rows)),
            "written_backfill_count": str(len(written_keys)),
            "unused_expected_diff_count": "0",
            "blocking_reasons": "",
        }
    ]
    return QuantMatrixVersionRows(
        quant_matrix_rows=_freeze_rows(matrix_rows),
        cell_provenance_rows=_freeze_rows(provenance_rows),
        row_summary_rows=_freeze_rows(row_summary_rows),
        expected_diff_summary_rows=_freeze_rows(expected_diff_summary_rows),
    )


def _sample_columns(matrix_header: Sequence[str]) -> tuple[str, ...]:
    header = tuple(matrix_header)
    if len(header) < 3 or header[:2] != ("Mz", "RT"):
        raise ValueError("quant matrix header must start with Mz, RT")
    return tuple(column for column in header if column not in {"Mz", "RT"})


def _identity_by_peak(
    rows: Sequence[Mapping[str, str]],
    matrix_row_count: int,
) -> dict[str, int]:
    result: dict[str, int] = {}
    seen_matrix_indexes: dict[int, str] = {}
    for row in rows:
        peak_hypothesis_id = row.get("peak_hypothesis_id", "")
        matrix_row_index = _matrix_row_index(row, matrix_row_count)
        if not peak_hypothesis_id:
            raise ValueError("matrix identity row missing peak_hypothesis_id")
        previous_peak = seen_matrix_indexes.setdefault(
            matrix_row_index,
            peak_hypothesis_id,
        )
        if previous_peak != peak_hypothesis_id:
            raise ValueError(f"duplicate matrix_row_index: {matrix_row_index}")
        previous = result.setdefault(peak_hypothesis_id, matrix_row_index)
        if previous != matrix_row_index:
            raise ValueError(f"duplicate peak_hypothesis_id: {peak_hypothesis_id}")
    missing_indexes = sorted(
        set(range(1, matrix_row_count + 1)) - set(seen_matrix_indexes)
    )
    if missing_indexes:
        raise ValueError(
            "matrix identity rows must cover every quant matrix row: "
            + ";".join(str(index) for index in missing_indexes),
        )
    return result


def _row_identity_by_matrix_index(
    rows: Sequence[Mapping[str, str]],
) -> dict[int, Mapping[str, str]]:
    result: dict[int, Mapping[str, str]] = {}
    for row in rows:
        matrix_row_index = int(row.get("matrix_row_index", ""))
        if matrix_row_index in result:
            raise ValueError(f"duplicate matrix_row_index: {matrix_row_index}")
        result[matrix_row_index] = row
    return result


def _matrix_row_index(row: Mapping[str, str], matrix_row_count: int) -> int:
    try:
        matrix_row_index = int(row.get("matrix_row_index", ""))
    except ValueError as exc:
        raise ValueError("invalid matrix_row_index") from exc
    if matrix_row_index < 1 or matrix_row_index > matrix_row_count:
        raise ValueError(f"matrix_row_index out of range: {matrix_row_index}")
    return matrix_row_index


def accepted_production_acceptance_rows_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    """Select the exact ProductionAcceptance rows ProductWriter may write."""

    result: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        if row.get("acceptance_decision") not in {
            "accept_basic_backfill",
            "accept_strict_backfill",
        }:
            continue
        if row.get("write_authority") != "TRUE":
            continue
        if row.get("matrix_write_allowed") != "TRUE":
            continue
        if row.get("shadow_only") != "FALSE":
            continue
        key = (row.get("peak_hypothesis_id", ""), row.get("sample_stem", ""))
        if not all(key):
            raise ValueError("accepted manifest row missing primary key")
        if key in result:
            raise ValueError(f"duplicate accepted manifest key: {key[0]}/{key[1]}")
        result[key] = row
    return result


def _expected_diff_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    result: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        if row.get("schema_version") != EXPECTED_DIFF_SCHEMA:
            raise ValueError("expected-diff schema_version mismatch")
        key = (row.get("peak_hypothesis_id", ""), row.get("sample_stem", ""))
        if not all(key):
            raise ValueError("expected-diff row missing primary key")
        if key in result:
            raise ValueError(f"duplicate expected-diff key: {key[0]}/{key[1]}")
        result[key] = row
    return result


def _validate_expected_diff(
    row: Mapping[str, str],
    *,
    baseline_value: str,
    activated_value: str,
) -> None:
    if row.get("expected_matrix_effect") != "write_accepted_backfill":
        raise ValueError("expected-diff effect must be write_accepted_backfill")
    if row.get("baseline_value", "") != baseline_value:
        raise ValueError("expected-diff baseline_value mismatch")
    if not numeric_equal(row.get("activated_value", ""), activated_value):
        raise ValueError("expected-diff activated_value mismatch")


def _cell_provenance_rows(
    *,
    matrix_rows: Sequence[Mapping[str, str]],
    sample_columns: Sequence[str],
    row_identity: Mapping[int, Mapping[str, str]],
    accepted_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    written_keys: set[tuple[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for matrix_row_index, matrix_row in enumerate(matrix_rows, start=1):
        identity = row_identity[matrix_row_index]
        peak_hypothesis_id = identity.get("peak_hypothesis_id", "")
        source_feature_family_ids = identity.get("source_feature_family_ids", "")
        for sample_stem in sample_columns:
            value = matrix_row.get(sample_stem, "")
            if not value:
                continue
            key = (peak_hypothesis_id, sample_stem)
            accepted = accepted_by_key.get(key)
            if key in written_keys and accepted is not None:
                rows.append(
                    {
                        "schema_version": CELL_PROVENANCE_SCHEMA,
                        "peak_hypothesis_id": peak_hypothesis_id,
                        "sample_stem": sample_stem,
                        "source_feature_family_ids": source_feature_family_ids,
                        "matrix_value": value,
                        "cell_status": "accepted_backfill",
                        "value_source": "ProductionAcceptanceManifest",
                        "write_authority": "TRUE",
                        "acceptance_decision": accepted.get(
                            "acceptance_decision",
                            "",
                        ),
                        "acceptance_basis": accepted.get("acceptance_basis", ""),
                        "truth_status": accepted.get("truth_status", ""),
                        "quant_value_source": accepted.get("quant_value_source", ""),
                        "matrix_area_source": accepted.get("matrix_area_source", ""),
                        "source_artifact_relpath": accepted.get(
                            "source_artifact_relpath",
                            "",
                        ),
                        "source_artifact_sha256": accepted.get(
                            "source_artifact_sha256",
                            "",
                        ),
                        "source_row_sha256": accepted.get("source_row_sha256", ""),
                        "manifest_sha256": accepted.get("manifest_sha256", ""),
                    }
                )
            else:
                rows.append(
                    {
                        "schema_version": CELL_PROVENANCE_SCHEMA,
                        "peak_hypothesis_id": peak_hypothesis_id,
                        "sample_stem": sample_stem,
                        "source_feature_family_ids": source_feature_family_ids,
                        "matrix_value": value,
                        "cell_status": "detected",
                        "value_source": "input_quant_matrix",
                        "write_authority": "FALSE",
                        "acceptance_decision": "",
                        "acceptance_basis": "",
                        "truth_status": "",
                        "quant_value_source": "",
                        "matrix_area_source": "",
                        "source_artifact_relpath": "",
                        "source_artifact_sha256": "",
                        "source_row_sha256": "",
                        "manifest_sha256": "",
                    }
                )
    return rows


def _row_summary_rows(
    *,
    matrix_rows: Sequence[Mapping[str, str]],
    sample_columns: Sequence[str],
    row_identity: Mapping[int, Mapping[str, str]],
    accepted_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    written_keys: set[tuple[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for matrix_row_index, matrix_row in enumerate(matrix_rows, start=1):
        identity = row_identity[matrix_row_index]
        peak_hypothesis_id = identity.get("peak_hypothesis_id", "")
        accepted_backfilled_count = sum(
            1
            for sample_stem in sample_columns
            if (peak_hypothesis_id, sample_stem) in written_keys
        )
        available_count = sum(1 for sample in sample_columns if matrix_row.get(sample))
        detected_count = available_count - accepted_backfilled_count
        missing_count = len(sample_columns) - available_count
        backfill_fraction = (
            0.0
            if available_count == 0
            else accepted_backfilled_count / available_count
        )
        prevalence_flags = _prevalence_flags(
            accepted_by_key.get((peak_hypothesis_id, sample_stem))
            for sample_stem in sample_columns
        )
        rows.append(
            {
                "schema_version": ROW_SUMMARY_SCHEMA,
                "peak_hypothesis_id": peak_hypothesis_id,
                "source_feature_family_ids": identity.get(
                    "source_feature_family_ids",
                    "",
                ),
                "detected_count": str(detected_count),
                "accepted_backfilled_count": str(accepted_backfilled_count),
                "quant_available_count": str(available_count),
                "missing_count": str(missing_count),
                "backfill_fraction": f"{backfill_fraction:.6f}",
                "prevalence_flags": prevalence_flags,
            }
        )
    return rows


def _prevalence_flags(rows: Iterable[Mapping[str, str] | None]) -> str:
    flags: list[str] = []
    for row in rows:
        if row is None:
            continue
        for flag in row.get("prevalence_flags", "").split(";"):
            flag = flag.strip()
            if flag and flag not in flags:
                flags.append(flag)
    return ";".join(flags)


def _format_numeric(value: str) -> str:
    parsed = optional_float(value)
    if parsed is None:
        raise ValueError(f"invalid numeric quant value: {value!r}")
    return f"{parsed:.6g}"


def _freeze_rows(
    rows: Sequence[Mapping[str, str]],
) -> tuple[Mapping[str, str], ...]:
    return tuple(MappingProxyType(dict(row)) for row in rows)
