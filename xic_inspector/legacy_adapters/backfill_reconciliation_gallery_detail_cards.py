"""Detail-card HTML helpers for the reconciliation gallery."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .backfill_reconciliation_gallery_chain_html import (
    _compact_issue_label,
    _component_summary_text,
)
from .backfill_reconciliation_gallery_counts import (
    _counts_html,
)
from .backfill_reconciliation_gallery_html import (
    badge as _badge,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    RepresentativeCell,
    ShadowPolicyCell,
    ShadowProjectionCell,
    _ordered_unique,
)
from .backfill_reconciliation_gallery_overlay_links import (
    _hypothesis_overlay_link_html,
    _missing_overlay_reason_text,
    _overlay_link_html,
)
from .backfill_reconciliation_gallery_review_modes import (
    _is_cid_nl_differential_review_group,
    _is_cid_nl_successor_review_group,
)
from .backfill_reconciliation_gallery_shadow_tables import (
    _cell_impact_legend_note_html,
    _shadow_policy_summary_note_html,
    _shadow_projection_summary_note_html,
)


def _detail_summary_html(
    group: ReconciliationGroup,
    representatives: Sequence[RepresentativeCell],
    *,
    html_path: Path,
    input_artifacts: object,
    shadow_policy_cells: Sequence[ShadowPolicyCell],
    shadow_projection_cells: Sequence[ShadowProjectionCell],
) -> str:
    support_summary = _escape(
        _component_summary_text(_support_summary_items(group), "none"),
    )
    blocker_summary = _escape(
        _component_summary_text(_blocker_summary_items(group), "none"),
    )
    return (
        '<div class="detail-summary-grid" aria-label="hypothesis summary">'
        + _detail_summary_card_html(
            "decision",
            "current product / evidence state",
            (
                '<div class="summary-line"><span>Product</span>'
                f"{_badge(group.product_behavior_state)}</div>"
                '<div class="summary-line"><span>Evidence</span>'
                f"{_badge(group.evidence_authority_state)}</div>"
            ),
        )
        + _detail_summary_card_html(
            "reason",
            "support / blocker",
            (
                f"<p><strong>support</strong> {support_summary}</p>"
                f"<p><strong>blocker</strong> {blocker_summary}</p>"
            ),
        )
        + _detail_summary_card_html(
            "visual evidence",
            _visual_summary_subtitle(group),
            _detail_visual_summary_html(group, html_path, input_artifacts),
        )
        + _detail_summary_card_html(
            "cell impact",
            f"{len(representatives)} representative cells",
            (
                _counts_html(group, shadow_projection_cells)
                + _cell_impact_legend_note_html(group, shadow_projection_cells)
                + _shadow_projection_summary_note_html(shadow_projection_cells)
                + _shadow_policy_summary_note_html(shadow_policy_cells)
            ),
        )
        + _cid_nl_review_focus_card_html(group)
        + _cid_nl_discovery_identity_card_html(group, representatives)
        + "</div>"
    )


def _cid_nl_review_focus_card_html(group: ReconciliationGroup) -> str:
    if not _is_cid_nl_successor_review_group(group):
        return ""
    return _detail_summary_card_html(
        "Review focus",
        "separate feature inclusion from identity authority",
        (
            "<p><strong>Feature inclusion question</strong> "
            "Does CID-NL/MS2 evidence plus MS1 trace context support carrying "
            "this successor as an untargeted feature candidate?</p>"
            "<p><strong>Identity authority question</strong> "
            "Should source and successor be replaced, merged, deduped, or "
            "kept as co-existing features? Current answer: review only.</p>"
        ),
    )


def _cid_nl_discovery_identity_card_html(
    group: ReconciliationGroup,
    representatives: Sequence[RepresentativeCell],
) -> str:
    if not _is_cid_nl_successor_review_group(group):
        return ""
    return _detail_summary_card_html(
        "Feature / identity relationship",
        "source row -> successor hypothesis",
        (
            "<p><strong>Source/successor relationship</strong></p>"
            + _cid_nl_identity_transition_list_html(group, representatives)
            + "<p><strong>successor cell decisions</strong></p>"
            + _cid_nl_successor_decision_list_html(representatives)
        ),
    )


def _detail_summary_card_html(title: str, subtitle: str, body_html: str) -> str:
    return (
        '<section class="detail-summary-card">'
        f"<h3>{_escape(title)}</h3>"
        f'<p class="summary-subtitle">{_escape(subtitle)}</p>'
        f'<div class="summary-body">{body_html}</div>'
        "</section>"
    )


def _review_answer_html(
    group: ReconciliationGroup,
    html_path: Path,
    input_artifacts: object,
    *,
    shadow_projection_cells: Sequence[ShadowProjectionCell] = (),
) -> str:
    support = _component_summary_text(_support_summary_items(group), "none")
    blocker = _component_summary_text(_blocker_summary_items(group), "none")
    overlay = _hypothesis_overlay_link_html(group, html_path)
    family_context = _overlay_link_html(
        group,
        html_path,
        label="family context",
    )
    if overlay and _is_cid_nl_successor_review_group(group):
        overlay_text = "MS1 context PNG available; not NL-tag evidence"
    elif overlay:
        overlay_text = "hypothesis overlay available"
    elif family_context and _is_cid_nl_differential_review_group(group):
        overlay_text = "hypothesis differential overlay available"
    elif family_context:
        overlay_text = "family context only, hypothesis overlay not generated"
    else:
        overlay_text = _missing_overlay_reason_text(input_artifacts)
    decision_text = _review_answer_decision_text(group, shadow_projection_cells)
    return (
        '<section class="review-answer">'
        "<strong>Reviewer readout</strong>"
        f"<p>{_escape(decision_text)}</p>"
        '<p class="review-answer-meta">'
        f"Support: {_escape(support)}. "
        f"Blocker or missing item: {_escape(blocker)}. "
        f"Overlay status: {_escape(overlay_text)}."
        "</p>"
        "</section>"
    )


def _review_answer_decision_text(
    group: ReconciliationGroup,
    shadow_projection_cells: Sequence[ShadowProjectionCell],
) -> str:
    if _is_cid_nl_successor_review_group(group):
        return (
            "結論：CID-NL adoption review only；"
            f"Write {group.rescued_cell_count}、"
            f"Preserve {group.detected_cell_count}、"
            f"Omit {group.provisional_cell_count}。"
            "這不是 active default matrix，也不是 ProductWriter authority。"
        )
    if shadow_projection_cells:
        current = sum(cell.current_matrix_written for cell in shadow_projection_cells)
        review = sum(cell.review_rescued_cell for cell in shadow_projection_cells)
        write = sum(
            cell.shadow_decision == "accept" and cell.projected_matrix_written
            for cell in shadow_projection_cells
        )
        block = sum(cell.shadow_decision == "block" for cell in shadow_projection_cells)
        context = sum(
            cell.shadow_decision == "context" for cell in shadow_projection_cells
        )
        if write:
            return (
                f"結論：會寫入 {write} 個新 matrix value。"
                f"目前已寫入 {current} 個，review candidate {review} 個。"
            )
        if current:
            return (
                f"結論：目前 production snapshot 已寫入 {current} 個 value；"
                "這一列沒有新的 activation write。"
            )
        if block:
            return (
                f"結論：未寫入 matrix；{block} 個 cell 被 hard blocker 擋下。"
                f"Review candidate {review} 個。"
            )
        if context:
            return (
                f"結論：未寫入 matrix；{context} 個 cell 仍是 review-only "
                "candidate。這代表 MS1/visual evidence 支持同峰候選，但還沒有 "
                "product-authorized standard-peak same-peak chain。"
            )
    product = _compact_issue_label(group.product_behavior_state)
    evidence = _compact_issue_label(group.evidence_authority_state)
    return f"結論：Product={product}；Evidence={evidence}。"


def _support_summary_items(group: ReconciliationGroup) -> tuple[str, ...]:
    return tuple(
        _ordered_unique(
            (
                group.top_support_component,
                *group.product_grade_support_components,
                *group.review_only_visual_components,
            ),
        ),
    )


def _blocker_summary_items(group: ReconciliationGroup) -> tuple[str, ...]:
    return tuple(
        _ordered_unique(
            (
                group.top_blocker,
                *group.blocker_components,
                *group.missing_evidence,
                *group.source_warnings,
            ),
        ),
    )


def _visual_summary_subtitle(group: ReconciliationGroup) -> str:
    if _is_cid_nl_successor_review_group(group):
        return "MS1 feature context / identity review only"
    return "hypothesis evidence / family context"


def _detail_visual_summary_html(
    group: ReconciliationGroup,
    html_path: Path,
    input_artifacts: object,
) -> str:
    link = _hypothesis_overlay_link_html(group, html_path) or _overlay_link_html(
        group,
        html_path,
        label="family context",
    )
    link_html = (
        f'<p class="summary-link">{link}</p>'
        if link
        else (
            '<p class="chain-note">'
            f"{_escape(_missing_overlay_reason_text(input_artifacts))}.</p>"
        )
    )
    note_text = _component_summary_text(group.overlay_evidence_notes, "no metric notes")
    return (
        link_html
        + _anchor_review_context_html(group)
        + '<p class="chain-note">'
        + _escape(note_text)
        + "</p>"
    )


def _anchor_review_context_html(group: ReconciliationGroup) -> str:
    if _is_cid_nl_successor_review_group(group):
        return (
            '<p class="review-note">CID-NL feature review: Candidate/Existing/Omit '
            "counts come from the diagnostic decision packet. Overlay orange "
            "detected/rescued traces are MS1 trace status only; they do not prove "
            "NL-tag coverage, force source/successor replacement, or grant "
            "ProductWriter authority.</p>"
        )
    if group.seed_detected_anchor_count == 0:
        return (
            '<p class="review-note">No detected NL anchor on this hypothesis; '
            "treat as provenance/context, not a backfill candidate.</p>"
        )
    if group.seed_detected_anchor_count == 1:
        return (
            '<p class="review-note">Single detected NL anchor: visual evidence is '
            "review-only unless product-grade support closes the gap.</p>"
        )
    return (
        '<p class="review-note">'
        f"{_escape(group.seed_detected_anchor_count)} detected NL anchors on this "
        "hypothesis.</p>"
    )


def _cid_nl_identity_transition_list_html(
    group: ReconciliationGroup,
    representatives: Sequence[RepresentativeCell],
) -> str:
    counts = Counter(
        _identity_transition_text(cell)
        for cell in representatives
        if cell.source_peak_hypothesis_id or cell.successor_peak_hypothesis_id
    )
    if not counts:
        if any(
            item.startswith("target_guardrail:")
            for item in group.dependent_context_components
        ):
            return (
                '<p class="chain-note">'
                "Target benchmark context only: this verifies Discovery "
                "recovered or preserved the target family, not a Backfill "
                "old-to-successor write candidate.</p>"
            )
        return (
            '<p class="chain-note">'
            "No old-to-successor identity mapping supplied.</p>"
        )
    return _summary_count_list_html(counts)


def _cid_nl_successor_decision_list_html(
    representatives: Sequence[RepresentativeCell],
) -> str:
    counts = Counter(
        cell.successor_decision for cell in representatives if cell.successor_decision
    )
    if not counts:
        return '<p class="chain-note">No successor decision supplied.</p>'
    return _summary_count_list_html(counts)


def _summary_count_list_html(counts: Counter[str]) -> str:
    items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    limit = 6
    visible = items[:limit]
    extra = len(items) - limit
    html = (
        '<ul class="component-list identity-transition-list">'
        + "".join(
            f"<li>{_escape(label)} <span class=\"muted\">x{count}</span></li>"
            for label, count in visible
        )
    )
    if extra > 0:
        html += f'<li class="muted">+{extra} more</li>'
    return html + "</ul>"


def _identity_transition_text(cell: RepresentativeCell) -> str:
    if not (cell.source_peak_hypothesis_id or cell.successor_peak_hypothesis_id):
        return ""
    old_peak = cell.source_peak_hypothesis_id or "<unknown>"
    successor = cell.successor_peak_hypothesis_id or "<none>"
    return f"{old_peak} -> {successor}"


def _overlay_evidence_notes_html(notes: Sequence[str]) -> str:
    if not notes:
        return ""
    items = "".join(f"<li>{_escape(note)}</li>" for note in notes)
    return (
        '<div class="detail-block"><strong>overlay evidence metrics</strong>'
        f'<ul class="metric-list">{items}</ul></div>'
    )


def _representative_cells_table_html(
    representatives: Sequence[RepresentativeCell],
) -> str:
    rep_rows = "".join(
        "<tr>"
        f"<td>{_escape(';'.join(cell.representative_roles))}</td>"
        f"<td>{_escape(cell.sample_stem)}</td>"
        f"<td>{_escape(_compact_issue_label(cell.cell_status))}</td>"
        f"<td>{_escape(cell.scan_support_score)}</td>"
        f"<td>{_escape(cell.apex_delta_sec)}</td>"
        f"<td>{_escape(cell.representative_reason)}</td>"
        "</tr>"
        for cell in representatives
    )
    if not rep_rows:
        rep_rows = (
            '<tr><td colspan="6">沒有可安全選出的 representative cell。</td></tr>'
        )
    return (
        '<table class="rep-table">'
        "<thead><tr>"
        '<th scope="col">roles</th><th scope="col">sample</th>'
        '<th scope="col">status</th><th scope="col">scan support</th>'
        '<th scope="col">apex delta</th><th scope="col">reason</th>'
        "</tr></thead>"
        f"<tbody>{rep_rows}</tbody></table>"
    )
