from dataclasses import replace
from pathlib import Path

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
        bind_cid_nl_recipe(original, "full").discovery_settings
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
        discovery_overrides={"nl_tolerance_ppm": 12.3456789},
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
    save_preset(load_preset("dna_dr"), path)
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
    save_preset(load_preset("dna_dr"), path)
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

    expected = bind_cid_nl_recipe(load_preset("dna_dr"), "full").discovery_settings
    assert settings == expected
