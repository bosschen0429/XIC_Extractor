from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_discovery


def test_run_discovery_main_rejects_duplicate_acquisition_content_before_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    (raw_dir / "A.raw").write_bytes(b"duplicate acquisition")
    (raw_dir / "B.raw").write_bytes(b"duplicate acquisition")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    pipeline_calls = 0

    def unexpected_pipeline(*_args, **_kwargs):
        nonlocal pipeline_calls
        pipeline_calls += 1
        pytest.fail("pipeline entered after run-spec compile failure")

    monkeypatch.setattr(run_discovery, "run_discovery_batch", unexpected_pipeline)

    code = run_discovery.main(
        ["--raw-dir", str(raw_dir), "--dll-dir", str(dll_dir)]
    )

    assert code == 2
    assert pipeline_calls == 0
    error = capsys.readouterr().err
    assert error.startswith("Discovery run specification failed: ")
    assert "duplicate ProcessingRunSpec acquisition_source_id" in error
    assert len(error.rstrip("\n")) <= 400


def test_run_discovery_main_propagates_unexpected_pipeline_value_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"pipeline-sentinel")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    def raise_pipeline_sentinel(*_args, **_kwargs):
        raise ValueError("pipeline sentinel must propagate")

    monkeypatch.setattr(run_discovery, "run_discovery", raise_pipeline_sentinel)

    with pytest.raises(ValueError, match="^pipeline sentinel must propagate$"):
        run_discovery.main(
            ["--raw", str(raw_path), "--dll-dir", str(dll_dir)]
        )

def test_run_discovery_cli_records_failed_compile_and_hashes_each_input_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    first = raw_dir / "A.raw"
    second = raw_dir / "B.raw"
    first.write_bytes(b"duplicate acquisition")
    second.write_bytes(b"duplicate acquisition")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    timing_path = tmp_path / "diagnostics" / "discovery_timing.json"
    from xic_extractor.discovery import processing_run_spec_adapter as adapter

    fingerprinted_paths: list[Path] = []
    owner = adapter.build_acquisition_source_identity

    def counted_fingerprint(path: Path, **kwargs):
        fingerprinted_paths.append(path)
        return owner(path, **kwargs)

    def unexpected_pipeline(*_args, **_kwargs):
        pytest.fail("pipeline entered after run-spec compile failure")

    monkeypatch.setattr(
        adapter,
        "build_acquisition_source_identity",
        counted_fingerprint,
    )
    monkeypatch.setattr(run_discovery, "run_discovery_batch", unexpected_pipeline)

    code = run_discovery.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--timing-output",
            str(timing_path),
        ]
    )

    assert code == 2
    assert fingerprinted_paths == [first.resolve(), second.resolve()]
    payload = json.loads(timing_path.read_text(encoding="utf-8"))
    assert [record["stage"] for record in payload["recent_records"]] == [
        "discover.compile_run_spec"
    ]
    assert payload["recent_records"][0]["metrics"] == {"raw_count": 2}
    assert "duplicate ProcessingRunSpec acquisition_source_id" in (
        capsys.readouterr().err
    )
