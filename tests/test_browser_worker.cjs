const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {spawn}=require('node:child_process');
const {createInterface}=require('node:readline');
const {once}=require('node:events');
const {randomUUID}=require('node:crypto');
let JSDOM;
try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const ROOT=path.resolve(__dirname,'..'),ACCOUNT='1000001',PLAN='2000001';

async function harness(t,options={}){
  const python=process.env.QC_TEST_PYTHON||'python';
  const child=spawn(python,['-X','utf8',path.join(__dirname,'browser_control_http_fixture.py'),...(options.live?['--live']:[])],{cwd:ROOT,stdio:['pipe','pipe','pipe'],windowsHide:true});
  const lines=createInterface({input:child.stdout});let stderr='';child.stderr.on('data',b=>stderr=(stderr+b.toString()).slice(-1500));
  const ready=await Promise.race([once(lines,'line').then(([s])=>JSON.parse(s)),once(child,'exit').then(()=>{throw Error('Temporary queue exited: '+stderr);})]);
  t.after(async()=>{lines.close();child.stdin.end('\n');await once(child,'exit');});
  const local={},session={},events={},requests=[],injections=[],model={budget:'1000.00',roi:'3.00',running:true,writes:0,reloads:0};
  const tab={id:7,groupId:10,url:options.live?`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${ACCOUNT}&adId=${PLAN}`:`https://qianchuan.jinritemai.com/overall-prom?aavid=${ACCOUNT}`,status:'complete'};
  local.token=ready.token;local.enabled=true;local.epoch='observer-one';
  const event=name=>({addListener:fn=>events[name]=fn});
  const storage=object=>({get:async keys=>typeof keys==='string'?{[keys]:object[keys]}:Object.fromEntries((keys||Object.keys(object)).map(k=>[k,object[k]])),set:async value=>Object.assign(object,value),remove:async key=>{delete object[key];}});
  let dom,docId,receiptLoss=options.loseReceiptAck===true;
  const context={URL,Date,Map,Set,AbortSignal,crypto:{randomUUID},setTimeout,clearTimeout,console};
  function render(){
    if(dom)dom.window.close();docId=randomUUID();
    dom=new JSDOM(options.live?`<!doctype html><body><p>ID：${ACCOUNT}</p>\n<div class="oc-promotion-key-info-bar"><div class="oc-promotion-key-info-bar-info-con">${model.running?'投放中':'已暂停'}\n计划ID：${PLAN}\n
      <div class="oc-promotion-key-info-bar-key-value-con" data-fixture-field="budget"><div class="key-con-item">预算(元)：</div><div class="value-con-item"><span data-auto-id="oc-typography"><span class="oc-typography-value-int budget">每日${model.budget}</span></span><div class="oc-promotion-key-info-bar-edit-icon"><iconpark-icon name="oc-icon-light-edit"></iconpark-icon></div></div></div>
      <div class="oc-promotion-key-info-bar-key-value-con" data-fixture-field="roi"><div class="key-con-item">净成交ROI目标：</div><div class="value-con-item"><span data-auto-id="oc-typography"><span class="oc-typography-value-int roi">${model.roi}</span></span><div class="oc-promotion-key-info-bar-edit-icon"><iconpark-icon name="oc-icon-light-edit"></iconpark-icon></div></div></div></div><div class="col-status-switch"><div data-auto-id="oc-switch"><div data-e2e="switch" class="ovui-switch ${model.running?'ovui-switch--checked':''}"></div></div></div></div></body>`:`<!doctype html><body><p>ID：${ACCOUNT}</p>\n<table><tr><td><div id="ad-name-${PLAN}">明确计划\nID：${PLAN}\n<button role="switch" aria-checked="${model.running}" type="button"></button></div></td><td class="status">${model.running?'投放中':'已暂停'}</td>
      <td><div class="budgetCell-fRJMeY">每日预算\n<span class="budget">${options.staleReadback&&model.reloads?'1000.00':model.budget}元</span><iconpark-icon name="oc-icon-light-edit"></iconpark-icon></div><div>综合营销ROI目标\n<span class="roi">${model.roi}</span><button type="button" data-roi-edit-button="true" aria-label="编辑综合营销ROI目标"></button></div></td></tr></table></body>`,{url:tab.url,runScripts:'outside-only'});
    const w=dom.window,d=w.document;
    w.HTMLElement.prototype.getClientRects=function(){return this.hidden||this.style.display==='none'?[]:[{x:0,y:0,width:100,height:20}];};
    Object.defineProperty(w.crypto,'randomUUID',{value:randomUUID});
    w.chrome={runtime:{sendMessage:async message=>{
      if(options.stopBeforeCommit){const c=session.qc_control_session_v1;session.qc_control_session_v1={...c,armed:false};}
      return new Promise(resolve=>events.message(message,{id:'test-extension',tab:{id:tab.id},documentId:docId,frameId:0,url:tab.url,...(options.senderOverride||{})},resolve));
    }}};
    function editor(field){
      const root=d.createElement('div');root.setAttribute('role','dialog');root.innerHTML=`<h2>${field==='budget'?'调整每日预算':options.live?'净成交ROI目标':'综合营销ROI目标'}</h2><input type="number" min="0.01" max="1000000" step="0.01"><button type="button">确定</button><button type="button">取消</button>`;
      root.querySelector('input').value=model[field];
      root.querySelectorAll('button')[0].addEventListener('click',()=>{model.writes++;model[field]=Number(root.querySelector('input').value).toFixed(2);d.querySelector('.'+field).textContent=(field==='budget'&&options.live?'每日':'')+model[field]+(field==='budget'&&!options.live?'元':'');root.remove();});
      root.querySelectorAll('button')[1].addEventListener('click',()=>root.remove());d.body.append(root);
    }
    if(options.live){
      d.querySelector('[data-fixture-field="budget"] .oc-promotion-key-info-bar-edit-icon').addEventListener('click',()=>editor('budget'));
      d.querySelector('[data-fixture-field="roi"] .oc-promotion-key-info-bar-edit-icon').addEventListener('click',()=>editor('roi'));
      d.querySelector('.col-status-switch .ovui-switch').addEventListener('click',()=>{
        model.writes++;model.running=false;d.querySelector('.ovui-switch').classList.remove('ovui-switch--checked');
        const info=d.querySelector('.oc-promotion-key-info-bar-info-con');info.firstChild.textContent=info.firstChild.textContent.replace('投放中','已暂停');
      });
    }else{
      d.querySelector('iconpark-icon').addEventListener('click',()=>editor('budget'));
      d.querySelector('[data-roi-edit-button]').addEventListener('click',()=>editor('roi'));
      d.querySelector('[role="switch"]').addEventListener('click',()=>{model.writes++;model.running=false;d.querySelector('[role="switch"]').setAttribute('aria-checked','false');d.querySelector('.status').textContent='已暂停';});
    }
  }
  context.chrome={
    storage:{local:storage(local),session:storage(session)},permissions:{contains:async()=>options.permissions!==false},
    tabs:{query:async()=>options.duplicateTabs?[tab,{...tab,id:8}]:[tab],get:async id=>({...tab,id}),reload:async()=>{if(options.reloadFails)throw Error('reload failure');model.reloads++;render();}},
    tabGroups:{query:async q=>q.title==='明确分组'?[{id:10,title:'明确分组'}]:[],get:async()=>({id:10,title:'明确分组'})},
    scripting:{executeScript:async args=>{
      const before=docId;injections.push(args.args[0]);
      if(args.target.documentIds)assert.deepEqual(Array.from(args.target.documentIds),[docId]);
      assert.equal(args.world,'ISOLATED');
      const func=dom.window.eval('('+args.func.toString()+')'),result=await func(...args.args);
      if(options.navigateAfterPrepare&&args.args[0]==='prepare')tab.url=tab.url.replace(ACCOUNT,'9999999');
      return [{frameId:0,...(options.missingDocument?{}:{documentId:before}),result}];
    }},
    action:{setBadgeText:async()=>{},setBadgeBackgroundColor:async()=>{}},alarms:{create:async()=>{},onAlarm:event('alarm')},
    runtime:{id:'test-extension',getURL:name=>'chrome-extension://test-extension/'+name,onInstalled:event('installed'),onStartup:event('startup'),onMessage:event('message')}
  };
  context.fetch=async(url,init={})=>{
    assert.ok(url.startsWith('http://127.0.0.1:8877/'));requests.push({path:new URL(url).pathname,body:init.body?JSON.parse(init.body):null});
    const response=await fetch(url.replace('http://127.0.0.1:8877',`http://127.0.0.1:${ready.port}`),init);
    if(receiptLoss&&url.endsWith('/worker-receipt')){receiptLoss=false;await response.text();throw Error('ack lost after commit');}
    return response;
  };
  vm.createContext(context);context.importScripts=(...names)=>names.forEach(name=>vm.runInContext(fs.readFileSync(path.join(ROOT,'extension',name),'utf8'),context));
  vm.runInContext(fs.readFileSync(path.join(ROOT,'extension/background.js'),'utf8'),context);render();
  t.after(()=>dom.window.close());
  async function api(pathname,body){
    const response=await fetch(`http://127.0.0.1:${ready.port}${pathname}`,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Monitor-CSRF':ready.csrf},body:JSON.stringify(body)});
    const value=await response.json();assert.equal(response.status,200,JSON.stringify(value));return value;
  }
  async function prepare(action='budget_up',after='1100.00'){
    const status=await api('/api/browser-actions'),worker=status.workers.find(w=>w.armed);
    assert.ok(worker&&worker.surfaces.length,'A qualified surface should be advertised');
    const draft=await api('/api/browser-actions/create',{worker_id:worker.worker_id,epoch:worker.epoch,account_id:ACCOUNT,plan_id:PLAN,action,after,reason:'隔离DOM与本地HTTP完整回放'});
    await api('/api/browser-actions/approve',{id:draft.id,intent_digest:draft.intent_digest,confirmed:true});return draft;
  }
  return {context,local,session,events,model,requests,injections,tab,api,prepare,
    arm:()=>context.setBrowserControlArmed(true),run:()=>context.runBrowserControl()};
}

test('an unarmed worker never reads a page or polls action endpoints',async t=>{
  const h=await harness(t);await h.run();assert.equal(h.injections.length,0);assert.equal(h.requests.length,0);assert.equal(h.model.writes,0);
});
test('real Python queue and DOM adapter execute one approved budget, refresh and verify once',async t=>{
  const h=await harness(t);await h.arm();await h.run();assert.equal(h.model.writes,0);
  await h.prepare();await h.run();
  const status=await h.api('/api/browser-actions');assert.equal(status.actions[0].state,'verified');assert.equal(h.model.writes,1);assert.equal(h.model.reloads,1);assert.equal(h.model.budget,'1100.00');
  await h.run();assert.equal(h.model.writes,1);assert.equal(h.injections.filter(p=>p==='submit').length,1);
});
test('ROI and pause use their own field and exact target through the same approval chain',async t=>{
  for(const [action,after,expected] of [['roi_target_up','3.15','target_roi'],['pause_plan',false,'running']]){
    const h=await harness(t);await h.arm();await h.run();await h.prepare(action,after);await h.run();
    const record=(await h.api('/api/browser-actions')).actions[0];assert.equal(record.state,'verified');assert.equal(record.intent.field,expected);assert.equal(h.model.writes,1);
  }
});
test('wrong or missing document identity cannot advertise executable objects',async t=>{
  const h=await harness(t,{missingDocument:true});await h.arm();await h.run();
  const status=await h.api('/api/browser-actions');assert.equal(status.execution_available,false);assert.equal(h.model.writes,0);
});
test('two tabs exposing the same account and plan are ambiguous, with no fallback',async t=>{
  const h=await harness(t,{duplicateTabs:true});await h.arm();await h.run();
  assert.equal((await h.api('/api/browser-actions')).execution_available,false);assert.equal(h.model.writes,0);
});
test('navigating to another account after preparation prevents begin and submission',async t=>{
  const h=await harness(t,{navigateAfterPrepare:true});await h.arm();await h.run();await h.prepare();await h.run();
  const status=await h.api('/api/browser-actions');assert.equal(status.actions[0].state,'rejected');assert.equal(h.model.writes,0);assert.ok(!h.requests.some(r=>r.path.endsWith('/worker-begin')));
});
test('stopping execution at the last check does not submit or silently record success',async t=>{
  const h=await harness(t,{stopBeforeCommit:true});await h.arm();await h.run();await h.prepare();await h.run();
  const status=await h.api('/api/browser-actions');assert.equal(h.model.writes,0);assert.notEqual(status.actions[0].state,'verified');
});
test('reload failure and stale readback each hold unknown without a second mutation',async t=>{
  for(const options of [{reloadFails:true},{staleReadback:true}]){
    const h=await harness(t,options);await h.arm();await h.run();await h.prepare();await h.run();
    assert.equal((await h.api('/api/browser-actions')).actions[0].state,'unknown');assert.equal(h.model.writes,1);
    await h.run();assert.equal(h.model.writes,1);assert.equal(h.injections.filter(p=>p==='submit').length,1);
  }
});
test('a lost receipt acknowledgement resends identical receipt without reconstructing or repeating the action',async t=>{
  const h=await harness(t,{loseReceiptAck:true});await h.arm();await h.run();await h.prepare();await h.run();
  assert.ok(h.session.qc_control_receipt_v1);assert.equal(h.model.writes,1);
  await h.run();assert.equal(h.session.qc_control_receipt_v1,undefined);assert.equal(h.model.writes,1);
  const receipts=h.requests.filter(r=>r.path.endsWith('/worker-receipt')).map(r=>r.body);
  assert.equal(receipts.length,2);assert.deepEqual(receipts[0],receipts[1]);assert.equal((await h.api('/api/browser-actions')).actions[0].state,'verified');
});
test('live detail budget net ROI and pause use the explicit plan ID and separate setting basis',async t=>{
  for(const [action,after] of [['budget_up','1100.00'],['roi_target_up','3.15'],['pause_plan',false]]){
    const h=await harness(t,{live:true});await h.arm();await h.run();
    const before=await h.api('/api/browser-actions');assert.equal(before.workers[0].surfaces[0].roi_basis,'net');
    assert.ok(before.workers[0].surfaces[0].actions.includes('pause_plan'));
    await h.prepare(action,after);await h.run();assert.equal((await h.api('/api/browser-actions')).actions[0].state,'verified');assert.equal(h.model.writes,1);
  }
});
test('a concurrent start cannot re-enable execution after a later stop request',async t=>{
  const h=await harness(t);const enabling=h.context.setBrowserControlArmed(true),stopping=h.context.setBrowserControlArmed(false);
  await Promise.all([enabling,stopping]);assert.equal(h.session.qc_control_session_v1.armed,false);await h.run();assert.equal(h.model.writes,0);
});
test('a forged extension, frame, tab or document cannot consume the final commit authorization',async t=>{
  for(const senderOverride of [{id:'foreign'},{frameId:1},{tab:{id:99}},{documentId:'other-document'}]){
    const h=await harness(t,{senderOverride});await h.arm();await h.run();await h.prepare();await h.run();
    assert.equal(h.model.writes,0);assert.notEqual((await h.api('/api/browser-actions')).actions[0].state,'verified');
  }
});
test('an emulated extension session reset leaves execution off and cannot reuse the old switch',async t=>{
  const h=await harness(t);await h.arm();const previous=h.session.qc_control_session_v1.epoch;
  delete h.session.qc_control_session_v1;vm.runInContext('browserControlInit=null',h.context);
  await h.context.browserControlSession();assert.equal(h.session.qc_control_session_v1.armed,false);assert.notEqual(h.session.qc_control_session_v1.epoch,previous);
  const before=h.injections.length;await h.run();assert.equal(h.injections.length,before);assert.equal(h.model.writes,0);
});
test('a delayed session write cannot undo a later stop, which blocks work immediately',async t=>{
  const h=await harness(t);await h.arm();
  const storage=h.context.chrome.storage.session,originalSet=storage.set;
  storage.set=async value=>{await new Promise(resolve=>setTimeout(resolve,20));return originalSet(value);};
  const enabling=h.context.setBrowserControlArmed(true);
  await new Promise(resolve=>setTimeout(resolve,5));const stopping=h.context.setBrowserControlArmed(false);
  const before=h.injections.length;await h.run();assert.equal(h.injections.length,before);
  await Promise.all([enabling,stopping]);assert.equal(h.session.qc_control_session_v1.armed,false);assert.equal(h.model.writes,0);
});
