'use strict';
// Parse Markdown, sanitize the generated HTML, then attach trusted UI controls.
window.MarvisMarkdown = (() => {
 function release(root) {
  root.querySelectorAll('img').forEach(img => {
   img._abort?.abort();
   if (img._objectURL) URL.revokeObjectURL(img._objectURL);
  });
 }
 async function copy(text, button) {
  try {
   if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
   else {
    const field = document.createElement('textarea'); field.value = text; field.className = 'copy-buffer';
    document.body.append(field); field.select();
    const success = document.execCommand('copy'); field.remove();
    if (!success) throw new Error('copy failed');
   }
   button.textContent = '已复制';
  } catch { button.textContent = '复制失败，请手动选择'; }
  setTimeout(() => { button.textContent = '复制代码'; }, 1800);
 }
 function preview(img) {
  const dialog = document.createElement('dialog'); dialog.className = 'image-preview';
  const close = document.createElement('button'); close.textContent = '关闭'; close.type = 'button';
  const large = document.createElement('img'); large.src = img.src; large.alt = img.alt;
  dialog.append(close, large); document.body.append(dialog);
  close.onclick = () => dialog.close();
  dialog.onclick = event => { if (event.target === dialog) dialog.close(); };
  dialog.onclose = () => dialog.remove(); dialog.showModal();
 }
 function image(img, source, options) {
  const caption = document.createElement('span'); caption.className = 'image-status';
  caption.textContent = '图片加载中…'; img.after(caption);
  img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.tabIndex = 0;
  img.onload = () => { caption.remove(); img.classList.add('loaded'); options.onImageLoad?.(); };
  img.onerror = () => { caption.textContent = '图片无法加载：' + (img.alt || source); img.hidden = true; };
  img.onclick = () => preview(img);
  img.onkeydown = e => { if (e.key === 'Enter') preview(img); };
  if (/^https:\/\//i.test(source) && options.remoteImages !== false) img.src = source;
  else if (/^data:image\/(png|jpeg|gif|webp);base64,[a-z\d+/=\s]+$/i.test(source)) img.src = source;
  else if (source && !/^[a-z][a-z\d+.-]*:/i.test(source) && !source.startsWith('//')) {
   if (!options.loadImage) { img.onerror(); return; }
   img._abort = new AbortController();
   let path;
   try { path = decodeURIComponent(source); } catch { img.onerror(); return; }
   options.loadImage(path, img._abort.signal).then(blob => {
    if (!img.isConnected || img._abort.signal.aborted) return;
    img._objectURL = URL.createObjectURL(blob); img.src = img._objectURL;
   }).catch(() => { if (!img._abort.signal.aborted) img.onerror(); });
  } else img.onerror();
 }
 function render(text, options = {}) {
  const root = document.createElement('div'); root.className = 'message-content markdown-body';
  const fragment = DOMPurify.sanitize(marked.parse(String(text || ''), {gfm: true, breaks: true}), {
   RETURN_DOM_FRAGMENT: true,
   ALLOWED_TAGS: ['p','br','hr','h1','h2','h3','h4','h5','h6','strong','em','del','blockquote','ul','ol','li','pre','code','a','img','table','thead','tbody','tr','th','td','input'],
   ALLOWED_ATTR: ['href','src','alt','title','class','align','start','type','checked','disabled'],
  });
  // Remove image sources before inserting into the document; local images need authentication.
  const images = [...fragment.querySelectorAll('img')].map(img => {
   const source = img.getAttribute('src') || ''; img.removeAttribute('src'); return [img, source];
  });
  fragment.querySelectorAll('a').forEach(link => {
   const href = link.getAttribute('href') || '';
   if (!/^(https?:\/\/|mailto:)/i.test(href)) link.removeAttribute('href');
   else { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
  });
  fragment.querySelectorAll('input').forEach(input => {
   if (input.type !== 'checkbox') input.remove(); else input.disabled = true;
  });
  root.append(fragment);
  root.querySelectorAll('pre > code').forEach(code => {
   const raw = code.textContent; const language = (code.className.match(/language-([\w+-]+)/) || [])[1] || '';
   code.className = 'hljs';
   if (raw.length < 100000 && language && hljs.getLanguage(language)) {
    code.innerHTML = hljs.highlight(raw, {language, ignoreIllegals: true}).value;
   }
   const block = document.createElement('div'); block.className = 'code-block';
   const toolbar = document.createElement('div'); toolbar.className = 'code-toolbar';
   const name = document.createElement('span'); name.textContent = language || 'text';
   const button = document.createElement('button'); button.type = 'button'; button.textContent = '复制代码';
   button.onclick = () => copy(raw, button); toolbar.append(name, button);
   const pre = code.parentNode; pre.before(block); block.append(toolbar, pre);
  });
  root.querySelectorAll('table').forEach(table => {
   const scroll = document.createElement('div'); scroll.className = 'table-scroll';
   scroll.tabIndex = 0; scroll.setAttribute('aria-label', '表格，可横向滚动'); table.before(scroll); scroll.append(table);
  });
  images.forEach(([img, source]) => image(img, source, options));
  return root;
 }
 return {render, release};
})();
