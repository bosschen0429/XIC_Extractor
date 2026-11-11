"""Discovery's in-process binding from one peak to its own measurement."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.discovery.models import DiscoveryEvent, DiscoveryPeak
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation


@dataclass(frozen=True, slots=True)
class DiscoveryPeakPhysicalBinding:
    """One final peak and the measurement Discovery judged it by.

    The measurement stays in the Discovery process; Alignment measures the
    peak again from RAW with the capture conditions the peak table carries.
    """

    final_ordinal: int
    peak: DiscoveryPeak
    physical_evaluation: PhysicalPeakEvaluation
    representative_event: DiscoveryEvent


__all__ = ["DiscoveryPeakPhysicalBinding"]
