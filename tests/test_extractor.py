import csv
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import NLResult
from xic_extractor.extraction.result_models import ExtractionResult
from xic_extractor.output.excel_pipeline import (
    _diagnostics_to_rows,
    _run_output_to_long_rows,
)
from xic_extractor.peak_detection.evidence_facts import (
    build_candidate_evidence_facts,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidateScore,
    PeakCandidatesResult,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.scoring_models import ScoringContext
from xic_extractor.peak_detection.signal_quality import SignalQuality
from xic_extractor.raw_reader import Ms2Scan, Ms2ScanEvent, RawReaderError

_OPEN_RAW_SEAM = "xic_extractor.extraction.target_extraction.open_raw"

_active_nl_fixture_results: list[NLResult] = []


@pytest.mark.parametrize("neutral_loss", [None, 116.0474])
def test_duplicate_samples_preserve_reference_supported_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, neutral_loss: float | None
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "A.raw").write_bytes(b"A")
    target = replace(
        _target("Main", neutral_loss_da=neutral_loss, rt_min=10, rt_max=10),
        cid_nl_tag="dR" if neutral_loss is not None else "",
        expected_rt_min=10,
        nl_ppm_max=20 if neutral_loss is not None else None,
    )

    class Raw(_FakeRaw):
        def extract_xic(self, *_args):
            rt = np.linspace(0, 20, 2001)
            signal = sum(
                height * np.exp(-0.5 * ((rt - center) / 0.1) ** 2)
                for center, height in ((10, 1000), (12, 10000))
            )
            return rt, 10 + signal

        def iter_ms2_scans(self, *_args):
            if neutral_loss is None:
                return
            for number, center in enumerate((10, 12), start=1):
                scan = Ms2Scan(
                    scan_number=number,
                    rt=center,
                    precursor_mz=target.mz,
                    masses=np.array([target.mz - neutral_loss]),
                    intensities=np.array([1000.0]),
                    base_peak=1000.0,
                )
                yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=number)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_a, **_kw: Raw())
    single = _run_formal_selection(config, [target])
    (config.data_dir / "B.raw").write_bytes(b"B")
    repeated = _run_formal_selection(config, [target])
    expected_area = 1000 * 0.1 * math.sqrt(2 * math.pi) * 60
    first = single.file_results[0].results[target.label]
    assert first.reported_rt == pytest.approx(10, abs=0.03)
    assert first.reported_peak_area == pytest.approx(expected_area, rel=0.08)
    for file in repeated.file_results:
        result = file.results[target.label]
        assert result.reported_rt == pytest.approx(10, abs=0.03)
        assert result.reported_peak_area == pytest.approx(first.reported_peak_area)
        assert result.selected_cid_nl_supported is (neutral_loss is not None)


@pytest.mark.parametrize("neutral_loss", [None, 116.0474])
def test_same_ion_valleys_remain_separate_under_supported_sample_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, neutral_loss: float | None
) -> None:
    config = _config(tmp_path)
    for sample in ("Shifted", "Reference"):
        (config.data_dir / f"{sample}.raw").write_bytes(sample.encode())
    targets = [
        replace(
            _target(
                label,
                neutral_loss_da=neutral_loss if mz == 500 else None,
                rt_min=reference,
                rt_max=reference,
            ),
            mz=mz,
            expected_rt_min=reference,
            cid_nl_tag="dR" if mz == 500 and neutral_loss is not None else "",
            nl_ppm_max=20 if mz == 500 and neutral_loss is not None else None,
        )
        for label, mz, reference in (
            ("T0", 500, 10),
            ("T1", 500, 12),
            ("AnchorA", 600, 16),
            ("AnchorB", 700, 22),
        )
    ]

    class Raw(_FakeRaw):
        def __init__(self, sample):
            super().__init__()
            self.shift = -2 if sample == "Shifted" else 0
            self.scale = 2 if sample == "Shifted" else 1

        def acquisition_rt_bounds(self):
            return 0, 30

        def extract_xic(self, mz, *_args):
            rt = np.linspace(0, 30, 3001)
            centers = (10, 12) if mz == 500 else (16,) if mz == 600 else (22,)
            sigma = 0.7 if mz == 500 else 0.2
            signal = sum(
                self.scale * 10000 * np.exp(-0.5 * ((rt - c - self.shift) / sigma) ** 2)
                for c in centers
            )
            return rt, 10 + signal

        def iter_ms2_scans(self, *_args):
            if neutral_loss is None:
                return
            for number, center in enumerate((10, 12), start=1):
                scan = Ms2Scan(
                    scan_number=number,
                    rt=center + self.shift,
                    precursor_mz=500,
                    masses=np.array([500 - neutral_loss]),
                    intensities=np.array([1000.0]),
                    base_peak=1000.0,
                )
                yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=number)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda path, *_a, **_kw: Raw(path.stem))
    workbook = tmp_path / "output" / "correspondence.xlsx"
    output = _run_formal_selection(config, targets, workbook_path=workbook)
    results = {file.sample_name: file.results for file in output.file_results}
    # Equal analytical Gaussians separated by their midpoint valley. The
    # broad-body baseline and outer truncation are not an identity oracle.
    unit_area = 10000 * 0.7 * math.sqrt(2 * math.pi) * 60
    for sample, shift, scale in (("Reference", 0, 1), ("Shifted", -2, 2)):
        left, right = results[sample]["T0"], results[sample]["T1"]
        assert left.reported_rt == pytest.approx(10 + shift, abs=0.1)
        assert right.reported_rt == pytest.approx(12 + shift, abs=0.1)
        assert left.reported_peak_end == pytest.approx(11 + shift, abs=0.1)
        assert left.reported_peak_end <= right.reported_peak_start
        for result in (left, right):
            assert result.reported_peak_area == pytest.approx(
                unit_area * scale, rel=0.2
            )
            assert (
                "targeted_shared_ion_region"
                not in result.targeted_product_projection.conflict_reasons
            )
            assert result.selected_cid_nl_supported is (neutral_loss is not None)
    for label in ("T0", "T1"):
        assert results["Shifted"][label].reported_peak_area == pytest.approx(
            results["Reference"][label].reported_peak_area * 2, rel=0.02
        )
    from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource

    saved = {
        (row["SampleName"], row["Target"]): row
        for row in TargetedWorkbookSource(workbook).records
    }
    for sample in ("Reference", "Shifted"):
        for label in ("T0", "T1"):
            row = saved[(sample, label)]
            assert row["RT"] == pytest.approx(
                results[sample][label].reported_rt, abs=5e-5
            )
            assert row["Area"] == pytest.approx(
                results[sample][label].reported_peak_area, rel=1e-8
            )
    reversed_output = _run_formal_selection(
        config, list(reversed(targets)), reverse_samples=True
    )
    assert {
        (file.sample_name, label): (result.reported_rt, result.reported_peak_area)
        for file in reversed_output.file_results
        for label, result in file.results.items()
    } == {
        (file.sample_name, label): (result.reported_rt, result.reported_peak_area)
        for file in output.file_results
        for label, result in file.results.items()
    }


def test_boundary_cid_nl_keeps_possible_lobes_without_merging_or_individual_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "Sample.raw").write_bytes(b"raw placeholder")
    target = replace(
        _target("Main", rt_min=9, rt_max=9),
        mz=500,
        expected_rt_min=9,
        nl_ppm_max=20,
    )

    class Raw(_FakeRaw):
        def extract_xic(self, *_args):
            rt = np.linspace(0, 20, 2001)
            signal = sum(
                10000 * np.exp(-0.5 * ((rt - center) / 0.22) ** 2)
                for center in (3, 8.6, 9.4)
            )
            return rt, 10 + signal

        def iter_ms2_scans(self, *_args):
            # One event is unique to a remote peak. The other lies at the
            # shared valley, not strictly inside either target lobe.
            for number, center in enumerate((3, 9), start=1):
                scan = Ms2Scan(
                    scan_number=number,
                    rt=center,
                    precursor_mz=target.mz,
                    masses=np.array([target.mz - target.neutral_loss_da]),
                    intensities=np.array([1000.0]),
                    base_peak=1000.0,
                )
                yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=number)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_a, **_kw: Raw())
    result = _run_formal_selection(config, [target]).file_results[0].results["Main"]
    assert 8 < result.reported_rt < 10
    assert not (result.reported_peak_start < 8.6 and result.reported_peak_end > 9.4)
    expected_lobe = 10000 * 0.22 * math.sqrt(2 * math.pi) * 60
    assert result.reported_peak_area == pytest.approx(expected_lobe, rel=0.08)
    assert not result.selected_cid_nl_supported
    assert result.cid_nl_relations.set_relation_event_indexes
    assert result.selected_cid_nl_candidate_index in (
        result.cid_nl_relations.set_related_candidate_indexes
    )


def _run_formal_selection(
    config, targets, *, reverse_samples=False, workbook_path=None
):
    from xic_extractor.applications.targeted.application import (
        build_canonical_targeted_application_service,
        build_targeted_application_request,
    )

    injection_order = {
        path.stem: index + 1
        for index, path in enumerate(
            sorted(config.data_dir.glob("*.raw"), reverse=reverse_samples)
        )
    }
    request = build_targeted_application_request(
        config,
        targets,
        injection_order=injection_order,
        excel_output_path=workbook_path,
    )
    return build_canonical_targeted_application_service(_run).execute(request)


@pytest.mark.parametrize("lobes", [2, 3])
def test_ms1_same_mass_targets_use_valleys_and_reference_order(
    tmp_path, monkeypatch, lobes
):
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_bytes(b"raw placeholder")
    rt = np.linspace(0, 20, 2001)
    centers = [9.0, 11.0, 13.0] if lobes == 3 else [10.0, 13.0]
    references = [8.9, 11.1, 13.2] if lobes == 3 else [9.4, 10.4, 13.2]
    intensity = 10.0 + sum(
        1000.0 * np.exp(-0.5 * ((rt - c) / 0.7) ** 2) for c in centers
    )
    targets = [
        replace(
            _target(f"T{i}", neutral_loss_da=None, rt_min=ref, rt_max=ref),
            mz=500.0 + i * 0.0001,
        )
        for i, ref in enumerate(references)
    ]
    raw = _FakeRaw()
    monkeypatch.setattr(raw, "extract_xic", lambda *_args: (rt, intensity))
    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: raw)
    output = _run(config, targets)
    results = output.file_results[0].results
    numeric = [
        results[t.label]
        for t in targets
        if results[t.label].reported_peak_area is not None
    ]
    assert len(numeric) == 3
    distinct = list(
        {(r.reported_peak_start, r.reported_peak_end): r for r in numeric}.values()
    )
    assert [r.reported_rt for r in distinct] == pytest.approx(centers, abs=0.1)
    assert all(
        left.reported_peak_end <= right.reported_peak_start
        for left, right in zip(distinct, distinct[1:], strict=False)
    )
    if lobes == 3:
        assert numeric[0].reported_peak_end == pytest.approx(10.0, abs=0.2)
        assert numeric[1].reported_peak_end == pytest.approx(12.0, abs=0.2)
    else:
        assert results["T0"].reported_peak_area == results["T1"].reported_peak_area
        for label in ("T0", "T1"):
            projection = results[label].targeted_product_projection
            assert "targeted_shared_ion_region" in projection.conflict_reasons
            assert projection.counted_detection is False
    reversed_results = _run(config, list(reversed(targets))).file_results[0].results
    assert {
        label: (r.reported_rt, r.reported_peak_area)
        for label, r in reversed_results.items()
    } == {label: (r.reported_rt, r.reported_peak_area) for label, r in results.items()}


