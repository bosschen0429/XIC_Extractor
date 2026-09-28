from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QComboBox, QFileDialog, QMessageBox, QPushButton

from gui.sections.discovery_samples_section import (
    _COL_BATCH,
    _COL_NAME,
    _COL_ORDER,
    _COL_ROLE,
    _COL_STEM,
    DiscoverySamplesSection,
)
from xic_extractor.sample_metadata import SampleMetadata, load_sample_metadata


def test_discovery_samples_section_fast_population(qtbot, tmp_path: Path) -> None:
    raw_dir = tmp_path / "BatchA"
    raw_dir.mkdir()
    (raw_dir / "Sample_QC_01.raw").touch()
    (raw_dir / "Subject_001.raw").touch()
    (raw_dir / "Blank_01.raw").touch()

    section = DiscoverySamplesSection()
    qtbot.addWidget(section)

    section.set_sources(raw_dir, None)
    assert section._table.rowCount() == 3
    assert [
        section._table.item(row, _COL_ORDER).text()
        for row in range(section._table.rowCount())
    ] == ["", "", ""]
    assert not (section._table.item(0, _COL_ORDER).flags() & Qt.ItemFlag.ItemIsEditable)

    button_texts = [button.text() for button in section.findChildren(QPushButton)]
    assert not any("RAW 標頭" in text for text in button_texts)
    assert not any("重置角色" in text for text in button_texts)
    assert not any("⬇" in text or "🔍" in text for text in button_texts)
    assert [
        section._table.horizontalHeaderItem(column).text()
        for column in range(section._table.columnCount())
    ] == ["檔案名稱", "批次", "進樣順序", "樣品名稱", "分析角色"]
    assert section._table.maximumHeight() > 10_000

    meta = section.get_sample_metadata()
    assert meta is not None
    assert len(meta) == 3

    by_stem = {m.raw_stem: m for m in meta}
    assert by_stem["Sample_QC_01"].sample_role == "qc"
    assert by_stem["Subject_001"].sample_role == "study_sample"
    assert by_stem["Blank_01"].sample_role == "blank"
    assert by_stem["Sample_QC_01"].batch_id == "BatchA"

    assert section._table.isHidden()
    assert section._filter_bar.isHidden()
    assert section._export_button.isHidden()
    assert section._inspect_button.objectName() == "inspect_samples"
    assert not section._inspect_button.isHidden()
    assert section._source_label.text() == "Word 來源：尚未匯入"


def test_detail_mode_preserves_edits_selection_and_scroll(
    qtbot, tmp_path: Path
) -> None:
    raw_dir = tmp_path / "BatchDetail"
    raw_dir.mkdir()
    for index in range(48):
        (raw_dir / f"Sample_{index:02d}.raw").touch()

    section = DiscoverySamplesSection()
    qtbot.addWidget(section)
    section.resize(800, 360)
    section.set_sources(raw_dir, None)
    section.set_detail_mode(True)
    section.show()
    qtbot.wait(10)

    edited_row = 24
    section._table.item(edited_row, _COL_BATCH).setText("Edited batch")
    section._table.item(edited_row, _COL_NAME).setText("Edited sample")
    role_combo = section._table.cellWidget(edited_row, _COL_ROLE)
    assert isinstance(role_combo, QComboBox)
    role_combo.setCurrentIndex(role_combo.findData("pooled_qc"))
    section._table.selectRow(edited_row)

    scroll_bar = section._table.verticalScrollBar()
    assert scroll_bar.maximum() > 0
    scroll_bar.setValue(min(8, scroll_bar.maximum()))
    scroll_position = scroll_bar.value()

    section.set_detail_mode(False)
    assert section._table.isHidden()
    section.set_detail_mode(True)

    assert not section._table.isHidden()
    assert section._get_selected_rows() == [edited_row]
    assert scroll_bar.value() == scroll_position
    metadata = section.get_sample_metadata()
    assert metadata is not None
    edited = metadata[edited_row]
    assert edited.batch_id == "Edited batch"
    assert edited.sample_name == "Edited sample"
    assert edited.sample_role == "pooled_qc"


