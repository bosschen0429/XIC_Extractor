import json
import tomllib
from pathlib import Path

import pytest

from scripts import run_discovery
from xic_extractor.discovery.blank_qualification import (
    get_default_background_library_path,
)
from xic_extractor.discovery.models import DiscoveryBatchOutputs, DiscoveryRunOutputs
from xic_extractor.raw_reader import RawReaderError


@pytest.mark.parametrize("blank_override", [False, True])
def test_run_discovery_cli_passes_single_raw_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    blank_override: bool,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_text("", encoding="utf-8")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "out"
    captured = {}

    def _fake_run_discovery(
        raw_path_arg, *, output_dir, settings, peak_config, run_spec
    ):
        captured["raw_path"] = raw_path_arg
        captured["output_dir"] = output_dir
        captured["settings"] = settings
        captured["peak_config"] = peak_config
        captured["run_spec"] = run_spec
        output_dir.mkdir(parents=True, exist_ok=True)
        candidates_csv = output_dir / "discovery_candidates.csv"
        review_csv = output_dir / "discovery_review.csv"
        candidates_csv.write_text("review_priority\n", encoding="utf-8")
        review_csv.write_text("review_priority\n", encoding="utf-8")
        return DiscoveryRunOutputs(
            candidates_csv=candidates_csv,
            review_csv=review_csv,
        )

    monkeypatch.setattr(run_discovery, "run_discovery", _fake_run_discovery)

    code = run_discovery.main(
        [
            "--raw",
            str(raw_path),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--neutral-loss-da",
            "116.0474",
            "--neutral-loss-tag",
            "DNA_dR",
            "--ms2-precursor-tol-da",
            "0.75",
            "--resolver-mode",
            "local_minimum",
        ] + (
            ["--enable-blank-qualification", "--blank-library",
             str(get_default_background_library_path())]
            if blank_override else []
        )
    )

    assert code == 0
    assert captured["raw_path"] == raw_path.resolve()
    assert captured["output_dir"] == output_dir.resolve()
    settings = captured["settings"]
    assert settings.neutral_loss_profile.tag == "DNA_dR"
    assert settings.neutral_loss_profile.neutral_loss_da == 116.0474
    assert len(settings.neutral_loss_profiles) == 1
    assert settings.ms2_precursor_tol_da == pytest.approx(0.75)
    assert settings.resolver_mode == "local_minimum"
    peak_config = captured["peak_config"]
    assert peak_config.data_dir == raw_path.parent.resolve()
    assert peak_config.dll_dir == dll_dir.resolve()
    assert peak_config.output_csv == output_dir.resolve() / "xic_results.csv"
    assert peak_config.diagnostics_csv == output_dir.resolve() / "xic_diagnostics.csv"
    assert peak_config.resolver_mode == "local_minimum"
    assert peak_config.ms2_precursor_tol_da == pytest.approx(0.75)
    assert peak_config.nl_min_intensity_ratio == settings.nl_min_intensity_ratio
    assert captured["run_spec"].discovery_method.settings == settings
    assert captured["run_spec"].acquisition_inputs[0].sample_stem == raw_path.stem
    stdout = capsys.readouterr().out
    assert "Discovery candidates CSV: " in stdout
    assert "Discovery review CSV: " in stdout
    assert "discovery_candidates.csv" in stdout
    assert "discovery_review.csv" in stdout

