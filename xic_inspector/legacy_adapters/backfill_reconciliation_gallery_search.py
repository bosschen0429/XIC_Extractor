"""Search text helpers for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Sequence

from .backfill_reconciliation_gallery_indices import (
    _is_projected_new_accept,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    ShadowProjectionCell,
    TargetBenchmarkContext,
)


def _search_blob(
    group: ReconciliationGroup,
    shadow_projection_cells: Sequence[ShadowProjectionCell] = (),
) -> str:
    return " ".join(
        (
            group.feature_family_id,
            group.seed_group_id,
            group.product_behavior_state,
            group.evidence_authority_state,
            group.reconciliation_class,
            group.top_support_component,
            group.top_blocker,
            ";".join(group.missing_evidence),
            ";".join(group.source_warnings),
            _shadow_projection_search_blob(shadow_projection_cells),
        ),
    )


def _family_search_blob(
    groups: Sequence[ReconciliationGroup],
    target_benchmark_contexts: Sequence[TargetBenchmarkContext] = (),
    shadow_projection_cells: Sequence[ShadowProjectionCell] = (),
) -> str:
    target_text = " ".join(
        " ".join(
            (
                context.target_label,
                context.role,
                context.status,
                context.selected_feature_id,
            ),
        )
        for context in target_benchmark_contexts
    )
    return " ".join(
        (
            *(_search_blob(group) for group in groups),
            target_text,
            _shadow_projection_search_blob(shadow_projection_cells),
        ),
    )


def _shadow_projection_search_blob(
    cells: Sequence[ShadowProjectionCell],
) -> str:
    terms: list[str] = []
    for cell in cells:
        terms.extend(
            (
                cell.feature_family_id,
                cell.seed_group_id,
                cell.sample_stem,
                cell.current_production_status,
                cell.shadow_decision,
                cell.projection_authority,
                cell.product_authority_chain,
                ";".join(cell.shadow_reasons),
                ";".join(cell.shadow_warnings),
            ),
        )
        if _is_projected_new_accept(cell):
            terms.extend(("projection_accept", "projected_new_write"))
    return " ".join(term for term in terms if term)
