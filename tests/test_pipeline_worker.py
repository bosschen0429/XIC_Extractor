from pathlib import Path

import pytest

from gui.workers.pipeline_worker import PipelineWorker
from xic_extractor.config import ConfigError, ExtractionConfig, Target
from xic_extractor.extractor import ExtractionResult, FileResult, RunOutput
from xic_extractor.neutral_loss import NLResult
from xic_extractor.raw_reader import RawReaderError
from xic_extractor.signal_processing import PeakDetectionResult, PeakResult


@pytest.fixture(autouse=True)
def _isolate_gui_shell_from_production_workflow(monkeypatch):
    """Keep GUI signaling tests focused; production composition is tested separately."""

    module = _module()

    class _Service:
        def execute_application(self, request):
            output = module.extractor.run(
                request.config,
                list(request.targets),
                progress_callback=request.progress_callback,
                should_stop=request.should_stop,
                injection_order=request.injection_order,
                rt_prior_library=request.rt_prior_library,
                model_selection_expected_diff_approvals=(
                    request.model_selection_expected_diff_approvals
                ),
            )
            if request.should_stop is None or not request.should_stop():
                module.write_excel_from_run_output(
                    request.config,
                    list(request.targets),
                    output,
                    output_path=request.excel_output_path,
                )
            return type(
                "Execution", (), {"active_output": output, "published_paths": ()}
            )()

    from xic_extractor.output.excel_pipeline import write_excel_from_run_output

    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        write_excel_from_run_output,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "build_canonical_targeted_application_service",
        lambda _runner: _Service(),
    )


def test_worker_runs_shared_pipeline_and_emits_structured_summary(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    config_dir = tmp_path / "config"
    config = _config(tmp_path, count_no_ms2_as_detected=True)
    targets = [_target("Analyte", istd_pair="ISTD"), _target("ISTD", is_istd=True)]
    output = RunOutput(
        file_results=[
            _file_result(
                "Tumor_1",
                {
                    "Analyte": _result(
                        area=10_000.0, nl=NLResult("OK", 1.0, None, 2, 0, 1)
                    ),
                    "ISTD": _result(
                        area=20_000.0, nl=NLResult("OK", 1.0, None, 2, 0, 1)
                    ),
                },
            ),
            _file_result(
                "Tumor_2",
                {
                    "Analyte": _result(
                        area=30_000.0, nl=NLResult("NO_MS2", None, None, 2, 0, 0)
                    ),
                    "ISTD": _result(
                        area=60_000.0,
                        nl=NLResult("NL_FAIL", None, None, 2, 0, 1),
                    ),
                },
            ),
        ],
        diagnostics=[],
    )
    calls: dict[str, object] = {}

    def _load_config(actual_config_dir: Path) -> tuple[ExtractionConfig, list[Target]]:
        calls["config_dir"] = actual_config_dir
        return config, targets

    def _extract(
        actual_config: ExtractionConfig,
        actual_targets: list[Target],
        progress_callback=None,
        should_stop=None,
        injection_order=None,
        rt_prior_library=None,
        model_selection_expected_diff_approvals=None,
    ) -> RunOutput:
        calls["extract_config"] = actual_config
        calls["extract_targets"] = actual_targets
        calls["should_stop"] = should_stop
        calls["injection_order"] = injection_order
        calls["rt_prior_library"] = rt_prior_library
        calls["model_selection_expected_diff_approvals"] = (
            model_selection_expected_diff_approvals
        )
        assert progress_callback is not None
        assert should_stop is not None
        progress_callback(1, 2, "Tumor_1.raw")
        return output

    def _excel(
        actual_config: ExtractionConfig,
        actual_targets: list[Target],
        actual_output: RunOutput,
        *,
        output_path: Path,
    ) -> Path:
        calls["excel_config"] = actual_config
        calls["excel_targets"] = actual_targets
        calls["excel_output"] = actual_output
        calls["excel_output_path"] = output_path
        return output_path

    monkeypatch.setattr(module.config_module, "load_config", _load_config)
    monkeypatch.setattr(module.extractor, "run", _extract)
    monkeypatch.setattr(module, "write_excel_from_run_output", _excel)
    worker = PipelineWorker(config_dir)
    emissions: dict[str, object] = {}
    worker.progress.connect(lambda *args: emissions.setdefault("progress", args))
    worker.finished.connect(lambda summary: emissions.setdefault("summary", summary))

    worker.run()

    assert calls["config_dir"] == config_dir
    assert calls["extract_config"] is config
    assert calls["extract_targets"] == targets
    assert calls["excel_config"] is config
    assert calls["excel_targets"] == targets
    assert calls["excel_output"] is output
    assert Path(calls["excel_output_path"]).parent == config.output_csv.parent
    assert Path(calls["excel_output_path"]).name.startswith("xic_results_")
    assert Path(calls["excel_output_path"]).suffix == ".xlsx"
    assert emissions["progress"] == (1, 2, "Tumor_1.raw")
    summary = emissions["summary"]
    assert summary["total_files"] == 2
    assert summary["excel_path"].endswith(".xlsx")
    assert summary["diagnostics_count"] == 0
    analyte = summary["targets"][0]
    assert analyte == {
        "label": "Analyte",
        "detected": 2,
        "total": 2,
        "nl_ok": 1,
        "nl_warn": 0,
        "nl_fail": 0,
        "nl_no_ms2": 1,
        "median_area": 20_000.0,
    }
    assert summary["istd_warnings"] == [{"label": "ISTD", "detected": 1, "total": 2}]


def test_worker_should_stop_reflects_interruption(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    config = _config(tmp_path)
    target = _target("Analyte")
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        module.config_module,
        "load_config",
        lambda _path: (config, [target]),
    )
    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        lambda *_args, **_kwargs: tmp_path / "out.xlsx",
    )

    worker = PipelineWorker(tmp_path / "config")
    interrupted = {"value": False}
    monkeypatch.setattr(worker, "isInterruptionRequested", lambda: interrupted["value"])

    def _extract(*_args, should_stop=None, **_kwargs) -> RunOutput:
        calls["before_stop"] = should_stop()
        interrupted["value"] = True
        calls["after_stop"] = should_stop()
        return RunOutput(file_results=[], diagnostics=[])

    monkeypatch.setattr(module.extractor, "run", _extract)

    worker.run()

    assert calls == {"before_stop": False, "after_stop": True}


