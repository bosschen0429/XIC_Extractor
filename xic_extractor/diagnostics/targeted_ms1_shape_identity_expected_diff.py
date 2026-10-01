from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.diagnostics.targeted_ms1_shape_identity import (
    TARGETED_MS1_SHAPE_IDENTITY_COLUMNS,
)
from xic_extractor.extraction.targeted_ms1_shape_identity_projection import (
    TargetedMs1ShapeIdentitySupport,
    targeted_ms1_shape_identity_supports_from_rows,
)
from xic_extractor.tabular_io import read_tsv_required, write_tsv
from xic_extractor.targeted_ms1_shape_identity_policy import (
    LIMITED_HMDC_MEDC_POLICY,
    LIMITED_HMDC_MEDC_TARGETS,
)

EXPECTED_DIFF_REQUIRED_COLUMNS = (
    "sample_name",
    "target_name",
    "role",
    "changed_fields",
    "baseline_product_state",
    "optin_product_state",
    "baseline_counted_detection",
    "optin_counted_detection",
    "baseline_rt",
    "optin_rt",
    "baseline_area",
    "optin_area",
    "baseline_nl",
    "optin_nl",
    "baseline_reason",
    "optin_reason",
)
MATRIX_DIFF_REQUIRED_COLUMNS = (
    "row_key",
    "column",
    "baseline_value",
    "optin_value",
)
EXPECTED_DIFF_GATE_SUMMARY_COLUMNS = (
    "metric",
    "value",
)

_ALLOWED_LONG_CHANGED_FIELDS = frozenset(
    {
        "Product State",
        "Counted Detection",
        "RT",
        "Area",
        "Int",
        "PeakStart",
        "PeakEnd",
        "PeakWidth",
        "Confidence",
        "Reason",
        "Projection Support Reasons",
        "Projection Not Counted Reasons",
    }
)
_REQUIRED_LONG_CHANGED_FIELDS = frozenset(
    {"Product State", "Counted Detection", "Reason"}
)
_ALLOWED_MATRIX_MEASUREMENTS = frozenset(
    {"RT", "Int", "Area", "PeakStart", "PeakEnd", "PeakWidth"}
)


@dataclass(frozen=True)
class TargetedMs1ShapeIdentityExpectedDiffGateSummary:
    gate_status: str
    activation_policy: str
    long_changed_rows: int
    matrix_changed_cells: int
    target_counts: Mapping[str, int]
    matrix_target_counts: Mapping[str, int]
    support_tsv_supported_rows: int | None = None
    support_tsv_target_counts: Mapping[str, int] | None = None

    def to_rows(self) -> tuple[dict[str, str], ...]:
        rows = [
            {"metric": "gate_status", "value": self.gate_status},
            {"metric": "activation_policy", "value": self.activation_policy},
            {
                "metric": "allowed_targets",
                "value": ";".join(sorted(LIMITED_HMDC_MEDC_TARGETS)),
            },
            {"metric": "long_changed_rows", "value": str(self.long_changed_rows)},
            {
                "metric": "matrix_changed_cells",
                "value": str(self.matrix_changed_cells),
            },
            {
                "metric": "target_counts",
                "value": _format_counts(self.target_counts),
            },
            {
                "metric": "matrix_target_counts",
                "value": _format_counts(self.matrix_target_counts),
            },
        ]
        if self.support_tsv_supported_rows is not None:
            rows.extend(
                [
                    {
                        "metric": "support_tsv_supported_rows",
                        "value": str(self.support_tsv_supported_rows),
                    },
                    {
                        "metric": "support_tsv_target_counts",
                        "value": _format_counts(
                            self.support_tsv_target_counts or {},
                        ),
                    },
                ]
            )
        return tuple(rows)


