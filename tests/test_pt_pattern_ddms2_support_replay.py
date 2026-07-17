from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tools.diagnostics import pt_pattern_ddms2_support_replay as replay_cli
from xic_extractor.discovery.pt_pattern_ddms2_support_replay import (
    SCHEMA_VERSION,
    run_ddms2_support_replay,
)


def test_ddms2_support_replay_requires_fullscan_accept_and_reports_support_only(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    evidence_csv = tmp_path / "pt_pattern_evidence.csv"
    ddms2_features_csv = tmp_path / "detected_features.csv"
    _write_csv(
        candidates_csv,
        (
            "sample_stem",
            "candidate_id",
            "base_feature_id",
            "base_mz",
            "base_rt_apex_min",
        ),
        [
            {
                "sample_stem": "CT-DNA+100uMCP_Fullscan",
                "candidate_id": "accepted_seed",
                "base_feature_id": "fullscan_base_1",
                "base_mz": "500.0000",
                "base_rt_apex_min": "10.0",
            },
            {
                "sample_stem": "CT-DNA+100uMCP_Fullscan",
                "candidate_id": "rejected_seed",
                "base_feature_id": "fullscan_base_2",
                "base_mz": "600.0000",
                "base_rt_apex_min": "12.0",
            },
        ],
    )
    _write_csv(
        evidence_csv,
        ("candidate_id", "trace_rule_call", "trace_detection_status"),
        [
            {
                "candidate_id": "accepted_seed",
                "trace_rule_call": "accept",
                "trace_detection_status": "detected",
            },
            {
                "candidate_id": "rejected_seed",
                "trace_rule_call": "reject",
                "trace_detection_status": "non_detected",
            },
        ],
    )
    _write_detected_features(
        ddms2_features_csv,
        [
            _feature_row("ddms2_base", 500.0000, 10.05, 1000.0),
            _feature_row("ddms2_plus1", 501.002111, 10.04, 900.0),
            _feature_row("ddms2_plus2", 502.002271, 10.03, 800.0),
            _feature_row("ddms2_plus4", 504.005213, 10.02, 700.0),
        ],
    )

    result = run_ddms2_support_replay(
        fullscan_candidates_csv=candidates_csv,
        fullscan_evidence_csv=evidence_csv,
        ddms2_detected_features_csv=ddms2_features_csv,
        mz_tolerance_ppm=20,
        rt_window_sec=45,
    )

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["schema_version"] == SCHEMA_VERSION
    assert row["fullscan_candidate_id"] == "accepted_seed"
    assert row["ddms2_ms1_support_status"] == "full_pattern_feature_supported"
    assert row["ddms2_position_count_supported"] == 4
    assert row["ddms2_non_base_positions_supported"] == 3
    assert row["ddms2_targeting_status"] == "unknown_target_list_missing"
    assert row["ddms2_ms2_support_status"] == "not_evaluable_without_target_list"
    assert row["changes_matrix_authority"] is False
    assert row["changes_counted_detection_authority"] is False
    assert row["changes_export_authority"] is False
    assert result.summary["seed_count"] == 1
    assert result.summary["ddms2_ms1_support_status_counts"] == {
        "full_pattern_feature_supported": 1,
    }


def test_ddms2_support_replay_uses_expected_mz_as_ppm_denominator(
    tmp_path: Path,
) -> None:
    candidates_csv, evidence_csv, ddms2_features_csv = _single_seed_inputs(
        tmp_path,
        base_mz=500.0,
        base_rt_apex_min=10.0,
    )
    expected_plus1 = 501.002111
    observed_plus1 = expected_plus1 * (1 + 10e-6)
    _write_detected_features(
        ddms2_features_csv,
        [
            _feature_row("base", 500.0, 10.0, 1000.0),
            _feature_row("plus1", observed_plus1, 10.0, 900.0),
        ],
    )

    result = run_ddms2_support_replay(
        fullscan_candidates_csv=candidates_csv,
        fullscan_evidence_csv=evidence_csv,
        ddms2_detected_features_csv=ddms2_features_csv,
        mz_tolerance_ppm=20,
        rt_window_sec=45,
    )

    row = result.rows[0]
    assert row["pt_plus_1_ddms2_feature_status"] == "feature_supported"
    assert row["pt_plus_1_ddms2_mz_error_ppm"] == pytest.approx(10.0)


def test_ddms2_support_replay_keeps_missing_support_diagnostic_only(
    tmp_path: Path,
) -> None:
    candidates_csv, evidence_csv, ddms2_features_csv = _single_seed_inputs(
        tmp_path,
        base_mz=700.0,
        base_rt_apex_min=20.0,
    )
    _write_detected_features(
        ddms2_features_csv,
        [_feature_row("far_away", 900.0, 20.0, 1000.0)],
    )

    result = run_ddms2_support_replay(
        fullscan_candidates_csv=candidates_csv,
        fullscan_evidence_csv=evidence_csv,
        ddms2_detected_features_csv=ddms2_features_csv,
        mz_tolerance_ppm=20,
        rt_window_sec=45,
    )

    row = result.rows[0]
    assert row["ddms2_ms1_support_status"] == "missing_base_feature_support"
    assert row["reason"] == "ddms2_base_feature_missing_within_mz_rt_window"
    assert row["ddms2_ms2_support_status"] == "not_evaluable_without_target_list"
    assert row["readiness_tier"] == "diagnostic_only"
    assert result.summary["changes_matrix_authority"] is False


def test_ddms2_support_replay_rejects_unknown_requested_sample_stem(
    tmp_path: Path,
) -> None:
    candidates_csv, evidence_csv, ddms2_features_csv = _single_seed_inputs(
        tmp_path,
        base_mz=500.0,
        base_rt_apex_min=10.0,
    )
    _write_detected_features(
        ddms2_features_csv,
        [_feature_row("base", 500.0, 10.0, 1000.0)],
    )

    try:
        run_ddms2_support_replay(
            fullscan_candidates_csv=candidates_csv,
            fullscan_evidence_csv=evidence_csv,
            ddms2_detected_features_csv=ddms2_features_csv,
            ddms2_sample_stem="missing_sample",
        )
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected ValueError")


@pytest.mark.parametrize(
    ("parameter", "value", "message"),
    (
        ("mz_tolerance_ppm", float("inf"), "mz_tolerance_ppm"),
        ("rt_window_sec", float("nan"), "rt_window_sec"),
    ),
)
def test_ddms2_support_replay_rejects_nonfinite_gate_values(
    tmp_path: Path,
    parameter: str,
    value: float,
    message: str,
) -> None:
    candidates_csv, evidence_csv, ddms2_features_csv = _single_seed_inputs(
        tmp_path,
        base_mz=500.0,
        base_rt_apex_min=10.0,
    )
    kwargs: dict[str, object] = {
        "fullscan_candidates_csv": candidates_csv,
        "fullscan_evidence_csv": evidence_csv,
        "ddms2_detected_features_csv": ddms2_features_csv,
        "mz_tolerance_ppm": 20.0,
        "rt_window_sec": 45.0,
    }
    kwargs[parameter] = value

    with pytest.raises(ValueError, match=f"{message}.*finite"):
        run_ddms2_support_replay(**kwargs)


@pytest.mark.parametrize(
    ("flag", "value"),
    (("--mz-tolerance-ppm", "inf"), ("--rt-window-sec", "nan")),
)
def test_ddms2_support_cli_rejects_nonfinite_gate_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    value: str,
) -> None:
    candidates_csv, evidence_csv, ddms2_features_csv = _single_seed_inputs(
        tmp_path,
        base_mz=500.0,
        base_rt_apex_min=10.0,
    )
    _write_detected_features(ddms2_features_csv, [])

    with pytest.raises(SystemExit) as exc_info:
        replay_cli.main(
            [
                "--fullscan-candidates-csv",
                str(candidates_csv),
                "--fullscan-evidence-csv",
                str(evidence_csv),
                "--ddms2-detected-features-csv",
                str(ddms2_features_csv),
                "--output-dir",
                str(tmp_path / "output"),
                flag,
                value,
            ]
        )

    assert exc_info.value.code == 2
    assert "must be > 0 and finite" in capsys.readouterr().err


