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
  assert.equal(await page.locator('.team-agent[data-mode=idle]').count(),9);
  assert.equal(await page.locator('.context-track[role=progressbar]').count(),9);
  assert.equal(await page.locator('.team-context .context-value').textContent(),'未采样 / 256k');
  const meters=await page.evaluate(()=>{
   const originalSession=session;session='context-qa';
   const snapshot=JSON.parse(JSON.stringify(state));snapshot.context_usage={
    'context-qa':{main:{estimated_tokens:128000,window_tokens:256000,trigger_tokens:256000},
     file:{estimated_tokens:245760,window_tokens:256000},context:{estimated_tokens:30000,window_tokens:1000000}},
    'other-qa':{main:{estimated_tokens:25600,window_tokens:256000}}};
   snapshot.tasks=[{id:'background-task',session_id:'other-qa',status:'running',created:Date.now()/1000,
    updated:Date.now()/1000,events:[{id:1,kind:'model_start',content:{agent:'main'}}]}];
   MarvisTeam.render(snapshot);
   const track=document.querySelector('.team-main .context-track');
   const first={main:track.getAttribute('aria-valuenow'),file:document.querySelector('.team-file .context-meter').dataset.pressure,
    context:document.querySelector('.team-context .context-value').textContent};
   snapshot.context_usage['context-qa'].main.estimated_tokens=10000;MarvisTeam.render(snapshot);
   const compacted={value:track.getAttribute('aria-valuenow'),stable:track===document.querySelector('.team-main .context-track')};
   snapshot.context_usage['context-qa'].main.estimated_tokens=300000;MarvisTeam.render(snapshot);
   const overflow={value:track.getAttribute('aria-valuenow'),text:track.getAttribute('aria-valuetext')};
   session='other-qa';MarvisTeam.render(snapshot);
   const switched={main:track.getAttribute('aria-valuenow'),file:document.querySelector('.team-file .context-value').textContent};
   session='new-qa';MarvisTeam.render(snapshot);
   const empty={main:document.querySelector('.team-main .context-value').textContent,
    active:document.querySelectorAll('.team-agent:not([data-mode=idle])').length,
    status:document.querySelector('#team-execution').textContent};
   session=originalSession;MarvisTeam.render(state);return {first,compacted,overflow,switched,empty};
  });
  assert.equal(meters.first.main,'50.0');assert.equal(meters.first.file,'high');assert.equal(meters.first.context,'30k / 1M · 3.0%');
  assert.equal(meters.compacted.value,'3.9');assert(meters.compacted.stable);
  assert.equal(meters.overflow.value,'100.0');assert(meters.overflow.text.includes('117.2%'));
  assert.equal(meters.switched.main,'10.0');assert.equal(meters.switched.file,'未采样 / 256k');
  assert.deepEqual(meters.empty,{main:'未采样 / 256k',active:0,status:'团队待命'});
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
  await page.waitForFunction(()=>Number(document.querySelector('.team-computer .context-track').getAttribute('aria-valuenow'))>0);
  assert(Number(await page.locator('.team-main .context-track').getAttribute('aria-valuenow'))>0);
  const runningSession=await page.evaluate(()=>session);
  await page.locator('#new-chat').click();await page.waitForFunction(old=>session!==old,runningSession);
  await page.waitForFunction(()=>document.querySelectorAll('.team-agent[data-mode=idle]').length===9);
  assert.equal(await page.locator('.context-value').filter({hasText:'未采样 / 256k'}).count(),9);
  assert.equal(await page.locator('#team-execution').textContent(),'团队待命');
  assert(await page.evaluate(sid=>state.tasks.some(t=>t.session_id===sid&&t.status==='running'),runningSession));
  await page.waitForTimeout(600);
  assert.equal(await page.locator('.team-main .context-value').textContent(),'未采样 / 256k','background updates must not replace draft context');
  await page.locator(`.session-entry[data-session="${runningSession}"] .session`).click();
  await page.waitForFunction(()=>Number(document.querySelector('.team-main .context-track').getAttribute('aria-valuenow'))>0);
  await page.screenshot({path:process.env.MARVIS_TEAM_SCREENSHOT_PATH||path.join(__dirname,'../docs/screenshots/team.png')});
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
  console.log('Team UI passed: session-scoped context/activity, new draft during background execution, restored readings, live handoffs, completion, stable DOM, reduced motion.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