def test_explicit_import_requests_details_but_restore_does_not(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    raw_dir = tmp_path / "BatchExplicit"
    raw_dir.mkdir()
    (raw_dir / "Sample_01.raw").touch()
    sequence = tmp_path / "sequence.csv"
    sequence.write_text(
        "Order,Raw File,Sample Name,Role\n5,Sample_01.raw,Sample 01,Sample\n",
        encoding="utf-8",
    )

    section = DiscoverySamplesSection()
    qtbot.addWidget(section)
    section.set_sources(raw_dir, None)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(sequence), ""),
    )

    import_spy = QSignalSpy(section.details_requested)
    section._import_button.click()

    assert len(import_spy) == 1
    assert section._table.isHidden()
    assert section._source_label.text() == "Word 來源：sequence.csv"
    assert section._status_label.text() == "已匯入：匹配 1/1、未匹配 RAW 0、歧義 0"
    metadata = section.get_sample_metadata()
    assert metadata is not None
    assert metadata[0].injection_order == 5

    restore_spy = QSignalSpy(section.details_requested)
    assert section.load_sequence_source(sequence) is True
    assert len(restore_spy) == 0


def test_discovery_samples_section_role_override(qtbot, tmp_path: Path) -> None:
    raw_dir = tmp_path / "BatchB"
    raw_dir.mkdir()
    (raw_dir / "Subject_001.raw").touch()

    section = DiscoverySamplesSection()
    qtbot.addWidget(section)

    section.set_sources(raw_dir, None)
    assert section._table.rowCount() == 1

    combo = section._table.cellWidget(0, _COL_ROLE)
    assert isinstance(combo, QComboBox)
    idx = combo.findData("pooled_qc")
    combo.setCurrentIndex(idx)

    assert section.get_custom_roles() == {"Subject_001": "pooled_qc"}
    meta = section.get_sample_metadata()
    assert meta is not None
    assert meta[0].sample_role == "pooled_qc"


def test_discovery_samples_section_batch_role_and_filter(qtbot, tmp_path: Path) -> None:
    from PyQt6.QtWidgets import QTableWidgetSelectionRange

    raw_dir = tmp_path / "BatchE"
    raw_dir.mkdir()
    (raw_dir / "Sample_01.raw").touch()
    (raw_dir / "Sample_02.raw").touch()
    (raw_dir / "Sample_03.raw").touch()
    (raw_dir / "QC_Pool.raw").touch()

    section = DiscoverySamplesSection()
    qtbot.addWidget(section)
    section.set_sources(raw_dir, None)
    assert section._table.rowCount() == 4

    # Test selection and batch apply using range
    section._table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 4), True)
    assert len(section._get_selected_rows()) == 2
    assert "2 筆" in section._apply_batch_button.text()

    idx = section._batch_role_combo.findData("blank")
    section._batch_role_combo.setCurrentIndex(idx)
    section._on_apply_batch_role()

    meta = section.get_sample_metadata()
    assert meta is not None
    by_stem = {m.raw_stem: m for m in meta}
    assert by_stem["QC_Pool"].sample_role == "blank"
    assert by_stem["Sample_01"].sample_role == "blank"
    assert by_stem["Sample_02"].sample_role == "study_sample"
    assert by_stem["Sample_03"].sample_role == "study_sample"

    # Test real-time filter
    section._filter_edit.setText("blank")
    assert not section._table.isRowHidden(0)
    assert not section._table.isRowHidden(1)
    assert section._table.isRowHidden(2)
    assert section._table.isRowHidden(3)
    assert "顯示 2 / 4 筆" in section._filter_status_label.text()

    section._filter_edit.setText("QC")
    assert not section._table.isRowHidden(0)
    assert section._table.isRowHidden(1)
    assert section._table.isRowHidden(2)
    assert section._table.isRowHidden(3)
    assert "顯示 1 / 4 筆" in section._filter_status_label.text()

    section._filter_edit.clear()
    assert not section._table.isRowHidden(2)
    assert section._filter_status_label.text() == ""


