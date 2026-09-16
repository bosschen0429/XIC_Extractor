from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook

from tools.diagnostics import cross_report_evidence_consistency as report


def test_path_style_cli_help_preserves_public_script_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = (
        repo_root / "tools" / "diagnostics" / "cross_report_evidence_consistency.py"
    )

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--targeted-reliability-rows-tsv" in result.stdout
    assert "--peak-candidates-tsv" in result.stdout
    assert "--output-dir" in result.stdout


def test_module_style_cli_help_preserves_public_module_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.diagnostics.cross_report_evidence_consistency",
            "--help",
        ],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--targeted-reliability-rows-tsv" in result.stdout
    assert "--peak-candidates-tsv" in result.stdout
    assert "--output-dir" in result.stdout


def test_cross_report_consistency_flags_mismatched_evidence(
    tmp_path: Path,
) -> None:
    reliability_rows = tmp_path / "targeted_peak_reliability_rows.tsv"
    peak_candidates = tmp_path / "peak_candidates.tsv"
    workbook = tmp_path / "targeted.xlsx"
    output_dir = tmp_path / "report"
    _write_reliability_rows(
        reliability_rows,
        [
            _reliability("S1", "clean_conflict", "benchmark_eligible"),
            _reliability("S2", "dropout_ok", "targeted_review_positive"),
            _reliability("S3", "dropout_missing", "targeted_review_positive"),
            _reliability("S4", "review_but_dropout", "targeted_review"),
            _reliability("S5", "negative_but_peak", "targeted_negative"),
            _reliability("S6", "missing_candidate", "targeted_review"),
            _reliability("S8", "negative_missing_candidate", "targeted_negative"),
            _reliability("S9", "clean_shape_review", "benchmark_eligible"),
            _reliability(
                "S10",
                "dropout_weak_area",
                "targeted_review",
                risk="plausible_nl_dropout;weak_area_rank",
                area_ratio="0.004",
            ),
        ],
    )
    _write_peak_candidates(
        peak_candidates,
        [
            _candidate(
                "S1",
                "clean_conflict",
                support="local_sn_strong;shape_clean;trace_clean",
                concern="nl_fail;no_ms2",
                ms2_present="FALSE",
                nl_match="FALSE",
            ),
            _candidate(
                "S9",
                "clean_shape_review",
                support="strict_nl_ok;local_sn_strong;trace_clean",
                concern="shape_poor",
                nl_match="TRUE",
            ),
            _candidate(
                "S2",
                "dropout_ok",
                support="local_sn_strong;shape_clean;trace_clean",
                concern="nl_fail",
                nl_match="FALSE",
            ),
            _candidate(
                "S3",
                "dropout_missing",
                support="local_sn_strong",
                concern="nl_fail;shape_poor",
                nl_match="FALSE",
            ),
            _candidate(
                "S4",
                "review_but_dropout",
                support="local_sn_strong;shape_clean;trace_clean",
                concern="nl_fail",
                nl_match="FALSE",
            ),
            _candidate(
                "S5",
                "negative_but_peak",
                support="local_sn_strong;shape_clean;trace_clean;strict_nl_ok",
                concern="",
                nl_match="TRUE",
            ),
            _candidate(
                "S7",
                "missing_reliability",
                support="local_sn_strong;shape_clean;trace_clean",
                concern="",
                nl_match="TRUE",
            ),
            _candidate(
                "S10",
                "dropout_weak_area",
                support="local_sn_strong;shape_clean;trace_clean",
                concern="nl_fail",
                nl_match="FALSE",
            ),
        ],
    )
    _write_targeted_workbook(
        workbook,
        {
            "clean_conflict": 301.1,
            "dropout_ok": 302.2,
            "dropout_missing": 303.3,
            "review_but_dropout": 304.4,
            "negative_but_peak": 305.5,
            "missing_candidate": 306.6,
            "missing_reliability": 307.7,
            "negative_missing_candidate": 308.8,
            "clean_shape_review": 309.9,
            "dropout_weak_area": 310.1,
        },
    )

    outputs, result = report.run_cross_report_evidence_consistency(
        targeted_reliability_rows_tsv=reliability_rows,
        peak_candidates_tsv=peak_candidates,
        output_dir=output_dir,
        targeted_workbook=workbook,
    )

    by_key = {(row.sample_name, row.target_label): row for row in result.rows}
    assert by_key[("S2", "dropout_ok")].consistency_status == "consistent"
    assert by_key[("S1", "clean_conflict")].issue_type == (
        "targeted_clean_candidate_conflict"
    )
    assert by_key[("S1", "clean_conflict")].target_mz == 301.1
    assert by_key[("S9", "clean_shape_review")].consistency_status == "consistent"
    assert by_key[("S10", "dropout_weak_area")].consistency_status == "consistent"
    assert by_key[("S10", "dropout_weak_area")].targeted_area_to_median_ratio == (0.004)
    assert by_key[("S3", "dropout_missing")].issue_type == (
        "review_positive_not_supported_by_candidate"
    )
    assert by_key[("S4", "review_but_dropout")].issue_type == (
        "targeted_review_candidate_suggests_dropout"
    )
    assert by_key[("S5", "negative_but_peak")].issue_type == (
        "targeted_negative_candidate_has_peak"
    )
    assert by_key[("S6", "missing_candidate")].issue_type == (
        "missing_selected_candidate"
    )
    assert by_key[("S8", "negative_missing_candidate")].consistency_status == (
        "consistent"
    )
    assert by_key[("S7", "missing_reliability")].issue_type == (
        "missing_targeted_reliability"
    )

    assert result.summary.rows_checked == 10
    assert result.summary.consistent_count == 4
    assert result.summary.mismatch_count == 6
    assert result.summary.issue_counts == (
        "targeted_clean_candidate_conflict:1;"
        "review_positive_not_supported_by_candidate:1;"
        "targeted_review_candidate_suggests_dropout:1;"
        "targeted_negative_candidate_has_peak:1;"
        "missing_selected_candidate:1;"
        "missing_targeted_reliability:1"
    )

    rows = _read_tsv(outputs.rows_tsv)
    with outputs.summary_tsv.open(encoding="utf-8", newline="") as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "rows_checked",
            "consistent_count",
            "mismatch_count",
            "missing_candidate_count",
            "missing_reliability_count",
            "issue_counts",
        ]
    with outputs.rows_tsv.open(encoding="utf-8", newline="") as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "sample_name",
            "target_label",
            "target_mz",
            "reliability_state",
            "targeted_risk_reasons",
            "resolver_mode",
            "selected_candidate_id",
            "selected_rt_apex_min",
            "selected_raw_score",
            "selected_confidence",
            "targeted_area_to_median_ratio",
            "candidate_support_labels",
            "candidate_concern_labels",
            "candidate_consistency_labels",
            "consistency_status",
            "issue_type",
            "reason",
        ]
    assert rows[0]["target_mz"]
    assert (output_dir / "cross_report_evidence_consistency.md").is_file()
    payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
    assert payload["summary"]["mismatch_count"] == 6