def test_worker_does_not_write_excel_or_emit_summary_after_cancellation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    config = _config(tmp_path)
    target = _target("Analyte")
    interrupted = {"value": False}
    wrote_excel: list[bool] = []
    summaries: list[dict] = []
    cancelled: list[bool] = []

    monkeypatch.setattr(
        module.config_module,
        "load_config",
        lambda _path: (config, [target]),
    )

    def _extract(*_args, **_kwargs) -> RunOutput:
        interrupted["value"] = True
        return RunOutput(file_results=[], diagnostics=[])

    def _excel(*_args, **_kwargs) -> Path:
        wrote_excel.append(True)
        return tmp_path / "out.xlsx"

    worker = PipelineWorker(tmp_path / "config")
    monkeypatch.setattr(worker, "isInterruptionRequested", lambda: interrupted["value"])
    monkeypatch.setattr(module.extractor, "run", _extract)
    monkeypatch.setattr(module, "write_excel_from_run_output", _excel)
    worker.finished.connect(summaries.append)
    worker.cancelled.connect(lambda: cancelled.append(True))

    worker.run()

    assert wrote_excel == []
    assert summaries == []
    assert cancelled == [True]


def test_worker_emits_finished_when_interruption_follows_successful_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    config = _config(tmp_path)
    target = _target("Analyte")
    output = RunOutput(file_results=[], diagnostics=[])
    interrupted = {"value": False}
    summaries: list[dict] = []
    cancelled: list[bool] = []

    monkeypatch.setattr(
        module.config_module,
        "load_config",
        lambda _path: (config, [target]),
    )

    class _Service:
        def execute_application(self, _request):
            interrupted["value"] = True
            return type(
                "Execution",
                (),
                {
                    "active_output": output,
                    "published_paths": (config.output_csv,),
                },
            )()

    monkeypatch.setattr(
        module,
        "build_canonical_targeted_application_service",
        lambda _runner: _Service(),
    )
    worker = PipelineWorker(tmp_path / "config")
    monkeypatch.setattr(worker, "isInterruptionRequested", lambda: interrupted["value"])
    worker.finished.connect(summaries.append)
    worker.cancelled.connect(lambda: cancelled.append(True))

    worker.run()

    assert len(summaries) == 1
    assert cancelled == []


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (ConfigError("settings.csv missing"), "設定檔錯誤：settings.csv missing"),
        (
            RawReaderError("pythonnet is not installed"),
            "Raw file 讀取失敗：pythonnet is not installed",
        ),
    ],
)
def test_worker_emits_user_friendly_errors(
    tmp_path: Path, monkeypatch, exception: Exception, expected: str
) -> None:
    module = _module()
    monkeypatch.setattr(
        module.config_module,
        "load_config",
        lambda _path: (_ for _ in ()).throw(exception),
    )
    worker = PipelineWorker(tmp_path / "config")
    errors: list[str] = []
    worker.error.connect(errors.append)

    worker.run()

    assert errors == [expected]


def test_stop_requests_interruption(tmp_path: Path, monkeypatch) -> None:
    worker = PipelineWorker(tmp_path / "config")
    called: list[bool] = []
    monkeypatch.setattr(worker, "requestInterruption", lambda: called.append(True))

    worker.stop()

    assert called == [True]


