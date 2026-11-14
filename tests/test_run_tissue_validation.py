from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_tissue_validation
from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.configuration.execution_profile import default_parallel_workers
from xic_extractor.discovery.blank_qualification import BlankBackgroundLibrary
from xic_extractor.raw_reader import EXPECTED_THERMO_DLLS


@pytest.fixture(autouse=True)
def _placeholder_raw_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    # RAW files here are placeholders; order them by their file times.
    install_fake_raw_headers(monkeypatch)


def _inputs(tmp_path: Path, *, count: int) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw_dir = tmp_path / f"raw-{count}"
    raw_dir.mkdir()
    for index in range(count):
        (raw_dir / f"sample-{index:03d}.raw").write_bytes(b"")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir(exist_ok=True)
    for name in EXPECTED_THERMO_DLLS:
        (dll_dir / name).write_bytes(b"")
    library = tmp_path / "background.json"
    BlankBackgroundLibrary(ions=(), min_intensity=25_000.0, source_blanks=()).save_json(
        library
    )
    return raw_dir, dll_dir, library


def _preflight_args(
    tmp_path: Path, *, dataset: int, raw_dir: Path, dll_dir: Path, library: Path
) -> list[str]:
    return [
        "--dataset",
        str(dataset),
        "--raw-dir",
        str(raw_dir),
        "--dll-dir",
        str(dll_dir),
        "--blank-library",
        str(library),
        "--output-dir",
        str(tmp_path / f"out-{dataset}"),
        "--preflight-only",
    ]


@pytest.mark.parametrize("blank_enabled", [False, True])
def test_preflight_8_and_85_share_science_without_output_or_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    blank_enabled: bool,
) -> None:
    monkeypatch.setattr(run_tissue_validation, "preflight_raw_reader", lambda _path: [])
    executed: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        run_tissue_validation,
        "_run_command",
        lambda command, _log: executed.append(tuple(command)) or 0,
    )
    if not blank_enabled:
        monkeypatch.setattr(
            run_tissue_validation.BlankBackgroundLibrary,
            "load_json",
            lambda _path: pytest.fail("disabled filtering must not read a library"),
        )
    plans = []
    for dataset in (8, 85):
        raw_dir, dll_dir, library = _inputs(tmp_path / str(dataset), count=dataset)
        output_dir = tmp_path / str(dataset) / f"out-{dataset}"
        code = run_tissue_validation.main(
            _preflight_args(
                tmp_path / str(dataset),
                dataset=dataset,
                raw_dir=raw_dir,
                dll_dir=dll_dir,
                library=library,
            )
            if blank_enabled
            else [
                "--dataset",
                str(dataset),
                "--raw-dir",
                str(raw_dir),
                "--dll-dir",
                str(dll_dir),
                "--output-dir",
                str(output_dir),
                "--preflight-only",
            ]
        )
        assert code == 0
        assert not output_dir.exists()
        plans.append(json.loads(capsys.readouterr().out))

    from scripts import run_alignment, run_discovery
    from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec

    for plan in plans:
        discovery_args = run_discovery._parse_args(plan["commands"]["discovery"][3:])
        alignment_args = run_alignment._parse_args(plan["commands"]["alignment"][3:])
        discovery_settings = run_discovery._discovery_settings(discovery_args)
        _, recipe = run_alignment._resolve_preset(alignment_args)
        assert CidNlDiscoveryMethodSpec(settings=discovery_settings) == (
            CidNlDiscoveryMethodSpec(settings=recipe.discovery_settings)
        )

    assert executed == []
    assert plans[0]["science"] == plans[1]["science"]
    assert (
        plans[0]["science"]["discovery"]["enable_blank_qualification"] is blank_enabled
    )
    if not blank_enabled:
        assert all(p["inputs"]["blank_library"] is None for p in plans)
    assert plans[0]["execution"]["parallel_workers"] == default_parallel_workers()
    assert plans[1]["execution"]["parallel_workers"] == default_parallel_workers()
    assert plans[0]["execution"]["alignment_workers"] == 4
    assert plans[1]["execution"]["alignment_workers"] == 4
    assert plans[0]["inputs"]["raw_count"] == 8
    assert plans[1]["inputs"]["raw_count"] == 85


def test_preflight_rejects_wrong_raw_count_before_creating_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_dir, dll_dir, library = _inputs(tmp_path, count=7)
    output_dir = tmp_path / "out-8"
    monkeypatch.setattr(run_tissue_validation, "preflight_raw_reader", lambda _path: [])

    code = run_tissue_validation.main(
        _preflight_args(
            tmp_path,
            dataset=8,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            library=library,
        )
    )

    assert code == 2
    assert "expected 8 analysis RAW files, found 7" in capsys.readouterr().err
    assert not output_dir.exists()