@pytest.mark.parametrize(
    "signal_kind",
    ["far_only", "double_top", "rippled_top", "single_scan", "broad", "separate"],
)
def test_ms1_only_localizes_then_integrates_without_shape_veto(
    tmp_path, monkeypatch, signal_kind
):
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_bytes(b"raw placeholder")
    target = _target("MS1", neutral_loss_da=None, rt_min=9.0, rt_max=9.0)
    rt = np.linspace(0, 20, 2001)
    remote = 500.0 * np.exp(-0.5 * ((rt - 3.0) / 0.10) ** 2)
    if signal_kind == "far_only":
        intensity = 10.0 + remote
    elif signal_kind == "double_top":
        intensity = (
            10.0
            + remote
            + 1000.0
            * (
                np.exp(-0.5 * ((rt - 8.6) / 0.22) ** 2)
                + np.exp(-0.5 * ((rt - 9.4) / 0.22) ** 2)
            )
        )
    elif signal_kind == "rippled_top":
        intensity = 10.0 + 1000.0 * np.exp(-0.5 * ((rt - 9.0) / 0.5) ** 2) * (
            1 + 0.05 * np.cos(rt * 60)
        )
    elif signal_kind == "broad":
        intensity = 10.0 + 1000.0 * np.exp(-0.5 * ((rt - 10.5) / 0.8) ** 2)
    elif signal_kind == "separate":
        target = replace(target, rt_min=9.4, rt_max=9.4)
        intensity = 10.0 + 1000.0 * (
            np.exp(-0.5 * ((rt - 8.6) / 0.08) ** 2)
            + np.exp(-0.5 * ((rt - 9.4) / 0.08) ** 2)
        )
    else:
        intensity = np.full_like(rt, 10.0)
        intensity[900] = 10000.0
    raw = _FakeRaw()

    def extract(mz, *_args):
        if mz == target.mz:
            return rt, intensity
        center = 4.0 if mz == 400.0 else 15.0
        return rt, 10.0 + 1000.0 * np.exp(-0.5 * ((rt - center) / 0.2) ** 2)

    monkeypatch.setattr(raw, "extract_xic", extract)
    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: raw)
    targets = [target]
    if signal_kind == "far_only":
        # Independent reference-linked bodies establish the sample's RT frame.
        # A single target alone does not supply a scientifically justified
        # universal maximum RT error.
        targets += [
            replace(
                _target("AnchorA", neutral_loss_da=None, rt_min=4, rt_max=4), mz=400
            ),
            replace(
                _target("AnchorB", neutral_loss_da=None, rt_min=15, rt_max=15), mz=500
            ),
        ]
    output = _run(config, targets)
    result = output.file_results[0].results[target.label]
    if signal_kind in {"far_only", "single_scan"}:
        assert result.reported_peak_area is None
        assert result.reported_rt is None
    else:
        assert result.reported_peak_area is not None
        assert result.reported_peak_area > 0
        if signal_kind == "broad":
            assert result.reported_rt > 10.0
            assert result.reported_peak_start < 9.0
        else:
            assert 8.0 < result.reported_rt < 10.0
        if signal_kind == "separate":
            assert result.reported_rt == pytest.approx(9.4, abs=0.02)
            assert result.reported_peak_start > 9.1
        if signal_kind == "double_top":
            # One target reference does not authorize adding two resolved
            # observed peaks. The selected value is one complete lobe.
            assert min(abs(result.reported_rt - center) for center in (8.6, 9.4)) < 0.02
            assert not (
                result.reported_peak_start < 8.6 and result.reported_peak_end > 9.4
            )
            one_lobe_area = 1000.0 * 0.22 * math.sqrt(2 * math.pi) * 60.0
            assert result.reported_peak_area == pytest.approx(one_lobe_area, rel=0.08)


@pytest.fixture(autouse=True)
def _disable_reader_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    global _active_nl_fixture_results
    _active_nl_fixture_results = []
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.preflight_raw_reader",
        lambda _dll_dir: [],
        raising=False,
    )
    install_fake_raw_headers(monkeypatch)


@pytest.mark.parametrize("neutral_loss", [None, 116.0474])
@pytest.mark.parametrize("control_signal", ["absent", "one_scan", "body"])
def test_peer_alternate_band_cannot_replace_unquantifiable_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    neutral_loss: float | None,
    control_signal: str,
) -> None:
    from xic_extractor.configuration.targets import parse_target_rows

    config = replace(
        _config(tmp_path),
        signal_processing_method="gaussian",
        signal_processing_window_points=9,
    )
    samples = ("Control", "Treated", "Dose")
    for sample in samples:
        (config.data_dir / f"{sample}.raw").write_bytes(b"raw placeholder")
    targets = list(
        parse_target_rows(
            [
                dict(
                    label=label,
                    mz=str(mz),
                    expected_rt_min=str(reference),
                    cid_nl_tag="dR" if label == "Main" and neutral_loss else "",
                    role="analyte",
                    istd_pair="",
                )
                for label, mz, reference in (
                    ("Main", 258.1085, 10.2),
                    ("ShiftA", 400, 20),
                    ("ShiftB", 500, 30),
                    ("StableA", 600, 5),
                    ("StableB", 700, 35),
                )
            ]
        )
    )

    class Raw(_FakeRaw):
        def __init__(self, sample):
            super().__init__()
            self.sample = sample
            self.shift = {"Control": 0, "Treated": 0.3, "Dose": 0.5}[sample]

        def acquisition_rt_bounds(self):
            return 0.0, 40.0

        def extract_xic(self, mz, *_args):
            rt = np.arange(0.0, 40.01, 0.02)
            center = 10 + self.shift
            if mz != 258.1085:
                reference = {400: 20, 500: 30, 600: 5, 700: 35}[round(mz)]
                offset = (
                    {"Control": -3, "Treated": 0, "Dose": 0.3}[self.sample]
                    if mz < 600
                    else self.shift
                )
                return rt, 20000 * np.exp(-0.5 * ((rt - reference - offset) / 0.1) ** 2)
            amplitude = {"Control": 10000, "Treated": 12000, "Dose": 18000}[self.sample]
            signal = amplitude * np.exp(-0.5 * ((rt - center) / 0.1) ** 2)
            if self.sample == "Control" and control_signal != "body":
                signal[:] = 0
                if control_signal == "one_scan":
                    signal[np.argmin(abs(rt - center))] = amplitude
            # This second band exists in every sample but is not the target
            # chosen in the peers. Its single peer detections still carry RT
            # correspondence evidence, even though they cannot carry quantity.
            if self.sample == "Control":
                signal += 7000 * np.exp(-0.5 * ((rt - 13) / 0.1) ** 2)
            else:
                signal[np.argmin(abs(rt - (13 + self.shift)))] += 5000
            return rt, signal

        def iter_ms2_scans(self, _rt_min, _rt_max):
            if neutral_loss is None:
                return
            for number, center in enumerate((10, 13), start=1):
                scan = Ms2Scan(
                    scan_number=number,
                    rt=center + self.shift,
                    precursor_mz=258.1085,
                    masses=np.array([258.1085 - neutral_loss]),
                    intensities=np.array([1000.0]),
                    base_peak=1000.0,
                )
                yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=number)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda path, *_a, **_kw: Raw(path.stem))
    output = _run(config, targets)
    results = {f.sample_name: f.results["Main"] for f in output.file_results}
    for sample, center, amplitude in (("Treated", 10.3, 12000), ("Dose", 10.5, 18000)):
        assert results[sample].reported_rt == pytest.approx(center, abs=0.03)
        expected_area = amplitude * 0.1 * np.sqrt(2 * np.pi) * 60
        assert results[sample].reported_peak_area == pytest.approx(
            expected_area, rel=0.08
        )
    control = results["Control"]
    if control_signal != "body":
        assert control.reported_peak_area is None
        assert control.reported_rt is None
        assert not control.targeted_product_projection.counted_detection
        assert (
            "targeted_cross_sample_region_conflict"
            in control.targeted_product_projection.review_reasons
        )
        assert "targeted_cross_sample_region_conflict" in (
            control.targeted_product_projection.conflict_reasons
        )
        if neutral_loss is not None:
            assert control.cid_nl_relations.supported_candidate_indexes
    else:
        assert control.reported_rt == pytest.approx(10.0, abs=0.03)
        expected_area = 10000 * 0.1 * np.sqrt(2 * np.pi) * 60
        assert control.reported_peak_area == pytest.approx(expected_area, rel=0.08)


@pytest.mark.parametrize("neutral_loss", [None, 116.0474])
def test_many_background_regions_do_not_erase_a_clear_targeted_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, neutral_loss: float | None
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    target = replace(
        _target("Main", rt_min=29.0, rt_max=31.0),
        expected_rt_min=30.0,
        neutral_loss_da=neutral_loss,
    )

    class Raw(_FakeRaw):
        def acquisition_rt_bounds(self):
            return 0.0, 60.0

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.arange(0.0, 60.005, 0.01)
            # More background lobes than the generic universe budget. Their
            # count cannot delete a clearly observed, unrelated main body.
            background = sum(
                50.0 * np.exp(-0.5 * ((rt - center) / 0.025) ** 2)
                for center in np.arange(1.0, 59.0, 0.32)
            )
            main = 20000.0 * np.exp(-0.5 * ((rt - 30.0) / 0.16) ** 2)
            return rt, background + main

        def iter_ms2_scans(self, rt_min, rt_max):
            if neutral_loss is None:
                return
            scan = Ms2Scan(
                scan_number=1,
                rt=30.0,
                precursor_mz=target.mz,
                masses=np.array([target.mz - neutral_loss]),
                intensities=np.array([1000.0]),
                base_peak=1000.0,
            )
            yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=1)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: Raw())
    result = _run(config, [target]).file_results[0].results["Main"]
    assert result.reported_rt == pytest.approx(30.0, abs=0.03)
    # Independent Gaussian area oracle, with truncated boundary/baseline room.
    expected_area = 20000.0 * 0.16 * np.sqrt(2 * np.pi) * 60
    assert result.reported_peak_area == pytest.approx(expected_area, rel=0.08)
    assert len(result.candidate_physical_evaluations) > 128
    if neutral_loss is not None:
        assert result.selected_cid_nl_supported