def test_main_window_run_uses_user_writable_config_dir(
    tmp_path: Path,
    monkeypatch,
    qtbot,
) -> None:
    # The run logic lives in TargetedView now; MainWindow wires it with the
    # user-writable _ROOT/config dir, so monkeypatch the targeted_view module.
    import gui.main_window as module
    import gui.views.targeted_view as targeted_module

    created: list[Path] = []
    data_dir = tmp_path / "data"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir()
    dll_dir.mkdir()

    class FakeWorker:
        progress = _Signal()
        finished = _Signal()
        error = _Signal()
        cancelled = _Signal()

        def __init__(self, config_dir: Path) -> None:
            created.append(config_dir)

        def start(self) -> None:
            pass

    monkeypatch.setattr(targeted_module, "PipelineWorker", FakeWorker)
    monkeypatch.setattr(
        targeted_module,
        "read_settings",
        lambda: {"data_dir": str(data_dir), "dll_dir": str(dll_dir)},
    )
    monkeypatch.setattr(targeted_module, "read_targets", lambda: [])
    monkeypatch.setattr(targeted_module, "write_settings", lambda _settings: None)
    monkeypatch.setattr(targeted_module, "write_targets", lambda _targets: None)
    window = module.MainWindow()
    qtbot.addWidget(window)

    window.targeted_view._on_run()

    assert created == [module._ROOT / "config"]

    # The fake worker is not a real thread; clear it so qtbot's teardown close
    # does not see a "busy" view and block on the confirm-close modal.
    window.targeted_view._worker = None


def test_main_window_does_not_start_worker_when_parallel_settings_are_invalid(
    monkeypatch, qtbot
) -> None:
    import gui.main_window as module
    import gui.views.targeted_view as targeted_module

    created: list[Path] = []
    saved: list[dict[str, str]] = []

    class FakeWorker:
        progress = _Signal()
        finished = _Signal()
        error = _Signal()
        cancelled = _Signal()

        def __init__(self, config_dir: Path) -> None:
            created.append(config_dir)

        def start(self) -> None:
            pass

    monkeypatch.setattr(targeted_module, "PipelineWorker", FakeWorker)
    monkeypatch.setattr(
        targeted_module,
        "read_settings",
        lambda: {
            "data_dir": "C:\\data",
            "dll_dir": "C:\\dll",
            "smooth_window": "15",
            "smooth_polyorder": "3",
            "peak_rel_height": "0.95",
            "peak_min_prominence_ratio": "0.10",
            "ms2_precursor_tol_da": "0.5",
            "nl_min_intensity_ratio": "0.01",
            "count_no_ms2_as_detected": "false",
            "parallel_mode": "proces",
            "parallel_workers": "0",
        },
    )
    monkeypatch.setattr(targeted_module, "read_targets", lambda: [])
    monkeypatch.setattr(
        targeted_module, "write_settings", lambda settings: saved.append(settings)
    )
    monkeypatch.setattr(targeted_module, "write_targets", lambda _targets: None)
    window = module.MainWindow()
    qtbot.addWidget(window)

    window.targeted_view._on_run()

    assert saved == []
    assert created == []
    assert "設定值無效" in window.statusBar().currentMessage()


def test_main_window_load_config_persists_first_run_settings_migration(
    tmp_path: Path,
    monkeypatch,
    qtbot,
) -> None:
    # MainWindow construction calls targeted_view.load_config(), which migrates
    # legacy setting keys and persists them on first run.
    import gui.main_window as module
    import gui.views.targeted_view as targeted_module

    saved: list[dict[str, str]] = []
    data_dir = tmp_path / "data"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir()
    dll_dir.mkdir()
    monkeypatch.setattr(
        targeted_module,
        "read_settings",
        lambda: {
            "data_dir": str(data_dir),
            "dll_dir": str(dll_dir),
            "smooth_points": "19",
            "smooth_sigma": "3.0",
        },
    )
    monkeypatch.setattr(targeted_module, "read_targets", lambda: [])
    monkeypatch.setattr(
        targeted_module,
        "write_settings",
        lambda settings: saved.append(settings),
    )
    window = module.MainWindow()
    qtbot.addWidget(window)

    assert len(saved) == 1
    assert saved[0]["smooth_window"] == "19"
    assert "smooth_points" not in saved[0]
    assert "smooth_sigma" not in saved[0]


def _module():
    import gui.workers.pipeline_worker as module

    return module


def _config(
    tmp_path: Path,
    *,
    count_no_ms2_as_detected: bool = False,
) -> ExtractionConfig:
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
        count_no_ms2_as_detected=count_no_ms2_as_detected,
    )


def _target(
    label: str,
    *,
    is_istd: bool = False,
    istd_pair: str = "",
) -> Target:
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
        istd_pair=istd_pair,
    )


def _file_result(sample_name: str, results: dict[str, ExtractionResult]) -> FileResult:
    return FileResult(sample_name=sample_name, results=results)


def _result(area: float, nl: NLResult) -> ExtractionResult:
    return ExtractionResult(
        peak_result=PeakDetectionResult(
            status="OK",
            peak=PeakResult(
                rt=9.1,
                intensity=1000.0,
                intensity_smoothed=900.0,
                area=area,
                peak_start=8.9,
                peak_end=9.3,
            ),
            n_points=20,
            max_smoothed=900.0,
            n_prominent_peaks=1,
        ),
        nl=nl,
    )


class _Signal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)
