"""Detail drawer assembly for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .backfill_reconciliation_gallery_chain_html import (
    _chain_item_html,
    _compact_product_reason,
    _component_list_html,
    _secondary_chain_details_html,
)
from .backfill_reconciliation_gallery_detail_cards import (
    _detail_summary_html,
    _overlay_evidence_notes_html,
    _representative_cells_table_html,
    _review_answer_html,
)
from .backfill_reconciliation_gallery_html import (
    badge as _badge,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_indices import (
    _shadow_projection_compact_summary,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    RepresentativeCell,
    ShadowPolicyCell,
    ShadowProjectionCell,
    TargetBenchmarkContext,
)
from .backfill_reconciliation_gallery_provenance import (
    _source_artifacts_html,
)
from .backfill_reconciliation_gallery_shadow_tables import (
    _shadow_policy_cells_html,
    _shadow_projection_cells_html,
)
from .backfill_reconciliation_gallery_state import (
    _shadow_policy_chain_subtitle,
    _shadow_policy_chain_title,
)
from .backfill_reconciliation_gallery_target_benchmark import (
    _target_benchmark_compact_summary,
    _target_benchmark_contexts_html,
)


def _details_html(
    group: ReconciliationGroup,
    representatives: Sequence[RepresentativeCell],
    *,
    shadow_policy_cells: Sequence[ShadowPolicyCell] = (),
    shadow_projection_cells: Sequence[ShadowProjectionCell] = (),
    target_benchmark_contexts: Sequence[TargetBenchmarkContext] = (),
    html_path: Path,
    input_artifacts: object,
    include_seed_context: bool = True,
) -> str:
    seed_context_item = (
        _chain_item_html(
            "seed / request",
            "dependent context",
            (
                f"basis={_escape(group.seed_group_basis)}<br>"
                f"m/z={_escape(group.seed_mz or 'unknown')} · "
                f"RT={_escape(group.seed_rt or 'unknown')} · "
                f"window={_escape(group.seed_rt_window or 'unknown')} · "
                f"ppm={_escape(group.seed_ppm or 'unknown')}"
            ),
        )
        if include_seed_context
        else ""
    )
    secondary_items = (
        seed_context_item
        + _chain_item_html(
            "Target benchmark",
            _target_benchmark_compact_summary(
                target_benchmark_contexts,
                input_artifacts,
            ),
            _target_benchmark_contexts_html(
                target_benchmark_contexts,
                input_artifacts,
            ),
            css_class="target-benchmark-chain",
        )
        + _chain_item_html(
            "source artifacts",
            "provenance",
            _source_artifacts_html(group.source_artifacts, input_artifacts, html_path),
        )
    )
    return (
        _review_answer_html(
            group,
            html_path,
            input_artifacts,
            shadow_projection_cells=shadow_projection_cells,
        )
        + _detail_summary_html(
            group,
            representatives,
            html_path=html_path,
            input_artifacts=input_artifacts,
            shadow_policy_cells=shadow_policy_cells,
            shadow_projection_cells=shadow_projection_cells,
        )
        + '<div class="details-grid evidence-chain">'
        + _chain_item_html(
            "product behavior",
            group.product_behavior_state,
            (
                f"{_badge(group.product_behavior_state)}"
                '<p class="chain-note">'
                f"{_escape(_compact_product_reason(group.top_product_reason))}"
                "</p>"
            ),
        )
        + _chain_item_html(
            "RT / alignment context",
            "context",
            _component_list_html(group.dependent_context_components)
            or (
                '<p class="chain-note">'
                "No dependent RT/alignment component supplied.</p>"
            ),
        )
        + _chain_item_html(
            "Hypothesis MS1 evidence",
            "visual evidence",
            _overlay_evidence_notes_html(group.overlay_evidence_notes)
            or '<p class="chain-note">No overlay metric notes supplied.</p>',
        )
        + (
            _chain_item_html(
                "Shadow production projection",
                _shadow_projection_compact_summary(shadow_projection_cells),
                _shadow_projection_cells_html(shadow_projection_cells, html_path),
                css_class="shadow-projection-chain",
            )
            if shadow_projection_cells
            else ""
        )
        + (
            _chain_item_html(
                _shadow_policy_chain_title(shadow_projection_cells),
                _shadow_policy_chain_subtitle(
                    shadow_policy_cells,
                    shadow_projection_cells,
                ),
                _shadow_policy_cells_html(
                    shadow_policy_cells,
                    html_path,
                    legacy_reference=bool(shadow_projection_cells),
                ),
                css_class="shadow-policy-chain",
            )
            if shadow_policy_cells or shadow_projection_cells
            else ""
        )
        + _chain_item_html(
            "Optional Candidate MS2 / review context",
            "not a backfill gate",
            _component_list_html(group.product_grade_support_components)
            or _component_list_html(group.review_only_visual_components)
            or '<p class="chain-note">No optional MS2/review context supplied.</p>',
        )
        + _chain_item_html(
            "blockers / missing evidence",
            "fail closed",
            _component_list_html(
                (
                    *group.blocker_components,
                    *group.missing_evidence,
                    *group.source_warnings,
                ),
            )
            or (
                '<p class="chain-note">'
                "No blocker or missing-evidence token supplied.</p>"
            ),
        )
        + _chain_item_html(
            "representative cells",
            f"{len(representatives)} cells",
            _representative_cells_table_html(representatives),
        )
        + _secondary_chain_details_html(
            "provenance / benchmark",
            "seed request, target benchmark, source artifacts",
            secondary_items,
        )
        + "</div>"
    )
