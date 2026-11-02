/* Selection anchors and citations are supplied and validated by the local server. */
(() => {
  'use strict';
  const esc = x => String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const norm = x => String(x??'').replace(/\s+/g,' ').trim();
  const stages = {queued:'等待回答',retrieving:'定位与检索原文',explaining:'生成通俗解释',verifying:'核验回答依据'};
  let result=null, selected=null, candidate=null, current=null, timer=null, generation=0, opened=false, sending=false;
  let items=[];
  const $ = s => document.querySelector(s);
  async function request(path, data) {
    const response=await fetch('api/jobs/'+result.id+'/questions'+path, data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{cache:'no-store'});
    const value=await response.json(); if(!response.ok)throw Error(value.error||'问答请求失败');return value;
  }
  function shell() {
    if($('#qa-panel'))return;
    document.body.insertAdjacentHTML('beforeend',`<button id="qa-float" type="button" hidden>问一下</button><aside id="qa-panel" class="qa-panel" role="complementary" aria-label="选文提问" hidden><header><h2>选文提问</h2><button type="button" id="qa-close" aria-label="关闭解释面板">×</button></header><div class="qa-scroll"><p class="qa-muted">围绕这段文字提问，回答会核对对应论文。</p><blockquote id="qa-selection"></blockquote><div class="qa-presets"><button type="button" data-qa-prompt="请用通俗的话解释这段内容，并说明它在本文中的作用。">通俗解释</button><button type="button" data-qa-prompt="请用一个简单的教学例子解释这段内容。">举个例子</button><button type="button" data-qa-prompt="请解释这段内容中的关键术语。">解释术语</button></div><form id="qa-form"><label for="qa-question">你的问题 / 继续追问</label><textarea id="qa-question" maxlength="1000" rows="3" placeholder="例如：为什么需要这一步？" required></textarea><div class="qa-submit"><small>每次最多8次模型调用</small><button class="button primary small" type="submit">发送问题</button></div></form><p id="qa-error" role="alert"></p><section id="qa-answer" aria-live="polite"></section><details open><summary>这份阅读的问答记录</summary><div id="qa-history"></div></details></div></aside>`);
    $('#qa-close').onclick=()=>{opened=false;$('#qa-panel').hidden=true;$('#qa-float').hidden=true;};
    $('#qa-float').onpointerdown=e=>e.preventDefault();
    $('#qa-float').onclick=()=>open(candidate);
    $('#qa-form').onsubmit=e=>{e.preventDefault();send();};
    $('#qa-panel').addEventListener('click', e=>{
      const prompt=e.target.closest('[data-qa-prompt]'); if(prompt){$('#qa-question').value=prompt.dataset.qaPrompt;send();}
      const history=e.target.closest('[data-qa-id]'); if(history){current=items.find(q=>q.id===history.dataset.qaId);selected=current;open(current,false);render();}
      const back=e.target.closest('[data-qa-back]');if(back){const target=document.querySelector('[data-qa-block="'+current.block_id+'"]');if(target){target.scrollIntoView({behavior:'smooth',block:'center'});target.classList.add('qa-highlight');setTimeout(()=>target.classList.remove('qa-highlight'),1800);}else $('#qa-error').textContent='请切回原来的报告或证据页，再定位选文。';}
    });
    document.addEventListener('pointerup', capture);
    document.addEventListener('keyup',e=>{if(e.key==='Escape')$('#qa-close').click();else if(e.key==='Shift')capture();});
    window.addEventListener('scroll',()=>{$('#qa-float').hidden=true;},true);
  }
  function capture() {
    if(!result)return;
    const s=window.getSelection();if(!s||s.isCollapsed)return;
    const parent=n=>(n.nodeType===1?n:n.parentElement)?.closest('[data-qa-block]');
    const first=parent(s.anchorNode),last=parent(s.focusNode);
    if(!first||first!==last){$('#qa-float').hidden=true;return;}
    const text=norm(s.toString());if(text.length<2||text.length>1600)return;
    const block=result.selection_index.blocks.find(b=>b.id===first.dataset.qaBlock);
    if(!block||!block.text.includes(text))return;
    candidate={block_id:block.id, selection:text, version:result.selection_index.version};
    const box=s.getRangeAt(0).getBoundingClientRect(),button=$('#qa-float');
    button.style.left=Math.min(window.innerWidth-100,Math.max(8,box.right-75))+'px';
    button.style.top=Math.min(window.innerHeight-45,Math.max(8,box.bottom+5))+'px';button.hidden=false;
  }
  function open(value, fresh=true) {
    if(!value)return;
    if(fresh){current=null;$('#qa-question').value='';}
    selected=value;opened=true;$('#qa-panel').hidden=false;$('#qa-float').hidden=true;
    $('#qa-selection').textContent=value.selection;$('#qa-error').textContent='';render();$('#qa-question').focus();
  }
  function render() {
    if(!$('#qa-answer'))return;
    const q=current, busy=sending||(q&&['queued','running'].includes(q.status));
    $('#qa-form button').disabled=!!busy;
    document.querySelectorAll('[data-qa-prompt]').forEach(b=>b.disabled=!!busy);
    $('#qa-answer').innerHTML=!q?'':`<div class="qa-question">${esc(q.question)}</div>`+
      (q.status==='failed'?`<p class="qa-failure">${esc(q.error)}</p>`:q.status!=='completed'?`<p class="qa-working">${esc(stages[q.stage]||'正在回答')}…</p>`:
      `<h3>解释</h3>${q.points.map(p=>`<p>${esc(p.text)}</p><div class="qa-citations">${p.evidence.map(e=>e.upload_id&&/^[a-f0-9]{32}$/.test(e.upload_id)?`<a target="_blank" rel="noopener" href="api/uploads/${e.upload_id}/paper.pdf#page=${Number(e.page)||1}">${esc(e.filename)} · p.${Number(e.page)||1} ↗</a><details><summary>查看原文依据</summary><blockquote>${esc(e.quote)}</blockquote></details>`:'').join('')}</div>`).join('')}${q.example?`<h3>教学示例</h3><small>虚构推演 · 未实际运行</small><p>${esc(q.example)}</p>`:''}${q.notice?`<p class="qa-muted">${esc(q.notice)}</p>`:''}<button type="button" data-qa-back>回到选文</button><p class="qa-muted">${q.calls}次调用 · ${q.total_tokens} tokens · ${(q.models_used||[]).map(x=>esc(x.model)).join('、')}</p>`);
    $('#qa-history').innerHTML=items.map(q=>`<button type="button" data-qa-id="${q.id}"><strong>${esc(q.question)}</strong><small>${esc(q.selection.slice(0,60))} · ${q.status==='completed'?'已回答':q.status==='failed'?'未完成':'回答中'}</small></button>`).join('')||'<p class="qa-muted">问答将保存在本机，刷新后仍可查看。</p>';
  }
  async function refresh() {
    if(!result)return;
    const g=generation;
    try{const data=await request('');if(g!==generation)return;items=data.questions;
      if(current)current=items.find(q=>q.id===current.id)||current;
      else if(!selected&&items.length){current=items.at(-1);selected=current;}
      render();
    }catch(e){if(g===generation)$('#qa-error').textContent=e.message;}
    clearTimeout(timer);if(items.some(q=>['queued','running'].includes(q.status)))timer=setTimeout(refresh,1500);
  }
  async function send() {
    if(sending||!selected)return;
    const question=$('#qa-question').value.trim();if(question.length<2){$('#qa-error').textContent='请输入至少2个字的问题。';return;}
    const g=generation;
    sending=true;$('#qa-error').textContent='';render();
    try{const q=await request('',{block_id:selected.block_id,selection:selected.selection,version:selected.version,
      question,parent_id:current?.status==='completed'?current.id:null});
      if(g!==generation)return;current=q;$('#qa-question').value='';await refresh();
    }catch(e){if(g===generation)$('#qa-error').textContent=e.message;}
    finally{if(g===generation){sending=false;render();}}
  }
  function mount(value) {
    shell();
    if(!value){result=null;generation++;items=[];selected=current=null;clearTimeout(timer);$('#qa-panel').hidden=true;$('#qa-float').hidden=true;return;}
    const changed=result?.id!==value.id||result?.selection_index?.version!==value.selection_index?.version;
    result=value;
    if(changed){generation++;items=[];selected=current=null;sending=false;clearTimeout(timer);$('#qa-panel').hidden=true;opened=false;}
    const blocks=value.selection_index?.blocks||[];
    const byText=new Map();blocks.forEach(b=>{if(!byText.has(b.text))byText.set(b.text,[]);byText.get(b.text).push(b);});
    const counts=new Map();
    document.querySelectorAll('#reader-result .reader-markdown p,#reader-result .reader-markdown li,#reader-result .reader-markdown td,#reader-result .reader-markdown h2,#reader-result .reader-markdown h3,#reader-result .reader-markdown h4,#reader-result .reader-markdown blockquote,#reader-result .reader-evidence-list h3,#reader-result .reader-evidence-list blockquote').forEach(el=>{
      let text=norm(el.textContent);if(el.matches('.reader-evidence-list h3'))text=text.replace(/^\d{2}/,'');
      const matches=(byText.get(text)||[]).filter(b=>b.kind===(el.closest('.reader-evidence-list')?'evidence':'report'));
      const i=counts.get(text)||0,block=matches[Math.min(i,matches.length-1)];counts.set(text,i+1);
      if(!block)return;
      el.dataset.qaBlock=block.id;el.classList.add('qa-selectable');
      const button=document.createElement('button');button.type='button';button.className='qa-paragraph';button.textContent='问这一段';
      button.setAttribute('aria-label','提问：'+block.text.slice(0,35));button.onclick=()=>{open({block_id:block.id,selection:block.text.slice(0,1600),version:value.selection_index.version});};
      el.append(button);
    });
    const head=$('#reader-result .reader-tabs');
    if(head){const b=document.createElement('button');b.type='button';b.textContent='问答记录';b.onclick=()=>{opened=true;$('#qa-panel').hidden=false;$('#qa-selection').textContent=selected?.selection||'请在报告或证据中选择文字，或点击段落旁的“问这一段”。';render();refresh();};head.append(b);}
    refresh();
  }
  window.PaperTeamQA={mount};
  window.addEventListener('hashchange',()=>{if(location.hash!=='#read')mount(null);});
})();
