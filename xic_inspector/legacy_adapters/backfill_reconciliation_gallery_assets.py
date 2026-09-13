"""Static assets for the backfill reconciliation HTML gallery."""

from __future__ import annotations


def lightbox_html() -> list[str]:
    return [
        '<div class="lightbox" role="dialog" aria-modal="true" '
        'aria-labelledby="lightboxTitle" aria-describedby="lightboxCaption" hidden>',
        '<div class="lightbox-panel">',
        '<div class="lightbox-header">',
        "<div>",
        '<h2 id="lightboxTitle">MS1 Evidence PNG</h2>',
        '<p id="lightboxCaption" class="lightbox-caption"></p>',
        '<p id="lightboxInterpretation" class="lightbox-interpretation"></p>',
        "</div>",
        '<div class="lightbox-actions">',
        '<a class="lightbox-direct" href="">Open PNG</a>',
        '<button type="button" class="lightbox-close" aria-label="Close PNG lightbox">'
        "Close</button>",
        "</div>",
        "</div>",
        '<img class="lightbox-image" alt="">',
        "</div>",
        "</div>",
    ]


def lightbox_script() -> str:
    return """
<script>
(() => {
  const modal = document.querySelector('.lightbox');
  if (!modal) return;
  const image = modal.querySelector('.lightbox-image');
  const title = modal.querySelector('#lightboxTitle');
  const caption = modal.querySelector('.lightbox-caption');
  const interpretation = modal.querySelector('.lightbox-interpretation');
  const direct = modal.querySelector('.lightbox-direct');
  const close = modal.querySelector('.lightbox-close');
  let previousFocus = null;
  const openModal = (link) => {
    previousFocus = document.activeElement;
    image.src = link.dataset.lightboxSrc;
    image.alt = link.dataset.lightboxCaption || 'overlay PNG';
    title.textContent = link.dataset.lightboxTitle || 'MS1 Evidence PNG';
    caption.textContent = link.dataset.lightboxCaption || '';
    interpretation.textContent = link.dataset.lightboxInterpretation || '';
    direct.href = link.href || link.dataset.lightboxSrc;
    modal.hidden = false;
    close.focus();
  };
  const closeModal = () => {
    modal.hidden = true;
    image.removeAttribute('src');
    direct.removeAttribute('href');
    if (previousFocus && previousFocus.focus) previousFocus.focus();
  };
  document.addEventListener('click', (event) => {
    const link = event.target.closest('[data-lightbox-src]');
    if (!link) return;
    event.preventDefault();
    openModal(link);
  });
  document.addEventListener('keydown', (event) => {
    const activeLink = event.target.closest('[data-lightbox-src]');
    const isOpenKey = event.key === 'Enter' || event.key === ' ' ||
      event.key === 'Spacebar';
    if (activeLink && isOpenKey) {
      event.preventDefault();
      openModal(activeLink);
      return;
    }
    if (modal.hidden) return;
    if (event.key === 'Escape') closeModal();
    if (event.key !== 'Tab') return;
    const focusable = Array.from(
      modal.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )
    ).filter((element) => !element.disabled && element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  modal.addEventListener('click', (event) => {
    if (event.target === modal) closeModal();
  });
  close.addEventListener('click', closeModal);
  const setDetailOpen = (button, open) => {
    const detailRow = document.getElementById(button.getAttribute('aria-controls'));
    if (!detailRow) return;
    button.setAttribute('aria-expanded', String(open));
    button.textContent = open ? 'Close' : 'Open';
    detailRow.hidden = !open;
    detailRow.classList.toggle('is-open', open);
  };
  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-detail-toggle]');
    if (!button) return;
    event.preventDefault();
    setDetailOpen(button, button.getAttribute('aria-expanded') !== 'true');
  });
  const focusFilter = document.querySelector('[data-filter-control]');
  const search = document.querySelector('[data-search-control]');
  const resultCount = document.querySelector('[data-result-count]');
  const resultLabel = resultCount?.dataset.resultLabel || 'families';
  const familyRows = Array.from(
    document.querySelectorAll('.review-table > tbody > tr[data-family-row]')
  );
  const sectionRows = Array.from(
    document.querySelectorAll('.review-table > tbody > tr[data-family-section]')
  );
  const totalFamilies = familyRows.length;
  const applyFilters = () => {
    const selected = focusFilter ? focusFilter.value : '';
    const term = search ? search.value.toLowerCase() : '';
    let visibleFamilies = 0;
    familyRows.forEach((row) => {
      const rowCategories = (row.dataset.category || '').split(/\\s+/);
      const focusOk = !selected || rowCategories.includes(selected);
      const searchOk = !term || (row.dataset.search || '').toLowerCase().includes(term);
      const visible = focusOk && searchOk;
      if (visible) visibleFamilies += 1;
      row.hidden = !visible;
      sectionRows
        .filter((sectionRow) => sectionRow.dataset.familySection === row.dataset.family)
        .forEach((sectionRow) => {
          if (!visible) {
            const button = sectionRow.querySelector('[data-detail-toggle]');
            if (button) setDetailOpen(button, false);
            sectionRow.hidden = true;
            return;
          }
          if (!sectionRow.classList.contains('detail-row')) {
            sectionRow.hidden = false;
          }
        });
    });
    if (resultCount) {
      resultCount.textContent =
        `顯示 ${visibleFamilies} / ${totalFamilies} ${resultLabel}`;
    }
  };
  if (focusFilter) focusFilter.addEventListener('change', applyFilters);
  if (search) search.addEventListener('input', applyFilters);
  applyFilters();
})();
</script>
"""


