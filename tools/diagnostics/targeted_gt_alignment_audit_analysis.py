from __future__ import annotations

from tools.diagnostics.targeted_gt_alignment_audit_models import (
    DRIFT_MODE,
    DUPLICATE_MODE,
    MISS_MODE,
    PASS_MODE,
    PRODUCTION_STATUSES,
    SPLIT_MODE,
    AuditConfig,
    TargetGroundTruth,
)
from tools.diagnostics.targeted_gt_alignment_audit_utils import (
    _cell_rt,
    _format_float,
    _is_trueish,
    _join_ids,
    _rt_delta_sec,
    _to_int,
)


def _classify_sample(
    target: TargetGroundTruth,
    cells: tuple[dict[str, str], ...],
    config: AuditConfig,
) -> dict[str, object]:
    production = [cell for cell in cells if _is_production_cell(cell)]
    duplicates = [cell for cell in cells if _status(cell) == "duplicate_assigned"]
    production_in_window = _production_cells_in_gt_window(target, production)
    closest = _closest_cell(target, production or duplicates or list(cells))
    closest_delta = _rt_delta_sec(target, closest)
    mode = _failure_mode(
        closest=closest,
        closest_delta_sec=closest_delta,
        production_in_window=production_in_window,
        production=production,
        duplicates=duplicates,
        config=config,
    )
    closest_rt = _cell_rt(closest) if closest is not None else None
    return {
        "sample_stem": target.sample_stem,
        "group": target.group,
        "gt_target_rt_min": _format_float(target.target_rt_min, 4),
        "gt_target_confidence": target.target_confidence,
        "gt_peak_start_min": _format_float(target.target_peak_start_min, 4),
        "gt_peak_end_min": _format_float(target.target_peak_end_min, 4),
        "family_count_total": len({cell["feature_id"] for cell in cells}),
        "family_ids_all": _join_ids(cell["feature_id"] for cell in cells),
        "production_family_ids": _join_ids(cell["feature_id"] for cell in production),
        "duplicate_family_ids": _join_ids(cell["feature_id"] for cell in duplicates),
        "production_family_count_in_gt_window": len(
            {cell["feature_id"] for cell in production_in_window}
        ),
        "production_family_ids_in_gt_window": _join_ids(
            cell["feature_id"] for cell in production_in_window
        ),
        "closest_family_id": closest.get("feature_id", "")
        if closest is not None
        else "",
        "closest_family_mz": closest.get("family_center_mz", "")
        if closest is not None
        else "",
        "closest_status": _status(closest) if closest is not None else "",
        "closest_apex_rt_min": _format_float(closest_rt, 4),
        "closest_rt_delta_sec": _format_float(closest_delta, 2),
        "failure_mode": mode,
    }


def _production_cells_in_gt_window(
    target: TargetGroundTruth,
    cells: list[dict[str, str]],
) -> list[dict[str, str]]:
    start = target.target_peak_start_min
    end = target.target_peak_end_min
    if start is None or end is None:
        return []
    return [
        cell
        for cell in cells
        if (rt := _cell_rt(cell)) is not None and start <= rt <= end
    ]


def _closest_cell(
    target: TargetGroundTruth,
    cells: list[dict[str, str]],
) -> dict[str, str] | None:
    closest: dict[str, str] | None = None
    closest_delta: float | None = None
    for cell in cells:
        delta = _rt_delta_sec(target, cell)
        if delta is None:
            continue
        if closest_delta is None or abs(delta) < abs(closest_delta):
            closest = cell
            closest_delta = delta
    return closest


def _failure_mode(
    *,
    closest: dict[str, str] | None,
    closest_delta_sec: float | None,
    production_in_window: list[dict[str, str]],
    production: list[dict[str, str]],
    duplicates: list[dict[str, str]],
    config: AuditConfig,
) -> str:
    if closest is None or closest_delta_sec is None:
        return MISS_MODE
    if production:
        if abs(closest_delta_sec) <= config.pass_rt_sec:
            if len({cell["feature_id"] for cell in production_in_window}) > 1:
                return SPLIT_MODE
            return PASS_MODE
        if abs(closest_delta_sec) <= config.drift_rt_sec:
            return DRIFT_MODE
        return MISS_MODE
    if duplicates and abs(closest_delta_sec) <= config.drift_rt_sec:
        return DUPLICATE_MODE
    return MISS_MODE


def _status(cell: dict[str, str] | None) -> str:
    return "" if cell is None else (cell.get("status") or "").strip().lower()


def _is_production_cell(cell: dict[str, str]) -> bool:
    if _status(cell) not in PRODUCTION_STATUSES:
        return False
    identity_decision = (cell.get("_review_identity_decision") or "").strip()
    if identity_decision and identity_decision != "production_family":
        return False
    if "_review_include_in_primary_matrix" in cell and not _is_trueish(
        cell.get("_review_include_in_primary_matrix")
    ):
        return False
    if (
        "_review_accepted_cell_count" in cell
        and _to_int(cell.get("_review_accepted_cell_count")) <= 0
    ):
        return False
    return True
