from __future__ import annotations

import pickle
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from gui.workers.discovery_worker import DiscoveryRequest, DiscoveryWorker
from gui.workers.discovery_worker import (
    _compile_processing_run_spec as compile_gui_processing_run_spec,
)
from scripts.run_discovery import (
    _compile_processing_run_spec as compile_cli_processing_run_spec,
)
from tests.test_discovery_worker import _alignment_outputs
from tests.test_processing_run_spec import _peak_config, _raws, _settings
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.presets import PresetError
from xic_extractor.processing_run_spec import (
    ProcessingRunSpec,
    canonical_processing_run_spec_bytes,
    serialize_processing_run_spec,
)


def test_equal_gui_cli_inputs_compile_equal_immutable_identity(tmp_path: Path) -> None:
    from xic_extractor.discovery.processing_run_spec_adapter import (
        compile_discovery_processing_run_spec_from_config,
    )

    raw_paths = _raws(tmp_path)
    settings = _settings()

    assert compile_gui_processing_run_spec is (
        compile_discovery_processing_run_spec_from_config
    )
    assert compile_cli_processing_run_spec is (
        compile_discovery_processing_run_spec_from_config
    )
    peak_config = _peak_config()
    gui_spec = compile_gui_processing_run_spec(raw_paths, settings, peak_config)
    cli_spec = compile_cli_processing_run_spec(raw_paths, settings, peak_config)

    assert gui_spec == cli_spec
    assert gui_spec.run_spec_id == cli_spec.run_spec_id
    assert gui_spec.profile_id == cli_spec.profile_id
    assert canonical_processing_run_spec_bytes(gui_spec) == (
        canonical_processing_run_spec_bytes(cli_spec)
    )
    assert serialize_processing_run_spec(gui_spec) == serialize_processing_run_spec(
        cli_spec
    )
    assert str(tmp_path).encode("utf-8") not in serialize_processing_run_spec(gui_spec)
    assert pickle.loads(pickle.dumps(gui_spec)) == gui_spec


