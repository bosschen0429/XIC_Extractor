from pathlib import Path

import pytest

from xic_extractor.discovery.tag_profiles import (
    FeatureTagProfile,
    load_feature_tag_profiles,
    resolve_selected_tag_profiles,
)


def test_load_feature_tag_profiles_parses_nl_and_tolerates_deferred_pi(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "Feature_List.csv"
    csv_path.write_text(
        "Tag No.,Tag Category,Tag Parameters (Da or m/z),Mass Tolerance (ppm),"
        "Intensity Cutoff (height),Top N Ion by Intensity "
        "(MS2; only for tag category 1-2),"
        "Consecutive Data Points (MS1; only for tag category 3),"
        "Minimum Intensity Ratio (MS1/MS1'; only for tag category 3),"
        "Maximum Intensity Ratio (MS1/MS1'; only for tag category 3),\n"
        "1,1,116.047344,20,10000,0,0,0,0,NL: dR\n"
        "2,1,132.0423,20,10000,0,0,0,0,NL: R\n"
        "3,1,146.0579,20,10000,0,0,0,0,NL: MeR\n"
        "57,2,117.0547,20,10000,0,0,0,0,PI: dR\n",
        encoding="utf-8",
    )

    profiles = load_feature_tag_profiles(csv_path)

    assert profiles[0] == FeatureTagProfile(
        tag_kind="neutral_loss",
        tag_label="NL: dR",
        tag_name="dR",
        parameter_mz_or_da=116.047344,
        mass_tolerance_ppm=20.0,
        intensity_cutoff=10000.0,
    )
    assert [profile.tag_name for profile in profiles] == ["dR", "R", "MeR"]
    assert all(profile.tag_kind == "neutral_loss" for profile in profiles)


def test_resolve_selected_tag_profiles_accepts_names_and_labels(
    tmp_path: Path,
) -> None:
    profiles = load_feature_tag_profiles(_feature_list(tmp_path))

    selected = resolve_selected_tag_profiles(profiles, ["dR", "NL: R", "MeR"])

    assert [profile.tag_name for profile in selected] == ["dR", "R", "MeR"]


def test_resolve_selected_tag_profiles_rejects_pi_labels(tmp_path: Path) -> None:
    csv_path = tmp_path / "Feature_List.csv"
    csv_path.write_text(
        "Tag No.,Tag Category,Tag Parameters (Da or m/z),Mass Tolerance (ppm),"
        "Intensity Cutoff (height),Top N Ion by Intensity "
        "(MS2; only for tag category 1-2),"
        "Consecutive Data Points (MS1; only for tag category 3),"
        "Minimum Intensity Ratio (MS1/MS1'; only for tag category 3),"
        "Maximum Intensity Ratio (MS1/MS1'; only for tag category 3),\n"
        "1,1,116.047344,20,10000,0,0,0,0,NL: dR\n"
        "57,2,117.0547,20,10000,0,0,0,0,PI: dR\n",
        encoding="utf-8",
    )
    profiles = load_feature_tag_profiles(csv_path)

    with pytest.raises(ValueError, match="not a selectable neutral-loss tag"):
        resolve_selected_tag_profiles(profiles, ["PI: dR"])


def test_load_feature_tag_profiles_rejects_unknown_category(tmp_path: Path) -> None:
    csv_path = tmp_path / "Feature_List.csv"
    csv_path.write_text(
        "Tag No.,Tag Category,Tag Parameters (Da or m/z),Mass Tolerance (ppm),"
        "Intensity Cutoff (height),Top N Ion by Intensity "
        "(MS2; only for tag category 1-2),"
        "Consecutive Data Points (MS1; only for tag category 3),"
        "Minimum Intensity Ratio (MS1/MS1'; only for tag category 3),"
        "Maximum Intensity Ratio (MS1/MS1'; only for tag category 3),\n"
        "99,9,123.4,20,10000,0,0,0,0,UNKNOWN\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported Tag Category"):
        load_feature_tag_profiles(csv_path)


def test_load_feature_tag_profiles_still_requires_tag_number(tmp_path: Path) -> None:
    path = _feature_list(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace("1,1,116.047344", ",1,116.047344", 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"Tag No\. is required"):
        load_feature_tag_profiles(path)


def _feature_list(tmp_path: Path) -> Path:
    csv_path = tmp_path / "Feature_List.csv"
    csv_path.write_text(
        "Tag No.,Tag Category,Tag Parameters (Da or m/z),Mass Tolerance (ppm),"
        "Intensity Cutoff (height),Top N Ion by Intensity "
        "(MS2; only for tag category 1-2),"
        "Consecutive Data Points (MS1; only for tag category 3),"
        "Minimum Intensity Ratio (MS1/MS1'; only for tag category 3),"
        "Maximum Intensity Ratio (MS1/MS1'; only for tag category 3),\n"
        "1,1,116.047344,20,10000,0,0,0,0,NL: dR\n"
        "2,1,132.0423,20,10000,0,0,0,0,NL: R\n"
        "3,1,146.0579,20,10000,0,0,0,0,NL: MeR\n",
        encoding="utf-8",
    )
    return csv_path
