import json
from pathlib import Path

import pytest

from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.models import (
    DiscoverySeed,
    DiscoverySettings,
    NeutralLossProfile,
)

NEUTRAL_LOSS_DA = 116.0474
RAW_FILE = Path("C:/data/TumorBC2312_DNA.raw")


def test_nearby_seeds_group_with_rt_bounds_and_deterministic_seed_order() -> None:
    later = _seed(scan_number=20, rt=7.90, product_intensity=5000.0)
    earlier_higher_scan = _seed(scan_number=12, rt=7.82, product_intensity=4000.0)
    earlier_lower_scan = _seed(scan_number=10, rt=7.82, product_intensity=3000.0)

    groups = group_discovery_seeds(
        (later, earlier_higher_scan, earlier_lower_scan),
        settings=_settings(seed_rt_gap_min=0.10),
    )

    assert len(groups) == 1
    group = groups[0]
    assert group.rt_seed_min == pytest.approx(7.82)
    assert group.rt_seed_max == pytest.approx(7.90)
    assert group.seeds == (earlier_lower_scan, earlier_higher_scan, later)


def test_rt_gap_larger_than_setting_splits_groups() -> None:
    groups = group_discovery_seeds(
        (
            _seed(scan_number=1, rt=24.00),
            _seed(scan_number=2, rt=27.00),
        ),
        settings=_settings(seed_rt_gap_min=0.20),
    )

    assert len(groups) == 2
    assert [group.rt_seed_min for group in groups] == [24.00, 27.00]


def test_rt_chain_groups_when_adjacent_gaps_stay_within_setting() -> None:
    groups = group_discovery_seeds(
        (
            _seed(scan_number=1, rt=7.80),
            _seed(scan_number=2, rt=7.99),
            _seed(scan_number=3, rt=8.18),
        ),
        settings=_settings(seed_rt_gap_min=0.20),
    )

    assert [(group.rt_seed_min, group.rt_seed_max) for group in groups] == [
        (7.80, 8.18),
    ]


def test_precursor_mz_outside_tolerance_splits_groups() -> None:
    groups = group_discovery_seeds(
        (
            _seed(scan_number=1, rt=7.80, precursor_mz=500.0000),
            _seed(scan_number=2, rt=7.85, precursor_mz=500.0200),
        ),
        settings=_settings(seed_rt_gap_min=0.20, precursor_mz_tolerance_ppm=20.0),
    )

    assert len(groups) == 2
    assert [group.precursor_mz for group in groups] == [500.0000, 500.0200]


@pytest.mark.parametrize(
    ("field", "values"),
    (
        ("precursor_mz", (500.0000, 500.0090, 500.0180)),
        ("product_mz", (200.0000, 200.0036, 200.0072)),
    ),
)
def test_mz_chain_splits_when_group_span_would_exceed_tolerance(
    field: str,
    values: tuple[float, float, float],
) -> None:
    seeds = [
        _seed(scan_number=1, rt=7.80, product_intensity=1000.0),
        _seed(scan_number=2, rt=7.85, product_intensity=3000.0),
        _seed(scan_number=3, rt=7.90, product_intensity=2000.0),
    ]
    seeds = [
        _replace_seed_value(seed, field=field, value=value)
        for seed, value in zip(seeds, values, strict=True)
    ]

    groups = group_discovery_seeds(
        seeds,
        settings=_settings(seed_rt_gap_min=0.20, precursor_mz_tolerance_ppm=20.0),
    )

    assert len(groups) == 2
    assert groups[0].seeds == (seeds[0], seeds[1])
    assert groups[1].seeds == (seeds[2],)


def test_product_mz_outside_tolerance_splits_groups() -> None:
    groups = group_discovery_seeds(
        (
            _seed(scan_number=1, rt=7.80, product_mz=200.0000),
            _seed(scan_number=2, rt=7.85, product_mz=200.0100),
        ),
        settings=_settings(seed_rt_gap_min=0.20, product_mz_tolerance_ppm=20.0),
    )

    assert len(groups) == 2
    assert [group.product_mz for group in groups] == [200.0000, 200.0100]


def test_interleaved_different_mz_seed_does_not_split_matching_group() -> None:
    groups = group_discovery_seeds(
        (
            _seed(scan_number=1, rt=19.00, precursor_mz=254.1260, product_mz=138.0780),
            _seed(scan_number=2, rt=19.05, precursor_mz=500.0000, product_mz=383.9526),
            _seed(scan_number=3, rt=19.10, precursor_mz=254.1261, product_mz=138.0781),
        ),
        settings=_settings(seed_rt_gap_min=0.20),
    )

    matching_groups = [
        group for group in groups if group.precursor_mz == pytest.approx(254.1260)
    ]
    assert len(matching_groups) == 1
    assert tuple(seed.scan_number for seed in matching_groups[0].seeds) == (1, 3)


