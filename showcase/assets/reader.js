/* Real local LangGraph jobs; only server events determine node status. */
(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const n = v => Number(v || 0).toLocaleString('en-US');
  const svg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 16V4m-4 4 4-4 4 4M4 15v5h16v-5"/></svg>';
  const names = {ingest:'解析论文',planner:'阅读规划',method_analyst:'方法分析',experiment_analyst:'实验分析',verifier:'证据核验',targeted_repair:'定向修订',paper_explainer:'逐篇讲解',reporter:'综合报告',single_reader:'单智能体阅读',reproducer:'复现模块（本轮跳过）'};
  const descriptions = {ingest:'提取文字 · 按页分块',planner:'制定检索与阅读计划',method_analyst:'方法、创新与适用条件',experiment_analyst:'数据集、指标与局限',verifier:'逐条核对引用与结论',targeted_repair:'补查未通过的结论',paper_explainer:'梳理问题与方法脉络',reporter:'整理报告与证据'};
  const statuses = {queued:'等待启动',running:'正在阅读',completed:'阅读完成',failed:'执行未完成',cancelled:'已停止'};
  const state = {files:[],question:'请用中文生成一份阅读报告：这篇论文解决什么问题，为什么要解决，方法如何工作，相比已有工作有哪些改进，实验如何设计、结果如何，有哪些局限？重要结论请给出原文证据和页码。',mode:'verified',explain:true,maxCalls:60,job:null,result:null,runtime:null,tab:'report',busy:false,error:'',history:[],timer:null};
  let savedId = '';
  let pollVersion = 0;
  try {savedId=localStorage.getItem('paperteam-active-job')||'';} catch {}
  async function api(path, options={}) {
    const r = await fetch('api/'+path,{cache:'no-store',...options});
    let data;try {data=await r.json();}catch {throw Error('未连接到本地阅读服务，请运行 start.bat 后打开 http://127.0.0.1:8600。');}
    if(!r.ok)throw Error(data.error||'请求失败，请稍后重试。');
    return data;
  }
  const post = (path,data) => api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const active = () => state.job && ['queued','running'].includes(state.job.status);
  const date = value => new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});
  function page() {
    return `<div id="reader-app"><section class="reader-hero"><div><span class="reader-eyebrow">PAPER READING / LIVE AGENTS</span><h1>从一篇论文，<br>到一份有据可查的阅读报告。</h1><p>上传 PDF，告诉智能体你想读懂什么。<br>在这里看它们规划、分析、核验，再把报告带走。</p></div><div class="reader-hero-mark"><span>PDF</span><i>→</i><span class="mark-agents">协作<br>核验</span><i>→</i><span>报告</span><small>LangGraph 驱动 · 真实执行进度</small></div></section>
    <div class="reader-layout"><div class="reader-main"><section class="reader-card"><div class="reader-section-title"><span class="reader-step">01</span><div><h2>添加你的论文</h2><p>文本型 PDF · 每篇不超过 20 MB · 最多 3 篇</p></div></div><label class="reader-drop" id="reader-drop" for="reader-file" tabindex="0">${svg}<strong>拖拽论文到这里，或点击选择文件</strong><span>单篇精读，或上传多篇做有证据的比较</span><input id="reader-file" type="file" accept="application/pdf,.pdf" multiple hidden></label><div id="reader-files"></div>
    <div class="reader-section-title reader-question-title"><span class="reader-step">02</span><div><h2>你想从论文里了解什么？</h2><p>具体的问题，能让报告更贴近你的需要</p></div></div><textarea id="reader-question" maxlength="4000" rows="5" aria-label="阅读要求">${esc(state.question)}</textarea><div class="reader-presets"><button type="button" data-reader="preset" data-value="general">全面精读</button><button type="button" data-reader="preset" data-value="method">重点看方法</button><button type="button" data-reader="preset" data-value="experiment">重点看实验</button></div>
    <details class="reader-options"><summary>阅读选项 <span>默认：多智能体 + 证据核验</span></summary><div class="reader-option-grid"><label>协作模式<select id="reader-mode"><option value="verified" ${state.mode==='verified'?'selected':''}>多智能体 + 证据核验（推荐）</option><option value="multi" ${state.mode==='multi'?'selected':''}>多智能体 · 仅检查引用格式</option><option value="single" ${state.mode==='single'?'selected':''}>单智能体 · 对照模式</option></select></label><label>模型调用上限<input id="reader-calls" type="number" min="10" max="200" value="${state.maxCalls}"></label></div><label class="reader-checkbox"><input id="reader-explain" type="checkbox" ${state.explain?'checked':''}> 生成详细讲解（核验模式生效，耗时更长）</label></details>
    <p class="reader-error" id="reader-error" role="alert">${esc(state.error)}</p><div class="reader-start-row"><span>论文文本会发送至你配置的模型服务</span><button class="button primary" id="reader-start" type="button" data-reader="start">开始阅读 <span>→</span></button></div></section><section id="reader-live" aria-live="polite"></section><section id="reader-result"></section></div>
    <aside class="reader-aside"><section class="reader-card reader-connection"><div class="reader-side-head"><span class="reader-dot"></span><h3>模型连接</h3><button data-reader="settings" type="button">配置 ↗</button></div><div id="reader-runtime"><p>正在检查本地阅读服务…</p></div><form id="reader-settings" hidden><label>Base URL<input name="base_url" type="url" placeholder="https://agi.tongji.edu.cn/v1" required></label><label>模型名称<input name="model" placeholder="DeepSeek-V4.1-Flash" required></label><label>API Key<input name="api_key" type="password" autocomplete="off" placeholder="首次配置必填，已配置可留空"></label><p>密钥保存在本机 .env，页面不会读回密钥。</p><button class="button primary small" type="submit">保存模型连接</button></form></section><section class="reader-card reader-delivery"><span class="reader-eyebrow">WHAT YOU GET</span><h3>读完之后，你会得到</h3><div><b>01</b><p><strong>一份中文阅读报告</strong><span>问题背景、方法脉络、实验与局限</span></p></div><div><b>02</b><p><strong>可以回到原文的证据</strong><span>引用片段、页码与核验结论</span></p></div><div><b>03</b><p><strong>完整的执行过程</strong><span>各节点状态、耗时与 token 用量</span></p></div><small>文本提取与模型输出存在局限，关键结论仍需回到原文判断。</small></section><section class="reader-card"><div class="reader-side-head"><h3>最近阅读</h3><button type="button" data-reader="history">刷新</button></div><div id="reader-history"><p class="reader-muted">完成的报告会保存在本机。</p></div></section><a class="reader-history-link" href="#overview">查看项目已有的研究记录 ↗</a></aside></div></div>`;
  }
  function error(message) {state.error=message;const el=$('#reader-error');if(el)el.textContent=message;}
  function updateFiles() {
    const el=$('#reader-files');if(!el)return;
    el.innerHTML=state.files.map(f=>`<div class="reader-file-row"><span class="reader-pdf-icon">PDF</span><div><strong>${esc(f.filename)}</strong><small>${(f.bytes/1024/1024).toFixed(2)} MB · 已上传到本机</small></div><button type="button" data-reader="remove" data-id="${f.id}" aria-label="移除 ${esc(f.filename)}">×</button></div>`).join('');
    updateStart();
  }
  function updateStart() {
    const el=$('#reader-start');if(!el)return;
    el.disabled=state.busy||active()||!state.files.length||!state.runtime?.configured||!state.runtime?.dependencies_ready;
    el.innerHTML=state.busy?'正在上传…':active()?'智能体正在阅读…':'开始阅读 <span>→</span>';
  }
  function updateRuntime() {
    const el=$('#reader-runtime');if(!el)return;
    const r=state.runtime;
    const footer=$('#footer-meta');if(footer)footer.textContent=(r?.model||'本地 Agent 服务')+' · LangGraph 实时阅读';
    el.innerHTML=r?`<strong class="reader-model">${esc(r.model||'尚未配置模型')}</strong><span class="reader-api-base">${esc(r.base_url||'请先添加 API 连接')}</span><span class="reader-connect-status ${r.configured&&r.dependencies_ready?'ready':''}">${r.configured&&r.dependencies_ready?'● 已就绪 · 可以开始阅读':!r.dependencies_ready?'● 依赖未就绪 · 请用 start.bat 启动':'● 等待配置 API Key'}</span>`:'<p>当前是静态展示页。请在项目目录双击 <strong>start.bat</strong> 启动可交互的阅读网站。</p>';
    if(r&&!r.configured){$('#reader-settings').hidden=false;fillSettings();}
    updateStart();
  }
  function fillSettings(){const form=$('#reader-settings');if(!form)return;form.elements.base_url.value=state.runtime?.base_url||'https://agi.tongji.edu.cn/v1';form.elements.model.value=state.runtime?.model||'DeepSeek-V4.1-Flash';}
  async function upload(files) {
    if(active())return error('请等待当前阅读完成后再上传。');
    if(state.busy)return;
    const selected=[...files];if(selected.length+state.files.length>3)return error('每次最多阅读 3 篇论文。');
    state.busy=true;error('');updateStart();
    try {
      for(const file of selected){
        if(!file.name.toLowerCase().endsWith('.pdf')||file.size>20*1024*1024)throw Error('请上传不超过 20 MB 的 PDF 文件。');
        const meta=await api('uploads',{method:'POST',headers:{'Content-Type':'application/pdf','X-Filename':encodeURIComponent(file.name)},body:file});
        state.files.push(meta);updateFiles();
      }
    }catch(e){error(e.message);}finally{state.busy=false;updateStart();}
  }
  function updateLive(){
    const el=$('#reader-live');if(!el)return;
    const j=state.job;if(!j){el.innerHTML='';return;}
    const nodes=j.mode==='single'?['ingest','single_reader','verifier','reporter']:['ingest','planner','method_analyst','experiment_analyst','verifier','targeted_repair',...(j.explain?['paper_explainer']:[]),'reporter'];
    const duration=Math.max(0,Math.round((new Date(j.finished_at||Date.now())-new Date(j.started_at||j.created_at))/1000));
    el.innerHTML=`<div class="reader-card"><div class="reader-live-heading"><div><span class="reader-eyebrow">03 / LIVE WORKFLOW</span><h2><span class="reader-job-dot ${active()?'pulse':''}"></span>${statuses[j.status]||esc(j.status)}</h2></div>${active()?'<button class="button subtle small" data-reader="stop">停止阅读</button>':'<button class="button subtle small" data-reader="new">新建阅读</button>'}</div><div class="reader-live-paper">${j.files.map(f=>esc(f.filename)).join(' · ')}</div><div class="reader-node-grid">${nodes.map(key=>{let node=j.nodes[key]||{};const skip=key==='targeted_repair'&&!j.nodes[key]&&j.status==='completed';const status=skip?'skipped':node.status||'waiting';return `<div class="reader-node ${status}"><span class="reader-node-symbol">${status==='completed'?'✓':status==='running'?'<i class="spinner"></i>':skip?'−':'·'}</span><strong>${esc(names[key])}</strong><small>${esc(descriptions[key]||'独立完成阅读分析')}</small><em>${({completed:'已完成',running:'执行中',failed:'未完成',cancelled:'已停止',skipped:'无需修订',waiting:'等待执行'})[status]}${node.rounds>1?' · 第 '+node.rounds+' 轮':''}</em></div>`;}).join('')}</div><div class="reader-run-stats"><div><strong>${Math.floor(duration/60)}<small>分</small>${duration%60}<small>秒</small></strong><span>实际耗时</span></div><div><strong>${n(j.calls)}</strong><span>已完成 API 调用</span></div><div><strong>${n(j.total_tokens)}</strong><span>已返回 token 用量</span></div><div><strong>${n(j.accepted_claims??state.result?.summary?.accepted_claims)}</strong><span>已接受的结论</span></div></div>${j.error?'<div class="reader-failure">'+esc(j.error)+'</div>':''}<details class="reader-live-events"><summary>查看实际执行事件 · ${j.events?.length||0} 条</summary><div class="reader-log">${(j.events||[]).map(e=>`<div><time>${date(e.time)}</time><span>${esc(names[e.node]||e.node)}</span><b class="${e.status}">${e.status==='running'?'开始执行':'执行完成'}</b></div>`).join('')}</div></details><p class="reader-progress-note">${active()?'状态来自 LangGraph 的真实执行事件。模型响应期间会等待，不预估完成百分比。':j.status==='completed'?'报告和运行记录已保存到本机，下方可阅读和下载。':'本次任务保留了已完成的日志；可修改配置或阅读要求后重试。'}</p></div>`;
  }
  // Escape all text before formatting; raw HTML and executable URLs are never rendered.
  function inline(text){return esc(text).replace(/\[\^(S\d+)\]/g,'<button class="reader-reference" type="button" data-reader="source" data-source="$1">[$1]</button>').replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');}
  function methodDiagram(text){
    const nodes=[...text.matchAll(/^\s*([A-Za-z0-9_-]+)\["(.*)"\]\s*$/gm)].slice(0,25).map(m=>({id:m[1],label:m[2]}));
    const edges=[...text.matchAll(/^\s*([A-Za-z0-9_-]+)\s*-->\|"(.*)"\|\s*([A-Za-z0-9_-]+)\s*$/gm)].map(m=>({from:m[1],to:m[3],label:m[2]}));
    if(!nodes.length)return '<pre><code>'+esc(text)+'</code></pre>';
    const ids=new Set(nodes.map(n=>n.id)),incoming=new Set(edges.map(e=>e.to)),level=new Map(),queue=nodes.filter(n=>!incoming.has(n.id)).map(n=>n.id);
    if(!queue.length)queue.push(nodes[0].id);queue.forEach(id=>level.set(id,0));
    for(let k=0;k<queue.length;k++){const id=queue[k];for(const edge of edges.filter(e=>e.from===id&&ids.has(e.to))){if(!level.has(edge.to)){level.set(edge.to,level.get(id)+1);queue.push(edge.to);}}}
    nodes.forEach(n=>{if(!level.has(n.id))level.set(n.id,0);});
    const counts=new Map(),positions=new Map();nodes.forEach(n=>{const col=level.get(n.id),row=counts.get(col)||0;counts.set(col,row+1);positions.set(n.id,{x:20+col*145,y:30+row*105});});
    const width=145*(Math.max(...level.values())+1)+15,height=105*Math.max(...counts.values())+45;
    const edgeSvg=edges.filter(e=>positions.has(e.from)&&positions.has(e.to)).map(e=>{const a=positions.get(e.from),b=positions.get(e.to),back=b.x<=a.x,sx=back?a.x:a.x+120,tx=back?b.x+120:b.x,sy=a.y+32,ty=b.y+32;const d=back?`M${sx},${sy} C${sx-28},${sy+65} ${tx+28},${ty+65} ${tx},${ty}`:`M${sx},${sy} C${sx+16},${sy} ${tx-16},${ty} ${tx},${ty}`;return `<path d="${d}" marker-end="url(#method-arrow)"><title>${esc(e.label)}</title></path>`;}).join('');
    const nodeSvg=nodes.map(n=>{const p=positions.get(n.id),label=n.label.replace(/&quot;/g,'"').replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>'),chars=Array.from(label),parts=[];for(let k=0;k<Math.min(chars.length,44);k+=11)parts.push(chars.slice(k,k+11).join(''));return `<g><title>${esc(label)}</title><rect x="${p.x}" y="${p.y}" width="120" height="66" rx="7"/><text text-anchor="middle" x="${p.x+60}" y="${p.y+18}">${parts.map((part,k)=>`<tspan x="${p.x+60}" dy="${k?13:0}">${esc(part)}</tspan>`).join('')}</text></g>`;}).join('');
    return `<div class="reader-method-diagram"><svg role="img" aria-label="根据已核验步骤整理的论文方法图" viewBox="0 0 ${width} ${height}"><defs><marker id="method-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7z"/></marker></defs>${edgeSvg}${nodeSvg}</svg><small>来自报告中已核验的方法步骤；鼠标停留在连接线上可查看关系说明。</small></div>`;
  }
  function markdown(raw){
    const lines=String(raw).split('\n');let out='',code=false,list=false,table=false,diagram=false,codeLines=[];
    const close=()=>{if(list){out+='</ul>';list=false;}if(table){out+='</tbody></table></div>';table=false;}};
    for(const line of lines){
      if(/^```/.test(line)){close();if(code){out+=diagram?methodDiagram(codeLines.join('\n')):'</code></pre>';code=false;diagram=false;codeLines=[];}else{code=true;diagram=/^```mermaid/.test(line);if(!diagram)out+='<pre><code>';}continue;}
      if(code){if(diagram)codeLines.push(line);else out+=esc(line)+'\n';continue;}
      if(/^\s*\|.*\|\s*$/.test(line)){
        if(/^\s*\|[\s:|\-]+\|\s*$/.test(line))continue;
        if(list){out+='</ul>';list=false;}if(!table){out+='<div class="reader-table-wrap"><table><tbody>';table=true;}
        out+='<tr>'+line.trim().slice(1,-1).split('|').map(c=>'<td>'+inline(c.trim())+'</td>').join('')+'</tr>';continue;
      }
      if(!line.trim()){close();continue;}
      const source=line.match(/^\[\^(S\d+)\]:\s*(.*)/);if(source){close();out+=`<p id="reader-source-${source[1]}" class="reader-source"><strong>[${source[1]}]</strong> ${inline(source[2])}</p>`;continue;}
      const heading=line.match(/^(#{1,6})\s+(.*)/);if(heading){close();const level=Math.min(heading[1].length+1,6);out+=`<h${level}>${inline(heading[2])}</h${level}>`;continue;}
      if(/^[-*]\s/.test(line)||/^\d+\.\s/.test(line)){if(table){close();}if(!list){out+='<ul>';list=true;}out+='<li>'+inline(line.replace(/^([-*]|\d+\.)\s+/,''))+'</li>';continue;}
      close();out+=/^\s*>/.test(line)?'<blockquote>'+inline(line.replace(/^\s*>\s?/,''))+'</blockquote>':'<p>'+inline(line)+'</p>';
    }
    close();if(code)out+='</code></pre>';return out;
  }
  function updateResult(){
    const el=$('#reader-result');if(!el)return;
    const r=state.result;if(!r||r.id!==state.job?.id){el.innerHTML='';window.PaperTeamQA?.mount(null);return;}
    const body=state.tab==='report'?`<article class="reader-markdown">${markdown(r.report)}</article>`:state.tab==='evidence'?`<div class="reader-evidence-list">${(r.claims||[]).map((c,i)=>`<article><h3><span>${String(i+1).padStart(2,'0')}</span>${esc(c.text)}</h3>${c.evidence.map(e=>{const paper=(r.papers||[]).find(p=>p.paper_id===c.paper_id);const file=r.files.find(f=>f.filename===paper?.filename)||r.files[0];return `<blockquote>${esc(e.quote)}</blockquote><a href="api/uploads/${file.id}/paper.pdf#page=${e.page||1}" target="_blank" rel="noopener">${esc(paper?.filename||'论文原文')} · 第 ${e.page||'?'} 页 ↗</a>`;}).join('')}</article>`).join('')||'<p>本次没有通过核验的结论。</p>'}</div>`:`<div class="reader-log">${r.events.map(e=>`<div><time>${date(e.time)}</time><span>${esc(names[e.node]||e.node)}</span><b class="${e.status}">${e.status==='running'?'开始执行':'执行完成'}</b></div>`).join('')}</div>`;
    el.innerHTML=`<section class="reader-card reader-report-card"><div class="reader-report-head"><div><span class="reader-eyebrow">YOUR READING REPORT</span><h2>阅读报告已生成</h2><p>${esc(r.summary?.model)} · ${r.summary?.accepted_claims||0} 条结论通过核验 · ${r.summary?.rejected_claims||0} 条未接受</p></div><a class="button primary small" href="api/jobs/${r.id}/report.md" download>下载报告 ↓</a></div><div class="reader-tabs" role="tablist">${[['report','阅读报告'],['evidence','原文证据'],['logs','执行日志']].map(([key,name])=>`<button type="button" role="tab" aria-selected="${state.tab===key}" class="${state.tab===key?'active':''}" data-reader="tab" data-tab="${key}">${name}</button>`).join('')}<button type="button" data-reader="print">打印 / 保存 PDF ↗</button></div>${body}</section>`;
    window.PaperTeamQA?.mount(r);
  }
  function updateHistory(){const el=$('#reader-history');if(!el)return;el.innerHTML=state.history.map(j=>`<button type="button" class="reader-history-item" data-reader="job" data-id="${j.id}"><strong>${esc(j.files.map(f=>f.filename).join(' · '))}</strong><span>${date(j.created_at)} <i>·</i> ${statuses[j.status]||esc(j.status)}</span></button>`).join('')||'<p class="reader-muted">还没有阅读记录。上传一篇论文试试。</p>';}
  async function history(){try{state.history=(await api('jobs')).jobs;updateHistory();}catch{}}
  async function poll(){
    clearTimeout(state.timer);
    if(!state.job?.id)return;
    try{
      const id=state.job.id,version=++pollVersion;const job=await api('jobs/'+id);if(version!==pollVersion||state.job?.id!==id)return;state.job=job;updateLive();updateStart();
      if(state.job.status==='completed'&&state.result?.id!==state.job.id){state.result=await api('jobs/'+state.job.id+'/result');updateResult();history();}
      if(['failed','cancelled'].includes(state.job.status))history();
    }catch(e){if($('#reader-app'))error('获取执行状态失败：'+e.message);}
    if(active()&&$('#reader-app'))state.timer=setTimeout(poll,1500);
  }
  async function selectJob(id){state.result=null;state.job={id};savedId=id;try{localStorage.setItem('paperteam-active-job',id);}catch{}await poll();updateResult();}
  async function start(){
    if(active())return;state.busy=true;error('');updateStart();
    try{
      const job=await post('jobs',{upload_ids:state.files.map(f=>f.id),question:state.question,mode:state.mode,max_calls:state.maxCalls,explain:state.explain});
      state.result=null;state.tab='report';await selectJob(job.id);history();$('#reader-live')?.scrollIntoView({behavior:'smooth',block:'start'});
    }catch(e){error(e.message);}finally{state.busy=false;updateStart();}
  }
  function mount(){
    clearTimeout(state.timer);updateFiles();updateLive();updateResult();updateHistory();
    const app=$('#reader-app');if(!app)return;
    const drop=$('#reader-drop');drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('dragging');});drop.addEventListener('dragleave',()=>drop.classList.remove('dragging'));drop.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('dragging');upload(e.dataTransfer.files);});drop.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('#reader-file').click();}});
    app.addEventListener('change',e=>{const t=e.target;if(t.id==='reader-file'){upload(t.files);t.value='';}if(t.id==='reader-mode')state.mode=t.value;if(t.id==='reader-explain')state.explain=t.checked;if(t.id==='reader-calls')state.maxCalls=Number(t.value);});
    app.addEventListener('input',e=>{if(e.target.id==='reader-question')state.question=e.target.value;});
    app.addEventListener('click',async e=>{
      const el=e.target.closest('[data-reader]');if(!el)return;const action=el.dataset.reader;
      if(action==='start')start();
      if(action==='settings'){const form=$('#reader-settings');form.hidden=!form.hidden;fillSettings();}
      if(action==='remove'&&!active()){state.files=state.files.filter(f=>f.id!==el.dataset.id);updateFiles();}
      if(action==='history')history();if(action==='job')selectJob(el.dataset.id);
      if(action==='tab'){state.tab=el.dataset.tab;updateResult();}
      if(action==='new'){state.job=null;state.result=null;savedId='';clearTimeout(state.timer);try{localStorage.removeItem('paperteam-active-job');}catch{}updateLive();updateResult();updateStart();$('#reader-drop').scrollIntoView({behavior:'smooth'});}
      if(action==='stop'){try{state.job=await post('jobs/'+state.job.id+'/stop',{});updateLive();updateStart();history();}catch(err){error(err.message);}}
      if(action==='source')document.getElementById('reader-source-'+el.dataset.source)?.scrollIntoView({behavior:'smooth',block:'center'});
      if(action==='print'){state.tab='report';updateResult();window.print();}
      if(action==='preset'){state.question=({general:'请用中文生成一份完整阅读报告，解释论文的问题背景、核心方法、创新点、实验设计与结果、局限和可改进方向。重要结论给出原文证据和页码。',method:'请重点解释论文方法：输入和输出是什么、各步骤如何衔接、关键算法或公式怎样理解、相比已有方法改进在哪里、适用条件和局限是什么。请附原文证据和页码。',experiment:'请重点分析论文实验：数据集和划分、基线、评价指标、关键结果、消融实验、可复现条件。区分作者报告和已验证事实，给出原文证据和页码。'})[el.dataset.value];$('#reader-question').value=state.question;}
    });
    $('#reader-settings').addEventListener('submit',async e=>{e.preventDefault();error('');const form=e.target;const button=form.querySelector('button');button.disabled=true;try{state.runtime=await post('settings',Object.fromEntries(new FormData(form)));form.elements.api_key.value='';form.hidden=true;updateRuntime();}catch(err){error(err.message);}finally{button.disabled=false;}});
    api('runtime').then(r=>{state.runtime=r;updateRuntime();}).catch(()=>{state.runtime=null;updateRuntime();});
    history();if(state.job?.id)poll();else if(savedId)selectJob(savedId);
  }
  window.PaperTeamReader={page,mount,markdown};
})();
