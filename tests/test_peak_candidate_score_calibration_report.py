import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.diagnostics import peak_candidate_score_calibration_report as report


def test_path_style_cli_help_preserves_public_script_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = (
        repo_root
        / "tools"
        / "diagnostics"
        / "peak_candidate_score_calibration_report.py"
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
    assert "--peak-candidates-tsv" in result.stdout
    assert "--output-dir" in result.stdout


def test_label_impact_accumulates_scores_and_preserves_order() -> None:
    rows = tuple(
        report._row_from_dict(Path("peak_candidates.tsv"), row_number, row)
        for row_number, row in enumerate(
            (
                _row(
                    "SampleA",
                    "TargetA",
                    "TargetA:selected",
                    selected=True,
                    raw_score=10,
                    confidence="HIGH",
                    support="shared_support;selected_only",
                    concern="",
                    ms2_present="TRUE",
                    nl_match="TRUE",
                ),
                _row(
                    "SampleB",
                    "TargetB",
                    "TargetB:selected",
                    selected=True,
                    raw_score=30,
                    confidence="HIGH",
                    support="shared_support",
                    concern="nl_fail",
                    ms2_present="TRUE",
                    nl_match="FALSE",
                ),
                _row(
                    "SampleC",
                    "TargetC",
                    "TargetC:rejected",
                    selected=False,
                    raw_score=80,
                    confidence="HIGH",
                    support="shared_support",
                    concern="",
                    ms2_present="TRUE",
                    nl_match="TRUE",
                ),
                _row(
                    "SampleD",
                    "TargetD",
                    "TargetD:rejected",
                    selected=False,
                    raw_score=40,
                    confidence="HIGH",
                    support="shared_support",
                    concern="",
                    ms2_present="TRUE",
                    nl_match="TRUE",
                ),
            ),
            start=2,
        )
    )

    label_rows = report._label_impact(rows)

    shared = next(row for row in label_rows if row.label == "shared_support")
    assert shared.label_kind == "support"
    assert shared.selected_count == 2
    assert shared.rejected_count == 2
    assert shared.selected_median_raw_score == 20.0
    assert shared.rejected_median_raw_score == 60.0
    assert [(row.label_kind, row.label) for row in label_rows] == [
        ("cap", "nl_fail_cap"),
        ("concern", "nl_fail"),
        ("support", "shared_support"),
        ("support", "selected_only"),
    ]


def test_score_calibration_report_flags_selected_risks_and_challengers(
    tmp_path: Path,
) -> None:
    peak_candidates = tmp_path / "peak_candidates.tsv"
    output_dir = tmp_path / "score_calibration"
    _write_peak_candidates(
        peak_candidates,
        [
            _row(
                "SampleA",
                "CleanTarget",
                "CleanTarget:selected",
                selected=True,
                raw_score=120,
                confidence="HIGH",
                support="strict_nl_ok;ms2_trace_strong;rt_prior_close",
                concern="",
                ms2_present="TRUE",
                nl_match="TRUE",
            ),
            _row(
                "SampleA",
                "CleanTarget",
                "CleanTarget:rejected_cwt",
                selected=False,
                raw_score=125,
                confidence="HIGH",
                proposal_sources="legacy_savgol;centwave_cwt",
                support="strict_nl_ok;ms2_trace_strong;cwt_same_apex_support",
                concern="",
                ms2_present="TRUE",
                nl_match="TRUE",
                rejection_reason="farther_from_preferred_rt",
            ),
            _row(
                "SampleA",
                "CleanTarget",
                "CleanTarget:rejected_alt",
                selected=False,
                raw_score=130,
                confidence="HIGH",
                rt_apex="8.72000",
                support="strict_nl_ok;ms2_trace_strong;trace_clean",
                concern="",
                ms2_present="TRUE",
                nl_match="TRUE",
                rejection_reason="farther_from_preferred_rt",
            ),
            _row(
                "SampleB",
                "RiskTarget",
                "RiskTarget:selected",
                selected=True,
                raw_score=20,
                confidence="VERY_LOW",
                support="local_sn_strong;trace_clean",
                concern="nl_fail",
                ms2_present="TRUE",
                nl_match="FALSE",
                reason="decision: review only, not counted; concerns: nl fail",
            ),
            _row(
                "SampleB",
                "RiskTarget",
                "RiskTarget:rejected_strict",
                selected=False,
                raw_score=95,
                confidence="HIGH",
                rt_apex="9.00000",
                support="strict_nl_ok;ms2_trace_moderate",
                concern="",
                ms2_present="TRUE",
                nl_match="TRUE",
                rejection_reason="farther_from_preferred_rt",
            ),
        ],
    )

    code = report.main(
        [
            "--peak-candidates-tsv",
            str(peak_candidates),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    payload = json.loads(
        (output_dir / "peak_candidate_score_calibration.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["summary"] == {
        "candidate_row_count": 5,
        "candidate_group_count": 2,
        "selected_row_count": 2,
        "rejected_row_count": 3,
        "selected_review_only_count": 1,
        "selected_nl_fail_count": 1,
        "selected_no_ms2_count": 0,
        "plausible_nl_dropout_selected_count": 0,
        "apex_evidence_shadow_group_count": 1,
        "high_score_rejected_challenger_group_count": 2,
        "strict_nl_rejected_challenger_group_count": 1,
        "cwt_supported_rejected_challenger_group_count": 0,
    }

    with (output_dir / "peak_candidate_score_calibration_summary.tsv").open(
        encoding="utf-8", newline=""
    ) as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "candidate_row_count",
            "candidate_group_count",
            "selected_row_count",
            "rejected_row_count",
            "selected_review_only_count",
            "selected_nl_fail_count",
            "selected_no_ms2_count",
            "plausible_nl_dropout_selected_count",
            "apex_evidence_shadow_group_count",
            "high_score_rejected_challenger_group_count",
            "strict_nl_rejected_challenger_group_count",
            "cwt_supported_rejected_challenger_group_count",
        ]

    risk_rows = _read_tsv(output_dir / "peak_candidate_score_risk_rows.tsv")
    with (output_dir / "peak_candidate_score_risk_rows.tsv").open(
        encoding="utf-8", newline=""
    ) as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "group_id",
            "sample_name",
            "target_label",
            "resolver_mode",
            "risk_type",
            "selected_candidate_id",
            "selected_rt_apex_min",
            "selected_raw_score",
            "selected_confidence",
            "selected_support_labels",
            "selected_concern_labels",
            "challenger_candidate_id",
            "challenger_rt_apex_min",
            "challenger_raw_score",
            "challenger_confidence",
            "challenger_support_labels",
            "challenger_concern_labels",
            "reason",
        ]
    assert {(row["group_id"], row["risk_type"]) for row in risk_rows} == {
        (
            "SampleA|CleanTarget|region_first_safe_merge",
            "high_score_rejected_challenger",
        ),
        ("SampleA|CleanTarget|region_first_safe_merge", "apex_evidence_shadow"),
        ("SampleB|RiskTarget|region_first_safe_merge", "selected_review_only"),
        ("SampleB|RiskTarget|region_first_safe_merge", "selected_nl_fail"),
        (
            "SampleB|RiskTarget|region_first_safe_merge",
            "high_score_rejected_challenger",
        ),
        ("SampleB|RiskTarget|region_first_safe_merge", "strict_nl_rejected_challenger"),
    }

    label_rows = _read_tsv(output_dir / "peak_candidate_score_label_impact.tsv")
    with (output_dir / "peak_candidate_score_label_impact.tsv").open(
        encoding="utf-8", newline=""
    ) as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "label_kind",
            "label",
            "selected_count",
            "rejected_count",
            "selected_rate",
            "selected_median_raw_score",
            "rejected_median_raw_score",
        ]
    strict_nl = next(row for row in label_rows if row["label"] == "strict_nl_ok")
    assert strict_nl["label_kind"] == "support"
    assert strict_nl["selected_count"] == "1"
    assert strict_nl["rejected_count"] == "3"
    cwt = next(row for row in label_rows if row["label"] == "cwt_same_apex_support")
    assert cwt["selected_count"] == "0"
    assert cwt["rejected_count"] == "1"
    assert (output_dir / "peak_candidate_score_calibration.md").is_file()


def test_score_calibration_summary_writer_rejects_extra_keys(
    tmp_path: Path,
) -> None:
    summary = {column: 0 for column in report._SUMMARY_COLUMNS}
    summary["unexpected"] = 1

    with pytest.raises(ValueError, match="dict contains fields not in fieldnames"):
        report._write_summary(
            tmp_path / "summary.tsv",
            {"summary": summary},
        )


def test_score_calibration_report_splits_plausible_nl_dropout_selected_rows(
    tmp_path: Path,
) -> None:
    peak_candidates = tmp_path / "peak_candidates.tsv"
    output_dir = tmp_path / "score_calibration"
    _write_peak_candidates(
        peak_candidates,
        [
            _row(
                "SampleA",
                "8-oxodG",
                "8-oxodG:selected",
                selected=True,
                raw_score=35,
                confidence="VERY_LOW",
                proposal_sources="local_minimum;centwave_cwt",
                support="local_sn_strong;shape_clean;trace_clean",
                concern="nl_fail",
                ms2_present="TRUE",
                nl_match="FALSE",
                reason="decision: review only, not counted; concerns: nl fail",
            ),
            _row(
                "SampleB",
                "8-oxodG",
                "8-oxodG:weak_selected",
                selected=True,
                raw_score=-20,
                confidence="VERY_LOW",
                support="local_sn_strong;shape_clean",
                concern="nl_fail;low_scan_support;poor_edge_recovery",
                ms2_present="TRUE",
                nl_match="FALSE",
                reason="decision: review only, not counted; concerns: nl fail",
            ),
            _row(
                "SampleC",
                "8-oxodG",
                "8-oxodG:no_ms2_selected",
                selected=True,
                raw_score=55,
                confidence="LOW",
                proposal_sources="local_minimum;centwave_cwt",
                support="local_sn_strong;shape_clean;trace_clean",
                concern="no_ms2",
                ms2_present="FALSE",
                nl_match="FALSE",
                reason="decision: review only, not counted; concerns: no MS2",
            ),
        ],
    )

    code = report.main(
        [
            "--peak-candidates-tsv",
            str(peak_candidates),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    payload = json.loads(
        (output_dir / "peak_candidate_score_calibration.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["summary"]["selected_nl_fail_count"] == 3
    assert payload["summary"]["selected_no_ms2_count"] == 1
    assert payload["summary"]["plausible_nl_dropout_selected_count"] == 1
    risk_rows = _read_tsv(output_dir / "peak_candidate_score_risk_rows.tsv")
    assert {
        row["group_id"]
        for row in risk_rows
        if row["risk_type"] == "plausible_nl_dropout_selected"
    } == {"SampleA|8-oxodG|region_first_safe_merge"}


def test_score_calibration_report_rejects_missing_required_columns(
    tmp_path: Path,
) -> None:
    peak_candidates = tmp_path / "peak_candidates.tsv"
    output_dir = tmp_path / "score_calibration"
    peak_candidates.write_text(
        "sample_name\ttarget_label\nSampleA\tTargetA\n",
        encoding="utf-8",
    )

    code = report.main(
        [
            "--peak-candidates-tsv",
            str(peak_candidates),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 2
    assert not (output_dir / "peak_candidate_score_calibration.json").exists()


def test_score_calibration_report_rejects_invalid_selected_value(
    tmp_path: Path,
) -> None:
    peak_candidates = tmp_path / "peak_candidates.tsv"
    output_dir = tmp_path / "score_calibration"
    row = _row(
        "SampleA",
        "TargetA",
        "TargetA:selected",
        selected=True,
        raw_score=10,
        confidence="HIGH",
        support="strict_nl_ok",
        concern="",
        ms2_present="TRUE",
        nl_match="TRUE",
    )
    row["selected"] = "MAYBE"
    _write_peak_candidates(peak_candidates, [row])

    code = report.main(
        [
            "--peak-candidates-tsv",
            str(peak_candidates),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 2
    assert not (output_dir / "peak_candidate_score_calibration.json").exists()


def _write_peak_candidates(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_HEADER, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _row(
    sample: str,
    target: str,
    candidate_id: str,
    *,
    selected: bool,
    raw_score: int,
    confidence: str,
    proposal_sources: str = "legacy_savgol",
    rt_apex: str = "8.50000",
    support: str,
    concern: str,
    ms2_present: str,
    nl_match: str,
    reason: str = "decision: accepted",
    rejection_reason: str = "",
) -> dict[str, str]:
    return {
        "sample_name": sample,
        "target_label": target,
        "resolver_mode": "region_first_safe_merge",
        "candidate_id": candidate_id,
        "proposal_sources": proposal_sources,
        "rt_apex_min": rt_apex,
        "selected": "TRUE" if selected else "FALSE",
        "confidence": confidence,
        "raw_score": str(raw_score),
        "support_labels": support,
        "concern_labels": concern,
        "cap_labels": "nl_fail_cap" if "nl_fail" in concern else "",
        "reason": reason,
        "rejection_reason": rejection_reason,
        "ms2_present": ms2_present,
        "nl_match": nl_match,
        "ms2_trace_strength": "moderate" if "ms2_trace_moderate" in support else "",
    }


_HEADER = (
    "sample_name",
    "target_label",
    "resolver_mode",
    "candidate_id",
    "proposal_sources",
    "rt_apex_min",
    "selected",
    "confidence",
    "raw_score",
    "support_labels",
    "concern_labels",
    "cap_labels",
    "reason",
    "rejection_reason",
    "ms2_present",
    "nl_match",
    "ms2_trace_strength",
)
