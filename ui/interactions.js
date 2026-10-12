'use strict';
let appCommands=[], commandIndex=0, commandHidden=false, treeData=null, treeRows=[], treeIndex=0;
const commandMenu=el('div','command-menu hidden');commandMenu.id='command-menu';commandMenu.setAttribute('role','listbox');$('#chat-form').prepend(commandMenu);
const treeDialog=el('dialog','tree-dialog');treeDialog.id='tree-dialog';
treeDialog.append(el('h2','','执行步骤树'),el('p','hint','当前对话内跳转 · ↑↓选择 · Enter继续 · Esc返回'));
const treeSearch=el('input');treeSearch.placeholder='搜索步骤内容';treeSearch.setAttribute('aria-label','搜索步骤');
const treeList=el('div','tree-list');treeList.setAttribute('role','listbox');
treeDialog.append(treeSearch,treeList,el('p','hint','选择用户消息：原文放回输入框；其他步骤：从该步骤继续。原分支保留。'),btn('关闭',()=>treeDialog.close()));document.body.append(treeDialog);
const commandDialog=el('dialog','command-dialog');commandDialog.append(el('h2','','命令输出'),el('pre'),btn('关闭',()=>commandDialog.close()));document.body.append(commandDialog);
function commandMatches(){const value=$('#prompt').value;if(!/^\/[^\s]*$/.test(value)||commandHidden)return [];return appCommands.filter(c=>c.name.startsWith(value)||c.aliases.some(a=>a.startsWith(value))).sort((a,b)=>Number(b.aliases.includes(value))-Number(a.aliases.includes(value)));}
function updateCommandMenu(){const matches=commandMatches();commandIndex=Math.min(commandIndex,Math.max(0,matches.length-1));commandMenu.classList.toggle('hidden',!matches.length);commandMenu.replaceChildren();matches.forEach((c,i)=>{const item=btn(c.usage+' — '+c.description,()=>completeCommand(c),'command-option'+(i===commandIndex?' selected':''));item.setAttribute('role','option');item.setAttribute('aria-selected',String(i===commandIndex));commandMenu.append(item);});commandMenu.children[commandIndex]?.scrollIntoView({block:'nearest'});}
function completeCommand(c){$('#prompt').value=c.name+' ';$('#prompt').focus();updateCommandMenu();}
function commandMenuKey(e){if(e.isComposing)return false;const matches=commandMatches();if(!matches.length)return false;if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();commandIndex=(commandIndex+(e.key==='ArrowUp'?-1:1)+matches.length)%matches.length;updateCommandMenu();return true;}if(e.key==='Tab'){e.preventDefault();completeCommand(matches[commandIndex]);return true;}if(e.key==='Escape'){e.preventDefault();commandHidden=true;updateCommandMenu();return true;}return false;}
$('#prompt').addEventListener('input',()=>{commandHidden=false;commandIndex=0;updateCommandMenu();});
safe(async()=>{appCommands=await api('commands');updateCommandMenu();});
async function runAppCommand(text){
 const result=await api('command',{text,session_id:session});session=result.session_id;
 if(result.view==='evaluation'){openEvaluation();return;}
 if(result.view==='tree'){await openStepTree();return;}
 if(result.view==='trace'){document.body.classList.toggle('hide-traces');return;}
 if(result.view==='result'){const tools=[...document.querySelectorAll('#messages .tool-details')];const target=tools[Number(result.arguments[0]||tools.length)-1];if(!target)throw Error('当前对话没有对应工具结果。');target.open=!target.open;if(target.open){const trace=target.closest('.trace-details');if(trace)trace.open=true;}return;}
 if(result.view==='quit'){window.close();toast('可使用窗口左上角关闭按钮退出 App。');return;}
 showPage(['chat','tasks','knowledge','workflows','settings'][result.page||0]);
 if(result.query!==undefined)$('#knowledge-query').value=result.query;
 await refresh();await renderMessages();
 if(result.text){commandDialog.querySelector('pre').textContent=result.text;commandDialog.showModal();}
 if(result.notice)toast(result.notice);
}

