import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
from xic_extractor.raw_reader import Ms2Scan, Ms2ScanEvent

NEUTRAL_LOSS_DA = 116.0474
RAW_FILE = Path("C:/data/TumorBC2312_DNA.raw")


def test_observed_loss_within_tolerance_produces_discovery_seed() -> None:
    product_mz = _product_for_loss_ppm(precursor_mz=258.1085, ppm=5.0)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=258.1085,
                masses=[product_mz],
                intensities=[12000.0],
                scan_number=6095,
                rt=7.83,
            )
        ]
    )

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(nl_tolerance_ppm=10.0),
    )

    assert len(seeds) == 1
    seed = seeds[0]
    assert seed.raw_file == RAW_FILE
    assert seed.sample_stem == "TumorBC2312_DNA"
    assert seed.scan_number == 6095
    assert seed.rt == 7.83
    assert seed.precursor_mz == 258.1085
    assert seed.product_mz == pytest.approx(product_mz)
    assert seed.product_intensity == 12000.0
    assert seed.neutral_loss_tag == "DNA_dR"
    assert seed.configured_neutral_loss_da == NEUTRAL_LOSS_DA
    assert seed.observed_neutral_loss_da == pytest.approx(258.1085 - product_mz)
    assert seed.observed_loss_error_ppm == pytest.approx(5.0)


def test_out_of_neutral_loss_tolerance_product_is_rejected() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=258.1085,
                masses=[_product_for_loss_ppm(precursor_mz=258.1085, ppm=30.0)],
                intensities=[12000.0],
            )
        ]
    )

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(nl_tolerance_ppm=10.0, product_search_ppm=50.0),
    )

    assert seeds == ()


def test_quadrature_window_accepts_forty_ppm_loss_and_rejects_outside_product() -> None:
    precursor_mz = 482.0
    product_inside = _product_for_loss_ppm(precursor_mz=precursor_mz, ppm=40.0)
    product_outside = _product_for_loss_ppm(precursor_mz=precursor_mz, ppm=120.0)
    raw = _FakeRaw([_scan_event(
        precursor_mz=precursor_mz,
        masses=[product_inside, product_outside],
        intensities=[12000.0, 10000.0],
    )])

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(nl_tolerance_ppm=20.0, product_search_ppm=1.0),
    )

    direct = [seed for seed in seeds if seed.precursor_mz_basis == "scan_precursor"]
    assert len(direct) == 1
    assert direct[0].product_mz == pytest.approx(product_inside)
    assert direct[0].observed_loss_error_ppm == pytest.approx(40.0)
    tag_evidence = json.loads(direct[0].tag_evidence_json)["DNA_dR"]
    assert (
        tag_evidence["neutral_loss_error_da"]
        < tag_evidence["neutral_loss_window_da"]
    )


def test_parse_error_event_is_ignored() -> None:
    raw = _FakeRaw([Ms2ScanEvent(scan=None, parse_error="bad filter", scan_number=7)])

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(),
    )

    assert seeds == ()


def test_low_intensity_below_base_peak_ratio_is_rejected() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=258.1085,
                masses=[
                    _product_for_loss_ppm(precursor_mz=258.1085, ppm=4.0),
                    200.0,
                ],
                intensities=[99.0, 10000.0],
            )
        ]
    )

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(nl_min_intensity_ratio=0.01),
    )

    assert seeds == ()


def test_malformed_non_1d_scan_arrays_are_ignored_without_raising() -> None:
    raw = _FakeRaw(
        [
            Ms2ScanEvent(
                scan=Ms2Scan(
                    scan_number=9,
                    rt=8.5,
                    precursor_mz=258.1085,
                    masses=np.asarray(
                        [[_product_for_loss_ppm(precursor_mz=258.1085, ppm=4.0)]],
                        dtype=float,
                    ),
                    intensities=np.asarray([[12000.0]], dtype=float),
                    base_peak=12000.0,
                ),
                parse_error=None,
                scan_number=9,
            )
        ]
    )

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(),
    )

    assert seeds == ()


