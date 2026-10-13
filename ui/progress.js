'use strict';
window.AgentProgress = (() => {
 function sync(parent, specs) {
  const old = new Map([...parent.children].map(node => [node.dataset.key, node]));
  let cursor = parent.firstElementChild;
  for (const spec of specs) {
   let node = old.get(spec.key);
   if (!node) { node = spec.make(); node.dataset.key = spec.key; }
   old.delete(spec.key); spec.update?.(node);
   if (node !== cursor) parent.insertBefore(node, cursor);
   cursor = node.nextElementSibling;
  }
  for (const node of old.values()) { release(node); node.remove(); }
 }
 function release(node) {
  node.querySelectorAll('.animated-text').forEach(body => { body._stopped = true; cancelAnimationFrame(body._frame); });
  MarvisMarkdown.release(node);
 }
 function text(body, value, animate = false) {
  body.classList.add('animated-text');body._stopped = false;
  // Finishing a model turn must not flush the remaining characters at once.
  // Keep the existing reveal loop until it catches up with the final target.
  if(!animate&&body._animate&&value!==body._shown&&value.startsWith(body._shown||''))animate=true;
  if (body._target === value && body._animate === animate) return;
  body._target = value;body._animate = animate;
  if (!animate) {
   cancelAnimationFrame(body._frame);body._frame = null;body._shown = value;paint(body,value);return;
  }
  if (!value.startsWith(body._shown || '')) body._shown = '';
  if (body._frame) return;
  let lastPaint = 0;
  function tick(now) {
   if (body._stopped || !body.isConnected) { body._frame = null;return; }
   const remaining = Array.from(body._target.slice((body._shown || '').length));
   body._shown = (body._shown || '') + remaining.slice(0,Math.max(1,Math.ceil(remaining.length / 7))).join('');
   if (now-lastPaint>45 || body._shown===body._target) {paint(body,body._shown);lastPaint=now;}
   if(body._shown!==body._target)body._frame=requestAnimationFrame(tick);else body._frame=null;
  }
  body._frame=requestAnimationFrame(tick);
 }
 function paint(body, value) {
  if(body._painted===value)return;
  body._painted=value;MarvisMarkdown.release(body);body.replaceChildren(richText(value));
  document.dispatchEvent(new Event('agent-content-resize'));
 }
 function steps(task) {
  const result = [];const byStep = new Map();let current=0;
  const get = step => {
   if(!byStep.has(step)){const item={step,notes:[],tools:[],events:[]};byStep.set(step,item);result.push(item);}
   return byStep.get(step);
  };
  for(const event of task.events){
   const d=event.content;
   if(event.kind==='model_start')current=d.step;
   const step=get(d.step ?? current);
   if(event.kind==='model_start')step.model=event;
   else if(['assistant_stream','assistant_note'].includes(event.kind))step.notes.push(event);
   else if(event.kind==='tool_call_stream'){
    const key=d.call_id||`stream-${d.index}`;let tool=step.tools.find(t=>t.key===key);
    if(!tool){tool={key};step.tools.push(tool);}tool.stream=event;
   }else if(event.kind==='tool_start'){
    const key=d.call_id||`event-${event.id}`;let tool=step.tools.find(t=>t.key===key);
    if(!tool){tool={key};step.tools.push(tool);}tool.start=event;
   }else if(['tool_result','tool_error','rejected','approval','approved'].includes(event.kind)){
    const tool=d.call_id?step.tools.find(t=>t.key===d.call_id):[...step.tools].reverse().find(t=>t.start?.content.tool===d.tool&&!t.result);
    if(tool){if(event.kind==='approval')tool.approval=event;else if(event.kind==='approved')tool.approved=event;else tool.result=event;}
    else step.events.push(event);
   }else if(['stream_fallback','failed','cancelled','rejected','cancel_requested','context_compaction_start','context_compaction','context_compaction_error'].includes(event.kind))step.events.push(event);
  }
  return result.filter(s=>s.model||s.notes.length||s.tools.length||s.events.length);
 }
 function makeTool() {
  const node=el('div','agent-tool');const line=el('div','tool-line');
  line.append(el('span','tool-icon','↗'),el('span','tool-name'),el('span','tool-state'));
  const detail=el('details','tool-details');detail.append(el('summary','','参数与返回结果'),el('div','tool-payload'));
  node.append(line,detail);return node;
 }
 function updateTool(node,tool,task) {
  const d=(tool.start||tool.stream).content, result=tool.result;
  node.querySelector('.tool-name').textContent=d.tool||'准备工具调用';
  const failed=result?.kind==='tool_error'||!!result?.content.result?.error||result?.content.result?.status==='failed';
  node.classList.toggle('tool-failed',failed);
  node.classList.toggle('tool-running',!result&&busy(task));
  node.querySelector('.tool-icon').textContent=failed?'!':result?'✓':'↗';
  node.querySelector('.tool-state').textContent=result?(failed?'失败':result.kind==='rejected'?'已拒绝':`${result.content.elapsed_ms||0} ms`):tool.approval&&!tool.approved?'等待确认':!busy(task)?'已结束':tool.start?'执行中':'生成调用';
  const payload=node.querySelector('.tool-payload');
  const value=JSON.stringify([d.arguments,result?.content,tool.approval?.content]);
  if(payload._value!==value){
   payload._value=value;payload.replaceChildren(el('div','tool-payload-label','调用参数'),el('pre','',typeof d.arguments==='string'?d.arguments:JSON.stringify(d.arguments,null,2)));
   if(result)payload.append(el('div','tool-payload-label',failed?'错误':'返回结果'),el('pre','',failed?(result.content.text||JSON.stringify(result.content.result,null,2)):JSON.stringify(result.content.result,null,2)));
   else if(tool.approval&&!tool.approved)payload.append(el('div','tool-payload-label','待确认操作'),el('pre','',JSON.stringify(tool.approval.content,null,2)));
  }
 }
 function conversation(task, finalMessage) {
  const groups=steps(task), lastGroup=groups.at(-1), latest=lastGroup?.step;
  const lastNote=lastGroup?.notes.at(-1);
  // The messages request can be newer than the live task snapshot. Associate
  // the saved answer with the final model turn, rather than comparing text
  // with a possibly incomplete stream. Tool-turn notes remain separate.
  const finalEvent=finalMessage&&!lastGroup?.tools.length&&lastNote?.kind==='assistant_stream'?lastNote:null;
  const items=[];
  for(const group of groups){
   for(const event of group.notes)items.push({kind:'reply',key:`reply-${task.id}-${event.id}`,event,
    message:event===finalEvent?finalMessage:null,
    note:event.kind==='assistant_note'||group.tools.length>0,
    streaming:event!==finalEvent&&busy(task)&&group.step===latest&&event.kind==='assistant_stream'});
   if(group.tools.length||group.events.length){
    const previous=items.at(-1);
    if(previous?.kind==='trace')previous.groups.push(group);
    else items.push({kind:'trace',key:`trace-${task.id}-${group.step}`,groups:[group]});
   }
  }
  return {items,finalRendered:!!finalEvent};
 }
 function paintDuration(node, now=Date.now()/1000) {
  const start=Number(node.dataset.start),end=node.dataset.end?Number(node.dataset.end):now;
  const seconds=Math.max(0,Math.floor(end-start));
  const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;
  node.textContent='用时 '+(hours?`${hours}小时 `:'')+(minutes?`${minutes}分钟 `:'')+`${rest}秒`;
 }
 function updateDuration(node,task,groups) {
  const events=groups.flatMap(group=>[group.model,...group.notes,...group.events,
   ...group.tools.flatMap(tool=>[tool.stream,tool.start,tool.approval,tool.approved,tool.result])]).filter(Boolean);
  const times=events.map(event=>event.created).filter(time=>typeof time==='number'&&Number.isFinite(time));
  node.hidden=!times.length;
  delete node.dataset.start;delete node.dataset.end;
  if(node.hidden){node.textContent='';return;}
  node.dataset.start=Math.min(...times);
  const pending=groups.some(group=>group.tools.some(tool=>!tool.result));
  const last=groups.at(-1)?.step===steps(task).at(-1)?.step;
  const running=busy(task)&&!task.branch_cutoff&&last&&pending;
  const stopped=task.events.filter(event=>['failed','cancelled','cancel_requested'].includes(event.kind))
   .map(event=>event.created).filter(time=>typeof time==='number'&&Number.isFinite(time));
  if(!running)node.dataset.end=Math.max(...times,...(pending&&last?stopped:[]));
  paintDuration(node);
 }
 // Update only the clocks still mounted in the conversation, without rebuilding traces.
 const durationTimer=setInterval(()=>{
  document.querySelectorAll('.trace-duration[data-start]:not([data-end])').forEach(node=>paintDuration(node));
 },1000);
 window.addEventListener('pagehide',()=>clearInterval(durationTimer));
 function updateTrace(root,task,groups=steps(task).filter(group=>group.tools.length||group.events.length)) {
  root.className='chat-trace agent-progress';
  if(!root.firstElementChild){
   const detail=el('details','trace-details'),heading=el('summary','progress-heading');
   heading.append(el('span','trace-label'),el('span','trace-preview'),el('span','trace-duration'));
   detail.append(heading,el('div','progress-steps'));root.append(detail);
   detail.addEventListener('toggle',()=>document.dispatchEvent(new Event('agent-content-resize')));
  }
  const detail=root.firstElementChild;
  updateDuration(detail.querySelector('.trace-duration'),task,groups);
  const tools=groups.flatMap(group=>group.tools);
  const errors=tools.filter(tool=>tool.result?.kind==='tool_error'||tool.result?.content.result?.error||tool.result?.content.result?.status==='failed').length;
  const pendingTools=tools.some(tool=>!tool.result);
  const interrupted=groups.some(group=>group.events.some(event=>['failed','cancelled','cancel_requested'].includes(event.kind)));
  const rejected=tools.filter(tool=>tool.result?.kind==='rejected').length;
  const label=task.branch_cutoff?'历史记录':pendingTools?(busy(task)?'正在执行':'执行未完成'):interrupted?'执行已中断':'已执行';
  detail.querySelector('.trace-label').textContent=tools.length
   ?`${label} ${tools.length} 项操作${errors?` · ${errors} 项失败`:''}${rejected?` · ${rejected} 项已拒绝`:''}`
   :`${label} · 执行记录`;
  const latest=steps(task).at(-1)?.step;
  const activeTools=busy(task)?groups.flatMap(group=>group.tools):[];
  const pending=activeTools.filter(tool=>!tool.result);
  const current=pending.find(tool=>tool.start)||pending.at(-1);
  const data=(current?.start||current?.stream)?.content;
  const actions={agent_dispatch:'分配任务',agent_report:'汇总任务结果',agent_inspect:'查看任务进展',system_info:'检查电脑信息',disk_usage:'检查磁盘空间',file_read:'读取文件',file_write:'写入文件',file_search:'搜索文件',web_search:'搜索资料',browser_open:'打开网页',code_execute:'运行代码',delivery_evaluate:'评估任务结果'};
  const preview=data?(current.approval&&!current.approved?'等待确认 · ':'')+(actions[data.tool]||'处理工具操作'):'';
  const characters=Array.from(preview);
  detail.querySelector('.trace-preview').textContent=characters.length>50?characters.slice(0,47).join('')+'...':preview;
  sync(detail.lastElementChild,groups.map(group=>({
   key:'step-'+group.step,
   make:()=>{const node=el('section','agent-step');node.append(el('div','step-content'));return node;},
   update:node=>{
    const active=busy(task)&&group.step===latest;node.classList.toggle('step-active',active);
    const items=[];
    items.push(...group.tools.map(tool=>({key:'tool-'+tool.key,make:makeTool,update:node=>updateTool(node,tool,task)})));
    items.push(...group.events.map(event=>({key:'event-'+event.id,make:()=>el('div','progress-status'),update:node=>{node.textContent=event.content.text||event.content.action||event.kind;}})));
    sync(node.lastElementChild,items);
   }
  })));
 }
 return {sync,release,text,steps,conversation,updateTrace};
})();