def test_run_discovery_rejects_retired_arbitrated_resolver_mode(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_text("", encoding="utf-8")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        run_discovery.main(
            [
                "--raw",
                str(raw_path),
                "--dll-dir",
                str(dll_dir),
                "--resolver-mode",
                "arbitrated",
            ],
        )

    assert exc_info.value.code == 2
    assert "arbitrated resolver mode is retired" in capsys.readouterr().err

def test_run_discovery_cli_passes_raw_dir_batch_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    first = raw_dir / "B.raw"
    second = raw_dir / "A.raw"
    first.write_bytes(b"second acquisition")
    second.write_bytes(b"first acquisition")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "out"
    captured = {}
    execution_root = tmp_path / "runtime"
    (execution_root / "config").mkdir(parents=True)
    (execution_root / "config" / "settings.csv").write_text(
        "key,value\nparallel_workers,1\n", encoding="utf-8"
    )
    from xic_extractor.configuration import execution_profile as execution_settings

    monkeypatch.setattr(execution_settings, "_runtime_root", lambda: execution_root)

    def _fake_run_discovery_batch(
        raw_paths, *, output_dir, settings, peak_config, run_spec, max_workers
    ):
        captured["raw_paths"] = raw_paths
        captured["output_dir"] = output_dir
        captured["settings"] = settings
        captured["peak_config"] = peak_config
        captured["run_spec"] = run_spec
        captured["max_workers"] = max_workers
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "discovery_batch_index.csv"
        sample_outputs = DiscoveryRunOutputs(
            candidates_csv=output_dir / "A" / "discovery_candidates.csv",
            review_csv=output_dir / "A" / "discovery_review.csv",
        )
        output_path.write_text("sample_stem\n", encoding="utf-8")
        return DiscoveryBatchOutputs(
            batch_index_csv=output_path,
            per_sample=(sample_outputs,),
        )

    monkeypatch.setattr(
        run_discovery,
        "run_discovery_batch",
        _fake_run_discovery_batch,
    )

    code = run_discovery.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    assert captured["raw_paths"] == (second.resolve(), first.resolve())
    assert captured["output_dir"] == output_dir.resolve()
    assert captured["peak_config"].data_dir == raw_dir.resolve()
    assert captured["settings"].resolver_mode == "local_minimum"
    assert captured["max_workers"] == 1
    assert tuple(
        item.sample_stem for item in captured["run_spec"].acquisition_inputs
    ) == ("A", "B")
    stdout = capsys.readouterr().out
    assert "Discovery batch index: " in stdout
    assert "discovery_batch_index.csv" in stdout

def test_run_discovery_accepts_feature_list_selected_tags_and_union_mode() -> None:
    args = run_discovery._parse_args(
        [
            "--raw",
            "sample.raw",
            "--dll-dir",
            "C:/Xcalibur/system/programs",
            "--output-dir",
            "out",
            "--feature-list",
            "Feature_List.csv",
            "--selected-tags",
            "dR,R,MeR",
            "--tag-combine-mode",
            "union",
        ]
    )

    assert args.feature_list == Path("Feature_List.csv")
    assert args.selected_tags == "dR,R,MeR"
    assert args.tag_combine_mode == "union"

def test_run_discovery_rejects_invalid_tag_combine_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_discovery._parse_args(
            [
                "--raw",
                "sample.raw",
                "--dll-dir",
                "C:/Xcalibur/system/programs",
                "--tag-combine-mode",
                "invalid",
            ]
        )

    assert exc_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err

def test_run_discovery_cli_writes_live_and_final_timing_json_for_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    raw_path = raw_dir / "A.raw"
    raw_path.write_text("", encoding="utf-8")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "out"
    timing_path = tmp_path / "diagnostics" / "discovery_timing.json"
    from xic_extractor.discovery import processing_run_spec_adapter as adapter

    fingerprinted_paths: list[Path] = []
    owner = adapter.build_acquisition_source_identity

    def counted_fingerprint(path: Path, **kwargs):
        fingerprinted_paths.append(path)
        return owner(path, **kwargs)

    monkeypatch.setattr(
        adapter,
        "build_acquisition_source_identity",
        counted_fingerprint,
    )

    def _fake_run_discovery_batch(
        raw_paths,
        *,
        output_dir,
        settings,
        peak_config,
        run_spec,
        max_workers,
        timing_recorder=None,
    ):
        assert run_spec.discovery_method.settings == settings
        assert timing_recorder is not None
        with timing_recorder.stage(
            "discover.write_batch_index",
            metrics={"raw_count": len(raw_paths), "row_count": len(raw_paths)},
        ):
            pass
        live_payload = json.loads(timing_path.read_text(encoding="utf-8"))
        assert live_payload["recent_records"][-1]["stage"] == (
            "discover.write_batch_index"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "discovery_batch_index.csv"
        output_path.write_text("sample_stem\n", encoding="utf-8")
        return DiscoveryBatchOutputs(batch_index_csv=output_path, per_sample=())

    monkeypatch.setattr(run_discovery, "run_discovery_batch", _fake_run_discovery_batch)

    code = run_discovery.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--timing-output",
            str(timing_path),
        ]
    )

    assert code == 0
    payload = json.loads(timing_path.read_text(encoding="utf-8"))
    assert payload["pipeline"] == "discovery"
    assert [record["stage"] for record in payload["records"]] == [
        "discover.compile_run_spec",
        "discover.write_batch_index",
    ]
    assert payload["records"][0]["metrics"] == {"raw_count": 1}
    assert payload["records"][1]["metrics"] == {"raw_count": 1, "row_count": 1}
    assert fingerprinted_paths == [raw_path.resolve()]
    assert "Timing JSON:" in capsys.readouterr().out

