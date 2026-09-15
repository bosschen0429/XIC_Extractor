"""Untargeted Inspector handoff, settings and report state observables."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from types import SimpleNamespace

from PyQt6.QtCore import Qt

from gui.inspector_export_dialog import InspectorExportDialog
from gui.sections.discovery_results_section import DiscoveryResultsSection
from gui.workers.discovery_worker import _alignment_summary
from gui.workers.inspector_report_worker import (
    InspectorJob,
    InspectorReportWorker,
)
from tests.test_xic_inspector_api import _create_mock_v2_db
from xic_inspector.api import EvidenceLocator, InspectorCancelled, SelectionRequest


def _source(tmp_path: Path) -> EvidenceLocator:
    db, matrix, digest = _create_mock_v2_db(tmp_path)
    return EvidenceLocator(db, matrix, digest, "req-01", "att-01", 2)


def _summary(source: EvidenceLocator) -> dict:
    return {
        "mode": "align_only",
        "matrix_tsv": str(source.matrix_path),
        "alignment_outputs": {"matrix_tsv": str(source.matrix_path)},
        "evidence": {
            "report_path": str(source.report_path),
            "matrix_path": str(source.matrix_path),
            "matrix_sha256": source.matrix_sha256,
            "processing_request_id": source.processing_request_id,
            "processing_attempt_id": source.processing_attempt_id,
            "schema_version": source.schema_version,
        },
    }


def test_completed_alignment_hands_off_locator_without_changing_path_map(tmp_path):
    source = _source(tmp_path)
    outputs = SimpleNamespace(matrix_tsv=source.matrix_path, evidence=source)
    route = SimpleNamespace(as_dict=lambda: {"route": "retained"})
    summary = _alignment_summary(outputs, route)
    assert summary["evidence"] == _summary(source)["evidence"]
    assert summary["alignment_outputs"] == {"matrix_tsv": str(source.matrix_path)}


def test_results_only_enable_export_for_valid_current_locator(qtbot, tmp_path):
    source = _source(tmp_path)
    section = DiscoveryResultsSection()
    qtbot.addWidget(section)
    section.update_results(_summary(source))
    assert section._export_inspector.isEnabled()
    assert section._open_matrix.isEnabled()
    assert not section._open_inspector.isEnabled()

    invalid = _summary(source)
    invalid["evidence"] = {"report_path": str(source.report_path)}
    section.update_results(invalid)
    assert not section._export_inspector.isEnabled()
    assert "無效" in section._inspector_status.text()
    assert section._open_matrix.isEnabled()

    section.update_results(_summary(source))
    section.clear_results()
    assert not section._export_inspector.isEnabled()
    assert not section._open_inspector.isEnabled()
    section.show_error("science failed")
    assert not section._export_inspector.isEnabled()


def test_dialog_maps_canonical_samples_and_query_fields(qtbot, tmp_path):
    dialog = InspectorExportDialog(tmp_path / "report.html")
    qtbot.addWidget(dialog)
    assert dialog.limit.value() == 30
    assert dialog.seed.text() == "0"
    assert not dialog.candidates.isEnabled()
    dialog.set_samples({"samples": [
        {"sample_stem": "s1", "canonical_sample_identity_id": "S1"},
        {"sample_stem": "s2", "canonical_sample_identity_id": "S2"},
    ]})
    dialog.samples.item(1).setCheckState(Qt.CheckState.Checked)
    dialog.limit.setValue(5)
    dialog._submit(False)
    assert dialog.selection == SelectionRequest(
        strategy="sample", limit=5, seed=0, sample_ids=("S2",)
    )
    assert dialog.output_path == tmp_path / "report.html"
    assert dialog.open_after_export is False

    query = InspectorExportDialog(tmp_path / "query.html")
    qtbot.addWidget(query)
    query.set_samples({"samples": []})
    query.strategy.setCurrentIndex(2)
    query.candidates.setText("C1")
    query.groups.setText("G1")
    query.mz_min.setText("499.5")
    query.mz_max.setText("500.5")
    query.rt_min.setText("0.8")
    query.rt_max.setText("1.2")
    query.offset.setText("1")
    query._submit(True)
    assert query.selection == SelectionRequest(
        strategy="query", candidate_ids=("C1",), group_ids=("G1",),
        mz_min=499.5, mz_max=500.5, rt_min=0.8, rt_max=1.2, offset=1,
    )
    assert query.open_after_export is True


def test_report_worker_calls_real_inspector_and_emits_result(qtbot, tmp_path):
    source = _source(tmp_path)
    job = InspectorJob(
        source, SelectionRequest(limit=1), tmp_path / "inspector.html", False
    )
    worker = InspectorReportWorker(job)
    results = []
    worker.succeeded.connect(results.append)
    worker.start()
    assert worker.wait(10000)
    qtbot.wait(50)
    result = results[0]
    assert result.source == source
    assert result.selected_count == 1
    assert result.sample_ids == ("S1", "S2")
    assert job.output_path.is_file()
    html = job.output_path.read_text(encoding="utf-8")
    assert 'id="inspector-data"' in html


def test_report_completion_after_clear_keeps_old_run_out_of_new_open(qtbot, tmp_path):
    source = _source(tmp_path)
    section = DiscoveryResultsSection()
    qtbot.addWidget(section)
    section.update_results(_summary(source))
    messages = []
    section.status_message.connect(lambda text, _timeout: messages.append(text))
    section._start_report(
        InspectorJob(source, SelectionRequest(limit=1), tmp_path / "late.html", False)
    )
    worker = section._report_worker
    assert worker is not None
    section.clear_results()
    assert worker.wait(10000)
    qtbot.wait(50)
    assert section._report_worker is None
    assert (tmp_path / "late.html").is_file()
    assert section._report_target is None
    assert not section._open_inspector.isEnabled()
    assert not section._export_inspector.isEnabled()
    assert any("request req-01 / attempt att-01" in text for text in messages)


def test_export_and_open_announces_success_before_browser_event_reentry(
    monkeypatch, qtbot, tmp_path
):
    import gui.sections.discovery_results_section as section_module

    source = _source(tmp_path)
    section = DiscoveryResultsSection()
    qtbot.addWidget(section)
    section.update_results(_summary(source))
    statuses = []
    section.status_message.connect(lambda text, _timeout: statuses.append(text))
    opened = []

    def reentrant_open(path: str) -> None:
        opened.append(path)
        # os.startfile can enter the Qt event loop before this slot returns.
        qtbot.wait(50)

    monkeypatch.setattr(section_module, "_open_path", reentrant_open)
    section._start_report(
        InspectorJob(
            source, SelectionRequest(limit=1), tmp_path / "opened.html", True
        )
    )
    worker = section._report_worker
    assert worker is not None
    assert worker.wait(10000)
    qtbot.wait(50)
    assert opened == [str(tmp_path / "opened.html")]
    assert (tmp_path / "opened.html").is_file()
    assert "selected 1" in section._inspector_status.text()
    assert "retained_curves_v1" in section._inspector_status.text()
    assert statuses and "selected 1" in statuses[-1]


def test_slow_report_keeps_start_snapshot_while_new_result_arrives(
    monkeypatch, qtbot, tmp_path
):
    import gui.workers.inspector_report_worker as worker_module

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    source = _source(first)
    next_source = _source(second)
    started = Event()
    release = Event()
    real_generate = worker_module.generate_report

    def slow_generate(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(worker_module, "generate_report", slow_generate)
    section = DiscoveryResultsSection()
    qtbot.addWidget(section)
    section.update_results(_summary(source))
    section._start_report(
        InspectorJob(source, SelectionRequest(limit=1), first / "old.html", False)
    )
    worker = section._report_worker
    assert worker is not None
    assert started.wait(5)
    section.clear_results()
    section.update_results(_summary(next_source))
    section._start_report(
        InspectorJob(next_source, SelectionRequest(limit=1), second / "new.html", False)
    )
    assert section._report_worker is worker
    assert not section._export_inspector.isEnabled()
    release.set()
    assert worker.wait(10000)
    qtbot.wait(50)
    assert (first / "old.html").is_file()
    assert not (second / "new.html").exists()
    assert section._report_target is None
    assert section._evidence == next_source
    assert section._export_inspector.isEnabled()
    assert "request req-01 / attempt att-01" in section._inspector_status.text()


def test_cancelled_report_does_not_open_browser_or_change_matrix(
    monkeypatch, qtbot, tmp_path
):
    import gui.sections.discovery_results_section as section_module
    import gui.workers.inspector_report_worker as worker_module

    source = _source(tmp_path)
    started = Event()
    release = Event()

    def cancelled_generate(*args, **kwargs):
        started.set()
        assert release.wait(10)
        if kwargs["is_cancelled"]():
            raise InspectorCancelled()
        raise AssertionError("report was not cancelled")

    opened = []
    monkeypatch.setattr(worker_module, "generate_report", cancelled_generate)
    monkeypatch.setattr(section_module, "_open_path", opened.append)
    section = DiscoveryResultsSection()
    qtbot.addWidget(section)
    section.update_results(_summary(source))
    section._start_report(
        InspectorJob(source, SelectionRequest(), tmp_path / "cancel.html", True)
    )
    worker = section._report_worker
    assert worker is not None
    assert started.wait(5)
    section.cancel_report()
    release.set()
    assert worker.wait(10000)
    qtbot.wait(50)
    assert "已取消" in section._inspector_status.text()
    assert section._open_matrix.isEnabled()
    assert not section._open_inspector.isEnabled()
    assert opened == []


def test_bound_source_error_is_visible_without_open(monkeypatch, qtbot, tmp_path):
    import gui.sections.discovery_results_section as section_module

    source = _source(tmp_path)
    bad_source = EvidenceLocator(
        source.report_path, source.matrix_path, "f" * 64,
        source.processing_request_id, source.processing_attempt_id,
        source.schema_version,
    )
    opened = []
    monkeypatch.setattr(section_module, "_open_path", opened.append)
    section = DiscoveryResultsSection()
    qtbot.addWidget(section)
    section.update_results(_summary(bad_source))
    section._start_report(
        InspectorJob(bad_source, SelectionRequest(), tmp_path / "error.html", True)
    )
    worker = section._report_worker
    assert worker is not None
    assert worker.wait(10000)
    qtbot.wait(50)
    assert "失敗 (code" in section._inspector_status.text()
    assert not (tmp_path / "error.html").exists()
    assert section._open_matrix.isEnabled()
    assert opened == []
