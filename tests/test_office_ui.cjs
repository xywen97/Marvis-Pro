// NODE_PATH=/tmp/marvis-render-qa/node_modules node tests/test_office_ui.cjs
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py'),'--team-tools'],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try{
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',code=>reject(new Error('fixture exited '+code)));}));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage({viewport:{width:1480,height:1100}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  await page.locator('[data-page=office]').click();assert.equal(await page.locator('.office-station').count(),9);
  assert.equal(await page.locator('.office-station[data-mode=idle]').count(),9);
  const compacting=await page.evaluate(()=>{
   const snapshot={agents:state.agents,tasks:[{id:'context-qa',status:'running',events:[
    {kind:'context_compaction_start',content:{agent:'context',target_agent:'coding'}}]}]};
   const start=MarvisOffice.activity(snapshot);
   snapshot.tasks[0].events.push({kind:'context_compaction',content:{agent:'coding'}});
   return {start,finish:MarvisOffice.activity(snapshot)};
  });
  assert.equal(compacting.start.context.mode,'thinking');
  assert.equal(compacting.finish.context.mode,'idle');assert.equal(compacting.finish.coding.mode,'thinking');
  const saved=await page.locator('.office-station[data-agent=main]').elementHandle();
  await page.locator('#office-start').click();await page.locator('#prompt').fill('请做一份本机健康评估');await page.locator('#prompt').press('Enter');
  await page.locator('[data-page=office]').click();
  await page.waitForFunction(()=>document.querySelector('.agent-main')?.dataset.mode==='thinking');
  await page.waitForFunction(()=>document.querySelector('.agent-computer')?.dataset.mode==='working');
  assert.equal(await page.locator('#office-active-count').textContent(),'1');
  const first=await page.locator('.agent-computer .mascot').evaluate(n=>getComputedStyle(n).transform);
  await page.waitForTimeout(100);const second=await page.locator('.agent-computer .mascot').evaluate(n=>getComputedStyle(n).transform);
  assert.notEqual(first,second,'working avatar must animate');
  assert(await saved.evaluate(node=>node===document.querySelector('.agent-main')),'avatar must retain its DOM and animation');
  await page.locator('.agent-computer').click();assert.equal(await page.locator('#office-agent-name').textContent(),'电脑管家');
  await page.screenshot({path:path.join(__dirname,'../docs/screenshots/office.png')});
  await page.waitForFunction(()=>document.querySelector('#office-done-count').textContent==='1');
  assert.equal(await page.locator('#office-tool-count').textContent(),'3');
  const waiting=await page.evaluate(()=>{
   const snapshot=JSON.parse(JSON.stringify(state));snapshot.tasks[0].status='waiting_approval';
   snapshot.tasks[0].events.push({kind:'tool_start',content:{agent:'file',tool:'file_write'}});
   return MarvisOffice.activity(snapshot);
  });assert.equal(waiting.file.mode,'waiting');assert.equal(waiting.main.mode,'waiting');
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('.agent-main .mascot').evaluate(n=>getComputedStyle(n).animationName),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});await page.locator('[data-page=chat]').click();
  assert.equal(await page.locator('.agent-main .mascot').evaluate(n=>getComputedStyle(n).animationPlayState),'paused');
  assert.deepEqual(errors,[]);
  console.log('Office UI passed: nine stable avatars, context handback, real thinking/tool/completion states, animated transforms, nested task links, counters, narrow layout, reduced motion and background pause.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
