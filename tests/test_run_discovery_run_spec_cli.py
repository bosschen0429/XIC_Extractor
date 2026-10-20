from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_discovery
from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.discovery.sample_process_execution import (
    DiscoveryProcessExecutionError,
    DiscoverySampleProcessError,
)
from xic_extractor.presets import (
    PresetTag,
    bind_cid_nl_recipe,
    load_preset,
    save_preset,
)
from xic_extractor.raw_reader import RawReaderError


@pytest.fixture(autouse=True)
def _placeholder_raw_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    # RAW files here are placeholders; order them by their file times.
    install_fake_raw_headers(monkeypatch)


@pytest.mark.parametrize("timing", [False, True])
@pytest.mark.parametrize("workers", [1, 2])
def test_discovery_cli_reports_batch_execution_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    timing: bool,
    workers: int,
) -> None:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    for stem in ("A", "B"):
        (raw_dir / f"{stem}.raw").write_bytes(stem.encode())
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    monkeypatch.setattr(
        run_discovery,
        "load_execution_profile",
        lambda *, parallel_workers=None: ExecutionProfile(
            parallel_workers=workers if parallel_workers is None else parallel_workers
        ),
    )

    def fail_batch(_paths, **kwargs):
        assert kwargs["max_workers"] == workers
        assert (kwargs["timing_recorder"] is not None) is timing
        if workers == 1:
            raise RawReaderError("A.raw: unable to open RAW")
        raise DiscoveryProcessExecutionError(
            DiscoverySampleProcessError(
                sample_index=0,
                sample_stem="A",
                raw_name="A.raw",
                message="unable to open RAW",
            ),
            completed_sample_stems=(),
            failed_sample_stems=("A",),
            cancelled_job_count=0,
            in_flight_job_count=1,
            unsubmitted_job_count=0,
        )

    monkeypatch.setattr(run_discovery, "run_discovery_batch", fail_batch)
    args = [
        "--raw-dir",
        str(raw_dir),
        "--dll-dir",
        str(dll_dir),
        "--output-dir",
        str(tmp_path / "out"),
    ]
    if timing:
        args.extend(("--timing-output", str(tmp_path / "timing.json")))

    assert run_discovery.main(args) == 2
    output = capsys.readouterr()
    assert output.err == "A.raw: unable to open RAW\n"
    assert output.out == ""


@pytest.mark.parametrize("timing", [False, True])
@pytest.mark.parametrize(
    ("input_mode", "default_workers", "override", "expected_workers"),
    [
        ("raw-dir", 8, None, 2),
        ("raw-dir", 1, None, 1),
        ("raw-dir", 8, 1, 1),
        ("raw-dir", 1, 3, 2),
        ("raw", 8, None, None),
        ("raw", 8, 3, None),
    ],
)
def test_discovery_cli_forwards_worker_budget_and_run_spec(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timing: bool,
    input_mode: str,
    default_workers: int,
    override: int | None,
    expected_workers: int | None,
) -> None:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    raw_paths = (raw_dir / "A.raw", raw_dir / "B.raw")
    for path in raw_paths:
        path.write_bytes(path.stem.encode())
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    observed = []

    def capture_pipeline(paths, **kwargs):
        observed.append((paths, kwargs))
        return SimpleNamespace(
            batch_index_csv=tmp_path / "batch.csv",
            candidates_csv=tmp_path / "candidates.csv",
            review_csv=tmp_path / "review.csv",
        )

    monkeypatch.setattr(
        run_discovery,
        "load_execution_profile",
        lambda *, parallel_workers=None: ExecutionProfile(
            parallel_workers=(
                default_workers if parallel_workers is None else parallel_workers
            )
        ),
    )
    monkeypatch.setattr(run_discovery, "run_discovery", capture_pipeline)
    monkeypatch.setattr(run_discovery, "run_discovery_batch", capture_pipeline)
    args = [
        f"--{input_mode}",
        str(raw_dir if input_mode == "raw-dir" else raw_paths[0]),
        "--dll-dir",
        str(dll_dir),
        "--output-dir",
        str(tmp_path / "out"),
    ]
    if override is not None:
        args.extend(("--max-workers", str(override)))
    if timing:
        args.extend(("--timing-output", str(tmp_path / "timing.json")))
    assert run_discovery.main(args) == 0
    ((paths, forwarded),) = observed
    assert paths == (raw_paths if input_mode == "raw-dir" else raw_paths[0])
    if expected_workers is None:
        assert "max_workers" not in forwarded
    else:
        assert forwarded["max_workers"] == expected_workers
    assert (forwarded["timing_recorder"] is not None) is timing
    assert forwarded["run_spec"].discovery_method.settings == forwarded["settings"]
    assert not forwarded["settings"].enable_blank_qualification
    assert all(
        item.blank_library_sha256 is None
        for item in forwarded["run_spec"].acquisition_inputs
    )


@pytest.mark.parametrize("value", ["0", "-1", "1.5"])
def test_discovery_cli_rejects_nonpositive_or_noninteger_worker_budget(
    value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        run_discovery._parse_args(
            ["--raw-dir", "raws", "--dll-dir", "dll", "--max-workers", value]
        )
    assert exc.value.code == 2
    assert "--max-workers" in capsys.readouterr().err


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
        run_discovery, "compile_discovery_processing_run_spec", unexpected_compile
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
