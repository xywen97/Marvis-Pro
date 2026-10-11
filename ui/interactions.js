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
 if(result.view==='tree'){await openStepTree();return;}
 if(result.view==='trace'){document.body.classList.toggle('hide-traces');return;}
 if(result.view==='result'){const tools=[...$('#messages .tool-details')];const target=tools[Number(result.arguments[0]||tools.length)-1];if(!target)throw Error('当前对话没有对应工具结果。');target.open=!target.open;return;}
 if(result.view==='quit'){window.close();toast('可使用窗口左上角关闭按钮退出 App。');return;}
 showPage(['chat','tasks','knowledge','workflows','settings'][result.page||0]);
 if(result.query!==undefined)$('#knowledge-query').value=result.query;
 await refresh();await renderMessages();
 if(result.text){commandDialog.querySelector('pre').textContent=result.text;commandDialog.showModal();}
 if(result.notice)toast(result.notice);
}
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
  copy.innerHTML='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="8" y="3" width="13" height="13" rx="3"/><rect x="3" y="8" width="13" height="13" rx="3"/></svg>';
  const branch=btn('',async()=>{if(branch.disabled)return;branch.disabled=true;try{const fork=await api('session-fork',{session_id:session,node_id:actions.value.nodeId});session=fork.session_id;showArchived=false;$('#prompt').value='';showPage('chat');await refresh();await renderMessages();$('#prompt').focus();toast('已分支到新聊天');}finally{branch.disabled=false;}});
  branch.innerHTML='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h6a5 5 0 0 1 5 5v9M10 14l5 5 5-5M14 5h6v6M20 5l-7 7"/></svg>';
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
