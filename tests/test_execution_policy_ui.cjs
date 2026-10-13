const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
async function run(){
 const fixture=spawn('./.venv/bin/python',['tests/execution_ui_fixture.py'],{stdio:['pipe','pipe','pipe']});let browser;
 fixture.stderr.on('data',data=>process.stderr.write(data));
 try{
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise(resolve=>lines.once('line',resolve)));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage({viewport:{width:1380,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>liveConnected);
  await page.locator('#prompt').fill('scoped save and verify');await page.locator('#prompt').press('Enter');
  await page.locator('#approval-dialog[open]').waitFor();
  assert(await page.locator('#approve-task').isVisible());
  assert.match(await page.locator('#approval-scope-note').textContent(),/15 分钟/);
  await page.setViewportSize({width:390,height:1000});
  const bounds=await page.locator('#approval-dialog').boundingBox();
  assert(bounds.x>=0&&bounds.x+bounds.width<=390);
  assert(await page.locator('#approval-dialog').evaluate(n=>n.scrollWidth<=n.clientWidth));
  await page.screenshot({path:'/tmp/marvis-execution-approval-mobile.png'});
  await page.setViewportSize({width:1380,height:1000});
  await page.locator('#approve-task').click();
  await page.waitForFunction(()=>state.tasks[0]?.status==='completed');
  assert.equal(await page.evaluate(()=>state.tasks[0].events.filter(e=>e.kind==='approval').length),1);
  assert(await page.evaluate(()=>state.tasks[0].events.some(e=>e.kind==='approved'&&e.content.source==='task_scope')));
  await page.locator('#prompt').fill('single save and verify');await page.locator('#prompt').press('Enter');
  await page.locator('#approval-dialog[open]').waitFor();
  await page.locator('#approve').click();
  await page.waitForFunction(()=>state.tasks[0]?.approval?.tool==='code_execute');
  await page.locator('#approval-dialog[open]').waitFor();
  assert(!(await page.locator('#approve-task').isVisible()));
  assert.match(await page.locator('#approval-plan').textContent(),/code_execute/);
  await page.locator('#approve').click();
  await page.waitForFunction(()=>state.tasks[0]?.status==='completed');
  assert.equal(await page.evaluate(()=>state.tasks[0].events.filter(e=>e.kind==='approval').length),2);
  await page.evaluate(()=>showPage('tasks'));
  await page.getByText(/总耗时.*等待确认.*模型.*工具/).first().waitFor();
  await page.screenshot({path:'/tmp/marvis-execution-policy.png'});
  assert.deepEqual(errors,[]);console.log('Execution policy UI passed: scoped approval, sequential approvals, timing.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
