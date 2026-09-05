"""Compare targeted resolver area and RT stability."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median, stdev

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.tabular_io import write_tsv  # noqa: E402,I001


ROW_FIELDS = (
    "target_label",
    "sample_count",
    "baseline_area_rsd_pct",
    "candidate_area_rsd_pct",
    "area_rsd_delta_pct",
    "rt_median_abs_delta_sec",
    "status",
    "failure_reasons",
)

SUMMARY_FIELDS = (
    "overall_status",
    "failed_count",
    "target_count",
    "max_area_rsd_delta_pct",
    "max_rt_median_abs_delta_sec",
    "max_rsd_regression_pct",
    "max_rt_median_shift_sec",
)

_MISSING_VALUES = {"", "ND", "NA", "N/A", "NONE", "NULL"}


@dataclass(frozen=True)
class TargetedResolverComparisonOutputs:
    rows_tsv: Path
    summary_tsv: Path
    json_path: Path
    markdown_path: Path


@dataclass(frozen=True)
class TargetedResolverComparisonRow:
    target_label: str
    sample_count: int
    baseline_area_rsd_pct: float | None
    candidate_area_rsd_pct: float | None
    area_rsd_delta_pct: float | None
    rt_median_abs_delta_sec: float | None
    status: str
    failure_reasons: tuple[str, ...]


@dataclass(frozen=True)
class TargetedResolverComparisonResult:
    overall_status: str
    failed_count: int
    max_area_rsd_delta_pct: float | None
    max_rt_median_abs_delta_sec: float | None
    rows: tuple[TargetedResolverComparisonRow, ...]


def run_targeted_resolver_comparison(
    *,
    baseline_results_csv: Path,
    candidate_results_csv: Path,
    targets_csv: Path,
    output_dir: Path,
    max_rsd_regression_pct: float = 0.5,
    max_rt_median_shift_sec: float = 0.5,
) -> tuple[TargetedResolverComparisonOutputs, TargetedResolverComparisonResult]:
    labels = _read_istd_labels(targets_csv)
    baseline = _read_results(baseline_results_csv, labels)
    candidate = _read_results(candidate_results_csv, labels)
    rows = tuple(
        _build_row(
            label,
            baseline=baseline,
            candidate=candidate,
            max_rsd_regression_pct=max_rsd_regression_pct,
            max_rt_median_shift_sec=max_rt_median_shift_sec,
        )
        for label in labels
    )
    result = TargetedResolverComparisonResult(
        overall_status="FAIL" if any(row.status == "FAIL" for row in rows) else "PASS",
        failed_count=sum(row.status == "FAIL" for row in rows),
        max_area_rsd_delta_pct=_max_present(row.area_rsd_delta_pct for row in rows),
        max_rt_median_abs_delta_sec=_max_present(
            row.rt_median_abs_delta_sec for row in rows
        ),
        rows=rows,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = TargetedResolverComparisonOutputs(
        rows_tsv=output_dir / "targeted_resolver_comparison_rows.tsv",
        summary_tsv=output_dir / "targeted_resolver_comparison_summary.tsv",
        json_path=output_dir / "targeted_resolver_comparison.json",
        markdown_path=output_dir / "targeted_resolver_comparison.md",
    )
    _write_outputs(
        outputs,
        result,
        max_rsd_regression_pct=max_rsd_regression_pct,
        max_rt_median_shift_sec=max_rt_median_shift_sec,
    )
    return outputs, result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare local_minimum and region_first_safe_merge targeted "
            "ISTD area RSD and RT shifts for the targeted resolver comparison."
        )
    )
    parser.add_argument("--baseline-results-csv", type=Path, required=True)
    parser.add_argument("--candidate-results-csv", type=Path, required=True)
    parser.add_argument("--targets-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-rsd-regression-pct", type=float, default=0.5)
    parser.add_argument("--max-rt-median-shift-sec", type=float, default=0.5)
    args = parser.parse_args(argv)

    try:
        outputs, result = run_targeted_resolver_comparison(
            baseline_results_csv=args.baseline_results_csv.resolve(),
            candidate_results_csv=args.candidate_results_csv.resolve(),
            targets_csv=args.targets_csv.resolve(),
            output_dir=args.output_dir.resolve(),
            max_rsd_regression_pct=args.max_rsd_regression_pct,
            max_rt_median_shift_sec=args.max_rt_median_shift_sec,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Rows TSV: {outputs.rows_tsv}")
    print(f"Summary TSV: {outputs.summary_tsv}")
    print(f"Gate JSON: {outputs.json_path}")
    print(f"Gate report: {outputs.markdown_path}")
    return 0 if result.overall_status == "PASS" else 1


def _read_istd_labels(path: Path) -> tuple[str, ...]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        _require_columns(path, reader.fieldnames, {"label", "is_istd"})
        labels = tuple(
            row["label"].strip()
            for row in reader
            if row.get("label", "").strip() and _parse_bool(row.get("is_istd", ""))
        )
    if not labels:
        raise ValueError(f"{path}: no ISTD target rows found")
    return labels


def _read_results(
    path: Path,
    labels: Sequence[str],
) -> dict[str, dict[str, tuple[float | None, float | None]]]:
    required = {"SampleName"}
    for label in labels:
        required.add(f"{label}_Area")
        required.add(f"{label}_RT")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        _require_columns(path, reader.fieldnames, required)
        rows: dict[str, dict[str, tuple[float | None, float | None]]] = {}
        for row in reader:
            sample = row.get("SampleName", "").strip()
            if not sample:
                continue
            rows[sample] = {
                label: (
                    _optional_float(path, label, "Area", row.get(f"{label}_Area")),
                    _optional_float(path, label, "RT", row.get(f"{label}_RT")),
                )
                for label in labels
            }
    return rows


def _build_row(
    label: str,
    *,
    baseline: Mapping[str, Mapping[str, tuple[float | None, float | None]]],
    candidate: Mapping[str, Mapping[str, tuple[float | None, float | None]]],
    max_rsd_regression_pct: float,
    max_rt_median_shift_sec: float,
) -> TargetedResolverComparisonRow:
    baseline_areas: list[float] = []
    candidate_areas: list[float] = []
    rt_deltas_min: list[float] = []
    paired_samples = sorted(set(baseline) & set(candidate))
    for sample in paired_samples:
        baseline_area, baseline_rt = baseline[sample][label]
        candidate_area, candidate_rt = candidate[sample][label]
        if (
            baseline_area is None
            or baseline_rt is None
            or candidate_area is None
            or candidate_rt is None
        ):
            continue
        baseline_areas.append(baseline_area)
        candidate_areas.append(candidate_area)
        rt_deltas_min.append(abs(candidate_rt - baseline_rt))

    sample_count = len(baseline_areas)
    baseline_rsd = _area_rsd_pct(baseline_areas)
    candidate_rsd = _area_rsd_pct(candidate_areas)
    area_delta = (
        None
        if baseline_rsd is None or candidate_rsd is None
        else candidate_rsd - baseline_rsd
    )
    rt_shift_sec = None if not rt_deltas_min else median(rt_deltas_min) * 60.0
    reasons: list[str] = []
    if sample_count < 2:
        reasons.append("paired_sample_count_lt_2")
    if area_delta is None:
        reasons.append("area_rsd_unavailable")
    elif area_delta > max_rsd_regression_pct:
        reasons.append("area_rsd_regression")
    if rt_shift_sec is None:
        reasons.append("rt_shift_unavailable")
    elif rt_shift_sec > max_rt_median_shift_sec:
        reasons.append("rt_median_shift_regression")
    return TargetedResolverComparisonRow(
        target_label=label,
        sample_count=sample_count,
        baseline_area_rsd_pct=baseline_rsd,
        candidate_area_rsd_pct=candidate_rsd,
        area_rsd_delta_pct=area_delta,
        rt_median_abs_delta_sec=rt_shift_sec,
        status="FAIL" if reasons else "PASS",
        failure_reasons=tuple(reasons),
    )


def _area_rsd_pct(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    if mean == 0:
        return None
    return stdev(values) / mean * 100.0


def _write_outputs(
    outputs: TargetedResolverComparisonOutputs,
    result: TargetedResolverComparisonResult,
    *,
    max_rsd_regression_pct: float,
    max_rt_median_shift_sec: float,
) -> None:
    _write_tsv(outputs.rows_tsv, ROW_FIELDS, (_row_dict(row) for row in result.rows))
    _write_tsv(
        outputs.summary_tsv,
        SUMMARY_FIELDS,
        (
            {
                **_summary_dict(result),
                "target_count": len(result.rows),
                "max_rsd_regression_pct": max_rsd_regression_pct,
                "max_rt_median_shift_sec": max_rt_median_shift_sec,
            },
        ),
    )
    outputs.json_path.write_text(
        json.dumps(
            {
                **_summary_dict(result),
                "thresholds": {
                    "max_rsd_regression_pct": max_rsd_regression_pct,
                    "max_rt_median_shift_sec": max_rt_median_shift_sec,
                },
                "rows": [_row_dict(row) for row in result.rows],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_markdown(outputs.markdown_path, result)


def _row_dict(row: TargetedResolverComparisonRow) -> dict[str, object]:
    return {
        **asdict(row),
        "failure_reasons": ";".join(row.failure_reasons),
    }


def _summary_dict(result: TargetedResolverComparisonResult) -> dict[str, object]:
    return {
        "overall_status": result.overall_status,
        "failed_count": result.failed_count,
        "max_area_rsd_delta_pct": result.max_area_rsd_delta_pct,
        "max_rt_median_abs_delta_sec": result.max_rt_median_abs_delta_sec,
    }


def _write_markdown(path: Path, result: TargetedResolverComparisonResult) -> None:
    lines = [
        "# Targeted Resolver Comparison",
        "",
        f"Overall status: {result.overall_status}",
        "",
        "| Target | Samples | Area RSD delta pct | RT median abs delta sec | "
        "Status | Reasons |",
        "|---|---:|---:|---:|---|---|",
    ]
    for row in result.rows:
        lines.append(
            "| "
            f"{row.target_label} | "
            f"{row.sample_count} | "
            f"{_format_value(row.area_rsd_delta_pct)} | "
            f"{_format_value(row.rt_median_abs_delta_sec)} | "
            f"{row.status} | "
            f"{';'.join(row.failure_reasons)} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_tsv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    write_tsv(
        path,
        tuple(rows),
        fieldnames,
        formatter=_format_value,
        lineterminator="\n",
    )


def _require_columns(
    path: Path,
    fieldnames: Sequence[str] | None,
    required: set[str],
) -> None:
    columns = set(fieldnames or ())
    missing = sorted(required - columns)
    if missing:
        raise ValueError(f"{path}: missing required columns: {', '.join(missing)}")


def _optional_float(
    path: Path,
    label: str,
    field: str,
    value: object,
) -> float | None:
    text = "" if value is None else str(value).strip()
    if text.upper() in _MISSING_VALUES:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise ValueError(f"{path}: non-numeric {label}_{field}: {text}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{path}: non-finite {label}_{field}: {text}")
    return parsed


def _parse_bool(value: object) -> bool:
    return str(value).strip().upper() in {"1", "TRUE", "YES", "Y"}


def _max_present(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _format_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return f"{value:.6g}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