def test_preflight_exposes_overrides_and_rejects_output_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_dir, dll_dir, library = _inputs(tmp_path, count=8)
    targets = tmp_path / "targets.xlsx"
    targets.write_bytes(
        Path("xic_extractor/configuration/data/targets.xlsx").read_bytes()
    )
    output_dir = tmp_path / "custom-output"
    monkeypatch.setattr(run_tissue_validation, "preflight_raw_reader", lambda _path: [])

    code = run_tissue_validation.main(
        [
            "--dataset",
            "8",
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--blank-library",
            str(library),
            "--targets-csv",
            str(targets),
            "--workers",
            "2",
            "--alignment-workers",
            "3",
            "--output-dir",
            str(output_dir),
            "--preflight-only",
        ]
    )
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["execution"]["parallel_workers"] == 2
    assert plan["execution"]["alignment_workers"] == 3
    discovery_command = plan["commands"]["discovery"]
    alignment_command = plan["commands"]["alignment"]
    assert discovery_command[discovery_command.index("--max-workers") + 1] == "2"
    assert alignment_command[alignment_command.index("--raw-workers") + 1] == "3"
    assert plan["inputs"]["targets_csv"] == str(targets.resolve())
    assert plan["inputs"]["blank_library"] == str(library.resolve())

    reused_output = tmp_path / "out-8"
    reused_output.mkdir()
    code = run_tissue_validation.main(
        _preflight_args(
            tmp_path,
            dataset=8,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            library=library,
        )
    )
    assert code == 2
    assert "output directory already exists" in capsys.readouterr().err


def test_execution_persists_plan_and_stops_after_failed_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_dir, dll_dir, library = _inputs(tmp_path, count=8)
    output_dir = tmp_path / "out-8"
    monkeypatch.setattr(run_tissue_validation, "preflight_raw_reader", lambda _path: [])
    executed: list[tuple[tuple[str, ...], Path]] = []

    def fail_discovery(command: list[str], log_path: Path) -> int:
        executed.append((tuple(command), log_path))
        return 9

    monkeypatch.setattr(run_tissue_validation, "_run_command", fail_discovery)

    code = run_tissue_validation.main(
        _preflight_args(
            tmp_path,
            dataset=8,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            library=library,
        )[:-1]
    )

    assert code == 9
    assert len(executed) == 1
    assert "scripts.run_discovery" in executed[0][0]
    assert (output_dir / "validation_plan.json").is_file()
    timings = json.loads(
        (output_dir / "stage_timings.json").read_text(encoding="utf-8")
    )
    assert timings["discovery"]["returncode"] == 9


@pytest.mark.parametrize(
    ("explicit", "configured", "expected"),
    [
        (None, None, None),
        (None, "env-cache", "env-cache"),
        ("cli-cache", "env-cache", "cli-cache"),
    ],
    ids=["run-own", "from-environment", "explicit-wins"],
)
def test_preflight_passes_a_shared_acquisition_cache_to_alignment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    explicit: str | None,
    configured: str | None,
    expected: str | None,
) -> None:
    raw_dir, dll_dir, _ = _inputs(tmp_path, count=8)
    monkeypatch.setattr(run_tissue_validation, "preflight_raw_reader", lambda _path: [])
    monkeypatch.setattr(run_tissue_validation, "_LOCAL_ENV", tmp_path / "absent.env")
    monkeypatch.delenv("XIC_ACQUISITION_CACHE_DIR", raising=False)
    if configured is not None:
        monkeypatch.setenv("XIC_ACQUISITION_CACHE_DIR", str(tmp_path / configured))
    argv = [
        "--dataset",
        "8",
        "--raw-dir",
        str(raw_dir),
        "--dll-dir",
        str(dll_dir),
        "--output-dir",
        str(tmp_path / "out"),
        "--preflight-only",
    ]
    if explicit is not None:
        argv += ["--acquisition-cache-dir", str(tmp_path / explicit)]

    assert run_tissue_validation.main(argv) == 0
    plan = json.loads(capsys.readouterr().out)
    command = plan["commands"]["alignment"]

    if expected is None:
        assert "--acquisition-cache-dir" not in command
        assert plan["inputs"]["acquisition_cache_dir"] is None
    else:
        want = str((tmp_path / expected).resolve())
        assert command[command.index("--acquisition-cache-dir") + 1] == want
        assert plan["inputs"]["acquisition_cache_dir"] == want
        from scripts import run_alignment

        parsed = run_alignment._parse_args(command[3:])
        assert parsed.acquisition_cache_dir == Path(want)


def test_85raw_preflight_applies_tracked_sample_metadata_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(run_tissue_validation, "preflight_raw_reader", lambda _path: [])
    raw_dir, dll_dir, _ = _inputs(tmp_path, count=85)
    tracked = run_tissue_validation._DATASET_SAMPLE_METADATA[85]
    excluded = [
        line.split("\t")[2]
        for line in tracked.read_text(encoding="utf-8").splitlines()[1:]
    ]
    assert len(excluded) == 6
    for stem in excluded:
        (raw_dir / "except sample").mkdir(exist_ok=True)
        (raw_dir / "except sample" / f"{stem}.raw").write_bytes(b"")

    code = run_tissue_validation.main(
        [
            "--dataset",
            "85",
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--preflight-only",
        ]
    )

    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["inputs"]["raw_count"] == 85
    assert plan["inputs"]["sample_metadata"] == str(tracked)
    discovery = plan["commands"]["discovery"]
    assert discovery[discovery.index("--sample-metadata") + 1] == str(tracked)
