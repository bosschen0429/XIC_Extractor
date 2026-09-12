from __future__ import annotations

from copy import deepcopy

import pytest

from gui.sections.discovery_method_section import DiscoveryMethodSection


@pytest.mark.parametrize(
    "overrides",
    [
        None,
        {"unknown": 1.0},
        {"rt_min": True},
        {"rt_min": "1.0"},
        {"rt_min": float("nan")},
        {"rt_min": 1_000.0},
        {"rt_min": 0.0001},
        {"resolver_mode": "arbitrated"},
        {"rt_min": 10.0, "rt_max": 9.0},
    ],
)
def test_persisted_override_bundle_fails_closed_without_partial_application(
    qtbot,
    overrides: object,
) -> None:
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    default_rt_min = section.numeric_value("rt_min")
    persisted = {"preset": "dna_dr", "overrides": deepcopy(overrides)}

    section.load(persisted)

    assert section.is_valid() is False
    assert "已保存的方法覆寫無效" in section.validation_reason()
    assert section.get_values() == persisted
    assert section.numeric_value("rt_min") == default_rt_min


@pytest.mark.parametrize(
    "method_bundle",
    [
        {
            "preset": "dna_dr",
            "overrides": {"future_owner": {"nested": [1, 2]}},
        },
        {
            "preset": "retired_cid_nl",
            "overrides": {"future_owner": {"nested": [1, 2]}},
        },
    ],
)
def test_quarantine_deepcopies_complete_opaque_method_bundle(
    qtbot,
    method_bundle: dict[str, object],
) -> None:
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    expected = deepcopy(method_bundle)

    section.load(method_bundle)
    method_bundle["preset"] = "mutated"
    overrides = method_bundle["overrides"]
    assert isinstance(overrides, dict)
    nested = overrides["future_owner"]
    assert isinstance(nested, dict)
    nested["nested"] = [999]

    first = section.get_values()
    assert first == expected
    first["preset"] = "mutated-again"
    first_overrides = first["overrides"]
    assert isinstance(first_overrides, dict)
    first_overrides.clear()
    assert section.get_values() == expected
    assert section.is_valid() is False


@pytest.mark.parametrize(
    "method_bundle",
    [
        {"preset": "dna_dr", "overrides": {"rt_min": 8.0, "rt_max": 7.0}},
        {
            "preset": "retired_cid_nl",
            "overrides": {"future_owner": {"opaque": True}},
        },
    ],
)
def test_method_quarantine_blocks_request_compiler_fingerprint_and_raw(
    monkeypatch: pytest.MonkeyPatch,
    qtbot,
    tmp_path,
    method_bundle: dict[str, object],
) -> None:
    import gui.views.untargeted_view as module

    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "output"
    for path in (raw_dir, dll_dir, output_dir):
        path.mkdir()
    persisted = {
        "mode": "full",
        "preset": "dna_dr",
        "raw_dir": str(raw_dir),
        "raw_file": "",
        "discovery_batch_index": "",
        "dll_dir": str(dll_dir),
        "output_dir": str(output_dir),
        **deepcopy(method_bundle),
    }
    calls = {"worker": 0, "compiler": 0, "fingerprint": 0, "raw": 0}

    def unexpected_worker(*_args, **_kwargs):
        calls["worker"] += 1
        pytest.fail("worker compiled a quarantined persisted bundle")

    def unexpected_fingerprint(*_args, **_kwargs):
        calls["fingerprint"] += 1
        pytest.fail("fingerprinted a quarantined persisted bundle")

    def unexpected_compiler(*_args, **_kwargs):
        calls["compiler"] += 1
        pytest.fail("compiled a quarantined persisted bundle")

    def unexpected_raw(*_args, **_kwargs):
        calls["raw"] += 1
        pytest.fail("opened RAW for a quarantined persisted bundle")

    monkeypatch.setattr(module, "read_discovery_config", lambda: persisted)
    monkeypatch.setattr(module, "DiscoveryWorker", unexpected_worker)
    monkeypatch.setattr(
        "gui.workers.discovery_worker._compile_processing_run_spec",
        unexpected_compiler,
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.processing_run_spec_adapter."
        "build_acquisition_source_identity",
        unexpected_fingerprint,
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.run_discovery",
        unexpected_raw,
    )

    view = module.UntargetedView()
    qtbot.addWidget(view)

    assert view._run._button.isEnabled() is False
    view._run._button.click()
    with pytest.raises(ValueError):
        view._build_request(view._current_config())
    assert {
        key: view._current_config()[key] for key in ("preset", "overrides")
    } == method_bundle
    assert calls == {
        "worker": 0,
        "compiler": 0,
        "fingerprint": 0,
        "raw": 0,
    }


