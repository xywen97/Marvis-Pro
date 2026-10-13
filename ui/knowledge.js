'use strict';
window.KnowledgeUI=(()=>{
 let mode='keyword',offset=0,sequence=0,previewSequence=0,indexTask=null,indexPath='',timer=null,selected=null,lastResult=null;
 const labels={ready:'语义就绪',processing:'生成语义索引',pending:'待同步',failed:'同步失败',stale:'需要更新',metadata:'仅元数据'};
 function stats(sync){
  $('#kb-total').textContent=sync.total;$('#kb-ready').textContent=sync.ready;$('#kb-stale').textContent=sync.stale;
  $('#kb-progress').textContent=`${sync.processing} 处理中 · ${sync.pending} 待同步 · ${sync.failed} 失败`;
  $('#kb-service-label').textContent=sync.message;$('#kb-service-dot').classList.toggle('connected',sync.available);
  $('#kb-sync-detail').textContent=sync.error||`可同步 ${sync.eligible} 份文档 · 文本同步至配置的 OpenViking 服务；图片保留元数据。`;
 }
 async function load({silent=false}={}){
  const id=++sequence,box=$('#knowledge-list');
  const query=$('#knowledge-query').value,category=$('#knowledge-filter').value;
  const params=new URLSearchParams({q:query,mode,category,offset});
  if(!silent){box.setAttribute('aria-busy','true');$('#kb-result-label').textContent=query.trim()?'正在搜索…':'正在加载资料…';}
  try{
   const result=await api('knowledge?'+params);
   if(id!==sequence)return;
   stats(result.sync);$('#kb-notice').textContent=result.notice||'';
   $('#kb-result-label').textContent=result.status==='unavailable'?'语义搜索暂不可用':`${query.trim()?(mode==='semantic'?'语义搜索':'关键词搜索'):'全部已索引资料'} · ${result.total} 份`;
   $('#kb-prev').disabled=offset===0;$('#kb-next').disabled=!result.has_more;$('#kb-page').textContent=`${Math.floor(offset/24)+1} / ${Math.max(1,Math.ceil(result.total/24))}`;
   const signature=JSON.stringify(result.files);
   if(signature!==lastResult){lastResult=signature;box.replaceChildren();
    if(!result.files.length){
     const empty=el('div','kb-empty');empty.append(el('span','',result.status==='unavailable'?'✦':'▤'),el('h3','',result.status==='unavailable'?'语义引擎尚未就绪':query.trim()?'没有找到匹配资料':category?'这个类型下暂无资料':'你的知识空间，等待第一份资料'),el('p','',result.status==='unavailable'?'启用 OpenViking 服务后重试，或切换到关键词搜索。':query.trim()?'试试更短的关键词，或用语义搜索描述你要找的内容。':category?'切换文件类型，或添加包含这类文件的目录。':'在上方添加资料目录并建立索引，文件会出现在这里。'));
     if(result.status==='unavailable')empty.append(btn('打开服务设置',()=>showPage('settings')));
     box.append(empty);
    }
    for(const f of result.files){
     const row=el('article','kb-file');row.dataset.path=f.path;row.classList.toggle('selected',selected===f.path);
     const top=el('div','kb-file-top'),icon=el('span','kb-file-icon',f.category==='图片'?'▧':f.category==='代码'?'⌘':f.category==='表格'?'▦':'▤'),info=el('div','kb-file-info');
     const title=btn(f.name,()=>preview(f),'kb-file-title');info.append(title,el('small','kb-file-path',f.path));top.append(icon,info,el('span','kb-state '+f.semantic_status,labels[f.semantic_status]||'待同步'));row.append(top);
     if(f.preview)row.append(el('p','kb-file-excerpt',f.preview));
     const bottom=el('div','kb-file-bottom');bottom.append(el('span','',`${f.category} · ${(f.size/1024).toFixed(1)} KB · ${new Date(f.modified*1000).toLocaleDateString()}`));
     const actions=el('div','kb-file-actions');actions.append(btn('预览',()=>preview(f)),btn(f.category==='图片'?'OCR ↗':'让 AI 阅读 ↗',()=>submit(f.category==='图片'?'识别图片文字 '+f.name:'读取 '+f.name,{tool:f.category==='图片'?'image_ocr':'file_read',arguments:{path:f.path}})));
     bottom.append(actions);row.append(bottom);box.append(row);
    }
   }
  }catch(e){if(id===sequence){$('#kb-result-label').textContent='加载失败，请重试';$('#kb-notice').textContent=e.message;if(!silent)toast(e.message);}}
  finally{if(id===sequence)box.setAttribute('aria-busy','false');}
 }
 async function preview(f){
  selected=f.path;const id=++previewSequence,body=$('#kb-preview-body');$('#kb-preview').classList.add('has-selection');
  document.querySelectorAll('.kb-file').forEach(row=>row.classList.toggle('selected',row.dataset.path===f.path));
  body.replaceChildren(el('h3','',f.name),el('p','kb-preview-path',f.path),el('p','hint','正在读取原文件…'));
  try{
   const result=await api((f.category==='图片'?'image':'knowledge-preview')+'?path='+encodeURIComponent(f.path));
   if(id!==previewSequence)return;
   body.replaceChildren(el('h3','',f.name),el('p','kb-preview-path',f.path));
   if(f.category==='图片'){const img=el('img','kb-preview-image');img.alt=f.name;img.src=`data:${result.mime};base64,${result.data}`;body.append(img,el('p','hint','图片文字可通过 OCR 提取。'));}
   else body.append(el('pre','kb-preview-text',result.text||'未提取到文本。扫描版 PDF 可先使用 OCR。'),el('p','hint','预览最多展示 12000 字符。AI 阅读可继续读取完整内容。'));
   body.append(btn(f.category==='图片'?'识别图片文字 ↗':'交给 AI 阅读 ↗',()=>submit('读取 '+f.name,{tool:f.category==='图片'?'image_ocr':'file_read',arguments:{path:f.path}}),'primary'));
  }catch(e){if(id===previewSequence)body.replaceChildren(el('h3','',f.name),el('p','hint',e.message));}
 }
 function reset(){offset=0;load();}
 function init(){
  document.querySelectorAll('[data-kb-mode]').forEach(b=>b.onclick=()=>{mode=b.dataset.kbMode;document.querySelectorAll('[data-kb-mode]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));$('#knowledge-query').placeholder=mode==='semantic'?'描述你想找的内容，例如：长期记忆是如何保存的？':'搜索文件名或文档中的关键词…';$('#kb-search-help').textContent=mode==='semantic'?'用自然语言描述问题，按内容含义查找':'按文件名或文档内容精确匹配';clearTimeout(timer);reset();});
  $('#knowledge-search-form').onsubmit=e=>{e.preventDefault();clearTimeout(timer);reset();};
  $('#knowledge-query').oninput=()=>{clearTimeout(timer);timer=setTimeout(reset,mode==='semantic'?650:250);};
  $('#knowledge-clear').onclick=()=>{clearTimeout(timer);$('#knowledge-query').value='';reset();$('#knowledge-query').focus();};
  $('#knowledge-filter').onchange=reset;$('#knowledge-refresh').onclick=()=>load();
  $('#kb-prev').onclick=()=>{offset=Math.max(0,offset-24);load();};$('#kb-next').onclick=()=>{offset+=24;load();};
  $('#kb-settings').onclick=()=>showPage('settings');
  $('#knowledge-sync').onclick=()=>safe(async()=>{const b=$('#knowledge-sync');b.disabled=true;try{stats(await api('knowledge-sync',{}));await load();toast('已请求同步；完成状态以语义就绪数量为准。');}finally{b.disabled=false;}});
  $('#kb-preview-close').onclick=()=>{++previewSequence;selected=null;$('#kb-preview').classList.remove('has-selection');$('#kb-preview-body').replaceChildren(el('div','kb-preview-empty','选择一份资料预览内容。'));document.querySelectorAll('.kb-file').forEach(r=>r.classList.remove('selected'));};
  $('#index-form').onsubmit=e=>{e.preventDefault();safe(async()=>{const b=$('#kb-index-button');b.disabled=true;indexPath=$('#index-path').value.trim();try{const t=await api('tasks',{prompt:'建立知识库索引',action:{tool:'knowledge_index',arguments:{path:indexPath}}});indexTask=t.id;$('#kb-index-status').textContent='正在扫描目录并提取文本…';await refresh();}catch(e){b.disabled=false;$('#kb-index-status').textContent=e.message;throw e;}});};
  setInterval(()=>{
   if(indexTask&&state){const t=state.tasks.find(x=>x.id===indexTask);if(t&&!busy(t)){indexTask=null;$('#kb-index-button').disabled=false;const r=t.events.find(e=>e.kind==='tool_result'&&e.content.tool==='knowledge_index')?.content.result;$('#kb-index-status').textContent=t.status==='completed'&&r?`索引完成：新增 / 更新 ${r.indexed}，未变化 ${r.unchanged}，文本不可用 ${r.text_unavailable}${r.scan_truncated?' · 扫描达到上限，请缩小目录':''}。语义索引在后台继续处理。`:('索引'+(statuses[t.status]||t.status)+'：'+(t.result||'请到任务中心查看详情'));if(page==='knowledge')load({silent:true});}}
  },500);
  setInterval(()=>{if(page==='knowledge'&&!$('#knowledge-list').matches('[aria-busy="true"]'))load({silent:true});},5000);
 }
 return {init,load};
})();
