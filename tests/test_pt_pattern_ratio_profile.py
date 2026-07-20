from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.diagnostics import pt_pattern_ratio_profile as ratio_profile_cli
from xic_extractor.discovery import delta_mass_csv
from xic_extractor.discovery.pt_pattern_ratio_profile import (
    RATIO_FORMULA_COLUMNS,
    RATIO_PROFILE_COLUMNS,
    RATIO_SUMMARY_COLUMNS,
    ratio_formula_manifest_rows,
    run_ratio_profile,
)
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows


def test_ratio_profile_writes_primary_rows_without_gate_language(
    tmp_path: Path,
) -> None:
    candidates_csv, evidence_csv = _write_profile_inputs(tmp_path)
    output_dir = tmp_path / "ratio_profile"

    outputs = run_ratio_profile(
        candidates_csv=candidates_csv,
        evidence_csv=evidence_csv,
        output_dir=output_dir,
        profile_id="fixture_ratio_profile",
    )

    rows = read_delimited_rows(
        outputs["accepted_ratio_profile_csv"],
        required_columns=RATIO_PROFILE_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )
    assert [row["candidate_id"] for row in rows] == [
        "candidate-1",
        "candidate-2",
        "candidate-3",
        "candidate-4",
        "candidate-5",
    ]
    forbidden = ("ratio_pass", "ratio_fail", "ratio_score", "ratio_confidence")
    assert not any(
        forbidden_token in column
        for column in rows[0]
        for forbidden_token in forbidden
    )
    first = rows[0]
    assert first["fh_base_to_plus1_height_ratio"] == "1"
    assert first["fh_base_to_plus2_height_ratio"] == "2"
    assert first["fh_base_to_plus4_height_ratio"] == "5"
    assert first["base_over_plus1_height_ratio"] == "1"
    assert first["base_over_plus2_height_ratio"] == "2"
    assert first["base_over_plus4_height_ratio"] == "5"
    assert first["plus1_over_base_height_ratio"] == "1"
    assert first["plus1_over_plus2_height_ratio"] == "2"
    assert first["plus1_over_plus4_height_ratio"] == "5"
    outlier_row = next(row for row in rows if row["candidate_id"] == "candidate-5")
    assert outlier_row["ratio_outlier_count"] != "0"
    assert "plus1_over_plus4_height_ratio" in outlier_row["ratio_extreme_columns"]
    assert outlier_row["ratio_review_note"] == "distribution_tail_review"


def test_ratio_profile_summarizes_primary_and_background_populations(
    tmp_path: Path,
) -> None:
    candidates_csv, evidence_csv = _write_profile_inputs(tmp_path)
    output_dir = tmp_path / "ratio_profile"

    outputs = run_ratio_profile(
        candidates_csv=candidates_csv,
        evidence_csv=evidence_csv,
        output_dir=output_dir,
        profile_id="fixture_ratio_profile",
    )

    primary_summary = read_delimited_rows(
        outputs["accepted_ratio_summary_csv"],
        required_columns=RATIO_SUMMARY_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )
    background_summary = read_delimited_rows(
        outputs["rejected_ratio_background_summary_csv"],
        required_columns=RATIO_SUMMARY_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )
    plus1_summary = next(
        row
        for row in primary_summary
        if row["ratio_name"] == "plus1_over_base_height_ratio"
    )
    assert plus1_summary["population_label"] == "primary_population"
    assert plus1_summary["row_count"] == "5"
    assert plus1_summary["median"] == "1"
    assert {row["population_label"] for row in background_summary} == {
        "background_population"
    }

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["readiness_tier"] == "diagnostic_only"
    assert summary["authority_scope"] == "ratio_profile_only"
    assert summary["opens_raw"] is False
    assert summary["changes_matrix"] is False
    assert summary["changes_counted_detection"] is False
    assert summary["changes_export"] is False
    assert summary["primary_row_count"] == 5
    assert summary["background_row_count"] == 2
    formula_rows = read_delimited_rows(
        outputs["ratio_formula_manifest_csv"],
        required_columns=RATIO_FORMULA_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )
    base_plus1 = next(
        row
        for row in formula_rows
        if row["ratio_name"] == "base_over_plus1_height_ratio"
    )
    assert base_plus1["formula_family"] == "base_over_position"
    assert base_plus1["numerator"] == "base position"
    assert base_plus1["denominator"] == "+1 position"


def test_ratio_profile_cli_writes_static_html_without_call_words(
    tmp_path: Path,
) -> None:
    candidates_csv, evidence_csv = _write_profile_inputs(tmp_path)
    output_dir = tmp_path / "ratio_profile"

    result = ratio_profile_cli.main(
        [
            "--candidates-csv",
            str(candidates_csv),
            "--evidence-csv",
            str(evidence_csv),
            "--output-dir",
            str(output_dir),
            "--profile-id",
            "fixture_ratio_profile",
        ]
    )

    assert result == 0
    html = (output_dir / "pt_pattern_ratio_profile.html").read_text(
        encoding="utf-8"
    )
    assert "diagnostic_only" in html
    assert "primary population" in html
    assert "background population" in html
    assert "先看結論" in html
    assert "Ratio 怎麼看" in html
    assert "Formula 判讀" in html
    assert "Debug: 完整 formula manifest" in html
    assert "+1 / base height" in html
    assert "base_over_plus1_height_ratio" in html
    assert 'data-chart="ratio-intervals"' in html
    assert 'data-chart="rt-ratio-small-multiples"' in html
    assert "accepted 與 background" in html
    assert "12.5-13.5 min" in html
    assert not re.search(r"\b(accept|reject|pass|fail)\b", html.lower())


