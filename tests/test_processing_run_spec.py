from __future__ import annotations

import json
import pickle
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from gui.workers.discovery_worker import (
    DiscoveryRequest,
    DiscoveryWorker,
)
from gui.workers.discovery_worker import (
    _compile_processing_run_spec as compile_gui_processing_run_spec,
)
from scripts.run_discovery import (
    _compile_processing_run_spec as compile_cli_processing_run_spec,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
    validate_processing_run_spec_call,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.presets import PresetError
from xic_extractor.processing_run_spec import (
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
    canonical_processing_run_spec_bytes,
    compile_processing_run_spec,
    deserialize_processing_run_spec,
    serialize_processing_run_spec,
)


def _settings() -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile(
            tag="DNA_dR",
            neutral_loss_da=116.0474,
        )
    )


def _raws(tmp_path: Path) -> tuple[Path, Path]:
    first = tmp_path / "A.raw"
    second = tmp_path / "B.raw"
    first.write_bytes(b"first acquisition")
    second.write_bytes(b"second acquisition")
    return first, second


def _peak_config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("raw"),
        dll_dir=Path("dll"),
        output_csv=Path("output/xic_results.csv"),
        diagnostics_csv=Path("output/xic_diagnostics.csv"),
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _compile_paths(
    raw_paths: tuple[Path, ...],
    *,
    settings: DiscoverySettings | None = None,
):
    return compile_discovery_processing_run_spec(
        raw_paths,
        discovery_settings=settings or _settings(),
    )


def test_equal_gui_cli_inputs_compile_equal_immutable_identity(tmp_path: Path) -> None:
    raw_paths = _raws(tmp_path)
    settings = _settings()

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
        if mode == "full":
            monkeypatch.setattr(
                worker,
                "_run_alignment",
                lambda *_args, **_kwargs: (SimpleNamespace(), None),
            )
            monkeypatch.setattr(
                worker,
                "_build_gallery",
                lambda *_args, **_kwargs: {},
            )
        worker.run()

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

    specs = tuple(cast(object, captured[key]) for key in (
        "gui_full",
        "gui_discovery_only",
        "cli",
    ))
    serialized = tuple(
        serialize_processing_run_spec(cast(ProcessingRunSpec, spec))
        for spec in specs
    )
    assert serialized[0] == serialized[1] == serialized[2]
    assert len({cast(ProcessingRunSpec, spec).run_spec_id for spec in specs}) == 1
    assert len({cast(ProcessingRunSpec, spec).profile_id for spec in specs}) == 1
    assert len(
        {
            tuple(
                item.acquisition_source.acquisition_source_id
                for item in cast(ProcessingRunSpec, spec).acquisition_inputs
            )
            for spec in specs
        }
    ) == 1


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


def test_run_spec_roundtrip_closes_content_hash_profile_and_order(
    tmp_path: Path,
) -> None:
    raw_paths = _raws(tmp_path)
    spec = _compile_paths(raw_paths)
    payload = serialize_processing_run_spec(spec)

    assert (
        deserialize_processing_run_spec(
            payload,
            expected_run_spec_hash=spec.run_spec_hash,
            expected_run_spec_id=spec.run_spec_id,
        )
        == spec
    )
    reversed_spec = _compile_paths(tuple(reversed(raw_paths)))
    assert reversed_spec.run_spec_id != spec.run_spec_id


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value.pop("run_spec_id"),
        lambda value: value.__setitem__("run_spec_hash", "0" * 64),
        lambda value: value["content"]["discovery_method"].__setitem__(
            "method_hash", "0" * 64
        ),
        lambda value: value["content"]["signal_processing_profile"].__setitem__(
            "profile_id", "signal-profile.v1:" + "0" * 64
        ),
        lambda value: value["content"]["acquisition_inputs"][0][
            "acquisition_source"
        ].__setitem__("content_sha256", "0" * 64),
    ],
)
def test_run_spec_rejects_unknown_partial_tampered_and_cross_content(
    tmp_path: Path,
    mutation,
) -> None:
    spec = _compile_paths(_raws(tmp_path))
    value = json.loads(serialize_processing_run_spec(spec))
    mutation(value)

    with pytest.raises((TypeError, ValueError)):
        deserialize_processing_run_spec(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            expected_run_spec_id=spec.run_spec_id,
        )


