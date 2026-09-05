from __future__ import annotations

import argparse
import csv
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from xic_extractor.tabular_io import write_tsv

DEFAULT_NUMERIC_TOLERANCE = 1e-9
REVIEW_IDENTITY_COLUMNS = (
    "feature_family_id",
    "include_in_primary_matrix",
    "identity_decision",
    "identity_confidence",
    "primary_evidence",
    "identity_reason",
)


@dataclass(frozen=True)
class AlignmentParityResult:
    status: str
    checks: dict[str, str]
    differences: tuple[str, ...]


def run_alignment_parity(
    *,
    baseline_matrix_tsv: Path,
    optimized_matrix_tsv: Path,
    baseline_review_tsv: Path | None = None,
    optimized_review_tsv: Path | None = None,
    baseline_targeted_summary_tsv: Path | None = None,
    optimized_targeted_summary_tsv: Path | None = None,
    output_json: Path | None = None,
    output_md: Path | None = None,
    output_dir: Path | None = None,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
) -> AlignmentParityResult:
    differences: list[str] = []
    checks: dict[str, str] = {}
    artifact_rows: dict[str, list[dict[str, str]]] = {}

    matrix_differences = _compare_matrix_tsv(
        baseline_matrix_tsv,
        optimized_matrix_tsv,
        numeric_tolerance=numeric_tolerance,
    )
    checks["primary_matrix_tsv"] = "pass" if not matrix_differences else "fail"
    differences.extend(matrix_differences)
    artifact_rows["matrix_parity.tsv"] = _difference_rows(
        "primary_matrix_tsv",
        matrix_differences,
    )

    if baseline_review_tsv is not None or optimized_review_tsv is not None:
        if baseline_review_tsv is None or optimized_review_tsv is None:
            message = "identity review comparison requires both TSV paths"
            checks["primary_identity_decisions"] = "fail"
            differences.append(message)
            artifact_rows["identity_parity.tsv"] = _difference_rows(
                "primary_identity_decisions",
                (message,),
            )
        else:
            identity_differences = _compare_identity_review_tsv(
                baseline_review_tsv,
                optimized_review_tsv,
            )
            checks["primary_identity_decisions"] = (
                "pass" if not identity_differences else "fail"
            )
            differences.extend(identity_differences)
            artifact_rows["identity_parity.tsv"] = _difference_rows(
                "primary_identity_decisions",
                identity_differences,
            )

    if (
        baseline_targeted_summary_tsv is not None
        or optimized_targeted_summary_tsv is not None
    ):
        if (
            baseline_targeted_summary_tsv is None
            or optimized_targeted_summary_tsv is None
        ):
            message = "targeted benchmark comparison requires both TSV paths"
            checks["targeted_istd_benchmark"] = "fail"
            differences.append(message)
            artifact_rows["targeted_benchmark_delta.tsv"] = _difference_rows(
                "targeted_istd_benchmark",
                (message,),
            )
        else:
            targeted_differences = _compare_targeted_summary_tsv(
                baseline_targeted_summary_tsv,
                optimized_targeted_summary_tsv,
            )
            checks["targeted_istd_benchmark"] = (
                "pass" if not targeted_differences else "fail"
            )
            differences.extend(targeted_differences)
            artifact_rows["targeted_benchmark_delta.tsv"] = _difference_rows(
                "targeted_istd_benchmark",
                targeted_differences,
            )

    result = AlignmentParityResult(
        status="pass" if not differences else "fail",
        checks=checks,
        differences=tuple(differences),
    )
    if output_dir is not None:
        _write_artifacts(output_dir, result, artifact_rows)
    if output_json is not None:
        _write_json(output_json, result)
    if output_md is not None:
        _write_markdown(output_md, result)
    return result


