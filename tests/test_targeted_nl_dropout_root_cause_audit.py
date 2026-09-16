from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook

from tools.diagnostics import targeted_nl_dropout_root_cause_audit as audit


def test_path_style_cli_help_preserves_public_script_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = (
        repo_root / "tools" / "diagnostics" / "targeted_nl_dropout_root_cause_audit.py"
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


def test_root_cause_audit_classifies_review_positive_buckets(
    tmp_path: Path,
) -> None:
    reliability_rows = tmp_path / "targeted_peak_reliability_rows.tsv"
    peak_candidates = tmp_path / "peak_candidates.tsv"
    workbook = tmp_path / "targeted.xlsx"
    output_dir = tmp_path / "dropout_audit"
    _write_reliability_rows(
        reliability_rows,
        [
            _reliability("S0", "not_in_scope", "targeted_review"),
            _reliability("S1", "missing_selected", "targeted_review_positive"),
            _reliability("S2", "hard_conflict", "targeted_review_positive"),
            _reliability("S3", "no_trigger", "targeted_review_positive"),
            _reliability("S4", "no_product", "targeted_review_positive"),
            _reliability("S5", "off_apex", "targeted_review_positive"),
            _reliability("S6", "ppm_fail", "targeted_review_positive"),
            _reliability("S7", "weak_product", "targeted_review_positive"),
            _reliability("S8", "coherent", "targeted_review_positive"),
        ],
    )
    _write_peak_candidates(
        peak_candidates,
        [
            _candidate("S0", "not_in_scope", selected="TRUE"),
            _candidate("S2", "hard_conflict", concern="shape_poor", delta="0.2"),
            _candidate("S3", "no_trigger", ms2_present="FALSE", trigger_count="0"),
            _candidate(
                "S4",
                "no_product",
                best_loss_ppm="",
                product_absence_reason="product_below_intensity_floor",
                nearest_product_loss_ppm="5",
                nearest_product_base_ratio="0.004",
                nearest_product_mz="127.05",
            ),
            _candidate("S5", "off_apex", delta="0.081"),
            _candidate("S6", "ppm_fail", best_loss_ppm="12"),
            _candidate("S7", "weak_product", product_ratio="0.015"),
            _candidate("S8", "coherent"),
        ],
    )
    _write_targeted_workbook(
        workbook,
        {
            "missing_selected": 301.1,
            "hard_conflict": 302.2,
            "no_trigger": 303.3,
            "no_product": 304.4,
            "off_apex": 305.5,
            "ppm_fail": 306.6,
            "weak_product": 307.7,
            "coherent": 308.8,
        },
    )

    outputs, result = audit.run_targeted_nl_dropout_root_cause_audit(
        targeted_reliability_rows_tsv=reliability_rows,
        peak_candidates_tsv=peak_candidates,
        output_dir=output_dir,
        targeted_workbook=workbook,
    )

    by_key = {(row.sample_name, row.target_label): row for row in result.rows}
    assert ("S0", "not_in_scope") not in by_key
    assert by_key[("S1", "missing_selected")].root_cause_bucket == (
        "no_selected_candidate"
    )
    assert by_key[("S1", "missing_selected")].target_mz == 301.1
    assert by_key[("S2", "hard_conflict")].root_cause_bucket == (
        "hard_candidate_conflict"
    )
    assert by_key[("S3", "no_trigger")].root_cause_bucket == "no_ms2_trigger"
    assert by_key[("S4", "no_product")].root_cause_bucket == ("no_diagnostic_product")
    assert by_key[("S4", "no_product")].diagnostic_product_absence_reason == (
        "product_below_intensity_floor"
    )
    assert (
        "product_below_intensity_floor"
        in by_key[("S4", "no_product")].root_cause_reason
    )
    assert by_key[("S5", "off_apex")].root_cause_bucket == "off_apex_ms2"
    assert by_key[("S6", "ppm_fail")].root_cause_bucket == "ppm_gate_fail"
    assert by_key[("S7", "weak_product")].root_cause_bucket == ("weak_product_ratio")
    assert by_key[("S8", "coherent")].root_cause_bucket == ("coherent_ms1_nl_dropout")
    assert result.summary.review_positive_count == 8
    assert result.summary.bucket_counts == (
        "coherent_ms1_nl_dropout:1;"
        "hard_candidate_conflict:1;"
        "no_diagnostic_product:1;"
        "no_ms2_trigger:1;"
        "no_selected_candidate:1;"
        "off_apex_ms2:1;"
        "ppm_gate_fail:1;"
        "weak_product_ratio:1"
    )

    rows = _read_tsv(outputs.rows_tsv)
    with outputs.summary_tsv.open(encoding="utf-8", newline="") as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "rows_checked",
            "review_positive_count",
            "included_count",
            "missing_candidate_count",
            "bucket_counts",
            "target_counts",
            "product_absence_reason_counts",
        ]
    with outputs.rows_tsv.open(encoding="utf-8", newline="") as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "sample_name",
            "target_label",
            "target_mz",
            "role",
            "reliability_state",
            "targeted_risk_reasons",
            "resolver_mode",
            "selected_candidate_id",
            "selected_rt_apex_min",
            "selected_raw_score",
            "selected_confidence",
            "proposal_sources",
            "support_labels",
            "concern_labels",
            "quality_flags",
            "ms2_present",
            "nl_match",
            "nl_status",
            "best_loss_ppm",
            "best_ms2_scan_rt_min",
            "apex_ms2_delta_min",
            "best_product_base_ratio",
            "trigger_scan_count",
            "strict_nl_scan_count",
            "ms2_alignment_source",
            "diagnostic_product_absence_reason",
            "nearest_product_loss_ppm",
            "nearest_product_base_ratio",
            "nearest_product_mz",
            "root_cause_bucket",
            "root_cause_reason",
        ]
    assert {row["target_label"] for row in rows} == {
        "missing_selected",
        "hard_conflict",
        "no_trigger",
        "no_product",
        "off_apex",
        "ppm_fail",
        "weak_product",
        "coherent",
    }
    assert (output_dir / "targeted_nl_dropout_root_cause.md").exists()
    payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
    assert payload["summary"]["review_positive_count"] == 8
    assert payload["summary"]["product_absence_reason_counts"] == (
        "product_below_intensity_floor:1"
    )


