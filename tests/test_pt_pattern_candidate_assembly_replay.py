from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.diagnostics import pt_pattern_candidate_assembly_replay as replay_cli
from xic_extractor.discovery.detected_features import (
    DetectedFeature,
    write_detected_features_csv,
)
from xic_extractor.discovery.pt_pattern_candidate_assembly_replay import (
    REPLAY_COLUMNS,
    run_candidate_assembly_replay,
)
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows


def test_candidate_assembly_replay_classifies_first_break_and_rt_sensitivity(
    tmp_path: Path,
) -> None:
    inputs = _write_inputs(tmp_path)
    output_dir = tmp_path / "replay"

    outputs = run_candidate_assembly_replay(
        detected_features_csv=inputs["features"],
        top102_diagnosis_csv=inputs["top102"],
        known_diagnosis_csv=inputs["known"],
        output_dir=output_dir,
        report_id="fixture_candidate_assembly",
    )

    rows = read_delimited_rows(
        outputs["replay_csv"],
        required_columns=REPLAY_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )
    by_compound = {row["compound"]: row for row in rows}
    assert by_compound["mass-incoherent"]["classification"] == "mass_incoherent"
    assert by_compound["mass-incoherent"]["missing_mass_positions"] == "pt_plus_2"
    assert by_compound["rt-incoherent"]["classification"] == "rt_incoherent"
    assert by_compound["rt-incoherent"]["limiting_position"] == "pt_plus_4"
    assert by_compound["rt-incoherent"]["recovered_at_60_sec"] == "FALSE"
    assert by_compound["rt-incoherent"]["recovered_at_180_sec"] == "TRUE"
    assert (
        by_compound["rt-incoherent"]["replay_signature_id"]
        == by_compound["rt-alias"]["replay_signature_id"]
    )
    assert by_compound["emittable"]["classification"] == "unexpected_emittable"

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["readiness_tier"] == "diagnostic_only"
    assert summary["opens_raw"] is False
    assert summary["changes_detector_rule"] is False
    assert summary["changes_matrix"] is False
    assert summary["row_count"] == 4
    assert summary["unique_target_window_count"] == 3
    assert summary["unique_replay_signature_count"] == 3
    assert summary["classification_counts"] == {
        "mass_incoherent": 1,
        "rt_incoherent": 2,
        "unexpected_emittable": 1,
    }
    assert summary["unique_classification_counts"] == {
        "mass_incoherent": 1,
        "rt_incoherent": 1,
        "unexpected_emittable": 1,
    }
    assert summary["threshold_recovery_counts"]["60"] == 1
    assert summary["threshold_recovery_counts"]["180"] == 3
    assert summary["unique_threshold_recovery_counts"]["60"] == 1
    assert summary["unique_threshold_recovery_counts"]["180"] == 2

    html = outputs["html"].read_text(encoding="utf-8")
    assert "Candidate assembly 到底卡在哪" in html
    assert 'data-chart="first-break"' in html
    assert 'data-chart="rt-sensitivity"' in html
    assert 'data-chart="limiting-position"' in html
    assert "不開 RAW" in html


