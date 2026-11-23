'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const {BatchChasePanel}=require('../static/batch-chase.js');
function fixture(){
 const dom=new JSDOM('<section id="batch-chase-panel"></section>'),calls=[];
 const ids=['8204031517311756221','8103844353694828102','8422644343410380385'];
 const intent={account_id:'10001',plan_id:'20001',date_start:'2026-10-22',date_end:'2026-10-26',material_ids:ids,
  task_name:'QC_20261026_test',after:{chase_type:'控成本追投',daily_budget:200,optimization_target:'直播间成交',bid_value:36.88}};
 const app={doc:dom.window.document,accountId:'10001',feature:'f5',dirty:false,account(){return {id:this.accountId,scene:'live'};},
  state:{batch_control:{previews:[{...intent,scanned:100,page:1,batch_size:3,worker_id:'worker',epoch:'epoch',armed:true,notes:[],batches:[{...intent,key:'key-one',materials:ids.map(id=>({id,name:'素材',spend:200,roi:4}))}]}],actions:[]}},
  note(){},task(_b,fn){return fn();},async api(path,body){calls.push({path,body});if(path.endsWith('/create')){const d={id:'draft',state:'draft',intent_digest:'digest',intent};this.state.batch_control.actions=[d];return d;}return {ok:true};},async refresh(){panel.render();}};
 const panel=new BatchChasePanel(app);panel.render();return {app,panel,dom,calls,intent};
}
const settle=async()=>{for(let n=0;n<5;n++)await new Promise(r=>setImmediate(r));};
test('preparation contains fixed source candidate and never implicitly approves',async()=>{
 const f=fixture();f.dom.window.document.querySelector('[data-batch-prepare]').click();await settle();
 assert.equal(f.calls.length,1);assert.equal(f.calls[0].path,'/api/cruise/batch/create');
 assert.equal(f.calls[0].body.account_id,'10001');assert.equal(f.dom.window.document.querySelector('#batch-approve').disabled,true);
 for(const mid of f.intent.material_ids)assert.ok(f.dom.window.document.body.textContent.includes(mid));
 assert.match(f.dom.window.document.body.textContent,/整批日预算 200 元/);
});
test('explicit checkbox approves only the exact reviewed batch digest',async()=>{
 const f=fixture();f.dom.window.document.querySelector('[data-batch-prepare]').click();await settle();
 const checkbox=f.dom.window.document.querySelector('#batch-confirm-check');checkbox.checked=true;checkbox.dispatchEvent(new f.dom.window.Event('change',{bubbles:true}));
 f.dom.window.document.querySelector('#batch-approve').click();await settle();
 assert.deepEqual(f.calls.at(-1),{path:'/api/cruise/batch/approve',body:{id:'draft',intent_digest:'digest',confirmed:true}});
});
test('polling retains a checked review, while account switch clears it',async()=>{
 const f=fixture();f.dom.window.document.querySelector('[data-batch-prepare]').click();await settle();
 f.panel.checked=true;f.panel.render();assert.equal(f.dom.window.document.querySelector('#batch-confirm-check').checked,true);
 f.app.accountId='10002';f.panel.render();assert.equal(f.panel.checked,false);assert.equal(f.panel.reviewId,null);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/approve')).length,0);
});
test('unknown creation is displayed as unresolved without a retry button',()=>{
 const f=fixture();f.app.state.batch_control.actions=[{id:'unknown-one',state:'unknown',intent:f.intent}];f.panel.render();
 assert.match(f.dom.window.document.body.textContent,/创建结果尚未核验/);assert.match(f.dom.window.document.body.textContent,/不会重试/);
 assert.equal(f.dom.window.document.querySelector('[data-batch-review]'),null);
});