def evaluate_limited_targeted_ms1_shape_identity_expected_diff(
    expected_diff_rows: Sequence[Mapping[str, str]],
    matrix_diff_rows: Sequence[Mapping[str, str]],
    *,
    expected_long_row_count: int | None = None,
    expected_matrix_cell_count: int | None = None,
    support_tsv_rows: Sequence[Mapping[str, str]] | None = None,
) -> TargetedMs1ShapeIdentityExpectedDiffGateSummary:
    _require_count(
        "expected-diff long rows",
        len(expected_diff_rows),
        expected_long_row_count,
    )
    _require_count(
        "matrix diff cells",
        len(matrix_diff_rows),
        expected_matrix_cell_count,
    )
    expected_keys: set[tuple[str, str]] = set()
    for index, row in enumerate(expected_diff_rows, start=2):
        _validate_expected_diff_row(row, row_number=index)
        key = (_clean_text(row["sample_name"]), _clean_text(row["target_name"]))
        if key in expected_keys:
            raise ValueError(
                "expected-diff rows contain duplicate sample/target key: "
                f"{key[0]}|{key[1]}",
            )
        expected_keys.add(key)
    matrix_target_counts: Counter[str] = Counter()
    matrix_keys: set[tuple[str, str]] = set()
    for index, row in enumerate(matrix_diff_rows, start=2):
        target_name = _validate_matrix_diff_row(row, row_number=index)
        matrix_keys.add((_clean_text(row["row_key"]), target_name))
        matrix_target_counts[target_name] += 1
    _require_matching_key_sets(expected_keys, matrix_keys)
    if support_tsv_rows is None:
        raise ValueError(
            "support TSV rows are required for limited targeted MS1 shape "
            "identity expected-diff gate",
        )
    supports = targeted_ms1_shape_identity_supports_from_rows(
        support_tsv_rows,
        activation_policy=LIMITED_HMDC_MEDC_POLICY,
    )
    _require_matching_support_keys(expected_keys, supports)

    return TargetedMs1ShapeIdentityExpectedDiffGateSummary(
        gate_status="pass",
        activation_policy=LIMITED_HMDC_MEDC_POLICY,
        long_changed_rows=len(expected_diff_rows),
        matrix_changed_cells=len(matrix_diff_rows),
        target_counts=Counter(
            _clean_text(row["target_name"]) for row in expected_diff_rows
        ),
        matrix_target_counts=dict(matrix_target_counts),
        support_tsv_supported_rows=len(supports),
        support_tsv_target_counts=Counter(
            support.target_name for support in supports
        ),
    )


def evaluate_limited_targeted_ms1_shape_identity_expected_diff_paths(
    *,
    expected_diff_summary_tsv: Path,
    matrix_diff_summary_tsv: Path,
    support_tsv: Path | None = None,
    expected_long_row_count: int | None = None,
    expected_matrix_cell_count: int | None = None,
) -> TargetedMs1ShapeIdentityExpectedDiffGateSummary:
    expected_diff_rows = read_tsv_required(
        expected_diff_summary_tsv,
        EXPECTED_DIFF_REQUIRED_COLUMNS,
    )
    matrix_diff_rows = read_tsv_required(
        matrix_diff_summary_tsv,
        MATRIX_DIFF_REQUIRED_COLUMNS,
    )
    support_tsv_rows = None
    if support_tsv is not None:
        support_tsv_rows = read_tsv_required(
            support_tsv,
            TARGETED_MS1_SHAPE_IDENTITY_COLUMNS,
        )
    return evaluate_limited_targeted_ms1_shape_identity_expected_diff(
        expected_diff_rows,
        matrix_diff_rows,
        expected_long_row_count=expected_long_row_count,
        expected_matrix_cell_count=expected_matrix_cell_count,
        support_tsv_rows=support_tsv_rows,
    )


def write_expected_diff_gate_summary(
    path: Path,
    summary: TargetedMs1ShapeIdentityExpectedDiffGateSummary,
) -> None:
    write_tsv(path, summary.to_rows(), EXPECTED_DIFF_GATE_SUMMARY_COLUMNS)


def _validate_expected_diff_row(
    row: Mapping[str, str],
    *,
    row_number: int,
) -> None:
    row_label = _expected_diff_row_label(row, row_number=row_number)
    target_name = _clean_text(row["target_name"])
    if target_name not in LIMITED_HMDC_MEDC_TARGETS:
        raise ValueError(
            f"{row_label}: target outside {LIMITED_HMDC_MEDC_POLICY} scope"
        )
    if _clean_text(row["role"]) != "Analyte":
        raise ValueError(f"{row_label}: expected role Analyte")
    changed_fields = {
        field.strip()
        for field in _clean_text(row["changed_fields"]).split(";")
        if field.strip()
    }
    unknown_changed_fields = changed_fields - _ALLOWED_LONG_CHANGED_FIELDS
    if unknown_changed_fields:
        joined = ", ".join(sorted(unknown_changed_fields))
        raise ValueError(f"{row_label}: unexpected changed fields: {joined}")
    missing_changed_fields = _REQUIRED_LONG_CHANGED_FIELDS - changed_fields
    if missing_changed_fields:
        joined = ", ".join(sorted(missing_changed_fields))
        raise ValueError(f"{row_label}: missing required changed fields: {joined}")
    _require_value(row_label, row, "baseline_product_state", "not_counted")
    _require_value(row_label, row, "optin_product_state", "detected_flagged")
    _require_value(row_label, row, "baseline_counted_detection", "FALSE")
    _require_value(row_label, row, "optin_counted_detection", "TRUE")
    _require_value(row_label, row, "baseline_rt", "ND")
    _require_value(row_label, row, "baseline_area", "ND")
    _require_value(row_label, row, "baseline_nl", "NL_FAIL")
    _require_value(row_label, row, "optin_nl", "NL_FAIL")
    if _clean_text(row["optin_rt"]) in {"", "ND"}:
        raise ValueError(f"{row_label}: opt-in RT must be populated")
    if _clean_text(row["optin_area"]) in {"", "ND"}:
        raise ValueError(f"{row_label}: opt-in area must be populated")
    if "analyte_nl_fail_requires_policy" not in row["baseline_reason"]:
        raise ValueError(f"{row_label}: baseline reason lacks policy blocker")
    if "own_max_same_peak_support" not in row["optin_reason"]:
        raise ValueError(f"{row_label}: opt-in reason lacks own_max_same_peak_support")
    if "analyte_nl_fail_requires_policy" in row["optin_reason"]:
        raise ValueError(f"{row_label}: opt-in reason retained policy blocker")