def test_run_discovery_cli_rejects_missing_raw(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_discovery.main(
        [
            "--raw",
            str(tmp_path / "missing.raw"),
            "--dll-dir",
            str(dll_dir),
        ]
    )

    assert code == 2
    assert "raw file does not exist" in capsys.readouterr().err

def test_run_discovery_cli_rejects_raw_dir_without_raw_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_discovery.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
        ]
    )

    assert code == 2
    assert "raw directory contains no .raw files" in capsys.readouterr().err

def test_run_discovery_cli_rejects_missing_dll_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_text("", encoding="utf-8")

    code = run_discovery.main(
        [
            "--raw",
            str(raw_path),
            "--dll-dir",
            str(tmp_path / "missing-dll"),
        ]
    )

    assert code == 2
    assert "dll directory does not exist" in capsys.readouterr().err

def test_run_discovery_cli_rejects_non_positive_float_args(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_text("", encoding="utf-8")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        run_discovery.main(
            [
                "--raw",
                str(raw_path),
                "--dll-dir",
                str(dll_dir),
                "--neutral-loss-da",
                "0",
            ]
        )

    assert exc_info.value.code == 2
    assert "value must be > 0" in capsys.readouterr().err

@pytest.mark.parametrize("value", ["nan", "inf"])
def test_run_discovery_cli_rejects_non_finite_positive_float_args(
    value: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_text("", encoding="utf-8")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        run_discovery.main(
            [
                "--raw",
                str(raw_path),
                "--dll-dir",
                str(dll_dir),
                "--nl-tolerance-ppm",
                value,
            ]
        )

    assert exc_info.value.code == 2
    assert "value must be > 0" in capsys.readouterr().err

def test_run_discovery_cli_rejects_inverted_rt_window(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_text("", encoding="utf-8")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        run_discovery.main(
            [
                "--raw",
                str(raw_path),
                "--dll-dir",
                str(dll_dir),
                "--rt-min",
                "10",
                "--rt-max",
                "5",
            ]
        )

    assert exc_info.value.code == 2
    assert "rt-min must be <= rt-max" in capsys.readouterr().err

@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--rt-min", "-1"),
        ("--rt-max", "nan"),
        ("--rt-max", "inf"),
    ],
)
def test_run_discovery_cli_rejects_invalid_rt_bounds(
    option: str,
    value: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_text("", encoding="utf-8")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        run_discovery.main(
            [
                "--raw",
                str(raw_path),
                "--dll-dir",
                str(dll_dir),
                option,
                value,
            ]
        )

    assert exc_info.value.code == 2
    assert "RT bounds must be finite values >= 0" in capsys.readouterr().err

def test_run_discovery_cli_returns_2_when_raw_reader_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_text("", encoding="utf-8")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    def _raise_raw_reader_error(*_args, **_kwargs):
        raise RawReaderError("boom")

    monkeypatch.setattr(run_discovery, "run_discovery", _raise_raw_reader_error)

    code = run_discovery.main(
        [
            "--raw",
            str(raw_path),
            "--dll-dir",
            str(dll_dir),
        ]
    )

    assert code == 2
    assert "boom" in capsys.readouterr().err

def test_pyproject_registers_discovery_cli_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert (
        pyproject["project"]["scripts"]["xic-discovery-cli"]
        == "scripts.run_discovery:main"
    )
