from __future__ import annotations

import zipfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from gui.sections.discovery_method_section import POSITIVE_DISCOVERY_OVERRIDE_FIELDS


@pytest.mark.parametrize(
    "outcome",
    [
        "missing_dll", "worker_failed", "publication_failed",
        "unwritable_output", "cancelled", "succeeded",
    ],
)
def test_real_discovery_view_restores_controls_and_preserves_run_problem(
    outcome, tmp_path, monkeypatch, qtbot
) -> None:
    import gui.views.untargeted_view as view_module
    import gui.workers.discovery_worker as worker_module
    from xic_extractor.discovery.pipeline import (
        DiscoveryProcessExecutionError,
        DiscoverySampleProcessError,
    )
    from xic_extractor.raw_reader import RawReaderError, preflight_raw_reader

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_path = raw_dir / "sample.raw"
    raw_path.write_bytes(b"synthetic input")
    dll_dir = tmp_path / "empty-dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "requested-output"
    stale_matrix = output_dir / "alignment_matrix.tsv"
    if outcome == "unwritable_output":
        output_dir.write_bytes(b"a file blocks the requested output directory")
    else:
        output_dir.mkdir()
        stale_matrix.write_text("previous attempt", encoding="utf-8")
    request = worker_module.DiscoveryRequest(
        mode="discovery_only", preset="dna_dr", tuning_overrides={},
        raw_dir=raw_dir, raw_file=None, dll_dir=dll_dir,
        output_dir=output_dir, discovery_batch_index=None,
    )
    monkeypatch.setattr(view_module, "read_discovery_config", lambda: {})
    monkeypatch.setattr(view_module, "write_discovery_config", lambda config: None)
    if outcome != "missing_dll":
        monkeypatch.setattr(
            worker_module, "_raw_paths_from_dir", lambda *args: (raw_path,)
        )
    output_errors = []

    def batch(*args, output_dir, progress_callback, **kwargs):
        progress_callback(0, 1, raw_path.name)
        if outcome == "missing_dll":
            errors = preflight_raw_reader(
                dll_dir,
                import_module_func=lambda name: SimpleNamespace(
                    get_runtime_info=lambda: None
                ),
            )
            raise RawReaderError(" ".join(errors))
        if outcome == "worker_failed":
            raise DiscoveryProcessExecutionError(
                DiscoverySampleProcessError(
                    0, "sample", str(raw_path), "sample worker lost"
                ),
                completed_sample_stems=(), failed_sample_stems=("sample",),
                cancelled_job_count=0, in_flight_job_count=0, unsubmitted_job_count=0,
            )
        if outcome == "publication_failed":
            raise RuntimeError("Discovery publication aborted")
        if outcome == "cancelled":
            raise InterruptedError("Discovery cancelled before batch index publication")
        try:
            output_dir.mkdir(exist_ok=True)
        except OSError as exc:
            output_errors.append(str(exc))
            raise
        index_path = output_dir / "discovery_batch_index.csv"
        index_path.write_text(
            "sample,high_count,medium_count,low_count,candidate_count\n"
            "sample.raw,0,0,0,0\n", encoding="utf-8"
        )
        return SimpleNamespace(batch_index_csv=index_path)

    monkeypatch.setattr(worker_module, "run_discovery_batch", batch)
    view = view_module.UntargetedView()
    qtbot.addWidget(view)
    view.resize(1000, 800)
    view.show()
    monkeypatch.setattr(view._source, "is_valid", lambda: True)
    monkeypatch.setattr(view._source, "missing_fields", lambda: [])
    monkeypatch.setattr(view._method, "is_valid", lambda: True)
    monkeypatch.setattr(view, "_build_request", lambda config: request)
    view.trigger_run()
    worker = view._worker
    assert worker is not None
    try:
        assert worker.wait(5000)
        qtbot.wait(1)
        assert not view.is_busy()
        assert view._source.isEnabled() and view._method.isEnabled()
        assert view._run._button.text() == "開始執行"
        assert view._run._button.isEnabled()
        if outcome == "succeeded":
            assert view._terminal_state == "完成"
            assert request.output_dir.joinpath("discovery_batch_index.csv").is_file()
            assert not view._results._open_matrix.isEnabled()
            return
        message = view._results._error.text()
        assert view._results._error.isVisible()
        assert str(output_dir) in message
        assert worker.stage in message
        assert "本次可用產物：無" in message
        assert "attempt" in message and "下一步" in message
        assert str(stale_matrix) not in message
        assert not view._results._open_matrix.isEnabled()
        assert view._run._status_label.toolTip() in message
        if outcome == "missing_dll":
            assert "ThermoFisher.CommonCore.RawFileReader.dll" in message
            assert str(dll_dir) in message and "open Settings" in message
        elif outcome == "worker_failed":
            assert "sample worker lost" in message
        elif outcome == "publication_failed":
            assert "Discovery publication aborted" in message
        elif outcome == "unwritable_output":
            assert len(output_errors) == 1 and output_errors[0] in message
        else:
            assert view._terminal_state == "已停止"
            assert "Discovery cancelled before batch index publication" in message
            assert view._results._title.text() == "本次執行已停止"
    finally:
        worker.stop()
        assert worker.wait(5000)
        view._worker = None


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_alignment_keeps_only_current_discovery_intermediate(
    cancelled, tmp_path, monkeypatch, qtbot
) -> None:
    import gui.views.untargeted_view as view_module
    import gui.workers.discovery_worker as worker_module

    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"synthetic input")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    stale_matrix = output_dir / "alignment_matrix.tsv"
    stale_matrix.write_text("previous attempt", encoding="utf-8")
    index_path = output_dir / "current-discovery.csv"
    index_path.write_text(
        "sample,high_count,medium_count,low_count,candidate_count\n"
        "sample.raw,0,0,0,0\n", encoding="utf-8"
    )
    request = worker_module.DiscoveryRequest(
        mode="full", preset="dna_dr", tuning_overrides={},
        raw_dir=tmp_path, raw_file=None, dll_dir=tmp_path,
        output_dir=output_dir, discovery_batch_index=None,
    )
    monkeypatch.setattr(view_module, "read_discovery_config", lambda: {})
    monkeypatch.setattr(view_module, "write_discovery_config", lambda config: None)
    monkeypatch.setattr(worker_module, "_raw_paths_from_dir", lambda *args: (raw_path,))
    monkeypatch.setattr(
        worker_module, "run_discovery_batch",
        lambda *args, **kwargs: SimpleNamespace(batch_index_csv=index_path),
    )
    reason = (
        "Alignment cancelled before Matrix publication"
        if cancelled else "Matrix publication failed"
    )

    def fail_alignment(*args, **kwargs):
        raise InterruptedError(reason) if cancelled else RuntimeError(reason)

    monkeypatch.setattr(worker_module, "run_untargeted_application", fail_alignment)
    view = view_module.UntargetedView()
    qtbot.addWidget(view)
    view.show()
    monkeypatch.setattr(view._source, "is_valid", lambda: True)
    monkeypatch.setattr(view._source, "missing_fields", lambda: [])
    monkeypatch.setattr(view._method, "is_valid", lambda: True)
    monkeypatch.setattr(view, "_build_request", lambda config: request)
    view.trigger_run()
    worker = view._worker
    assert worker is not None
    try:
        assert worker.wait(5000)
        qtbot.wait(1)
        assert not view.is_busy()
        message = view._results._error.text()
        assert reason in message and "Alignment" in message
        assert str(index_path) in message and "中間產物" in message
        assert str(stale_matrix) not in message
        assert not view._results._open_matrix.isEnabled()
        assert view._source.isEnabled() and view._method.isEnabled()
    finally:
        worker.stop()
        assert worker.wait(5000)
        view._worker = None


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "succeeded"])
def test_discovery_stop_preserves_worker_terminal_outcome(
    outcome, tmp_path, monkeypatch, qtbot
) -> None:
    import gui.views.untargeted_view as view_module
    import gui.workers.discovery_worker as worker_module
    from gui.workers.discovery_worker import DiscoveryRequest
    from xic_extractor.discovery.pipeline import (
        DiscoveryProcessExecutionError,
        DiscoverySampleProcessError,
    )

    started, release = Event(), Event()
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"synthetic input")
    request = DiscoveryRequest(
        mode="discovery_only", preset="dna_dr", tuning_overrides={},
        raw_dir=tmp_path, raw_file=None, dll_dir=tmp_path,
        output_dir=tmp_path / "out", discovery_batch_index=None,
    )
    monkeypatch.setattr(view_module, "read_discovery_config", lambda: {})
    monkeypatch.setattr(view_module, "write_discovery_config", lambda config: None)
    monkeypatch.setattr(worker_module, "_raw_paths_from_dir", lambda *args: (raw_path,))
    index_path = request.output_dir / "discovery_batch_index.csv"

    def batch(*args, should_cancel, progress_callback, **kwargs):
        if outcome == "succeeded":
            # Publication has committed before the user requests cancellation.
            index_path.parent.mkdir()
            index_path.write_text(
                "sample,high_count,medium_count,low_count,candidate_count\n"
                "sample.raw,0,0,0,0\n", encoding="utf-8"
            )
        progress_callback(1, 1, raw_path.stem)
        started.set()
        assert release.wait(5)
        assert should_cancel()
        if outcome == "failed":
            raise DiscoveryProcessExecutionError(
                DiscoverySampleProcessError(0, "sample", "sample.raw", "read failed"),
                completed_sample_stems=(), failed_sample_stems=("sample",),
                cancelled_job_count=0, in_flight_job_count=0, unsubmitted_job_count=0,
            )
        if outcome == "succeeded":
            return SimpleNamespace(batch_index_csv=index_path)
        raise InterruptedError("Discovery cancelled before batch index publication")

    monkeypatch.setattr(worker_module, "run_discovery_batch", batch)
    view = view_module.UntargetedView()
    qtbot.addWidget(view)
    monkeypatch.setattr(view._source, "is_valid", lambda: True)
    monkeypatch.setattr(view._method, "is_valid", lambda: True)
    monkeypatch.setattr(view, "_build_request", lambda config: request)
    view.trigger_run()
    worker = view._worker
    assert worker is not None
    stop = worker.stop

    def release_after_stop():
        stop()
        release.set()

    monkeypatch.setattr(worker, "stop", release_after_stop)
    try:
        assert started.wait(5)
        qtbot.wait(1)
        assert "sample" in view._run._status_label.text()
        view.trigger_stop(confirm=False)
        assert worker.wait(5000)
        qtbot.wait(1)
        assert not view.is_busy()
        assert view._source.isEnabled()
        assert view._method.isEnabled()
        assert view._run._button.text() == "開始執行"
        if outcome == "failed":
            assert "read failed" in view._run._status_label.text()
            assert view._terminal_state == "執行失敗"
        elif outcome == "cancelled":
            assert view._terminal_state == "已停止"
        else:
            assert view._terminal_state == "完成"
            assert index_path.is_file()
    finally:
        release.set()
        worker.wait(5000)


