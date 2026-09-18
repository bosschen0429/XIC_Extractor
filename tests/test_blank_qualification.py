from pathlib import Path

from xic_extractor.discovery.blank_qualification import (
    BlankBackgroundIon,
    BlankBackgroundLibrary,
    BlankQualificationCategory,
    qualify_candidates_against_blank,
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


def test_candidate_qualification_categories():
    ions = (
        BlankBackgroundIon(
            mz=279.0960, max_intensity=100000.0, scan_count=20, source_blank="Blank_1"
        ),
        BlankBackgroundIon(
            mz=300.1000, max_intensity=50000.0, scan_count=15, source_blank="Blank_1"
        ),
    )
    lib = BlankBackgroundLibrary(
        ions=ions,
        min_intensity=25000.0,
        source_blanks=("Blank_1",),
    )

    candidates = [
        # Candidate 1: In blank, high sample intensity (FC >= 3.0) -> OVERRIDE
        {
            "candidate_id": "C1",
            "precursor_mz": 279.0960,
            "ms1_height": 500000.0,
        },
        # Candidate 2: In blank (FC < 3.0) -> SYSTEM_BACKGROUND
        {
            "candidate_id": "C2",
            "precursor_mz": 300.1000,
            "ms1_height": 60000.0,
        },
        # Candidate 3: Not in blank at all -> CLEAN_ANALYTE
        {
            "candidate_id": "C3",
            "precursor_mz": 450.2000,
            "ms1_height": 80000.0,
        },
    ]

    verdicts = qualify_candidates_against_blank(
        candidates, lib, fold_change_threshold=3.0, ppm_tol=10.0
    )
    assert len(verdicts) == 3

    # C1: High abundance override
    assert verdicts[0].candidate_id == "C1"
    assert verdicts[0].category == BlankQualificationCategory.HIGH_ABUNDANCE_OVERRIDE
    assert verdicts[0].is_excluded_from_review is False
    assert verdicts[0].fold_change == 5.0

    # C2: System background
    assert verdicts[1].candidate_id == "C2"
    assert verdicts[1].category == BlankQualificationCategory.SYSTEM_BACKGROUND
    assert verdicts[1].is_excluded_from_review is True
    assert verdicts[1].fold_change == 1.2

    # C3: Clean analyte
    assert verdicts[2].candidate_id == "C3"
    assert verdicts[2].category == BlankQualificationCategory.CLEAN_ANALYTE
    assert verdicts[2].is_excluded_from_review is False
    assert verdicts[2].matched_blank_mz is None


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

    # resolve_background_library fallback
    resolved = resolve_background_library()
    assert resolved is lib


def test_apply_blank_qualification_to_candidates():
    from xic_extractor.discovery.blank_qualification import (
        BlankBackgroundIon,
        BlankBackgroundLibrary,
        apply_blank_qualification_to_candidates,
    )
    from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySeed

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

    qualified = apply_blank_qualification_to_candidates(
        (c_background, c_override, c_clean),
        lib,
        ppm_tol=10.0,
        fold_change_threshold=3.0,
    )

    # c_background demoted
    assert qualified[0].discovery_candidate_state == "review_only_orphan_nl"
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
