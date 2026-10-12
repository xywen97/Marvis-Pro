const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py')],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try{
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',code=>reject(new Error('fixture exited '+code)));}));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage({viewport:{width:1480,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  await page.locator('#prompt').fill('保存这条测试对话');await page.locator('#prompt').press('Enter');
  await page.waitForFunction(()=>state.tasks.some(t=>t.status==='running'));
  const sid=await page.evaluate(()=>session);
  const row=page.locator(`.session-entry[data-session="${sid}"]`);
  assert.equal(await row.getAttribute('data-activity'),'running');
  const spinner=row.locator('.session-spinner');assert.equal(await spinner.count(),1);
  const firstFrame=await spinner.evaluate(node=>getComputedStyle(node).transform);
  await page.waitForTimeout(100);
  assert.notEqual(await spinner.evaluate(node=>getComputedStyle(node).transform),firstFrame,'running conversation spinner must rotate');
  const inactiveRunning=await page.evaluate(()=>{
   const selected=session;session=null;renderSessionList();
   const row=document.querySelector('[data-activity=running]');const result=!!row&&!row.classList.contains('selected')&&!!row.querySelector('.session-spinner');
   session=selected;renderSessionList();return result;
  });assert(inactiveRunning,'running marker must remain visible when another conversation is selected');
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await spinner.evaluate(node=>getComputedStyle(node).animationName),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await row.locator('summary').click();assert(await row.getByRole('button',{name:'归档对话',exact:true}).isDisabled());
  assert(await row.getByRole('button',{name:'删除对话',exact:true}).isDisabled());
  await page.waitForFunction(()=>state.tasks[0]?.status==='completed');
  assert.equal(await row.locator('.session-spinner').count(),0);assert.equal(await row.getAttribute('data-activity'),'idle');
  await row.locator('summary').click();await row.getByRole('button',{name:'归档对话',exact:true}).click();
  await page.waitForFunction(()=>state.sessions.length===0&&state.archived_sessions.length===1);
  assert.equal(await page.locator('#messages .message').count(),0);
  await page.locator('#session-archive-toggle').click();await row.locator('.session').click();
  await page.waitForFunction(()=>document.querySelector('#prompt').disabled&&document.querySelectorAll('#messages .message').length===2);
  await page.reload();await page.waitForFunction(()=>liveConnected&&state.archived_sessions.length===1);
  await page.locator('#session-archive-toggle').click();await row.locator('summary').click();
  await row.getByRole('button',{name:'恢复对话',exact:true}).click();
  await page.waitForFunction(()=>state.sessions.length===1&&state.archived_sessions.length===0);
  assert.equal(await page.evaluate(()=>session),sid);assert(await page.locator('#prompt').isEnabled());
  await row.locator('summary').click();await row.getByRole('button',{name:'删除对话',exact:true}).click();
  assert(await page.locator('#session-delete-dialog').isVisible());await page.locator('#session-delete-cancel').click();
  assert.equal(await page.locator('#messages .message').count(),2);
  await row.locator('summary').click();await row.getByRole('button',{name:'删除对话',exact:true}).click();
  await page.locator('#session-delete-confirm').click();
  await page.waitForFunction(()=>state.sessions.length===0&&state.tasks.length===0);
  assert.equal(await page.locator('#messages .message').count(),0);
  await page.locator('#new-chat').click();await page.waitForFunction(()=>state.sessions.length===1);
  assert(await page.locator('#prompt').isEnabled());
  const transitions=await page.evaluate(()=>{
   const saved=state.tasks,checks=[];const sid=state.sessions[0].id;
   for(const status of ['queued','waiting_approval','running','failed','cancelled']){
    state.tasks=[{id:'indicator-qa',session_id:sid,status}];renderSessionList();
    const row=document.querySelector(`.session-entry[data-session="${sid}"]`);
    checks.push({status:row.dataset.activity,spinning:!!row.querySelector('.session-spinner'),label:row.querySelector('.session').getAttribute('aria-label')});
   }
   state.tasks=saved;renderSessionList();return checks;
  });
  assert.deepEqual(transitions.map(t=>t.status),['queued','waiting_approval','running','idle','idle']);
  assert.deepEqual(transitions.map(t=>t.spinning),[false,false,true,false,false]);
  assert(transitions[1].label.includes('等待确认'));assert(transitions[2].label.includes('执行中'));
  assert.deepEqual(errors,[]);
  console.log('Session UI passed: animated running markers across selections, task-state transitions, reduced motion, busy protection, archive persistence, read-only archive preview, restore, cancel deletion, full delete, current session fallback and new conversation.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
