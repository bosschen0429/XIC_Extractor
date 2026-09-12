import pytest


def test_theme_switch_controls_and_restart_preserve_visible_palette(
    monkeypatch, qtbot, qapp, tmp_path
) -> None:
    from PyQt6.QtCore import QSettings, Qt
    from PyQt6.QtGui import QPalette
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QPushButton

    from gui import config_io, discovery_config_io, main_window, styles
    from gui.method_editor import MethodEditorDialog
    from xic_extractor.presets import load_preset

    monkeypatch.setattr(config_io, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path)
    settings_path = str(tmp_path / "window.ini")
    monkeypatch.setattr(
        main_window,
        "QSettings",
        lambda *_: QSettings(settings_path, QSettings.Format.IniFormat),
    )
    window = main_window.MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.findChild(QPushButton, "theme_switch") is None
    assert not window._dark_action.isChecked()
    window.activateWindow()
    window.setFocus()
    qapp.processEvents()
    QTest.keyClick(window, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert window._dark_action.isChecked()
    assert (
        window._header_title.palette().color(QPalette.ColorRole.WindowText).name()
        == styles.DARK_PALETTE["text"]
    )

    # Amber actions need dark foreground text to remain legible.
    editor = MethodEditorDialog(load_preset("dna_dr"))
    qtbot.addWidget(editor)
    editor.show()
    qapp.processEvents()
    apply_button = editor.findChild(QPushButton, "apply_method")
    assert (
        apply_button.palette().color(QPalette.ColorRole.ButtonText).name()
        == styles.DARK_PALETTE["primary_text"]
    )
    editor.reject()
    window.close()

    restored = main_window.MainWindow()
    qtbot.addWidget(restored)
    restored.show()
    assert restored._dark_action.isChecked()
    restored._dark_action.trigger()
    qapp.processEvents()
    assert not restored._dark_action.isChecked()
    assert (
        restored._header_title.palette().color(QPalette.ColorRole.WindowText).name()
        == styles.LIGHT_PALETTE["text"]
    )
    restored.close()
    saved = QSettings(settings_path, QSettings.Format.IniFormat)
    assert saved.value("window/dark", type=bool) is False


class _CloseEvent:
    def __init__(self) -> None:
        self.accepted = False
        self.ignored = False

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


@pytest.mark.parametrize("index", [0, 1])
def test_dll_directory_is_remembered_without_saving_or_running(
    monkeypatch, qtbot, tmp_path, index
) -> None:
    from PyQt6.QtCore import QSettings

    from gui import main_window
    from gui.views import targeted_view, untargeted_view

    monkeypatch.setattr(targeted_view, "read_settings", lambda: {})
    monkeypatch.setattr(targeted_view, "read_targets", lambda: [])
    monkeypatch.setattr(untargeted_view, "read_discovery_config", lambda: {})
    ini = str(tmp_path / "preferences.ini")
    monkeypatch.setattr(
        main_window,
        "QSettings",
        lambda *_: QSettings(ini, QSettings.Format.IniFormat),
    )
    dll = tmp_path / "Thermo"
    dll.mkdir()
    window = main_window.MainWindow()
    qtbot.addWidget(window)
    field = (
        window.targeted_view._settings._dll_dir_edit
        if index == 0
        else window.untargeted_view._source._fields["dll_dir"]._edit
    )
    field.setText(str(dll))
    assert QSettings(ini, QSettings.Format.IniFormat).value(
        "environment/dll_dir"
    ) == str(dll)
    # A cleared field or an incomplete typed replacement must not erase memory.
    field.setText(str(tmp_path / "missing"))
    field.clear()
    restarted = main_window.MainWindow()
    qtbot.addWidget(restarted)
    assert restarted.targeted_view._settings.get_values()["dll_dir"] == str(dll)
    assert restarted.untargeted_view._source.get_values()["dll_dir"] == str(dll)
    assert not restarted.targeted_view._settings.environment_section.is_expanded()
    assert not restarted.untargeted_view._source._dll_section.is_expanded()


@pytest.mark.parametrize("size", [(1180, 880), (1040, 640)])
def test_main_window_action_remains_visible_while_reviewing_settings(
    monkeypatch, qtbot, tmp_path, size
) -> None:
    from PyQt6.QtCore import QPoint, QSettings

    import gui.views.targeted_view as targeted_module
    from gui import config_io, discovery_config_io

    monkeypatch.setattr(targeted_module, "read_settings", lambda: {})
    monkeypatch.setattr(targeted_module, "read_targets", lambda: [])
    monkeypatch.setattr(config_io, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path)

    import gui.main_window as module

    monkeypatch.setattr(
        module,
        "QSettings",
        lambda *_: QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
    )
    window = module.MainWindow()
    qtbot.addWidget(window)
    window.resize(*size)
    window.show()

    for index, view in enumerate(window._views):
        window._select_view(index)
        qtbot.wait(10)
        button = view._run._button
        bottom = button.mapTo(window, QPoint(0, button.height())).y()
        assert button.isVisible()
        assert bottom <= window.height() - window.statusBar().height()


@pytest.mark.parametrize("index", [0, 1])
def test_detail_workspace_keeps_edits_and_fits_short_window(
    monkeypatch, qtbot, tmp_path, index
) -> None:
    from PyQt6.QtCore import QPoint, QSettings, Qt

    from gui import config_io, discovery_config_io, main_window
    from gui.views import targeted_view, untargeted_view

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for number in range(20):
        (raw_dir / f"Sample_{number:02}.raw").touch()
    monkeypatch.setattr(targeted_view, "read_settings", lambda: {})
    monkeypatch.setattr(
        targeted_view, "read_targets", lambda: [{"label": "Target", "mz": "250"}]
    )
    monkeypatch.setattr(
        untargeted_view, "read_discovery_config", lambda: {"raw_dir": str(raw_dir)}
    )
    monkeypatch.setattr(config_io, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        main_window,
        "QSettings",
        lambda *_: QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
    )
    window = main_window.MainWindow()
    qtbot.addWidget(window)
    window.resize(1040, 640)
    window.show()
    window._select_view(index)
    view = window._views[index]
    section = view._targets if index == 0 else view._samples
    qtbot.mouseClick(section._inspect_button, Qt.MouseButton.LeftButton)
    qtbot.wait(10)
    table = section._table
    assert table.isVisible()
    assert table.width() > 750
    assert table.height() >= 150
    table_bottom = table.mapTo(view._workspace, QPoint(0, table.height())).y()
    assert table_bottom <= view._run.y()
    if index == 0:
        table.item(0, 0).setText("Edited target")
        before = section.get_targets()
    else:
        before = section.get_custom_roles()
    table.selectRow(0)
    qtbot.mouseClick(view._workspace._back, Qt.MouseButton.LeftButton)
    qtbot.wait(10)
    assert table.isHidden()
    qtbot.mouseClick(section._inspect_button, Qt.MouseButton.LeftButton)
    qtbot.wait(10)
    assert section._table is table
    assert table.currentRow() == 0
    after = section.get_targets() if index == 0 else section.get_custom_roles()
    assert after == before


def test_close_event_requests_stop_for_busy_view_before_accepting(
    monkeypatch, qtbot
) -> None:
    import gui.main_window as module
    import gui.views.targeted_view as targeted_module

    monkeypatch.setattr(targeted_module, "read_settings", lambda: {})
    monkeypatch.setattr(targeted_module, "read_targets", lambda: [])
    monkeypatch.setattr(module, "confirm_close_while_busy", lambda _parent: True)

    window = module.MainWindow()
    qtbot.addWidget(window)
    busy = {"value": True}
    stop_confirms: list[bool] = []

    def stop_targeted(*, confirm=True):
        stop_confirms.append(confirm)
        busy["value"] = False

    window.targeted_view.is_busy = lambda: busy["value"]  # type: ignore[method-assign]
    window.targeted_view.trigger_stop = stop_targeted  # type: ignore[method-assign]
    window.untargeted_view.is_busy = lambda: False  # type: ignore[method-assign]
    window.untargeted_view.persist_config = lambda: None  # type: ignore[method-assign]
    window._save_window_state = lambda: None  # type: ignore[method-assign]

    event = _CloseEvent()
    window.closeEvent(event)

    assert stop_confirms == [False]
    assert event.accepted is True
    assert event.ignored is False


def test_close_event_waits_when_busy_view_has_not_stopped(monkeypatch, qtbot) -> None:
    import gui.main_window as module
    import gui.views.targeted_view as targeted_module

    monkeypatch.setattr(targeted_module, "read_settings", lambda: {})
    monkeypatch.setattr(targeted_module, "read_targets", lambda: [])
    monkeypatch.setattr(module, "confirm_close_while_busy", lambda _parent: True)

    window = module.MainWindow()
    qtbot.addWidget(window)
    stop_confirms: list[bool] = []
    window.targeted_view.is_busy = lambda: True  # type: ignore[method-assign]
    window.targeted_view.trigger_stop = (  # type: ignore[method-assign]
        lambda *, confirm=True: stop_confirms.append(confirm)
    )
    window.untargeted_view.is_busy = lambda: False  # type: ignore[method-assign]

    event = _CloseEvent()
    window.closeEvent(event)

    assert stop_confirms == [False]
    assert event.accepted is False
    assert event.ignored is True
    assert "正在停止工作" in window.statusBar().currentMessage()