def test_weak_product_ratio_uses_configured_nl_min_intensity_ratio(
    tmp_path: Path,
) -> None:
    reliability_rows = tmp_path / "targeted_peak_reliability_rows.tsv"
    peak_candidates = tmp_path / "peak_candidates.tsv"
    _write_reliability_rows(
        reliability_rows,
        [_reliability("S1", "borderline_product", "targeted_review_positive")],
    )
    _write_peak_candidates(
        peak_candidates,
        [
            _candidate(
                "S1",
                "borderline_product",
                product_ratio="0.035",
            )
        ],
    )

    _outputs, result = audit.run_targeted_nl_dropout_root_cause_audit(
        targeted_reliability_rows_tsv=reliability_rows,
        peak_candidates_tsv=peak_candidates,
        output_dir=tmp_path / "dropout_audit",
        nl_min_intensity_ratio=0.02,
    )

    assert result.rows[0].root_cause_bucket == "weak_product_ratio"
    assert "2 * nl_min_intensity_ratio" in result.rows[0].root_cause_reason


def test_hard_candidate_conflict_precedes_ppm_off_apex_and_weak_product(
    tmp_path: Path,
) -> None:
    reliability_rows = tmp_path / "targeted_peak_reliability_rows.tsv"
    peak_candidates = tmp_path / "peak_candidates.tsv"
    _write_reliability_rows(
        reliability_rows,
        [_reliability("S1", "conflicted", "targeted_review_positive")],
    )
    _write_peak_candidates(
        peak_candidates,
        [
            _candidate(
                "S1",
                "conflicted",
                concern="nl_fail;shape_poor",
                quality_flags="edge_clipped",
                best_loss_ppm="99",
                delta="0.5",
                product_ratio="0.001",
            )
        ],
    )

    _outputs, result = audit.run_targeted_nl_dropout_root_cause_audit(
        targeted_reliability_rows_tsv=reliability_rows,
        peak_candidates_tsv=peak_candidates,
        output_dir=tmp_path / "dropout_audit",
    )

    assert result.rows[0].root_cause_bucket == "hard_candidate_conflict"


def test_soft_trace_flags_with_cwt_context_do_not_mask_nl_dropout(
    tmp_path: Path,
) -> None:
    reliability_rows = tmp_path / "targeted_peak_reliability_rows.tsv"
    peak_candidates = tmp_path / "peak_candidates.tsv"
    _write_reliability_rows(
        reliability_rows,
        [_reliability("S1", "soft_trace_dropout", "targeted_review_positive")],
    )
    _write_peak_candidates(
        peak_candidates,
        [
            _candidate(
                "S1",
                "soft_trace_dropout",
                support="local_sn_strong;cwt_same_apex_support",
                quality_flags="low_trace_continuity;poor_edge_recovery",
                best_loss_ppm="",
                product_absence_reason="product_outside_diagnostic_window",
                nearest_product_loss_ppm="25",
            )
        ],
    )

    _outputs, result = audit.run_targeted_nl_dropout_root_cause_audit(
        targeted_reliability_rows_tsv=reliability_rows,
        peak_candidates_tsv=peak_candidates,
        output_dir=tmp_path / "dropout_audit",
    )

    assert result.rows[0].root_cause_bucket == "no_diagnostic_product"