class _SlowStoppingWorker:
    def __init__(self) -> None:
        self.stop_called = False
        self.stage = "Alignment"
        self.output_dir = Path("requested-output")
        self.available_outputs = ()
        self.cancellation_reason = "使用者要求停止"

    def stop(self) -> None:
        self.stop_called = True

    def wait(self, _timeout_ms: int) -> bool:
        return False


def test_completion_survives_readiness_refresh(monkeypatch, qtbot) -> None:
    import gui.views.untargeted_view as module

    monkeypatch.setattr(module, "read_discovery_config", lambda: {})
    view = module.UntargetedView()
    qtbot.addWidget(view)
    states: list[str] = []
    view.state_changed.connect(states.append)
    worker = _SlowStoppingWorker()
    view._worker = worker
    view._run.set_running(True)
    view._on_finished(worker, {"mode": "full", "sample_count": 3})
    view.emit_state()

    assert not view.is_busy()
    assert states[-1] == "完成"
    assert "完成" in view._run._status_label.text()


def test_completion_starts_default_inspector_report(monkeypatch, qtbot) -> None:
    import gui.views.untargeted_view as module

    monkeypatch.setattr(module, "read_discovery_config", lambda: {})
    view = module.UntargetedView()
    qtbot.addWidget(view)
    calls: list[str] = []
    monkeypatch.setattr(
        view._results, "generate_default_report", lambda: calls.append("report")
    )
    worker = _SlowStoppingWorker()
    view._worker = worker
    view._run.set_running(True)
    view._on_finished(worker, {"mode": "full", "sample_count": 3})

    assert calls == ["report"]