const evaluationDialog=el('dialog','evaluation-dialog');evaluationDialog.id='evaluation-dialog';
evaluationDialog.innerHTML=`<h2>唤醒评测助手</h2><p class="hint">请提供评测对象与要求。评测完成后，需求和结果将交给主控汇总。</p>
 <form id="evaluation-form" class="form-grid">
 <label>评测对象类型<select name="subject_type"><option>代码</option><option>输出</option><option>指标</option><option>任务</option><option>其他</option></select></label>
 <label>评测对象<textarea name="candidate" rows="5" maxlength="60000" required placeholder="粘贴代码、输出、指标或任务说明；评测文件时填写文件说明并在下方提供路径。"></textarea></label>
 <label>评测要求<textarea name="requirements" rows="3" maxlength="10000" required placeholder="预期结果、验收标准与约束；合规评测请提供适用规范。"></textarea></label>
 <label>参考证据（可选）<textarea name="evidence" rows="2" maxlength="20000" placeholder="测试结果、数据或参考资料；用户提供的证据会单独标注。"></textarea></label>
 <label>文件路径（可选，每行一个）<textarea name="paths" rows="2" placeholder="授权目录内的完整路径，只读评测。"></textarea></label>
 <label>当前对话执行证据<select name="source_task_id"><option value="">不附带历史任务</option></select></label>
 <p id="evaluation-error" class="hint" role="alert"></p>
 <div class="dialog-actions"><button type="button" id="evaluation-cancel">取消</button><button type="submit" class="primary">开始评测</button></div></form>`;