@pytest.mark.parametrize(
    "event",
    [
        "refresh_theme",
        "load_valid_bundle",
        "preset_changed_signal",
        "numeric_value_changed",
        "editing_finished_without_change",
        "resolver_index_changed_signal",
    ],
)
def test_programmatic_events_do_not_clear_invalid_override_quarantine(
    qtbot,
    event: str,
) -> None:
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    persisted = {
        "preset": "dna_dr",
        "overrides": {"future_owner": {"nested": [1, 2]}},
    }
    section.load(persisted)

    if event == "refresh_theme":
        section.refresh_theme()
    elif event == "load_valid_bundle":
        section.load({"preset": "dna_dr", "overrides": {}})
    elif event == "preset_changed_signal":
        section._preset.currentIndexChanged.emit(section._preset.currentIndex())
    elif event == "numeric_value_changed":
        section._spins["rt_min"].setValue(section.numeric_value("rt_min") + 1.0)
    elif event == "editing_finished_without_change":
        section._spins["rt_min"].editingFinished.emit()
    else:
        section._resolver.currentIndexChanged.emit(section._resolver.currentIndex())

    assert section.is_valid() is False
    assert section.get_values() == persisted


def test_unavailable_preset_requires_explicit_valid_preset_activation(qtbot) -> None:
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    persisted = {
        "preset": "retired_cid_nl",
        "overrides": {"future_owner": {"nested": [1, 2]}},
    }

    section.load(persisted)
    section.refresh_theme()
    section.load({"preset": "dna_dr", "overrides": {}})
    section._preset.setCurrentIndex(section._preset.findData("dna_dr"))
    section._spins["rt_min"].setValue(section.numeric_value("rt_min") + 1.0)
    section._spins["rt_min"].editingFinished.emit()

    assert section.is_valid() is False
    assert section.get_values() == persisted

    section._preset.activated.emit(section._preset.currentIndex())

    assert section.is_valid() is True
    assert section.get_values() == {"preset": "dna_dr", "overrides": {"rt_max": 60.0}}


def test_invalid_override_quarantine_clears_on_explicit_preset_reselect(
    qtbot,
) -> None:
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    invalid = {"rt_min": 8.0, "rt_max": 7.0}

    section.load({"preset": "dna_dr", "overrides": invalid})
    assert section.is_valid() is False
    section._preset.activated.emit(section._preset.currentIndex())
    assert section.is_valid() is True
    assert section.get_values()["overrides"] == {"rt_max": 60.0}


def test_invalid_override_quarantine_clears_only_after_valid_method_edit(
    qtbot,
) -> None:
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)
    invalid = {"future_owner": {"nested": [1, 2]}}

    section.load({"preset": "dna_dr", "overrides": invalid})
    assert section.is_valid() is False
    section._spins["rt_min"].setValue(section.numeric_value("rt_min") + 1.0)
    assert section.is_valid() is False
    assert section.get_values()["overrides"] == invalid

    section._spins["rt_min"].editingFinished.emit()

    assert section.is_valid() is True
    assert section.get_values()["overrides"] == {
        "rt_min": section.numeric_value("rt_min"),
        "rt_max": 60.0,
    }


@pytest.mark.parametrize(
    "method_bundle",
    [
        {"preset": "dna_dr", "overrides": {"future_owner": {"value": 1}}},
        {
            "preset": "retired_cid_nl",
            "overrides": {"future_owner": {"value": 1}},
        },
    ],
)
def test_quarantined_bundle_survives_persist_close_and_reload(
    monkeypatch: pytest.MonkeyPatch,
    qtbot,
    tmp_path,
    method_bundle: dict[str, object],
) -> None:
    import gui.views.untargeted_view as module

    source = {
        "mode": "full",
        "raw_dir": str(tmp_path / "raw"),
        "raw_file": "",
        "discovery_batch_index": "",
        "dll_dir": str(tmp_path / "dll"),
        "output_dir": str(tmp_path / "output"),
    }
    stored = {"config": {**source, **deepcopy(method_bundle)}}
    monkeypatch.setattr(
        module,
        "read_discovery_config",
        lambda: deepcopy(stored["config"]),
    )
    monkeypatch.setattr(
        module,
        "write_discovery_config",
        lambda config: stored.update(config=deepcopy(config)),
    )

    view = module.UntargetedView()
    qtbot.addWidget(view)
    view.persist_config()
    view.close()
    reloaded = module.UntargetedView()
    qtbot.addWidget(reloaded)

    expected = deepcopy(method_bundle)
    assert {key: stored["config"][key] for key in expected} == expected
    assert {key: reloaded._current_config()[key] for key in expected} == expected
    assert reloaded._method.is_valid() is False