test('current account read failure is shown and clears after a newer scan',()=>{
 const f=fixture();f.app.state.batch_control.previews=[];
 f.app.account=()=>({id:f.app.accountId,scene:'live',lanes:{auto:{last_at:'2026-10-26T16:00:00+08:00'}}});
 f.app.state.logs=[{account_id:'10002',event:'collection_navigation',at:'2026-10-26T16:02:00+08:00',detail:{lane:'auto',ok:false,message:'其他户错误'}},
  {account_id:'10001',event:'collection_navigation',at:'2026-10-26T16:01:00+08:00',detail:{lane:'auto',ok:false,message:'当前页面有编辑窗口，未刷新以保留输入'}}];
 f.panel.render();assert.match(f.dom.window.document.body.textContent,/本次素材读取未完成：当前页面有编辑窗口/);
 assert.doesNotMatch(f.dom.window.document.body.textContent,/其他户错误/);
 f.app.account=()=>({id:f.app.accountId,scene:'live',lanes:{auto:{last_at:'2026-10-26T16:03:00+08:00'}}});
 f.panel.render();assert.doesNotMatch(f.dom.window.document.body.textContent,/本次素材读取未完成/);
 assert.equal(f.calls.length,0);
});

test('an expired review remains visible and renewal always requires a new unchecked confirmation',async()=>{
 const f=fixture();f.app.state.batch_control.actions=[{id:'expired',state:'expired',expires_at:'2026-10-01T00:00:00Z',intent:f.intent}];
 f.panel.reviewId='expired';f.panel.checked=true;f.panel.render();
 assert.match(f.dom.window.document.body.textContent,/这批确认单已过期/);
 assert.equal(f.panel.checked,false);assert.equal(f.dom.window.document.querySelector('#batch-approve'),null);
 for(const id of f.intent.material_ids)assert.ok(f.dom.window.document.body.textContent.includes(id));
 f.app.api=async(path,body)=>{f.calls.push({path,body});const d={id:'renewed',state:'draft',expires_at:new Date(Date.now()+900000).toISOString(),intent_digest:'new-digest',intent:f.intent};f.app.state.batch_control.actions.push(d);return d;};
 f.dom.window.document.querySelector('[data-batch-renew]').click();await settle();
 assert.deepEqual(f.calls,[{path:'/api/cruise/batch/renew',body:{id:'expired'}}]);
 assert.equal(f.panel.reviewId,'renewed');assert.equal(f.dom.window.document.querySelector('#batch-confirm-check').checked,false);
 assert.equal(f.dom.window.document.querySelector('#batch-approve').disabled,true);
 assert.match(f.dom.window.document.body.textContent,/审核截止/);
});

test('a review that expires between polls loses its approval controls without losing its details',()=>{
 const f=fixture();f.app.state.batch_control.actions=[{id:'timed-out',state:'draft',expires_at:new Date(Date.now()-1000).toISOString(),intent:f.intent}];
 f.panel.reviewId='timed-out';f.panel.checked=true;f.panel.render();
 assert.match(f.dom.window.document.body.textContent,/这批确认单已过期/);
 assert.equal(f.dom.window.document.querySelector('#batch-approve'),null);
 assert.ok(f.dom.window.document.querySelector('[data-batch-renew]'));
 assert.equal(f.calls.length,0);
});

test('running material permission is visible in the exact batch review with native status',async()=>{
 const f=fixture();f.intent.allow_running_materials=true;
 const candidate=f.app.state.batch_control.previews[0].batches[0];candidate.allow_running_materials=true;candidate.materials[0].chase_status='调控中';
 f.app.account=()=>({id:f.app.accountId,scene:'live',profile:{features:{f5:{allow_running_materials:true}}}});
 f.panel.render();assert.match(f.dom.window.document.body.textContent,/状态调控中/);
 f.dom.window.document.querySelector('[data-batch-prepare]').click();await settle();
 const review=f.dom.window.document.querySelector('[aria-label="核对这一批追投"]');
 assert.match(review.textContent,/本批选择：允许调控中素材再次追投/);
 assert.equal(f.dom.window.document.querySelector('#batch-confirm-check').checked,false);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/approve')).length,0);
});
