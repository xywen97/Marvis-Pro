const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(failure=false){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py'),'--team-tools',failure?'--evaluation-failure':'--evaluation'],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try{
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',code=>reject(new Error('fixture exited '+code)));}));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage({viewport:{width:1480,height:1100}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  assert.equal(await page.locator('.team-agent').count(),8);
  await page.locator('#prompt').fill('检查本机并交付结果');await page.locator('#prompt').press('Enter');
  await page.waitForFunction(()=>['thinking','working'].includes(document.querySelector('.team-evaluation').dataset.mode));
  assert.equal(await page.locator('.message.assistant-response').count(),0,'draft must not appear as an answer while DeepEval is judging');
  assert.equal(await page.locator('.team-main').getAttribute('data-mode'),'coordinating');
  await page.locator('[data-page=office]').click();
  assert(['thinking','working'].includes(await page.locator('.agent-evaluation').getAttribute('data-mode')));
  await page.locator('.agent-evaluation').click();assert.equal(await page.locator('#office-agent-name').textContent(),'评测助手');
  await page.waitForFunction(failed=>state.tasks[0]?.status===(failed?'failed':'completed'),failure);
  assert.equal(await page.evaluate(()=>state.tasks[0].events.some(e=>e.kind==='delivery_accepted')),!failure);
  await page.locator('[data-page=chat]').click();
  await page.waitForFunction(text=>document.querySelector('#messages').textContent.includes(text),failure?'主控汇总':'最后一段');
  if(failure){
   const response=page.locator('.message.assistant-response');
   assert((await response.textContent()).includes('电脑管家已完成磁盘和内存检查'));
   assert((await response.textContent()).includes('交付未放行'));
   assert(!(await response.textContent()).includes('最后一段'));
   assert(await page.evaluate(()=>state.tasks[0].events.some(e=>e.kind==='failure_summary')));
   assert(!(await page.locator('.trace-label').allTextContents()).includes('失败 · 执行过程'));
  }
  const reviews=await page.evaluate(()=>state.tasks[0].events.filter(e=>e.kind==='delivery_evaluation').map(e=>e.content));
  assert.equal(reviews.length,failure?2:1);assert.equal(reviews.at(-1).passed,!failure);assert.equal(reviews[0].framework,'deepeval');assert.equal(reviews[0].metrics.length,3);
  assert.deepEqual(errors,[]);
  console.log(failure?'Evaluation failure UI passed: real rejected scores, final main summary, no accepted delivery, accurate trace labels.':'Evaluation UI passed: eight agents, independent judge activity, no premature draft, real DeepEval receipt and accepted final output.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
(async()=>{await run();await run(true);})().catch(error=>{console.error(error);process.exitCode=1});