@pytest.mark.parametrize("neutral_loss", [None, 116.0474])
@pytest.mark.parametrize("reference_bias", [0.0, 2.5, 22.5])
def test_batch_context_uses_supported_bodies_and_retention_uncertainty(
    tmp_path, monkeypatch, neutral_loss, reference_bias
):
    config = _config(tmp_path)
    for sample in ("Control", "Treated"):
        (config.data_dir / f"{sample}.raw").write_bytes(b"raw placeholder")
    from xic_extractor.configuration.targets import parse_target_rows

    targets = list(
        parse_target_rows(
            [
                {
                    "label": label,
                    "mz": str(mz),
                    "expected_rt_min": str(reference),
                    "cid_nl_tag": "dR"
                    if label == "Main" and neutral_loss is not None
                    else "",
                    "role": "analyte",
                    "istd_pair": "",
                }
                for label, mz, reference in (
                    ("Main", 258.1085, 10 + reference_bias),
                    ("AnchorA", 400, 20),
                    ("AnchorB", 500, 30),
                )
            ]
        )
    )
    acquisitions = []

    class Raw(_FakeRaw):
        def __init__(self, sample):
            super().__init__()
            self.shift = -2 if sample == "Control" else 0
            self.amplitude = 10000 if sample == "Control" else 20000

        def acquisition_rt_bounds(self):
            return 0, 40

        def extract_xic(self, mz, *_args):
            acquisitions.append((self.shift, mz))
            rt = np.linspace(0, 40, 8001)
            reference = 10 if mz == targets[0].mz else 20 if mz == 400 else 30
            center = reference + self.shift
            signal = self.amplitude * np.exp(-0.5 * ((rt - center) / 0.4) ** 2)
            signal = np.minimum(signal, self.amplitude / 2)
            if mz == targets[0].mz:
                signal += 100 * np.exp(-0.5 * ((rt - reference) / 0.02) ** 2)
            return rt, 10 + signal

        def iter_ms2_scans(self, rt_min, rt_max):
            if neutral_loss is None:
                return
            scan = Ms2Scan(
                scan_number=1,
                rt=10 + self.shift,
                precursor_mz=targets[0].mz,
                masses=np.array([targets[0].mz - neutral_loss]),
                intensities=np.array([1000.0]),
                base_peak=1000.0,
            )
            yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=1)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda path, *_args, **_kwargs: Raw(path.stem))
    from xic_extractor.applications.targeted.application import (
        build_targeted_application_request,
    )
    from xic_extractor.applications.targeted.production_workflow import (
        execute_targeted_production_workflow,
        snapshot_targeted_production_inputs,
    )

    request = build_targeted_application_request(
        config, targets, injection_order={"Control": 1, "Treated": 2}
    )
    snapshot = snapshot_targeted_production_inputs(request)
    output = _run(
        config,
        targets,
        acquisition_sources_by_sample=snapshot.acquisition_sources_by_sample(),
    )
    results = {f.sample_name: f.results["Main"] for f in output.file_results}
    sigma = 0.4
    plateau_edge = sigma * math.sqrt(2 * math.log(2))
    unit_area = plateau_edge + sigma * math.sqrt(2 * math.pi) * math.erfc(
        plateau_edge / (sigma * math.sqrt(2))
    )
    for sample, center, amplitude in (("Control", 8, 10000), ("Treated", 10, 20000)):
        result = results[sample]
        if reference_bias == 22.5:
            assert result.reported_peak_area is None
            assert result.reported_rt is None
            if neutral_loss is not None:
                # The acquired event still supports the regional population.
                # An event at a retained valley is set-level evidence, not
                # individual support manufactured by merging before selection.
                relations = result.cid_nl_relations
                assert (
                    relations.supported_candidate_indexes
                    or relations.set_relation_event_indexes
                )
                assert not result.selected_cid_nl_supported
            continue
        assert result.reported_rt == pytest.approx(center, abs=0.55)
        assert result.reported_peak_start < center < result.reported_peak_end
        assert result.reported_peak_area == pytest.approx(
            amplitude * unit_area * 60, rel=0.15
        )
        assert result.selected_cid_nl_supported is (neutral_loss is not None)
        assert result.targeted_candidate_assessment is not None
        assert len(result.targeted_candidate_assessment.universe.segments) >= 1
    if reference_bias != 22.5:
        assert results["Treated"].reported_peak_area / results[
            "Control"
        ].reported_peak_area == pytest.approx(2, rel=0.1)
        assert (
            "targeted_reference_displacement"
            in results["Control"].targeted_product_projection.review_reasons
        )
    finalized = execute_targeted_production_workflow(
        request, output, expected_inputs=snapshot
    )
    main = next(
        result
        for result in finalized
        if result.completed_projection.row_binding.target.definition.target.label
        == "Main"
    )
    assert main.completed_projection.accepted_cell_count == (
        0 if reference_bias == 22.5 else 2
    )
    assert main.completed_projection.no_write_count == (
        2 if reference_bias == 22.5 else 0
    )
    assert tuple(d.value for d in main.completed_projection.decisions) == (
        results["Control"].reported_peak_area,
        results["Treated"].reported_peak_area,
    )
    assert len(acquisitions) == len(set(acquisitions)) == 6


def test_positive_cid_nl_cannot_manufacture_area_from_one_acquired_impulse(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_bytes(b"raw placeholder")
    target = _target("Impulse")
    rt = np.linspace(0, 20, 2001)
    intensity = np.zeros_like(rt)
    intensity[900] = 10000
    raw = _FakeRaw()
    monkeypatch.setattr(raw, "extract_xic", lambda *_args: (rt, intensity))
    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: raw)
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.0, 1, 0, 1)])
    from xic_extractor.applications.targeted.application import (
        build_targeted_application_request,
    )
    from xic_extractor.applications.targeted.production_workflow import (
        execute_targeted_production_workflow,
        snapshot_targeted_production_inputs,
    )

    request = build_targeted_application_request(
        config, [target], injection_order={"SampleA": 1}
    )
    snapshot = snapshot_targeted_production_inputs(request)
    output = _run(
        config,
        [target],
        acquisition_sources_by_sample=snapshot.acquisition_sources_by_sample(),
    )
    result = output.file_results[0].results[target.label]
    assert result.cid_nl_products
    assert result.cid_nl_relations.supported_candidate_indexes
    assert result.reported_peak_area is None
    assert not result.targeted_product_projection.counted_detection
    assert result.candidate_physical_evaluations
    assert all(e.state != "available" for e in result.candidate_physical_evaluations)
    finalized = execute_targeted_production_workflow(
        request, output, expected_inputs=snapshot
    )
    assert finalized[0].completed_projection.accepted_cell_count == 0
    assert finalized[0].completed_projection.no_write_count == 1


def test_run_raises_before_processing_when_reader_preflight_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.preflight_raw_reader",
        lambda _dll_dir: ["pythonnet is not installed"],
        raising=False,
    )
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args: pytest.fail(
            "open_raw should not be called after preflight failure"
        ),
    )

    with pytest.raises(RawReaderError, match="pythonnet is not installed"):
        _run(config, [_target("Analyte")])

    assert not config.output_csv.exists()
    assert not config.diagnostics_csv.exists()


def test_run_writes_success_rows_with_area_columns_and_optional_nl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None), _target("WithNL")]
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(peak_centers=[9.5, 9.5]),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(
                    8.5,
                    1200.0,
                    3400.25,
                    neutral_loss_required=False,
                ),
                _ok_peak(9.5, 2200.0, 4400.75),
            ]
        ),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.5, 3, 0, 2)])

    output = _run(config, targets, progress_callback=lambda *_args: None)

    results = output.file_results[0].results
    assert all(d.issue != "FILE_ERROR" for d in output.diagnostics)
    assert len(output.file_results) == 1
    long_rows = _run_output_to_long_rows(output, targets)
    no_nl_projection = results["NoNL"].targeted_product_projection
    assert no_nl_projection is not None
    assert no_nl_projection.conflict_reasons == ()
    expected_rows = [
        {
            **_formatted_long_success_row("NoNL", results["NoNL"], nl=""),
            "Confidence": "LOW",
        },
        {
            **_formatted_long_success_row("WithNL", results["WithNL"], nl="OK"),
            "Confidence": "LOW",
        },
    ]
    for row, expected in zip(long_rows, expected_rows, strict=True):
        actual = _core_long_row(row)
        assert actual.pop("Reason").startswith(expected.pop("Reason").rstrip("。"))
        assert actual == expected
        assert "targeted_shared_ion_region:" in row["Projection Review Reasons"]
    assert [row["Counted Detection"] for row in long_rows] == ["TRUE", "TRUE"]
    assert [row["Product State"] for row in long_rows] == ["detected_flagged"] * 2
    assert all(not row["Projection Conflict Reasons"] for row in long_rows)
    assert results["WithNL"].candidate_ms2_evidence is not None
    assert results["WithNL"].candidate_ms2_evidence.nl_match is True


@pytest.mark.parametrize("outside_height", [0.0, 50000.0])
def test_outside_strong_peak_cannot_erase_in_window_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outside_height: float
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    windows: list[tuple[float, float]] = []

    class Raw(_FakeRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            windows.append((rt_min, rt_max))
            rt = np.arange(rt_min, rt_max + 0.0025, 0.0025)
            inside = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.04) ** 2)
            outside = outside_height * np.exp(-0.5 * ((rt - 6.0) / 0.04) ** 2)
            return rt, 10.0 + inside + outside

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: Raw())
    target = replace(_target("Inside", neutral_loss_da=None), rt_min=4.5, rt_max=5.5)
    output = _run(config, [target])
    result = output.file_results[0].results["Inside"]
    assert windows == [(0.0, 20.0)]
    assert result.reported_rt == pytest.approx(5.0, abs=0.002)
    assert result.reported_peak_area is not None
    known_area = 1000.0 * 0.04 * np.sqrt(2 * np.pi) * 60.0
    if outside_height == 0:
        assert result.reported_peak_area == pytest.approx(known_area, rel=0.04)
    # A full-acquisition AsLS baseline can change quantitation under interference;
    # Targeted must retain the component and publish the shared physical answer.
    assert result.physical_evaluation is not None
    assert result.physical_evaluation.facts is not None
    assert (
        result.reported_peak_area
        == result.physical_evaluation.facts.area_counts_seconds
    )
    assert result.reported_peak_area > 0


def test_ms1_only_without_rt_calibration_keeps_provisional_measurement_and_risk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")

    class Raw(_FakeRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.arange(rt_min, rt_max + 0.0025, 0.0025)
            outside = 5000.0 * np.exp(-0.5 * ((rt - 6.0) / 0.04) ** 2)
            return rt, 10.0 + outside

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: Raw())
    target = replace(_target("Empty", neutral_loss_da=None), rt_min=4.5, rt_max=5.5)
    output = _run(config, [target])
    result = output.file_results[0].results["Empty"]

    assert result.reported_rt == pytest.approx(6.0)
    evaluation = result.physical_evaluation
    assert evaluation is not None
    assert evaluation.segment_universe.state == "available"
    assert evaluation.state == "available"
    assert any(
        s.apex_rt_min == pytest.approx(6.0)
        for s in evaluation.segment_universe.segments
    )
    assert result.reported_peak_area > 0
    assert (
        "targeted_retention_context_unavailable"
        in result.targeted_product_projection.review_reasons
    )
    assert (
        "targeted_reference_displacement"
        in result.targeted_product_projection.review_reasons
    )


