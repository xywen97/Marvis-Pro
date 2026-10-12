// NODE_PATH=/tmp/marvis-render-qa/node_modules node tests/test_progress_ui.cjs
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py'),'--multi-tools'],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try {
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',code=>reject(new Error('fixture exited '+code)));}));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage({viewport:{width:1380,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.context().grantPermissions(['clipboard-read','clipboard-write']);
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  await page.locator('#prompt').fill('请做一份本机健康评估');await page.locator('#prompt').press('Enter');
  const thought=page.locator('#messages > .message.assistant').first();await thought.waitFor();
  const saved=await thought.elementHandle();const samples=[];
  for(let n=0;n<8;n++){samples.push(await thought.textContent());await page.waitForTimeout(110);}
  assert(new Set(samples).size>=3,'public note should reveal multiple partial frames');
  assert(await saved.evaluate(node=>node===document.querySelector('#messages > .message.assistant')),'bubble DOM must survive updates');
  assert.equal(await thought.locator('.message-heading').textContent(),'MarvisXY');
  assert.equal(await page.locator('.chat-trace .message').count(),0,'model prose must stay outside execution traces');
  const tools=page.locator('.agent-tool');await tools.first().waitFor({state:'attached'});
  const traces=page.locator('.trace-details');assert.equal(await traces.first().getAttribute('open'),null);
  await page.waitForFunction(()=>document.querySelector('.trace-preview')?.textContent.includes('system_info'));
  const savedTrace=await traces.first().elementHandle();await traces.first().locator(':scope > summary').click();
  assert((await tools.first().textContent()).includes('生成调用'),'tool should appear while its arguments are generated');
  const details=tools.first().locator('details');assert.equal(await details.getAttribute('open'),null);
  await details.locator('summary').click();const savedDetails=await details.elementHandle();
  await page.waitForFunction(()=>document.querySelectorAll('.agent-tool').length===2);
  // The UI preserves details for either a successful call or an ownership rejection
  // from the independent-agent coordinator (this fixture calls physical tools directly).
  await page.waitForFunction(()=>/返回结果|错误/.test(document.querySelector('.tool-payload')?.textContent||''));
  assert(await savedDetails.evaluate(node=>node.isConnected&&node.open),'expanded result must stay open across streaming updates');
  assert(await savedTrace.evaluate(node=>node.isConnected&&node.open),'outer trace must preserve its expanded state');
  assert(await details.locator('pre').last().textContent().then(t=>t.includes('total_gb')||t.includes('不能调用其他 Agent 的工具')));
  assert.equal(await tools.nth(1).locator('details').getAttribute('open'),null);
  const secondNote=page.locator('#messages > .message.assistant').nth(1);await secondNote.waitFor();
  const secondNoteNode=await secondNote.elementHandle();
  const response=page.locator('#messages > .message.assistant').nth(2);await response.waitFor();
  const responseNode=await response.elementHandle();const responseSamples=[];
  while(await response.getAttribute('class').then(value=>value.includes('streaming'))){
   responseSamples.push(await response.locator('.message-text').textContent());await page.waitForTimeout(90);
  }
  await page.waitForFunction(()=>state.tasks[0]?.status==='completed');
  assert(new Set(responseSamples).size>=3,'final answer must stream directly in its own conversation bubble');
  assert(await responseNode.evaluate(node=>node===document.querySelectorAll('#messages > .message.assistant')[2]),'completion retains the streaming reply DOM');
  assert.equal(await page.locator('.agent-step').count(),2,'only tool-bearing steps belong in execution traces');
  assert.equal(await traces.count(),2,'different execution blocks must stay separate');
  assert.equal(await traces.nth(1).getAttribute('open'),null,'new blocks must default to collapsed independently');
  await traces.nth(1).locator(':scope > summary').click();
  await traces.first().locator(':scope > summary').click();
  assert.equal(await traces.first().getAttribute('open'),null);
  assert.notEqual(await traces.nth(1).getAttribute('open'),null);
  const previewChecks=await page.evaluate(()=>{
   const root=document.createElement('div');document.body.append(root);
   const task={status:'running',events:[{id:1,kind:'model_start',content:{step:1}},{id:2,kind:'tool_start',content:{step:1,call_id:'a',tool:'agent_dispatch',arguments:{task:'检查😀'.repeat(40)}}}]};
   AgentProgress.updateTrace(root,task);const detail=root.firstElementChild;
   const truncated=root.querySelector('.trace-preview').textContent;
   detail.open=true;
   task.events.push({id:3,kind:'tool_result',content:{step:1,call_id:'a',result:{}}},{id:4,kind:'tool_start',content:{step:1,call_id:'b',tool:'agent_report',arguments:{id:'worker'}}});
   AgentProgress.updateTrace(root,task);
   const next=root.querySelector('.trace-preview').textContent;
   const stable=root.firstElementChild===detail&&detail.open;
   task.status='completed';AgentProgress.updateTrace(root,task);
   const completed=root.querySelector('.trace-preview').textContent;
   root.remove();return {truncated,next,stable,completed};
  });
  assert.equal(Array.from(previewChecks.truncated).length,50);assert(previewChecks.truncated.endsWith('...'));
  assert.equal(previewChecks.next,'agent_report {"id":"worker"}');assert(previewChecks.stable);assert.equal(previewChecks.completed,'');
  await page.evaluate(()=>{document.querySelector('.tool-details').open=false;});
  await page.evaluate(()=>runAppCommand('/result 1'));
  assert.notEqual(await traces.first().getAttribute('open'),null,'result command must expand its containing trace');
  assert.notEqual(await details.getAttribute('open'),null);
  await traces.first().locator(':scope > summary').focus();await page.keyboard.press('Enter');
  assert.equal(await traces.first().getAttribute('open'),null,'native collapse must work from the keyboard');
  assert((await thought.textContent()).includes('随后整理检查结果。'));
  assert.equal(await page.locator('#messages > .message.assistant').count(),3);
  assert(await secondNoteNode.evaluate(node=>node===document.querySelectorAll('#messages > .message.assistant')[1]));
  assert((await secondNote.textContent()).includes('然后给出完整总结。'));
  const order=await page.locator('#messages > *').evaluateAll(nodes=>nodes.map(node=>node.classList.contains('user')?'user':node.classList.contains('message')?'reply':'tools'));
  assert.deepEqual(order,['user','reply','tools','reply','tools','reply']);
  assert.equal(await page.locator('.chat-trace .message').count(),0);
  assert.equal(await thought.locator('.bubble-actions button').count(),2);
  assert(await thought.getByRole('button',{name:'分支到新聊天',exact:true}).isEnabled());
  await page.evaluate(()=>document.body.classList.add('hide-traces'));
  assert(await thought.isVisible());assert(await response.isVisible());
  await page.evaluate(()=>document.body.classList.remove('hide-traces'));
  assert.equal(await tools.count(),3);assert.deepEqual(errors,[]);
  await page.evaluate(()=>{document.querySelector('.tool-details').open=false;document.querySelector('#chat-scroll').scrollTop=0;followTail=false;});
  await page.screenshot({path:'/tmp/marvis-agent-steps.png'});
  const original=await page.evaluate(()=>session);const source=await page.evaluate(async()=>await api('tree?session='+session));
  const noteText=await thought.locator('.bubble-actions').evaluate(node=>node.value.text);
  await thought.getByRole('button',{name:'复制文本',exact:true}).click();
  await page.waitForFunction(async text=>(await navigator.clipboard.readText())===text,noteText);
  await thought.getByRole('button',{name:'分支到新聊天',exact:true}).click();
  await page.waitForFunction(sid=>session!==sid,original);
  const fork=await page.evaluate(async()=>await api('tree?session='+session));
  assert.equal(fork.nodes.at(-1).kind,'thinking');assert.equal(fork.nodes.at(-1).content,noteText);
  assert.deepEqual(await page.evaluate(async sid=>await api('tree?session='+sid),original),source);
  await page.waitForFunction(()=>document.querySelectorAll('#messages > .message.assistant').length===1);
  await page.reload();await page.waitForFunction(()=>liveConnected);
  await page.waitForSelector('#messages > .message.assistant');
  assert.equal(await page.locator('.chat-trace .message').count(),0);
  assert(await page.locator('#messages > .message.assistant').getByRole('button',{name:'分支到新聊天',exact:true}).isEnabled());
  console.log(`Agent steps UI passed: ${new Set(samples).size} note frames; independent default collapse; live tool/argument preview; 50 Unicode characters; stable expanded state; result command; keyboard toggle.`);
 } finally {if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
