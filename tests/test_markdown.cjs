// npm install --prefix /tmp/marvis-render-qa jsdom@26.1.0
// NODE_PATH=/tmp/marvis-render-qa/node_modules node tests/test_markdown.cjs
const {JSDOM} = require('jsdom');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const dom = new JSDOM('<!doctype html><body></body>', {runScripts:'outside-only', url:'http://127.0.0.1:1234/'});
const {window} = dom;
for (const file of ['vendor/marked.js','vendor/purify.js','vendor/highlight.js','markdown.js']) {
 window.eval(fs.readFileSync(path.join(__dirname,'../ui',file),'utf8'));
}
const sample = '# 标题\n\n**基本面**\n\n| 项目 | 内容 |\n|---|---|\n| 公司 | 腾讯 |\n\n```python\nprint("你好")\n```\n\n> 引用\n\n- 列表\n- [x] 完成\n\n![演示](https://example.com/image.png)\n\n[链接](https://example.com)';
const render = text => {const root=window.MarvisMarkdown.render(text);window.document.body.append(root);return root;};
const root=render(sample);
assert.equal(root.querySelector('h1').textContent,'标题');
assert.equal(root.querySelector('strong').textContent,'基本面');
assert.equal(root.querySelector('tbody td').textContent,'公司');
assert(root.querySelector('.table-scroll > table'));
assert(root.querySelector('.hljs-built_in'));
assert.equal(root.querySelector('pre code').textContent,'print("你好")\n');
assert.equal(root.querySelector('.code-toolbar span').textContent,'python');
assert.equal(root.querySelector('.code-toolbar button').textContent,'复制代码');
assert(root.querySelector('blockquote'));assert(root.querySelector('input[disabled]'));
assert.equal(root.querySelector('img').src,'https://example.com/image.png');
assert.equal(root.querySelector('a').rel,'noopener noreferrer');
const attack=render('<script>alert(1)</script><img src=x onerror=alert(1)><svg onload=alert(1)></svg><iframe src="https://evil.example"></iframe>\n\n[bad](javascript:alert(1))\n\n```html\n<script>alert(1)</script>\n```');
assert.equal(attack.querySelectorAll('script,svg,iframe,[onerror],[onload],a[href]').length,0);
assert(attack.querySelector('pre').textContent.includes('<script>alert(1)</script>'));
const partial=render('```js\nconst result =');assert(partial.querySelector('pre code'));
const unknown=render('```unknown-language\n<a>literal</a>\n```');assert.equal(unknown.querySelector('code').textContent,'<a>literal</a>\n');
let copied='';Object.defineProperty(window.navigator,'clipboard',{value:{writeText:async text=>{copied=text;}}});
root.querySelector('.code-toolbar button').click();assert.equal(copied,'print("你好")\n');
let localPath='';const local=window.MarvisMarkdown.render('![本地](/Users/ice/a%20b.png)',{loadImage:async p=>{localPath=p;return new window.Blob();}});
assert.equal(localPath,'/Users/ice/a b.png');
const offline=window.MarvisMarkdown.render('![外部](https://example.com/a.png)',{remoteImages:false});
assert(!offline.querySelector('img').hasAttribute('src'));
assert.doesNotThrow(()=>window.MarvisMarkdown.render('![坏路径](/bad%ZZ.png)',{loadImage:async()=>new window.Blob()}));
console.log('Markdown rendering, tables, highlighted code, copy, images, streaming and HTML sanitization passed.');
