from __future__ import annotations

import pickle
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from scripts.run_discovery import (
    compile_discovery_processing_run_spec as compile_cli_processing_run_spec,
)
from tests.test_discovery_worker import _alignment_outputs
from tests.test_processing_run_spec import _raws, _settings
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
from xic_workbench.untargeted import DiscoveryRequest, UntargetedRun, parse_request
from xic_workbench.untargeted import (
    compile_discovery_processing_run_spec as compile_workbench_processing_run_spec,
)


def test_equal_workbench_cli_inputs_compile_equal_immutable_identity(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.processing_run_spec_adapter import (
        compile_discovery_processing_run_spec,
    )

    raw_paths = _raws(tmp_path)
    settings = _settings()

    assert compile_workbench_processing_run_spec is (
        compile_discovery_processing_run_spec
    )
    assert compile_cli_processing_run_spec is (compile_discovery_processing_run_spec)
    workbench_spec = compile_workbench_processing_run_spec(
        raw_paths, discovery_settings=settings
    )
    cli_spec = compile_cli_processing_run_spec(raw_paths, discovery_settings=settings)

    assert workbench_spec == cli_spec
    assert workbench_spec.run_spec_id == cli_spec.run_spec_id
    assert workbench_spec.profile_id == cli_spec.profile_id
    assert canonical_processing_run_spec_bytes(workbench_spec) == (
        canonical_processing_run_spec_bytes(cli_spec)
    )
    assert serialize_processing_run_spec(
        workbench_spec
    ) == serialize_processing_run_spec(cli_spec)
    assert str(tmp_path).encode("utf-8") not in serialize_processing_run_spec(
        workbench_spec
    )
    assert pickle.loads(pickle.dumps(workbench_spec)) == workbench_spec


def test_actual_workbench_full_and_cli_main_reach_equal_final_run_spec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_config_dir: Path,
) -> None:
    import xic_workbench.untargeted as workbench_module
    from scripts import run_discovery as cli_module
    from tests.raw_header_fakes import install_fake_raw_headers

    install_fake_raw_headers(monkeypatch)
    from xic_extractor.configuration.workspace_state import save_workspace

    save_workspace(isolated_config_dir, settings={"parallel_workers": "2"})
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
        captured["workbench"] = kwargs["run_spec"]
        return SimpleNamespace(batch_index_csv=index_path, per_sample=())

    monkeypatch.setattr(workbench_module, "run_discovery_batch", worker_batch)
    monkeypatch.setattr(
        workbench_module,
        "read_analysis_set",
        lambda *_args: SimpleNamespace(
            raw_paths=(raw_dir / "A.raw", raw_dir / "B.raw")
        ),
    )
    request = DiscoveryRequest(
        mode="full",
        preset="dna_dr",
        tuning_overrides={},
        raw_dir=raw_dir,
        raw_file=None,
        dll_dir=dll_dir,
        output_dir=tmp_path / "full",
        discovery_batch_index=None,
    )
    run = UntargetedRun(
        request, progress=lambda *_args: None, should_cancel=lambda: False
    )
    monkeypatch.setattr(run, "_run_alignment", lambda **_kwargs: _alignment_outputs())
    run.execute()

    def cli_batch(*_args, **kwargs):
        captured["cli"] = kwargs["run_spec"]
        captured["cli_workers"] = kwargs["max_workers"]
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
    assert captured["cli_workers"] == 2

    specs = tuple(cast(object, captured[key]) for key in ("workbench", "cli"))
    serialized = tuple(
        serialize_processing_run_spec(cast(ProcessingRunSpec, spec)) for spec in specs
    )
    assert serialized[0] == serialized[1]
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


def test_actual_workbench_entry_rejects_invalid_preset_before_fingerprint_or_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_workbench.untargeted as workbench_module

    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sample")
    monkeypatch.setattr(
        "xic_extractor.discovery.processing_run_spec_adapter."
        "build_acquisition_source_identity",
        lambda *_args, **_kwargs: pytest.fail("fingerprinted invalid preset"),
    )
    monkeypatch.setattr(
        workbench_module,
        "run_discovery",
        lambda *_args, **_kwargs: pytest.fail("opened RAW for invalid preset"),
    )

    with pytest.raises(PresetError):
        parse_request(
            {
                "mode": "discovery_only",
                "preset": "pt_pattern",
                "raw_file": str(raw_path),
                "dll_dir": str(tmp_path / "dll"),
                "output_dir": str(tmp_path / "out"),
            }
        )


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
            dll_dir=_peak_config(tmp_path).dll_dir,
            raw_opener=_open,
            run_spec=spec,
        )
    with pytest.raises(TypeError, match="ProcessingRunSpec"):
        run_discovery(
            raw_path,
            output_dir=tmp_path / "invalid-out",
            settings=settings,
            dll_dir=_peak_config(tmp_path).dll_dir,
            raw_opener=_open,
            run_spec=object(),  # type: ignore[arg-type]
        )
    assert opens == []


def test_adapter_rejects_invalid_profile_before_fingerprinting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )

    monkeypatch.setattr(
        "xic_extractor.discovery.processing_run_spec_adapter.build_acquisition_source_identity",
        lambda *_args, **_kwargs: pytest.fail("fingerprinted invalid profile"),
    )
    with pytest.raises(TypeError, match="SignalProcessingProfile"):
        compile_discovery_processing_run_spec(
            _raws(tmp_path),
            discovery_settings=_settings(),
            signal_processing_profile=cast(SignalProcessingProfile, object()),
        )
