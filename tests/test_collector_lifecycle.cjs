const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.resolve(__dirname,'../extension');
const token='t'.repeat(43);
const defaultPolicy={protocol:2,revision:'a'.repeat(64),accounts:[{id:'1999999999999002',scene:'live',tab_group:'示例分组A'},{id:'1999999999999001',scene:'product',tab_group:'示例分组A'}]};
const url=(role='live')=>`https://qianchuan.jinritemai.com/${role==='board'?'board-next':role==='product'?'overall-prom':'uni-prom'}?aavid=${role==='product'?'1999999999999001':'1999999999999002'}`;

function worker(options={}){
  const store={token,enabled:true,epoch:'epoch-1',...options.store};
  const events={},requests=[],policyRequests=[],injected=[],queries=[],gets=new Map(),alarmRequests=[];let granted=options.granted??true,alarm=options.noAlarm?undefined:{periodInMinutes:1};
  const tabs=options.tabs||[{id:1,groupId:10,url:url()},{id:2,groupId:10,url:url('board')},{id:3,groupId:10,url:url('product')}];
  const event=name=>({addListener:fn=>events[name]=fn});
  const context={URL,Map,Set,Date,AbortSignal,setTimeout,clearTimeout,crypto:{randomUUID:()=>`epoch-${Math.random()}`},chrome:{
    permissions:{contains:async()=>granted},
    storage:{local:{get:async()=>({...store}),set:async value=>Object.assign(store,value)},session:{get:async()=>({})}},
    tabs:{query:async query=>{queries.push(query);return tabs;},get:async id=>{
      const count=(gets.get(id)||0)+1;gets.set(id,count);
      return options.get?options.get(id,count,tabs):tabs.find(t=>t.id===id);
    }},
    tabGroups:{query:async q=>(options.groups||[{id:10,title:'示例分组A'}]).filter(g=>g.title===q.title),get:async id=>(options.groups||[{id:10,title:'示例分组A'}]).find(g=>g.id===id)||{id,title:'其他'}},
    scripting:{executeScript:async args=>{
      injected.push(args.target.tabId);
      if(options.inject)await options.inject(args,store,()=>granted=false);
      return [{result:options.result?options.result(args):{snapshot:{account_id:args.args[0].accountId,entity_id:String(args.target.tabId)}}}];
    }},
    action:{setBadgeText:async()=>{},setBadgeBackgroundColor:async()=>{}},
    alarms:{get:async()=>alarm,create:async(name,value)=>{alarmRequests.push({name,value});alarm=value;},onAlarm:event('alarm')},
    runtime:{id:'unit-extension',getURL:name=>'chrome-extension://unit-extension/'+name,
      onInstalled:event('installed'),onStartup:event('startup'),onMessage:event('message')}
  },fetch:async(address,init={})=>{
    if(address.endsWith('/api/collector-config')){
      policyRequests.push({address,options:init});
      const policy=typeof options.policy==='function'?options.policy(policyRequests.length):options.policy||defaultPolicy;
      return {ok:true,json:async()=>policy};
    }
    requests.push({address,options:init,body:JSON.parse(init.body)});
    if(options.fetch)return options.fetch(address,init);
    return {ok:true,json:async()=>({ok:true})};
  }};
  vm.createContext(context);
  context.importScripts=(...names)=>names.forEach(name=>vm.runInContext(fs.readFileSync(path.join(root,name),'utf8'),context));
  vm.runInContext(fs.readFileSync(path.join(root,'background.js'),'utf8'),context);
  return {context,store,events,requests,policyRequests,injected,queries,alarmRequests,collect:()=>context.collect()};
}

test('manifest defers narrow site grants and does not request global tabs or cookie access',()=>{
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
  assert.match(manifest.version,/^1\.\d+\.\d+$/);assert.equal(manifest.host_permissions,undefined);
  assert.deepEqual(manifest.permissions,['tabGroups','scripting','alarms','storage']);
  assert.deepEqual(manifest.optional_host_permissions,['https://qianchuan.jinritemai.com/*','http://127.0.0.1:8877/*']);
  assert.equal(manifest.externally_connectable,undefined);assert.equal(manifest.content_scripts,undefined);
});

