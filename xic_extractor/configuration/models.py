from dataclasses import dataclass, field
from pathlib import Path

from xic_extractor.configuration.execution_profile import default_parallel_workers
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    resolve_mass_recalibration,
)
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    resolve_signal_processing_profile,
)
from xic_extractor.configuration.targeted_identification import IdentificationMethod
from xic_extractor.settings_schema import (
    CANONICAL_RESOLVER_MODE,
)


@dataclass(frozen=True)
class ExtractionConfig:
    data_dir: Path
    dll_dir: Path
    output_csv: Path
    diagnostics_csv: Path
    smooth_window: int
    smooth_polyorder: int
    peak_rel_height: float
    peak_min_prominence_ratio: float
    ms2_precursor_tol_da: float
    nl_min_intensity_ratio: float
    # Internal morphology evidence budget, separate from shared signal processing.
    ms1_morphology_smoothing_window_points: int = 15
    resolver_mode: str = CANONICAL_RESOLVER_MODE
    resolver_chrom_threshold: float = 0.05
    resolver_min_search_range_min: float = 0.08
    resolver_min_relative_height: float = 0.02
    resolver_min_absolute_height: float = 25.0
    resolver_min_ratio_top_edge: float = 1.7
    resolver_peak_duration_min: float = 0.0
    resolver_peak_duration_max: float = 2.0
    resolver_min_scans: int = 5
    count_no_ms2_as_detected: bool = False
    # Internal Untargeted Owner Backfill acquisition budget; not a Targeted setting.
    nl_rt_anchor_half_window_min: float = 1.0
    injection_order_source: Path | None = None
    baseline_integration_method: str = "asls"
    parallel_mode: str = "serial"
    parallel_workers: int = field(default_factory=default_parallel_workers)
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
    cid_nl_tag: str = ""
    method_name: str = ""
    identification_method: IdentificationMethod | None = None


class ConfigError(Exception):
    """Raised when a configuration document or input value is invalid."""
