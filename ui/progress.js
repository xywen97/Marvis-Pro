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
   }else if(['tool_result','tool_error','approval','approved'].includes(event.kind)){
    const tool=d.call_id?step.tools.find(t=>t.key===d.call_id):[...step.tools].reverse().find(t=>t.start?.content.tool===d.tool&&!t.result);
    if(tool){if(event.kind==='approval')tool.approval=event;else if(event.kind==='approved')tool.approved=event;else tool.result=event;}
    else step.events.push(event);
   }else if(['stream_fallback','failed','cancelled','rejected','cancel_requested'].includes(event.kind))step.events.push(event);
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
  const failed=result?.kind==='tool_error';
  node.classList.toggle('tool-failed',failed);
  node.classList.toggle('tool-running',!result&&busy(task));
  node.querySelector('.tool-icon').textContent=failed?'!':result?'✓':'↗';
  node.querySelector('.tool-state').textContent=result?(failed?'失败':`${result.content.elapsed_ms||0} ms`):tool.approval&&!tool.approved?'等待确认':!busy(task)?'已结束':tool.start?'执行中':'生成调用';
  const payload=node.querySelector('.tool-payload');
  const value=JSON.stringify([d.arguments,result?.content,tool.approval?.content]);
  if(payload._value!==value){
   payload._value=value;payload.replaceChildren(el('div','tool-payload-label','调用参数'),el('pre','',typeof d.arguments==='string'?d.arguments:JSON.stringify(d.arguments,null,2)));
   if(result)payload.append(el('div','tool-payload-label',failed?'错误':'返回结果'),el('pre','',failed?result.content.text:JSON.stringify(result.content.result,null,2)));
   else if(tool.approval&&!tool.approved)payload.append(el('div','tool-payload-label','待确认操作'),el('pre','',JSON.stringify(tool.approval.content,null,2)));
  }
 }
 function updateTrace(root,task) {
  root.className='chat-trace agent-progress';
  if(!root.firstElementChild)root.append(el('div','progress-heading'),el('div','progress-steps'));
  root.firstElementChild.textContent=`${statuses[task.status]} · 执行过程`;
  const groups=steps(task), latest=groups[groups.length-1]?.step;
  sync(root.lastElementChild,groups.map(group=>({
   key:'step-'+group.step,
   make:()=>{const node=el('section','agent-step');node.append(el('div','step-heading'),el('div','step-content'));return node;},
   update:node=>{
    const active=busy(task)&&group.step===latest;node.classList.toggle('step-active',active);
    const finishedReply=task.status==='completed'&&group.step===latest&&!group.tools.length&&group.notes.some(e=>e.kind==='assistant_stream');
    node.firstElementChild.textContent=group.step?`Step ${group.step} · ${active?'处理中':finishedReply?'回复完成':'处理记录'}`:'工具执行';
    const notes=group.notes.filter(e=>e.kind==='assistant_note'||group.tools.length||(busy(task)&&group.step===latest));
    const items=notes.map(event=>({key:'note-'+event.id,
     make:()=>{const note=el('div','thought-bubble message assistant');note.append(el('div','thought-heading','✦ 处理说明'),el('div','thought-text'));return note;},
     update:note=>{const streaming=active&&event.kind==='assistant_stream';note.classList.toggle('streaming',streaming);text(note.lastElementChild,event.content.text,streaming);}
    }));
    items.push(...group.tools.map(tool=>({key:'tool-'+tool.key,make:makeTool,update:node=>updateTool(node,tool,task)})));
    items.push(...group.events.map(event=>({key:'event-'+event.id,make:()=>el('div','progress-status'),update:node=>{node.textContent=event.content.text||event.content.action||event.kind;}})));
    sync(node.lastElementChild,items);
   }
  })));
 }
 return {sync,release,text,steps,updateTrace};
})();
