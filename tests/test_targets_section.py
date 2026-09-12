import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QComboBox

import gui.sections.targets_section as targets_section_module
from gui.sections.targets_section import TargetsSection


def _sample_targets() -> list[dict[str, str]]:
    return [
        {
            "label": "5-hmdC",
            "mz": "258.1085",
            "rt_min": "8.0",
            "rt_max": "10.0",
            "ppm_tol": "20",
            "neutral_loss_da": "116.0474",
            "nl_ppm_warn": "20",
            "nl_ppm_max": "50",
            "is_istd": "false",
            "istd_pair": "d3-5-hmdC",
        },
        {
            "label": "d3-5-hmdC",
            "mz": "261.1273",
            "rt_min": "8.0",
            "rt_max": "10.0",
            "ppm_tol": "20",
            "neutral_loss_da": "116.0474",
            "nl_ppm_warn": "20",
            "nl_ppm_max": "50",
            "is_istd": "true",
            "istd_pair": "",
        },
    ]


def test_load_row_count(qtbot):
    section = TargetsSection()
    qtbot.addWidget(section)
    section.load(_sample_targets())
    assert section._table.rowCount() == 2
    assert section._summary_label.text() == "2 個目標 · 1 個 ISTD"
    assert section._dirty_label.text() == "無未儲存變更"


def test_summary_requests_same_section_detail_view(qtbot):
    section = TargetsSection()
    qtbot.addWidget(section)
    section.load(_sample_targets())
    detail_requests = []
    section.details_requested.connect(lambda: detail_requests.append(True))

    assert section._table.isHidden()
    assert section._inspect_button.objectName() == "inspect_targets"

    qtbot.mouseClick(section._inspect_button, Qt.MouseButton.LeftButton)
    assert detail_requests == [True]

    section.set_detail_mode(True)
    assert not section._table.isHidden()

    section.set_detail_mode(False)
    assert section._table.isHidden()


def test_get_targets_round_trips(qtbot):
    section = TargetsSection()
    qtbot.addWidget(section)
    section.load(_sample_targets())
    targets = section.get_targets()
    assert targets[0]["label"] == "5-hmdC"
    assert targets[0]["neutral_loss_da"] == "116.0474"
    assert targets[1]["neutral_loss_da"] == "116.0474"


def test_hidden_target_pair_rt_metadata_round_trips(qtbot):
    section = TargetsSection()
    qtbot.addWidget(section)
    rows = _sample_targets()
    rows[0]["paired_rt_relation"] = "istd_not_later_than_pair"
    rows[1]["isotope_label_type"] = "deuterated"

    section.load(rows)
    targets = section.get_targets()

    assert targets[0]["paired_rt_relation"] == "istd_not_later_than_pair"
    assert targets[1]["isotope_label_type"] == "deuterated"


def test_summary_detail_round_trip_keeps_unsaved_values_precision_and_metadata(qtbot):
    section = TargetsSection()
    qtbot.addWidget(section)
    changes = []
    section.changed.connect(lambda: changes.append(True))
    rows = _sample_targets()
    rows[0]["paired_rt_relation"] = "istd_not_later_than_pair"
    section.load(rows)
    section.set_detail_mode(True)
    section.set_detail_mode(False)
    section.set_detail_mode(True)

    assert changes == []

    section._table.item(0, 0).setText("5-hmdC edited")
    assert changes == [True]
    combo = section._table.cellWidget(0, section._COL_NL)
    assert isinstance(combo, QComboBox)
    combo.setEditText("116.047400")
    section._table.selectRow(1)

    section.set_detail_mode(False)
    assert section._dirty_label.text() == "有未儲存變更"
    section.set_detail_mode(True)

    targets = section.get_targets()
    assert targets[0]["label"] == "5-hmdC edited"
    assert targets[0]["neutral_loss_da"] == "116.047400"
    assert targets[0]["paired_rt_relation"] == "istd_not_later_than_pair"
    assert section._table.currentRow() == 1


def test_nl_combo_shows_preset(qtbot):
    section = TargetsSection()
    qtbot.addWidget(section)
    section.load(_sample_targets())
    combo = section._table.cellWidget(0, section._COL_NL)
    assert isinstance(combo, QComboBox)
    assert combo.currentText() == "dR · 116.0474"


def test_add_row(qtbot):
    section = TargetsSection()
    qtbot.addWidget(section)
    section.load(_sample_targets())
    section.details_requested.connect(lambda: section.set_detail_mode(True))
    qtbot.mouseClick(section._add_button, Qt.MouseButton.LeftButton)
    assert section._table.rowCount() == 3
    assert not section._table.isHidden()
    assert section._dirty_label.text() == "有未儲存變更"