test('an authorized round sends only to two fixed local paths with redirects and credentials disabled',async()=>{
  const w=worker();await w.collect();assert.deepEqual(w.injected,[1,2,3]);assert.equal(w.requests.length,2);
  assert.deepEqual(w.requests.map(r=>r.address),['http://127.0.0.1:8877/api/ingest','http://127.0.0.1:8877/api/heartbeat']);
  assert.ok(w.requests.every(r=>r.options.redirect==='error'&&r.options.credentials==='omit'&&r.options.cache==='no-store'));
  assert.equal(w.requests[1].body.tab_count,3);
});

test('worker initialization recreates a missing minute alarm without relying on install/startup or arming execution',async()=>{
 const w=worker({noAlarm:true});await new Promise(setImmediate);
 assert.equal(w.alarmRequests.length,1);assert.equal(w.alarmRequests[0].name,'observe');
 assert.equal(w.alarmRequests[0].value.periodInMinutes,1);assert.equal(w.injected.length,0);assert.equal(w.requests.length,0);
});

test('a queued API-ID job runs before ordinary DOM collection and a slow collector cannot block that worker poll',async()=>{
 const w=worker(),order=[];
 w.context.runHybridRules=async()=>{order.push('rules');return false;};
 w.context.runHybridChase=async()=>{order.push('chase');return true;};
 w.context.collect=async()=>{throw Error('普通采集不应先执行');};
 await w.events.alarm({name:'observe'});assert.deepEqual(order,['rules','chase']);assert.equal(w.requests.length,0);
});

test('a nonresponsive page read times out without preventing the next page and no timed-out data is ingested',async()=>{
 const w=worker({inject:args=>args.target.tabId===1?new Promise(()=>{}):Promise.resolve()});
 w.context.setTimeout=fn=>setTimeout(fn,1);await w.collect();
 assert.deepEqual(w.injected,[1,2,3]);assert.equal(w.requests.at(-1).body.tab_count,2);
 assert.ok(w.requests[0].body.snapshots.every(s=>s.entity_id!=='1'));
});

test('first install stays disabled and does not read a page or transmit',async()=>{
  const w=worker({store:{enabled:undefined}});await w.events.installed();await w.collect();
  assert.equal(w.store.enabled,false);assert.equal(w.injected.length,0);assert.equal(w.requests.length,0);
});

test('disabled, missing and malformed pairing states never query tabs',async()=>{
  for(const store of [{enabled:false},{token:''},{token:'invalid short'}]){
    const w=worker({store});await w.collect();assert.equal(w.queries.length,0);assert.equal(w.requests.length,0);
  }
});

test('ungranted site access pauses before any tab query or injection',async()=>{
  const w=worker({granted:false});await w.collect();assert.equal(w.store.enabled,false);
  assert.equal(w.queries.length,0);assert.equal(w.injected.length,0);assert.equal(w.requests.length,0);
});

test('pause or epoch/token replacement during a read discards the pending batch',async()=>{
  for(const change of [{enabled:false},{epoch:'epoch-2'},{token:'n'.repeat(43)}]){
    const w=worker({inject:async(args,store)=>Object.assign(store,change)});await w.collect();
    assert.equal(w.injected.length,1);assert.equal(w.requests.length,0);
  }
});

test('revoked permission during a read prevents its local upload',async()=>{
  const w=worker({inject:async(args,store,revoke)=>revoke()});await w.collect();
  assert.equal(w.injected.length,1);assert.equal(w.requests.length,0);
});

test('same-account navigation from live management to board is not accepted as the old role',async()=>{
  const w=worker({get:(id,count,tabs)=>id===1?{id,groupId:10,url:url('board')}:tabs.find(t=>t.id===id)});
  await w.collect();assert.deepEqual(w.injected,[2,3]);
  assert.ok(w.requests[1].body.issues.some(s=>s.includes('缺少 live')));
});

