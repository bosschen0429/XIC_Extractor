(() => {
  let nextInstance = 0;
  const mounted = new WeakMap();
  // The page owns the curve display choice: every live view, and a view a host
  // mounts later for another group, draws curves on the same signal basis.
  const display = {signal: 'smoothed', overlays: new Set()};
  const displayViews = new Set();
  const signalSwitchMarkup = `<div class="signal-switch" role="group" aria-label="曲線訊號（套用所有項目）" title="套用到所有項目與檢視" hidden><div class="axis-switch"><button type="button" data-signal="raw">原始</button><button type="button" data-signal="smoothed">平滑</button></div></div>`;
  const detailMarkup = `
<main class="panel detail-panel" id="detail-panel"><div id="workspace-content"></div></main>
`;
  const dialogMarkup = `
<dialog id="zoom-dialog"><div class="dialog-header"><h3 id="zoom-title">放大檢視</h3><button type="button" id="zoom-close" aria-label="關閉放大圖">×</button></div><div class="svg-container zoom-container" id="zoom-container"></div></dialog>
<dialog id="report-info-dialog" class="report-dialog"><div class="dialog-header"><h3>關於這份報告</h3><button type="button" id="report-info-close" aria-label="關閉報告資訊">×</button></div><div id="report-info-body"></div></dialog>
`;
  const offlineMarkup = `
<div class="notice" id="report-notice" hidden></div>
  <header>
    <div class="header-main"><div class="header-copy"><span class="brand-mark" aria-hidden="true">XIC</span><div><h1>%%REPORT_TITLE%%</h1><p class="scope-summary"><strong>%%STRATEGY_LABEL%%</strong><span>%%SELECTED_COUNT%% / %%RUN_TOTAL%% 個項目</span><span id="loaded-range"></span></p></div></div><div class="header-actions">${signalSwitchMarkup}<span class="badge badge-%%CAPABILITY_BADGE%%">%%CAPABILITY_LABEL%%</span><button id="btn-report-info" type="button">報告資訊</button></div></div>
    <div class="filter-line"><label class="search-control" for="search"><span class="sr-only">本頁搜尋</span><input type="text" id="search" placeholder="搜尋項目、樣本、m/z 或判定"></label><details class="advanced-filters"><summary>進階篩選</summary><div class="filter-fields"><label>m/z <input type="number" id="mz-min" step="0.0001" placeholder="下限">–<input type="number" id="mz-max" step="0.0001" placeholder="上限"></label><label>RT（min）<input type="number" id="rt-min" step="0.01" placeholder="起點">–<input type="number" id="rt-max" step="0.01" placeholder="終點"></label><button type="button" id="clear">重設篩選</button></div></details><div class="active-filters" id="active-filters"></div></div>
  </header>
  <div class="workbench"><aside class="panel list-panel"><div class="list-header"><span>檢閱項目</span><span id="list-count">%%ITEM_COUNT%% 筆</span></div><div class="record-list" id="record-list"></div></aside>%%DETAIL_SURFACE%%</div>
  <div id="report-info-source" data-strategy="%%STRATEGY%%" data-strategy-label="%%STRATEGY_LABEL%%" data-source-name="%%SOURCE_DB_NAME%%" data-capability-label="%%CAPABILITY_LABEL%%" data-run-total="%%RUN_TOTAL%%" data-eligible-count="%%ELIGIBLE_COUNT%%" hidden></div>
%%DIALOG_SURFACE%%
`;

  function mount(container, data, options = {}) {
  const mode = options.mode || 'offline';
  if (!['offline', 'result', 'curves'].includes(mode)) throw new TypeError('Unknown Inspector mode');
  mounted.get(container)?.destroy();
  const root = document.createElement('div');
  root.className = 'xic-inspector';
  root.dataset.mode = mode;
  const instanceId = `xic-inspector-${++nextInstance}-`;
  // Hosts own acquisition and navigation; offline never exposes a live request.
  const onRequestCurves = mode !== 'offline' && typeof options.onRequestCurves === 'function' ? options.onRequestCurves : null;
  // The host owns writes; the Inspector only asks, and never offline.
  const onRequestExclusion = mode !== 'offline' && typeof options.onRequestExclusion === 'function' ? options.onRequestExclusion : null;
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const $ = id => root.querySelector(`[data-inspector-id="${id}"]`);
  let destroyed = false;
  const cleanups = [], frames = new Set(), timers = new Set();
  let deltaView = null;
  cleanups.push(() => deltaView?.destroy());
  const instance = {element: root, destroy() {
    if (destroyed) return;
    destroyed = true;
    cleanups.forEach(cleanup => cleanup());
    frames.forEach(id => cancelAnimationFrame(id));
    timers.forEach(id => clearTimeout(id));
    root.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
    root.remove();
    if (mounted.get(container) === instance) mounted.delete(container);
  }};
  const afterPaint = callback => {
    const id = requestAnimationFrame(() => { frames.delete(id); if (!destroyed) callback(); });
    frames.add(id);
  };
  const later = (callback, delay) => {
    const id = setTimeout(() => { timers.delete(id); if (!destroyed) callback(); }, delay);
    timers.add(id);
  };
  function setHTML(element, content) {
    // Prefix DOM and SVG reference identities at the markup boundary, not in
    // the scientific presentation helpers that produce the same plot for both hosts.
    element.innerHTML = String(content)
      .replace(/(^|\s)id="([^"]+)"/g, (_, space, id) => `${space}data-inspector-id="${id}" id="${instanceId}${id}"`)
      .replace(/(^|\s)for="([^"]+)"/g, (_, space, id) => `${space}for="${instanceId}${id}"`)
      .replace(/url\(#([^)]+)\)/g, (_, id) => `url(#${instanceId}${id})`);
  }
  container.append(root);
  mounted.set(container, instance);

  if (data.summary?.source_kind === 'targeted_workbook') {
    renderTargetedWorkbook(data);
    return instance;
  }
  if (data.items?.length && data.items.every(item => item.curves?.some(curve => curve.quality_flags?.target_label))) {
    renderTargetedCurves(data);
    return instance;
  }

  function renderTargetedCurves(payload) {
    // A target and its ISTD are two known ions, not competing candidates.
    // Share their time axis, but keep absolute intensity scales independent.
    const node = (tag, text, cls) => {
      const el = document.createElement(tag);
      if (text !== undefined) el.textContent = text;
      if (cls) el.className = cls;
      return el;
    };
    const finite = value => typeof value === 'number' && Number.isFinite(value);
    const number = value => finite(value) ? Number(value.toPrecision(4)).toLocaleString('en-US') : '—';
    const area = value => finite(value) ? value.toExponential(2) : String(value ?? '—');
    const offline = mode === 'offline';
    const review = node('main', undefined, 'tx-review');
    root.classList.add('tx-body');
    root.append(review);
    const heading = node('header', undefined, 'tx-heading');
    if (offline) heading.append(node('h1', 'Targeted XIC'));
    const sampleName = node('p', '', `tx-sample-name${!offline ? ' sr-only' : ''}`);
    heading.append(sampleName);
    const itemSelect = node('select'); itemSelect.setAttribute('aria-label', '目標');
    for (const [index, item] of payload.items.entries()) {
      itemSelect.append(new Option(item.curves.find(c => !c.quality_flags.paired_istd)?.quality_flags.target_label || item.title, String(index)));
    }
    if (payload.items.length > 1) heading.append(itemSelect);
    const sampleSelect = node('select'); sampleSelect.setAttribute('aria-label', '樣本');
    heading.append(sampleSelect); review.append(heading);
    const toolbar = node('div', undefined, 'tx-toolbar');
    const legend = node('div', undefined, 'tx-legend');
    legend.append(node('span', '原始 scan 點', 'tx-raw-key'), node('span', '平滑線', 'tx-smooth-key'));
    const boundsLabel = node('label', undefined, 'tx-bounds');
    const bounds = node('input'); bounds.type = 'checkbox'; bounds.checked = true;
    boundsLabel.append(bounds, document.createTextNode('已保存峰界'));
    toolbar.append(legend, boundsLabel, node('span', '同一 RT 範圍 · 強度各自縮放', 'tx-scale-note'));
    const range = node('form', undefined, 'tx-range');
    const start = node('input'), end = node('input');
    for (const [field, label] of [[start, '顯示 RT 起點'], [end, '顯示 RT 終點']]) {
      field.type = 'number'; field.step = 'any'; field.required = true; field.setAttribute('aria-label', label);
      field.addEventListener('input', () => start.setCustomValidity(''));
    }
    const apply = node('button', '套用範圍'); apply.type = 'submit';
    const nearby = node('button', '峰附近'), full = node('button', '完整 RT');
    nearby.type = full.type = 'button';
    range.append(node('span', 'RT'), start, node('span', '–'), end, node('span', 'min'), apply, nearby, full);
    review.append(toolbar, range);
    const charts = node('div', undefined, 'tx-charts'); review.append(charts);
    const provenance = node('details', undefined, 'tx-provenance');
    provenance.append(node('summary', '本次 RAW 讀取設定 · 尚未比對是否為分析原檔'));
    provenance.append(node('p', '曲線已從目前選擇的 RAW 成功讀取，依樣本名稱配對。工作簿未保留可比對的原檔指紋，因此不能確認與原分析使用的是同一份檔案。峰界與面積沿用已保存結果，不重新積分；請確認下方 RAW 資料夾與檔名正確。'));
    const extraction = node('p'); provenance.append(extraction); review.append(provenance);
    let item, sample, rtWindow;
    const traces = () => item.curves.filter(curve => curve.sample_id === sample);
    const extent = () => [Math.min(...traces().map(c => c.raw_rts[0])), Math.max(...traces().map(c => c.raw_rts[c.raw_rts.length - 1]))];
    function setWindow(value) {
      rtWindow = value;
      start.value = String(Number(value[0].toFixed(4)));
      end.value = String(Number(value[1].toFixed(4)));
      start.setCustomValidity('');
      draw();
    }
    function draw() {
      charts.replaceChildren();
      const width = Math.max(300, root.clientWidth - 34), height = 200;
      const [lo, hi] = rtWindow, left = 58, right = width - 14, top = 14, bottom = height - 30;
      const x = value => left + (value - lo) / (hi - lo) * (right - left);
      for (const curve of traces()) {
        const flags = curve.quality_flags, paired = flags.paired_istd;
        const card = node('section', undefined, `tx-chart${paired ? ' tx-istd' : ''}`);
        const head = node('div', undefined, 'tx-chart-heading');
        const identity = node('div');
        identity.append(node('span', paired ? '配對 ISTD' : flags.target_role === 'ISTD' ? 'ISTD' : '目標', 'tx-role'), node('h2', flags.target_label, 'tx-chart-title'));
        const metadata = node('p', `m/z ${curve.provenance.mz.toFixed(4)} · 保存 RT ${finite(curve.apex_rt) ? curve.apex_rt.toFixed(4) + ' min' : '—'} · 面積 ${area(flags.saved_area)}`, 'tx-chart-meta');
        head.append(identity, metadata); card.append(head);
        const failures = flags.reference_support_failures || [];
        const supportReason = failures.includes('insufficient_original_xic_support')
          ? '原始訊號支撐不足：近內標段少於 2 個高於基線的原始 scan；平滑展開的鐘形不代表完整峰。'
          : failures.includes('insufficient_original_xic_continuity')
          ? '原始訊號支撐不足：近內標段沒有連續的有效原始 scan。' : '';
        if (supportReason) {
          card.append(node('p', `ND 訊號檢查 · ${supportReason} 這是本次讀取 XIC 的檢查結果；原始分析來源未驗證，不改寫已保存判定。`, 'tx-support-note'));
        }
        const series = curve.processed_curves.find(s => s.kind === 'smoothed');
        const points = series?.rts.flatMap((rt, i) => rt >= lo && rt <= hi && finite(series.intensities[i]) ? [[rt, series.intensities[i]]] : []) || [];
        const rawPoints = curve.raw_rts.flatMap((rt, i) => rt >= lo && rt <= hi && finite(curve.raw_intensities[i]) ? [[rt, curve.raw_intensities[i]]] : []);
        if (!points.length) {
          card.append(node('p', '此範圍沒有訊號點，請調整 RT 範圍。', 'tx-empty')); charts.append(card); continue;
        }
        let low = 0, high = 0;
        [...points, ...rawPoints].forEach(([, value]) => { low = Math.min(low, value); high = Math.max(high, value); });
        if (low === high) high = low + 1;
        const y = value => bottom - (value - low) / (high - low) * (bottom - top);
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.setAttribute('role', 'img');
        svg.setAttribute('aria-label', `${flags.target_label} XIC，RT ${number(lo)} 到 ${number(hi)} 分鐘`);
        let markup = '';
        for (let i = 0; i <= 3; i++) {
          const value = low + (high - low) * i / 3, yp = y(value);
          markup += `<line class="tx-grid" x1="${left}" x2="${right}" y1="${yp}" y2="${yp}"/><text x="${left - 9}" y="${yp + 4}" text-anchor="end">${value === 0 ? '0' : Math.abs(value) >= 1e5 ? value.toExponential(1) : number(value)}</text>`;
        }
        for (let i = 0; i <= 4; i++) {
          const value = lo + (hi - lo) * i / 4;
          markup += `<text x="${x(value)}" y="${height - 8}" text-anchor="middle">${value.toFixed(2)}</text>`;
        }
        if (bounds.checked && finite(curve.bound_left_rt) && finite(curve.bound_right_rt)) {
          const a = Math.max(lo, curve.bound_left_rt), b = Math.min(hi, curve.bound_right_rt);
          if (b > a) markup += `<rect class="tx-saved-bounds" x="${x(a)}" y="${top}" width="${x(b) - x(a)}" height="${bottom - top}"/>`;
          if (finite(curve.apex_rt) && curve.apex_rt >= lo && curve.apex_rt <= hi) markup += `<line class="tx-apex" x1="${x(curve.apex_rt)}" x2="${x(curve.apex_rt)}" y1="${top}" y2="${bottom}"/>`;
        }
        markup += `<polyline data-kind="smoothed" points="${points.map(([rt, intensity]) => `${x(rt)},${y(intensity)}`).join(' ')}"/>`;
        markup += rawPoints.map(([rt, intensity]) => `<circle data-kind="raw" cx="${x(rt)}" cy="${y(intensity)}" r="2.2"><title>RT ${rt.toFixed(4)} min · ${number(intensity)}</title></circle>`).join('');
        setHTML(svg, markup); card.append(svg); charts.append(card);
      }
      extraction.textContent = traces().map(c => `${c.quality_flags.target_label}：m/z ${c.provenance.mz} ± ${c.provenance.ppm} ppm`).join('；');
    }
    function selectSample() {
      sample = sampleSelect.value; sampleName.textContent = sample;
      setWindow(item.review_rt_window || extent());
    }
    function selectItem() {
      item = payload.items[Number(itemSelect.value) || 0];
      const ids = [...new Set(item.curves.map(c => c.sample_id))];
      sampleSelect.replaceChildren(...ids.map(id => new Option(id, id)));
      sampleSelect.hidden = ids.length === 1;
      selectSample();
    }
    range.addEventListener('submit', event => {
      event.preventDefault();
      const lo = start.valueAsNumber, hi = end.valueAsNumber;
      if (!finite(lo) || !finite(hi) || lo >= hi) {
        start.setCustomValidity('起點必須小於終點。'); start.reportValidity(); return;
      }
      setWindow([lo, hi]);
    });
    nearby.addEventListener('click', () => setWindow(item.review_rt_window || extent()));
    full.addEventListener('click', () => setWindow(extent()));
    bounds.addEventListener('change', draw);
    sampleSelect.addEventListener('change', selectSample); itemSelect.addEventListener('change', selectItem);
    selectItem();
    let previousWidth = review.clientWidth;
    const observer = new ResizeObserver(() => { if (review.clientWidth !== previousWidth) { previousWidth = review.clientWidth; draw(); } });
    observer.observe(review);
    cleanups.push(() => observer.disconnect());
  }

  function renderTargetedWorkbook(payload) {
    const groupOf = new Map((payload.samples || []).map(sample => [sample.id, sample]));
    const element = (tag, text, className) => {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    };
    const value = (v, digits = 4) => v === null || v === undefined || v === ''
      ? '未提供' : typeof v === 'number' ? v.toLocaleString('en-US', {maximumFractionDigits: digits}) : String(v);
    const range = (start, end) => `${value(start)} – ${value(end)} min`;
    // Current target lists save one expected RT as equal RT min/max.
    const rtSetting = (start, end) => start === end && start !== null && start !== undefined && start !== ''
      ? `預期 RT ${value(start)} min` : `設定 RT ${range(start, end)}`;
    const counted = v => v === true || v === 'TRUE' ? '是' : v === false || v === 'FALSE' ? '否' : '未記錄';
    const reviewWhy = review => review?.Why === '峰不在內標位置，報 ND'
      ? 'ND；舊紀錄僅描述所選候選峰偏離內標，近內標訊號的排除原因未記錄，請看原始 scan'
      : review?.Why || '—';
    const isNumber = v => typeof v === 'number' && Number.isFinite(v);
    // Intensities span decades, so they read as Excel-style scientific notation.
    const scientific = v => {
      if (!isNumber(v)) return value(v);
      const [mantissa, exponent] = v.toExponential(2).split('e');
      const power = Number(exponent);
      return `${mantissa}E${power < 0 ? '-' : '+'}${String(Math.abs(power)).padStart(2, '0')}`;
    };
    const offline = mode === 'offline';
    const facts = pairs => {
      const grid = element('dl', undefined, 'tgt-facts');
      for (const [label, saved] of pairs) {
        const field = element('div');
        field.append(element('dt', label), element('dd', value(saved)));
        grid.append(field);
      }
      return grid;
    };
    const focusBanner = focus => {
      const n = focus.samples.length, rest = focus.decided - n;
      const text = n && {
        minority_counted: `${rest} / ${focus.decided} 筆未計入；標「與多數不同」的 ${n} 筆計入，確認是否抓錯峰。`,
        minority_not_counted: `${rest} / ${focus.decided} 筆計入；標「與多數不同」的 ${n} 筆未計入，確認是否漏抓。`,
        mixed: `沒有明顯多數；先看 ${n} 筆有檢閱註記的結果。`,
      }[focus.kind];
      return text ? element('p', text, 'tgt-focus-banner') : null;
    };
    const review = element('main', undefined, 'tgt-review');
    if (offline) review.append(element('h1', 'Targeted 結果'));
    for (const item of payload.items) {
      const target = item.details.target, focus = item.details.focus;
      const istdRecords = item.details.istd_records || {};
      const focusSet = new Set(focus.samples);
      const reviews = new Map(item.details.review_queue.map(row => [row.Sample, row]));
      const section = element('section');
      if (payload.items.length > 1) section.append(element('h2', item.title));
      const context = [target.Role, target['ISTD Pair'] ? `配對 ISTD：${target['ISTD Pair']}` : null, rtSetting(target['RT min'], target['RT max'])].filter(Boolean);
      section.append(element('p', context.join(' · '), 'tgt-context'));
      const banner = focusBanner(focus);
      if (banner) section.append(banner);
      // Rows keep the saved injection order; minority rows are highlighted, not moved.
      const records = item.details.records;
      const filters = [
        ['all', '全部', () => true],
        ['focus', '需先看', record => focusSet.has(record.SampleName)],
      ].map(([key, label, keep]) => ({key, label, keep, count: records.filter(keep).length}));
      const bar = element('div', undefined, 'tgt-filter');
      bar.setAttribute('role', 'group');
      bar.setAttribute('aria-label', '篩選樣本');
      const table = element('table', undefined, 'tgt-table');
      const head = element('tr');
      // A paired analyte reads beside its ISTD in the same sample row.
      const paired = Object.keys(istdRecords).length > 0;
      const canLoadCurves = Boolean(onRequestCurves), canExclude = Boolean(onRequestExclusion);
      const titles = ['樣本', 'RT (min)', '面積', '計入偵測', ...(paired ? ['ISTD RT (min)', 'ISTD 面積'] : []), '檢閱註記', ...(canLoadCurves || canExclude ? ['操作'] : [])];
      for (const title of titles) head.append(element('th', title));
      const thead = element('thead');thead.append(head);
      const body = element('tbody');
      let openRow = null;
      const closeDetail = () => {
        if (!openRow) return;
        openRow.detail.remove();
        openRow.row.classList.remove('selected');
        openRow.button.setAttribute('aria-expanded', 'false');
        openRow = null;
      };
      const detailFor = record => {
        const panel = element('div', undefined, 'tgt-evidence');
        // An observed neutral loss supports the result; its absence under CID is a
        // given fact, so it stays with the selection record instead of up front.
        const nl = String(record.NL || '');
        const nlObserved = nl === 'OK' || nl.startsWith('WARN');
        const pairs = [['積分範圍', range(record.PeakStart, record.PeakEnd)]];
        if (nlObserved) pairs.push(['NL 證據', record['NL Evidence']]);
        panel.append(facts(pairs));
        const background = [record.Reason, nlObserved ? '' : record['NL Evidence'] && `NL 證據：${record['NL Evidence']}`]
          .filter(Boolean);
        if (background.length) {
          const raw = element('details', undefined, 'tgt-raw');
          raw.append(element('summary', '原始選峰紀錄'), ...background.map(text => element('p', text)));
          panel.append(raw);
        }
        return panel;
      };
      const rows = records.map(record => {
        const review = reviews.get(record.SampleName), row = element('tr');
        const sampleCell = element('td'), button = element('button', record.SampleName, 'tgt-sample');
        button.type = 'button';button.setAttribute('aria-expanded', 'false');
        const grouped = groupOf.get(record.SampleName);
        if (grouped?.group_color) {
          const dot = element('span', undefined, 'sample-dot');
          dot.style.background = grouped.group_color;
          sampleCell.append(dot);
        }
        sampleCell.append(button);
        if (grouped && 'group' in grouped) sampleCell.append(element('span', grouped.group || 'Other', 'sample-group'));
        if (focusSet.has(record.SampleName)) {
          row.classList.add('focus');
          sampleCell.append(element('span', '與多數不同', 'tgt-badge attention'));
        }
        row.append(sampleCell, element('td', value(record.RT), 'tgt-number'), element('td', scientific(record.Area), 'tgt-number'), element('td', counted(record['Counted Detection']), 'tgt-counted'));
        if (paired) {
          const istd = istdRecords[record.SampleName];
          const istdArea = element('td', istd ? scientific(istd.Area) : '—', 'tgt-number tgt-istd');
          if (istd && counted(istd['Counted Detection']) !== '是') istdArea.append(element('span', 'ISTD 未計入', 'tgt-badge'));
          row.append(element('td', istd ? value(istd.RT) : '—', 'tgt-number tgt-istd'), istdArea);
        }
        row.append(element('td', reviewWhy(review), 'tgt-reason'));
        if (canLoadCurves || canExclude) {
          const cell = element('td', undefined, 'tgt-actions');
          if (canLoadCurves) {
            const view = element('button', '看 XIC', 'tgt-xic');
            view.type = 'button';view.setAttribute('aria-label', `看 ${record.SampleName} 的 ${item.title} XIC`);
            view.addEventListener('click', () => onRequestCurves({item_id: item.id, sample_id: record.SampleName, opener: view}));
            cell.append(view);
          }
          if (canExclude) {
            const exclude = element('button', '標記排除', 'tgt-exclude');
            exclude.type = 'button';exclude.setAttribute('aria-label', `標記排除 ${record.SampleName}`);
            exclude.addEventListener('click', () => onRequestExclusion({sample_id: record.SampleName, opener: exclude}));
            cell.append(exclude);
          }
          row.append(cell);
        }
        button.addEventListener('click', () => {
          const same = openRow?.row === row;
          closeDetail();
          if (same) return;
          // Evidence opens under its row, so choosing a sample never moves the reader away.
          const detail = element('tr', undefined, 'tgt-detail-row'), cell = element('td');
          cell.colSpan = titles.length;cell.append(detailFor(record));detail.append(cell);
          row.after(detail);row.classList.add('selected');button.setAttribute('aria-expanded', 'true');
          openRow = {row, detail, button};
        });
        body.append(row);
        return {record, row};
      });
      const applyFilter = active => {
        closeDetail();
        for (const {record, row} of rows) row.hidden = !active.keep(record);
        for (const button of bar.children) button.setAttribute('aria-pressed', String(button.dataset.filter === active.key));
      };
      for (const filter of filters) {
        if (filter.key !== 'all' && !filter.count) continue;
        const button = element('button', `${filter.label}（${filter.count}）`);
        button.type = 'button';button.dataset.filter = filter.key;
        button.addEventListener('click', () => applyFilter(filter));
        bar.append(button);
      }
      if (bar.children.length > 1) section.append(bar);
      // Opened to read first: start on the minority rows, one click from all.
      const readFirst = payload.selection?.attention_only || payload.selection?.strategy === 'attention';
      applyFilter(readFirst && filters[1].count ? filters[1] : filters[0]);
      table.append(thead, body);
      const scroll = element('div', undefined, 'tgt-table-scroll');scroll.append(table);
      section.append(scroll);
      const method = element('details', undefined, 'tgt-method');
      method.append(element('summary', '方法設定'));
      method.append(facts([
        ['目標 m/z', target['m/z']], ['m/z 容差 (ppm)', target['ppm tol']],
        ['NL (Da)', target['NL (Da)']], ['NL 容差 warn / max (ppm)', `${value(target['NL ppm warn'])} / ${value(target['NL ppm max'])}`],
        ['樣本適用範圍', target['Sample applicability']],
      ]));
      if (item.details.identification_method) {
        const saved = item.details.identification_method;
        const combinationLabel = saved.combine_mode === 'any' ? '符合任一條件（OR）' : '符合全部條件（AND）';
        method.append(facts([['辨認方式', combinationLabel], ['m/z 容差 (ppm)', saved.mz_tolerance_ppm]]));
        const definitions = element('details'); definitions.append(element('summary', '已儲存的辨認條件'));
        saved.conditions.forEach((condition, index) => {
          definitions.append(element('h4', `${index + 1}. ${condition.kind === 'cid_nl' ? `CID-NL · ${condition.tag}` : 'Delta Mass'}`));
          if (condition.kind === 'delta_mass') {
            definitions.append(facts(condition.positions.map(p => [p.label, `${p.offset_mz > 0 ? '+' : ''}${p.offset_mz} m/z${p.offset_mz === 0 ? '（定量離子）' : ''}${p.compare_shape ? ' · 比較峰形' : ''}`])));
            definitions.append(facts([['共洗脫容差 (s)', condition.apex_floor_sec], ['峰形最低 r', condition.min_shape_r], ['強度比例／峰形參考', '定量離子（0）']]));
          }
        });
        method.append(definitions);
        const statuses = {matched: '符合', not_matched: '不符合', not_acquired: '沒有 MS2', not_evaluated: '未評估', TRUE: '是', FALSE: '否', pass: '通過', fail: '未通過'};
        for (const [title, records, fields, collapsed] of [
          [`辨認條件結果 · ${combinationLabel}`, item.details.method_evidence || [], [['SampleName', '樣本'], ['Candidate', '候選'], ['Candidate RT', '候選峰 RT'], ['Selected', '已選'], ['Method Accepted', '方法符合'], ['Condition', '條件編號'], ['Condition Label', '條件'], ['Status', '結果'], ['Reason', '說明']], false],
          ['Delta Mass 配對明細', item.details.delta_mass_evidence || [], [['SampleName', '樣本'], ['Candidate RT', '候選峰 RT'], ['Condition', '條件'], ['Pattern Status', '配對'], ['Position', '位置'], ['Expected m/z', '查詢 m/z'], ['Height Ratio to Quant', '相對定量離子強度'], ['Ratio Status', '比例參考'], ['Shape r', '峰形 r'], ['Reason', '原因']], true],
        ]) {
          if (!records.length) continue;
          const evidence = element(collapsed ? 'details' : 'div'); evidence.append(element(collapsed ? 'summary' : 'h4', title));
          const table = element('table', undefined, 'tgt-method-table'); const header = element('tr');
          for (const [, label] of fields) header.append(element('th', label));
          table.append(header);
          for (const record of records) {
            const row = element('tr');
            for (const [key] of fields) row.append(element('td', statuses[record[key]] || value(record[key])));
            table.append(row);
          }
          const scroll = element('div', undefined, 'tgt-table-scroll'); scroll.append(table); evidence.append(scroll); section.append(evidence);
        }
      }
      const labels = {
        signal_processing_method: '平滑方法', signal_processing_window_points: '平滑視窗 (points)',
        ms2_precursor_tol_da: 'MS2 precursor 容差 (Da)', nl_min_intensity_ratio: 'NL 最低相對強度',
        nl_rt_anchor_search_margin_min: 'NL 錨定搜尋半徑 (min)', nl_rt_anchor_half_window_min: '錨定後 XIC 半寬 (min)',
        nl_fallback_half_window_min: 'NL fallback 半寬 (min)', rolling_window_size: 'ISTD RT 滾動視窗',
        dirty_matrix_mode: '髒基質模式', targeted_ms1_shape_identity_activation_policy: 'MS1 shape identity policy',
      };
      method.append(facts(payload.summary.method_context.filter(([key]) => labels[key]).map(([key, saved]) => [labels[key], saved])));
      section.append(method);
      review.append(section);
    }
    if (!payload.items.length) review.append(element('p', '沒有符合條件的目標。'));
    const provenance = element('details', undefined, 'tgt-provenance');
    provenance.append(element('summary', '執行資訊'));
    const labels = {generated_at: '建立時間', app_version: '版本', target_config_hash: '目標設定識別', parallel_mode: '執行模式', parallel_workers: 'Workers', limited_identity_gate: 'Limited identity gate'};
    provenance.append(facts(payload.summary.method_context.filter(([key]) => labels[key]).map(([key, saved]) => [labels[key], saved])));
    review.append(provenance);
    // The online host states the readonly result scope beside this surface.
    if (offline) review.append(element('p', '唯讀結果 · 未保存逐點曲線，不重建色譜圖。', 'tgt-note'));
    root.append(review);
  }
  const metadata = options.metadata || {};
  const strategy = metadata.strategy ?? data.selection?.strategy ?? 'sample';
  const capability = metadata.evidence_capability ?? data.evidence_capability ?? 'metadata_only';
  const fields = {
    REPORT_TITLE: metadata.title ?? 'XIC Inspector',
    STRATEGY: strategy,
    STRATEGY_LABEL: {sample:'抽樣檢閱',attention:'爭議聚焦',query:'指定目標查詢'}[strategy] || strategy,
    SELECTED_COUNT: metadata.selected_count ?? data.items?.length ?? 0,
    RUN_TOTAL: metadata.run_total ?? data.items?.length ?? 0,
    ELIGIBLE_COUNT: metadata.eligible_count ?? data.items?.length ?? 0,
    ITEM_COUNT: data.items?.length ?? 0,
    SOURCE_DB_NAME: metadata.source_db_name ?? '',
    CAPABILITY_BADGE: capability === 'retained_curves' ? 'ok' : 'null',
    CAPABILITY_LABEL: capability === 'retained_curves' ? '含已保存曲線' : '僅有結果紀錄',
  };
  const chrome = mode === 'offline'
    ? offlineMarkup.replace('%%DETAIL_SURFACE%%', detailMarkup).replace('%%DIALOG_SURFACE%%', dialogMarkup)
    : (mode === 'curves' ? '' : `<div class="signal-bar">${signalSwitchMarkup}</div>`) + detailMarkup + dialogMarkup;
  setHTML(root, chrome.replace(/%%([A-Z_]+)%%/g, (_, key) => esc(fields[key] ?? '')));
  const items=Array.isArray(data.items)?data.items:[], samples=Array.isArray(data.samples)?data.samples:[];
  const deltaReview = data.summary?.source_kind === 'delta_mass_review';
  const nlReview = items.length > 0 && items.every(item => item.delta_mass?.neutral_losses?.length);
  const envelopeReview = deltaReview && items.some(item => item.delta_mass?.envelope?.status === 'available');
  const envelopeScore = item => item.delta_mass?.envelope?.status === 'available' ? item.delta_mass.envelope.spread : Infinity;
  const compareEnvelope = (a,b) => envelopeScore(a)-envelopeScore(b);
  if (mode === 'offline' && deltaReview) {
    root.querySelector('.scope-summary').hidden = true;
    root.querySelector('.header-actions > .badge').remove();
    root.querySelector('.list-header > span').textContent = envelopeReview ? '候選 · 本頁依包絡偏差排序' : '候選';
    root.querySelector('.advanced-filters > summary').textContent = '篩選';
    $('search').placeholder = '搜尋樣本、m/z、判讀';
  }
  // The host supplies the complete saved-source catalog, even when this view
  // contains fewer traces. Colour identity must not depend on loaded curves.
  const catalog=Array.isArray(options.samples)?options.samples:samples;
  const sampleById=new Map([...samples,...catalog].map(s=>[s.id,s]));
  const sampleColors=new Map(catalog.map((s,index)=>[s.id,index]));
  const colors=Array.from({length:8},(_,index)=>`var(--curve-${index+1})`);
  const has=(o,k)=>Object.prototype.hasOwnProperty.call(o||{},k);
  const finite=v=>typeof v==='number'&&Number.isFinite(v), fixed=(v,d)=>finite(v)?v.toFixed(d):'未提供';
  const mz=v=>fixed(v,4), rt=v=>fixed(v,2), drt=v=>fixed(v,3);
  const rtMin=v=>finite(v)?`${rt(v)} min`:'未提供', drtMin=v=>finite(v)?`${drt(v)} min`:'未提供';
  const tickNum=v=>v===0?'0':Math.abs(v)>=1e6?v.toExponential(1):Math.abs(v)>=100?Math.round(v).toLocaleString('en-US'):num(v);
  const num=v=>finite(v)?v.toLocaleString('en-US',{maximumFractionDigits:3}):'未提供';
  const sample=id=>sampleById.get(id), sampleName=id=>sample(id)?.label||id||'未記錄樣本名稱';
  const color=id=>(mode==='offline'&&sample(id)?.group_color)||colors[(sampleColors.get(id)??0)%colors.length];
  const itemRows=item=>Array.isArray(item.samples)?item.samples:[];
  const witnesses=item=>Array.isArray(item.witnesses)?item.witnesses:(Array.isArray(item.candidates)?item.candidates:[]);
  const candidateId=w=>w?.candidate_id||w?.id||'', witnessSample=w=>w?.sample_key||w?.sample_id||'';
  const records=item=>Array.isArray(item?.details?.readable_records)?item.details.readable_records:[];
  const itemName=item=>`項目 ${String(items.indexOf(item)+1).padStart(2,'0')}`;
  const itemTitle=item=>item.title||'已記錄的結果';
  // Long identifiers wrap after their separators instead of inside a word.
  const breakable=text=>esc(text).replace(/([|_\/])/g,'$1<wbr>');
  const reviewReason=(item,compact=false)=>item.delta_mass ? window.XicInspectorDeltaMass.summary(item.delta_mass,compact) : item.reason_label;
  const badge=item=>item.badge_label||({attention:'需留意',excluded:'已排除',rest:'已記錄'}[item.stratum]||'既有結果');
  const result=row=>!has(row,'quantitative_value')?'<span class="result-missing">無單一結果</span>':row.quantitative_value===null?'<span class="result-null">空值</span>':`<span class="${row.quantitative_value===0?'result-zero':'result-number'}">${num(row.quantitative_value)}</span>${row.quantity_unit?` <span class="result-unit">${esc(row.quantity_unit)}</span>`:''}`;
  const isCollapsedExcluded=item=>item.kind==='candidate'&&item.stratum==='excluded', queryReport=data.selection?.strategy==='query'||$('report-info-source')?.dataset.strategy==='query';
  const primaryItems=items.filter(item=>['group','row'].includes(item.kind)&&!isCollapsedExcluded(item));
  const initialItem=envelopeReview?[...items].sort(compareEnvelope)[0]:nlReview?(items.find(item=>item.delta_mass.neutral_losses.some(n=>n.status==='supported'))||items[0]):queryReport?items[0]:primaryItems.reduce((best,item)=>(item.review_summary?.observed_sample_count??-1)>(best?.review_summary?.observed_sample_count??-1)?item:best,null)||items.find(item=>!isCollapsedExcluded(item));
  const state={id:initialItem?.id,sample:null,candidate:'',capture:'',visible:new Set(samples.map(s=>s.id)),bounds:true,evidence:'',evidenceContext:'',excludedOpen:queryReport};
  const curveUi={model:null,query:'',collapsed:new Set(),pinned:null,hover:null,observer:null};
  cleanups.push(()=>curveUi.observer?.disconnect());
  let filtered=items, listed=[], dialogOpener=null, evidenceOpener=null;

  function candidates(item,sid=state.sample){
    const rows=witnesses(item).filter(w=>!sid||witnessSample(w)===sid), ids=[];
    rows.forEach(w=>{const id=candidateId(w);if(id&&!ids.includes(id))ids.push(id);});
    return ids.map((id,index)=>({id,index,witness:rows.find(w=>candidateId(w)===id)}));
  }
  function sampleNotices(item,row){
    const notices=[], count=candidates(item,row.sample_id).length, curve=(item.curves||[]).some(c=>c.sample_id===row.sample_id&&c.raw_rts?.length);
    const quality=row.signal_quality;
    const risks=row.membership_risks;
    if(Array.isArray(risks)){
      if(!risks.length)notices.push({kind:'membership',label:'配對風險未記錄',short:'風險未記錄'});
      else if(risks.some(r=>(r.risk_flags?.includes('accepted_competition')||r.risk_flags?.includes('backfill_competition'))))notices.push({kind:'membership',label:risks.some(r=>(r.risk_flags?.includes('accepted_competition')||r.risk_flags?.includes('backfill_competition'))&&r.selected_owners?.length>1)?'已配對，有其他合理候選':'保持分開，有其他合理候選',short:'配對競爭'});
      if(risks?.some(r=>r.risk_flags?.includes('accepted_contradiction')))notices.push({kind:'membership',label:'已接受含矛盾證據的配對',short:'含矛盾證據'});
      if(risks?.some(r=>(r.risk_flags?.includes('low_rt_support')||r.risk_flags?.includes('backfill_low_localization_confidence'))))notices.push({kind:'membership',label:'RT 支援較弱',short:'弱 RT 支援'});
      if(risks?.some(r=>r.risk_flags?.includes('weak_identity')))notices.push({kind:'membership',label:'身分證據較弱',short:'弱身分證據'});
      if(risks?.some(r=>r.risk_flags?.includes('direction_disagreement')))notices.push({kind:'membership',label:'前後方向配置不一致',short:'方向分歧'});
    }
    if(Array.isArray(quality)){
      if(quality.some(q=>q.low_signal_to_noise===true))notices.push({kind:'signal',label:'低 SNR（非 LOQ 判定）',short:'低 SNR'});
      else if(!quality.length||quality.every(q=>q.status==='not_recorded'))notices.push({kind:'signal',label:'SNR 未記錄',short:'SNR 未記錄'});
      else if(quality.some(q=>q.signal_to_noise==null))notices.push({kind:'signal',label:'SNR 未知',short:'SNR 未知'});
    }
    if(count>1&&!channelRoles(item,row.sample_id))notices.push({kind:'candidates',label:`${count} 個候選`,short:`${count} 候選`});
    if(!has(row,'quantitative_value'))notices.push({kind:'missing',label:'無單一結果',short:'無單一結果'});
    else if(row.quantitative_value===null)notices.push({kind:'null',label:'空值',short:'空值'});
    else if(row.quantitative_value===0)notices.push({kind:'zero',label:'數值 0',short:'0'});
    if(!curve)notices.push({kind:'curve',label:'曲線未提供',short:'缺曲線'});
    return notices;
  }
  function originLabel(origin){
    return origin?.label||({discovery_support:'初始探索支持',backfill_support:'Backfill（回填）支持',unavailable:'來源未提供'}[origin?.kind]||'來源未提供');
  }
  function originClass(origin){return ['discovery_support','backfill_support'].includes(origin?.kind)?origin.kind:'unavailable';}
  function curveOrigin(item,sampleId){return itemRows(item).find(row=>row.sample_id===sampleId)?.support_origin||{kind:'unavailable'};}
  function originDash(origin){return {discovery_support:'',backfill_support:'2 4',unavailable:'1 5'}[originClass(origin)];}
  // Line style distinguishes support origins; when no sample records an origin there is nothing to distinguish.
  const originsRecorded=item=>itemRows(item).some(r=>originClass(r.support_origin)!=='unavailable');
  function curveDash(item,origin){return originsRecorded(item)?originDash(origin):'';}
  // Distinct trace roles in one sample are measured channels of that sample, not competing candidates.
  function channelRoles(item,sid){const ws=witnesses(item).filter(w=>witnessSample(w)===sid),roles=ws.map(w=>w.role);return ws.length>1&&roles.every(Boolean)&&new Set(roles).size===roles.length?roles:null;}
  const channelDashes=['','7 4','2 3','10 3 2 3','1 4'];
  function channelStyles(item){
    const roles=[];itemRows(item).forEach(r=>(channelRoles(item,r.sample_id)||[]).forEach(role=>{if(!roles.includes(role))roles.push(role);}));
    return new Map(roles.map((role,i)=>[role,channelDashes[i%channelDashes.length]]));
  }
  // Channels share their sample's colour, so line style tells them apart unless it already tells support origins apart.
  function lineDash(item){
    const styles=originsRecorded(item)?new Map():channelStyles(item),roleOf=new Map(witnesses(item).map(w=>[candidateId(w),w.role]));
    return (curve,origin)=>curve.quality_flags?.acquired_at_group_position?'6 4':styles.get(roleOf.get(curve.candidate_id))??curveDash(item,origin);
  }
  function originRefs(origin){return [...new Set(['method_refs','capture_refs','observation_refs'].flatMap(key=>Array.isArray(origin?.[key])?origin[key]:[]).filter(Boolean))];}
  function sampleAlignment(item,row){
    const curves=(item.curves||[]).filter(c=>c.sample_id===row.sample_id&&c.raw_rts?.length), aligned=curves.filter(c=>c.aligned_rts?.length).length;
    if(!curves.length)return {kind:'unavailable',label:'校正：無曲線'};
    if(curves.some(c=>c.rt_alignment_methods?.includes('raw_fallback')))return {kind:'unavailable',label:'無 RT 估計；顯示原始 RT'};
    if(!aligned)return {kind:'unavailable',label:'校正：對齊未提供'};
    if(aligned<curves.length)return {kind:'partial',label:`校正：${aligned}/${curves.length} 段有對齊座標`};
    return {kind:'complete',label:'校正：對齊可用'};
  }
  const injectionOrders=data.summary?.injection_order||{};
  const rel={axis:'raw',captures:{}};
  const shortName=name=>String(name||'未提供樣本').replace('Breast_Cancer_Tissue_pooled_','').replace('Benignfat','Fat ').replace('_DNA','');
  function relationshipModel(item){
    const rows=itemRows(item), byId=new Map();
    const add=(id,sid,label)=>{if(!id||byId.has(id))return;const row=rows.find(r=>r.sample_id===sid),order=injectionOrders[label];
      const refs=row?.support_origin?.capture_refs||[],all=(item.curves||[]).filter(c=>c.sample_id===sid);
      // Group curves require the source's exact support binding, including missing bindings.
      const curves=refs.length?all.filter(c=>refs.includes(c.evidence_ref)):['group','row'].includes(item.kind)?[]:all;
      byId.set(id,{id,sid,label:label||sampleName(sid),row,order:finite(order)?order:null,curves});};
    (item.relationship_members||[]).forEach(m=>add(m.subject_id,m.sample_id,m.sample_label));
    if(!byId.size)rows.forEach(r=>add(r.sample_id,r.sample_id,r.sample_stem||sampleName(r.sample_id)));
    const nodes=[...byId.values()].sort((a,b)=>(a.order===null)-(b.order===null)||(a.order??0)-(b.order??0));
    return {nodes};
  }
  function relationshipCurve(node){return node.curves.find(c=>c.evidence_ref===rel.captures[node.id])||node.curves[0]||null;}
  function lanePath(xs,ys,x,y){let started=false,path='';xs.forEach((v,i)=>{if(!finite(v)||!finite(ys[i])){started=false;return;}path+=`${started?'L':'M'}${x(v).toFixed(2)},${y(ys[i]).toFixed(2)} `;started=true;});return path;}
  function relationshipLanes(item,model){
    const nodes=model.nodes.filter(n=>state.visible.has(n.sid)),axis=rel.axis;
    let lo=Infinity,hi=-Infinity;
    nodes.forEach(n=>{const c=relationshipCurve(n);((axis==='aligned'?c?.aligned_rts:c?.raw_rts)||[]).forEach(v=>{if(finite(v)){lo=Math.min(lo,v);hi=Math.max(hi,v);}});});
    if(!finite(lo)){lo=0;hi=1;}if(lo===hi)hi=lo+1;
    const x=v=>8+(v-lo)/(hi-lo)*672;
    const lanes=nodes.map(n=>{const c=relationshipCurve(n),series=c?seriesList(c,axis,display.signal)[0]:null,xs=series?.rts||[],ys=series?.intensities||[];let chart='';
      if(!c||!series||!xs.some(finite))chart=`<div class="lane-missing">${!c?'來源未提供此樣本的精確支持曲線；不以其他候選替代。':axis==='aligned'?'此擷取未提供所選訊號的校正時間；切回原始 RT 查看。':display.signal==='smoothed'?'此擷取未保存平滑曲線；可切換原始訊號檢查。':'此擷取未提供原始訊號。'}</div>`;
      else{let max=0;ys.forEach(v=>{if(finite(v))max=Math.max(max,v);});const y=v=>76-(max?v/max:0)*68;let svg='';for(let i=0;i<=4;i++){const t=lo+(hi-lo)*i/4;svg+=`<line x1="${x(t)}" x2="${x(t)}" y1="4" y2="76" class="lane-grid"/><text x="${x(t)}" y="94" text-anchor="${i===0?'start':i===4?'end':'middle'}" class="lane-tick">${t.toFixed(2)}</text>`;}
        const left=axis==='aligned'?c.aligned_bound_left_rt:c.bound_left_rt,right=axis==='aligned'?c.aligned_bound_right_rt:c.bound_right_rt,apex=axis==='aligned'?c.aligned_apex_rt:c.apex_rt;
        if(finite(left)&&finite(right))svg+=`<rect x="${x(left)}" y="4" width="${Math.max(0,x(right)-x(left))}" height="72" class="lane-bound"/>`;
        svg+=`<path d="${lanePath(xs,ys,x,y)}" class="lane-raw" data-kind="${esc(display.signal)}" data-support-origin="${originClass(n.row?.support_origin)}" stroke-dasharray="${curveDash(item,n.row?.support_origin)}"/>`;
        if(finite(apex))svg+=`<line x1="${x(apex)}" x2="${x(apex)}" y1="4" y2="76" class="lane-apex"/>`;
        chart=`<svg viewBox="0 0 690 100" preserveAspectRatio="none" aria-label="${esc(n.label)} 的${axis==='aligned'?'校正':'原始'}時間曲線"><title>${esc(n.label)} · ${esc(c.stage_label)} · ${display.signal==='smoothed'?'平滑':'原始'}最高強度 ${num(max)}</title>${svg}</svg>`;}
      return `<article class="relationship-lane" data-lane-member="${esc(n.id)}" data-injection-order="${n.order??''}"><div class="lane-label"><div class="lane-name"><span class="injection-order">${n.order===null?'?':String(n.order).padStart(2,'0')}</span>${esc(shortName(n.label))}</div><span class="reading-hint">${esc(c?.stage_label||'支持曲線未提供')}${axis==='aligned'&&c?.rt_alignment_label?' · '+esc(c.rt_alignment_label):''}</span>${n.curves.length>1?`<select data-lane-capture="${esc(n.id)}" aria-label="${esc(n.label)} 曲線選擇">${n.curves.map(v=>`<option value="${esc(v.evidence_ref)}" ${v===c?'selected':''}>${esc(v.stage_label||v.kind)}</option>`).join('')}</select>`:''}<details><summary>定量與來源</summary><p>正式結果：${result(n.row||{})}</p>${c?`<p>記錄峰頂：${drt(c.apex_rt)} min<br>積分範圍：${drt(c.bound_left_rt)}–${drt(c.bound_right_rt)} min<br>當次原始計數面積：${num(c.area)} counts·s</p>${evidenceButtons([c.evidence_ref],`${n.label} 的支持曲線`,'查看曲線來源')}`:''}${evidenceButtons(n.row?.decision_refs,`${n.label} 的正式結果`)}${evidenceButtons(originRefs(n.row?.support_origin),`${n.label} 的支持來源`,'查看支持記錄')}</details></div><div class="lane-chart">${chart}</div></article>`;
    }).join('');
    return `<section class="relationship-curves" aria-label="依進樣順序的跨樣本曲線"><div class="relation-section-heading"><div><h3>跨樣本曲線</h3><p class="reading-hint">${nodes.length} 個樣本 · 依進樣先後，由上而下 · 共用${axis==='aligned'?'校正':'原始'}時間軸 · 各自最高點縮放；? 表示順序未提供</p></div><div class="relation-options"><div class="axis-switch"><button type="button" data-time-axis="raw" aria-pressed="${axis==='raw'}">原始 RT</button><button type="button" data-time-axis="aligned" aria-pressed="${axis==='aligned'}">校正 RT</button></div></div></div><div id="relationship-lanes">${lanes||'<p class="lane-missing">目前沒有可見樣本；可在樣本結果中恢復顯示。</p>'}</div><p class="reading-hint">藍：${display.signal==='smoothed'?'平滑訊號':'原始訊號'} · 線型依支持來源（實線：初始探索；長虛線：重新擷取；點虛線：Backfill） · 紫色虛線：記錄峰頂。只有無 RT 估計的樣本顯示原始 RT，並標示「無 RT 估計」。</p></section>`;
  }
  function sampleRelationshipPanel(item){
    const model=relationshipModel(item);
    if(!model.nodes.some(n=>n.curves.length))return '';
    return `<section class="relationship-workbench" aria-label="跨樣本曲線"><div class="relation-section-heading"><h3>樣本關係</h3></div>${relationshipLanes(item,model)}</section>`;
  }

  function candidateLabel(candidate){
    const w=candidate.witness||{};
    return `${w.label||w.title||w.role||`候選 ${candidate.index+1}`} · m/z ${mz(w.precursor_mz)} · RT ${drtMin(w.rt_apex_min)}`;
  }
  function curveKey(curve,index){return curve.evidence_ref||[curve.sample_id,curve.candidate_id,curve.kind,index].map(v=>String(v??'')).join('|');}
  function selectedCurves(item){
    if(!state.sample)return [];
    return (item.curves||[]).filter(c=>c.sample_id===state.sample&&(!state.candidate||c.candidate_id===state.candidate||c.candidate_ids?.includes(state.candidate)));
  }
  function activeCurve(item){
    const curves=selectedCurves(item), all=item.curves||[];
    return curves.find(c=>curveKey(c,all.indexOf(c))===state.capture)||curves[0]||null;
  }
  // Baseline and baseline-positive signals overlay the page's main signal where an item retains them.
  function overlays(item){
    const curves=state.sample?selectedCurves(item):(item.curves||[]).filter(c=>state.visible.has(c.sample_id)), out=new Set();
    curves.forEach(c=>(c.processed_curves||[]).forEach(s=>{const k=s.kind==='positive_signal'?'positive':s.kind;if(['baseline','positive'].includes(k)&&(s.rts||c.raw_rts)?.length&&s.intensities?.length)out.add(k);}));
    return ['baseline','positive'].filter(k=>out.has(k));
  }
  function alignmentCoverage(item,peer=false){
    const curves=curvesForPlot(item,peer), rawSamples=new Set(curves.filter(c=>seriesList(c,'raw',display.signal).length).map(c=>c.sample_id)), alignedSamples=new Set(curves.filter(c=>seriesList(c,'aligned',display.signal).length).map(c=>c.sample_id));
    return {available:alignedSamples.size,total:rawSamples.size};
  }
  function alignedAvailable(item,peer=false){return alignmentCoverage(item,peer).available>0;}
  function normalize(item,changed=false){
    if(state.sample&&!itemRows(item).some(r=>r.sample_id===state.sample))state.sample=null;
    const cs=candidates(item);
    if(!state.sample)state.candidate='';
    else if(changed||!cs.some(c=>c.id===state.candidate)){state.candidate=cs[0]?.id||'';state.capture='';}
    const curves=selectedCurves(item), all=item.curves||[];
    if(!state.sample)state.capture='';
    else if(!curves.some(c=>curveKey(c,all.indexOf(c))===state.capture))state.capture=curves[0]?curveKey(curves[0],all.indexOf(curves[0])):'';
    // Keep the selected signal when absent; never substitute raw for smoothed.
  }
  function seriesList(curve,axis,signal=display.signal){
    const rts=axis==='aligned'?curve.aligned_rts:curve.raw_rts;
    const raw=rts?.length&&curve.raw_intensities?.length?{kind:'raw',rts,intensities:curve.raw_intensities}:null;
    if(signal==='raw')return raw?[raw]:[];
    const found=(curve.processed_curves||[]).find(s=>s.kind===signal||(signal==='positive'&&s.kind==='positive_signal'));
    if(!found)return [];
    const processedRts=axis==='aligned'?found.aligned_rts:(found.rts||curve.raw_rts);
    const processed=processedRts?.length&&found.intensities?.length?{...found,rts:processedRts}:null;
    return processed?[processed]:[];
  }
  function curvesForPlot(item,peer=false){
    return (item.curves||[]).filter(c=>state.visible.has(c.sample_id)&&(peer||!state.sample||c.sample_id!==state.sample||((!state.candidate||c.candidate_id===state.candidate||c.candidate_ids?.includes(state.candidate))&&(!state.capture||curveKey(c,(item.curves||[]).indexOf(c))===state.capture))));
  }

  function filteredItems(){
    const q=$('search').value.trim().toLowerCase(), limits=['mz-min','mz-max','rt-min','rt-max'].map(id=>$(id).value===''?null:Number($(id).value));
    return items.filter(item=>{
      const text=[itemName(item),itemTitle(item),item.reason_label,reviewReason(item),item.method_label,mz(item.precursor_mz),rt(item.rt_apex_min),...itemRows(item).flatMap(r=>[sampleName(r.sample_id),r.status_label,r.decision_label,...(r.membership_risks||[]).flatMap(risk=>[risk.phase,...(risk.risk_flags||[])])])].join(' ').toLowerCase();
      if(q&&!text.includes(q))return false;if(limits.every(v=>v===null))return true;
      const rows=witnesses(item).length?witnesses(item):[item];
      return rows.some(r=>(limits[0]===null||finite(r.precursor_mz)&&r.precursor_mz>=limits[0])&&(limits[1]===null||finite(r.precursor_mz)&&r.precursor_mz<=limits[1])&&(limits[2]===null||finite(r.rt_apex_min)&&r.rt_apex_min>=limits[2])&&(limits[3]===null||finite(r.rt_apex_min)&&r.rt_apex_min<=limits[3]));
    });
  }
  function filterStatus(){
    const active=[];if($('search').value.trim())active.push(`搜尋「${$('search').value.trim()}」`);
    for(const [lo,hi,label] of [['mz-min','mz-max','m/z'],['rt-min','rt-max','RT']])if($(lo).value||$(hi).value)active.push(`${label} ${$(lo).value||'不限'}–${$(hi).value||'不限'}`);
    setHTML($('active-filters'),active.length?`${deltaReview?'':`<span class="active-label">本頁已套用</span>${active.map(x=>`<span class="filter-chip">${esc(x)}</span>`).join('')}`}<button type="button" id="clear-inline">清除</button>`:'');
    if (deltaReview) {
      const rangeCount = active.length - Number(Boolean($('search').value.trim()));
      root.querySelector('.advanced-filters > summary').textContent = rangeCount ? `篩選（${rangeCount}）` : '篩選';
    }
    const excluded=filtered.filter(isCollapsedExcluded).length;
    $('loaded-range').textContent=active.length?`符合篩選 ${filtered.length} 個${excluded?`，其中 ${excluded} 個為已排除候選`:''}`:'';
  }
  function list(selectFallback=true){
    filtered=filteredItems();
    const regular=filtered.filter(item=>!isCollapsedExcluded(item)), excluded=filtered.filter(isCollapsedExcluded), localFilterActive=$('search').value.trim()!==''||['mz-min','mz-max','rt-min','rt-max'].some(id=>$(id).value!==''), showExcluded=state.excludedOpen||localFilterActive;
    if (envelopeReview) regular.sort(compareEnvelope);
    else if (nlReview) regular.sort((a,b) => Number(b.delta_mass.neutral_losses.some(n => n.status === 'supported')) - Number(a.delta_mass.neutral_losses.some(n => n.status === 'supported')));
    listed=showExcluded?[...regular,...excluded]:regular;
    if(!listed.some(i=>i.id===state.id)&&(state.id||selectFallback)){state.id=listed[0]?.id;reset();}
    $('list-count').textContent=deltaReview
      ? `${filtered.length === items.length ? items.length : `${filtered.length} / ${items.length}`} 筆${Number(fields.RUN_TOTAL) !== items.length ? `（全批 ${fields.RUN_TOTAL}）` : ''}`
      : `${filtered.length} / ${items.length} 筆`;
    const recordButton=item=>{const dm=item.delta_mass,reason=reviewReason(item,true);return `<button type="button" class="record-button ${item.id===state.id?'selected':''}" data-item="${esc(item.id)}" aria-current="${item.id===state.id}"><div class="record-name"><span>${breakable(itemTitle(item))}</span>${dm?'':`<span class="badge badge-${esc(item.stratum)}">${esc(badge(item))}</span>`}</div><div class="record-meta">${dm?'':`${itemName(item)} · `}m/z ${mz(item.precursor_mz)} · RT ${rtMin(item.rt_apex_min)}</div>${reason||!dm?`<div class="record-reason">${esc(reason||'未記錄判定說明')}</div>`:''}</button>`;};
    const excludedGroup=excluded.length?`<section class="excluded-group"><button type="button" class="excluded-toggle" data-excluded-toggle aria-expanded="${showExcluded}" ${localFilterActive?'disabled title="篩選結果會保持展開"':''}><span>已排除候選</span><span>${excluded.length} 筆 ${showExcluded?'▾':'▸'}</span></button>${showExcluded?`<div class="excluded-items">${excluded.map(recordButton).join('')}</div>`:''}</section>`:'';
    setHTML($('record-list'),regular.map(recordButton).join('')+excludedGroup||'<p class="empty-state">這份報告沒有符合的項目。請清除篩選條件。</p>');
    filterStatus();detail();
  }
  function summary(item){
    let values=0,nulls=0,noSingle=0,noCurves=0;
    itemRows(item).forEach(r=>{if(!has(r,'quantitative_value'))noSingle++;else if(r.quantitative_value===null)nulls++;else values++;if(!(item.curves||[]).some(c=>c.sample_id===r.sample_id&&c.raw_rts?.length))noCurves++;});
    const multi=itemRows(item).filter(r=>candidates(item,r.sample_id).length>1&&!channelRoles(item,r.sample_id));
    const fact=(label,value,unit='',warn=false,title='')=>`<div class="fact ${warn?'warn':''}"${title?` title="${esc(title)}"`:''}><dt>${label}</dt><dd>${value}${unit?`<small>${unit}</small>`:''}</dd></div>`;
    return `<dl class="fact-row">${fact('m/z',mz(item.precursor_mz))}${fact('主要 RT',rt(item.rt_apex_min),finite(item.rt_apex_min)?'min':'')}${fact('樣本',itemRows(item).length)}${fact('有定量值',values)}${nulls?fact('空值',nulls,'',true):''}${noSingle?fact('無單一結果',noSingle,'',true):''}${noCurves?fact('未提供曲線',noCurves,'',true):''}${multi.length?fact('多個候選',multi.length,'個樣本',true,multi.map(r=>sampleName(r.sample_id)).join('、')):''}</dl>`;
  }
  const recordRefText=ref=>Array.isArray(ref)?JSON.stringify(ref):ref;
  function evidenceButtons(refs,context,label='查看定量依據'){
    const values=[...new Set((Array.isArray(refs)?refs:[]).filter(Boolean).map(recordRefText))];
    // Several references are told apart by their readable record title when the report embeds one.
    const titled=ref=>records(items.find(i=>i.id===state.id)).find(r=>recordRefText(r.ref)===ref)?.title;
    return values.length?`<div class="evidence-actions">${values.map((ref,i)=>`<button type="button" class="evidence-link" data-evidence-ref="${esc(ref)}" data-evidence-context="${esc(context)}">${values.length>1?esc(titled(ref)||`${label} ${i+1}`):label}</button>`).join('')}</div>`:'';
  }
  function sampleTable(item){
    const excluded=isCollapsedExcluded(item), evidenceLabel=excluded?'查看排除依據':'查看定量依據';
    return `<div class="table-scroll compact-samples"><table class="sample-table"><thead><tr><th>顯示</th><th>樣本與狀態</th><th class="num">正式結果</th><th class="num"><span class="sr-only">操作</span></th></tr></thead><tbody>${itemRows(item).map(r=>{const notices=sampleNotices(item,r),current=r.sample_id===state.sample,evidenceContext=excluded?`${sampleName(r.sample_id)} 的排除依據`:`${sampleName(r.sample_id)} 的正式定量值`,origin=r.support_origin||{kind:'unavailable'},alignment=sampleAlignment(item,r),supportRefs=originRefs(origin);return `<tr class="sample-row ${notices.length?'sample-notice':''} ${current?'focused-row':''}" data-sample-row="${esc(r.sample_id)}"><td><label><input type="checkbox" data-sample-visible="${esc(r.sample_id)}" ${state.visible.has(r.sample_id)?'checked':''}><span class="sr-only">在圖中顯示 ${esc(sampleName(r.sample_id))}</span></label></td><td><span class="sample-identity"><span class="sample-dot" style="background:${color(r.sample_id)}"></span>${esc(sampleName(r.sample_id))}${sample(r.sample_id)?.group!==undefined?`<span class="sample-group">${esc(sample(r.sample_id).group||'Other')}</span>`:''}</span><div class="sample-sub"><span class="badge badge-${esc(r.status_kind)}">${esc(r.status_label||'未記錄')}</span>${notices.length?`<span class="sample-flags" aria-label="需要留意：${esc(notices.map(n=>n.label).join('、'))}">${notices.map(n=>`<span class="sample-flag flag-${n.kind}">${esc(n.label)}</span>`).join('')}</span>`:''}</div><div class="sample-sub sample-provenance"><span class="support-origin origin-${originClass(origin)}">${esc(originLabel(origin))}</span><span class="alignment-status alignment-${alignment.kind}">${esc(alignment.label)}</span></div></td><td class="num">${result(r)}</td><td><div class="sample-actions"><button type="button" class="sample-focus" data-focus="${esc(r.sample_id)}" ${current?'disabled aria-current="true"':''}>${current?'目前查看':'查看樣本'}</button>${evidenceButtons(r.decision_refs,evidenceContext,evidenceLabel)}${supportRefs.length?evidenceButtons(supportRefs,`${sampleName(r.sample_id)} 的${originLabel(origin)}`,'查看支持記錄'):''}</div></td></tr>`;}).join('')}</tbody></table></div><p class="reading-hint">勾選控制圖中顯示；「查看樣本」帶出候選與積分。支持來源說明樣本如何進入目前族群；校正狀態只反映保存的對齊座標。</p>`;
  }
  function candidateControl(item){
    if(!state.sample)return '<p class="control-value">先從樣本表查看一個樣本，才會列出該樣本的候選。</p>';
    const cs=candidates(item);if(!cs.length)return '<p class="control-value">此樣本沒有候選峰紀錄。</p>';
    if(cs.length===1){const c=cs[0];return `<p class="control-value" data-current-candidate="${esc(c.id)}">${esc(candidateLabel(c))}<span class="viewing-tag">目前查看</span></p>`;}
    return `<select id="candidate-select" aria-label="查看候選">${cs.map(c=>`<option value="${esc(c.id)}" ${c.id===state.candidate?'selected':''}>${esc(candidateLabel(c))}</option>`).join('')}</select><span class="viewing-tag">目前查看</span><p class="control-note">切換只改變目前查看的曲線；正式定量值保持不變。</p>`;
  }
  function captureControl(item){
    if(!state.sample)return '<p class="control-value">查看樣本後顯示擷取階段。</p>';const cs=selectedCurves(item),all=item.curves||[];
    if(!cs.length)return '<p class="control-value">此候選未提供保存曲線。</p>';
    if(cs.length===1)return `<p class="control-value" data-current-capture="${esc(curveKey(cs[0],all.indexOf(cs[0])))}">${esc(cs[0].stage_label||cs[0].kind||'保留曲線')}</p>`;
    return `<select id="capture-select" aria-label="擷取階段">${cs.map(c=>{const key=curveKey(c,all.indexOf(c));return `<option value="${esc(key)}" ${key===state.capture?'selected':''}>${esc(c.stage_label||c.kind||'保留曲線')}</option>`;}).join('')}</select>`;
  }
  function overlayControl(item){const ks=overlays(item),labels={baseline:'疊加基線',positive:'疊加基線以上訊號'};return ks.length?`<div class="control-group overlay-controls">${ks.map(k=>`<label><input type="checkbox" data-overlay="${k}" ${display.overlays.has(k)?'checked':''}> ${labels[k]}</label>`).join('')}</div>`:'';}
  function compactNames(item){
    // Legends show the distinguishing tail of long shared sample stems; the full name stays in the tooltip.
    const names=itemRows(item).map(r=>sampleName(r.sample_id));if(names.length<2)return n=>n;
    let prefix=names[0];names.forEach(n=>{while(!n.startsWith(prefix))prefix=prefix.slice(0,-1);});
    const cut=Math.max(...['_','-',' ','.'].map(c=>prefix.lastIndexOf(c)))+1;
    return cut>=8&&names.every(n=>n.length>cut)?n=>'…'+n.slice(cut):n=>n;
  }
  function legend(item){const short=compactNames(item);return itemRows(item).map(r=>{const notices=sampleNotices(item,r),origin=r.support_origin,name=sampleName(r.sample_id);return `<label class="legend-item ${notices.length?'has-notice':''}" title="${esc(name)} · ${esc(originLabel(origin))}"><input type="checkbox" data-sample-visible="${esc(r.sample_id)}" ${state.visible.has(r.sample_id)?'checked':''}><svg class="legend-line" viewBox="0 0 22 8" aria-hidden="true"><line x1="0" x2="22" y1="4" y2="4" stroke="${color(r.sample_id)}" stroke-width="2.5" stroke-dasharray="${curveDash(item,origin)}"/></svg><span>${esc(short(name))} · <small>${esc(originLabel(origin))}</small></span>${notices.map(n=>`<span class="legend-notice flag-${n.kind}" title="${esc(n.label)}">${esc(n.short)}</span>`).join('')}</label>`;}).join('');}
  function channelKey(item){
    if(originsRecorded(item))return '';const styles=channelStyles(item);
    return styles.size?`<div class="channel-key" aria-label="曲線通道線型"><span>通道</span>${[...styles].map(([role,dash])=>`<span><svg class="legend-line" viewBox="0 0 22 8" aria-hidden="true"><line x1="0" x2="22" y1="4" y2="4" stroke="currentColor" stroke-width="2" stroke-dasharray="${dash}"/></svg>${esc(role)}</span>`).join('')}</div>`:'';
  }
  function readout(item){
    if(!state.sample)return '<p class="integration-empty">在下方樣本表按「查看樣本」，這裡會顯示該樣本的積分範圍、峰頂、面積與正式定量值。</p>';
    const row=itemRows(item).find(r=>r.sample_id===state.sample)||{}, curve=activeCurve(item), c=candidates(item).find(x=>x.id===state.candidate), excluded=isCollapsedExcluded(item), evidenceLabel=excluded?'查看排除依據':'查看定量依據', evidenceContext=excluded?`${sampleName(state.sample)} 的排除依據`:'正式定量值';
    const measuredMz=c&&Object.prototype.hasOwnProperty.call(c.witness||{},"ms1_apex_mz")?`<p data-readout="measured-mz">實測 MS1 apex m/z：${finite(c.witness.ms1_apex_mz)?c.witness.ms1_apex_mz.toFixed(6):"未提供"}</p>`:"";
    if(!curve)return `<div class="integration-empty"><strong>${esc(sampleName(state.sample))}</strong>${measuredMz}<p>此候選未提供保存曲線；正式定量結果仍為 ${result(row)}。</p>${evidenceButtons(row.decision_refs,evidenceContext,evidenceLabel)}</div>`;
    const candidateName=c?(c.witness?.label||c.witness?.title||c.witness?.role||`候選 ${c.index+1}`):'',stage=curve.stage_label||curve.kind||'保留曲線';
    const rawApex=curve.integration?.raw_apex_rt_min, showRaw=finite(rawApex)&&(!finite(curve.apex_rt)||Math.abs(rawApex-curve.apex_rt)>1e-7);
    return `<div class="readout-heading"><div><span class="eyebrow">目前檢閱 · 下列 RT 均為原始時間</span><strong>${esc(sampleName(state.sample))}${c?` · ${esc(candidateName)}`:''}${stage===candidateName?'':` · ${esc(stage)}`}</strong></div>${curve.evidence_ref?`<button type="button" class="evidence-link" data-evidence-ref="${esc(curve.evidence_ref)}" data-evidence-context="目前曲線與積分">查看曲線來源</button>`:''}</div>${measuredMz}<dl class="integration-grid"><div><dt>積分範圍（原始時間）</dt><dd data-readout="bounds">${finite(curve.bound_left_rt)||finite(curve.bound_right_rt)?`${drt(curve.bound_left_rt)}–${drt(curve.bound_right_rt)} min`:'未提供'}</dd></div><div><dt>積分峰頂（原始時間）</dt><dd data-readout="apex">${drtMin(curve.apex_rt)}</dd></div>${showRaw?`<div><dt>原始訊號峰頂（原始時間）</dt><dd data-readout="raw-apex">${drt(rawApex)} min</dd></div>`:''}<div><dt>當次原始計數面積</dt><dd data-readout="area">${finite(curve.area)?`${num(curve.area)} <span>counts·s</span>`:'未提供'}</dd></div><div><dt>正式定量值</dt><dd data-readout="quantity">${result(row)}</dd></div></dl><p class="control-note">面積是這次積分的原始計數面積；正式定量值是來源保存的決策結果，兩者分開顯示。</p>${evidenceButtons(row.decision_refs,evidenceContext,evidenceLabel)}`;
  }
  function curveAcquisitionLabel(curve){
    if(curve.quality_flags?.acquired_at_group_position)return '依群組位置擷取';
    if(curve.quality_flags?.new_diagnostic_extraction)return '依樣本峰頂擷取';
    return curve.stage_label||'來源保存曲線';
  }
  function curveSidebarModel(item){
    if(curveUi.model?.item===item)return curveUi.model;
    const bySample=new Map(), scope=options.sampleIds?new Set(options.sampleIds):null;
    for(const curve of item.curves||[]){
      if(!seriesList(curve,'raw','raw').length&&!seriesList(curve,'raw','smoothed').length)continue;
      if(!bySample.has(curve.sample_id))bySample.set(curve.sample_id,[]);
      bySample.get(curve.sample_id).push(curve);
    }
    const dash=lineDash(item),groups=new Map();
    const rows=catalog.filter(s=>!scope||scope.has(s.id)).map(s=>{
      const curves=bySample.get(s.id)||[], first=curves[0];
      return {id:s.id,name:sampleName(s.id),group:s.group||data.sample_groups?.[s.id]||'樣本',curves,
        dash:first?dash(first,curveOrigin(item,s.id)):'',available:curves.length>0};
    });
    for(const row of rows){
      if(!groups.has(row.group))groups.set(row.group,[]);
      groups.get(row.group).push(row);
    }
    state.visible=new Set(rows.filter(row=>row.available).map(row=>row.id));
    curveUi.model={item,rows,groups:[...groups].map(([name,rows])=>({name,rows,available:rows.filter(row=>row.available)}))};
    curveUi.pinned=null;curveUi.hover=null;
    return curveUi.model;
  }
  function curveSampleList(model){
    const query=curveUi.query.trim().toLocaleLowerCase();
    return model.groups.map(group=>{
      const rows=group.rows.filter(row=>row.name.toLocaleLowerCase().includes(query));
      if(!rows.length)return '';
      const open=!curveUi.collapsed.has(group.name);
      return `<section class="curve-sample-group"><button type="button" class="curve-collapse" data-curve-collapse="${esc(group.name)}" aria-expanded="${open}" aria-label="${open?'收合':'展開'} ${esc(group.name)} 組別"><span class="curve-chevron" aria-hidden="true">›</span><span class="curve-group-name">${esc(group.name)}</span><span class="curve-group-count" data-curve-count="${esc(group.name)}"></span></button><div class="curve-group-body" ${open?'':'hidden'}>${rows.map(row=>{
        const tail=row.name.length>20?row.name.slice(-10):'',leading=tail?row.name.slice(0,-10):row.name;
        return `<div class="curve-sample-row${row.available?'':' curve-unavailable'}" data-sample-id="${esc(row.id)}" ${row.available?'':`tabindex="0" aria-label="${esc(row.name)}，無曲線"`}><input type="checkbox" data-sample-visible="${esc(row.id)}" aria-label="在圖中顯示 ${esc(row.name)}" ${state.visible.has(row.id)?'checked':''} ${row.available?'':'disabled'}><button type="button" class="curve-sample-focus" data-curve-focus="${esc(row.id)}" aria-label="聚焦 ${esc(row.name)}" aria-pressed="false" ${row.available?'':'disabled'}><svg class="curve-swatch" aria-hidden="true" viewBox="0 0 28 8"><line x1="1" y1="4" x2="27" y2="4" stroke="${color(row.id)}" stroke-width="2.2" stroke-dasharray="${row.dash}"/></svg><span class="curve-sample-name" aria-hidden="true"><span class="curve-name-leading">${esc(leading)}</span><span class="curve-name-tail">${esc(tail)}</span></span></button>${row.available?'':'<span class="curve-missing" aria-hidden="true">—</span>'}</div>`;
      }).join('')}</div></section>`;
    }).join('')||'<p class="curve-list-empty">沒有符合的樣本</p>';
  }
  function syncCurveSelection(){
    const model=curveUi.model;
    const inputs=[...root.querySelectorAll('[data-curve-group]')],counts=[...root.querySelectorAll('[data-curve-count]')];
    for(const group of model.groups){
      const selected=group.available.filter(row=>state.visible.has(row.id)).length;
      for(const input of inputs){
        if(input.dataset.curveGroup!==group.name)continue;
        input.checked=selected>0&&selected===group.available.length;
        input.indeterminate=selected>0&&selected<group.available.length;
        input.disabled=!group.available.length;
        input.setAttribute('aria-checked',input.indeterminate?'mixed':String(input.checked));
      }
      for(const count of counts){
        if(count.dataset.curveCount===group.name)count.textContent=`${selected}/${group.available.length}`;
      }
    }
    for(const input of root.querySelectorAll('[data-sample-visible]'))input.checked=state.visible.has(input.dataset.sampleVisible);
    if(curveUi.pinned&&!state.visible.has(curveUi.pinned))curveUi.pinned=null;
    applyCurveEmphasis();
  }
  function curveLineOpacity(id,overlay=false){
    const focused=curveUi.hover||curveUi.pinned;
    return (focused?(id===focused?1:.15):.92)*(overlay?.7:1);
  }
  function applyCurveEmphasis(){
    for(const line of root.querySelectorAll('svg[data-curve-chart] polyline[data-sample-id]')){
      line.style.opacity=String(curveLineOpacity(line.dataset.sampleId,line.dataset.curveOverlay==='true'));
    }
    for(const row of root.querySelectorAll('.curve-sample-row')){
      row.classList.toggle('is-hovered',row.dataset.sampleId===curveUi.hover);
      row.classList.toggle('is-pinned',row.dataset.sampleId===curveUi.pinned);
      row.querySelector('[data-curve-focus]').setAttribute('aria-pressed',String(row.dataset.sampleId===curveUi.pinned));
    }
    if($('curve-clear-focus'))$('curve-clear-focus').hidden=!curveUi.pinned;
  }
  function hideCurveTooltip(){
    if($('curve-tooltip'))$('curve-tooltip').hidden=true;
    curveUi.describedElement?.removeAttribute('aria-describedby');
    curveUi.describedElement=null;
    curveUi.hover=null;applyCurveEmphasis();
  }
  function showCurveTooltip(id,event,element,detail='',origin=''){
    const row=curveUi.model.rows.find(row=>row.id===id),tip=$('curve-tooltip');
    if(!row||!tip)return;
    // Modal content lives in the browser's top layer, outside the main chart's
    // stacking context. Keep its tooltip in that same dialog.
    const host=element.closest('dialog[open]')||$('curve-workspace');
    if(tip.parentElement!==host)host.append(tip);
    curveUi.hover=row.available?id:null;applyCurveEmphasis();
    const name=document.createElement('strong'),note=document.createElement('span');
    name.textContent=row.name;
    note.textContent=origin||(row.available?[...new Set(row.curves.map(curveAcquisitionLabel))].join(' / '):'無曲線');
    if(curveWorkspaceAxis()==='aligned'&&row.available)note.textContent+=' · '+[...new Set(row.curves.map(c=>c.rt_alignment_label).filter(Boolean))].join(' / ');
    tip.replaceChildren(name,note);
    if(detail){const value=document.createElement('span');value.textContent=detail;tip.append(value);}
    tip.hidden=false;
    curveUi.describedElement?.removeAttribute('aria-describedby');
    curveUi.describedElement=element;element.setAttribute('aria-describedby',tip.id);
    const bounds=host.getBoundingClientRect(),anchor=element.getBoundingClientRect();
    const left=(event?.clientX??anchor.right)-bounds.left+12,top=(event?.clientY??anchor.top)-bounds.top+16;
    tip.style.left=`${host.scrollLeft+Math.max(8,Math.min(left,bounds.width-tip.offsetWidth-8))}px`;
    tip.style.top=`${host.scrollTop+Math.max(8,Math.min(top,bounds.height-tip.offsetHeight-8))}px`;
  }
  function drawCurveWorkspacePlot(){
    const element=$('svg-raw');if(!element||destroyed)return;
    const width=Math.max(280,element.clientWidth);
    curveUi.width=width;
    const item=curveUi.model.item,axis=curveWorkspaceAxis();
    const fallback=axis==='aligned'?[...new Set((item.curves||[]).filter(c=>state.visible.has(c.sample_id)&&c.rt_alignment_methods?.includes('raw_fallback')).map(c=>sampleName(c.sample_id)))]:[];
    setHTML(element,plot(item,axis,false,true,width)+(fallback.length?`<p class="alignment-summary partial">無 RT 估計，顯示原始 RT：${fallback.map(esc).join('、')}</p>`:''));
    curveUi.geometry=new WeakMap();
    applyCurveEmphasis();
  }
  function curveWorkspaceAxis(){return data.summary?.sample_rt_calibration?'aligned':'raw';}
  function renderCurveWorkspace(item){
    const model=curveSidebarModel(item);
    hideCurveTooltip();$('curve-tooltip')?.remove();
    curveUi.observer?.disconnect();
    setHTML($('workspace-content'),`<section class="curve-section" id="curve-workspace"><div class="curve-group-controls"><div class="curve-group-options" role="group" aria-label="顯示組別">${model.groups.length>1?model.groups.map(group=>`<label class="curve-group-option"><input type="checkbox" data-curve-group="${esc(group.name)}" aria-label="顯示 ${esc(group.name)} 整組"><span>${esc(group.name)}</span></label>`).join(''):''}<div class="curve-bulk-actions"><button type="button" data-curve-all="true" aria-label="全部顯示">全選</button><button type="button" data-curve-all="false" aria-label="全部隱藏">清空</button></div></div>${signalSwitchMarkup}</div><div class="curve-sample-workspace"><aside class="curve-sidebar" aria-label="樣本曲線顯示"><div class="curve-list-tools"><input type="search" id="curve-search" aria-label="搜尋樣本名稱" placeholder="搜尋樣本" value="${esc(curveUi.query)}"><button type="button" id="curve-clear-focus" data-curve-clear aria-label="解除聚焦" title="解除聚焦" hidden>×</button></div><div class="curve-sample-list" id="curve-sample-list">${curveSampleList(model)}</div></aside><section class="curve-main" aria-label="層析圖"><div class="curve-plot-tools">${rtViewControls(item)}${overlayControl(item)}<button type="button" class="zoom-btn" data-zoom="${curveWorkspaceAxis()}" aria-label="放大層析圖">放大 ↗</button></div><div class="svg-container" id="svg-raw"></div></section></div><div class="curve-tooltip" id="curve-tooltip" role="tooltip" hidden></div></section>`);
    syncSignalSwitch();syncCurveSelection();drawCurveWorkspacePlot();
    curveUi.observer=new ResizeObserver(()=>{
      if($('svg-raw')?.clientWidth!==curveUi.width)drawCurveWorkspacePlot();
    });
    curveUi.observer.observe($('svg-raw'));
  }
  function changeCurveVisibility(update){
    hideCurveTooltip();update();syncCurveSelection();drawCurveWorkspacePlot();
  }
  function curveWorkspaceClick(event){
    const all=event.target.closest('[data-curve-all]');
    if(all){changeCurveVisibility(()=>{
      state.visible=new Set(all.dataset.curveAll==='true'?curveUi.model.rows.filter(row=>row.available).map(row=>row.id):[]);
    });return true;}
    const collapse=event.target.closest('[data-curve-collapse]');
    if(collapse){
      const group=collapse.dataset.curveCollapse,open=curveUi.collapsed.has(group);
      if(open)curveUi.collapsed.delete(group);else curveUi.collapsed.add(group);
      collapse.setAttribute('aria-expanded',String(open));
      collapse.setAttribute('aria-label',`${open?'收合':'展開'} ${group} 組別`);
      collapse.nextElementSibling.hidden=!open;return true;
    }
    if(event.target.closest('[data-curve-clear]')){
      curveUi.pinned=null;hideCurveTooltip();return true;
    }
    const focus=event.target.closest('[data-curve-focus]');
    if(focus){
      const id=focus.dataset.curveFocus;
      curveUi.pinned=curveUi.pinned===id?null:id;
      if(!state.visible.has(id))changeCurveVisibility(()=>state.visible.add(id));
      else applyCurveEmphasis();
      return true;
    }
    const svg=event.target.closest('svg[data-curve-chart]');
    if(svg){
      const nearest=nearestCurve(svg,event);
      if(nearest){curveUi.pinned=curveUi.pinned===nearest.line.dataset.sampleId?null:nearest.line.dataset.sampleId;applyCurveEmphasis();}
      return true;
    }
    return false;
  }
  function nearestCurve(svg,event){
    const point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY;
    const position=point.matrixTransform(svg.getScreenCTM().inverse()),chart=svg.dataset;
    const left=+chart.chartLeft,top=+chart.chartTop,width=+chart.chartWidth,height=+chart.chartHeight;
    if(position.x<left||position.x>left+width||position.y<top||position.y>top+height)return null;
    let lines=curveUi.geometry?.get(svg);
    if(!lines){
      lines=[...svg.querySelectorAll('polyline[data-sample-id][data-curve-overlay="false"]')].map(line=>({line,points:Array.from(line.points,point=>({x:point.x,y:point.y}))}));
      curveUi.geometry??=new WeakMap();curveUi.geometry.set(svg,lines);
    }
    let nearest=null,distance=Infinity;
    for(const {line,points} of lines){
      if(!points.length||position.x<points[0].x||position.x>points[points.length-1].x)continue;
      let start=0,end=points.length-1;
      while(end-start>1){const middle=(start+end)>>1;if(points[middle].x<=position.x)start=middle;else end=middle;}
      const a=points[start],b=points[end],y=a.y+(b.y-a.y)*(b.x===a.x?0:(position.x-a.x)/(b.x-a.x));
      const gap=Math.abs(position.y-y);
      if(gap<distance){distance=gap;nearest={line,y,rt:+chart.domainStart+(+chart.domainEnd-(+chart.domainStart))*(position.x-left)/width};}
    }
    if(nearest)nearest.signal=+chart.signalLow+(+chart.signalHigh-(+chart.signalLow))*(top+height-nearest.y)/height;
    return nearest;
  }
  if(mode==='curves'){
    root.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&!root.querySelector('dialog[open]')&&(curveUi.pinned||curveUi.hover)){
        event.preventDefault();event.stopPropagation();curveUi.pinned=null;hideCurveTooltip();
      }
    });
    root.addEventListener('click',event=>{
      if(curveWorkspaceClick(event))event.stopPropagation();
    },true);
    $('detail-panel').addEventListener('change',event=>{
      const input=event.target;
      if(input.dataset.curveGroup!==undefined){
        const group=curveUi.model.groups.find(group=>group.name===input.dataset.curveGroup);
        changeCurveVisibility(()=>group.available.forEach(row=>{
          if(input.checked)state.visible.add(row.id);else state.visible.delete(row.id);
        }));
      }else if(input.dataset.sampleVisible){
        changeCurveVisibility(()=>{
          if(input.checked)state.visible.add(input.dataset.sampleVisible);else state.visible.delete(input.dataset.sampleVisible);
        });
      }else return;
      event.stopPropagation();
    },true);
    root.addEventListener('input',event=>{
      if(event.target.dataset.inspectorId!=='curve-search')return;
      curveUi.query=event.target.value;hideCurveTooltip();
      setHTML($('curve-sample-list'),curveSampleList(curveUi.model));syncCurveSelection();
    });
    root.addEventListener('pointerover',event=>{
      const row=event.target.closest('.curve-sample-row');
      if(row)showCurveTooltip(row.dataset.sampleId,event,row.querySelector('[data-curve-focus]'));
    });
    root.addEventListener('pointermove',event=>{
      const svg=event.target.closest('svg[data-curve-chart]');if(!svg)return;
      const nearest=nearestCurve(svg,event);
      if(nearest)showCurveTooltip(nearest.line.dataset.sampleId,event,svg,`RT ${rt(nearest.rt)} min · 訊號 ≈ ${num(nearest.signal)}`,nearest.line.dataset.curveOrigin);
      else hideCurveTooltip();
    });
    root.addEventListener('pointerout',event=>{
      const region=event.target.closest('.curve-sample-row,svg[data-curve-chart]');
      if(region&&!region.contains(event.relatedTarget))hideCurveTooltip();
    });
    root.addEventListener('focusin',event=>{
      const row=event.target.closest('.curve-sample-row');
      if(row)showCurveTooltip(row.dataset.sampleId,null,event.target);else hideCurveTooltip();
    });
    root.addEventListener('focusout',event=>{
      const row=event.target.closest('.curve-sample-row');
      if(row&&!row.contains(event.relatedTarget))hideCurveTooltip();
    });
  }
  function plot(item,axis,peer=false,wide=false,width=0,signal=display.signal,presentation={}){
    const shown=curvesForPlot(item,peer),drawable=shown.flatMap(curve=>seriesList(curve,axis,signal).map(series=>({curve,series})));
    const extra=drawable.length?[...display.overlays].filter(k=>overlays(item).includes(k)).flatMap(kind=>shown.flatMap(curve=>seriesList(curve,axis,kind).map(series=>({curve,series})))):[];
    if(!drawable.length&&presentation.emptyMessage)return `<div class="empty-state">${esc(presentation.emptyMessage)}</div>`;
    if(!drawable.length){const [chosen,other]=signal==='raw'?['原始訊號','平滑訊號']:['平滑訊號','原始訊號'];return `<div class="empty-state"><strong>${axis==='aligned'?'所選資料未提供對齊座標':shown.length?`所選來源未保存${chosen}`:'目前沒有可顯示的曲線'}</strong><p>${axis==='aligned'?'請切回原始時間；不會以原始座標代替。':shown.length?`不以${other}代替；可在上方切換訊號。`:'請確認樣本顯示勾選，或來源是否保存曲線。'}</p></div>`;}
    const reviewWindow=axis==='raw'?(state.plotWindow||(Array.isArray(item.review_rt_window)?item.review_rt_window:null)):mode==='curves'?state.plotWindow:null;
    let lo=reviewWindow?.[0]??Infinity,hi=reviewWindow?.[1]??-Infinity,lowY=0,highY=0;
    [...drawable,...extra].forEach(({series:s})=>s.rts.forEach((v,i)=>{const y=s.intensities[i];if(finite(v)&&finite(y)&&(!reviewWindow||v>=lo&&v<=hi)){if(!reviewWindow){lo=Math.min(lo,v);hi=Math.max(hi,v);}lowY=Math.min(lowY,y);highY=Math.max(highY,y);}}));
    if(!finite(lo)||!finite(hi))return '<div class="empty-state">未提供成對的時間與訊號數值。</div>';if(lo===hi){lo-=.5;hi+=.5;}if(lowY===highY)highY=lowY+1;
    const W=width||(wide?1060:760),H=presentation.height??(width?420:330),L=62,T=24,IW=W-88,IH=H-82,x=v=>L+(v-lo)/(hi-lo)*IW,y=v=>T+IH-(v-lowY)/(highY-lowY)*IH;
    const clipId=`curve-window-${axis}${mode==='curves'?(width?'-main':'-zoom'):''}`;
    let svg=`<defs><clipPath id="${clipId}"><rect x="${L}" y="${T}" width="${IW}" height="${IH}"/></clipPath></defs><text x="8" y="14" font-size="13" fill="#48534f">訊號強度</text>`;
    for(let i=0;i<=4;i++){const v=lowY+(highY-lowY)*i/4;svg+=`<line x1="${L}" y1="${y(v)}" x2="${L+IW}" y2="${y(v)}" stroke="#e4e9ec"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end" font-size="13" fill="#48534f">${tickNum(v)}</text>`;}
    svg+=`<path d="M${L},${T}V${T+IH}H${L+IW}" fill="none" stroke="#91a0a8"/>`;
    for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4;svg+=`<text x="${x(v)}" y="${H-32}" text-anchor="${width&&i===0?'start':width&&i===4?'end':'middle'}" font-size="13" fill="#48534f">${rt(v)}</text>`;}
    const active=peer?null:activeCurve(item);
    if(active&&state.bounds){const left=axis==='aligned'?active.aligned_bound_left_rt:active.bound_left_rt,right=axis==='aligned'?active.aligned_bound_right_rt:active.bound_right_rt;if(finite(left)&&finite(right)){const a=Math.max(lo,Math.min(left,right)),b=Math.min(hi,Math.max(left,right));if(b>=a)svg+=`<rect data-integration-range="true" x="${x(a)}" y="${T}" width="${Math.max(0,x(b)-x(a))}" height="${IH}" fill="${color(active.sample_id)}" opacity=".09"><title>積分範圍 ${drt(left)}–${drt(right)} min</title></rect>`;}}
    const dash=lineDash(item);
    [...extra.map(d=>({...d,overlay:true})),...drawable].forEach(({curve,series:s,overlay})=>{
      const focused=peer||!state.sample||curve.sample_id===state.sample;
      const opacity=mode==='curves'?curveLineOpacity(curve.sample_id,overlay):(!peer&&state.sample&&!focused?.16:.92)*(overlay?.7:1);
      const points=s.rts.map((v,i)=>finite(v)&&finite(s.intensities[i])?`${x(v)},${y(s.intensities[i])}`:'').filter(Boolean).join(' ');
      const signalName={raw:'原始訊號',baseline:'基線',smoothed:'平滑訊號',positive:'基線以上訊號',positive_signal:'基線以上訊號'}[s.kind]||'處理訊號',origin=curveOrigin(item,curve.sample_id);
      const title=mode==='curves'?'':`<title>${esc(sampleName(curve.sample_id))} · ${signalName} · ${esc(originLabel(origin))} · ${esc(curve.stage_label||curve.kind||'保留曲線')}</title>`;
      const style=presentation.curveStyle?.(curve)||{},stroke=style.stroke??color(curve.sample_id);
      svg+=`<polyline data-kind="${esc(s.kind)}" data-sample-id="${esc(curve.sample_id)}" data-position-label="${esc(curve.position_label||'')}" data-curve-overlay="${!!overlay}" data-curve-origin="${esc(curveAcquisitionLabel(curve))}" data-curve-ref="${esc(curve.evidence_ref||'')}" data-support-origin="${originClass(origin)}" clip-path="url(#${clipId})" fill="none" stroke="${stroke}" stroke-width="${style.width??(overlay?1.2:s.kind==='raw'?1.7:2.2)}" stroke-dasharray="${overlay?'3 3':style.dash??dash(curve,origin)}" opacity="${opacity}" points="${points}">${title}</polyline>`;
      if(presentation.pointLimit>0 && s.rts.filter(v=>v>=lo&&v<=hi).length<=presentation.pointLimit){
        s.rts.forEach((v,i)=>{if(v>=lo&&v<=hi)svg+=`<circle cx="${x(v)}" cy="${y(s.intensities[i])}" r="4" fill="${stroke}" stroke="var(--card)" stroke-width="2"/>`;});
      }
      if(overlay||curve!==active||!state.bounds)return;
      const marks=axis==='aligned'?[['bound','左峰界',curve.aligned_bound_left_rt],['bound','右峰界',curve.aligned_bound_right_rt],['apex','積分峰頂',curve.aligned_apex_rt]]:[['bound','左峰界',curve.bound_left_rt],['bound','右峰界',curve.bound_right_rt],['apex','積分峰頂',curve.apex_rt],...(finite(curve.integration?.raw_apex_rt_min)&&(!finite(curve.apex_rt)||Math.abs(curve.integration.raw_apex_rt_min-curve.apex_rt)>1e-7)?[['raw-apex','原始訊號峰頂',curve.integration.raw_apex_rt_min]]:[])];
      marks.forEach(([kind,label,v])=>{if(finite(v))svg+=`<line data-marker="${kind}" x1="${x(v)}" x2="${x(v)}" y1="${T}" y2="${T+IH}" stroke="${color(curve.sample_id)}" stroke-dasharray="${kind==='bound'?'5 4':kind==='raw-apex'?'2 4':'1 3'}"><title>${label} ${drt(v)} min</title></line>`;});
    });
    const hoverData=mode==='curves'||presentation.coordinateData?`data-curve-chart="true" data-domain-start="${lo}" data-domain-end="${hi}" data-signal-low="${lowY}" data-signal-high="${highY}" data-chart-left="${L}" data-chart-width="${IW}" data-chart-top="${T}" data-chart-height="${IH}"`:'';
    return `<svg role="img" ${hoverData} aria-label="${axis==='raw'?'原始時間':'對齊時間'}層析圖" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${svg}<text x="${L+IW/2}" y="${H-6}" text-anchor="middle" font-size="13" fill="#48534f">${axis==='raw'?'原始 RT':'對齊 RT'}（min）</text></svg>`;
  }
  function plotCard(item,axis,signalLabel,wide=false){
    const isAligned=axis==='aligned', coverage=alignmentCoverage(item), total=Math.max(coverage.total,coverage.available);
    if(isAligned&&!alignedAvailable(item))return `<p class="plot-note" id="plot-aligned"><strong>對齊時間圖不可用</strong>：來源未提供目前所選曲線與${signalLabel}的對齊座標；不以原始座標代替。</p>`;
    const calibration=isAligned?`<p class="alignment-summary ${coverage.available<total?'partial':''}">${coverage.available} / ${total} 個顯示中樣本有對齊座標${coverage.available<total?'；其餘不外插':''}</p>`:'';
    return `<section class="plot-card" id="plot-${axis}"><div class="plot-header"><div><h4>${isAligned?'對齊 RT':'原始 RT'} · ${signalLabel}</h4>${calibration}</div><button type="button" data-zoom="${axis}">放大 ↗</button></div>${isAligned?'':rtViewControls(item)}<div class="svg-container" id="svg-${axis}">${plot(item,axis,false,wide)}</div></section>`;
  }
  function rtViewControls(item) {
    if (!Array.isArray(item.review_rt_window)) return '';
    let alignedWindow=null;
    if(mode==='curves'&&curveWorkspaceAxis()==='aligned'){
      let lower=Infinity,upper=-Infinity;
      for(const curve of item.curves||[])for(const value of curve.aligned_rts||[])if(finite(value)){lower=Math.min(lower,value);upper=Math.max(upper,value);}
      if(finite(lower)&&finite(upper))alignedWindow=[lower,upper];
    }
    const window = state.plotWindow || alignedWindow || item.review_rt_window;
    return `<div class="rt-range"><span aria-hidden="true">RT</span><input id="plot-rt-start" type="number" step="any" value="${drt(window[0])}" aria-label="顯示 RT 起點"><span aria-hidden="true">–</span><input id="plot-rt-end" type="number" step="any" value="${drt(window[1])}" aria-label="顯示 RT 終點"><button type="button" data-rt-view="apply" aria-label="套用範圍">套用</button><button type="button" class="text-action" data-rt-view="full" aria-label="完整已載入範圍">全部</button></div>`;
  }
  function fieldValue(value){
    // Serialized objects/lists from source records read as key-value lines instead of raw JSON text.
    if(typeof value==='string'&&/^\s*[\[{]/.test(value)){try{const parsed=JSON.parse(value);if(Array.isArray(parsed)&&parsed.every(v=>v===null||typeof v!=='object'))return esc(parsed.map(v=>v??'未提供').join('、')||'（空）');if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed)){const entries=Object.entries(parsed);return entries.length?`<dl class="kv">${entries.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v!==null&&typeof v==='object'?JSON.stringify(v):v??'未提供')}</dd>`).join('')}</dl>`:'（空）';}}catch(_){}}
    return value===''?'<span class="muted">（空）</span>':esc(value??'未提供');
  }
  function evidencePanel(item){
    if(!state.evidence)return '';const record=records(item).find(r=>recordRefText(r.ref)===state.evidence);
    if(!record)return `<aside class="evidence-panel" id="evidence-panel" tabindex="-1" aria-live="polite"><div class="evidence-header"><div><span class="eyebrow">${esc(state.evidenceContext||'目前資料')}</span><h3>來源內容未提供</h3></div><button type="button" class="evidence-close" id="close-evidence">關閉來源</button></div><p>${records(item).length?'這筆引用沒有對應到本報告內嵌的可讀紀錄。':'本報告未提供可讀來源紀錄，無法在離線頁面中補出說明。'}</p><details><summary>完整紀錄與引用</summary><div class="technical-ref"><code>${esc(state.evidence)}</code><button type="button" data-copy="${esc(state.evidence)}">複製</button></div><p class="control-note">本報告僅保留引用，未包含內容。</p></details></aside>`;
    const fields=Array.isArray(record.fields)?record.fields:[],links=Array.isArray(record.links)?record.links:[],tech=Array.isArray(record.technical_refs)?record.technical_refs:[];
    return `<aside class="evidence-panel" id="evidence-panel" tabindex="-1" aria-live="polite"><div class="evidence-header"><div><span class="eyebrow">${esc(state.evidenceContext||'目前資料')}</span><h3>${esc(record.title||'可讀來源紀錄')}</h3></div><button type="button" class="evidence-close" id="close-evidence">關閉來源</button></div>${record.summary?`<p class="evidence-summary">${esc(record.summary)}</p>`:''}${links.length?`<section class="record-links"><h4>相關紀錄</h4>${links.map(link=>records(item).some(r=>recordRefText(r.ref)===recordRefText(link.ref))?`<button type="button" data-record-ref="${esc(recordRefText(link.ref))}">${esc(link.label||'查看相關紀錄')}</button>`:`<div class="external-reference"><strong>${esc(link.label||'外部引用')}</strong><span>本報告僅保留引用，未包含內容。</span></div>`).join('')}</section>`:''}<details><summary>完整紀錄與引用</summary>${fields.length?`<dl class="evidence-fields">${fields.map(f=>`<div><dt>${esc(f.label||'欄位')}</dt><dd>${fieldValue(f.value)}</dd></div>`).join('')}</dl>`:''}${tech.length?tech.map(t=>`<div class="technical-ref"><span><strong>${esc(t.label||'技術引用')}</strong><code>${esc(t.value??'')}</code></span><button type="button" data-copy="${esc(t.value??'')}">複製</button></div>`).join(''):`<div class="technical-ref"><span><strong>紀錄識別碼</strong><code>${esc(record.ref)}</code></span><button type="button" data-copy="${esc(record.ref)}">複製</button></div>`}</details></aside>`;
  }
  function differentialSeries(curve){
    if(display.signal==='raw')return {rts:curve.raw_rts||[],intensities:curve.raw_intensities||[]};
    return (curve.processed_curves||[]).find(s=>s.kind==='smoothed')||{rts:[],intensities:[]};
  }
  function differentialColor(curve){
    const decision=curve.quality_flags?.successor_decision;
    return decision==='write_authorized'?'#0072B2':decision==='no_write_detected_baseline_preserved'?'#D55E00':'#888888';
  }
  function differentialPlot(item,side,sharedMax){
    const f=item.transition_review,window=f[`${side}_rt_window`],center=f[`${side}_center_rt`];
    const lo=window[0],hi=window[1],W=760,H=330,L=62,T=24,IW=672,IH=248;
    const x=v=>L+(v-lo)/(hi-lo)*IW,y=v=>T+IH-v/sharedMax*IH;
    let svg=`<defs><clipPath id="differential-${side}"><rect x="${L}" y="${T}" width="${IW}" height="${IH}"/></clipPath></defs><text x="8" y="14" font-size="13" fill="#48534f">訊號強度</text>`;
    for(let i=0;i<=4;i++){const v=sharedMax*i/4;svg+=`<line x1="${L}" y1="${y(v)}" x2="${L+IW}" y2="${y(v)}" stroke="#e4e9ec"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end" font-size="10">${v===0?'0':v.toExponential(1)}</text><text x="${x(lo+(hi-lo)*i/4)}" y="296" text-anchor="middle" font-size="10">${rt(lo+(hi-lo)*i/4)}</text>`;}
    svg+=`<path d="M${L},${T}V${T+IH}H${L+IW}" fill="none" stroke="#91a0a8"/><line data-marker="centre-rt" x1="${x(center)}" x2="${x(center)}" y1="${T}" y2="${T+IH}" stroke="#222" stroke-dasharray="6 4"><title>Hypothesis centre RT ${drt(center)} min</title></line>`;
    (item.curves||[]).filter(c=>c.kind===side&&state.visible.has(c.sample_id)&&(!state.sample||state.sample===c.sample_id)).forEach(c=>{
      const s=differentialSeries(c),points=s.rts.map((v,i)=>finite(v)&&finite(s.intensities[i])?`${x(v)},${y(s.intensities[i])}`:'').filter(Boolean).join(' ');
      if(points)svg+=`<polyline data-kind="${display.signal}" data-decision="${esc(c.quality_flags?.successor_decision||'')}" clip-path="url(#differential-${side})" fill="none" stroke="${differentialColor(c)}" stroke-width="2" opacity=".75" points="${points}"><title>${esc(sampleName(c.sample_id))} · ${esc(c.quality_flags?.successor_decision||'unclassified')}</title></polyline>`;
    });
    return `<svg role="img" aria-label="${side} hypothesis chromatogram, shared absolute y scale" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${svg}<text x="398" y="324" text-anchor="middle" font-size="11">原始 RT（min）</text></svg>`;
  }
  function differentialScatter(item){
    const bySample=new Map();(item.curves||[]).forEach(c=>{if(!state.visible.has(c.sample_id)||state.sample&&state.sample!==c.sample_id)return;const pair=bySample.get(c.sample_id)||{};pair[c.kind]=c;bySample.set(c.sample_id,pair);});
    const maximum=c=>Math.max(0,...((c?.processed_curves||[]).find(s=>s.kind==='smoothed')?.intensities||[]));
    const pairs=[...bySample.values()].filter(p=>p.source&&p.successor).map(p=>({source:maximum(p.source)+1,successor:maximum(p.successor)+1,curve:p.successor}));
    const max=Math.max(1,...pairs.map(p=>Math.max(p.source,p.successor))),logMax=Math.max(1,Math.log10(max));
    const L=68,T=22,S=230,x=v=>L+Math.log10(v)/logMax*S,y=v=>T+S-Math.log10(v)/logMax*S;
    let svg=`<path d="M${L},${T}V${T+S}H${L+S}" fill="none" stroke="#91a0a8"/><line data-reference="identity" x1="${x(1)}" y1="${y(1)}" x2="${x(max)}" y2="${y(max)}" stroke="#666" stroke-dasharray="6 4"/><text x="${L+S/2}" y="285" text-anchor="middle" font-size="11">Source Gaussian15 max + 1</text><text transform="translate(16 ${T+S/2}) rotate(-90)" text-anchor="middle" font-size="11">Successor Gaussian15 max + 1</text>`;
    pairs.forEach(p=>svg+=`<circle data-decision="${esc(p.curve.quality_flags?.successor_decision||'')}" cx="${x(p.source)}" cy="${y(p.successor)}" r="5" fill="${differentialColor(p.curve)}" opacity=".8"><title>${esc(sampleName(p.curve.sample_id))}: ${num(p.source)} / ${num(p.successor)}</title></circle>`);
    return `<svg role="img" aria-label="Log-log source versus successor Gaussian15 maxima with y equals x" viewBox="0 0 330 300" xmlns="http://www.w3.org/2000/svg">${svg}</svg>`;
  }
  function differentialNotes(f){
    const fields=[['Feature inclusion gate',f.feature_inclusion_gate],['Identity authority gate',f.identity_authority_gate],['Relationship',f.source_successor_relationship],['Source identity',f.source_identity_decision],['Source accepted cells',f.source_accepted_cell_count],['Successor identity',f.successor_identity_decision],['Successor accepted cells',f.successor_accepted_cell_count],['Δm/z',f.mz_delta],['ΔRT (min)',f.rt_delta],['Median Gaussian15 successor/source max ratio',f.median_gaussian15_successor_source_max_ratio]];
    return `<p>Diagnostic only: feature inclusion does not grant matrix write authority. Replacement, merge, or deduplication requires a separate expected diff. A source peak does not invalidate successor inclusion; different m/z values may be expected.</p><dl class="factlist">${fields.map(([label,value])=>`<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join('')}</dl>`;
  }
  function differentialDetail(item){
    const f=item.transition_review,shown=(item.curves||[]).filter(c=>state.visible.has(c.sample_id)&&(!state.sample||state.sample===c.sample_id));
    const sharedMax=Math.max(1,...shown.flatMap(c=>differentialSeries(c).intensities.filter(finite)))*1.05;
    const roster=itemRows(item).map(r=>{const c=(item.curves||[]).find(x=>x.sample_id===r.sample_id),decision=c?.quality_flags?.successor_decision||'unclassified';return `<label class="legend-item"><input type="checkbox" data-sample-visible="${esc(r.sample_id)}" ${state.visible.has(r.sample_id)?'checked':''}><span class="legend-line" style="background:${differentialColor(c||{})}"></span>${esc(sampleName(r.sample_id))} · ${esc(decision)} · ${esc(r.status_label||'')}</label><button type="button" data-focus="${esc(r.sample_id)}" aria-label="查看 ${esc(sampleName(r.sample_id))}">查看樣本</button>`;}).join('');
    setHTML($('workspace-content'),`<div class="detail-top"><div><p class="detail-eyebrow">CID-NL feature inclusion / identity relationship review</p><h2>${esc(item.title)} | Candidate ${esc(f.write_authorized_count)} / Existing ${esc(f.no_write_detected_baseline_preserved_count)}</h2><p class="detail-summary">${esc(f.sample_count)} samples · ${esc(f.source_peak_hypothesis_id)} → ${esc(f.successor_peak_hypothesis_id)}</p></div><span class="badge badge-rest">診斷曲線</span></div><section class="advanced-inspection" aria-label="Source / successor differential review"><div class="inspection-heading"><h3 id="inspection-title" tabindex="-1">${state.sample?esc(sampleName(state.sample)):'所有可見樣本'}</h3>${state.sample?'<button type="button" class="return-all" id="btn-unfocus">← 返回所有樣本</button>':''}</div><div class="sample-legend" aria-label="樣本曲線顯示">${roster}</div><div class="charts-grid"><section class="plot-card"><h4>Source · ${esc(f.source_peak_hypothesis_id)} · m/z ${mz(f.source_mz)} · RT ${drt(f.source_center_rt)}</h4><div class="svg-container">${differentialPlot(item,'source',sharedMax)}</div></section><section class="plot-card"><h4>Successor · ${esc(f.successor_peak_hypothesis_id)} · m/z ${mz(f.successor_mz)} · RT ${drt(f.successor_center_rt)}</h4><div class="svg-container">${differentialPlot(item,'successor',sharedMax)}</div></section><section class="plot-card"><h4>Same-sample max relationship · log-log</h4><div class="svg-container">${differentialScatter(item)}</div></section><section class="plot-card"><h4>Review notes</h4>${differentialNotes(f)}</section></div><p class="reading-hint">兩側使用相同絕對訊號上限；黑色虛線標示各 hypothesis 中心 RT。藍色：write authorized；橘色：baseline preserved；灰色：其他。</p></section>`);
  }
  function detail(){
    deltaView?.destroy();deltaView=null;
    const item=items.find(i=>i.id===state.id);if(!item){const excluded=filtered.filter(isCollapsedExcluded).length;setHTML($('workspace-content'),excluded?`<div class="empty-state"><strong>目前未選取項目</strong><p>左側有 ${excluded} 筆已排除候選；展開後可查看保存曲線與來源紀錄。</p></div>`:'<p class="empty-state">這份報告沒有符合的項目。</p>');return;}normalize(item);if(item.transition_review?.review_kind==='cid_nl_differential'){differentialDetail(item);return;}
    if(item.delta_mass){
      deltaView=window.XicInspectorDeltaMass.mount($('workspace-content'),item,{
        renderXic(target,window,width,presentation){
          state.plotWindow=window;
          setHTML(target,plot(item,'raw',true,true,width,'raw',presentation));
        },
      });
      return;
    }
    if(mode==='result'){
      setHTML($('workspace-content'),`<section class="sample-section"><h3>已保存的樣本結果</h3><div class="table-scroll"><table class="sample-table saved-table"><thead><tr><th>樣本</th><th class="num">Area</th><th>判定</th><th><span class="sr-only">依據</span></th></tr></thead><tbody>${itemRows(item).map(r=>{const value=r.quantitative_value,numeric=finite(value)&&value!==0;return `<tr class="${numeric?'':'saved-empty'}"><td><span class="sample-dot" style="background:${color(r.sample_id)}"></span>${esc(sampleName(r.sample_id))}</td><td class="num">${numeric?Number(value).toExponential(3):value===0?'<span class="result-zero">0</span>':has(r,'quantitative_value')?'<span class="result-null">空值</span>':'<span class="result-missing">—</span>'}</td><td class="saved-note">${numeric?'':esc(r.decision_label||'未提供定量決策')}</td><td class="num">${evidenceButtons(r.decision_refs,`${sampleName(r.sample_id)} 的定量依據`,'查看依據')}</td></tr>${state.evidence&&state.evidenceContext===`${sampleName(r.sample_id)} 的定量依據`?`<tr class="evidence-row"><td colspan="4">${evidencePanel(item)}</td></tr>`:''}`;}).join('')}</tbody></table></div></section>${state.evidence&&!itemRows(item).some(r=>state.evidenceContext===`${sampleName(r.sample_id)} 的定量依據`)?evidencePanel(item):''}${(panel=>panel?`<details class="internal-evidence"><summary>內部證據</summary>${panel}</details>`:'')(sampleRelationshipPanel(item))}`);
      return;
    }
    const graph=state.sample?`${sampleName(state.sample)} · 目前查看`:'所有可見樣本',selectedSignalLabel={raw:'原始訊號',baseline:'基線',smoothed:'平滑訊號',positive:'基線以上訊號'}[display.signal]||'訊號',signalLabel=selectedSignalLabel;
    if(mode==='curves'){
      renderCurveWorkspace(item);
      return;
    }
    const aligned=alignedAvailable(item);
    setHTML($('workspace-content'),`<div class="detail-top"><div><p class="detail-eyebrow">${itemName(item)}${item.method_label?` · ${esc(item.method_label)}`:''}</p><h2>${breakable(itemTitle(item))}</h2><p class="detail-reason">${esc(item.reason_label||'未記錄項目判定說明。')}</p></div><span class="badge badge-${esc(item.stratum)}">${esc(badge(item))}</span></div>${summary(item)}${evidencePanel(item)}<section class="advanced-inspection" aria-label="候選與積分"><section class="inspection-section" id="inspection-workspace"><div class="inspection-heading"><div class="inspection-title"><h3 id="inspection-title" tabindex="-1">${esc(graph)}</h3>${state.sample?'<button type="button" class="return-all" id="btn-unfocus">← 返回所有樣本</button>':''}</div><span class="focus-status">${state.sample?'候選與擷取':'曲線與積分'}</span></div><div class="selection-controls">${state.sample?`<div class="control-group"><span class="control-label">${channelRoles(item,state.sample)?'曲線通道':'候選峰'}</span>${candidateControl(item)}</div><div class="control-group"><span class="control-label">擷取階段</span>${captureControl(item)}</div>`:''}${overlayControl(item)}</div><div class="chart-toolbar"><div class="sample-legend" aria-label="樣本曲線顯示">${legend(item)}</div>${channelKey(item)}<label><input id="chk-bounds" type="checkbox" ${state.bounds?'checked':''}> 顯示積分範圍與峰頂</label></div><div class="analysis-layout ${state.evidence?'with-evidence':''}"><div class="chart-column"><div class="charts-grid ${aligned?'':'single'}">${plotCard(item,'raw',signalLabel,!aligned)}${aligned?plotCard(item,'aligned',signalLabel):''}</div>${aligned?'':plotCard(item,'aligned',signalLabel)}<section class="integration-card" id="integration-readout">${readout(item)}</section></div></div></section></section>${sampleRelationshipPanel(item)}<section class="sample-section" aria-label="所有樣本的正式結果與支持紀錄"><div class="section-heading"><h3>所有樣本的正式結果與支持紀錄</h3></div>${sampleTable(item)}</section>`);
  }
  function reset(){state.plotWindow=null;rel.captures={};state.sample=null;state.candidate='';state.capture='';state.evidence='';state.evidenceContext='';evidenceOpener=null;}
  function clearFilters(){for(const id of ['search','mz-min','mz-max','rt-min','rt-max'])$(id).value='';list();}
  if (mode === 'offline') $('record-list').addEventListener('click',event=>{const toggle=event.target.closest('[data-excluded-toggle]');if(toggle){const opening=!state.excludedOpen;state.excludedOpen=opening;list(!(opening&&!state.id));$('record-list').querySelector('[data-excluded-toggle]')?.focus();return;}const b=event.target.closest('[data-item]');if(!b)return;state.id=b.dataset.item;reset();list();$('record-list').querySelector('[aria-current="true"]')?.focus();});
  $('detail-panel').addEventListener('click', event => {
    const button = event.target.closest('[data-rt-view]');
    if (!button) return;
    const item = items.find(i => i.id === state.id);
    if (!item) return;
    const start = $('plot-rt-start'), end = $('plot-rt-end');
    start.setCustomValidity('');
    if (button.dataset.rtView === 'full') {
      let lower = Infinity, upper = -Infinity;
      for (const curve of item.curves || []) {
        const rt = mode==='curves'&&curveWorkspaceAxis()==='aligned'?curve.aligned_rts||[]:curve.raw_rts||[];
        if (rt.length) { lower = Math.min(lower, rt[0]); upper = Math.max(upper, rt[rt.length - 1]); }
      }
      if (!finite(lower) || !finite(upper) || lower >= upper) return;
      state.plotWindow = [lower, upper];
    } else {
      const lower = start.valueAsNumber, upper = end.valueAsNumber;
      if (!finite(lower) || !finite(upper) || lower >= upper) {
        start.setCustomValidity('請輸入有效範圍，起點必須小於終點。');start.reportValidity();return;
      }
      state.plotWindow = [lower, upper];
    }
    detail();
  });
  $('detail-panel').addEventListener('click',event=>{
    const item=items.find(i=>i.id===state.id);if(!item)return;
    const axis=event.target.closest('[data-time-axis]');
    if(!axis)return;event.stopImmediatePropagation();
    rel.axis=axis.dataset.timeAxis;
    detail();
  });
  $('detail-panel').addEventListener('change',event=>{
    const el=event.target;
    if(el.dataset.laneCapture)rel.captures[el.dataset.laneCapture]=el.value;
    else return;event.stopImmediatePropagation();detail();
  });
  $('detail-panel').addEventListener('change',event=>{const el=event.target,item=items.find(i=>i.id===state.id);if(!item)return;if(el.dataset.inspectorId==='plot-rt-start'||el.dataset.inspectorId==='plot-rt-end')return;if(el.dataset.inspectorId==='candidate-select'){state.candidate=el.value;state.capture='';state.evidence='';normalize(item);}if(el.dataset.inspectorId==='capture-select'){state.capture=el.value;state.evidence='';}if(el.dataset.overlay){if(el.checked)display.overlays.add(el.dataset.overlay);else display.overlays.delete(el.dataset.overlay);refreshViews();return;}if(el.dataset.inspectorId==='chk-bounds')state.bounds=el.checked;if(el.dataset.sampleVisible){if(el.checked)state.visible.add(el.dataset.sampleVisible);else state.visible.delete(el.dataset.sampleVisible);}detail();});
  $('detail-panel').addEventListener('click',async event=>{const item=items.find(i=>i.id===state.id);if(!item)return;const focus=event.target.closest('[data-focus]');if(focus){state.sample=focus.dataset.focus;state.candidate='';state.capture='';state.evidence='';state.evidenceContext='';evidenceOpener=null;normalize(item,true);detail();afterPaint(()=>{const heading=$('inspection-title');heading?.focus({preventScroll:true});heading?.scrollIntoView({behavior:'smooth',block:'start'});});return;}if(event.target.closest('[data-inspector-id="btn-unfocus"]')){state.sample=null;state.candidate='';state.capture='';state.evidence='';state.evidenceContext='';evidenceOpener=null;normalize(item);detail();afterPaint(()=>{const heading=$('inspection-title');heading?.focus({preventScroll:true});heading?.scrollIntoView({behavior:'smooth',block:'start'});});return;}const evidence=event.target.closest('[data-evidence-ref]');if(evidence){evidenceOpener={ref:evidence.dataset.evidenceRef,context:evidence.dataset.evidenceContext||''};state.evidence=evidenceOpener.ref;state.evidenceContext=evidenceOpener.context;detail();afterPaint(()=>{const panel=$('evidence-panel');panel?.scrollIntoView({behavior:'smooth',block:'start'});panel?.focus({preventScroll:true});});return;}const link=event.target.closest('[data-record-ref]');if(link){state.evidence=link.dataset.recordRef;detail();afterPaint(()=>{const panel=$('evidence-panel');panel?.scrollIntoView({behavior:'smooth',block:'start'});panel?.focus({preventScroll:true});});return;}if(event.target.closest('[data-inspector-id="close-evidence"]')){state.evidence='';state.evidenceContext='';detail();afterPaint(()=>{const opener=[...root.querySelectorAll('[data-evidence-ref]')].find(el=>el.dataset.evidenceRef===evidenceOpener?.ref&&(el.dataset.evidenceContext||'')===evidenceOpener?.context);opener?.scrollIntoView({block:'nearest'});opener?.focus({preventScroll:true});evidenceOpener=null;});return;}const copy=event.target.closest('[data-copy]');if(copy){try{await navigator.clipboard.writeText(copy.dataset.copy||'');if(destroyed)return;const old=copy.textContent;copy.textContent='已複製';later(()=>copy.textContent=old,1200);}catch(_){copy.textContent='無法複製';}return;}const zoom=event.target.closest('[data-zoom]');if(zoom){dialogOpener=zoom;$('zoom-title').textContent=`${itemTitle(item)} · ${state.sample?sampleName(state.sample):'所有可見樣本'} · ${zoom.dataset.zoom==='raw'?'原始時間':'對齊時間'}`;setHTML($('zoom-container'),plot(item,zoom.dataset.zoom));$('zoom-dialog').showModal();}});
  if (mode === 'offline') {
  for(const id of ['search','mz-min','mz-max','rt-min','rt-max'])$(id).addEventListener('input',list);
  $('clear').addEventListener('click',clearFilters);$('active-filters').addEventListener('click',event=>{if(event.target.closest('[data-inspector-id="clear-inline"]'))clearFilters();});
  }
  $('zoom-close').addEventListener('click',()=>$('zoom-dialog').close());
  $('zoom-dialog').addEventListener('close',event=>{
    event.stopPropagation();
    if(destroyed)return;
    if(mode==='curves'){
      hideCurveTooltip();$('curve-workspace')?.append($('curve-tooltip'));
    }
    dialogOpener?.focus();
  });
  root.addEventListener('keydown',event=>{const dialog=root.querySelector('dialog[open]');if(dialog){if(event.key==='Escape'){event.preventDefault();event.stopPropagation();dialog.close();}return;}if(event.key==='Escape'&&state.evidence){event.preventDefault();event.stopPropagation();state.evidence='';state.evidenceContext='';detail();afterPaint(()=>{const opener=[...root.querySelectorAll('[data-evidence-ref]')].find(el=>el.dataset.evidenceRef===evidenceOpener?.ref&&(el.dataset.evidenceContext||'')===evidenceOpener?.context);opener?.scrollIntoView({block:'nearest'});opener?.focus({preventScroll:true});evidenceOpener=null;});return;}if(mode !== 'offline'||event.target.closest('input,textarea,select')||!['ArrowDown','ArrowUp'].includes(event.key)||!listed.length)return;event.preventDefault();const i=listed.findIndex(x=>x.id===state.id);state.id=listed[Math.max(0,Math.min(listed.length-1,i+(event.key==='ArrowDown'?1:-1)))].id;reset();list();$('record-list').querySelector('[aria-current="true"]')?.focus();});
  if (mode === 'offline') $('btn-report-info').addEventListener('click',event=>{dialogOpener=event.target;const r=data.selection||{},source=$('report-info-source').dataset,range=(a,b,f)=>a==null&&b==null?'不限':`${a==null?'不限':f(a)} 至 ${b==null?'不限':f(b)}`;setHTML($('report-info-body'),`<dl class="factlist"><dt>檢閱方式</dt><dd>${esc(source.strategyLabel)}</dd><dt>包含項目</dt><dd>${items.length} 筆（執行共 ${esc(source.runTotal)} 筆，${esc(source.eligibleCount)} 筆可供本次選取）</dd><dt>來源檔案</dt><dd>${esc(source.sourceName)}</dd><dt>可用資料</dt><dd>${esc(source.capabilityLabel)}</dd>${data.summary?.matrix_path?`<dt>Matrix 路徑</dt><dd>${esc(data.summary.matrix_path)}</dd>`:''}<dt>m/z 範圍</dt><dd>${range(r.mz_min,r.mz_max,mz)}</dd><dt>RT（min）</dt><dd>${range(r.rt_min,r.rt_max,rt)}</dd><dt>樣本</dt><dd>${r.sample_ids?.length?r.sample_ids.map(sampleName).map(esc).join('、'):'全部樣本'}</dd></dl>`);$('report-info-dialog').showModal();});
  $('report-info-close').addEventListener('click',()=>$('report-info-dialog').close());$('report-info-dialog').addEventListener('close',event=>{event.stopPropagation();if(!destroyed)dialogOpener?.focus();});
  function syncSignalSwitch(){
    const curves=items.some(item=>item.curves?.length&&!item.delta_mass);
    root.querySelectorAll('.signal-switch,.signal-bar').forEach(el=>el.hidden=!curves);
    root.querySelectorAll('[data-signal]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.signal===display.signal)));
  }
  function refreshViews(){displayViews.forEach(view=>view());}
  const showDisplay=()=>{syncSignalSwitch();if(data.summary?.source_kind!=='delta_mass_review')detail();};
  displayViews.add(showDisplay);cleanups.push(()=>displayViews.delete(showDisplay));
  root.addEventListener('click',event=>{
    const button=event.target.closest('[data-signal]');
    if(!button||button.dataset.signal===display.signal)return;
    const hadFocus=document.activeElement===button;
    display.signal=button.dataset.signal;refreshViews();
    if(mode==='curves'&&hadFocus)root.querySelector(`[data-signal="${display.signal}"]`)?.focus({preventScroll:true});
  });
  syncSignalSwitch();
  if(mode === 'offline'){if(data.notice){$('report-notice').textContent=data.notice;$('report-notice').hidden=false;}list();}
  else detail();
  return instance;
  }
  window.XicInspector = Object.freeze({mount});
})();
