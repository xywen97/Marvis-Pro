'use strict';
window.MarvisTeam = (() => {
 const labels={idle:'待命',thinking:'处理中',coordinating:'协调中',working:'执行中',waiting:'待确认',done:'已完成',error:'异常',stopped:'已停止'};
 const avatar=`<svg viewBox="0 0 56 56" aria-hidden="true"><g class="team-character">
 <path class="team-head" d="M13 20 11 9l12 6c5-2 8-2 13 0l10-6-1 12c8 14 0 24-17 24S5 34 13 20z"/>
 <path class="team-scarf" d="M12 38q16 8 31 0v7q-16 8-31 0z"/><path class="team-scarf" d="m39 44 3 9 7-2-5-10z"/>
 <g class="team-eyes"><ellipse cx="21" cy="28" rx="3" ry="4"/><ellipse cx="35" cy="28" rx="3" ry="4"/></g>
 <path d="m26 34 2 2 2-2" class="team-mouth"/></g>
 <g class="team-check"><circle cx="44" cy="13" r="9"/><path d="m40 13 3 3 5-6"/></g></svg>`;
 let initialized=false, owner='main', currentId=null, flight=null, queue=[], reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const seen=new Map();let seeded=false, renderedSession;
 function init(snapshot){
  if(initialized)return;initialized=true;const box=$('#agents');$('.team-heading span').textContent=snapshot.agents.length;box.classList.add('animated-team');
  for(const agent of snapshot.agents){
   const row=el('button','agent-row team-agent team-'+agent.id);row.type='button';row.dataset.agent=agent.id;
   row.title=agent.guidance||agent.role;
   const art=el('div','agent-avatar');art.innerHTML=avatar;
   const info=el('div','agent-info');info.append(el('strong','',agent.name),el('small','',agent.role));
   const meter=el('div','context-meter'),caption=el('div','context-caption');
   caption.append(el('span','','Context'),el('span','context-value'));
   const track=el('div','context-track');track.setAttribute('role','progressbar');track.setAttribute('aria-valuemin','0');track.setAttribute('aria-valuemax','100');
   track.append(el('span','context-fill'));meter.append(caption,track);info.append(meter);
   row.append(art,info,el('span','agent-status','待命'));row.onclick=()=>{
    const task=state.tasks.find(t=>t.session_id===session&&['running','waiting_approval'].includes(t.status));
    if(task)safe(async()=>{session=task.session_id;showPage('chat');await renderMessages();});else showPage('office');
   };box.append(row);
  }
  const overlay=document.createElementNS('http://www.w3.org/2000/svg','svg');overlay.classList.add('team-route');overlay.setAttribute('aria-hidden','true');
  const path=document.createElementNS(overlay.namespaceURI,'path');overlay.append(path);box.append(overlay);
  const marker=el('span','team-transfer','✦');marker.setAttribute('aria-hidden','true');box.append(marker);
 }
 function destination(event){
  if(['context_compaction_start','context_model_start','context_compaction_error'].includes(event.kind))return 'context';
  if(event.kind==='context_compaction')return event.content.agent||'main';
  if(['model_start','agent_start'].includes(event.kind))return event.content.agent||'main';
  if(['tool_result','tool_error','rejected'].includes(event.kind))return event.content.agent||'main';
  if(event.kind==='agent_result')return 'main';
  if(event.kind==='tool_start'){
   const tool=event.content.tool||'';
   return event.content.agent||(tool==='web_search'||tool==='knowledge_search'?'search':tool.startsWith('system_')?'computer':tool.startsWith('app_')?'app':tool.startsWith('browser_')?'browser':'file');
  }
  if(['model_start','tool_result','tool_error','rejected','completed','cancelled','failed'].includes(event.kind))return 'main';
  return null;
 }
 function collect(snapshot){
  // On initial load, start at the current owner instead of replaying stored history.
  const active=snapshot.tasks.find(t=>['running','waiting_approval'].includes(t.status));
  if(!seeded){
   for(const task of snapshot.tasks)seen.set(task.id,Math.max(0,...task.events.map(e=>e.id||0)));
   if(active){currentId=active.id;for(const event of active.events)owner=destination(event)||owner;}
   seeded=true;return;
  }
  const tasks=[...snapshot.tasks].sort((a,b)=>a.created-b.created);
  for(const task of tasks){
   const previous=seen.get(task.id)||0;
   const events=task.events.filter(e=>(e.id||0)>previous);
   if(events.length)seen.set(task.id,Math.max(...events.map(e=>e.id)));
   // Other clients may add old imports. Only animate fresh live activity.
   if(!events.length||(!['running','waiting_approval','queued'].includes(task.status)&&Date.now()/1000-task.updated>5))continue;
   if(currentId!==task.id){currentId=task.id;owner='main';}
   for(const event of events){
    const target=destination(event);
    if(target&&target!==owner){queue.push({from:owner,to:target});owner=target;}
   }
  }
  if(seen.size>160)for(const key of seen.keys()){if(!snapshot.tasks.some(t=>t.id===key))seen.delete(key);}
  play();
 }
 function play(){
  if(flight||!queue.length)return;
  const box=$('#agents');if(!box.getClientRects().length||document.hidden||reduced.matches){queue=[];return;}
  const edge=queue.shift(),from=box.querySelector(`[data-agent="${edge.from}"] .agent-avatar`),to=box.querySelector(`[data-agent="${edge.to}"] .agent-avatar`);
  if(!from||!to){play();return;}
  const bounds=box.getBoundingClientRect(),a=from.getBoundingClientRect(),b=to.getBoundingClientRect();
  const x=a.left+a.width/2-bounds.left,y1=a.top+a.height/2-bounds.top,y2=b.top+b.height/2-bounds.top;
  const overlay=box.querySelector('.team-route');overlay.setAttribute('viewBox',`0 0 ${bounds.width} ${bounds.height}`);
  overlay.querySelector('path').setAttribute('d',`M ${x} ${y1} C ${x+20} ${y1} ${x+20} ${y2} ${x} ${y2}`);
  box.dataset.handoff=`${edge.from}:${edge.to}`;overlay.classList.add('visible');
  const marker=box.querySelector('.team-transfer');marker.classList.add('visible');
  const frames=[];
  for(let i=0;i<=20;i++){
   const t=i/20,curve=3*(1-t)*t*20,pos=y1+(y2-y1)*(3*t*t-2*t*t*t);
   frames.push({transform:`translate(${x+curve-7}px,${pos-7}px) scale(${i===0||i===20?.7:1})`,offset:t});
  }
  const duration=queue.length>3?260:560;
  flight=marker.animate(frames,{duration,easing:'linear',fill:'forwards'});
  flight.onfinish=()=>{
   marker.classList.remove('visible');overlay.classList.remove('visible');flight.cancel();flight=null;
   to.closest('.team-agent').classList.remove('team-arrived');void to.offsetWidth;to.closest('.team-agent').classList.add('team-arrived');
   play();
  };
 }
 function stop(){queue=[];if(flight){flight.cancel();flight=null;}$('#agents .team-transfer')?.classList.remove('visible');$('#agents .team-route')?.classList.remove('visible');}
 function tokenLabel(value){return value>=1000000?`${+(value/1000000).toFixed(2)}M`:value>=1000?`${+(value/1000).toFixed(1)}k`:String(value);}
 function updateContext(row,snapshot,sid){
  const reading=snapshot.context_usage?.[sid]?.[row.dataset.agent];
  const capacity=reading?.window_tokens||snapshot.context_policy?.window_tokens||256000;
  const used=Math.max(0,reading?.estimated_tokens||0),percent=used/capacity*100,level=Math.min(100,percent);
  const meter=row.querySelector('.context-meter'),track=meter.querySelector('.context-track');
  meter.dataset.pressure=percent>=95?'high':percent>=80?'medium':'low';
  meter.querySelector('.context-value').textContent=reading?`${tokenLabel(used)} / ${tokenLabel(capacity)} · ${percent.toFixed(1)}%`:`未采样 / ${tokenLabel(capacity)}`;
  track.querySelector('.context-fill').style.width=`${level}%`;
  if(reading)track.setAttribute('aria-valuenow',level.toFixed(1));else track.removeAttribute('aria-valuenow');
  track.setAttribute('aria-label',`${row.querySelector('strong').textContent}上下文占用`);
  track.setAttribute('aria-valuetext',reading?`估算 ${used.toLocaleString()} / ${capacity.toLocaleString()} tokens，占用 ${percent.toFixed(1)}%`:`尚未采样，窗口容量 ${capacity.toLocaleString()} tokens`);
  const trigger=reading?.trigger_tokens||snapshot.context_policy?.trigger_tokens||capacity;
  meter.title=`${reading?'最近采样的上下文占用（估算）':'当前对话尚无模型用量采样'}\n${used.toLocaleString()} / ${capacity.toLocaleString()} tokens\n摘要触发值：${trigger.toLocaleString()} tokens\n模型：${reading?.model||snapshot.context_policy?.model||'当前模型'}`;
 }
 function render(snapshot){
  if(!snapshot)return;
  // The chat sidebar belongs to the selected session, including an empty draft.
  // Background tasks remain visible in the session list and task center.
  snapshot={...snapshot,tasks:snapshot.tasks.filter(t=>t.session_id===session)};
  if(renderedSession!==session){
   stop();seen.clear();seeded=false;owner='main';currentId=null;renderedSession=session;
  }
  init(snapshot);collect(snapshot);
  const modes=MarvisOffice.activity(snapshot);
  if(modes.main.mode==='done'){
   for(const event of modes.main.task.events){
    if(event.kind==='tool_start'){const agent=destination(event);if(modes[agent])modes[agent]={...modes[agent],mode:'done'};}
   }
  }
  if(Object.entries(modes).some(([id,item])=>id!=='main'&&['working','thinking'].includes(item.mode))&&modes.main.mode==='thinking')modes.main={...modes.main,mode:'coordinating'};
  for(const row of document.querySelectorAll('.team-agent')){
   const item=modes[row.dataset.agent];if(row.dataset.mode!==item.mode)row.dataset.mode=item.mode;
   row.querySelector('.agent-status').textContent=labels[item.mode];
   row.setAttribute('aria-label',snapshot.agents.find(a=>a.id===row.dataset.agent).name+' · '+labels[item.mode]);
   updateContext(row,snapshot,session);
  }
  const task=snapshot.tasks.find(t=>['running','waiting_approval'].includes(t.status));
  const selected=task&&snapshot.agents.find(a=>a.id===owner);
  const status=$('#team-execution');
  status.textContent=task?`${task.status==='waiting_approval'?'等待确认':'正在处理'} · ${selected?.name||'主控'}`:modes.main.mode==='done'?'✓ 任务完成，团队已就绪':modes.main.mode==='error'?'任务失败，请查看执行过程':modes.main.mode==='stopped'?'任务已停止':'团队待命';
 }
 reduced.addEventListener?.('change',()=>{if(reduced.matches)stop();});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
 window.addEventListener('resize',stop);
 return {render,stop};
})();
