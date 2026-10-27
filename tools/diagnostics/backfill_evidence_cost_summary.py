from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from xic_extractor.diagnostics.backfill_evidence.tsv import (
    iter_skipped_evidence_tsv,
)
from xic_extractor.tabular_io import write_tsv


@dataclass(frozen=True)
class BackfillEvidenceCostSummaryResult:
    status: str
    correctness_status: str
    operations_status: str
    operations_status_reason: str
    outcome_status: str
    outcome_detail: str
    metrics: dict[str, float | int | str]
    rows: tuple[dict[str, str], ...]


def run_backfill_evidence_cost_summary(
    *,
    baseline_timing_json: Path,
    optimized_timing_json: Path,
    skipped_evidence_ledger_tsv: Path,
    output_dir: Path | None = None,
    baseline_economics_json: Path | None = None,
    optimized_economics_json: Path | None = None,
    baseline_owner_backfill_economics_json: Path | None = None,
    optimized_owner_backfill_economics_json: Path | None = None,
    correctness_status: str = "not_evaluated",
) -> BackfillEvidenceCostSummaryResult:
    baseline_economics_json = (
        baseline_owner_backfill_economics_json or baseline_economics_json
    )
    optimized_economics_json = (
        optimized_owner_backfill_economics_json or optimized_economics_json
    )
    if baseline_economics_json is None or optimized_economics_json is None:
        raise ValueError(
            "baseline and optimized owner-backfill economics JSON are required"
        )

    baseline_timing = _timing_metrics(baseline_timing_json)
    optimized_timing = _timing_metrics(optimized_timing_json)
    baseline_economics = _economics_totals(baseline_economics_json)
    optimized_economics = _economics_totals(optimized_economics_json)
    ledger_metrics = _ledger_metrics(skipped_evidence_ledger_tsv)

    canonical_correctness_status = _canonical_correctness_status(correctness_status)
    metrics: dict[str, float | int | str] = {
        **_prefixed("baseline", baseline_timing),
        **_prefixed("optimized", optimized_timing),
        **_economics_metrics(baseline_economics, optimized_economics, ledger_metrics),
        **ledger_metrics,
        "backfill_scope": "production-equivalent",
    }
    metrics.update(_delta_metrics(baseline_timing, optimized_timing))
    operations_status, reason = _operations_status(metrics)
    outcome_status, outcome_detail = _outcome_status(
        canonical_correctness_status,
        operations_status,
    )
    status = outcome_status
    metrics["correctness_status"] = canonical_correctness_status
    metrics["operations_status"] = operations_status
    metrics["operations_status_reason"] = reason
    metrics["outcome_status"] = outcome_status
    metrics["outcome_detail"] = outcome_detail
    metrics["status"] = status
    rows = tuple(
        {"metric": key, "value": _format_value(value)}
        for key, value in sorted(metrics.items())
    )
    result = BackfillEvidenceCostSummaryResult(
        status=status,
        correctness_status=canonical_correctness_status,
        operations_status=operations_status,
        operations_status_reason=reason,
        outcome_status=outcome_status,
        outcome_detail=outcome_detail,
        metrics=metrics,
        rows=rows,
    )
    if output_dir is not None:
        write_outputs(
            output_dir,
            result,
            baseline_economics_json=baseline_economics_json,
            optimized_economics_json=optimized_economics_json,
            skipped_evidence_ledger_tsv=skipped_evidence_ledger_tsv,
        )
    return result


