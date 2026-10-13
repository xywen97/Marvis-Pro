const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'knowledge_ui_fixture.py')],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try{
  const lines=readline.createInterface({input:fixture.stdout});const data=JSON.parse(await new Promise(resolve=>lines.once('line',resolve)));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage({viewport:{width:1700,height:1150},deviceScaleFactor:1});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  await page.locator('[data-page=knowledge]').click();await page.waitForFunction(()=>document.querySelectorAll('.kb-file').length===24);
  assert.equal(await page.locator('#kb-total').textContent(),'30');assert.equal(await page.locator('#kb-ready').textContent(),'30');
  assert(!await page.locator('#toast').isVisible());
  await page.locator('#kb-index-button').click();
  await page.waitForFunction(()=>document.querySelector('#kb-index-status').textContent.includes('索引完成'));
  assert(await page.locator('#knowledge').isVisible());
  assert.equal(await page.locator('#kb-index-button').isDisabled(),false);
  await page.locator('#kb-next').click();await page.waitForFunction(()=>document.querySelectorAll('.kb-file').length===6);
  await page.locator('#kb-prev').click();await page.waitForFunction(()=>document.querySelectorAll('.kb-file').length===24);
  await page.locator('#knowledge-query').fill('接入指南');await page.waitForFunction(()=>document.querySelectorAll('.kb-file').length===1);
  await page.getByRole('button',{name:'预览',exact:true}).click();await page.locator('.kb-preview-text').waitFor();assert.match(await page.locator('.kb-preview-text').textContent(),/用户偏好/);
  await page.locator('[data-kb-mode=semantic]').click();await page.locator('#knowledge-query').fill('怎样保存用户偏好？');
  await page.waitForFunction(()=>document.querySelector('#kb-result-label').textContent.includes('语义搜索 · 1'));
  assert.match(await page.locator('.kb-file-excerpt').textContent(),/语义检索/);
  await page.getByRole('button',{name:'关闭通知',exact:true}).click();
  await page.setViewportSize({width:1700,height:1400});
  await page.evaluate(()=>document.querySelectorAll('main,.page').forEach(e=>e.scrollTop=0));
  await page.screenshot({path:process.env.KNOWLEDGE_SCREENSHOT||'/tmp/marvis-knowledge.png',fullPage:true});
  await page.locator('#knowledge-clear').click();await page.waitForFunction(()=>document.querySelectorAll('.kb-file').length===24);
  await page.locator('#knowledge-filter').selectOption('代码');await page.waitForFunction(()=>document.querySelectorAll('.kb-file').length===1);assert.match(await page.locator('.kb-file-title').textContent(),/项目架构/);
  fixture.stdin.write(JSON.stringify({action:'unavailable'})+'\n');await page.locator('#knowledge-query').fill('这是怎样工作的？');
  await page.getByText('语义引擎尚未就绪',{exact:true}).waitFor();
  await page.locator('[data-kb-mode=keyword]').click();await page.locator('#knowledge-clear').click();await page.waitForFunction(()=>document.querySelectorAll('.kb-file').length===1);
  await page.locator('#knowledge-filter').selectOption('');await page.waitForFunction(()=>document.querySelectorAll('.kb-file').length===24);
  await page.setViewportSize({width:760,height:1000});
  await page.evaluate(()=>document.querySelectorAll('main,.page').forEach(e=>e.scrollTop=0));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:'/tmp/marvis-knowledge-small.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('Knowledge UI passed: default browse, pagination, preview, keyword/semantic modes, clear, filters, unavailable service and responsive layout.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
