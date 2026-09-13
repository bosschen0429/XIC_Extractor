"""Method-neutral offline workbench interactions over supplied presentation data."""

# ruff: noqa: E501
WORKBENCH_JS = r"""(() => {
  const data = JSON.parse(document.getElementById('inspector-data').textContent);
  const items = data.items || [], samples = data.samples || [];
  const colors = ['#1d6fa3','#16855b','#6f46c7','#b9781c','#ab4d63','#637d8d','#2a9d8f','#e76f51'];
  const color = id => colors[Math.max(0, samples.findIndex(s => s.id === id)) % colors.length];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const json = value => esc(JSON.stringify(value ?? null, null, 2));
  const value = x => x == null ? '未提供' : esc(x);
  const number = (x, digits = 4) => typeof x === 'number' ? x.toLocaleString('en-US', {maximumFractionDigits:digits}) : '未提供';
  const sampleName = id => samples.find(s => s.id === id)?.label || id;
  const itemName = item => `項目 ${String(items.indexOf(item) + 1).padStart(2,'0')}`;
  const badge = item => ({attention:'需留意',excluded:'已排除',rest:'其他結果'}[item.stratum] || item.stratum || '既有結果');
  const $ = id => document.getElementById(id);
  const state = {id: items[0]?.id, sample: null, candidate: '', visible: new Set(samples.map(s => s.id)), bounds:true, processed:false};
  let filtered = items, dialogOpener = null;
  function witnesses(item) { return item.witnesses || item.candidates || []; }
  function filteredItems() {
    const q = $('search').value.toLowerCase();
    const limits = ['mz-min','mz-max','rt-min','rt-max'].map(id => $(id).value === '' ? null : Number($(id).value));
    return items.filter(item => {
      if (q && !JSON.stringify(item).toLowerCase().includes(q)) return false;
      if (limits.every(x => x === null)) return true;
      const rows = witnesses(item).length ? witnesses(item) : [item];
      return rows.some(w => {
        const mz = w.precursor_mz, rt = w.rt_apex_min;
        return (limits[0] === null || mz != null && mz >= limits[0]) &&
          (limits[1] === null || mz != null && mz <= limits[1]) &&
          (limits[2] === null || rt != null && rt >= limits[2]) &&
          (limits[3] === null || rt != null && rt <= limits[3]);
      });
    });
  }
  function list() {
    filtered = filteredItems();
    if (!filtered.some(i => i.id === state.id)) {state.id = filtered[0]?.id; state.candidate = '';}
    $('list-count').textContent = `${filtered.length} / ${items.length} 筆`;
    $('record-list').innerHTML = filtered.map(item => `<button class="record-button ${item.id === state.id ? 'selected' : ''}" data-item="${esc(item.id)}" aria-current="${item.id === state.id}"><div class="record-name">${itemName(item)} <span class="badge badge-${esc(item.stratum)}">${esc(badge(item))}</span></div><div class="record-meta">m/z ${number(item.precursor_mz)}<br>RT ${number(item.rt_apex_min,3)} min · ${(item.samples || []).length} 個樣本</div><div class="record-reason">${esc(item.reason_label || '查看當次結果與曲線')}</div></button>`).join('') || '<p class="empty-state">這份報告沒有符合的項目。請清除篩選條件；如需查詢整批結果，請重新產生報告。</p>';
    detail();
  }
  function candidateId(w) { return w.candidate_id || w.id || ''; }
  function detail() {
    const item = items.find(i => i.id === state.id);
    if (!item) {$('workspace-content').innerHTML = '<p class="empty-state">沒有選取項目</p>'; return;}
    const candidates = [...new Set(witnesses(item).map(candidateId).filter(Boolean))];
    if (state.candidate && !candidates.includes(state.candidate)) state.candidate = '';
    $('workspace-content').innerHTML = `
      <div class="detail-top"><div><p class="detail-eyebrow">${itemName(item)} · ${esc(badge(item))}</p><h2>m/z ${number(item.precursor_mz)}</h2><p class="detail-meta">RT ${number(item.rt_apex_min,3)} min · ${(item.samples || []).length} 個樣本 · ${candidates.length} 個候選</p></div><button id="btn-show-details">判定與來源 ↗</button></div>
      <div class="finding-card"><strong>${esc(item.reason_label || '已保留當次結果，請對照下方曲線與樣本數值。')}</strong><p class="reading-hint">${esc(item.route_label || '詳細判定與完整來源可由右上角展開查看。')}</p></div>
      <div class="chart-toolbar"><label>候選 <select id="candidate-select"><option value="">全部候選</option>${candidates.map((id,index) => {const w=witnesses(item).find(w => candidateId(w) === id);return `<option value="${esc(id)}" ${id === state.candidate ? 'selected' : ''}>候選 ${index+1} · ${esc(w.sample_stem || sampleName(w.canonical_sample_identity_id))} · RT ${number(w.rt_apex_min,3)}</option>`;}).join('')}</select></label><label><input id="chk-bounds" type="checkbox" ${state.bounds ? 'checked' : ''}> 峰界與峰頂</label><label><input id="chk-processed" type="checkbox" ${state.processed ? 'checked' : ''}> 顯示處理曲線</label><button id="btn-unfocus" ${state.sample ? '' : 'hidden'}>顯示所有樣本</button></div>
      <div class="sample-legend">${samples.map(s => `<label><input type="checkbox" data-sample="${esc(s.id)}" ${state.visible.has(s.id) ? 'checked' : ''}><span style="color:${color(s.id)}">●</span> ${esc(s.label || s.id)}</label>`).join('')}</div>
      <div class="charts-grid">${['raw','aligned'].map(axis => `<section class="plot-card"><div class="plot-header"><h4>${axis === 'raw' ? '原始時間軸' : '對齊後時間軸'}</h4><button data-zoom="${axis}">放大 ↗</button></div><div class="svg-container" id="svg-${axis}">${plot(item, axis)}</div><p class="chart-caption">${axis === 'raw' ? '實線：原始訊號；處理曲線以虛線顯示。' : '僅呈現當次保存的對齊座標。'}</p></section>`).join('')}</div>
      <section class="sample-section"><h3>各樣本結果</h3><p class="reading-hint">點樣本名稱可聚焦曲線。0 是零值；— 是空值；未提供表示沒有單一可顯示數值。</p><div class="table-scroll"><table class="sample-table"><thead><tr><th>樣本</th><th>當次狀態</th><th class="num">定量值</th><th>說明</th><th>曲線</th></tr></thead><tbody>${(item.samples || []).map(s => `<tr class="${s.sample_id === state.sample ? 'focused-row' : ''}"><td><button class="sample-btn" data-focus="${esc(s.sample_id)}" style="color:${color(s.sample_id)}">${esc(s.sample_stem || sampleName(s.sample_id))}</button></td><td>${esc(s.status_label || '未提供')}</td><td class="num" title="${esc(s.quantitative_value ?? '')}">${s.quantitative_value === null ? '—' : number(s.quantitative_value,3)}</td><td>${esc(s.decision_label || '未提供文字說明；可展開原始判定。')}</td><td>${s.evidence_status === 'available' ? '已保留' : '未提供'}</td></tr>`).join('')}</tbody></table></div></section>
      <details><summary>候選與查詢命中明細（${candidates.length} 個候選）</summary><pre>${json(witnesses(item).filter(w => !state.candidate || candidateId(w) === state.candidate))}</pre></details>
      <details><summary>曲線峰界、面積、來源與 evidence refs</summary><pre>${json((item.curves || []).filter(c => !state.candidate || (c.candidate_id === state.candidate || c.candidate_ids?.includes(state.candidate))).map(({raw_rts,raw_intensities,processed_rts,processed_intensities,processed_curves,aligned_rts,...facts}) => facts))}</pre></details>`;
  }
  function plot(item, axis) {
    const visible = (item.curves || []).filter(c => state.visible.has(c.sample_id) && (!state.candidate || (c.candidate_id === state.candidate || c.candidate_ids?.includes(state.candidate))));
    const curves = visible.filter(c => (axis === 'aligned' ? c.aligned_rts : c.raw_rts)?.length);
    if (!curves.length) return `<div class="empty-state"><strong>${axis === 'aligned' ? '未提供對齊曲線' : '目前沒有可顯示的曲線'}</strong><p>${axis === 'aligned' ? '這份結果未保存所選曲線的對齊座標。' : '請確認樣本勾選；未保存的曲線無法在此重畫。'}</p></div>`;
    let lo = Infinity, hi = -Infinity, lowY = 0, highY = 0;
    const series = c => {
      const raw = {kind:'raw', rts:axis === 'aligned' ? c.aligned_rts : c.raw_rts, intensities:c.raw_intensities || []};
      const processed = c.processed_curves || (c.processed_intensities ? [{kind:'processed',rts:c.processed_rts,intensities:c.processed_intensities}] : []);
      return [raw, ...(state.processed ? processed.map(p => ({...p,rts:axis === 'aligned' ? p.aligned_rts : p.rts})).filter(p => p.rts?.length) : [])];
    };
    curves.forEach(c => series(c).forEach(s => s.rts.forEach((x,i) => {lo = Math.min(lo,x); hi = Math.max(hi,x); lowY = Math.min(lowY,s.intensities[i]); highY = Math.max(highY,s.intensities[i]);})));
    if (!Number.isFinite(lo) || !Number.isFinite(lowY) || !Number.isFinite(highY)) return '<p>曲線 unavailable</p>';
    if (lo === hi) {lo -= .5; hi += .5;}
    if (lowY === highY) highY = lowY + 1;
    const W=540,H=250,L=52,T=15,IW=468,IH=195;
    const x = n => L+(n-lo)/(hi-lo)*IW, y = n => T+IH-(n-lowY)/(highY-lowY)*IH;
    let svg = `<path d="M${L},${T}V${T+IH}H${L+IW}" fill="none" stroke="#777"/><text x="3" y="15" font-size="10">${highY.toPrecision(4)}</text><text x="3" y="210" font-size="10">${lowY}</text>`;
    for(let i=0;i<=4;i++) {const n=lo+(hi-lo)*i/4;svg+=`<text x="${x(n)}" y="228" text-anchor="middle" font-size="10">${n.toFixed(3)}</text>`;}
    curves.forEach(c => {
      const opacity = state.sample && state.sample !== c.sample_id ? .15 : .9;
      series(c).forEach((s,idx) => {
        const points = s.rts.map((n,i) => `${x(n)},${y(s.intensities[i])}`).join(' ');
        svg += `<polyline data-kind="${esc(s.kind)}" fill="none" stroke="${color(c.sample_id)}" stroke-width="${idx ? 1.2 : 1.8}" stroke-dasharray="${idx ? `${2+idx*2} 3` : ''}" opacity="${opacity}" points="${points}"><title>${esc(c.sample_id)} · ${esc(s.kind)} · ${esc(c.evidence_ref || '')}</title></polyline>`;
      });
      if (state.bounds && (!state.sample || state.sample === c.sample_id)) {
        const marks = axis === 'aligned' ? [c.aligned_bound_left_rt,c.aligned_bound_right_rt,c.aligned_apex_rt] : [c.bound_left_rt,c.bound_right_rt,c.apex_rt];
        marks.forEach((n,idx) => {if(n != null) svg += `<line data-marker="${idx === 2 ? 'apex' : 'bound'}" x1="${x(n)}" x2="${x(n)}" y1="${T}" y2="${T+IH}" stroke="${color(c.sample_id)}" stroke-dasharray="${idx === 2 ? '1 2' : '4 3'}" opacity="${opacity}"><title>${esc(c.sample_id)} ${idx === 2 ? 'apex' : 'bound'} ${n}</title></line>`;});
      }
    });
    return `<svg role="img" aria-label="${axis} owner evidence curves" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${svg}<text x="285" y="247" text-anchor="middle" font-size="11">RT (min)</text></svg>`;
  }
  $('record-list').addEventListener('click', e => {const b=e.target.closest('[data-item]');if(b){state.id=b.dataset.item;state.candidate='';state.sample=null;list();$('record-list').querySelector('[aria-current="true"]')?.focus();}});
  $('detail-panel').addEventListener('change', e => {
    const el=e.target;
    if(el.id==='candidate-select') state.candidate=el.value;
    if(el.id==='chk-bounds') state.bounds=el.checked;
    if(el.id==='chk-processed') state.processed=el.checked;
    if(el.dataset.sample) {if(el.checked)state.visible.add(el.dataset.sample);else state.visible.delete(el.dataset.sample);}
    detail();
  });
  $('detail-panel').addEventListener('click', e => {
    const focus=e.target.closest('[data-focus]');if(focus){state.sample=state.sample===focus.dataset.focus?null:focus.dataset.focus;detail();}
    if(e.target.id==='btn-unfocus'){state.sample=null;detail();}
    const item=items.find(i=>i.id===state.id);if(!item)return;
    const zoom=e.target.closest('[data-zoom]');
    if(zoom){dialogOpener=zoom;$('zoom-title').textContent=`${itemName(item)} · m/z ${number(item.precursor_mz)} · ${zoom.dataset.zoom === 'raw' ? '原始時間軸' : '對齊後時間軸'}`;$('zoom-container').innerHTML=plot(item,zoom.dataset.zoom);$('zoom-dialog').showModal();}
    if(e.target.id==='btn-show-details'){dialogOpener=e.target;$('details-body').innerHTML=`<dl class="factlist"><dt>檢閱項目</dt><dd>${itemName(item)} · m/z ${number(item.precursor_mz)}</dd><dt>當次判定</dt><dd>${esc(item.reason_label || item.reason || '未提供')}</dd><dt>結果去向</dt><dd>${esc(item.route_label || item.route?.destination || '未提供')}</dd><dt>完整識別碼</dt><dd class="mono">${esc(item.id)}</dd></dl><details><summary>各樣本原始判定與證據</summary><pre>${json(item.samples)}</pre></details><details><summary>完整來源紀錄（JSON）</summary><pre>${json({summary:data.summary,selection:data.selection,matched_refs:item.matched_refs,details:item.details,item_ref:item.id})}</pre></details>`;$('details-dialog').showModal();}
  });
  for(const id of ['search','mz-min','mz-max','rt-min','rt-max']) $(id).addEventListener('input',list);
  $('clear').addEventListener('click',()=>{for(const id of ['search','mz-min','mz-max','rt-min','rt-max'])$(id).value='';list();});
  for(const prefix of ['zoom','details']){$(`${prefix}-close`).addEventListener('click',()=>$(`${prefix}-dialog`).close());$(`${prefix}-dialog`).addEventListener('close',()=>dialogOpener?.focus());}
  document.addEventListener('keydown',e=>{
    const dialog=document.querySelector('dialog[open]');if(dialog){if(e.key==='Escape'){e.preventDefault();dialog.close();}return;}
    if(e.target.closest('input,textarea,select') || !['ArrowDown','ArrowUp'].includes(e.key) || !filtered.length)return;
    e.preventDefault();const idx=filtered.findIndex(i=>i.id===state.id);state.id=filtered[Math.max(0,Math.min(filtered.length-1,idx+(e.key==='ArrowDown'?1:-1)))].id;state.sample=null;state.candidate='';list();$('record-list').querySelector('[aria-current="true"]')?.focus();
  });
  const meta=document.createElement('details');meta.innerHTML=`<summary>產生資訊與完整查詢條件${data.selection?.matched_count != null ? ' · 命中 '+esc(data.selection.matched_count)+' 筆' : ''}</summary><pre>${json({tool:data.tool,summary:data.summary,selection:data.selection})}</pre>`;document.body.append(meta);
  list();
})();
"""
