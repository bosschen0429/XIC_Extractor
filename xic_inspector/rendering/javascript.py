"""Method-neutral offline workbench interactions over supplied presentation data."""

# ruff: noqa: E501
WORKBENCH_JS = r"""(() => {
  const data=JSON.parse(document.getElementById('inspector-data').textContent);
  const items=Array.isArray(data.items)?data.items:[], samples=Array.isArray(data.samples)?data.samples:[];
  const colors=['#176b87','#25855f','#7856a8','#b2692d','#a34e67','#537788','#298c84','#c85b46'];
  const $=id=>document.getElementById(id), has=(o,k)=>Object.prototype.hasOwnProperty.call(o||{},k);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite=v=>typeof v==='number'&&Number.isFinite(v), fixed=(v,d)=>finite(v)?v.toFixed(d):'未提供';
  const mz=v=>fixed(v,4), rt=v=>fixed(v,2), drt=v=>fixed(v,3);
  const num=v=>finite(v)?v.toLocaleString('en-US',{maximumFractionDigits:3}):'未提供';
  const sample=id=>samples.find(s=>s.id===id), sampleName=id=>sample(id)?.label||id||'未記錄樣本名稱';
  const color=id=>colors[Math.max(0,samples.findIndex(s=>s.id===id))%colors.length];
  const itemRows=item=>Array.isArray(item.samples)?item.samples:[];
  const witnesses=item=>Array.isArray(item.witnesses)?item.witnesses:(Array.isArray(item.candidates)?item.candidates:[]);
  const candidateId=w=>w?.candidate_id||w?.id||'', witnessSample=w=>w?.canonical_sample_identity_id||w?.sample_id||'';
  const records=item=>Array.isArray(item?.details?.readable_records)?item.details.readable_records:[];
  const itemName=item=>`項目 ${String(items.indexOf(item)+1).padStart(2,'0')}`;
  const itemTitle=item=>item.title||'已記錄的結果';
  const badge=item=>item.badge_label||({attention:'需留意',excluded:'已排除',rest:'已記錄'}[item.stratum]||'既有結果');
  const result=row=>!has(row,'quantitative_value')?'<span class="result-missing">無單一結果</span>':row.quantitative_value===null?'<span class="result-null">空值</span>':`<span class="result-number">${num(row.quantitative_value)}</span>`;
  const state={id:items[0]?.id,sample:null,candidate:'',capture:'',signal:'raw',visible:new Set(samples.map(s=>s.id)),bounds:true,evidence:'',evidenceContext:''};
  let filtered=items, dialogOpener=null, evidenceOpener=null;

  function candidates(item,sid=state.sample){
    const rows=witnesses(item).filter(w=>!sid||witnessSample(w)===sid), ids=[];
    rows.forEach(w=>{const id=candidateId(w);if(id&&!ids.includes(id))ids.push(id);});
    return ids.map((id,index)=>({id,index,witness:rows.find(w=>candidateId(w)===id)}));
  }
  function candidateLabel(candidate){
    const w=candidate.witness||{};
    return `${w.label||w.title||`候選 ${candidate.index+1}`} · m/z ${mz(w.precursor_mz)} · RT ${drt(w.rt_apex_min)} min`;
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
  function signals(item){
    const curves=state.sample?selectedCurves(item):(item.curves||[]).filter(c=>state.visible.has(c.sample_id)), out=new Set();
    if(curves.some(c=>c.raw_rts?.length&&c.raw_intensities?.length))out.add('raw');
    curves.forEach(c=>(c.processed_curves||[]).forEach(s=>{const k=s.kind==='positive_signal'?'positive':s.kind;if(['baseline','smoothed','positive'].includes(k)&&(s.rts||c.raw_rts)?.length&&s.intensities?.length)out.add(k);}));
    return ['raw','baseline','smoothed','positive'].filter(k=>out.has(k));
  }
  function alignedAvailable(item){return curvesForPlot(item).some(c=>seriesList(c,'aligned').length);}
  function normalize(item,changed=false){
    if(state.sample&&!itemRows(item).some(r=>r.sample_id===state.sample))state.sample=null;
    const cs=candidates(item);
    if(!state.sample)state.candidate='';
    else if(changed||!cs.some(c=>c.id===state.candidate)){state.candidate=cs[0]?.id||'';state.capture='';}
    const curves=selectedCurves(item), all=item.curves||[];
    if(!state.sample)state.capture='';
    else if(!curves.some(c=>curveKey(c,all.indexOf(c))===state.capture))state.capture=curves[0]?curveKey(curves[0],all.indexOf(curves[0])):'';
    const ss=signals(item);if(!ss.includes(state.signal))state.signal=ss[0]||'raw';
  }
  function seriesList(curve,axis){
    const rts=axis==='aligned'?curve.aligned_rts:curve.raw_rts;
    const raw=rts?.length&&curve.raw_intensities?.length?{kind:'raw',rts,intensities:curve.raw_intensities}:null;
    if(state.signal==='raw')return raw?[raw]:[];
    const found=(curve.processed_curves||[]).find(s=>s.kind===state.signal||(state.signal==='positive'&&s.kind==='positive_signal'));
    if(!found)return [];
    const processedRts=axis==='aligned'?found.aligned_rts:(found.rts||curve.raw_rts);
    const processed=processedRts?.length&&found.intensities?.length?{...found,rts:processedRts}:null;
    return processed?[...(raw?[raw]:[]),processed]:[];
  }
  function curvesForPlot(item){
    return (item.curves||[]).filter(c=>state.visible.has(c.sample_id)&&(!state.sample||c.sample_id!==state.sample||((!state.candidate||c.candidate_id===state.candidate||c.candidate_ids?.includes(state.candidate))&&(!state.capture||curveKey(c,(item.curves||[]).indexOf(c))===state.capture))));
  }

  function filteredItems(){
    const q=$('search').value.trim().toLowerCase(), limits=['mz-min','mz-max','rt-min','rt-max'].map(id=>$(id).value===''?null:Number($(id).value));
    return items.filter(item=>{
      const text=[itemName(item),itemTitle(item),item.reason_label,item.method_label,mz(item.precursor_mz),rt(item.rt_apex_min),...itemRows(item).flatMap(r=>[sampleName(r.sample_id),r.status_label,r.decision_label])].join(' ').toLowerCase();
      if(q&&!text.includes(q))return false;if(limits.every(v=>v===null))return true;
      const rows=witnesses(item).length?witnesses(item):[item];
      return rows.some(r=>(limits[0]===null||finite(r.precursor_mz)&&r.precursor_mz>=limits[0])&&(limits[1]===null||finite(r.precursor_mz)&&r.precursor_mz<=limits[1])&&(limits[2]===null||finite(r.rt_apex_min)&&r.rt_apex_min>=limits[2])&&(limits[3]===null||finite(r.rt_apex_min)&&r.rt_apex_min<=limits[3]));
    });
  }
  function filterStatus(){
    const active=[];if($('search').value.trim())active.push(`搜尋「${$('search').value.trim()}」`);
    for(const [lo,hi,label] of [['mz-min','mz-max','m/z'],['rt-min','rt-max','RT']])if($(lo).value||$(hi).value)active.push(`${label} ${$(lo).value||'不限'}–${$(hi).value||'不限'}`);
    $('active-filters').innerHTML=active.length?`<span class="active-label">本頁已套用</span>${active.map(x=>`<span class="filter-chip">${esc(x)}</span>`).join('')}<button type="button" id="clear-inline">清除</button>`:'<span>本頁未套用額外篩選</span>';
    $('loaded-range').textContent=`正在搜尋本報告已載入的 ${items.length} 個項目；目前顯示 ${filtered.length} 個。`;
  }
  function list(){
    filtered=filteredItems();
    if(!filtered.some(i=>i.id===state.id)){state.id=filtered[0]?.id;reset();}
    $('list-count').textContent=`${filtered.length} / ${items.length} 筆`;
    $('record-list').innerHTML=filtered.map(item=>`<button type="button" class="record-button ${item.id===state.id?'selected':''}" data-item="${esc(item.id)}" aria-current="${item.id===state.id}"><div class="record-name"><span>${esc(itemTitle(item))}</span><span class="badge badge-${esc(item.stratum)}">${esc(badge(item))}</span></div><div class="record-meta">${itemName(item)} · m/z ${mz(item.precursor_mz)} · RT ${rt(item.rt_apex_min)} min</div><div class="record-reason">${esc(item.reason_label||'未記錄判定說明')}</div></button>`).join('')||'<p class="empty-state">這份報告沒有符合的項目。請清除篩選條件。</p>';
    filterStatus();detail();
  }
  function summary(item){
    let values=0,nulls=0,noSingle=0,noCurves=0;
    itemRows(item).forEach(r=>{if(!has(r,'quantitative_value'))noSingle++;else if(r.quantitative_value===null)nulls++;else values++;if(!(item.curves||[]).some(c=>c.sample_id===r.sample_id&&c.raw_rts?.length))noCurves++;});
    const parts=[`${itemRows(item).length} 個樣本`,`${values} 個有定量值`];if(nulls)parts.push(`${nulls} 個空值`);if(noSingle)parts.push(`${noSingle} 個無單一結果`);if(noCurves)parts.push(`${noCurves} 個未提供曲線`);
    const multi=itemRows(item).filter(r=>candidates(item,r.sample_id).length>1);if(multi.length)parts.push(`${multi.map(r=>sampleName(r.sample_id)).join('、')} 有多個候選`);return parts.join('；')+'。';
  }
  function evidenceButtons(refs,context,label='查看定量依據'){
    const values=[...new Set((Array.isArray(refs)?refs:[]).filter(Boolean))];
    return values.length?`<div class="evidence-actions">${values.map((ref,i)=>`<button type="button" class="evidence-link" data-evidence-ref="${esc(ref)}" data-evidence-context="${esc(context)}">${label}${values.length>1?` ${i+1}`:''}</button>`).join('')}</div>`:'';
  }
  function sampleTable(item){
    return `<div class="table-scroll compact-samples"><table class="sample-table"><thead><tr><th>顯示</th><th>樣本與狀態</th><th class="num">正式結果</th></tr></thead><tbody>${itemRows(item).map(r=>{const curve=(item.curves||[]).some(c=>c.sample_id===r.sample_id&&c.raw_rts?.length),current=r.sample_id===state.sample;return `<tr class="${current?'focused-row':''}" data-sample-row="${esc(r.sample_id)}"><td><label><input type="checkbox" data-sample-visible="${esc(r.sample_id)}" ${state.visible.has(r.sample_id)?'checked':''}><span class="sr-only">在圖中顯示 ${esc(sampleName(r.sample_id))}</span></label></td><td><span class="sample-identity"><span class="sample-dot" style="background:${color(r.sample_id)}"></span>${esc(sampleName(r.sample_id))}</span><span class="badge badge-${esc(r.status_kind)}">${esc(r.status_label||'未記錄')}</span><button type="button" class="sample-focus" data-focus="${esc(r.sample_id)}" ${current?'disabled aria-current="true"':''}>${current?'目前查看':'查看樣本'}</button></td><td class="num"><strong>${result(r)}</strong><span class="curve-status">曲線：${curve?'已提供':'未提供'}</span>${evidenceButtons(r.decision_refs,`${sampleName(r.sample_id)} 的正式定量值`)}</td></tr>`;}).join('')}</tbody></table></div><p class="reading-hint">勾選只控制圖中顯示；「查看樣本」會帶出該樣本的候選與積分。空值、0、無單一結果及缺曲線分開顯示。</p>`;
  }
  function candidateControl(item){
    if(!state.sample)return '<p class="control-value">先從樣本表查看一個樣本，才會列出該樣本的候選。</p>';
    const cs=candidates(item);if(!cs.length)return '<p class="control-value">此樣本沒有候選峰紀錄。</p>';
    if(cs.length===1){const c=cs[0];return `<p class="control-value" data-current-candidate="${esc(c.id)}">${esc(candidateLabel(c))}<span class="viewing-tag">目前查看</span></p>`;}
    return `<label for="candidate-select">查看候選</label><select id="candidate-select">${cs.map(c=>`<option value="${esc(c.id)}" ${c.id===state.candidate?'selected':''}>${esc(candidateLabel(c))}</option>`).join('')}</select><span class="viewing-tag">目前查看</span><p class="control-note">切換只改變目前查看的曲線；正式定量值保持不變。</p>`;
  }
  function captureControl(item){
    if(!state.sample)return '<p class="control-value">查看樣本後顯示擷取階段。</p>';const cs=selectedCurves(item),all=item.curves||[];
    if(!cs.length)return '<p class="control-value">此候選未提供保存曲線。</p>';
    if(cs.length===1)return `<p class="control-value" data-current-capture="${esc(curveKey(cs[0],all.indexOf(cs[0])))}">${esc(cs[0].stage_label||cs[0].kind||'保留曲線')}</p>`;
    return `<label for="capture-select">擷取階段</label><select id="capture-select">${cs.map(c=>{const key=curveKey(c,all.indexOf(c));return `<option value="${esc(key)}" ${key===state.capture?'selected':''}>${esc(c.stage_label||c.kind||'保留曲線')}</option>`;}).join('')}</select>`;
  }
  function signalControl(item){const ss=signals(item),labels={raw:'原始訊號',baseline:'基線',smoothed:'平滑訊號',positive:'基線以上訊號'};if(!ss.length)return '<p class="control-value">未提供訊號資料。</p>';return ss.length===1?`<p class="control-value">${labels[ss[0]]}</p>`:`<label for="signal-select">訊號種類</label><select id="signal-select">${ss.map(s=>`<option value="${s}" ${s===state.signal?'selected':''}>${labels[s]}</option>`).join('')}</select>`;}
  function legend(item){return itemRows(item).map(r=>`<label class="legend-item"><input type="checkbox" data-sample-visible="${esc(r.sample_id)}" ${state.visible.has(r.sample_id)?'checked':''}><span class="legend-line" style="background:${color(r.sample_id)}"></span>${esc(sampleName(r.sample_id))}</label>`).join('');}
  function readout(item){
    if(!state.sample)return '<div class="integration-empty"><strong>跨樣本比較</strong><p>從樣本表查看一個樣本後，這裡會顯示該候選與擷取階段的積分界線、峰頂、面積及正式定量值。</p></div>';
    const row=itemRows(item).find(r=>r.sample_id===state.sample)||{}, curve=activeCurve(item), c=candidates(item).find(x=>x.id===state.candidate);
    if(!curve)return `<div class="integration-empty"><strong>${esc(sampleName(state.sample))}</strong><p>此候選未提供保存曲線；正式定量結果仍為 ${result(row)}。</p>${evidenceButtons(row.decision_refs,'正式定量值')}</div>`;
    const rawApex=curve.integration?.raw_apex_rt_min, showRaw=finite(rawApex)&&(!finite(curve.apex_rt)||Math.abs(rawApex-curve.apex_rt)>1e-7);
    return `<div class="readout-heading"><div><span class="eyebrow">目前檢閱 · 下列 RT 均為原始時間</span><strong>${esc(sampleName(state.sample))}${c?` · ${esc((c.witness?.label||c.witness?.title||`候選 ${c.index+1}`))}`:''} · ${esc(curve.stage_label||curve.kind||'保留曲線')}</strong></div>${curve.evidence_ref?`<button type="button" class="evidence-link" data-evidence-ref="${esc(curve.evidence_ref)}" data-evidence-context="目前曲線與積分">查看曲線來源</button>`:''}</div><dl class="integration-grid"><div><dt>積分範圍（原始時間）</dt><dd data-readout="bounds">${drt(curve.bound_left_rt)}–${drt(curve.bound_right_rt)} min</dd></div><div><dt>積分峰頂（原始時間）</dt><dd data-readout="apex">${drt(curve.apex_rt)} min</dd></div>${showRaw?`<div><dt>原始訊號峰頂（原始時間）</dt><dd data-readout="raw-apex">${drt(rawApex)} min</dd></div>`:''}<div><dt>當次原始計數面積</dt><dd data-readout="area">${num(curve.area)} <span>counts·s</span></dd></div><div><dt>正式定量值</dt><dd data-readout="quantity">${result(row)}</dd></div></dl><p class="control-note">面積是這次積分的原始計數面積；正式定量值是來源保存的決策結果，兩者分開顯示。</p>${evidenceButtons(row.decision_refs,'正式定量值')}`;
  }
  function plot(item,axis){
    const drawable=curvesForPlot(item).flatMap(curve=>seriesList(curve,axis).map(series=>({curve,series})));
    if(!drawable.length)return `<div class="empty-state"><strong>${axis==='aligned'?'所選資料未提供對齊座標':'目前沒有可顯示的曲線'}</strong><p>${axis==='aligned'?'請切回原始時間；不會以原始座標代替。':'請確認樣本顯示勾選，或來源是否保存曲線。'}</p></div>`;
    let lo=Infinity,hi=-Infinity,lowY=0,highY=0;
    drawable.forEach(({series:s})=>s.rts.forEach((v,i)=>{const y=s.intensities[i];if(finite(v)&&finite(y)){lo=Math.min(lo,v);hi=Math.max(hi,v);lowY=Math.min(lowY,y);highY=Math.max(highY,y);}}));
    if(!finite(lo)||!finite(hi))return '<div class="empty-state">未提供成對的時間與訊號數值。</div>';if(lo===hi){lo-=.5;hi+=.5;}if(lowY===highY)highY=lowY+1;
    const W=760,H=330,L=62,T=24,IW=672,IH=248,x=v=>L+(v-lo)/(hi-lo)*IW,y=v=>T+IH-(v-lowY)/(highY-lowY)*IH;
    let svg='<text x="8" y="14" font-size="11" fill="#5c6971">訊號強度</text>';
    for(let i=0;i<=4;i++){const v=lowY+(highY-lowY)*i/4;svg+=`<line x1="${L}" y1="${y(v)}" x2="${L+IW}" y2="${y(v)}" stroke="#e4e9ec"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end" font-size="10" fill="#5c6971">${v===0?'0':Math.abs(v)>=10000?v.toExponential(1):num(v)}</text>`;}
    svg+=`<path d="M${L},${T}V${T+IH}H${L+IW}" fill="none" stroke="#91a0a8"/>`;
    for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4;svg+=`<text x="${x(v)}" y="296" text-anchor="middle" font-size="10" fill="#5c6971">${rt(v)}</text>`;}
    const active=activeCurve(item);
    if(active&&state.bounds){const left=axis==='aligned'?active.aligned_bound_left_rt:active.bound_left_rt,right=axis==='aligned'?active.aligned_bound_right_rt:active.bound_right_rt;if(finite(left)&&finite(right)){const a=Math.max(lo,Math.min(left,right)),b=Math.min(hi,Math.max(left,right));if(b>=a)svg+=`<rect data-integration-range="true" x="${x(a)}" y="${T}" width="${Math.max(0,x(b)-x(a))}" height="${IH}" fill="${color(active.sample_id)}" opacity=".09"><title>積分範圍 ${drt(left)}–${drt(right)} min</title></rect>`;}}
    drawable.forEach(({curve,series:s})=>{const focused=!state.sample||curve.sample_id===state.sample,opacity=state.sample&&!focused?.16:.92,points=s.rts.map((v,i)=>finite(v)&&finite(s.intensities[i])?`${x(v)},${y(s.intensities[i])}`:'').filter(Boolean).join(' '),signalName={raw:'原始訊號',baseline:'基線',smoothed:'平滑訊號',positive:'基線以上訊號',positive_signal:'基線以上訊號'}[s.kind]||'處理訊號';svg+=`<polyline data-kind="${esc(s.kind)}" data-curve-ref="${esc(curve.evidence_ref||'')}" fill="none" stroke="${color(curve.sample_id)}" stroke-width="${s.kind==='raw'?1.7:2.2}" stroke-dasharray="${s.kind==='raw'?'':'6 4'}" opacity="${opacity}" points="${points}"><title>${esc(sampleName(curve.sample_id))} · ${signalName} · ${esc(curve.stage_label||curve.kind||'保留曲線')}</title></polyline>`;if(curve!==active||!state.bounds||s.kind!==state.signal&&!(state.signal==='positive'&&s.kind==='positive_signal'))return;
      const marks=axis==='aligned'?[['bound','左峰界',curve.aligned_bound_left_rt],['bound','右峰界',curve.aligned_bound_right_rt],['apex','積分峰頂',curve.aligned_apex_rt]]:[['bound','左峰界',curve.bound_left_rt],['bound','右峰界',curve.bound_right_rt],['apex','積分峰頂',curve.apex_rt],...(finite(curve.integration?.raw_apex_rt_min)&&(!finite(curve.apex_rt)||Math.abs(curve.integration.raw_apex_rt_min-curve.apex_rt)>1e-7)?[['raw-apex','原始訊號峰頂',curve.integration.raw_apex_rt_min]]:[])];
      marks.forEach(([kind,label,v])=>{if(finite(v))svg+=`<line data-marker="${kind}" x1="${x(v)}" x2="${x(v)}" y1="${T}" y2="${T+IH}" stroke="${color(curve.sample_id)}" stroke-dasharray="${kind==='bound'?'5 4':kind==='raw-apex'?'2 4':'1 3'}"><title>${label} ${drt(v)} min</title></line>`;});
    });
    return `<svg role="img" aria-label="${axis==='raw'?'原始時間':'對齊時間'}層析圖" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${svg}<text x="398" y="324" text-anchor="middle" font-size="11">${axis==='raw'?'原始 RT':'對齊 RT'}（min）</text></svg>`;
  }
  function plotCard(item,axis,signalLabel){
    const isAligned=axis==='aligned';
    if(isAligned&&!alignedAvailable(item))return '<section class="plot-card plot-unavailable" id="plot-aligned"><div class="plot-header"><div><h4>對齊時間 · '+signalLabel+'</h4><p>來源未提供目前所選曲線與訊號的對齊座標。</p></div></div><div class="aligned-unavailable"><strong>對齊時間圖不可用</strong><p>原始時間圖仍可檢閱；此處不以原始座標或標線代替對齊資料。</p></div></section>';
    const overlay=state.signal==='raw'?'實線為原始訊號。':'實線為原始訊號，虛線為目前所選處理訊號。';
    return `<section class="plot-card" id="plot-${axis}"><div class="plot-header"><h4>${isAligned?'對齊時間':'原始時間'} · ${signalLabel}</h4><button type="button" data-zoom="${axis}">放大圖表 ↗</button></div><div class="svg-container" id="svg-${axis}">${plot(item,axis)}</div><p class="chart-caption">${overlay}${isAligned?'只使用來源提供的對齊座標與標線。':'峰界與積分讀值使用來源保存的原始時間值。'}</p></section>`;
  }
  function evidencePanel(item){
    if(!state.evidence)return '';const record=records(item).find(r=>r.ref===state.evidence);
    if(!record)return `<aside class="evidence-panel" id="evidence-panel" tabindex="-1" aria-live="polite"><div class="evidence-header"><div><span class="eyebrow">${esc(state.evidenceContext||'目前資料')}</span><h3>來源內容未提供</h3></div><button type="button" class="evidence-close" id="close-evidence">關閉來源</button></div><p>${records(item).length?'這筆引用沒有對應到本報告內嵌的可讀紀錄。':'本報告未提供可讀來源紀錄，無法在離線頁面中補出說明。'}</p><details><summary>完整紀錄與引用</summary><div class="technical-ref"><code>${esc(state.evidence)}</code><button type="button" data-copy="${esc(state.evidence)}">複製</button></div><p class="control-note">本報告僅保留引用，未包含內容。</p></details></aside>`;
    const fields=Array.isArray(record.fields)?record.fields:[],links=Array.isArray(record.links)?record.links:[],tech=Array.isArray(record.technical_refs)?record.technical_refs:[];
    return `<aside class="evidence-panel" id="evidence-panel" tabindex="-1" aria-live="polite"><div class="evidence-header"><div><span class="eyebrow">${esc(state.evidenceContext||'目前資料')}</span><h3>${esc(record.title||'可讀來源紀錄')}</h3></div><button type="button" class="evidence-close" id="close-evidence">關閉來源</button></div>${record.summary?`<p class="evidence-summary">${esc(record.summary)}</p>`:''}${links.length?`<section class="record-links"><h4>相關紀錄</h4>${links.map(link=>records(item).some(r=>r.ref===link.ref)?`<button type="button" data-record-ref="${esc(link.ref)}">${esc(link.label||'查看相關紀錄')}</button>`:`<div class="external-reference"><strong>${esc(link.label||'外部引用')}</strong><span>本報告僅保留引用，未包含內容。</span></div>`).join('')}</section>`:''}<details><summary>完整紀錄與引用</summary>${fields.length?`<dl class="evidence-fields">${fields.map(f=>`<div><dt>${esc(f.label||'欄位')}</dt><dd>${esc(f.value??'未提供')}</dd></div>`).join('')}</dl>`:''}${tech.length?tech.map(t=>`<div class="technical-ref"><span><strong>${esc(t.label||'技術引用')}</strong><code>${esc(t.value??'')}</code></span><button type="button" data-copy="${esc(t.value??'')}">複製</button></div>`).join(''):`<div class="technical-ref"><span><strong>紀錄識別碼</strong><code>${esc(record.ref)}</code></span><button type="button" data-copy="${esc(record.ref)}">複製</button></div>`}</details></aside>`;
  }
  function detail(){
    const item=items.find(i=>i.id===state.id);if(!item){$('workspace-content').innerHTML='<p class="empty-state">沒有選取項目</p>';return;}normalize(item);
    const graph=state.sample?`${sampleName(state.sample)} · 目前查看`:'所有可見樣本',selectedSignalLabel={raw:'原始訊號',baseline:'基線',smoothed:'平滑訊號',positive:'基線以上訊號'}[state.signal]||'訊號',signalLabel=state.signal==='raw'?selectedSignalLabel:`原始＋${selectedSignalLabel}`;
    $('workspace-content').innerHTML=`<div class="detail-top"><div><p class="detail-eyebrow">${itemName(item)} · ${itemRows(item).length} 個樣本</p><h2>${esc(itemTitle(item))}</h2><div class="detail-meta"><span>m/z <strong>${mz(item.precursor_mz)}</strong></span><span>主要 RT <strong>${rt(item.rt_apex_min)} min</strong></span>${item.method_label?`<span>${esc(item.method_label)}</span>`:''}</div><p class="detail-summary">${esc(summary(item))} ${esc(item.reason_label||'未記錄項目判定說明。')}</p></div><span class="badge badge-${esc(item.stratum)}">${esc(badge(item))}</span></div><section class="inspection-section" id="inspection-workspace"><div class="inspection-heading"><div class="inspection-title"><h3 id="inspection-title" tabindex="-1">${esc(graph)}</h3>${state.sample?'<button type="button" class="return-all" id="btn-unfocus">← 返回所有樣本</button>':''}</div><span class="focus-status">${state.sample?'候選與擷取':'曲線與積分 · 比較所有可見樣本'}</span></div><div class="selection-controls ${state.sample?'':'overview-controls'}">${state.sample?`<div class="control-group"><span class="control-label">候選峰</span>${candidateControl(item)}</div><div class="control-group"><span class="control-label">擷取階段</span>${captureControl(item)}</div>`:''}<div class="control-group"><span class="control-label">訊號種類</span>${signalControl(item)}</div></div><div class="chart-toolbar"><div class="sample-legend" aria-label="樣本曲線顯示">${legend(item)}</div><label><input id="chk-bounds" type="checkbox" ${state.bounds?'checked':''}> 顯示所選積分範圍與峰頂</label></div><div class="analysis-layout ${state.evidence?'with-evidence':''}"><div class="chart-column"><div class="charts-grid">${plotCard(item,'raw',signalLabel)}${plotCard(item,'aligned',signalLabel)}</div><section class="integration-card" id="integration-readout">${readout(item)}</section></div>${evidencePanel(item)}</div></section><section class="sample-section"><div class="section-heading"><h3>樣本結果</h3></div>${sampleTable(item)}</section>`;
  }
  function reset(){state.sample=null;state.candidate='';state.capture='';state.signal='raw';state.evidence='';state.evidenceContext='';evidenceOpener=null;}
  function clearFilters(){for(const id of ['search','mz-min','mz-max','rt-min','rt-max'])$(id).value='';list();}
  $('record-list').addEventListener('click',event=>{const b=event.target.closest('[data-item]');if(!b)return;state.id=b.dataset.item;reset();list();$('record-list').querySelector('[aria-current="true"]')?.focus();});
  $('detail-panel').addEventListener('change',event=>{const el=event.target,item=items.find(i=>i.id===state.id);if(!item)return;if(el.id==='candidate-select'){state.candidate=el.value;state.capture='';state.evidence='';normalize(item);}if(el.id==='capture-select'){state.capture=el.value;state.evidence='';}if(el.id==='signal-select')state.signal=el.value;if(el.id==='chk-bounds')state.bounds=el.checked;if(el.dataset.sampleVisible){if(el.checked)state.visible.add(el.dataset.sampleVisible);else state.visible.delete(el.dataset.sampleVisible);}detail();});
  $('detail-panel').addEventListener('click',async event=>{const item=items.find(i=>i.id===state.id);if(!item)return;const focus=event.target.closest('[data-focus]');if(focus){state.sample=focus.dataset.focus;state.candidate='';state.capture='';state.evidence='';state.evidenceContext='';evidenceOpener=null;normalize(item,true);detail();requestAnimationFrame(()=>{const heading=$('inspection-title');heading?.focus({preventScroll:true});heading?.scrollIntoView({behavior:'smooth',block:'start'});});return;}if(event.target.closest('#btn-unfocus')){state.sample=null;state.candidate='';state.capture='';state.evidence='';state.evidenceContext='';evidenceOpener=null;normalize(item);detail();requestAnimationFrame(()=>{const heading=$('inspection-title');heading?.focus({preventScroll:true});heading?.scrollIntoView({behavior:'smooth',block:'start'});});return;}const evidence=event.target.closest('[data-evidence-ref]');if(evidence){evidenceOpener={ref:evidence.dataset.evidenceRef,context:evidence.dataset.evidenceContext||''};state.evidence=evidenceOpener.ref;state.evidenceContext=evidenceOpener.context;detail();requestAnimationFrame(()=>{const panel=$('evidence-panel');panel?.scrollIntoView({behavior:'smooth',block:'start'});panel?.focus({preventScroll:true});});return;}const link=event.target.closest('[data-record-ref]');if(link){state.evidence=link.dataset.recordRef;detail();requestAnimationFrame(()=>{const panel=$('evidence-panel');panel?.scrollIntoView({behavior:'smooth',block:'start'});panel?.focus({preventScroll:true});});return;}if(event.target.closest('#close-evidence')){state.evidence='';state.evidenceContext='';detail();requestAnimationFrame(()=>{const opener=[...document.querySelectorAll('[data-evidence-ref]')].find(el=>el.dataset.evidenceRef===evidenceOpener?.ref&&(el.dataset.evidenceContext||'')===evidenceOpener?.context);opener?.scrollIntoView({block:'nearest'});opener?.focus({preventScroll:true});evidenceOpener=null;});return;}const copy=event.target.closest('[data-copy]');if(copy){try{await navigator.clipboard.writeText(copy.dataset.copy||'');const old=copy.textContent;copy.textContent='已複製';setTimeout(()=>copy.textContent=old,1200);}catch(_){copy.textContent='無法複製';}return;}const zoom=event.target.closest('[data-zoom]');if(zoom){dialogOpener=zoom;$('zoom-title').textContent=`${itemTitle(item)} · ${state.sample?sampleName(state.sample):'所有可見樣本'} · ${zoom.dataset.zoom==='raw'?'原始時間':'對齊時間'}`;$('zoom-container').innerHTML=plot(item,zoom.dataset.zoom);$('zoom-dialog').showModal();}});
  for(const id of ['search','mz-min','mz-max','rt-min','rt-max'])$(id).addEventListener('input',list);
  $('clear').addEventListener('click',clearFilters);$('active-filters').addEventListener('click',event=>{if(event.target.closest('#clear-inline'))clearFilters();});
  $('zoom-close').addEventListener('click',()=>$('zoom-dialog').close());$('zoom-dialog').addEventListener('close',()=>dialogOpener?.focus());
  document.addEventListener('keydown',event=>{const dialog=document.querySelector('dialog[open]');if(dialog){if(event.key==='Escape'){event.preventDefault();dialog.close();}return;}if(event.key==='Escape'&&state.evidence){event.preventDefault();state.evidence='';state.evidenceContext='';detail();requestAnimationFrame(()=>{const opener=[...document.querySelectorAll('[data-evidence-ref]')].find(el=>el.dataset.evidenceRef===evidenceOpener?.ref&&(el.dataset.evidenceContext||'')===evidenceOpener?.context);opener?.scrollIntoView({block:'nearest'});opener?.focus({preventScroll:true});evidenceOpener=null;});return;}if(event.target.closest('input,textarea,select')||!['ArrowDown','ArrowUp'].includes(event.key)||!filtered.length)return;event.preventDefault();const i=filtered.findIndex(x=>x.id===state.id);state.id=filtered[Math.max(0,Math.min(filtered.length-1,i+(event.key==='ArrowDown'?1:-1)))].id;reset();list();$('record-list').querySelector('[aria-current="true"]')?.focus();});
  $('btn-report-info').addEventListener('click',event=>{dialogOpener=event.target;const r=data.selection||{},source=$('report-info-source').dataset,range=(a,b,f)=>a==null&&b==null?'不限':`${a==null?'不限':f(a)} 至 ${b==null?'不限':f(b)}`;$('report-info-body').innerHTML=`<dl class="factlist"><dt>檢閱方式</dt><dd>${esc(source.strategyLabel)}</dd><dt>包含項目</dt><dd>${items.length} 筆（執行共 ${esc(source.runTotal)} 筆，${esc(source.eligibleCount)} 筆可供本次選取）</dd><dt>來源資料庫</dt><dd>${esc(source.sourceName)}</dd><dt>可用資料</dt><dd>${esc(source.capabilityLabel)}</dd>${data.summary?.matrix_path?`<dt>Matrix 路徑</dt><dd>${esc(data.summary.matrix_path)}</dd>`:''}<dt>m/z 範圍</dt><dd>${range(r.mz_min,r.mz_max,mz)}</dd><dt>RT（min）</dt><dd>${range(r.rt_min,r.rt_max,rt)}</dd><dt>樣本</dt><dd>${r.sample_ids?.length?r.sample_ids.map(sampleName).map(esc).join('、'):'全部樣本'}</dd></dl>`;$('report-info-dialog').showModal();});
  $('report-info-close').addEventListener('click',()=>$('report-info-dialog').close());$('report-info-dialog').addEventListener('close',()=>dialogOpener?.focus());
  if(data.notice){$('report-notice').textContent=data.notice;$('report-notice').hidden=false;}list();
})();
"""
