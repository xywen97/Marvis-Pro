'use strict';
const $ = s => document.querySelector(s);
let token = new URLSearchParams(location.hash.slice(1)).get('token') || sessionStorage.getItem('marvis-token') || '';
if (token) sessionStorage.setItem('marvis-token', token);
history.replaceState(null, '', location.pathname);
let state = null, session = null, page = 'chat', approvalId = null, pathAction = null, refreshing = false;
let liveConnected = false, liveAbort = null, stateUpdates = Promise.resolve(), messageVersion = 0;
const signatures = {};
const statuses = {queued:'排队中',running:'执行中',waiting_approval:'等待确认',completed:'已完成',failed:'失败',cancelled:'已停止',interrupted:'已中断'};
const busy = t => ['queued','running','waiting_approval'].includes(t.status);
function el(tag, cls, text) { const e = document.createElement(tag); if(cls)e.className=cls; if(text!==undefined)e.textContent=text; return e; }
function btn(text, callback, cls='') { const b=el('button',cls,text); b.type='button'; b.onclick=()=>safe(callback); return b; }
function toast(text) { $('#toast').textContent=text; $('#toast').classList.remove('hidden'); clearTimeout(toast.timer); toast.timer=setTimeout(()=>$('#toast').classList.add('hidden'),4500); }
async function safe(fn) { try { return await fn(); } catch(e) { toast(e.message); } }
async function api(path, data) { const r=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{'X-Marvis-Token':token,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)}); const v=await r.json(); if(!r.ok)throw new Error(v.error||'请求失败');return v; }
function richText(text) {
 return MarvisMarkdown.render(text, {remoteImages:state?.settings.mode!=='local', loadImage:async(path, signal)=>{
  const response=await fetch('/api/image?path='+encodeURIComponent(path),{headers:{'X-Marvis-Token':token},signal});
  const value=await response.json();if(!response.ok)throw new Error(value.error);
  return new Blob([Uint8Array.from(atob(value.data),c=>c.charCodeAt(0))],{type:value.mime});
 }});
}
function showPage(next) { page=next; document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id===next));document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===next));$('#page-title').textContent={chat:'工作台',knowledge:'知识库',tasks:'任务中心',workflows:'自动化',settings:'设置'}[next]; if(next==='tasks'&&state)renderTasks();if(next==='knowledge')safe(loadKnowledge);if(next==='settings'&&state)fillSettings(); }
function changed(name, value) { const sig=JSON.stringify(value); if(Object.prototype.hasOwnProperty.call(signatures,name)&&signatures[name]===sig)return false; signatures[name]=sig;return true; }
function acceptState(value) {
 stateUpdates=stateUpdates.catch(()=>{}).then(async()=>{
  state=value;$('#connection').textContent='● 已连接';$('#connection').style.color='';renderState();
  if(session&&changed('messages-session',[session,state.sessions.find(s=>s.id===session)?.active_leaf,state.tasks.filter(t=>t.session_id===session).map(t=>[t.id,t.status,t.updated])]))await renderMessages();
 });
 return stateUpdates;
}
async function refresh() { if(refreshing)return;refreshing=true;try { await acceptState(await api('state')); }catch(e){$('#connection').textContent='● 连接中断';$('#connection').style.color='#c97070';}finally{refreshing=false;} }
async function watchLive() {
 while(!liveAbort?.signal.aborted) {
  liveAbort=new AbortController();
  try {
   const response=await fetch('/api/live',{headers:{'X-Marvis-Token':token,Accept:'text/event-stream'},signal:liveAbort.signal});
   if(!response.ok||!response.body?.getReader)throw new Error('实时连接不可用');
   const reader=response.body.getReader(), decoder=new TextDecoder();let buffer='';
   while(true){
    const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});
    let boundary;
    while((boundary=buffer.indexOf('\n\n'))>=0){
     const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
     const data=frame.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');
     if(data){liveConnected=true;await acceptState(JSON.parse(data));}
    }
   }
  } catch(e) {
   liveConnected=false;if(liveAbort.signal.aborted)return;
   await new Promise(resolve=>setTimeout(resolve,1000));
  } finally { liveConnected=false; }
 }
}
window.addEventListener('pagehide',()=>liveAbort?.abort());
function renderState() {
 $('#knowledge-badge').textContent=state.knowledge_count;$('#mode-toggle').textContent=state.settings.mode==='local'?'◉ 本地模式':'◉ 效率模式';$('#model-label').textContent=state.settings.offline?'离线工具模式':state.settings.mode==='local'?'Ollama · qwen3:latest':state.settings.model.model||'模型未配置';
 if(!$('#index-path').value)$('#index-path').value=state.settings.roots[0];
 if(changed('sessions', [state.sessions,session])) {const box=$('#sessions');box.replaceChildren();state.sessions.forEach(s=>box.append(btn('◌  '+s.title,async()=>{session=s.id;showPage('chat');await renderMessages();renderState();},'session'+(session===s.id?' selected':''))));}
 const active=state.tasks.filter(busy), current=active.find(t=>t.status!=='queued');
 if(changed('agents',current?[current.id,current.status,current.events.filter(e=>e.kind==='tool_start').slice(-1)[0]?.content.agent]:null)) { const box=$('#agents');box.replaceChildren();const last=current?.events.filter(e=>e.kind==='tool_start').slice(-1)[0];state.agents.forEach(a=>{let working=!!current&&(a.id==='main'||a.id===last?.content.agent);const row=el('div','agent-row'+(working?' busy':''));row.append(el('div','agent-avatar',a.icon));const info=el('div','agent-info');info.append(el('strong','',a.name),el('small','',a.role));row.append(info,el('span','agent-status',working?'工作中':'待命'));box.append(row);}); }
 const visible=active.find(t=>t.session_id===session), indicator=$('#active-task');indicator.classList.toggle('hidden',!visible);if(visible){indicator.replaceChildren(el('span','',statuses[visible.status]+' · '+visible.prompt.slice(0,40)),btn('停止',()=>api('cancel',{id:visible.id})));}
 if(page==='tasks'&&changed('tasks',state.tasks))renderTasks();
 if(changed('workflows',state.workflows))renderWorkflows();
 const pending=state.tasks.find(t=>t.approval);
 if(pending&&approvalId!==pending.id){approvalId=pending.id;$('#approval-plan').textContent=JSON.stringify(pending.approval,null,2);if(!$('#approval-dialog').open)$('#approval-dialog').showModal();}
 if(!pending&&approvalId){approvalId=null;$('#approval-dialog').close();}
}
let followTail=true, scrollFrame=null;
function scrollTail() {
 if(!followTail||scrollFrame)return;
 const tick=()=>{
  if(!followTail){scrollFrame=null;return;}
  const box=$('#chat-scroll'), target=box.scrollHeight-box.clientHeight, gap=target-box.scrollTop;
  box.scrollTop=gap<2?target:box.scrollTop+Math.max(1,gap*.3);
  if(gap>2)scrollFrame=requestAnimationFrame(tick);else scrollFrame=null;
 };
 scrollFrame=requestAnimationFrame(tick);
}
$('#chat-scroll').addEventListener('wheel',event=>{if(event.deltaY<0)followTail=false;},{passive:true});
$('#chat-scroll').addEventListener('touchstart',()=>{followTail=false;},{passive:true});
$('#chat-scroll').addEventListener('scroll',()=>{
 if(!scrollFrame){const box=$('#chat-scroll');followTail=box.scrollHeight-box.scrollTop-box.clientHeight<80;}
},{passive:true});
document.addEventListener('agent-content-resize',scrollTail);
async function renderMessages() {
 const currentSession=session, version=++messageVersion;
 const messages=session?await api('messages?session='+encodeURIComponent(session)):[];
 if(version!==messageVersion||session!==currentSession)return;
 const tasks=new Map((state?.tasks||[]).map(t=>[t.id,t]));
 const box=$('#messages');$('#welcome').classList.toggle('hidden',messages.length>0);
 if(box.dataset.session!==String(session)){
  AgentProgress.release(box);box.replaceChildren();box.dataset.session=String(session);followTail=true;
 }
 const specs=[];
 for(const message of messages){
  specs.push({key:'message-'+message.id,make:()=>{
   const row=el('div','message '+message.role),body=el('div','message-body');
   body.append(el('div','message-heading',message.role==='user'?'你':'MarvisXY'),el('div','message-text'));
   row.append(el('div','message-avatar',message.role==='user'?'你':'✦'),body);return row;
  },update:row=>{
   const content=row.querySelector('.message-text');
   if(message.role==='assistant')AgentProgress.text(content,message.content);
   else if(content.textContent!==message.content)content.replaceChildren(el('div','message-content',message.content));
  }});
  if(message.role==='user'&&tasks.has(message.task_id)){
   const task=tasks.get(message.task_id);
   specs.push({key:'trace-'+task.id,make:()=>el('div','agent-progress'),update:root=>AgentProgress.updateTrace(root,task)});
  }
 }
 AgentProgress.sync(box,specs);scrollTail();
}
async function submit(prompt, action) { const result=await api('tasks',{prompt,session_id:session,action});session=result.session_id;showPage('chat');await renderMessages();await refresh(); }
function renderTasks() { const box=$('#task-list');box.replaceChildren();if(!state.tasks.length){box.append(el('div','empty','还没有任务。\n从工作台发送第一条指令。'));return;}for(const t of state.tasks){const card=el('div','task-card');card.append(el('h3','',t.prompt));const meta=el('div','task-meta');meta.append(el('span','',new Date(t.created*1000).toLocaleString()),el('span','status '+t.status,statuses[t.status]));card.append(meta);const controls=el('div','task-actions');controls.append(btn('打开对话',async()=>{session=t.session_id;showPage('chat');await renderMessages();}));if(busy(t))controls.append(btn('停止任务',()=>api('cancel',{id:t.id})));if(t.approval)controls.append(btn('查看待确认操作',()=>{approvalId=t.id;$('#approval-plan').textContent=JSON.stringify(t.approval,null,2);$('#approval-dialog').showModal();}));card.append(controls);const details=el('details');details.append(el('summary','',`执行记录 · ${t.events.length} 步`));for(const e of t.events){const step=el('div','event');step.append(el('strong','',e.kind+' · '+new Date(e.created*1000).toLocaleTimeString()),el('pre','',JSON.stringify(e.content,null,2)));details.append(step);}card.append(details);box.append(card);} }
function renderWorkflows(){const box=$('#workflow-list');box.replaceChildren();if(!state.workflows.length){box.append(el('div','empty','选择模板，或创建你的第一个自动化工作流。'));return;}for(const w of state.workflows){const row=el('div','workflow-row');row.append(el('h3','',w.name),el('p','hint',w.prompt));row.append(el('p','hint',w.enabled?`每 ${w.interval/60} 分钟 · 下次执行 ${new Date(w.next_run*1000).toLocaleString()}`:'已暂停'));const controls=el('div','task-actions');controls.append(btn('立即运行',async()=>{const t=await api('workflow-action',{id:w.id,action:'run'});session=t.session_id;showPage('chat');await renderMessages();await refresh();}),btn(w.enabled?'暂停':'恢复',async()=>{await api('workflow-action',{id:w.id,action:'toggle'});await refresh();}),btn('删除',async()=>{await api('workflow-action',{id:w.id,action:'delete'});await refresh();}));row.append(controls);box.append(row);} }
let knowledgeTimer;
async function loadKnowledge(){const result=await api('knowledge?q='+encodeURIComponent($('#knowledge-query').value));const filter=$('#knowledge-filter').value;const files=result.files.filter(f=>!filter||f.category===filter);const box=$('#knowledge-list');box.replaceChildren();if(!files.length){box.append(el('div','empty','这里还没有匹配的文件。\n添加目录并建立索引，或换一个搜索关键词。'));return;}for(const f of files){const row=el('div','file-row');row.append(el('span','file-icon',f.category==='图片'?'▧':'▤'));const info=el('div','file-info');info.append(el('strong','',f.name),el('small','',f.path),el('small','',`${f.category} · ${(f.size/1024).toFixed(1)} KB · ${new Date(f.modified*1000).toLocaleDateString()}`));row.append(info);if(f.category!=='图片')row.append(btn('读取',()=>submit('读取 '+f.name,{tool:'file_read',arguments:{path:f.path}})));else row.append(btn('OCR',()=>submit('识别图片文字 '+f.name,{tool:'image_ocr',arguments:{path:f.path}})));box.append(row);} }
function fillSettings(){const f=$('#settings-form');f.elements.mode.value=state.settings.mode;f.elements.base_url.value=state.settings.model.base_url||'http://127.0.0.1:11434/v1';f.elements.model.value=state.settings.model.model||'qwen3:latest';f.elements.roots.value=state.settings.roots.join('\n');}
function pathDialog(action,title,defaultValue,hint){pathAction=action;$('#path-title').textContent=title;$('#path-hint').textContent=hint||'请输入已授权目录内的完整路径。';$('#path-value').value=defaultValue||state?.settings.roots[0]||'';$('#path-query').value='';$('#path-query').classList.toggle('hidden',action!=='files');$('#path-dialog').showModal();$('#path-value').focus();}
$('#chat-form').onsubmit=e=>{e.preventDefault();const p=$('#prompt').value.trim();if(!p)return;safe(async()=>{await submit(p);$('#prompt').value='';});};
$('#prompt').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#chat-form').requestSubmit();}};
$('#new-chat').onclick=()=>safe(async()=>{const s=await api('sessions',{});session=s.id;showPage('chat');await renderMessages();await refresh();$('#prompt').focus();});
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>showPage(b.dataset.page));
document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>safe(()=>submit(b.dataset.prompt)));
document.querySelectorAll('[data-quick]').forEach(b=>b.onclick=()=>{const a=b.dataset.quick;if(a==='system')safe(()=>submit('检查电脑状态',{tool:'system_info',arguments:{}}));else if(a==='workflow')showPage('workflows');else pathDialog(a,a==='files'?'查找本地文件':'整理目录',state?.settings.roots[0]);});
$('#attach').onclick=()=>pathDialog('attach','添加文件路径','');
$('#path-cancel').onclick=()=>$('#path-dialog').close();
$('#path-form').onsubmit=e=>{e.preventDefault();const path=$('#path-value').value.trim();if(pathAction==='attach'){$('#prompt').value+=' "'+path+'"';$('#path-dialog').close();return;}safe(async()=>{const action=pathAction==='files'?{tool:'file_list',arguments:{path,query:$('#path-query').value,content:true}}:{tool:'file_organize',arguments:{path}};await submit((pathAction==='files'?'搜索文件：':'整理目录：')+path,action);$('#path-dialog').close();});};
$('#mode-toggle').onclick=()=>safe(async()=>{await api('settings',{mode:state.settings.mode==='local'?'efficiency':'local'});await refresh();toast('运行模式已切换');});
$('#settings-form').onsubmit=e=>{e.preventDefault();const f=e.target;safe(async()=>{await api('settings',{mode:f.elements.mode.value,model:{base_url:f.elements.base_url.value,model:f.elements.model.value},roots:f.elements.roots.value.split('\n').map(x=>x.trim()).filter(Boolean)});await refresh();toast('设置已保存');});};
$('#index-form').onsubmit=e=>{e.preventDefault();safe(()=>submit('建立知识库索引',{tool:'knowledge_index',arguments:{path:$('#index-path').value}}));};
$('#knowledge-search').onclick=()=>safe(loadKnowledge);$('#knowledge-query').oninput=()=>{clearTimeout(knowledgeTimer);knowledgeTimer=setTimeout(()=>safe(loadKnowledge),250);};$('#knowledge-filter').onchange=()=>safe(loadKnowledge);
$('#workflow-form').onsubmit=e=>{e.preventDefault();const f=e.target;safe(async()=>{await api('workflows',{name:f.elements.name.value,prompt:f.elements.prompt.value,interval:Number(f.elements.minutes.value)*60});f.reset();await refresh();toast('工作流已创建');});};
document.querySelectorAll('[data-template]').forEach(b=>b.onclick=()=>{const f=$('#workflow-form');if(b.dataset.template==='disk'){f.elements.name.value='磁盘空间巡检';f.elements.prompt.value='检查磁盘空间';f.elements.minutes.value=60;}else{f.elements.name.value='更新文档索引';f.elements.prompt.value='/tool knowledge_index '+JSON.stringify({path:state.settings.roots[0]});f.elements.minutes.value=1440;}f.elements.name.focus();});
async function approve(value){const id=approvalId;if(!id)return;await api('approve',{id,approved:value});$('#approval-dialog').close();await refresh();}
$('#approve').onclick=()=>safe(()=>approve(true));$('#reject').onclick=()=>safe(()=>approve(false));$('#approval-dialog').oncancel=e=>{e.preventDefault();safe(()=>approve(false));};
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='n'){e.preventDefault();$('#new-chat').click();}});
safe(async()=>{await refresh();session=state?.sessions[0]?.id||null;await renderMessages();if(state)renderState();watchLive();});
setInterval(()=>{if(!liveConnected)refresh();},250);
