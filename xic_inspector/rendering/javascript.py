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
  const result=row=>!has(row,'quantitative_value')?'<span class="result-missing">無單一結果</span>':row.quantitative_value===null?'<span class="result-null">空值</span>':row.quantitative_value===0?'<span class="result-zero">0</span>':`<span class="result-number">${num(row.quantitative_value)}</span>`;
  const isCollapsedExcluded=item=>item.kind==='candidate'&&item.stratum==='excluded', queryReport=data.selection?.strategy==='query'||$('report-info-source')?.dataset.strategy==='query';
  const primaryItems=items.filter(item=>['group','row'].includes(item.kind)&&!isCollapsedExcluded(item));
  const initialItem=queryReport?items[0]:primaryItems.reduce((best,item)=>(item.review_summary?.observed_sample_count??-1)>(best?.review_summary?.observed_sample_count??-1)?item:best,null)||items.find(item=>!isCollapsedExcluded(item));
  const state={id:initialItem?.id,sample:null,candidate:'',capture:'',signal:'smoothed',visible:new Set(samples.map(s=>s.id)),bounds:true,evidence:'',evidenceContext:'',excludedOpen:queryReport};
  let filtered=items, listed=[], dialogOpener=null, evidenceOpener=null;

  function candidates(item,sid=state.sample){
    const rows=witnesses(item).filter(w=>!sid||witnessSample(w)===sid), ids=[];
    rows.forEach(w=>{const id=candidateId(w);if(id&&!ids.includes(id))ids.push(id);});
    return ids.map((id,index)=>({id,index,witness:rows.find(w=>candidateId(w)===id)}));
  }
  function sampleNotices(item,row){
    const notices=[], count=candidates(item,row.sample_id).length, curve=(item.curves||[]).some(c=>c.sample_id===row.sample_id&&c.raw_rts?.length);
    if(count>1)notices.push({kind:'candidates',label:`${count} 個候選`,short:`${count} 候選`});
    if(!has(row,'quantitative_value'))notices.push({kind:'missing',label:'無單一結果',short:'無單一結果'});
    else if(row.quantitative_value===null)notices.push({kind:'null',label:'空值',short:'空值'});
    else if(row.quantitative_value===0)notices.push({kind:'zero',label:'數值 0',short:'0'});
    if(!curve)notices.push({kind:'curve',label:'曲線未提供',short:'缺曲線'});
    return notices;
  }
  function originLabel(origin){
    return origin?.label||({discovery_support:'初始探索支持',reacquired_support:'重新擷取支持',backfill_support:'Backfill（回填）支持',unavailable:'來源未提供'}[origin?.kind]||'來源未提供');
  }
  function originClass(origin){return ['discovery_support','reacquired_support','backfill_support'].includes(origin?.kind)?origin.kind:'unavailable';}
  function originRefs(origin){return [...new Set(['method_refs','capture_refs','observation_refs'].flatMap(key=>Array.isArray(origin?.[key])?origin[key]:[]).filter(Boolean))];}
  function sampleAlignment(item,row){
    const curves=(item.curves||[]).filter(c=>c.sample_id===row.sample_id&&c.raw_rts?.length), aligned=curves.filter(c=>c.aligned_rts?.length).length;
    if(!curves.length)return {kind:'unavailable',label:'校正：無曲線'};
    if(!aligned)return {kind:'unavailable',label:'校正：對齊未提供'};
    if(aligned<curves.length)return {kind:'partial',label:`校正：${aligned}/${curves.length} 段有對齊座標`};
    return {kind:'complete',label:'校正：對齊可用'};
  }
  const injectionOrders=data.summary?.known_istd_reference?.calibration?.injection_order||{};
  const rel={node:null,pair:null,reviews:false,axis:'raw',signal:'smoothed',captures:{}};
  const shortName=name=>String(name||'未提供樣本').replace('Breast_Cancer_Tissue_pooled_','').replace('Benignfat','Fat ').replace('_DNA','');
  function relationshipModel(item){
    const pairs=Array.isArray(item.internal_pair_support)?item.internal_pair_support:[], rows=itemRows(item), byId=new Map();
    const add=(id,sid,label)=>{if(!id||byId.has(id))return;const row=rows.find(r=>r.sample_id===sid),order=injectionOrders[label];
      const refs=row?.support_origin?.capture_refs||[],all=(item.curves||[]).filter(c=>c.sample_id===sid);
      // Group curves require the source's exact support binding, including missing bindings.
      const curves=refs.length?all.filter(c=>refs.includes(c.evidence_ref)):['group','row'].includes(item.kind)?[]:all;
      byId.set(id,{id,sid,label:label||sampleName(sid),row,order:finite(order)?order:null,curves});};
    (item.relationship_members||[]).forEach(m=>add(m.member_subject_id,m.sample_id,m.sample_label));
    pairs.forEach(p=>['left','right'].forEach(side=>{const label=p[`${side}_sample_label`],matches=rows.filter(r=>r.sample_stem===label||sampleName(r.sample_id)===label);add(p[`${side}_member_subject_id`],matches.length===1?matches[0].sample_id:null,label);}));
    if(!byId.size)rows.forEach(r=>add(r.sample_id,r.sample_id,r.sample_stem||sampleName(r.sample_id)));
    const nodes=[...byId.values()].sort((a,b)=>(a.order===null)-(b.order===null)||(a.order??0)-(b.order??0));
    return {nodes,pairs:pairs.filter(p=>byId.has(p.left_member_subject_id)&&byId.has(p.right_member_subject_id)),byId};
  }
  const pairIds=p=>[p.left_member_subject_id,p.right_member_subject_id];
  function relationshipSelection(model){
    const pair=model.pairs.find(p=>p.edge_ref===rel.pair);
    return pair?pairIds(pair):rel.node&&model.byId.has(rel.node)?[rel.node]:model.nodes.map(n=>n.id);
  }
  function relationshipCurve(node){return node.curves.find(c=>c.evidence_ref===rel.captures[node.id])||node.curves[0]||null;}
  function relationshipNetwork(model){
    const ids=relationshipSelection(model),positions=new Map(),width=720,height=Math.max(340,model.nodes.length*32);
    model.nodes.forEach((n,i)=>{const t=-Math.PI/2+i*Math.PI*2/Math.max(1,model.nodes.length);positions.set(n.id,[width/2+230*Math.cos(t),height/2+(height/2-64)*Math.sin(t)]);});
    let markup='';
    model.pairs.forEach(p=>{if(p.support_kind!=='direct'&&!rel.reviews)return;const [a,b]=pairIds(p),[x1,y1]=positions.get(a),[x2,y2]=positions.get(b),selected=rel.pair===p.edge_ref,dim=(rel.pair&&!selected)||(rel.node&&!pairIds(p).includes(rel.node)),label=`${shortName(model.byId.get(a).label)} 與 ${shortName(model.byId.get(b).label)}，${p.support_kind==='direct'?'直接支持':'僅供檢閱'}`;
      markup+=`<g role="button" tabindex="0" data-relation-pair="${esc(p.edge_ref)}" aria-label="${esc(label)}" aria-pressed="${!!selected}"><line class="relation-edge ${p.support_kind==='direct'?'':'review'} ${selected?'selected':''} ${dim?'dim':''}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/><line class="relation-hit" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/><title>${esc(label)} · 點選比較</title></g>`;
    });
    model.nodes.forEach(n=>{const [x,y]=positions.get(n.id),active=(rel.node||rel.pair)&&ids.includes(n.id),dim=(rel.node||rel.pair)&&!ids.includes(n.id);markup+=`<g class="relation-node ${active?'selected':''} ${dim?'dim':''}" role="button" tabindex="0" data-relation-node="${esc(n.id)}" aria-label="查看 ${esc(shortName(n.label))}" aria-pressed="${!!active}"><circle cx="${x}" cy="${y}" r="22"/><text x="${x}" y="${y+4}" text-anchor="middle" class="relation-number">${n.order===null?'?':String(n.order).padStart(2,'0')}</text><text x="${x}" y="${y+(y>height/2?42:-34)}" text-anchor="middle" class="relation-label">${esc(shortName(n.label))}</text><title>${esc(n.label)}</title></g>`;});
    return `<svg id="relationship-network" viewBox="0 0 ${width} ${height}" aria-label="樣本配對支持關係圖">${markup}</svg>`;
  }
  function relationshipDetail(item,model){
    const pair=model.pairs.find(p=>p.edge_ref===rel.pair),node=model.byId.get(rel.node);
    if(pair){const a=model.byId.get(pair.left_member_subject_id),b=model.byId.get(pair.right_member_subject_id),basis=pair.rt_basis==='drift_corrected'?'校正 RT':pair.rt_basis==='raw'?'原始 RT':'來源未提供';return `<span class="eyebrow">目前比較</span><h3>${esc(shortName(a.label))}<br>↔ ${esc(shortName(b.label))}</h3><span class="badge badge-${pair.support_kind==='direct'?'ok':'attention'}">${pair.support_kind==='direct'?'直接支持':'僅供檢閱'}</span><dl class="relation-metrics"><div><dt>原始 RT 差</dt><dd>${drt(pair.rt_raw_delta_sec)} s</dd></div><div><dt>校正 RT 差</dt><dd>${drt(pair.rt_drift_corrected_delta_sec)} s</dd></div></dl><p class="reading-hint">判定採用：${basis}。下方只比較這兩個樣本。</p><details><summary>配對判斷與來源</summary><p>分數 ${num(pair.score)} · ${esc(pair.decision||'未提供')}<br>${esc(pair.reason||'未提供')}</p>${evidenceButtons([pair.edge_ref],'目前配對的支持紀錄','查看配對紀錄')}</details>`;}
    if(node){const pairs=model.pairs.filter(p=>pairIds(p).includes(node.id));return `<span class="eyebrow">目前樣本</span><h3>${esc(shortName(node.label))}</h3><p class="reading-hint">${esc(node.row?.status_label||'來源未提供狀態')} · ${node.curves.length} 份來源綁定曲線</p><div class="relation-neighbors">${pairs.map(p=>{const other=model.byId.get(pairIds(p).find(id=>id!==node.id));return `<button type="button" data-relation-pair="${esc(p.edge_ref)}"><span>${esc(shortName(other.label))}</span><span>${p.support_kind==='direct'?'直接支持':'僅供檢閱'} →</span></button>`;}).join('')||'<p class="reading-hint">沒有保存的成員配對紀錄。</p>'}</div>${evidenceButtons(node.row?.decision_refs,`${node.label} 的正式結果`)}${evidenceButtons(originRefs(node.row?.support_origin),`${node.label} 的支持來源`,'查看支持記錄')}`;}
    const direct=model.pairs.filter(p=>p.support_kind==='direct').length;return `<span class="eyebrow">群組概覽</span><h3>${model.nodes.length} 個樣本的連結</h3><p>${direct} 對直接支持，${model.pairs.length-direct} 對僅供檢閱。</p><div class="relation-guide"><p><b>1　點樣本，看它連到誰</b><br>相關連線與單一樣本一起顯示。</p><p><b>2　點連線，比較兩個樣本</b><br>RT 差與所選曲線一起出現。</p></div><details><summary>連線如何解讀</summary><p>實線是保存的直接支持，可透過其他成員連接；這不表示族群內每一對都一致。圖的位置不表示 RT 距離，也不是最終分組決策的完整追蹤。</p></details>`;
  }
  function lanePath(xs,ys,x,y){let started=false,path='';xs.forEach((v,i)=>{if(!finite(v)||!finite(ys[i])){started=false;return;}path+=`${started?'L':'M'}${x(v).toFixed(2)},${y(ys[i]).toFixed(2)} `;started=true;});return path;}
  function relationshipLanes(item,model){
    const ids=relationshipSelection(model),nodes=model.nodes.filter(n=>ids.includes(n.id)&&state.visible.has(n.sid)),axis=rel.axis;
    let lo=Infinity,hi=-Infinity;
    nodes.forEach(n=>{const c=relationshipCurve(n);((axis==='aligned'?c?.aligned_rts:c?.raw_rts)||[]).forEach(v=>{if(finite(v)){lo=Math.min(lo,v);hi=Math.max(hi,v);}});});
    if(!finite(lo)){lo=0;hi=1;}if(lo===hi)hi=lo+1;
    const x=v=>8+(v-lo)/(hi-lo)*672;
    const lanes=nodes.map(n=>{const c=relationshipCurve(n),series=c?seriesList(c,axis,rel.signal)[0]:null,xs=series?.rts||[],ys=series?.intensities||[];let chart='';
      if(!c||!series||!xs.some(finite))chart=`<div class="lane-missing">${!c?'來源未提供此樣本的精確支持曲線；不以其他候選替代。':axis==='aligned'?'此擷取未提供所選訊號的校正時間；切回原始 RT 查看。':rel.signal==='smoothed'?'此擷取未保存平滑曲線；可切換原始訊號檢查。':'此擷取未提供原始訊號。'}</div>`;
      else{let max=0;ys.forEach(v=>{if(finite(v))max=Math.max(max,v);});const y=v=>76-(max?v/max:0)*68;let svg='';for(let i=0;i<=4;i++){const t=lo+(hi-lo)*i/4;svg+=`<line x1="${x(t)}" x2="${x(t)}" y1="4" y2="76" class="lane-grid"/><text x="${x(t)}" y="94" text-anchor="${i===0?'start':i===4?'end':'middle'}" class="lane-tick">${t.toFixed(2)}</text>`;}
        const left=axis==='aligned'?c.aligned_bound_left_rt:c.bound_left_rt,right=axis==='aligned'?c.aligned_bound_right_rt:c.bound_right_rt,apex=axis==='aligned'?c.aligned_apex_rt:c.apex_rt;
        if(finite(left)&&finite(right))svg+=`<rect x="${x(left)}" y="4" width="${Math.max(0,x(right)-x(left))}" height="72" class="lane-bound"/>`;
        svg+=`<path d="${lanePath(xs,ys,x,y)}" class="lane-raw" data-kind="${esc(rel.signal)}"/>`;
        if(finite(apex))svg+=`<line x1="${x(apex)}" x2="${x(apex)}" y1="4" y2="76" class="lane-apex"/>`;
        chart=`<svg viewBox="0 0 690 100" preserveAspectRatio="none" aria-label="${esc(n.label)} 的${axis==='aligned'?'校正':'原始'}時間曲線"><title>${esc(n.label)} · ${esc(c.stage_label)} · ${rel.signal==='smoothed'?'平滑':'原始'}最高強度 ${num(max)}</title>${svg}</svg>`;}
      return `<article class="relationship-lane" data-lane-member="${esc(n.id)}" data-injection-order="${n.order??''}"><div class="lane-label"><div class="lane-name"><span class="injection-order">${n.order===null?'?':String(n.order).padStart(2,'0')}</span>${esc(shortName(n.label))}</div><span class="reading-hint">${esc(c?.stage_label||'支持曲線未提供')}</span>${n.curves.length>1?`<select data-lane-capture="${esc(n.id)}" aria-label="${esc(n.label)} 曲線選擇">${n.curves.map(v=>`<option value="${esc(v.evidence_ref)}" ${v===c?'selected':''}>${esc(v.stage_label||v.kind)}</option>`).join('')}</select>`:''}<details><summary>定量與來源</summary><p>正式結果：${result(n.row||{})}</p>${c?`<p>記錄峰頂：${drt(c.apex_rt)} min<br>積分範圍：${drt(c.bound_left_rt)}–${drt(c.bound_right_rt)} min<br>當次原始計數面積：${num(c.area)} counts·s</p>${evidenceButtons([c.evidence_ref],`${n.label} 的支持曲線`,'查看曲線來源')}`:''}${evidenceButtons(n.row?.decision_refs,`${n.label} 的正式結果`)}${evidenceButtons(originRefs(n.row?.support_origin),`${n.label} 的支持來源`,'查看支持記錄')}</details></div><div class="lane-chart">${chart}</div></article>`;
    }).join('');
    return `<section class="relationship-curves" aria-label="依進樣順序的跨樣本曲線"><div class="relation-section-heading"><div><h3>${rel.pair?'這兩個樣本的曲線':rel.node?'這個樣本的曲線':'跨樣本曲線'}</h3><p class="reading-hint">${nodes.length} 個樣本 · 依進樣先後，由上而下 · 共用${axis==='aligned'?'校正':'原始'}時間軸 · 各自最高點縮放；? 表示順序未提供</p></div><div class="relation-options"><div class="axis-switch"><button type="button" data-time-axis="raw" aria-pressed="${axis==='raw'}">原始 RT</button><button type="button" data-time-axis="aligned" aria-pressed="${axis==='aligned'}">校正 RT</button></div><label for="relationship-signal">曲線</label><select id="relationship-signal"><option value="smoothed" ${rel.signal==='smoothed'?'selected':''}>平滑訊號</option><option value="raw" ${rel.signal==='raw'?'selected':''}>原始訊號</option></select></div></div><div id="relationship-lanes">${lanes||'<p class="lane-missing">目前沒有可見樣本；可在樣本結果中恢復顯示。</p>'}</div><p class="reading-hint">藍：${rel.signal==='smoothed'?'平滑訊號':'原始訊號'} · 紫色虛線：記錄峰頂。校正座標缺失時不以原始 RT 代替。</p></section>`;
  }
  function internalPairSupportPanel(item){
    const model=relationshipModel(item);
    return `<section class="relationship-workbench" aria-label="樣本配對支持"><div class="relation-section-heading"><h3>樣本關係</h3><div class="relation-options"><label><input type="checkbox" id="relationship-reviews" ${rel.reviews?'checked':''}>顯示僅供檢閱連線</label><button type="button" data-relation-reset>顯示全部樣本</button></div></div><div class="relationship-top"><div class="relationship-graph">${relationshipNetwork(model)}<p class="reading-hint">實線：直接支持　虛線：僅供檢閱 · 節點編號為原始進樣順序</p></div><aside class="relationship-detail" aria-live="polite">${relationshipDetail(item,model)}</aside></div>${relationshipLanes(item,model)}</section>`;
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
    return ['smoothed','raw','baseline','positive'].filter(k=>k==='smoothed'||out.has(k));
  }
  function alignmentCoverage(item,peer=false){
    const curves=curvesForPlot(item,peer), rawSamples=new Set(curves.filter(c=>seriesList(c,'raw',state.signal).length).map(c=>c.sample_id)), alignedSamples=new Set(curves.filter(c=>seriesList(c,'aligned',state.signal).length).map(c=>c.sample_id));
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
  function seriesList(curve,axis,signal=state.signal){
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
    const excluded=filtered.filter(isCollapsedExcluded).length;
    $('loaded-range').textContent=`本報告已載入 ${items.length} 個項目；目前符合篩選 ${filtered.length} 個${excluded?`，其中 ${excluded} 個為已排除候選`:''}。`;
  }
  function list(selectFallback=true){
    filtered=filteredItems();
    const regular=filtered.filter(item=>!isCollapsedExcluded(item)), excluded=filtered.filter(isCollapsedExcluded), localFilterActive=$('search').value.trim()!==''||['mz-min','mz-max','rt-min','rt-max'].some(id=>$(id).value!==''), showExcluded=state.excludedOpen||localFilterActive;
    listed=showExcluded?[...regular,...excluded]:regular;
    if(!listed.some(i=>i.id===state.id)&&(state.id||selectFallback)){state.id=listed[0]?.id;reset();}
    $('list-count').textContent=`${filtered.length} / ${items.length} 筆`;
    const recordButton=item=>`<button type="button" class="record-button ${item.id===state.id?'selected':''}" data-item="${esc(item.id)}" aria-current="${item.id===state.id}"><div class="record-name"><span>${esc(itemTitle(item))}</span><span class="badge badge-${esc(item.stratum)}">${esc(badge(item))}</span></div><div class="record-meta">${itemName(item)} · m/z ${mz(item.precursor_mz)} · RT ${rt(item.rt_apex_min)} min</div><div class="record-reason">${esc(item.reason_label||'未記錄判定說明')}</div></button>`;
    const excludedGroup=excluded.length?`<section class="excluded-group"><button type="button" class="excluded-toggle" data-excluded-toggle aria-expanded="${showExcluded}" ${localFilterActive?'disabled title="篩選結果會保持展開"':''}><span>已排除候選</span><span>${excluded.length} 筆 ${showExcluded?'▾':'▸'}</span></button>${showExcluded?`<div class="excluded-items">${excluded.map(recordButton).join('')}</div>`:''}</section>`:'';
    $('record-list').innerHTML=regular.map(recordButton).join('')+excludedGroup||'<p class="empty-state">這份報告沒有符合的項目。請清除篩選條件。</p>';
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
    const excluded=isCollapsedExcluded(item), evidenceLabel=excluded?'查看排除依據':'查看定量依據';
    return `<div class="table-scroll compact-samples"><table class="sample-table"><thead><tr><th>顯示</th><th>樣本、支持與狀態</th><th class="num">正式結果</th></tr></thead><tbody>${itemRows(item).map(r=>{const notices=sampleNotices(item,r),curve=!notices.some(n=>n.kind==='curve'),current=r.sample_id===state.sample,evidenceContext=excluded?`${sampleName(r.sample_id)} 的排除依據`:`${sampleName(r.sample_id)} 的正式定量值`,origin=r.support_origin||{kind:'unavailable'},alignment=sampleAlignment(item,r),supportRefs=originRefs(origin);return `<tr class="sample-row ${notices.length?'sample-notice':''} ${current?'focused-row':''}" data-sample-row="${esc(r.sample_id)}"><td><label><input type="checkbox" data-sample-visible="${esc(r.sample_id)}" ${state.visible.has(r.sample_id)?'checked':''}><span class="sr-only">在圖中顯示 ${esc(sampleName(r.sample_id))}</span></label></td><td><span class="sample-identity"><span class="sample-dot" style="background:${color(r.sample_id)}"></span>${esc(sampleName(r.sample_id))}</span><span class="badge badge-${esc(r.status_kind)}">${esc(r.status_label||'未記錄')}</span><span class="sample-provenance"><span class="support-origin origin-${originClass(origin)}">${esc(originLabel(origin))}</span><span class="alignment-status alignment-${alignment.kind}">${esc(alignment.label)}</span></span>${supportRefs.length?evidenceButtons(supportRefs,`${sampleName(r.sample_id)} 的${originLabel(origin)}`,'查看支持記錄'):''}${notices.length?`<span class="sample-flags" aria-label="需要留意：${esc(notices.map(n=>n.label).join('、'))}">${notices.map(n=>`<span class="sample-flag flag-${n.kind}">${esc(n.label)}</span>`).join('')}</span>`:''}<button type="button" class="sample-focus" data-focus="${esc(r.sample_id)}" ${current?'disabled aria-current="true"':''}>${current?'目前查看':'查看樣本'}</button></td><td class="num"><strong>${result(r)}</strong><span class="curve-status ${curve?'':'curve-unavailable'}">曲線：${curve?'已提供':'未提供'}</span>${evidenceButtons(r.decision_refs,evidenceContext,evidenceLabel)}</td></tr>`;}).join('')}</tbody></table></div><p class="reading-hint">支持來源說明樣本如何進入目前族群；校正狀態只反映保存的對齊座標。勾選控制圖中顯示，「查看樣本」帶出候選與積分。</p>`;
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
  function legend(item){return itemRows(item).map(r=>{const notices=sampleNotices(item,r);return `<label class="legend-item ${notices.length?'has-notice':''}"><input type="checkbox" data-sample-visible="${esc(r.sample_id)}" ${state.visible.has(r.sample_id)?'checked':''}><span class="legend-line" style="background:${color(r.sample_id)}"></span><span>${esc(sampleName(r.sample_id))}</span>${notices.map(n=>`<span class="legend-notice flag-${n.kind}" title="${esc(n.label)}">${esc(n.short)}</span>`).join('')}</label>`;}).join('');}
  function readout(item){
    if(!state.sample)return '<div class="integration-empty"><strong>跨樣本比較</strong><p>從樣本表查看一個樣本後，這裡會顯示該候選與擷取階段的積分界線、峰頂、面積及正式定量值。</p></div>';
    const row=itemRows(item).find(r=>r.sample_id===state.sample)||{}, curve=activeCurve(item), c=candidates(item).find(x=>x.id===state.candidate), excluded=isCollapsedExcluded(item), evidenceLabel=excluded?'查看排除依據':'查看定量依據', evidenceContext=excluded?`${sampleName(state.sample)} 的排除依據`:'正式定量值';
    if(!curve)return `<div class="integration-empty"><strong>${esc(sampleName(state.sample))}</strong><p>此候選未提供保存曲線；正式定量結果仍為 ${result(row)}。</p>${evidenceButtons(row.decision_refs,evidenceContext,evidenceLabel)}</div>`;
    const rawApex=curve.integration?.raw_apex_rt_min, showRaw=finite(rawApex)&&(!finite(curve.apex_rt)||Math.abs(rawApex-curve.apex_rt)>1e-7);
    return `<div class="readout-heading"><div><span class="eyebrow">目前檢閱 · 下列 RT 均為原始時間</span><strong>${esc(sampleName(state.sample))}${c?` · ${esc((c.witness?.label||c.witness?.title||`候選 ${c.index+1}`))}`:''} · ${esc(curve.stage_label||curve.kind||'保留曲線')}</strong></div>${curve.evidence_ref?`<button type="button" class="evidence-link" data-evidence-ref="${esc(curve.evidence_ref)}" data-evidence-context="目前曲線與積分">查看曲線來源</button>`:''}</div><dl class="integration-grid"><div><dt>積分範圍（原始時間）</dt><dd data-readout="bounds">${drt(curve.bound_left_rt)}–${drt(curve.bound_right_rt)} min</dd></div><div><dt>積分峰頂（原始時間）</dt><dd data-readout="apex">${drt(curve.apex_rt)} min</dd></div>${showRaw?`<div><dt>原始訊號峰頂（原始時間）</dt><dd data-readout="raw-apex">${drt(rawApex)} min</dd></div>`:''}<div><dt>當次原始計數面積</dt><dd data-readout="area">${num(curve.area)} <span>counts·s</span></dd></div><div><dt>正式定量值</dt><dd data-readout="quantity">${result(row)}</dd></div></dl><p class="control-note">面積是這次積分的原始計數面積；正式定量值是來源保存的決策結果，兩者分開顯示。</p>${evidenceButtons(row.decision_refs,evidenceContext,evidenceLabel)}`;
  }
  function plot(item,axis,peer=false){
    const drawable=curvesForPlot(item,peer).flatMap(curve=>seriesList(curve,axis,state.signal).map(series=>({curve,series})));
    if(!drawable.length)return `<div class="empty-state"><strong>${axis==='aligned'?'所選資料未提供對齊座標':'目前沒有可顯示的曲線'}</strong><p>${axis==='aligned'?'請切回原始時間；不會以原始座標代替。':state.signal==='smoothed'?'所選來源未保存可用的平滑曲線；可切換原始訊號檢查。':'請確認樣本顯示勾選，或來源是否保存曲線。'}</p></div>`;
    const reviewWindow=axis==='raw'&&Array.isArray(item.review_rt_window)?item.review_rt_window:null;
    let lo=reviewWindow?.[0]??Infinity,hi=reviewWindow?.[1]??-Infinity,lowY=0,highY=0;
    drawable.forEach(({series:s})=>s.rts.forEach((v,i)=>{const y=s.intensities[i];if(finite(v)&&finite(y)&&(!reviewWindow||v>=lo&&v<=hi)){if(!reviewWindow){lo=Math.min(lo,v);hi=Math.max(hi,v);}lowY=Math.min(lowY,y);highY=Math.max(highY,y);}}));
    if(!finite(lo)||!finite(hi))return '<div class="empty-state">未提供成對的時間與訊號數值。</div>';if(lo===hi){lo-=.5;hi+=.5;}if(lowY===highY)highY=lowY+1;
    const W=760,H=330,L=62,T=24,IW=672,IH=248,x=v=>L+(v-lo)/(hi-lo)*IW,y=v=>T+IH-(v-lowY)/(highY-lowY)*IH;
    let svg=`<defs><clipPath id="curve-window-${axis}"><rect x="${L}" y="${T}" width="${IW}" height="${IH}"/></clipPath></defs><text x="8" y="14" font-size="11" fill="#5c6971">訊號強度</text>`;
    for(let i=0;i<=4;i++){const v=lowY+(highY-lowY)*i/4;svg+=`<line x1="${L}" y1="${y(v)}" x2="${L+IW}" y2="${y(v)}" stroke="#e4e9ec"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end" font-size="10" fill="#5c6971">${v===0?'0':Math.abs(v)>=10000?v.toExponential(1):num(v)}</text>`;}
    svg+=`<path d="M${L},${T}V${T+IH}H${L+IW}" fill="none" stroke="#91a0a8"/>`;
    for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4;svg+=`<text x="${x(v)}" y="296" text-anchor="middle" font-size="10" fill="#5c6971">${rt(v)}</text>`;}
    const active=peer?null:activeCurve(item);
    if(active&&state.bounds){const left=axis==='aligned'?active.aligned_bound_left_rt:active.bound_left_rt,right=axis==='aligned'?active.aligned_bound_right_rt:active.bound_right_rt;if(finite(left)&&finite(right)){const a=Math.max(lo,Math.min(left,right)),b=Math.min(hi,Math.max(left,right));if(b>=a)svg+=`<rect data-integration-range="true" x="${x(a)}" y="${T}" width="${Math.max(0,x(b)-x(a))}" height="${IH}" fill="${color(active.sample_id)}" opacity=".09"><title>積分範圍 ${drt(left)}–${drt(right)} min</title></rect>`;}}
    drawable.forEach(({curve,series:s})=>{const focused=peer||!state.sample||curve.sample_id===state.sample,opacity=!peer&&state.sample&&!focused?.16:.92,points=s.rts.map((v,i)=>finite(v)&&finite(s.intensities[i])?`${x(v)},${y(s.intensities[i])}`:'').filter(Boolean).join(' '),signalName={raw:'原始訊號',baseline:'基線',smoothed:'平滑訊號',positive:'基線以上訊號',positive_signal:'基線以上訊號'}[s.kind]||'處理訊號';svg+=`<polyline data-kind="${esc(s.kind)}" data-curve-ref="${esc(curve.evidence_ref||'')}" clip-path="url(#curve-window-${axis})" fill="none" stroke="${color(curve.sample_id)}" stroke-width="${s.kind==='raw'?1.7:2.2}" stroke-dasharray="${s.kind==='baseline'?'6 4':''}" opacity="${opacity}" points="${points}"><title>${esc(sampleName(curve.sample_id))} · ${signalName} · ${esc(curve.stage_label||curve.kind||'保留曲線')}</title></polyline>`;if(curve!==active||!state.bounds||s.kind!==state.signal&&!(state.signal==='positive'&&s.kind==='positive_signal'))return;
      const marks=axis==='aligned'?[['bound','左峰界',curve.aligned_bound_left_rt],['bound','右峰界',curve.aligned_bound_right_rt],['apex','積分峰頂',curve.aligned_apex_rt]]:[['bound','左峰界',curve.bound_left_rt],['bound','右峰界',curve.bound_right_rt],['apex','積分峰頂',curve.apex_rt],...(finite(curve.integration?.raw_apex_rt_min)&&(!finite(curve.apex_rt)||Math.abs(curve.integration.raw_apex_rt_min-curve.apex_rt)>1e-7)?[['raw-apex','原始訊號峰頂',curve.integration.raw_apex_rt_min]]:[])];
      marks.forEach(([kind,label,v])=>{if(finite(v))svg+=`<line data-marker="${kind}" x1="${x(v)}" x2="${x(v)}" y1="${T}" y2="${T+IH}" stroke="${color(curve.sample_id)}" stroke-dasharray="${kind==='bound'?'5 4':kind==='raw-apex'?'2 4':'1 3'}"><title>${label} ${drt(v)} min</title></line>`;});
    });
    return `<svg role="img" aria-label="${axis==='raw'?'原始時間':'對齊時間'}層析圖" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${svg}<text x="398" y="324" text-anchor="middle" font-size="11">${axis==='raw'?'原始 RT':'對齊 RT'}（min）</text></svg>`;
  }
  function plotCard(item,axis,signalLabel){
    const isAligned=axis==='aligned', coverage=alignmentCoverage(item), total=Math.max(coverage.total,coverage.available);
    if(isAligned&&!alignedAvailable(item))return '<section class="plot-card plot-unavailable" id="plot-aligned"><div class="plot-header"><div><h4>對齊時間 · '+signalLabel+'</h4><p>來源未提供目前所選曲線與訊號的對齊座標。</p></div></div><div class="aligned-unavailable"><strong>對齊時間圖不可用</strong><p>原始時間圖仍可檢閱；此處不以原始座標或標線代替對齊資料。</p></div></section>';
    const overlay=`僅顯示${signalLabel}。`;
    const calibration=isAligned?`<p class="alignment-summary ${coverage.available<total?'partial':''}">校正狀態：${coverage.available} / ${total} 個目前顯示樣本有對齊座標${coverage.available<total?'；其餘不外插':''}</p>`:'';
    return `<section class="plot-card" id="plot-${axis}"><div class="plot-header"><div><h4>${isAligned?'對齊時間':'原始時間'} · ${signalLabel}</h4>${calibration}</div><button type="button" data-zoom="${axis}">放大圖表 ↗</button></div><div class="svg-container" id="svg-${axis}">${plot(item,axis)}</div><p class="chart-caption">${overlay}${isAligned?'只使用來源提供的對齊座標與標線。':'峰界與積分讀值使用來源保存的原始時間值。'}</p></section>`;
  }
  function evidencePanel(item){
    if(!state.evidence)return '';const record=records(item).find(r=>r.ref===state.evidence);
    if(!record)return `<aside class="evidence-panel" id="evidence-panel" tabindex="-1" aria-live="polite"><div class="evidence-header"><div><span class="eyebrow">${esc(state.evidenceContext||'目前資料')}</span><h3>來源內容未提供</h3></div><button type="button" class="evidence-close" id="close-evidence">關閉來源</button></div><p>${records(item).length?'這筆引用沒有對應到本報告內嵌的可讀紀錄。':'本報告未提供可讀來源紀錄，無法在離線頁面中補出說明。'}</p><details><summary>完整紀錄與引用</summary><div class="technical-ref"><code>${esc(state.evidence)}</code><button type="button" data-copy="${esc(state.evidence)}">複製</button></div><p class="control-note">本報告僅保留引用，未包含內容。</p></details></aside>`;
    const fields=Array.isArray(record.fields)?record.fields:[],links=Array.isArray(record.links)?record.links:[],tech=Array.isArray(record.technical_refs)?record.technical_refs:[];
    return `<aside class="evidence-panel" id="evidence-panel" tabindex="-1" aria-live="polite"><div class="evidence-header"><div><span class="eyebrow">${esc(state.evidenceContext||'目前資料')}</span><h3>${esc(record.title||'可讀來源紀錄')}</h3></div><button type="button" class="evidence-close" id="close-evidence">關閉來源</button></div>${record.summary?`<p class="evidence-summary">${esc(record.summary)}</p>`:''}${links.length?`<section class="record-links"><h4>相關紀錄</h4>${links.map(link=>records(item).some(r=>r.ref===link.ref)?`<button type="button" data-record-ref="${esc(link.ref)}">${esc(link.label||'查看相關紀錄')}</button>`:`<div class="external-reference"><strong>${esc(link.label||'外部引用')}</strong><span>本報告僅保留引用，未包含內容。</span></div>`).join('')}</section>`:''}<details><summary>完整紀錄與引用</summary>${fields.length?`<dl class="evidence-fields">${fields.map(f=>`<div><dt>${esc(f.label||'欄位')}</dt><dd>${esc(f.value??'未提供')}</dd></div>`).join('')}</dl>`:''}${tech.length?tech.map(t=>`<div class="technical-ref"><span><strong>${esc(t.label||'技術引用')}</strong><code>${esc(t.value??'')}</code></span><button type="button" data-copy="${esc(t.value??'')}">複製</button></div>`).join(''):`<div class="technical-ref"><span><strong>紀錄識別碼</strong><code>${esc(record.ref)}</code></span><button type="button" data-copy="${esc(record.ref)}">複製</button></div>`}</details></aside>`;
  }
  function detail(){
    const item=items.find(i=>i.id===state.id);if(!item){const excluded=filtered.filter(isCollapsedExcluded).length;$('workspace-content').innerHTML=excluded?`<div class="empty-state"><strong>目前未選取項目</strong><p>左側有 ${excluded} 筆已排除候選；展開後可查看保存曲線與來源紀錄。</p></div>`:'<p class="empty-state">這份報告沒有符合的項目。</p>';return;}normalize(item);
    const graph=state.sample?`${sampleName(state.sample)} · 目前查看`:'所有可見樣本',selectedSignalLabel={raw:'原始訊號',baseline:'基線',smoothed:'平滑訊號',positive:'基線以上訊號'}[state.signal]||'訊號',signalLabel=selectedSignalLabel;
    $('workspace-content').innerHTML=`<div class="detail-top"><div><p class="detail-eyebrow">${itemName(item)} · ${itemRows(item).length} 個樣本</p><h2>${esc(itemTitle(item))}</h2><div class="detail-meta"><span>m/z <strong>${mz(item.precursor_mz)}</strong></span><span>主要 RT <strong>${rt(item.rt_apex_min)} min</strong></span>${item.method_label?`<span>${esc(item.method_label)}</span>`:''}</div><p class="detail-summary">${esc(summary(item))} ${esc(item.reason_label||'未記錄項目判定說明。')}</p></div><span class="badge badge-${esc(item.stratum)}">${esc(badge(item))}</span></div>${evidencePanel(item)}<section class="advanced-inspection" aria-label="候選與積分"><section class="inspection-section" id="inspection-workspace"><div class="inspection-heading"><div class="inspection-title"><h3 id="inspection-title" tabindex="-1">${esc(graph)}</h3>${state.sample?'<button type="button" class="return-all" id="btn-unfocus">← 返回所有樣本</button>':''}</div><span class="focus-status">${state.sample?'候選與擷取':'曲線與積分 · 比較所有可見樣本'}</span></div><div class="selection-controls ${state.sample?'':'overview-controls'}">${state.sample?`<div class="control-group"><span class="control-label">候選峰</span>${candidateControl(item)}</div><div class="control-group"><span class="control-label">擷取階段</span>${captureControl(item)}</div>`:''}<div class="control-group"><span class="control-label">訊號種類</span>${signalControl(item)}</div></div><div class="chart-toolbar"><div class="sample-legend" aria-label="樣本曲線顯示">${legend(item)}</div><label><input id="chk-bounds" type="checkbox" ${state.bounds?'checked':''}> 顯示所選積分範圍與峰頂</label></div><div class="analysis-layout ${state.evidence?'with-evidence':''}"><div class="chart-column"><div class="charts-grid">${plotCard(item,'raw',signalLabel)}${plotCard(item,'aligned',signalLabel)}</div><section class="integration-card" id="integration-readout">${readout(item)}</section></div></div></section></section>${internalPairSupportPanel(item)}<section class="sample-section" aria-label="所有樣本的正式結果與支持紀錄"><div class="section-heading"><h3>所有樣本的正式結果與支持紀錄</h3></div>${sampleTable(item)}</section>`;
  }
  function reset(){rel.node=null;rel.pair=null;rel.captures={};state.sample=null;state.candidate='';state.capture='';state.signal='smoothed';state.evidence='';state.evidenceContext='';evidenceOpener=null;}
  function clearFilters(){for(const id of ['search','mz-min','mz-max','rt-min','rt-max'])$(id).value='';list();}
  $('record-list').addEventListener('click',event=>{const toggle=event.target.closest('[data-excluded-toggle]');if(toggle){const opening=!state.excludedOpen;state.excludedOpen=opening;list(!(opening&&!state.id));$('record-list').querySelector('[data-excluded-toggle]')?.focus();return;}const b=event.target.closest('[data-item]');if(!b)return;state.id=b.dataset.item;reset();list();$('record-list').querySelector('[aria-current="true"]')?.focus();});
  $('detail-panel').addEventListener('click',event=>{
    const item=items.find(i=>i.id===state.id);if(!item)return;
    const node=event.target.closest('[data-relation-node]'),pair=event.target.closest('[data-relation-pair]'),axis=event.target.closest('[data-time-axis]'),resetButton=event.target.closest('[data-relation-reset]');
    if(!node&&!pair&&!axis&&!resetButton)return;event.stopImmediatePropagation();
    if(node){rel.node=rel.node===node.dataset.relationNode?null:node.dataset.relationNode;rel.pair=null;}
    if(pair){rel.pair=pair.dataset.relationPair;rel.node=null;const found=relationshipModel(item).pairs.find(p=>p.edge_ref===rel.pair);if(found?.support_kind!=='direct')rel.reviews=true;}
    if(axis)rel.axis=axis.dataset.timeAxis;
    if(resetButton){rel.node=null;rel.pair=null;relationshipModel(item).nodes.forEach(n=>state.visible.add(n.sid));}
    detail();
  });
  $('detail-panel').addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)&&event.target.matches('g[data-relation-node],g[data-relation-pair]')){event.preventDefault();event.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}});
  $('detail-panel').addEventListener('change',event=>{
    const el=event.target;
    if(el.id==='relationship-reviews'){rel.reviews=el.checked;const item=items.find(i=>i.id===state.id),pair=relationshipModel(item).pairs.find(p=>p.edge_ref===rel.pair);if(!rel.reviews&&pair?.support_kind!=='direct')rel.pair=null;}
    else if(el.id==='relationship-signal')rel.signal=el.value;
    else if(el.dataset.laneCapture)rel.captures[el.dataset.laneCapture]=el.value;
    else return;event.stopImmediatePropagation();detail();
  });
  $('detail-panel').addEventListener('change',event=>{const el=event.target,item=items.find(i=>i.id===state.id);if(!item)return;if(el.id==='candidate-select'){state.candidate=el.value;state.capture='';state.evidence='';normalize(item);}if(el.id==='capture-select'){state.capture=el.value;state.evidence='';}if(el.id==='signal-select')state.signal=el.value;if(el.id==='chk-bounds')state.bounds=el.checked;if(el.dataset.sampleVisible){if(el.checked)state.visible.add(el.dataset.sampleVisible);else state.visible.delete(el.dataset.sampleVisible);}detail();});
  $('detail-panel').addEventListener('click',async event=>{const item=items.find(i=>i.id===state.id);if(!item)return;const focus=event.target.closest('[data-focus]');if(focus){state.sample=focus.dataset.focus;state.candidate='';state.capture='';state.evidence='';state.evidenceContext='';evidenceOpener=null;normalize(item,true);detail();requestAnimationFrame(()=>{const heading=$('inspection-title');heading?.focus({preventScroll:true});heading?.scrollIntoView({behavior:'smooth',block:'start'});});return;}if(event.target.closest('#btn-unfocus')){state.sample=null;state.candidate='';state.capture='';state.evidence='';state.evidenceContext='';evidenceOpener=null;normalize(item);detail();requestAnimationFrame(()=>{const heading=$('inspection-title');heading?.focus({preventScroll:true});heading?.scrollIntoView({behavior:'smooth',block:'start'});});return;}const evidence=event.target.closest('[data-evidence-ref]');if(evidence){evidenceOpener={ref:evidence.dataset.evidenceRef,context:evidence.dataset.evidenceContext||''};state.evidence=evidenceOpener.ref;state.evidenceContext=evidenceOpener.context;detail();requestAnimationFrame(()=>{const panel=$('evidence-panel');panel?.scrollIntoView({behavior:'smooth',block:'start'});panel?.focus({preventScroll:true});});return;}const link=event.target.closest('[data-record-ref]');if(link){state.evidence=link.dataset.recordRef;detail();requestAnimationFrame(()=>{const panel=$('evidence-panel');panel?.scrollIntoView({behavior:'smooth',block:'start'});panel?.focus({preventScroll:true});});return;}if(event.target.closest('#close-evidence')){state.evidence='';state.evidenceContext='';detail();requestAnimationFrame(()=>{const opener=[...document.querySelectorAll('[data-evidence-ref]')].find(el=>el.dataset.evidenceRef===evidenceOpener?.ref&&(el.dataset.evidenceContext||'')===evidenceOpener?.context);opener?.scrollIntoView({block:'nearest'});opener?.focus({preventScroll:true});evidenceOpener=null;});return;}const copy=event.target.closest('[data-copy]');if(copy){try{await navigator.clipboard.writeText(copy.dataset.copy||'');const old=copy.textContent;copy.textContent='已複製';setTimeout(()=>copy.textContent=old,1200);}catch(_){copy.textContent='無法複製';}return;}const zoom=event.target.closest('[data-zoom]');if(zoom){dialogOpener=zoom;$('zoom-title').textContent=`${itemTitle(item)} · ${state.sample?sampleName(state.sample):'所有可見樣本'} · ${zoom.dataset.zoom==='raw'?'原始時間':'對齊時間'}`;$('zoom-container').innerHTML=plot(item,zoom.dataset.zoom);$('zoom-dialog').showModal();}});
  for(const id of ['search','mz-min','mz-max','rt-min','rt-max'])$(id).addEventListener('input',list);
  $('clear').addEventListener('click',clearFilters);$('active-filters').addEventListener('click',event=>{if(event.target.closest('#clear-inline'))clearFilters();});
  $('zoom-close').addEventListener('click',()=>$('zoom-dialog').close());$('zoom-dialog').addEventListener('close',()=>dialogOpener?.focus());
  document.addEventListener('keydown',event=>{const dialog=document.querySelector('dialog[open]');if(dialog){if(event.key==='Escape'){event.preventDefault();dialog.close();}return;}if(event.key==='Escape'&&state.evidence){event.preventDefault();state.evidence='';state.evidenceContext='';detail();requestAnimationFrame(()=>{const opener=[...document.querySelectorAll('[data-evidence-ref]')].find(el=>el.dataset.evidenceRef===evidenceOpener?.ref&&(el.dataset.evidenceContext||'')===evidenceOpener?.context);opener?.scrollIntoView({block:'nearest'});opener?.focus({preventScroll:true});evidenceOpener=null;});return;}if(event.target.closest('input,textarea,select')||!['ArrowDown','ArrowUp'].includes(event.key)||!listed.length)return;event.preventDefault();const i=listed.findIndex(x=>x.id===state.id);state.id=listed[Math.max(0,Math.min(listed.length-1,i+(event.key==='ArrowDown'?1:-1)))].id;reset();list();$('record-list').querySelector('[aria-current="true"]')?.focus();});
  $('btn-report-info').addEventListener('click',event=>{dialogOpener=event.target;const r=data.selection||{},source=$('report-info-source').dataset,range=(a,b,f)=>a==null&&b==null?'不限':`${a==null?'不限':f(a)} 至 ${b==null?'不限':f(b)}`;$('report-info-body').innerHTML=`<dl class="factlist"><dt>檢閱方式</dt><dd>${esc(source.strategyLabel)}</dd><dt>包含項目</dt><dd>${items.length} 筆（執行共 ${esc(source.runTotal)} 筆，${esc(source.eligibleCount)} 筆可供本次選取）</dd><dt>來源檔案</dt><dd>${esc(source.sourceName)}</dd><dt>可用資料</dt><dd>${esc(source.capabilityLabel)}</dd>${data.summary?.matrix_path?`<dt>Matrix 路徑</dt><dd>${esc(data.summary.matrix_path)}</dd>`:''}<dt>m/z 範圍</dt><dd>${range(r.mz_min,r.mz_max,mz)}</dd><dt>RT（min）</dt><dd>${range(r.rt_min,r.rt_max,rt)}</dd><dt>樣本</dt><dd>${r.sample_ids?.length?r.sample_ids.map(sampleName).map(esc).join('、'):'全部樣本'}</dd></dl>`;$('report-info-dialog').showModal();});
  $('report-info-close').addEventListener('click',()=>$('report-info-dialog').close());$('report-info-dialog').addEventListener('close',()=>dialogOpener?.focus());
  if(data.notice){$('report-notice').textContent=data.notice;$('report-notice').hidden=false;}list();
})();
"""
