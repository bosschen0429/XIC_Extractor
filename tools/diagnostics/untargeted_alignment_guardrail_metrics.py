"""Metric calculation helpers for untargeted alignment guardrails."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from tools.diagnostics.untargeted_alignment_guardrail_io import (
    _read_optional_tsv,
    _read_required_tsv,
)
from tools.diagnostics.untargeted_alignment_guardrail_models import (
    CASE_WINDOWS,
    COMPARISON_METRICS,
    PRODUCTION_STATUSES,
    CaseAssertion,
    CaseWindow,
    GuardrailMetrics,
)


def compute_guardrails(alignment_dir: Path) -> GuardrailMetrics:
    review_rows = _read_required_tsv(alignment_dir / "alignment_review.tsv")
    cell_rows = _read_required_tsv(alignment_dir / "alignment_cells.tsv")
    edge_rows = _read_optional_tsv(alignment_dir / "owner_edge_evidence.tsv")

    status_counts = _status_counts_by_family(cell_rows)
    review_has_warning_column = any("warning" in row for row in review_rows)
    review_by_family = {
        row.get("feature_family_id", ""): row
        for row in review_rows
        if row.get("feature_family_id", "")
    }
    family_ids = set(review_by_family) | set(status_counts)

    zero_present = 0
    duplicate_only = 0
    high_backfill = 0
    negative_8oxodg = 0
    negative_checkpoint = 0
    accepted_quantitative_cells = 0
    accepted_backfill_cells = 0
    review_backfill_count = 0
    backfill_only_review = 0
    identity_anchor_lost = 0
    duplicate_claim_pressure = 0
    for family_id in family_ids:
        counts = status_counts[family_id]
        detected_count = counts["detected"]
        backfilled_count = counts["backfilled"]
        duplicate_assigned_count = counts["duplicate_assigned"]
        review_row = review_by_family.get(family_id, {})
        accepted_cells = _int_value(review_row.get("accepted_cell_count"))
        accepted_backfills = _int_value(review_row.get("accepted_backfill_count"))
        review_backfills = _int_value(review_row.get("review_backfill_count"))
        row_flags = _row_flags(review_row)
        accepted_quantitative_cells += accepted_cells
        accepted_backfill_cells += accepted_backfills
        review_backfill_count += review_backfills
        if "rescue_only_review" in row_flags:
            backfill_only_review += 1
        if "identity_anchor_lost" in row_flags:
            identity_anchor_lost += 1
        if "duplicate_claim_pressure" in row_flags:
            duplicate_claim_pressure += 1

        production_family = _is_production_family(review_row, counts)
        if not production_family:
            zero_present += 1
        if not production_family and duplicate_assigned_count > 0:
            duplicate_only += 1
        if _is_high_backfill_dependency(
            review_row,
            detected_count,
            backfilled_count,
            review_has_warning_column,
        ):
            high_backfill += 1
        if production_family and _row_in_mz_window(
            review_row,
            "family_center_mz",
            284.0989,
            20.0,
        ):
            negative_8oxodg += 1
        if production_family and _row_in_mz_window(
            review_row,
            "family_center_mz",
            284.0989,
            20.0,
        ):
            negative_checkpoint += 1

    return GuardrailMetrics(
        zero_present_families=zero_present,
        duplicate_only_families=duplicate_only,
        high_backfill_dependency_families=high_backfill,
        negative_8oxodg_production_families=negative_8oxodg,
        negative_checkpoint_production_families=negative_checkpoint,
        accepted_quantitative_cells=accepted_quantitative_cells,
        accepted_backfill_cells=accepted_backfill_cells,
        accepted_backfill_rate=(
            accepted_backfill_cells / accepted_quantitative_cells
            if accepted_quantitative_cells
            else 0.0
        ),
        review_backfill_count=review_backfill_count,
        rescue_only_review_families=backfill_only_review,
        identity_anchor_lost_families=identity_anchor_lost,
        duplicate_claim_pressure_families=duplicate_claim_pressure,
        istd_false_missing_recovery=0,
        case_assertions=_compute_case_assertions(
            review_rows,
            status_counts,
            edge_rows,
        ),
    )


def _compute_case_assertions(
    review_rows: list[dict[str, str]],
    status_counts: Mapping[str, Counter[str]],
    edge_rows: list[dict[str, str]],
) -> dict[str, CaseAssertion]:
    assertions = {}
    for window in CASE_WINDOWS:
        review_in_window = [
            row for row in review_rows if _review_row_in_window(row, window)
        ]
        production_family_count = sum(
            1
            for row in review_in_window
            if _is_production_family(
                row,
                status_counts[row.get("feature_family_id", "")],
            )
        )
        owner_count = sum(
            _int_value(row.get("event_cluster_count")) for row in review_in_window
        )
        event_count = sum(
            _int_value(row.get("event_member_count")) for row in review_in_window
        )
        supporting_event_count = max(event_count - owner_count, 0)
        ambiguous_count = sum(
            status_counts[row.get("feature_family_id", "")]["ambiguous_ms1_owner"]
            for row in review_in_window
        )
        preserved_split_or_ambiguous = (
            window.name == "case2_mz296_dense_duplicate"
            and (len(review_in_window) > 1 or ambiguous_count > 0)
        )
        strong_edge_count = _strong_edge_count(edge_rows, window)
        status, reason = _case_status_reason(
            window.name,
            production_family_count,
            supporting_event_count,
            strong_edge_count,
            preserved_split_or_ambiguous,
        )
        assertions[window.name] = CaseAssertion(
            production_family_count=production_family_count,
            owner_count=owner_count,
            event_count=event_count,
            supporting_event_count=supporting_event_count,
            strong_edge_count=strong_edge_count,
            preserved_split_or_ambiguous=preserved_split_or_ambiguous,
            status=status,
            reason=reason,
        )
    return assertions


def _case_status_reason(
    case_name: str,
    production_family_count: int,
    supporting_event_count: int,
    strong_edge_count: int,
    preserved_split_or_ambiguous: bool,
) -> tuple[str, str]:
    if case_name == "case1_mz242_5medC_like":
        if production_family_count > 0 and supporting_event_count > 0:
            return "PASS", "production family retains supporting events"
        return "WARN", "missing production family or supporting events"
    if case_name == "case2_mz296_dense_duplicate":
        if preserved_split_or_ambiguous:
            return "PASS", "split or ambiguous ownership is preserved"
        return "WARN", "dense duplicate case collapsed without ambiguity marker"
    if case_name == "case3_mz322_dense_duplicate":
        if strong_edge_count > 0:
            return "PASS", "strong owner edge evidence is present"
        return "WARN", "no strong owner edge evidence in case window"
    if production_family_count == 0:
        return "PASS", "shadow duplicate case has no production family"
    return "WARN", "shadow duplicate case still has production family"


def _status_counts_by_family(
    cell_rows: list[dict[str, str]],
) -> defaultdict[str, Counter[str]]:
    counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in cell_rows:
        family_id = row.get("feature_family_id", "")
        if family_id:
            counts[family_id][row.get("status", "")] += 1
    return counts


def _is_high_backfill_dependency(
    review_row: Mapping[str, str],
    detected_count: int,
    backfilled_count: int,
    review_has_warning_column: bool,
) -> bool:
    if "high_backfill_dependency" in _row_flags(review_row):
        return True
    if review_has_warning_column:
        return review_row.get("warning") == "high_backfill_dependency"
    return backfilled_count > detected_count and backfilled_count >= 2


def _production_present_count(counts: Counter[str]) -> int:
    return sum(counts[status] for status in PRODUCTION_STATUSES)


def _is_production_family(
    review_row: Mapping[str, str],
    counts: Counter[str],
) -> bool:
    identity_decision = review_row.get("identity_decision")
    if identity_decision:
        return identity_decision == "production_family"
    if "include_in_primary_matrix" in review_row:
        return _is_trueish(review_row.get("include_in_primary_matrix"))
    if "accepted_cell_count" in review_row:
        return _int_value(review_row.get("accepted_cell_count")) > 0
    return _production_present_count(counts) > 0


def _is_trueish(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "t", "yes", "y"}


def _review_row_in_window(row: Mapping[str, str], window: CaseWindow) -> bool:
    return _row_in_mz_window(
        row,
        "family_center_mz",
        window.mz,
        window.ppm,
    ) and _float_in_range(
        _first_present(row, ("family_center_rt", "family_center_rt_min")),
        window.rt_min,
        window.rt_max,
    )


def _strong_edge_count(rows: list[dict[str, str]], window: CaseWindow) -> int:
    count = 0
    for row in rows:
        if row.get("decision") != "strong_edge":
            continue
        real_schema_match = _edge_endpoint_in_window(
            row,
            "left",
            window,
            mz_keys=("left_precursor_mz", "left_mz", "left_family_center_mz"),
            rt_keys=(
                "left_rt_min",
                "left_family_center_rt",
                "left_family_center_rt_min",
            ),
        ) and _edge_endpoint_in_window(
            row,
            "right",
            window,
            mz_keys=("right_precursor_mz", "right_mz", "right_family_center_mz"),
            rt_keys=(
                "right_rt_min",
                "right_family_center_rt",
                "right_family_center_rt_min",
            ),
        )
        legacy_schema_match = _edge_endpoint_in_window(
            row,
            "endpoint_a",
            window,
        ) and _edge_endpoint_in_window(
            row,
            "endpoint_b",
            window,
        )
        if real_schema_match or legacy_schema_match:
            count += 1
    return count


def _edge_endpoint_in_window(
    row: Mapping[str, str],
    prefix: str,
    window: CaseWindow,
    *,
    mz_keys: Sequence[str] | None = None,
    rt_keys: Sequence[str] | None = None,
) -> bool:
    if mz_keys is None:
        mz_keys = (f"{prefix}_mz", f"{prefix}_family_center_mz")
    if rt_keys is None:
        rt_keys = (
            f"{prefix}_rt_min",
            f"{prefix}_family_center_rt",
            f"{prefix}_family_center_rt_min",
        )
    mz_value = _first_present(row, mz_keys)
    rt_value = _first_present(row, rt_keys)
    return _mz_in_ppm(mz_value, window.mz, window.ppm) and _float_in_range(
        rt_value,
        window.rt_min,
        window.rt_max,
    )


def _row_in_mz_window(
    row: Mapping[str, str],
    key: str,
    center_mz: float,
    ppm: float,
) -> bool:
    return _mz_in_ppm(row.get(key), center_mz, ppm)


def _mz_in_ppm(value: str | None, center_mz: float, ppm: float) -> bool:
    parsed = _float_value(value)
    if parsed is None:
        return False
    return abs(parsed - center_mz) <= center_mz * ppm / 1_000_000


def _float_in_range(value: str | None, minimum: float, maximum: float) -> bool:
    parsed = _float_value(value)
    return parsed is not None and minimum <= parsed <= maximum


def _float_value(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _int_value(value: str | None) -> int:
    parsed = _float_value(value)
    if parsed is None or not math.isfinite(parsed):
        return 0
    return int(parsed)


def _row_flags(row: Mapping[str, str | None]) -> set[str]:
    value = row.get("row_flags", "")
    if value is None or value == "":
        return set()
    return {part.strip() for part in value.split(";") if part.strip()}


def _first_present(row: Mapping[str, str], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _metrics_for_comparison(metrics: GuardrailMetrics) -> dict[str, int]:
    return {metric: int(getattr(metrics, metric)) for metric in COMPARISON_METRICS}


def _metrics_to_json(metrics: GuardrailMetrics) -> dict[str, object]:
    payload = asdict(metrics)
    payload["case_assertions"] = {
        case_name: asdict(assertion)
        for case_name, assertion in metrics.case_assertions.items()
    }
    return payload