def test_actual_gui_modes_and_cli_main_reach_equal_final_run_spec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gui.workers import discovery_worker as worker_module
    from scripts import run_discovery as cli_module

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "B.raw").write_bytes(b"second acquisition")
    (raw_dir / "A.raw").write_bytes(b"first acquisition")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "out"
    index_path = output_dir / "discovery_batch_index.csv"
    index_path.parent.mkdir()
    index_path.write_text(
        "sample,high_count,medium_count,low_count,candidate_count\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def worker_batch(*_args, **kwargs):
        mode = kwargs["output_dir"].name
        captured[f"gui_{mode}"] = kwargs["run_spec"]
        return SimpleNamespace(batch_index_csv=index_path, per_sample=())

    monkeypatch.setattr(worker_module, "run_discovery_batch", worker_batch)
    finished: list[dict[str, object]] = []
    errors: list[str] = []
    for mode, output_name in (("full", "full"), ("discovery_only", "discovery_only")):
        request = DiscoveryRequest(
            mode=cast(object, mode),  # type: ignore[arg-type]
            preset="dna_dr",
            tuning_overrides={},
            raw_dir=raw_dir,
            raw_file=None,
            dll_dir=dll_dir,
            output_dir=tmp_path / output_name,
            discovery_batch_index=None,
        )
        worker = DiscoveryWorker(request)
        worker.finished.connect(finished.append)
        worker.error.connect(errors.append)
        if mode == "full":
            outputs = _alignment_outputs()
            monkeypatch.setattr(
                worker,
                "_run_alignment",
                lambda *_args, **_kwargs: outputs,
            )
        worker.run()

    assert errors == []
    assert len(finished) == 2

    def cli_batch(*_args, **kwargs):
        captured["cli"] = kwargs["run_spec"]
        return SimpleNamespace(batch_index_csv=index_path, per_sample=())

    monkeypatch.setattr(cli_module, "run_discovery_batch", cli_batch)
    assert (
        cli_module.main(
            [
                "--raw-dir",
                str(raw_dir),
                "--dll-dir",
                str(dll_dir),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )

    specs = tuple(
        cast(object, captured[key])
        for key in (
            "gui_full",
            "gui_discovery_only",
            "cli",
        )
    )
    serialized = tuple(
        serialize_processing_run_spec(cast(ProcessingRunSpec, spec)) for spec in specs
    )
    assert serialized[0] == serialized[1] == serialized[2]
    assert {
        cast(ProcessingRunSpec, spec).discovery_method.settings.ms1_search_padding_min
        for spec in specs
    } == {0.35}
    assert len({cast(ProcessingRunSpec, spec).run_spec_id for spec in specs}) == 1
    assert len({cast(ProcessingRunSpec, spec).profile_id for spec in specs}) == 1
    assert (
        len(
            {
                tuple(
                    item.acquisition_source.acquisition_source_id
                    for item in cast(ProcessingRunSpec, spec).acquisition_inputs
                )
                for spec in specs
            }
        )
        == 1
    )


def test_actual_gui_entry_rejects_invalid_preset_before_fingerprint_or_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gui.workers import discovery_worker as worker_module

    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sample")
    monkeypatch.setattr(
        "xic_extractor.discovery.processing_run_spec_adapter."
        "build_acquisition_source_identity",
        lambda *_args, **_kwargs: pytest.fail("fingerprinted invalid preset"),
    )
    monkeypatch.setattr(
        worker_module,
        "run_discovery",
        lambda *_args, **_kwargs: pytest.fail("opened RAW for invalid preset"),
    )
    request = DiscoveryRequest(
        mode="discovery_only",
        preset="pt_pattern",
        tuning_overrides={},
        raw_dir=None,
        raw_file=raw_path,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        discovery_batch_index=None,
    )

    with pytest.raises(PresetError):
        DiscoveryWorker(request)._run_discovery_only(request)


def test_actual_gui_entry_rejects_invalid_profile_before_fingerprint_or_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gui.workers import discovery_worker as worker_module

    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sample")
    monkeypatch.setattr(
        "xic_extractor.discovery.processing_run_spec_adapter."
        "build_acquisition_source_identity",
        lambda *_args, **_kwargs: pytest.fail("fingerprinted invalid profile"),
    )
    monkeypatch.setattr(
        worker_module,
        "run_discovery",
        lambda *_args, **_kwargs: pytest.fail("opened RAW for invalid profile"),
    )
    request = DiscoveryRequest(
        mode="discovery_only",
        preset="dna_dr",
        tuning_overrides={},
        raw_dir=None,
        raw_file=raw_path,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        discovery_batch_index=None,
    )
    worker = DiscoveryWorker(request)
    monkeypatch.setattr(
        worker,
        "_discovery_peak_config",
        lambda *_args: SimpleNamespace(
            signal_processing_method="savgol",
            signal_processing_window_points=15,
        ),
    )

    with pytest.raises(ValueError, match="method"):
        worker._run_discovery_only(request)


def test_actual_cli_entry_rejects_invalid_method_before_fingerprint_or_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import run_discovery as cli_module

    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sample")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    monkeypatch.setattr(
        "xic_extractor.discovery.processing_run_spec_adapter."
        "build_acquisition_source_identity",
        lambda *_args, **_kwargs: pytest.fail("fingerprinted invalid method"),
    )
    monkeypatch.setattr(
        cli_module,
        "run_discovery",
        lambda *_args, **_kwargs: pytest.fail("opened RAW for invalid method"),
    )

    with pytest.raises(SystemExit):
        cli_module.main(
            [
                "--raw",
                str(raw_path),
                "--dll-dir",
                str(dll_dir),
                "--resolver-mode",
                "arbitrated",
            ]
        )


@pytest.mark.parametrize(
    "builder",
    [compile_gui_processing_run_spec, compile_cli_processing_run_spec],
)
def test_highest_entry_rejects_invalid_profile_before_fingerprinting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    builder,
) -> None:
    raw_paths = _raws(tmp_path)
    invalid = cast(
        ExtractionConfig,
        SimpleNamespace(
            signal_processing_method="savgol",
            signal_processing_window_points=15,
        ),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.processing_run_spec_adapter."
        "build_acquisition_source_identity",
        lambda *_args, **_kwargs: pytest.fail("fingerprinted invalid profile"),
    )

    with pytest.raises(ValueError, match="method"):
        builder(raw_paths, _settings(), invalid)


def test_adapter_rejects_invalid_method_before_fingerprinting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_paths = _raws(tmp_path)
    monkeypatch.setattr(
        "xic_extractor.discovery.processing_run_spec_adapter."
        "build_acquisition_source_identity",
        lambda *_args, **_kwargs: pytest.fail("fingerprinted invalid method"),
    )
    with pytest.raises(TypeError, match="DiscoverySettings"):
        compile_discovery_processing_run_spec(
            raw_paths,
            discovery_settings=cast(DiscoverySettings, object()),
        )


def test_run_spec_conflicts_fail_before_raw_opener(tmp_path: Path) -> None:
    from tests.test_discovery_pipeline import _FakeRawHandle, _peak_config, _settings
    from xic_extractor.discovery.pipeline import run_discovery

    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"compiled acquisition")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    opens: list[Path] = []

    def _open(path: Path, _dll_dir: Path):
        opens.append(path)
        return _FakeRawHandle(events=[])

    with pytest.raises(ValueError, match="settings conflict"):
        run_discovery(
            raw_path,
            output_dir=tmp_path / "settings-out",
            settings=replace(settings, seed_rt_gap_min=0.33),
            peak_config=_peak_config(tmp_path),
            raw_opener=_open,
            run_spec=spec,
        )
    with pytest.raises(ValueError, match="peak config conflicts"):
        run_discovery(
            raw_path,
            output_dir=tmp_path / "profile-out",
            settings=settings,
            peak_config=replace(
                _peak_config(tmp_path),
                signal_processing_window_points=21,
            ),
            raw_opener=_open,
            run_spec=spec,
        )
    with pytest.raises(TypeError, match="ProcessingRunSpec"):
        run_discovery(
            raw_path,
            output_dir=tmp_path / "invalid-out",
            settings=settings,
            peak_config=_peak_config(tmp_path),
            raw_opener=_open,
            run_spec=object(),  # type: ignore[arg-type]
        )
    assert opens == []
