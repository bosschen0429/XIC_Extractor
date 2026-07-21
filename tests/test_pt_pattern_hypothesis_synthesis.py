from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.diagnostics import pt_pattern_hypothesis_synthesis as synthesis_cli
from xic_extractor.discovery.pt_pattern_hypothesis_synthesis import (
    ASSEMBLY_LAYER_COLUMNS,
    KNOWN_EXAMPLE_COLUMNS,
    run_hypothesis_synthesis,
)
from xic_extractor.discovery.pt_pattern_ratio_profile import RATIO_SUMMARY_COLUMNS
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows


def test_hypothesis_synthesis_writes_diagnostic_only_report(
    tmp_path: Path,
) -> None:
    paths = _write_inputs(tmp_path)
    output_dir = tmp_path / "synthesis"

    outputs = run_hypothesis_synthesis(
        ratio_summary_csv=paths["ratio_summary"],
        background_ratio_summary_csv=paths["background_summary"],
        interference_summary_json=paths["interference"],
        plus1_seed_summary_csv=paths["plus1_summary"],
        inclusion_summary_csv=paths["inclusion_summary"],
        top102_diagnosis_csv=paths["top102"],
        known_diagnosis_csv=paths["known"],
        output_dir=output_dir,
        report_id="fixture_hypothesis",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["readiness_tier"] == "diagnostic_only"
    assert summary["authority_scope"] == "hypothesis_synthesis_only"
    assert summary["opens_raw"] is False
    assert summary["changes_matrix"] is False
    assert summary["changes_counted_detection"] is False
    assert summary["changes_detector_rule"] is False
    assert summary["ratio"]["primary_row_count"] == 3
    assert summary["interference"]["rt_12p5_13p5_accept_count"] == 2
    assert summary["plus1_seed"]["threshold_100000"][
        "current_146_accept_recovered"
    ] == 2
    assert summary["inclusion_list"]["top102"]["feature_all4_exist_20ppm"] == 2
    assert summary["observations"]
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "一句話結論" in markdown
    assert "目前不能相信什麼" in markdown
    assert "Ratio 公式目前怎麼用" in markdown
    assert "+1/base height" in markdown
    assert "Key Numbers" not in markdown
    html = outputs["html"].read_text(encoding="utf-8")
    assert "Pt Pattern 現況總覽" in html
    assert "先查 candidate assembly" in html
    assert 'data-chart="rt-hotspot"' in html
    assert 'data-chart="ratio-separation"' in html
    assert 'data-chart="plus1-threshold"' in html
    assert 'data-chart="assembly-stage"' in html
    assert "ratio: `" not in markdown

    layer_rows = read_delimited_rows(
        outputs["assembly_layer_summary_csv"],
        required_columns=ASSEMBLY_LAYER_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )
    assert {
        (row["query_set"], row["coelution_root_layer"], row["pipeline_status"])
        for row in layer_rows
    } == {
        ("top102", "accepted", "accepted_146"),
        ("top102", "candidate_rejected_by_trace_gate", "candidate_rejected"),
        (
            "manual_known",
            "no_coeluting_full_pattern_under_current_rule",
            "no_candidate_within20",
        ),
    }

    examples = read_delimited_rows(
        outputs["known_miss_examples_csv"],
        required_columns=KNOWN_EXAMPLE_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )
    assert len(examples) == 1
    assert examples[0]["diagnostic_note"] == (
        "four feature positions exist nearby but assembly did not accept"
    )


def test_hypothesis_synthesis_cli(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path)
    output_dir = tmp_path / "synthesis"

    result = synthesis_cli.main(
        [
            "--ratio-summary-csv",
            str(paths["ratio_summary"]),
            "--background-ratio-summary-csv",
            str(paths["background_summary"]),
            "--interference-summary-json",
            str(paths["interference"]),
            "--plus1-seed-summary-csv",
            str(paths["plus1_summary"]),
            "--inclusion-summary-csv",
            str(paths["inclusion_summary"]),
            "--top102-diagnosis-csv",
            str(paths["top102"]),
            "--known-diagnosis-csv",
            str(paths["known"]),
            "--output-dir",
            str(output_dir),
            "--report-id",
            "fixture_hypothesis",
        ]
    )

    assert result == 0
    assert (output_dir / "pt_pattern_hypothesis_synthesis.md").exists()
    assert (output_dir / "pt_pattern_hypothesis_synthesis.html").exists()
    summary = json.loads(
        (output_dir / "pt_pattern_hypothesis_synthesis_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["report_id"] == "fixture_hypothesis"


def test_hypothesis_synthesis_rejects_duplicate_ratio_name(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path)
    rows = read_delimited_rows(
        paths["ratio_summary"],
        required_columns=("ratio_name",),
        delimiter=",",
        encoding="utf-8",
    )
    write_delimited_rows(
        paths["ratio_summary"],
        [*rows, rows[0]],
        RATIO_SUMMARY_COLUMNS,
    )

    with pytest.raises(ValueError, match="duplicate primary ratio_name"):
        run_hypothesis_synthesis(
            ratio_summary_csv=paths["ratio_summary"],
            output_dir=tmp_path / "synthesis",
            report_id="duplicate_ratio",
        )


def test_hypothesis_synthesis_rejects_duplicate_plus1_threshold(
    tmp_path: Path,
) -> None:
    paths = _write_inputs(tmp_path)
    rows = read_delimited_rows(
        paths["plus1_summary"],
        required_columns=("threshold_plus1_height",),
        delimiter=",",
        encoding="utf-8",
    )
    write_delimited_rows(
        paths["plus1_summary"],
        [*rows, rows[0]],
        tuple(rows[0]),
    )

    with pytest.raises(ValueError, match="duplicate plus1 threshold_plus1_height"):
        run_hypothesis_synthesis(
            ratio_summary_csv=paths["ratio_summary"],
            plus1_seed_summary_csv=paths["plus1_summary"],
            output_dir=tmp_path / "synthesis",
            report_id="duplicate_plus1",
        )


def test_hypothesis_synthesis_rejects_duplicate_inclusion_key(
    tmp_path: Path,
) -> None:
    paths = _write_inputs(tmp_path)
    rows = read_delimited_rows(
        paths["inclusion_summary"],
        required_columns=("set", "metric"),
        delimiter=",",
        encoding="utf-8",
    )
    write_delimited_rows(
        paths["inclusion_summary"],
        [*rows, rows[0]],
        tuple(rows[0]),
    )

    with pytest.raises(ValueError, match="duplicate inclusion set/metric"):
        run_hypothesis_synthesis(
            ratio_summary_csv=paths["ratio_summary"],
            inclusion_summary_csv=paths["inclusion_summary"],
            output_dir=tmp_path / "synthesis",
            report_id="duplicate_inclusion",
        )


def test_hypothesis_synthesis_rejects_fractional_plus1_threshold_identity(
    tmp_path: Path,
) -> None:
    paths = _write_inputs(tmp_path)
    rows = read_delimited_rows(
        paths["plus1_summary"],
        required_columns=("threshold_plus1_height",),
        delimiter=",",
        encoding="utf-8",
    )
    rows[0]["threshold_plus1_height"] = "25000.5"
    write_delimited_rows(paths["plus1_summary"], rows, tuple(rows[0]))

    with pytest.raises(ValueError, match="finite integer identity key"):
        run_hypothesis_synthesis(
            ratio_summary_csv=paths["ratio_summary"],
            plus1_seed_summary_csv=paths["plus1_summary"],
            output_dir=tmp_path / "synthesis",
            report_id="fractional_plus1",
        )


def _write_inputs(tmp_path: Path) -> dict[str, Path]:
    ratio_summary = tmp_path / "accepted_ratio_summary.csv"
    background_summary = tmp_path / "rejected_ratio_background_summary.csv"
    plus1_summary = tmp_path / "plus1_intensity_pruned_summary.csv"
    inclusion_summary = tmp_path / "inclusion_list_reinterpreted_summary.csv"
    top102 = tmp_path / "top102_coelution_root_cause_diagnosis.csv"
    known = tmp_path / "known_named_root_cause_diagnostic.csv"
    interference = tmp_path / "interference_analysis_summary.json"

    write_delimited_rows(
        ratio_summary,
        [
            _ratio_summary("plus1_over_base_height_ratio", median=0.9),
            _ratio_summary("base_over_plus1_height_ratio", median=1.1),
            _ratio_summary("plus1_over_plus4_height_ratio", median=4.0),
        ],
        RATIO_SUMMARY_COLUMNS,
    )
    write_delimited_rows(
        background_summary,
        [
            _ratio_summary(
                "plus1_over_base_height_ratio",
                median=0.8,
                population_label="background_population",
            ),
        ],
        RATIO_SUMMARY_COLUMNS,
    )
    write_delimited_rows(
        plus1_summary,
        [
            _plus1_row(25000, candidates=4, accepted=3, top102=3),
            _plus1_row(100000, candidates=3, accepted=2, top102=2),
        ],
        (
            "threshold_plus1_height",
            "diagnostic_full_pattern_candidates",
            "unique_base_count",
            "current_386_candidate_recovered",
            "current_146_accept_recovered",
            "current_146_accept_recall_fraction",
            "top102_rows_hit_by_plus1_mz",
            "top102_recall_fraction",
            "manual_known_rows_hit_by_plus1_mz",
            "manual_known_recall_fraction",
            "edge_to_candidate_ratio",
        ),
    )
    write_delimited_rows(
        inclusion_summary,
        [
            {"set": "top102", "metric": "row_count", "value": 3, "note": ""},
            {
                "set": "top102",
                "metric": "feature_all4_exist_20ppm",
                "value": 2,
                "note": "",
            },
        ],
        ("set", "metric", "value", "note"),
    )
    write_delimited_rows(
        top102,
        [
            _assembly_row(
                "top102",
                "compound-a",
                "accepted_146",
                "accepted",
                all4=True,
                coherent=True,
            ),
            _assembly_row(
                "top102",
                "compound-b",
                "candidate_rejected",
                "candidate_rejected_by_trace_gate",
                reason="apex_delta_above_threshold",
                all4=True,
                coherent=True,
            ),
        ],
        _assembly_columns(),
    )
    write_delimited_rows(
        known,
        [
            _assembly_row(
                "manual_known",
                "known-a",
                "no_candidate_within20",
                "no_coeluting_full_pattern_under_current_rule",
                all4=True,
                coherent=False,
            ),
        ],
        _assembly_columns(),
    )
    interference.write_text(
        json.dumps(
            {
                "input_accept_count": 3,
                "rt_12p5_13p5_accept_count": 2,
                "rt_12p5_13p5_overlap_candidate_count": 2,
                "base_delta_overlap_candidate_count": 2,
                "base_delta_overlap_cluster_count": 1,
                "ratio_above_expected_with_overlap_count": 2,
                "ratio_above_expected_without_overlap_count": 0,
                "largest_clusters": [
                    {
                        "candidate_count": 2,
                        "edge_count": 5,
                        "rt_span_sec": 1.3,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return {
        "ratio_summary": ratio_summary,
        "background_summary": background_summary,
        "plus1_summary": plus1_summary,
        "inclusion_summary": inclusion_summary,
        "top102": top102,
        "known": known,
        "interference": interference,
    }


def _ratio_summary(
    ratio_name: str,
    *,
    median: float,
    population_label: str = "primary_population",
) -> dict[str, object]:
    row = {column: "" for column in RATIO_SUMMARY_COLUMNS}
    row.update(
        {
            "schema_version": "pt_pattern_ratio_profile.v1",
            "readiness_tier": "diagnostic_only",
            "evidence_owner": "owner",
            "authority_scope": "ratio_profile_only",
            "profile_id": "fixture",
            "population_label": population_label,
            "ratio_name": ratio_name,
            "row_count": 3,
            "missing_count": 0,
            "min": median,
            "q25": median,
            "median": median,
            "q75": median,
            "q95": median,
            "max": median,
            "outlier_count": 0,
        }
    )
    return row


def _plus1_row(
    threshold: int,
    *,
    candidates: int,
    accepted: int,
    top102: int,
) -> dict[str, object]:
    return {
        "threshold_plus1_height": threshold,
        "diagnostic_full_pattern_candidates": candidates,
        "unique_base_count": candidates,
        "current_386_candidate_recovered": candidates,
        "current_146_accept_recovered": accepted,
        "current_146_accept_recall_fraction": accepted / 3,
        "top102_rows_hit_by_plus1_mz": top102,
        "top102_recall_fraction": top102 / 3,
        "manual_known_rows_hit_by_plus1_mz": 1,
        "manual_known_recall_fraction": 0.5,
        "edge_to_candidate_ratio": 1.5,
    }


def _assembly_row(
    query_set: str,
    compound: str,
    pipeline_status: str,
    layer: str,
    *,
    reason: str = "",
    all4: bool,
    coherent: bool,
) -> dict[str, object]:
    count = 1 if all4 else 0
    return {
        "query_set": query_set,
        "list_row_1based": 1,
        "compound": compound,
        "list_plus1_mz": 501.0,
        "expected_base_mz": 500.0,
        "pipeline_status": pipeline_status,
        "coelution_root_layer": layer,
        "candidate_reject_reason_if_any": reason,
        "accepted_match_count_20ppm": 1 if pipeline_status == "accepted_146" else 0,
        "candidate_match_count_20ppm": 1
        if pipeline_status == "candidate_rejected"
        else 0,
        "feature_base_count_20ppm": count,
        "feature_plus1_count_20ppm": count,
        "feature_plus2_count_20ppm": count,
        "feature_plus4_count_20ppm": count,
        "coherent_full_pattern_exists": "TRUE" if coherent else "FALSE",
    }


def _assembly_columns() -> tuple[str, ...]:
    return (
        "query_set",
        "list_row_1based",
        "compound",
        "list_plus1_mz",
        "expected_base_mz",
        "pipeline_status",
        "coelution_root_layer",
        "candidate_reject_reason_if_any",
        "accepted_match_count_20ppm",
        "candidate_match_count_20ppm",
        "feature_base_count_20ppm",
        "feature_plus1_count_20ppm",
        "feature_plus2_count_20ppm",
        "feature_plus4_count_20ppm",
        "coherent_full_pattern_exists",
    )
