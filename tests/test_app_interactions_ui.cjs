const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py'),'--tools'],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try{
  const lines=readline.createInterface({input:fixture.stdout});const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',c=>reject(Error('fixture exited '+c)));}));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const context=await browser.newContext({viewport:{width:1480,height:1000},permissions:['clipboard-read','clipboard-write']});const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>liveConnected&&appCommands.length>0);
  let selectedFiles=['/授权目录/带 空格.txt','/授权目录/second.md'];let pickerFailed=false;
  await page.route('**/api/pick-files',route=>route.fulfill({status:pickerFailed?400:200,contentType:'application/json',body:JSON.stringify(pickerFailed?{error:'所选文件不在授权目录内'}:{paths:selectedFiles})}));
  await page.locator('#prompt').fill('请读取');await page.locator('#attach').click();
  await page.waitForFunction(()=>document.querySelector('#prompt').value.includes('second.md'));
  assert.equal(await page.locator('#prompt').inputValue(),'请读取 "/授权目录/带 空格.txt" "/授权目录/second.md"');
  assert.equal(await page.locator('#path-dialog[open]').count(),0);
  selectedFiles=[];await page.locator('#attach').click();await page.waitForFunction(()=>!document.querySelector('#attach').disabled);
  assert.equal(await page.locator('#prompt').inputValue(),'请读取 "/授权目录/带 空格.txt" "/授权目录/second.md"');
  pickerFailed=true;await page.locator('#attach').click();await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('授权目录'));
  assert(await page.locator('#attach').isEnabled());await page.locator('#prompt').fill('');
  await page.locator('#prompt').fill('/t');assert(await page.locator('#command-menu').isVisible());assert((await page.locator('#command-menu').textContent()).includes('/tree (t)'));
  await page.locator('#prompt').press('Tab');assert.equal(await page.locator('#prompt').inputValue(),'/tree ');
  await page.locator('#prompt').press('Enter');await page.waitForSelector('#tree-dialog[open]');assert(await page.locator('#tree-dialog').isVisible());await page.locator('#tree-dialog').getByRole('button',{name:'关闭',exact:true}).click();
  assert.equal(await page.evaluate(()=>state.tasks.length),0);
  await page.locator('#prompt').fill('/h');await page.locator('#prompt').press('Enter');await page.waitForSelector('.command-dialog[open]');assert((await page.locator('.command-dialog pre').textContent()).includes('/quit (q)'));await page.locator('.command-dialog button').click();
  await page.locator('#prompt').fill('第一条分支测试');await page.locator('#prompt').press('Enter');await page.waitForFunction(()=>state.tasks[0]?.status==='completed');await page.waitForSelector('.message.assistant .bubble-actions');
  const original=await page.evaluate(()=>session);
  const source=await page.evaluate(async()=>await api('tree?session='+session));
  const answer=page.locator('.message.assistant .message-body').last();await answer.getByRole('button',{name:'复制文本',exact:true}).click();
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),source.nodes.findLast(n=>n.kind==='assistant').content);
  assert(await page.locator('.assistant-note .bubble-actions').count()>0);
  await answer.getByRole('button',{name:'分支到新聊天',exact:true}).click();await page.waitForFunction(sid=>session!==sid,original);await page.waitForFunction(()=>state.sessions.length===2);
  const fork=await page.evaluate(()=>session);assert.notEqual(fork,original);
  assert.deepEqual(await page.evaluate(async sid=>await api('tree?session='+sid),original),source);
  await page.locator('#prompt').fill('/t');await page.locator('#prompt').press('Enter');await page.waitForSelector('#tree-dialog[open]');
  await page.locator('#tree-dialog .tree-option.user').click();await page.waitForFunction(()=>document.querySelector('#prompt').value==='第一条分支测试');
  assert.equal(await page.evaluate(()=>session),fork);assert.equal(await page.locator('#messages .message').count(),0);
  await page.locator('#prompt').fill('/does-not-exist');await page.locator('#prompt').press('Enter');await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('未知命令'));
  assert.equal(await page.locator('#prompt').inputValue(),'/does-not-exist');
  await page.locator('#prompt').fill('修改后的问题');await page.locator('#prompt').press('Enter');await page.waitForFunction(()=>state.tasks[0]?.status==='completed'&&document.querySelectorAll('#messages .message.assistant').length>0);
  await page.locator('#prompt').fill('/tree');await page.locator('#prompt').press('Enter');await page.waitForSelector('#tree-dialog[open]');
  await page.screenshot({path:'/tmp/marvis-app-tree.png'});
  assert.deepEqual(errors,[]);console.log('App interactions passed: slash aliases/completion/help, no model task for commands, exact clipboard copy, thought actions, independent fork/source preservation, same-session tree editor, unknown-command protection.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