def _compare_matrix_tsv(
    baseline_path: Path,
    optimized_path: Path,
    *,
    numeric_tolerance: float,
) -> list[str]:
    baseline = _read_tsv(baseline_path)
    optimized = _read_tsv(optimized_path)
    differences: list[str] = []
    if baseline.fieldnames != optimized.fieldnames:
        differences.append(
            "primary matrix columns differ: "
            f"{baseline.fieldnames!r} != {optimized.fieldnames!r}"
        )
        return differences
    key = "feature_family_id"
    baseline_rows = _rows_by_key(baseline.rows, key, baseline_path)
    optimized_rows = _rows_by_key(optimized.rows, key, optimized_path)
    if baseline_rows.keys() != optimized_rows.keys():
        differences.append(
            "primary matrix feature ids differ: "
            "missing_in_optimized="
            f"{sorted(baseline_rows.keys() - optimized_rows.keys())}; "
            f"new_in_optimized={sorted(optimized_rows.keys() - baseline_rows.keys())}"
        )
    for feature_id in sorted(baseline_rows.keys() & optimized_rows.keys()):
        left = baseline_rows[feature_id]
        right = optimized_rows[feature_id]
        for column in baseline.fieldnames:
            if _values_match(
                left.get(column, ""), right.get(column, ""), numeric_tolerance
            ):
                continue
            differences.append(
                f"primary matrix {feature_id} column {column}: "
                f"{left.get(column, '')!r} != {right.get(column, '')!r}"
            )
    return differences


def _compare_identity_review_tsv(
    baseline_path: Path,
    optimized_path: Path,
) -> list[str]:
    baseline = _read_tsv(baseline_path, required_columns=REVIEW_IDENTITY_COLUMNS)
    optimized = _read_tsv(optimized_path, required_columns=REVIEW_IDENTITY_COLUMNS)
    baseline_rows = _rows_by_key(baseline.rows, "feature_family_id", baseline_path)
    optimized_rows = _rows_by_key(optimized.rows, "feature_family_id", optimized_path)
    differences: list[str] = []
    baseline_primary_ids = {
        feature_id
        for feature_id, row in baseline_rows.items()
        if _is_primary_matrix_row(row)
    }
    optimized_primary_ids = {
        feature_id
        for feature_id, row in optimized_rows.items()
        if _is_primary_matrix_row(row)
    }
    if baseline_primary_ids != optimized_primary_ids:
        differences.append(
            "primary identity feature ids differ: "
            "missing_in_optimized="
            f"{sorted(baseline_primary_ids - optimized_primary_ids)}; "
            f"new_in_optimized={sorted(optimized_primary_ids - baseline_primary_ids)}"
        )
    for feature_id in sorted(baseline_rows.keys() & optimized_rows.keys()):
        left = baseline_rows[feature_id]
        right = optimized_rows[feature_id]
        if not (_is_primary_matrix_row(left) or _is_primary_matrix_row(right)):
            continue
        for column in REVIEW_IDENTITY_COLUMNS[1:]:
            if (
                _normalized_bool_or_text(left.get(column, ""))
                == _normalized_bool_or_text(right.get(column, ""))
            ):
                continue
            differences.append(
                f"identity {feature_id} column {column}: "
                f"{left.get(column, '')!r} != {right.get(column, '')!r}"
            )
    return differences


def _is_primary_matrix_row(row: Mapping[str, str]) -> bool:
    return _is_true(row.get("include_in_primary_matrix", ""))


