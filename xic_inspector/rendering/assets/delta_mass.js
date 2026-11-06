/* A sample-local pattern is one candidate with several positions, not a Matrix row. */
(() => {
  const svgNS = 'http://www.w3.org/2000/svg';
  const node = (tag, text, cls) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (cls) element.className = cls;
    return element;
  };
  const mark = (tag, attributes, text) => {
    const element = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const number = value => value == null ? '—' : Number(value.toPrecision(7)).toLocaleString('en-US');
  const color = index => `var(--dm-series-${Math.min(index + 1, 3)})`;
  // Beyond the three categorical hues, position identity also uses distinct dashes.
  const dash = index => index === 0 ? '' : index === 1 ? '7 4' : index === 2 ? '2 3' : `${index * 3} 4 2 4`;
  function table(headers, rows) {
    const wrap = node('div', undefined, 'table-scroll'), result = node('table', undefined, 'sample-table dm-table');
    const head = node('tr'); headers.forEach(label => head.append(node('th', label)));
    const thead = node('thead'); thead.append(head); result.append(thead);
    const body = node('tbody');
    for (const values of rows) { const row = node('tr'); values.forEach(value => row.append(node('td', value))); body.append(row); }
    result.append(body); wrap.append(result); return wrap;
  }
  function nearest(values, target) {
    let lo = 0, hi = values.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (values[mid] < target) lo = mid; else hi = mid; }
    return Math.abs(values[lo] - target) <= Math.abs(values[hi] - target) ? lo : hi;
  }
  function mount(container, item, {renderXic}) {
    const saved = item.delta_mass, root = node('section', undefined, 'dm-review');
    container.replaceChildren(root);
    // Position/scan controls belong to this view, not the host's sample controls.
    root.addEventListener('change', event => event.stopPropagation());
    root.append(node('h2', saved.method_label), node('p', saved.sample_stem, 'dm-subtitle'));
    if (saved.status !== 'candidate') root.append(node('p', `保存判定：${saved.status} · ${saved.reject_reason}`, 'dm-note'));
    root.append(node('p', '候選判斷沿用 Discovery；曲線與 MS2 為新讀取的診斷資料，不是原決策重播。', 'dm-note'));
    const positions = table(
      ['位置 · 設定 ΔDa', '擷取 m/z', '保存 apex (min)', '保存 height', `Height / ${saved.ratio_reference_label}`, `Shape r / ${saved.shape_reference_label}`, '保存狀態'],
      saved.positions.map(p => [
        `${p.label} · ${number(p.delta_da)}`, number(p.observed_mz ?? p.expected_mz), number(p.apex_rt_min),
        number(p.height), number(p.height_ratio_to_base), number(p.shape_r), p.position_status,
      ]),
    );
    positions.setAttribute('aria-label', '保存的 Delta Mass 位置證據'); root.append(positions);
    const legend = node('div', undefined, 'dm-legend');
    saved.positions.forEach((p, index) => {
      const entry = node('span'), key = mark('svg', {viewBox:'0 0 30 12', width:30, height:12, 'aria-hidden':true});
      key.append(mark('line', {x1:0, x2:30, y1:6, y2:6, stroke:color(index), 'stroke-width':2, 'stroke-dasharray':dash(index)}));
      const label = item.curves[index]?.raw_rts.length ? p.label : `${p.label}（無曲線）`;
      entry.append(key, document.createTextNode(label)); legend.append(entry);
    });
    const toolbar = node('form', undefined, 'dm-range');
    const start = node('input'), end = node('input');
    for (const [input, label] of [[start, '顯示 RT 起點'], [end, '顯示 RT 終點']]) {
      input.type = 'number'; input.step = 'any'; input.required = true; input.setAttribute('aria-label', label);
      input.addEventListener('input', () => start.setCustomValidity(''));
    }
    const apply = node('button', '套用範圍'); apply.type = 'submit';
    const full = node('button', '完整範圍'); full.type = 'button';
    toolbar.append(node('span', 'RT'), start, node('span', '–'), end, node('span', 'min'), apply, full);
    root.append(node('h3', '原始 XIC'), legend, node('p', '共用強度座標 · 未平滑、未正規化', 'dm-note'), toolbar);
    const chart = node('div', undefined, 'svg-container dm-xic');
    const readout = node('output', '游標或方向鍵可查看原始 scan 數值。', 'dm-readout');
    readout.setAttribute('aria-live', 'polite');
    const rawTable = node('details', undefined, 'dm-data');
    rawTable.append(node('summary', '原始 XIC 數值'));
    const rawRows = node('div'); rawTable.append(rawRows);
    root.append(chart, readout, rawTable);

    const ms2 = node('section', undefined, 'dm-ms2');
    ms2.append(node('h3', 'MS2'), node('p', '時間窗內的 MS2；未判定與候選的前驅關係。', 'dm-note'));
    const scans = node('select'); scans.setAttribute('aria-label', 'MS2 scan');
    scans.append(new Option('選擇 MS2 scan', ''));
    for (const scan of saved.spectra) scans.append(new Option(`#${scan.scan_number} · RT ${number(scan.rt_min)} · precursor ${number(scan.precursor_mz)}`, String(scan.scan_number)));
    scans.disabled = !saved.spectra.length;
    const spectrum = node('div', undefined, 'dm-spectrum'), spectrumReadout = node('output', '', 'dm-readout');
    spectrumReadout.setAttribute('aria-live', 'polite');
    ms2.append(scans, spectrum, spectrumReadout); root.append(ms2);
    if (saved.ms2_read_errors.length) {
      const failures = node('details', undefined, 'dm-errors');
      failures.append(node('summary', `${saved.ms2_read_errors.length} 筆 MS2 讀取錯誤（不能當成沒有 MS2）`));
      failures.append(table(['Scan', '讀取時間窗 (min)', '錯誤'], saved.ms2_read_errors.map(e => [String(e.scan_number), e.requested_rt_window.map(number).join('–'), e.message])));
      ms2.append(failures);
    }
    const provenance = node('details', undefined, 'dm-provenance');
    provenance.append(node('summary', '來源與擷取設定'));
    provenance.append(table(['項目', '內容'], [
      ['Candidate', saved.candidate_id], ['保存狀態', saved.status], ['保存原因', saved.reject_reason || '—'],
      ['Discovery', saved.discovery_directory], ['本次 RAW', saved.raw_file],
      ['m/z tolerance', `${number(saved.xic_ppm)} ppm`],
      ['已取得 RT', saved.rt_window.map(number).join('–') + ' min'],
      ['RAW 對應', '依保存的 sample 名稱選檔；未驗證是否為原分析檔'],
    ]));
    root.append(provenance);
    let window = [...saved.rt_window], selectedScan = null, width = 0;

    function updateRawTable() {
      if (!rawTable.open) return;
      rawRows.replaceChildren(table(['位置', 'RT (min)', '原始強度'], item.curves.flatMap(curve => curve.raw_rts.flatMap((rt, i) =>
        rt >= window[0] && rt <= window[1] ? [[curve.position_label, String(rt), String(curve.raw_intensities[i])]] : [],
      ))));
    }
    rawTable.addEventListener('toggle', updateRawTable);
    function showXicAt(svg, target) {
      const d = svg.dataset, lo = +d.domainStart, hi = +d.domainEnd;
      const rows = item.curves.filter(c => c.raw_rts.length).map(c => {
        const index = nearest(c.raw_rts, target);
        return `${c.position_label}: RT ${number(c.raw_rts[index])} · ${number(c.raw_intensities[index])}`;
      });
      readout.textContent = rows.join('　');
      let crosshair = svg.querySelector('[data-dm-crosshair]');
      if (!crosshair) { crosshair = mark('line', {'data-dm-crosshair':true, stroke:'var(--subtle)', 'stroke-width':1, 'pointer-events':'none'}); svg.append(crosshair); }
      const x = +d.chartLeft + (Math.max(lo, Math.min(hi, target)) - lo) / (hi - lo) * +d.chartWidth;
      for (const [key, value] of Object.entries({x1:x, x2:x, y1:+d.chartTop, y2:+d.chartTop + +d.chartHeight})) crosshair.setAttribute(key, value);
    }
    function drawXic() {
      width = Math.max(320, chart.clientWidth);
      renderXic(chart, window, width);
      const svg = chart.querySelector('svg');
      if (!svg) return;
      svg.setAttribute('tabindex', '0');
      svg.setAttribute('aria-label', 'Delta Mass 原始 XIC；左右鍵查看 scan');
      const d = svg.dataset, lo = +d.domainStart, hi = +d.domainEnd;
      const x = value => +d.chartLeft + (value - lo) / (hi - lo) * +d.chartWidth;
      const ticks = mark('g', {'aria-label':'MS2 時間點'});
      for (const scan of saved.spectra) {
        if (scan.rt_min < lo || scan.rt_min > hi) continue;
        const active = scan.scan_number === selectedScan;
        const tick = mark('line', {
          x1:x(scan.rt_min), x2:x(scan.rt_min), y1:+d.chartTop,
          y2:+d.chartTop + (active ? +d.chartHeight : 12),
          stroke:active ? 'var(--accent)' : 'var(--subtle)',
          'stroke-width':active ? 2 : 1,
          'data-ms2-scan':scan.scan_number,
        });
        tick.append(mark('title', {}, `MS2 #${scan.scan_number} · RT ${number(scan.rt_min)} · precursor ${number(scan.precursor_mz)}`));
        const hit = mark('rect', {
          x:x(scan.rt_min)-6, y:+d.chartTop, width:12, height:24,
          fill:'transparent', 'data-ms2-scan':scan.scan_number,
        });
        hit.append(mark('title', {}, `選擇 MS2 #${scan.scan_number}`));
        ticks.append(tick, hit);
      }
      svg.append(ticks);
      svg.addEventListener('click', event => {
        const scan = event.target.closest('[data-ms2-scan]');
        if (scan) chooseScan(Number(scan.dataset.ms2Scan));
      });
      svg.addEventListener('pointermove', event => {
        const box = svg.getBoundingClientRect();
        const local = (event.clientX - box.left) / box.width * svg.viewBox.baseVal.width;
        showXicAt(svg, lo + (local - +d.chartLeft) / +d.chartWidth * (hi - lo));
      });
      const scanRts = [...new Set(item.curves.flatMap(c => c.raw_rts).filter(rt => rt >= lo && rt <= hi))].sort((a,b) => a-b);
      let cursor = 0;
      svg.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || !scanRts.length) return;
        event.preventDefault(); event.stopPropagation();
        cursor = Math.max(0, Math.min(scanRts.length - 1, cursor + (event.key === 'ArrowRight' ? 1 : -1)));
        showXicAt(svg, scanRts[cursor]);
      });
      updateRawTable();
    }
    function setWindow(next) {
      window = next;
      start.value = String(next[0]); end.value = String(next[1]);
      const visible = saved.spectra.filter(s => s.rt_min >= next[0] && s.rt_min <= next[1]);
      scans.replaceChildren(new Option('選擇 MS2 scan', ''));
      for (const scan of visible) scans.append(new Option(`#${scan.scan_number} · RT ${number(scan.rt_min)} · precursor ${number(scan.precursor_mz)}`, String(scan.scan_number)));
      scans.disabled = !visible.length;
      if (!visible.some(s => s.scan_number === selectedScan)) selectedScan = null;
      scans.value = selectedScan == null ? '' : String(selectedScan);
      start.setCustomValidity(''); drawXic(); drawSpectrum();
    }
    toolbar.addEventListener('submit', event => {
      event.preventDefault();
      const lo = Number(start.value), hi = Number(end.value);
      if (!(lo < hi && lo >= saved.rt_window[0] && hi <= saved.rt_window[1])) {
        start.setCustomValidity('請輸入已取得 RT 範圍內的起點與終點。'); start.reportValidity(); return;
      }
      setWindow([lo, hi]);
    });
    full.addEventListener('click', () => setWindow([...saved.rt_window]));
    function chooseScan(id) {
      selectedScan = id; scans.value = id == null ? '' : String(id);
      drawSpectrum(); drawXic();
    }
    scans.addEventListener('change', () => chooseScan(scans.value ? Number(scans.value) : null));
    function drawSpectrum() {
      spectrum.replaceChildren(); spectrumReadout.textContent = '';
      const scan = saved.spectra.find(s => s.scan_number === selectedScan);
      if (!scan) {
        const visible = saved.spectra.some(s => s.rt_min >= window[0] && s.rt_min <= window[1]);
        spectrum.append(node('p', visible ? '選擇上方 MS2 刻度或 scan。' : saved.ms2_read_errors.length ? '沒有可用 spectrum；請查看讀取錯誤。' : '此時間窗沒有 MS2。', 'dm-note'));
        return;
      }
      spectrum.append(node('p', `Scan #${scan.scan_number} · RT ${number(scan.rt_min)} min · 記錄的 precursor m/z ${number(scan.precursor_mz)}`, 'dm-scan-heading'));
      const meta = scan.precursor_metadata;
      if (meta) spectrum.append(node('p', `Monoisotopic m/z ${number(meta.monoisotopic_mz)} · charge ${number(meta.charge)} · isolation width ${number(meta.isolation_width_da)} Da · offset ${number(meta.isolation_offset_da)} Da`, 'dm-note'));
      if (!scan.mz.length) { spectrum.append(node('p', '此 scan 沒有 spectrum 點。')); return; }
      const W = Math.max(320, chart.clientWidth), H = 280, L = 62, R = W - 26, T = 24, B = H - 45;
      let lo = Infinity, hi = -Infinity, max = 1;
      scan.mz.forEach((mz, i) => { lo = Math.min(lo, mz); hi = Math.max(hi, mz); max = Math.max(max, scan.intensity[i]); });
      if (lo === hi) { lo -= 1; hi += 1; }
      const x = value => L + (value - lo) / (hi - lo) * (R - L), y = value => B - value / max * (B - T);
      const svg = mark('svg', {viewBox:`0 0 ${W} ${H}`, role:'img', tabindex:0, 'aria-label':`MS2 scan ${scan.scan_number}；左右鍵查看 fragment`, 'data-spectrum-scan':scan.scan_number});
      for (let i = 0; i <= 4; i++) {
        const mz = lo + (hi - lo) * i / 4, value = max * i / 4;
        svg.append(mark('line', {x1:L, x2:R, y1:y(value), y2:y(value), stroke:'var(--line)'}));
        svg.append(mark('text', {x:x(mz), y:B+20, 'text-anchor':'middle', fill:'var(--muted)'}, mz.toFixed(1)));
        svg.append(mark('text', {x:L-7, y:y(value)+4, 'text-anchor':'end', fill:'var(--muted)'}, number(value)));
      }
      svg.append(mark('text', {x:L, y:14, fill:'var(--muted)'}, '原始強度'));
      svg.append(mark('text', {x:(L+R)/2, y:H-3, 'text-anchor':'middle', fill:'var(--muted)'}, 'm/z'));
      scan.mz.forEach((mz, i) => svg.append(mark('line', {x1:x(mz), x2:x(mz), y1:B, y2:y(scan.intensity[i]), stroke:'var(--accent)', 'stroke-width':1.5})));
      const highlight = mark('line', {stroke:'var(--ink)', 'stroke-width':3}); svg.append(highlight);
      function showPeak(index) {
        const mz = scan.mz[index], intensity = scan.intensity[index];
        for (const [key, value] of Object.entries({x1:x(mz), x2:x(mz), y1:B, y2:y(intensity)})) highlight.setAttribute(key, value);
        spectrumReadout.textContent = `m/z ${mz} · 原始強度 ${intensity}`;
      }
      svg.addEventListener('pointermove', event => {
        const box = svg.getBoundingClientRect(), target = lo + ((event.clientX - box.left) / box.width * W - L) / (R - L) * (hi - lo);
        let index = 0; scan.mz.forEach((mz, i) => { if (Math.abs(mz-target) < Math.abs(scan.mz[index]-target)) index = i; }); showPeak(index);
      });
      let cursor = 0;
      svg.addEventListener('keydown', event => {
        if (!['ArrowLeft','ArrowRight'].includes(event.key)) return;
        event.preventDefault(); event.stopPropagation();
        cursor = Math.max(0, Math.min(scan.mz.length-1, cursor + (event.key==='ArrowRight'?1:-1))); showPeak(cursor);
      });
      spectrum.append(svg);
      const values = node('details', undefined, 'dm-data'); values.append(node('summary', 'Spectrum 數值'));
      values.addEventListener('toggle', () => {
        if (values.open && values.children.length === 1) values.append(table(['m/z', '原始強度'], scan.mz.map((mz, i) => [String(mz), String(scan.intensity[i])])));
      }); spectrum.append(values);
    }
    setWindow(window);
    const observer = new ResizeObserver(() => {
      if (Math.max(320, chart.clientWidth) !== width) { drawXic(); drawSpectrum(); }
    });
    observer.observe(chart);
    return {destroy() { observer.disconnect(); root.remove(); }};
  }
  window.XicInspectorDeltaMass = Object.freeze({mount, dash});
})();