def test_run_spec_rejects_duplicate_and_reordered_json_fields(tmp_path: Path) -> None:
    spec = _compile_paths(_raws(tmp_path))
    payload = serialize_processing_run_spec(spec).decode("utf-8")
    duplicate = payload.replace(
        '{"schema_version":"processing_run_spec_envelope_v1",',
        '{"schema_version":"processing_run_spec_envelope_v1",'
        '"schema_version":"processing_run_spec_envelope_v1",',
        1,
    )
    value = json.loads(payload)
    reordered = json.dumps(
        {
            "run_spec_hash": value["run_spec_hash"],
            "schema_version": value["schema_version"],
            "run_spec_id": value["run_spec_id"],
            "content": value["content"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with pytest.raises(ValueError, match="duplicate"):
        deserialize_processing_run_spec(duplicate)
    with pytest.raises(ValueError, match="fields must be exactly"):
        deserialize_processing_run_spec(reordered)


def test_run_spec_call_rejects_conflicts_before_runtime(tmp_path: Path) -> None:
    raw_paths = _raws(tmp_path)
    settings = _settings()
    spec = _compile_paths(raw_paths, settings=settings)

    with pytest.raises(ValueError, match="settings conflict"):
        validate_processing_run_spec_call(
            spec,
            raw_paths,
            discovery_settings=DiscoverySettings(
                neutral_loss_profile=NeutralLossProfile("DNA_dR", 98.0)
            ),
            signal_processing_profile=SignalProcessingProfile(),
        )
    with pytest.raises(ValueError, match="profile"):
        validate_processing_run_spec_call(
            spec,
            raw_paths,
            discovery_settings=settings,
            signal_processing_profile=SignalProcessingProfile(window_points=21),
        )
    with pytest.raises(ValueError, match="runtime RAW path"):
        validate_processing_run_spec_call(
            spec,
            (raw_paths[0], tmp_path / "C.raw"),
            discovery_settings=settings,
            signal_processing_profile=SignalProcessingProfile(),
        )


def test_run_spec_pure_owner_compiles_typed_inputs(tmp_path: Path) -> None:
    adapter_spec = _compile_paths(_raws(tmp_path))
    pure_spec = compile_processing_run_spec(
        adapter_spec.acquisition_inputs,
        discovery_settings=_settings(),
    )

    assert pure_spec == adapter_spec


def test_fresh_run_spec_import_adds_only_the_owner_module() -> None:
    code = textwrap.dedent(
        """
        import importlib.abc
        import sys

        import xic_extractor.processing_identity
        import xic_extractor.peak_detection.signal_processing_profile
        baseline = {
            name for name in sys.modules if name.startswith("xic_extractor")
        }

        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.startswith("xic_extractor.discovery"):
                    raise RuntimeError(f"blocked Discovery import: {fullname}")
                return None

        blocker = Blocker()
        sys.meta_path.insert(0, blocker)
        import xic_extractor.processing_run_spec
        delta = {
            name for name in sys.modules
            if name.startswith("xic_extractor") and name not in baseline
        }
        assert delta == {"xic_extractor.processing_run_spec"}, sorted(delta)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_run_spec_runtime_local_imports_keep_forbidden_modules_absent() -> None:
    code = textwrap.dedent(
        """
        import importlib.abc
        import sys
        import tempfile
        from pathlib import Path

        import xic_extractor.processing_identity as identity
        import xic_extractor.peak_detection.signal_processing_profile
        import xic_extractor.processing_run_spec as owner
        from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile

        prohibited = {
            "xic_extractor.discovery.pipeline",
            "xic_extractor.discovery.ms1_backfill",
            "xic_extractor.discovery.ms2_seeds",
            "xic_extractor.discovery.csv_writer",
            "xic_extractor.raw_reader",
            "gui",
            "scripts.run_discovery",
        }

        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname in prohibited:
                    raise RuntimeError(f"blocked runtime import: {fullname}")
                return None

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.raw"
            path.write_bytes(b"run-spec-runtime-import")
            source = identity.build_acquisition_source_identity(
                path,
                acquisition_format="thermo_raw",
                observation_namespace="thermo_raw_scan_v1",
            )
            acquisition = owner.ProcessingAcquisitionInput(
                input_ordinal=0,
                adapter_input_key=path.name,
                sample_stem=path.stem,
                acquisition_source=source,
            )
            settings = DiscoverySettings(
                neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
            )
            sys.meta_path.insert(0, Blocker())
            spec = owner.compile_processing_run_spec(
                (acquisition,), discovery_settings=settings
            )
            restored = owner.deserialize_processing_run_spec(
                owner.serialize_processing_run_spec(spec)
            )
            assert restored == spec
            assert prohibited.isdisjoint(sys.modules), sorted(
                prohibited.intersection(sys.modules)
            )
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_run_spec_rejects_duplicate_acquisition_content_aliases_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.raw"
    alias = tmp_path / "alias.raw"
    first.write_bytes(b"identical acquisition")
    alias.write_bytes(b"identical acquisition")
    calls: list[Path] = []
    from xic_extractor.discovery import processing_run_spec_adapter as adapter

    owner = adapter.build_acquisition_source_identity

    def counted(path: Path, **kwargs):
        calls.append(path)
        return owner(path, **kwargs)

    monkeypatch.setattr(adapter, "build_acquisition_source_identity", counted)

    with pytest.raises(ValueError, match="duplicate.*acquisition_source_id"):
        compile_discovery_processing_run_spec(
            (first, alias),
            discovery_settings=_settings(),
        )

    assert calls == [first, alias]


def test_pure_run_spec_rejects_same_source_under_different_names(
    tmp_path: Path,
) -> None:
    spec = _compile_paths(_raws(tmp_path))
    source = spec.acquisition_inputs[0].acquisition_source

    with pytest.raises(ValueError, match="duplicate.*acquisition_source_id"):
        compile_processing_run_spec(
            (
                spec.acquisition_inputs[0],
                ProcessingAcquisitionInput(
                    input_ordinal=1,
                    adapter_input_key="renamed.raw",
                    sample_stem="renamed",
                    acquisition_source=source,
                ),
            ),
            discovery_settings=_settings(),
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


def test_run_spec_rejects_invalid_direct_profile_type(tmp_path: Path) -> None:
    valid = _compile_paths(_raws(tmp_path))
    with pytest.raises(TypeError, match="SignalProcessingProfile"):
        compile_processing_run_spec(
            valid.acquisition_inputs,
            discovery_settings=_settings(),
            signal_processing_profile="gaussian",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="method"):
        SignalProcessingProfile(method="savgol")
