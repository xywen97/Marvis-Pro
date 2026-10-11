// NODE_PATH=/tmp/marvis-render-qa/node_modules node tests/test_empty_sessions_ui.cjs
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py'),'--empty-duplicates'],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try {
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',code=>reject(new Error('fixture exited '+code)));}));
  browser=await chromium.launch({headless:true});const page=await browser.newPage();
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  const initial=await page.evaluate(()=>session);
  assert.equal(await page.locator('#sessions button').count(),1,'historical duplicate empty entries should be hidden');
  await page.locator('#prompt').fill('未发送的草稿');
  await page.evaluate(()=>{for(let i=0;i<20;i++)document.querySelector('#new-chat').click();});
  await page.waitForFunction(()=>!newSessionPending);await page.waitForTimeout(200);
  assert.equal(await page.evaluate(()=>session),initial);
  assert.equal(await page.locator('#prompt').inputValue(),'未发送的草稿');
  assert.equal(await page.evaluate(()=>state.sessions.length),2,'clicks must not create any new records');
  await page.locator('#prompt').press('Enter');await page.waitForFunction(()=>!!document.querySelector('#messages > .message.assistant'));
  await page.locator('#new-chat').click();await page.waitForFunction(old=>session!==old,initial);
  const empty=await page.evaluate(()=>session);
  assert.equal(await page.locator('#messages .message').count(),0);
  await page.evaluate(()=>{for(let i=0;i<20;i++)document.querySelector('#new-chat').click();});
  await page.waitForFunction(()=>!newSessionPending);await page.waitForTimeout(200);
  assert.equal(await page.evaluate(()=>session),empty);assert.equal(await page.evaluate(()=>state.sessions.length),2);
  assert.equal(await page.locator('#sessions button').count(),2,'filled history and one empty draft remain visible');
  console.log('Empty sessions UI passed: rapid clicks reuse current draft, unsent input survives, historical duplicate empties hidden, populated history preserved.');
 } finally {if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
