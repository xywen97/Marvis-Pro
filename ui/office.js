'use strict';
window.MarvisOffice = (() => {
 const names={main:'MarvisXY',computer:'电脑管家',file:'文件管家',app:'软件管家',browser:'浏览器管家',search:'搜索助手'};
 const labels={idle:'待命',thinking:'思考与规划',working:'执行工具',waiting:'等待你确认',done:'任务完成',error:'任务异常',stopped:'任务已停止'};
 const drawing=`<svg viewBox="0 0 240 220" aria-hidden="true">
  <ellipse cx="124" cy="187" rx="90" ry="14" class="desk-shadow"/>
  <path d="M40 110v61l9 5v-59M196 110v61l-9 5v-59" class="desk-leg"/>
  <path d="M26 105l145-16 44 24-147 18z" class="desk-top"/>
  <path d="M26 105v9l42 25 147-17v-9L68 131z" class="desk-edge"/>
  <path d="M112 88v13l-15 3 37 5 15-5-25-5V87" class="monitor-foot"/>
  <rect x="80" y="35" width="90" height="61" rx="5" class="monitor"/>
  <rect x="86" y="41" width="78" height="48" rx="2" class="monitor-screen"/>
  <g class="screen-lines"><path d="M95 52h40M95 61h56M95 70h32M95 79h44"/></g>
  <circle cx="155" cy="51" r="3" class="screen-light"/>
  <path d="M49 94v12c0 6 14 6 14 0V94z" class="coffee"/><ellipse cx="56" cy="94" rx="7" ry="3" class="coffee-top"/>
  <path d="M64 96c10-1 10 10 0 8" fill="none" class="coffee-handle"/>
  <g class="coffee-steam"><path d="M52 86c-5-4 5-6 0-10M59 87c-5-4 5-6 0-10"/></g>
  <path d="M82 182h68l-8-35H91z" class="chair-base"/>
  <path d="M116 177v22m0-4-27 9m27-9 28 9" class="chair-legs"/>
  <g class="mascot">
   <path d="M104 144l-7 27h19l6-21 7 21h18l-9-32z" class="mascot-body"/>
   <path d="M103 98c-12 9-13 30-8 47 8 17 39 17 48 0 6-15 3-38-9-46z" class="mascot-body"/>
   <path d="M95 104c-12-7-15-26-5-40l-2-16 16 8c12-6 23-6 34 0l15-8-2 17c12 16 5 33-5 40z" class="mascot-head"/>
   <path d="M95 103c15 5 34 5 50-1l2 12c-18 7-40 7-55 0z" class="scarf"/>
   <path d="M139 111l2 24 11-2-4-25" class="scarf-tail scarf"/>
   <path d="M98 124l-20-14m58 14 15-13" class="mascot-arms"/>
   <path d="M109 71h18" class="mascot-mark"/>
  </g>
  <rect x="91" y="144" width="58" height="40" rx="12" class="chair-back"/>
  <g class="work-spark"><path d="M179 42v10m-5-5h10M69 61v8m-4-4h8"/></g>
 </svg>`;
 let initialized=false, selected='main';
 function role(tool='') {
  if(tool==='knowledge_search'||tool==='web_search')return 'search';
  if(tool.startsWith('system_'))return 'computer';if(tool.startsWith('app_'))return 'app';
  if(tool.startsWith('browser_'))return 'browser';return 'file';
 }
 function activity(snapshot) {
  const modes=Object.fromEntries(snapshot.agents.map(a=>[a.id,{mode:'idle',task:null,tool:''}]));
  const task=snapshot.tasks.find(t=>['running','waiting_approval'].includes(t.status));
  if(task){
   let agent='main', tool='';
   for(const event of task.events){
    if(event.kind==='tool_start'){agent=event.content.agent||role(event.content.tool);tool=event.content.tool;}
    else if(['model_start','tool_result','tool_error'].includes(event.kind)){agent='main';tool='';}
   }
   modes.main={mode:task.status==='waiting_approval'?'waiting':'thinking',task,tool:''};
   if(agent!=='main'&&modes[agent])modes[agent]={mode:task.status==='waiting_approval'?'waiting':'working',task,tool};
  }else{
   const recent=snapshot.tasks.find(t=>['completed','failed','cancelled'].includes(t.status)&&Date.now()/1000-t.updated<5);
   if(recent)modes.main={mode:recent.status==='completed'?'done':recent.status==='cancelled'?'stopped':'error',task:recent,tool:''};
  }
  return modes;
 }
 function init(snapshot) {
  if(initialized)return;initialized=true;
  const room=$('#office-stations');
  for(const agent of snapshot.agents){
   const station=el('button','office-station agent-'+agent.id);station.type='button';station.dataset.agent=agent.id;
   const art=el('div','station-art');art.innerHTML=drawing;
   station.append(el('span','station-label',names[agent.id]||agent.name),art,el('span','station-status','待命'));
   station.onclick=()=>{selected=agent.id;render(state);};room.append(station);
  }
 }
 function render(snapshot) {
  if(!snapshot)return;init(snapshot);
  const modes=activity(snapshot);
  for(const station of document.querySelectorAll('.office-station')){
   const item=modes[station.dataset.agent];
   if(station.dataset.mode!==item.mode)station.dataset.mode=item.mode;
   station.classList.toggle('selected',station.dataset.agent===selected);
   station.querySelector('.station-status').textContent=labels[item.mode]+(item.tool?' · '+item.tool:'');
   station.setAttribute('aria-label',(names[station.dataset.agent]||'管家')+' · '+labels[item.mode]);
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
  $('#office-agent-state').textContent=labels[item.mode];
  $('#office-agent-work').textContent=item.task?item.task.prompt:'没有正在执行的任务。点击工位可以查看各管家的状态。';
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
