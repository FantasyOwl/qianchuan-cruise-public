const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const aid='1999999999999002',pid='8218616704149013',token='t'.repeat(43),source=fs.readFileSync(require.resolve('../extension/live-close-worker.js'),'utf8');
function fixture(options={}){
 const session={worker_id:'worker',epoch:'epoch',armed:true},policy={revision:'rev',accounts:[{id:aid,scene:'live',tab_group:'示例分组A'}]},config={token};
 const tab={id:10,groupId:3,status:'complete',url:`https://qianchuan.jinritemai.com/uni-prom?aavid=${aid}`},storage={},posts=[],alarms=[],calls=[],surfaces=[{account_id:aid,plan_id:pid,group:'示例分组A',path:'/uni-prom',home_url:tab.url,captured_at:new Date().toISOString(),parent_budget:'500000.00',running:true,busy:false,control_mode:'none',control_budget:null,control_minutes:null,control_created_at:null}];
 const intent={target_kind:'live_close',action:'one_click_control',account_id:aid,plan_id:pid,group:'示例分组A',home_url:tab.url,trigger_at:new Date(Date.now()-1000).toISOString(),budget:'200.00',duration_minutes:30},command={id:'a'.repeat(32),lease:'lease',intent,intent_digest:'digest',expires_at:new Date(Date.now()+120000).toISOString()};
 const parent={account_id:aid,plan_id:pid,aweme_id:'79987870170',group:'示例分组A',detail_url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${aid}&adId=${pid}`,captured_at:new Date().toISOString()};
 if(options.mapped){Object.assign(intent,{aweme_id:parent.aweme_id,detail_url:parent.detail_url});Object.assign(surfaces[0],{aweme_id:parent.aweme_id,detail_url:parent.detail_url,parent_captured_at:parent.captured_at});}
 let armed=options.armed!==false,offered=false,documentId='document-a',submits=0,reloads=0,prepared=false;
 const context={URL,Date,AbortSignal,setTimeout:(fn)=>setTimeout(fn,0),validObserverToken:v=>v===token,observerScopeCheckpoint:async()=>true,observerCheckpoint:async()=>true,browserControlCheckpoint:async()=>armed,
  identity:t=>{const u=new URL(t.url);return {scene:'live',role:u.pathname==='/uni-prom/detail'?'detail':'live',planId:u.searchParams.get('adId'),accountId:u.searchParams.get('aavid'),group:'示例分组A'};},
  chrome:{runtime:{id:'extension'},tabGroups:{get:async()=>({title:options.group||'示例分组A'})},alarms:{create:async(name,value)=>alarms.push({name,value})},
   storage:{session:{set:async x=>Object.assign(storage,x),get:async key=>({[key]:storage[key]}),remove:async key=>delete storage[key]}},
   tabs:{query:async()=>options.duplicates?[tab,{...tab,id:11}]:[tab],get:async id=>({...tab,id}),update:async(id,changes)=>{calls.push('navigate');tab.url=changes.url;},reload:async()=>{reloads++;documentId='document-b';}},
   scripting:{executeScript:async args=>{
    if(!args.args)return [{frameId:0,documentId,result:!options.editor}];
    const [phase,request]=args.args;calls.push(phase);
    if(phase==='inspect_parent')return [{frameId:0,documentId,result:{parent:{...parent,aweme_id:options.ownerMismatch?'79987870171':parent.aweme_id,captured_at:new Date().toISOString()}}}];
    if(phase==='inspect')return [{frameId:0,documentId,result:{surfaces:surfaces.map(s=>({...s,...(reloads?{busy:true,control_mode:'control',control_budget:'200.00',control_minutes:30,control_created_at:new Date().toISOString()}:{}),captured_at:new Date().toISOString()}))}}];
    if(phase==='prepare'){prepared=true;return [{frameId:0,documentId,result:options.busy?{ok:false,code:'plan_busy_or_not_running'}:{prepared:true,submit_token:'nonce',surface:{...surfaces[0]}}}];}
    if(phase==='submit'){
     const sender={id:'extension',frameId:0,tab:{id:tab.id},documentId,...(options.sender||{})};
     const permit=await context.liveCloseFinalCheck({id:request.id,nonce:request.submit_token},sender);
     if(permit.allow)submits++;
     return [{frameId:0,documentId,result:{submitted:permit.allow===true}}];
    }
    return [{frameId:0,documentId,result:{ok:true}}];
   }}},
  fetch:async(url,init)=>{
   assert.equal(new URL(url).origin,'http://127.0.0.1:8877');assert.equal(init.credentials,'omit');assert.equal(init.redirect,'error');const name=url.split('/').at(-1),body=JSON.parse(init.body);posts.push({name,body});
   let value={ok:true};if(name==='worker-observe')value.alarms=options.alarm?[{id:'b'.repeat(32),at:new Date(Date.now()+60000).toISOString()}]:[];
   if(name==='worker-parents')value.parents=options.mapped?[parent]:[];
   if(name==='worker-claim'){value.command=offered||options.noCommand?null:command;offered=true;}
   if(name==='worker-begin'){value.submit_once=true;if(options.stopAtBegin)armed=false;}
   if(name==='worker-receipt'){value.state=body.submitted&&body.observation?'verified':'unknown';if(options.lostReceipt&&!storage.lost){storage.lost=true;throw Error('lost ack');}}
   return {ok:true,status:200,json:async()=>value};
  },liveCloseDom:()=>{}};
 vm.createContext(context);vm.runInContext(source,context);
 return {context,config,session,policy,posts,calls,alarms,storage,get submits(){return submits;},get reloads(){return reloads;},run:()=>context.runLiveClose(config,session,policy)};
}
test('unarmed executor never navigates or claims timed commands',async()=>{const f=fixture({armed:false});assert.equal(await f.run(),false);assert.equal(f.calls.length,0);assert.equal(f.posts.length,0);});
test('confirmed timed command uses existing homepage, one commit, new-document receipt and no replay',async()=>{
 const f=fixture();assert.equal(await f.run(),true);assert.equal(f.submits,1);assert.equal(f.reloads,1);const begin=f.posts.find(p=>p.name==='worker-begin'),r=f.posts.find(p=>p.name==='worker-receipt');assert.equal(begin.body.observation.document_id,'document-a');assert.equal(r.body.observation.document_id,'document-b');assert.equal(r.body.submitted,true);await f.run();assert.equal(f.submits,1);
});
test('open editor, busy native entry, other group or duplicate pages cannot submit',async()=>{
 for(const opts of [{editor:true},{busy:true},{group:'other'},{duplicates:true}]){const f=fixture(opts);await f.run();assert.equal(f.submits,0);assert.equal(f.calls.includes('submit'),false);assert.equal(f.posts.at(-1).name,'worker-receipt');assert.equal(f.posts.at(-1).body.submitted,false);}
});
test('switch revocation and forged document fail final permission without advertising success',async()=>{
 for(const opts of [{stopAtBegin:true},{sender:{documentId:'forged'}},{sender:{frameId:1}},{sender:{id:'other'}}]){const f=fixture(opts);await f.run();assert.equal(f.submits,0);assert.equal(f.posts.at(-1).body.observation,null);}
});
test('persisted receipt is resent exactly and does not cause another submit',async()=>{
 const f=fixture({lostReceipt:true});await f.run();await f.context.liveCloseFlush(f.config);assert.equal(f.submits,1);const receipts=f.posts.filter(p=>p.name==='worker-receipt');assert.equal(receipts.length>=2,true);assert.deepEqual(receipts.at(-1).body,receipts[0].body);
});
test('future preconfirmed alarms register wakeups without creating a command',async()=>{
 const f=fixture({alarm:true,noCommand:true});await f.run();assert.equal(f.alarms.length,1);assert.match(f.alarms[0].name,/^live-close-/);assert.equal(f.submits,0);
});
test('mapped homepage execution first revisits the confirmed parent and rechecks aweme ownership',async()=>{
 const f=fixture({mapped:true});await f.run();assert.equal(f.submits,1);assert.equal(f.calls.includes('inspect_parent'),true);assert.equal(f.calls.filter(c=>c==='navigate').length,2);assert.equal(f.posts.find(p=>p.name==='worker-begin').body.observation.aweme_id,'79987870170');
});
test('a different aweme owner on the same parent detail prevents preparation and commit',async()=>{
 const f=fixture({mapped:true,ownerMismatch:true});await f.run();assert.equal(f.submits,0);assert.equal(f.calls.includes('prepare'),false);assert.equal(f.posts.some(p=>p.name==='worker-begin'),false);
});
