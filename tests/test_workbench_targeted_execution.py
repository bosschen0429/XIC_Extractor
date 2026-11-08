"""Targeted execution inputs, publication, and cancellation contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.configuration.models import Target
from xic_workbench import targeted


def test_targeted_run_uses_canonical_service_and_returns_published_two_sample_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xic_workbench.targeted as module

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text("settings", encoding="utf-8")
    (config_dir / "targets.csv").write_text("targets", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    excel_path = output_dir / "xic_results_20261002_1200.xlsx"
    report_path = output_dir / "review_report_20261002_1200.html"
    excel_path.write_text("workbook", encoding="utf-8")
    report_path.write_text("report", encoding="utf-8")
    config = SimpleNamespace(
        output_csv=output_dir / "xic_results.csv",
        count_no_ms2_as_detected=True,
        targeted_ms1_shape_identity_activation_policy="explicit_support_tsv",
    )
    targets = [
        _target("Analyte", istd_pair="ISTD"),
        _target("ISTD", is_istd=True),
    ]
    output = SimpleNamespace(
        file_results=[
            SimpleNamespace(
                results={
                    "Analyte": _result(area=10.0, nl_status="OK", detected=True),
                    "ISTD": _result(area=20.0, nl_status="OK", detected=True),
                }
            ),
            SimpleNamespace(
                results={
                    "Analyte": _result(area=30.0, nl_status="NO_MS2", detected=False),
                    "ISTD": _result(area=60.0, nl_status="NL_FAIL", detected=False),
                }
            ),
        ],
        diagnostics=[object()],
    )
    progress_events: list[tuple[int, int, str]] = []
    captured: dict[str, object] = {}

    class Service:
        def execute_application(self, request):
            captured["request"] = request
            assert request.progress_callback is not None
            request.progress_callback(1, 2, "Sample_A.raw")
            return SimpleNamespace(
                active_output=output,
                published_paths=(excel_path, report_path),
            )

    _substitute_prepared_inputs(monkeypatch, module, config_dir, config, targets)
    monkeypatch.setattr(
        "xic_extractor.applications.targeted.application."
        "build_canonical_targeted_application_service",
        lambda _runner: Service(),
    )
    run = module.TargetedRun(
        config_dir,
        progress=lambda current, total, name: progress_events.append(
            (current, total, name)
        ),
        should_cancel=lambda: False,
    )

    summary = run.execute()

    request = captured["request"]
    assert request.excel_output_path.parent == output_dir
    assert progress_events == [(1, 2, "Sample_A.raw")]
    assert summary["published_paths"] == [str(excel_path), str(report_path)]
    assert summary["total_files"] == 2
    assert summary["diagnostics_count"] == 1
    assert summary["targets"][0] == {
        "label": "Analyte",
        "detected": 1,
        "total": 2,
        "nl_ok": 1,
        "nl_warn": 0,
        "nl_fail": 0,
        "nl_no_ms2": 1,
        "median_area": 20.0,
    }
    assert summary["istd_warnings"] == [{"label": "ISTD", "detected": 1, "total": 2}]
    assert run.available_outputs == (
        ("已發布 Targeted 結果", excel_path),
        ("已發布 Targeted 結果", report_path),
    )


def test_targeted_run_raises_cancellation_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xic_workbench.targeted as module

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text("settings", encoding="utf-8")
    (config_dir / "targets.csv").write_text("targets", encoding="utf-8")
    config = SimpleNamespace(
        output_csv=tmp_path / "output" / "xic_results.csv",
        count_no_ms2_as_detected=False,
        targeted_ms1_shape_identity_activation_policy="explicit_support_tsv",
    )

    class Service:
        def execute_application(self, _request):
            return SimpleNamespace(
                active_output=SimpleNamespace(file_results=[], diagnostics=[]),
                published_paths=(),
            )

    _substitute_prepared_inputs(monkeypatch, module, config_dir, config, [])
    monkeypatch.setattr(
        "xic_extractor.applications.targeted.application."
        "build_canonical_targeted_application_service",
        lambda _runner: Service(),
    )
    run = module.TargetedRun(
        config_dir,
        progress=lambda _current, _total, _name: None,
        should_cancel=lambda: True,
    )

    with pytest.raises(InterruptedError, match="樣本資訊讀取已停止"):
        run.execute()

    assert run.available_outputs == ()
    assert run.cancellation_reason == "樣本資訊讀取已停止。"


def test_targeted_run_keeps_success_when_cancellation_arrives_after_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xic_workbench.targeted as module

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text("settings", encoding="utf-8")
    (config_dir / "targets.csv").write_text("targets", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    published = output_dir / "xic_results_done.xlsx"
    published.write_text("done", encoding="utf-8")
    config = SimpleNamespace(
        output_csv=output_dir / "xic_results.csv",
        count_no_ms2_as_detected=False,
        targeted_ms1_shape_identity_activation_policy="explicit_support_tsv",
    )
    target = _target("Analyte")
    output = SimpleNamespace(
        file_results=[
            SimpleNamespace(
                results={"Analyte": _result(area=10.0, nl_status="OK", detected=True)}
            )
        ],
        diagnostics=[],
    )

    cancelled = False

    class Service:
        def execute_application(self, _request):
            nonlocal cancelled
            cancelled = True
            return SimpleNamespace(active_output=output, published_paths=(published,))

    _substitute_prepared_inputs(monkeypatch, module, config_dir, config, [target])
    monkeypatch.setattr(
        "xic_extractor.applications.targeted.application."
        "build_canonical_targeted_application_service",
        lambda _runner: Service(),
    )
    run = module.TargetedRun(
        config_dir,
        progress=lambda _current, _total, _name: None,
        should_cancel=lambda: cancelled,
    )

    summary = run.execute()

    assert summary["published_paths"] == [str(published)]
    assert summary["total_files"] == 1


def test_targeted_run_preserves_the_canonical_cancellation_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xic_workbench.targeted as module

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text("settings", encoding="utf-8")
    (config_dir / "targets.csv").write_text("targets", encoding="utf-8")
    config = SimpleNamespace(
        output_csv=tmp_path / "output" / "xic_results.csv",
        count_no_ms2_as_detected=False,
        targeted_ms1_shape_identity_activation_policy="explicit_support_tsv",
    )

    class Service:
        def execute_application(self, _request):
            raise InterruptedError("fingerprinting stopped at Sample_B.raw")

    _substitute_prepared_inputs(monkeypatch, module, config_dir, config, [])
    monkeypatch.setattr(
        "xic_extractor.applications.targeted.application."
        "build_canonical_targeted_application_service",
        lambda _runner: Service(),
    )
    run = module.TargetedRun(
        tmp_path / "config",
        progress=lambda _current, _total, _name: None,
        should_cancel=lambda: False,
    )

    with pytest.raises(InterruptedError, match="fingerprinting stopped"):
        run.execute()

    assert run.cancellation_reason == "fingerprinting stopped at Sample_B.raw"


def _substitute_prepared_inputs(monkeypatch, module, config_dir, config, targets):
    """Substitute the admission interface, not parsing internals.

    These adapter tests start with admitted inputs. The neighboring snapshot
    tests use real CSV preparation and verify frozen calculation/manifest bytes.
    RAW membership still crosses the real discovery/check interface here.
    """
    from xic_extractor.configuration.loader import PreparedTargetedConfig

    config.data_dir = config_dir.parent / "raw"
    config.data_dir.mkdir(exist_ok=True)
    config.dll_dir = config_dir.parent / "dll"
    config.injection_order_source = None
    monkeypatch.setattr(
        module.config_loader,
        "prepare_targeted_config",
        lambda *_args, **_kwargs: PreparedTargetedConfig(
            config, tuple(targets), config_dir
        ),
    )


def _target(label: str, *, is_istd: bool = False, istd_pair: str = "") -> Target:
    return Target(
        label=label,
        mz=250.0,
        rt_min=1.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=is_istd,
        istd_pair=istd_pair,
    )


def _result(*, area: float, nl_status: str, detected: bool) -> SimpleNamespace:
    return SimpleNamespace(
        nl=SimpleNamespace(status=nl_status),
        targeted_product_projection=SimpleNamespace(counted_detection=detected),
        peak_result=SimpleNamespace(status="OK", peak=object()),
        reported_peak_area=area,
    )


def test_targeted_run_reads_confirmed_annotations_for_this_run_only(
    tmp_path, monkeypatch
) -> None:
    calls = []

    class Stop(Exception):
        pass

    def prepare(config_dir, **kwargs):
        calls.append(kwargs)
        raise Stop

    monkeypatch.setattr(targeted.config_loader, "prepare_targeted_config", prepare)
    annotations = tmp_path / "confirmed.tsv"
    for value in (annotations, None):
        try:
            targeted.TargetedRun(
                tmp_path,
                sample_annotations=value,
                progress=lambda *_: None,
                should_cancel=lambda: False,
            )
        except Stop:
            pass

    assert calls[0]["settings_overrides"] == {
        "injection_order_source": str(annotations)
    }
    assert calls[1]["settings_overrides"] is None