@pytest.mark.parametrize("with_raw", [False, True])
def test_discovery_samples_section_file_dropped_imports_csv(
    qtbot, tmp_path: Path, monkeypatch, with_raw: bool
) -> None:
    csv_file = tmp_path / "sample_metadata.csv"
    csv_file.write_text(
        "schema_version,sample_name,raw_stem,injection_order,sample_role,"
        "batch_id,prep_batch_id,matrix_type,group,excluded,exclusion_reason\n"
        "sample_metadata,Sample1,Stem1,10,calibrator,BatchX,Prep1,serum,G1,true,carryover\n"
        "sample_metadata,Sample2,Stem2,20,pooled_qc,BatchX,Prep2,plasma,G2,false,\n",
        encoding="utf-8",
    )

    section = DiscoverySamplesSection()
    qtbot.addWidget(section)
    if with_raw:
        raw_dir = tmp_path / "BatchX"
        raw_dir.mkdir()
        for stem in ("Stem1", "Stem2", "Unmatched"):
            (raw_dir / f"{stem}.raw").touch()
        section.set_sources(raw_dir, None)

    # Trigger file dropped
    section._table.file_dropped.emit(csv_file)
    source = load_sample_metadata(csv_file)
    expected = source + (
        (
            SampleMetadata(
                "Unmatched", "Unmatched", sample_role="study_sample", batch_id="BatchX"
            ),
        )
        if with_raw
        else ()
    )
    assert section.get_sample_metadata() == expected

    section._table.item(0, _COL_NAME).setText("Edited sample")
    section._table.item(0, _COL_BATCH).setText("Edited batch")
    combo = section._table.cellWidget(0, _COL_ROLE)
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(combo.findData("blank"))
    expected = (
        replace(
            expected[0],
            sample_name="Edited sample",
            batch_id="Edited batch",
            sample_role="blank",
        ),
        *expected[1:],
    )
    section._filter_edit.setText("Edited sample")
    section._table.selectRow(0)
    assert section._table.isRowHidden(1)
    assert section.get_sample_metadata() == expected

    exported = tmp_path / "exported.csv"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *_a, **_kw: (str(exported), "")
    )
    section._export_button.click()
    assert load_sample_metadata(exported) == expected


@pytest.mark.parametrize(
    "transition", ["new_source", "unmatched_source", "new_batch", "clear"]
)
@pytest.mark.parametrize("raw_source", ["none", "empty", "populated"])
def test_sample_source_fields_follow_current_records(
    qtbot, tmp_path, transition, raw_source
):
    raw_dir = tmp_path / "BatchA"
    raw_dir.mkdir()
    if raw_source != "empty":
        (raw_dir / "Sample1.raw").touch()
    source = tmp_path / "metadata.csv"
    source.write_text(
        "schema_version,sample_name,raw_stem,injection_order,sample_role,"
        "batch_id,prep_batch_id,matrix_type,group,excluded,exclusion_reason\n"
        "sample_metadata,Sample1,Sample1,1,study_sample,BatchA,P1,serum,G1,true,carryover\n",
        encoding="utf-8",
    )
    section = DiscoverySamplesSection()
    qtbot.addWidget(section)
    if raw_source != "none":
        section.set_sources(raw_dir, None)
    assert section.load_sequence_source(source)
    assert section.get_sample_metadata() == load_sample_metadata(source)

    if transition in {"new_source", "unmatched_source"}:
        name = "Sample1" if transition == "new_source" else "Other"
        replacement = tmp_path / "replacement.csv"
        replacement.write_text(
            f"Order,Raw File,Sample Name\n2,{name}.raw,{name}\n", encoding="utf-8"
        )
        assert section.load_sequence_source(replacement)
        if raw_source != "populated":
            assert section.get_sample_metadata() == load_sample_metadata(replacement)
            assert (
                section._status_label.text() == "已載入 1 筆上機資訊（尚無 RAW 可匹配）"
            )
            return
        expected_order = 2 if transition == "new_source" else None
    else:
        if transition == "clear":
            section.clear_all()
            assert section.get_sample_metadata() is None
            (raw_dir / "Sample1.raw").touch()
        else:
            raw_dir = tmp_path / "BatchB"
            raw_dir.mkdir()
            (raw_dir / "Sample1.raw").touch()
        section.set_sources(raw_dir, None)
        expected_order = None
    assert section.get_sample_metadata() == (
        SampleMetadata(
            "Sample1",
            "Sample1",
            injection_order=expected_order,
            sample_role="study_sample",
            batch_id=raw_dir.name,
        ),
    )


