'use strict';
window.MarvisNotifications=(()=>{
 const seen=new Map(), titles={completed:'任务已完成',failed:'任务失败',cancelled:'任务已停止',interrupted:'任务已中断',waiting_approval:'需要你的授权'};
 let initialized=false;
 async function openTask(id){
  await refresh();
  const task=state?.tasks.find(t=>t.id===id);
  if(!task){toast('这条任务已删除或不在最近任务中。');return;}
  session=task.session_id;showArchived=!!allSessions().find(s=>s.id===session)?.archived;
  showPage('chat');await renderMessages();renderState();
  if(task.approval){approvalId=task.id;$('#approval-plan').textContent=JSON.stringify(task.approval,null,2);if(!$('#approval-dialog').open)$('#approval-dialog').showModal();}
  $('#chat-scroll').scrollTop=$('#chat-scroll').scrollHeight;
 }
 function show(task){
  const container=$('#task-notifications'),card=el('div','task-notification');card.dataset.task=task.id;card.dataset.status=task.status;
  container.querySelector(`[data-task="${CSS.escape(task.id)}"]`)?.remove();
  const open=btn('',()=>openTask(task.id),'task-notification-open');
  open.append(el('strong','',`MarvisXY · ${titles[task.status]}`),el('span','',task.prompt.slice(0,100)),el('small','','点击打开对话'));
  const close=btn('×',()=>card.remove(),'task-notification-close');close.setAttribute('aria-label','关闭通知');
  card.append(open,close);container.prepend(card);
  while(container.children.length>4)container.lastElementChild.remove();
  if(task.status!=='waiting_approval')setTimeout(()=>card.remove(),15000);
  if(state.notification_host!=='desktop'&&'Notification' in window&&Notification.permission==='granted'){
   try{
    const notice=new Notification(`MarvisXY · ${titles[task.status]}`,{body:task.prompt.slice(0,100)+'\n点击打开对应对话',tag:task.id});
    notice.onclick=()=>{window.focus();safe(()=>openTask(task.id));notice.close();};
   }catch(e){console.warn('系统通知不可用',e);}
  }
 }
 function update(snapshot){
  for(const card of $('#task-notifications').children){
   const task=snapshot.tasks.find(t=>t.id===card.dataset.task);
   if(!task||(card.dataset.status==='waiting_approval'&&task.status!=='waiting_approval'))card.remove();
  }
  for(const task of snapshot.tasks){
   const approval=task.events?.filter(e=>e.kind==='approval').at(-1)?.id;
   const key=task.status+(task.status==='waiting_approval'?':'+approval:'');
   const detailsArrived=task.status==='waiting_approval'&&seen.get(task.id)==='waiting_approval:undefined';
   if(seen.get(task.id)!==key&&!detailsArrived&&titles[task.status]&&(initialized||task.status==='waiting_approval'))show(task);
   seen.set(task.id,key);
  }
  initialized=true;
  const enable=$('#enable-notifications');enable.hidden=snapshot.notification_host==='desktop';
  enable.disabled=!('Notification' in window)||Notification.permission==='granted';
  enable.textContent='Notification' in window&&Notification.permission==='granted'?'系统通知已开启':'开启系统通知';
 }
 async function enable(){
  if(!('Notification' in window)){toast('当前浏览器不支持系统通知，应用内提醒已开启。');return;}
  const permission=await Notification.requestPermission();
  toast(permission==='granted'?'系统通知已开启。':'请在浏览器的网站设置中允许通知；应用内提醒已开启。');
  if(state)update(state);
 }
 return {update,openTask,enable};
})();