@pytest.mark.parametrize("with_supported_main", [True, False])
def test_formal_target_ranks_only_physically_supported_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_supported_main: bool
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    target = replace(
        _target("Qualified", neutral_loss_da=None),
        expected_rt_min=4.7 if with_supported_main else 6.0,
        rt_min=4.0 if with_supported_main else 5.5,
        rt_max=7.0 if with_supported_main else 6.5,
    )

    class Raw(_FakeRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.linspace(rt_min, rt_max, round((rt_max - rt_min) * 100) + 1)
            raw = np.zeros_like(rt)
            if with_supported_main:
                sigma = np.where(rt < 4.7, 0.07, 0.25)
                raw = 1000.0 * np.exp(-0.5 * ((rt - 4.7) / sigma) ** 2)
                raw[raw < 1e-4] = 0.0
            fragmented = 1000.0 * np.exp(-0.5 * ((rt - 6.0) / 0.12) ** 2)
            fragmented[fragmented < 1e-4] = 0.0
            fragmented[::2] = 0.0
            # The observed apex supplies adjacent acquired support. With empty
            # flanks, this cluster also qualifies through local hit enrichment.
            fragmented[int(np.argmin(np.abs(rt - 6.0)))] = 1000.0
            return rt, raw + fragmented

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: Raw())
    output = _run(config, [target])
    result = output.file_results[0].results[target.label]
    evaluation = result.physical_evaluation
    assert evaluation is not None
    assert evaluation.segment_universe.state == "available"
    assert len(evaluation.segment_universe.segments) == (
        2 if with_supported_main else 1
    )
    projection = result.targeted_product_projection
    assert projection is not None
    long_rows = _run_output_to_long_rows(output, [target])

    if with_supported_main:
        # The asymmetric analytic lobe is supported even though its canonical
        # shape is less symmetric than the smoothed, interrupted tail.
        assert result.reported_rt == pytest.approx(4.7, abs=0.04)
        assert evaluation.state == "available"
        assert evaluation.facts is not None
        assert result.reported_peak_area == evaluation.facts.area_counts_seconds
        # The distant interrupted component is not the reference envelope;
        # only the localized main peak participates in shape review.
        assert projection.product_state == "detected_flagged"
        assert projection.counted_detection is True
        assert "trace_morphology_review" in projection.review_reasons
        assert projection.conflict_reasons == ()
        assert sorted(
            candidate.selection_apex_rt for candidate in result.peak_result.candidates
        ) == pytest.approx([4.7, 6.0], abs=0.04)
        assert long_rows[0]["Counted Detection"] == "TRUE"
    else:
        # MS1-only measures the canonical total envelope even with interrupted
        # acquired support; it does not switch to censored-cluster quantitation.
        assert result.reported_rt == pytest.approx(6.0, abs=0.04)
        assert evaluation.state == "available"
        assert evaluation.facts is not None
        assert evaluation.integration is not None
        assert evaluation.facts.area_source == (
            "trapezoid_counts_seconds_on_canonical_signal"
        )
        assert result.reported_peak_area == evaluation.facts.area_counts_seconds
        assert result.reported_peak_area > 0
        assert projection.counted_detection is True
        assert _result_row(output, [target], "Qualified", 0)["RT"] != "ND"
        assert _result_row(output, [target], "Qualified", 0)["Area"] != "ND"
        assert long_rows[0]["Counted Detection"] == "TRUE"


def test_formal_target_drops_selection_when_final_physical_owner_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xic_extractor.peak_detection import (
        physical_peak_candidate_adapter as physical_owner,
    )
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        answer_physical_peak_question,
        mint_physical_peak_question,
    )
    from xic_extractor.peak_detection.physical_peak_models import (
        PHYSICAL_PEAK_NO_SELECTION_POLICY,
    )

    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    target = _target("Rejected", neutral_loss_da=None)
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory(peak_centers=[9.0]))

    def unavailable_answer(prepared, universe, question):
        # Consume a real, owner-bound unavailable answer. The consumer must
        # not fall back to the value ranked before the final owner response.
        rejected_question = mint_physical_peak_question(
            prepared.projection,
            universe,
            query_rt_min=universe.context_rt_start_min,
            selection_policy_version=PHYSICAL_PEAK_NO_SELECTION_POLICY,
        )
        return answer_physical_peak_question(prepared, universe, rejected_question)

    monkeypatch.setattr(
        physical_owner, "answer_physical_peak_question", unavailable_answer
    )
    output = _run(config, [target])
    result = output.file_results[0].results[target.label]

    assert result.physical_evaluation is not None
    assert result.physical_evaluation.reason == "no_selection"
    assert result.reported_rt is None
    assert result.reported_peak_area is None
    assert result.peak is None
    assert result.selected_hypothesis is None
    assert result.selection_decision is None
    assert result.targeted_product_projection is not None
    assert result.targeted_product_projection.counted_detection is False
    row = _run_output_to_long_rows(output, [target])[0]
    assert row["RT"] == row["Area"] == "ND"


@pytest.mark.parametrize("neutral_loss_da", [None, 116.0474])
def test_retained_apex_outside_rt_hint_is_measured_without_inventing_nl_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    neutral_loss_da: float | None,
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    target = replace(
        _target("OutsideApex", neutral_loss_da=neutral_loss_da),
        rt_min=9.3,
        rt_max=12.3,
    )

    class BroadTailRaw(_FakeRaw):
        def iter_ms2_scans(self, rt_min, rt_max):
            return iter(())

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.linspace(rt_min, rt_max, 1801)
            return rt, 5000.0 * np.exp(-0.5 * ((rt - 12.6) / 0.8) ** 2)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: BroadTailRaw())
    _set_ms2_fixtures([NLResult("OK", 1.0, 10.8, 1, 0, 1)])

    output = _run(config, [target])
    result = output.file_results[0].results[target.label]
    evaluation = result.physical_evaluation
    assert evaluation is not None
    assert evaluation.segment_universe.state == "available"
    assert evaluation.segment_universe.segments
    assert all(
        segment.apex_rt_min > target.rt_max
        for segment in evaluation.segment_universe.segments
    )
    assert any(
        segment.left_rt_min < target.rt_max < segment.right_rt_min
        for segment in evaluation.segment_universe.segments
    )
    assert evaluation.state == "available"
    assert evaluation.facts is not None
    assert evaluation.integration is not None
    assert result.peak is not None
    assert result.selected_hypothesis is not None
    assert result.reported_rt == pytest.approx(12.6, abs=0.02)
    assert result.reported_peak_area == evaluation.facts.area_counts_seconds
    assert result.reported_peak_area > 0
    assert result.targeted_product_projection is not None
    assert result.targeted_product_projection.counted_detection is (
        neutral_loss_da is None
    )
    row = _run_output_to_long_rows(output, [target])[0]
    assert float(row["RT"]) == pytest.approx(12.6, abs=0.02)
    assert float(row["Area"]) > 0

    from xic_extractor.applications.targeted.application import (
        build_targeted_application_request,
    )
    from xic_extractor.applications.targeted.production_workflow import (
        execute_targeted_production_workflow,
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config, [target], injection_order={"SampleA": 1}
        ),
        output,
    )
    assert len(finalized) == 1
    assert finalized[0].scientific_core.cell_observations == ()
    # A retained measurement can be saved without establishing CID-NL identity.
    assert finalized[0].completed_projection.accepted_cell_count == 1
    assert finalized[0].completed_projection.no_write_count == 0


def test_target_selection_window_uses_one_wide_physical_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None), _target("WithNL")]
    raw = _WidePhysicalContextRaw()
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.0, 1, 0, 1)])

    output = _run(config, targets)

    assert raw.windows == [(0.0, 20.0), (0.0, 20.0)]
    no_nl = output.file_results[0].results["NoNL"].physical_evaluation
    with_nl = output.file_results[0].results["WithNL"].physical_evaluation
    assert no_nl is not None and no_nl.facts is not None
    assert with_nl is not None and with_nl.facts is not None
    assert no_nl.prepared.profile == with_nl.prepared.profile
    assert no_nl.prepared == with_nl.prepared
    assert no_nl.segment_universe == (with_nl.segment_universe)
    assert no_nl.facts.apex_rt_min == pytest.approx(9.6, abs=0.03)
    assert no_nl.facts.right_rt_min > 10.0
    assert no_nl.facts.apex_rt_min < 10.0
    assert with_nl.facts.apex_rt_min == pytest.approx(no_nl.facts.apex_rt_min)
    assert with_nl.facts.left_rt_min == pytest.approx(no_nl.facts.left_rt_min)
    assert with_nl.facts.right_rt_min == pytest.approx(no_nl.facts.right_rt_min)
    assert with_nl.facts.area_counts_seconds == pytest.approx(
        no_nl.facts.area_counts_seconds
    )


def test_run_multi_sample_preserves_sample_attribution_and_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    (config.data_dir / "SampleB.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte", neutral_loss_da=None)]

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(
            peak_centers_by_sample={
                "SampleA.raw": [8.50],
                "SampleB.raw": [9.50],
            }
        ),
    )

    output = _run(config, targets)

    assert len(output.file_results) == 2
    res_a = output.file_results[0].results["Analyte"].peak_result.peak
    assert res_a.rt == pytest.approx(8.50, abs=0.05)
    assert output.file_results[1].sample_name == "SampleB"
    res_b = output.file_results[1].results["Analyte"].peak_result.peak
    assert res_b.rt == pytest.approx(9.50, abs=0.05)

    assert len(output.file_results) == 2
    assert output.file_results[0].sample_name == "SampleA"
    assert float(_result_row(output, targets, "Analyte", 0)["RT"]) == pytest.approx(
        8.50, abs=0.05
    )
    assert output.file_results[1].sample_name == "SampleB"
    assert float(_result_row(output, targets, "Analyte", 1)["RT"]) == pytest.approx(
        9.50, abs=0.05
    )
    assert float(_result_row(output, targets, "Analyte", 0)["RT"]) != float(
        _result_row(output, targets, "Analyte", 1)["RT"]
    )

    long_rows = _run_output_to_long_rows(output, targets)
    assert [r["SampleName"] for r in long_rows] == ["SampleA", "SampleB"]
    assert float(long_rows[0]["RT"]) == pytest.approx(8.50, abs=0.05)
    assert float(long_rows[1]["RT"]) == pytest.approx(9.50, abs=0.05)


def test_run_wires_candidate_ms2_evidence_into_scoring_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory(peak_centers=[8.5]))
    _set_ms2_fixtures([NLResult("OK", 1.0, 8.5, 1, 0, 1)])

    output = _run(config, [_target("WithNL")])

    assert output.file_results[0].error is None
    result = output.file_results[0].results["WithNL"]
    index = result.selected_cid_nl_candidate_index
    assert index is not None
    assert result.candidate_ms2_evidence is result.cid_nl_candidate_evidence[index]
    assert result.candidate_ms2_evidence.nl_match is True
    assert result.selected_cid_nl_supported is True
    assert result.targeted_product_projection.counted_detection is True