test('a closing tab does not consume the live role before an eligible fallback tab',async()=>{
  const tabs=[{id:1,groupId:10,url:url()},{id:4,groupId:10,url:url()}];
  const w=worker({tabs,get:(id,count,tabs)=>{if(id===1)throw Error('closed');return tabs.find(t=>t.id===id);}});
  await w.collect();assert.deepEqual(w.injected,[4]);assert.equal(w.requests[0].body.snapshots[0].entity_id,'4');
});

test('moving a read tab outside the authorized group drops its data before sending',async()=>{
  const w=worker({get:(id,count,tabs)=>({...tabs.find(t=>t.id===id),groupId:id===1&&count>1?20:10})});
  await w.collect();assert.equal(w.requests[0].body.snapshots.length,2);
  assert.ok(!w.requests[0].body.snapshots.some(s=>s.entity_id==='1'));assert.equal(w.requests[1].body.tab_count,2);
});

test('page or prefix-lookalike senders cannot trigger collection',()=>{
  const w=worker();let replied=false;
  for(const sender of [{id:'unit-extension',url:url()},{id:'unit-extension',url:'chrome-extension://unit-extension/popup.html-other'},
    {id:'foreign',url:'chrome-extension://unit-extension/popup.html'}])w.events.message({action:'collect'},sender,()=>replied=true);
  assert.equal(w.injected.length,0);assert.equal(w.requests.length,0);assert.equal(replied,false);
});

test('unknown local paths fail before a request and network failures do not persist raw error text',async()=>{
  const w=worker({fetch:async()=>{throw Error('secret-test-error');}});
  await assert.rejects(w.context.post('/api/settings',{},token),/白名单/);assert.equal(w.requests.length,0);
  await w.collect();assert.ok(!JSON.stringify(w.store.lastState).includes('secret-test-error'));
  assert.ok(!JSON.stringify(w.store.lastState).includes(token));
});

function popup(granted){
  const store={},elements=new Map(),requests=[],messages=[],connections=[],alarmRequests=[];
  let alarm={periodInMinutes:1};
  const el=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',addEventListener(name,fn){this[name]=fn;}});return elements.get(id);};
  const context={AbortSignal,document:{querySelector:el},crypto:{randomUUID:()=>`epoch-${Math.random()}`},chrome:{
    permissions:{contains:async()=>granted,request:async value=>{requests.push(value);return granted;}},
    storage:{local:{get:async()=>({...store}),set:async value=>Object.assign(store,value)},session:{get:async()=>({})}},
    alarms:{get:async()=>alarm,create:async(name,value)=>{alarmRequests.push({name,value});alarm=value;}},
    runtime:{getManifest:()=>({version:'1.10.0'}),sendMessage:async value=>messages.push(value)}
  },fetch:async(url,options)=>{connections.push({url,options});return {ok:true,json:async()=>url.endsWith('/api/collector-config')?defaultPolicy:{ok:true,extension_version:'1.10.0',armed:false,ad_changes:0}};}};
  vm.createContext(context);
  for(const name of ['access.js','popup.js'])vm.runInContext(fs.readFileSync(path.join(root,name),'utf8'),context);
  return {store,elements,requests,messages,el,connections,context,alarmRequests,dropAlarm:()=>alarm=undefined};
}

test('multiple live accounts have distinct slots and only their explicitly configured groups are read',async()=>{
  const policy={protocol:2,revision:'b'.repeat(64),accounts:[{id:'101',scene:'live',tab_group:'甲组'},{id:'202',scene:'live',tab_group:'乙组'},{id:'303',scene:'product',tab_group:'甲组'}]};
  const make=(id,groupId,account,path)=>({id,groupId,url:`https://qianchuan.jinritemai.com/${path}?aavid=${account}`});
  const tabs=[make(1,10,'101','uni-prom'),make(2,10,'101','board-next'),make(3,20,'202','uni-prom'),make(4,20,'202','board-next'),make(5,10,'303','overall-prom'),make(6,10,'202','uni-prom'),make(7,20,'999','uni-prom'),make(8,10,'101','uni-prom/detail')];
  const w=worker({policy,tabs,groups:[{id:10,title:'甲组'},{id:20,title:'乙组'}]});await w.collect();
  assert.deepEqual(w.injected,[1,2,3,4,5]);assert.equal(w.requests.at(-1).body.tab_count,5);
  assert.equal(w.requests[0].body.collector_protocol,2);assert.equal(w.requests[0].body.policy_revision,policy.revision);
  assert.ok(w.policyRequests.every(r=>r.address==='http://127.0.0.1:8877/api/collector-config'&&r.options.credentials==='omit'&&r.options.redirect==='error'));
});

