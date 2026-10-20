from pathlib import Path

import pytest

from xic_extractor.discovery.blank_qualification import (
    BlankBackgroundIon,
    BlankBackgroundLibrary,
    get_default_background_library,
    get_default_background_library_path,
)


@pytest.mark.parametrize("suffix", [".json", ".json.gz"])
def test_loaded_unordered_library_still_matches_background(
    tmp_path: Path,
    suffix: str,
) -> None:
    import gzip
    import json

    path = tmp_path / f"library{suffix}"
    payload = {
        "version": "blank_background_library",
        "min_intensity": 25000.0,
        "source_blanks": ["B1"],
        "ions": [
            {"mz": mz, "max_intensity": 100000.0, "scan_count": 5, "source_blank": "B1"}
            for mz in [300.0, 100.0, 200.0]
        ],
    }
    if suffix.endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle)
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    library = BlankBackgroundLibrary.load_json(path)
    (match,) = library.match(300.0, ppm_tol=10.0)
    assert match[0].mz == 300.0


def test_background_library_snapshot_preserves_precision_and_rejects_noncanonical() -> (
    None
):
    import json

    from xic_extractor.discovery.blank_qualification import (
        deserialize_background_library,
        serialize_background_library,
    )

    library = BlankBackgroundLibrary(
        ions=(
            BlankBackgroundIon(
                mz=300.12345678912345,
                max_intensity=12345.6789012345,
                scan_count=5,
                source_blank="B1",
            ),
        ),
        min_intensity=10000.0,
        source_blanks=("B1",),
    )
    payload = serialize_background_library(library)
    restored = deserialize_background_library(payload)
    assert restored.ions[0].mz == 300.12345678912345
    assert restored.ions[0].max_intensity == 12345.6789012345
    assert restored == library
    with pytest.raises(ValueError):
        deserialize_background_library(payload + " ")
    with_extra_key = json.loads(payload)
    with_extra_key["unexpected"] = "not part of the library"
    with pytest.raises(ValueError):
        deserialize_background_library(
            json.dumps(
                with_extra_key,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


def test_blank_library_matching():
    ions = (
        BlankBackgroundIon(
            mz=200.0000, max_intensity=100000.0, scan_count=5, source_blank="B1"
        ),
        BlankBackgroundIon(
            mz=250.1234, max_intensity=50000.0, scan_count=10, source_blank="B1,B3"
        ),
        BlankBackgroundIon(
            mz=500.2500, max_intensity=200000.0, scan_count=8, source_blank="B1"
        ),
    )
    lib = BlankBackgroundLibrary(
        ions=ions,
        min_intensity=25000.0,
        source_blanks=("B1", "B3"),
    )

    # Match exact
    matches = lib.match(250.1234, ppm_tol=5.0)
    assert len(matches) == 1
    assert matches[0][0].mz == 250.1234
    assert abs(matches[0][1]) < 0.001

    # Match within 10 ppm
    # 250.1234 * 8 / 1e6 = 0.00200098 -> 250.1254 is ~8 ppm away
    matches_8ppm = lib.match(250.1254, ppm_tol=10.0)
    assert len(matches_8ppm) == 1
    assert matches_8ppm[0][0].mz == 250.1234

    # Miss when outside tolerance
    matches_miss = lib.match(250.1254, ppm_tol=5.0)
    assert len(matches_miss) == 0

    # Miss completely far mz
    assert len(lib.match(300.0, ppm_tol=10.0)) == 0


def test_blank_library_serialization(tmp_path: Path):
    ions = (
        BlankBackgroundIon(
            mz=279.0960, max_intensity=1500000.0, scan_count=50, source_blank="Blank_1"
        ),
        BlankBackgroundIon(
            mz=391.1110,
            max_intensity=890000.0,
            scan_count=45,
            source_blank="Blank_1,Blank_3",
        ),
    )
    lib = BlankBackgroundLibrary(
        ions=ions,
        min_intensity=25000.0,
        source_blanks=("Blank_1", "Blank_3"),
    )

    json_file = tmp_path / "test_library.json"
    lib.save_json(json_file)
    assert json_file.exists()

    loaded = BlankBackgroundLibrary.load_json(json_file)
    assert len(loaded.ions) == 2
    assert loaded.min_intensity == 25000.0
    assert loaded.source_blanks == ("Blank_1", "Blank_3")
    assert loaded.ions[0].mz == 279.0960
    assert loaded.ions[0].max_intensity == 1500000.0
    assert loaded.ions[1].mz == 391.1110


def test_default_background_library_packaged_and_loadable():
    from xic_extractor.discovery.blank_qualification import (
        get_default_background_library,
        get_default_background_library_path,
        resolve_background_library,
    )

    path = get_default_background_library_path()
    assert path.is_file(), f"Expected packaged library at {path}"

    lib = get_default_background_library()
    assert lib is not None
    assert len(lib.ions) == 34659
    assert len(lib.source_blanks) == 11

    # The packaged library stays loadable on request, but a run never inherits
    # it. It was measured on a 15-minute gradient; applying it to a run acquired
    # on another method would demote candidates to review_only_orphan_nl with
    # ms1_peak_found=False against a background population that run never saw.
    assert resolve_background_library() is None
    assert resolve_background_library(raw_dir=None, library_path=path) is not None


def test_resolve_background_library_missing_explicit_path_raises(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.blank_qualification import resolve_background_library

    missing = tmp_path / "nonexistent_blank_lib.json"
    with pytest.raises(FileNotFoundError, match="Blank background library not found"):
        resolve_background_library(library_path=missing)


@pytest.mark.parametrize("preset", [None, "dna_dr"])
@pytest.mark.parametrize("enabled", [False, True])
def test_discovery_only_filters_background_when_explicitly_enabled(
    tmp_path: Path,
    preset: str | None,
    enabled: bool,
):
    from scripts.run_discovery import _discovery_settings, _parse_args
    from xic_extractor.discovery.blank_qualification import (
        BlankBackgroundIon,
        BlankBackgroundLibrary,
    )
    from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySeed
    from xic_extractor.discovery.pipeline import _apply_blank_qualification_if_enabled
    from xic_extractor.discovery.processing_run_spec_adapter import (
        compile_discovery_processing_run_spec,
    )
    from xic_extractor.execution.timing import TimingRecorder

    lib = BlankBackgroundLibrary(
        ions=(
            BlankBackgroundIon(
                mz=300.1000,
                max_intensity=100000.0,
                scan_count=10,
                source_blank="B1",
            ),
            BlankBackgroundIon(
                mz=400.2000,
                max_intensity=50000.0,
                scan_count=10,
                source_blank="B1",
            ),
        ),
        min_intensity=25000.0,
        source_blanks=("B1",),
    )

    seed = DiscoverySeed(
        raw_file=Path("test.raw"),
        sample_stem="test",
        scan_number=1,
        rt=5.0,
        precursor_mz=300.1000,
        product_mz=184.0,
        product_intensity=5000.0,
        neutral_loss_tag="tag",
        configured_neutral_loss_da=116.0,
        observed_neutral_loss_da=116.0,
        observed_loss_error_ppm=1.0,
    )

    c_background = DiscoveryCandidate.from_values(
        raw_file=Path("test.raw"),
        sample_stem="test",
        precursor_mz=300.1002,  # ~0.67 ppm match
        product_mz=184.0,
        observed_neutral_loss_da=116.0,
        best_seed=seed,
        seed_scan_ids=(1,),
        neutral_loss_tag="tag",
        configured_neutral_loss_da=116.0,
        neutral_loss_mass_error_ppm=1.0,
        rt_seed_min=4.9,
        rt_seed_max=5.1,
        ms1_search_rt_min=4.8,
        ms1_search_rt_max=5.2,
        ms1_seed_delta_min=0.01,
        ms1_peak_rt_start=4.9,
        ms1_peak_rt_end=5.1,
        ms1_height=80000.0,  # FC = 80000 / 100000 = 0.8 < 3.0 -> SYSTEM_BACKGROUND
        ms1_trace_quality="clean",
        seed_event_count=2,
        ms1_peak_found=True,
        ms1_apex_rt=5.01,
        ms1_area=80000.0,
        ms2_product_max_intensity=5000.0,
        review_priority="HIGH",
        reason="initial candidate",
    )

    c_override = DiscoveryCandidate.from_values(
        raw_file=Path("test.raw"),
        sample_stem="test",
        precursor_mz=400.2001,  # ~0.25 ppm match
        product_mz=184.0,
        observed_neutral_loss_da=116.0,
        best_seed=seed,
        seed_scan_ids=(1,),
        neutral_loss_tag="tag",
        configured_neutral_loss_da=116.0,
        neutral_loss_mass_error_ppm=1.0,
        rt_seed_min=4.9,
        rt_seed_max=5.1,
        ms1_search_rt_min=4.8,
        ms1_search_rt_max=5.2,
        ms1_seed_delta_min=0.01,
        ms1_peak_rt_start=4.9,
        ms1_peak_rt_end=5.1,
        ms1_height=400000.0,  # FC = 400000 / 50000 = 8.0 >= 3.0 -> OVERRIDE
        ms1_trace_quality="clean",
        seed_event_count=2,
        ms1_peak_found=True,
        ms1_apex_rt=5.01,
        ms1_area=400000.0,
        ms2_product_max_intensity=5000.0,
        review_priority="HIGH",
        reason="initial candidate",
    )

    c_clean = DiscoveryCandidate.from_values(
        raw_file=Path("test.raw"),
        sample_stem="test",
        precursor_mz=550.3000,  # Not in blank -> CLEAN
        product_mz=184.0,
        observed_neutral_loss_da=116.0,
        best_seed=seed,
        seed_scan_ids=(1,),
        neutral_loss_tag="tag",
        configured_neutral_loss_da=116.0,
        neutral_loss_mass_error_ppm=1.0,
        rt_seed_min=4.9,
        rt_seed_max=5.1,
        ms1_search_rt_min=4.8,
        ms1_search_rt_max=5.2,
        ms1_seed_delta_min=0.01,
        ms1_peak_rt_start=4.9,
        ms1_peak_rt_end=5.1,
        ms1_height=150000.0,
        ms1_trace_quality="clean",
        seed_event_count=2,
        ms1_peak_found=True,
        ms1_apex_rt=5.01,
        ms1_area=150000.0,
        ms2_product_max_intensity=5000.0,
        review_priority="HIGH",
        reason="initial candidate",
    )

    raw = tmp_path / "test.raw"
    raw.write_bytes(b"sample")
    lib.save_json(tmp_path / "blank_library.json")
    args = ["--raw", str(raw), "--dll-dir", "dll"]
    if enabled:
        args.append("--enable-blank-qualification")
    if preset is not None:
        args.extend(["--preset", preset])
    settings = _discovery_settings(_parse_args(args))
    spec = compile_discovery_processing_run_spec(
        (raw,),
        discovery_settings=settings,
    )
    assert (spec.acquisition_inputs[0].blank_library_sha256 is not None) is enabled
    qualified = _apply_blank_qualification_if_enabled(
        (c_background, c_override, c_clean),
        settings=settings,
        timing_recorder=TimingRecorder.disabled("discovery"),
        sample_stem="test",
        acquisition_input=spec.acquisition_inputs[0],
    )

    if not enabled:
        assert qualified == (c_background, c_override, c_clean)
        return

    # c_background demoted
    assert qualified[0].discovery_candidate_state == "review_only_orphan_nl"
    assert qualified[0].ms1_peak_found is False
    assert qualified[0].ms1_feature_row_id == ""
    assert qualified[0].review_priority == "LOW"
    assert "system background: blank m/z 300.1000" in qualified[0].reason

    # c_override preserved
    assert qualified[1].discovery_candidate_state == "ms1_feature_nl_supported"
    assert qualified[1].review_priority == "HIGH"
    assert "high abundance tissue signal (8.0x)" in qualified[1].reason

    # c_clean preserved unchanged
    assert qualified[2].discovery_candidate_state == "ms1_feature_nl_supported"
    assert qualified[2].review_priority == "HIGH"
    assert qualified[2].reason == "initial candidate"


def test_blank_library_gzip_serialization(tmp_path: Path) -> None:
    ions = (
        BlankBackgroundIon(
            mz=279.0960, max_intensity=1500000.0, scan_count=50, source_blank="Blank_1"
        ),
        BlankBackgroundIon(
            mz=391.1110,
            max_intensity=890000.0,
            scan_count=45,
            source_blank="Blank_1,Blank_3",
        ),
    )
    lib = BlankBackgroundLibrary(
        ions=ions,
        min_intensity=25000.0,
        source_blanks=("Blank_1", "Blank_3"),
    )

    gz_file = tmp_path / "test_library.json.gz"
    lib.save_json(gz_file)
    assert gz_file.exists()

    loaded = BlankBackgroundLibrary.load_json(gz_file)
    assert len(loaded.ions) == 2
    assert loaded.min_intensity == 25000.0
    assert loaded.source_blanks == ("Blank_1", "Blank_3")
    assert loaded.ions[0].mz == 279.0960
    assert loaded.ions[0].max_intensity == 1500000.0
    assert loaded.ions[1].mz == 391.1110


def test_packaged_default_background_library_loads_successfully() -> None:
    path = get_default_background_library_path()
    assert path.is_file()
    assert path.name.endswith(".json.gz")
    lib = get_default_background_library()
    assert lib is not None
    assert len(lib.ions) > 30000
    assert lib.min_intensity >= 25000.0


def test_discovery_cli_blank_argument_resolution(tmp_path: Path) -> None:
    from scripts.run_discovery import _discovery_settings, _parse_args

    # 1. Normal Discovery preserves candidates unless explicitly opted in.
    args_default = _parse_args(["--raw", "dummy.raw", "--dll-dir", "dll"])
    settings_default = _discovery_settings(args_default)
    assert not settings_default.enable_blank_qualification

    # 2. --enable-blank-qualification -> True
    args_enable = _parse_args(
        ["--raw", "dummy.raw", "--dll-dir", "dll", "--enable-blank-qualification"]
    )
    settings_enable = _discovery_settings(args_enable)
    assert settings_enable.enable_blank_qualification
    assert settings_enable.blank_library_path is None

    # 3. --blank-library -> True with path
    custom_lib = tmp_path / "custom.json"
    custom_lib.write_text("{}", encoding="utf-8")
    args_custom = _parse_args(
        ["--raw", "dummy.raw", "--dll-dir", "dll", "--blank-library", str(custom_lib)]
    )
    settings_custom = _discovery_settings(args_custom)
    assert settings_custom.enable_blank_qualification
    assert settings_custom.blank_library_path == str(custom_lib)

    # 4. --blank-dir -> True
    blank_dir = tmp_path / "blanks"
    blank_dir.mkdir()
    args_dir = _parse_args(
        ["--raw", "dummy.raw", "--dll-dir", "dll", "--blank-dir", str(blank_dir)]
    )
    settings_dir = _discovery_settings(args_dir)
    assert settings_dir.enable_blank_qualification

    # 5. --no-blank-qualification explicitly disables
    args_no = _parse_args(
        [
            "--raw",
            "dummy.raw",
            "--dll-dir",
            "dll",
            "--enable-blank-qualification",
            "--no-blank-qualification",
        ]
    )
    settings_no = _discovery_settings(args_no)
    assert not settings_no.enable_blank_qualification


def test_run_discovery_cli_missing_blank_library_fails(tmp_path: Path) -> None:
    from scripts.run_discovery import main

    dummy_raw = tmp_path / "test.raw"
    dummy_raw.write_bytes(b"")
    dummy_dll = tmp_path / "dll"
    dummy_dll.mkdir()
    missing_lib = tmp_path / "missing_lib.json"

    code = main(
        [
            "--raw",
            str(dummy_raw),
            "--dll-dir",
            str(dummy_dll),
            "--blank-library",
            str(missing_lib),
        ]
    )
    assert code == 2


@pytest.mark.parametrize("change", ["same_path_new_content", "new_dir_same_names"])
def test_run_discovery_blank_dir_uses_current_blank_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    from scripts.run_discovery import main

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    sample_raw = raw_dir / "sample.raw"
    sample_raw.write_bytes(b"sample")

    blank_dir = tmp_path / "blanks"
    blank_dir.mkdir()
    (blank_dir / "Blank_1.raw").write_bytes(b"100000")

    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    cached_file = output_dir / "blank_library.json"

    def mock_extract(blank_raws, dll_dir):
        return BlankBackgroundLibrary(
            ions=(
                BlankBackgroundIon(
                    mz=300.0,
                    max_intensity=float(blank_raws[0].read_bytes()),
                    scan_count=5,
                    source_blank="Blank_1",
                ),
            ),
            min_intensity=25000.0,
            source_blanks=tuple(sorted(p.stem for p in blank_raws)),
        )

    monkeypatch.setattr(
        "scripts.run_discovery.extract_blank_background_library", mock_extract
    )

    # Mock run-spec compilation and run_discovery to avoid running the full pipeline.
    monkeypatch.setattr(
        "scripts.run_discovery.compile_discovery_processing_run_spec",
        lambda *args, **kwargs: None,
    )
    from unittest.mock import MagicMock

    mock_outputs = MagicMock()
    mock_outputs.candidates_csv = Path("dummy.csv")
    monkeypatch.setattr(
        "scripts.run_discovery.run_discovery",
        lambda *args, **kwargs: mock_outputs,
    )

    code = main(
        [
            "--raw",
            str(sample_raw),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--blank-dir",
            str(blank_dir),
        ]
    )
    assert code == 0
    assert BlankBackgroundLibrary.load_json(cached_file).ions[0].max_intensity == 100000

    if change == "new_dir_same_names":
        blank_dir = tmp_path / "other_blanks"
        blank_dir.mkdir()
    (blank_dir / "Blank_1.raw").write_bytes(b"200000")
    code = main(
        [
            "--raw",
            str(sample_raw),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--blank-dir",
            str(blank_dir),
        ]
    )
    assert code == 0
    library = BlankBackgroundLibrary.load_json(cached_file)
    assert library.ions[0].max_intensity == 200000


def test_run_discovery_explicit_disable_skips_blank_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from scripts.run_discovery import main

    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"sample")
    dll = tmp_path / "dll"
    dll.mkdir()
    blanks = tmp_path / "blanks"
    blanks.mkdir()
    (blanks / "Blank_1.raw").write_bytes(b"blank")
    output = tmp_path / "output"
    settings_seen = []

    def forbidden_extraction(*args, **kwargs):
        pytest.fail("explicit disable must not acquire blank RAWs")

    def compile_spec(raw_paths, *, discovery_settings):
        settings = discovery_settings
        settings_seen.append(settings)
        return None

    def discover(raw_path, *, settings, **kwargs):
        settings_seen.append(settings)
        return SimpleNamespace(candidates_csv="candidates.csv", review_csv="review.csv")

    monkeypatch.setattr(
        "scripts.run_discovery.extract_blank_background_library",
        forbidden_extraction,
    )
    monkeypatch.setattr(
        "scripts.run_discovery.compile_discovery_processing_run_spec",
        compile_spec,
    )
    monkeypatch.setattr("scripts.run_discovery.run_discovery", discover)
    assert (
        main(
            [
                "--raw",
                str(raw),
                "--dll-dir",
                str(dll),
                "--output-dir",
                str(output),
                "--blank-dir",
                str(blanks),
                "--no-blank-qualification",
            ]
        )
        == 0
    )
    assert len(settings_seen) == 2
    assert all(not settings.enable_blank_qualification for settings in settings_seen)
    assert not (output / "blank_library.json").exists()
