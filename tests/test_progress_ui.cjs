// NODE_PATH=/tmp/marvis-render-qa/node_modules node tests/test_progress_ui.cjs
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py'),'--tools'],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try {
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',code=>reject(new Error('fixture exited '+code)));}));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage({viewport:{width:1380,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  await page.locator('#prompt').fill('请做一份本机健康评估');await page.locator('#prompt').press('Enter');
  const thought=page.locator('.thought-bubble').first();await thought.waitFor();
  const saved=await thought.elementHandle();const samples=[];
  for(let n=0;n<8;n++){samples.push(await thought.textContent());await page.waitForTimeout(110);}
  assert(new Set(samples).size>=3,'public note should reveal multiple partial frames');
  assert(await saved.evaluate(node=>node===document.querySelector('.thought-bubble')),'bubble DOM must survive updates');
  const tools=page.locator('.agent-tool');await tools.first().waitFor();
  assert((await tools.first().textContent()).includes('生成调用'),'tool should appear while its arguments are generated');
  const details=tools.first().locator('details');assert.equal(await details.getAttribute('open'),null);
  await details.locator('summary').click();const savedDetails=await details.elementHandle();
  await page.waitForFunction(()=>document.querySelectorAll('.agent-tool').length===2);
  await page.waitForFunction(()=>document.querySelector('.tool-payload')?.textContent.includes('返回结果'));
  assert(await savedDetails.evaluate(node=>node.isConnected&&node.open),'expanded result must stay open across streaming updates');
  assert(await details.locator('pre').last().textContent().then(t=>t.includes('total_gb')));
  assert.equal(await tools.nth(1).locator('details').getAttribute('open'),null);
  await page.waitForFunction(()=>!!document.querySelector('#messages > .message.assistant'));
  assert.equal(await page.locator('.agent-step').count(),2);
  assert((await thought.textContent()).includes('随后整理检查结果。'));
  assert.equal(await page.locator('#messages > .message.assistant').count(),1);
  assert.equal(await tools.count(),2);assert.deepEqual(errors,[]);
  await page.evaluate(()=>{document.querySelector('.tool-details').open=false;document.querySelector('#chat-scroll').scrollTop=0;followTail=false;});
  await page.screenshot({path:'/tmp/marvis-agent-steps.png'});
  console.log(`Agent steps UI passed: ${new Set(samples).size} note frames; stable DOM; early tool-call rows; repeated tools grouped correctly; nested collapsed results; open state preserved.`);
 } finally {if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
