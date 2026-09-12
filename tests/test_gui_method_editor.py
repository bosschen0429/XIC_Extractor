from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QLineEdit, QTableWidget

from gui.method_editor import MethodEditorDialog
from gui.sections.discovery_method_section import DiscoveryMethodSection
from xic_extractor.presets import bind_cid_nl_recipe, load_preset


def test_editor_save_without_edits_preserves_method_text(qtbot, tmp_path: Path) -> None:
    original = load_preset("dna_dr")
    original = replace(
        original,
        name=" Method ",
        description=" Description ",
        tags=(replace(original.tags[0], name="DNA_dR "),),
    )
    editor = MethodEditorDialog(original)
    qtbot.addWidget(editor)
    destination = tmp_path / "unchanged.toml"

    assert editor.save_to(destination)

    reloaded = load_preset(destination)
    assert (reloaded.name, reloaded.description) == (
        original.name,
        original.description,
    )
    assert bind_cid_nl_recipe(reloaded, "full").discovery_settings == (
        bind_cid_nl_recipe(
            original, "full", recipe_overrides={"rt_max": 60.0}
        ).discovery_settings
    )


def test_editor_saves_tag_composition_as_cli_method(qtbot, tmp_path: Path) -> None:
    editor = MethodEditorDialog(
        load_preset("dna_dr"),
        discovery_overrides={
            "nl_tolerance_ppm": 12.5,
        },
    )
    qtbot.addWidget(editor)
    name = editor.findChild(QLineEdit, "method_name")
    tags = editor.findChild(QTableWidget, "method_tags")
    combine = editor.findChild(QComboBox, "method_combine")
    assert name is not None and tags is not None and combine is not None
    name.setText("dR 或 R")
    editor.add_tag(name="R", value=132.0423)
    combine.setCurrentIndex(combine.findData("union"))

    destination = tmp_path / "custom.toml"
    assert editor.save_to(destination) is True

    saved = load_preset(destination)
    settings = bind_cid_nl_recipe(saved, "full").discovery_settings
    assert saved.name == "dR 或 R"
    assert settings.selected_tag_names == ("DNA_dR", "R")
    assert settings.tag_combine_mode == "union"
    assert settings.neutral_loss_profiles[1].neutral_loss_da == 132.0423
    assert settings.nl_tolerance_ppm == 12.5
    assert editor.saved_path == destination.resolve()


def test_editor_cancel_does_not_change_original_method(qtbot) -> None:
    preset = load_preset("dna_dr")
    editor = MethodEditorDialog(preset)
    qtbot.addWidget(editor)
    editor.add_tag(name="R", value=132.0423)

    editor.reject()

    assert editor.saved_path is None
    assert len(preset.tags) == 1
    assert preset.combine_mode == "single"


def test_invalid_editor_method_preserves_existing_file(qtbot, tmp_path: Path) -> None:
    editor = MethodEditorDialog(load_preset("dna_dr"))
    qtbot.addWidget(editor)
    editor.add_tag(name="R", value=132.0423)
    combine = editor.findChild(QComboBox, "method_combine")
    assert combine is not None
    combine.setCurrentIndex(combine.findData("single"))
    destination = tmp_path / "existing.toml"
    destination.write_text("existing bytes", encoding="utf-8")

    assert editor.save_to(destination) is False

    assert destination.read_text(encoding="utf-8") == "existing bytes"
    assert editor.saved_path is None
    assert editor.validation_message()


def test_custom_method_import_and_restart_preserve_exact_settings(
    qtbot, tmp_path: Path
) -> None:
    from dataclasses import replace

    from xic_extractor.presets import save_preset

    preset = replace(
        load_preset("dna_dr"),
        name="精確方法",
        discovery_overrides={"nl_tolerance_ppm": 12.3456789, "rt_max": 60.0},
    )
    path = tmp_path / "precise.toml"
    save_preset(preset, path)
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    assert section.import_method(path) is True
    stored = section.get_values()
    assert stored == {"preset": str(path.resolve()), "overrides": {}}

    restored = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(restored)
    restored.load(stored)

    assert restored.is_valid()
    settings = bind_cid_nl_recipe(restored.selected_method(), "full").discovery_settings
    assert settings.nl_tolerance_ppm == 12.3456789


def test_invalid_import_leaves_current_method_and_overrides(
    qtbot, tmp_path: Path
) -> None:
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    section.set_override("nl_tolerance_ppm", 12.5)
    before = section.get_values()
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("unknown = true", encoding="utf-8")

    assert section.import_method(invalid) is False

    assert section.get_values() == before
    assert section.is_valid()


def test_displayed_custom_method_survives_external_file_change(
    qtbot, tmp_path: Path
) -> None:
    from xic_extractor.presets import save_preset

    path = tmp_path / "selected.toml"
    save_preset(load_preset("dna_dr"), path, discovery_overrides={"rt_max": 60.0})
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    assert section.import_method(path)
    before = section.selected_method()
    path.write_text("invalid external edit", encoding="utf-8")

    section.refresh_theme()

    assert section.selected_method() == before
    assert section.is_valid()


