from __future__ import annotations

import zipfile

import pytest

from gui.sections.discovery_method_section import POSITIVE_DISCOVERY_OVERRIDE_FIELDS


class _SlowStoppingWorker:
    def __init__(self) -> None:
        self.stop_called = False

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

    req = view._build_request(view._current_config())
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
