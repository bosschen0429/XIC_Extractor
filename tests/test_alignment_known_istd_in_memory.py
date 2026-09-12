from pathlib import Path

import pytest

from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
    mint_known_istd_reference_from_measurements,
    read_known_istd_reference,
)
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.targets import read_targets


def _make_target(label: str, is_istd: bool = True) -> Target:
    return Target(
        label=label,
        mz=200.0,
        rt_min=1.0,
        rt_max=5.0,
        ppm_tol=10.0,
        neutral_loss_da=100.0,
        nl_ppm_warn=10.0,
        nl_ppm_max=20.0,
        is_istd=is_istd,
        istd_pair="",
    )


def test_mint_known_istd_reference_validates_inputs() -> None:
    # Requires at least one ISTD
    with pytest.raises(ValueError, match="at least one ISTD target"):
        mint_known_istd_reference_from_measurements(
            targets=[_make_target("Analyte1", is_istd=False)],
            coordinates={},
        )

    # Requires unique target labels
    with pytest.raises(ValueError, match="unique ISTD target labels"):
        mint_known_istd_reference_from_measurements(
            targets=[_make_target("ISTD1"), _make_target("ISTD1")],
            coordinates={},
        )

    # Rejects undeclared targets in coordinates
    with pytest.raises(ValueError, match="undeclared ISTD target"):
        mint_known_istd_reference_from_measurements(
            targets=[_make_target("ISTD1")],
            coordinates={("UnknownTarget", "Sample1"): 2.5},
        )

    # Rejects negative RT
    with pytest.raises(ValueError, match="reference RT must be nonnegative"):
        mint_known_istd_reference_from_measurements(
            targets=[_make_target("ISTD1")],
            coordinates={("ISTD1", "Sample1"): -1.0},
        )


def test_mint_known_istd_reference_computes_centers_and_deltas() -> None:
    targets = [_make_target("ISTD1"), _make_target("ISTD2")]
    # Sample1: ISTD1=2.0, ISTD2=10.0
    # Sample2: ISTD1=2.2, ISTD2=10.2
    # Sample3: ISTD1=2.4, ISTD2=10.4
    # Median center: ISTD1 = 2.2, ISTD2 = 10.2
    coords = {
        ("ISTD1", "S1"): 2.0,
        ("ISTD1", "S2"): 2.2,
        ("ISTD1", "S3"): 2.4,
        ("ISTD2", "S1"): 10.0,
        ("ISTD2", "S2"): 10.2,
        ("ISTD2", "S3"): 10.4,
    }
    ref = mint_known_istd_reference_from_measurements(targets, coords)

    assert isinstance(ref, KnownIstdReference)
    assert len(ref.coordinates) == 6

    # Verify deltas:
    # ("ISTD1", "S1"): 2.0 - 2.2 = -0.2
    # ("ISTD1", "S2"): 2.2 - 2.2 = 0.0
    # ("ISTD1", "S3"): 2.4 - 2.2 = +0.2
    coord_map = {(t, s): (rt, delta) for t, s, rt, delta in ref.coordinates}
    assert coord_map[("ISTD1", "S1")][0] == pytest.approx(2.0)
    assert coord_map[("ISTD1", "S1")][1] == pytest.approx(-0.2)
    assert coord_map[("ISTD1", "S2")][1] == pytest.approx(0.0)
    assert coord_map[("ISTD1", "S3")][1] == pytest.approx(0.2)


def test_mint_known_istd_reference_parity_with_workbook() -> None:
    wb_path = Path("output/xic_results_20260512_1200.xlsx")
    if not wb_path.is_file():
        pytest.skip("Test workbook output/xic_results_20260512_1200.xlsx not found")

    ref_wb = read_known_istd_reference(wb_path)
    assert len(ref_wb.coordinates) == 595
    assert len({s for _, s, _, _ in ref_wb.coordinates}) == 85
    assert len({t for t, _, _, _ in ref_wb.coordinates}) == 7


def test_read_targets_from_csv() -> None:
    csv_path = Path("config/targets.example.csv")
    if not csv_path.is_file():
        pytest.skip("config/targets.example.csv not found")

    targets = read_targets(csv_path)
    assert len(targets) == 14
    istd_targets = [t for t in targets if t.is_istd]
    assert len(istd_targets) == 7
    assert all(t.is_istd for t in istd_targets)