def test_rt_recovery_keeps_supported_physical_candidate_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_bytes(b"")
    target = _target("WeakSupported")
    weak_height = 1000.0

    class Raw(_FakeRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.linspace(rt_min, rt_max, round((rt_max - rt_min) * 300) + 1)
            strong = 10000 * np.exp(-0.5 * ((rt - 8.0) / 0.08) ** 2)
            supported = weak_height * np.exp(-0.5 * ((rt - 9.0) / 0.08) ** 2)
            return rt, 10 + strong + supported

        def iter_ms2_scans(self, rt_min, rt_max):
            if rt_min <= 9.0 <= rt_max:
                scan = Ms2Scan(
                    scan_number=1,
                    rt=9.0,
                    precursor_mz=target.mz,
                    masses=np.array([target.mz - target.neutral_loss_da]),
                    intensities=np.array([1000.0]),
                    base_peak=1000.0,
                )
                yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=1)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: Raw())
    output = _run(config, [target])

    file_result = output.file_results[0]
    assert file_result.error is None
    result = file_result.results[target.label]
    assert result.reported_rt == pytest.approx(9.0, abs=0.02)
    known_area = (
        weak_height
        * 0.08
        * np.sqrt(2 * np.pi)
        * 60
        * math.erf(math.sqrt(math.log(10.0)))
    )
    assert result.reported_peak_area == pytest.approx(known_area, rel=0.04)
    assert result.selected_cid_nl_supported is True
    assert result.targeted_product_projection.counted_detection is True


def test_run_does_not_write_intermediate_csv_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None)]
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1200.0, 3400.25)]),
    )

    output = _run(config, targets)

    assert len(output.file_results) == 1
    assert not config.output_csv.exists()
    assert not config.output_csv.with_name("xic_results_long.csv").exists()
    assert not config.diagnostics_csv.exists()
    assert not config.output_csv.with_name("method_manifest.json").exists()
    assert not config.output_csv.with_name("peak_candidates.tsv").exists()
    assert not config.output_csv.with_name("peak_candidate_boundaries.tsv").exists()
    assert not config.output_csv.with_name("selected_envelope_diagnostics.tsv").exists()
    assert not config.output_csv.with_name(
        "peak_candidate_boundary_summary.tsv"
    ).exists()


def test_run_loads_scoring_inputs_from_config_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config = ExtractionConfig(
        **{
            **config.__dict__,
            "injection_order_source": tmp_path / "sample_info.csv",
        }
    )
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None)]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1200.0, 3400.25)]),
    )
    config.injection_order_source.write_text(
        "Sample_Name,Injection_Order\nSampleA,1\n", encoding="utf-8"
    )

    output = _run(config, targets)

    assert [result.sample_name for result in output.file_results] == ["SampleA"]


def test_run_falls_back_to_main_pass_when_prepass_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True), _target("Analyte", istd_pair="ISTD")]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(9.05, 1500.0, 2000.0),
                _ok_peak(9.07, 1200.0, 1800.0),
            ]
        ),
    )

    output = _run(config, targets)

    assert output.file_results[0].results["ISTD"].peak_result.peak is not None
    assert output.file_results[0].results["Analyte"].peak_result.peak is not None


def test_istd_single_pass_preserves_scoring_metadata_without_ms2_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(
                    9.05,
                    1500.0,
                    2000.0,
                    confidence="LOW",
                    reason="concerns: rt_prior (major)",
                    severities=((2, "rt_prior"),),
                )
            ]
        ),
    )

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    assert result.peak_result.confidence == "LOW"
    assert result.peak_result.reason == "concerns: rt_prior (major)"
    assert result.confidence == "LOW"
    assert result.selection_decision is not None
    assert result.confidence == result.selection_decision.projected_confidence
    assert result.severities == ((2, "rt_prior"),)


def test_run_does_not_publish_peak_nl_without_a_physical_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("WithNL")]

    class FlatRaw(_FakeRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.arange(rt_min, rt_max + 0.0025, 0.0025)
            return rt, np.full(len(rt), 10.0)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: FlatRaw())
    _set_ms2_fixtures([NLResult("OK", 1.2, None, 3, 0, 2)])

    output = _run(config, targets)

    assert _result_row(output, targets, "WithNL", 0)["RT"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["Int"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["Area"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["PeakStart"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["PeakEnd"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["PeakWidth"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["NL"] == ""
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert diagnostics[0]["Issue"] == "PEAK_NOT_FOUND"
    assert diagnostics[0]["Target"] == "WithNL"
    assert diagnostics[0]["Reason"] == "Physical peak unavailable: peak_not_found"


def test_run_projects_very_low_without_peak_scoped_nl_for_nd_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("WithNL")]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [_failed_peak("PEAK_NOT_FOUND", n_points=15, max_smoothed=1234.0)]
        ),
    )
    _set_ms2_fixtures([NLResult("NL_FAIL", 200.0, None, 10, 1, 3)])

    output = _run(config, targets)

    long_rows = _run_output_to_long_rows(output, targets)
    assert [_core_long_row(row) for row in long_rows] == [
        {
            "SampleName": "SampleA",
            "Group": "Other",
            "Target": "WithNL",
            "Role": "Analyte",
            "ISTD Pair": "",
            "RT": "ND",
            "Area": "ND",
            "NL": "",
            "Int": "ND",
            "PeakStart": "ND",
            "PeakEnd": "ND",
            "PeakWidth": "ND",
            "Confidence": "VERY_LOW",
            "Reason": "搜尋範圍內沒有可定量的 MS1 峰。",
        }
    ]
    assert long_rows[0]["Projection Reason"] == (
        "decision: not_counted; support: trace_coherent; "
        "review: targeted_no_plausible_measurable_region; "
        "not_counted: missing_positive_ms1_peak"
    )
    assert long_rows[0]["Product State"] == "not_counted"
    assert long_rows[0]["Counted Detection"] == "FALSE"
    assert "missing_positive_ms1_peak" in long_rows[0]["Projection Not Counted Reasons"]
    result = output.file_results[0].results["WithNL"]
    assert result.confidence == ""
    assert result.targeted_product_projection is not None
    assert result.targeted_product_projection.product_state == "not_counted"


def test_run_leaves_confidence_blank_for_file_error_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "Bad.raw").write_text("", encoding="utf-8")
    targets = [_target("WithNL")]
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(errors={"Bad.raw": RuntimeError("file locked")}),
    )

    output = _run(config, targets)

    long_rows = _run_output_to_long_rows(output, targets)
    assert [_core_long_row(row) for row in long_rows] == [
        {
            "SampleName": "Bad",
            "Group": "Other",
            "Target": "WithNL",
            "Role": "Analyte",
            "ISTD Pair": "",
            "RT": "ERROR",
            "Area": "ERROR",
            "NL": "ERROR",
            "Int": "ERROR",
            "PeakStart": "ERROR",
            "PeakEnd": "ERROR",
            "PeakWidth": "ERROR",
            "Confidence": "",
            "Reason": "RAW 檔無法處理。",
        }
    ]
    assert long_rows[0]["Product State"] == "excluded"
    assert long_rows[0]["Counted Detection"] == "FALSE"
    assert long_rows[0]["Projection Exclusion Reasons"] == "file_error"


def test_run_writes_file_error_row_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "Bad.raw").write_text("", encoding="utf-8")
    (config.data_dir / "Good.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None), _target("WithNL")]
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(errors={"Bad.raw": RuntimeError("file locked")}),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1000.0, 2000.0), _ok_peak(9.5, 1100.0, 2100.0)]),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, None, 2, 0, 1)])

    output = _run(config, targets)

    assert output.file_results[1].sample_name == "Good"
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert diagnostics[0]["SampleName"] == "Bad"
    assert diagnostics[0]["Target"] == ""
    assert diagnostics[0]["Issue"] == "FILE_ERROR"
    assert "file locked" in diagnostics[0]["Reason"]
    assert output.file_results[0].error is not None


@pytest.mark.parametrize(
    ("peak_status", "n_points", "max_smoothed", "issue", "reason_part"),
    [
        ("NO_SIGNAL", 0, None, "NO_SIGNAL", "XIC empty"),
        ("WINDOW_TOO_SHORT", 7, None, "WINDOW_TOO_SHORT", "Only 7 scans"),
        ("PEAK_NOT_FOUND", 15, 25.0, "PEAK_NOT_FOUND", "prominence"),
    ],
)
def test_peak_diagnostic_owner_preserves_status_and_reason(
    tmp_path: Path,
    peak_status: str,
    n_points: int,
    max_smoothed: float | None,
    issue: str,
    reason_part: str,
) -> None:
    from xic_extractor.output.messages import build_diagnostic_records

    config = _config(tmp_path)
    target = _target("NoNL", neutral_loss_da=None)
    result = ExtractionResult(
        peak_result=_failed_peak(
            peak_status,
            n_points=n_points,
            max_smoothed=max_smoothed,
        ),
        nl=None,
    )
    diagnostics = build_diagnostic_records("SampleA", target, result, config)
    assert diagnostics[0].issue == issue
    assert diagnostics[0].target_label == "NoNL"
    assert reason_part in diagnostics[0].reason


@pytest.mark.parametrize(
    ("nl_result", "issue", "reason_part"),
    [
        (
            NLResult("NL_FAIL", 200.0, None, 10, 1, 3),
            "NL_FAIL",
            "subcause=no_target_product_support",
        ),
        (
            NLResult("NL_FAIL", None, None, 10, 1, 3),
            "NL_FAIL",
            "target-peak CID-NL support for 116.047 Da was not established",
        ),
        (
            NLResult("NO_MS2", None, None, 42, 2, 0),
            "NO_MS2",
            "no candidate-aligned MS2 trigger",
        ),
    ],
)
def test_run_writes_neutral_loss_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    nl_result: NLResult,
    issue: str,
    reason_part: str,
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("WithNL")]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1000.0, 2000.0)]),
    )
    _set_ms2_fixtures([nl_result])

    output = _run(config, targets)

    assert _result_row(output, targets, "WithNL", 0)["NL"] == nl_result.to_token()
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert diagnostics[0]["Issue"] == issue
    assert diagnostics[0]["Target"] == "WithNL"
    assert reason_part in diagnostics[0]["Reason"]


def test_istd_no_ms2_keeps_ms1_peak_and_writes_confidence_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(peak_centers=[9.05]),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(9.05, 1200.0, 3400.25)]),
    )
    _set_ms2_fixtures([NLResult("NO_MS2", None, None, 12, 0, 0)])

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    expected = _formatted_peak_columns("ISTD", result)
    assert _result_row(output, targets, "ISTD", 0)["RT"] == expected["ISTD_RT"]
    assert _result_row(output, targets, "ISTD", 0)["Int"] == expected["ISTD_Int"]
    assert _result_row(output, targets, "ISTD", 0)["Area"] == expected["ISTD_Area"]
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert any(
        record["Target"] == "ISTD" and record["Issue"] == "NO_MS2"
        for record in diagnostics
    )
    assert any(
        record["Target"] == "ISTD"
        and record["Issue"] == "ISTD_CONFIDENCE"
        and "confidence=MEDIUM" in record["Reason"]
        and "flags=NO_MS2" in record["Reason"]
        and "MS1 peak retained" in record["Reason"]
        for record in diagnostics
    )


