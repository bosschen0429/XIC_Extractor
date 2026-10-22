"""Per-scan EASY-IC m/z recalibration at the RAW reader boundary.

Expected masses come from the policy's analytical model: a scan whose vendor
trailer reports an internal correction of ``c`` ppm records every m/z
``2 * c`` ppm high, so true m/z = recorded m/z / (1 + 2c * 1e-6).
"""

from types import SimpleNamespace

import numpy as np
import pytest

from xic_extractor.configuration.mass_recalibration import MassRecalibration
from xic_extractor.raw_reader import RawFileHandle, RawReaderError
from xic_extractor.xic_models import XICRequest

TRUE_MZ = 500.0
NEIGHBOUR_MZ = TRUE_MZ * (1 + 20e-6)
# Scan 1 is a normal period, scan 3 a shifted period; scan 2 is an MS2 scan.
LM_BY_SCAN = {1: 1.0, 2: 12.0, 3: 12.5}
RT_BY_SCAN = {1: 10.0, 2: 10.01, 3: 10.02}


def _recorded(true_mz: float, scan: int) -> float:
    return true_mz * (1.0 + 2.0 * LM_BY_SCAN[scan] * 1e-6)


class _Vendor:
    def __init__(self, *, trailer_labels=("LM Correction (ppm):",), ms2=None):
        self.trailer_labels = list(trailer_labels)
        self.ms2 = ms2 or {}
        self.chromatogram_calls = 0
        self.centroids = {
            # The analyte at its recorded mass, plus a neighbour 20 ppm above
            # the true analyte mass that a 5 ppm window must exclude.
            1: ([_recorded(TRUE_MZ, 1), _recorded(NEIGHBOUR_MZ, 1)], [100.0, 7.0]),
            3: ([_recorded(TRUE_MZ, 3), _recorded(NEIGHBOUR_MZ, 3)], [300.0, 7.0]),
        }

    def ScanNumberFromRetentionTime(self, rt):
        return 1 if rt <= 10.0 else 3

    def RetentionTimeFromScanNumber(self, scan):
        return RT_BY_SCAN[scan]

    def GetFilterForScanNumber(self, scan):
        return SimpleNamespace(MSOrder="Ms2" if scan == 2 else "Ms1")

    def GetCentroidStream(self, scan, _refresh):
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


def _handle(vendor, policy=MassRecalibration.EASY_IC_DOUBLED_LM):
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


def test_ms2_fragments_use_their_scan_and_precursors_use_the_parent_ms1():
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
            MSOrder="Ms2",
            Filter=SimpleNamespace(
                Reactions=[SimpleNamespace(PrecursorMass=filter_precursor)]
            ),
        )
        vendor.GetSimplifiedScan = lambda scan: SimpleNamespace(
            Masses=[product_reading], Intensities=[50.0]
        )
        values = {
            1: [str(LM_BY_SCAN[1]), "0", "0"],
            2: [str(LM_BY_SCAN[2]), str(parent_reading), "1"],
        }
        vendor.GetTrailerExtraInformation = lambda scan: SimpleNamespace(
            Labels=list(labels), Values=values[scan]
        )
        vendor.ScanNumberFromRetentionTime = lambda rt: 2
        handle = _handle(vendor)
        return next(iter(handle.iter_ms2_scan_subset(9.0, 11.0, frozenset({2})))).scan

    # A data-dependent pick records the parent reading in both header fields.
    scan = run(parent_reading)
    assert scan.masses[0] == pytest.approx(true_product, abs=1e-6)
    assert scan.precursor_metadata.monoisotopic_mz == pytest.approx(
        true_precursor, abs=1e-4
    )
    assert scan.precursor_mz == pytest.approx(true_precursor, abs=1e-4)

    # A targeted inclusion mass is a method value, not a parent measurement.
    scan = run(true_precursor)
    assert scan.precursor_mz == true_precursor
    assert scan.precursor_metadata.monoisotopic_mz == pytest.approx(
        true_precursor, abs=1e-4
    )


def test_recalibration_requires_the_vendor_correction_trailer():
    handle = _handle(_Vendor(trailer_labels=("Charge State:",)))

    with pytest.raises(RawReaderError, match="LM Correction"):
        handle.extract_xic_many(
            (XICRequest(mz=TRUE_MZ, rt_min=9.9, rt_max=10.05, ppm_tol=5.0),)
        )


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