def test_gui_request_binds_displayed_method_after_external_edit(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    import gui.views.untargeted_view as module
    from gui.workers.discovery_worker import DiscoveryWorker
    from xic_extractor.presets import save_preset

    path = tmp_path / "selected.toml"
    save_preset(load_preset("dna_dr"), path, discovery_overrides={"rt_max": 60.0})
    config = {
        "mode": "full",
        "preset": str(path),
        "overrides": {},
        "raw_dir": str(tmp_path),
        "dll_dir": str(tmp_path),
        "output_dir": str(tmp_path / "output"),
    }
    monkeypatch.setattr(module, "read_discovery_config", lambda: config)
    view = module.UntargetedView()
    qtbot.addWidget(view)
    path.write_text("invalid external edit", encoding="utf-8")

    request = view._build_request(view._current_config())
    settings = DiscoveryWorker(request)._build_settings(request)

    expected = bind_cid_nl_recipe(
        load_preset("dna_dr"), "full", recipe_overrides={"rt_max": 60.0}
    ).discovery_settings
    assert settings == expected


def test_apply_and_save_are_independent_and_restart_uses_same_owner(
    qtbot, tmp_path, monkeypatch
):
    from gui import discovery_config_io

    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path / "config")
    editor = MethodEditorDialog(load_preset("dna_dr"))
    qtbot.addWidget(editor)
    editor._select_tag("RNA_MeR", 146.0579)
    assert "未套用" in editor._state.text()
    portable = tmp_path / "portable.toml"
    assert editor.save_to(portable)
    assert editor.applied_preset is None
    assert editor.apply_current()
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    assert section.apply_method(editor.applied_preset)
    stored = section.get_values()
    assert stored["preset"] != str(portable)
    restored = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(restored)
    restored.load(stored)
    assert restored.is_valid()
    gui_settings = bind_cid_nl_recipe(
        restored.selected_method(), "full"
    ).discovery_settings
    cli_settings = bind_cid_nl_recipe(load_preset(portable), "full").discovery_settings
    assert gui_settings == cli_settings
    assert gui_settings.tag_combine_mode == "union"
    assert gui_settings.rt_max == 60.0


def test_unsupported_editor_preserves_original_strategy(qtbot, tmp_path):
    preset = load_preset("pt_pattern_cid_nl_dr")
    editor = MethodEditorDialog(preset)
    qtbot.addWidget(editor)
    assert not editor.apply_current()
    destination = tmp_path / "method.toml"
    assert not editor.save_to(destination)
    assert not destination.exists()
    assert editor.applied_preset is None
    assert "純 CID-NL" in editor.validation_message()


def test_out_of_range_import_preserves_current_method(qtbot, tmp_path):
    from xic_extractor.presets import save_preset

    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    before = section.get_values()
    path = tmp_path / "long_run.toml"
    save_preset(load_preset("dna_dr"), path, discovery_overrides={"rt_max": 90.0})
    assert not section.import_method(path)
    assert section.get_values() == before
    assert "0–60" in section._method_message.text()
    assert (
        bind_cid_nl_recipe(load_preset(path), "full").discovery_settings.rt_max == 90.0
    )


@pytest.mark.parametrize("combine_mode", ["union", "intersection"])
def test_checked_tags_and_combine_mode_round_trip(qtbot, tmp_path, combine_mode):
    editor = MethodEditorDialog(load_preset("dna_dr"))
    qtbot.addWidget(editor)
    assert editor._tags.rowCount() == 3
    editor._tags.item(1, 0).setCheckState(Qt.CheckState.Checked)
    editor._tags.item(1, 1).setText("132.0423456789")
    editor._combine.setCurrentIndex(editor._combine.findData(combine_mode))
    assert "132.0423456789 Da" in editor._summary.text()
    path = tmp_path / "checked.toml"
    assert editor.save_to(path)
    settings = bind_cid_nl_recipe(load_preset(path), "full").discovery_settings
    assert settings.selected_tag_names == ("DNA_dR", "RNA_ribose")
    assert settings.tag_combine_mode == combine_mode
    assert settings.neutral_loss_profiles[1].neutral_loss_da == 132.0423456789


def test_no_checked_tags_cannot_replace_current_method(qtbot):
    editor = MethodEditorDialog(load_preset("dna_dr"))
    qtbot.addWidget(editor)
    editor._tags.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert not editor.apply_current()
    assert editor.applied_preset is None


def test_new_method_can_apply_a_builtin_tag_without_an_empty_custom_row(
    qtbot, monkeypatch, tmp_path
):
    from gui import discovery_config_io

    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path / "config")

    def choose_dr(editor):
        editor._select_tag("DNA_dR", 116.0474)
        assert editor.apply_current()
        return editor.result()

    monkeypatch.setattr(MethodEditorDialog, "exec", choose_dr)
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    section._new_method()
    selected = section.selected_method()
    assert selected.name == "新方法"
    assert tuple(tag.name for tag in selected.tags) == ("DNA_dR",)
    assert section.is_valid()