def test_run_reports_progress_and_stops_between_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "A.raw").write_text("", encoding="utf-8")
    (config.data_dir / "B.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None)]
    progress_calls: list[tuple[int, int, str]] = []
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1000.0, 2000.0)]),
    )

    output = _run(
        config,
        targets,
        progress_callback=lambda current, total, filename: progress_calls.append(
            (current, total, filename)
        ),
        should_stop=lambda: bool(progress_calls),
    )

    assert progress_calls == [(1, 2, "A.raw")]
    assert [file_result.sample_name for file_result in output.file_results] == ["A"]


def test_paired_analyte_uses_strict_anchor_peak_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=12.7, rt_max=14.7),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]
    strict_flags: list[bool] = []

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(peak_centers=[13.70, 13.75]),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _capturing_peak_sequence(
            [_ok_peak(13.70, 2000.0, 3000.0), _ok_peak(13.75, 500.0, 800.0)],
            strict_flags,
        ),
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("OK", 1.0, 13.75, 1, 0, 1),
        ]
    )

    _run(config, targets)

    assert strict_flags == [False, True]


def test_unpaired_isomers_use_expected_rt_to_select_their_own_supported_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        replace(_target("Early", rt_min=4.0, rt_max=6.0), expected_rt_min=4.9),
        replace(_target("Late", rt_min=24.0, rt_max=26.0), expected_rt_min=25.3),
    ]

    class Raw(_FakeRaw):
        def acquisition_rt_bounds(self):
            return 0.0, 60.0

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.arange(rt_min, rt_max + 0.005, 0.01)
            sigma = np.where(rt < 5.2, 0.08, 0.23)
            early = 2500.0 * np.exp(-0.5 * ((rt - 5.2) / sigma) ** 2)
            late = 10000.0 * np.exp(-0.5 * ((rt - 25.35) / 0.08) ** 2)
            return rt, 10.0 + early + late

        def iter_ms2_scans(self, rt_min, rt_max):
            # Both isomers have independent NL support; the later peak has
            # denser DDA acquisition, which must not erase their RT identity.
            scan_rts = (5.2, 25.29, 25.32, 25.35, 25.38, 25.41)
            for number, scan_rt in enumerate(scan_rts, start=1):
                scan = Ms2Scan(
                    scan_number=number,
                    rt=scan_rt,
                    precursor_mz=258.1085,
                    masses=np.array([258.1085 - 116.0474]),
                    intensities=np.array([1000.0]),
                    base_peak=1000.0,
                )
                yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=number)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: Raw())
    output = _run(config, targets)
    results = output.file_results[0].results

    assert results["Early"].reported_rt == pytest.approx(5.2, abs=0.1)
    assert results["Late"].reported_rt == pytest.approx(25.35, abs=0.1)
    for result in results.values():
        assert result.reported_peak_area is not None
        assert result.reported_peak_area > 0
        assert result.targeted_product_projection is not None
        assert result.targeted_product_projection.counted_detection is True


def test_istd_far_peak_uses_expected_rt_as_identity_prior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    anchor_reference_rts: list[float | None] = []
    preferred_rts: list[float | None] = []

    def _fake_find_nl_anchor_rt(*_args, **kwargs) -> float:
        anchor_reference_rts.append(kwargs["reference_rt"])
        return 7.08

    def _fake_select_peak_and_area(
        candidates_result: PeakCandidatesResult,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: object | None = None,
        istd_confidence_note: str | None = None,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        preferred_rts.append(preferred_rt)
        return _with_runtime_typed_scores(
            _ok_peak(7.08, 1200.0, 3400.25),
            candidates_result=candidates_result,
            preferred_rt=preferred_rt,
            evidence_role=_typed_kwarg(_kwargs, "evidence_role"),
            istd_pair=_typed_kwarg(_kwargs, "istd_pair"),
            paired_istd_anchor_rt=_typed_float_kwarg(
                _kwargs,
                "paired_istd_anchor_rt",
            ),
        )

    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _fake_select_peak_and_area,
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 7.08, 1, 0, 1)])

    _run(config, targets)

    assert anchor_reference_rts == []
    assert _dedupe_consecutive(preferred_rts) == [9.0]


def test_istd_near_peak_uses_expected_rt_as_identity_prior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    anchor_reference_rts: list[float | None] = []
    preferred_rts: list[float | None] = []

    def _fake_find_nl_anchor_rt(*_args, **kwargs) -> float:
        anchor_reference_rts.append(kwargs["reference_rt"])
        return 8.55

    def _fake_select_peak_and_area(
        candidates_result: PeakCandidatesResult,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: object | None = None,
        istd_confidence_note: str | None = None,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        preferred_rts.append(preferred_rt)
        return _with_runtime_typed_scores(
            _ok_peak(8.55, 1200.0, 3400.25),
            candidates_result=candidates_result,
            preferred_rt=preferred_rt,
            evidence_role=_typed_kwarg(_kwargs, "evidence_role"),
            istd_pair=_typed_kwarg(_kwargs, "istd_pair"),
            paired_istd_anchor_rt=_typed_float_kwarg(
                _kwargs,
                "paired_istd_anchor_rt",
            ),
        )

    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _fake_select_peak_and_area,
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 8.55, 1, 0, 1)])

    _run(config, targets)

    assert anchor_reference_rts == []
    assert _dedupe_consecutive(preferred_rts) == [9.0]


def test_istd_peak_not_found_does_not_reacquire_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    raw = _RecordingRaw()

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [_failed_peak("PEAK_NOT_FOUND", n_points=15, max_smoothed=1234.0)]
        ),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.0, 1, 0, 1)])

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    assert result.peak_result.status == "PEAK_NOT_FOUND"
    assert result.peak is None
    assert raw.windows == [(0.0, 20.0)]


def test_istd_no_signal_does_not_reacquire_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]

    class NoSignalRaw(_RecordingRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt, _intensity = super().extract_xic(mz, rt_min, rt_max, ppm_tol)
            return rt, np.zeros(len(rt))

    raw = NoSignalRaw()

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_failed_peak("NO_SIGNAL", n_points=0, max_smoothed=None)]),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.0, 1, 0, 1)])

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    assert result.peak_result.status == "NO_SIGNAL"
    assert result.peak is None
    assert raw.windows == [(0.0, 20.0)]


def test_istd_weak_peak_keeps_single_complete_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    raw = _RecordingRaw(peak_centers=[9.05], intensity_scale=0.02)

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(
                    9.05,
                    100.0,
                    200.0,
                    quality_flags=("poor_edge_recovery", "low_trace_continuity"),
                ),
            ]
        ),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.0, 1, 0, 1)])

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    assert result.peak_result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.05, abs=0.02)
    known_area = 100.0 * 0.08 * np.sqrt(2 * np.pi) * 60
    assert result.reported_peak_area == pytest.approx(known_area, rel=0.04)
    assert raw.windows == [(0.0, 20.0)]


def test_istd_low_confidence_peak_keeps_single_complete_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    raw = _RecordingRaw(peak_centers=[9.05], intensity_scale=0.02)

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(
                    9.05,
                    100.0,
                    200.0,
                    confidence="VERY_LOW",
                    reason="decision: review only, not counted",
                ),
            ]
        ),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.0, 1, 0, 1)])

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    assert result.peak_result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.05, abs=0.02)
    known_area = 100.0 * 0.08 * np.sqrt(2 * np.pi) * 60
    assert result.reported_peak_area == pytest.approx(known_area, rel=0.04)
    assert raw.windows == [(0.0, 20.0)]


def test_paired_analyte_with_nl_remains_counted_despite_target_anchor_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=13.4, rt_max=13.8),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]

    raw = _RecordingRaw(peak_centers=[13.70, 13.06])
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [_ok_peak(13.70, 2000.0, 3000.0), _ok_peak(13.06, 5000.0, 8000.0)]
        ),
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("OK", 1.0, 13.06, 1, 0, 1),
        ]
    )

    output = _run(config, targets)

    expected_peak = _formatted_peak_columns(
        "Analyte", output.file_results[0].results["Analyte"]
    )
    # The fixture supplies a symmetric Gaussian on a fixed acquired
    # grid. Its nearest sample to the known center is the independent apex
    # oracle; the mocked ranking result does not own physical peak facts.
    assert len(raw.windows) == 2
    sampled_rt = np.arange(raw.windows[1][0], raw.windows[1][1] + 0.0025, 0.0025)
    expected_apex_rt = sampled_rt[np.argmin(np.abs(sampled_rt - 13.06))]
    assert _result_row(output, targets, "Analyte", 0)["RT"] == f"{expected_apex_rt:.4f}"
    assert (
        _result_row(output, targets, "Analyte", 0)["Int"]
        == expected_peak["Analyte_Int"]
    )
    assert (
        _result_row(output, targets, "Analyte", 0)["Area"]
        == expected_peak["Analyte_Area"]
    )
    long_rows = _run_output_to_long_rows(output, targets)
    analyte_row = next(row for row in long_rows if row["Target"] == "Analyte")
    assert analyte_row["Confidence"] == "LOW"
    assert analyte_row["Product State"] == "detected_flagged"
    assert "targeted_reference_displacement" in analyte_row["Projection Review Reasons"]
    assert analyte_row["Counted Detection"] == "TRUE"
    assert analyte_row["Projection Conflict Reasons"] == ""
    assert output.file_results[0].results["Analyte"].confidence == "LOW"
    # Expected RT and the paired offset are identity priors, not DDA anchor crops.
    assert all(record.issue != "NL_ANCHOR_FALLBACK" for record in output.diagnostics)
    assert output.file_results[0].results["Analyte"].candidate_ms2_evidence.nl_match


def test_paired_analyte_accepts_peak_close_to_target_anchor_even_if_farther_from_istd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=12.7, rt_max=14.7),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(peak_centers=[13.70, 13.95]),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [_ok_peak(13.70, 2000.0, 3000.0), _ok_peak(13.95, 5000.0, 8000.0)]
        ),
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("OK", 1.0, 13.95, 1, 0, 1),
        ]
    )

    output = _run(config, targets)

    result = output.file_results[0].results["Analyte"]
    expected = _formatted_peak_columns("Analyte", result)
    assert _result_row(output, targets, "Analyte", 0)["RT"] == expected["Analyte_RT"]
    assert float(_result_row(output, targets, "Analyte", 0)["RT"]) == pytest.approx(
        13.95, abs=0.03
    )
    assert (
        _result_row(output, targets, "Analyte", 0)["Area"] == expected["Analyte_Area"]
    )
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert not any(
        record["Target"] == "Analyte" and record["Issue"] == "ANCHOR_RT_MISMATCH"
        for record in diagnostics
    )


