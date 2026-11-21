"""Per-RAW restoration and independent analytical mass/path oracles."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from xic_extractor.configuration.execution_profile import (
    ExecutionProfile,
    in_process_execution,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    MassRecalibrationResult,
)
from xic_extractor.mass_accuracy import mz_tolerance_da
from xic_extractor.precursor_evidence import supports_direct_nl
from xic_extractor.raw_mass_recalibration import restored_xics
from xic_extractor.raw_reader import RawFileHandle
from xic_extractor.xic_models import XICRequest

TRUE_MZ = 500.0
NEIGHBOUR_MZ = TRUE_MZ * (1 + 20e-6)
LM_BY_SCAN = {1: 1.0, 2: 12.0, 3: 12.5}
RT_BY_SCAN = {1: 10.0, 2: 10.01, 3: 10.02}


def _recorded(true_mz: float, scan: int) -> float:
    return true_mz * (1.0 + (2.0 * (LM_BY_SCAN[scan] - 1.0)) * 1e-6)


class _Vendor:
    def __init__(
        self,
        *,
        trailer_labels=("LM Correction (ppm):", "Number of LM Found:"),
        ms2=None,
    ):
        self.trailer_labels = list(trailer_labels)
        self.ms2 = ms2 or {}
        self.chromatogram_calls = 0
        self.RunHeaderEx = SimpleNamespace(FirstSpectrum=1, LastSpectrum=63)
        self.centroid_reads = 0
        self.trailer_reads = 0
        self.found = {1: 1, 4: 1, 5: 1}
        self.polarity = "Positive"
        self.centroids = {
            1: ([_recorded(TRUE_MZ, 1), _recorded(NEIGHBOUR_MZ, 1)], [100.0, 7.0]),
            3: ([_recorded(TRUE_MZ, 3), _recorded(NEIGHBOUR_MZ, 3)], [300.0, 7.0]),
        }

        for scan in range(4, 64):
            LM_BY_SCAN[scan] = 1.0 if scan in (4, 5) else float(scan % 14)
            RT_BY_SCAN[scan] = 11.0 + scan * 0.01
            self.centroids[scan] = ([], [])

    def ScanNumberFromRetentionTime(self, rt):
        return 1 if rt <= 10.0 else 3

    def RetentionTimeFromScanNumber(self, scan):
        return RT_BY_SCAN[scan]

    def GetFilterForScanNumber(self, scan):
        return SimpleNamespace(
            MSOrder="Ms2" if scan == 2 else "Ms1", Polarity=self.polarity
        )

    def GetCentroidStream(self, scan, _refresh):
        self.centroid_reads += 1
        masses, intensities = self.centroids[scan]
        return SimpleNamespace(Masses=masses, Intensities=intensities)

    def GetChromatogramData(self, *_args):
        self.chromatogram_calls += 1
        raise AssertionError("recalibrated XICs must not use the vendor XIC")

    def GetTrailerExtraHeaderInformation(self):
        return [SimpleNamespace(Label=label) for label in self.trailer_labels]

    def GetTrailerExtraInformation(self, scan):
        self.trailer_reads += 1
        return SimpleNamespace(
            Labels=self.trailer_labels,
            Values=[str(LM_BY_SCAN[scan]), str(self.found.get(scan, 0))],
        )


def _handle(vendor, policy=MassRecalibration.EASY_IC_RESTORE):
    thermo = SimpleNamespace(ms1_order="Ms1", ms2_order="Ms2")
    return RawFileHandle(vendor, thermo, policy)


def test_recalibrated_xic_finds_the_ion_in_every_scan_and_keeps_neighbours_out():
    vendor = _Vendor()
    trace = _handle(vendor).extract_xic_many(
        (XICRequest(mz=TRUE_MZ, rt_min=9.9, rt_max=10.05, ppm_tol=5.0),)
    )[0]

    assert trace.rt.tolist() == [10.0, 10.02]
    assert trace.intensity.tolist() == [100.0, 300.0]
    assert vendor.chromatogram_calls == 0


def test_recalibrated_ms1_centroid_reads_return_true_masses():
    handle = _handle(_Vendor())

    assert handle.ms1_centroid(10.05, 499.99, 500.001)[0] == pytest.approx(TRUE_MZ)
    observations = handle.read_ms1_mass_windows(
        ((3, 499.999, 500.001), (2, 499.0, 501.0))
    )
    assert observations[0].centroid_mzs == pytest.approx((TRUE_MZ,))
    assert observations[0].rt_min == RT_BY_SCAN[3]
    assert observations[1] is None


@pytest.mark.parametrize(
    "policy", [MassRecalibration.NONE, MassRecalibration.EASY_IC_RESTORE]
)
def test_ms1_index_and_xic_share_mass_coordinates_and_centroid_reads(policy):
    vendor = _Vendor()
    original_filter = vendor.GetFilterForScanNumber

    def scan_filter(scan):
        result = original_filter(scan)
        if result.MSOrder == "Ms1":
            result.MSOrder = "Ms"
        return result

    vendor.GetFilterForScanNumber = scan_filter
    handle = RawFileHandle(
        vendor, SimpleNamespace(ms1_order="Ms", ms2_order="Ms2"), policy
    )
    index = handle.read_ms1_scan_index(centroid=True)
    target = next(scan for scan in index if scan.scan_number == 3)
    expected = (
        TRUE_MZ
        if policy is MassRecalibration.EASY_IC_RESTORE
        else _recorded(TRUE_MZ, 3)
    )
    assert target.masses[0] == pytest.approx(expected)
    assert target.intensities.tolist() == [300, 7]
    if policy is MassRecalibration.EASY_IC_RESTORE:
        reads = vendor.centroid_reads
        trace = handle.extract_xic_many((XICRequest(TRUE_MZ, 9.9, 10.05, 5),))[0]
        assert trace.intensity.tolist() == [100, 300]
        assert vendor.centroid_reads == reads


@pytest.mark.parametrize("outcome", ["applied", "unavailable"])
def test_reopened_handle_reuses_probe_and_initializes_its_trailer(outcome):
    vendor = _Vendor()
    first = _handle(vendor)
    result = replace(first.mass_recalibration_result, outcome=outcome)
    if outcome != "applied":
        result = replace(result, a=None, b=None)
    reopened_vendor = _Vendor()
    reopened_vendor.trailer_labels.insert(0, "Other trailer:")
    reopened_vendor.GetTrailerExtraInformation = lambda scan: SimpleNamespace(
        Values=["999", str(LM_BY_SCAN[scan]), "0"]
    )
    reopened = _handle(reopened_vendor)
    reopened.use_mass_recalibration_result(result)
    assert reopened.mass_recalibration_result == result
    assert reopened_vendor.centroid_reads == 0
    if outcome == "applied":
        observations = reopened.read_ms1_mass_windows(((3, 499.999, 500.001),))
        assert observations[0].centroid_mzs == pytest.approx((TRUE_MZ,))
        trace = reopened.extract_xic_many(
            (XICRequest(mz=TRUE_MZ, rt_min=9.9, rt_max=10.05, ppm_tol=5.0),)
        )[0]
        assert trace.intensity.tolist() == [100.0, 300.0]


def test_batch_prepares_each_raw_once_and_preserves_sample_attribution(
    tmp_path, monkeypatch
):
    from xic_extractor import raw_mass_recalibration_batch
    from xic_extractor.processing_identity import build_acquisition_source_identity

    inputs = []
    opened = []
    closed = []
    for stem in ("applied", "unavailable"):
        path = tmp_path / f"{stem}.raw"
        path.write_bytes(stem.encode())
        source = build_acquisition_source_identity(
            path,
            acquisition_format="thermo_raw",
            observation_namespace="thermo_raw_file_reader:Device.MS:1",
        )
        inputs.append((path, source))

    def open_vendor(
        path,
        dll_dir,
        *,
        mass_recalibration,
        expected_acquisition_source,
        _expected_path_identity,
    ):
        assert expected_acquisition_source == dict(inputs)[path]
        opened.append(path.stem)
        vendor = _Vendor()
        if path.stem == "unavailable":
            vendor.found = {}
        vendor.Dispose = lambda: closed.append(path.stem)
        return _handle(vendor, mass_recalibration)

    monkeypatch.setattr(raw_mass_recalibration_batch, "open_raw", open_vendor)
    prepared = raw_mass_recalibration_batch.prepare_batch_mass_recalibration(
        inputs,
        tmp_path,
        MassRecalibration.EASY_IC_RESTORE,
        execution=ExecutionProfile(alignment_workers=2, backend="thread"),
    )
    assert sorted(opened) == sorted(closed) == ["applied", "unavailable"]
    for path, source in inputs:
        assert prepared[path.stem].acquisition.source == source
        assert prepared[path.stem].result.outcome == path.stem
    assert prepared["applied"].result.baseline_lm == 1.0
    assert prepared["applied"].result.n_anchor == 3
    assert (
        raw_mass_recalibration_batch.prepare_batch_mass_recalibration(
            inputs,
            tmp_path,
            MassRecalibration.NONE,
            execution=in_process_execution(),
        )
        == {}
    )
    assert len(opened) == 2


@pytest.mark.parametrize("outcome", ("applied", "none", "unavailable"))
@pytest.mark.parametrize("isotope_number,charge", ((0, 1), (1, 1), (2, 1), (1, 2)))
def test_ms2_fragments_use_their_scan_and_precursors_use_the_parent_ms1(
    outcome, isotope_number, charge, monkeypatch
):
    monkeypatch.setitem(LM_BY_SCAN, 1, 12.5)
    labels = (
        "LM Correction (ppm):",
        "Monoisotopic M/Z:",
        "Master Scan Number:",
        "Charge State:",
        "Number of LM Found:",
    )
    true_precursor, true_product = 500.0, 383.9526
    selected_true = true_precursor + isotope_number * 1.0033548 / charge
    selected_reading = round(_recorded(selected_true, 1), 4)
    parent_reading = round(_recorded(true_precursor, 1), 4)
    product_reading = _recorded(true_product, 2)

    def run(filter_precursor):
        vendor = _Vendor(trailer_labels=labels)
        vendor.GetFilterForScanNumber = lambda scan: SimpleNamespace(
            MSOrder="Ms2" if scan == 2 else "Ms1",
            Polarity="Positive",
            Filter=SimpleNamespace(
                Reactions=[SimpleNamespace(PrecursorMass=filter_precursor)]
            ),
        )
        vendor.GetSimplifiedScan = lambda scan: SimpleNamespace(
            Masses=[product_reading], Intensities=[50.0]
        )
        values = {
            s: [str(lm), "0", "0", "1", str(vendor.found.get(s, 0))]
            for s, lm in LM_BY_SCAN.items()
        }
        values.update(
            {
                1: [str(LM_BY_SCAN[1]), "0", "0", "1", "1"],
                2: [str(LM_BY_SCAN[2]), str(parent_reading), "1", str(charge), "1"],
            }
        )
        vendor.GetTrailerExtraInformation = lambda scan: SimpleNamespace(
            Labels=list(labels), Values=values[scan]
        )
        vendor.ScanNumberFromRetentionTime = lambda rt: 2
        if outcome == "unavailable":
            for value in values.values():
                value[-1] = "0"
        handle = _handle(
            vendor,
            MassRecalibration.NONE
            if outcome == "none"
            else MassRecalibration.EASY_IC_RESTORE,
        )
        return next(iter(handle.iter_ms2_scan_subset(9.0, 11.0, frozenset({2})))).scan

    scan = run(selected_reading)
    expected_product = true_product if outcome == "applied" else product_reading
    expected_precursor = true_precursor if outcome == "applied" else parent_reading
    assert scan.masses[0] == pytest.approx(expected_product, abs=1e-6)
    assert scan.precursor_metadata.monoisotopic_mz == pytest.approx(
        expected_precursor, abs=1e-4
    )
    assert scan.precursor_mz == pytest.approx(
        selected_true if outcome == "applied" else selected_reading, abs=1e-4
    )

    if charge == 1 and isotope_number >= 1:
        metadata = replace(
            scan.precursor_metadata, isolation_width_da=1.6, isolation_offset_da=0.0
        )
        assert supports_direct_nl(
            "scan_monoisotopic", metadata.monoisotopic_mz, scan.precursor_mz, metadata
        )

    scan = run(selected_true)
    assert scan.precursor_mz == selected_true
    assert scan.precursor_metadata.monoisotopic_mz == pytest.approx(
        expected_precursor, abs=1e-4
    )


@pytest.mark.parametrize(
    "case,reason",
    [
        ("missing_lm", "missing_lock_mass_trailers"),
        ("missing_found", "missing_lock_mass_trailers"),
        ("no_finds", "no_ms1_lock_mass_anchor"),
        ("nonfinite", "no_ms1_lock_mass_anchor"),
        ("negative", "negative_polarity"),
        ("mixed", "unsupported_or_mixed_polarity"),
        ("unknown", "unsupported_or_mixed_polarity"),
    ],
)
def test_unavailable_preserves_vendor_mass_paths(case, reason):
    vendor = _Vendor()
    if case == "missing_lm":
        vendor.trailer_labels = ["Other:", "Number of LM Found:"]
    elif case == "missing_found":
        vendor.trailer_labels = ["LM Correction (ppm):"]
    elif case == "no_finds":
        vendor.found = {}
    elif case == "nonfinite":
        vendor.GetTrailerExtraInformation = lambda scan: SimpleNamespace(
            Values=["nan", "1"]
        )
    elif case == "negative":
        vendor.polarity = "Negative"
    elif case == "unknown":
        vendor.polarity = "Any"
    elif case == "mixed":
        vendor.GetFilterForScanNumber = lambda scan: SimpleNamespace(
            MSOrder="Ms2" if scan == 2 else "Ms1",
            Polarity="Negative" if scan == 2 else "Positive",
        )
    probe = _handle(vendor)
    vendor_handle = _handle(vendor, MassRecalibration.NONE)
    for handle in (probe, vendor_handle):
        handle._thermo = SimpleNamespace(
            ms1_order="Ms1",
            ms2_order="Ms2",
            mass_range_factory=lambda low, high: (low, high),
            trace_settings_factory=lambda *args: SimpleNamespace(),
            mass_range_trace_type="MassRange",
        )
    vendor.GetChromatogramData = lambda *args: SimpleNamespace(
        PositionsArray=[[10.0, 10.02]], IntensitiesArray=[[107.0, 7.0]]
    )
    request = XICRequest(TRUE_MZ, 9.9, 10.05, 5.0)
    assert np.array_equal(
        probe.extract_xic_many((request,))[0].intensity,
        vendor_handle.extract_xic_many((request,))[0].intensity,
    )
    assert probe.ms1_centroid(10.05, 499, 501) == vendor_handle.ms1_centroid(
        10.05, 499, 501
    )
    assert probe.read_ms1_mass_windows(
        ((3, 499, 501),)
    ) == vendor_handle.read_ms1_mass_windows(((3, 499, 501),))
    result = probe.mass_recalibration_result
    assert (result.outcome, result.reason) == (
        "not_applicable" if case == "negative" else "unavailable",
        reason,
    )
    assert result.baseline_lm is None


def test_anchor_uses_only_finite_ms1_finds_and_reads_no_spectra():
    vendor = _Vendor()
    vendor.GetTrailerExtraInformation = lambda scan: SimpleNamespace(
        Values={
            1: ["0.9", "1"],
            2: ["-20", "1"],
            3: ["30", "0"],
            4: ["1.1", "1"],
            5: ["50", "1"],
            6: ["nan", "1"],
            7: ["inf", "1"],
            8: ["1", "nan"],
        }.get(scan, ["1", "0"])
    )
    handle = _handle(vendor)
    result = handle.mass_recalibration_result
    assert result.outcome == "applied"
    assert result.baseline_lm == 1.1
    assert result.n_anchor == 3
    assert vendor.centroid_reads == 0
    assert handle.mass_recalibration_result is result


@pytest.mark.parametrize("mono_present", (True, False))
@pytest.mark.parametrize("measured_precursor", (True, False))
def test_parent_centroid_classifies_selected_mass_without_reliable_mono(
    mono_present, measured_precursor, monkeypatch
):
    monkeypatch.setitem(LM_BY_SCAN, 1, 12.5)
    labels = (
        "LM Correction (ppm):",
        "Monoisotopic M/Z:",
        "Master Scan Number:",
        "Charge State:",
        "Number of LM Found:",
    )
    true_product = TRUE_MZ - 116.0474
    selected = _recorded(TRUE_MZ, 1) if measured_precursor else TRUE_MZ
    mono = _recorded(TRUE_MZ - 1.0033548, 1) - 0.0003
    vendor = _Vendor(trailer_labels=labels)
    vendor.GetFilterForScanNumber = lambda scan: SimpleNamespace(
        MSOrder="Ms2" if scan == 2 else "Ms1",
        Polarity="Positive",
        Filter=SimpleNamespace(Reactions=[SimpleNamespace(PrecursorMass=selected)]),
    )
    vendor.GetSimplifiedScan = lambda scan: SimpleNamespace(
        Masses=[_recorded(true_product, 2)], Intensities=[50.0]
    )
    vendor.GetTrailerExtraInformation = lambda scan: SimpleNamespace(
        Labels=list(labels),
        Values=[
            str(LM_BY_SCAN[scan]),
            str(mono) if mono_present else "0",
            "1",
            "1" if mono_present else "0",
            str(vendor.found.get(scan, 0)),
        ],
    )
    vendor.ScanNumberFromRetentionTime = lambda rt: 2
    handle = _handle(vendor)
    assert handle.mass_recalibration_result.outcome == "applied"
    reads = vendor.centroid_reads
    scans = [
        next(handle.iter_ms2_scan_subset(9.0, 11.0, frozenset({2}))).scan
        for _ in range(2)
    ]

    assert scans[0].precursor_mz == pytest.approx(TRUE_MZ, abs=1e-8)
    assert scans[0].precursor_mz - scans[0].masses[0] == pytest.approx(
        116.0474, abs=1e-8
    )
    assert scans[1].precursor_mz == scans[0].precursor_mz
    if not mono_present:
        assert scans[0].precursor_metadata.monoisotopic_mz is None
    assert vendor.centroid_reads - reads == 1


def test_default_policy_keeps_the_vendor_xic():
    vendor = _Vendor()
    vendor.GetChromatogramData = lambda settings, start, end: SimpleNamespace(
        PositionsArray=[[10.0]], IntensitiesArray=[[1.0]]
    )
    handle = RawFileHandle(
        vendor,
        SimpleNamespace(
            ms1_order="Ms1",
            ms2_order="Ms2",
            mass_range_factory=lambda low, high: (low, high),
            trace_settings_factory=lambda: SimpleNamespace(
                MassRanges=None, Filter=None
            ),
            mass_range_trace_type="MassRange",
        ),
    )

    trace = handle.extract_xic_many(
        (XICRequest(mz=TRUE_MZ, rt_min=9.9, rt_max=10.05, ppm_tol=5.0),)
    )[0]
    assert np.array_equal(trace.intensity, [1.0])
    assert handle.mass_recalibration is MassRecalibration.NONE


@pytest.mark.parametrize("polarity", ("Positive", "Negative"))
def test_ms2_only_shift_is_restored_only_in_positive_mode(polarity):
    vendor = _Vendor()
    vendor.GetFilterForScanNumber = lambda scan: SimpleNamespace(
        MSOrder="Ms2" if scan == 2 else "Ms1",
        Polarity=polarity,
        Filter=SimpleNamespace(Reactions=[SimpleNamespace(PrecursorMass=500.0)]),
    )
    vendor.GetTrailerExtraInformation = lambda scan: SimpleNamespace(
        Labels=vendor.trailer_labels,
        Values=["-6" if scan == 2 else "1", "1"],
    )
    vendor.GetSimplifiedScan = lambda scan: SimpleNamespace(
        Masses=[250.0 * (1 - 14e-6)], Intensities=[50.0]
    )
    vendor.ScanNumberFromRetentionTime = lambda rt: 2
    handle = _handle(vendor)
    event = next(handle.iter_ms2_scan_subset(9, 11, frozenset({2})))
    assert event.parse_error is None
    assert event.scan.masses == pytest.approx(
        [250.0 if polarity == "Positive" else 250.0 * (1 - 14e-6)], abs=1e-9
    )
    assert event.scan.precursor_mz == 500.0
    result = handle.mass_recalibration_result
    if polarity == "Positive":
        assert result.baseline_lm == 1.0
        assert result.outcome == "applied"
    else:
        assert result.baseline_lm is None
        assert (result.outcome, result.reason) == (
            "not_applicable",
            "negative_polarity",
        )
    assert vendor.centroid_reads == 0


def test_saved_legacy_fit_replays_its_formula_without_new_preparation():
    from xic_extractor.configuration.mass_recalibration import (
        deserialize_mass_recalibration_result,
    )

    saved = deserialize_mass_recalibration_result(
        '{"outcome":"applied","reason":null,"a":0.7,"b":1.9,'
        '"n_fit":60,"n_val":60,"val_raw_p95":25.0,'
        '"val_corrected_p95":1.0,"policy":"easy_ic_probe"}'
    )
    vendor = _Vendor()
    vendor.centroids[3] = ([500.0 * (1 + 24.45e-6)], [100.0])
    vendor.found = {}
    handle = _handle(vendor, MassRecalibration.EASY_IC_PROBE)
    handle.use_mass_recalibration_result(saved)
    result = handle.read_ms1_mass_windows(((3, 499.999, 500.001),))[0]
    assert result.centroid_mzs == pytest.approx((500.0,), abs=1e-9)
    assert vendor.trailer_reads == 1
    assert vendor.centroid_reads == 1


def test_legacy_policy_without_saved_fit_does_not_silently_run_a_new_model():
    from xic_extractor.raw_reader_errors import RawReaderError

    with pytest.raises(RawReaderError, match="replay-only"):
        _ = _handle(
            _Vendor(), MassRecalibration.EASY_IC_PROBE
        ).mass_recalibration_result


def test_missing_scan_lm_is_not_inferred_from_baseline():
    from xic_extractor.raw_reader_errors import RawReaderError

    vendor = _Vendor()
    handle = _handle(vendor)
    assert handle.mass_recalibration_result.outcome == "applied"
    vendor.GetTrailerExtraInformation = lambda scan: SimpleNamespace(
        Values=["nan", "0"]
    )
    with pytest.raises(RawReaderError, match="finite 'LM Correction"):
        handle.read_ms1_mass_windows(((3, 499, 501),))


_TOL_100 = mz_tolerance_da(100.0, 10.0)
_GRID_SPECTRA = {
    1: (
        [
            np.nextafter(100.0 - _TOL_100, 0.0),
            100.0 - _TOL_100,
            100.0,
            100.0 + _TOL_100,
            np.nextafter(100.0 + _TOL_100, np.inf),
            200.0,
        ],
        [1000.0, 1.0, 2.0, 4.0, 2000.0, 8.0],
    ),
    2: ([], []),
    4: ([100.0, 100.0005, 150.0], [16.0, 0.0, 32.0]),
    5: ([150.0], [64.0]),
}


class _GridVendor:
    """Unit-scale restoration: every scan records the baseline LM."""

    def __init__(self, gain: float = 1.0):
        self.gain = gain
        self.centroid_reads = 0

    def ScanNumberFromRetentionTime(self, rt):
        return int(rt)

    def RetentionTimeFromScanNumber(self, scan):
        return float(scan)

    def GetFilterForScanNumber(self, scan):
        return SimpleNamespace(MSOrder="Ms2" if scan == 3 else "Ms1")

    def GetCentroidStream(self, scan, _refresh):
        self.centroid_reads += 1
        masses, intensities = _GRID_SPECTRA[scan]
        return SimpleNamespace(
            Masses=masses, Intensities=[value * self.gain for value in intensities]
        )

    def GetChromatogramData(self, *_args):
        raise AssertionError("recalibrated XICs must not use the vendor XIC")

    def GetTrailerExtraHeaderInformation(self):
        return [SimpleNamespace(Label="LM Correction (ppm):")]

    def GetTrailerExtraInformation(self, scan):
        return SimpleNamespace(Values=["1.0"])


def _grid_handle(vendor: _GridVendor | None = None) -> RawFileHandle:
    handle = _handle(vendor or _GridVendor())
    handle.use_mass_recalibration_result(
        MassRecalibrationResult(
            "applied", policy=MassRecalibration.EASY_IC_RESTORE, baseline_lm=1.0
        )
    )
    return handle


def _pairs(trace):
    return list(zip(trace.rt.tolist(), trace.intensity.tolist(), strict=True))


def test_batched_recalibrated_xics_match_hand_sums_in_request_order():
    peak = XICRequest(mz=100.0, rt_min=1.0, rt_max=5.0, ppm_tol=10.0)
    other_mass = XICRequest(mz=150.0, rt_min=1.0, rt_max=5.0, ppm_tol=10.0)
    inner = XICRequest(mz=100.0, rt_min=4.0, rt_max=5.0, ppm_tol=10.0)
    no_ms1 = XICRequest(mz=100.0, rt_min=3.0, rt_max=3.0, ppm_tol=10.0)
    narrow = XICRequest(mz=100.0, rt_min=1.0, rt_max=5.0, ppm_tol=2.0)
    early = XICRequest(mz=100.0, rt_min=1.0, rt_max=2.0, ppm_tol=10.0)
    expected = {
        peak: [(1.0, 7.0), (2.0, 0.0), (4.0, 16.0), (5.0, 0.0)],
        other_mass: [(1.0, 0.0), (2.0, 0.0), (4.0, 32.0), (5.0, 64.0)],
        inner: [(4.0, 16.0), (5.0, 0.0)],
        no_ms1: [],
        narrow: [(1.0, 2.0), (2.0, 0.0), (4.0, 16.0), (5.0, 0.0)],
        early: [(1.0, 7.0), (2.0, 0.0)],
    }
    requests = (inner, peak, no_ms1, narrow, other_mass, early, peak)
    for order in (requests, requests[::-1]):
        vendor = _GridVendor()
        traces = _grid_handle(vendor).extract_xic_many(order)
        assert [_pairs(trace) for trace in traces] == [expected[r] for r in order]
        assert vendor.centroid_reads == 4
    duplicates = [t for r, t in zip(order, traces, strict=True) if r == peak]
    assert duplicates[0].intensity is not duplicates[1].intensity


def test_recalibrated_xics_stay_with_their_own_sample():
    request = XICRequest(mz=150.0, rt_min=4.0, rt_max=5.0, ppm_tol=10.0)
    low, high = (
        _grid_handle(_GridVendor(gain)).extract_xic_many((request,))[0]
        for gain in (1.0, 3.0)
    )
    assert _pairs(low) == [(4.0, 32.0), (5.0, 64.0)]
    assert _pairs(high) == [(4.0, 96.0), (5.0, 192.0)]


def test_restored_xics_equal_one_window_at_a_time_sums_bit_for_bit():
    rng = np.random.default_rng(1173)
    spectra = []
    for scan in range(40):
        masses = np.sort(
            np.concatenate((rng.uniform(99.99, 100.01, 30), rng.uniform(100, 400, 200)))
        )
        spectra.append((10.0 + scan / 60, masses, rng.lognormal(10, 2, masses.size)))
    requests = tuple(
        XICRequest(mz=float(mz), rt_min=0.0, rt_max=1.0, ppm_tol=float(ppm))
        for mz, ppm in zip(
            np.concatenate(([100.0, 100.0], rng.uniform(99.995, 400, 60))),
            np.concatenate(([50.0, 50.0], rng.choice([5.0, 10.0, 30.0], 60))),
            strict=True,
        )
    )

    traces = restored_xics(spectra, requests)

    widest = 0
    for request, trace in zip(requests, traces, strict=True):
        tolerance = mz_tolerance_da(request.mz, request.ppm_tol)
        expected = []
        for _rt, masses, intensities in spectra:
            low = np.searchsorted(masses, request.mz - tolerance, side="left")
            high = np.searchsorted(masses, request.mz + tolerance, side="right")
            widest = max(widest, int(high - low))
            expected.append(float(intensities[low:high].sum()))
        assert trace.rt.tobytes() == np.array([s[0] for s in spectra]).tobytes()
        assert trace.intensity.tobytes() == np.array(expected).tobytes()
    assert widest >= 9


def test_superwindow_crops_shared_recalibrated_reads_back_to_each_request():
    from xic_extractor.raw_superwindow_source import RawSuperWindowSource

    peak = XICRequest(mz=100.0, rt_min=4.0, rt_max=5.0, ppm_tol=10.0)
    context = XICRequest(mz=100.0, rt_min=1.0, rt_max=5.0, ppm_tol=10.0)
    neighbour = XICRequest(mz=150.0, rt_min=2.0, rt_max=4.0, ppm_tol=10.0)
    requests = (peak, neighbour, context)

    batched = RawSuperWindowSource(_grid_handle()).extract_xic_many(requests)

    for request, trace in zip(requests, batched, strict=True):
        alone = _grid_handle().extract_xic_many((request,))[0]
        assert trace.rt.tobytes() == alone.rt.tobytes()
        assert trace.intensity.tobytes() == alone.intensity.tobytes()
