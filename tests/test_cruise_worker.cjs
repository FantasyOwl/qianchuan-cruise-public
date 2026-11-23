'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {spawn}=require('node:child_process'),{createInterface}=require('node:readline'),{once}=require('node:events'),{randomUUID}=require('node:crypto');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const ROOT=path.resolve(__dirname,'..'),ACCOUNT='1000001',PLAN='2000001',TASK='3000001';
// A real SQLite approval queue behind an isolated local HTTP server. No monitor
// process, persistent account database or external platform is used by tests.
const PYTHON_FIXTURE=String.raw`
import json,sys,tempfile,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from monitor.cruise_actions import CruiseActions
from monitor.batch_chase import BatchChaseActions
from monitor.live_close import LiveCloseSchedules
from monitor.cruise_rules import default_profile
policy={'protocol':2,'revision':'a'*64,'accounts':[{'id':'1000001','scene':'live','tab_group':'明确分组','observe':True},{'id':'1000002','scene':'product','tab_group':'明确分组','observe':True}]}
token='isolated_test_pairing_token_0123456789'
class H(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def send(self,status,value):
  b=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  if self.path=='/api/collector-config':return self.send(200,policy)
  if self.path=='/api/cruise/actions':return self.send(200,queue.status(policy))
  return self.send(404,{'error':'unhandled test GET'})
 def do_POST(self):
  try:
   p=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
   if self.path.startswith('/api/cruise/close/worker-'):
    if self.headers.get('X-Monitor-Token')!=token:return self.send(403,{})
    name=self.path.removeprefix('/api/cruise/close/worker-')
    if name=='parents':return self.send(200,{'ok':True,'parents':[]})
    methods={'observe':close.observe,'claim':close.claim,'begin':close.begin,'receipt':close.receipt}
    if name not in methods:return self.send(404,{})
    return self.send(200,methods[name](p) if name=='receipt' else methods[name](p,policy))
   if self.path.startswith('/api/cruise/batch/worker-'):
    if self.headers.get('X-Monitor-Token')!=token:return self.send(403,{})
    batch_method=self.path.removeprefix('/api/cruise/batch/worker-')
    methods={'observe':batch.observe,'claim':batch.claim,'begin':batch.begin,'receipt':batch.receipt}
    if batch_method not in methods:return self.send(404,{})
    return self.send(200,methods[batch_method](p) if batch_method=='receipt' else methods[batch_method](p,policy))
   if self.path=='/api/browser-actions/worker-observe':return self.send(200,{'ok':True})
   if self.path=='/api/browser-actions/worker-claim':return self.send(200,{'ok':True,'command':None})
   name=self.path.removeprefix('/api/cruise/actions/')
   if name.startswith('worker-'):
    if self.headers.get('X-Monitor-Token')!=token:return self.send(403,{})
    name=name.removeprefix('worker-')
   elif self.headers.get('X-Test-CSRF')!='isolated':return self.send(403,{})
   methods={'observe':queue.observe,'claim':queue.claim,'begin':queue.begin,'receipt':queue.receipt,'create':queue.create,'approve':queue.approve}
   if name not in methods:return self.send(404,{})
   return self.send(200,methods[name](p) if name=='receipt' else methods[name](p,policy))
  except ValueError as e:return self.send(400,{'error':str(e)})
  except Exception as e:return self.send(500,{'error':type(e).__name__+': '+str(e)})
with tempfile.TemporaryDirectory(prefix='cruise-worker-') as tmp:
 queue=CruiseActions(Path(tmp)/'queue.db')
 batch=BatchChaseActions(Path(tmp)/'batch.db',lambda aid:(default_profile(),'fixture-profile'))
 close=LiveCloseSchedules(Path(tmp)/'close.db')
 server=ThreadingHTTPServer(('127.0.0.1',0),H)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 print(json.dumps({'port':server.server_address[1],'token':token,'policy':policy}),flush=True)
 try:sys.stdin.readline()
 finally:server.shutdown();server.server_close();thread.join()
`;
async function harness(t,options={}){
  const python=process.env.QC_TEST_PYTHON||'python';
  const child=spawn(python,['-X','utf8','-u','-c',PYTHON_FIXTURE],{cwd:ROOT,stdio:['pipe','pipe','pipe'],windowsHide:true});
  const lines=createInterface({input:child.stdout});let stderr='';child.stderr.on('data',b=>stderr=(stderr+b.toString()).slice(-2000));
  const ready=await Promise.race([once(lines,'line').then(([s])=>JSON.parse(s)),once(child,'exit').then(()=>{throw Error('Test queue exited: '+stderr);})]);
  t.after(async()=>{lines.close();child.stdin.end('\n');if(child.exitCode===null)await once(child,'exit');});
  const local={token:ready.token,enabled:true,epoch:'observe-one'},session={},events={},requests=[],injections=[];
  const model={budget:'1500.00',hours:'2',status:'调控中',writes:0,reloads:0,navigations:0},tab={id:7,groupId:10,
    url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${ACCOUNT}&adId=${PLAN}`+(options.query||''),status:'complete'};
  const storage=o=>({get:async keys=>typeof keys==='string'?{[keys]:o[keys]}:Object.fromEntries((keys||Object.keys(o)).map(k=>[k,o[k]])),
    set:async value=>Object.assign(o,value),remove:async key=>{delete o[key];}});
  const event=name=>({addListener:fn=>events[name]=fn});let dom,docId,loseReceipt=options.loseReceiptAck===true;
  const context={URL,URLSearchParams,Date,Map,Set,AbortSignal,crypto:{randomUUID},setTimeout,clearTimeout,console};
  function render(){
    dom?.window.close();docId=randomUUID();
    const labels=['','任务名称','调控状态','调控预算(元)','调控时长(小时)','追投方式','创建时间','调控消耗','调控支付ROI','调控成交订单数','调控成交金额','调控综合成本'];
    dom=new JSDOM(`<body><pre>ID：${new URL(tab.url).searchParams.get('aavid')}</pre><div class="oc-promotion-key-info-bar">投放中\n计划ID：${PLAN}</div><div class="ovui-table"><table><thead><tr>${labels.map(h=>'<th>'+h+'</th>').join('')}</tr></thead><tbody><tr><td><input type="checkbox"></td><td><div>明确追投任务</div><div>ID：${TASK}</div><button class="stop">结束调控</button></td><td class="status">${model.status}</td><td class="budget"><span>${options.staleReadback&&model.reloads?'1500.00':model.budget}</span><iconpark-icon name="oc-icon-light-edit"></iconpark-icon></td><td class="duration"><span>${model.hours}</span><i class="edit-icon"></i></td><td>控成本追投</td><td>2026-10-25 07:00:00</td><td>100.00</td><td>2.50</td><td>5</td><td>250.00</td><td>20.00</td></tr></tbody></table><button disabled data-auto-id="bar-groups-group-item-btn-pause">暂停</button></div>${options.editorOpen?'<div role="dialog"><input value="用户草稿"><button>确定</button></div>':''}</body>`,{url:tab.url,runScripts:'outside-only'});
    const w=dom.window,d=w.document,blocks=new Set(['DIV','P','TR','TD','PRE','SECTION']);
    const rendered=n=>n.nodeType===3?n.textContent:n.nodeType!==1?'':[...n.childNodes].map(c=>(blocks.has(c.tagName)?'\n':'')+rendered(c)+(blocks.has(c.tagName)?'\n':'')).join('');
    Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return rendered(this).replace(/\n+/g,'\n').trim();}});
    w.HTMLElement.prototype.getClientRects=function(){return this.hidden||this.style.display==='none'?[]:[{}];};
    Object.defineProperty(w.crypto,'randomUUID',{value:randomUUID});
    w.chrome={runtime:{sendMessage:async message=>{
      if(options.stopBeforeCommit)session.qc_control_session_v1={...session.qc_control_session_v1,armed:false};
      if(options.dateBeforeCommit){tab.url=tab.url.replace('2026-10-25','2026-10-24');dom.reconfigure({url:tab.url});}
      return new Promise(resolve=>events.message(message,{id:'test-extension',tab:{id:tab.id},frameId:0,documentId:docId,url:tab.url,...(options.senderOverride||{})},resolve));
    }}};
    function dialog(title,body,save){
      const root=d.createElement('div');root.setAttribute('role','dialog');root.innerHTML='<h3>'+title+'</h3>'+body+'<button>确定</button><button>取消</button>';
      root.querySelector('button').addEventListener('click',()=>{save(root);root.remove();});root.querySelectorAll('button')[1].addEventListener('click',()=>root.remove());d.body.append(root);
    }
    d.querySelector('.budget iconpark-icon').addEventListener('click',()=>dialog('调控预算',`<input value="${model.budget}">`,root=>{
      model.writes++;model.budget=Number(root.querySelector('input').value).toFixed(2);d.querySelector('.budget span').textContent=model.budget;
    }));
    d.querySelector('.duration .edit-icon').addEventListener('click',()=>dialog('调控时长（小时）',`<input type="number" min="0.5" step="0.5" value="${model.hours}">`,root=>{
      model.writes++;model.hours=root.querySelector('input').value;d.querySelector('.duration span').textContent=model.hours;
    }));
    const checkbox=d.querySelector('input[type="checkbox"]'),pause=d.querySelector('[data-auto-id$="btn-pause"]');
    checkbox.addEventListener('change',()=>pause.disabled=!checkbox.checked);
    pause.addEventListener('click',()=>dialog('确认暂停调控任务 '+TASK,'',()=>{model.writes++;model.status='调控暂停';d.querySelector('.status').textContent=model.status;}));
    d.querySelector('.stop').addEventListener('click',()=>dialog('结束调控任务 '+TASK,'',()=>{model.writes++;model.status='调控结束';d.querySelector('.status').textContent=model.status;}));
  }
  context.chrome={storage:{local:storage(local),session:storage(session)},permissions:{contains:async()=>true},
    tabs:{query:async()=>options.duplicateTabs?[tab,{...tab,id:8}]:[tab],get:async id=>({...tab,id}),
      reload:async()=>{if(options.reloadFails)throw Error('reload failed');model.reloads++;render();},
      update:async(id,value)=>{assert.equal(id,7);assert.deepEqual(Object.keys(value),['url']);model.navigations++;tab.url=value.url;if(options.normalizeNavigation){const u=new URL(tab.url);u.searchParams.set('page','1');tab.url=u.href;}render();return tab;}},
    tabGroups:{query:async()=>[{id:10,title:'明确分组'}],get:async()=>({id:10,title:options.wrongGroup?'其他分组':'明确分组'})},
    scripting:{executeScript:async args=>{
      const before=docId;assert.equal(args.world,'ISOLATED');if(args.target.documentIds)assert.deepEqual(Array.from(args.target.documentIds),[docId]);
      injections.push({task:args.func.name==='cruiseTaskDom',phase:args.args[0]});
      const func=dom.window.eval('('+args.func.toString()+')'),result=await func(...args.args);
      if(options.navigateAfterPrepare&&args.args[0]==='prepare'){tab.url=tab.url.replace(ACCOUNT,'9999999');dom.reconfigure({url:tab.url});}
      if(options.dateAfterPrepare&&args.args[0]==='prepare'){tab.url=tab.url.replaceAll('2026-10-25','2026-10-24');dom.reconfigure({url:tab.url});}
      return [{frameId:0,...(options.missingDocument?{}:{documentId:before}),result}];
    }},
    action:{setBadgeText:async()=>{},setBadgeBackgroundColor:async()=>{}},alarms:{create:async()=>{},onAlarm:event('alarm')},
    runtime:{id:'test-extension',getURL:name=>'chrome-extension://test-extension/'+name,onInstalled:event('installed'),onStartup:event('startup'),onMessage:event('message')}
  };
  const json=value=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
  context.fetch=async(url,init={})=>{
    assert.ok(url.startsWith('http://127.0.0.1:8877/'));const pathname=new URL(url).pathname;
    requests.push({path:pathname,body:init.body?JSON.parse(init.body):null});
    if(pathname==='/api/cruise/worker-jobs')return json({protocol:1,revision:options.jobRevision||ready.policy.revision,navigation_allowed:options.navigationAllowed!==false,jobs:options.jobs||[]});
    if(pathname==='/api/cruise/worker-job-result')return json({ok:true});
    if(pathname==='/api/browser-actions/worker-claim'&&options.mainBlocked)return json({ok:true,command:null,blocked:'other queue unknown'});
    if(pathname==='/api/cruise/actions/worker-claim'&&options.taskBlocked)return json({ok:true,command:null,blocked:'task unknown'});
    const response=await fetch(url.replace('http://127.0.0.1:8877',`http://127.0.0.1:${ready.port}`),init);
    if(loseReceipt&&pathname==='/api/cruise/actions/worker-receipt'){loseReceipt=false;await response.text();throw Error('ack lost');}
    return response;
  };
  vm.createContext(context);context.importScripts=(...names)=>names.forEach(name=>vm.runInContext(fs.readFileSync(path.join(ROOT,'extension',name),'utf8'),context));
  vm.runInContext(fs.readFileSync(path.join(ROOT,'extension/background.js'),'utf8'),context);render();t.after(()=>dom.window.close());
  async function api(pathname,body){
    const response=await fetch(`http://127.0.0.1:${ready.port}${pathname}`,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Test-CSRF':'isolated'},body:JSON.stringify(body)});
    const data=await response.json();assert.equal(response.status,200,JSON.stringify(data));return data;
  }
  async function prepare(action='control_budget_up',after='1800.00'){
    const status=await api('/api/cruise/actions'),worker=status.workers.find(w=>w.armed);assert.ok(worker?.surfaces.length);
    const draft=await api('/api/cruise/actions/create',{worker_id:worker.worker_id,epoch:worker.epoch,account_id:ACCOUNT,plan_id:PLAN,task_id:TASK,action,after,reason:'隔离队列与任务页面完整回放'});
    await api('/api/cruise/actions/approve',{id:draft.id,intent_digest:draft.intent_digest,confirmed:true});return draft;
  }
  return {context,local,session,events,requests,injections,model,tab,api,prepare,
    appendDom:html=>dom.window.document.body.insertAdjacentHTML('beforeend',html),
    reconfigure:(url,refresh=false)=>{tab.url=url;dom.reconfigure({url});if(refresh)render();},arm:()=>context.setBrowserControlArmed(true),run:()=>context.runBrowserControl()};
}
test('unarmed shared session does not inspect, claim or navigate cruise tasks',async t=>{
  const h=await harness(t);await h.run();assert.equal(h.requests.length,0);assert.equal(h.injections.length,0);assert.equal(h.model.writes,0);
});
test('real task queue and task DOM perform one confirmed budget, duration, pause or stop with new-document readback',async t=>{
  for(const [action,after] of [['control_budget_up','1800.00'],['control_duration_up','150.00'],['control_pause',false],['control_stop','调控结束']]){
    const h=await harness(t);await h.arm();await h.run();assert.equal(h.model.writes,0);await h.prepare(action,after);await h.run();
    const record=(await h.api('/api/cruise/actions')).actions[0];assert.equal(record.state,'verified');assert.equal(h.model.writes,1);assert.equal(h.model.reloads,1);
    assert.equal(record.intent.task_id,TASK);assert.equal(record.intent.plan_id,PLAN);await h.run();assert.equal(h.model.writes,1);
    assert.equal(h.injections.filter(i=>i.task&&i.phase==='submit').length,1);
  }
});
test('extended read-only metrics are narrowed without converting missing values or losing explicit task context',async t=>{
  const h=await harness(t);await h.arm();await h.run();const observe=h.requests.find(r=>r.path==='/api/cruise/actions/worker-observe');
  assert.deepEqual(Object.keys(observe.body.surfaces[0].metrics).sort(),['comprehensive_cost','orders','roi','spend']);
  assert.equal(observe.body.surfaces[0].metrics.comprehensive_cost,20);assert.equal(observe.body.surfaces[0].context.control_delivery_mode,'cost_control');
  assert.equal(observe.body.surfaces[0].metric_basis,'control_payment');
});
test('statistics date comes only from a unique valid same-day URL range and never from the fresh capture clock',async t=>{
  for(const [query,expected] of [
    ['',undefined],['&dr=2026-10-25%2C2026-10-25','2026-10-25'],['&dr=2026-10-25%2C2026-10-26',undefined],
    ['&dr=2026-02-30%2C2026-02-30',undefined],['&dr=2026-10-25%2C2026-10-25&dr=2026-10-25%2C2026-10-25',undefined]]){
    const h=await harness(t,{query});await h.arm();await h.run();const status=await h.api('/api/cruise/actions');
    assert.equal(status.workers[0].surfaces[0].context.metric_date,expected);assert.equal(h.model.writes,0);
  }
});
test('duplicate tabs, wrong Chrome group or missing document cannot advertise an executable task',async t=>{
  for(const options of [{duplicateTabs:true},{wrongGroup:true},{missingDocument:true}]){
    const h=await harness(t,options);await h.arm();await h.run();assert.equal((await h.api('/api/cruise/actions')).execution_available,false);assert.equal(h.model.writes,0);
  }
});
test('account navigation after preparation prevents begin and never writes another account',async t=>{
  const h=await harness(t,{navigateAfterPrepare:true});await h.arm();await h.run();await h.prepare();await h.run();assert.equal(h.model.writes,0);
  assert.equal((await h.api('/api/cruise/actions')).actions[0].state,'rejected');assert.ok(!h.requests.some(r=>r.path==='/api/cruise/actions/worker-begin'));
});
test('statistics date changes before preparation, before begin or at final permission prevent any mutation',async t=>{
  for(const options of [{beforePrepare:true},{dateAfterPrepare:true},{dateBeforeCommit:true}]){
    const h=await harness(t,{query:'&dr=2026-10-25%2C2026-10-25',...options});await h.arm();await h.run();await h.prepare();
    if(options.beforePrepare)h.reconfigure(h.tab.url.replaceAll('2026-10-25','2026-10-24'));
    await h.run();assert.equal(h.model.writes,0);assert.notEqual((await h.api('/api/cruise/actions')).actions[0].state,'verified');
  }
});
test('forged sender or last-moment execution stop cannot save a prepared task editor',async t=>{
  for(const options of [{senderOverride:{id:'foreign'}},{senderOverride:{frameId:1}},{senderOverride:{tab:{id:9}}},{senderOverride:{documentId:'wrong'}},{stopBeforeCommit:true}]){
    const h=await harness(t,options);await h.arm();await h.run();await h.prepare();await h.run();assert.equal(h.model.writes,0);
    assert.notEqual((await h.api('/api/cruise/actions')).actions[0].state,'verified');
  }
});
test('reload failure and unchanged refreshed value remain unknown without a second submission',async t=>{
  for(const options of [{reloadFails:true},{staleReadback:true}]){
    const h=await harness(t,options);await h.arm();await h.run();await h.prepare();await h.run();
    assert.equal((await h.api('/api/cruise/actions')).actions[0].state,'unknown');assert.equal(h.model.writes,1);await h.run();assert.equal(h.model.writes,1);
  }
});
test('lost receipt acknowledgement resends identical persisted task receipt before further claims',async t=>{
  const h=await harness(t,{loseReceiptAck:true});await h.arm();await h.run();await h.prepare();await h.run();assert.ok(h.session.qc_cruise_receipt_v1);
  const before=h.requests.length;await h.run();assert.equal(h.session.qc_cruise_receipt_v1,undefined);assert.equal(h.model.writes,1);
  const calls=h.requests.slice(before);assert.ok(!calls.some(r=>r.path.endsWith('/worker-claim')));
  const receipts=h.requests.filter(r=>r.path==='/api/cruise/actions/worker-receipt');assert.equal(receipts.length,2);assert.deepEqual(receipts[0].body,receipts[1].body);
});
function job(overrides={}){
  return {id:'a'.repeat(24),account_id:ACCOUNT,plan_id:PLAN,lane:'main',url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${ACCOUNT}&adId=${PLAN}&dr=2026-10-26%2C2026-10-26#uniDetail=${encodeURIComponent(JSON.stringify({tb:'uni_task_center'}))}`,...overrides};
}
test('live homepage read reuses the current detail tab and preserves an existing editor',async t=>{
 const read=job({job_kind:'live_home',plan_id:'',url:`https://qianchuan.jinritemai.com/uni-prom?aavid=${ACCOUNT}&dr=2026-10-26%2C2026-10-26`});
 const h=await harness(t,{jobs:[read]});await h.context.runObserverReadJobs();assert.equal(h.model.navigations,1);assert.equal(h.model.reloads,1);assert.equal(h.model.writes,0);assert.equal(h.tab.url,read.url);
 const g=await harness(t,{jobs:[read],editorOpen:true});await g.context.runObserverReadJobs();assert.equal(g.model.navigations,0);assert.equal(g.model.writes,0);assert.match(g.requests.find(r=>r.path==='/api/cruise/worker-job-result').body.message,/编辑窗口/);
});
test('explicit local collection job reuses one exact existing parent page and refreshes without ad writes',async t=>{
  const h=await harness(t,{jobs:[job()]});await h.arm();await h.run();assert.equal(h.model.navigations,1);assert.equal(h.model.reloads,1);assert.equal(h.model.writes,0);
  const result=h.requests.find(r=>r.path==='/api/cruise/worker-job-result');assert.equal(result.body.ok,true);assert.equal(h.tab.url,job().url);
});
test('read-only navigation accepts platform-added URL parameters but still verifies account parent view and date',async t=>{
 const h=await harness(t,{jobs:[job()],normalizeNavigation:true});await h.arm();await h.run();
 assert.equal(h.model.reloads,1);assert.equal(h.model.writes,0);
 assert.equal(h.requests.find(r=>r.path==='/api/cruise/worker-job-result').body.ok,true);
 assert.match(h.tab.url,/page=1/);
});
test('explicit plan data refresh navigates a unique live list to its bound detail without changing advertisements',async t=>{
 const read=job({job_kind:'plan_data',url:job().url.replace(encodeURIComponent('uni_task_center'),'data')});
 const h=await harness(t,{jobs:[read],normalizeNavigation:true});
 h.reconfigure(`https://qianchuan.jinritemai.com/uni-prom?aavid=${ACCOUNT}`);
 await h.arm();await h.run();assert.equal(h.model.navigations,1);assert.equal(h.model.reloads,1);assert.equal(h.model.writes,0);
 assert.equal(h.requests.find(r=>r.path==='/api/cruise/worker-job-result').body.ok,true);
});
test('a live data refresh preserves the existing complete route instead of replacing it with a minimal address',async t=>{
 const read=job({job_kind:'plan_data',url:job().url.replace(encodeURIComponent('uni_task_center'),'data')});
 const h=await harness(t,{jobs:[read],normalizeNavigation:true});
 const full=new URL(job().url);full.searchParams.set('ct','1');full.hash='uniDetail='+encodeURIComponent(JSON.stringify({tb:'uni_task_center',bcf:'saved-verified-route'}));
 h.reconfigure(full.href,true);await h.context.runObserverReadJobs();
 const result=new URL(h.tab.url);assert.equal(result.searchParams.get('ct'),'1');
 assert.equal(JSON.parse(new URLSearchParams(result.hash.slice(1)).get('uniDetail')).bcf,'saved-verified-route');
 assert.equal(JSON.parse(new URLSearchParams(result.hash.slice(1)).get('uniDetail')).tb,'data');
 assert.equal(h.model.writes,0);assert.equal(h.model.reloads,1);
});
test('an empty live plan ID is recovered only through the registered full read-only target',async t=>{
 const full=new URL(job().url);full.searchParams.set('ct','1');full.hash='uniDetail='+encodeURIComponent(JSON.stringify({tb:'data',bcf:'verified-parent-route'}));
 const h=await harness(t,{jobs:[job({job_kind:'plan_data',url:full.href})],normalizeNavigation:true});
 h.reconfigure(`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${ACCOUNT}&adId=`,true);
 await h.context.runObserverReadJobs();assert.equal(new URL(h.tab.url).searchParams.get('adId'),PLAN);
 assert.equal(new URL(h.tab.url).searchParams.get('ct'),'1');assert.equal(h.model.writes,0);
 assert.equal(h.requests.find(r=>r.path==='/api/cruise/worker-job-result').body.ok,true);
});
test('product subview changes even when its date is already today',async t=>{
 const read=job({account_id:'1000002',plan_id:'',job_kind:'plan_data',url:'https://qianchuan.jinritemai.com/overall-prom?aavid=1000002&dateRange=2026-10-26%2C2026-10-26#uniVideoTab=1'});
 const h=await harness(t,{jobs:[read]});h.reconfigure('https://qianchuan.jinritemai.com/overall-prom?aavid=1000002&dateRange=2026-10-26%2C2026-10-26#?uniVideoTab=2',true);
 await h.context.runObserverReadJobs();assert.equal(h.model.navigations,1);
 assert.equal(new URLSearchParams(new URL(h.tab.url).hash.slice(2)).get('uniVideoTab'),'1');assert.equal(h.model.writes,0);
 assert.equal(new URLSearchParams(new URL(h.tab.url).hash.slice(2)).get('pageSize'),'50');
});
test('product data refresh preserves active filters and hash prefix while selecting the requested day',async t=>{
 const read=job({account_id:'1000002',plan_id:'',job_kind:'plan_data',url:'https://qianchuan.jinritemai.com/overall-prom?aavid=1000002&dateRange=2026-10-26%2C2026-10-26'});
 const h=await harness(t,{jobs:[read],normalizeNavigation:true});
 h.reconfigure('https://qianchuan.jinritemai.com/overall-prom?aavid=1000002&dateRange=2026-10-25%2C2026-10-25#?overallPromViewTab=product&sortType=desc',true);
 await h.context.runObserverReadJobs();assert.equal(h.model.navigations,1);assert.equal(h.model.reloads,1);assert.equal(h.model.writes,0);
 assert.equal(new URL(h.tab.url).searchParams.get('dateRange'),'2026-10-26,2026-10-26');
 assert.match(new URL(h.tab.url).hash,/^#\?overallPromViewTab=product/);assert.match(h.tab.url,/sortType=desc/);
 assert.ok(!h.requests.some(r=>r.path.endsWith('/worker-claim')||r.path.endsWith('/worker-begin')));
});
test('a read-only data refresh works with execution off but never navigates an open editor',async t=>{
 const read=job({job_kind:'plan_data',url:job().url.replace(encodeURIComponent('uni_task_center'),'data')});
 const h=await harness(t,{jobs:[read],editorOpen:true});await h.context.runObserverReadJobs();
 assert.equal(h.model.navigations,0);assert.equal(h.model.reloads,0);assert.equal(h.model.writes,0);
 assert.match(h.requests.find(r=>r.path==='/api/cruise/worker-job-result').body.message,/编辑窗口/);
});

test('read-only refresh proceeds past a plain hover tip without claiming an advertising action',async t=>{
 const read=job({job_kind:'plan_data',url:job().url.replace(encodeURIComponent('uni_task_center'),'data')});
 const h=await harness(t,{jobs:[read]});h.appendDom('<div class="ovui-popover">整体消耗说明</div>');
 await h.context.runObserverReadJobs();
 assert.equal(h.model.reloads,1);assert.equal(h.model.writes,0);
 assert.equal(h.requests.find(r=>r.path==='/api/cruise/worker-job-result').body.ok,true);
 assert.ok(!h.requests.some(r=>r.path.endsWith('/worker-begin')));
});

test('a failed account label check is not reported as an open editor',async t=>{
 const read=job({job_kind:'plan_data',url:job().url.replace(encodeURIComponent('uni_task_center'),'data')});
 const h=await harness(t,{jobs:[read]});
 const original=h.context.browserControlInject;
 h.context.browserControlInject=async(...args)=>args[1]==='inspect_navigation'?{result:{ok:false,code:'account_label_missing'}}:original(...args);
 await h.context.runObserverReadJobs();
 const receipt=h.requests.find(r=>r.path==='/api/cruise/worker-job-result').body;
 assert.equal(receipt.ok,false);assert.match(receipt.message,/账户标识/);assert.doesNotMatch(receipt.message,/编辑窗口/);
 assert.equal(h.model.navigations,0);assert.equal(h.model.reloads,0);assert.equal(h.model.writes,0);
});
test('jobs are not navigated while either queue is blocked, server navigation is disabled or policy differs',async t=>{
  for(const options of [{mainBlocked:true},{taskBlocked:true},{navigationAllowed:false},{jobRevision:'b'.repeat(64)}]){
    const h=await harness(t,{jobs:[job()],...options});await h.arm();await h.run();assert.equal(h.model.navigations,0);assert.equal(h.model.reloads,0);assert.equal(h.model.writes,0);
  }
});
test('job scope rejects foreign account, different parent, duplicate identity params and unsupported route/view',async t=>{
  const good=job();
  const bad=[job({account_id:'9999999'}),job({plan_id:'9999999'}),job({url:good.url.replace('/uni-prom/detail','/api/write')}),
    job({url:good.url.replace('aavid='+ACCOUNT,'aavid='+ACCOUNT+'&aavid='+ACCOUNT)}),job({url:good.url.replace(encodeURIComponent('uni_task_center'),encodeURIComponent('unknown_write'))})];
  const h=await harness(t,{jobs:bad});await h.arm();await h.run();assert.equal(h.model.navigations,0);assert.equal(h.model.reloads,0);assert.equal(h.model.writes,0);
});
test('ambiguous existing pages produce a failed read-only job without selecting a fallback',async t=>{
  const h=await harness(t,{jobs:[job()],duplicateTabs:true});await h.arm();await h.run();assert.equal(h.model.navigations,0);assert.equal(h.model.reloads,0);
  assert.equal(h.requests.find(r=>r.path==='/api/cruise/worker-job-result').body.ok,false);
});
test('stopping shared execution revokes task inventory and clears both pending commit permits',async t=>{
  const h=await harness(t);await h.arm();await h.run();await h.context.setBrowserControlArmed(false);
  assert.equal((await h.api('/api/cruise/actions')).execution_available,false);assert.equal(h.session.qc_control_session_v1.armed,false);assert.equal(h.model.writes,0);
});