def test_product_search_ppm_cannot_reject_observed_loss_within_tolerance() -> None:
    precursor_mz = 258.1085
    product_mz = _product_for_loss_ppm(precursor_mz=precursor_mz, ppm=10.0)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=precursor_mz,
                masses=[product_mz],
                intensities=[12000.0],
            )
        ]
    )

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(nl_tolerance_ppm=20.0, product_search_ppm=1.0),
    )

    assert len(seeds) == 1
    assert seeds[0].product_mz == pytest.approx(product_mz)
    assert seeds[0].observed_loss_error_ppm == pytest.approx(10.0)


def test_isolation_offset_product_infers_monoisotopic_precursor_seed() -> None:
    scan_precursor_mz = 300.202820
    product_mz = 184.11322021484375
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=scan_precursor_mz,
                masses=[product_mz],
                intensities=[39671.9],
                scan_number=19867,
                rt=23.8043,
            )
        ]
    )

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(),
    )

    assert len(seeds) == 1
    seed = seeds[0]
    assert seed.scan_number == 19867
    assert seed.rt == pytest.approx(23.8043)
    assert seed.precursor_mz == pytest.approx(product_mz + NEUTRAL_LOSS_DA)
    assert seed.product_mz == pytest.approx(product_mz)
    assert seed.observed_neutral_loss_da == pytest.approx(NEUTRAL_LOSS_DA)
    assert seed.observed_loss_error_ppm == 0.0
    evidence = json.loads(seed.tag_evidence_json)
    assert evidence["DNA_dR"]["precursor_mz_basis"] == "product_plus_neutral_loss"
    assert evidence["DNA_dR"]["scan_precursor_mz"] == pytest.approx(
        scan_precursor_mz
    )


def test_same_ms2_scan_can_emit_direct_and_inferred_precursor_seeds() -> None:
    scan_precursor_mz = 301.165192
    monoisotopic_product_mz = 184.11322021484375
    isotope_product_mz = 185.1158905029297
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=scan_precursor_mz,
                masses=[monoisotopic_product_mz, isotope_product_mz],
                intensities=[146673.0, 197119.9],
                scan_number=19261,
                rt=23.1409,
            )
        ]
    )

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(),
    )

    seeds_by_product = {round(seed.product_mz, 6): seed for seed in seeds}
    assert set(seeds_by_product) == {
        round(monoisotopic_product_mz, 6),
        round(isotope_product_mz, 6),
    }
    inferred = seeds_by_product[round(monoisotopic_product_mz, 6)]
    direct = seeds_by_product[round(isotope_product_mz, 6)]
    assert inferred.precursor_mz == pytest.approx(
        monoisotopic_product_mz + NEUTRAL_LOSS_DA
    )
    assert direct.precursor_mz == pytest.approx(scan_precursor_mz)


def test_inferred_precursor_seed_respects_ms2_precursor_window() -> None:
    product_mz = 184.11322021484375
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=305.0,
                masses=[product_mz],
                intensities=[39671.9],
                scan_number=19867,
                rt=23.8043,
            )
        ]
    )

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(ms2_precursor_tol_da=1.6),
    )

    assert seeds == ()


def test_observed_loss_error_equal_to_tolerance_is_accepted() -> None:
    precursor_mz = 258.1085
    product_mz = _product_for_loss_ppm(precursor_mz=precursor_mz, ppm=10.0)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=precursor_mz,
                masses=[product_mz],
                intensities=[12000.0],
            )
        ]
    )

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(nl_tolerance_ppm=10.0),
    )

    assert len(seeds) == 1
    assert seeds[0].observed_loss_error_ppm == pytest.approx(10.0)


