from pathlib import Path

from xic_extractor.instrument_qc.classification import (
    InstrumentQCClass,
    classify_instrument_qc_raw,
)
from xic_extractor.instrument_qc.mixstds import (
    discover_mixstds_raws,
    load_mixstds_target_registry,
)


def _write_raw(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_mixstds_classification_uses_name_not_folder() -> None:
    root = Path("C:/data/batch")

    for raw in (
        root / "STDs" / "Mix_STDs_01.raw",
        root / "Pairs" / "Mix STDs 01.raw",
        root / "250ppb_Mix_STDs.raw",
    ):
        assert classify_instrument_qc_raw(raw, root) == InstrumentQCClass.MIX_STDS
    assert classify_instrument_qc_raw(root / "STDs" / "TumorBC_DNA.raw", root) is None


def test_discover_mixstds_finds_flat_folder_raw(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    mix = root / "250ppb_Mix_STDs.raw"
    _write_raw(mix)
    _write_raw(root / "Blank_1.raw")
    _write_raw(root / "SDOLEK-1.raw")

    assert discover_mixstds_raws(root, []) == (mix,)


def test_discover_mixstds_prefers_stds_over_pairs_duplicate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "raw"
    primary = root / "STDs" / "Mix_STDs_01.raw"
    duplicate = root / "Pairs" / "Mix_STDs_01.raw"
    biological = root / "TumorBC2257_DNA.raw"
    _write_raw(primary)
    _write_raw(duplicate)
    _write_raw(biological)

    diagnostics = []
    selected = discover_mixstds_raws(root, diagnostics)

    assert selected == (primary,)
    assert [diag.issue for diag in diagnostics] == ["DUPLICATE_MIXSTDS_RAW_STEM"]
    assert "Pairs" in diagnostics[0].detail


def test_discover_mixstds_uses_pairs_as_fallback_when_stds_missing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "raw"
    fallback = root / "Pairs" / "Mix_STDs_01.raw"
    _write_raw(fallback)

    assert discover_mixstds_raws(root, []) == (fallback,)


def test_mixstds_target_registry_loads_reviewed_csv(tmp_path: Path) -> None:
    registry = tmp_path / "mixstds.csv"
    registry.write_text(
        "compound,precursor_mz,rt_min,rt_max,ppm_tol\n"
        "STD-A,123.4567,1.0,2.0,8\n",
        encoding="utf-8",
    )

    result = load_mixstds_target_registry(registry)

    assert result.status == "loaded"
    assert result.source == registry
    assert len(result.targets) == 1
    assert result.targets[0].compound == "STD-A"
    assert result.targets[0].precursor_mz == 123.4567
    assert result.targets[0].rt_min == 1.0
    assert result.targets[0].rt_max == 2.0
    assert result.targets[0].ppm_tol == 8.0


def test_mixstds_target_registry_loads_optional_hcd_groups(tmp_path: Path) -> None:
    registry = tmp_path / "mixstds.csv"
    registry.write_text(
        "compound,precursor_mz,rt_min,rt_max,ppm_tol,hcd_base_group,"
        "hcd_product_group\n"
        "STD-A,123.4567,1.0,2.0,8,G,G_custom\n",
        encoding="utf-8",
    )

    result = load_mixstds_target_registry(registry)

    assert result.hcd_base_groups == {"STD-A": "G"}
    assert result.hcd_product_groups == {"STD-A": "G_custom"}


def test_mixstds_target_registry_loads_existing_targets_csv_schema(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "targets.csv"
    registry.write_text(
        "label,mz,rt_min,rt_max,ppm_tol,neutral_loss_da,nl_ppm_warn,"
        "nl_ppm_max,is_istd,istd_pair\n"
        "5-hmdC,258.1085,8.05,10.05,20,116.0474,20,50,false,d3-5-hmdC\n",
        encoding="utf-8",
    )

    result = load_mixstds_target_registry(registry)

    assert result.status == "loaded"
    assert result.targets[0].compound == "5-hmdC"
    assert result.targets[0].precursor_mz == 258.1085
    assert result.targets[0].rt_min == 8.05
    assert result.targets[0].rt_max == 10.05
    assert result.targets[0].ppm_tol == 20.0
    assert result.targets[0].neutral_loss_da == 116.0474


def test_versioned_mixstds_registry_loads_reviewed_targets() -> None:
    result = load_mixstds_target_registry(Path("config/MixSTDs.csv"))

    assert result.status == "loaded"
    assert len(result.targets) == 48
    by_compound = {target.compound: target for target in result.targets}
    assert by_compound["5-cadC"].rt_min == 11.20
    assert by_compound["5-cadC"].rt_max == 12.70
    assert by_compound["N6-6Ah-dA"].rt_min == 19.30
    assert by_compound["N6-6Ah-dA"].rt_max == 20.80
    assert by_compound["t6A"].rt_min == 30.30
    assert by_compound["t6A"].rt_max == 33.90


def test_mixstds_target_registry_accepts_utf8_sig_targets_csv(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "targets.csv"
    registry.write_text(
        "label,mz,rt_min,rt_max,ppm_tol\n"
        "5-hmdC,258.1085,8.05,10.05,20\n",
        encoding="utf-8-sig",
    )

    result = load_mixstds_target_registry(registry)

    assert result.status == "loaded"
    assert result.targets[0].compound == "5-hmdC"


def test_mixstds_target_registry_missing_is_explicit() -> None:
    result = load_mixstds_target_registry(None)

    assert result.status == "missing"
    assert result.targets == ()
    assert "target registry" in result.reason
