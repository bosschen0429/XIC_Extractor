from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

from tests.alignment_known_istd_in_memory_fixtures import (
    _make_target,
)
from tests.targeted_fixtures import ScopedMs2WindowReader
from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
    mint_known_istd_reference_from_measurements,
)
from xic_extractor.applications.untargeted.istd_reference import (
    extract_batch_known_istd_reference,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.targets import read_targets
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
from xic_extractor.xic_models import XICTrace


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
    assert ref.workbook_sha256 == ""
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


def test_read_targets_from_csv() -> None:
    csv_path = Path("xic_extractor/configuration/data/targets.csv")
    if not csv_path.is_file():
        pytest.skip("xic_extractor/configuration/data/targets.csv not found")

    targets = read_targets(csv_path)
    assert len(targets) == 14
    istd_targets = [t for t in targets if t.is_istd]
    assert len(istd_targets) == 7
    assert all(t.is_istd for t in istd_targets)


def test_untargeted_istd_uses_targeted_selection_for_multiple_supported_peaks(
    tmp_path,
    monkeypatch,
):
    from xic_extractor.configuration.targets import parse_target_rows
    from xic_extractor.extraction.scoring_factory import build_scoring_context_factory
    from xic_extractor.extraction.target_extraction import (
        credible_istd_anchor_rt,
        process_file,
    )

    targets = parse_target_rows(
        [
            {
                "label": "ISTD",
                "mz": "300",
                "expected_rt_min": "8.3",
                "cid_nl_tag": "dR",
                "role": "istd",
                "istd_pair": "",
            }
        ]
    )
    measured = {"S1": 8.1, "S2": 8.5}
    calls = []

    class Raw(ScopedMs2WindowReader):
        def __init__(self, sample):
            self.sample = sample
            self.apex = measured[sample]

        def acquisition_rt_bounds(self):
            return 1.0, 15.0

        def extract_xic(self, mz, low, high, ppm):
            rt = np.arange(low, high + 0.001, 0.02)
            intensity = (
                10
                + 100000 * np.exp(-0.5 * ((rt - self.apex) / 0.13) ** 2)
                + 300000 * np.exp(-0.5 * ((rt - 4) / 0.13) ** 2)
            )
            return rt, intensity

        def extract_xic_many(self, requests):
            return tuple(
                XICTrace.from_arrays(
                    *self.extract_xic(x.mz, x.rt_min, x.rt_max, x.ppm_tol)
                )
                for x in requests
            )

        def iter_ms2_scans(self, low, high):
            calls.append(self.sample)
            return tuple(
                Ms2ScanEvent(
                    Ms2Scan(
                        i, rt, 300.0, np.array([183.9526]), np.array([1000.0]), 1000.0
                    ),
                    None,
                    i,
                )
                for i, rt in enumerate((4.0, self.apex), 1)
                if low <= rt <= high
            )

    @contextmanager
    def open_raw(path, *args, **kwargs):
        yield Raw(path.stem)

    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", open_raw)
    monkeypatch.setattr("xic_extractor.extraction.target_extraction.open_raw", open_raw)
    config = _config(tmp_path)
    factory = build_scoring_context_factory(config=config)
    expected = {}
    for sample in measured:
        result, _ = process_file(
            config,
            list(targets),
            tmp_path / f"{sample}.raw",
            scoring_context_factory=factory,
        )
        assert result.error is None
        expected[sample] = credible_istd_anchor_rt(result.results["ISTD"])
    assert expected == pytest.approx(measured, abs=0.021)
    calls.clear()
    reference = extract_batch_known_istd_reference(
        targets,
        {s: tmp_path / f"{s}.raw" for s in measured},
        tmp_path,
        config,
    )
    assert {sample: rt for _, sample, rt, _ in reference.coordinates} == expected
    assert calls == ["S1", "S2"]


def test_istd_reference_preserves_targeted_anchor_rejection(tmp_path, monkeypatch):
    from tests.target_extraction_fixtures import _candidate, _peak_result
    from xic_extractor.extraction.result_models import ExtractionResult, FileResult
    from xic_extractor.output.targeted_product_model import TargetedProductProjection

    target = _make_target("ISTD")

    def process_file(
        config,
        targets,
        path,
        *,
        scoring_context_factory,
        expected_acquisition_source,
        prepared_mass_recalibration,
        defer_analyte_selection,
    ):
        assert defer_analyte_selection is False
        assert targets == [target]
        assert config.dll_dir == tmp_path / "dll"
        projection = (
            TargetedProductProjection(
                product_state="not_counted",
                counted_detection=False,
                review_state="review_required",
                projection_reason="selected_envelope_boundary_defer",
            )
            if path.stem == "rejected"
            else None
        )
        return FileResult(
            path.stem,
            {
                "ISTD": ExtractionResult(
                    peak_result=_peak_result(_candidate(rt=3.0)),
                    nl=None,
                    targeted_product_projection=projection,
                )
            },
        ), []

    monkeypatch.setattr(
        "xic_extractor.extraction.target_extraction.process_file", process_file
    )
    reference = extract_batch_known_istd_reference(
        [target, _make_target("Analyte", is_istd=False)],
        {"A": tmp_path / "accepted.raw", "B": tmp_path / "rejected.raw"},
        tmp_path / "dll",
        _config(tmp_path),
    )
    assert reference.coordinates == (("ISTD", "A", 3.0, 0.0),)


def test_istd_reference_runs_raw_files_through_targeted_process_backend(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from tests.target_extraction_fixtures import _candidate, _peak_result
    from xic_extractor.extraction.result_models import (
        ExtractionResult,
        FileResult,
        RunOutput,
    )

    target = _make_target("ISTD")
    calls = []

    def run_process(
        config, targets, *, raw_paths, injection_order, mass_recalibration_by_sample
    ):
        calls.append((config.parallel_mode, targets, raw_paths))
        return RunOutput(
            file_results=[
                FileResult(
                    path.stem,
                    {
                        "ISTD": ExtractionResult(
                            peak_result=_peak_result(_candidate(rt=rt)), nl=None
                        )
                    },
                )
                for path, rt in zip(raw_paths, (3.0, 3.2), strict=True)
            ],
            diagnostics=[],
        )

    monkeypatch.setattr(
        "xic_extractor.extraction.process_backend.run_process", run_process
    )
    reference = extract_batch_known_istd_reference(
        [target, _make_target("Analyte", is_istd=False)],
        {"B": tmp_path / "b.raw", "A": tmp_path / "a.raw"},
        tmp_path / "dll",
        replace(_config(tmp_path), parallel_mode="process"),
    )
    assert calls == [("process", [target], [tmp_path / "a.raw", tmp_path / "b.raw"])]
    assert {(label, sample) for label, sample, *_ in reference.coordinates} == {
        ("ISTD", "A"),
        ("ISTD", "B"),
    }


def test_alignment_peak_config_runs_istd_extraction_in_process_mode(tmp_path):
    from scripts.run_alignment import _peak_config

    assert (
        _peak_config(tmp_path, tmp_path, tmp_path, "local_minimum").parallel_mode
        == "process"
    )


def test_istd_file_failure_is_not_an_empty_reference(tmp_path, monkeypatch):
    from xic_extractor.extraction.result_models import FileResult

    monkeypatch.setattr(
        "xic_extractor.extraction.target_extraction.process_file",
        lambda *args, **kwargs: (FileResult("S1", {}, error="reader failed"), []),
    )
    with pytest.raises(RuntimeError, match="S1: reader failed"):
        extract_batch_known_istd_reference(
            [_make_target("ISTD")],
            {"S1": tmp_path / "S1.raw"},
            tmp_path,
            _config(tmp_path),
        )


@pytest.mark.parametrize("separate_batch", [False, True])
def test_istd_reference_recovers_cross_sample_peak_without_local_nl(
    tmp_path, monkeypatch, separate_batch
):
    from xic_extractor.configuration.targets import parse_target_rows
    from xic_extractor.sample_metadata import SampleMetadata

    masses = (300, 400, 500, 600, 700)
    centers = (8, 12, 16, 23, 30)
    targets = parse_target_rows(
        [
            dict(
                label=f"I{i}",
                mz=str(mz),
                expected_rt_min=str(rt),
                cid_nl_tag="dR",
                role="istd",
                istd_pair="",
            )
            for i, (mz, rt) in enumerate(zip(masses, centers, strict=True))
        ]
    )

    class Raw(ScopedMs2WindowReader):
        def __init__(self, sample):
            self.sample = sample
            self.shift = int(sample[1:]) * 0.1

        def acquisition_rt_bounds(self):
            return 1.0, 35.0

        def extract_xic(self, mz, low, high, ppm):
            rt = np.arange(low, high + 0.001, 0.02)
            apex = centers[masses.index(round(mz))] + self.shift
            intensity = 10 + 100000 * np.exp(-0.5 * ((rt - apex) / 0.13) ** 2)
            if round(mz) == 600 and self.sample == "S2":
                intensity += 300000 * np.exp(-0.5 * ((rt - 9.7) / 0.13) ** 2)
            return rt, intensity

        def extract_xic_many(self, requests):
            return tuple(
                XICTrace.from_arrays(
                    *self.extract_xic(x.mz, x.rt_min, x.rt_max, x.ppm_tol)
                )
                for x in requests
            )

        def iter_ms2_scans(self, low, high):
            return tuple(
                Ms2ScanEvent(
                    Ms2Scan(
                        i + 1,
                        rt,
                        float(mz),
                        np.array([mz - 116.0474]),
                        np.array([1000.0]),
                        1000.0,
                    ),
                    None,
                    i + 1,
                )
                for i, (mz, center) in enumerate(zip(masses, centers, strict=True))
                for rt in [
                    9.7 if mz == 600 and self.sample == "S2" else center + self.shift
                ]
                if low <= rt <= high
            )

    @contextmanager
    def open_raw(path, *args, **kwargs):
        yield Raw(path.stem)

    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", open_raw)
    monkeypatch.setattr("xic_extractor.extraction.target_extraction.open_raw", open_raw)
    reference = extract_batch_known_istd_reference(
        targets,
        {s: tmp_path / f"{s}.raw" for s in ("S0", "S1", "S2", "S3")},
        tmp_path,
        _config(tmp_path),
        sample_metadata=tuple(
            SampleMetadata(
                s, s, batch_id="other" if separate_batch and s == "S2" else "main"
            )
            for s in ("S0", "S1", "S2", "S3")
        ),
    )
    coordinates = {
        (label, sample): rt for label, sample, rt, _ in reference.coordinates
    }
    for index, center in enumerate(centers):
        for sample in ("S0", "S1", "S2", "S3"):
            if (index, sample) != (3, "S2"):
                assert coordinates[f"I{index}", sample] == pytest.approx(
                    center + int(sample[1:]) * 0.1, abs=0.021
                )
    if separate_batch:
        # The unrelated 9.7-minute NL peak is not an initial ISTD anchor.
        # Without same-cohort peers, the unconfirmed 23.2-minute body cannot
        # become a confirmed Alignment coordinate either.
        assert ("I3", "S2") not in coordinates
        return
    assert coordinates["I3", "S2"] == pytest.approx(23.2, abs=0.021)


def _config(tmp_path):
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "unused.csv",
        diagnostics_csv=tmp_path / "unused-diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=1.1,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
    )
