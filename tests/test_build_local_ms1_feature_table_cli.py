from __future__ import annotations

from pathlib import Path

import pytest

from scripts import build_local_ms1_feature_table
from xic_extractor.discovery.local_ms1_features import (
    LocalMs1FeatureOutputs,
    LocalMs1FeatureSettings,
)
from xic_extractor.ms1_scan_index import MS1ScanReadError


def test_cli_builds_local_ms1_feature_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = tmp_path / "Sample_1.raw"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "out"
    raw.write_text("", encoding="utf-8")
    dll_dir.mkdir()
    captured: dict[str, object] = {}

    class _Handle:
        def __enter__(self) -> "_Handle":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def _fake_open_raw(raw_path: Path, dll_path: Path) -> _Handle:
        captured["raw_path"] = raw_path
        captured["dll_dir"] = dll_path
        return _Handle()

    def _fake_build_index(_handle: _Handle) -> tuple[object, ...]:
        captured["build_index"] = True
        return ()

    def _fake_build_table(
        index: tuple[object, ...],
        *,
        sample_stem: str,
        raw_path: str,
        raw_file_size_bytes: int,
        raw_file_mtime_ns: int,
        settings: object,
    ) -> tuple[tuple[object, ...], object, tuple[object, ...]]:
        captured["index"] = index
        captured["sample_stem"] = sample_stem
        captured["raw_path_text"] = raw_path
        captured["raw_file_size_bytes"] = raw_file_size_bytes
        captured["raw_file_mtime_ns"] = raw_file_mtime_ns
        captured["settings"] = settings
        return (), object(), ()

    def _fake_write_outputs(
        out_dir: Path,
        features: tuple[object, ...],
        summary: object,
        diagnostics: tuple[object, ...],
    ) -> LocalMs1FeatureOutputs:
        out_dir.mkdir(parents=True, exist_ok=True)
        detected = out_dir / "detected_features.csv"
        summary_json = out_dir / "local_ms1_feature_summary.json"
        diagnostics_csv = out_dir / "local_ms1_feature_diagnostics.csv"
        detected.write_text("sample_stem\n", encoding="utf-8")
        summary_json.write_text("{}\n", encoding="utf-8")
        diagnostics_csv.write_text("trace_id\n", encoding="utf-8")
        return LocalMs1FeatureOutputs(detected, summary_json, diagnostics_csv)

    monkeypatch.setattr(build_local_ms1_feature_table, "open_raw", _fake_open_raw)
    monkeypatch.setattr(
        build_local_ms1_feature_table,
        "build_ms1_scan_index",
        _fake_build_index,
    )
    monkeypatch.setattr(
        build_local_ms1_feature_table,
        "build_local_ms1_feature_table",
        _fake_build_table,
    )
    monkeypatch.setattr(
        build_local_ms1_feature_table,
        "write_local_ms1_feature_outputs",
        _fake_write_outputs,
    )

    code = build_local_ms1_feature_table.main(
        [
            "--raw",
            str(raw),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--mass-detection-noise-level",
            "50000",
            "--min-trace-seed-intensity",
            "100000",
            "--min-consecutive-trace-intensity",
            "25000",
            "--feature-link-ppm",
            "12.5",
            "--max-missing-scans",
            "2",
            "--min-observed-scans",
            "4",
            "--min-duration-min",
            "0.02",
            "--max-duration-min",
            "1.25",
            "--gaussian-window-points",
            "5",
            "--min-ok-prominence",
            "21.5",
            "--weak-support-prominence",
            "4.5",
            "--max-ok-gap-fraction",
            "0.11",
            "--max-weak-gap-fraction",
            "0.33",
            "--max-ok-mz-ppm-std",
            "8.5",
            "--max-weak-mz-ppm-std",
            "18.5",
            "--optimal-component-max-size",
            "10",
            "--optimal-component-max-edge-density",
            "0.25",
            "--optimal-component-max-edges",
            "17",
            "--split-valley-fraction-of-smaller-apex",
            "0.4",
        ]
    )

    assert code == 0
    assert captured["sample_stem"] == "Sample_1"
    assert captured["raw_file_size_bytes"] == 0
    assert isinstance(captured["raw_file_mtime_ns"], int)
    assert captured["settings"] == LocalMs1FeatureSettings(
        mass_detection_noise_level=50000.0,
        min_trace_seed_intensity=100000.0,
        min_consecutive_trace_intensity=25000.0,
        feature_link_ppm=12.5,
        max_missing_scans=2,
        min_observed_scans=4,
        min_duration_min=0.02,
        max_duration_min=1.25,
        gaussian_window_points=5,
        min_ok_prominence=21.5,
        weak_support_prominence=4.5,
        max_ok_gap_fraction=0.11,
        max_weak_gap_fraction=0.33,
        max_ok_mz_ppm_std=8.5,
        max_weak_mz_ppm_std=18.5,
        optimal_component_max_size=10,
        optimal_component_max_edge_density=0.25,
        optimal_component_max_edges=17,
        split_valley_fraction_of_smaller_apex=0.4,
    )
    stdout = capsys.readouterr().out
    assert "detected_features.csv" in stdout
    assert "local_ms1_feature_summary.json" in stdout
    assert "local_ms1_feature_diagnostics.csv" in stdout


