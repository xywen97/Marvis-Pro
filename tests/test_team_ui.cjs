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
  const page=await browser.newPage({viewport:{width:1480,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  assert.equal(await page.locator('.team-agent[data-mode=idle]').count(),8);
  const main=await page.locator('.team-main').elementHandle();
  assert.equal(await page.locator('.team-main .team-character').evaluate(n=>getComputedStyle(n).animationName),'team-idle');
  await page.evaluate(()=>{
   window.handoffs=[];
   new MutationObserver(records=>{for(const r of records)if(r.attributeName==='data-handoff')window.handoffs.push(r.target.dataset.handoff);}).observe(document.querySelector('#agents'),{attributes:true});
  });
  await page.locator('#prompt').fill('请做一份本机健康评估');await page.locator('#prompt').press('Enter');
  await page.waitForFunction(()=>document.querySelector('.team-main').dataset.mode==='thinking');
  await page.waitForFunction(()=>document.querySelector('.team-computer').dataset.mode==='working');
  assert.equal(await page.locator('.team-main').getAttribute('data-mode'),'coordinating');
  assert.equal(await page.locator('.team-computer .agent-status').textContent(),'执行中');
  const first=await page.locator('.team-computer .team-character').evaluate(n=>getComputedStyle(n).transform);
  await page.waitForTimeout(100);
  assert.notEqual(await page.locator('.team-computer .team-character').evaluate(n=>getComputedStyle(n).transform),first);
  await page.screenshot({path:path.join(__dirname,'../docs/screenshots/team.png')});
  await page.waitForFunction(()=>document.querySelector('.team-main').dataset.mode==='done');
  assert.equal(await page.locator('.team-computer').getAttribute('data-mode'),'done');
  await page.waitForFunction(()=>getComputedStyle(document.querySelector('.team-main .team-check')).opacity==='1');
  assert(await main.evaluate(node=>node===document.querySelector('.team-main')),'stream updates retain avatar DOM');
  const routes=await page.evaluate(()=>window.handoffs);
  assert(routes.includes('main:computer'));assert(routes.includes('computer:main'));
  await page.waitForFunction(()=>document.querySelector('.team-main').dataset.mode==='idle');
  const count=await page.evaluate(()=>window.handoffs.length);await page.waitForTimeout(1100);
  assert.equal(await page.evaluate(()=>window.handoffs.length),count,'old events must not replay');
  const feedback=await page.evaluate(()=>{
   const snapshot=JSON.parse(JSON.stringify(state));const task=snapshot.tasks[0];
   task.status='waiting_approval';task.events.push({id:0,kind:'tool_start',content:{agent:'file',tool:'file_write'}});
   MarvisTeam.render(snapshot);const waiting=document.querySelector('.team-file').dataset.mode;
   task.status='failed';task.updated=Date.now()/1000;MarvisTeam.render(snapshot);
   const error=document.querySelector('.team-main').dataset.mode;
   task.status='cancelled';MarvisTeam.render(snapshot);const stopped=document.querySelector('.team-main').dataset.mode;
   MarvisTeam.render(state);return {waiting,error,stopped};
  });assert.deepEqual(feedback,{waiting:'waiting',error:'error',stopped:'stopped'});
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('.team-main .team-character').evaluate(n=>getComputedStyle(n).animationName),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});await page.locator('[data-page=office]').click();
  assert.equal(await page.locator('.team-main .team-character').evaluate(n=>getComputedStyle(n).animationPlayState),'paused');
  assert.equal(await page.locator('.team-transfer.visible').count(),0);
  assert.deepEqual(errors,[]);
  console.log('Team UI passed: idle, live thinking/tool execution, handoffs in both directions, completion, stable streaming DOM, no history replay, reduced motion, hidden pause.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