test('policy changes while a read is in flight discard pending data and old scope heartbeat',async()=>{
  let changed=false;
  const w=worker({policy:()=>({...defaultPolicy,revision:(changed?'b':'a').repeat(64)}),inject:async()=>{changed=true;}});
  await w.collect();assert.equal(w.injected.length,1);assert.equal(w.requests.length,0);
});

test('pause during the awaited policy check prevents the next page injection',async()=>{
  let w;w=worker({policy:n=>{if(n>1)w.store.enabled=false;return defaultPolicy;}});
  await w.collect();assert.equal(w.injected.length,0);assert.equal(w.requests.length,0);
});

test('invalid account policies fail before any tab query and do not fall back to hard-coded accounts',async()=>{
  for(const policy of [{...defaultPolicy,revision:'bad'},{...defaultPolicy,accounts:[defaultPolicy.accounts[0],defaultPolicy.accounts[0]]},
    {...defaultPolicy,accounts:[{id:'101',scene:'live',tab_group:''}]},{...defaultPolicy,protocol:1}]){
    const w=worker({policy});await w.collect();assert.equal(w.queries.length,0);assert.equal(w.injected.length,0);assert.equal(w.requests.length,0);
  }
});

test('an empty active scope reads no account and still communicates its exact policy',async()=>{
  const w=worker({policy:{protocol:2,revision:'b'.repeat(64),accounts:[]}});await w.collect();
  assert.equal(w.injected.length,0);assert.equal(w.requests.length,1);assert.equal(w.requests[0].body.tab_count,0);assert.equal(w.requests[0].body.collector_protocol,2);
});

test('large multi-account observations use bounded posts and recheck cancellation between chunks',async()=>{
  const result=args=>({snapshots:Array.from({length:250},(_,i)=>({account_id:args.args[0].accountId,entity_id:String(i)}))});
  const tabs=[{id:1,groupId:10,url:url()}];const w=worker({tabs,result});await w.collect();
  assert.deepEqual(w.requests.filter(r=>r.address.endsWith('/api/ingest')).map(r=>r.body.snapshots.length),[100,100,50]);
  let stopped;stopped=worker({tabs,result,fetch:async()=>{stopped.store.enabled=false;return{ok:true,json:async()=>({ok:true})};}});await stopped.collect();
  assert.equal(stopped.requests.length,1);assert.equal(stopped.requests[0].body.snapshots.length,100);
});

