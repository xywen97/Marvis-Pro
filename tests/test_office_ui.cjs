// NODE_PATH=/tmp/marvis-render-qa/node_modules node tests/test_office_ui.cjs
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const path=require('node:path');
async function run(){
 const fixture=spawn(path.join(__dirname,'../.venv/bin/python'),[path.join(__dirname,'streaming_fixture.py'),'--team-tools'],{stdio:['pipe','pipe','pipe']});
 fixture.stderr.on('data',data=>process.stderr.write(data));let browser;
 try{
  const lines=readline.createInterface({input:fixture.stdout});
  const data=JSON.parse(await new Promise((resolve,reject)=>{lines.once('line',resolve);fixture.once('exit',code=>reject(new Error('fixture exited '+code)));}));
  browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  const page=await browser.newPage({viewport:{width:1480,height:1100}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(data.url);await page.waitForFunction(()=>typeof liveConnected!=='undefined'&&liveConnected);
  await page.locator('[data-page=office]').click();assert.equal(await page.locator('.office-station').count(),9);
  assert.equal(await page.locator('.office-station[data-mode=idle]').count(),9);
  assert.equal(await page.locator('.seat-indicator[data-active=false]').count(),8);
  const idleMotion=await page.evaluate(()=>{
   function sample(selector,name,progress){
    const node=document.querySelector(selector),animation=node.getAnimations().find(a=>a.animationName===name);
    animation.pause();const timing=animation.effect.getTiming();animation.currentTime=Number(timing.duration)*progress+timing.delay;
    const matrix=new DOMMatrixReadOnly(getComputedStyle(node).transform);animation.play();return {yScale:matrix.d,tilt:matrix.b};
   }
   return {open:sample('.agent-main .mascot-eyes','office-blink',.8),closed:sample('.agent-main .mascot-eyes','office-blink',.86),
    resting:sample('.agent-file .mascot-head-group','office-head-idle',.2),tilted:sample('.agent-file .mascot-head-group','office-head-idle',.45),
    backTilt:sample('.agent-coding .mascot-head-group','office-head-idle',.45)};
  });
  assert(idleMotion.open.yScale>.9&&idleMotion.closed.yScale<.15,'blink must visibly close the eyes');
  assert(Math.abs(idleMotion.resting.tilt)<.01&&Math.abs(idleMotion.tilted.tilt)>.05,'idle head tilts independently of body');
  assert(Math.abs(idleMotion.backTilt.tilt)>.05,'back-facing cats also tilt their heads');
  const compacting=await page.evaluate(()=>{
   const snapshot={agents:state.agents,tasks:[{id:'context-qa',status:'running',events:[
    {kind:'context_compaction_start',content:{agent:'context',target_agent:'coding'}}]}]};
   const start=MarvisOffice.activity(snapshot);
   snapshot.tasks[0].events.push({kind:'context_compaction',content:{agent:'coding'}});
   return {start,finish:MarvisOffice.activity(snapshot)};
  });
  assert.equal(compacting.start.context.mode,'thinking');
  assert.equal(compacting.finish.context.mode,'idle');assert.equal(compacting.finish.coding.mode,'thinking');
  const saved=await page.locator('.office-station[data-agent=main]').elementHandle();
  await page.locator('#office-start').click();await page.locator('#prompt').fill('请做一份本机健康评估');await page.locator('#prompt').press('Enter');
  await page.locator('[data-page=office]').click();
  await page.waitForFunction(()=>document.querySelector('.agent-main')?.dataset.mode==='thinking');
  await page.waitForFunction(()=>document.querySelector('.agent-computer')?.dataset.mode==='working');
  assert.equal(await page.locator('#office-active-count').textContent(),'1');
  assert.equal(await page.locator('.seat-indicator[data-agent=computer]').getAttribute('data-active'),'true');
  assert.equal(await page.locator('.agent-main .station-status').textContent(),'统筹任务');
  await page.waitForSelector('.office-flow[data-agent=computer][data-direction=dispatch]');
  const dispatch=page.locator('.flow-dispatch[data-agent=computer]').last();
  const packetStart=await dispatch.locator('.flow-packet').first().evaluate(n=>getComputedStyle(n).offsetDistance);
  await page.waitForTimeout(100);
  const packetEnd=await dispatch.locator('.flow-packet').first().evaluate(n=>getComputedStyle(n).offsetDistance);
  assert.notEqual(packetStart,packetEnd,'dispatch packets must travel along the route');
  const stableFlows=await page.evaluate(()=>{
   const layer=document.querySelector('#office-data-flows'),before=layer.children.length;
   MarvisOffice.render(state);MarvisOffice.render(state);return before===layer.children.length;
  });assert(stableFlows,'snapshot refreshes must not replay transfers');
  const first=await page.locator('.agent-computer .mascot').evaluate(n=>getComputedStyle(n).transform);
  await page.waitForTimeout(100);const second=await page.locator('.agent-computer .mascot').evaluate(n=>getComputedStyle(n).transform);
  assert.notEqual(first,second,'working avatar must animate');
  assert.equal(await page.locator('.agent-computer .mascot-head-group').evaluate(n=>getComputedStyle(n).animationName),'office-head-work');
  assert.equal(await page.locator('.agent-computer .mascot-arms').evaluate(n=>getComputedStyle(n).animationName),'office-paws');
  assert(await saved.evaluate(node=>node===document.querySelector('.agent-main')),'avatar must retain its DOM and animation');
  await page.locator('.agent-computer').click();assert.equal(await page.locator('#office-agent-name').textContent(),'电脑管家');
  assert.equal(await page.locator('#office-evaluation-start').isVisible(),false);
  await page.screenshot({path:process.env.MARVIS_OFFICE_SCREENSHOT_PATH||path.join(__dirname,'../docs/screenshots/office.png')});
  await page.waitForSelector('.office-flow[data-agent=computer][data-direction=return]');
  const directions=await page.evaluate(()=>{
   const outbound=document.querySelector('.flow-dispatch .flow-track'),inbound=document.querySelector('.flow-return .flow-track');
   return {out:[outbound.getPointAtLength(0).x,outbound.getPointAtLength(outbound.getTotalLength()).x],
    back:[inbound.getPointAtLength(0).x,inbound.getPointAtLength(inbound.getTotalLength()).x]};
  });
  assert(directions.out[0]<directions.out[1]&&directions.back[0]>directions.back[1],'result flow must reverse the dispatch direction');
  await page.waitForFunction(()=>document.querySelector('#office-done-count').textContent==='1');
  assert.equal(await page.locator('.seat-indicator[data-active=true]').count(),0);
  const stages=await page.evaluate(()=>{
   const snapshot=JSON.parse(JSON.stringify(state));snapshot.tasks[0].status='running';
   snapshot.tasks[0].events=[{kind:'model_start',content:{agent:'app'}}];
   const analyzing=MarvisOffice.activity(snapshot);
   snapshot.tasks[0].events.push({kind:'tool_result',content:{agent:'app',tool:'app_list'}});
   const reviewing=MarvisOffice.activity(snapshot);
   snapshot.tasks[0].events.push({kind:'agent_result',content:{agent:'app'}});
   return {analyzing,reviewing,returned:MarvisOffice.activity(snapshot)};
  });
  assert.equal(stages.analyzing.app.stage,'analyzing');assert.equal(stages.analyzing.main.stage,'coordinating');
  assert.equal(stages.reviewing.app.stage,'reviewing');assert.equal(stages.returned.app.mode,'idle');
  assert.equal(stages.returned.main.stage,'reviewing');
  assert.equal(await page.locator('#office-tool-count').textContent(),'3');
  const waiting=await page.evaluate(()=>{
   const snapshot=JSON.parse(JSON.stringify(state));snapshot.tasks[0].status='waiting_approval';
   snapshot.tasks[0].events.push({kind:'tool_start',content:{agent:'file',tool:'file_write'}});
   return MarvisOffice.activity(snapshot);
  });assert.equal(waiting.file.mode,'waiting');assert.equal(waiting.main.mode,'waiting');
  const historical=await page.evaluate(()=>{
   const snapshot=JSON.parse(JSON.stringify(state));const task=snapshot.tasks[0];task.id='old-transfer-qa';
   task.events=[{id:999,kind:'agent_result',created:Date.now()/1000-60,content:{agent:'app'}}];
   MarvisOffice.render(snapshot);const replayed=!!document.querySelector('.office-flow[data-agent=app]');
   MarvisOffice.render(state);return replayed;
  });assert.equal(historical,false,'opening an old task must not replay historical transfers');
  await page.evaluate(()=>{
   window.indicatorOriginal=state;state=JSON.parse(JSON.stringify(state));
   const task=state.tasks[0];task.id='seat-failure-qa';task.status='running';
   task.events=[{id:1,kind:'model_start',created:Date.now()/1000,content:{agent:'app',request_id:'seat-request'}}];
   MarvisOffice.render(state);
   const panel=document.querySelector('.seat-indicator[data-agent=app]');window.seatIterations=0;
   panel.addEventListener('animationiteration',()=>window.seatIterations++);
   task.events.push({id:2,kind:'tool_error',created:Date.now()/1000,content:{agent:'app',request_id:'seat-request',tool:'app_open',text:'test failure'}});
   MarvisOffice.render(state);MarvisOffice.render(state);
  });
  const panel=page.locator('.seat-indicator[data-agent=app]');
  await page.waitForFunction(()=>getComputedStyle(document.querySelector('.seat-indicator[data-agent=app]')).fill==='rgb(239, 114, 123)');
  assert.equal(await panel.evaluate(n=>getComputedStyle(n).animationIterationCount),'3');
  await page.waitForFunction(()=>!document.querySelector('.seat-indicator[data-agent=app]').classList.contains('failure-flash'));
  assert.equal(await page.evaluate(()=>window.seatIterations),2,'three flashes must finish, without restarting on refresh');
  assert.equal(await panel.evaluate(n=>getComputedStyle(n).fill),'rgb(93, 189, 153)');
  const duplicate=await page.evaluate(()=>{
   const task=state.tasks[0];
   task.events.push({id:3,kind:'agent_result',created:Date.now()/1000,content:{agent:'app',request_id:'seat-request',result:{status:'partial'}}});
   MarvisOffice.render(state);return document.querySelector('.seat-indicator[data-agent=app]').classList.contains('failure-flash');
  });assert.equal(duplicate,false,'a report of the same tool failure must not flash again');
  await page.evaluate(()=>{
   const task=state.tasks[0];
   task.events.push({id:4,kind:'tool_result',created:Date.now()/1000,content:{agent:'app',tool:'app_open',result:{error:'second failure'}}});
   MarvisOffice.render(state);task.status='failed';task.updated=Date.now()/1000;MarvisOffice.render(state);
  });
  assert.equal(await panel.getAttribute('data-active'),'false');
  await page.waitForFunction(()=>!document.querySelector('.seat-indicator[data-agent=app]').classList.contains('failure-flash'));
  assert.equal(await panel.evaluate(n=>getComputedStyle(n).fill),'rgb(194, 208, 228)');
  await page.evaluate(()=>{
   const task=state.tasks[0];task.id='old-seat-failure-qa';
   task.events=[{id:5,kind:'tool_error',created:Date.now()/1000-60,content:{agent:'app',tool:'app_open'}}];
   MarvisOffice.render(state);
  });assert.equal(await panel.getAttribute('data-failure'),null,'old failures must not replay');
  await page.evaluate(()=>{state=window.indicatorOriginal;delete window.indicatorOriginal;MarvisOffice.render(state);});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.emulateMedia({reducedMotion:'reduce'});
  const reducedFlow=await page.evaluate(()=>{
   const snapshot=JSON.parse(JSON.stringify(state));const task=snapshot.tasks[0];task.id='reduced-flow-qa';
   task.events=[{id:1000,kind:'agent_start',created:Date.now()/1000,content:{agent:'app'}}];
   MarvisOffice.render(snapshot);const flow=document.querySelector('.flow-dispatch[data-agent=app]');
   const result={animation:getComputedStyle(flow).animationName,packet:getComputedStyle(flow.querySelector('.flow-packet')).display};
   MarvisOffice.render(state);return result;
  });assert.deepEqual(reducedFlow,{animation:'none',packet:'none'});
  assert.equal(await page.locator('.agent-main .mascot').evaluate(n=>getComputedStyle(n).animationName),'none');
  assert.equal(await page.locator('.agent-main .mascot-head-group').evaluate(n=>getComputedStyle(n).animationName),'none');
  assert.equal(await page.locator('.agent-main .mascot-eyes').evaluate(n=>getComputedStyle(n).animationName),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});await page.locator('[data-page=chat]').click();
  assert.equal(await page.locator('.agent-main .mascot').evaluate(n=>getComputedStyle(n).animationPlayState),'paused');
  assert.deepEqual(errors,[]);
  console.log('Office UI passed: nine stable avatars, context handback, real thinking/tool/completion states, animated transforms, nested task links, counters, green/gray seat indicators and exactly three red failure flashes with live recovery and deduplication, distinct analysis/review/coordination states, dispatch and return packet directions without replay, narrow layout, reduced motion and background pause.');
 }finally{if(browser)await browser.close();fixture.stdin.end();}
}
run().catch(error=>{console.error(error);process.exitCode=1});
