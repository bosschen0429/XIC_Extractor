from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_discovery
from xic_extractor.presets import (
    PresetTag,
    bind_cid_nl_recipe,
    load_preset,
    save_preset,
)


@pytest.mark.parametrize("combine_mode", ["union", "intersection"])
def test_discovery_cli_runs_saved_method_with_only_explicit_tuning_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    combine_mode: str,
) -> None:
    preset = replace(
        load_preset("dna_dr"),
        tags=(
            PresetTag("neutral_loss", "A", 116.0474),
            PresetTag("neutral_loss", "B", 132.0423),
        ),
        combine_mode=combine_mode,
        discovery_overrides={"nl_tolerance_ppm": 12.345, "rt_min": 2.0},
    )
    method = tmp_path / "method.toml"
    saved = save_preset(preset, method)
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"saved-method-cli")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    observed = []

    def capture_pipeline(_raw_path, **kwargs):
        observed.append(kwargs["settings"])
        assert kwargs["run_spec"].discovery_method.settings == kwargs["settings"]
        return SimpleNamespace(
            candidates_csv=tmp_path / "candidates.csv",
            review_csv=tmp_path / "review.csv",
        )

    monkeypatch.setattr(run_discovery, "run_discovery", capture_pipeline)
    assert (
        run_discovery.main(
            [
                "--raw",
                str(raw_path),
                "--dll-dir",
                str(dll_dir),
                "--preset",
                str(method),
                "--rt-max",
                "10",
            ]
        )
        == 0
    )
    assert observed == [
        bind_cid_nl_recipe(
            saved,
            "discovery_only",
            recipe_overrides={"rt_max": 10.0},
        ).discovery_settings
    ]


@pytest.mark.parametrize(
    "selector",
    [
        ["--neutral-loss-tag", "other"],
        ["--neutral-loss-da", "100"],
        ["--feature-list", "tags.csv"],
        ["--selected-tags", "A"],
        ["--tag-combine-mode", "union"],
    ],
)
def test_discovery_cli_rejects_competing_method_sources(selector) -> None:
    with pytest.raises(SystemExit) as exc:
        run_discovery._parse_args(
            [
                "--raw",
                "sample.raw",
                "--dll-dir",
                "dll",
                "--preset",
                "dna_dr",
                *selector,
            ]
        )
    assert exc.value.code == 2


def test_discovery_cli_reports_unreadable_preset_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"preset-read-error")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    method = tmp_path / "method.toml"
    method.write_text("unreadable method", encoding="utf-8")
    read_bytes = Path.read_bytes

    def unreadable_method(path: Path) -> bytes:
        if path == method:
            raise PermissionError("preset access denied " + "x" * 500)
        return read_bytes(path)

    def unexpected_compile(*_args, **_kwargs):
        pytest.fail("execution entered after preset read failure")

    monkeypatch.setattr(Path, "read_bytes", unreadable_method)
    monkeypatch.setattr(
        run_discovery, "_compile_processing_run_spec", unexpected_compile
    )
    code = run_discovery.main(
        ["--raw", str(raw_path), "--dll-dir", str(dll_dir), "--preset", str(method)]
    )

    assert code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.startswith(
        "Discovery run specification failed: preset access denied"
    )
    assert len(output.err.rstrip("\n")) <= 400
    assert "Traceback" not in output.err


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

    code = run_discovery.main(["--raw-dir", str(raw_dir), "--dll-dir", str(dll_dir)])

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
        run_discovery.main(["--raw", str(raw_path), "--dll-dir", str(dll_dir)])


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