def _compare_targeted_summary_tsv(
    baseline_path: Path,
    optimized_path: Path,
) -> list[str]:
    required = ("target_label", "active_tag", "status")
    baseline = _read_tsv(baseline_path, required_columns=required)
    optimized = _read_tsv(optimized_path, required_columns=required)
    baseline_by_label = _rows_by_key(baseline.rows, "target_label", baseline_path)
    optimized_by_label = _rows_by_key(optimized.rows, "target_label", optimized_path)
    differences: list[str] = []
    if baseline_by_label.keys() != optimized_by_label.keys():
        differences.append(
            "targeted benchmark target labels differ: "
            f"missing_in_optimized="
            f"{sorted(baseline_by_label.keys() - optimized_by_label.keys())}; "
            f"new_in_optimized="
            f"{sorted(optimized_by_label.keys() - baseline_by_label.keys())}"
        )
    baseline_active_fails = _active_fail_labels(baseline_by_label)
    optimized_active_fails = _active_fail_labels(optimized_by_label)
    new_active_failures = sorted(optimized_active_fails - baseline_active_fails)
    if new_active_failures:
        differences.append(
            "targeted benchmark has new active FAIL targets: "
            + ", ".join(new_active_failures)
        )
    for label in sorted(baseline_by_label.keys() & optimized_by_label.keys()):
        left = baseline_by_label[label]
        right = optimized_by_label[label]
        for column in ("status", "primary_match_count", "selected_feature_id"):
            if column not in baseline.fieldnames or column not in optimized.fieldnames:
                continue
            if left.get(column, "") == right.get(column, ""):
                continue
            differences.append(
                f"targeted benchmark {label} column {column}: "
                f"{left.get(column, '')!r} != {right.get(column, '')!r}"
            )
    return differences


@dataclass(frozen=True)
class _TsvTable:
    fieldnames: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def _read_tsv(
    path: Path,
    *,
    required_columns: Sequence[str] = (),
) -> _TsvTable:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = tuple(reader.fieldnames or ())
        missing = [column for column in required_columns if column not in fieldnames]
        if missing:
            raise ValueError(f"{path}: missing required columns: {', '.join(missing)}")
        return _TsvTable(fieldnames, tuple(dict(row) for row in reader))


def _rows_by_key(
    rows: Sequence[dict[str, str]],
    key: str,
    path: Path,
) -> dict[str, dict[str, str]]:
    keyed: dict[str, dict[str, str]] = {}
    for row in rows:
        value = row.get(key, "")
        if not value:
            raise ValueError(f"{path}: row missing {key}")
        if value in keyed:
            raise ValueError(f"{path}: duplicate {key} {value!r}")
        keyed[value] = row
    return keyed


def _active_fail_labels(rows: Mapping[str, dict[str, str]]) -> set[str]:
    return {
        label
        for label, row in rows.items()
        if _is_true(row.get("active_tag", "")) and row.get("status", "") == "FAIL"
    }


def _values_match(left: str, right: str, numeric_tolerance: float) -> bool:
    if left == right:
        return True
    left_float = _float_or_none(left)
    right_float = _float_or_none(right)
    if left_float is None or right_float is None:
        return False
    return abs(left_float - right_float) <= numeric_tolerance


