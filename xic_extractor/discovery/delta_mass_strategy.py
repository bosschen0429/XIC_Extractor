"""Discovery execution intent for a shared Delta Mass method."""

from dataclasses import dataclass

from xic_extractor.configuration.delta_mass import DeltaMassPatternSettings
from xic_extractor.configuration.mass_recalibration import MassRecalibration


@dataclass(frozen=True)
class DeltaMassDiscoveryStrategy:
    label: str
    settings: DeltaMassPatternSettings
    cid_nl_composite: bool
    smoothing_window_sec: float = 6.6
    mass_recalibration: MassRecalibration = MassRecalibration.NONE
