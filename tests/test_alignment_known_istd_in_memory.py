from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.acquisition.istd_reference import (
    extract_batch_known_istd_reference,
    read_known_istd_reference,
)
from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
    mint_known_istd_reference_from_measurements,
)
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.targets import read_targets
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata, Ms2Scan, Ms2ScanEvent
from xic_extractor.xic_models import XICRequest, XICTrace


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


@pytest.mark.parametrize(
    ("matched_rts", "neutral_loss", "precursor_context", "expected_rt"),
    [
        ((8.1,), 116.0474, "target", 8.1),
        ((9.0,), 116.0474, "target", 9.0),
        ((8.1, 9.0), 116.0474, "target", None),
        ((), 116.0474, "target", None),
        ((8.1,), 132.0423, "target", 8.1),
        ((8.1,), 116.0474, "outside_isolation", None),
        ((8.1,), None, "target", None),
        ((9.0,), 116.0474, "coisolated_other", None),
        ((9.0,), 116.0474, "coisolated_target_mono", 9.0),
        ((9.0,), 116.0474, "outside_target_ppm", None),
        ((9.0,), 116.0474, "inside_target_ppm", 9.0),
    ],
)
def test_batch_istd_coordinates_require_unique_native_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    matched_rts: tuple[float, ...],
    neutral_loss: float | None,
    precursor_context: str,
    expected_rt: float | None,
) -> None:
    target = replace(
        _make_target("ISTD1"),
        mz=300.0,
        rt_min=8.0,
        rt_max=10.0,
        neutral_loss_da=neutral_loss,
        nl_ppm_warn=10.0 if neutral_loss is not None else None,
        nl_ppm_max=20.0 if neutral_loss is not None else None,
    )
    # Both labels share an acquisition window; the non-ISTD must not be acquired.
    targets = (target, replace(target, label="ISTD2"), _make_target("Analyte", False))
    events = []
    for number, apex in enumerate((8.1, 9.0), start=1):
        outside_isolation = precursor_context == "outside_isolation"
        coisolated = precursor_context.startswith("coisolated_")
        selected_mz = target.mz + {
            "outside_isolation": 1.0,
            "coisolated_other": 0.5,
            "coisolated_target_mono": 0.5,
            "outside_target_ppm": 0.004,
            "inside_target_ppm": 0.002,
        }.get(precursor_context, 0.0)
        metadata = (
            Ms2PrecursorMetadata(
                monoisotopic_mz=(
                    None if precursor_context == "coisolated_other" else target.mz
                ),
                charge=1,
                isolation_width_da=0.4 if outside_isolation else 1.6,
                isolation_offset_da=0.0,
            )
            if outside_isolation or coisolated
            else None
        )
        product_precursor = (
            selected_mz
            if precursor_context in {
                "coisolated_other", "outside_target_ppm", "inside_target_ppm",
            }
            else target.mz
        )
        product_mz = (
            product_precursor
            - (neutral_loss or 116.0474)
            + (0.0 if apex in matched_rts else 2.0)
        )
        events.append(
            Ms2ScanEvent(
                Ms2Scan(
                    number,
                    apex,
                    selected_mz,
                    np.array([product_mz]),
                    np.array([100.0]),
                    100.0,
                    metadata,
                ),
                None,
                number,
            )
        )
    opens = []
    batches = []
    ms2_calls = []

    class Raw:
        def extract_xic_many(
            self, requests: tuple[XICRequest, ...]
        ) -> tuple[XICTrace, ...]:
            batches.append(requests)
            traces = []
            for request in requests:
                rt = np.arange(request.rt_min, request.rt_max + 0.001, 0.02)
                # Early strong peak needs context to return to baseline; the
                # weaker near-window-center peak is not a valid identity proxy.
                intensity = (
                    10.0
                    + 100000.0 * np.exp(-0.5 * ((rt - 8.1) / 0.13) ** 2)
                    + 20000.0 * np.exp(-0.5 * ((rt - 9.0) / 0.09) ** 2)
                )
                traces.append(XICTrace.from_arrays(rt, intensity))
            return tuple(traces)

        def iter_ms2_scans(
            self, rt_min: float, rt_max: float
        ) -> tuple[Ms2ScanEvent, ...]:
            ms2_calls.append((rt_min, rt_max))
            return tuple(events)

    @contextmanager
    def open_raw(path: Path, *, dll_dir: Path):
        opens.append((path, dll_dir))
        yield Raw()

    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", open_raw)
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "unused.csv",
        diagnostics_csv=tmp_path / "unused-diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=1.1,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
    )
    reference = extract_batch_known_istd_reference(
        targets,
        {"S2": tmp_path / "S2.raw", "S1": tmp_path / "S1.raw"},
        tmp_path,
        config,
        source_identifier="test-source",
        source_sha256="a" * 64,
    )
    if expected_rt is None:
        assert reference.coordinates == ()
    else:
        assert {(label, sample) for label, sample, _, _ in reference.coordinates} == {
            (label, sample) for label in ("ISTD1", "ISTD2") for sample in ("S1", "S2")
        }
        assert all(
            rt == pytest.approx(expected_rt, abs=0.021)
            for _, _, rt, _ in reference.coordinates
        )
    assert reference.workbook_path == "test-source"
    assert reference.workbook_sha256 == "a" * 64
    assert len(opens) == len(batches) == 2
    assert all(len(requests) == 2 for requests in batches)
    assert all(
        (request.rt_min, request.rt_max) == (7.0, 11.0)
        for requests in batches
        for request in requests
    )
    assert ms2_calls == ([] if neutral_loss is None else [(8.0, 10.0)] * 2)
