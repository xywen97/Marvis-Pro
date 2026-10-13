// NODE_PATH=/tmp/marvis-render-qa/node_modules node tests/test_streaming_ui.cjs
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py'),'--burst-tail'],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try {
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',code=>reject(new Error('fixture exited '+code)));}));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage({viewport:{width:1380,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  await page.locator('#prompt').fill('请给出一段逐步生成的示例');await page.locator('#prompt').press('Enter');
  const bubble=page.locator('.message.assistant.streaming');await bubble.waitFor();
  const originalBubble=await bubble.elementHandle();
  assert.equal(await page.locator('.chat-trace .message').count(),0);
  const first=await bubble.locator('.message-content').textContent();assert(!first.includes('最后一段'));
  const samples=[];
  await page.evaluate(()=>{
   window.finishFrames=[];
   new MutationObserver(()=>{
    if(state.tasks[0]?.status==='completed')finishFrames.push(document.querySelector('.message.assistant .message-text')?.textContent||'');
   }).observe(document.querySelector('#messages'),{subtree:true,childList:true,attributes:true,characterData:true});
  });
  while(await bubble.count()){
   const text=await bubble.locator('.message-content').textContent().catch(()=>null);if(text)samples.push(text);
   await page.waitForTimeout(100);
  }
  assert(new Set(samples).size>=3,'must paint at least three different partial replies');
  const answer=page.locator('.message.assistant:not(.streaming)');await answer.waitFor();
  assert(await originalBubble.evaluate(node=>node===document.querySelector('.message.assistant')),'streaming reply remains the same node at completion');
  assert((await answer.textContent()).includes('最后一段'));assert.equal(await answer.locator('pre code').textContent(),'print("逐步显示")\n');
  await page.waitForFunction(()=>{const body=document.querySelector('.message.assistant .message-text');return body&&body._shown===body._target;});
  const finishFrames=await page.evaluate(()=>finishFrames);
  assert(new Set(finishFrames).size>=2,'completion must continue revealing the burst tail instead of flushing it at once');
  assert.equal(await page.locator('.message.assistant').count(),1,'completed reply must not duplicate its streaming bubble');
  const handoff=await page.evaluate(async()=>{
   // The messages endpoint can return the saved answer while /api/live still
   // holds an earlier task snapshot. Force that ordering deterministically.
   const originalState=state,originalApi=api,originalSession=session;
   const task=structuredClone(state.tasks[0]);
   const messages=await api('messages?session='+session);
   const final=messages.find(message=>message.role==='assistant');
   const stream=task.events.find(event=>event.kind==='assistant_stream');
   const originalNode=document.querySelector('.message.assistant');
   task.status='running';stream.content.text=final.content.slice(0,70);
   try{
    api=async()=>messages;state={...state,tasks:[task]};
    await renderMessages();
    const row=document.querySelector('.message.assistant');
    const result={count:document.querySelectorAll('.message.assistant').length,
     streaming:row.classList.contains('streaming'),sameNode:row===originalNode,
     target:row.querySelector('.message-text')._target,entryId:row.dataset.entryId,final:final.content,id:final.id};
    // A complete stream may also differ from the saved answer's formatting.
    task.status='completed';stream.content.text=final.content+'\n\n附加的流式尾部';
    await renderMessages();
    result.rewrittenCount=document.querySelectorAll('.message.assistant').length;
    result.rewrittenTarget=document.querySelector('.message-text.animated-text')._target;
    return result;
   }finally{api=originalApi;state=originalState;session=originalSession;await renderMessages();}
  });
  assert.equal(handoff.count,1,'a stale partial snapshot and persisted answer must share one bubble');
  assert.equal(handoff.streaming,false,'a saved answer must clear the streaming cursor even when task status is stale');
  assert.equal(handoff.sameNode,true,'final handoff must preserve the original bubble');
  assert.equal(handoff.target,handoff.final,'saved answer is authoritative');
  assert.equal(handoff.entryId,handoff.id,'saved answer keeps copy and branch actions');
  assert.equal(handoff.rewrittenCount,1,'formatting differences must not duplicate the saved answer');
  assert.equal(handoff.rewrittenTarget,handoff.final);
  assert.deepEqual(errors,[]);
  // Reconnection and the polling fallback must also show unfinished responses.
  await page.route('**/api/live',route=>route.abort());await page.evaluate(()=>liveAbort.abort());
  await page.locator('#prompt').fill('再生成一次示例');await page.locator('#prompt').press('Enter');
  await bubble.waitFor();assert(!(await bubble.textContent()).includes('最后一段'));
  await page.waitForFunction(()=>!document.querySelector('.streaming'));
  assert.equal(await page.locator('.message.assistant').count(),2);assert.deepEqual(errors,[]);
  console.log(`Streaming UI passed: ${new Set(samples).size} partial frames before completion, rendered final code, no duplicate reply, polling fallback.`);
 } finally {if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
