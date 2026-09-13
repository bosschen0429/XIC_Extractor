"""Internal HTML writer for the opt-in Untargeted explanation prototype.

The report is a bounded consumer.  It reuses the Backfill reconciliation
gallery's presentation assets, but it never loads scientific artifacts,
reconstructs scientific facts, or writes Matrix values. The path-aware writer
rechecks navigation artifact bytes against owner-minted receipts.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from xic_extractor.alignment.evidence_claims import (
    EvidenceClaim,
    EvidenceClaimState,
)
from xic_extractor.alignment.untargeted_explanation_contract import (
    AlignmentExplanationPayload,
    BackfillExplanationPayload,
    DiscoveryExplanationPayload,
    ExplanationIndexEntry,
    ExplanationLocator,
    ExplanationLocatorKind,
    ExplanationSection,
    ExplanationTopic,
    MatrixExplanationPayload,
    UntargetedExplanationIndex,
)
from xic_extractor.processing_identity import fingerprint_file_content
from xic_inspector.reconciliation.backfill_reconciliation_gallery_assets import (
    gallery_css,
    lightbox_html,
    lightbox_script,
)
from xic_inspector.reconciliation.backfill_reconciliation_gallery_html import (
    escape_attr,
    escape_html,
)

_AVAILABLE_STATES = frozenset(
    {
        EvidenceClaimState.OWNER_REHYDRATED,
        EvidenceClaimState.PRODUCT_AUTHORIZED,
    }
)


def _render_untargeted_evidence_report(index: UntargetedExplanationIndex) -> str:
    if not isinstance(index, UntargetedExplanationIndex):
        raise TypeError("index must be an UntargetedExplanationIndex")
    rows = [
        fragment
        for rank, entry in enumerate(index.entries, start=1)
        for fragment in _entry_rows(rank, entry)
    ]
    lines = [
        "<!doctype html>",
        '<html lang="zh-Hant">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Untargeted Evidence Report</title>",
        "<style>",
        gallery_css(),
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        '<header class="gallery-hero" aria-label="report introduction">',
        '<div class="hero-kicker">Integrated explanation surface</div>',
        "<h1>Untargeted Evidence Report</h1>",
        '<p class="hero-copy">Discovery、Alignment、Backfill 與最終 Matrix '
        "結果沿同一 hypothesis/sample route 呈現；缺少 owner explanation "
        "時明確標示 unavailable。</p>",
        '<div class="hero-status-strip" aria-label="report role">',
        "<span>explanation only</span>",
        "<span>owner-validated inputs</span>",
        "<span>does not write Matrix</span>",
        "<span>offline static report</span>",
        "</div>",
        "</header>",
        *_summary_html(index),
        *_run_binding_html(index),
        '<p class="authority-note"><strong>Authority ceiling:</strong> '
        "此頁不是 durable machine evidence、scientific owner、validation "
        "authority 或 ProductWriter。畫面只重述 typed owner result；locator "
        "僅供導覽。</p>",
        *_filter_html(len(index.entries)),
    ]
    if rows:
        lines.extend(_table_shell(rows))
    else:
        lines.append(
            '<p class="empty-state">沒有 requested hypothesis/sample routes。</p>'
        )
    lines.extend(lightbox_html())
    lines.extend(["</main>", lightbox_script(), "</body>", "</html>"])
    return "\n".join(lines) + "\n"


def _write_untargeted_evidence_report(
    path: Path,
    index: UntargetedExplanationIndex,
) -> Path:
    """Write prototype UTF-8/LF bytes without changing default outputs."""

    if not isinstance(path, Path):
        raise TypeError("path must be a Path")
    if not isinstance(index, UntargetedExplanationIndex):
        raise TypeError("index must be an UntargetedExplanationIndex")
    path.parent.mkdir(parents=True, exist_ok=True)
    _verify_locator_artifacts(path.parent, index, report_path=path)
    rendered = _render_untargeted_evidence_report(index)
    path.write_bytes(rendered.encode("utf-8"))
    return path


def _verify_locator_artifacts(
    navigation_root: Path | None,
    index: UntargetedExplanationIndex,
    *,
    report_path: Path | None = None,
) -> None:
    locators = tuple(
        locator
        for entry in index.entries
        for section in entry.sections
        for locator in section.locators
    )
    if not locators:
        return
    if navigation_root is None:
        raise ValueError(
            "navigation_root is required when an explanation index has locators"
        )
    root = navigation_root.resolve()
    for locator in locators:
        artifact = root.joinpath(*PurePosixPath(locator.relative_path).parts)
        if not artifact.is_file():
            raise ValueError(
                f"explanation locator artifact is unavailable: {locator.relative_path}"
            )
        resolved_artifact = artifact.resolve(strict=True)
        if not resolved_artifact.is_relative_to(root):
            raise ValueError("explanation locator artifact escaped the report root")
        if report_path is not None and report_path.exists():
            try:
                output_aliases_artifact = report_path.samefile(resolved_artifact)
            except OSError as exc:
                raise ValueError(
                    "untargeted evidence report output identity could not be verified"
                ) from exc
            if output_aliases_artifact:
                raise ValueError(
                    "untargeted evidence report output aliases an explanation "
                    "locator artifact"
                )
        if fingerprint_file_content(resolved_artifact) != locator.artifact_fingerprint:
            raise ValueError(
                f"explanation locator artifact changed: {locator.relative_path}"
            )


def _summary_html(index: UntargetedExplanationIndex) -> list[str]:
    available = sum(
        section.claim.state in _AVAILABLE_STATES
        for entry in index.entries
        for section in entry.sections
    )
    total = len(index.entries) * len(tuple(ExplanationTopic))
    unavailable = total - available
    return [
        '<section class="summary" aria-label="report summary">',
        '<div class="summary-item"><span>requested routes</span>'
        f"<strong>{len(index.entries)}</strong></div>",
        '<div class="summary-item"><span>available sections</span>'
        f"<strong>{available}</strong></div>",
        '<div class="summary-item"><span>unavailable sections</span>'
        f"<strong>{unavailable}</strong></div>",
        '<div class="summary-item"><span>schema</span>'
        f"<strong>{escape_html(index.schema_version)}</strong></div>",
        "</section>",
    ]


def _run_binding_html(index: UntargetedExplanationIndex) -> list[str]:
    binding = index.run_binding
    lines = [
        '<section class="artifact-strip" aria-label="run and Matrix binding">',
        "<span><strong>attempt</strong> "
        f"{escape_html(binding.processing_attempt.processing_attempt_id)}</span>",
        "<span><strong>Matrix bytes</strong> "
        f"{escape_html(binding.matrix_artifact_claim.state.value)}</span>",
        "<span><strong>Matrix ↔ run</strong> "
        f"{escape_html(binding.matrix_run_binding_claim.state.value)}</span>",
        "<span><strong>runtime finalization</strong> "
        f"{escape_html(binding.runtime_finalization_claim.state.value)}</span>",
        "</section>",
        '<details class="secondary-chain">',
        "<summary>Run / artifact binding explanations</summary>",
        '<div class="secondary-chain-body">',
        *_claim_explanation_html(
            "processing attempt",
            binding.attempt_claim,
        ),
        *_claim_explanation_html(
            "Matrix artifact bytes",
            binding.matrix_artifact_claim,
        ),
        *_claim_explanation_html(
            "Matrix ↔ run",
            binding.matrix_run_binding_claim,
        ),
        *_claim_explanation_html(
            "runtime finalization",
            binding.runtime_finalization_claim,
        ),
    ]
    if binding.matrix_fingerprint is not None:
        lines.append(
            '<p class="chain-note"><strong>Matrix SHA-256:</strong> '
            f"{escape_html(binding.matrix_fingerprint.content_sha256)} · "
            f"{binding.matrix_fingerprint.byte_size} bytes</p>"
        )
    if binding.matrix_identity_fingerprint is not None:
        lines.append(
            '<p class="chain-note"><strong>Matrix identity SHA-256:</strong> '
            f"{escape_html(binding.matrix_identity_fingerprint.content_sha256)} · "
            f"{binding.matrix_identity_fingerprint.byte_size} bytes</p>"
        )
    lines.extend(("</div>", "</details>"))
    return lines


def _claim_explanation_html(label: str, claim: object) -> tuple[str, ...]:
    if not isinstance(claim, EvidenceClaim):
        raise TypeError("claim must be an EvidenceClaim")
    return (
        '<p class="chain-note"><strong>'
        f"{escape_html(label)}:</strong> {escape_html(claim.state.value)} — "
        f"{escape_html(claim.reason)}</p>",
    )


def _filter_html(route_count: int) -> list[str]:
    return [
        '<section class="filters" aria-label="table filters">',
        '<label for="categoryFilter">Availability</label>',
        '<select id="categoryFilter" data-filter-control>',
        '<option value="" selected>All routes</option>',
        '<option value="complete">All explanations available</option>',
        '<option value="partial">Some explanations unavailable</option>',
        "</select>",
        '<label for="searchBox">Search</label>',
        '<input id="searchBox" type="search" data-search-control '
        'aria-label="Search Matrix row, PeakHypothesis, sample, claim or reason">',
        '<span class="result-count" data-result-count data-result-label="routes">顯示 '
        f"{route_count} / "
        f"{route_count} routes</span>",
        "</section>",
    ]


def _table_shell(rows: Iterable[str]) -> list[str]:
    return [
        '<div class="table-wrap">',
        '<table class="review-table" aria-describedby="reportTableDescription">',
        '<caption id="reportTableDescription">One bounded route per final '
        "Matrix row and canonical sample. Details retain the Discovery, "
        "Alignment, Backfill, and Matrix claim ceilings.</caption>",
        "<colgroup>",
        '<col class="col-priority">',
        '<col class="col-family">',
        '<col class="col-state">',
        '<col class="col-issue">',
        '<col class="col-counts">',
        '<col class="col-overlay">',
        '<col class="col-details">',
        "</colgroup>",
        "<thead><tr>",
        '<th scope="col">rank</th>',
        '<th scope="col">hypothesis / sample</th>',
        '<th scope="col">availability</th>',
        '<th scope="col">Matrix result</th>',
        '<th scope="col">missing</th>',
        '<th scope="col">links</th>',
        '<th scope="col">chain</th>',
        "</tr></thead>",
        "<tbody>",
        *rows,
        "</tbody>",
        "</table>",
        "</div>",
    ]


def _entry_rows(
    rank: int,
    entry: ExplanationIndexEntry,
) -> tuple[str, ...]:
    subject = entry.subject
    route_id = f"route-{subject.matrix_row_index}-{subject.matrix_sample_index}"
    detail_id = f"{route_id}-details"
    unavailable = tuple(
        section
        for section in entry.sections
        if section.claim.state not in _AVAILABLE_STATES
    )
    category = "complete" if not unavailable else "partial"
    search = _entry_search_text(entry)
    matrix = entry.section(ExplanationTopic.MATRIX)
    matrix_text = _matrix_value_text(matrix)
    link_html = _entry_link_html(entry)
    return (
        '<tr class="seed-decision-row" data-family-row '
        f'data-family="{escape_attr(route_id)}" '
        f'data-family-section="{escape_attr(route_id)}" '
        f'data-category="{escape_attr(category)}" '
        f'data-search="{escape_attr(search)}">',
        f'<td class="cell-priority" data-label="rank">{rank}</td>',
        '<th class="cell-family seed-cell" scope="row" '
        'data-label="hypothesis / sample">'
        f'<span class="family-id">{escape_html(subject.peak_hypothesis_id)}</span>'
        f'<span class="family-meta">sample={escape_html(subject.sample_stem)} · '
        f"row={subject.matrix_row_index} · cell={subject.matrix_sample_index} · "
        f"m/z={escape_html(_matrix_coordinates(matrix)[0])} · "
        f"RT={escape_html(_matrix_coordinates(matrix)[1])}</span>"
        "</th>",
        '<td class="cell-state" data-label="availability">'
        f"{_availability_badge(category)}</td>",
        '<td class="cell-issue" data-label="Matrix result">'
        f'<span class="issue-text">{escape_html(matrix_text)}</span></td>',
        '<td class="cell-counts" data-label="missing">'
        f"{sum(len(section.missing_requirements) for section in unavailable)}</td>",
        f'<td class="cell-overlay" data-label="links">{link_html}</td>',
        '<td class="cell-details" data-label="chain">'
        '<button type="button" class="detail-toggle" aria-expanded="false" '
        f'aria-controls="{escape_attr(detail_id)}" '
        f'data-detail-toggle="{escape_attr(detail_id)}">Open</button></td>',
        "</tr>",
        f'<tr class="detail-row" id="{escape_attr(detail_id)}" '
        f'data-family-section="{escape_attr(route_id)}" '
        f'data-detail-for="{escape_attr(route_id)}" hidden>',
        '<td colspan="7"><div class="detail-drawer">',
        '<div class="detail-drawer-head"><strong>Integrated evidence chain</strong>'
        f"<span>{escape_html(subject.peak_hypothesis_id)} · "
        f"{escape_html(subject.sample_stem)}</span></div>",
        '<div class="chain-body" aria-label="route identity binding">',
        '<p class="chain-note"><strong>source feature family:</strong> '
        f"{escape_html(subject.source_feature_family_id)}</p>",
        '<p class="chain-note"><strong>Matrix row identity basis:</strong> '
        f"{escape_html(subject.matrix_row_identity_basis)}</p>",
        '<p class="chain-note"><strong>canonical sample identity:</strong> '
        f"{escape_html(subject.sample.identity.sample_identity_id)}</p>",
        '<p class="chain-note"><strong>Matrix row binding SHA-256:</strong> '
        f"{escape_html(subject.matrix_row_binding_sha256)}</p>",
        "</div>",
        '<div class="family-details"><div class="evidence-chain">',
        *(_section_html(section) for section in entry.sections),
        "</div></div>",
        "</div></td>",
        "</tr>",
    )


def _entry_search_text(entry: ExplanationIndexEntry) -> str:
    subject = entry.subject
    values = [
        str(subject.matrix_row_index),
        str(subject.matrix_sample_index),
        subject.peak_hypothesis_id,
        subject.source_feature_family_id,
        subject.sample_stem,
        subject.sample.identity.sample_identity_id,
        subject.matrix_row_identity_basis,
    ]
    for section in entry.sections:
        values.extend(
            (
                section.topic.value,
                section.source_owner,
                section.claim.subject,
                section.claim.state.value,
                section.claim.reason,
                *(claim.subject for claim in section.supporting_claims),
                *(claim.reason for claim in section.supporting_claims),
            )
        )
        payload = section.payload
        if isinstance(payload, DiscoveryExplanationPayload):
            values.extend(
                (
                    payload.candidate_universe_artifact_id,
                    *payload.candidate_peak_content_ids,
                    payload.selected_peak_hypothesis_id or "",
                    payload.selection_outcome,
                    *payload.selection_reasons,
                )
            )
        elif isinstance(payload, AlignmentExplanationPayload):
            values.extend(
                (
                    payload.group_hypothesis_id,
                    *payload.member_peak_hypothesis_ids,
                    payload.sample_member_peak_hypothesis_id or "",
                    payload.grouping_disposition,
                    payload.representative_group_hypothesis_id,
                    *payload.decision_reasons,
                )
            )
        elif isinstance(payload, BackfillExplanationPayload):
            values.extend(
                (
                    str(payload.evaluated).lower(),
                    payload.disposition,
                    payload.production_status,
                    str(payload.write_matrix_value).lower(),
                    payload.policy_version,
                    payload.matched_candidate_peak_content_id or "",
                    *payload.decision_reasons,
                )
            )
        elif isinstance(payload, MatrixExplanationPayload):
            values.extend(
                (
                    str(payload.written).lower(),
                    payload.matrix_value or "",
                    payload.matrix_row_mz,
                    payload.matrix_row_rt,
                    payload.sample_stem,
                )
            )
    return " ".join(value for value in values if value)


def _availability_badge(category: str) -> str:
    css_class = "product_grade_support" if category == "complete" else "not_assessable"
    label = "complete" if category == "complete" else "partial / unavailable"
    return f'<span class="badge {css_class}">{label}</span>'


def _matrix_value_text(section: ExplanationSection) -> str:
    payload = section.payload
    if not isinstance(payload, MatrixExplanationPayload):
        return "unavailable"
    if payload.written:
        return f"written: {payload.matrix_value}"
    return "not written"


def _matrix_coordinates(section: ExplanationSection) -> tuple[str, str]:
    payload = section.payload
    if not isinstance(payload, MatrixExplanationPayload):
        return ("unavailable", "unavailable")
    return (payload.matrix_row_mz, payload.matrix_row_rt)


def _payload_html(section: ExplanationSection) -> tuple[str, ...]:
    payload = section.payload
    if isinstance(payload, DiscoveryExplanationPayload):
        selected = payload.selected_peak_hypothesis_id or "none"
        return (
            '<p class="review-note"><strong>Candidate universe:</strong> '
            f"{escape_html(payload.candidate_universe_artifact_id)}</p>",
            '<p class="chain-note"><strong>Selection:</strong> '
            f"{escape_html(payload.selection_outcome)} · "
            f"selected PeakHypothesis={escape_html(selected)}</p>",
            *_text_list_html(
                "Candidate PeakContents",
                payload.candidate_peak_content_ids,
            ),
            *_text_list_html("Selection reasons", payload.selection_reasons),
        )
    if isinstance(payload, AlignmentExplanationPayload):
        sample_member = payload.sample_member_peak_hypothesis_id or "none"
        return (
            '<p class="review-note"><strong>Peak group:</strong> '
            f"{escape_html(payload.group_hypothesis_id)}</p>",
            '<p class="chain-note"><strong>Sample member:</strong> '
            f"{escape_html(sample_member)} · disposition="
            f"{escape_html(payload.grouping_disposition)}</p>",
            '<p class="chain-note"><strong>Representative group:</strong> '
            f"{escape_html(payload.representative_group_hypothesis_id)}</p>",
            *_text_list_html(
                "Member PeakHypotheses",
                payload.member_peak_hypothesis_ids,
            ),
            *_text_list_html("Alignment reasons", payload.decision_reasons),
        )
    if isinstance(payload, BackfillExplanationPayload):
        matched = payload.matched_candidate_peak_content_id or "none"
        return (
            '<p class="review-note"><strong>Backfill disposition:</strong> '
            f"{escape_html(payload.disposition)}</p>",
            '<p class="chain-note"><strong>Production decision:</strong> '
            f"{escape_html(payload.production_status)} · write Matrix="
            f"{str(payload.write_matrix_value).lower()}</p>",
            '<p class="chain-note"><strong>Policy:</strong> '
            f"{escape_html(payload.policy_version)} · evaluated="
            f"{str(payload.evaluated).lower()}</p>",
            '<p class="chain-note"><strong>Matched candidate PeakContent:</strong> '
            f"{escape_html(matched)}</p>",
            *_text_list_html("Backfill reasons", payload.decision_reasons),
        )
    if isinstance(payload, MatrixExplanationPayload):
        return (
            '<p class="review-note"><strong>Matrix:</strong> '
            f"{escape_html(_matrix_value_text(section))}</p>",
            '<p class="chain-note"><strong>Matrix row:</strong> '
            f"m/z={escape_html(payload.matrix_row_mz)} · "
            f"RT={escape_html(payload.matrix_row_rt)} · "
            f"sample={escape_html(payload.sample_stem)}</p>",
        )
    if payload is not None:
        raise TypeError("unsupported explanation payload")
    return ()


def _text_list_html(label: str, values: tuple[str, ...]) -> tuple[str, ...]:
    return (
        f'<p class="chain-note"><strong>{escape_html(label)}:</strong></p>',
        '<ul class="component-list">',
        *(f"<li>{escape_html(value)}</li>" for value in values),
        "</ul>",
    )


def _section_html(section: ExplanationSection) -> str:
    state = section.claim.state.value
    status_class = (
        "product_grade_support"
        if section.claim.state in _AVAILABLE_STATES
        else "not_assessable"
    )
    body = [
        '<article class="chain-item">',
        '<div class="chain-head">',
        f"<h3>{escape_html(section.topic.value.title())}</h3>",
        f'<span class="badge {status_class}">{escape_html(state)}</span>',
        "</div>",
        '<div class="chain-body">',
        f'<p class="chain-note"><strong>owner:</strong> '
        f"{escape_html(section.source_owner)}</p>",
        f'<p class="chain-note">{escape_html(section.claim.reason)}</p>',
    ]
    body.extend(_payload_html(section))
    if section.missing_requirements:
        body.extend(
            (
                '<p class="review-note"><strong>Unavailable:</strong></p>',
                '<ul class="component-list">',
                *(
                    f"<li>{escape_html(requirement)}</li>"
                    for requirement in section.missing_requirements
                ),
                "</ul>",
            )
        )
    if section.supporting_claims:
        body.extend(
            (
                '<details class="secondary-chain">',
                f"<summary>Owner claim census <small>"
                f"{len(section.supporting_claims)} claims</small></summary>",
                '<div class="secondary-chain-body">',
                *(
                    '<div class="chain-item"><div class="chain-body">'
                    f"<strong>{escape_html(claim.subject)}</strong> · "
                    f"{escape_html(claim.state.value)}"
                    f'<p class="chain-note">{escape_html(claim.reason)}</p>'
                    "</div></div>"
                    for claim in section.supporting_claims
                ),
                "</div></details>",
            )
        )
    if section.locators:
        body.extend(
            (
                '<ul class="path-list">',
                *(f"<li>{_locator_link(locator)}</li>" for locator in section.locators),
                "</ul>",
            )
        )
    body.extend(("</div>", "</article>"))
    return "".join(body)


def _entry_link_html(entry: ExplanationIndexEntry) -> str:
    locators = tuple(
        locator for section in entry.sections for locator in section.locators
    )
    if not locators:
        return '<span class="muted">unavailable</span>'
    return " ".join(_locator_link(locator, compact=True) for locator in locators)


def _locator_link(
    locator: ExplanationLocator,
    *,
    compact: bool = False,
) -> str:
    href = locator.relative_path
    label = locator.kind.value if compact else locator.relative_path
    if not href:
        return escape_html(label)
    attributes = [
        f'href="{escape_attr(href)}"',
        f'title="{escape_attr(locator.relative_path)}"',
    ]
    if locator.kind is ExplanationLocatorKind.OVERLAY:
        attributes.extend(
            (
                f'data-lightbox-src="{escape_attr(href)}"',
                'data-lightbox-title="Evidence overlay"',
                f'data-lightbox-caption="{escape_attr(locator.relative_path)}"',
                'data-lightbox-interpretation="Navigation only; '
                'no scientific authority."',
            )
        )
    return f"<a {' '.join(attributes)}>{escape_html(label)}</a>"


__all__ = ()
