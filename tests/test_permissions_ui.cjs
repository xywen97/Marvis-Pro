const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
async function run(){
 const fixture=spawn('./.venv/bin/python',['tests/streaming_fixture.py'],{stdio:['pipe','pipe','pipe']});let browser;
 fixture.stderr.on('data',data=>process.stderr.write(data));
 try{
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise(resolve=>lines.once('line',resolve)));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage({viewport:{width:1380,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>liveConnected);
  const toggle=page.locator('#permission-toggle');
  await toggle.click();assert(await page.locator('#permission-control').evaluate(n=>n.open));
  await page.getByRole('menuitemradio',{name:/请求批准/}).click();
  await page.waitForFunction(()=>state.settings.approval_policy==='ask');
  assert.equal(await page.locator('#permission-label').textContent(),'请求批准');
  await toggle.click();await page.getByRole('menuitemradio',{name:/完全访问权限/}).click();
  await page.waitForFunction(()=>state.settings.approval_policy==='full');
  await page.reload();await page.waitForFunction(()=>liveConnected);
  assert.equal(await page.locator('#permission-label').textContent(),'完全访问权限');
  await toggle.click();await page.getByRole('menuitemradio',{name:/帮我批准/}).focus();await page.keyboard.press('ArrowDown');
  assert(await page.getByRole('menuitemradio',{name:/完全访问权限/}).evaluate(n=>n===document.activeElement));
  await page.keyboard.press('Escape');assert(!await page.locator('#permission-control').evaluate(n=>n.open));
  await page.setViewportSize({width:390,height:1000});await toggle.click();
  const bounds=await page.locator('.permission-menu').boundingBox();assert(bounds.x>=0&&bounds.x+bounds.width<=390);
  await page.screenshot({path:'/tmp/marvis-permissions-mobile.png'});
  await page.getByRole('menuitemradio',{name:/帮我批准/}).click();await page.waitForFunction(()=>state.settings.approval_policy==='auto');
  await page.setViewportSize({width:1380,height:1000});await toggle.click();await page.screenshot({path:'/tmp/marvis-permissions.png'});
  await page.keyboard.press('Escape');await page.locator('#prompt').fill('生成一个示例');await page.locator('#prompt').press('Enter');
  await page.waitForFunction(()=>state.tasks.some(busy));
  assert.equal(await toggle.getAttribute('aria-disabled'),'true');await toggle.click();assert(!await page.locator('#permission-control').evaluate(n=>n.open));
  await page.waitForFunction(()=>state.tasks[0]?.status==='completed');
  assert.deepEqual(errors,[]);console.log('Permissions UI passed: three persisted modes, selection, keyboard, mobile bounds, active-task lock.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
