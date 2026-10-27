"""Shared models for the targeted ISTD benchmark diagnostic."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ACTIVE_NEUTRAL_LOSS_DA = 116.0474
ACTIVE_NEUTRAL_LOSS_TOLERANCE_DA = 0.01
ISOTOPE_SHIFT_DA = 1.003355
TARGET_MATCH_RT_SEC = 60.0
MEAN_RT_DELTA_MAX_MIN = 0.15
SAMPLE_RT_MEDIAN_ABS_DELTA_MAX_MIN = 0.15
SAMPLE_RT_P95_ABS_DELTA_MAX_MIN = 0.30
LOG_AREA_SPEARMAN_MIN = 0.90
LOG_AREA_PEARSON_MIN = 0.80


@dataclass(frozen=True)
class BenchmarkThresholds:
    active_neutral_loss_da: float = ACTIVE_NEUTRAL_LOSS_DA
    additional_active_neutral_loss_das: tuple[float, ...] = ()
    active_neutral_loss_tolerance_da: float = ACTIVE_NEUTRAL_LOSS_TOLERANCE_DA
    default_match_ppm: float = 20.0
    match_rt_sec: float = TARGET_MATCH_RT_SEC
    mean_rt_delta_max_min: float = MEAN_RT_DELTA_MAX_MIN
    sample_rt_median_abs_delta_max_min: float = SAMPLE_RT_MEDIAN_ABS_DELTA_MAX_MIN
    sample_rt_p95_abs_delta_max_min: float = SAMPLE_RT_P95_ABS_DELTA_MAX_MIN
    log_area_spearman_min: float = LOG_AREA_SPEARMAN_MIN
    log_area_pearson_min: float = LOG_AREA_PEARSON_MIN


@dataclass(frozen=True)
class TargetDefinition:
    label: str
    role: str
    mz: float
    rt_min: float
    rt_max: float
    ppm_tol: float
    neutral_loss_da: float
    product_mz: float


@dataclass(frozen=True)
class TargetedPoint:
    sample_stem: str
    target_label: str
    role: str
    rt: float | None
    area: float | None
    nl: str
    confidence: str
    reason: str

    @property
    def positive(self) -> bool:
        return self.area is not None and self.area > 0 and self.rt is not None


@dataclass(frozen=True)
class TargetedReliabilityPoint:
    sample_stem: str
    target_label: str
    reliability_state: str
    risk_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlignmentFeature:
    feature_id: str
    neutral_loss_tag: str
    family_center_mz: float
    family_center_rt: float
    family_product_mz: float
    family_observed_neutral_loss_da: float
    include_in_primary_matrix: bool


@dataclass(frozen=True)
class AlignmentCell:
    feature_id: str
    sample_stem: str
    status: str
    area: float | None
    apex_rt: float | None


@dataclass(frozen=True)
class AlignmentMatrixData:
    areas_by_family: dict[str, dict[str, float]]
    sample_stems: frozenset[str]


@dataclass(frozen=True)
class CandidateMatch:
    target_label: str
    feature_id: str
    include_in_primary_matrix: bool
    family_center_mz: float
    family_center_rt: float
    family_product_mz: float
    family_observed_neutral_loss_da: float
    mz_delta_ppm: float
    rt_delta_sec: float
    product_delta_ppm: float
    loss_delta_da: float
    mass_shift_da: float
    match_type: str
    distance_score: float


@dataclass(frozen=True)
class BenchmarkSummary:
    target_label: str
    role: str
    active_tag: bool
    neutral_loss_da: float
    target_mz: float
    target_rt_min: float
    target_rt_max: float
    targeted_positive_count: int
    targeted_total_count: int
    targeted_mean_rt: float | None
    candidate_match_count: int
    primary_match_count: int
    primary_feature_ids: tuple[str, ...]
    selected_feature_id: str
    untargeted_positive_count: int
    coverage_minimum: int
    paired_area_n: int
    log_area_pearson: float | None
    log_area_spearman: float | None
    family_mean_rt_delta_min: float | None
    sample_rt_pair_n: int
    sample_rt_median_abs_delta_min: float | None
    sample_rt_p95_abs_delta_min: float | None
    status: str
    failure_modes: tuple[str, ...]
    note: str
    targeted_reliability_mode: str = "not_provided"
    clean_targeted_positive_count: int = 0
    targeted_review_positive_count: int = 0
    targeted_review_count: int = 0
    targeted_negative_count: int = 0
    coverage_denominator_count: int = 0
    targeted_reliability_warning_modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkOutputs:
    summary_tsv: Path
    matches_tsv: Path
    json_path: Path
    markdown_path: Path