def test_multiple_matching_products_choose_smallest_loss_error_then_higher_intensity(
) -> None:
    precursor_mz = 258.1085
    best_by_error = _product_for_loss_ppm(precursor_mz=precursor_mz, ppm=3.0)
    tied_higher_intensity = _product_for_loss_ppm(precursor_mz=precursor_mz, ppm=-3.0)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=precursor_mz,
                masses=[
                    _product_for_loss_ppm(precursor_mz=precursor_mz, ppm=8.0),
                    best_by_error,
                    tied_higher_intensity,
                ],
                intensities=[50000.0, 1000.0, 2000.0],
            )
        ]
    )

    seeds = collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(nl_tolerance_ppm=10.0),
    )

    assert len(seeds) == 1
    assert seeds[0].product_mz == pytest.approx(tied_higher_intensity)
    assert seeds[0].product_intensity == 2000.0
    assert seeds[0].observed_loss_error_ppm == pytest.approx(3.0)


def test_iter_ms2_scans_uses_settings_rt_window() -> None:
    raw = _FakeRaw([])

    collect_strict_nl_seeds(
        raw,
        raw_file=RAW_FILE,
        settings=_settings(rt_min=1.25, rt_max=9.75),
    )

    assert raw.requested_window == (1.25, 9.75)


def test_multi_profile_seed_collection_keeps_seed_groups_per_tag() -> None:
    settings = DiscoverySettings(
        selected_tag_names=("dR", "R", "MeR"),
        tag_combine_mode="union",
        neutral_loss_profiles=(
            NeutralLossProfile(tag="dR", neutral_loss_da=116.047344),
            NeutralLossProfile(tag="R", neutral_loss_da=132.0423),
            NeutralLossProfile(tag="MeR", neutral_loss_da=146.0579),
        ),
    )
    seeds = collect_strict_nl_seeds(
        _FakeRaw(
            [
                scan_for_loss(116.047344),
                scan_for_loss(132.0423),
                scan_for_loss(146.0579),
            ]
        ),
        raw_file=RAW_FILE,
        settings=settings,
    )

    groups = group_discovery_seeds(seeds, settings=settings)

    assert [seed.neutral_loss_tag for seed in seeds] == ["dR", "R", "MeR"]
    assert len(groups) == 3
    assert [group.matched_tag_names for group in groups] == [
        ("dR",),
        ("R",),
        ("MeR",),
    ]


def _settings(**overrides: float) -> DiscoverySettings:
    values = {
        "neutral_loss_profile": NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
        **overrides,
    }
    return DiscoverySettings(**values)


def _product_for_loss_ppm(precursor_mz: float, ppm: float) -> float:
    observed_loss = NEUTRAL_LOSS_DA * (1.0 + ppm / 1_000_000.0)
    return precursor_mz - observed_loss


def _scan_event(
    *,
    precursor_mz: float,
    masses: list[float],
    intensities: list[float],
    scan_number: int = 1,
    rt: float = 8.5,
) -> Ms2ScanEvent:
    return Ms2ScanEvent(
        scan=Ms2Scan(
            scan_number=scan_number,
            rt=rt,
            precursor_mz=precursor_mz,
            masses=np.asarray(masses, dtype=float),
            intensities=np.asarray(intensities, dtype=float),
            base_peak=max(intensities) if intensities else 0.0,
        ),
        parse_error=None,
        scan_number=scan_number,
    )


def scan_for_loss(
    loss_da: float,
    *,
    precursor_mz: float = 400.0,
    rt: float = 5.0,
) -> Ms2ScanEvent:
    product_mz = precursor_mz - loss_da
    scan_number = int(round(loss_da * 1000))
    return Ms2ScanEvent(
        scan=Ms2Scan(
            scan_number=scan_number,
            rt=rt,
            precursor_mz=precursor_mz,
            masses=np.asarray([product_mz], dtype=float),
            intensities=np.asarray([50000.0], dtype=float),
            base_peak=50000.0,
        ),
        parse_error=None,
        scan_number=scan_number,
    )


class _FakeRaw:
    def __init__(self, events: list[Ms2ScanEvent]) -> None:
        self._events = events
        self.requested_window: tuple[float, float] | None = None

    def iter_ms2_scans(self, rt_min: float, rt_max: float) -> Iterator[Ms2ScanEvent]:
        self.requested_window = (rt_min, rt_max)
        yield from self._events
