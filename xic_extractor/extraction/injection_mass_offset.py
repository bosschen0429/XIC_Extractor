"""One injection's systematic MS1 mass error, read from its spiked ISTDs.

Every ISTD of an injection shares the instrument's mass calibration, so the
median of their apex centroid errors is that injection's offset. An ISTD
selected on a neighbouring interference cannot move the median; at least three
measured ISTDs are required for it to be outvoted.
"""

from __future__ import annotations

from collections.abc import Iterable
from statistics import median
from typing import TYPE_CHECKING, Any

from xic_extractor.configuration.models import Target
from xic_extractor.xic_models import XICRequest

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import ExtractionResult

MIN_MEASURED_ISTDS = 3
# Apex core read on each side of the reported ISTD apex.
_APEX_HALF_WINDOW_MIN = 0.05


def injection_mass_offset_ppm(
    raw: Any, istds: Iterable[tuple[Target, ExtractionResult]]
) -> float | None:
    apexes = [
        (target, result.reported_rt)
        for target, result in istds
        if result.reported_rt is not None
    ]
    if len(apexes) < MIN_MEASURED_ISTDS:
        return None
    errors = [
        error
        for target, apex_rt in apexes
        if (error := _apex_mass_error_ppm(raw, target, apex_rt)) is not None
    ]
    return median(errors) if len(errors) >= MIN_MEASURED_ISTDS else None


def _apex_mass_error_ppm(raw: Any, target: Target, apex_rt: float) -> float | None:
    tolerance = target.mz * target.ppm_tol / 1e6
    first, last = raw.scan_window_for_request(
        XICRequest(
            target.mz,
            apex_rt - _APEX_HALF_WINDOW_MIN,
            apex_rt + _APEX_HALF_WINDOW_MIN,
            target.ppm_tol,
        )
    )
    errors = [
        (centroid - target.mz) / target.mz * 1e6
        for scan in range(first, last + 1)
        if (
            centroid := raw.ms1_centroid_mz(
                raw.retention_time_for_scan(scan),
                target.mz - tolerance,
                target.mz + tolerance,
            )
        )
        is not None
    ]
    return median(errors) if errors else None
