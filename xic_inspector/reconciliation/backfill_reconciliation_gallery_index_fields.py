"""Index field derivation helpers for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from xic_extractor.diagnostics.diagnostic_io import optional_float, text_value

from .backfill_reconciliation_gallery_evidence import (
    _cell_has_primary_area_context,
    _cell_writes_primary_matrix,
)
from .backfill_reconciliation_gallery_indices import (
    _representative_sort_key,
)
from .backfill_reconciliation_gallery_models import (
    RepresentativeCell,
    _ordered_unique,
    _SeedRecord,
)


def _representative_cells_for_group(
    *,
    family: str,
    seed_group_id: str,
    product_behavior_state: str,
    evidence: Mapping[str, Any],
    group_cells: Sequence[Mapping[str, str]],
    seed_record: _SeedRecord,
) -> tuple[RepresentativeCell, ...]:
    rescued = [
        row for row in group_cells if text_value(row.get("status")).lower() == "rescued"
    ]
    if not rescued:
        return ()
    by_key: dict[str, RepresentativeCell] = {}

    def add(role: str, row: Mapping[str, str], reason: str) -> None:
        key = _source_row_key(family, row)
        existing = by_key.get(key)
        roles = (
            (role,)
            if existing is None
            else _ordered_unique((*existing.representative_roles, role))
        )
        by_key[key] = RepresentativeCell(
            feature_family_id=family,
            seed_group_id=seed_group_id,
            representative_roles=tuple(roles),
            sample_stem=text_value(row.get("sample_stem")),
            cell_status=text_value(row.get("status")),
            product_cell_state=_product_cell_state(row, product_behavior_state),
            shape_similarity=text_value(row.get("shape_similarity")),
            scan_support_score=text_value(row.get("scan_support_score")),
            apex_delta_sec=_apex_delta_sec(row, seed_record),
            boundary_overlap=text_value(row.get("boundary_overlap")),
            interference_signal=text_value(
                row.get("interference_signal")
                or row.get("neighbor_interference")
                or row.get("trace_quality"),
            ),
            representative_reason=reason,
            source_row_key=key,
        )

    support_row = max(
        rescued,
        key=lambda row: (
            optional_float(row.get("shape_similarity")) or -1.0,
            optional_float(row.get("scan_support_score")) or -1.0,
            text_value(row.get("sample_stem")),
        ),
    )
    add("strongest_support", support_row, "highest existing support metric")
    seed_row = min(
        rescued,
        key=lambda row: (
            abs(optional_float(_apex_delta_sec(row, seed_record)) or 999999.0),
            text_value(row.get("sample_stem")),
        ),
    )
    add("seed_representative", seed_row, "seed/request representative")
    if evidence.get("blocker_components"):
        add("strongest_blocker", rescued[0], "existing blocker component")
    if evidence.get("authority_state") in {
        "product_grade_support",
        "review_only_visual_support",
        "evidence_blocks_backfill",
    }:
        add("product_disagreement_example", rescued[0], "product/evidence example")
    return tuple(sorted(by_key.values(), key=_representative_sort_key))


def _product_cell_state(row: Mapping[str, str], group_state: str) -> str:
    if _cell_writes_primary_matrix(row):
        return "primary_matrix"
    if _cell_has_primary_area_context(row):
        return "candidate_context"
    if group_state == "product_rescued_context_only":
        return "candidate_context"
    return group_state


def _apex_delta_sec(row: Mapping[str, str], seed_record: _SeedRecord) -> str:
    direct = text_value(row.get("backfill_apex_delta_sec") or row.get("rt_delta_sec"))
    if direct:
        return direct
    apex = optional_float(row.get("apex_rt"))
    seed_rt = optional_float(seed_record.seed_rt)
    if apex is None or seed_rt is None:
        return ""
    return f"{(apex - seed_rt) * 60:.6g}"


def _source_row_key(family: str, row: Mapping[str, str]) -> str:
    sample = text_value(row.get("sample_stem")) or "unknown_sample"
    status = text_value(row.get("status")) or "unknown_status"
    return f"{family}::{sample}::{status}"


def _top_product_reason(row: Mapping[str, str]) -> str:
    for column in ("identity_reason", "primary_evidence", "reason", "row_flags"):
        value = text_value(row.get(column))
        if value:
            return value
    return ""


def _tag_or_class(
    review_row: Mapping[str, str],
    seed_aware_row: Mapping[str, str],
) -> str:
    for value in (
        review_row.get("neutral_loss_tag"),
        seed_aware_row.get("review_classification"),
        review_row.get("group_construction_role"),
    ):
        parsed = text_value(value)
        if parsed:
            return parsed
    return ""


def _first_label(values: Sequence[str]) -> str:
    return values[0] if values else ""
