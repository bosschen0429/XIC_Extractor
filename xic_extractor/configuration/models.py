from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    resolve_mass_recalibration,
)
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    resolve_signal_processing_profile,
)
from xic_extractor.configuration.targeted_identification import TargetIdentification


@dataclass(frozen=True)
class ExtractionConfig:
    data_dir: Path
    dll_dir: Path
    output_csv: Path
    diagnostics_csv: Path
    ms2_precursor_tol_da: float
    nl_min_intensity_ratio: float
    execution: ExecutionProfile
    ms1_morphology_smoothing_window_points: int = 15
    count_no_ms2_as_detected: bool = False
    injection_order_source: Path | None = None
    baseline_integration_method: str = "asls"
    target_config_hash: str = ""
    signal_processing_method: str = DEFAULT_SIGNAL_PROCESSING_PROFILE.method
    signal_processing_window_points: int = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE.window_points
    )
    mass_recalibration: MassRecalibration = MassRecalibration.EASY_IC_RESTORE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "mass_recalibration",
            resolve_mass_recalibration(self.mass_recalibration),
        )
        resolve_signal_processing_profile(
            signal_processing_method=self.signal_processing_method,
            signal_processing_window_points=self.signal_processing_window_points,
        )


@dataclass(frozen=True)
class Target:
    label: str
    mz: float
    rt_min: float
    rt_max: float
    ppm_tol: float
    neutral_loss_da: float | None
    nl_ppm_warn: float | None
    nl_ppm_max: float | None
    is_istd: bool
    istd_pair: str
    expected_rt_min: float | None = None
    identification: TargetIdentification | None = None


class ConfigError(Exception):
    """Raised when a configuration document or input value is invalid."""