document.body.append(evaluationDialog);
const officeEvaluation=btn('唤醒评测助手',()=>openEvaluation());officeEvaluation.id='office-evaluation-start';officeEvaluation.hidden=true;
document.querySelector('.office-agent-card').append(officeEvaluation);
let evaluationSession=null;
function openEvaluation(){
 evaluationSession=session;
 const form=$('#evaluation-form'),select=form.elements.source_task_id;
 select.replaceChildren(new Option('不附带历史任务',''));
 for(const task of state?.tasks||[]){
  if(task.session_id===session&&['completed','failed','cancelled'].includes(task.status))select.append(new Option(task.prompt.slice(0,60),task.id));
 }
 $('#evaluation-error').textContent='';
 if(!evaluationDialog.open)evaluationDialog.showModal();
 form.elements.candidate.focus();
}
$('#evaluation-cancel').onclick=()=>evaluationDialog.close();
$('#evaluation-form').onsubmit=event=>{
 event.preventDefault();safe(async()=>{
  const form=event.target,button=form.querySelector('[type=submit]');
  if(evaluationSession!==session){$('#evaluation-error').textContent='对话已切换，请重新唤醒评测助手。';return;}
  const args={candidate:form.elements.candidate.value.trim(),requirements:form.elements.requirements.value.trim(),
   subject_type:form.elements.subject_type.value,evidence:form.elements.evidence.value.trim(),
   paths:form.elements.paths.value.split('\n').map(p=>p.trim()).filter(Boolean)};
  if(!args.candidate||!args.requirements){$('#evaluation-error').textContent='请填写评测对象和评测要求。';return;}
  if(form.elements.source_task_id.value)args.source_task_id=form.elements.source_task_id.value;
  button.disabled=true;
  try{
   const result=await api('tasks',{prompt:'用户主动评测：'+args.subject_type+'\n评测要求：'+args.requirements,
    session_id:evaluationSession,action:{tool:'delivery_evaluate',arguments:args}});
   session=result.session_id;evaluationDialog.close();form.reset();showPage('chat');await renderMessages();await refresh();
  }catch(error){$('#evaluation-error').textContent=error.message;}finally{button.disabled=false;}
 });
};
async function copyBubble(text){
 try{await navigator.clipboard.writeText(text);}catch(error){
  const field=el('textarea','clipboard-fallback');field.value=text;document.body.append(field);field.select();const ok=document.execCommand('copy');field.remove();$('#prompt').focus();if(!ok)throw Error('复制失败，请选择文本后复制。');
 }toast('已复制');
}
function bubbleActions(body,nodeId,text){
 let actions=body.querySelector(':scope > .bubble-actions');
 if(!actions){
  actions=el('div','bubble-actions');body.append(actions);
  const copy=btn('',()=>copyBubble(actions.value.text||''));copy.title='复制文本';copy.setAttribute('aria-label','复制文本');
  copy.innerHTML='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="8" width="14" height="14" rx="2"/><path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2"/></svg>';
  const branch=btn('',async()=>{if(branch.disabled)return;branch.disabled=true;try{const fork=await api('session-fork',{session_id:session,node_id:actions.value.nodeId});session=fork.session_id;showArchived=false;$('#prompt').value='';showPage('chat');await refresh();await renderMessages();$('#prompt').focus();toast('已分支到新聊天');}finally{branch.disabled=false;}});
  branch.innerHTML='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="6" r="3"/><path d="M6 9v6M9 18h1a8 8 0 0 0 8-8V9"/></svg>';
  branch.title='分支到新聊天';branch.setAttribute('aria-label','分支到新聊天');actions.append(copy,branch);
 }
 actions.value={nodeId,text};actions.lastElementChild.disabled=!nodeId||state?.tasks.some(t=>t.session_id===session&&busy(t));
}
async function openStepTree(){treeData=await api('tree?session='+encodeURIComponent(session));treeSearch.value='';renderStepTree(treeData.session.active_leaf);treeDialog.showModal();highlightTree();treeSearch.focus();}
function renderStepTree(selected){
 const children=new Map();for(const n of treeData.nodes){const key=n.parent_id||null;if(!children.has(key))children.set(key,[]);children.get(key).push(n);}
 const ordered=[{id:null,kind:'root',content:'会话起点',depth:0}];const stack=(children.get(null)||[]).map(n=>({...n,depth:1})).reverse();
 while(stack.length){const n=stack.pop();ordered.push(n);stack.push(...(children.get(n.id)||[]).map(c=>({...c,depth:n.depth+1})).reverse());}
 const query=treeSearch.value.toLocaleLowerCase();treeRows=ordered.filter(n=>(n.kind+' '+n.content+' '+(n.data?.tool||'')).toLocaleLowerCase().includes(query));
 treeIndex=Math.max(0,treeRows.findIndex(n=>n.id===selected));treeList.replaceChildren();
 treeRows.forEach((n,i)=>{const item=btn((n.id===treeData.session.active_leaf?'● ':'')+n.kind+' · '+(n.data?.tool||'')+' '+n.content.replace(/\n/g,' '),()=>selectAppStep(n.id),'tree-option '+n.kind);item.style.paddingLeft=Math.min(n.depth,8)*14+12+'px';item.setAttribute('role','option');item.dataset.index=i;treeList.append(item);});highlightTree();
}
function highlightTree(){[...treeList.children].forEach((item,i)=>{item.classList.toggle('selected',i===treeIndex);item.setAttribute('aria-selected',String(i===treeIndex));});treeList.children[treeIndex]?.scrollIntoView({block:'nearest'});}
async function selectAppStep(id){const result=await api('tree-navigate',{session_id:session,node_id:id});treeDialog.close();$('#prompt').value=result.input;showPage('chat');await refresh();await renderMessages();$('#prompt').focus();toast('已切换历史位置；下一条消息将在当前对话内形成分支。');}
treeSearch.oninput=()=>renderStepTree();
treeDialog.addEventListener('keydown',e=>{if(e.isComposing)return;if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();treeIndex=Math.max(0,Math.min(treeRows.length-1,treeIndex+(e.key==='ArrowUp'?-1:1)));highlightTree();}else if(e.key==='Enter'&&e.target.tagName!=='BUTTON'&&treeRows.length){e.preventDefault();safe(()=>selectAppStep(treeRows[treeIndex].id));}});
