"""Strict targeted ISTD benchmark gate for untargeted alignment outputs."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.diagnostics.targeted_istd_benchmark_loaders import (
    read_alignment_cells,
    read_alignment_matrix,
    read_alignment_review,
    read_target_definitions,
    read_targeted_points,
    read_targeted_reliability_points,
)
from tools.diagnostics.targeted_istd_benchmark_matching import (
    _match_target_to_alignment,
)
from tools.diagnostics.targeted_istd_benchmark_models import (
    ISOTOPE_SHIFT_DA,
    BenchmarkOutputs,
    BenchmarkSummary,
    BenchmarkThresholds,
    CandidateMatch,
)
from tools.diagnostics.targeted_istd_benchmark_summary import (
    _benchmark_points,
    _summarize_target,
)
from tools.diagnostics.targeted_istd_benchmark_writers import write_benchmark_outputs

__all__ = (
    "BenchmarkThresholds",
    "ISOTOPE_SHIFT_DA",
    "main",
    "run_targeted_istd_benchmark",
)


def run_targeted_istd_benchmark(
    *,
    targeted_workbook: Path,
    alignment_dir: Path,
    output_dir: Path,
    thresholds: BenchmarkThresholds = BenchmarkThresholds(),
    targeted_reliability_json: Path | None = None,
    strict_targeted_reliability: bool = False,
) -> tuple[BenchmarkOutputs, tuple[BenchmarkSummary, ...]]:
    if strict_targeted_reliability and targeted_reliability_json is None:
        raise ValueError(
            "--strict-targeted-reliability requires --targeted-reliability-json"
        )
    targets = read_target_definitions(targeted_workbook)
    targeted_points = read_targeted_points(targeted_workbook)
    targeted_reliability = (
        read_targeted_reliability_points(targeted_reliability_json)
        if targeted_reliability_json is not None
        else {}
    )
    review_rows = read_alignment_review(alignment_dir / "alignment_review.tsv")
    matrix = read_alignment_matrix(alignment_dir / "alignment_matrix.tsv")
    cells = read_alignment_cells(alignment_dir / "alignment_cells.tsv")

    summaries: list[BenchmarkSummary] = []
    matches: list[CandidateMatch] = []
    for target in targets:
        target_points = tuple(
            point
            for point in targeted_points.get(target.label, ())
            if point.sample_stem in matrix.sample_stems
        )
        matching_points = _benchmark_points(
            target_points,
            reliability=targeted_reliability,
            strict_targeted_reliability=strict_targeted_reliability,
        )
        target_matches = _match_target_to_alignment(
            target,
            review_rows,
            target_points=matching_points,
            thresholds=thresholds,
        )
        matches.extend(target_matches)
        summaries.append(
            _summarize_target(
                target,
                target_points,
                target_matches,
                matrix=matrix.areas_by_family,
                cells=cells,
                thresholds=thresholds,
                reliability=targeted_reliability,
                strict_targeted_reliability=strict_targeted_reliability,
            ),
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = BenchmarkOutputs(
        summary_tsv=output_dir / "targeted_istd_benchmark_summary.tsv",
        matches_tsv=output_dir / "targeted_istd_benchmark_matches.tsv",
        json_path=output_dir / "targeted_istd_benchmark.json",
        markdown_path=output_dir / "targeted_istd_benchmark.md",
    )
    write_benchmark_outputs(
        outputs,
        summaries=summaries,
        matches=matches,
        thresholds=thresholds,
    )
    return outputs, tuple(summaries)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    thresholds = BenchmarkThresholds(
        active_neutral_loss_da=args.active_neutral_loss_da,
        additional_active_neutral_loss_das=tuple(
            args.additional_active_neutral_loss_da
        ),
        active_neutral_loss_tolerance_da=args.active_neutral_loss_tolerance_da,
        default_match_ppm=args.default_match_ppm,
        match_rt_sec=args.match_rt_sec,
        mean_rt_delta_max_min=args.mean_rt_delta_max_min,
        sample_rt_median_abs_delta_max_min=(args.sample_rt_median_abs_delta_max_min),
        sample_rt_p95_abs_delta_max_min=args.sample_rt_p95_abs_delta_max_min,
        log_area_spearman_min=args.log_area_spearman_min,
        log_area_pearson_min=args.log_area_pearson_min,
    )
    try:
        outputs, summaries = run_targeted_istd_benchmark(
            targeted_workbook=args.targeted_workbook.resolve(),
            alignment_dir=args.alignment_dir.resolve(),
            output_dir=args.output_dir.resolve(),
            thresholds=thresholds,
            targeted_reliability_json=(
                args.targeted_reliability_json.resolve()
                if args.targeted_reliability_json is not None
                else None
            ),
            strict_targeted_reliability=args.strict_targeted_reliability,
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Summary TSV: {outputs.summary_tsv}")
    print(f"Matches TSV: {outputs.matches_tsv}")
    print(f"Benchmark JSON: {outputs.json_path}")
    print(f"Benchmark report: {outputs.markdown_path}")
    return 1 if any(row.status == "FAIL" for row in summaries) else 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run strict targeted ISTD benchmark for untargeted alignment.",
    )
    parser.add_argument("--targeted-workbook", type=Path, required=True)
    parser.add_argument("--alignment-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--active-neutral-loss-da", type=float, default=116.0474)
    parser.add_argument(
        "--additional-active-neutral-loss-da",
        action="append",
        default=[],
        type=float,
        help=(
            "Additional selected neutral-loss masses treated as active in a "
            "multi-tag benchmark."
        ),
    )
    parser.add_argument(
        "--active-neutral-loss-tolerance-da",
        type=float,
        default=0.01,
    )
    parser.add_argument("--default-match-ppm", type=float, default=20.0)
    parser.add_argument("--match-rt-sec", type=float, default=60.0)
    parser.add_argument("--mean-rt-delta-max-min", type=float, default=0.15)
    parser.add_argument(
        "--sample-rt-median-abs-delta-max-min",
        type=float,
        default=0.15,
    )
    parser.add_argument(
        "--sample-rt-p95-abs-delta-max-min",
        type=float,
        default=0.30,
    )
    parser.add_argument("--log-area-spearman-min", type=float, default=0.90)
    parser.add_argument("--log-area-pearson-min", type=float, default=0.80)
    parser.add_argument("--targeted-reliability-json", type=Path)
    parser.add_argument(
        "--strict-targeted-reliability",
        action="store_true",
        help=(
            "Use only benchmark_eligible targeted samples for coverage and "
            "RT/area calculations."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