def _float_or_none(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _normalized_bool_or_text(value: str) -> str:
    if _is_true(value):
        return "TRUE"
    if value.strip().lower() in {"false", "0", "no"}:
        return "FALSE"
    return value


def _is_true(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes"}


def _write_json(path: Path, result: AlignmentParityResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(result), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_markdown(path: Path, result: AlignmentParityResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Alignment Parity",
        "",
        f"Status: {result.status}",
        "",
        "| Check | Status |",
        "|---|---|",
    ]
    lines.extend(
        f"| {check} | {status} |"
        for check, status in sorted(result.checks.items())
    )
    if result.differences:
        lines.extend(["", "## Differences", ""])
        lines.extend(f"- {difference}" for difference in result.differences)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_artifacts(
    output_dir: Path,
    result: AlignmentParityResult,
    artifact_rows: Mapping[str, Sequence[Mapping[str, str]]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in (
        "matrix_parity.tsv",
        "identity_parity.tsv",
        "targeted_benchmark_delta.tsv",
    ):
        _write_rows_tsv(
            output_dir / filename,
            artifact_rows.get(filename, _difference_rows(filename, ())),
        )
    _write_json(output_dir / "alignment_parity.json", result)
    _write_markdown(output_dir / "alignment_parity.md", result)


def _difference_rows(
    check: str,
    differences: Sequence[str],
) -> list[dict[str, str]]:
    if not differences:
        return [{"check": check, "status": "PASS", "difference": ""}]
    return [
        {"check": check, "status": "FAIL", "difference": difference}
        for difference in differences
    ]


def _write_rows_tsv(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(path, rows, ("check", "status", "difference"), lineterminator="\n")


def _error_result(error: Exception) -> AlignmentParityResult:
    return AlignmentParityResult(
        status="fail",
        checks={"input_validation": "fail"},
        differences=(f"{type(error).__name__}: {error}",),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run alignment parity checks.")
    parser.add_argument("--baseline-alignment-dir", type=Path)
    parser.add_argument("--optimized-alignment-dir", type=Path)
    parser.add_argument("--baseline-matrix-tsv", type=Path)
    parser.add_argument("--optimized-matrix-tsv", type=Path)
    parser.add_argument("--baseline-review-tsv", type=Path)
    parser.add_argument("--optimized-review-tsv", type=Path)
    parser.add_argument("--baseline-targeted-summary-tsv", type=Path)
    parser.add_argument("--optimized-targeted-summary-tsv", type=Path)
    parser.add_argument("--baseline-benchmark-summary-tsv", type=Path)
    parser.add_argument("--optimized-benchmark-summary-tsv", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--numeric-tolerance",
        type=float,
        default=DEFAULT_NUMERIC_TOLERANCE,
    )
    args = parser.parse_args(argv)
    try:
        baseline_matrix_tsv, optimized_matrix_tsv = _resolve_matrix_paths(args)
        baseline_review_tsv, optimized_review_tsv = _resolve_review_paths(args)
        baseline_targeted, optimized_targeted = _resolve_benchmark_paths(args)
        result = run_alignment_parity(
            baseline_matrix_tsv=baseline_matrix_tsv,
            optimized_matrix_tsv=optimized_matrix_tsv,
            baseline_review_tsv=baseline_review_tsv,
            optimized_review_tsv=optimized_review_tsv,
            baseline_targeted_summary_tsv=baseline_targeted,
            optimized_targeted_summary_tsv=optimized_targeted,
            output_json=args.output_json,
            output_md=args.output_md,
            output_dir=args.output_dir,
            numeric_tolerance=args.numeric_tolerance,
        )
    except (OSError, ValueError) as exc:
        result = _error_result(exc)
        if args.output_dir is not None:
            _write_artifacts(args.output_dir, result, {})
        if args.output_json is not None:
            _write_json(args.output_json, result)
        if args.output_md is not None:
            _write_markdown(args.output_md, result)
    return 0 if result.status == "pass" else 1


def _resolve_matrix_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    if (
        args.baseline_alignment_dir is not None
        or args.optimized_alignment_dir is not None
    ):
        if args.baseline_alignment_dir is None or args.optimized_alignment_dir is None:
            raise ValueError("baseline and optimized alignment dirs are both required")
        return (
            args.baseline_alignment_dir / "alignment_matrix.tsv",
            args.optimized_alignment_dir / "alignment_matrix.tsv",
        )
    if args.baseline_matrix_tsv is None or args.optimized_matrix_tsv is None:
        raise ValueError("baseline and optimized matrix TSV paths are required")
    return args.baseline_matrix_tsv, args.optimized_matrix_tsv


def _resolve_review_paths(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    if (
        args.baseline_alignment_dir is not None
        or args.optimized_alignment_dir is not None
    ):
        if args.baseline_alignment_dir is None or args.optimized_alignment_dir is None:
            raise ValueError("baseline and optimized alignment dirs are both required")
        return (
            args.baseline_alignment_dir / "alignment_review.tsv",
            args.optimized_alignment_dir / "alignment_review.tsv",
        )
    return args.baseline_review_tsv, args.optimized_review_tsv


def _resolve_benchmark_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    baseline = args.baseline_benchmark_summary_tsv or args.baseline_targeted_summary_tsv
    optimized = (
        args.optimized_benchmark_summary_tsv
        or args.optimized_targeted_summary_tsv
    )
    if baseline is None or optimized is None:
        raise ValueError(
            "baseline and optimized targeted benchmark summary TSVs are required"
        )
    return baseline, optimized


if __name__ == "__main__":
    raise SystemExit(main())
