"""Contract tests for the Pt pattern discovery CLI.

Covers ``scripts.run_pt_pattern_discovery.main()``. Unlike the neutral-loss
discovery CLI, this CLI takes a Detected Features CSV (not a RAW file / DLL
directory) as input, so it has no RAW/DLL requirement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import run_pt_pattern_discovery
from xic_extractor.discovery.delta_mass_pipeline import DeltaMassDiscoveryOutputs

DETECTED_FEATURES_HEADER = (
    "sample_stem,feature_id,source_feature_row_id,mz,rt_apex_min,rt_start_min,"
    "rt_end_min,height,area,trace_quality,shape_score,source\r\n"
)


def _write_detected_features_csv(path: Path) -> None:
    row = "Sample_1,base-1,,500.0,10.00,,,1000.0,500.0,GOOD,0.9,targeted\r\n"
    path.write_text(DETECTED_FEATURES_HEADER + row, encoding="utf-8")


def test_cli_accepts_pt_pattern_preset_with_no_raw_or_dll_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv)
    output_dir = tmp_path / "out"
    captured = {}

    def _fake_run_pt_pattern_discovery(
        *,
        detected_features_csv,
        output_dir,
        preset_name,
        cid_nl_candidates_csv,
        trace_summary_csv,
    ):
        captured["detected_features_csv"] = detected_features_csv
        captured["output_dir"] = output_dir
        captured["preset_name"] = preset_name
        captured["cid_nl_candidates_csv"] = cid_nl_candidates_csv
        captured["trace_summary_csv"] = trace_summary_csv
        output_dir.mkdir(parents=True, exist_ok=True)
        candidates_csv = output_dir / "pt_pattern_candidates.csv"
        review_csv = output_dir / "pt_pattern_review.csv"
        evidence_csv = output_dir / "pt_pattern_evidence.csv"
        sidecar_json = output_dir / "pt_pattern_sidecar.json"
        candidates_csv.write_text("schema_version\n", encoding="utf-8")
        review_csv.write_text("review_priority\n", encoding="utf-8")
        evidence_csv.write_text("schema_version\n", encoding="utf-8")
        sidecar_json.write_text("{}", encoding="utf-8")
        return DeltaMassDiscoveryOutputs(
            candidates_csv=candidates_csv,
            review_csv=review_csv,
            evidence_csv=evidence_csv,
            sidecar_json=sidecar_json,
        )

    monkeypatch.setattr(
        run_pt_pattern_discovery,
        "run_pt_pattern_discovery",
        _fake_run_pt_pattern_discovery,
    )

    code = run_pt_pattern_discovery.main(
        [
            "--detected-features-csv",
            str(detected_features_csv),
            "--output-dir",
            str(output_dir),
            "--preset",
            "pt_pattern",
        ]
    )

    assert code == 0
    assert captured["preset_name"] == "pt_pattern"
    assert captured["cid_nl_candidates_csv"] is None
    assert captured["trace_summary_csv"] is None
    stdout = capsys.readouterr().out
    assert "pt_pattern_candidates.csv" in stdout
    assert "pt_pattern_review.csv" in stdout
    assert "pt_pattern_evidence.csv" in stdout
    assert "pt_pattern_sidecar.json" in stdout


def test_cli_accepts_pt_pattern_cid_nl_dr_preset_with_composite_csv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv)
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text("ms1_feature_row_id\n", encoding="utf-8")
    output_dir = tmp_path / "out"
    captured = {}

    def _fake_run_pt_pattern_discovery(
        *,
        detected_features_csv,
        output_dir,
        preset_name,
        cid_nl_candidates_csv,
        trace_summary_csv,
    ):
        captured["preset_name"] = preset_name
        captured["cid_nl_candidates_csv"] = cid_nl_candidates_csv
        captured["trace_summary_csv"] = trace_summary_csv
        output_dir.mkdir(parents=True, exist_ok=True)
        candidates_csv = output_dir / "pt_pattern_candidates.csv"
        review_csv = output_dir / "pt_pattern_review.csv"
        evidence_csv = output_dir / "pt_pattern_evidence.csv"
        sidecar_json = output_dir / "pt_pattern_sidecar.json"
        candidates_csv.write_text("schema_version\n", encoding="utf-8")
        review_csv.write_text("review_priority\n", encoding="utf-8")
        evidence_csv.write_text("schema_version\n", encoding="utf-8")
        sidecar_json.write_text("{}", encoding="utf-8")
        return DeltaMassDiscoveryOutputs(
            candidates_csv=candidates_csv,
            review_csv=review_csv,
            evidence_csv=evidence_csv,
            sidecar_json=sidecar_json,
        )

    monkeypatch.setattr(
        run_pt_pattern_discovery,
        "run_pt_pattern_discovery",
        _fake_run_pt_pattern_discovery,
    )

    code = run_pt_pattern_discovery.main(
        [
            "--detected-features-csv",
            str(detected_features_csv),
            "--output-dir",
            str(output_dir),
            "--preset",
            "pt_pattern_cid_nl_dr",
            "--cid-nl-candidates-csv",
            str(cid_nl_candidates_csv),
        ]
    )

    assert code == 0
    assert captured["preset_name"] == "pt_pattern_cid_nl_dr"
    assert captured["cid_nl_candidates_csv"] == cid_nl_candidates_csv.resolve()
    assert captured["trace_summary_csv"] is None


def test_cli_accepts_optional_trace_summary_csv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv)
    trace_summary_csv = tmp_path / "trace_summary.csv"
    trace_summary_csv.write_text("schema_version,candidate_id\n", encoding="utf-8")
    output_dir = tmp_path / "out"
    captured = {}

    def _fake_run_pt_pattern_discovery(
        *,
        detected_features_csv,
        output_dir,
        preset_name,
        cid_nl_candidates_csv,
        trace_summary_csv,
    ):
        captured["trace_summary_csv"] = trace_summary_csv
        output_dir.mkdir(parents=True, exist_ok=True)
        candidates_csv = output_dir / "pt_pattern_candidates.csv"
        review_csv = output_dir / "pt_pattern_review.csv"
        evidence_csv = output_dir / "pt_pattern_evidence.csv"
        sidecar_json = output_dir / "pt_pattern_sidecar.json"
        candidates_csv.write_text("schema_version\n", encoding="utf-8")
        review_csv.write_text("review_priority\n", encoding="utf-8")
        evidence_csv.write_text("schema_version\n", encoding="utf-8")
        sidecar_json.write_text("{}", encoding="utf-8")
        return DeltaMassDiscoveryOutputs(
            candidates_csv=candidates_csv,
            review_csv=review_csv,
            evidence_csv=evidence_csv,
            sidecar_json=sidecar_json,
        )

    monkeypatch.setattr(
        run_pt_pattern_discovery,
        "run_pt_pattern_discovery",
        _fake_run_pt_pattern_discovery,
    )

    code = run_pt_pattern_discovery.main(
        [
            "--detected-features-csv",
            str(detected_features_csv),
            "--output-dir",
            str(output_dir),
            "--preset",
            "pt_pattern",
            "--trace-summary-csv",
            str(trace_summary_csv),
        ]
    )

    assert code == 0
    assert captured["trace_summary_csv"] == trace_summary_csv.resolve()


def test_cli_rejects_composite_preset_without_cid_nl_candidates_csv(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv)

    with pytest.raises(SystemExit) as exc_info:
        run_pt_pattern_discovery.main(
            [
                "--detected-features-csv",
                str(detected_features_csv),
                "--preset",
                "pt_pattern_cid_nl_dr",
            ]
        )

    assert exc_info.value.code == 2
    assert "--cid-nl-candidates-csv" in capsys.readouterr().err


def test_cli_does_not_declare_any_raw_or_dll_arguments() -> None:
    parser_help = run_pt_pattern_discovery._build_parser().format_help()

    assert "--raw" not in parser_help
    assert "--dll-dir" not in parser_help


def test_cli_rejects_missing_detected_features_csv(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_pt_pattern_discovery.main(
        [
            "--detected-features-csv",
            str(tmp_path / "missing.csv"),
            "--preset",
            "pt_pattern",
        ]
    )

    assert code == 2
    assert "detected features csv does not exist" in capsys.readouterr().err


def test_cli_rejects_missing_trace_summary_csv(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv)

    code = run_pt_pattern_discovery.main(
        [
            "--detected-features-csv",
            str(detected_features_csv),
            "--preset",
            "pt_pattern",
            "--trace-summary-csv",
            str(tmp_path / "missing_trace_summary.csv"),
        ]
    )

    assert code == 2
    assert "trace summary csv does not exist" in capsys.readouterr().err
