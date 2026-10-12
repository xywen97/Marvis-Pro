'use strict';
window.MarvisOffice = (() => {
 const names={main:'MarvisXY',computer:'电脑管家',file:'文件管家',app:'软件管家',browser:'浏览器管家',search:'搜索助手',coding:'编程助手',evaluation:'评测助手',context:'上下文管理助手'};
 const labels={idle:'待命',thinking:'分析任务',working:'执行工具',waiting:'等待你确认',done:'任务完成',error:'任务异常',stopped:'任务已停止'};
 // A circle viewed from above projects to an ellipse. Seats share this projection.
 const table=`<svg class="office-table" viewBox="0 0 900 720" aria-hidden="true">
  <defs>
   <linearGradient id="office-table-surface" x2="0" y2="1"><stop stop-color="#fafcff"/><stop offset="1" stop-color="#dce6f3"/></linearGradient>
   <linearGradient id="office-table-rim" x2="0" y2="1"><stop stop-color="#c3d1e3"/><stop offset="1" stop-color="#9eafc7"/></linearGradient>
   <radialGradient id="office-command-glow"><stop stop-color="#7c9eff" stop-opacity=".25"/><stop offset="1" stop-color="#7c9eff" stop-opacity="0"/></radialGradient>
  </defs>
  <ellipse cx="450" cy="438" rx="342" ry="227" fill="#bcc9dc" opacity=".12"/>
  <ellipse cx="450" cy="398" rx="323" ry="211" fill="#e5ebf4"/>
  <ellipse cx="450" cy="399" rx="307" ry="199" fill="none" stroke="#d4dfed"/>
  <path d="M390 382v133q60 32 120 0V382" fill="#c3d0e2"/>
  <ellipse cx="450" cy="513" rx="60" ry="19" fill="#b3c2d7"/>
  <path d="M160 340v23a290 190 0 0 0 580 0v-23" fill="url(#office-table-rim)"/>
  <ellipse cx="450" cy="340" rx="290" ry="190" fill="url(#office-table-surface)" stroke="#bacbe0" stroke-width="2"/>
  <ellipse cx="450" cy="340" rx="276" ry="179" fill="none" stroke="#fff" stroke-width="2" opacity=".8"/>
  <ellipse cx="450" cy="340" rx="200" ry="130" fill="none" stroke="#c2d1e5" stroke-dasharray="3 9"/>
  <ellipse cx="450" cy="340" rx="134" ry="88" fill="url(#office-command-glow)"/>
  <ellipse cx="450" cy="354" rx="78" ry="47" fill="#e9efff" stroke="#a6bbef"/>
  <ellipse cx="450" cy="354" rx="67" ry="39" fill="none" stroke="#bdccf1"/>
  <g class="office-seat-indicators" stroke="#f7faff" stroke-width="2"></g>
 </svg>`;
 function drawing(front=false,main=false){return `<svg viewBox="0 0 160 180" aria-hidden="true">
  <ellipse cx="80" cy="154" rx="48" ry="13" class="desk-shadow"/>
  ${main?'':`<path d="M77 139v25m0-4-25 10m25-10 27 10" class="chair-legs"/>
  <rect x="47" y="77" width="66" height="59" rx="18" class="chair-back"/>
  <ellipse cx="80" cy="135" rx="34" ry="14" class="chair-base"/>`}
  <g class="mascot">
   <path d="M61 110l-6 29h19l6-22 6 22h19l-6-29z" class="mascot-body"/>
   <path d="M62 77c-13 13-13 35-5 45 11 12 35 12 46 0 8-13 6-35-6-45z" class="mascot-body"/>
   <path d="M48 73c-7-12-6-27 1-37l-2-19 19 9c9-3 20-3 29 0l18-9-2 19c8 13 9 28 1 40-13 16-51 15-64-3z" class="mascot-head"/>
   <path d="M51 81q29 12 58-1l1 12q-30 14-61 0z" class="scarf"/>
   <path d="m101 91 3 24 12-3-7-23" class="scarf-tail scarf"/>
   <path d="m60 103-18-12m58 12 18-12" class="mascot-arms"/>
   ${front?`<g class="mascot-face"><ellipse cx="64" cy="56" rx="3" ry="4"/><ellipse cx="96" cy="56" rx="3" ry="4"/><path d="m76 66 4 3 4-3m-4 3v4"/></g>`:'<path d="M69 46h22" class="mascot-mark"/>'}
  </g>
  ${main?'<ellipse cx="80" cy="144" rx="40" ry="8" class="command-light"/>':front?'':'<rect x="48" y="116" width="64" height="34" rx="12" class="chair-back"/>'}
  <g class="work-spark"><path d="M128 39v12m-6-6h12M29 65v8m-4-4h8"/></g>
 </svg>`;}
 let initialized=false, selected='main';
 const seats=new Map(), indicators=new Map(), eventCursors=new Map();
 const svgNS='http://www.w3.org/2000/svg';
 function statusText(item){
  if(item.mode==='working'&&item.tool==='agent_dispatch')return '分派任务';
  if(item.mode==='thinking')return item.stage==='coordinating'?'统筹任务':item.stage==='reviewing'?'整理结果':item.stage==='context'?'整理上下文':labels.thinking;
  return labels[item.mode];
 }
 function svgNode(tag,attrs={}){
  const node=document.createElementNS(svgNS,tag);
  for(const [key,value] of Object.entries(attrs))node.setAttribute(key,value);
  return node;
 }
 function showFlow(agent,direction){
  const seat=seats.get(agent),main=seats.get('main');
  if(!seat||!main||agent==='main')return;
  const returning=direction==='return',from=returning?seat:main,to=returning?main:seat;
  const dx=to.x-from.x,dy=to.y-from.y,length=Math.hypot(dx,dy),ux=dx/length,uy=dy/length;
  const start={x:from.x+ux*65,y:from.y+uy*65},end={x:to.x-ux*65,y:to.y-uy*65};
  const mid={x:(start.x+end.x)/2-uy*28,y:(start.y+end.y)/2+ux*28};
  const route=`M ${start.x} ${start.y} Q ${mid.x} ${mid.y} ${end.x} ${end.y}`;
  const layer=$('#office-data-flows');
  // Keep bursts legible and bound transient DOM even under many rapid handoffs.
  while(layer.children.length>=8)layer.firstElementChild.remove();
  const group=svgNode('g',{'class':'office-flow flow-'+direction,'data-agent':agent,'data-direction':direction});
  group.append(svgNode('path',{d:route,'class':'flow-track'}),svgNode('path',{d:route,'class':'flow-stream'}));
  for(let i=0;i<3;i++){
   const dot=svgNode('circle',{r:i===0?4:2.5,'class':'flow-packet'});
   dot.style.offsetPath=`path("${route}")`;dot.style.animationDelay=(-i*.35)+'s';group.append(dot);
  }
  group.append(svgNode('circle',{cx:end.x,cy:end.y,r:8,'class':'flow-arrival'}));
  const badge=svgNode('g',{'class':'flow-badge',transform:`translate(${mid.x} ${mid.y})`});
  badge.append(svgNode('rect',{x:-49,y:-12,width:98,height:24,rx:12}));
  const label=svgNode('text',{'text-anchor':'middle',y:4});label.textContent=returning?'结果回传 →':'任务分派 →';badge.append(label);group.append(badge);
  layer.append(group);
  $('#office-flow-announcement').textContent=returning?`${names[agent]}已向主控返回任务结果`:`主控已向${names[agent]}分派任务`;
  setTimeout(()=>group.remove(),4600);
 }
 function blinkFailure(agent){
  const indicator=indicators.get(agent);
  if(!indicator||indicator.classList.contains('failure-flash'))return;
  indicator.classList.add('failure-flash');
  indicator.setAttribute('data-failure','true');
  const clear=()=>{clearTimeout(timer);indicator.removeEventListener('animationend',clear);indicator.classList.remove('failure-flash');indicator.removeAttribute('data-failure');};
  indicator.addEventListener('animationend',clear,{once:true});
  // Also clear when motion is disabled or the office animation is paused offscreen.
  const timer=setTimeout(clear,2100);
 }
 function failureAgent(task,event,index){
  const data=event.content,result=data.result;
  const toolFailed=event.kind==='tool_error'||(event.kind==='tool_result'&&(result?.error||result?.status==='failed'));
  if(toolFailed){
   // Dispatch envelopes mirror the worker's report, which has its own event.
   if(data.tool==='agent_dispatch'&&result?.agent)return null;
   return data.agent||role(data.tool);
  }
  if(event.kind==='context_compaction_error')return 'context';
  if(event.kind==='agent_result'&&['failed','partial'].includes(result?.status)){
   // Do not flash a second time for the summary of an already reported tool failure.
   const alreadyReported=task.events.slice(0,index).some(previous=>{
    const content=previous.content;
    return content.agent===data.agent&&content.request_id===data.request_id&&
     (previous.kind==='tool_error'||(previous.kind==='tool_result'&&(content.result?.error||content.result?.status==='failed')));
   });
   return alreadyReported?null:data.agent;
  }
  return null;
 }
 function renderFlows(snapshot){
  for(const task of snapshot.tasks){
   const last=eventCursors.get(task.id),events=task.events||[];
   for(const [index,event] of events.entries()){
    // First view shows only fresh transfers, never replays an old task history.
    if(last===undefined?Date.now()/1000-event.created>5:event.id<=last)continue;
    if(event.kind==='agent_start')showFlow(event.content.agent,'dispatch');
    else if(event.kind==='agent_result')showFlow(event.content.agent,'return');
    const failed=failureAgent(task,event,index);if(failed)blinkFailure(failed);
   }
   if(events.length)eventCursors.set(task.id,events[events.length-1].id);
  }
  for(const id of eventCursors.keys())if(!snapshot.tasks.some(task=>task.id===id))eventCursors.delete(id);
 }

 function role(tool='') {
  if(tool==='delivery_evaluate')return 'evaluation';if(tool.startsWith('code_'))return 'coding';
  if(tool==='knowledge_search'||tool==='web_search')return 'search';
  if(tool.startsWith('system_'))return 'computer';if(tool.startsWith('app_'))return 'app';
  if(tool.startsWith('browser_'))return 'browser';return 'file';
 }
 function activity(snapshot) {
  const modes=Object.fromEntries(snapshot.agents.map(a=>[a.id,{mode:'idle',task:null,tool:''}]));
  const task=snapshot.tasks.find(t=>['running','waiting_approval'].includes(t.status));
  if(task){
   let agent='main', tool='', phase='thinking',stage='analyzing';
   for(const event of task.events){
    if(['context_compaction_start','context_model_start'].includes(event.kind)){agent='context';tool='';phase='thinking';stage='context';}
    else if(event.kind==='context_compaction'){agent=event.content.agent||'main';tool='';phase='thinking';stage='analyzing';}
    else if(event.kind==='tool_start'){agent=event.content.agent||role(event.content.tool);tool=event.content.tool;phase='working';stage='';}
    else if(['model_start','agent_start','tool_result','tool_error','rejected'].includes(event.kind)){agent=event.content.agent||'main';tool='';phase='thinking';stage=['tool_result','tool_error','rejected'].includes(event.kind)?'reviewing':'analyzing';}
    else if(event.kind==='agent_result'){agent='main';tool='';phase='thinking';stage='reviewing';}
   }
   modes.main={mode:task.status==='waiting_approval'?'waiting':agent==='main'?phase:'thinking',task,tool:agent==='main'?tool:'',stage:agent==='main'?stage:'coordinating'};
   if(agent!=='main'&&modes[agent])modes[agent]={mode:task.status==='waiting_approval'?'waiting':phase,task,tool,stage};
  }else{
   const recent=snapshot.tasks.find(t=>['completed','failed','cancelled'].includes(t.status)&&Date.now()/1000-t.updated<5);
   if(recent)modes.main={mode:recent.status==='completed'?'done':recent.status==='cancelled'?'stopped':'error',task:recent,tool:''};
  }
  return modes;
 }
 function init(snapshot) {
  if(initialized)return;initialized=true;
  const room=$('#office-stations');
  room.innerHTML=table;
  const flows=svgNode('svg',{id:'office-data-flows','class':'office-data-flows',viewBox:'0 0 900 720','aria-hidden':'true'});room.append(flows);
  const announcement=el('span','office-flow-announcement');announcement.id='office-flow-announcement';announcement.setAttribute('role','status');room.append(announcement);
  const assistants=snapshot.agents.filter(agent=>agent.id!=='main');
  for(const agent of snapshot.agents){
   const station=el('button','office-station agent-'+agent.id);station.type='button';station.dataset.agent=agent.id;
   const index=assistants.indexOf(agent),angle=-Math.PI/2+index*Math.PI*2/assistants.length;
   const isMain=agent.id==='main';
   const x=isMain?450:450+335*Math.cos(angle),y=isMain?335:350+245*Math.sin(angle);
   seats.set(agent.id,{x,y});
   if(!isMain){
    // Project the tangential seat panel into the same plane as the round table.
    const points=[[-27,-12],[27,-12],[27,12],[-27,12]].map(([tangent,radial])=>{
     const radius=250+radial;
     return [450+radius*Math.cos(angle)-tangent*Math.sin(angle),340+(radius*Math.sin(angle)+tangent*Math.cos(angle))*.58];
    });
    const indicator=svgNode('path',{'class':'seat-indicator','data-agent':agent.id,'data-active':'false',d:'M '+points.map(point=>point.join(' ')).join(' L ')+' Z'});
    room.querySelector('.office-seat-indicators').append(indicator);indicators.set(agent.id,indicator);
   }
   station.style.left=(x/900*100)+'%';station.style.top=(y/720*100)+'%';
   station.style.zIndex=isMain?'3':'4';
   station.classList.toggle('command-station',isMain);
   const art=el('div','station-art');art.innerHTML=drawing(isMain||y<280,isMain);
   station.append(art,el('span','station-label',names[agent.id]||agent.name),el('span','station-status','待命'));
   if(isMain)station.append(el('span','command-caption','主控'));
   station.onclick=()=>{selected=agent.id;render(state);};room.append(station);
  }
 }
 function render(snapshot) {
  if(!snapshot)return;init(snapshot);
  const modes=activity(snapshot);
  for(const [agent,indicator] of indicators){
   const active=['thinking','working','waiting'].includes(modes[agent]?.mode);
   indicator.dataset.active=String(active);
  }
  renderFlows(snapshot);
  for(const station of document.querySelectorAll('.office-station')){
   const item=modes[station.dataset.agent];
   if(station.dataset.mode!==item.mode)station.dataset.mode=item.mode;
   station.classList.toggle('selected',station.dataset.agent===selected);
   station.setAttribute('aria-pressed',String(station.dataset.agent===selected));
   station.querySelector('.station-status').textContent=statusText(item)+(item.tool?' · '+item.tool:'');
   station.setAttribute('aria-label',(names[station.dataset.agent]||'管家')+' · '+statusText(item));
  }
  const active=snapshot.tasks.filter(t=>['queued','running','waiting_approval'].includes(t.status));
  const today=new Date();today.setHours(0,0,0,0);
  const todays=snapshot.tasks.filter(t=>t.created*1000>=today.getTime());
  $('#office-active-count').textContent=active.length;
  $('#office-done-count').textContent=todays.filter(t=>t.status==='completed').length;
  $('#office-tool-count').textContent=todays.reduce((n,t)=>n+t.events.filter(e=>e.kind==='tool_start').length,0);
  $('#office-room-state').textContent=active.length?'团队正在处理 '+active.length+' 个任务':'团队已就绪，等待你的下一个目标';
  const agent=snapshot.agents.find(a=>a.id===selected),item=modes[selected];
  $('#office-agent-name').textContent=names[selected];$('#office-agent-role').textContent=agent.role;
  $('#office-agent-state').textContent=statusText(item);
  $('#office-evaluation-start').hidden=selected!=='evaluation';
  $('#office-agent-work').textContent=item.task?item.task.prompt:(agent.guidance||'没有正在执行的任务。点击工位可以查看各管家的状态。');
  const action=$('#office-agent-open');action.hidden=!item.task;action.onclick=()=>safe(async()=>{session=item.task.session_id;showPage('chat');await renderMessages();});
  const list=$('#office-recent');
  const signature=JSON.stringify(snapshot.tasks.slice(0,6).map(t=>[t.id,t.status]));
  if(list._signature!==signature){
   list._signature=signature;list.replaceChildren();
   for(const task of snapshot.tasks.slice(0,6)){
    const row=btn('',async()=>{session=task.session_id;showPage('chat');await renderMessages();},'office-task');
    row.append(el('span','',task.prompt),el('small','',statuses[task.status]));list.append(row);
   }
   if(!snapshot.tasks.length)list.append(el('p','hint','还没有任务。从工作台发送第一条指令吧。'));
  }
 }
 return {render,activity};
})();
