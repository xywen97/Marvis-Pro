const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(lowScore=false){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py'),'--team-tools',lowScore?'--evaluation-failure':'--evaluation'],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try{
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',code=>reject(new Error('fixture exited '+code)));}));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage({viewport:{width:1480,height:1100}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  await page.locator('#prompt').fill('检查本机并交付结果');await page.locator('#prompt').press('Enter');
  await page.waitForFunction(()=>state.tasks[0]?.status==='completed');
  assert(await page.evaluate(()=>!state.tasks[0].events.some(e=>e.content.agent==='evaluation')),'ordinary tasks never invoke judge');
  await page.locator('.team-evaluation').click();
  assert(await page.locator('#evaluation-dialog').isVisible());
  assert((await page.locator('#evaluation-dialog').textContent()).includes('评测对象与要求'));
  await page.locator('#evaluation-form [name=candidate]').fill('磁盘和内存检查结果');
  await page.locator('#evaluation-form [type=submit]').click();
  assert.equal(await page.evaluate(()=>state.tasks.length),1,'missing requirements must not run assessment');
  await page.locator('#evaluation-form [name=requirements]').fill('检查结论必须有真实证据，清楚说明限制');
  await page.locator('#evaluation-form [name=subject_type]').selectOption('输出');
  await page.locator('#evaluation-form [name=source_task_id]').selectOption({index:1});
  await page.locator('#evaluation-form [type=submit]').click();
  await page.waitForFunction(()=>['thinking','working'].includes(document.querySelector('.team-evaluation').dataset.mode)).catch(async error=>{
   console.error(await page.evaluate(()=>({formError:document.querySelector('#evaluation-error').textContent,
    tasks:state.tasks.map(t=>({status:t.status,result:t.result.slice(0,2000),events:t.events.filter(e=>e.kind==='agent_result').map(e=>e.content)}))})));
   throw error;
  });
  await page.waitForFunction(()=>state.tasks[0]?.status==='completed'&&state.tasks.length===2);
  await page.waitForFunction(()=>document.querySelector('#messages').textContent.includes('主控已汇总需求和评测结果'));
  const assessment=await page.evaluate(()=>state.tasks[0]);
  const request=assessment.events.find(e=>e.kind==='user_evaluation_request').content;
  const result=assessment.events.find(e=>e.kind==='user_evaluation_result').content.result;
  assert(request.user_initiated);assert.equal(result.passed,!lowScore);assert.equal(result.metrics.length,3);
  assert.equal(result.judge_calls,3,'low score does not cause automatic repair or re-evaluation');
  assert(assessment.events.some(e=>e.kind==='evaluation_handoff'&&e.content.user_initiated));
  assert(assessment.events.some(e=>e.kind==='user_evaluation_summary'));
  assert(!assessment.events.some(e=>e.kind==='delivery_accepted'));
  assert(assessment.result.includes('用户主动评测'));assert(assessment.result.includes(request.request.requirements));
  await page.locator('#prompt').fill('/评测');await page.locator('#prompt').press('Enter');
  await page.waitForFunction(()=>document.querySelector('#evaluation-dialog').open);
  await page.locator('#evaluation-cancel').click();
  assert.equal(await page.evaluate(()=>state.tasks.length),2,'opening or cancelling intake does not start evaluation');
  await page.locator('[data-page=office]').click();await page.locator('.agent-evaluation').click();
  await page.locator('#office-evaluation-start').click();assert(await page.locator('#evaluation-dialog').isVisible());
  assert.deepEqual(errors,[]);
  console.log(lowScore?'User evaluation low-score UI passed: completed assessment, main summary, no automatic retries.':'User evaluation UI passed: ordinary task unchanged, explicit intake, required fields, real SDK, main handoff, command and office entry.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
(async()=>{await run();await run(true);})().catch(error=>{console.error(error);process.exitCode=1});
