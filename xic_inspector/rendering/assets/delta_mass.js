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
  const mass = value => value == null ? '—' : value.toLocaleString('en-US', {maximumFractionDigits:6});
  const color = index => `var(--dm-series-${Math.min(index + 1, 4)})`;
  // Four distinct hues cover Pt; additional positions also use distinct dashes.
  const dash = index => index === 0 ? '' : index === 1 ? '7 4' : index === 2 ? '2 3' : `${index * 3} 4 2 4`;
  const positionName = (saved, label) => {
    const name = label.toLowerCase();
    if (name === 'base' || name === 'light') return 'BASE';
    const plus = /^plus[_ ](.+)$/i.exec(label);
    if (plus) return `PLUS ${plus[1]}`;
    if (name === 'heavy') {
      const position = saved.positions.find(p => p.label === label);
      return position ? `PLUS ${number(position.delta_da)}` : 'PLUS';
    }
    return label;
  };
  const nlStatus = status => ({supported:'有 NL 支持', no_match:'未找到 NL 支持', no_ms2:'沒有相關 MS2', no_spectrum:'缺少 spectrum', incomplete:'讀取不完整', no_peak_bounds:'缺少峰範圍', ambiguous:'峰歸屬不明', unresolved:'前驅或歸屬未確認'}[status] || status);
  const nlShortStatus = status => ({supported:'支持', no_match:'未見支持', no_ms2:'無相關 MS2'}[status] || nlStatus(status));
  const findings = saved => (saved.neutral_losses || []).filter(n => ['supported','ambiguous','unresolved','incomplete','no_peak_bounds','no_spectrum'].includes(n.status));
  const envelopeReason = (reason, saved) => {
    const [code, positions] = (reason || '').split(':');
    const text = {missing_position:'缺少位置',no_trace:'沒有 XIC',rt_grid_mismatch:'四位置 scan 時間不一致',invalid_observations:'觀測資料無效',insufficient_background:'背景區間資料不足',no_base_signal:'base 沒有淨訊號',nonpositive_core:'核心淨訊號不大於零',mass_below_model:'超出單電荷模型範圍'}[code] || code;
    return positions ? `${text}：${positions.split(',').map(p => positionName(saved,p)).join(', ')}` : text;
  };
  const envelopeSummary = saved => {
    const e = saved.envelope;
    if (!e) return '';
    return e.status === 'available' ? `${e.model} · 包絡偏差 ${number(e.spread)}` : `包絡無法比較：${envelopeReason(e.reason,saved)}`;
  };
  const summary = (saved, compact = false) => {
    const results = compact ? findings(saved) : saved.neutral_losses || [], multiple = new Set(results.map(n => n.tag)).size > 1;
    const label = compact ? nlShortStatus : nlStatus;
    if (compact && results.length && results.length === saved.neutral_losses.length && results[0].status !== 'supported' && results.every(n => n.status === results[0].status)) return [envelopeSummary(saved), `NL：${label(results[0].status)}`].filter(Boolean).join(' · ');
    const nl = results.map(n => compact
      ? n.status === 'supported' ? `${positionName(saved,n.position_label)} 有 ${n.tag} 支持` : `${positionName(saved,n.position_label)} / ${n.tag}：${label(n.status)}`
      : `${positionName(saved,n.position_label)}${multiple ? ` / ${n.tag}` : ''}：${label(n.status)}`).join(' · ');
    return [envelopeSummary(saved), nl].filter(Boolean).join(' · ');
  };
  function table(headers, rows) {
    const wrap = node('div', undefined, 'table-scroll'), result = node('table', undefined, 'sample-table dm-table');
    const head = node('tr'); headers.forEach(label => head.append(node('th', label)));
    const thead = node('thead'); thead.append(head); result.append(thead);
    const body = node('tbody');
    for (const values of rows) { const row = node('tr'); values.forEach(value => { const cell = node('td'); if (value instanceof Node) cell.append(value); else cell.textContent = value; row.append(cell); }); body.append(row); }
    result.append(body); wrap.append(result); return wrap;
  }
  function nearest(values, target) {
    let lo = 0, hi = values.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (values[mid] < target) lo = mid; else hi = mid; }
    return Math.abs(values[lo] - target) <= Math.abs(values[hi] - target) ? lo : hi;
  }
  function mount(container, item, {renderXic}) {
    const saved = item.delta_mass, root = node('section', undefined, 'dm-review');
    const labelFor = label => positionName(saved, label);
    const nlResults = saved.neutral_losses || [];
    container.replaceChildren(root);
    // Position/scan controls belong to this view, not the host's sample controls.
    root.addEventListener('change', event => event.stopPropagation());
    root.append(node('h2', `m/z ${mass(saved.base_mz)} · ${number(saved.base_rt_apex_min)} min`));
    if (saved.envelope) {
      const e = saved.envelope, section = node('section', undefined, 'dm-envelope');
      section.setAttribute('aria-label', 'Pt 包絡比較');
      section.append(node('h3', e.status === 'available' ? `最接近的小模型：${e.model}` : envelopeSummary(saved)));
      if (e.status === 'available') {
        section.append(node('p', '比較四個位置的強度比例：把 BASE 設為 100，看實測比例是否接近模型預期。', 'dm-note'));
        const relative = value => (value * 100).toLocaleString('en-US', {maximumFractionDigits:1});
        section.append(table(['位置', '實測（BASE = 100）', '模型預期（BASE = 100）'], e.position_labels.map((label, i) => [labelFor(label), relative(e.observed_relative[i]), relative(e.expected_relative[i])])));
        section.append(node('p', `包絡偏差 ${number(e.spread)}（1 最一致，越大越不一致）· 模型碳數 ${e.carbon_count} · 同步 ${e.core_scan_count} scans`, 'dm-note'));
      }
      section.append(node('p', '固定單電荷四位置的小模型比較；只供排序與檢閱，不確認 Pt 身分，也不改原候選判定。', 'dm-note'));
      root.append(section);
    }
    const massReference = saved.positions.reduce((a,b) => a.delta_da <= b.delta_da ? a : b);
    for (const position of saved.positions) {
      if (position === massReference) continue;
      const measured = position.feature_mz != null && massReference.feature_mz != null;
      const gapLabel = saved.positions.length === 2 ? 'Δm/z' : `Δm/z ${labelFor(position.label)} − ${labelFor(massReference.label)}`;
      const gap = node('p', `${gapLabel}：${measured ? `實測 ${mass(position.feature_mz-massReference.feature_mz)} · ` : ''}設定 ${mass(position.delta_da-massReference.delta_da)}`, 'dm-mass-gap');
      gap.setAttribute('aria-label', gapLabel);
      root.append(gap);
    }
    const advanced = node('details', undefined, 'dm-advanced');
    advanced.append(node('summary', '詳細資料'));
    if (saved.status !== 'candidate') root.append(node('p', `保存判定：${saved.status} · ${saved.reject_reason}`, 'dm-note'));
    const supportButtons = [];
    const visibleResults = findings(saved);
    if (visibleResults.length) {
      const losses = [...new Set(visibleResults.map(n => `${n.tag} · ${mass(n.neutral_loss_da)} Da`))];
      const rows = [];
      for (const result of visibleResults) {
        // Only identical non-support answers collapse. Supporting positions
        // keep distinct actions so their evidence can never be crossed.
        const same = result.status !== 'supported' && rows.find(row => row.result.status === result.status && row.result.tag === result.tag && row.result.neutral_loss_da === result.neutral_loss_da);
        if (same) same.labels.push(labelFor(result.position_label));
        else rows.push({result, labels:[labelFor(result.position_label)]});
      }
      const answers = table(losses.length === 1 ? ['位置', losses[0]] : ['位置', 'NL', '判讀'], rows.map(({result, labels}) => {
        const matches = result.matches.filter(m => m.status === 'supported');
        let answer = nlShortStatus(result.status);
        if (result.status === 'supported' && matches.length) {
          const count = new Set(matches.map(m => m.scan_number)).size;
          answer = node('button', count > 1 ? `有支持（${count}）` : '有支持', 'dm-nl-support');
          answer.type = 'button';
          answer.setAttribute('aria-label', `${labelFor(result.position_label)} · ${result.tag}：有 NL 支持（${count}）`);
          answer.setAttribute('aria-pressed', 'false');
          answer.addEventListener('click', () => showSupport(result));
          supportButtons.push([answer, result]);
        }
        return losses.length === 1 ? [labels.join(' / '), answer] : [labels.join(' / '), `${result.tag} · ${mass(result.neutral_loss_da)} Da`, answer];
      }));
      answers.setAttribute('aria-label', '各位置 NL 判讀'); answers.classList.add('dm-nl-summary'); root.append(answers);
    }
    const positions = table(
      ['位置 · 設定 ΔDa', 'm/z', '保存狀態', 'Feature 峰界 (min)'],
      saved.positions.map(p => [
        `${labelFor(p.label)} · ${number(p.delta_da)}`, number(p.observed_mz ?? p.expected_mz),
        p.position_status, p.peak_rt_window?.map(number).join('–') || '—',
      ]),
    );
    positions.setAttribute('aria-label', '保存的 Delta Mass 位置證據');
    advanced.append(node('h3', 'Delta Mass 位置證據'), positions);
    const legend = node('div', undefined, 'dm-legend');
    saved.positions.forEach((p, index) => {
      const entry = node('span'), key = mark('svg', {viewBox:'0 0 30 12', width:30, height:12, 'aria-hidden':true});
      key.append(mark('line', {x1:0, x2:30, y1:6, y2:6, stroke:color(index), 'stroke-width':2, 'stroke-dasharray':dash(index)}));
      const label = item.curves[index]?.raw_rts.length ? labelFor(p.label) : `${labelFor(p.label)}（無曲線）`;
      entry.append(key, document.createTextNode(label)); legend.append(entry);
    });
    root.append(legend);
    const chart = node('div', undefined, 'svg-container dm-xic');
    const readout = node('output', '', 'dm-readout');
    readout.setAttribute('aria-live', 'polite');
    const rawTable = node('details', undefined, 'dm-data');
    rawTable.append(node('summary', 'XIC 數值'));
    const rawRows = node('div'); rawTable.append(rawRows);
    root.append(chart, readout); advanced.append(rawTable);

    const ms2 = node('section', undefined, 'dm-ms2');
    const ms2Heading = node('h3', 'MS2', 'dm-nl-measurement'); ms2Heading.tabIndex = -1; ms2.append(ms2Heading);
    const spectrum = node('div', undefined, 'dm-spectrum'), spectrumReadout = node('output', '', 'dm-readout');
    const spectrumDetails = node('div');
    spectrumReadout.setAttribute('aria-live', 'polite');
    const supportNav = node('div', undefined, 'dm-support-nav'); supportNav.hidden = true;
    const previous = node('button', '上一筆支持'), next = node('button', '下一筆支持'), supportIndex = node('span');
    previous.type = next.type = 'button';
    supportNav.append(previous, supportIndex, next);
    ms2.append(supportNav, spectrum, spectrumReadout);
    advanced.append(spectrumDetails);
    root.append(ms2);
    if (saved.ms2_read_errors.length) {
      const failures = node('details', undefined, 'dm-errors');
      failures.append(node('summary', `${saved.ms2_read_errors.length} 筆 MS2 讀取錯誤（不能當成沒有 MS2）`));
      failures.append(table(['Scan', '讀取時間窗 (min)', '錯誤'], saved.ms2_read_errors.map(e => [String(e.scan_number), e.requested_rt_window.map(number).join('–'), e.message])));
      root.append(failures);
    }
    const provenance = node('details', undefined, 'dm-provenance');
    provenance.append(node('summary', '來源與擷取設定'));
    provenance.append(node('p', '判讀沿用保存結果；曲線與 MS2 為新讀取的診斷資料，不是原決策重播。顯示範圍不重算判讀。', 'dm-note'));
    provenance.append(table(['項目', '內容'], [
      ['Candidate', saved.candidate_id], ['保存狀態', saved.status], ['保存原因', saved.reject_reason || '—'],
      ['方法', saved.method_label],
      ['Discovery', saved.discovery_directory], ['本次 RAW', saved.raw_file],
      ['m/z tolerance', `${number(saved.xic_ppm)} ppm`],
      ['已取得 RT', saved.rt_window.map(number).join('–') + ' min'],
      ['RAW 對應', '依保存的 sample 名稱選檔；未驗證是否為原分析檔'],
    ]));
    advanced.append(provenance);
    root.append(advanced);
    const xicPresentation = {
      height:260, coordinateData:true, pointLimit:50,
      emptyMessage:'這個候選沒有可用的 RAW XIC；不以保存的 height 補畫曲線。',
      curveStyle:curve => ({stroke:color(curve.position_index), dash:dash(curve.position_index), width:2}),
    };
    let window = [...saved.rt_window], selectedScan = null, width = 0, activeNl = null, supportMatches = [], supportCursor = 0;

    function showSupport(result) {
      activeNl = result;
      supportMatches = result.matches.filter(m => m.status === 'supported').slice().sort((a,b) => a.scan_number-b.scan_number);
      supportCursor = 0;
      supportButtons.forEach(([button, answer]) => button.setAttribute('aria-pressed', String(answer === result)));
      selectedScan = supportMatches[0].scan_number;
      const bounds = saved.positions.find(p => p.label === result.position_label)?.peak_rt_window;
      if (bounds) {
        const padding = Math.max(.03, (bounds[1]-bounds[0])*.15);
        setWindow([Math.max(saved.rt_window[0], bounds[0]-padding), Math.min(saved.rt_window[1], bounds[1]+padding)]);
      } else setWindow([...saved.rt_window]);
      root.scrollIntoView({block:'start'}); ms2Heading.focus({preventScroll:true});
    }
    function stepSupport(delta) {
      supportCursor = Math.max(0, Math.min(supportMatches.length-1, supportCursor+delta));
      selectedScan = supportMatches[supportCursor].scan_number;
      if (!saved.spectra.some(s => s.scan_number === selectedScan && s.rt_min >= window[0] && s.rt_min <= window[1])) setWindow([...saved.rt_window]);
      else { drawSpectrum(); drawXic(); }
    }
    previous.addEventListener('click', () => stepSupport(-1)); next.addEventListener('click', () => stepSupport(1));

    function updateRawTable() {
      if (!rawTable.open) return;
      rawRows.replaceChildren(table(['位置', 'RT (min)', '原始強度'], item.curves.flatMap(curve => curve.raw_rts.flatMap((rt, i) =>
        rt >= window[0] && rt <= window[1] ? [[labelFor(curve.position_label), String(rt), String(curve.raw_intensities[i])]] : [],
      ))));
    }
    rawTable.addEventListener('toggle', updateRawTable);
    function showXicAt(svg, target) {
      const d = svg.dataset, lo = +d.domainStart, hi = +d.domainEnd;
      const rows = item.curves.filter(c => c.raw_rts.length).map(c => {
        const index = nearest(c.raw_rts, target);
        return `${labelFor(c.position_label)}: RT ${number(c.raw_rts[index])} · ${number(c.raw_intensities[index])}`;
      });
      readout.textContent = rows.join('　');
      let crosshair = svg.querySelector('[data-dm-crosshair]');
      if (!crosshair) { crosshair = mark('line', {'data-dm-crosshair':true, stroke:'var(--subtle)', 'stroke-width':1, 'pointer-events':'none'}); svg.append(crosshair); }
      const x = +d.chartLeft + (Math.max(lo, Math.min(hi, target)) - lo) / (hi - lo) * +d.chartWidth;
      for (const [key, value] of Object.entries({x1:x, x2:x, y1:+d.chartTop, y2:+d.chartTop + +d.chartHeight})) crosshair.setAttribute(key, value);
    }
    function drawXic() {
      width = Math.max(320, chart.clientWidth);
      renderXic(chart, window, width, xicPresentation);
      const svg = chart.querySelector('svg');
      if (!svg) return;
      svg.setAttribute('tabindex', '0');
      svg.setAttribute('aria-label', 'Delta Mass 原始 XIC；左右鍵查看 scan');
      const d = svg.dataset, lo = +d.domainStart, hi = +d.domainEnd;
      const x = value => +d.chartLeft + (value - lo) / (hi - lo) * +d.chartWidth;
      const ticks = mark('g', {'aria-label':'MS2 時間點'});
      const evidenceScans = new Set((activeNl ? [activeNl] : nlResults).flatMap(n => n.matches.filter(m => m.status === 'supported').map(m => m.scan_number)));
      const visibleScans = saved.spectra.filter(scan => scan.rt_min >= lo && scan.rt_min <= hi && evidenceScans.has(scan.scan_number)).sort((a, b) => a.rt_min - b.rt_min);
      const peakBounds = saved.positions.find(p => p.label === activeNl?.position_label)?.peak_rt_window;
      if (peakBounds) peakBounds.forEach(rt => {
        if (rt < lo || rt > hi) return;
        ticks.append(mark('line', {x1:x(rt), x2:x(rt), y1:+d.chartTop+20, y2:+d.chartTop + +d.chartHeight, stroke:'var(--muted)', 'stroke-dasharray':'4 4', 'data-nl-boundary':rt, 'pointer-events':'none'}));
      });
      if (peakBounds && peakBounds[1] >= lo && peakBounds[0] <= hi) ticks.append(mark('text', {x:x(Math.max(lo, peakBounds[0]))+4, y:+d.chartTop+32, fill:'var(--muted)'}, `${labelFor(activeNl.position_label)} 峰界`));
      for (const [index, scan] of visibleScans.entries()) {
        const active = scan.scan_number === selectedScan;
        const tick = mark('line', {
          x1:x(scan.rt_min), x2:x(scan.rt_min), y1:+d.chartTop,
          y2:+d.chartTop + (active ? +d.chartHeight : 12),
          stroke:active ? 'var(--accent)' : 'var(--subtle)',
          'stroke-width':active ? 2 : 1,
          'pointer-events':'none',
          'data-ms2-scan':scan.scan_number,
        });
        tick.append(mark('title', {}, `MS2 #${scan.scan_number} · RT ${number(scan.rt_min)} · precursor ${number(scan.precursor_mz)}`));
        // Bound adjacent hit regions at their midpoint: dense runs must not
        // silently select the last painted scan instead of the nearest time.
        const left = Math.max(x(scan.rt_min) - 6, index ? x((visibleScans[index - 1].rt_min + scan.rt_min) / 2) : +d.chartLeft);
        const right = Math.min(x(scan.rt_min) + 6, index + 1 < visibleScans.length ? x((scan.rt_min + visibleScans[index + 1].rt_min) / 2) : +d.chartLeft + +d.chartWidth);
        const hit = mark('rect', {
          x:left, y:+d.chartTop, width:Math.max(0, right-left), height:24,
          fill:'transparent', 'data-ms2-scan':scan.scan_number,
        });
        hit.append(mark('title', {}, `MS2 #${scan.scan_number} · RT ${number(scan.rt_min)} · precursor ${number(scan.precursor_mz)}`));
        ticks.append(tick, hit);
      }
      svg.append(ticks);
      svg.addEventListener('click', event => {
        const scan = event.target.closest('[data-ms2-scan]');
        if (scan) {
          const id = Number(scan.dataset.ms2Scan), index = supportMatches.findIndex(m => m.scan_number === id);
          if (activeNl && index >= 0) { supportCursor = index; selectedScan = id; drawSpectrum(); drawXic(); }
          else {
            const result = nlResults.find(n => n.matches.some(m => m.status === 'supported' && m.scan_number === id));
            if (result) {
              showSupport(result);
              supportCursor = supportMatches.findIndex(m => m.scan_number === id);
              selectedScan = id; drawSpectrum(); drawXic();
            }
          }
        }
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
      drawXic(); drawSpectrum();
    }
    function drawSpectrum() {
      spectrum.replaceChildren(); spectrumDetails.replaceChildren(); spectrumReadout.textContent = '';
      ms2Heading.textContent = activeNl ? 'NL 證據' : 'MS2';
      ms2Heading.setAttribute('aria-label', ms2Heading.textContent);
      ms2.hidden = selectedScan == null;
      supportNav.hidden = !activeNl || supportMatches.length <= 1;
      supportIndex.textContent = `${supportCursor+1} / ${supportMatches.length}`;
      previous.disabled = supportCursor === 0; next.disabled = supportCursor === supportMatches.length-1;
      const scan = saved.spectra.find(s => s.scan_number === selectedScan);
      if (!scan) {
        if (activeNl) spectrum.append(node('p', `支持紀錄 #${selectedScan} 缺少 spectrum。`, 'dm-note'));
        return;
      }
      spectrumDetails.append(node('p', `Scan #${scan.scan_number} · RT ${number(scan.rt_min)} min · 記錄的 precursor m/z ${number(scan.precursor_mz)}`, 'dm-scan-heading'));
      const match = activeNl ? supportMatches[supportCursor] : null;
      if (match) {
        const basis = {parent_ms1:'parent MS1', scan_precursor:'scan precursor', scan_monoisotopic:'monoisotopic precursor'}[match.reference_source] || match.reference_source;
        ms2Heading.textContent = `${labelFor(activeNl.position_label)} · 實測 NL ${mass(match.observed_loss_da)} Da`;
        spectrumDetails.append(node('p', `實測前驅 ${mass(match.precursor_mz)}（${basis}） → 產物 ${mass(match.product_mz)} · 產物 / base peak ${number(match.product_base_ratio*100)}%`, 'dm-nl-measurement'));
      }
      const meta = scan.precursor_metadata;
      if (meta) spectrumDetails.append(node('p', `Monoisotopic m/z ${number(meta.monoisotopic_mz)} · charge ${number(meta.charge)} · isolation width ${number(meta.isolation_width_da)} Da · offset ${number(meta.isolation_offset_da)} Da`, 'dm-note'));
      if (!scan.mz.length) { spectrum.append(node('p', '此 scan 沒有 spectrum 點。')); return; }
      const W = Math.max(320, chart.clientWidth), H = 230 + (match ? 28 : 0), L = 62, R = W - 26, T = match ? 52 : 24, B = H - 45;
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
      svg.append(mark('text', {x:match ? 8 : L, y:T-10, fill:'var(--muted)'}, '原始強度'));
      svg.append(mark('text', {x:(L+R)/2, y:H-3, 'text-anchor':'middle', fill:'var(--muted)'}, 'm/z'));
      scan.mz.forEach((mz, i) => svg.append(mark('line', {x1:x(mz), x2:x(mz), y1:B, y2:y(scan.intensity[i]), stroke:'var(--accent)', 'stroke-width':1.5})));
      const highlight = mark('line', {stroke:'var(--ink)', 'stroke-width':3}); svg.append(highlight);
      if (match) {
        const index = scan.mz.indexOf(match.product_mz);
        if (index >= 0) {
          const product = mark('line', {x1:x(match.product_mz), x2:x(match.product_mz), y1:B, y2:y(scan.intensity[index]), stroke:'var(--ink)', 'stroke-width':3, 'data-nl-product-mz':match.product_mz});
          product.append(mark('title', {}, `NL 實測產物 m/z ${match.product_mz}`)); svg.append(product);
          // A dedicated annotation lane stays above every spectrum peak.
          svg.append(mark('text', {x:(L+R)/2, y:18, 'text-anchor':'middle', 'font-weight':600, fill:'var(--ink)', 'data-nl-product-label':true}, `NL 產物 m/z ${mass(match.product_mz)}`));
          svg.append(mark('path', {d:`M${(L+R)/2},26 L${x(match.product_mz)},${T-8} L${x(match.product_mz)},${y(scan.intensity[index])-3}`, fill:'none', stroke:'var(--muted)', 'stroke-dasharray':'3 3', 'pointer-events':'none', 'data-nl-leader':true}));
        } else spectrum.append(node('p', '保存的 NL 產物不在此 spectrum 中，未繪製產物標記。', 'dm-note'));
      }
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
      }); spectrumDetails.append(values);
    }
    setWindow(window);
    const observer = new ResizeObserver(() => {
      if (Math.max(320, chart.clientWidth) !== width) { drawXic(); drawSpectrum(); }
    });
    observer.observe(chart);
    return {destroy() { observer.disconnect(); root.remove(); }};
  }
  window.XicInspectorDeltaMass = Object.freeze({mount, summary});
})();
