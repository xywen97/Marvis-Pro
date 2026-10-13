const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'memory_ui_fixture.py')],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try{
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise(resolve=>lines.once('line',resolve)));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  await page.locator('[data-page=settings]').click();
  assert.match(await page.locator('#memory-status').textContent(),/停用/);
  const toggle=page.getByRole('switch',{name:'启用长期记忆'});
  assert.equal(await toggle.isChecked(),false);
  await toggle.check();await page.waitForFunction(()=>state.settings.memory_enabled===true&&!document.querySelector('#memory-enabled').disabled);
  await page.reload();await page.waitForFunction(()=>liveConnected);await page.locator('[data-page=settings]').click();
  assert.equal(await toggle.isChecked(),true);
  await toggle.uncheck();await page.waitForFunction(()=>state.settings.memory_enabled===false&&!document.querySelector('#memory-enabled').disabled);
  assert.match(await page.locator('#memory-status').textContent(),/停用/);
  const key=page.locator('[name=api_key]');assert.equal(await key.getAttribute('type'),'password');
  await page.locator('[name=base_url]').fill('https://model.example/v1');
  await page.locator('[name=model]').fill('chat');await key.fill('ui-private-key');
  await page.getByRole('button',{name:'保存设置'}).click();
  await page.waitForFunction(()=>state.settings.model.has_key===true);
  await page.waitForFunction(()=>document.querySelector('[name=api_key]').value==='');
  assert.equal(await key.inputValue(),'');
  assert(!JSON.stringify(await page.evaluate(()=>state)).includes('ui-private-key'));
  await page.reload();await page.waitForFunction(()=>liveConnected);
  await page.locator('[data-page=settings]').click();assert.equal(await key.inputValue(),'');
  assert.equal(await toggle.isChecked(),false);
  assert.deepEqual(errors,[]);
  console.log('Memory UI passed: visible lifecycle, masked key input, no key echo, persisted model credentials.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