def test_paired_analyte_keeps_target_nl_anchor_far_from_istd_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=14.1, rt_max=16.1),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]
    raw = _RecordingRaw(peak_centers=[13.70, 15.12])
    preferred_rts: list[float | None] = []

    def _fake_select_peak_and_area(
        candidates_result: PeakCandidatesResult,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: object | None = None,
        istd_confidence_note: str | None = None,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        preferred_rts.append(preferred_rt)
        peak_rt = 13.70 if len(preferred_rts) == 1 else 15.12
        return _with_runtime_typed_scores(
            _ok_peak(peak_rt, 5000.0, 8000.0),
            candidates_result=candidates_result,
            preferred_rt=preferred_rt,
            evidence_role=_typed_kwarg(_kwargs, "evidence_role"),
            istd_pair=_typed_kwarg(_kwargs, "istd_pair"),
            paired_istd_anchor_rt=_typed_float_kwarg(
                _kwargs,
                "paired_istd_anchor_rt",
            ),
        )

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _fake_select_peak_and_area,
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("NL_FAIL", None, None, 1, 0, 1),
        ]
    )

    _run(config, targets)

    assert _dedupe_consecutive(preferred_rts) == pytest.approx([13.70, 15.10])
    assert raw.windows[1] == (0.0, 20.0)


def test_paired_analyte_istd_rt_fallback_does_not_force_counted_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=12.7, rt_max=14.7),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]

    raw = _RecordingRaw(peak_centers=[13.70, 13.72])
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(13.70, 2000.0, 3000.0),
                _ok_peak(13.72, 5000.0, 8000.0, nl_match=False),
            ]
        ),
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("NL_FAIL", None, None, 1, 0, 1),
        ]
    )

    output = _run(config, targets)

    long_rows = _run_output_to_long_rows(output, targets)
    analyte_row = next(row for row in long_rows if row["Target"] == "Analyte")
    expected_peak = _formatted_peak_columns(
        "Analyte", output.file_results[0].results["Analyte"]
    )
    assert len(raw.windows) == 2
    sampled_rt = np.arange(raw.windows[1][0], raw.windows[1][1] + 0.0025, 0.0025)
    expected_apex_rt = sampled_rt[np.argmin(np.abs(sampled_rt - 13.72))]
    assert analyte_row["RT"] == f"{expected_apex_rt:.4f}"
    assert analyte_row["Area"] == expected_peak["Analyte_Area"]
    assert analyte_row["NL"] == "NL_FAIL"
    assert analyte_row["Product State"] == "not_counted"
    assert analyte_row["Counted Detection"] == "FALSE"
    assert (
        "cid_nl_method_not_supported" in analyte_row["Projection Not Counted Reasons"]
    )


def test_paired_analyte_fallback_preserves_not_counted_peak_in_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=12.7, rt_max=14.7),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]

    raw = _RecordingRaw(peak_centers=[13.70, 14.31])
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(13.70, 2000.0, 3000.0),
                _ok_peak(14.31, 5000.0, 8000.0, nl_match=False),
            ]
        ),
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("NL_FAIL", None, None, 1, 0, 1),
        ]
    )

    output = _run(config, targets)

    expected_peak = _formatted_peak_columns(
        "Analyte", output.file_results[0].results["Analyte"]
    )
    assert len(raw.windows) == 2
    sampled_rt = np.arange(raw.windows[1][0], raw.windows[1][1] + 0.0025, 0.0025)
    expected_apex_rt = sampled_rt[np.argmin(np.abs(sampled_rt - 14.31))]
    assert _result_row(output, targets, "Analyte", 0)["RT"] == f"{expected_apex_rt:.4f}"
    assert (
        _result_row(output, targets, "Analyte", 0)["Area"]
        == expected_peak["Analyte_Area"]
    )
    long_rows = _run_output_to_long_rows(output, targets)
    analyte_row = next(row for row in long_rows if row["Target"] == "Analyte")
    assert analyte_row["RT"] == expected_peak["Analyte_RT"]
    assert analyte_row["Area"] == expected_peak["Analyte_Area"]
    assert analyte_row["Confidence"] == "VERY_LOW"
    assert analyte_row["Product State"] == "not_counted"
    assert analyte_row["Counted Detection"] == "FALSE"
    assert (
        "cid_nl_method_not_supported" in analyte_row["Projection Not Counted Reasons"]
    )
    assert output.file_results[0].results["Analyte"].confidence == "LOW"
    selected_peak = output.file_results[0].results["Analyte"].peak_result.peak
    assert selected_peak is not None
    istd_rt = output.file_results[0].results["ISTD"].reported_rt
    assert istd_rt is not None
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert any(
        record["Target"] == "Analyte"
        and record["Issue"] == "ANCHOR_RT_MISMATCH"
        and f"Paired analyte peak RT {selected_peak.rt:.3f}" in record["Reason"]
        and f"ISTD anchor at {istd_rt:.3f}" in record["Reason"]
        and "allowed ±0.50 min" in record["Reason"]
        for record in diagnostics
    )


def _run(config: ExtractionConfig, targets: list[Target], **kwargs):
    from xic_extractor.extractor import acquire

    return acquire(config, targets, **kwargs)


def _result_row(output, targets, label, sample_index=0):
    rows = _run_output_to_long_rows(output, targets)
    sample = output.file_results[sample_index].sample_name
    return next(
        row for row in rows if row["SampleName"] == sample and row["Target"] == label
    )


def _config(tmp_path: Path) -> ExtractionConfig:
    data_dir = tmp_path / "raw"
    output_dir = tmp_path / "output"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir()
    output_dir.mkdir()
    dll_dir.mkdir()
    return ExtractionConfig(
        data_dir=data_dir,
        dll_dir=dll_dir,
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target(
    label: str,
    *,
    neutral_loss_da: float | None = 116.0474,
    is_istd: bool = False,
    istd_pair: str = "",
    rt_min: float = 8.0,
    rt_max: float = 10.0,
) -> Target:
    return Target(
        label=label,
        mz=261.1085 if is_istd else 258.1085,
        rt_min=rt_min,
        rt_max=rt_max,
        ppm_tol=20.0,
        neutral_loss_da=neutral_loss_da,
        cid_nl_tag="dR",
        nl_ppm_warn=20.0 if neutral_loss_da is not None else None,
        nl_ppm_max=50.0 if neutral_loss_da is not None else None,
        is_istd=is_istd,
        istd_pair=istd_pair,
    )


def _ok_peak(
    rt: float,
    intensity: float,
    area: float,
    *,
    confidence: str | None = None,
    reason: str | None = None,
    severities: tuple[tuple[int, str], ...] = (),
    quality_flags: tuple[str, ...] = (),
    selection_apex_index: int = 7,
    neutral_loss_required: bool = True,
    ms2_present: bool = True,
    nl_match: bool = True,
) -> PeakDetectionResult:
    peak = PeakResult(
        rt=rt,
        intensity=intensity,
        intensity_smoothed=intensity,
        area=area,
        peak_start=rt - 0.5,
        peak_end=rt + 0.5,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=intensity,
        selection_apex_index=selection_apex_index,
        raw_apex_rt=rt,
        raw_apex_intensity=intensity,
        raw_apex_index=selection_apex_index,
        prominence=intensity * 0.5,
        quality_flags=quality_flags,
        region_scan_count=15,
        region_duration_min=1.0,
        region_edge_ratio=1.5,
    )
    return PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=15,
        max_smoothed=intensity,
        n_prominent_peaks=1,
        candidates=(candidate,),
        confidence=confidence,
        reason=reason,
        severities=severities,
        candidate_scores=(
            _typed_candidate_score(
                candidate,
                neutral_loss_required=neutral_loss_required,
                ms2_present=ms2_present,
                nl_match=nl_match,
            ),
        ),
    )


def _failed_peak(
    status: str, *, n_points: int, max_smoothed: float | None
) -> PeakDetectionResult:
    return PeakDetectionResult(
        status=status,
        peak=None,
        n_points=n_points,
        max_smoothed=max_smoothed,
        n_prominent_peaks=0,
    )


