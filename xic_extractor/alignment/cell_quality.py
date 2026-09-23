from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.promotion_policy import (
    cell_has_product_authorized_drift_corrected_same_peak_backfill_support,
    cell_owner_backfill_write_block_reason,
)

CellQualityStatus = Literal[
    "detected_quantifiable",
    "rescue_quantifiable",
    "review_backfill",
    "duplicate_loser",
    "ambiguous_owner",
    "blank",
    "invalid",
]


@dataclass(frozen=True)
class CellQualityDecision:
    sample_stem: str
    feature_family_id: str
    raw_status: str
    quality_status: CellQualityStatus
    matrix_area: float | None
    quality_reason: str

    @property
    def is_quantifiable_cell(self) -> bool:
        return self.quality_status in {
            "detected_quantifiable",
            "rescue_quantifiable",
        }

    @property
    def is_detected_identity_support(self) -> bool:
        return self.quality_status == "detected_quantifiable"


def build_cell_quality_decisions(
    cells: Iterable[AlignedCell],
    config: AlignmentConfig,
) -> dict[tuple[str, str], CellQualityDecision]:
    return {
        (decision.feature_family_id, decision.sample_stem): decision
        for decision in (decide_cell_quality(cell, config) for cell in cells)
    }


def decide_cell_quality(
    cell: AlignedCell,
    config: AlignmentConfig,
) -> CellQualityDecision:
    if cell.status == "detected":
        area = _valid_area(cell.matrix_area)
        if area is None:
            return _decision(
                cell,
                "invalid",
                None,
                _invalid_matrix_area_reason(cell),
            )
        return _decision(cell, "detected_quantifiable", area, "")

    if cell.status == "backfilled":
        area = _valid_area(cell.matrix_area)
        if area is None:
            return _decision(
                cell,
                "invalid",
                None,
                _invalid_matrix_area_reason(cell),
            )
        if not _has_complete_peak(cell):
            return _decision(cell, "review_backfill", None, "incomplete_peak")
        owner_block_reason = cell_owner_backfill_write_block_reason(cell)
        if owner_block_reason:
            return _decision(cell, "review_backfill", None, owner_block_reason)
        if cell.rt_delta_sec is None:
            return _decision(cell, "review_backfill", None, "rt_outside_max")
        rt_delta_sec = abs(cell.rt_delta_sec)
        if rt_delta_sec <= config.preferred_rt_sec:
            return _decision(cell, "rescue_quantifiable", area, "")
        if cell_has_product_authorized_drift_corrected_same_peak_backfill_support(
            cell,
            preferred_rt_sec=config.preferred_rt_sec,
        ):
            return _decision(cell, "rescue_quantifiable", area, "")
        if rt_delta_sec > config.max_rt_sec:
            return _decision(cell, "review_backfill", None, "rt_outside_max")
        return _decision(cell, "review_backfill", None, "rt_outside_preferred")

    if cell.status == "duplicate_assigned":
        return _decision(cell, "duplicate_loser", None, "duplicate_loser")
    if cell.status == "ambiguous_ms1_owner":
        return _decision(cell, "ambiguous_owner", None, "ambiguous_ms1_owner")
    if cell.status == "absent":
        return _decision(cell, "blank", None, "absent")
    if cell.status == "unchecked":
        return _decision(cell, "blank", None, "unchecked")
    return _decision(cell, "invalid", None, f"unsupported_status:{cell.status}")


def decision_map_by_family(
    decisions: Mapping[tuple[str, str], CellQualityDecision],
) -> dict[str, tuple[CellQualityDecision, ...]]:
    grouped: dict[str, list[CellQualityDecision]] = {}
    for decision in decisions.values():
        grouped.setdefault(decision.feature_family_id, []).append(decision)
    return {
        family_id: tuple(sorted(values, key=lambda item: item.sample_stem))
        for family_id, values in grouped.items()
    }


def _decision(
    cell: AlignedCell,
    quality_status: CellQualityStatus,
    matrix_area: float | None,
    reason: str,
) -> CellQualityDecision:
    return CellQualityDecision(
        sample_stem=cell.sample_stem,
        feature_family_id=cell.cluster_id,
        raw_status=cell.status,
        quality_status=quality_status,
        matrix_area=matrix_area,
        quality_reason=reason,
    )


def _valid_area(value: float | None) -> float | None:
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        return None
    return float(value)


def _invalid_matrix_area_reason(cell: AlignedCell) -> str:
    if cell.selected_integration is None and _valid_area(cell.area) is None:
        return "invalid_area"
    return cell.matrix_area_missing_reason or "invalid_area"


def _has_complete_peak(cell: AlignedCell) -> bool:
    return all(
        _finite(value)
        for value in (
            cell.apex_rt,
            cell.height,
            cell.peak_start_rt,
            cell.peak_end_rt,
        )
    )


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
