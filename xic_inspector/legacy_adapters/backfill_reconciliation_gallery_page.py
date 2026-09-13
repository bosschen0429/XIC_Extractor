"""HTML page assembly for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from .backfill_reconciliation_gallery_assets import (
    gallery_css as _gallery_css,
)
from .backfill_reconciliation_gallery_assets import (
    lightbox_html as _lightbox_html,
)
from .backfill_reconciliation_gallery_assets import (
    lightbox_script as _lightbox_script,
)
from .backfill_reconciliation_gallery_filters import (
    _default_visible_family_count,
    _filter_html,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    ReconciliationIndex,
)
from .backfill_reconciliation_gallery_provenance import (
    _write_local_overlay_interpretation_guide,
)
from .backfill_reconciliation_gallery_render_context import (
    _gallery_render_context,
    _html_scope_notice,
)
from .backfill_reconciliation_gallery_review_modes import (
    _is_cid_nl_successor_review_index,
)
from .backfill_reconciliation_gallery_summary import (
    _summary_html,
)
from .backfill_reconciliation_gallery_table_rows import (
    _family_groups,
    _table_html,
)


def write_reconciliation_gallery_html(
    path: Path,
    index: ReconciliationIndex,
    *,
    output_paths: Mapping[str, Path],
) -> None:
    """Render a table-first human review gallery from a reconciliation index."""

    path.parent.mkdir(parents=True, exist_ok=True)
    local_interpretation_guide = _write_local_overlay_interpretation_guide(
        path.parent,
    )
    render_context = _gallery_render_context(index)
    gallery_title = _gallery_document_title(render_context.html_groups)
    hero = _gallery_hero_copy(render_context.html_groups)
    lines = [
        "<!doctype html>",
        '<html lang="zh-Hant">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_escape(gallery_title)}</title>",
        "<style>",
        _gallery_css(),
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        '<header class="gallery-hero" aria-label="gallery introduction">',
        f'<div class="hero-kicker">{_escape(str(hero["kicker"]))}</div>',
        f"<h1>{_escape(str(hero['heading']))}</h1>",
        f'<p class="hero-copy">{_escape(str(hero["copy"]))}</p>',
        '<div class="hero-status-strip" aria-label="gallery role">',
        *[
            f"<span>{_escape(label)}</span>"
            for label in tuple(hero["status_labels"])
        ],
        "</div>",
        "</header>",
        *_summary_html(
            index,
            output_paths,
            html_path=path,
            local_interpretation_guide=local_interpretation_guide,
        ),
        *_html_scope_notice(render_context.all_groups, render_context.html_groups),
        *_filter_html(
            total_families=len(_family_groups(render_context.html_groups)),
            default_visible_families=_default_visible_family_count(
                render_context.html_groups,
            ),
            has_shadow_projection=bool(
                render_context.html_shadow_projection_cells,
            ),
        ),
    ]
    if not render_context.html_groups:
        lines.append(
            '<p class="empty-state">沒有 backfill family/seed group 可審閱。</p>',
        )
    else:
        lines.extend(
            _table_html(
                render_context.html_groups,
                representatives_by_group=render_context.representatives_by_group,
                shadow_policy_cells_by_group=(
                    render_context.shadow_policy_cells_by_group
                ),
                shadow_policy_cells_by_family=(
                    render_context.shadow_policy_cells_by_family
                ),
                shadow_projection_cells_by_group=(
                    render_context.shadow_projection_cells_by_group
                ),
                shadow_projection_cells_by_family=(
                    render_context.shadow_projection_cells_by_family
                ),
                target_benchmark_contexts_by_family=(
                    render_context.target_benchmark_contexts_by_family
                ),
                html_path=path,
                input_artifacts=index.summary.get("input_artifacts", {}),
            ),
        )
        lines.extend(_lightbox_html())
    lines.extend(["</main>", _lightbox_script(), "</body>", "</html>"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _gallery_document_title(groups: Sequence[ReconciliationGroup]) -> str:
    if _is_cid_nl_successor_review_index(groups):
        return "Evidence Review Gallery - CID-NL Feature Inclusion Review"
    return "Backfill evidence reconciliation gallery"


def _gallery_hero_copy(
    groups: Sequence[ReconciliationGroup],
) -> dict[str, str | tuple[str, ...]]:
    if _is_cid_nl_successor_review_index(groups):
        return {
            "kicker": "Evidence review surface",
            "heading": "CID-NL Feature Inclusion / Identity Review",
            "copy": (
                "CID-NL/MS2 evidence, MS1 feature context, source/successor "
                "identity relationships, representative cells, and adoption "
                "candidate decisions are shown here without granting "
                "ProductWriter authority."
            ),
            "status_labels": (
                "feature inclusion first",
                "identity authority separate",
                "MS1 trace context only",
                "does not write matrix",
            ),
        }
    return {
        "kicker": "Matrix-decision audit surface",
        "heading": "Backfill Evidence Reconciliation",
        "copy": (
            "Production decisions, same-peak evidence, missing artifacts, "
            "and review-only context are reconciled here without recalculating "
            "domain evidence."
        ),
        "status_labels": (
            "artifact consumer only",
            "does not write matrix",
            "hypothesis first",
            "human review ready",
        ),
    }
