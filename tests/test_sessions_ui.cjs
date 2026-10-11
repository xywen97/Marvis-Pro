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
  const row=page.locator(`[data-session="${sid}"]`);
  await row.locator('summary').click();assert(await row.getByRole('button',{name:'归档对话',exact:true}).isDisabled());
  assert(await row.getByRole('button',{name:'删除对话',exact:true}).isDisabled());
  await page.waitForFunction(()=>state.tasks[0]?.status==='completed');
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
  assert(await page.locator('#prompt').isEnabled());assert.deepEqual(errors,[]);
  console.log('Session UI passed: busy protection, archive persistence, read-only archive preview, restore, cancel deletion, full delete, current session fallback and new conversation.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