def test_failed_explicit_sample_import_preserves_source_and_edits(
    qtbot, tmp_path, monkeypatch
):
    source = tmp_path / "metadata.csv"
    source.write_text(
        "schema_version,sample_name,raw_stem,injection_order,sample_role,"
        "batch_id,prep_batch_id,matrix_type,group,excluded,exclusion_reason\n"
        "sample_metadata,Sample1,Sample1,1,study_sample,BatchA,P1,serum,G1,true,carryover\n",
        encoding="utf-8",
    )
    section = DiscoverySamplesSection()
    qtbot.addWidget(section)
    assert section.load_sequence_source(source)
    section._table.item(0, _COL_NAME).setText("Edited sample")
    expected = (replace(load_sample_metadata(source)[0], sample_name="Edited sample"),)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a: warnings.append(True))
    section._table.file_dropped.emit(tmp_path / "missing.csv")
    assert warnings == [True]
    assert section.get_sequence_source() == str(source)
    assert section.get_sample_metadata() == expected


def test_discovery_samples_section_switching_raw_dir_clears_memory(
    qtbot, tmp_path: Path
) -> None:
    batch1 = tmp_path / "Batch1"
    batch1.mkdir()
    (batch1 / "sample_01.raw").touch()

    batch2 = tmp_path / "Batch2"
    batch2.mkdir()
    (batch2 / "sample_01.raw").touch()

    section = DiscoverySamplesSection()
    qtbot.addWidget(section)

    # Load Batch1 and customize role
    section.set_sources(batch1, None)
    assert section._table.rowCount() == 1
    combo = section._table.cellWidget(0, _COL_ROLE)
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(combo.findData("pooled_qc"))
    assert section.get_custom_roles() == {"sample_01": "pooled_qc"}

    # Switch to Batch2 -> should completely clear custom roles and edits
    section.set_sources(batch2, None)
    assert section._table.rowCount() == 1
    assert section.get_custom_roles() == {}
    meta = section.get_sample_metadata()
    assert meta is not None
    # Defaults back to inferred role, not the previous batch's customized role
    assert meta[0].sample_role == "study_sample"


def test_discovery_samples_section_clear_all(qtbot, tmp_path: Path) -> None:
    raw_dir = tmp_path / "BatchClear"
    raw_dir.mkdir()
    (raw_dir / "sample_01.raw").touch()

    section = DiscoverySamplesSection()
    qtbot.addWidget(section)
    section.set_sources(raw_dir, None)
    assert section._table.rowCount() == 1

    section.clear_all()
    assert section._table.rowCount() == 0
    assert section.get_custom_roles() == {}
    assert section.get_sample_metadata() is None