def test_ratio_formula_manifest_keeps_featurehunter_as_comparator_only() -> None:
    rows = ratio_formula_manifest_rows(profile_id="fixture_ratio_profile")

    assert len(rows) == 22
    by_name = {row["ratio_name"]: row for row in rows}
    assert by_name["fh_base_to_plus1_height_ratio"]["formula_family"] == (
        "historical_featurehunter_pair"
    )
    assert by_name["fh_base_to_plus1_height_ratio"]["numerator"] == (
        "lower-m/z precursor ion"
    )
    assert by_name["plus1_over_plus4_height_ratio"]["formula_family"] == (
        "plus1_anchor"
    )


def test_ratio_profile_rejects_duplicate_candidate_population_keys(
    tmp_path: Path,
) -> None:
    candidates_csv, evidence_csv = _write_profile_inputs(tmp_path)
    candidates = read_delimited_rows(
        candidates_csv,
        required_columns=("sample_stem", "candidate_id"),
        delimiter=",",
        encoding="utf-8",
    )
    write_delimited_rows(
        candidates_csv,
        [*candidates, candidates[0]],
        delta_mass_csv.PT_CANDIDATE_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate sample_stem/candidate_id key"):
        run_ratio_profile(
            candidates_csv=candidates_csv,
            evidence_csv=evidence_csv,
            output_dir=tmp_path / "ratio_profile",
            profile_id="duplicate_candidate",
        )


def _write_profile_inputs(tmp_path: Path) -> tuple[Path, Path]:
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    evidence_csv = tmp_path / "pt_pattern_evidence.csv"
    candidates = [
        _candidate("candidate-1", plus1=1.0, plus2=0.5, plus4=0.2),
        _candidate("candidate-2", plus1=1.0, plus2=0.6, plus4=0.25),
        _candidate("candidate-3", plus1=1.0, plus2=0.7, plus4=0.3),
        _candidate("candidate-4", plus1=1.0, plus2=0.8, plus4=0.4),
        _candidate("candidate-5", plus1=1.0, plus2=0.9, plus4=0.01),
        _candidate("candidate-6", plus1=0.8, plus2=0.2, plus4=0.1),
        _candidate("candidate-7", plus1=1.2, plus2=0.4, plus4=0.2),
    ]
    evidence = [
        _evidence("candidate-1", trace_rule_call="accept"),
        _evidence("candidate-2", trace_rule_call="accept"),
        _evidence("candidate-3", trace_rule_call="accept"),
        _evidence("candidate-4", trace_rule_call="accept"),
        _evidence("candidate-5", trace_rule_call="accept"),
        _evidence("candidate-6", trace_rule_call="reject"),
        _evidence("candidate-7", trace_rule_call="not_evaluated"),
    ]
    write_delimited_rows(
        candidates_csv,
        candidates,
        delta_mass_csv.PT_CANDIDATE_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )
    write_delimited_rows(
        evidence_csv,
        evidence,
        delta_mass_csv.PT_EVIDENCE_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )
    return candidates_csv, evidence_csv


def _candidate(
    candidate_id: str,
    *,
    plus1: float,
    plus2: float,
    plus4: float,
) -> dict[str, object]:
    row: dict[str, object] = {
        column: "" for column in delta_mass_csv.PT_CANDIDATE_COLUMNS
    }
    row.update(
        {
            "schema_version": "pt_candidate.v1",
            "sample_stem": "sample-a",
            "method_label": "pt_only",
            "pattern_id": "pt",
            "candidate_id": candidate_id,
            "candidate_state": "candidate",
            "base_mz": 500.0,
            "base_rt_apex_min": 12.5,
            "base_height": 100000.0,
            "base_area": 1000000.0,
            "pt_plus_1_height_ratio_to_base": plus1,
            "pt_plus_2_height_ratio_to_base": plus2,
            "pt_plus_4_height_ratio_to_base": plus4,
            "pt_plus_1_area_ratio_to_base": plus1 * 0.9,
            "pt_plus_2_area_ratio_to_base": plus2 * 0.9,
            "pt_plus_4_area_ratio_to_base": plus4 * 0.9,
        }
    )
    return row


def _evidence(candidate_id: str, *, trace_rule_call: str) -> dict[str, object]:
    row: dict[str, object] = {
        column: "" for column in delta_mass_csv.PT_EVIDENCE_COLUMNS
    }
    row.update(
        {
            "schema_version": "pt_evidence.v1",
            "sample_stem": "sample-a",
            "method_label": "pt_only",
            "pattern_id": "pt",
            "candidate_id": candidate_id,
            "trace_rule_call": trace_rule_call,
            "trace_detection_status": "detected"
            if trace_rule_call == "accept"
            else "not_detected",
            "combined_pt_pattern_status": "pt_pattern_detected_diagnostic"
            if trace_rule_call == "accept"
            else "not_detected",
            "non_base_positions_supported": 3,
            "max_abs_apex_delta_sec": 0.5,
            "min_pearson_r_to_base": 0.95,
            "min_active_overlap_fraction": 0.9,
            "matrix_write_authority": "FALSE",
            "counted_detection_authority": "FALSE",
            "export_activation_authority": "FALSE",
        }
    )
    return row