def test_cli_uses_lab_default_mass_detection_noise_level(tmp_path: Path) -> None:
    parser = build_local_ms1_feature_table._build_parser()
    args = parser.parse_args(
        [
            "--raw",
            str(tmp_path / "Sample_1.raw"),
            "--dll-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    settings = build_local_ms1_feature_table._settings_from_args(args, parser)

    assert settings.mass_detection_noise_level == 25_000.0


def test_cli_rejects_missing_raw(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = build_local_ms1_feature_table.main(
        [
            "--raw",
            str(tmp_path / "missing.raw"),
            "--dll-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert code == 2
    assert "RAW file does not exist" in capsys.readouterr().err


def test_cli_reports_ms1_scan_read_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = tmp_path / "Sample_1.raw"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "out"
    raw.write_text("", encoding="utf-8")
    dll_dir.mkdir()

    class _Handle:
        def __enter__(self) -> "_Handle":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        build_local_ms1_feature_table,
        "open_raw",
        lambda *_args: _Handle(),
    )

    def _raise_scan_read_error(_handle: _Handle) -> tuple[object, ...]:
        raise MS1ScanReadError("failed to read MS1 scan 42: synthetic failure")

    monkeypatch.setattr(
        build_local_ms1_feature_table,
        "build_ms1_scan_index",
        _raise_scan_read_error,
    )

    code = build_local_ms1_feature_table.main(
        [
            "--raw",
            str(raw),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 2
    assert "failed to read MS1 scan 42" in capsys.readouterr().err
    assert not output_dir.exists()


def test_cli_rejects_invalid_float_before_settings_construction(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_local_ms1_feature_table.main(
            [
                "--raw",
                str(tmp_path / "missing.raw"),
                "--dll-dir",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "out"),
                "--max-ok-gap-fraction",
                "1.5",
            ]
        )

    assert exc_info.value.code == 2
    assert "--max-ok-gap-fraction must be between 0 and 1" in capsys.readouterr().err


def test_cli_rejects_invalid_int_before_settings_construction(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_local_ms1_feature_table.main(
            [
                "--raw",
                str(tmp_path / "missing.raw"),
                "--dll-dir",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "out"),
                "--optimal-component-max-edges",
                "0",
            ]
        )

    assert exc_info.value.code == 2
    assert "--optimal-component-max-edges must be > 0" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (
            ["--min-ok-prominence", "2", "--weak-support-prominence", "3"],
            "--min-ok-prominence must be >= --weak-support-prominence",
        ),
        (
            ["--max-ok-gap-fraction", "0.5", "--max-weak-gap-fraction", "0.4"],
            "--max-ok-gap-fraction must be <= --max-weak-gap-fraction",
        ),
        (
            ["--max-ok-mz-ppm-std", "20", "--max-weak-mz-ppm-std", "10"],
            "--max-ok-mz-ppm-std must be <= --max-weak-mz-ppm-std",
        ),
    ],
)
def test_cli_rejects_inverted_quality_tiers_before_raw_io(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    message: str,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_local_ms1_feature_table.main(
            [
                "--raw",
                str(tmp_path / "missing.raw"),
                "--dll-dir",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "out"),
                *flags,
            ]
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert message in captured.err
    assert "Traceback" not in captured.err