def test_untargeted_stop_waits_for_cancelled_signal_before_restoring_controls(
    monkeypatch,
    qtbot,
) -> None:
    import gui.views.untargeted_view as module

    monkeypatch.setattr(module, "confirm_stop", lambda _parent: True)
    view = module.UntargetedView()
    qtbot.addWidget(view)
    status_messages: list[str] = []
    view.status_message.connect(lambda text, _timeout: status_messages.append(text))

    worker = _SlowStoppingWorker()
    view._worker = worker
    view._source.set_enabled(False)
    view._method.set_enabled(False)
    view._run.set_running(True)

    view._on_stop()

    assert worker.stop_called is True
    assert view.is_busy()
    assert view._source.isEnabled() is False
    assert view._method.isEnabled() is False
    assert status_messages[-1] == "正在停止，等待目前階段結束..."
    assert not view._run._button.isEnabled()
    assert view._run._button.text() == "停止中…"

    view._on_cancelled(worker)

    assert view.is_busy() is False
    assert view._source.isEnabled() is True
    assert view._method.isEnabled() is True
    assert view._run._button.text() == "開始執行"
    assert status_messages[-1] == "已停止執行"


def test_next_batch_reuses_method_and_environment_but_clears_batch_result(
    monkeypatch, qtbot, tmp_path
) -> None:
    import gui.views.untargeted_view as module

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "Sample.raw").touch()
    saved = {
        "raw_dir": str(raw),
        "output_dir": str(tmp_path / "result"),
        "dll_dir": str(tmp_path / "dll"),
        "preset": "rna_ribose",
        "overrides": {"rt_max": 35},
        "custom_roles": {"Sample": "qc"},
    }
    monkeypatch.setattr(module, "read_discovery_config", lambda: saved)
    view = module.UntargetedView()
    qtbot.addWidget(view)
    method = view._method.get_values()
    source = view._source.get_values()
    worker = _SlowStoppingWorker()
    view._worker = worker
    view._run.set_running(True)
    view._on_finished(worker, {"mode": "full", "sample_count": 1})
    assert not view._results.isHidden()
    view._source.new_batch_requested.emit()
    assert view._method.get_values() == method
    assert view._source.get_values()["dll_dir"] == source["dll_dir"]
    assert view._source.get_values()["targets_csv"] == source["targets_csv"]
    assert view._source.get_values()["raw_dir"] == ""
    assert view._source.get_values()["output_dir"] == ""
    assert view._samples._table.rowCount() == 0
    assert view._samples.get_custom_roles() == {}
    assert view._results.isHidden()
    assert "完成" not in view._run._status_label.text()