def _peak_sequence(results: list[PeakDetectionResult]):
    pending = list(results)
    by_candidates: dict[PeakCandidatesResult, PeakDetectionResult] = {}

    def _fake_select_peak_and_area(
        candidates_result: PeakCandidatesResult,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: object | None = None,
        istd_confidence_note: str | None = None,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        cached = by_candidates.get(candidates_result)
        if cached is not None:
            return cached
        result = _with_runtime_typed_scores(
            pending.pop(0),
            candidates_result=candidates_result,
            preferred_rt=preferred_rt,
            evidence_role=_typed_kwarg(_kwargs, "evidence_role"),
            istd_pair=_typed_kwarg(_kwargs, "istd_pair"),
            paired_istd_anchor_rt=_typed_float_kwarg(
                _kwargs,
                "paired_istd_anchor_rt",
            ),
        )
        by_candidates[candidates_result] = result
        return result

    return _fake_select_peak_and_area


def _capturing_peak_sequence(
    results: list[PeakDetectionResult],
    strict_flags: list[bool],
):
    pending = list(results)
    by_candidates: dict[PeakCandidatesResult, PeakDetectionResult] = {}

    def _fake_select_peak_and_area(
        candidates_result: PeakCandidatesResult,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: object | None = None,
        istd_confidence_note: str | None = None,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        cached = by_candidates.get(candidates_result)
        if cached is not None:
            return cached
        strict_flags.append(strict_preferred_rt)
        result = _with_runtime_typed_scores(
            pending.pop(0),
            candidates_result=candidates_result,
            preferred_rt=preferred_rt,
            evidence_role=_typed_kwarg(_kwargs, "evidence_role"),
            istd_pair=_typed_kwarg(_kwargs, "istd_pair"),
            paired_istd_anchor_rt=_typed_float_kwarg(
                _kwargs,
                "paired_istd_anchor_rt",
            ),
        )
        by_candidates[candidates_result] = result
        return result

    return _fake_select_peak_and_area


def _dedupe_consecutive(values: list[float | None]) -> list[float | None]:
    deduped: list[float | None] = []
    for value in values:
        if not deduped or value != deduped[-1]:
            deduped.append(value)
    return deduped


def _set_ms2_fixtures(results: list[NLResult]) -> None:
    global _active_nl_fixture_results
    _active_nl_fixture_results = list(results)


def _typed_kwarg(kwargs: dict[str, object], name: str) -> str | None:
    value = kwargs.get(name)
    return value if isinstance(value, str) else None


def _typed_float_kwarg(kwargs: dict[str, object], name: str) -> float | None:
    value = kwargs.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _open_raw_factory(
    *,
    errors: dict[str, Exception] | None = None,
    peak_centers: list[float] | None = None,
    peak_centers_by_sample: dict[str, list[float]] | None = None,
):
    error_by_name = errors or {}
    sample_centers = peak_centers_by_sample or {}

    def _fake_open_raw(path: Path, dll_dir: Path, **_options):
        if path.name in error_by_name:
            raise error_by_name[path.name]
        centers = sample_centers.get(path.name, peak_centers)
        return _FakeRaw(peak_centers=centers)

    return _fake_open_raw


class _FakeRaw:
    def acquisition_rt_bounds(self):
        return 0.0, 20.0

    def __init__(self, *, peak_centers: list[float] | None = None) -> None:
        self._peak_centers = list(peak_centers or [])
        self._scan_rt: float | None = (
            self._peak_centers[0] if self._peak_centers else 10.0
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_xic(
        self, mz: float, rt_min: float, rt_max: float, ppm_tol: float
    ) -> tuple[np.ndarray, np.ndarray]:
        center = (
            self._peak_centers.pop(0) if self._peak_centers else (rt_min + rt_max) / 2
        )
        self._scan_rt = center
        rt = np.arange(rt_min, rt_max + 0.0025, 0.0025)
        intensity = 10.0 + 5000.0 * np.exp(-0.5 * ((rt - center) / 0.08) ** 2)
        return rt, intensity

    def iter_ms2_scans(self, rt_min: float, rt_max: float):
        # Acquisition is immutable and shared across all targets, not generated
        # by whichever target happened to query NL most recently.
        scans = _active_nl_fixture_results
        for index, result in enumerate(scans):
            scan_rt = (
                result.best_scan_rt
                if result.best_scan_rt is not None
                else self._scan_rt
            )
            if (
                result.status == "NO_MS2"
                or scan_rt is None
                or not rt_min <= scan_rt <= rt_max
            ):
                continue
            precursors = (
                (258.1085, 261.1085)
                if len(scans) == 1
                else ((261.1085,) if index == 0 else (258.1085,))
            )
            for precursor_mz in precursors:
                product_mz = (
                    precursor_mz - 116.0474 * (1 + result.best_ppm / 1_000_000)
                    if result.best_ppm is not None
                    else 300.0
                )
                scan_number = index * 2 + (1 if precursor_mz == 258.1085 else 2)
                scan = Ms2Scan(
                    scan_number=scan_number,
                    rt=scan_rt,
                    precursor_mz=precursor_mz,
                    masses=np.array([product_mz]),
                    intensities=np.array([1000.0]),
                    base_peak=1000.0,
                )
                yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=scan_number)


class _RecordingRaw(_FakeRaw):
    def __init__(
        self, *, peak_centers: list[float] | None = None, intensity_scale: float = 1.0
    ) -> None:
        super().__init__(peak_centers=peak_centers)
        self.windows: list[tuple[float, float]] = []
        self.intensity_scale = intensity_scale

    def extract_xic(
        self, mz: float, rt_min: float, rt_max: float, ppm_tol: float
    ) -> tuple[np.ndarray, np.ndarray]:
        self.windows.append((rt_min, rt_max))
        rt, intensity = super().extract_xic(mz, rt_min, rt_max, ppm_tol)
        return rt, intensity * self.intensity_scale


class _WidePhysicalContextRaw(_FakeRaw):
    def __init__(self) -> None:
        super().__init__()
        self._scan_rt = 9.6
        self.windows: list[tuple[float, float]] = []

    def extract_xic(
        self, mz: float, rt_min: float, rt_max: float, ppm_tol: float
    ) -> tuple[np.ndarray, np.ndarray]:
        self.windows.append((rt_min, rt_max))
        rt = np.linspace(rt_min, rt_max, 1201)
        selected = 5000.0 * np.exp(-0.5 * ((rt - 9.6) / 0.55) ** 2)
        outside_selection = 15000.0 * np.exp(-0.5 * ((rt - 11.4) / 0.12) ** 2)
        return rt, 10.0 + selected + outside_selection


def _core_long_row(row: dict[str, str]) -> dict[str, str]:
    core_headers = (
        "SampleName",
        "Group",
        "Target",
        "Role",
        "ISTD Pair",
        "RT",
        "Area",
        "NL",
        "Int",
        "PeakStart",
        "PeakEnd",
        "PeakWidth",
        "Confidence",
        "Reason",
    )
    return {header: row[header] for header in core_headers}


def _formatted_peak_columns(
    label: str,
    result: ExtractionResult,
) -> dict[str, str]:
    evaluation = result.physical_evaluation
    assert evaluation is not None
    facts = evaluation.facts
    integration = evaluation.integration
    assert facts is not None
    assert integration is not None
    return {
        f"{label}_RT": f"{facts.apex_rt_min:.4f}",
        f"{label}_Int": f"{integration.height_raw:.0f}",
        f"{label}_IntSmoothed": f"{facts.height:.0f}",
        f"{label}_Area": f"{facts.area_counts_seconds:.2f}",
        f"{label}_PeakStart": f"{facts.left_rt_min:.4f}",
        f"{label}_PeakEnd": f"{facts.right_rt_min:.4f}",
        f"{label}_PeakWidth": (f"{facts.right_rt_min - facts.left_rt_min:.4f}"),
    }


def _formatted_long_success_row(
    label: str,
    result: ExtractionResult,
    *,
    nl: str,
) -> dict[str, str]:
    peak = _formatted_peak_columns(label, result)
    return {
        "SampleName": "SampleA",
        "Group": "Other",
        "Target": label,
        "Role": "Analyte",
        "ISTD Pair": "",
        "RT": peak[f"{label}_RT"],
        "Area": peak[f"{label}_Area"],
        "NL": nl,
        "Int": peak[f"{label}_Int"],
        "PeakStart": peak[f"{label}_PeakStart"],
        "PeakEnd": peak[f"{label}_PeakEnd"],
        "PeakWidth": peak[f"{label}_PeakWidth"],
        "Confidence": "HIGH",
        "Reason": "MS2 見到預期的中性丟失。" if nl == "OK" else "MS1 峰形合格。",
    }


def _typed_candidate_score(
    candidate: PeakCandidate,
    *,
    neutral_loss_required: bool = True,
    ms2_present: bool = True,
    nl_match: bool = True,
    role: str = "Analyte",
    istd_pair: str = "",
    preferred_rt: float | None = None,
    paired_istd_anchor_rt: float | None = None,
    rt_min: float | None = None,
    rt_max: float | None = None,
) -> PeakCandidateScore:
    rt_array, intensity_array = _synthetic_peak_trace(candidate.selection_apex_rt)
    apex_index = int(np.argmin(np.abs(rt_array - candidate.selection_apex_rt)))
    facts = build_candidate_evidence_facts(
        candidate,
        ScoringContext(
            rt_array=rt_array,
            intensity_array=intensity_array,
            apex_index=apex_index,
            signal_quality=SignalQuality("available", 1.0, 100.0, False),
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=ms2_present,
            nl_match=nl_match,
            rt_prior=(
                preferred_rt
                if preferred_rt is not None
                else candidate.selection_apex_rt
            ),
            rt_prior_sigma=0.1,
            rt_min=rt_min if rt_min is not None else candidate.peak.peak_start,
            rt_max=rt_max if rt_max is not None else candidate.peak.peak_end,
            neutral_loss_required=neutral_loss_required,
            ms2_trace_strength="strong" if ms2_present and nl_match else "none",
            ms2_alignment_source="fixture_candidate",
            trigger_scan_count=3 if ms2_present else 0,
            strict_nl_scan_count=1 if ms2_present and nl_match else 0,
        ),
        role=role,
        istd_pair=istd_pair,
        paired_istd_anchor_rt_min=paired_istd_anchor_rt,
    )
    return PeakCandidateScore(
        candidate=candidate,
        confidence="HIGH",
        reason="decision: accepted by typed fixture",
        raw_score=95,
        support_labels=("legacy_fixture_support",),
        evidence_facts=facts,
    )


def _synthetic_peak_trace(apex_rt: float) -> tuple[np.ndarray, np.ndarray]:
    rt = np.linspace(apex_rt - 0.5, apex_rt + 0.5, 201)
    intensity = 10.0 + 5000.0 * np.exp(-0.5 * ((rt - apex_rt) / 0.08) ** 2)
    return rt, intensity


def _with_runtime_typed_scores(
    result: PeakDetectionResult,
    *,
    candidates_result: PeakCandidatesResult,
    preferred_rt: float | None,
    evidence_role: str | None,
    istd_pair: str | None,
    paired_istd_anchor_rt: float | None,
) -> PeakDetectionResult:
    if result.peak is None:
        return result
    result = _bind_fixture_candidates_to_physical_segments(result, candidates_result)
    rt_min = min(
        candidate.peak.peak_start for candidate in candidates_result.candidates
    )
    rt_max = max(candidate.peak.peak_end for candidate in candidates_result.candidates)
    existing_scores = {score.candidate: score for score in result.candidate_scores}
    scores = tuple(
        _typed_candidate_score(
            candidate,
            neutral_loss_required=_existing_neutral_loss_required(
                existing_scores.get(candidate)
            ),
            ms2_present=_existing_ms2_present(existing_scores.get(candidate)),
            nl_match=_existing_nl_match(existing_scores.get(candidate)),
            role=evidence_role or "Analyte",
            istd_pair=istd_pair or "",
            preferred_rt=preferred_rt,
            paired_istd_anchor_rt=paired_istd_anchor_rt,
            rt_min=rt_min,
            rt_max=rt_max,
        )
        for candidate in result.candidates
    )
    return replace(result, candidate_scores=scores)


def _bind_fixture_candidates_to_physical_segments(
    result: PeakDetectionResult,
    candidates_result: PeakCandidatesResult,
) -> PeakDetectionResult:
    if not result.candidates:
        return result
    physical_candidates = candidates_result.candidates
    if not physical_candidates:
        return result
    replacements: dict[PeakCandidate, PeakCandidate] = {}
    for candidate in result.candidates:
        physical = min(
            physical_candidates,
            key=lambda item: abs(item.selection_apex_rt - candidate.selection_apex_rt),
        )
        # Selection can choose an existing physical candidate, not invent new
        # measurements while copying its segment label.
        replacements[candidate] = physical
    selected = next(
        candidate for candidate in result.candidates if candidate.peak == result.peak
    )
    return replace(
        result,
        peak=replacements[selected].peak,
        candidates=tuple(replacements[candidate] for candidate in result.candidates),
        candidate_scores=tuple(
            replace(score, candidate=replacements.get(score.candidate, score.candidate))
            for score in result.candidate_scores
        ),
    )


def _existing_neutral_loss_required(score: PeakCandidateScore | None) -> bool:
    facts = score.evidence_facts if score is not None else None
    if facts is None:
        return True
    return facts.chemical.neutral_loss_required


def _existing_ms2_present(score: PeakCandidateScore | None) -> bool:
    facts = score.evidence_facts if score is not None else None
    if facts is None:
        return True
    return bool(facts.chemical.ms2_present)


def _existing_nl_match(score: PeakCandidateScore | None) -> bool:
    facts = score.evidence_facts if score is not None else None
    if facts is None:
        return True
    return bool(facts.chemical.nl_match)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