def test_sequence_import_reports_match_gaps_and_clears_unowned_orders(
    qtbot, tmp_path: Path
) -> None:
    raw_dir = tmp_path / "BatchSequence"
    raw_dir.mkdir()
    for stem in ("Sample-01", "Ambiguous1", "Ambiguous_1", "Other"):
        (raw_dir / f"{stem}.raw").touch()

    sequence = tmp_path / "sequence.csv"
    sequence.write_text(
        "Order,Raw File,Sample Name,Role\n"
        "7,Sample-01.raw,Sample 01,Sample\n"
        "8,Missing.raw,Missing,Sample\n"
        "9,Ambiguous-1.raw,Ambiguous,QC\n",
        encoding="utf-8",
    )

    section = DiscoverySamplesSection()
    qtbot.addWidget(section)
    section.set_sources(raw_dir, None)
    section._import_from_path(sequence)

    assert section._status_label.text() == ("已匯入：匹配 1/4、未匹配 RAW 3、歧義 1")
    assert section._source_label.text() == "Word 來源：sequence.csv"
    assert "序列未對應 RAW：1" in section._status_label.toolTip()
    assert section.get_sequence_source() == str(sequence)

    metadata = section.get_sample_metadata()
    assert metadata is not None
    by_stem = {row.raw_stem: row for row in metadata}
    assert by_stem["Sample-01"].injection_order == 7
    assert by_stem["Ambiguous1"].injection_order is None
    assert by_stem["Ambiguous_1"].injection_order is None
    assert by_stem["Other"].injection_order is None

    replacement = tmp_path / "replacement.csv"
    replacement.write_text(
        "Order,Raw File,Sample Name,Role\n20,Missing.raw,Missing,Sample\n",
        encoding="utf-8",
    )
    section._import_from_path(replacement)

    metadata = section.get_sample_metadata()
    assert metadata is not None
    assert all(row.injection_order is None for row in metadata)
    assert section.get_sequence_source() == str(replacement)


def test_sequence_source_restore_failure_is_non_modal_and_persisted(
    qtbot, tmp_path: Path
) -> None:
    raw_dir = tmp_path / "BatchRestore"
    raw_dir.mkdir()
    (raw_dir / "Sample_01.raw").touch()
    section = DiscoverySamplesSection()
    qtbot.addWidget(section)
    section.set_sources(raw_dir, None)
    section._table.item(0, _COL_ORDER).setText("12")
    missing = tmp_path / "missing.docx"

    assert section.load_sequence_source(missing) is False
    assert section.get_sequence_source() == str(missing)
    assert section._table.item(0, _COL_ORDER).text() == ""
    assert "無法載入上機資訊" in section._status_label.text()
    assert "1 個 RAW" in section._status_label.text()
    assert section._source_label.text() == "Word 來源：missing.docx"
    assert str(missing) in section._status_label.toolTip()


def test_samples_section_lists_nested_raw_once_per_stem(qtbot, tmp_path: Path) -> None:
    raw_dir = tmp_path / "Batch"
    for rel in ("S1.raw", "validation/S1.raw", "Blank/Blank_1.raw"):
        (raw_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (raw_dir / rel).touch()

    section = DiscoverySamplesSection()
    qtbot.addWidget(section)
    section.set_sources(raw_dir, None)

    assert [
        section._table.item(row, _COL_STEM).text()
        for row in range(section._table.rowCount())
    ] == ["Blank_1", "S1"]


def test_imported_metadata_keeps_excluded_samples_for_discovery(
    qtbot, tmp_path: Path
) -> None:
    from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS

    raw_dir = tmp_path / "Batch"
    for rel in ("S1.raw", "except sample/S2.raw"):
        (raw_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (raw_dir / rel).touch()
    metadata = tmp_path / "samples.tsv"
    row = dict.fromkeys(SAMPLE_METADATA_COLUMNS, "")
    row |= {
        "schema_version": "sample_metadata",
        "sample_name": "S2",
        "raw_stem": "S2",
        "excluded": "true",
        "exclusion_reason": "injected but outside the design",
    }
    metadata.write_text(
        "\t".join(SAMPLE_METADATA_COLUMNS)
        + "\n"
        + "\t".join(row[c] for c in SAMPLE_METADATA_COLUMNS)
        + "\n",
        encoding="utf-8",
    )

    section = DiscoverySamplesSection()
    qtbot.addWidget(section)
    section.set_sources(raw_dir, None)
    assert section.load_sequence_source(metadata)

    rows = {r.raw_stem: r for r in section.get_sample_metadata() or ()}
    assert rows["S2"].excluded
    assert not rows["S1"].excluded