def test_untargeted_view_lists_cid_nl_tag_starting_methods(qtbot) -> None:
    import gui.views.untargeted_view as module

    view = module.UntargetedView()
    qtbot.addWidget(view)
    combo = view._method._preset
    names = tuple(combo.itemData(index) for index in range(combo.count()))

    assert names == ("dna_dr", "rna_ribose", "rna_mer")
    for name in names:
        assert module.filter_cid_nl_recipe_presets((name,)) == (name,)


def test_untargeted_view_quarantines_persisted_zero_overrides_before_run(
    monkeypatch,
    qtbot,
    tmp_path,
) -> None:
    import gui.views.untargeted_view as module

    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "output"
    for path in (raw_dir, dll_dir, output_dir):
        path.mkdir()
    persisted_config = {
        "mode": "full",
        "preset": "dna_dr",
        "raw_dir": str(raw_dir),
        "raw_file": "",
        "discovery_batch_index": "",
        "dll_dir": str(dll_dir),
        "output_dir": str(output_dir),
        "overrides": {field: 0.0 for field in POSITIVE_DISCOVERY_OVERRIDE_FIELDS},
    }
    monkeypatch.setattr(module, "read_discovery_config", lambda: persisted_config)

    view = module.UntargetedView()
    qtbot.addWidget(view)

    assert view._method.is_valid() is False
    assert view._run._button.isEnabled() is False
    assert "已保存的方法覆寫無效" in view._method.validation_reason()
    assert view._current_config()["overrides"] == persisted_config["overrides"]
    with pytest.raises(ValueError, match="已保存的方法覆寫無效"):
        view._build_request(view._current_config())


