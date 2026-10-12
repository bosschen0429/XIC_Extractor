(() => {
  const data=JSON.parse(document.getElementById('inspector-data').textContent);
  if (data.summary?.source_kind === 'targeted_workbook') {
    renderTargetedWorkbook(data);
    return;
  }

  function renderTargetedWorkbook(payload) {
    const element = (tag, text, className) => {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    };
    const value = (v, digits = 4) => v === null || v === undefined || v === ''
      ? '未提供' : typeof v === 'number' ? v.toLocaleString('en-US', {maximumFractionDigits: digits}) : String(v);
    const range = (start, end) => `${value(start)} – ${value(end)} min`;
    const counted = v => v === true || v === 'TRUE' ? '是' : v === false || v === 'FALSE' ? '否' : '未記錄';
    const isNumber = v => typeof v === 'number' && Number.isFinite(v);
    // Intensities span decades, so they read as Excel-style scientific notation.
    const scientific = v => {
      if (!isNumber(v)) return value(v);
      const [mantissa, exponent] = v.toExponential(2).split('e');
      const power = Number(exponent);
      return `${mantissa}E${power < 0 ? '-' : '+'}${String(Math.abs(power)).padStart(2, '0')}`;
    };
    const embedded = new URLSearchParams(location.search).has('embed');
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
        minority_counted: `${rest} / ${focus.decided} 筆未計入；標「少數」的 ${n} 筆計入，確認是否抓錯峰。`,
        minority_not_counted: `${rest} / ${focus.decided} 筆計入；標「少數」的 ${n} 筆未計入，確認是否漏抓。`,
        mixed: `沒有明顯多數；先看 ${n} 筆有檢閱註記的結果。`,
      }[focus.kind];
      return text ? element('p', text, 'tgt-focus-banner') : null;
    };
    const root = element('main', undefined, 'tgt-review');
    if (!embedded) root.append(element('h1', 'Targeted 結果'));
    for (const item of payload.items) {
      const target = item.details.target, focus = item.details.focus;
      const istdRecords = item.details.istd_records || {};
      const focusSet = new Set(focus.samples);
      const reviews = new Map(item.details.review_queue.map(row => [row.Sample, row]));
      const section = element('section');
      if (payload.items.length > 1) section.append(element('h2', item.title));
      const context = [target.Role, target['ISTD Pair'] ? `配對 ISTD：${target['ISTD Pair']}` : null, `設定 RT ${range(target['RT min'], target['RT max'])}`].filter(Boolean);
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
      const titles = ['樣本', 'RT (min)', '面積', '計入偵測', ...(paired ? ['ISTD RT (min)', 'ISTD 面積'] : []), '檢閱註記'];
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
        const review = reviews.get(record.SampleName), istd = istdRecords[record.SampleName];
        const panel = element('div', undefined, 'tgt-evidence');
        const pairs = [['積分範圍', range(record.PeakStart, record.PeakEnd)], ['NL 證據', record['NL Evidence']]];
        if (istd) {
          const delta = isNumber(record.RT) && isNumber(istd.RT)
            ? `，與 ISTD 相差 ${record.RT - istd.RT >= 0 ? '+' : ''}${(record.RT - istd.RT).toFixed(3)} min` : '';
          pairs.push([`配對 ISTD（${istd.Target}）`, `RT ${value(istd.RT)} min${delta} · 面積 ${scientific(istd.Area)} · 計入 ${counted(istd['Counted Detection'])}`]);
        }
        panel.append(facts(pairs));
        if (review?.Why) panel.append(element('h4', '檢閱註記'), element('p', review.Why));
        if (review?.Action) panel.append(element('h4', '建議檢查'), element('p', review.Action));
        if (review?.Evidence) panel.append(element('h4', '檢閱線索'), element('p', review.Evidence));
        if (record.Reason) {
          const raw = element('details', undefined, 'tgt-raw');
          raw.append(element('summary', '原始選峰紀錄'), element('p', record.Reason));
          panel.append(raw);
        }
        return panel;
      };
      const rows = records.map(record => {
        const review = reviews.get(record.SampleName), row = element('tr');
        const sampleCell = element('td'), button = element('button', record.SampleName, 'tgt-sample');
        button.type = 'button';button.setAttribute('aria-expanded', 'false');
        sampleCell.append(button);
        if (focusSet.has(record.SampleName)) {
          row.classList.add('focus');
          sampleCell.append(element('span', '少數', 'tgt-badge attention'));
        }
        row.append(sampleCell, element('td', value(record.RT), 'tgt-number'), element('td', scientific(record.Area), 'tgt-number'), element('td', counted(record['Counted Detection']), 'tgt-counted'));
        if (paired) {
          const istd = istdRecords[record.SampleName];
          const istdArea = element('td', istd ? scientific(istd.Area) : '—', 'tgt-number tgt-istd');
          if (istd && counted(istd['Counted Detection']) !== '是') istdArea.append(element('span', 'ISTD 未計入', 'tgt-badge'));
          row.append(element('td', istd ? value(istd.RT) : '—', 'tgt-number tgt-istd'), istdArea);
        }
        row.append(element('td', review?.Why || '—', 'tgt-reason'));
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
      section.append(scroll, element('p', '點樣本名稱展開細節。', 'tgt-note'));
      const method = element('details', undefined, 'tgt-method');
      method.append(element('summary', '方法設定'));
      method.append(facts([
        ['目標 m/z', target['m/z']], ['m/z 容差 (ppm)', target['ppm tol']],
        ['NL (Da)', target['NL (Da)']], ['NL 容差 warn / max (ppm)', `${value(target['NL ppm warn'])} / ${value(target['NL ppm max'])}`],
        ['樣本適用範圍', target['Sample applicability']],
      ]));
      const labels = {
        signal_processing_method: '平滑方法', signal_processing_window_points: '平滑視窗 (points)',
        ms2_precursor_tol_da: 'MS2 precursor 容差 (Da)', nl_min_intensity_ratio: 'NL 最低相對強度',
        nl_rt_anchor_search_margin_min: 'NL 錨定搜尋半徑 (min)', nl_rt_anchor_half_window_min: '錨定後 XIC 半寬 (min)',
        nl_fallback_half_window_min: 'NL fallback 半寬 (min)', rolling_window_size: 'ISTD RT 滾動視窗',
        dirty_matrix_mode: '髒基質模式', targeted_ms1_shape_identity_activation_policy: 'MS1 shape identity policy',
      };
      method.append(facts(payload.summary.method_context.filter(([key]) => labels[key]).map(([key, saved]) => [labels[key], saved])));
      section.append(method);
      root.append(section);
    }
    if (!payload.items.length) root.append(element('p', '沒有符合條件的目標。'));
    const provenance = element('details', undefined, 'tgt-provenance');
    provenance.append(element('summary', '執行資訊'));
    const labels = {generated_at: '建立時間', app_version: '版本', config_hash: '設定識別', target_config_hash: '目標設定識別', parallel_mode: '執行模式', parallel_workers: 'Workers', limited_identity_gate: 'Limited identity gate'};
    provenance.append(facts(payload.summary.method_context.filter(([key]) => labels[key]).map(([key, saved]) => [labels[key], saved])));
    root.append(provenance);
    // The workbench already states this beside the embedded view.
    if (!embedded) root.append(element('p', '唯讀結果 · 未保存逐點曲線，不重建色譜圖。', 'tgt-note'));
    document.body.replaceChildren(root);
  }
  const items=Array.isArray(data.items)?data.items:[], samples=Array.isArray(data.samples)?data.samples:[];
  const colors=Array.from({length:8},(_,index)=>`var(--curve-${index+1})`);
  const $=id=>document.getElementById(id), has=(o,k)=>Object.prototype.hasOwnProperty.call(o||{},k);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite=v=>typeof v==='number'&&Number.isFinite(v), fixed=(v,d)=>finite(v)?v.toFixed(d):'未提供';
  const mz=v=>fixed(v,4), rt=v=>fixed(v,2), drt=v=>fixed(v,3);
  const tickNum=v=>v===0?'0':Math.abs(v)>=1e6?v.toExponential(1):Math.abs(v)>=100?Math.round(v).toLocaleString('en-US'):num(v);
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
  const result=row=>!has(row,'quantitative_value')?'<span class="result-missing">無單一結果</span>':row.quantitative_value===null?'<span class="result-null">空值</span>':`<span class="${row.quantitative_value===0?'result-zero':'result-number'}">${num(row.quantitative_value)}</span>${row.quantity_unit?` <span class="result-unit">${esc(row.quantity_unit)}</span>`:''}`;
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
  function curveOrigin(item,sampleId){return itemRows(item).find(row=>row.sample_id===sampleId)?.support_origin||{kind:'unavailable'};}
  function originDash(origin,signal=''){return {discovery_support:signal==='baseline'?'6 4':'',reacquired_support:'8 4',backfill_support:'2 4',unavailable:'1 5'}[originClass(origin)];}
  // Line style distinguishes support origins; when no sample records an origin there is nothing to distinguish.
  function curveDash(item,origin,signal=''){return itemRows(item).some(r=>originClass(r.support_origin)!=='unavailable')?originDash(origin,signal):'';}
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
    return {nodes,pairs:pairs.filter(p=>byId.has(p.left_member_subject_id)&&byId.has(p.right_member_subject_id)),byId,availability:item.relationship_availability||'unavailable'};
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
    if(!model.pairs.length){const label=model.availability==='recorded'?'本群組沒有保存的配對紀錄':model.availability==='not_retained'?'來源未保存樣本配對圖':'來源未提供樣本關係資料';return `<strong>${label}</strong>。不能據此判定沒有配對支持。`;}
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
        svg+=`<path d="${lanePath(xs,ys,x,y)}" class="lane-raw" data-kind="${esc(rel.signal)}" data-support-origin="${originClass(n.row?.support_origin)}" stroke-dasharray="${curveDash(item,n.row?.support_origin)}"/>`;
        if(finite(apex))svg+=`<line x1="${x(apex)}" x2="${x(apex)}" y1="4" y2="76" class="lane-apex"/>`;
        chart=`<svg viewBox="0 0 690 100" preserveAspectRatio="none" aria-label="${esc(n.label)} 的${axis==='aligned'?'校正':'原始'}時間曲線"><title>${esc(n.label)} · ${esc(c.stage_label)} · ${rel.signal==='smoothed'?'平滑':'原始'}最高強度 ${num(max)}</title>${svg}</svg>`;}
      return `<article class="relationship-lane" data-lane-member="${esc(n.id)}" data-injection-order="${n.order??''}"><div class="lane-label"><div class="lane-name"><span class="injection-order">${n.order===null?'?':String(n.order).padStart(2,'0')}</span>${esc(shortName(n.label))}</div><span class="reading-hint">${esc(c?.stage_label||'支持曲線未提供')}</span>${n.curves.length>1?`<select data-lane-capture="${esc(n.id)}" aria-label="${esc(n.label)} 曲線選擇">${n.curves.map(v=>`<option value="${esc(v.evidence_ref)}" ${v===c?'selected':''}>${esc(v.stage_label||v.kind)}</option>`).join('')}</select>`:''}<details><summary>定量與來源</summary><p>正式結果：${result(n.row||{})}</p>${c?`<p>記錄峰頂：${drt(c.apex_rt)} min<br>積分範圍：${drt(c.bound_left_rt)}–${drt(c.bound_right_rt)} min<br>當次原始計數面積：${num(c.area)} counts·s</p>${evidenceButtons([c.evidence_ref],`${n.label} 的支持曲線`,'查看曲線來源')}`:''}${evidenceButtons(n.row?.decision_refs,`${n.label} 的正式結果`)}${evidenceButtons(originRefs(n.row?.support_origin),`${n.label} 的支持來源`,'查看支持記錄')}</details></div><div class="lane-chart">${chart}</div></article>`;
    }).join('');
    return `<section class="relationship-curves" aria-label="依進樣順序的跨樣本曲線"><div class="relation-section-heading"><div><h3>${rel.pair?'這兩個樣本的曲線':rel.node?'這個樣本的曲線':'跨樣本曲線'}</h3><p class="reading-hint">${nodes.length} 個樣本 · 依進樣先後，由上而下 · 共用${axis==='aligned'?'校正':'原始'}時間軸 · 各自最高點縮放；? 表示順序未提供</p></div><div class="relation-options"><div class="axis-switch"><button type="button" data-time-axis="raw" aria-pressed="${axis==='raw'}">原始 RT</button><button type="button" data-time-axis="aligned" aria-pressed="${axis==='aligned'}">校正 RT</button></div><label for="relationship-signal">曲線</label><select id="relationship-signal"><option value="smoothed" ${rel.signal==='smoothed'?'selected':''}>平滑訊號</option><option value="raw" ${rel.signal==='raw'?'selected':''}>原始訊號</option></select></div></div><div id="relationship-lanes">${lanes||'<p class="lane-missing">目前沒有可見樣本；可在樣本結果中恢復顯示。</p>'}</div><p class="reading-hint">藍：${rel.signal==='smoothed'?'平滑訊號':'原始訊號'} · 線型依支持來源（實線：初始探索；長虛線：重新擷取；點虛線：Backfill） · 紫色虛線：記錄峰頂。校正座標缺失時不以原始 RT 代替。</p></section>`;
  }
  function internalPairSupportPanel(item){
    const model=relationshipModel(item);
    if(!model.pairs.length)return `<section class="relationship-workbench" aria-label="樣本配對支持"><div class="relation-section-heading"><h3>樣本關係</h3></div><p class="relation-empty" aria-live="polite">${relationshipDetail(item,model)}</p>${model.nodes.some(n=>n.curves.length)?relationshipLanes(item,model):''}</section>`;
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
    $('active-filters').innerHTML=active.length?`<span class="active-label">本頁已套用</span>${active.map(x=>`<span class="filter-chip">${esc(x)}</span>`).join('')}<button type="button" id="clear-inline">清除</button>`:'';
    const excluded=filtered.filter(isCollapsedExcluded).length;
    $('loaded-range').textContent=active.length?`符合篩選 ${filtered.length} 個${excluded?`，其中 ${excluded} 個為已排除候選`:''}`:'';
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
    const multi=itemRows(item).filter(r=>candidates(item,r.sample_id).length>1);
    const fact=(label,value,unit='',warn=false,title='')=>`<div class="fact ${warn?'warn':''}"${title?` title="${esc(title)}"`:''}><dt>${label}</dt><dd>${value}${unit?`<small>${unit}</small>`:''}</dd></div>`;
    return `<dl class="fact-row">${fact('m/z',mz(item.precursor_mz))}${fact('主要 RT',rt(item.rt_apex_min),'min')}${fact('樣本',itemRows(item).length)}${fact('有定量值',values)}${nulls?fact('空值',nulls,'',true):''}${noSingle?fact('無單一結果',noSingle,'',true):''}${noCurves?fact('未提供曲線',noCurves,'',true):''}${multi.length?fact('多個候選',multi.length,'個樣本',true,multi.map(r=>sampleName(r.sample_id)).join('、')):''}</dl>`;
  }
  function evidenceButtons(refs,context,label='查看定量依據'){
    const values=[...new Set((Array.isArray(refs)?refs:[]).filter(Boolean))];
    // Several references are told apart by their readable record title when the report embeds one.
    const titled=ref=>records(items.find(i=>i.id===state.id)).find(r=>r.ref===ref)?.title;
    return values.length?`<div class="evidence-actions">${values.map((ref,i)=>`<button type="button" class="evidence-link" data-evidence-ref="${esc(ref)}" data-evidence-context="${esc(context)}">${values.length>1?esc(titled(ref)||`${label} ${i+1}`):label}</button>`).join('')}</div>`:'';
  }
  function sampleTable(item){
    const excluded=isCollapsedExcluded(item), evidenceLabel=excluded?'查看排除依據':'查看定量依據';
    return `<div class="table-scroll compact-samples"><table class="sample-table"><thead><tr><th>顯示</th><th>樣本與狀態</th><th class="num">正式結果</th><th class="num"><span class="sr-only">操作</span></th></tr></thead><tbody>${itemRows(item).map(r=>{const notices=sampleNotices(item,r),current=r.sample_id===state.sample,evidenceContext=excluded?`${sampleName(r.sample_id)} 的排除依據`:`${sampleName(r.sample_id)} 的正式定量值`,origin=r.support_origin||{kind:'unavailable'},alignment=sampleAlignment(item,r),supportRefs=originRefs(origin);return `<tr class="sample-row ${notices.length?'sample-notice':''} ${current?'focused-row':''}" data-sample-row="${esc(r.sample_id)}"><td><label><input type="checkbox" data-sample-visible="${esc(r.sample_id)}" ${state.visible.has(r.sample_id)?'checked':''}><span class="sr-only">在圖中顯示 ${esc(sampleName(r.sample_id))}</span></label></td><td><span class="sample-identity"><span class="sample-dot" style="background:${color(r.sample_id)}"></span>${esc(sampleName(r.sample_id))}</span><div class="sample-sub"><span class="badge badge-${esc(r.status_kind)}">${esc(r.status_label||'未記錄')}</span>${notices.length?`<span class="sample-flags" aria-label="需要留意：${esc(notices.map(n=>n.label).join('、'))}">${notices.map(n=>`<span class="sample-flag flag-${n.kind}">${esc(n.label)}</span>`).join('')}</span>`:''}</div><div class="sample-sub sample-provenance"><span class="support-origin origin-${originClass(origin)}">${esc(originLabel(origin))}</span><span class="alignment-status alignment-${alignment.kind}">${esc(alignment.label)}</span></div></td><td class="num">${result(r)}</td><td><div class="sample-actions"><button type="button" class="sample-focus" data-focus="${esc(r.sample_id)}" ${current?'disabled aria-current="true"':''}>${current?'目前查看':'查看樣本'}</button>${evidenceButtons(r.decision_refs,evidenceContext,evidenceLabel)}${supportRefs.length?evidenceButtons(supportRefs,`${sampleName(r.sample_id)} 的${originLabel(origin)}`,'查看支持記錄'):''}</div></td></tr>`;}).join('')}</tbody></table></div><p class="reading-hint">勾選控制圖中顯示；「查看樣本」帶出候選與積分。支持來源說明樣本如何進入目前族群；校正狀態只反映保存的對齊座標。</p>`;
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
  function signalControl(item){const ss=signals(item),labels={raw:'原始訊號',baseline:'基線',smoothed:'平滑訊號',positive:'基線以上訊號'};if(!ss.length)return '<p class="control-value">未提供訊號資料。</p>';return ss.length===1?`<p class="control-value">${labels[ss[0]]}</p>`:`<select id="signal-select" aria-label="訊號種類">${ss.map(s=>`<option value="${s}" ${s===state.signal?'selected':''}>${labels[s]}</option>`).join('')}</select>`;}
  function compactNames(item){
    // Legends show the distinguishing tail of long shared sample stems; the full name stays in the tooltip.
    const names=itemRows(item).map(r=>sampleName(r.sample_id));if(names.length<2)return n=>n;
    let prefix=names[0];names.forEach(n=>{while(!n.startsWith(prefix))prefix=prefix.slice(0,-1);});
    const cut=Math.max(...['_','-',' ','.'].map(c=>prefix.lastIndexOf(c)))+1;
    return cut>=8&&names.every(n=>n.length>cut)?n=>'…'+n.slice(cut):n=>n;
  }
  function legend(item){const short=compactNames(item);return itemRows(item).map(r=>{const notices=sampleNotices(item,r),origin=r.support_origin,name=sampleName(r.sample_id);return `<label class="legend-item ${notices.length?'has-notice':''}" title="${esc(name)} · ${esc(originLabel(origin))}"><input type="checkbox" data-sample-visible="${esc(r.sample_id)}" ${state.visible.has(r.sample_id)?'checked':''}><svg class="legend-line" viewBox="0 0 22 8" aria-hidden="true"><line x1="0" x2="22" y1="4" y2="4" stroke="${color(r.sample_id)}" stroke-width="2.5" stroke-dasharray="${curveDash(item,origin)}"/></svg><span>${esc(short(name))} · <small>${esc(originLabel(origin))}</small></span>${notices.map(n=>`<span class="legend-notice flag-${n.kind}" title="${esc(n.label)}">${esc(n.short)}</span>`).join('')}</label>`;}).join('');}
  function readout(item){
    if(!state.sample)return '<p class="integration-empty">在下方樣本表按「查看樣本」，這裡會顯示該樣本的積分範圍、峰頂、面積與正式定量值。</p>';
    const row=itemRows(item).find(r=>r.sample_id===state.sample)||{}, curve=activeCurve(item), c=candidates(item).find(x=>x.id===state.candidate), excluded=isCollapsedExcluded(item), evidenceLabel=excluded?'查看排除依據':'查看定量依據', evidenceContext=excluded?`${sampleName(state.sample)} 的排除依據`:'正式定量值';
    if(!curve)return `<div class="integration-empty"><strong>${esc(sampleName(state.sample))}</strong><p>此候選未提供保存曲線；正式定量結果仍為 ${result(row)}。</p>${evidenceButtons(row.decision_refs,evidenceContext,evidenceLabel)}</div>`;
    const rawApex=curve.integration?.raw_apex_rt_min, showRaw=finite(rawApex)&&(!finite(curve.apex_rt)||Math.abs(rawApex-curve.apex_rt)>1e-7);
    return `<div class="readout-heading"><div><span class="eyebrow">目前檢閱 · 下列 RT 均為原始時間</span><strong>${esc(sampleName(state.sample))}${c?` · ${esc((c.witness?.label||c.witness?.title||`候選 ${c.index+1}`))}`:''} · ${esc(curve.stage_label||curve.kind||'保留曲線')}</strong></div>${curve.evidence_ref?`<button type="button" class="evidence-link" data-evidence-ref="${esc(curve.evidence_ref)}" data-evidence-context="目前曲線與積分">查看曲線來源</button>`:''}</div><dl class="integration-grid"><div><dt>積分範圍（原始時間）</dt><dd data-readout="bounds">${drt(curve.bound_left_rt)}–${drt(curve.bound_right_rt)} min</dd></div><div><dt>積分峰頂（原始時間）</dt><dd data-readout="apex">${drt(curve.apex_rt)} min</dd></div>${showRaw?`<div><dt>原始訊號峰頂（原始時間）</dt><dd data-readout="raw-apex">${drt(rawApex)} min</dd></div>`:''}<div><dt>當次原始計數面積</dt><dd data-readout="area">${num(curve.area)} <span>counts·s</span></dd></div><div><dt>正式定量值</dt><dd data-readout="quantity">${result(row)}</dd></div></dl><p class="control-note">面積是這次積分的原始計數面積；正式定量值是來源保存的決策結果，兩者分開顯示。</p>${evidenceButtons(row.decision_refs,evidenceContext,evidenceLabel)}`;
  }
  function plot(item,axis,peer=false,wide=false){
    const drawable=curvesForPlot(item,peer).flatMap(curve=>seriesList(curve,axis,state.signal).map(series=>({curve,series})));
    if(!drawable.length)return `<div class="empty-state"><strong>${axis==='aligned'?'所選資料未提供對齊座標':'目前沒有可顯示的曲線'}</strong><p>${axis==='aligned'?'請切回原始時間；不會以原始座標代替。':state.signal==='smoothed'?'所選來源未保存可用的平滑曲線；可切換原始訊號檢查。':'請確認樣本顯示勾選，或來源是否保存曲線。'}</p></div>`;
    const reviewWindow=axis==='raw'&&Array.isArray(item.review_rt_window)?item.review_rt_window:null;
    let lo=reviewWindow?.[0]??Infinity,hi=reviewWindow?.[1]??-Infinity,lowY=0,highY=0;
    drawable.forEach(({series:s})=>s.rts.forEach((v,i)=>{const y=s.intensities[i];if(finite(v)&&finite(y)&&(!reviewWindow||v>=lo&&v<=hi)){if(!reviewWindow){lo=Math.min(lo,v);hi=Math.max(hi,v);}lowY=Math.min(lowY,y);highY=Math.max(highY,y);}}));
    if(!finite(lo)||!finite(hi))return '<div class="empty-state">未提供成對的時間與訊號數值。</div>';if(lo===hi){lo-=.5;hi+=.5;}if(lowY===highY)highY=lowY+1;
    const W=wide?1060:760,H=330,L=62,T=24,IW=W-88,IH=248,x=v=>L+(v-lo)/(hi-lo)*IW,y=v=>T+IH-(v-lowY)/(highY-lowY)*IH;
    let svg=`<defs><clipPath id="curve-window-${axis}"><rect x="${L}" y="${T}" width="${IW}" height="${IH}"/></clipPath></defs><text x="8" y="14" font-size="13" fill="#48534f">訊號強度</text>`;
    for(let i=0;i<=4;i++){const v=lowY+(highY-lowY)*i/4;svg+=`<line x1="${L}" y1="${y(v)}" x2="${L+IW}" y2="${y(v)}" stroke="#e4e9ec"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end" font-size="13" fill="#48534f">${tickNum(v)}</text>`;}
    svg+=`<path d="M${L},${T}V${T+IH}H${L+IW}" fill="none" stroke="#91a0a8"/>`;
    for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4;svg+=`<text x="${x(v)}" y="298" text-anchor="middle" font-size="13" fill="#48534f">${rt(v)}</text>`;}
    const active=peer?null:activeCurve(item);
    if(active&&state.bounds){const left=axis==='aligned'?active.aligned_bound_left_rt:active.bound_left_rt,right=axis==='aligned'?active.aligned_bound_right_rt:active.bound_right_rt;if(finite(left)&&finite(right)){const a=Math.max(lo,Math.min(left,right)),b=Math.min(hi,Math.max(left,right));if(b>=a)svg+=`<rect data-integration-range="true" x="${x(a)}" y="${T}" width="${Math.max(0,x(b)-x(a))}" height="${IH}" fill="${color(active.sample_id)}" opacity=".09"><title>積分範圍 ${drt(left)}–${drt(right)} min</title></rect>`;}}
    drawable.forEach(({curve,series:s})=>{const focused=peer||!state.sample||curve.sample_id===state.sample,opacity=!peer&&state.sample&&!focused?.16:.92,points=s.rts.map((v,i)=>finite(v)&&finite(s.intensities[i])?`${x(v)},${y(s.intensities[i])}`:'').filter(Boolean).join(' '),signalName={raw:'原始訊號',baseline:'基線',smoothed:'平滑訊號',positive:'基線以上訊號',positive_signal:'基線以上訊號'}[s.kind]||'處理訊號',origin=curveOrigin(item,curve.sample_id);svg+=`<polyline data-kind="${esc(s.kind)}" data-curve-ref="${esc(curve.evidence_ref||'')}" data-support-origin="${originClass(origin)}" clip-path="url(#curve-window-${axis})" fill="none" stroke="${color(curve.sample_id)}" stroke-width="${s.kind==='raw'?1.7:2.2}" stroke-dasharray="${curveDash(item,origin,s.kind)}" opacity="${opacity}" points="${points}"><title>${esc(sampleName(curve.sample_id))} · ${signalName} · ${esc(originLabel(origin))} · ${esc(curve.stage_label||curve.kind||'保留曲線')}</title></polyline>`;if(curve!==active||!state.bounds||s.kind!==state.signal&&!(state.signal==='positive'&&s.kind==='positive_signal'))return;
      const marks=axis==='aligned'?[['bound','左峰界',curve.aligned_bound_left_rt],['bound','右峰界',curve.aligned_bound_right_rt],['apex','積分峰頂',curve.aligned_apex_rt]]:[['bound','左峰界',curve.bound_left_rt],['bound','右峰界',curve.bound_right_rt],['apex','積分峰頂',curve.apex_rt],...(finite(curve.integration?.raw_apex_rt_min)&&(!finite(curve.apex_rt)||Math.abs(curve.integration.raw_apex_rt_min-curve.apex_rt)>1e-7)?[['raw-apex','原始訊號峰頂',curve.integration.raw_apex_rt_min]]:[])];
      marks.forEach(([kind,label,v])=>{if(finite(v))svg+=`<line data-marker="${kind}" x1="${x(v)}" x2="${x(v)}" y1="${T}" y2="${T+IH}" stroke="${color(curve.sample_id)}" stroke-dasharray="${kind==='bound'?'5 4':kind==='raw-apex'?'2 4':'1 3'}"><title>${label} ${drt(v)} min</title></line>`;});
    });
    return `<svg role="img" aria-label="${axis==='raw'?'原始時間':'對齊時間'}層析圖" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${svg}<text x="${L+IW/2}" y="324" text-anchor="middle" font-size="13" fill="#48534f">${axis==='raw'?'原始 RT':'對齊 RT'}（min）</text></svg>`;
  }
  function plotCard(item,axis,signalLabel,wide=false){
    const isAligned=axis==='aligned', coverage=alignmentCoverage(item), total=Math.max(coverage.total,coverage.available);
    if(isAligned&&!alignedAvailable(item))return `<p class="plot-note" id="plot-aligned"><strong>對齊時間圖不可用</strong>：來源未提供目前所選曲線與${signalLabel}的對齊座標；不以原始座標代替。</p>`;
    const calibration=isAligned?`<p class="alignment-summary ${coverage.available<total?'partial':''}">${coverage.available} / ${total} 個顯示中樣本有對齊座標${coverage.available<total?'；其餘不外插':''}</p>`:'';
    return `<section class="plot-card" id="plot-${axis}"><div class="plot-header"><div><h4>${isAligned?'對齊時間':'原始時間'} · ${signalLabel}</h4>${calibration}</div><button type="button" data-zoom="${axis}">放大 ↗</button></div><div class="svg-container" id="svg-${axis}">${plot(item,axis,false,wide)}</div></section>`;
  }
  function fieldValue(value){
    // Serialized objects/lists from source records read as key-value lines instead of raw JSON text.
    if(typeof value==='string'&&/^\s*[\[{]/.test(value)){try{const parsed=JSON.parse(value);if(Array.isArray(parsed)&&parsed.every(v=>v===null||typeof v!=='object'))return esc(parsed.map(v=>v??'未提供').join('、')||'（空）');if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed)){const entries=Object.entries(parsed);return entries.length?`<dl class="kv">${entries.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v!==null&&typeof v==='object'?JSON.stringify(v):v??'未提供')}</dd>`).join('')}</dl>`:'（空）';}}catch(_){}}
    return value===''?'<span class="muted">（空）</span>':esc(value??'未提供');
  }
  function evidencePanel(item){
    if(!state.evidence)return '';const record=records(item).find(r=>r.ref===state.evidence);
    if(!record)return `<aside class="evidence-panel" id="evidence-panel" tabindex="-1" aria-live="polite"><div class="evidence-header"><div><span class="eyebrow">${esc(state.evidenceContext||'目前資料')}</span><h3>來源內容未提供</h3></div><button type="button" class="evidence-close" id="close-evidence">關閉來源</button></div><p>${records(item).length?'這筆引用沒有對應到本報告內嵌的可讀紀錄。':'本報告未提供可讀來源紀錄，無法在離線頁面中補出說明。'}</p><details><summary>完整紀錄與引用</summary><div class="technical-ref"><code>${esc(state.evidence)}</code><button type="button" data-copy="${esc(state.evidence)}">複製</button></div><p class="control-note">本報告僅保留引用，未包含內容。</p></details></aside>`;
    const fields=Array.isArray(record.fields)?record.fields:[],links=Array.isArray(record.links)?record.links:[],tech=Array.isArray(record.technical_refs)?record.technical_refs:[];
    return `<aside class="evidence-panel" id="evidence-panel" tabindex="-1" aria-live="polite"><div class="evidence-header"><div><span class="eyebrow">${esc(state.evidenceContext||'目前資料')}</span><h3>${esc(record.title||'可讀來源紀錄')}</h3></div><button type="button" class="evidence-close" id="close-evidence">關閉來源</button></div>${record.summary?`<p class="evidence-summary">${esc(record.summary)}</p>`:''}${links.length?`<section class="record-links"><h4>相關紀錄</h4>${links.map(link=>records(item).some(r=>r.ref===link.ref)?`<button type="button" data-record-ref="${esc(link.ref)}">${esc(link.label||'查看相關紀錄')}</button>`:`<div class="external-reference"><strong>${esc(link.label||'外部引用')}</strong><span>本報告僅保留引用，未包含內容。</span></div>`).join('')}</section>`:''}<details><summary>完整紀錄與引用</summary>${fields.length?`<dl class="evidence-fields">${fields.map(f=>`<div><dt>${esc(f.label||'欄位')}</dt><dd>${fieldValue(f.value)}</dd></div>`).join('')}</dl>`:''}${tech.length?tech.map(t=>`<div class="technical-ref"><span><strong>${esc(t.label||'技術引用')}</strong><code>${esc(t.value??'')}</code></span><button type="button" data-copy="${esc(t.value??'')}">複製</button></div>`).join(''):`<div class="technical-ref"><span><strong>紀錄識別碼</strong><code>${esc(record.ref)}</code></span><button type="button" data-copy="${esc(record.ref)}">複製</button></div>`}</details></aside>`;
  }
  function differentialSeries(curve){
    if(state.signal==='raw')return {rts:curve.raw_rts||[],intensities:curve.raw_intensities||[]};
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
      if(points)svg+=`<polyline data-kind="${state.signal}" data-decision="${esc(c.quality_flags?.successor_decision||'')}" clip-path="url(#differential-${side})" fill="none" stroke="${differentialColor(c)}" stroke-width="2" opacity=".75" points="${points}"><title>${esc(sampleName(c.sample_id))} · ${esc(c.quality_flags?.successor_decision||'unclassified')}</title></polyline>`;
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
    $('workspace-content').innerHTML=`<div class="detail-top"><div><p class="detail-eyebrow">CID-NL feature inclusion / identity relationship review</p><h2>${esc(item.title)} | Candidate ${esc(f.write_authorized_count)} / Existing ${esc(f.no_write_detected_baseline_preserved_count)}</h2><p class="detail-summary">${esc(f.sample_count)} samples · ${esc(f.source_peak_hypothesis_id)} → ${esc(f.successor_peak_hypothesis_id)}</p></div><span class="badge badge-rest">診斷曲線</span></div><section class="advanced-inspection" aria-label="Source / successor differential review"><div class="inspection-heading"><h3 id="inspection-title" tabindex="-1">${state.sample?esc(sampleName(state.sample)):'所有可見樣本'}</h3>${state.sample?'<button type="button" class="return-all" id="btn-unfocus">← 返回所有樣本</button>':''}</div><div class="selection-controls"><div class="control-group"><span class="control-label">訊號種類</span><select id="signal-select"><option value="smoothed" ${state.signal==='smoothed'?'selected':''}>預先計算 Gaussian15</option><option value="raw" ${state.signal==='raw'?'selected':''}>原始訊號</option></select></div></div><div class="sample-legend" aria-label="樣本曲線顯示">${roster}</div><div class="charts-grid"><section class="plot-card"><h4>Source · ${esc(f.source_peak_hypothesis_id)} · m/z ${mz(f.source_mz)} · RT ${drt(f.source_center_rt)}</h4><div class="svg-container">${differentialPlot(item,'source',sharedMax)}</div></section><section class="plot-card"><h4>Successor · ${esc(f.successor_peak_hypothesis_id)} · m/z ${mz(f.successor_mz)} · RT ${drt(f.successor_center_rt)}</h4><div class="svg-container">${differentialPlot(item,'successor',sharedMax)}</div></section><section class="plot-card"><h4>Same-sample max relationship · log-log</h4><div class="svg-container">${differentialScatter(item)}</div></section><section class="plot-card"><h4>Review notes</h4>${differentialNotes(f)}</section></div><p class="reading-hint">兩側使用相同絕對訊號上限；黑色虛線標示各 hypothesis 中心 RT。藍色：write authorized；橘色：baseline preserved；灰色：其他。</p></section>`;
  }
  function detail(){
    const item=items.find(i=>i.id===state.id);if(!item){const excluded=filtered.filter(isCollapsedExcluded).length;$('workspace-content').innerHTML=excluded?`<div class="empty-state"><strong>目前未選取項目</strong><p>左側有 ${excluded} 筆已排除候選；展開後可查看保存曲線與來源紀錄。</p></div>`:'<p class="empty-state">這份報告沒有符合的項目。</p>';return;}normalize(item);if(item.transition_review?.review_kind==='cid_nl_differential'){differentialDetail(item);return;}
    const graph=state.sample?`${sampleName(state.sample)} · 目前查看`:'所有可見樣本',selectedSignalLabel={raw:'原始訊號',baseline:'基線',smoothed:'平滑訊號',positive:'基線以上訊號'}[state.signal]||'訊號',signalLabel=selectedSignalLabel;
    const aligned=alignedAvailable(item);
    $('workspace-content').innerHTML=`<div class="detail-top"><div><p class="detail-eyebrow">${itemName(item)}${item.method_label?` · ${esc(item.method_label)}`:''}</p><h2>${esc(itemTitle(item))}</h2><p class="detail-reason">${esc(item.reason_label||'未記錄項目判定說明。')}</p></div><span class="badge badge-${esc(item.stratum)}">${esc(badge(item))}</span></div>${summary(item)}${evidencePanel(item)}<section class="advanced-inspection" aria-label="候選與積分"><section class="inspection-section" id="inspection-workspace"><div class="inspection-heading"><div class="inspection-title"><h3 id="inspection-title" tabindex="-1">${esc(graph)}</h3>${state.sample?'<button type="button" class="return-all" id="btn-unfocus">← 返回所有樣本</button>':''}</div><span class="focus-status">${state.sample?'候選與擷取':'曲線與積分'}</span></div><div class="selection-controls">${state.sample?`<div class="control-group"><span class="control-label">候選峰</span>${candidateControl(item)}</div><div class="control-group"><span class="control-label">擷取階段</span>${captureControl(item)}</div>`:''}<div class="control-group"><span class="control-label">訊號種類</span>${signalControl(item)}</div></div><div class="chart-toolbar"><div class="sample-legend" aria-label="樣本曲線顯示">${legend(item)}</div><label><input id="chk-bounds" type="checkbox" ${state.bounds?'checked':''}> 顯示積分範圍與峰頂</label></div><div class="analysis-layout ${state.evidence?'with-evidence':''}"><div class="chart-column"><div class="charts-grid ${aligned?'':'single'}">${plotCard(item,'raw',signalLabel,!aligned)}${aligned?plotCard(item,'aligned',signalLabel):''}</div>${aligned?'':plotCard(item,'aligned',signalLabel)}<section class="integration-card" id="integration-readout">${readout(item)}</section></div></div></section></section>${internalPairSupportPanel(item)}<section class="sample-section" aria-label="所有樣本的正式結果與支持紀錄"><div class="section-heading"><h3>所有樣本的正式結果與支持紀錄</h3></div>${sampleTable(item)}</section>`;
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
