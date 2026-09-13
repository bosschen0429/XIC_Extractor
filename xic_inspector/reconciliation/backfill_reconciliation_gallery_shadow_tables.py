"""Shadow policy and projection table rendering for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from xic_extractor.diagnostics.diagnostic_io import split_semicolon_labels, text_value

from .backfill_reconciliation_gallery_chain_html import (
    _component_list_html,
)
from .backfill_reconciliation_gallery_html import (
    badge as _badge,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_indices import (
    _shadow_policy_cell_sort_key,
    _shadow_policy_compact_summary,
    _shadow_projection_cell_sort_key,
    _shadow_projection_compact_summary,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    ShadowPolicyCell,
    ShadowProjectionCell,
    _ordered_unique,
)
from .backfill_reconciliation_gallery_overlay_links import (
    _shadow_policy_overlay_link_html,
    _shadow_projection_overlay_link_html,
)
from .backfill_reconciliation_gallery_review_modes import (
    _is_cid_nl_successor_review_group,
)
from .backfill_reconciliation_gallery_state import (
    _projection_matrix_state_html,
)


def _shadow_policy_summary_note_html(
    cells: Sequence[ShadowPolicyCell],
) -> str:
    summary = _shadow_policy_compact_summary(cells)
    if not summary:
        return ""
    return f'<p class="chain-note">shadow policy: {_escape(summary)}</p>'


def _cell_impact_legend_note_html(
    group: ReconciliationGroup,
    shadow_projection_cells: Sequence[ShadowProjectionCell],
) -> str:
    if shadow_projection_cells:
        return (
            '<p class="chain-note">'
            "Current=目前 production snapshot 已寫入的 cells；"
            "Review=只有 review/candidate context，尚未寫入 matrix；"
            "Write=這輪 projection/activation 可寫入的新 cells；"
            "Block=hard blockers。Context/review-only 不會直接改 matrix。</p>"
        )
    if _is_cid_nl_successor_review_group(group):
        return (
            '<p class="chain-note">Write=successor adoption candidate writes；'
            "Preserve=successor baseline already has a detected value, so no "
            "Backfill write；Omit=no safe successor write target。"
            "These are authority-review counts, not NL-tag coverage and not "
            "active matrix writes.</p>"
        )
    return (
        '<p class="chain-note">NL=detected required-tag anchors；'
        "Candidate-only=目前 hypothesis 的候選 cells，不代表已寫入 matrix；"
        "Dup=family duplicate-assigned cell context；"
        "Review=仍需人工判斷的 provisional cells。</p>"
    )


def _shadow_projection_summary_note_html(
    cells: Sequence[ShadowProjectionCell],
) -> str:
    summary = _shadow_projection_compact_summary(cells)
    if not summary:
        return ""
    return f'<p class="chain-note">shadow projection: {_escape(summary)}</p>'


def _shadow_policy_cells_html(
    cells: Sequence[ShadowPolicyCell],
    html_path: Path,
    *,
    legacy_reference: bool = False,
) -> str:
    if not cells:
        return (
            '<p class="chain-note">'
            "No shadow policy cell rows supplied for this seed group.</p>"
        )
    rows = "".join(
        "<tr>"
        f"<td>{_escape(cell.sample_stem)}</td>"
        f"<td>{_badge(cell.current_product_cell_state)}</td>"
        f"<td>{_badge(cell.shadow_policy_decision)}</td>"
        "<td>"
        f"{_escape(cell.decision_reason or 'no reason supplied')}"
        f"{_shadow_policy_gap_html(cell.production_gap)}"
        "</td>"
        "<td>"
        f"{_escape(_shadow_metric_text(cell))}"
        "</td>"
        "<td>"
        f"{_shadow_policy_evidence_html(cell)}"
        "</td>"
        f"<td>{_shadow_policy_overlay_link_html(cell, html_path)}</td>"
        "</tr>"
        for cell in sorted(cells, key=_shadow_policy_cell_sort_key)
    )
    return (
        '<p class="chain-note">'
        f"{_shadow_policy_intro_text(legacy_reference)}</p>"
        '<div class="shadow-policy-table-wrap">'
        '<table class="shadow-policy-table">'
        "<thead><tr>"
        '<th scope="col">sample</th>'
        '<th scope="col">current</th>'
        '<th scope="col">shadow decision</th>'
        '<th scope="col">reason / gap</th>'
        '<th scope="col">own-max evidence</th>'
        '<th scope="col">support / blockers</th>'
        '<th scope="col">overlay</th>'
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _shadow_policy_intro_text(legacy_reference: bool) -> str:
    if legacy_reference:
        return (
            "legacy_reference_only；這裡保留舊 MS1 own-max + RT shadow policy "
            "作為比較來源。若與 Shadow production projection 不一致，"
            "以 projection table 追 current production decision / projected "
            "decision sidecar。"
        )
    return (
        "diagnostic_only；這裡只描述 MS1 own-max + RT shadow policy "
        "會如何解讀既有 candidate cells，不會修改 product output。"
    )


def _shadow_projection_cells_html(
    cells: Sequence[ShadowProjectionCell],
    html_path: Path,
) -> str:
    if not cells:
        return (
            '<p class="chain-note">'
            "No shadow production projection rows supplied for this seed group.</p>"
        )
    rows = "".join(
        "<tr>"
        f"<td>{_escape(cell.sample_stem)}</td>"
        f"<td>{_projection_matrix_state_html(cell.current_matrix_written)}</td>"
        f"<td>{_badge(cell.current_production_status)}</td>"
        f"<td>{_badge(cell.shadow_decision)}</td>"
        f"<td>{_projection_matrix_state_html(cell.projected_matrix_written)}</td>"
        "<td>"
        f"{_component_list_html(cell.shadow_reasons) or 'none'}"
        f"{_shadow_projection_warnings_html(cell.shadow_warnings)}"
        "</td>"
        "<td>"
        f"{_shadow_projection_evidence_html(cell)}"
        "</td>"
        f"<td>{_shadow_projection_overlay_link_html(cell, html_path)}</td>"
        "</tr>"
        for cell in sorted(cells, key=_shadow_projection_cell_sort_key)
    )
    return (
        '<p class="chain-note">'
        "shadow_projection_only；這裡顯示 current production decision snapshot "
        "與 projected decision 的差異；alignment_matrix.tsv 目前只做來源 hash，"
        "仍不會直接修改 product output。</p>"
        '<div class="shadow-policy-table-wrap">'
        '<table class="shadow-policy-table shadow-projection-table">'
        "<thead><tr>"
        '<th scope="col">sample</th>'
        '<th scope="col">current decision</th>'
        '<th scope="col">current state</th>'
        '<th scope="col">shadow decision</th>'
        '<th scope="col">projected decision</th>'
        '<th scope="col">reasons / warnings</th>'
        '<th scope="col">evidence</th>'
        '<th scope="col">overlay</th>'
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _shadow_projection_warnings_html(warnings: Sequence[str]) -> str:
    if not warnings:
        return ""
    return (
        '<div class="warning-list"><span>warnings</span>'
        f"{_component_list_html(warnings)}</div>"
    )


def _shadow_projection_metric_text(cell: ShadowProjectionCell) -> str:
    parts = []
    if cell.detected_anchor_count:
        parts.append(f"anchors={cell.detected_anchor_count}")
    if cell.request_window_overlap:
        parts.append(f"request window={cell.request_window_overlap}")
    if cell.local_global_ratio:
        parts.append(f"local/global={cell.local_global_ratio}")
    if cell.evidence_gate_status:
        parts.append(f"gate={cell.evidence_gate_status}")
    return " · ".join(parts) or "not supplied"


def _shadow_projection_evidence_html(cell: ShadowProjectionCell) -> str:
    metric = _escape(_shadow_projection_metric_text(cell))
    if not cell.product_authority_chain:
        return metric
    return (
        metric
        + '<div class="projection-authority-chain">'
        + "<span>MS1 product rule / optional context chain</span> "
        + _escape(cell.product_authority_chain)
        + "</div>"
    )


def _shadow_policy_gap_html(value: str) -> str:
    gap = text_value(value)
    if not gap:
        return ""
    return f'<br><span class="gap-label">gap</span> {_badge(gap)}'


def _shadow_metric_text(cell: ShadowPolicyCell) -> str:
    parts = []
    if cell.own_max_shape_supported_fraction:
        parts.append(f"own-max={cell.own_max_shape_supported_fraction}")
    if cell.absolute_trace_apex_cluster_fraction:
        parts.append(f"apex cluster={cell.absolute_trace_apex_cluster_fraction}")
    if cell.evidence_gate_status:
        parts.append(f"gate={cell.evidence_gate_status}")
    return " · ".join(parts) or "not supplied"


def _shadow_policy_evidence_html(cell: ShadowPolicyCell) -> str:
    items = _ordered_unique(
        (
            *split_semicolon_labels(cell.support_components),
            *split_semicolon_labels(cell.blockers),
            *split_semicolon_labels(cell.missing_evidence),
            cell.overlay_family_verdict,
        ),
    )
    return _component_list_html(items) or "none"