def test_untargeted_view_retains_persisted_pt_as_invalid_visible_sentinel(
    monkeypatch,
    qtbot,
    tmp_path,
) -> None:
    import gui.views.untargeted_view as module

    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "output"
    for path in (raw_dir, dll_dir, output_dir):
        path.mkdir()
    persisted_config = {
        "mode": "full",
        "preset": "pt_pattern",
        "raw_dir": str(raw_dir),
        "raw_file": "",
        "discovery_batch_index": "",
        "dll_dir": str(dll_dir),
        "output_dir": str(output_dir),
        "overrides": {"future_owner": {"opaque": [1, 2]}},
    }
    monkeypatch.setattr(module, "read_discovery_config", lambda: persisted_config)

    view = module.UntargetedView()
    qtbot.addWidget(view)

    assert view._method._preset.currentData() == ""
    assert "pt_pattern" in view._method._preset.currentText()
    assert "GUI 僅支援純 CID-NL" in view._method.validation_reason()
    assert "pt_pattern" in view._method._preset_preview.text()
    assert view._method._preset_preview.isHidden() is False
    assert view._run._button.isEnabled() is False
    assert view._current_config()["preset"] == "pt_pattern"
    assert view._current_config()["overrides"] == persisted_config["overrides"]
    with pytest.raises(ValueError, match="pt_pattern"):
        view._build_request(view._current_config())


def test_untargeted_view_builds_request_with_sample_metadata_and_targets(
    monkeypatch,
    qtbot,
    tmp_path,
) -> None:
    import gui.views.untargeted_view as module

    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "output"
    targets_csv = tmp_path / "targets.csv"
    for path in (raw_dir, dll_dir, output_dir):
        path.mkdir()
    targets_csv.write_text(
        "label,mz,rt_min,rt_max,ppm_tol,neutral_loss_da,"
        "nl_ppm_warn,nl_ppm_max,is_istd,istd_pair\n"
        "analyte,258.1085,8,10,20,116.0474,20,50,FALSE,\n"
        "internal standard,261.127276,8,10,20,116.0474,20,50,TRUE,\n",
        encoding="utf-8",
    )
    (raw_dir / "Sample_QC_01.raw").touch()

    persisted_config = {
        "mode": "full",
        "preset": "dna_dr",
        "raw_dir": str(raw_dir),
        "raw_file": "",
        "discovery_batch_index": "",
        "targets_csv": str(targets_csv),
        "dll_dir": str(dll_dir),
        "output_dir": str(output_dir),
        "custom_roles": {"Sample_QC_01": "pooled_qc"},
        "overrides": {},
    }
    monkeypatch.setattr(module, "read_discovery_config", lambda: persisted_config)

    view = module.UntargetedView()
    qtbot.addWidget(view)

    assert view._source.is_valid() is True
    assert view._samples._table.rowCount() == 1
    meta = view._samples.get_sample_metadata()
    assert meta is not None
    assert meta[0].sample_role == "pooled_qc"

    metadata_csv = tmp_path / "sample_metadata.csv"
    metadata_csv.write_text(
        "schema_version,sample_name,raw_stem,injection_order,sample_role,"
        "batch_id,prep_batch_id,matrix_type,group,excluded,exclusion_reason\n"
        "sample_metadata,Sample_QC_01,Sample_QC_01,1,pooled_qc,B1,P1,serum,QC,true,carryover\n",
        encoding="utf-8",
    )
    assert view._samples.load_sequence_source(metadata_csv)

    monkeypatch.setattr(module, "read_settings", lambda: {"parallel_workers": "3"})
    req = view._build_request(view._current_config())
    assert req.execution_profile.parallel_workers == 3
    assert req.execution_profile.alignment_workers == 4
    assert req.targets_csv == targets_csv
    assert req.sample_metadata is not None
    assert req.sample_metadata[0].raw_stem == "Sample_QC_01"
    assert req.sample_metadata[0].sample_role == "pooled_qc"
    from xic_extractor.sample_metadata import load_sample_metadata

    assert req.sample_metadata == load_sample_metadata(metadata_csv)


