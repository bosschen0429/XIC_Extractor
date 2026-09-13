"""Review-table row rendering for the reconciliation gallery."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.diagnostics.diagnostic_io import (
    split_semicolon_labels,
    text_value,
)

from .backfill_reconciliation_gallery_chain_html import (
    _compact_issue_label,
    _component_list_html,
)
from .backfill_reconciliation_gallery_counts import (
    _consolidated_counts_html,
    _counts_html,
)
from .backfill_reconciliation_gallery_detail_drawer import (
    _details_html,
)
from .backfill_reconciliation_gallery_family_pattern import (
    _family_anchor_summary_html,
    _family_pattern_status_html,
)
from .backfill_reconciliation_gallery_filters import (
    _family_filter_categories,
    _group_filter_categories,
    _shadow_projection_filter_categories,
)
from .backfill_reconciliation_gallery_html import (
    badge as _badge,
)
from .backfill_reconciliation_gallery_html import (
    escape_attr as _escape_attr,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_html import (
    href_for_path as _href_for_path,
)
from .backfill_reconciliation_gallery_indices import (
    _group_sort_key,
    _is_projected_new_accept,
    _representative_sort_key,
    _shadow_policy_cells_for_family_groups,
    _shadow_policy_cells_for_group,
    _shadow_policy_compact_summary,
    _shadow_projection_cell_sort_key,
    _shadow_projection_cells_for_family_groups,
    _shadow_projection_cells_for_group,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    RepresentativeCell,
    ShadowPolicyCell,
    ShadowProjectionCell,
    TargetBenchmarkContext,
    _ordered_unique,
)
from .backfill_reconciliation_gallery_overlay_links import (
    _family_pattern_link_html,
    _hypothesis_overlay_link_html,
    _hypothesis_overlay_path,
    _missing_overlay_reason_text,
    _missing_overlay_status_html,
    _overlay_href_context,
    _overlay_link_html,
    _overlay_not_required_by_gate,
    _overlay_not_required_status_html,
    _seed_overlay_cell_html,
    _shadow_projection_overlay_link_html,
)
from .backfill_reconciliation_gallery_ranges import (
    _compact_text_values,
    _seed_mz_range,
    _seed_rt_range,
    _seed_window_range,
)
from .backfill_reconciliation_gallery_search import (
    _family_search_blob,
    _search_blob,
)
from .backfill_reconciliation_gallery_shadow_tables import (
    _shadow_projection_evidence_html,
    _shadow_projection_warnings_html,
)
from .backfill_reconciliation_gallery_state import (
    _projection_matrix_state_html,
    _state_html_for_shadow,
)
from .backfill_reconciliation_gallery_target_benchmark import (
    _family_target_summary_html,
)

_HIGH_SEED_ALIAS_COUNT = 5


def _table_html(
    groups: Sequence[ReconciliationGroup],
    *,
    representatives_by_group: Mapping[tuple[str, str], tuple[RepresentativeCell, ...]],
    shadow_policy_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowPolicyCell, ...],
    ],
    shadow_policy_cells_by_family: Mapping[str, tuple[ShadowPolicyCell, ...]],
    shadow_projection_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowProjectionCell, ...],
    ],
    shadow_projection_cells_by_family: Mapping[
        str,
        tuple[ShadowProjectionCell, ...],
    ],
    target_benchmark_contexts_by_family: Mapping[
        str,
        tuple[TargetBenchmarkContext, ...],
    ],
    html_path: Path,
    input_artifacts: object,
) -> list[str]:
    lines = [
        '<div class="table-wrap">',
        '<table class="review-table" aria-describedby="galleryTableDescription">',
        '<caption id="galleryTableDescription">'
        "Hypothesis-first backfill evidence review queue. "
        "Family rows are compact MS1 pattern context headers."
        "</caption>",
        "<colgroup>",
        '<col class="col-priority">',
        '<col class="col-family">',
        '<col class="col-state">',
        '<col class="col-issue">',
        '<col class="col-counts">',
        '<col class="col-overlay">',
        '<col class="col-details">',
        "</colgroup>",
        "<thead>",
        "<tr>",
        '<th scope="col">rank</th>',
        '<th scope="col">family / hypothesis</th>',
        '<th scope="col">state</th>',
        '<th scope="col">issue</th>',
        (
            '<th scope="col"><span title="Cell-level impact. With shadow '
            "projection input: Current=current production decision writes, "
            "Review=projection candidate pool, Write=new projected matrix "
            "writes, Block=projected hard blockers. Without projection "
            "input: NL/Candidate/Dup/Review remain alignment provenance counts, "
            'not matrix-written counts or target benchmark coverage.">'
            "impact</span></th>"
        ),
        '<th scope="col">overlay</th>',
        '<th scope="col">chain</th>',
        "</tr>",
        "</thead>",
        "<tbody>",
    ]
    for priority, family_groups in enumerate(_family_groups(groups), start=1):
        lines.extend(
            _family_table_row(
                priority,
                family_groups,
                representatives_by_group=representatives_by_group,
                shadow_policy_cells_by_group=shadow_policy_cells_by_group,
                shadow_policy_cells_by_family=shadow_policy_cells_by_family,
                shadow_projection_cells_by_group=shadow_projection_cells_by_group,
                shadow_projection_cells_by_family=shadow_projection_cells_by_family,
                target_benchmark_contexts=target_benchmark_contexts_by_family.get(
                    family_groups[0].feature_family_id,
                    (),
                ),
                html_path=html_path,
                input_artifacts=input_artifacts,
            ),
        )
    lines.extend(["</tbody>", "</table>", "</div>"])
    return lines


def _family_groups(
    groups: Sequence[ReconciliationGroup],
) -> tuple[tuple[ReconciliationGroup, ...], ...]:
    grouped: dict[str, list[ReconciliationGroup]] = {}
    for group in sorted(groups, key=_group_sort_key):
        grouped.setdefault(group.feature_family_id, []).append(group)
    return tuple(
        tuple(items)
        for _, items in sorted(
            grouped.items(),
            key=lambda item: _family_sort_key(tuple(item[1])),
        )
    )


def _family_sort_key(groups: tuple[ReconciliationGroup, ...]) -> tuple[int, str, str]:
    primary = sorted(groups, key=_group_sort_key)[0]
    return _group_sort_key(primary)


def _family_tag_html(groups: Sequence[ReconciliationGroup]) -> str:
    tags = _compact_text_values(group.tag_or_class for group in groups)
    pieces = [
        "1 seed group" if len(groups) == 1 else f"{len(groups)} seed groups",
    ]
    if tags:
        pieces.append("class=" + "/".join(tags))
    return f'<span class="family-meta">{_escape(" · ".join(pieces))}</span>'


def _family_detail_summary(
    groups: Sequence[ReconciliationGroup],
    *,
    representatives_by_group: Mapping[tuple[str, str], tuple[RepresentativeCell, ...]],
    shadow_policy_cells: Sequence[ShadowPolicyCell] = (),
) -> str:
    representative_count = sum(
        len(
            representatives_by_group.get(
                (group.feature_family_id, group.seed_group_id),
                (),
            ),
        )
        for group in groups
    )
    seed_label = "1 seed" if len(groups) == 1 else f"{len(groups)} seeds"
    rep_label = "1 rep" if representative_count == 1 else f"{representative_count} reps"
    shadow = _shadow_policy_compact_summary(shadow_policy_cells)
    if shadow:
        return f"{seed_label} · {rep_label} · {shadow}"
    return f"{seed_label} · {rep_label}"


def _top_issue_html(group: ReconciliationGroup) -> str:
    support = text_value(group.top_support_component)
    blocker = text_value(group.top_blocker)
    missing = "; ".join((*group.missing_evidence, *group.source_warnings))
    detail = blocker or missing or support or group.top_product_reason or "no top issue"
    detail_class = (
        "blocker" if blocker or missing else "support" if support else "context"
    )
    return (
        '<div class="top-issue">'
        f"{_badge(group.reconciliation_class)}"
        f'<span class="issue-text {detail_class}" '
        f'title="{_escape_attr(_compact_issue_label(detail))}">'
        f"{_escape(_compact_issue_label(detail))}</span>"
        "</div>"
    )


def _family_table_row(
    priority: int,
    family_groups: Sequence[ReconciliationGroup],
    *,
    representatives_by_group: Mapping[tuple[str, str], tuple[RepresentativeCell, ...]],
    shadow_policy_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowPolicyCell, ...],
    ],
    shadow_policy_cells_by_family: Mapping[str, tuple[ShadowPolicyCell, ...]],
    shadow_projection_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowProjectionCell, ...],
    ],
    shadow_projection_cells_by_family: Mapping[
        str,
        tuple[ShadowProjectionCell, ...],
    ],
    target_benchmark_contexts: Sequence[TargetBenchmarkContext],
    html_path: Path,
    input_artifacts: object,
) -> list[str]:
    ordered_groups = tuple(sorted(family_groups, key=_group_sort_key))
    group = ordered_groups[0]
    family_projection_cells = _shadow_projection_cells_for_family_groups(
        ordered_groups,
        shadow_projection_cells_by_group=shadow_projection_cells_by_group,
        shadow_projection_cells_by_family=shadow_projection_cells_by_family,
    )
    classes = " ".join(
        _ordered_unique(row.reconciliation_class for row in ordered_groups),
    )
    categories = " ".join(
        _family_filter_categories(ordered_groups, family_projection_cells),
    )
    search_blob = _escape_attr(
        _family_search_blob(
            ordered_groups,
            target_benchmark_contexts,
            family_projection_cells,
        ),
    )
    row = [
        (
            '<tr class="family-section-row" data-family-row '
            f'data-family="{_escape_attr(group.feature_family_id)}" '
            f'data-class="{_escape_attr(classes)}" '
            f'data-category="{_escape_attr(categories)}" '
            f'data-search="{search_blob}">'
        ),
        f'<td class="cell-priority" data-label="rank">{priority}</td>',
        (
            '<th class="cell-family family-context-cell" scope="row" '
            'colspan="4" data-label="family / hypothesis">'
            f'<span class="family-id">{_escape(group.feature_family_id)}</span>'
            f"{_family_tag_html(ordered_groups)}"
            f"{_family_anchor_summary_html(ordered_groups)}"
            f"{_family_target_summary_html(target_benchmark_contexts)}"
            f"{_family_pattern_status_html(ordered_groups)}"
            "</th>"
        ),
        (
            '<td class="cell-overlay" data-label="overlay">'
            f"{_family_pattern_link_html(ordered_groups, html_path, input_artifacts)}"
            "</td>"
        ),
        '<td class="cell-details" data-label="chain">',
        "</td>",
        "</tr>",
    ]
    href_counts, first_index_by_href = _overlay_href_context(ordered_groups, html_path)
    if _consolidated_seed_alias_family(ordered_groups):
        row.extend(
            _consolidated_seed_alias_rows(
                priority,
                ordered_groups,
                representatives_by_group=representatives_by_group,
                shadow_policy_cells_by_group=shadow_policy_cells_by_group,
                shadow_policy_cells_by_family=shadow_policy_cells_by_family,
                shadow_projection_cells_by_group=shadow_projection_cells_by_group,
                shadow_projection_cells_by_family=shadow_projection_cells_by_family,
                target_benchmark_contexts=target_benchmark_contexts,
                html_path=html_path,
                input_artifacts=input_artifacts,
                href_counts=href_counts,
                first_index_by_href=first_index_by_href,
            )
        )
        return row
    for index, seed_group in enumerate(ordered_groups, start=1):
        row.extend(
            _seed_decision_rows(
                priority,
                index,
                seed_group,
                representatives_by_group=representatives_by_group,
                shadow_policy_cells_by_group=shadow_policy_cells_by_group,
                shadow_policy_cells_by_family=shadow_policy_cells_by_family,
                shadow_projection_cells_by_group=shadow_projection_cells_by_group,
                shadow_projection_cells_by_family=shadow_projection_cells_by_family,
                target_benchmark_contexts=target_benchmark_contexts,
                html_path=html_path,
                input_artifacts=input_artifacts,
                href_counts=href_counts,
                first_index_by_href=first_index_by_href,
                total_seed_groups=len(ordered_groups),
            )
        )
    return row


def _consolidated_seed_alias_rows(
    priority: int,
    groups: Sequence[ReconciliationGroup],
    *,
    representatives_by_group: Mapping[tuple[str, str], tuple[RepresentativeCell, ...]],
    shadow_policy_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowPolicyCell, ...],
    ],
    shadow_policy_cells_by_family: Mapping[str, tuple[ShadowPolicyCell, ...]],
    shadow_projection_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowProjectionCell, ...],
    ],
    shadow_projection_cells_by_family: Mapping[
        str,
        tuple[ShadowProjectionCell, ...],
    ],
    target_benchmark_contexts: Sequence[TargetBenchmarkContext],
    html_path: Path,
    input_artifacts: object,
    href_counts: Mapping[str, int],
    first_index_by_href: Mapping[str, int],
) -> list[str]:
    base = groups[0]
    detail_id = _detail_row_id(f"{base.feature_family_id}-hypothesis", priority)
    shadow_cells = _shadow_policy_cells_for_family_groups(
        groups,
        shadow_policy_cells_by_group=shadow_policy_cells_by_group,
        shadow_policy_cells_by_family=shadow_policy_cells_by_family,
    )
    projection_cells = _shadow_projection_cells_for_family_groups(
        groups,
        shadow_projection_cells_by_group=shadow_projection_cells_by_group,
        shadow_projection_cells_by_family=shadow_projection_cells_by_family,
    )
    representatives = _representatives_for_groups(groups, representatives_by_group)
    category = " ".join(_family_filter_categories(groups, projection_cells))
    search_blob = _family_search_blob(groups, (), projection_cells)
    return [
        (
            '<tr class="seed-decision-row consolidated-seed-row" '
            f'data-family-section="{_escape_attr(base.feature_family_id)}" '
            f'data-class="{_escape_attr(base.reconciliation_class)}" '
            f'data-category="{_escape_attr(category)}" '
            f'data-detail-row="{_escape_attr(detail_id)}" '
            f'data-search="{_escape_attr(search_blob)}">'
        ),
        (
            '<td class="cell-priority seed-rank" data-label="rank">'
            f"{priority}.1</td>"
        ),
        (
            '<th class="cell-family seed-cell" scope="row" '
            'data-label="family / hypothesis">'
            f'<span class="seed-summary">m/z {_escape(_seed_mz_range(groups))} '
            f'· RT {_escape(_seed_rt_range(groups))}</span>'
            f'<span class="seed-window">window '
            f'{_escape(_seed_window_range(groups))}</span>'
            "</th>"
        ),
        (
            '<td class="cell-state" data-label="state">'
            f"{_state_html_for_shadow(base, shadow_cells, projection_cells)}</td>"
        ),
        (
            '<td class="cell-issue" data-label="issue">'
            f"{_top_issue_html(base)}</td>"
        ),
        (
            '<td class="cell-counts" data-label="impact">'
            f"{_consolidated_counts_html(groups, projection_cells)}"
            "</td>"
        ),
        (
            '<td class="cell-overlay" data-label="overlay">'
            + _consolidated_overlay_cell_html(
                groups,
                html_path=html_path,
                input_artifacts=input_artifacts,
                href_counts=href_counts,
                first_index_by_href=first_index_by_href,
            )
            + "</td>"
        ),
        '<td class="cell-details" data-label="chain">',
        (
            '<button type="button" class="detail-toggle" '
            'aria-expanded="false" '
            f'aria-controls="{_escape_attr(detail_id)}" '
            f'data-detail-toggle="{_escape_attr(detail_id)}">Open</button>'
        ),
        "</td>",
        "</tr>",
        (
            f'<tr class="detail-row" id="{_escape_attr(detail_id)}" '
            f'data-family-section="{_escape_attr(base.feature_family_id)}" '
            f'data-detail-for="{_escape_attr(base.feature_family_id)}" hidden>'
        ),
        '<td colspan="7">',
        '<div class="detail-drawer">',
        '<div class="detail-drawer-head">',
        "<strong>Consolidated hypothesis evidence chain</strong>",
        (
            f"<span>{_escape(_seed_alias_count_label(len(groups)))}；"
            "seed aliases collapsed under one product hypothesis；"
            "這些 rows 不是獨立 peak decisions。</span>"
        ),
        "</div>",
        _consolidated_seed_alias_details_html(
            groups,
            representatives,
            shadow_policy_cells=shadow_cells,
            shadow_projection_cells=projection_cells,
            target_benchmark_contexts=target_benchmark_contexts,
            html_path=html_path,
            input_artifacts=input_artifacts,
        ),
        "</div>",
        "</td>",
        "</tr>",
    ]


def _consolidated_seed_alias_family(
    groups: Sequence[ReconciliationGroup],
) -> bool:
    if len(groups) <= 1:
        return False
    return any(
        "primary_family_consolidated"
        in split_semicolon_labels(group.family_evidence)
        for group in groups
    )


def _seed_alias_count_label(count: int) -> str:
    if count >= _HIGH_SEED_ALIAS_COUNT:
        return f"{count} seed aliases · high alias count"
    return f"{count} seed aliases"


def _representatives_for_groups(
    groups: Sequence[ReconciliationGroup],
    representatives_by_group: Mapping[tuple[str, str], tuple[RepresentativeCell, ...]],
) -> tuple[RepresentativeCell, ...]:
    representatives: list[RepresentativeCell] = []
    for group in groups:
        representatives.extend(
            representatives_by_group.get(
                (group.feature_family_id, group.seed_group_id),
                (),
            ),
        )
    return tuple(sorted(representatives, key=_representative_sort_key))


def _consolidated_overlay_cell_html(
    groups: Sequence[ReconciliationGroup],
    *,
    html_path: Path,
    input_artifacts: object,
    href_counts: Mapping[str, int],
    first_index_by_href: Mapping[str, int],
) -> str:
    del href_counts, first_index_by_href
    unique_groups = []
    seen_hrefs: set[str] = set()
    for group in groups:
        href = _href_for_path(group.overlay_png_path, html_path)
        if not href or href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        unique_groups.append(group)
    if not unique_groups:
        if any(_overlay_not_required_by_gate(group) for group in groups):
            return _overlay_not_required_status_html("no consolidated overlay")
        return _missing_overlay_status_html(
            input_artifacts,
            "no consolidated overlay",
        )
    hypothesis_link = _hypothesis_overlay_link_html(unique_groups[0], html_path)
    if hypothesis_link:
        return hypothesis_link
    link = _overlay_link_html(
        unique_groups[0],
        html_path,
        label="family context",
        caption=(
            f"{unique_groups[0].feature_family_id} | consolidated MS1 "
            "family context"
        ),
    )
    if not link:
        return _missing_overlay_status_html(
            input_artifacts,
            "no consolidated overlay",
        )
    if len(unique_groups) <= 1:
        return link
    return (
        f"{link}<br>"
        '<span class="overlay-scope" '
        'title="Alias-level PNGs share the same family MS1 context; '
        'open details for every alias path.">'
        f"{len(unique_groups)} alias overlays share the same MS1 family context"
        "</span>"
    )


def _consolidated_seed_alias_details_html(
    groups: Sequence[ReconciliationGroup],
    representatives: Sequence[RepresentativeCell],
    *,
    shadow_policy_cells: Sequence[ShadowPolicyCell],
    shadow_projection_cells: Sequence[ShadowProjectionCell],
    target_benchmark_contexts: Sequence[TargetBenchmarkContext],
    html_path: Path,
    input_artifacts: object,
) -> str:
    base = groups[0]
    return (
        _consolidated_review_answer_html(groups, input_artifacts, html_path)
        +
        '<p class="chain-note">'
        "seed aliases collapsed under one product hypothesis because the "
        "alignment review marked this family as primary_family_consolidated. "
        "The aliases remain below as provenance; they are not separate peak "
        "decisions.</p>"
        + _seed_alias_table_html(groups, html_path, input_artifacts)
        + _projection_accept_cells_html(groups, shadow_projection_cells, html_path)
        + _details_html(
            base,
            representatives,
            shadow_policy_cells=shadow_policy_cells,
            shadow_projection_cells=shadow_projection_cells,
            target_benchmark_contexts=target_benchmark_contexts,
            html_path=html_path,
            input_artifacts=input_artifacts,
            include_seed_context=False,
        )
    )


def _consolidated_review_answer_html(
    groups: Sequence[ReconciliationGroup],
    input_artifacts: object,
    html_path: Path,
) -> str:
    base = groups[0]
    overlay_text = _consolidated_overlay_readout(groups, input_artifacts, html_path)
    alias_warning = (
        "<p>High alias count: review the consolidated overlay and seed table before "
        "treating this as a simple one-seed backfill.</p>"
        if len(groups) >= _HIGH_SEED_ALIAS_COUNT
        else ""
    )
    return (
        '<section class="review-answer">'
        "<strong>Reviewer readout</strong>"
        "<p>"
        "This row is one consolidated MS1 hypothesis, not "
        f"{len(groups)} independent peak decisions. "
        "Product state is "
        f"{_escape(_compact_issue_label(base.product_behavior_state))}; "
        "evidence state is "
        f"{_escape(_compact_issue_label(base.evidence_authority_state))}. "
        f"Overlay status: {_escape(overlay_text)}."
        "</p>"
        f"{alias_warning}"
        "</section>"
    )


def _consolidated_overlay_readout(
    groups: Sequence[ReconciliationGroup],
    input_artifacts: object,
    html_path: Path,
) -> str:
    if any(_hypothesis_overlay_path(group, html_path) for group in groups):
        return "hypothesis overlay available"
    if any(_href_for_path(group.overlay_png_path, html_path) for group in groups):
        return "family context available, hypothesis overlay not generated"
    return _missing_overlay_reason_text(input_artifacts)


def _projection_accept_cells_html(
    groups: Sequence[ReconciliationGroup],
    cells: Sequence[ShadowProjectionCell],
    html_path: Path,
) -> str:
    accepted = tuple(
        cell
        for cell in sorted(cells, key=_shadow_projection_cell_sort_key)
        if _is_projected_new_accept(cell)
    )
    if not accepted:
        return ""
    groups_by_seed = {group.seed_group_id: group for group in groups}
    rows = "".join(
        "<tr>"
        f"<td>{_escape(cell.sample_stem)}</td>"
        "<td>"
        f"{_projection_accept_seed_hint_html(groups_by_seed.get(cell.seed_group_id))}"
        f"<code>{_escape(cell.seed_group_id)}</code>"
        "</td>"
        "<td>"
        f"{_projection_matrix_state_html(cell.current_matrix_written)}"
        " -> "
        f"{_projection_matrix_state_html(cell.projected_matrix_written)}"
        "</td>"
        "<td>"
        f"{_component_list_html(cell.shadow_reasons) or 'none'}"
        f"{_shadow_projection_warnings_html(cell.shadow_warnings)}"
        "</td>"
        f"<td>{_shadow_projection_evidence_html(cell)}</td>"
        f"<td>{_shadow_projection_overlay_link_html(cell, html_path)}</td>"
        "</tr>"
        for cell in accepted
    )
    return (
        '<section class="projection-accept-index">'
        "<h3>Projection write cells</h3>"
        '<p class="chain-note">'
        "Only blank candidate cells that shadow projection would "
        "turn into writes are listed here; projection_only, product output is "
        "unchanged.</p>"
        '<div class="seed-alias-table-wrap">'
        '<table class="seed-alias-table projection-accept-table">'
        "<thead><tr>"
        '<th scope="col">sample</th>'
        '<th scope="col">seed request</th>'
        '<th scope="col">decision</th>'
        '<th scope="col">reason / warning</th>'
        '<th scope="col">MS1 product rule / optional context</th>'
        '<th scope="col">overlay</th>'
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
        "</section>"
    )


def _projection_accept_seed_hint_html(group: ReconciliationGroup | None) -> str:
    if group is None:
        return '<span class="seed-summary">seed not matched in gallery row</span>'
    return (
        '<span class="seed-summary">'
        f"m/z {_escape(group.seed_mz or 'unknown')} · "
        f"RT {_escape(group.seed_rt or 'unknown')} · "
        f"window {_escape(group.seed_rt_window or 'unknown')}"
        "</span>"
    )


def _seed_alias_table_html(
    groups: Sequence[ReconciliationGroup],
    html_path: Path,
    input_artifacts: object,
) -> str:
    href_counts, first_index_by_href = _overlay_href_context(groups, html_path)
    rows = "".join(
        "<tr>"
        f"<td>alias {index}</td>"
        f"<td>{_escape(group.seed_mz or 'unknown')}</td>"
        f"<td>{_escape(group.seed_rt or 'unknown')}</td>"
        f"<td>{_escape(group.seed_rt_window or 'unknown')}</td>"
        f"<td>{_escape(_compact_issue_label(_seed_issue_text(group)))}</td>"
        "<td>"
        + _seed_overlay_cell_html(
            group,
            seed_index=index,
            html_path=html_path,
            href_counts=href_counts,
            first_index_by_href=first_index_by_href,
            total_seed_groups=len(groups),
            input_artifacts=input_artifacts,
        )
        + "</td>"
        f'<td><code>{_escape(group.seed_group_id)}</code></td>'
        "</tr>"
        for index, group in enumerate(groups, start=1)
    )
    return (
        '<div class="seed-alias-table-wrap">'
        '<table class="seed-alias-table">'
        "<thead><tr>"
        '<th scope="col">alias</th>'
        '<th scope="col">m/z</th>'
        '<th scope="col">RT</th>'
        '<th scope="col">window</th>'
        '<th scope="col">issue</th>'
        '<th scope="col">overlay</th>'
        '<th scope="col">seed request</th>'
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _seed_decision_rows(
    priority: int,
    seed_index: int,
    group: ReconciliationGroup,
    *,
    representatives_by_group: Mapping[tuple[str, str], tuple[RepresentativeCell, ...]],
    shadow_policy_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowPolicyCell, ...],
    ],
    shadow_policy_cells_by_family: Mapping[str, tuple[ShadowPolicyCell, ...]],
    shadow_projection_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowProjectionCell, ...],
    ],
    shadow_projection_cells_by_family: Mapping[
        str,
        tuple[ShadowProjectionCell, ...],
    ],
    target_benchmark_contexts: Sequence[TargetBenchmarkContext],
    html_path: Path,
    input_artifacts: object,
    href_counts: Mapping[str, int],
    first_index_by_href: Mapping[str, int],
    total_seed_groups: int,
) -> list[str]:
    detail_id = _detail_row_id(
        f"{group.feature_family_id}-seed-{seed_index}",
        priority,
    )
    shadow_cells = _shadow_policy_cells_for_group(
        group,
        shadow_policy_cells_by_group=shadow_policy_cells_by_group,
        shadow_policy_cells_by_family=shadow_policy_cells_by_family,
        allow_family_fallback=False,
    )
    projection_cells = _shadow_projection_cells_for_group(
        group,
        shadow_projection_cells_by_group=shadow_projection_cells_by_group,
        shadow_projection_cells_by_family=shadow_projection_cells_by_family,
        allow_family_fallback=False,
    )
    representatives = representatives_by_group.get(
        (group.feature_family_id, group.seed_group_id),
        (),
    )
    category = " ".join(
        _ordered_unique(
            (
                *_group_filter_categories(group),
                *_shadow_projection_filter_categories(projection_cells),
            ),
        ),
    )
    return [
        (
            '<tr class="seed-decision-row" data-family-section="'
            f'{_escape_attr(group.feature_family_id)}" '
            f'data-class="{_escape_attr(group.reconciliation_class)}" '
            f'data-category="{_escape_attr(category)}" '
            f'data-detail-row="{_escape_attr(detail_id)}" '
            f'data-search="{_escape_attr(_search_blob(group, projection_cells))}">'
        ),
        (
            '<td class="cell-priority seed-rank" data-label="rank">'
            f"{priority}.{seed_index}</td>"
        ),
        (
            '<th class="cell-family seed-cell" scope="row" '
            'data-label="family / hypothesis">'
            f'<span class="seed-summary">m/z {_escape(group.seed_mz or "unknown")} '
            f'· RT {_escape(group.seed_rt or "unknown")}</span>'
            f'<span class="seed-window">window '
            f'{_escape(group.seed_rt_window or "unknown")}</span>'
            "</th>"
        ),
        (
            '<td class="cell-state" data-label="state">'
            f"{_state_html_for_shadow(group, shadow_cells, projection_cells)}</td>"
        ),
        (
            '<td class="cell-issue" data-label="issue">'
            f"{_top_issue_html(group)}</td>"
        ),
        (
            '<td class="cell-counts" data-label="impact">'
            f"{_counts_html(group, projection_cells)}"
            "</td>"
        ),
        (
            '<td class="cell-overlay" data-label="overlay">'
            + _seed_overlay_cell_html(
                group,
                seed_index=seed_index,
                html_path=html_path,
                href_counts=href_counts,
                first_index_by_href=first_index_by_href,
                total_seed_groups=total_seed_groups,
                input_artifacts=input_artifacts,
            )
            + "</td>"
        ),
        '<td class="cell-details" data-label="chain">',
        (
            '<button type="button" class="detail-toggle" '
            'aria-expanded="false" '
            f'aria-controls="{_escape_attr(detail_id)}" '
            f'data-detail-toggle="{_escape_attr(detail_id)}">Open</button>'
        ),
        "</td>",
        "</tr>",
        (
            f'<tr class="detail-row" id="{_escape_attr(detail_id)}" '
            f'data-family-section="{_escape_attr(group.feature_family_id)}" '
            f'data-detail-for="{_escape_attr(group.seed_group_id)}" hidden>'
        ),
        '<td colspan="7">',
        '<div class="detail-drawer">',
        '<div class="detail-drawer-head">',
        '<strong>Hypothesis evidence chain</strong>',
        (
            '<span>Family 是 pattern context；'
            "這裡才是 hypothesis 的 support/blocker，"
            "seed 只作為 request provenance。</span>"
        ),
        "</div>",
        _details_html(
            group,
            representatives,
            shadow_policy_cells=shadow_cells,
            shadow_projection_cells=projection_cells,
            target_benchmark_contexts=target_benchmark_contexts,
            html_path=html_path,
            input_artifacts=input_artifacts,
        ),
        "</div>",
        "</td>",
        "</tr>",
    ]


def _detail_row_id(family_id: str, priority: int) -> str:
    token = re.sub(r"[^a-zA-Z0-9_-]+", "-", family_id).strip("-").lower()
    return f"family-detail-{priority}-{token or 'item'}"


def _seed_table_row_html(
    index: int,
    group: ReconciliationGroup,
    *,
    html_path: Path,
    href_counts: Mapping[str, int],
    first_index_by_href: Mapping[str, int],
    input_artifacts: object,
) -> str:
    overlay_html = _seed_overlay_cell_html(
        group,
        seed_index=index,
        html_path=html_path,
        href_counts=href_counts,
        first_index_by_href=first_index_by_href,
        total_seed_groups=max(len(first_index_by_href), 1),
        input_artifacts=input_artifacts,
    )
    return (
        "<tr>"
        f'<td><span title="{_escape_attr(group.seed_group_id)}">'
        f"seed {index}</span></td>"
        f"<td>{_escape(group.seed_mz)}</td>"
        f"<td>{_escape(group.seed_rt)} · {_escape(group.seed_rt_window)}</td>"
        f"<td>{_badge(group.evidence_authority_state)}</td>"
        f"<td>{_badge(group.reconciliation_class)}</td>"
        f"<td>{_escape(_compact_issue_label(_seed_issue_text(group)))}</td>"
        f"<td>{overlay_html}</td>"
        "</tr>"
    )


def _family_details_html(
    groups: Sequence[ReconciliationGroup],
    *,
    representatives_by_group: Mapping[tuple[str, str], tuple[RepresentativeCell, ...]],
    shadow_policy_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowPolicyCell, ...],
    ],
    shadow_policy_cells_by_family: Mapping[str, tuple[ShadowPolicyCell, ...]],
    html_path: Path,
    input_artifacts: object,
) -> str:
    if len(groups) == 1:
        group = groups[0]
        shadow_cells = _shadow_policy_cells_for_group(
            group,
            shadow_policy_cells_by_group=shadow_policy_cells_by_group,
            shadow_policy_cells_by_family=shadow_policy_cells_by_family,
            allow_family_fallback=True,
        )
        return (
            '<div class="family-details single-seed">'
            + _details_html(
                group,
                representatives_by_group.get(
                    (group.feature_family_id, group.seed_group_id),
                    (),
                ),
                shadow_policy_cells=shadow_cells,
                html_path=html_path,
                input_artifacts=input_artifacts,
                include_seed_context=False,
            )
            + "</div>"
        )
    href_counts, first_index_by_href = _overlay_href_context(groups, html_path)
    seed_rows = "".join(
        _seed_table_row_html(
            index,
            group,
            html_path=html_path,
            href_counts=href_counts,
            first_index_by_href=first_index_by_href,
            input_artifacts=input_artifacts,
        )
        for index, group in enumerate(groups, start=1)
    )
    seed_details = "".join(
        '<details class="seed-subdetails">'
        f'<summary title="{_escape_attr(group.seed_group_id)}">'
        f"{_escape(_seed_detail_summary(group, index))}</summary>"
        + _details_html(
            group,
            representatives_by_group.get(
                (group.feature_family_id, group.seed_group_id),
                (),
            ),
            shadow_policy_cells=_shadow_policy_cells_for_group(
                group,
                shadow_policy_cells_by_group=shadow_policy_cells_by_group,
                shadow_policy_cells_by_family=shadow_policy_cells_by_family,
                allow_family_fallback=False,
            ),
            html_path=html_path,
            input_artifacts=input_artifacts,
        )
        + "</details>"
        for index, group in enumerate(groups, start=1)
    )
    return (
        '<div class="family-details">'
        '<div class="seed-table-wrap">'
        '<table class="seed-table">'
        "<thead><tr>"
        '<th scope="col">seed</th>'
        '<th scope="col">mz</th>'
        '<th scope="col">rt / window</th>'
        '<th scope="col">evidence state</th>'
        '<th scope="col">class</th>'
        '<th scope="col">main issue</th>'
        '<th scope="col">overlay</th>'
        "</tr></thead>"
        f"<tbody>{seed_rows}</tbody>"
        "</table>"
        "</div>"
        f"{seed_details}"
        "</div>"
    )


def _seed_issue_text(group: ReconciliationGroup) -> str:
    return (
        group.top_blocker
        or ";".join(group.missing_evidence)
        or group.top_support_component
        or group.reconciliation_class
    )


def _seed_detail_summary(group: ReconciliationGroup, index: int) -> str:
    issue = _compact_issue_label(_seed_issue_text(group))
    return f"H{index} · RT {group.seed_rt or 'unknown'} · {issue}"