def test_istd_checkbox_round_trips(qtbot):
    section = TargetsSection()
    qtbot.addWidget(section)
    section.load(_sample_targets())
    cb0 = section._table.cellWidget(0, section._COL_ISTD).findChild(QCheckBox)
    cb1 = section._table.cellWidget(1, section._COL_ISTD).findChild(QCheckBox)
    assert isinstance(cb0, QCheckBox)
    assert not cb0.isChecked()
    assert cb1.isChecked()
    targets = section.get_targets()
    assert targets[0]["is_istd"] == "false"
    assert targets[1]["is_istd"] == "true"


def test_istd_pair_round_trips(qtbot):
    section = TargetsSection()
    qtbot.addWidget(section)
    section.load(_sample_targets())
    targets = section.get_targets()
    assert targets[0]["istd_pair"] == "d3-5-hmdC"
    assert targets[1]["istd_pair"] == ""


@pytest.mark.parametrize(("suffix", "delimiter"), [(".csv", ","), (".tsv", "\t")])
def test_targets_section_file_dropped_imports_csv_and_tsv_with_hidden_fields(
    qtbot, tmp_path, suffix, delimiter
):
    path = tmp_path / f"targets{suffix}"
    path.write_text(
        delimiter.join(
            [
                "label",
                "mz",
                "rt_min",
                "rt_max",
                "paired_rt_relation",
                "source_note",
            ]
        )
        + "\n"
        + delimiter.join(
            [
                "TargetA",
                "250.1",
                "5.0",
                "6.0",
                "istd_not_later_than_pair",
                "keep me",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    section = TargetsSection()
    qtbot.addWidget(section)
    section.details_requested.connect(lambda: section.set_detail_mode(True))
    section._table.file_dropped.emit(path)

    assert section._table.rowCount() == 1
    assert not section._table.isHidden()
    targets = section.get_targets()
    assert targets[0]["label"] == "TargetA"
    assert targets[0]["mz"] == "250.1"
    assert targets[0]["paired_rt_relation"] == "istd_not_later_than_pair"
    assert targets[0]["source_note"] == "keep me"


def test_targets_section_import_button_accepts_incomplete_draft(
    qtbot, tmp_path, monkeypatch
):
    path = tmp_path / "draft.csv"
    path.write_text("label,mz\nDraft target,\n", encoding="utf-8")
    monkeypatch.setattr(
        targets_section_module.QFileDialog,
        "getOpenFileName",
        lambda *args: (str(path), ""),
    )
    section = TargetsSection()
    qtbot.addWidget(section)
    section.details_requested.connect(lambda: section.set_detail_mode(True))

    qtbot.mouseClick(section._import_button, Qt.MouseButton.LeftButton)

    assert section.get_targets()[0]["label"] == "Draft target"
    assert section.get_targets()[0]["mz"] == ""
    assert section._dirty_label.text() == "有未儲存變更"


@pytest.mark.parametrize(
    ("filename", "content", "expected_error"),
    [
        ("empty.csv", "", "file is empty"),
        ("header_only.csv", "label,mz\n", "at least one target row"),
        ("unknown.csv", "name,mass\nTargetA,250.1\n", "required column is missing"),
        (
            "extra_value.csv",
            "label,mz\nTargetA,250.1,unexpected\n",
            "more values than columns",
        ),
        ("missing_value.tsv", "label\tmz\nTargetA\n", "fewer values than columns"),
    ],
)
@pytest.mark.parametrize("initial_dirty", [False, True])
def test_targets_section_failed_drop_preserves_rows_and_dirty_state(
    qtbot,
    tmp_path,
    monkeypatch,
    filename,
    content,
    expected_error,
    initial_dirty,
):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    warnings = []
    monkeypatch.setattr(
        targets_section_module.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    section = TargetsSection()
    qtbot.addWidget(section)
    section.load(_sample_targets())
    section._set_dirty(initial_dirty)
    before = section.get_targets()
    before_dirty_text = section._dirty_label.text()

    section._table.file_dropped.emit(path)

    assert section.get_targets() == before
    assert section._dirty_label.text() == before_dirty_text
    assert len(warnings) == 1
    assert warnings[0][0] == "匯入失敗"
    assert expected_error in warnings[0][1]


def test_targets_section_failed_button_import_preserves_current_edits(
    qtbot, tmp_path, monkeypatch
):
    path = tmp_path / "malformed.csv"
    path.write_text("label,mz\nTargetA\n", encoding="utf-8")
    monkeypatch.setattr(
        targets_section_module.QFileDialog,
        "getOpenFileName",
        lambda *args: (str(path), ""),
    )
    monkeypatch.setattr(
        targets_section_module.QMessageBox, "warning", lambda *args: None
    )
    section = TargetsSection()
    qtbot.addWidget(section)
    section.load(_sample_targets())
    section._table.item(0, 0).setText("edited target")
    before = section.get_targets()

    qtbot.mouseClick(section._import_button, Qt.MouseButton.LeftButton)

    assert section.get_targets() == before
    assert section._dirty_label.text() == "有未儲存變更"