def test_untargeted_view_reset_batch_button_clears_state(
    monkeypatch,
    qtbot,
    tmp_path,
) -> None:
    import gui.views.untargeted_view as module

    raw_dir = tmp_path / "BatchReset"
    raw_dir.mkdir()
    (raw_dir / "Sample_01.raw").touch()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    persisted_config = {
        "mode": "full",
        "preset": "rna_ribose",
        "raw_dir": str(raw_dir),
        "raw_file": "",
        "discovery_batch_index": "",
        "dll_dir": str(dll_dir),
        "output_dir": str(tmp_path / "output"),
        "custom_roles": {"Sample_01": "qc"},
        "overrides": {},
    }
    monkeypatch.setattr(module, "read_discovery_config", lambda: persisted_config)

    view = module.UntargetedView()
    qtbot.addWidget(view)

    assert view._samples._table.rowCount() == 1
    assert view._method.get_values()["preset"] == "rna_ribose"

    # Click the reset button
    view._source._reset_btn.click()

    # Paths wiped
    assert view._source.get_values()["raw_dir"] == ""
    assert view._source.get_values()["output_dir"] == ""
    # DLL dir kept sticky
    assert view._source.get_values()["dll_dir"] == str(dll_dir)
    # Samples cleared
    assert view._samples._table.rowCount() == 0
    assert view._samples.get_custom_roles() == {}
    # Preset reset to default dna_dr
    assert view._method.get_values()["preset"] == "dna_dr"


def test_untargeted_view_auto_output_directory_naming(qtbot, tmp_path) -> None:
    import gui.views.untargeted_view as module

    view = module.UntargetedView()
    qtbot.addWidget(view)

    raw_path = tmp_path / "Cohort_2026_BreastCancer"
    raw_path.mkdir()

    # When output_dir is default relative "output"
    view._source._fields["output_dir"].set_value("output")
    view._source._fields["raw_dir"].set_value(str(raw_path))
    assert view._source.get_values()["output_dir"] == "output/Cohort_2026_BreastCancer"

    # When switching to another batch, output_dir updates cleanly
    raw_path2 = tmp_path / "Cohort_2026_LungCancer"
    raw_path2.mkdir()
    view._source._fields["raw_dir"].set_value(str(raw_path2))
    assert view._source.get_values()["output_dir"] == "output/Cohort_2026_LungCancer"

    # When user enters a custom path not named output, it is preserved
    custom_out = str(tmp_path / "MyCustomResults")
    view._source._fields["output_dir"].set_value(custom_out)
    view._source._fields["raw_dir"].set_value(str(raw_path))
    assert view._source.get_values()["output_dir"] == custom_out


def test_untargeted_reopens_word_sequence_and_keeps_role_correction(
    monkeypatch, qtbot, tmp_path
) -> None:
    from gui import discovery_config_io
    from gui.sections.discovery_samples_section import _COL_ROLE
    from gui.views.untargeted_view import UntargetedView
    from xic_extractor.sample_metadata import load_sample_metadata

    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path / "config")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for name in ("Sample_A", "Sample_B"):
        (raw_dir / f"{name}.raw").touch()
    word = tmp_path / "sequence.docx"
    rows = (
        ("ID", "File Name", "Instrument Method", "Sample", "Inj"),
        ("1", "Sample_B", "CID", "Sample_B", "20"),
        ("2", "Sample_A", "CID", "Sample_A", "20"),
    )
    table = "".join(
        "<w:tr>"
        + "".join(
            f"<w:tc><w:p><w:r><w:t>{value}</w:t></w:r></w:p></w:tc>" for value in row
        )
        + "</w:tr>"
        for row in rows
    )
    with zipfile.ZipFile(word, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:tbl>{table}</w:tbl></w:body></w:document>",
        )
    discovery_config_io.write_discovery_config(
        {"raw_dir": str(raw_dir), "dll_dir": str(tmp_path), "preset": "dna_dr"}
    )
    first = UntargetedView()
    qtbot.addWidget(first)
    assert first._samples.load_sequence_source(word)
    role = first._samples._table.cellWidget(0, _COL_ROLE)
    role.setCurrentIndex(role.findData("pooled_qc"))
    first.persist_config()

    reopened = UntargetedView()
    qtbot.addWidget(reopened)
    request = reopened._build_request(reopened._current_config())
    assert reopened._samples.get_sequence_source() == str(word)
    assert request.sample_metadata is not None
    assert {m.raw_stem: m.injection_order for m in request.sample_metadata} == {
        m.raw_stem: m.injection_order for m in load_sample_metadata(word)
    }
    assert request.sample_metadata[0].sample_role == "pooled_qc"