def _validate_matrix_diff_row(
    row: Mapping[str, str],
    *,
    row_number: int,
) -> str:
    row_label = f"matrix diff row {row_number}"
    column = _clean_text(row["column"])
    target_name, measurement = _split_matrix_column(column, row_label=row_label)
    if target_name not in LIMITED_HMDC_MEDC_TARGETS:
        raise ValueError(
            f"{row_label}: target outside {LIMITED_HMDC_MEDC_POLICY} scope"
        )
    if measurement not in _ALLOWED_MATRIX_MEASUREMENTS:
        raise ValueError(f"{row_label}: unexpected matrix measurement {measurement!r}")
    _require_value(row_label, row, "baseline_value", "ND")
    if _clean_text(row["optin_value"]) in {"", "ND"}:
        raise ValueError(f"{row_label}: opt-in matrix value must be populated")
    return target_name


def _split_matrix_column(column: str, *, row_label: str) -> tuple[str, str]:
    for target_name in sorted(LIMITED_HMDC_MEDC_TARGETS, key=len, reverse=True):
        prefix = f"{target_name}_"
        if column.startswith(prefix):
            return target_name, column[len(prefix) :]
    raise ValueError(f"{row_label}: cannot parse limited target from column {column!r}")


def _require_matching_key_sets(
    expected_keys: set[tuple[str, str]],
    matrix_keys: set[tuple[str, str]],
) -> None:
    missing_matrix_keys = expected_keys - matrix_keys
    unexpected_matrix_keys = matrix_keys - expected_keys
    if missing_matrix_keys:
        joined = _format_key_set(missing_matrix_keys)
        raise ValueError(
            "matrix diff is missing sample/target keys from expected diff: "
            f"{joined}",
        )
    if unexpected_matrix_keys:
        joined = _format_key_set(unexpected_matrix_keys)
        raise ValueError(
            "matrix diff contains sample/target keys outside expected diff: "
            f"{joined}",
        )


def _require_matching_support_keys(
    expected_keys: set[tuple[str, str]],
    supports: Sequence[TargetedMs1ShapeIdentitySupport],
) -> None:
    support_keys = {
        (support.sample_name, support.target_name) for support in supports
    }
    missing_support_keys = expected_keys - support_keys
    unused_support_keys = support_keys - expected_keys
    if missing_support_keys:
        joined = _format_key_set(missing_support_keys)
        raise ValueError(
            "support TSV is missing sample/target keys from expected diff: "
            f"{joined}",
        )
    if unused_support_keys:
        joined = _format_key_set(unused_support_keys)
        raise ValueError(
            "support TSV contains supported sample/target keys that did not "
            f"change output: {joined}",
        )


def _require_value(
    row_label: str,
    row: Mapping[str, str],
    field: str,
    expected_value: str,
) -> None:
    actual_value = _clean_text(row[field])
    if actual_value != expected_value:
        raise ValueError(
            f"{row_label}: expected {field}={expected_value!r}, "
            f"got {actual_value!r}",
        )


def _require_count(label: str, actual: int, expected: int | None) -> None:
    if expected is None:
        return
    if actual != expected:
        raise ValueError(f"{label}: expected {expected}, got {actual}")


def _expected_diff_row_label(
    row: Mapping[str, str],
    *,
    row_number: int,
) -> str:
    return (
        f"expected-diff row {row_number} "
        f"{_clean_text(row.get('sample_name', ''))}|"
        f"{_clean_text(row.get('target_name', ''))}"
    )


def _format_counts(counts: Mapping[str, int]) -> str:
    return ";".join(f"{key}={counts[key]}" for key in sorted(counts))


def _format_key_set(keys: set[tuple[str, str]]) -> str:
    return ";".join(f"{sample}|{target}" for sample, target in sorted(keys))


def _clean_text(value: str) -> str:
    return str(value or "").strip()
