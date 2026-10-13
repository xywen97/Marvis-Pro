// NODE_PATH=/tmp/marvis-render-qa/node_modules node tests/test_notifications_ui.cjs
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
  const tab=await browser.newPage({viewport:{width:1380,height:900}}),errors=[];
  tab.on('pageerror',e=>errors.push(e.message));
  await tab.addInitScript(()=>{
   window.sentNotifications=[];
   window.Notification=class {
    static permission='granted';
    constructor(title,options){this.title=title;this.options=options;window.sentNotifications.push(this);}
    close(){this.closed=true;}
   };
  });
  await tab.goto(data.url);await tab.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  assert.equal(await tab.locator('.task-notification').count(),0);
  await tab.locator('#prompt').fill('通知验证');await tab.locator('#prompt').press('Enter');
  await tab.waitForFunction(()=>state.tasks.length>0);
  const target=await tab.evaluate(()=>state.tasks[0].session_id);
  await tab.locator('[data-page="tasks"]').click();
  await tab.waitForFunction(()=>state.tasks[0]?.status==='completed');
  await tab.locator('.task-notification').waitFor();
  assert.match(await tab.locator('.task-notification strong').textContent(),/任务已完成/);
  assert.equal(await tab.evaluate(()=>sentNotifications.length),1);
  await tab.evaluate(()=>{MarvisNotifications.update(state);MarvisNotifications.update(state);});
  assert.equal(await tab.evaluate(()=>sentNotifications.length),1);
  await tab.evaluate(()=>sentNotifications[0].onclick());
  await tab.waitForFunction(id=>page==='chat'&&session===id,target);
  await tab.locator('[data-page="tasks"]').click();
  await tab.locator('.task-notification-open').click();
  await tab.waitForFunction(id=>page==='chat'&&session===id,target);
  await tab.evaluate(()=>{
   liveAbort.abort();
   const original=state.tasks[0];
   const pending={...original,status:'waiting_approval',approval:{tool:'write_file',arguments:{path:'/tmp/example'}},events:[...original.events,{id:10001,kind:'approval',content:{}}]};
   state={...state,tasks:[pending],notification_host:'desktop'};renderState();MarvisNotifications.update(state);
   MarvisNotifications.update(state);
  });
  assert.match(await tab.locator('.task-notification strong').textContent(),/需要你的授权/);
  assert.equal(await tab.evaluate(()=>sentNotifications.length),1,'native host must not duplicate browser notifications');
  assert.equal(await tab.locator('#approval-dialog').evaluate(node=>node.open),true);
  await tab.evaluate(()=>{
   state.tasks[0].status='running';state.tasks[0].approval=null;renderState();MarvisNotifications.update(state);
  });
  assert.equal(await tab.locator('.task-notification').count(),0,'resolved approval reminder should disappear');
  await tab.evaluate(()=>{
   state.tasks[0].status='waiting_approval';state.tasks[0].approval={tool:'write_file'};
   state.tasks[0].events.push({id:10002,kind:'approval',content:{}});renderState();MarvisNotifications.update(state);
  });
  assert.equal(await tab.locator('.task-notification').count(),1,'another approval must notify again');
  assert.deepEqual(errors,[]);
  await tab.screenshot({path:'/tmp/marvis-notifications.png'});
  console.log('Notification transitions, deduplication, native routing and click navigation passed');
 }finally{if(browser)await browser.close();fixture.stdin.end();await new Promise(resolve=>fixture.once('exit',resolve));}
})().catch(error=>{console.error(error);process.exit(1);});
