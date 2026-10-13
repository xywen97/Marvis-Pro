const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py')],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try{
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',code=>reject(new Error('fixture exited '+code)));}));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const tab=await browser.newPage(),errors=[];tab.on('pageerror',error=>errors.push(error.message));
  await tab.goto(data.url);await tab.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  await tab.locator('#prompt').fill('上一条对话');await tab.locator('#prompt').press('Enter');
  await tab.waitForFunction(()=>state.tasks[0]?.status==='completed');
  const previous=await tab.evaluate(()=>({session,snapshot:structuredClone(state)}));
  let injected=false,newId;
  await tab.route('**/api/messages?session=*',async route=>{
   const id=new URL(route.request().url()).searchParams.get('session');
   if(id!==previous.session&&!injected){
    injected=true;newId=id;
    // An old SSE/poll response arrives after creation, before its empty
    // message response finishes. The user should stay on the new session.
    await tab.evaluate(snapshot=>acceptState(snapshot),previous.snapshot);
   }
   await route.fulfill({response:await route.fetch()});
  });
  await tab.locator('#new-chat').click();
  await tab.waitForFunction(()=>!newSessionPending&&document.activeElement===document.querySelector('#prompt'));
  assert(injected,'the delayed old snapshot must be injected');
  assert.equal(await tab.evaluate(()=>session),newId,'stale state must not return selection to the old session');
  assert.equal(await tab.locator('#messages .message').count(),0);
  await tab.locator('#prompt').fill('新对话草稿');
  await tab.evaluate(async old=>{await refresh();await acceptState(old);},previous.snapshot);
  assert.equal(await tab.evaluate(()=>session),newId,'old snapshots remain invalid after the new session is confirmed');
  assert.equal(await tab.locator('#prompt').inputValue(),'新对话草稿');
  assert.equal(await tab.locator('#sessions .session-entry.selected').getAttribute('data-session'),newId);
  assert.equal(await tab.locator('#messages .message').count(),0);
  assert.deepEqual(errors,[]);
  console.log('New conversation survives delayed snapshots before and after confirmation; selection, empty view and draft remain intact.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
})().catch(error=>{console.error(error);process.exitCode=1;});