def _single_seed_inputs(
    tmp_path: Path,
    *,
    base_mz: float,
    base_rt_apex_min: float,
) -> tuple[Path, Path, Path]:
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    evidence_csv = tmp_path / "pt_pattern_evidence.csv"
    ddms2_features_csv = tmp_path / "detected_features.csv"
    _write_csv(
        candidates_csv,
        (
            "sample_stem",
            "candidate_id",
            "base_feature_id",
            "base_mz",
            "base_rt_apex_min",
        ),
        [
            {
                "sample_stem": "fullscan",
                "candidate_id": "seed",
                "base_feature_id": "fullscan_base",
                "base_mz": str(base_mz),
                "base_rt_apex_min": str(base_rt_apex_min),
            },
        ],
    )
    _write_csv(
        evidence_csv,
        ("candidate_id", "trace_rule_call", "trace_detection_status"),
        [
            {
                "candidate_id": "seed",
                "trace_rule_call": "accept",
                "trace_detection_status": "detected",
            },
        ],
    )
    return candidates_csv, evidence_csv, ddms2_features_csv


def _feature_row(
    feature_id: str,
    mz: float,
    rt_apex_min: float,
    height: float,
) -> dict[str, str]:
    return {
        "sample_stem": "ddms2_sample",
        "feature_id": feature_id,
        "source_feature_row_id": feature_id,
        "mz": str(mz),
        "rt_apex_min": str(rt_apex_min),
        "rt_start_min": str(rt_apex_min - 0.01),
        "rt_end_min": str(rt_apex_min + 0.01),
        "height": str(height),
        "area": str(height * 2),
        "trace_quality": "OK",
        "shape_score": "0.9",
        "source": "local_ms1_feature",
    }


def _write_detected_features(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    _write_csv(
        path,
        (
            "sample_stem",
            "feature_id",
            "source_feature_row_id",
            "mz",
            "rt_apex_min",
            "rt_start_min",
            "rt_end_min",
            "height",
            "area",
            "trace_quality",
            "shape_score",
            "source",
        ),
        rows,
    )


def _write_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