test('same-account detail tabs have separate exact plan identities and cannot consume overview roles',async()=>{
  const make=(id,plan)=>({id,groupId:10,url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=1999999999999002&adId=${plan}`});
  const w=worker({tabs:[make(1,'8339456325564332585'),make(2,'8618530283142212632'),{id:3,groupId:10,url:url()}]});
  await w.collect();assert.deepEqual(w.injected,[1,2,3]);assert.equal(w.requests.at(-1).body.tab_count,3);
  assert.equal(w.requests.at(-1).body.issues.some(x=>x.includes('缺少 live')),false);
});

test('detail navigation to another plan during observation discards the old plan batch',async()=>{
  const original={id:1,groupId:10,url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=1999999999999002&adId=8339456325564332585`};
  const w=worker({tabs:[original],get:(id,count)=>count>1?{...original,url:original.url.replace('8339456325564332585','8618530283142212632')}:original});
  await w.collect();assert.equal(w.injected.length,1);assert.equal(w.requests.length,1);
  assert.ok(w.requests[0].address.endsWith('/api/heartbeat'));assert.equal(w.requests[0].body.tab_count,0);
});

test('ambiguous account and plan parameters never establish an observation identity',async()=>{
  const detail=`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=1999999999999002&adId=8339456325564332585`;
  for(const suffix of ['&aavid=1999999999999001','&adId=8618530283142212632']){
    const w=worker({tabs:[{id:1,groupId:10,url:detail+suffix}]});await w.collect();assert.equal(w.injected.length,0);
  }
});

test('denying the popup site prompt keeps collection disabled and does not save a new token',async()=>{
  const p=popup(false);await Promise.resolve();await Promise.resolve();p.el('#token').value=token;
  await p.el('#save').click();assert.equal(p.store.enabled,false);assert.equal(p.store.token,undefined);
  assert.equal(p.messages.length,0);assert.match(p.el('#status').textContent,/保持暂停/);
});

test('popup starts only after its user permission grant and pause advances the cancellation epoch',async()=>{
  const p=popup(true);await Promise.resolve();await Promise.resolve();p.el('#token').value=token;
  await p.el('#save').click();assert.equal(p.store.enabled,true);assert.equal(p.store.token,token);
  assert.deepEqual(p.messages.map(m=>m.action),['control-stop','collect']);
  const epoch=p.store.epoch;await p.el('#stop').click();assert.equal(p.store.enabled,false);assert.notEqual(p.store.epoch,epoch);
  assert.match(p.el('#status').textContent,/不再启动新读取/);assert.equal(p.requests.length,1);
});
test('execution popup requires its separate user checkbox before requesting execution mode',async()=>{
  const p=popup(true);await Promise.resolve();await Promise.resolve();
  await p.el('#control-enable').click();assert.equal(p.messages.length,0);assert.match(p.el('#control-status').textContent,/先核对/);
  p.el('#control-consent').checked=true;await p.el('#control-enable').click();assert.equal(p.messages[0].action,'control-start');
});

test('connection check works with execution closed and never invokes an execution worker or changes its switch',async()=>{
 const p=popup(true);await new Promise(setImmediate);Object.assign(p.store,{enabled:true,token});
 await p.el('#control-check').click();
 assert.match(p.el('#control-status').textContent,/执行模式关闭；本机连接正常（扩展1.10.0）/);
 assert.deepEqual(p.connections.map(r=>r.url),['http://127.0.0.1:8877/api/collector-config','http://127.0.0.1:8877/api/cruise/hybrid/worker-ping']);
 assert.equal(JSON.parse(p.connections[1].options.body).armed,false);
 assert.deepEqual(p.messages,[]);assert.equal(p.store.enabled,true);
});

test('connection check reports a local authorization failure without showing a token or pretending connected',async()=>{
 const p=popup(true);await new Promise(setImmediate);Object.assign(p.store,{enabled:true,token});
 p.context.fetch=async()=>({ok:false,status:403});await p.el('#control-check').click();
 assert.match(p.el('#control-status').textContent,/连接检查失败.*HTTP 403/);
 assert.equal(p.el('#control-status').textContent.includes(token),false);assert.deepEqual(p.messages,[]);
});

test('paused collection cannot be resumed or authorized by a connection check',async()=>{
 const p=popup(true);await new Promise(setImmediate);Object.assign(p.store,{enabled:false,token});
 await p.el('#control-check').click();assert.match(p.el('#control-status').textContent,/先开启只读采集/);
 assert.equal(p.connections.length,0);assert.equal(p.requests.length,0);assert.deepEqual(p.messages,[]);assert.equal(p.store.enabled,false);
});

test('a dropped reader alarm is restored for an enabled collector without enabling execution',async()=>{
 const p=popup(true);await new Promise(setImmediate);Object.assign(p.store,{enabled:true,token});p.dropAlarm();
 await p.el('#control-check').click();
 assert.equal(p.alarmRequests.length,1);assert.equal(p.alarmRequests[0].name,'observe');
 assert.equal(p.alarmRequests[0].value.periodInMinutes,1);assert.ok(p.alarmRequests[0].value.when>Date.now()-10000);
 assert.match(p.el('#control-status').textContent,/执行模式关闭.*已恢复定时读取/);assert.deepEqual(p.messages,[]);
});
