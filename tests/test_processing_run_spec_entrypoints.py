from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from tests.discovery_worker_fixtures import _alignment_outputs
from xic_extractor.applications.untargeted.full_analysis import (
    FullAnalysisRequest,
    UntargetedAnalysis,
)
from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.presets import PresetError
from xic_extractor.processing_run_spec import ProcessingRunSpec
from xic_workbench.untargeted import parse_request


def test_actual_workbench_full_and_cli_main_reach_equal_final_run_spec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_config_dir: Path,
) -> None:
    import xic_extractor.applications.untargeted.full_analysis as workbench_module
    from scripts import run_discovery as cli_module
    from tests.raw_header_fakes import install_fake_raw_headers

    install_fake_raw_headers(monkeypatch)
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
        "sample_stem,peak_count,admitted_peak_count\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def worker_batch(*_args, **kwargs):
        captured["workbench"] = _args[0]
        captured["workbench_workers"] = kwargs["execution"].parallel_workers
        return SimpleNamespace(batch_index_csv=index_path, per_sample=())

    monkeypatch.setattr(workbench_module, "run_discovery_batch", worker_batch)
    monkeypatch.setattr(
        workbench_module,
        "read_analysis_set",
        lambda *_args, **_kwargs: SimpleNamespace(
            raw_paths=(raw_dir / "A.raw", raw_dir / "B.raw"),
            sample_metadata=(),
        ),
    )
    request = FullAnalysisRequest(
        preset="dna_dr",
        tuning_overrides={},
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=tmp_path / "full",
        execution_profile=ExecutionProfile(parallel_workers=1),
    )
    run = UntargetedAnalysis(
        request, progress=lambda *_args: None, should_cancel=lambda: False
    )
    monkeypatch.setattr(run, "_run_alignment", lambda **_kwargs: _alignment_outputs())
    run.execute()

    def cli_batch(*_args, **kwargs):
        captured["cli"] = _args[0]
        captured["cli_workers"] = kwargs["execution"].parallel_workers
        return SimpleNamespace(batch_index_csv=index_path, per_sample=())

    monkeypatch.setattr(cli_module, "run_discovery_batch", cli_batch)
    assert (
        cli_module.main(
            [
                "--max-workers",
                "2",
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
    assert captured["workbench_workers"] == 1
    assert captured["cli_workers"] == 2

    specs = tuple(
        cast(ProcessingRunSpec, captured[key]) for key in ("workbench", "cli")
    )
    assert {spec.settings.ms1_search_padding_min for spec in specs} == {0.35}
    assert specs[0] == specs[1]


def test_actual_workbench_entry_rejects_invalid_preset_before_fingerprint_or_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_extractor.applications.untargeted.full_analysis as workbench_module

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "sample.raw").write_bytes(b"sample")
    monkeypatch.setattr(
        "xic_extractor.discovery.processing_run_spec_adapter."
        "build_local_acquisition_source_identity",
        lambda *_args, **_kwargs: pytest.fail("fingerprinted invalid preset"),
    )
    monkeypatch.setattr(
        workbench_module,
        "run_discovery_batch",
        lambda *_args, **_kwargs: pytest.fail("opened RAW for invalid preset"),
    )

    with pytest.raises(PresetError):
        UntargetedAnalysis(
            parse_request(
                {
                    "preset": "no_such_preset",
                    "raw_dir": str(raw_dir),
                    "dll_dir": str(tmp_path / "dll"),
                    "output_dir": str(tmp_path / "out"),
                }
            ),
            progress=lambda *_: None,
            should_cancel=lambda: False,
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
        "build_local_acquisition_source_identity",
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
