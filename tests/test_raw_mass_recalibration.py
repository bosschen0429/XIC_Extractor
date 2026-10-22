"""Per-RAW probe and independent analytical mass/path oracles."""

from types import SimpleNamespace

import numpy as np
import pytest

from xic_extractor.configuration.mass_recalibration import MassRecalibration
from xic_extractor.raw_reader import RawFileHandle
from xic_extractor.xic_models import XICRequest

TRUE_MZ = 500.0
NEIGHBOUR_MZ = TRUE_MZ * (1 + 20e-6)
# Scan 1 is a normal period, scan 3 a shifted period; scan 2 is an MS2 scan.
LM_BY_SCAN = {1: 1.0, 2: 12.0, 3: 12.5}
RT_BY_SCAN = {1: 10.0, 2: 10.01, 3: 10.02}


def _recorded(true_mz: float, scan: int) -> float:
    return true_mz * (1.0 + (0.7 + 1.9 * LM_BY_SCAN[scan]) * 1e-6)


class _Vendor:
    def __init__(self, *, trailer_labels=("LM Correction (ppm):",), ms2=None):
        self.trailer_labels = list(trailer_labels)
        self.ms2 = ms2 or {}
        self.chromatogram_calls = 0
        self.RunHeaderEx = SimpleNamespace(FirstSpectrum=1, LastSpectrum=63)
        self.centroid_reads = 0
        self.centroids = {
            # The analyte at its recorded mass, plus a neighbour 20 ppm above
            # the true analyte mass that a 5 ppm window must exclude.
            1: ([_recorded(TRUE_MZ, 1), _recorded(NEIGHBOUR_MZ, 1)], [100.0, 7.0]),
            3: ([_recorded(TRUE_MZ, 3), _recorded(NEIGHBOUR_MZ, 3)], [300.0, 7.0]),
        }

        for scan in range(4, 64):
            LM_BY_SCAN[scan] = float(scan % 14)
            RT_BY_SCAN[scan] = 11.0 + scan * 0.01
            self.centroids[scan] = ([], [])
        for scan, (masses, intensities) in self.centroids.items():
            masses.extend([_recorded(371.101233, scan), _recorded(391.284286, scan)])
            intensities.extend([1000.0, 2000.0])

    def ScanNumberFromRetentionTime(self, rt):
        return 1 if rt <= 10.0 else 3

    def RetentionTimeFromScanNumber(self, scan):
        return RT_BY_SCAN[scan]

    def GetFilterForScanNumber(self, scan):
        return SimpleNamespace(MSOrder="Ms2" if scan == 2 else "Ms1")

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
        return SimpleNamespace(
            Labels=self.trailer_labels, Values=[str(LM_BY_SCAN[scan])]
        )


def _handle(vendor, policy=MassRecalibration.EASY_IC_PROBE):
    thermo = SimpleNamespace(ms1_order="Ms1", ms2_order="Ms2")
    return RawFileHandle(vendor, thermo, policy)


def test_recalibrated_xic_finds_the_ion_in_every_scan_and_keeps_neighbours_out():
    vendor = _Vendor()
    trace = _handle(vendor).extract_xic_many(
        (XICRequest(mz=TRUE_MZ, rt_min=9.9, rt_max=10.05, ppm_tol=5.0),)
    )[0]

    # Without recalibration the scan-3 reading sits 25 ppm high, outside 5 ppm.
    assert trace.rt.tolist() == [10.0, 10.02]
    assert trace.intensity.tolist() == [100.0, 300.0]
    assert vendor.chromatogram_calls == 0


def test_recalibrated_ms1_centroid_reads_return_true_masses():
    handle = _handle(_Vendor())

    assert handle.ms1_centroid_mz(10.05, 499.99, 500.001) == pytest.approx(TRUE_MZ)
    observations = handle.read_ms1_mass_windows(
        ((3, 499.999, 500.001), (2, 499.0, 501.0))
    )
    assert observations[0].centroid_mzs == pytest.approx((TRUE_MZ,))
    assert observations[0].rt_min == RT_BY_SCAN[3]
    assert observations[1] is None