def test_observed_neutral_loss_drift_outside_tolerance_splits_groups() -> None:
    groups = group_discovery_seeds(
        (
            _seed(scan_number=1, rt=7.80, observed_neutral_loss_da=116.0474),
            _seed(scan_number=2, rt=7.85, observed_neutral_loss_da=116.0510),
        ),
        settings=_settings(seed_rt_gap_min=0.20, nl_tolerance_ppm=20.0),
    )

    assert len(groups) == 2
    assert [group.observed_neutral_loss_da for group in groups] == [116.0474, 116.0510]


def test_non_positive_or_non_finite_mz_values_do_not_match_by_ppm() -> None:
    groups = group_discovery_seeds(
        (
            _seed(scan_number=1, rt=7.80, precursor_mz=0.0),
            _seed(scan_number=2, rt=7.85, precursor_mz=0.0),
            _seed(scan_number=3, rt=7.90, product_mz=float("inf")),
            _seed(scan_number=4, rt=7.95, product_mz=float("inf")),
        ),
        settings=_settings(seed_rt_gap_min=0.20),
    )

    assert len(groups) == 4


def test_different_neutral_loss_tag_or_sample_identity_splits_groups() -> None:
    groups = group_discovery_seeds(
        (
            _seed(scan_number=1, rt=7.80, neutral_loss_tag="DNA_dR"),
            _seed(scan_number=2, rt=7.82, neutral_loss_tag="RNA_rR"),
            _seed(scan_number=3, rt=7.84, raw_file=Path("C:/data/Other.raw")),
            _seed(scan_number=4, rt=7.86, sample_stem="OtherSample"),
        ),
        settings=_settings(seed_rt_gap_min=0.20),
    )

    assert len(groups) == 4
    group_keys = [
        (group.raw_file, group.sample_stem, group.neutral_loss_tag) for group in groups
    ]
    assert group_keys == [
        (Path("C:/data/Other.raw"), "TumorBC2312_DNA", "DNA_dR"),
        (RAW_FILE, "OtherSample", "DNA_dR"),
        (RAW_FILE, "TumorBC2312_DNA", "DNA_dR"),
        (RAW_FILE, "TumorBC2312_DNA", "RNA_rR"),
    ]


def test_representative_numeric_fields_come_from_deterministic_best_seed() -> None:
    highest_intensity = _seed(
        scan_number=50,
        rt=7.85,
        precursor_mz=500.0010,
        product_mz=383.9538,
        product_intensity=20000.0,
        observed_neutral_loss_da=116.0472,
        observed_loss_error_ppm=1.72,
    )
    same_intensity_better_error = _seed(
        scan_number=60,
        rt=7.86,
        precursor_mz=500.0020,
        product_mz=383.9546,
        product_intensity=15000.0,
        observed_neutral_loss_da=116.0475,
        observed_loss_error_ppm=0.86,
    )
    earliest_rt_tie = _seed(
        scan_number=70,
        rt=7.83,
        precursor_mz=500.0030,
        product_mz=383.9555,
        product_intensity=15000.0,
        observed_neutral_loss_da=116.0476,
        observed_loss_error_ppm=0.86,
    )

    groups = group_discovery_seeds(
        (same_intensity_better_error, highest_intensity, earliest_rt_tie),
        settings=_settings(seed_rt_gap_min=0.20),
    )

    assert len(groups) == 1
    group = groups[0]
    assert group.precursor_mz == highest_intensity.precursor_mz
    assert group.product_mz == highest_intensity.product_mz
    assert group.observed_neutral_loss_da == highest_intensity.observed_neutral_loss_da
    assert (
        group.neutral_loss_mass_error_ppm == highest_intensity.observed_loss_error_ppm
    )


def test_best_seed_tie_breaks_by_error_then_rt_then_scan_number() -> None:
    smallest_error = _seed(
        scan_number=30,
        rt=7.84,
        product_intensity=10000.0,
        observed_loss_error_ppm=0.25,
    )
    same_error_earlier_scan = _seed(
        scan_number=20,
        rt=7.84,
        product_intensity=10000.0,
        observed_loss_error_ppm=0.5,
        precursor_mz=500.0010,
        product_mz=383.9535,
        observed_neutral_loss_da=116.0475,
    )
    same_error_later_rt = _seed(
        scan_number=10,
        rt=7.86,
        product_intensity=10000.0,
        observed_loss_error_ppm=0.5,
    )
    worse_error = _seed(
        scan_number=5,
        rt=7.82,
        product_intensity=10000.0,
        observed_loss_error_ppm=2.0,
    )

    groups = group_discovery_seeds(
        (same_error_later_rt, same_error_earlier_scan, smallest_error, worse_error),
        settings=_settings(seed_rt_gap_min=0.20),
    )

    assert len(groups) == 1
    assert (
        groups[0].neutral_loss_mass_error_ppm == smallest_error.observed_loss_error_ppm
    )

    same_error_groups = group_discovery_seeds(
        (same_error_later_rt, same_error_earlier_scan, worse_error),
        settings=_settings(seed_rt_gap_min=0.20),
    )

    assert len(same_error_groups) == 1
    assert same_error_groups[0].precursor_mz == same_error_earlier_scan.precursor_mz
    assert same_error_groups[0].product_mz == same_error_earlier_scan.product_mz
    assert (
        same_error_groups[0].observed_neutral_loss_da
        == same_error_earlier_scan.observed_neutral_loss_da
    )
    assert (
        same_error_groups[0].neutral_loss_mass_error_ppm
        == same_error_earlier_scan.observed_loss_error_ppm
    )


