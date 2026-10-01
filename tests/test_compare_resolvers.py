from pathlib import Path

from scripts.compare_resolvers import (
    compare_rows,
    main,
)
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.extraction.result_models import (
    ExtractionResult,
    FileResult,
    RunOutput,
)
from xic_extractor.peak_detection.models import PeakDetectionResult, PeakResult


def test_compare_rows_reports_flips_rt_area_istd_and_confidence_changes() -> None:
    savgol_output = RunOutput(
        file_results=[
            FileResult(
                sample_name="SampleA",
                results={
                    "d3-N6-medA": _result(
                        "d3-N6-medA",
                        role="ISTD",
                        rt=8.50,
                        area=1000.0,
                        confidence="HIGH",
                    ),
                    "8-oxo-Guo": _result(
                        "8-oxo-Guo",
                        role="Analyte",
                        rt=None,
                        area=None,
                        confidence="LOW",
                    ),
                    "d3-5-hmdC": _result(
                        "d3-5-hmdC",
                        role="ISTD",
                        rt=9.00,
                        area=5000.0,
                        confidence="MEDIUM",
                    ),
                },
            )
        ],
        diagnostics=[],
    )
    local_output = RunOutput(
        file_results=[
            FileResult(
                sample_name="SampleA",
                results={
                    "d3-N6-medA": _result(
                        "d3-N6-medA",
                        role="ISTD",
                        rt=None,
                        area=None,
                        confidence="VERY_LOW",
                    ),
                    "8-oxo-Guo": _result(
                        "8-oxo-Guo",
                        role="Analyte",
                        rt=14.39,
                        area=2200.0,
                        confidence="MEDIUM",
                    ),
                    "d3-5-hmdC": _result(
                        "d3-5-hmdC",
                        role="ISTD",
                        rt=9.08,
                        area=3000.0,
                        confidence="HIGH",
                    ),
                },
            )
        ],
        diagnostics=[],
    )

    report = compare_rows(
        savgol_output,
        local_output,
        focus_targets={"d3-N6-medA", "d3-5-hmdC", "8-oxo-Guo", "8-oxodG"},
        rt_delta_threshold=0.05,
        area_ratio_threshold=0.20,
    )

    assert report.summary.detected_to_nd == 1
    assert report.summary.nd_to_detected == 1
    assert report.summary.rt_changed == 1
    assert report.summary.area_changed == 1
    assert report.summary.istd_detected_losses == 1
    assert report.summary.confidence_changed == 3
    assert {row.target for row in report.focus_rows} == {
        "d3-N6-medA",
        "d3-5-hmdC",
        "8-oxo-Guo",
    }


def test_compare_resolvers_main_runs_both_modes_and_writes_csv(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import scripts.compare_resolvers as module

    config = _config(tmp_path)
    targets = [
        _target("d3-N6-medA", is_istd=True),
        _target("8-oxo-Guo"),
    ]

    def _load_config(_config_dir: Path):
        return config, targets

    def _run(config_arg, _targets):
        if config_arg.resolver_mode == "savgol":
            return RunOutput(
                file_results=[
                    FileResult(
                        sample_name="SampleA",
                        results={
                            "d3-N6-medA": _result(
                                "d3-N6-medA",
                                role="ISTD",
                                rt=8.50,
                                area=1000.0,
                                confidence="HIGH",
                            ),
                            "8-oxo-Guo": _result(
                                "8-oxo-Guo",
                                role="Analyte",
                                rt=None,
                                area=None,
                                confidence="LOW",
                            ),
                        },
                    )
                ],
                diagnostics=[],
            )
        return RunOutput(
            file_results=[
                FileResult(
                    sample_name="SampleA",
                    results={
                        "d3-N6-medA": _result(
                            "d3-N6-medA",
                            role="ISTD",
                            rt=None,
                            area=None,
                            confidence="VERY_LOW",
                        ),
                        "8-oxo-Guo": _result(
                            "8-oxo-Guo",
                            role="Analyte",
                            rt=14.39,
                            area=2200.0,
                            confidence="MEDIUM",
                        ),
                    },
                )
            ],
            diagnostics=[],
        )

    monkeypatch.setattr(module, "load_config", _load_config)
    monkeypatch.setattr(module.extractor, "acquire", _run)

    output_path = tmp_path / "resolver_compare.csv"
    exit_code = main(
        [
            "--base-dir",
            str(tmp_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert output_path.exists()
    raw_csv = output_path.read_bytes()
    assert raw_csv.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw_csv
    csv_text = output_path.read_text(encoding="utf-8-sig")
    assert csv_text.splitlines()[0].split(",") == [
        "SampleName",
        "Target",
        "Role",
        "Issue",
        "LegacyDetected",
        "LocalDetected",
        "LegacyRT",
        "LocalRT",
        "RTDelta",
        "LegacyArea",
        "LocalArea",
        "AreaRatioDelta",
        "LegacyConfidence",
        "LocalConfidence",
    ]
    assert "SampleA,d3-N6-medA,ISTD,FLIP_DETECTED_TO_ND" in csv_text
    assert "SampleA,8-oxo-Guo,Analyte,FLIP_ND_TO_DETECTED" in csv_text
    stdout = capsys.readouterr().out
    assert "savgol vs local_minimum" in stdout
    assert "Detected->ND: 1" in stdout


def _config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "output" / "xic_results.csv",
        diagnostics_csv=tmp_path / "output" / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target(label: str, *, is_istd: bool = False) -> Target:
    return Target(
        label=label,
        mz=258.1085,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=is_istd,
        istd_pair="",
    )


def _result(
    label: str,
    *,
    role: str,
    rt: float | None,
    area: float | None,
    confidence: str,
) -> ExtractionResult:
    peak = None
    if rt is not None and area is not None:
        peak = PeakResult(
            rt=rt,
            intensity=100.0,
            intensity_smoothed=100.0,
            area=area,
            peak_start=rt - 0.05,
            peak_end=rt + 0.05,
        )
    return ExtractionResult(
        peak_result=PeakDetectionResult(
            status="OK" if peak is not None else "PEAK_NOT_FOUND",
            peak=peak,
            n_points=100,
            max_smoothed=100.0 if peak is not None else None,
            n_prominent_peaks=1 if peak is not None else 0,
        ),
        nl=None,
        target_label=label,
        role=role,
        confidence=confidence,
        reason="",
    )
