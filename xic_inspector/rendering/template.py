"""HTML template, CSS styles, and client-side JavaScript for XIC Inspector Variant A."""
# ruff: noqa: E501

from __future__ import annotations

from xic_inspector.rendering.javascript import WORKBENCH_JS

_SHELL = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>__REPORT_TITLE__</title>
  <meta name="xic-request-id" content="__REQUEST_ID__">
  <meta name="xic-evidence-capability" content="__EVIDENCE_CAPABILITY__">
  <meta name="xic-source-name" content="__SOURCE_DB_NAME__">
  <style>
    :root {
      --bg: #f5f7f8;
      --card: #ffffff;
      --panel: #ffffff;
      --border: #d0d7de;
      --border-light: #e1e4e8;
      --text: #17202a;
      --muted: #5a6673;
      --blue: #1d6fa3;
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
      font-family: Aptos, "Segoe UI Variable", "Segoe UI", system-ui, sans-serif;
      color: var(--text);
      background: var(--bg);
      line-height: 1.5;
      padding: 24px 28px;
      max-width: 1510px;
      margin: auto;
    }
    header {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 16px 20px;
      margin-bottom: 16px;
    }
    .header-title { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 8px; }
    .header-title h1 { font-size: 23px; font-weight: 650; }
    .brand { display: flex; gap: 10px; align-items: center; }
    .brand-mark { background: var(--text); color: white; border-radius: 4px; padding: 9px 6px; font-size: 13px; font-weight: 700; }
    .report-actions { display: flex; gap: 12px; align-items: center; }
    .notice { text-align: center; background: #edf2f5; padding: 8px 14px; margin: -24px -28px 18px; font-size: 12px; color: var(--muted); }
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
      grid-template-columns: 288px minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }
    .panel {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 22px;
      min-width: 0;
    }
    .list-panel {
      max-height: calc(100vh - 170px);
      overflow-y: auto;
      padding: 0;
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
    .list-header { padding: 14px 17px; }
    .record-list { display: flex; flex-direction: column; }
    .record-button {
      display: block;
      width: 100%;
      text-align: left;
      padding: 16px 17px;
      background: none;
      border: 0;
      border-bottom: 1px solid var(--border-light);
      border-radius: 0;
      cursor: pointer;
      font-size: 15px;
      color: inherit;
    }
    .record-button:hover { background: #f6f8fa; }
    .record-button.selected {
      background: var(--blue-bg);
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
    .record-reason { font-size: 12px; color: var(--muted); margin-top: 9px; line-height: 1.6; }
    .record-name { align-items: flex-start; gap: 7px; }
    .record-name > span:first-child { min-width: 0; }

    /* Badge */
    .badge {
      display: inline-block;
      font-size: 11px;
      font-weight: 600;
      padding: 1px 6px;
      border-radius: 4px;
      text-transform: uppercase;
    }
    .badge-ok { background: #dafbe1; color: var(--green); }
    .badge-conflict { background: #ffebe9; color: var(--red); }
    .badge-attention { background: #fff0d5; color: #865000; }
    .badge-excluded { background: #f0ebf8; color: #66438c; }
    .badge-null { background: #eaeef2; color: var(--gray); }
    .badge-zero { background: #e8deff; color: var(--purple); }
    .badge-rest { background: #edf2f5; color: #536372; }
    .badge { white-space: nowrap; font-weight: 500; padding: 3px 8px; }
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
    .detail-meta { font-size: 13px; color: var(--muted); margin-top: 10px; display: flex; flex-wrap: wrap; gap: 16px; }
    .detail-meta strong { font-family: var(--font-mono); font-weight: 500; }
    .detail-actions { display: grid; justify-items: end; gap: 10px; flex: none; }
    .finding-card {
      border-bottom: 1px solid var(--border-light);
      padding: 2px 0 16px;
      font-size: 14px;
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
      padding: 0;
    }
    .legend-item { display: flex; align-items: center; gap: 6px; cursor: pointer; }
    .legend-swatch { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .sample-legend label, .chart-toolbar label { display: flex; align-items: center; gap: 5px; }
    .legend-line { display: inline-block; width: 14px; height: 3px; }
    .candidate-bar { font-size: 12px; margin: -2px 0 12px; }
    .candidate-bar select { max-width: 300px; margin-left: 6px; }
    .focus-status { color: var(--muted); font-size: 12px; margin-left: auto; }

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
      border: 1px solid var(--border-light);
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
    .svg-container { width: 100%; aspect-ratio: 540 / 250; }
    .svg-container svg { width: 100%; height: auto; display: block; }
    #zoom-container svg { height: 100%; }
    .chart-caption { font-size: 11px; color: var(--muted); margin-top: 6px; }

    /* Sample Table */
    .sample-section h3 { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
    .sample-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    .sample-table th, .sample-table td {
      border-bottom: 1px solid var(--border-light);
      padding: 8px 10px;
      text-align: left;
    }
    .sample-table th { background: #f6f8fa; font-weight: 600; color: var(--muted); font-size: 12px; }
    .sample-table tr.focused-row { background: #f0f7ff; }
    .sample-table td.num { font-family: var(--font-mono); text-align: right; }
    .sample-table th.num { text-align: right; }
    .table-scroll { overflow-x: auto; }
    .reading-hint { color: var(--muted); font-size: 12px; margin: 6px 0 12px; }
    .finding-card .reading-hint { margin-bottom: 0; }
    .detail-top h2 { font-size: 21px; font-weight: 650; }
    .record-meta { margin-top: 5px; line-height: 1.7; }
    .sample-section { margin-bottom: 22px; }
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
      max-height: 88vh;
      overflow-y: auto;
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
    details { margin: 12px 0; padding: 8px; border: 1px solid var(--border-light); border-radius: 4px; }
    summary { cursor: pointer; font-weight: 600; }
    .detail-panel h2, .detail-panel .mono { overflow-wrap: anywhere; }
    button, select { padding: 5px 8px; border: 1px solid var(--border); border-radius: 4px; background: white; color: var(--text); cursor: pointer; }
    select { max-width: 100%; }
    :focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
    [hidden] { display: none !important; }
    button:disabled { cursor: default; opacity: .45; }
    button, input, select { font: inherit; }
    input[type="checkbox"] { accent-color: var(--blue); }
    #details-body { display: grid; gap: 24px; }
    #details-body h3 { margin-bottom: 8px; font-size: 15px; }
    #details-body p { font-size: 13px; }
    #details-body .sample-table { font-size: 12px; }
    @media (max-width: 740px) {
      body { padding: 14px; }
      .panel { padding: 15px; }
      .list-panel { padding: 0; max-height: 280px; }
      .record-list { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); }
      .record-button { padding: 12px; font-size: 13px; }
      .record-reason { display: none; }
      .record-meta { font-size: 11px; }
      .detail-top { flex-wrap: wrap; gap: 10px; }
      .chart-toolbar select { max-width: 230px; }
      .notice { margin: -14px -14px 14px; }
    }
  </style>
</head>
<body>
  <div class="notice" id="report-notice" hidden></div>
  <header>
    <div class="header-title">
      <div class="brand"><span class="brand-mark" aria-hidden="true">XIC</span><div><h1>__REPORT_TITLE__</h1><p class="reading-hint">既有結果檢閱</p></div></div>
      <div class="report-actions"><span class="badge badge-__CAPABILITY_BADGE__">__CAPABILITY_LABEL__</span><button id="btn-report-info" type="button">報告資訊</button></div>
    </div>
    <div class="header-meta">
      <span><strong>__STRATEGY_LABEL__</strong> · 這份報告包含 __SELECTED_COUNT__ 個項目</span>
      <span>左側選項目，右側對照曲線與樣本結果。</span>
    </div>
    <div class="filter-bar" id="filters">
      <label>搜尋：<input type="text" id="search" placeholder="項目、樣本、m/z 或判定"></label>
      <label>m/z 範圍：<input type="number" id="mz-min" step="0.0001" placeholder="下限"> ~ <input type="number" id="mz-max" step="0.0001" placeholder="上限"></label>
      <label>RT 範圍 (min)：<input type="number" id="rt-min" step="0.01" placeholder="起點"> ~ <input type="number" id="rt-max" step="0.01" placeholder="終點"></label>
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

  <div id="report-info" hidden><section><h3>報告範圍</h3><dl class="factlist"><dt>檢閱方式</dt><dd>__STRATEGY_LABEL__</dd><dt>包含項目</dt><dd>__SELECTED_COUNT__ 筆</dd><dt>運算中的項目</dt><dd>共 __RUN_TOTAL__ 筆，其中 __ELIGIBLE_COUNT__ 筆可供本次選取</dd><dt>可用資料</dt><dd>__CAPABILITY_LABEL__</dd></dl></section></div>

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

  <script>__WORKBENCH_JS__</script>
</body>
</html>
"""

HTML_TEMPLATE = _SHELL.replace("__WORKBENCH_JS__", WORKBENCH_JS)
