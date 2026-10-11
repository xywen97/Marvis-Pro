// NODE_PATH=/tmp/marvis-render-qa/node_modules node tests/test_streaming_ui.cjs
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py')],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try {
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',code=>reject(new Error('fixture exited '+code)));}));
  browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1380,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  await page.locator('#prompt').fill('请给出一段逐步生成的示例');await page.locator('#prompt').press('Enter');
  const bubble=page.locator('.message.assistant.streaming');await bubble.waitFor();
  const first=await bubble.locator('.message-content').textContent();assert(!first.includes('最后一段'));
  const samples=[];
  while(await bubble.count()){
   const text=await bubble.locator('.message-content').textContent().catch(()=>null);if(text)samples.push(text);
   await page.waitForTimeout(100);
  }
  assert(new Set(samples).size>=3,'must paint at least three different partial replies');
  const answer=page.locator('.message.assistant:not(.streaming)');await answer.waitFor();
  assert((await answer.textContent()).includes('最后一段'));assert.equal(await answer.locator('pre code').textContent(),'print("逐步显示")\n');
  assert.equal(await page.locator('.message.assistant').count(),1,'final reply replaces the pending bubble');
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