def test_low_scan_support_stays_hard_candidate_conflict(
    tmp_path: Path,
) -> None:
    reliability_rows = tmp_path / "targeted_peak_reliability_rows.tsv"
    peak_candidates = tmp_path / "peak_candidates.tsv"
    _write_reliability_rows(
        reliability_rows,
        [_reliability("S1", "low_scan", "targeted_review_positive")],
    )
    _write_peak_candidates(
        peak_candidates,
        [
            _candidate(
                "S1",
                "low_scan",
                support="local_sn_strong;cwt_same_apex_support",
                quality_flags="low_scan_support",
                best_loss_ppm="",
                product_absence_reason="product_outside_diagnostic_window",
            )
        ],
    )

    _outputs, result = audit.run_targeted_nl_dropout_root_cause_audit(
        targeted_reliability_rows_tsv=reliability_rows,
        peak_candidates_tsv=peak_candidates,
        output_dir=tmp_path / "dropout_audit",
    )

    assert result.rows[0].root_cause_bucket == "hard_candidate_conflict"


def test_root_cause_cli_reports_missing_required_columns(
    tmp_path: Path,
    capsys,
) -> None:
    reliability_rows = tmp_path / "targeted_peak_reliability_rows.tsv"
    peak_candidates = tmp_path / "peak_candidates.tsv"
    reliability_rows.write_text("sample_name\ttarget_label\nS1\tT1\n", encoding="utf-8")
    peak_candidates.write_text("sample_name\ttarget_label\nS1\tT1\n", encoding="utf-8")

    code = audit.main(
        [
            "--targeted-reliability-rows-tsv",
            str(reliability_rows),
            "--peak-candidates-tsv",
            str(peak_candidates),
            "--output-dir",
            str(tmp_path / "dropout_audit"),
        ],
    )

    assert code == 2
    assert "missing required columns" in capsys.readouterr().err


def _reliability(sample: str, target: str, state: str) -> dict[str, str]:
    return {
        "sample_name": sample,
        "target_label": target,
        "role": "ISTD",
        "rt": "10.1",
        "area": "1000",
        "confidence": "VERY_LOW",
        "nl": "NL_FAIL",
        "prior_rt": "",
        "prior_source": "",
        "total_severity": "",
        "quality_flags": "",
        "reliability_state": state,
        "risk_reasons": "low_confidence;plausible_nl_dropout",
        "known_exception": "",
    }


def _candidate(
    sample: str,
    target: str,
    *,
    selected: str = "TRUE",
    support: str = "local_sn_strong;trace_clean",
    concern: str = "nl_fail",
    quality_flags: str = "",
    ms2_present: str = "TRUE",
    best_loss_ppm: str = "2",
    delta: str = "0.02",
    product_ratio: str = "0.2",
    trigger_count: str = "2",
    product_absence_reason: str = "",
    nearest_product_loss_ppm: str = "",
    nearest_product_base_ratio: str = "",
    nearest_product_mz: str = "",
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
        "quality_flags": quality_flags,
        "region_scan_count": "8",
        "region_duration_min": "0.4",
        "region_edge_ratio": "0.1",
        "region_trace_continuity": "0.9",
        "ms2_present": ms2_present,
        "nl_match": "FALSE",
        "ms2_trace_strength": "strong",
        "rt_prior_min": "10.0",
        "rt_prior_sigma": "0.2",
        "confidence": "VERY_LOW",
        "raw_score": "40",
        "support_labels": support,
        "concern_labels": concern,
        "cap_labels": "",
        "reason": "",
        "selected": selected,
        "selection_rank": "1",
        "selection_reference_rt_min": "10.0",
        "rejection_reason": "",
        "nl_status": "NL_FAIL",
        "best_loss_ppm": best_loss_ppm,
        "best_ms2_scan_rt_min": "10.12",
        "apex_ms2_delta_min": delta,
        "best_product_base_ratio": product_ratio,
        "trigger_scan_count": trigger_count,
        "strict_nl_scan_count": "0",
        "ms2_alignment_source": "region",
        "diagnostic_product_absence_reason": product_absence_reason,
        "nearest_product_loss_ppm": nearest_product_loss_ppm,
        "nearest_product_base_ratio": nearest_product_base_ratio,
        "nearest_product_mz": nearest_product_mz,
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