def test_candidate_assembly_replay_cli_writes_outputs(tmp_path: Path) -> None:
    inputs = _write_inputs(tmp_path)
    output_dir = tmp_path / "replay"

    result = replay_cli.main(
        [
            "--detected-features-csv",
            str(inputs["features"]),
            "--top102-diagnosis-csv",
            str(inputs["top102"]),
            "--known-diagnosis-csv",
            str(inputs["known"]),
            "--output-dir",
            str(output_dir),
            "--report-id",
            "fixture_candidate_assembly",
        ]
    )

    assert result == 0
    assert (output_dir / "pt_pattern_candidate_assembly_replay.csv").is_file()
    assert (output_dir / "pt_pattern_candidate_assembly_replay_summary.json").is_file()
    assert (output_dir / "pt_pattern_candidate_assembly_replay.html").is_file()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("coherent_full_pattern_exists", "garbage"),
        ("coherent_full_pattern_exists", ""),
        ("feature_base_count_20ppm", "1.5"),
        ("feature_base_count_20ppm", "-1"),
        ("compound", "   "),
        ("query_set", "   "),
        ("list_plus1_mz", "0"),
        ("expected_base_mz", "-1"),
    ],
)
def test_candidate_assembly_replay_rejects_invalid_top102_domain_values(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    inputs = _write_inputs(tmp_path)
    rows = read_delimited_rows(
        inputs["top102"],
        required_columns=(field,),
        delimiter=",",
        encoding="utf-8",
    )
    rows[0][field] = value
    write_delimited_rows(inputs["top102"], rows, tuple(rows[0]))

    with pytest.raises(ValueError, match=field):
        run_candidate_assembly_replay(
            detected_features_csv=inputs["features"],
            top102_diagnosis_csv=inputs["top102"],
            known_diagnosis_csv=inputs["known"],
            output_dir=tmp_path / "replay",
            report_id="invalid_top102_domain",
        )


@pytest.mark.parametrize(
    ("invalid_field", "zero_fields"),
    [
        ("feature_plus1_count_20ppm", ("feature_base_count_20ppm",)),
        (
            "feature_plus2_count_20ppm",
            ("feature_base_count_20ppm", "feature_plus1_count_20ppm"),
        ),
        (
            "feature_plus4_count_20ppm",
            (
                "feature_base_count_20ppm",
                "feature_plus1_count_20ppm",
                "feature_plus2_count_20ppm",
            ),
        ),
    ],
)
def test_candidate_assembly_replay_validates_all_counts_before_filtering(
    tmp_path: Path,
    invalid_field: str,
    zero_fields: tuple[str, ...],
) -> None:
    inputs = _write_inputs(tmp_path)
    rows = read_delimited_rows(
        inputs["top102"],
        required_columns=(invalid_field, *zero_fields),
        delimiter=",",
        encoding="utf-8",
    )
    for field in zero_fields:
        rows[0][field] = "0"
    rows[0][invalid_field] = "garbage"
    write_delimited_rows(inputs["top102"], rows, tuple(rows[0]))

    with pytest.raises(ValueError, match=invalid_field):
        run_candidate_assembly_replay(
            detected_features_csv=inputs["features"],
            top102_diagnosis_csv=inputs["top102"],
            known_diagnosis_csv=inputs["known"],
            output_dir=tmp_path / "replay",
            report_id="all_counts_validated",
        )


@pytest.mark.parametrize("source", ["top102", "known"])
def test_candidate_assembly_replay_rejects_inconsistent_target_identity(
    tmp_path: Path,
    source: str,
) -> None:
    inputs = _write_inputs(tmp_path)
    rows = read_delimited_rows(
        inputs[source],
        required_columns=("list_plus1_mz", "expected_base_mz"),
        delimiter=",",
        encoding="utf-8",
    )
    if source == "known":
        rows = [
            {
                "compound": "known-a",
                "list_plus1_mz": "501.002111",
                "expected_base_mz": "500.0",
                "chosen_candidate_id": "",
                "feature_base_nearest_abs_ppm": "1.0",
                "feature_plus1_nearest_abs_ppm": "1.0",
                "feature_plus2_nearest_abs_ppm": "1.0",
                "feature_plus4_nearest_abs_ppm": "1.0",
            }
        ]
    rows[0]["list_plus1_mz"] = "501.002111"
    rows[0]["expected_base_mz"] = "900"
    write_delimited_rows(inputs[source], rows, tuple(rows[0]))

    with pytest.raises(ValueError, match=r"list_plus1_mz.*Pt \+1 delta"):
        run_candidate_assembly_replay(
            detected_features_csv=inputs["features"],
            top102_diagnosis_csv=inputs["top102"],
            known_diagnosis_csv=inputs["known"],
            output_dir=tmp_path / "replay",
            report_id="inconsistent_target_identity",
        )


@pytest.mark.parametrize("value", ["garbage", "-0.1"])
def test_candidate_assembly_replay_rejects_invalid_known_nearest_ppm(
    tmp_path: Path,
    value: str,
) -> None:
    inputs = _write_inputs(tmp_path)
    columns = (
        "compound",
        "list_plus1_mz",
        "expected_base_mz",
        "chosen_candidate_id",
        "feature_base_nearest_abs_ppm",
        "feature_plus1_nearest_abs_ppm",
        "feature_plus2_nearest_abs_ppm",
        "feature_plus4_nearest_abs_ppm",
    )
    write_delimited_rows(
        inputs["known"],
        [
            {
                "compound": "known-a",
                "list_plus1_mz": "501.002111",
                "expected_base_mz": "500.0",
                "chosen_candidate_id": "",
                "feature_base_nearest_abs_ppm": value,
                "feature_plus1_nearest_abs_ppm": "1.0",
                "feature_plus2_nearest_abs_ppm": "1.0",
                "feature_plus4_nearest_abs_ppm": "1.0",
            }
        ],
        columns,
    )

    with pytest.raises(ValueError, match="feature_base_nearest_abs_ppm"):
        run_candidate_assembly_replay(
            detected_features_csv=inputs["features"],
            top102_diagnosis_csv=inputs["top102"],
            known_diagnosis_csv=inputs["known"],
            output_dir=tmp_path / "replay",
            report_id="invalid_known_nearest_ppm",
        )


def _write_inputs(tmp_path: Path) -> dict[str, Path]:
    features_csv = tmp_path / "detected_features.csv"
    top102_csv = tmp_path / "top102.csv"
    known_csv = tmp_path / "known.csv"
    features = [
        _feature("rt-base", 500.0, 10.0),
        _feature("rt-plus1", 501.002111, 10.0),
        _feature("rt-plus2", 502.002271, 10.0),
        _feature("rt-plus4", 504.005213, 11.6666667),
        _feature("mass-base", 600.0114, 20.0),
        _feature("mass-plus1", 601.002111, 20.0),
        _feature("mass-plus2", 601.9909, 20.0),
        _feature("mass-plus4", 604.005213, 20.0),
        _feature("emit-base", 700.0, 30.0),
        _feature("emit-plus1", 701.002111, 30.0),
        _feature("emit-plus2", 702.002271, 30.0),
        _feature("emit-plus4", 704.005213, 30.0),
    ]
    write_detected_features_csv(features_csv, features)
    write_delimited_rows(
        top102_csv,
        [
            _top102_row("rt-incoherent", 501.002111),
            _top102_row("rt-alias", 501.002111),
            _top102_row("mass-incoherent", 601.002111),
            _top102_row("emittable", 701.002111),
        ],
        (
            "query_set",
            "compound",
            "list_plus1_mz",
            "expected_base_mz",
            "pipeline_status",
            "coelution_root_layer",
            "feature_base_count_20ppm",
            "feature_plus1_count_20ppm",
            "feature_plus2_count_20ppm",
            "feature_plus4_count_20ppm",
            "coherent_full_pattern_exists",
        ),
    )
    write_delimited_rows(
        known_csv,
        [],
        (
            "compound",
            "list_plus1_mz",
            "expected_base_mz",
            "chosen_candidate_id",
            "feature_base_nearest_abs_ppm",
            "feature_plus1_nearest_abs_ppm",
            "feature_plus2_nearest_abs_ppm",
            "feature_plus4_nearest_abs_ppm",
        ),
    )
    return {"features": features_csv, "top102": top102_csv, "known": known_csv}


def _feature(feature_id: str, mz: float, rt_apex_min: float) -> DetectedFeature:
    return DetectedFeature(
        sample_stem="sample",
        feature_id=feature_id,
        source_feature_row_id=None,
        mz=mz,
        rt_apex_min=rt_apex_min,
        rt_start_min=rt_apex_min - 0.05,
        rt_end_min=rt_apex_min + 0.05,
        height=100_000.0,
        area=1_000_000.0,
        trace_quality="OK",
        shape_score=None,
        source="local_ms1_feature",
    )


def _top102_row(compound: str, plus1_mz: float) -> dict[str, object]:
    return {
        "query_set": "top102",
        "compound": compound,
        "list_plus1_mz": plus1_mz,
        "expected_base_mz": plus1_mz - 1.002111,
        "pipeline_status": "no_candidate_within20",
        "coelution_root_layer": "no_coeluting_full_pattern_under_current_rule",
        "feature_base_count_20ppm": 1,
        "feature_plus1_count_20ppm": 1,
        "feature_plus2_count_20ppm": 1,
        "feature_plus4_count_20ppm": 1,
        "coherent_full_pattern_exists": False,
    }
