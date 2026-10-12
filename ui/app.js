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
let newSessionPending=null, showArchived=false, deletingSession=null;
// Keep deleted IDs hidden even if an in-flight state snapshot predates deletion.
const deletedSessions=new Set();
function withoutDeletedSessions(value){
 return {...value,sessions:value.sessions.filter(s=>!deletedSessions.has(s.id)),archived_sessions:(value.archived_sessions||[]).filter(s=>!deletedSessions.has(s.id)),tasks:value.tasks.filter(t=>!deletedSessions.has(t.session_id))};
}
function visibleSessions() {
 const empty=state.sessions.find(s=>s.id===session&&s.is_empty)||state.sessions.find(s=>s.is_empty);
 return showArchived?(state.archived_sessions||[]):state.sessions.filter(s=>!s.is_empty||s.id===empty?.id);
}
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
function showPage(next) { page=next;document.body.classList.toggle('office-view',next==='office');if(next==='office'){MarvisTeam.stop();MarvisOffice.render(state);} document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id===next));document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===next));$('#page-title').textContent={chat:'工作台',office:'办公室',knowledge:'知识库',tasks:'任务中心',workflows:'自动化',settings:'设置'}[next]; if(next==='tasks'&&state)renderTasks();if(next==='knowledge')safe(loadKnowledge);if(next==='settings'&&state)fillSettings(); }
function changed(name, value) { const sig=JSON.stringify(value); if(Object.prototype.hasOwnProperty.call(signatures,name)&&signatures[name]===sig)return false; signatures[name]=sig;return true; }
function acceptState(value) {
 stateUpdates=stateUpdates.catch(()=>{}).then(async()=>{
  state=withoutDeletedSessions(value);
  if(session&&!allSessions().some(s=>s.id===session)){session=state.sessions[0]?.id||null;$('#prompt').value='';await renderMessages();}
  $('#connection').textContent='● 已连接';$('#connection').style.color='';renderState();
  if(session&&changed('messages-session',[session,allSessions().find(s=>s.id===session)?.active_leaf,allSessions().find(s=>s.id===session)?.active_entry,state.tasks.filter(t=>t.session_id===session).map(t=>[t.id,t.status,t.updated])]))await renderMessages();
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
 if(page==='office')MarvisOffice.render(state);
 $('#knowledge-badge').textContent=state.knowledge_count;$('#mode-toggle').textContent=state.settings.mode==='local'?'◉ 本地模式':'◉ 效率模式';$('#model-label').textContent=state.settings.offline?'离线工具模式':state.settings.mode==='local'?'Ollama · qwen3:latest':state.settings.model.model||'模型未配置';
 if(!$('#index-path').value)$('#index-path').value=state.settings.roots[0];
 renderSessionList();
 updateComposer();
 const active=state.tasks.filter(busy);
 MarvisTeam.render(state);
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
function messageSpec(key,message,{streaming=false,note=false,displayText=message.content}={}){
 return {key,make:()=>{
  const row=el('div','message '+message.role),body=el('div','message-body');
  body.append(el('div','message-heading',message.role==='user'?'你':'MarvisXY'),el('div','message-text'));
  row.append(el('div','message-avatar',message.role==='user'?'你':'✦'),body);return row;
 },update:row=>{
  row.classList.toggle('streaming',streaming);
  row.classList.toggle('assistant-note',message.role==='assistant'&&note);
  row.classList.toggle('assistant-response',message.role==='assistant'&&!note);
  row.dataset.entryId=message.id||'';
  const content=row.querySelector('.message-text');
  if(message.role==='assistant')AgentProgress.text(content,displayText,streaming);
  else if(content.textContent!==message.content)content.replaceChildren(el('div','message-content',message.content));
  bubbleActions(row.querySelector('.message-body'),message.id,message.content);
 }};
}
async function renderMessages() {
 updateComposer();
 if(state)MarvisTeam.render(state);
 const currentSession=session, version=++messageVersion;
 const messages=session?await api('messages?session='+encodeURIComponent(session)):[];
 if(version!==messageVersion||session!==currentSession)return;
 const tasks=new Map((state?.tasks||[]).map(t=>[t.id,t]));
 const box=$('#messages');$('#welcome').classList.toggle('hidden',messages.length>0);
 if(box.dataset.session!==String(session)){
  AgentProgress.release(box);box.replaceChildren();box.dataset.session=String(session);followTail=true;
 }
 const specs=[],renderedAnswers=new Set();
 const answers=new Map(messages.filter(message=>message.role==='assistant'&&message.task_id).map(message=>[message.task_id,message]));
 for(const message of messages){
  if(renderedAnswers.has(message.id))continue;
  specs.push(messageSpec('message-'+message.id,message));
  if(message.role==='user'&&tasks.has(message.task_id)){
   let task=tasks.get(message.task_id);
   if(message.event_cutoff!==undefined&&!busy(task))task={...task,branch_cutoff:true,events:task.events.filter(e=>e.id<=message.event_cutoff)};
   const finalMessage=answers.get(task.id),conversation=AgentProgress.conversation(task,finalMessage);
   if(conversation.finalRendered)renderedAnswers.add(finalMessage.id);
   for(const item of conversation.items){
    if(item.kind==='reply'){
     const reply=item.message||{role:'assistant',id:item.event.kind==='assistant_note'?'event-'+item.event.id:null,content:item.event.content.text};
     specs.push(messageSpec(item.key,reply,{streaming:item.streaming,note:item.note,displayText:item.event.content.text}));
    }else specs.push({key:item.key,make:()=>el('div','agent-progress'),update:root=>AgentProgress.updateTrace(root,task,[item.group])});
   }
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
$('#chat-form').onsubmit=e=>{e.preventDefault();const p=$('#prompt').value.trim();if(!p)return;safe(async()=>{
 if(p.startsWith('/'))await runAppCommand(p);else await submit(p);
 if($('#prompt').value.trim()===p)$('#prompt').value='';updateCommandMenu();
});};
$('#prompt').onkeydown=e=>{if(commandMenuKey(e))return;if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#chat-form').requestSubmit();}};
$('#new-chat').onclick=()=>safe(async()=>{
 if(!newSessionPending)newSessionPending=api('sessions',{session_id:session}).finally(()=>{newSessionPending=null;});
 const s=await newSessionPending;showArchived=false;session=s.id;showPage('chat');await renderMessages();await refresh();$('#prompt').focus();
});
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>showPage(b.dataset.page));
document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>safe(()=>submit(b.dataset.prompt)));
document.querySelectorAll('[data-quick]').forEach(b=>b.onclick=()=>{const a=b.dataset.quick;if(a==='system')safe(()=>submit('检查电脑状态',{tool:'system_info',arguments:{}}));else if(a==='workflow')showPage('workflows');else pathDialog(a,a==='files'?'查找本地文件':'整理目录',state?.settings.roots[0]);});
$('#attach').onclick=()=>safe(async()=>{
 const button=$('#attach');if(button.disabled)return;button.disabled=true;
 try{
  const result=await api('pick-files',{});
  if(!result.paths?.length)return;
  const prompt=$('#prompt');const paths=result.paths.map(path=>JSON.stringify(path)).join(' ');
  prompt.value+=(prompt.value&&!/\s$/.test(prompt.value)?' ':'')+paths;
  updateCommandMenu();prompt.focus();toast(`已添加 ${result.paths.length} 个文件`);
 }finally{button.disabled=false;}
});
$('#path-cancel').onclick=()=>$('#path-dialog').close();
$('#path-form').onsubmit=e=>{e.preventDefault();const path=$('#path-value').value.trim();safe(async()=>{const action=pathAction==='files'?{tool:'file_list',arguments:{path,query:$('#path-query').value,content:true}}:{tool:'file_organize',arguments:{path}};await submit((pathAction==='files'?'搜索文件：':'整理目录：')+path,action);$('#path-dialog').close();});};
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

$('#office-start').onclick=()=>{showPage('chat');$('#prompt').focus();};
setInterval(()=>{MarvisTeam.render(state);if(page==='office')MarvisOffice.render(state);},1000);

function allSessions(){return [...(state?.sessions||[]),...(state?.archived_sessions||[])];}
function renderSessionList(){
 if(!changed('sessions',[state.sessions,state.archived_sessions,session,showArchived,state.tasks.filter(busy).map(t=>[t.session_id,t.status])]))return;
 $('#session-list-title').textContent=showArchived?'已归档对话':'最近对话';
 $('#session-archive-toggle').classList.toggle('active',showArchived);
 $('#session-archive-toggle').setAttribute('aria-pressed',String(showArchived));
 const box=$('#sessions');box.replaceChildren();
 for(const s of visibleSessions()){
  const row=el('div','session-entry'+(session===s.id?' selected':''));row.dataset.session=s.id;
  const tasks=state.tasks.filter(t=>t.session_id===s.id&&busy(t));
  const active=tasks.find(t=>t.status==='running')||tasks.find(t=>t.status==='waiting_approval')||tasks[0];
  const activity=active?.status||'idle';row.dataset.activity=activity;
  const open=btn('',async()=>{session=s.id;showPage('chat');await renderMessages();renderState();},'session'+(session===s.id?' selected':''));
  const indicator=el('span','session-indicator'+(activity==='running'?' session-spinner':''),activity==='waiting_approval'?'Ⅱ':'');
  indicator.setAttribute('aria-hidden','true');
  open.append(indicator,el('span','session-title',s.title));
  open.title=s.title+(active?' · '+statuses[activity]:'');open.setAttribute('aria-label',open.title);row.append(open);
  const menu=el('details','session-menu'),trigger=el('summary','','⋯');trigger.setAttribute('aria-label','管理对话：'+s.title);menu.append(trigger);
  const actions=el('div','session-menu-actions');const running=!!active;
  const archive=btn(s.archived?'恢复对话':'归档对话',()=>manageSession(s.id,s.archived?'restore':'archive'));
  const remove=btn('删除对话',()=>{deletingSession=s.id;$('#session-delete-description').textContent='“'+s.title+'”';$('#session-delete-dialog').showModal();},'danger');
  for(const action of [archive,remove]){action.disabled=running;if(running)action.title='请等待或停止正在执行的任务';actions.append(action);}
  menu.append(actions);row.append(menu);box.append(row);
 }
 if(!box.children.length)box.append(el('p','session-empty',showArchived?'没有已归档的对话':'还没有对话'));
}
async function manageSession(id,action){
 if(action==='delete')return deleteSession(id);
 await api('session-action',{session_id:id,action});
 if(session===id&&action!=='restore'){session=null;$('#prompt').value='';}
 if(action==='restore'){showArchived=false;session=id;}
 await refresh();if(!session)session=state.sessions[0]?.id||null;
 await renderMessages();renderState();toast(action==='archive'?'对话已归档':action==='restore'?'对话已恢复':'对话已删除');
}
async function deleteSession(id){
 if(deletedSessions.has(id))return;
 const saved={sessions:state.sessions.filter(s=>s.id===id),archived:(state.archived_sessions||[]).filter(s=>s.id===id),tasks:state.tasks.filter(t=>t.session_id===id),session,prompt:$('#prompt').value};
 const rows=[...$('#sessions').querySelectorAll('.session-entry')];
 const positions=new Map(rows.map(row=>[row.dataset.session,row.getBoundingClientRect().top]));
 const row=rows.find(row=>row.dataset.session===id);
 const motion=!matchMedia('(prefers-reduced-motion: reduce)').matches;
 let exitGhost;
 if(row&&motion){
  const rect=row.getBoundingClientRect(),ghost=row.cloneNode(true);
  ghost.classList.add('session-delete-ghost');ghost.removeAttribute('data-session');ghost.setAttribute('aria-hidden','true');ghost.inert=true;
  ghost.querySelector('.session-menu')?.remove();
  Object.assign(ghost.style,{top:rect.top+'px',left:rect.left+'px',width:rect.width+'px',height:rect.height+'px'});
  document.body.append(ghost);exitGhost=ghost;
  ghost.animate([{opacity:1,transform:'translateX(0) scale(1)'},{opacity:0,transform:'translateX(-10px) scale(.98)'}],{duration:180,easing:'cubic-bezier(.2,.7,.2,1)',fill:'forwards'}).finished.then(()=>ghost.remove(),()=>ghost.remove());
 }
 $('#session-delete-dialog').close();deletingSession=null;
 deletedSessions.add(id);state=withoutDeletedSessions(state);
 if(session===id){
  session=state.sessions[0]?.id||null;$('#prompt').value='';
  ++messageVersion;AgentProgress.release($('#messages'));$('#messages').replaceChildren();
  $('#messages').dataset.session=String(session);
 }
 const fallback=session;
 renderState();safe(renderMessages);
 if(motion)for(const next of $('#sessions').querySelectorAll('.session-entry')){
  const previous=positions.get(next.dataset.session),delta=previous-next.getBoundingClientRect().top;
  if(Number.isFinite(delta)&&delta)next.animate([{transform:`translateY(${delta}px)`},{transform:'translateY(0)'}],{duration:240,easing:'cubic-bezier(.2,.7,.2,1)'});
 }
 try{
  await api('session-action',{session_id:id,action:'delete'});
  toast('对话已删除');
 }catch(error){
  exitGhost?.remove();
  deletedSessions.delete(id);
  for(const [key,items] of [['sessions',saved.sessions],['archived_sessions',saved.archived],['tasks',saved.tasks]]){
   state[key]=[...state[key],...items.filter(item=>!state[key].some(current=>current.id===item.id))];
  }
  state.sessions.sort((a,b)=>b.created-a.created);state.archived_sessions.sort((a,b)=>b.created-a.created);
  if(saved.session===id&&session===fallback&&!$('#prompt').value){session=id;$('#prompt').value=saved.prompt;}
  renderState();safe(renderMessages);
  throw new Error('删除失败，对话已恢复：'+error.message);
 }
}
$('#session-archive-toggle').onclick=()=>{showArchived=!showArchived;renderSessionList();};
$('#session-delete-cancel').onclick=()=>{$('#session-delete-dialog').close();deletingSession=null;};
$('#session-delete-confirm').onclick=()=>safe(async()=>{const id=deletingSession;if(!id)return;const button=$('#session-delete-confirm');button.disabled=true;try{await manageSession(id,'delete');}finally{button.disabled=false;}});
document.addEventListener('click',event=>{document.querySelectorAll('.session-menu[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;});});

function updateComposer(){
 const archived=!!allSessions().find(s=>s.id===session)?.archived;
 $('#prompt').disabled=archived;$('#chat-form .send').disabled=archived;
 $('#prompt').placeholder=archived?'已归档的对话：请先恢复后再继续':'告诉我，你想完成什么…';
}
