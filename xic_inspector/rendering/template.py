"""HTML template, CSS styles, and client-side JavaScript for XIC Inspector Variant A."""
# ruff: noqa: E501

from __future__ import annotations

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>__REPORT_TITLE__</title>
  <style>
    :root {
      --bg: #f4f6f8;
      --card: #ffffff;
      --panel: #ffffff;
      --border: #d0d7de;
      --border-light: #e1e4e8;
      --text: #24292f;
      --muted: #57606a;
      --blue: #0969da;
      --blue-bg: #edf5fa;
      --green: #1a7f37;
      --red: #cf222e;
      --orange: #bc4c00;
      --purple: #8250df;
      --gray: #6e7781;
      --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      color: var(--text);
      background: var(--bg);
      line-height: 1.5;
      padding: 16px;
    }
    header {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 16px 20px;
      margin-bottom: 16px;
    }
    .header-title { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 8px; }
    .header-title h1 { font-size: 20px; font-weight: 600; }
    .header-meta { font-size: 13px; color: var(--muted); margin-top: 6px; display: flex; flex-wrap: wrap; gap: 16px; }
    .filter-bar {
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--border-light);
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      font-size: 13px;
    }
    .filter-bar label { display: flex; align-items: center; gap: 6px; font-weight: 500; }
    .filter-bar input[type="text"], .filter-bar input[type="number"] {
      padding: 4px 8px;
      font-size: 13px;
      border: 1px solid var(--border);
      border-radius: 4px;
      outline: none;
    }
    .filter-bar input[type="number"] { width: 90px; }
    .filter-bar input:focus { border-color: var(--blue); }
    .filter-bar button {
      padding: 4px 10px;
      font-size: 13px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: #fff;
      cursor: pointer;
    }
    .filter-bar button:hover { background: #f6f8fa; }

    /* Workbench Layout */
    .workbench {
      display: grid;
      grid-template-columns: 310px minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }
    .panel {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 16px;
    }
    .list-panel {
      max-height: calc(100vh - 170px);
      overflow-y: auto;
      padding: 8px;
    }
    .list-header {
      font-size: 13px;
      font-weight: 600;
      color: var(--muted);
      padding: 4px 8px 8px;
      border-bottom: 1px solid var(--border-light);
      display: flex;
      justify-content: space-between;
    }
    .record-list { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
    .record-button {
      display: block;
      width: 100%;
      text-align: left;
      padding: 8px 10px;
      background: none;
      border: 1px solid transparent;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      color: inherit;
    }
    .record-button:hover { background: #f6f8fa; }
    .record-button.selected {
      background: var(--blue-bg);
      border-color: #b6d3f2;
      box-shadow: inset 3px 0 var(--blue);
    }
    .record-name {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-weight: 600;
      margin-bottom: 2px;
    }
    .record-meta { font-size: 12px; color: var(--muted); font-family: var(--font-mono); }
    .record-reason { font-size: 12px; color: var(--muted); margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    /* Badge */
    .badge {
      display: inline-block;
      font-size: 11px;
      font-weight: 600;
      padding: 1px 6px;
      border-radius: 12px;
      text-transform: uppercase;
    }
    .badge-ok { background: #dafbe1; color: var(--green); }
    .badge-conflict { background: #ffebe9; color: var(--red); }
    .badge-attention { background: #ffebe9; color: var(--red); }
    .badge-excluded { background: #fff8c5; color: var(--orange); }
    .badge-null { background: #eaeef2; color: var(--gray); }
    .badge-zero { background: #e8deff; color: var(--purple); }
    .badge-rest { background: #ddf4ff; color: var(--blue); }
    .badge-missing { background: #eaeef2; color: var(--gray); }

    /* Evidence Workspace */
    .detail-top {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      border-bottom: 1px solid var(--border-light);
      padding-bottom: 12px;
      margin-bottom: 14px;
    }
    .detail-eyebrow { font-size: 12px; font-weight: 500; color: var(--muted); margin-bottom: 4px; }
    .detail-title { font-size: 18px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
    .detail-meta { font-size: 13px; color: var(--muted); font-family: var(--font-mono); margin-top: 4px; }
    .finding-card {
      background: #f6f8fa;
      border-left: 3px solid var(--blue);
      padding: 10px 14px;
      border-radius: 0 4px 4px 0;
      font-size: 13px;
      margin-bottom: 16px;
    }
    .chart-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      font-size: 13px;
      margin-bottom: 12px;
    }
    .chart-toggles { display: flex; gap: 16px; align-items: center; }
    .chart-toggles label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
    .sample-legend {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      font-size: 12px;
      margin-bottom: 14px;
      padding: 8px 12px;
      background: #fcfcfc;
      border: 1px solid var(--border-light);
      border-radius: 4px;
    }
    .legend-item { display: flex; align-items: center; gap: 6px; cursor: pointer; }
    .legend-swatch { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }

    /* Charts */
    .charts-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-bottom: 18px;
    }
    @media (max-width: 900px) {
      .charts-grid { grid-template-columns: 1fr; }
      .workbench { grid-template-columns: 1fr; }
    }
    .plot-card {
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      background: #fff;
    }
    .plot-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
    .plot-header h4 { font-size: 13px; font-weight: 600; }
    .plot-header button {
      background: none;
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 2px 6px;
      font-size: 11px;
      cursor: pointer;
    }
    .plot-header button:hover { background: #f6f8fa; }
    .svg-container { width: 100%; height: 260px; }
    .svg-container svg { width: 100%; height: 100%; display: block; }
    .chart-caption { font-size: 11px; color: var(--muted); margin-top: 6px; }

    /* Sample Table */
    .sample-section h3 { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
    .sample-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    .sample-table th, .sample-table td {
      border: 1px solid var(--border-light);
      padding: 8px 10px;
      text-align: left;
    }
    .sample-table th { background: #f6f8fa; font-weight: 600; color: var(--muted); font-size: 12px; }
    .sample-table tr.focused-row { background: #f0f7ff; }
    .sample-table td.num { font-family: var(--font-mono); text-align: right; }
    .sample-btn {
      background: none;
      border: none;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      color: var(--blue);
      display: flex;
      align-items: center;
      gap: 6px;
    }

    /* Dialogs */
    dialog {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 20px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.15);
      background: #fff;
      max-width: 800px;
      width: 90vw;
      margin: auto;
    }
    dialog::backdrop { background: rgba(0,0,0,0.4); }
    .dialog-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
    .dialog-header h3 { font-size: 16px; font-weight: 600; }
    .dialog-header button {
      background: none;
      border: none;
      font-size: 18px;
      cursor: pointer;
      color: var(--muted);
    }
    .factlist { display: grid; grid-template-columns: 140px 1fr; gap: 8px 12px; font-size: 13px; margin-top: 10px; }
    .factlist dt { color: var(--muted); }
    .factlist dd { word-break: break-all; }
    .mono { font-family: var(--font-mono); }

    .empty-state {
      text-align: center;
      padding: 40px 20px;
      color: var(--muted);
    }
  </style>
</head>
<body>
  <header>
    <div class="header-title">
      <h1>__REPORT_TITLE__</h1>
      <span class="badge badge-__CAPABILITY_BADGE__">__EVIDENCE_CAPABILITY__</span>
    </div>
    <div class="header-meta">
      <span>策略：<strong>__STRATEGY__</strong></span>
      <span>請求批次：<code class="mono">__REQUEST_ID__</code></span>
      <span>母體／合格／選取：<strong>__RUN_TOTAL__ / __ELIGIBLE_COUNT__ / __SELECTED_COUNT__</strong></span>
      <span>來源 DB：<code class="mono">__SOURCE_DB_NAME__</code></span>
    </div>
    <div class="filter-bar" id="filters">
      <label>搜尋：<input type="text" id="search" placeholder="ID / 判定 / 數值"></label>
      <label>m/z 範圍：<input type="number" id="mz-min" step="any" placeholder="min"> ~ <input type="number" id="mz-max" step="any" placeholder="max"></label>
      <label>RT 範圍 (min)：<input type="number" id="rt-min" step="any" placeholder="min"> ~ <input type="number" id="rt-max" step="any" placeholder="max"></label>
      <button type="button" id="clear">重設篩選</button>
    </div>
  </header>

  <div class="workbench">
    <aside class="panel list-panel">
      <div class="list-header">
        <span>檢閱項目</span>
        <span id="list-count">__ITEM_COUNT__ 筆</span>
      </div>
      <div class="record-list" id="record-list"></div>
    </aside>

    <main class="panel detail-panel" id="detail-panel">
      <div id="workspace-content"></div>
    </main>
  </div>

  <dialog id="zoom-dialog">
    <div class="dialog-header">
      <h3 id="zoom-title">放大檢視</h3>
      <button type="button" id="zoom-close">×</button>
    </div>
    <div class="svg-container" id="zoom-container" style="height: 60vh;"></div>
  </dialog>

  <dialog id="details-dialog">
    <div class="dialog-header">
      <h3 id="details-title">判定與來源明細</h3>
      <button type="button" id="details-close">×</button>
    </div>
    <div id="details-body"></div>
  </dialog>

  <script id="inspector-data" type="application/json">
__INSPECTOR_DATA_JSON__
  </script>

  <script>
    (function() {
      const data = JSON.parse(document.getElementById('inspector-data').textContent);
      const items = data.items || [];
      const samples = data.samples || [];

      const SAMPLE_COLORS = [
        '#1d6fa3', '#16855b', '#6f46c7', '#b9781c', '#ab4d63', '#637d8d',
        '#2a9d8f', '#e76f51', '#457b9d', '#e63946', '#3a86ff', '#8338ec'
      ];
      samples.forEach((s, idx) => {
        if (!s.color) s.color = SAMPLE_COLORS[idx % SAMPLE_COLORS.length];
      });

      const state = {
        selectedId: items.length ? items[0].id : null,
        focusedSampleId: null,
        visibleSamples: new Set(samples.map(s => s.id)),
        showBounds: true,
        showProcessed: true,
        search: '',
        mzMin: null,
        mzMax: null,
        rtMin: null,
        rtMax: null,
      };

      function escapeHtml(str) {
        if (str == null) return '';
        return String(str)
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
          .replace(/'/g, '&#39;');
      }

      function formatNum(val, decimals = 4) {
        if (val == null) return '—';
        if (val === 0) return '0';
        return Number(val).toLocaleString(undefined, {
          minimumFractionDigits: 0,
          maximumFractionDigits: decimals
        });
      }

      function matchesFilter(item) {
        if (state.search) {
          const q = state.search.toLowerCase();
          const text = [
            item.id,
            item.title,
            item.stratum,
            item.reason,
            item.precursor_mz,
            item.rt_apex_min
          ].join(' ').toLowerCase();
          if (!text.includes(q)) return false;
        }
        if (state.mzMin != null && (item.precursor_mz == null || item.precursor_mz < state.mzMin)) return false;
        if (state.mzMax != null && (item.precursor_mz == null || item.precursor_mz > state.mzMax)) return false;
        if (state.rtMin != null && (item.rt_apex_min == null || item.rt_apex_min < state.rtMin)) return false;
        if (state.rtMax != null && (item.rt_apex_min == null || item.rt_apex_min > state.rtMax)) return false;
        return true;
      }

      function renderList() {
        const filtered = items.filter(matchesFilter);
        const listEl = document.getElementById('record-list');
        document.getElementById('list-count').textContent = filtered.length + ' 筆';

        if (filtered.length === 0) {
          listEl.innerHTML = '<div class="empty-state">無符合項目</div>';
          document.getElementById('workspace-content').innerHTML = '<div class="empty-state">調整篩選條件；查無符合項目。</div>';
          return;
        }

        if (!filtered.some(it => it.id === state.selectedId)) {
          state.selectedId = filtered[0].id;
          state.focusedSampleId = null;
        }

        listEl.innerHTML = filtered.map(it => {
          const isSel = it.id === state.selectedId;
          const badgeClass = 'badge-' + (it.badge || it.stratum || 'rest');
          return `
            <button class="record-button ${isSel ? 'selected' : ''}" data-id="${escapeHtml(it.id)}">
              <div class="record-name">
                <span>${escapeHtml(it.title || it.id)}</span>
                <span class="badge ${badgeClass}">${escapeHtml(it.stratum)}</span>
              </div>
              <div class="record-meta">
                m/z ${formatNum(it.precursor_mz)} · RT ${formatNum(it.rt_apex_min, 2)} min
              </div>
              <div class="record-reason">${escapeHtml(it.reason || '')}</div>
            </button>
          `;
        }).join('');

        renderDetail();
      }

      function renderDetail() {
        const item = items.find(it => it.id === state.selectedId);
        const ws = document.getElementById('workspace-content');
        if (!item) {
          ws.innerHTML = '<div class="empty-state">請由左側選取項目</div>';
          return;
        }

        const badgeClass = 'badge-' + (item.badge || item.stratum || 'rest');
        const focusText = state.focusedSampleId
          ? `聚焦樣本: <strong>${escapeHtml(state.focusedSampleId)}</strong> <button type="button" id="btn-unfocus" style="margin-left:6px;font-size:11px;padding:1px 5px;border:1px solid #ccc;border-radius:3px;cursor:pointer;">解除</button>`
          : '比較所有可見樣本';

        ws.innerHTML = `
          <div class="detail-top">
            <div>
              <div class="detail-eyebrow">${escapeHtml(item.eyebrow || '檢閱項目')} · <span class="mono">${escapeHtml(item.id)}</span></div>
              <div class="detail-title">
                <span>${escapeHtml(item.title || item.id)}</span>
                <span class="badge ${badgeClass}">${escapeHtml(item.stratum)}</span>
              </div>
              <div class="detail-meta">
                Precursor m/z: ${formatNum(item.precursor_mz)} · RT apex: ${formatNum(item.rt_apex_min, 2)} min
              </div>
            </div>
            <div>
              <button type="button" id="btn-show-details" style="padding:6px 12px;font-size:13px;border:1px solid var(--border);border-radius:4px;background:#fff;cursor:pointer;">判定與來源 ↗</button>
            </div>
          </div>

          <div class="finding-card">
            <strong>判定說明：</strong> ${escapeHtml(item.reason || '無異常判定')}
          </div>

          <div class="chart-toolbar">
            <div class="chart-toggles">
              <label><input type="checkbox" id="chk-bounds" ${state.showBounds ? 'checked' : ''}> 顯示已供給的峰界線</label>
              <label><input type="checkbox" id="chk-processed" ${state.showProcessed ? 'checked' : ''}> 疊加當次處理曲線</label>
            </div>
            <div>${focusText}</div>
          </div>

          <div class="sample-legend">
            ${samples.map(s => `
              <label class="legend-item">
                <input type="checkbox" data-sample-toggle="${escapeHtml(s.id)}" ${state.visibleSamples.has(s.id) ? 'checked' : ''}>
                <span class="legend-swatch" style="background:${s.color};"></span>
                <span>${escapeHtml(s.label || s.id)}</span>
              </label>
            `).join('')}
          </div>

          <div class="charts-grid">
            <div class="plot-card">
              <div class="plot-header">
                <h4>原始時間軸 (Raw RT)</h4>
                <button type="button" data-zoom="raw">放大 ↗</button>
              </div>
              <div class="svg-container" id="svg-raw">${renderSvg(item, 'raw')}</div>
              <div class="chart-caption">實線：原始 XIC；虛線：處理曲線；垂直虛線：選定峰界線</div>
            </div>

            <div class="plot-card">
              <div class="plot-header">
                <h4>對齊時間軸 (Aligned RT)</h4>
                <button type="button" data-zoom="aligned">放大 ↗</button>
              </div>
              <div class="svg-container" id="svg-aligned">${renderSvg(item, 'aligned')}</div>
              <div class="chart-caption">僅顯示科學 owner 提供的對齊時間座標；若無則顯示 unavailable</div>
            </div>
          </div>

          <div class="sample-section">
            <h3>各樣本定量與決策明細</h3>
            <table class="sample-table">
              <thead>
                <tr>
                  <th>樣本</th>
                  <th>狀態</th>
                  <th style="text-align:right;">定量結果</th>
                  <th>判定理由 / 證據</th>
                </tr>
              </thead>
              <tbody>
                ${(item.samples || []).map(s => {
                  const isFocused = s.sample_id === state.focusedSampleId;
                  const sDef = samples.find(x => x.id === s.sample_id) || {};
                  const sColor = sDef.color || '#666';
                  return `
                    <tr class="${isFocused ? 'focused-row' : ''}">
                      <td>
                        <button type="button" class="sample-btn" data-focus-sample="${escapeHtml(s.sample_id)}">
                          <span class="legend-swatch" style="background:${sColor};"></span>
                          <span>${escapeHtml(s.sample_stem || s.sample_id)}</span>
                        </button>
                      </td>
                      <td><span class="badge badge-${escapeHtml(s.status_kind || 'rest')}">${escapeHtml(s.status_label || s.status_kind || 'OK')}</span></td>
                      <td class="num">${formatNum(s.quantitative_value)}</td>
                      <td>${escapeHtml(s.decision_reason || '—')}</td>
                    </tr>
                  `;
                }).join('')}
              </tbody>
            </table>
          </div>
        `;
      }

      function renderSvg(item, axisKind) {
        const curves = (item.curves || []).filter(c => state.visibleSamples.has(c.sample_id));
        if (!curves.length) {
          return '<div class="empty-state" style="padding-top:80px;">無可見樣本曲線</div>';
        }

        // Gather bounds
        let minX = Infinity, maxX = -Infinity, maxY = 0;
        curves.forEach(c => {
          const rts = axisKind === 'aligned' ? (c.aligned_rts || c.raw_rts) : c.raw_rts;
          if (!rts || !rts.length) return;
          rts.forEach((x, i) => {
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            const y = c.raw_intensities[i] || 0;
            if (y > maxY) maxY = y;
          });
        });

        if (minX >= maxX || maxY <= 0) {
          if (axisKind === 'aligned') {
            return '<div class="empty-state" style="padding-top:80px;">對齊座標未提供 (unavailable)</div>';
          }
          return '<div class="empty-state" style="padding-top:80px;">曲線未提供或全 0</div>';
        }

        const W = 540, H = 250, pL = 50, pR = 20, pT = 20, pB = 35;
        const innerW = W - pL - pR;
        const innerH = H - pT - pB;

        const scaleX = x => pL + ((x - minX) / (maxX - minX)) * innerW;
        const scaleY = y => pT + innerH - (y / maxY) * innerH;

        let svgContent = '';

        // Axes grid
        svgContent += `<line x1="${pL}" y1="${pT}" x2="${pL}" y2="${pT + innerH}" stroke="#d0d7de" stroke-width="1"/>`;
        svgContent += `<line x1="${pL}" y1="${pT + innerH}" x2="${pL + innerW}" y2="${pT + innerH}" stroke="#d0d7de" stroke-width="1"/>`;

        // Ticks
        for (let i = 0; i <= 4; i++) {
          const xVal = minX + (i / 4) * (maxX - minX);
          const px = scaleX(xVal);
          svgContent += `<text x="${px}" y="${pT + innerH + 16}" font-size="10" fill="#57606a" text-anchor="middle">${xVal.toFixed(2)}</text>`;
        }
        svgContent += `<text x="${pL + innerW / 2}" y="${H - 4}" font-size="11" fill="#24292f" text-anchor="middle">RT (min)</text>`;

        // Draw curves
        curves.forEach(c => {
          const sDef = samples.find(x => x.id === c.sample_id) || {};
          const color = sDef.color || '#1d6fa3';
          const isFocused = state.focusedSampleId === c.sample_id;
          const opacity = state.focusedSampleId ? (isFocused ? 1.0 : 0.2) : 0.85;
          const strokeW = isFocused ? 2.8 : 1.6;

          const rts = axisKind === 'aligned' ? (c.aligned_rts || c.raw_rts) : c.raw_rts;
          const rawInts = c.raw_intensities || [];

          if (rts && rts.length) {
            const pts = rts.map((x, i) => `${scaleX(x).toFixed(1)},${scaleY(rawInts[i] || 0).toFixed(1)}`).join(' ');
            svgContent += `<polyline fill="none" stroke="${color}" stroke-width="${strokeW}" opacity="${opacity}" points="${pts}"/>`;
          }

          // Processed curves
          if (state.showProcessed && c.processed_rts && c.processed_intensities) {
            const pPts = c.processed_rts.map((x, i) => `${scaleX(x).toFixed(1)},${scaleY(c.processed_intensities[i] || 0).toFixed(1)}`).join(' ');
            svgContent += `<polyline fill="none" stroke="${color}" stroke-dasharray="5 3" stroke-width="1.2" opacity="${opacity}" points="${pPts}"/>`;
          }

          // Peak bounds
          if (state.showBounds && c.bound_left_rt != null && c.bound_right_rt != null) {
            if (!state.focusedSampleId || isFocused) {
              const bL = scaleX(c.bound_left_rt);
              const bR = scaleX(c.bound_right_rt);
              svgContent += `<line x1="${bL}" y1="${pT}" x2="${bL}" y2="${pT + innerH}" stroke="${color}" stroke-dasharray="3 3" stroke-width="1" opacity="0.7"/>`;
              svgContent += `<line x1="${bR}" y1="${pT}" x2="${bR}" y2="${pT + innerH}" stroke="${color}" stroke-dasharray="3 3" stroke-width="1" opacity="0.7"/>`;
            }
          }
        });

        return `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${svgContent}</svg>`;
      }

      // Event Listeners
      document.getElementById('record-list').addEventListener('click', e => {
        const btn = e.target.closest('.record-button');
        if (!btn) return;
        state.selectedId = btn.dataset.id;
        state.focusedSampleId = null;
        renderList();
      });

      document.getElementById('detail-panel').addEventListener('click', e => {
        if (e.target.id === 'btn-unfocus') {
          state.focusedSampleId = null;
          renderDetail();
        }
        const sBtn = e.target.closest('[data-focus-sample]');
        if (sBtn) {
          const sid = sBtn.dataset.focusSample;
          state.focusedSampleId = state.focusedSampleId === sid ? null : sid;
          renderDetail();
        }
        if (e.target.id === 'btn-show-details') {
          const item = items.find(it => it.id === state.selectedId);
          if (!item) return;
          document.getElementById('details-title').textContent = item.id + ' · 判定與來源明細';
          document.getElementById('details-body').innerHTML = `
            <dl class="factlist">
              <dt>項目識別碼</dt><dd class="mono">${escapeHtml(item.id)}</dd>
              <dt>請求識別碼</dt>
              <dd class="mono">${escapeHtml(data.summary?.processing_request_id || '—')}</dd>
              <dt>嘗試識別碼</dt>
              <dd class="mono">${escapeHtml(data.summary?.processing_attempt_id || '—')}</dd>
              <dt>Precursor m/z</dt>
              <dd class="mono">${formatNum(item.precursor_mz)}</dd>
              <dt>RT apex (min)</dt>
              <dd class="mono">${formatNum(item.rt_apex_min, 2)}</dd>
              <dt>抽樣分層</dt><dd class="mono">${escapeHtml(item.stratum)}</dd>
              <dt>主要判定理由</dt><dd>${escapeHtml(item.reason || '無')}</dd>
            </dl>
          `;
          document.getElementById('details-dialog').showModal();
        }
        const zoomBtn = e.target.closest('[data-zoom]');
        if (zoomBtn) {
          const item = items.find(it => it.id === state.selectedId);
          if (!item) return;
          const axis = zoomBtn.dataset.zoom;
          const axisLabel = axis === 'aligned' ? '對齊時間軸' : '原始時間軸';
          document.getElementById('zoom-title').textContent = `${item.id} · ${axisLabel}`;
          document.getElementById('zoom-container').innerHTML = renderSvg(item, axis);
          document.getElementById('zoom-dialog').showModal();
        }
      });

      document.getElementById('detail-panel').addEventListener('change', e => {
        if (e.target.id === 'chk-bounds') {
          state.showBounds = e.target.checked;
          renderDetail();
        }
        if (e.target.id === 'chk-processed') {
          state.showProcessed = e.target.checked;
          renderDetail();
        }
        if (e.target.dataset.sampleToggle) {
          const sid = e.target.dataset.sampleToggle;
          if (e.target.checked) state.visibleSamples.add(sid);
          else state.visibleSamples.delete(sid);
          renderDetail();
        }
      });

      // Filter Inputs
      function onFilterChange() {
        state.search = document.getElementById('search').value.trim();
        const mzMin = parseFloat(document.getElementById('mz-min').value);
        state.mzMin = isNaN(mzMin) ? null : mzMin;
        const mzMax = parseFloat(document.getElementById('mz-max').value);
        state.mzMax = isNaN(mzMax) ? null : mzMax;
        const rtMin = parseFloat(document.getElementById('rt-min').value);
        state.rtMin = isNaN(rtMin) ? null : rtMin;
        const rtMax = parseFloat(document.getElementById('rt-max').value);
        state.rtMax = isNaN(rtMax) ? null : rtMax;
        renderList();
      }

      ['search', 'mz-min', 'mz-max', 'rt-min', 'rt-max'].forEach(id => {
        document.getElementById(id).addEventListener('input', onFilterChange);
      });

      document.getElementById('clear').addEventListener('click', () => {
        ['search', 'mz-min', 'mz-max', 'rt-min', 'rt-max'].forEach(id => {
          document.getElementById(id).value = '';
        });
        onFilterChange();
      });

      // Dialog Close Buttons & Backdrop click
      document.getElementById('zoom-close').addEventListener('click', () => {
        document.getElementById('zoom-dialog').close();
      });
      document.getElementById('details-close').addEventListener('click', () => {
        document.getElementById('details-dialog').close();
      });
      const dialogList = [
        document.getElementById('zoom-dialog'),
        document.getElementById('details-dialog'),
      ];
      dialogList.forEach(d => {
        d.addEventListener('click', e => {
          if (e.target === d) d.close();
        });
      });

      // Keyboard navigation
      document.addEventListener('keydown', e => {
        if (document.querySelector('dialog[open]')) {
          if (e.key === 'Escape') {
            document.querySelectorAll('dialog[open]').forEach(d => d.close());
          }
          return;
        }
        if (e.target.closest('input, textarea, select')) return;

        const filtered = items.filter(matchesFilter);
        if (!filtered.length) return;
        const curIdx = filtered.findIndex(it => it.id === state.selectedId);

        if (e.key === 'ArrowDown') {
          e.preventDefault();
          const nextIdx = Math.min(filtered.length - 1, curIdx + 1);
          state.selectedId = filtered[nextIdx].id;
          state.focusedSampleId = null;
          renderList();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          const prevIdx = Math.max(0, curIdx - 1);
          state.selectedId = filtered[prevIdx].id;
          state.focusedSampleId = null;
          renderList();
        }
      });

      // Initial render
      renderList();
    })();
  </script>
</body>
</html>
"""
