"""TSV and summary row materialization for the reconciliation gallery."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from xic_extractor.diagnostics.diagnostic_io import text_value

from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    RepresentativeCell,
)

SCHEMA_VERSION = "backfill_evidence_reconciliation_v0"


def _group_as_row(group: ReconciliationGroup, *, priority_rank: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "priority_rank": priority_rank,
        "feature_family_id": group.feature_family_id,
        "seed_group_id": group.seed_group_id,
        "seed_group_basis": group.seed_group_basis,
        "seed_mz": group.seed_mz,
        "seed_rt": group.seed_rt,
        "seed_rt_window": group.seed_rt_window,
        "seed_ppm": group.seed_ppm,
        "tag_or_class": group.tag_or_class,
        "product_behavior_state": group.product_behavior_state,
        "evidence_authority_state": group.evidence_authority_state,
        "reconciliation_class": group.reconciliation_class,
        "detected_cell_count": group.detected_cell_count,
        "rescued_cell_count": group.rescued_cell_count,
        "provisional_cell_count": group.provisional_cell_count,
        "top_product_reason": group.top_product_reason,
        "top_support_component": group.top_support_component,
        "top_blocker": group.top_blocker,
        "missing_evidence": ";".join(group.missing_evidence),
        "overlay_png_path": group.overlay_png_path,
        "overlay_trace_json_path": group.overlay_trace_json_path,
        "source_artifacts": ";".join(group.source_artifacts),
        "source_warnings": ";".join(group.source_warnings),
    }


def _representative_as_row(cell: RepresentativeCell) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_family_id": cell.feature_family_id,
        "seed_group_id": cell.seed_group_id,
        "representative_roles": ";".join(cell.representative_roles),
        "sample_stem": cell.sample_stem,
        "cell_status": cell.cell_status,
        "product_cell_state": cell.product_cell_state,
        "shape_similarity": cell.shape_similarity,
        "scan_support_score": cell.scan_support_score,
        "apex_delta_sec": cell.apex_delta_sec,
        "boundary_overlap": cell.boundary_overlap,
        "interference_signal": cell.interference_signal,
        "representative_reason": cell.representative_reason,
        "source_row_key": cell.source_row_key,
    }


def _summary(
    groups: Sequence[ReconciliationGroup],
    representatives: Sequence[RepresentativeCell],
    input_artifacts: Mapping[str, object],
) -> dict[str, object]:
    reconciliation_counts = Counter(group.reconciliation_class for group in groups)
    missing_counts: Counter[str] = Counter()
    for group in groups:
        missing_counts.update(
            set(group.missing_evidence)
            | {
                token
                for token in group.source_warnings
                if token.startswith(("join_gap_", "stale_"))
            },
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_label": "diagnostic_only",
        "group_count": len(groups),
        "representative_cell_count": len(representatives),
        "reconciliation_class_counts": dict(sorted(reconciliation_counts.items())),
        "missing_evidence_counts": dict(sorted(missing_counts.items())),
        "excluded_family_counts": {},
        "input_artifacts": dict(input_artifacts),
        "matrix_contract_changed": False,
        "product_behavior_changed": False,
    }


def _string_object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {text_value(key): item for key, item in value.items() if text_value(key)}