def test_cross_report_consistency_cli_reports_missing_columns(
    tmp_path: Path,
    capsys,
) -> None:
    reliability_rows = tmp_path / "targeted_peak_reliability_rows.tsv"
    peak_candidates = tmp_path / "peak_candidates.tsv"
    reliability_rows.write_text("sample_name\ttarget_label\nS1\tT1\n", encoding="utf-8")
    peak_candidates.write_text("sample_name\ttarget_label\nS1\tT1\n", encoding="utf-8")

    code = report.main(
        [
            "--targeted-reliability-rows-tsv",
            str(reliability_rows),
            "--peak-candidates-tsv",
            str(peak_candidates),
            "--output-dir",
            str(tmp_path / "report"),
        ]
    )

    assert code == 2
    assert "missing required columns" in capsys.readouterr().err


def _reliability(
    sample: str,
    target: str,
    state: str,
    *,
    risk: str = "",
    area_ratio: str = "",
) -> dict[str, str]:
    return {
        "sample_name": sample,
        "target_label": target,
        "role": "ISTD",
        "rt": "10.1",
        "area": "1000",
        "confidence": "HIGH",
        "nl": "OK",
        "prior_rt": "",
        "prior_source": "",
        "total_severity": "",
        "quality_flags": "",
        "reliability_state": state,
        "risk_reasons": risk,
        "known_exception": "",
        "area_to_target_median_ratio": area_ratio,
    }


def _candidate(
    sample: str,
    target: str,
    *,
    support: str,
    concern: str,
    ms2_present: str = "TRUE",
    nl_match: str,
) -> dict[str, str]:
    return {
        "sample_name": sample,
        "group": "QC",
        "target_label": target,
        "role": "ISTD",
        "istd_pair": "",
        "analysis_mode": "targeted",
        "resolver_mode": "region_first_safe_merge",
        "candidate_id": f"{sample}|{target}|selected",
        "proposal_sources": "local_minimum",
        "proposal_count": "1",
        "source_apex_rank": "1",
        "merge_note": "",
        "rt_left_min": "9.9",
        "rt_apex_min": "10.1",
        "rt_right_min": "10.3",
        "raw_apex_rt_min": "10.1",
        "rt_width_min": "0.4",
        "selection_apex_intensity": "1000",
        "raw_apex_intensity": "1100",
        "prominence": "900",
        "area_raw_counts_seconds": "1000",
        "area_baseline_corrected": "",
        "area_uncertainty": "",
        "quality_flags": "",
        "region_scan_count": "8",
        "region_duration_min": "0.4",
        "region_edge_ratio": "0.1",
        "region_trace_continuity": "0.9",
        "ms2_present": ms2_present,
        "nl_match": nl_match,
        "ms2_trace_strength": "strong",
        "rt_prior_min": "10.0",
        "rt_prior_sigma": "0.2",
        "confidence": "HIGH",
        "raw_score": "40",
        "support_labels": support,
        "concern_labels": concern,
        "cap_labels": "",
        "reason": "",
        "selected": "TRUE",
        "selection_rank": "1",
        "selection_reference_rt_min": "10.0",
        "rejection_reason": "",
    }


def _write_reliability_rows(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    _write_tsv(path, rows, tuple(rows[0]))


def _write_peak_candidates(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(path, rows, tuple(rows[0]))


def _write_targeted_workbook(path: Path, target_mz: dict[str, float]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Targets"
    sheet.append(("Label", "m/z"))
    for label, mz in target_mz.items():
        sheet.append((label, mz))
    workbook.save(path)


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: tuple[str, ...],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
