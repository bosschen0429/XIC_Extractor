from dataclasses import dataclass, field
from pathlib import Path

from xic_extractor.configuration.execution_profile import default_parallel_workers
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    resolve_signal_processing_profile,
)
from xic_extractor.settings_schema import CANONICAL_RESOLVER_MODE
from xic_extractor.targeted_ms1_shape_identity_policy import (
    EXPLICIT_SUPPORT_TSV_POLICY,
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
    # Compatibility field for callers outside the canonical profile migration.
    # Settings ingestion maps the legacy spelling one-way into the canonical
    # field; covered Owner Backfill processing does not read this field.
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
    nl_rt_anchor_search_margin_min: float = 2.0
    nl_rt_anchor_half_window_min: float = 1.0
    nl_fallback_half_window_min: float = 2.0
    injection_order_source: Path | None = None
    rolling_window_size: int = 5
    dirty_matrix_mode: bool = False
    rt_prior_library_path: Path | None = None
    target_pair_rt_calibration_path: Path | None = None
    model_selection_expected_diff_approval_registry: Path | None = None
    targeted_ms1_shape_identity_support_tsv: Path | None = None
    targeted_ms1_shape_identity_activation_policy: str = EXPLICIT_SUPPORT_TSV_POLICY
    emit_score_breakdown: bool = False
    emit_review_report: bool = False
    emit_peak_candidates: bool = False
    keep_intermediate_csv: bool = False
    baseline_audit_method: str = ""
    baseline_integration_method: str = "asls"
    parallel_mode: str = "serial"
    parallel_workers: int = field(default_factory=default_parallel_workers)
    config_hash: str = ""
    target_config_hash: str = ""
    signal_processing_method: str = DEFAULT_SIGNAL_PROCESSING_PROFILE.method
    signal_processing_window_points: int = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE.window_points
    )

    def __post_init__(self) -> None:
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
    isotope_label_type: str = "unknown"
    paired_rt_relation: str = "none"
    sample_applicability: str = "all"


class ConfigError(Exception):
    """Raised when settings.csv or targets.csv contains invalid values."""