def write_outputs(
    output_dir: Path,
    result: BackfillEvidenceCostSummaryResult,
    *,
    baseline_economics_json: Path | None = None,
    optimized_economics_json: Path | None = None,
    skipped_evidence_ledger_tsv: Path | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "backfill_evidence_cost_summary.json").write_text(
        json.dumps(asdict(result), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_tsv(
        output_dir / "backfill_evidence_cost_summary.tsv",
        result.rows,
        ("metric", "value"),
    )
    _write_markdown(output_dir / "backfill_evidence_cost_summary.md", result)
    if baseline_economics_json is not None:
        shutil.copyfile(
            baseline_economics_json,
            output_dir / "owner_backfill_economics_full_audit.json",
        )
    if optimized_economics_json is not None:
        shutil.copyfile(
            optimized_economics_json,
            output_dir / "owner_backfill_economics_production_equivalent.json",
        )
    if skipped_evidence_ledger_tsv is not None:
        shutil.copyfile(
            skipped_evidence_ledger_tsv,
            output_dir / "skipped_evidence_ledger.tsv",
        )
        (output_dir / "skipped_evidence_summary.json").write_text(
            json.dumps(
                {
                    key: value
                    for key, value in result.metrics.items()
                    if key.startswith("skipped_")
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def _write_markdown(path: Path, result: BackfillEvidenceCostSummaryResult) -> None:
    lines = [
        "# Backfill Evidence Cost Summary",
        "",
        f"Status: {result.status}",
        f"Correctness status: {result.correctness_status}",
        f"Operations status: {result.operations_status}",
        f"Operations status reason: {result.operations_status_reason}",
        f"Outcome status: {result.outcome_status}",
        f"Outcome detail: {result.outcome_detail}",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    lines.extend(f"| {row['metric']} | {row['value']} |" for row in result.rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _timing_metrics(path: Path) -> dict[str, float | int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise ValueError(f"{path}: records must be a list")
    owner_backfill_elapsed = 0.0
    total_stage_elapsed = 0.0
    owner_extract_xic_count = 0
    owner_raw_chromatogram_call_count = 0
    for record in records:
        stage = record.get("stage")
        elapsed = _float(record.get("elapsed_sec", 0.0))
        if stage == "alignment.owner_backfill":
            owner_backfill_elapsed += elapsed
        if not str(stage or "").endswith(".extract_xic"):
            total_stage_elapsed += elapsed
        if stage == "alignment.owner_backfill.extract_xic":
            metrics = record.get("metrics", {})
            owner_extract_xic_count += _int(metrics.get("extract_xic_count", 0))
            owner_raw_chromatogram_call_count += _int(
                metrics.get("raw_chromatogram_call_count", 0),
            )
    return {
        "owner_backfill_elapsed_sec": owner_backfill_elapsed,
        "total_stage_elapsed_sec": total_stage_elapsed,
        "owner_backfill_extract_xic_count": owner_extract_xic_count,
        "owner_backfill_raw_chromatogram_call_count": (
            owner_raw_chromatogram_call_count
        ),
    }


def _ledger_metrics(path: Path) -> dict[str, int]:
    row_count = 0
    skipped_requests = 0
    feature_ids: set[str] = set()
    for record in iter_skipped_evidence_tsv(path):
        row_count += 1
        skipped_requests += record.raw_xic_requests_skipped
        feature_ids.add(record.feature_id)
    return {
        "skipped_evidence_row_count": row_count,
        "raw_xic_requests_skipped": skipped_requests,
        "skipped_raw_xic_requests": skipped_requests,
        "skipped_feature_count": len(feature_ids),
    }


def _economics_totals(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    totals = payload.get("totals", {})
    if not isinstance(totals, Mapping):
        raise ValueError(f"{path}: totals must be an object")
    return totals


def _economics_metrics(
    baseline: Mapping[str, Any],
    optimized: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> dict[str, float | int]:
    baseline_targets = _int(baseline.get("request_target_count", 0))
    reported_optimized_targets = _int(optimized.get("request_target_count", 0))
    baseline_extracts = _int(baseline.get("request_extract_count_estimate", 0))
    reported_optimized_extracts = _int(
        optimized.get("request_extract_count_estimate", 0)
    )
    skipped_requests = _int(ledger.get("raw_xic_requests_skipped", 0))
    target_saved = max(
        baseline_targets - reported_optimized_targets,
        skipped_requests,
    )
    extract_saved = max(
        baseline_extracts - reported_optimized_extracts,
        skipped_requests,
    )
    effective_optimized_targets = max(0, baseline_targets - target_saved)
    effective_optimized_extracts = max(0, baseline_extracts - extract_saved)
    return {
        "baseline_request_target_count": baseline_targets,
        "optimized_request_target_count": effective_optimized_targets,
        "optimized_reported_request_target_count": reported_optimized_targets,
        "request_target_count_saved": target_saved,
        "request_target_reduction_pct": _pct_saved(
            baseline_targets,
            effective_optimized_targets,
        ),
        "baseline_request_extract_count_estimate": baseline_extracts,
        "optimized_request_extract_count_estimate": effective_optimized_extracts,
        "optimized_reported_request_extract_count_estimate": (
            reported_optimized_extracts
        ),
        "request_extract_count_estimate_saved": extract_saved,
    }


def _delta_metrics(
    baseline: Mapping[str, float | int],
    optimized: Mapping[str, float | int],
) -> dict[str, float | int]:
    baseline_owner = float(baseline.get("owner_backfill_elapsed_sec", 0.0))
    optimized_owner = float(optimized.get("owner_backfill_elapsed_sec", 0.0))
    baseline_total = float(baseline.get("total_stage_elapsed_sec", 0.0))
    optimized_total = float(optimized.get("total_stage_elapsed_sec", 0.0))
    baseline_xic = int(baseline.get("owner_backfill_extract_xic_count", 0))
    optimized_xic = int(optimized.get("owner_backfill_extract_xic_count", 0))
    baseline_raw_calls = int(
        baseline.get("owner_backfill_raw_chromatogram_call_count", 0)
    )
    optimized_raw_calls = int(
        optimized.get("owner_backfill_raw_chromatogram_call_count", 0)
    )
    return {
        "owner_backfill_elapsed_sec": optimized_owner,
        "owner_backfill_elapsed_saved_sec": baseline_owner - optimized_owner,
        "owner_backfill_elapsed_ratio": (
            optimized_owner / baseline_owner if baseline_owner > 0 else 0.0
        ),
        "owner_backfill_speedup_ratio": (
            baseline_owner / optimized_owner if optimized_owner > 0 else 0.0
        ),
        "whole_alignment_wall_clock_improvement_pct": _pct_saved(
            baseline_total,
            optimized_total,
        ),
        "owner_backfill_extract_xic_saved": baseline_xic - optimized_xic,
        "owner_backfill_raw_chromatogram_call_saved": (
            baseline_raw_calls - optimized_raw_calls
        ),
    }


def _operations_status(
    metrics: Mapping[str, float | int | str],
) -> tuple[str, str]:
    positive_metrics = _positive_resource_improvement_metrics(metrics)
    if positive_metrics:
        return "PASS", "positive_resource_improvement:" + ",".join(positive_metrics)
    return "inconclusive", "no_positive_resource_improvement"


def _outcome_status(
    correctness_status: str,
    operations_status: str,
) -> tuple[str, str]:
    if correctness_status in {"FAIL", "fail", "diagnostic_only"}:
        if operations_status != "PASS":
            return "diagnostic_only", "correctness_blocker;perf_stall"
        return "diagnostic_only", "correctness_blocker"
    if operations_status != "PASS":
        return "inconclusive", "perf_stall"
    if correctness_status == "PASS":
        return "production_candidate", ""
    return "inconclusive", "correctness_not_evaluated"


def _canonical_correctness_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == "pass":
        return "PASS"
    if normalized == "fail":
        return "FAIL"
    if normalized in {"not_evaluated", "diagnostic_only"}:
        return normalized
    raise ValueError(
        "correctness_status must be PASS, FAIL, not_evaluated, or diagnostic_only"
    )


def _positive_resource_improvement_metrics(
    metrics: Mapping[str, float | int | str],
) -> tuple[str, ...]:
    checks = (
        ("raw_xic_requests_skipped", int(metrics.get("raw_xic_requests_skipped", 0))),
        (
            "request_target_count_saved",
            int(metrics.get("request_target_count_saved", 0)),
        ),
        (
            "request_extract_count_estimate_saved",
            int(metrics.get("request_extract_count_estimate_saved", 0)),
        ),
        (
            "owner_backfill_extract_xic_saved",
            int(metrics.get("owner_backfill_extract_xic_saved", 0)),
        ),
        (
            "owner_backfill_raw_chromatogram_call_saved",
            int(metrics.get("owner_backfill_raw_chromatogram_call_saved", 0)),
        ),
        (
            "owner_backfill_elapsed_saved_sec",
            float(metrics.get("owner_backfill_elapsed_saved_sec", 0.0)),
        ),
        (
            "whole_alignment_wall_clock_improvement_pct",
            float(metrics.get("whole_alignment_wall_clock_improvement_pct", 0.0)),
        ),
    )
    return tuple(name for name, value in checks if value > 0)


def _prefixed(
    prefix: str,
    values: Mapping[str, float | int],
) -> dict[str, float | int]:
    return {f"{prefix}_{key}": value for key, value in values.items()}


def _pct_saved(baseline: float | int, optimized: float | int) -> float:
    baseline_value = float(baseline)
    if baseline_value <= 0:
        return 0.0
    return ((baseline_value - float(optimized)) / baseline_value) * 100.0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _format_value(value: float | int | str) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _error_result(error: Exception) -> BackfillEvidenceCostSummaryResult:
    metrics: dict[str, float | int | str] = {
        "status": "inconclusive",
        "correctness_status": "not_evaluated",
        "operations_status": "fail",
        "operations_status_reason": f"{type(error).__name__}: {error}",
        "outcome_status": "inconclusive",
        "outcome_detail": "tool_error",
    }
    rows = tuple(
        {"metric": key, "value": _format_value(value)}
        for key, value in sorted(metrics.items())
    )
    return BackfillEvidenceCostSummaryResult(
        status="inconclusive",
        correctness_status="not_evaluated",
        operations_status="fail",
        operations_status_reason=str(metrics["operations_status_reason"]),
        outcome_status="inconclusive",
        outcome_detail="tool_error",
        metrics=metrics,
        rows=rows,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize backfill evidence cost savings."
    )
    parser.add_argument("--baseline-timing-json", type=Path, required=True)
    parser.add_argument("--optimized-timing-json", type=Path, required=True)
    parser.add_argument("--skipped-evidence-ledger-tsv", type=Path, required=True)
    parser.add_argument("--baseline-economics-json", type=Path)
    parser.add_argument("--optimized-economics-json", type=Path)
    parser.add_argument("--baseline-owner-backfill-economics-json", type=Path)
    parser.add_argument("--optimized-owner-backfill-economics-json", type=Path)
    parser.add_argument(
        "--correctness-status",
        default="not_evaluated",
        choices=("PASS", "FAIL", "not_evaluated", "diagnostic_only"),
        help=(
            "Result-equivalence gate status to combine with operations status. "
            "Use PASS only after parity/correctness validation has passed."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_backfill_evidence_cost_summary(
            baseline_timing_json=args.baseline_timing_json,
            optimized_timing_json=args.optimized_timing_json,
            skipped_evidence_ledger_tsv=args.skipped_evidence_ledger_tsv,
            output_dir=args.output_dir,
            baseline_economics_json=args.baseline_economics_json,
            optimized_economics_json=args.optimized_economics_json,
            baseline_owner_backfill_economics_json=(
                args.baseline_owner_backfill_economics_json
            ),
            optimized_owner_backfill_economics_json=(
                args.optimized_owner_backfill_economics_json
            ),
            correctness_status=args.correctness_status,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = _error_result(exc)
        write_outputs(args.output_dir, result)
    return _exit_code(result)


def _exit_code(result: BackfillEvidenceCostSummaryResult) -> int:
    if result.outcome_status == "diagnostic_only":
        return 1
    return 0 if result.operations_status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
