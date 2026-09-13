"""Method-neutral offline workbench interactions over supplied presentation data."""

# ruff: noqa: E501
WORKBENCH_JS = r"""(() => {
  const data = JSON.parse(document.getElementById('inspector-data').textContent);
  const items = data.items || [], samples = data.samples || [];
  const colors = ['#1d6fa3','#16855b','#6f46c7','#b9781c','#ab4d63','#637d8d','#2a9d8f','#e76f51'];
  const color = id => colors[Math.max(0, samples.findIndex(s => s.id === id)) % colors.length];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fixed = (x, digits) => typeof x === 'number' && Number.isFinite(x) ? x.toFixed(digits) : '未提供';
  const mz = x => fixed(x,4), rt = x => fixed(x,2);
  const quantity = x => x === null ? '—' : typeof x === 'number' && Number.isFinite(x) ? x.toLocaleString('en-US',{maximumFractionDigits:3}) : '未提供';
  const sampleName = id => {const index=samples.findIndex(s => s.id===id);return samples[index]?.label || (index>=0 ? `樣本 ${index+1}` : '未記錄樣本名稱');};
  const itemName = item => `項目 ${String(items.indexOf(item) + 1).padStart(2,'0')}`;
  const itemTitle = item => item.title || '已記錄的結果';
  const badge = item => item.badge_label || ({attention:'需留意',excluded:'已排除',rest:'已記錄'}[item.stratum] || '既有結果');
  const $ = id => document.getElementById(id);
  const state = {id: items[0]?.id, sample: null, candidate: '', visible: new Set(samples.map(s => s.id)), bounds:true, processed:false};
  let filtered = items, dialogOpener = null;
  function witnesses(item) { return item.witnesses || item.candidates || []; }
  function filteredItems() {
    const q = $('search').value.trim().toLowerCase();
    const limits = ['mz-min','mz-max','rt-min','rt-max'].map(id => $(id).value === '' ? null : Number($(id).value));
    return items.filter(item => {
      const searchable = [itemName(item), itemTitle(item), item.reason_label, item.method_label, mz(item.precursor_mz), rt(item.rt_apex_min), ...(item.samples || []).flatMap(s => [sampleName(s.sample_id),s.status_label,s.decision_label])].join(' ').toLowerCase();
      if (q && !searchable.includes(q)) return false;
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
    $('record-list').innerHTML = filtered.map(item => `<button class="record-button ${item.id === state.id ? 'selected' : ''}" data-item="${esc(item.id)}" aria-current="${item.id === state.id}"><div class="record-name"><span>${esc(itemTitle(item))}</span><span class="badge badge-${esc(item.stratum)}">${esc(badge(item))}</span></div><div class="record-meta">${itemName(item)} · ${mz(item.precursor_mz)} · ${rt(item.rt_apex_min)} min</div><div class="record-reason">${esc(item.reason_label || '未記錄判定說明')}</div></button>`).join('') || '<p class="empty-state">這份報告沒有符合的項目。請清除篩選條件。</p>';
    detail();
  }
  function candidateId(w) { return w.candidate_id || w.id || ''; }
  function candidateIds(item) {return [...new Set(witnesses(item).map(candidateId).filter(Boolean))];}
  function candidateName(item,id) {return `候選 ${candidateIds(item).indexOf(id)+1}`;}
  function selectedCurves(item) {return (item.curves || []).filter(c => (!state.candidate || c.candidate_id===state.candidate || c.candidate_ids?.includes(state.candidate)));}
  function table(head,rows) {return rows.length ? `<div class="table-scroll"><table class="sample-table"><thead><tr>${head.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(cell=>`<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '<p class="reading-hint">未提供相關紀錄。</p>';}
  function details(item) {
    const candidates=witnesses(item).filter(w=>(!state.candidate || candidateId(w)===state.candidate) && (!state.sample || (w.canonical_sample_identity_id || w.sample_id)===state.sample));
    const curves=selectedCurves(item).filter(c=>!state.sample || state.sample===c.sample_id);
    const shownSamples=(item.samples||[]).filter(s=>!state.sample || s.sample_id===state.sample);
    const isQuery=data.selection?.strategy==='query';
    return `<section><h3>當次判定</h3><p>${esc(item.reason_label || '未記錄判定說明')}</p></section>
      <section><h3>${state.sample ? esc(sampleName(state.sample))+' 的結果' : '各樣本結果'}</h3>${table(['樣本','當次狀態','定量結果','判定理由'],shownSamples.map(s=>[sampleName(s.sample_id),s.status_label || '未記錄',quantity(s.quantitative_value),s.decision_label || '未記錄理由']))}</section>
      <section><h3>候選峰</h3><p class="reading-hint">每一列是當次評估的一個候選峰，可對照樣本、m/z 與峰頂時間。</p>${table(['候選','樣本','m/z','RT（min）',...(isQuery ? ['查詢比對'] : [])],candidates.map(w=>[candidateName(item,candidateId(w)),w.sample_stem || sampleName(w.canonical_sample_identity_id || w.sample_id),mz(w.precursor_mz),fixed(w.rt_apex_min,3),...(isQuery ? [w.matched ? '符合查詢條件' : '同項目的參考候選'] : [])]))}</section>
      <section><h3>峰的位置與面積</h3><p class="reading-hint">這裡是各次擷取曲線的面積；最終數值請以上方定量結果為準。</p>${table(['樣本','曲線來源','左峰界（min）','峰頂（min）','右峰界（min）','峰面積'],curves.map(c=>[sampleName(c.sample_id),c.stage_label || '保留曲線',fixed(c.bound_left_rt,3),fixed(c.apex_rt,3),fixed(c.bound_right_rt,3),quantity(c.area)]))}</section>
      <section><h3>資料可用性</h3>${table(['樣本','原始曲線','對齊曲線'],shownSamples.map(s=>[sampleName(s.sample_id),selectedCurves(item).some(c=>c.sample_id===s.sample_id && c.raw_rts?.length) ? '已提供' : '未提供',selectedCurves(item).some(c=>c.sample_id===s.sample_id && c.aligned_rts?.length) ? '已提供' : '未提供']))}</section>`;
  }
  function detail() {
    const item = items.find(i => i.id === state.id);
    if (!item) {$('workspace-content').innerHTML = '<p class="empty-state">沒有選取項目</p>'; return;}
    const candidates = candidateIds(item);
    if (state.candidate && !candidates.includes(state.candidate)) state.candidate = '';
    $('workspace-content').innerHTML = `
      <div class="detail-top"><div><p class="detail-eyebrow">${itemName(item)} · ${(item.samples || []).length} 個樣本</p><h2>${esc(itemTitle(item))}</h2><div class="detail-meta"><span>m/z <strong>${mz(item.precursor_mz)}</strong></span><span>RT <strong>${rt(item.rt_apex_min)} min</strong></span>${item.method_label ? `<span>${esc(item.method_label)}</span>` : ''}</div></div><div class="detail-actions"><span class="badge badge-${esc(item.stratum)}">${esc(badge(item))}</span><button id="btn-show-details">判定與來源</button></div></div>
      <div class="finding-card"><p>${esc(item.reason_label || '未記錄判定說明')}</p></div>
      <div class="chart-toolbar"><label><input id="chk-bounds" type="checkbox" ${state.bounds ? 'checked' : ''}> 顯示峰界與峰頂</label><label><input id="chk-processed" type="checkbox" ${state.processed ? 'checked' : ''}> 疊加處理曲線</label><span class="focus-status">${state.sample ? '聚焦 '+esc(sampleName(state.sample)) : '比較所有可見樣本'}</span><button id="btn-unfocus" ${state.sample ? '' : 'disabled'}>解除聚焦</button></div>
      <div class="sample-legend">${samples.map(s => `<label><input type="checkbox" data-sample="${esc(s.id)}" ${state.visible.has(s.id) ? 'checked' : ''}><span class="legend-line" style="background:${color(s.id)}"></span> ${esc(sampleName(s.id))}</label>`).join('')}</div>
      <div class="candidate-bar" ${candidates.length>1 ? '' : 'hidden'}><label>查看候選 <select id="candidate-select"><option value="">全部候選</option>${candidates.map((id,index) => {const w=witnesses(item).find(w => candidateId(w) === id);return `<option value="${esc(id)}" ${id === state.candidate ? 'selected' : ''}>候選 ${index+1} · ${esc(w.sample_stem || sampleName(w.canonical_sample_identity_id))} · RT ${fixed(w.rt_apex_min,3)}</option>`;}).join('')}</select></label></div>
      <div class="charts-grid">${['raw','aligned'].map(axis => `<section class="plot-card"><div class="plot-header"><h4>${axis === 'raw' ? '原始時間軸' : '對齊後時間軸'}</h4><button data-zoom="${axis}">放大 ↗</button></div><div class="svg-container" id="svg-${axis}">${plot(item, axis)}</div><p class="chart-caption">${axis === 'raw' ? '實線：原始訊號；處理曲線以虛線顯示。' : '僅呈現當次保存的對齊座標。'}</p></section>`).join('')}</div>
      <section class="sample-section"><h3>各樣本結果</h3><div class="table-scroll"><table class="sample-table"><thead><tr><th>樣本</th><th>當次狀態</th><th class="num">定量結果</th><th>判定理由</th></tr></thead><tbody>${(item.samples || []).map(s => `<tr class="${s.sample_id === state.sample ? 'focused-row' : ''}"><td><button class="sample-btn" data-focus="${esc(s.sample_id)}" style="color:${color(s.sample_id)}">${esc(sampleName(s.sample_id))}</button></td><td><span class="badge badge-${esc(s.status_kind)}">${esc(s.status_label || '未記錄')}</span></td><td class="num" title="${esc(s.quantitative_value ?? '')}">${quantity(s.quantitative_value)}</td><td>${esc(s.decision_label || '未記錄理由')}</td></tr>`).join('')}</tbody></table></div><p class="reading-hint">點樣本名稱可聚焦曲線。— 表示空值；0 表示當次結果為零。</p></section>`;
  }
  function plot(item, axis) {
    const visible = selectedCurves(item).filter(c => state.visible.has(c.sample_id));
    const curves = visible.filter(c => (axis === 'aligned' ? c.aligned_rts : c.raw_rts)?.length);
    if (!curves.length) return `<div class="empty-state"><strong>${axis === 'aligned' ? '未提供對齊曲線' : '目前沒有可顯示的曲線'}</strong><p>${axis === 'aligned' ? '這份結果未保存所選曲線的對齊座標。' : '請確認樣本勾選；未保存的曲線無法在此重畫。'}</p></div>`;
    let lo = Infinity, hi = -Infinity, lowY = 0, highY = 0;
    const series = c => {
      const raw = {kind:'raw', rts:axis === 'aligned' ? c.aligned_rts : c.raw_rts, intensities:c.raw_intensities || []};
      const processed = c.processed_curves || (c.processed_intensities ? [{kind:'processed',rts:c.processed_rts,intensities:c.processed_intensities}] : []);
      return [raw, ...(state.processed ? processed.map(p => ({...p,rts:axis === 'aligned' ? p.aligned_rts : p.rts})).filter(p => p.rts?.length) : [])];
    };
    curves.forEach(c => series(c).forEach(s => s.rts.forEach((x,i) => {lo = Math.min(lo,x); hi = Math.max(hi,x); lowY = Math.min(lowY,s.intensities[i]); highY = Math.max(highY,s.intensities[i]);})));
    if (!Number.isFinite(lo) || !Number.isFinite(lowY) || !Number.isFinite(highY)) return '<p class="empty-state">未提供可顯示的曲線。</p>';
    if (lo === hi) {lo -= .5; hi += .5;}
    if (lowY === highY) highY = lowY + 1;
    const W=540,H=250,L=52,T=15,IW=468,IH=195;
    const x = n => L+(n-lo)/(hi-lo)*IW, y = n => T+IH-(n-lowY)/(highY-lowY)*IH;
    let svg = '<text x="8" y="11" font-size="10" fill="#5a6673">訊號強度</text>';
    for(let i=0;i<=4;i++) {const n=lowY+(highY-lowY)*i/4;svg+=`<line x1="${L}" y1="${y(n)}" x2="${L+IW}" y2="${y(n)}" stroke="#e7ecf0"/><text x="${L-7}" y="${y(n)+4}" text-anchor="end" font-size="9" fill="#5a6673">${n===0 ? '0' : Math.abs(n)>=10000 ? n.toExponential(1) : quantity(n)}</text>`;}
    svg+=`<path d="M${L},${T}V${T+IH}H${L+IW}" fill="none" stroke="#94a3b0"/>`;
    for(let i=0;i<=4;i++) {const n=lo+(hi-lo)*i/4;svg+=`<text x="${x(n)}" y="228" text-anchor="middle" font-size="10" fill="#5a6673">${rt(n)}</text>`;}
    curves.forEach(c => {
      const opacity = state.sample && state.sample !== c.sample_id ? .15 : .9;
      series(c).forEach((s,idx) => {
        const points = s.rts.map((n,i) => `${x(n)},${y(s.intensities[i])}`).join(' ');
        const curveLabel={raw:'原始訊號',baseline:'基線',smoothed:'平滑訊號',positive:'基線以上訊號',positive_signal:'基線以上訊號',processed:'處理曲線'}[s.kind] || '處理曲線';
        svg += `<polyline data-kind="${esc(s.kind)}" fill="none" stroke="${color(c.sample_id)}" stroke-width="${idx ? 1.2 : 1.8}" stroke-dasharray="${idx ? `${2+idx*2} 3` : ''}" opacity="${opacity}" points="${points}"><title>${esc(sampleName(c.sample_id))} · ${curveLabel} · ${esc(c.stage_label || '保留曲線')}</title></polyline>`;
      });
      if (state.bounds && (!state.sample || state.sample === c.sample_id)) {
        const marks = axis === 'aligned' ? [c.aligned_bound_left_rt,c.aligned_bound_right_rt,c.aligned_apex_rt] : [c.bound_left_rt,c.bound_right_rt,c.apex_rt];
        marks.forEach((n,idx) => {if(n != null) svg += `<line data-marker="${idx === 2 ? 'apex' : 'bound'}" x1="${x(n)}" x2="${x(n)}" y1="${T}" y2="${T+IH}" stroke="${color(c.sample_id)}" stroke-dasharray="${idx === 2 ? '1 2' : '4 3'}" opacity="${opacity}"><title>${esc(sampleName(c.sample_id))} · ${['左峰界','右峰界','峰頂'][idx]} ${fixed(n,3)} min</title></line>`;});
      }
    });
    return `<svg role="img" aria-label="${axis==='raw' ? '原始時間軸' : '對齊時間軸'}層析圖" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${svg}<text x="285" y="247" text-anchor="middle" font-size="11">RT（min）</text></svg>`;
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
    if(zoom){dialogOpener=zoom;$('zoom-title').textContent=`${itemTitle(item)} · m/z ${mz(item.precursor_mz)} · ${zoom.dataset.zoom === 'raw' ? '原始時間軸' : '對齊後時間軸'}`;$('zoom-container').innerHTML=plot(item,zoom.dataset.zoom);$('zoom-dialog').showModal();}
    if(e.target.id==='btn-show-details'){dialogOpener=e.target;$('details-title').textContent=itemTitle(item)+' · 判定與來源';$('details-body').innerHTML=details(item);$('details-dialog').showModal();}
  });
  for(const id of ['search','mz-min','mz-max','rt-min','rt-max']) $(id).addEventListener('input',list);
  $('clear').addEventListener('click',()=>{for(const id of ['search','mz-min','mz-max','rt-min','rt-max'])$(id).value='';list();});
  for(const prefix of ['zoom','details']){$(`${prefix}-close`).addEventListener('click',()=>$(`${prefix}-dialog`).close());$(`${prefix}-dialog`).addEventListener('close',()=>dialogOpener?.focus());}
  document.addEventListener('keydown',e=>{
    const dialog=document.querySelector('dialog[open]');if(dialog){if(e.key==='Escape'){e.preventDefault();dialog.close();}return;}
    if(e.target.closest('input,textarea,select') || !['ArrowDown','ArrowUp'].includes(e.key) || !filtered.length)return;
    e.preventDefault();const idx=filtered.findIndex(i=>i.id===state.id);state.id=filtered[Math.max(0,Math.min(filtered.length-1,idx+(e.key==='ArrowDown'?1:-1)))].id;state.sample=null;state.candidate='';list();$('record-list').querySelector('[aria-current="true"]')?.focus();
  });
  $('btn-report-info').addEventListener('click',e=>{
    dialogOpener=e.target;$('details-title').textContent='關於這份報告';
    const request=data.selection || {},range=(a,b,format)=>a==null && b==null ? '不限' : `${a==null ? '不限' : format(a)} 至 ${b==null ? '不限' : format(b)}`;
    $('details-body').innerHTML=$('report-info').innerHTML+`<section><h3>選取範圍</h3>${table(['條件','範圍'],[['m/z',range(request.mz_min,request.mz_max,mz)],['RT（min）',range(request.rt_min,request.rt_max,rt)],['樣本',request.sample_ids?.length ? request.sample_ids.map(sampleName).join('、') : '全部樣本'],...(request.candidate_ids?.length ? [['指定候選',`${request.candidate_ids.length} 個候選`]] : []),...(request.group_ids?.length ? [['指定分組',`${request.group_ids.length} 個分組`]] : [])])}</section>`;$('details-dialog').showModal();
  });
  if(data.notice){$('report-notice').textContent=data.notice;$('report-notice').hidden=false;}
  list();
})();
"""
