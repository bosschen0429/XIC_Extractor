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
    pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; font-size: 12px; }
    details { margin: 12px 0; padding: 8px; border: 1px solid var(--border-light); border-radius: 4px; }
    summary { cursor: pointer; font-weight: 600; }
    .detail-panel h2, .detail-panel .mono { overflow-wrap: anywhere; }
    button, select { padding: 5px 8px; border: 1px solid var(--border); border-radius: 4px; background: white; color: var(--text); cursor: pointer; }
    select { max-width: 100%; }
    :focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
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

  <script>__WORKBENCH_JS__</script>
</body>
</html>
"""

HTML_TEMPLATE = _SHELL.replace("__WORKBENCH_JS__", WORKBENCH_JS)