def test_group_exposes_mixed_precursor_and_error_basis() -> None:
    direct = _seed(
        scan_number=19561,
        rt=23.4669,
        precursor_mz=300.160635,
        product_mz=184.113235,
        scan_precursor_mz=300.160635,
        precursor_mz_basis="scan_precursor",
    )
    inferred = _seed(
        scan_number=19488,
        rt=23.3893,
        precursor_mz=300.160635,
        product_mz=184.113235,
        scan_precursor_mz=301.164978,
        precursor_mz_basis="product_plus_neutral_loss",
    )

    groups = group_discovery_seeds(
        (direct, inferred),
        settings=_settings(seed_rt_gap_min=0.20),
    )

    assert len(groups) == 1
    group = groups[0]
    assert group.precursor_mz_basis == "mixed"
    assert group.neutral_loss_error_basis == "mixed"
    assert group.scan_precursor_mz == direct.scan_precursor_mz
    assert group.scan_precursor_delta_da == 0.0
    assert group.max_scan_precursor_abs_delta_da == pytest.approx(1.004343)
    receipts = json.loads(group.tag_evidence_json)["DNA_dR"]["scan_receipts"]
    assert receipts["19561"]["precursor_mz_basis"] == "scan_precursor"
    assert receipts["19488"]["precursor_mz_basis"] == "product_plus_neutral_loss"
    assert receipts["19488"]["neutral_loss_error_ppm"] is None


def _settings(**overrides: float) -> DiscoverySettings:
    values = {
        "neutral_loss_profile": NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
        **overrides,
    }
    return DiscoverySettings(**values)


def _seed(
    *,
    raw_file: Path = RAW_FILE,
    sample_stem: str = "TumorBC2312_DNA",
    scan_number: int,
    rt: float,
    precursor_mz: float = 500.0000,
    product_mz: float = 383.9526,
    product_intensity: float = 10000.0,
    neutral_loss_tag: str = "DNA_dR",
    configured_neutral_loss_da: float = NEUTRAL_LOSS_DA,
    observed_neutral_loss_da: float = NEUTRAL_LOSS_DA,
    observed_loss_error_ppm: float = 0.0,
    scan_precursor_mz: float | None = None,
    precursor_mz_basis: str = "scan_precursor",
) -> DiscoverySeed:
    return DiscoverySeed(
        raw_file=raw_file,
        sample_stem=sample_stem,
        scan_number=scan_number,
        rt=rt,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        product_intensity=product_intensity,
        neutral_loss_tag=neutral_loss_tag,
        configured_neutral_loss_da=configured_neutral_loss_da,
        observed_neutral_loss_da=observed_neutral_loss_da,
        observed_loss_error_ppm=(
            None
            if precursor_mz_basis == "product_plus_neutral_loss"
            else observed_loss_error_ppm
        ),
        scan_precursor_mz=scan_precursor_mz,
        precursor_mz_basis=precursor_mz_basis,  # type: ignore[arg-type]
    )


def _replace_seed_value(
    seed: DiscoverySeed, *, field: str, value: float
) -> DiscoverySeed:
    values = {
        "raw_file": seed.raw_file,
        "sample_stem": seed.sample_stem,
        "scan_number": seed.scan_number,
        "rt": seed.rt,
        "precursor_mz": seed.precursor_mz,
        "product_mz": seed.product_mz,
        "product_intensity": seed.product_intensity,
        "neutral_loss_tag": seed.neutral_loss_tag,
        "configured_neutral_loss_da": seed.configured_neutral_loss_da,
        "observed_neutral_loss_da": seed.observed_neutral_loss_da,
        "observed_loss_error_ppm": seed.observed_loss_error_ppm,
        "scan_precursor_mz": seed.scan_precursor_mz,
        "precursor_mz_basis": seed.precursor_mz_basis,
    }
    values[field] = value
    return DiscoverySeed(**values)
