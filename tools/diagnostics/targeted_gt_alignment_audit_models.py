from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PRODUCTION_STATUSES = {"detected", "backfilled"}


PASS_MODE = "PASS"


SPLIT_MODE = "SPLIT"


DRIFT_MODE = "DRIFT"


DUPLICATE_MODE = "DUPLICATE"


MISS_MODE = "MISS"


@dataclass(frozen=True)
class AuditConfig:
    target_workbook: Path
    alignment_run: Path
    target_label: str
    istd_label: str
    target_mz: float
    ppm: float
    pass_rt_sec: float
    drift_rt_sec: float
    output_dir: Path


@dataclass(frozen=True)
class TargetGroundTruth:
    sample_stem: str
    group: str
    target_mz: float
    target_rt_min: float | None
    target_peak_start_min: float | None
    target_peak_end_min: float | None
    target_peak_width_min: float | None
    target_area: float | None
    target_nl_ok: str
    target_reason: str
    istd_rt_min: float | None
    istd_rt_delta_sec: float | None
