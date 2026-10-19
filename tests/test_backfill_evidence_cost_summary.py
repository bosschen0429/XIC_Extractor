import csv
import json
from pathlib import Path

import pytest

from tests.skipped_evidence_ledger_fixture import write_skipped_evidence_ledger_fixture
from tools.diagnostics import backfill_evidence_cost_summary
from tools.diagnostics.backfill_evidence_cost_summary import (
    run_backfill_evidence_cost_summary,
)
from xic_extractor.alignment.backfill.evidence_contract import (
    PREDICATE_VERSION,
    SkippedEvidenceRecord,
)


def test_backfill_evidence_cost_summary_passes_when_backfill_cost_drops(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline_timing.json"
    optimized = tmp_path / "optimized_timing.json"
    baseline_economics = tmp_path / "baseline_economics.json"
    optimized_economics = tmp_path / "optimized_economics.json"
    ledger = tmp_path / "skipped_evidence_ledger.tsv"
    _timing(
        baseline,
        owner_backfill_elapsed=100.0,
        owner_extract_xic_count=1000,
        raw_chromatogram_call_count=1000,
    )
    _timing(
        optimized,
        owner_backfill_elapsed=40.0,
        owner_extract_xic_count=300,
        raw_chromatogram_call_count=300,
    )
    _write_ledger(
        ledger,
        [("FAM001", "S2", 1), ("FAM001", "S3", 1)],
    )
    _economics(baseline_economics, request_target_count=100)
    _economics(optimized_economics, request_target_count=40)

    result = run_backfill_evidence_cost_summary(
        baseline_timing_json=baseline,
        optimized_timing_json=optimized,
        baseline_owner_backfill_economics_json=baseline_economics,
        optimized_owner_backfill_economics_json=optimized_economics,
        skipped_evidence_ledger_tsv=ledger,
        output_dir=tmp_path / "summary",
    )

    assert result.status == "inconclusive"
    assert result.correctness_status == "not_evaluated"
    assert result.operations_status == "PASS"
    assert result.outcome_status == "inconclusive"
    assert result.outcome_detail == "correctness_not_evaluated"
    assert result.metrics["owner_backfill_elapsed_saved_sec"] == 60.0
    assert result.metrics["request_target_reduction_pct"] == 60.0
    assert result.metrics["owner_backfill_speedup_ratio"] == 2.5
    assert result.metrics["raw_xic_requests_skipped"] == 2
    assert (tmp_path / "summary" / "backfill_evidence_cost_summary.json").exists()
    with (tmp_path / "summary" / "backfill_evidence_cost_summary.tsv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        tsv_rows = {row["metric"]: row["value"] for row in reader}
        assert reader.fieldnames == ["metric", "value"]
    assert tsv_rows["owner_backfill_elapsed_saved_sec"] == "60"
    assert tsv_rows["request_target_reduction_pct"] == "60"
    assert tsv_rows["status"] == "inconclusive"
    assert (tmp_path / "summary" / "backfill_evidence_cost_summary.md").exists()
    assert (tmp_path / "summary" / "owner_backfill_economics_full_audit.json").exists()
    assert (
        tmp_path / "summary" / "owner_backfill_economics_production_equivalent.json"
    ).exists()
    assert (tmp_path / "summary" / "skipped_evidence_ledger.tsv").exists()
    assert (tmp_path / "summary" / "skipped_evidence_summary.json").exists()


def test_backfill_evidence_cost_summary_passes_on_small_positive_resource_improvement(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline_timing.json"
    optimized = tmp_path / "optimized_timing.json"
    baseline_economics = tmp_path / "baseline_economics.json"
    optimized_economics = tmp_path / "optimized_economics.json"
    ledger = tmp_path / "skipped_evidence_ledger.tsv"
    _timing(baseline, owner_backfill_elapsed=100.0, owner_extract_xic_count=100)
    _timing(optimized, owner_backfill_elapsed=80.0, owner_extract_xic_count=80)
    _write_ledger(ledger, [("FAM001", "S2", 1)])
    _economics(baseline_economics, request_target_count=100)
    _economics(optimized_economics, request_target_count=80)

    result = run_backfill_evidence_cost_summary(
        baseline_timing_json=baseline,
        optimized_timing_json=optimized,
        baseline_owner_backfill_economics_json=baseline_economics,
        optimized_owner_backfill_economics_json=optimized_economics,
        skipped_evidence_ledger_tsv=ledger,
    )

    assert result.status == "inconclusive"
    assert result.operations_status == "PASS"
    assert "positive_resource_improvement" in result.operations_status_reason
    assert "request_target_count_saved" in result.operations_status_reason


def test_backfill_evidence_cost_summary_uses_ledger_when_economics_estimator_is_static(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline_timing.json"
    optimized = tmp_path / "optimized_timing.json"
    baseline_economics = tmp_path / "baseline_economics.json"
    optimized_economics = tmp_path / "optimized_economics.json"
    ledger = tmp_path / "skipped_evidence_ledger.tsv"
    _timing(baseline, owner_backfill_elapsed=100.0, owner_extract_xic_count=100)
    _timing(optimized, owner_backfill_elapsed=40.0, owner_extract_xic_count=40)
    _economics(baseline_economics, request_target_count=100)
    _economics(optimized_economics, request_target_count=100)
    _write_ledger(
        ledger,
        [(f"FAM{i:03d}", "S2", 1) for i in range(60)],
    )

    result = run_backfill_evidence_cost_summary(
        baseline_timing_json=baseline,
        optimized_timing_json=optimized,
        baseline_owner_backfill_economics_json=baseline_economics,
        optimized_owner_backfill_economics_json=optimized_economics,
        skipped_evidence_ledger_tsv=ledger,
    )

    assert result.status == "inconclusive"
    assert result.metrics["optimized_reported_request_target_count"] == 100
    assert result.metrics["optimized_request_target_count"] == 40
    assert result.metrics["request_target_count_saved"] == 60
    assert result.metrics["request_target_reduction_pct"] == 60.0


def test_backfill_evidence_cost_summary_marks_inconclusive_when_nothing_improves(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline_timing.json"
    optimized = tmp_path / "optimized_timing.json"
    baseline_economics = tmp_path / "baseline_economics.json"
    optimized_economics = tmp_path / "optimized_economics.json"
    ledger = tmp_path / "skipped_evidence_ledger.tsv"
    _timing(baseline, owner_backfill_elapsed=100.0, owner_extract_xic_count=100)
    _timing(optimized, owner_backfill_elapsed=100.0, owner_extract_xic_count=100)
    _economics(baseline_economics, request_target_count=100)
    _economics(optimized_economics, request_target_count=100)
    _write_ledger(ledger, [])

    result = run_backfill_evidence_cost_summary(
        baseline_timing_json=baseline,
        optimized_timing_json=optimized,
        baseline_owner_backfill_economics_json=baseline_economics,
        optimized_owner_backfill_economics_json=optimized_economics,
        skipped_evidence_ledger_tsv=ledger,
    )

    assert result.status == "inconclusive"
    assert result.operations_status == "inconclusive"
    assert result.outcome_detail == "perf_stall"
    assert result.operations_status_reason == "no_positive_resource_improvement"


def test_backfill_evidence_cost_summary_rejects_malformed_ledger(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline_timing.json"
    optimized = tmp_path / "optimized_timing.json"
    baseline_economics = tmp_path / "baseline_economics.json"
    optimized_economics = tmp_path / "optimized_economics.json"
    ledger = tmp_path / "skipped_evidence_ledger.tsv"
    _timing(baseline, owner_backfill_elapsed=100.0, owner_extract_xic_count=100)
    _timing(optimized, owner_backfill_elapsed=40.0, owner_extract_xic_count=40)
    _economics(baseline_economics, request_target_count=100)
    _economics(optimized_economics, request_target_count=40)
    ledger.write_text("feature_family_id\tsample_stem\nFAM001\tS2\n", encoding="utf-8")

    try:
        run_backfill_evidence_cost_summary(
            baseline_timing_json=baseline,
            optimized_timing_json=optimized,
            baseline_owner_backfill_economics_json=baseline_economics,
            optimized_owner_backfill_economics_json=optimized_economics,
            skipped_evidence_ledger_tsv=ledger,
        )
    except ValueError as exc:
        assert "raw_xic_requests_skipped" in str(exc)
    else:
        raise AssertionError("expected malformed ledger to fail")


def test_backfill_evidence_cost_summary_cli_returns_zero_for_positive_improvement(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline_timing.json"
    optimized = tmp_path / "optimized_timing.json"
    baseline_economics = tmp_path / "baseline_economics.json"
    optimized_economics = tmp_path / "optimized_economics.json"
    ledger = tmp_path / "skipped_evidence_ledger.tsv"
    output_dir = tmp_path / "summary"
    _timing(baseline, owner_backfill_elapsed=100.0, owner_extract_xic_count=100)
    _timing(optimized, owner_backfill_elapsed=80.0, owner_extract_xic_count=80)
    _economics(baseline_economics, request_target_count=100)
    _economics(optimized_economics, request_target_count=80)
    _write_ledger(ledger, [("FAM001", "S2", 1)])

    code = backfill_evidence_cost_summary.main(
        [
            "--baseline-timing-json",
            str(baseline),
            "--optimized-timing-json",
            str(optimized),
            "--baseline-owner-backfill-economics-json",
            str(baseline_economics),
            "--optimized-owner-backfill-economics-json",
            str(optimized_economics),
            "--skipped-evidence-ledger-tsv",
            str(ledger),
            "--output-dir",
            str(output_dir),
        ]
    )

    payload = json.loads(
        (output_dir / "backfill_evidence_cost_summary.json").read_text(encoding="utf-8")
    )
    assert code == 0
    assert payload["operations_status"] == "PASS"
    assert payload["outcome_status"] == "inconclusive"


def test_backfill_evidence_cost_summary_cli_returns_nonzero_for_correctness_blocker(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline_timing.json"
    optimized = tmp_path / "optimized_timing.json"
    baseline_economics = tmp_path / "baseline_economics.json"
    optimized_economics = tmp_path / "optimized_economics.json"
    ledger = tmp_path / "skipped_evidence_ledger.tsv"
    output_dir = tmp_path / "summary"
    _timing(baseline, owner_backfill_elapsed=100.0, owner_extract_xic_count=100)
    _timing(optimized, owner_backfill_elapsed=80.0, owner_extract_xic_count=80)
    _economics(baseline_economics, request_target_count=100)
    _economics(optimized_economics, request_target_count=80)
    _write_ledger(ledger, [("FAM001", "S2", 1)])

    code = backfill_evidence_cost_summary.main(
        [
            "--baseline-timing-json",
            str(baseline),
            "--optimized-timing-json",
            str(optimized),
            "--baseline-owner-backfill-economics-json",
            str(baseline_economics),
            "--optimized-owner-backfill-economics-json",
            str(optimized_economics),
            "--skipped-evidence-ledger-tsv",
            str(ledger),
            "--correctness-status",
            "FAIL",
            "--output-dir",
            str(output_dir),
        ]
    )

    payload = json.loads(
        (output_dir / "backfill_evidence_cost_summary.json").read_text(encoding="utf-8")
    )
    assert code == 1
    assert payload["operations_status"] == "PASS"
    assert payload["outcome_status"] == "diagnostic_only"


def test_backfill_evidence_cost_summary_combines_correctness_and_operations(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline_timing.json"
    optimized = tmp_path / "optimized_timing.json"
    baseline_economics = tmp_path / "baseline_economics.json"
    optimized_economics = tmp_path / "optimized_economics.json"
    ledger = tmp_path / "skipped_evidence_ledger.tsv"
    _timing(baseline, owner_backfill_elapsed=100.0, owner_extract_xic_count=100)
    _timing(optimized, owner_backfill_elapsed=80.0, owner_extract_xic_count=80)
    _economics(baseline_economics, request_target_count=100)
    _economics(optimized_economics, request_target_count=80)
    _write_ledger(ledger, [("FAM001", "S2", 1)])

    result = run_backfill_evidence_cost_summary(
        baseline_timing_json=baseline,
        optimized_timing_json=optimized,
        baseline_owner_backfill_economics_json=baseline_economics,
        optimized_owner_backfill_economics_json=optimized_economics,
        skipped_evidence_ledger_tsv=ledger,
        correctness_status="PASS",
    )

    assert result.operations_status == "PASS"
    assert result.correctness_status == "PASS"
    assert result.status == "production_candidate"
    assert result.outcome_status == "production_candidate"


def test_backfill_evidence_cost_summary_correctness_blocker_overrides_ops_pass(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline_timing.json"
    optimized = tmp_path / "optimized_timing.json"
    baseline_economics = tmp_path / "baseline_economics.json"
    optimized_economics = tmp_path / "optimized_economics.json"
    ledger = tmp_path / "skipped_evidence_ledger.tsv"
    _timing(baseline, owner_backfill_elapsed=100.0, owner_extract_xic_count=100)
    _timing(optimized, owner_backfill_elapsed=80.0, owner_extract_xic_count=80)
    _economics(baseline_economics, request_target_count=100)
    _economics(optimized_economics, request_target_count=80)
    _write_ledger(ledger, [("FAM001", "S2", 1)])

    result = run_backfill_evidence_cost_summary(
        baseline_timing_json=baseline,
        optimized_timing_json=optimized,
        baseline_owner_backfill_economics_json=baseline_economics,
        optimized_owner_backfill_economics_json=optimized_economics,
        skipped_evidence_ledger_tsv=ledger,
        correctness_status="FAIL",
    )

    assert result.operations_status == "PASS"
    assert result.status == "diagnostic_only"
    assert result.outcome_status == "diagnostic_only"
    assert result.outcome_detail == "correctness_blocker"


def test_backfill_evidence_cost_summary_keeps_correctness_blocker_on_perf_stall(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline_timing.json"
    optimized = tmp_path / "optimized_timing.json"
    baseline_economics = tmp_path / "baseline_economics.json"
    optimized_economics = tmp_path / "optimized_economics.json"
    ledger = tmp_path / "skipped_evidence_ledger.tsv"
    _timing(baseline, owner_backfill_elapsed=100.0, owner_extract_xic_count=100)
    _timing(optimized, owner_backfill_elapsed=100.0, owner_extract_xic_count=100)
    _economics(baseline_economics, request_target_count=100)
    _economics(optimized_economics, request_target_count=100)
    _write_ledger(ledger, [])

    result = run_backfill_evidence_cost_summary(
        baseline_timing_json=baseline,
        optimized_timing_json=optimized,
        baseline_owner_backfill_economics_json=baseline_economics,
        optimized_owner_backfill_economics_json=optimized_economics,
        skipped_evidence_ledger_tsv=ledger,
        correctness_status="FAIL",
    )

    assert result.operations_status == "inconclusive"
    assert result.status == "diagnostic_only"
    assert result.outcome_detail == "correctness_blocker;perf_stall"


def test_backfill_evidence_cost_summary_cli_error_uses_inconclusive_outcome(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "summary"

    code = backfill_evidence_cost_summary.main(
        [
            "--baseline-timing-json",
            str(tmp_path / "missing-baseline.json"),
            "--optimized-timing-json",
            str(tmp_path / "missing-optimized.json"),
            "--baseline-owner-backfill-economics-json",
            str(tmp_path / "missing-baseline-economics.json"),
            "--optimized-owner-backfill-economics-json",
            str(tmp_path / "missing-optimized-economics.json"),
            "--skipped-evidence-ledger-tsv",
            str(tmp_path / "missing-ledger.tsv"),
            "--output-dir",
            str(output_dir),
        ]
    )

    payload = json.loads(
        (output_dir / "backfill_evidence_cost_summary.json").read_text(encoding="utf-8")
    )
    assert code == 1
    assert payload["status"] == "inconclusive"
    assert payload["outcome_status"] == "inconclusive"
    assert payload["outcome_detail"] == "tool_error"
    assert payload["operations_status"] == "fail"


def test_backfill_evidence_cost_summary_cli_rejects_invalid_correctness_status(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        backfill_evidence_cost_summary.main(
            [
                "--baseline-timing-json",
                str(tmp_path / "baseline.json"),
                "--optimized-timing-json",
                str(tmp_path / "optimized.json"),
                "--baseline-owner-backfill-economics-json",
                str(tmp_path / "baseline-economics.json"),
                "--optimized-owner-backfill-economics-json",
                str(tmp_path / "optimized-economics.json"),
                "--skipped-evidence-ledger-tsv",
                str(tmp_path / "ledger.tsv"),
                "--correctness-status",
                "FAILED",
                "--output-dir",
                str(tmp_path / "summary"),
            ]
        )

    assert exc_info.value.code == 2


def _timing(
    path: Path,
    *,
    owner_backfill_elapsed: float,
    owner_extract_xic_count: int,
    raw_chromatogram_call_count: int = 0,
) -> None:
    payload = {
        "records": [
            {
                "stage": "alignment.owner_backfill",
                "elapsed_sec": owner_backfill_elapsed,
                "metrics": {},
            },
            {
                "stage": "alignment.owner_backfill.extract_xic",
                "elapsed_sec": owner_backfill_elapsed,
                "metrics": {
                    "extract_xic_count": owner_extract_xic_count,
                    "raw_chromatogram_call_count": raw_chromatogram_call_count,
                },
            },
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _economics(path: Path, *, request_target_count: int) -> None:
    payload = {
        "totals": {
            "request_target_count": request_target_count,
            "request_extract_count_estimate": request_target_count,
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_ledger(
    path: Path,
    rows: list[tuple[str, str, int]],
) -> None:
    records = tuple(
        SkippedEvidenceRecord(
            feature_family_id=feature_family_id,
            sample_stem=sample_stem,
            family_center_mz=200.0 + index,
            family_center_rt=5.0,
            rt_window_start=2.0,
            rt_window_end=8.0,
            pre_backfill_category="single_detected_no_consolidation_candidate",
            skipped_stage="owner_backfill",
            skip_reason="single_detected_no_consolidation_candidate",
            backfill_scope="production-equivalent",
            predicate_version=PREDICATE_VERSION,
            raw_xic_requests_skipped=raw_xic_requests_skipped,
            would_emit_in_full_audit=True,
            full_audit_available=True,
            source_artifact="owner_backfill",
        )
        for index, (
            feature_family_id,
            sample_stem,
            raw_xic_requests_skipped,
        ) in enumerate(rows)
    )
    write_skipped_evidence_ledger_fixture(path, records)
