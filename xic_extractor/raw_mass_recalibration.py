"""Mass-restoration decisions and arithmetic over in-memory acquisition facts."""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    MassRecalibrationResult,
)
from xic_extractor.mass_accuracy import mz_tolerance_da, ppm_scale_factor
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.raw_reader_errors import RawReaderError
from xic_extractor.xic_models import XICRequest, XICTrace

_RECORDED_PRECURSOR_ROUNDING_MZ = 1e-4
_CARBON_ISOTOPE_SPACING_DA = 1.0033548


@dataclass(frozen=True, slots=True)
class PreparedRawMassRecalibration:
    """One run's mass result for an already identified RAW source."""

    acquisition: AcquisitionSourceExpectation
    result: MassRecalibrationResult


def resolve_mass_restoration(
    result: MassRecalibrationResult,
    polarities: set[str],
    anchors: list[float],
    *,
    has_lock_mass_trailers: bool,
) -> MassRecalibrationResult:
    """Restore positive-only acquisitions from their MS1 lock-mass finds."""
    if polarities == {"Negative"}:
        return replace(result, outcome="not_applicable", reason="negative_polarity")
    if polarities != {"Positive"}:
        return replace(result, reason="unsupported_or_mixed_polarity")
    if not has_lock_mass_trailers:
        return replace(result, reason="missing_lock_mass_trailers")
    if not anchors:
        return replace(result, reason="no_ms1_lock_mass_anchor")
    return replace(
        result,
        outcome="applied",
        baseline_lm=float(np.median(anchors)),
        n_anchor=len(anchors),
    )


def mass_restoration_scale(
    result: MassRecalibrationResult, correction_ppm: float | None, scan_number: int
) -> float:
    """Recorded m/z divided by baseline m/z (legacy replay uses its fit)."""
    if correction_ppm is None:
        raise RawReaderError(
            f"scan {scan_number} lacks a finite 'LM Correction (ppm)' value"
        )
    if result.policy is MassRecalibration.EASY_IC_RESTORE:
        assert result.baseline_lm is not None
        # Mechanism-supported ppm approximation, not absolute recalibration.
        shift_ppm = 2.0 * (correction_ppm - result.baseline_lm)
    else:
        assert result.a is not None and result.b is not None
        shift_ppm = result.a + result.b * correction_ppm
    return ppm_scale_factor(shift_ppm)


def is_recorded_precursor(
    precursor_mz: float, metadata: Ms2PrecursorMetadata, parent_scale: float
) -> bool:
    """Distinguish recorded mono/isotopes from inclusion-list setpoints."""
    monoisotopic = metadata.monoisotopic_mz
    measured = (
        monoisotopic is not None
        and abs(precursor_mz - monoisotopic) <= _RECORDED_PRECURSOR_ROUNDING_MZ
    )
    if monoisotopic is not None and metadata.charge is not None:
        # Both measured masses share the parent scale, including spacing.
        spacing = _CARBON_ISOTOPE_SPACING_DA * parent_scale / metadata.charge
        isotope_number = round((precursor_mz - monoisotopic) / spacing)
        if isotope_number >= 1:
            measured = (
                abs(precursor_mz - (monoisotopic + isotope_number * spacing))
                <= _RECORDED_PRECURSOR_ROUNDING_MZ
            )
    return measured


def matches_parent_centroid(selected: float, parent_masses: np.ndarray) -> bool:
    """Only arithmetic roundoff, never a tolerance window admitting setpoints."""
    index = int(np.searchsorted(parent_masses, selected))
    return any(
        math.isclose(selected, float(mass), rel_tol=1e-12, abs_tol=0.0)
        for mass in parent_masses[max(0, index - 1) : index + 1]
    )


def restore_ms2_precursor(
    precursor_mz: float,
    metadata: Ms2PrecursorMetadata,
    parent_scale: float,
    *,
    measured: bool,
) -> tuple[float, Ms2PrecursorMetadata]:
    """Apply the parent coordinate scale, leaving setpoints unchanged."""
    monoisotopic = metadata.monoisotopic_mz
    return (
        precursor_mz / parent_scale if measured else precursor_mz,
        replace(
            metadata,
            monoisotopic_mz=None
            if monoisotopic is None
            else monoisotopic / parent_scale,
        ),
    )


def restore_ms1_spectrum(
    rt: float, masses: np.ndarray, intensities: np.ndarray, scale: float
) -> tuple[float, np.ndarray, np.ndarray]:
    """Retain positive finite centroids, sort stably, then restore masses."""
    valid = (
        np.isfinite(masses)
        & np.isfinite(intensities)
        & (masses > 0)
        & (intensities > 0)
    )
    order = np.argsort(masses[valid], kind="stable")
    return rt, masses[valid][order] / scale, intensities[valid][order]


def restored_xic(
    spectra: Sequence[tuple[float, np.ndarray, np.ndarray]], request: XICRequest
) -> XICTrace:
    """Sum restored centroids in the requested inclusive mass window."""
    tolerance = mz_tolerance_da(request.mz, request.ppm_tol)
    rts: list[float] = []
    values: list[float] = []
    for rt, masses, intensities in spectra:
        low = np.searchsorted(masses, request.mz - tolerance, side="left")
        high = np.searchsorted(masses, request.mz + tolerance, side="right")
        rts.append(rt)
        values.append(float(intensities[low:high].sum()))
    return XICTrace.from_arrays(rts, values) if rts else XICTrace.empty()