@pytest.mark.parametrize("outcome", ("applied", "none", "unavailable", "not_needed"))
def test_ms2_fragments_use_their_scan_and_precursors_use_the_parent_ms1(outcome):
    labels = (
        "LM Correction (ppm):",
        "Monoisotopic M/Z:",
        "Master Scan Number:",
    )
    true_precursor, true_product = 500.0, 383.9526
    parent_reading = round(_recorded(true_precursor, 1), 4)
    product_reading = _recorded(true_product, 2)

    def run(filter_precursor):
        vendor = _Vendor(trailer_labels=labels)
        vendor.GetFilterForScanNumber = lambda scan: SimpleNamespace(
            MSOrder="Ms2" if scan == 2 else "Ms1",
            Filter=SimpleNamespace(
                Reactions=[SimpleNamespace(PrecursorMass=filter_precursor)]
            ),
        )
        vendor.GetSimplifiedScan = lambda scan: SimpleNamespace(
            Masses=[product_reading], Intensities=[50.0]
        )
        values = {s: [str(lm), "0", "0"] for s, lm in LM_BY_SCAN.items()}
        values.update(
            {
                1: [str(LM_BY_SCAN[1]), "0", "0"],
                2: [str(LM_BY_SCAN[2]), str(parent_reading), "1"],
            }
        )
        vendor.GetTrailerExtraInformation = lambda scan: SimpleNamespace(
            Labels=list(labels), Values=values[scan]
        )
        vendor.ScanNumberFromRetentionTime = lambda rt: 2
        if outcome == "unavailable":
            vendor.RunHeaderEx.LastSpectrum = 3
        elif outcome == "not_needed":
            for masses, _ in vendor.centroids.values():
                masses[-1] = 391.284286 * (1 + 2e-6)
        handle = _handle(
            vendor,
            MassRecalibration.NONE
            if outcome == "none"
            else MassRecalibration.EASY_IC_PROBE,
        )
        return next(iter(handle.iter_ms2_scan_subset(9.0, 11.0, frozenset({2})))).scan

    # A data-dependent pick records the parent reading in both header fields.
    scan = run(parent_reading)
    expected_product = true_product if outcome == "applied" else product_reading
    expected_precursor = true_precursor if outcome == "applied" else parent_reading
    assert scan.masses[0] == pytest.approx(expected_product, abs=1e-6)
    assert scan.precursor_metadata.monoisotopic_mz == pytest.approx(
        expected_precursor, abs=1e-4
    )
    assert scan.precursor_mz == pytest.approx(expected_precursor, abs=1e-4)

    # A targeted inclusion mass is a method value, not a parent measurement.
    scan = run(true_precursor)
    assert scan.precursor_mz == true_precursor
    assert scan.precursor_metadata.monoisotopic_mz == pytest.approx(
        expected_precursor, abs=1e-4
    )


@pytest.mark.parametrize(
    ("case", "outcome", "reason"),
    [
        ("clean", "not_needed", None),
        ("few", "unavailable", "insufficient_references"),
        ("missing_lm", "unavailable", "no_correction_signal"),
        ("constant_lm", "unavailable", "no_correction_signal"),
        ("bad_validation", "unavailable", "validation_failed"),
    ],
)
def test_probe_fallback_preserves_every_vendor_mass_path(case, outcome, reason):
    vendor = _Vendor()
    if case == "clean":
        for masses, _ in vendor.centroids.values():
            masses[-1] = 391.284286 * (1 + 2e-6)
    elif case == "few":
        vendor.RunHeaderEx.LastSpectrum = 3
    elif case == "missing_lm":
        vendor.trailer_labels = ["Charge State:"]
    elif case == "constant_lm":
        vendor.GetTrailerExtraInformation = lambda scan: SimpleNamespace(Values=["1"])
    elif case == "bad_validation":
        for masses, _ in vendor.centroids.values():
            masses[-1] = 391.284286 * (1 + 38e-6)
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
    assert probe.ms1_centroid_mz(10.05, 499, 501) == vendor_handle.ms1_centroid_mz(
        10.05, 499, 501
    )
    assert probe.read_ms1_mass_windows(
        ((3, 499, 501),)
    ) == vendor_handle.read_ms1_mass_windows(((3, 499, 501),))
    result = probe.mass_recalibration_result
    assert (result.outcome, result.reason) == (outcome, reason)
    assert result.a is None and result.b is None


def test_probe_fits_this_raw_once_and_exposes_coefficients():
    vendor = _Vendor()
    handle = _handle(vendor)
    result = handle.mass_recalibration_result
    assert result.outcome == "applied"
    assert result.a == pytest.approx(0.7)
    assert result.b == pytest.approx(1.9)
    assert result.n_fit == result.n_val == 62
    reads = vendor.centroid_reads
    assert handle.mass_recalibration_result is result
    assert vendor.centroid_reads == reads


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
