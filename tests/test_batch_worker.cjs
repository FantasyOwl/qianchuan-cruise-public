'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../extension/batch-worker.js'),'utf8');
function fixture(options={}){
 const config={token:'t'.repeat(43)},session={worker_id:'worker',epoch:'epoch'},policy={revision:'rev'},storage={},posts=[];
 const surface={tab_id:10,document_id:'old-document'},command={id:'a'.repeat(32),lease:'lease',intent_digest:'digest',expires_at:new Date(Date.now()+60000).toISOString(),intent:{surface}};
 let armed=true,offered=false,submits=0,ackLost=false;
 const context={URL,URLSearchParams,Date,AbortSignal,setTimeout,clearTimeout,
  validObserverToken:v=>v===config.token,observerCheckpoint:async()=>true,observerScopeCheckpoint:async()=>true,
  browserControlCheckpoint:async()=>armed,browserControlTab:async()=>({identity:{},tab:{url:'https://qianchuan.jinritemai.com/uni-prom/detail',status:'complete'}}),batchMaterialDom:()=>{},
  chrome:{runtime:{id:'extension'},storage:{session:{set:async v=>Object.assign(storage,v),get:async k=>({[k]:storage[k]}),remove:async k=>delete storage[k]},local:{set:async()=>{}}}},
  fetch:async(url,init)=>{
   assert.equal(new URL(url).origin,'http://127.0.0.1:8877');const name=url.split('/').at(-1),body=JSON.parse(init.body);posts.push({name,body});let value={ok:true};
   if(name==='worker-claim'){value.command=offered?null:command;offered=true;}
   if(name==='worker-begin'){value.submit_once=true;if(options.revokeAtBegin)armed=false;}
   if(name==='worker-receipt'){value.state=body.submitted&&body.observation?'verified':'rejected';if(options.lostReceipt&&!ackLost){ackLost=true;throw Error('ack lost');}}
   return {ok:true,status:200,json:async()=>value};
  }};
 vm.createContext(context);vm.runInContext(source,context);
 context.batchObserve=async()=>[];context.batchRecheck=async()=>false;context.batchReadback=async()=>({task_id:'8325113491300243'});
 context.batchInject=async(tab,phase)=>{
  if(phase==='prepare')return {result:{ok:true,prepared:true,submit_token:'nonce',surface}};
  if(phase==='submit'){submits++;return {result:{submitted:true}};}return {result:{ok:true}};
 };
 return {context,config,session,policy,storage,posts,get submits(){return submits;},run:()=>context.runBatchControl(config,session,policy)};
}
test('lost receipt acknowledgement preserves the exact receipt and never repeats a submit',async()=>{
 const f=fixture({lostReceipt:true});await f.run();assert.equal(f.submits,1);
 const first=f.posts.find(p=>p.name==='worker-receipt').body;assert.equal(first.submitted,true);assert.equal(first.observation.task_id,'8325113491300243');
 assert.deepEqual(JSON.parse(JSON.stringify(f.storage.qc_batch_receipt_v1)),first);assert.equal(f.posts.filter(p=>p.name==='worker-receipt').length,1);
 await f.context.batchFlushReceipt(f.config);const receipts=f.posts.filter(p=>p.name==='worker-receipt');assert.equal(receipts.length,2);assert.deepEqual(receipts[1].body,first);
 assert.equal(f.storage.qc_batch_receipt_v1,undefined);await f.run();assert.equal(f.submits,1);
});
test('switch revoked after begin cannot click and reports an uncertain lease without retry',async()=>{
 const f=fixture({revokeAtBegin:true});await f.run();assert.equal(f.submits,0);const receipt=f.posts.find(p=>p.name==='worker-receipt').body;
 assert.equal(receipt.submitted,true);assert.equal(receipt.observation,null);assert.equal(receipt.message,'batch_switch_changed');
});