def gallery_css() -> str:
    return """
:root {
  --bg: #f5f7f8;
  --surface: #ffffff;
  --surface-muted: #f8fafc;
  --text: #17202a;
  --muted: #5a6673;
  --line: #cbd5e1;
  --line-soft: #e2e8f0;
  --focus: #7db3dc;
  --blue: #1d6fa3;
  --green: #16855b;
  --amber: #9a6100;
  --red: #a12b2b;
  --purple: #6f46c7;
  --shadow: 0 8px 24px rgba(23, 32, 42, 0.08);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: Aptos, "Segoe UI Variable", "Segoe UI", system-ui, sans-serif;
  line-height: 1.45;
}
main {
  max-width: 1540px;
  margin: 0 auto;
  padding: 28px 30px 44px;
}
h1 { margin: 0 0 14px; font-size: 28px; }
a, button, input, select, summary { outline-offset: 3px; }
a:focus-visible,
button:focus-visible,
input:focus-visible,
select:focus-visible,
summary:focus-visible { outline: 3px solid var(--focus); }
.summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(150px, 100%), 1fr));
  gap: 8px;
  margin-bottom: 14px;
}
.summary-item,
.authority-note,
.artifact-strip,
.provenance-panel,
.html-scope-note,
.filters {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  box-shadow: var(--shadow);
}
.summary-item {
  min-width: 0;
  min-height: 58px;
  padding: 8px 10px;
  border-left: 5px solid var(--blue);
}
.summary-item span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}
.summary-item strong {
  display: block;
  margin-top: 4px;
  overflow-wrap: anywhere;
}
.authority-note {
  grid-column: 1 / -1;
  margin: 0;
  padding: 9px 11px;
  border-left: 5px solid var(--red);
  color: #742525;
  font-weight: 700;
}
.artifact-strip {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding: 8px 11px;
}
.artifact-strip span {
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}
.artifact-strip a {
  font-weight: 700;
}
.provenance-panel {
  grid-column: 1 / -1;
  padding: 0;
}
.provenance-panel > summary {
  padding: 9px 11px;
  cursor: pointer;
  color: #334155;
  overflow-wrap: anywhere;
  white-space: normal;
}
.provenance-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr));
  gap: 8px 14px;
  margin: 0;
  padding: 0 11px 11px;
  list-style: none;
}
.provenance-list li {
  min-width: 0;
}
.artifact-label {
  display: block;
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
}
.provenance-list a {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 700;
}
.html-scope-note {
  margin: 0 0 10px;
  padding: 9px 11px;
  border-left: 5px solid var(--blue);
  color: #334155;
  font-weight: 700;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
  margin: 0 0 14px;
  padding: 10px 12px;
}
.filters label { font-weight: 700; }
.filters select,
.filters input {
  min-height: 34px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 5px 8px;
}
.filters input { min-width: min(360px, 100%); }
.result-count {
  margin-left: auto;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  white-space: nowrap;
}
.table-wrap {
  overflow-x: auto;
  max-width: 1090px;
  margin: 0 auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  box-shadow: var(--shadow);
}
.review-table {
  width: 1084px;
  min-width: 1084px;
  border-collapse: collapse;
  font-size: 13px;
  table-layout: fixed;
}
.review-table caption {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
.review-table .col-priority { width: 54px; }
.review-table .col-family { width: 310px; }
.review-table .col-state { width: 220px; }
.review-table .col-issue { width: 220px; }
.review-table .col-counts { width: 92px; }
.review-table .col-overlay { width: 78px; }
.review-table .col-details { width: 110px; }
.review-table tbody tr {
  --row-bg: var(--surface);
  background: var(--row-bg);
}
.review-table th,
.review-table td {
  padding: 8px 9px;
  border-bottom: 1px solid var(--line-soft);
  text-align: center;
  vertical-align: top;
  overflow-wrap: anywhere;
  background: var(--row-bg, var(--surface));
}
.review-table thead th {
  position: sticky;
  top: 0;
  z-index: 4;
  background: #e9eef3;
  color: #243240;
  white-space: nowrap;
}
.review-table tbody tr:nth-child(even) { --row-bg: var(--surface-muted); }
.review-table tbody tr:hover { --row-bg: #eef6fb; }
.review-table tbody tr.family-section-row {
  --row-bg: #eef3f7;
}
.review-table tbody tr.family-section-row th,
.review-table tbody tr.family-section-row td {
  padding-top: 7px;
  padding-bottom: 7px;
  border-top: 2px solid var(--line);
  border-bottom: 1px solid var(--line);
}
.review-table tbody tr.seed-decision-row {
  --row-bg: var(--surface);
}
.review-table tbody tr.seed-decision-row:nth-of-type(even) {
  --row-bg: var(--surface-muted);
}
.review-table td:nth-child(1) {
  text-align: center;
  vertical-align: middle;
  font-variant-numeric: tabular-nums;
}
.review-table tbody th[scope="row"] {
  box-shadow: 1px 0 0 var(--line-soft);
}
.review-table thead th:nth-child(1),
.review-table thead th:nth-child(2) {
  z-index: 5;
  background: #e2e8f0;
}
.cell-counts,
.cell-overlay,
.cell-details {
  text-align: center;
  vertical-align: middle;
}
.cell-family,
.cell-state,
.cell-issue {
  text-align: center;
}
.seed-rank {
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
}
.seed-cell {
  border-left: 4px solid #d8e3ec;
}
.pattern-label,
.pattern-status,
.anchor-status,
.target-status {
  display: block;
  color: var(--muted);
  font-size: 11px;
  font-weight: 900;
  line-height: 1.25;
  text-transform: uppercase;
}
.pattern-label {
  margin-top: 3px;
  text-transform: none;
}
.family-context-cell {
  text-align: left;
}
.family-context-cell .family-id,
.family-context-cell .family-meta,
.family-context-cell .seed-summary,
.family-context-cell .seed-window,
.family-context-cell .anchor-status,
.family-context-cell .pattern-status,
.family-context-cell .target-status {
  display: inline-block;
  margin: 0 8px 0 0;
  vertical-align: middle;
}
.family-context-cell .anchor-status,
.family-context-cell .pattern-status,
.family-context-cell .target-status {
  padding: 2px 6px;
  border: 1px solid var(--line-soft);
  border-radius: 999px;
  background: #f8fafc;
  line-height: 1.2;
  text-transform: none;
}
.family-context-cell .anchor-status {
  border-left: 4px solid var(--green);
  color: #1c3f31;
}
.family-context-cell .target-status {
  border-left: 4px solid var(--blue);
  color: #173e5c;
}
.cell-state,
.cell-issue {
  vertical-align: middle;
}
.overlay-scope {
  display: inline-block;
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
  line-height: 1.25;
}
.overlay-missing-reason {
  display: inline-block;
  margin-top: 2px;
  color: #5f6f7c;
  font-weight: 700;
}
.muted {
  color: var(--muted);
}
.state-stack {
  display: grid;
  gap: 5px;
  align-content: center;
}
.state-line {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr);
  align-items: center;
  gap: 6px;
}
.state-key {
  color: var(--muted);
  font-size: 10px;
  font-weight: 900;
  line-height: 1;
  text-align: right;
  text-transform: none;
}
.cell-state .badge {
  justify-self: start;
  max-width: 100%;
}
.shadow-pill {
  display: inline-block;
  justify-self: start;
  max-width: 100%;
  padding: 3px 6px;
  border: 1px solid var(--line);
  border-left: 4px solid var(--blue);
  border-radius: 6px;
  background: #f8fafc;
  font-size: 12px;
  font-weight: 800;
  line-height: 1.25;
  white-space: normal;
}
.detail-toggle {
  min-height: 30px;
  padding: 4px 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  font-weight: 800;
}
.detail-toggle[aria-expanded="true"] {
  border-color: var(--blue);
  background: #eef6fb;
}
.review-table .detail-row > td {
  position: static;
  padding: 0;
  text-align: left;
  vertical-align: top;
  background: #f8fafc;
}
.detail-drawer {
  margin: 0;
  padding: 12px;
  border-top: 1px solid var(--line-soft);
  border-left: 4px solid var(--blue);
}
.detail-drawer-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px 14px;
  margin-bottom: 10px;
}
.detail-drawer-head span {
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}
.review-answer {
  margin: 0 0 10px;
  padding: 11px 12px;
  border: 1px solid #cfdae0;
  border-radius: 6px;
  background: #fff;
  box-shadow: inset 5px 0 0 var(--blue);
}
.review-answer strong {
  display: block;
  margin-bottom: 4px;
  color: #14242d;
}
.review-answer p {
  margin: 0;
  color: #334155;
}
.review-answer-meta {
  margin-top: 6px !important;
  color: var(--muted) !important;
  font-size: 12px;
}
.family-id {
  display: block;
  font-size: 14px;
  font-weight: 800;
  letter-spacing: 0;
  text-align: center;
}
.family-meta,
.seed-summary,
.seed-window {
  display: block;
  margin-top: 3px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.35;
  text-align: center;
}
.seed-count {
  display: inline-block;
  padding: 2px 6px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: #f8fafc;
  font-weight: 700;
}
.badge {
  display: inline-block;
  max-width: 100%;
  padding: 3px 6px;
  border: 1px solid var(--line);
  border-left-width: 4px;
  border-radius: 6px;
  background: #fff;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.25;
  overflow-wrap: normal;
  white-space: nowrap;
}
.badge.product_grade_support,
.badge.product_primary_backfilled,
.badge.product_accepts_and_product_grade_supports { border-left-color: var(--green); }
.badge.review_only_visual_support,
.badge.product_rejects_but_visual_supports { border-left-color: var(--blue); }
.badge.evidence_blocks_backfill,
.badge.product_accepts_but_evidence_conflicts,
.badge.product_rejects_and_evidence_blocks { border-left-color: var(--red); }
.badge.not_assessable,
.badge.not_assessable_missing_overlay,
.badge.not_assessable_missing_seed_provenance,
.badge.not_assessable_join_gap { border-left-color: var(--amber); }
.badge.evidence_inconclusive,
.badge.human_visual_judgment_only,
.badge.product_rescued_context_only,
.badge.product_provisional { border-left-color: var(--purple); }
.top-issue {
  display: inline-grid;
  justify-items: start;
  gap: 5px;
  max-width: 100%;
  margin-inline: auto;
}
.issue-text {
  display: block;
  max-width: 100%;
  padding-left: 8px;
  border-left: 3px solid var(--line);
  color: #334155;
  line-height: 1.35;
  text-align: left;
}
.issue-text.blocker { border-left-color: var(--red); }
.issue-text.support { border-left-color: var(--green); }
.count-stack {
  display: flex;
  justify-content: center;
  gap: 8px;
  margin: 0;
  font-variant-numeric: tabular-nums;
}
.count-stack div {
  display: grid;
  justify-items: center;
  gap: 1px;
  min-width: 38px;
}
.count-stack dt {
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
  overflow-wrap: normal;
  white-space: nowrap;
}
.count-stack dd {
  margin: 0;
  font-weight: 800;
}
details summary {
  cursor: pointer;
  font-weight: 700;
  min-height: 28px;
  line-height: 1.35;
}
.details-grid {
  display: grid;
  gap: 10px;
  padding-top: 8px;
}
.detail-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 10px;
}
.detail-summary-card {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: #fff;
}
.detail-summary-card h3 {
  margin: 0;
  font-size: 12px;
  text-transform: uppercase;
  color: #334155;
}
.summary-subtitle {
  margin: 3px 0 8px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 700;
}
.summary-body p {
  margin: 5px 0;
}
.summary-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
}
.summary-line span:first-child {
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
}
.summary-link {
  margin: 0 0 6px;
}
.detail-block {
  margin: 0;
}
.family-details {
  display: grid;
  gap: 10px;
  padding-top: 8px;
}
.seed-table-wrap {
  overflow-x: auto;
}
.seed-table {
  width: 100%;
  min-width: 860px;
  border-collapse: collapse;
  font-size: 12px;
}
.seed-table th,
.seed-table td {
  padding: 6px;
  border: 1px solid var(--line-soft);
}
.seed-table th {
  background: #f1f5f9;
}
.seed-subdetails {
  padding-top: 4px;
}
.path-list,
.metric-list,
.component-list {
  margin: 4px 0 0;
  padding-left: 18px;
}
.metric-list li,
.component-list li {
  margin: 2px 0;
}
.evidence-chain {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.chain-item {
  min-width: 0;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: #fff;
}
.chain-item:nth-last-child(-n + 2) {
  grid-column: 1 / -1;
}
.shadow-policy-chain {
  grid-column: 1 / -1;
}
.secondary-chain {
  grid-column: 1 / -1;
}
.secondary-chain summary {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 10px;
  cursor: pointer;
  font-weight: 800;
  background: #f8fafc;
}
.secondary-chain summary small {
  color: var(--muted);
  font-size: 11px;
  font-weight: 700;
}
.secondary-chain-body {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  padding: 10px;
  border-top: 1px solid var(--line-soft);
}
.secondary-chain-body .chain-item {
  background: #fff;
}
.chain-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line-soft);
  background: #f8fafc;
}
.chain-head h3 {
  margin: 0;
  font-size: 13px;
}
.chain-state {
  color: var(--muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}
.chain-body {
  padding: 9px 10px;
}
.chain-note {
  margin: 6px 0 0;
  color: #334155;
}
.review-note {
  margin: 6px 0;
  padding-left: 8px;
  border-left: 3px solid var(--warn);
  color: #334155;
  font-weight: 700;
}
.rep-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.shadow-policy-table,
.target-benchmark-table,
.seed-alias-table {
  width: 100%;
  min-width: 900px;
  border-collapse: collapse;
  font-size: 12px;
}
.shadow-policy-table-wrap,
.target-benchmark-table-wrap,
.seed-alias-table-wrap {
  overflow-x: auto;
}
.rep-table th,
.rep-table td,
.shadow-policy-table th,
.shadow-policy-table td,
.target-benchmark-table th,
.target-benchmark-table td,
.seed-alias-table th,
.seed-alias-table td {
  padding: 6px;
  border: 1px solid var(--line-soft);
  overflow-wrap: anywhere;
}
.projection-accept-index {
  margin: 10px 0 12px;
}
.projection-accept-index h3 {
  margin: 0 0 4px;
  font-size: 13px;
}
.projection-accept-table {
  min-width: 980px;
}
.projection-authority-chain {
  margin-top: 6px;
  padding-left: 8px;
  border-left: 3px solid var(--ok);
  color: #334155;
  font-weight: 700;
}
.projection-authority-chain span {
  display: block;
  color: var(--muted);
  font-size: 10px;
  letter-spacing: .02em;
  text-transform: uppercase;
}
.gap-label {
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
}
.empty-state {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
}
.lightbox[hidden] { display: none; }
.lightbox {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.72);
}
.lightbox-panel {
  width: min(1120px, 96vw);
  max-height: 92vh;
  overflow: auto;
  border-radius: 8px;
  background: #fff;
  padding: 0;
}
.lightbox-header {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line-soft);
  background: #fff;
}
.lightbox-header h2 {
  margin: 0;
  font-size: 20px;
}
.lightbox-caption {
  margin: 3px 0 0;
  color: var(--muted);
}
.lightbox-interpretation {
  margin: 4px 0 0;
  color: #334155;
  font-size: 12px;
  font-weight: 700;
}
.lightbox-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.lightbox-close {
  min-height: 34px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  font-weight: 700;
}
.lightbox-direct {
  display: inline-flex;
  align-items: center;
  min-height: 34px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #f8fafc;
  font-weight: 700;
}
.lightbox-image {
  display: block;
  width: calc(100% - 28px);
  margin: 14px;
  max-height: 74vh;
  object-fit: contain;
}
@media (max-width: 760px) {
  main { padding: 18px 12px 32px; }
  h1 { font-size: 22px; }
  .review-table { min-width: 1084px; }
  .detail-summary-grid,
  .secondary-chain-body { grid-template-columns: 1fr; }
  .evidence-chain { grid-template-columns: 1fr; }
  .chain-item:nth-last-child(-n + 2) { grid-column: auto; }
  .lightbox-header { display: grid; }
}

/* Final reconciliation gallery visual system. These rules intentionally sit
   after the legacy layout so the stable HTML/JS contract remains untouched. */
:root {
  color-scheme: light;
  --bg: #f1f3f1;
  --bg-pattern: rgba(34, 49, 54, 0.035);
  --surface: #fdfdfb;
  --surface-raised: #ffffff;
  --surface-muted: #f6f7f4;
  --surface-accent: #eef5f1;
  --text: #121916;
  --muted: #64706b;
  --line: #cbd3ce;
  --line-soft: #e2e7e3;
  --focus: #2c8171;
  --blue: #2f6f84;
  --green: #23704d;
  --amber: #956c1a;
  --red: #a13b42;
  --purple: #6c5d91;
  --ok: #23704d;
  --warn: #956c1a;
  --shadow: 0 24px 58px rgba(25, 35, 31, 0.105);
  --shadow-soft: 0 12px 30px rgba(25, 35, 31, 0.072);
  --motion: cubic-bezier(0.32, 0.72, 0, 1);
  --radius: 8px;
  --radius-sm: 6px;
}
html {
  scroll-behavior: smooth;
}
body {
  background:
    linear-gradient(
      180deg,
      rgba(255,255,255,0.58),
      rgba(255,255,255,0.18) 44%,
      rgba(255,255,255,0.52)
    ),
    linear-gradient(90deg, var(--bg-pattern) 1px, transparent 1px),
    linear-gradient(180deg, var(--bg-pattern) 1px, transparent 1px),
    var(--bg);
  background-size: auto, 28px 28px, 28px 28px, auto;
  color: var(--text);
  font-family: Aptos, "Segoe UI Variable", "Segoe UI", system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.5;
}
main {
  width: min(100%, 1620px);
  max-width: 1620px;
  padding: 26px clamp(14px, 2.4vw, 34px) 48px;
}
.gallery-hero {
  position: relative;
  overflow: hidden;
  margin: 0 0 14px;
  padding: clamp(18px, 2.4vw, 30px);
  border: 1px solid rgba(59, 84, 74, 0.22);
  border-radius: var(--radius);
  background:
    linear-gradient(135deg, rgba(255,255,255,0.96), rgba(245,248,244,0.92)),
    repeating-linear-gradient(
      90deg,
      transparent 0 42px,
      rgba(43,103,88,0.044) 42px 43px
    );
  box-shadow: var(--shadow);
}
.gallery-hero::before {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 7px;
  background: linear-gradient(180deg, var(--green), var(--blue));
}
.hero-kicker {
  color: #2d6c5c;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
h1 {
  max-width: 920px;
  margin: 4px 0 8px;
  color: #0d1820;
  font-size: clamp(30px, 4vw, 56px);
  font-weight: 780;
  line-height: 0.98;
  text-wrap: balance;
}
.hero-copy {
  max-width: 760px;
  margin: 0;
  color: #455865;
  font-size: clamp(14px, 1.4vw, 17px);
  line-height: 1.55;
  text-wrap: pretty;
}
.hero-status-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}
.hero-status-strip span {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 3px 9px;
  border: 1px solid rgba(43, 103, 88, 0.22);
  border-radius: var(--radius-sm);
  background: rgba(255,255,255,0.78);
  color: #294237;
  font-size: 12px;
  font-weight: 800;
}
.summary {
  grid-template-columns: repeat(auto-fit, minmax(min(174px, 100%), 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
.summary-item,
.authority-note,
.artifact-strip,
.provenance-panel,
.html-scope-note,
.filters,
.table-wrap,
.empty-state {
  border-color: rgba(158, 174, 184, 0.68);
  border-radius: var(--radius);
  background: rgba(252,253,253,0.94);
  box-shadow: var(--shadow-soft);
}
.summary-item {
  min-height: 76px;
  padding: 11px 12px;
  border-left: 0;
  box-shadow: inset 0 4px 0 var(--blue), var(--shadow-soft);
}
.decision-legend {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: minmax(210px, 0.75fr) repeat(3, minmax(180px, 1fr));
  gap: 1px;
  overflow: hidden;
  border: 1px solid rgba(146, 158, 151, 0.72);
  border-radius: var(--radius);
  background: var(--line-soft);
  box-shadow: var(--shadow-soft);
}
.decision-legend-heading,
.decision-legend-item {
  min-width: 0;
  padding: 12px 14px;
  background: rgba(255,255,255,0.94);
}
.decision-legend-heading {
  background: #13211c;
  color: #f8fbf7;
}
.decision-legend-heading span {
  display: block;
  margin-bottom: 6px;
  color: rgba(248,251,247,0.72);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.decision-legend-heading strong,
.decision-legend-item strong {
  display: block;
  line-height: 1.2;
}
.decision-token {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  margin-bottom: 8px;
  padding: 2px 8px;
  border: 1px solid var(--line);
  border-left: 4px solid var(--amber);
  border-radius: var(--radius-sm);
  background: #fff;
  color: #283a33;
  font-size: 12px;
  font-weight: 850;
}
.decision-legend-item:first-of-type .decision-token {
  border-left-color: var(--green);
}
.decision-legend-item:last-of-type .decision-token {
  border-left-color: var(--red);
}
.decision-legend-item p {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.42;
}
.summary-item span,
.artifact-strip span,
.artifact-label,
.gap-label,
.pattern-label,
.pattern-status,
.anchor-status,
.target-status,
.summary-line span:first-child,
.chain-state,
.count-stack dt {
  letter-spacing: 0.04em;
}
.summary-item span {
  color: #657685;
  font-size: 11px;
}
.summary-item strong {
  color: #101820;
  font-size: 16px;
  line-height: 1.25;
  font-variant-numeric: tabular-nums;
}
.authority-note,
.html-scope-note {
  padding: 11px 13px;
  border-left: 0;
  box-shadow: inset 5px 0 0 var(--red), var(--shadow-soft);
}
.html-scope-note {
  box-shadow: inset 5px 0 0 var(--blue), var(--shadow-soft);
}
.artifact-strip,
.provenance-panel > summary {
  padding: 10px 13px;
}
.artifact-strip a,
.provenance-list a,
.summary-link a,
.lightbox-direct {
  color: #086f86;
  text-decoration: none;
}
.artifact-strip a:hover,
.provenance-list a:hover,
.summary-link a:hover,
.lightbox-direct:hover {
  text-decoration: underline;
  text-decoration-thickness: 2px;
  text-underline-offset: 3px;
}
.provenance-panel[open] > summary {
  border-bottom: 1px solid var(--line-soft);
}
.provenance-list {
  padding: 11px 13px 13px;
}
.filters {
  position: sticky;
  top: 0;
  z-index: 12;
  gap: 9px 12px;
  margin-bottom: 14px;
  padding: 11px 13px;
  backdrop-filter: blur(16px);
  background: rgba(252,253,253,0.92);
}
.filters label {
  color: #2a3c48;
  font-size: 12px;
  font-weight: 850;
}
.filters select,
.filters input {
  min-height: 38px;
  border-color: #b6c5ce;
  border-radius: var(--radius-sm);
  background: #fff;
  color: var(--text);
  transition:
    border-color 180ms var(--motion),
    box-shadow 180ms var(--motion),
    transform 180ms var(--motion);
}
.filters select:hover,
.filters input:hover {
  border-color: #78a498;
}
.filters select:focus-visible,
.filters input:focus-visible {
  box-shadow: 0 0 0 4px rgba(43, 103, 88, 0.16);
}
.filters input {
  min-width: min(430px, 100%);
}
.result-count {
  padding: 5px 8px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  background: #f8faf7;
  color: #40534a;
  font-variant-numeric: tabular-nums;
}
.table-wrap {
  max-width: none;
  margin: 0;
  overflow: auto;
  border-radius: var(--radius);
}
.review-table {
  width: 100%;
  min-width: 1180px;
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}
.review-table .col-priority { width: 60px; }
.review-table .col-family { width: 330px; }
.review-table .col-state { width: 222px; }
.review-table .col-issue { width: 260px; }
.review-table .col-counts { width: 146px; }
.review-table .col-overlay { width: 88px; }
.review-table .col-details { width: 116px; }
.review-table th,
.review-table td {
  padding: 9px 10px;
  border-bottom-color: var(--line-soft);
  text-align: center;
  vertical-align: middle;
}
.review-table thead th {
  background: #e4ebe6;
  color: #1b2b24;
  font-size: 11px;
  font-weight: 850;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  white-space: nowrap;
}
.review-table thead th:nth-child(1),
.review-table thead th:nth-child(2) {
  background: #dce7e0;
}
.review-table tbody tr:nth-child(even) {
  --row-bg: #fafbf8;
}
.review-table tbody tr:hover {
  --row-bg: #edf6f1;
}
.review-table tbody tr.family-section-row {
  --row-bg: #e8eee9;
}
.review-table tbody tr.family-section-row th,
.review-table tbody tr.family-section-row td {
  border-top: 2px solid #a9bec8;
  border-bottom-color: #c5d5dc;
}
.review-table td:nth-child(1),
.cell-counts,
.cell-overlay,
.cell-details {
  text-align: center;
}
.cell-family,
.cell-state,
.cell-issue {
  text-align: center;
}
.family-id {
  color: #0f1c24;
  font-size: 14px;
  font-weight: 850;
  text-align: center;
}
.family-meta,
.seed-summary,
.seed-window {
  color: #60717d;
  text-align: center;
}
.family-context-cell .family-id {
  margin-right: 12px;
}
.family-context-cell .anchor-status,
.family-context-cell .pattern-status,
.family-context-cell .target-status,
.seed-count,
.shadow-pill,
.badge {
  border-radius: var(--radius-sm);
  background: #ffffff;
}
.seed-cell {
  border-left-color: #b6cbd4;
}
.seed-cell .seed-summary,
.seed-cell .seed-window {
  color: var(--red);
  font-weight: 850;
}
.state-line {
  grid-template-columns: auto auto;
  justify-content: center;
  width: 100%;
}
.state-key {
  color: #667886;
}
.state-stack {
  justify-items: center;
  text-align: center;
}
.cell-state .badge,
.shadow-pill {
  justify-self: center;
}
.shadow-pill,
.badge {
  padding: 4px 7px;
  box-shadow: 0 1px 0 rgba(17, 26, 34, 0.04);
}
.badge {
  font-weight: 800;
}
.badge.product_grade_support,
.badge.product_primary_backfilled,
.badge.product_accepts_and_product_grade_supports {
  border-left-color: var(--green);
}
.badge.review_only_visual_support,
.badge.product_rejects_but_visual_supports {
  border-left-color: var(--blue);
}
.badge.evidence_blocks_backfill,
.badge.product_accepts_but_evidence_conflicts,
.badge.product_rejects_and_evidence_blocks {
  border-left-color: var(--red);
}
.badge.product_rescued_context_only,
.badge.product_provisional,
.badge.not_assessable,
.badge.not_assessable_missing_overlay,
.badge.not_assessable_missing_seed_provenance,
.badge.not_assessable_join_gap {
  border-left-color: var(--amber);
}
.badge.evidence_inconclusive,
.badge.human_visual_judgment_only {
  border-left-color: var(--purple);
}
.top-issue {
  justify-items: center;
  margin-inline: auto;
  text-align: center;
}
.issue-text {
  color: #273842;
  padding-inline: 8px;
  text-align: center;
}
.count-stack {
  gap: 12px;
  flex-wrap: nowrap;
}
.count-stack div {
  min-width: 38px;
}
.count-stack dt {
  white-space: nowrap;
  overflow-wrap: normal;
}
.count-stack dd {
  color: #10202a;
}
.detail-toggle,
.lightbox-close,
.lightbox-direct {
  min-height: 34px;
  border-color: #b6c5ce;
  border-radius: var(--radius-sm);
  background: #ffffff;
  color: #17333d;
  cursor: pointer;
  transition:
    background 180ms var(--motion),
    border-color 180ms var(--motion),
    transform 160ms var(--motion),
    box-shadow 180ms var(--motion);
}
.detail-toggle:hover,
.lightbox-close:hover,
.lightbox-direct:hover {
  border-color: var(--blue);
  background: #edf6f1;
  box-shadow: 0 6px 16px rgba(43, 103, 88, 0.12);
}
.detail-toggle:active,
.lightbox-close:active,
.lightbox-direct:active {
  transform: translateY(1px);
}
.detail-toggle[aria-expanded="true"] {
  border-color: var(--blue);
  background: #dceee7;
  box-shadow: inset 0 0 0 1px rgba(43, 103, 88, 0.28);
}
.review-table .detail-row > td {
  background: #f5f8f5;
}
.detail-drawer {
  padding: 14px;
  border-left: 0;
  box-shadow: inset 5px 0 0 var(--blue);
}
.detail-drawer-head {
  padding: 2px 0 10px;
  border-bottom: 1px solid var(--line-soft);
}
.detail-drawer-head h2 {
  margin: 0;
  font-size: 19px;
  line-height: 1.15;
}
.detail-summary-grid {
  grid-template-columns: repeat(4, minmax(170px, 1fr));
}
.detail-summary-card,
.chain-item {
  border-color: #cfdae0;
  border-radius: var(--radius-sm);
  background: #ffffff;
  box-shadow: 0 6px 18px rgba(16, 31, 42, 0.06);
}
.detail-summary-card h3,
.chain-head h3 {
  color: #14242d;
  font-weight: 850;
}
.summary-subtitle,
.chain-state,
.secondary-chain summary small {
  color: #667886;
}
.chain-head,
.secondary-chain summary {
  background: #f1f6f8;
}
.chain-body,
.secondary-chain-body {
  background: #ffffff;
}
.shadow-policy-table th,
.target-benchmark-table th,
.seed-alias-table th,
.seed-table th,
.rep-table th {
  background: #e7eef2;
  color: #21323d;
  font-size: 11px;
  letter-spacing: 0.035em;
  text-transform: uppercase;
}
.shadow-policy-table td,
.target-benchmark-table td,
.seed-alias-table td,
.seed-table td,
.rep-table td {
  background: #ffffff;
}
.projection-authority-chain {
  border-left-color: var(--green);
}
.lightbox {
  background: rgba(9, 18, 24, 0.78);
  backdrop-filter: blur(6px);
}
.lightbox-panel {
  border: 1px solid rgba(255,255,255,0.32);
  border-radius: var(--radius);
  box-shadow: 0 26px 70px rgba(0,0,0,0.38);
}
.lightbox-header {
  background: rgba(255,255,255,0.96);
}
.lightbox-header h2 {
  font-size: 22px;
  line-height: 1.1;
}
.lightbox-image {
  border-radius: var(--radius-sm);
  background: #f8fafb;
}
.empty-state {
  padding: 22px;
  color: #40515d;
  font-weight: 750;
}
@media (max-width: 760px) {
  main {
    padding: 14px 10px 30px;
  }
  .gallery-hero {
    padding: 16px 15px 17px;
  }
  h1 {
    font-size: 30px;
  }
  .hero-status-strip span {
    width: 100%;
    justify-content: center;
  }
  .decision-legend {
    grid-template-columns: 1fr;
  }
  .filters {
    position: static;
    display: grid;
    grid-template-columns: 1fr;
  }
  .result-count {
    margin-left: 0;
    white-space: normal;
  }
  .review-table {
    min-width: 1120px;
  }
  .detail-summary-grid {
    grid-template-columns: 1fr;
  }
}
@media (prefers-reduced-motion: reduce) {
  html {
    scroll-behavior: auto;
  }
  *,
  *::before,
  *::after {
    transition-duration: 0.01ms !important;
  }
}
"""
