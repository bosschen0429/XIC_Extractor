"""Audit untargeted alignment against a targeted workbook ground truth."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.diagnostics.targeted_gt_alignment_audit_analysis import (
    _classify_sample,
    _closest_cell,
    _failure_mode,
    _is_production_cell,
    _production_cells_in_gt_window,
    _status,
)
from tools.diagnostics.targeted_gt_alignment_audit_io import (
    _cells_by_sample_in_review_range,
    _filter_review_by_mz,
    _load_target_ground_truth,
    _load_tsv,
    _propagate_sample_context,
    _rows_by_target_role,
    _target_workbook_rows,
)
from tools.diagnostics.targeted_gt_alignment_audit_models import (
    DRIFT_MODE,
    DUPLICATE_MODE,
    MISS_MODE,
    PASS_MODE,
    PRODUCTION_STATUSES,
    SPLIT_MODE,
    AuditConfig,
    TargetGroundTruth,
)
from tools.diagnostics.targeted_gt_alignment_audit_utils import (
    _cell_rt,
    _format_float,
    _is_numeric_text,
    _is_trueish,
    _join_ids,
    _rt_delta_sec,
    _to_float,
    _to_int,
    _unescape_excel_formula,
)
from tools.diagnostics.targeted_gt_alignment_audit_writers import (
    _as_output_dict,
    _escape_excel_formula,
    _svg_text,
    _write_dict_csv,
    _write_report,
    _write_svg,
)

__all__ = [
    "DRIFT_MODE",
    "DUPLICATE_MODE",
    "MISS_MODE",
    "PASS_MODE",
    "PRODUCTION_STATUSES",
    "SPLIT_MODE",
    "AuditConfig",
    "TargetGroundTruth",
    "_as_output_dict",
    "_cell_rt",
    "_cells_by_sample_in_review_range",
    "_classify_sample",
    "_closest_cell",
    "_escape_excel_formula",
    "_failure_mode",
    "_filter_review_by_mz",
    "_format_float",
    "_is_numeric_text",
    "_is_production_cell",
    "_is_trueish",
    "_join_ids",
    "_load_target_ground_truth",
    "_load_tsv",
    "_parse_args",
    "_production_cells_in_gt_window",
    "_propagate_sample_context",
    "_rows_by_target_role",
    "_rt_delta_sec",
    "_status",
    "_svg_text",
    "_target_workbook_rows",
    "_to_float",
    "_to_int",
    "_unescape_excel_formula",
    "_write_dict_csv",
    "_write_report",
    "_write_svg",
    "main",
]


def main(argv: Sequence[str] | None = None) -> int:
    config = _parse_args(argv)
    target_rows = _load_target_ground_truth(config)
    review_rows = _load_tsv(config.alignment_run / "alignment_review.tsv")
    cell_rows = _load_tsv(config.alignment_run / "alignment_cells.tsv")

    review_in_mz = _filter_review_by_mz(review_rows, config)
    review_index = {row["feature_id"]: row for row in review_in_mz}
    cells_by_sample = _cells_by_sample_in_review_range(cell_rows, review_index)
    comparison = [
        _classify_sample(target, cells_by_sample.get(target.sample_stem, ()), config)
        for target in target_rows
    ]

    config.output_dir.mkdir(parents=True, exist_ok=True)
    _write_dict_csv(config.output_dir / "gt_target.csv", target_rows)
    _write_dict_csv(config.output_dir / "comparison.csv", comparison)
    _write_report(
        config.output_dir / "failure_mode_report.md",
        comparison,
        review_in_mz,
        config,
    )
    _write_svg(
        config.output_dir / "failure_mode_chart.svg",
        comparison,
        config,
    )
    print(f"Wrote {config.output_dir / 'gt_target.csv'}")
    print(f"Wrote {config.output_dir / 'comparison.csv'}")
    print(f"Wrote {config.output_dir / 'failure_mode_report.md'}")
    print(f"Wrote {config.output_dir / 'failure_mode_chart.svg'}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> AuditConfig:
    parser = argparse.ArgumentParser(
        description="Audit untargeted alignment against targeted workbook GT.",
    )
    parser.add_argument("--target-workbook", type=Path, required=True)
    parser.add_argument("--alignment-run", type=Path, required=True)
    parser.add_argument("--target-label", required=True)
    parser.add_argument("--istd-label", required=True)
    parser.add_argument("--target-mz", type=float, required=True)
    parser.add_argument("--ppm", type=float, required=True)
    parser.add_argument("--pass-rt-sec", type=float, required=True)
    parser.add_argument("--drift-rt-sec", type=float, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    return AuditConfig(
        target_workbook=args.target_workbook,
        alignment_run=args.alignment_run,
        target_label=args.target_label,
        istd_label=args.istd_label,
        target_mz=args.target_mz,
        ppm=args.ppm,
        pass_rt_sec=args.pass_rt_sec,
        drift_rt_sec=args.drift_rt_sec,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
