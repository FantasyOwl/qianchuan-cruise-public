'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const {Panel}=require('../static/official-workflow.js');
const aid='1999999999999002',pid='8218616704149013',mid='84300024958326955';
function fixture(){
 const doc=new JSDOM('<section id="official-workflow-panel"></section>').window.document,calls=[];
 const batch={key:'batch',date_start:'2026-10-24',date_end:'2026-10-28',material_ids:[mid],materials:[{id:mid,name:'<img src=x onerror=alert(1)>',metrics:{spend:200,roi:4},running_chase:false}],rule:{optimization_target:'直播间成交',daily_budget:200,bid_value:20.88},ready:true,rule_index:0};
 const workflow={account_id:aid,plan_id:pid,tasks:[],batches:[batch],materials:batch.materials,materials_read:true,material_total:500,pages_read:1,captured_at:'2026-10-28T12:00:00+08:00',notes:[]};
 const app={doc,dirty:false,account:()=>({id:aid,scene:'live'}),state:{monitoring:{accounts:[{account_id:aid,backend:'official_api',plans:[{plan_id:pid,entry_label:'乘方',name:'母计划'}]}]},api_workflows:{[aid+':'+pid]:workflow},api_actions:[]},$:s=>doc.querySelector(s),
  task:async(b,fn)=>{await fn();},refresh:async()=>panel.render(),note:()=>{},api:async(path,body)=>{calls.push({path,body});if(path.endsWith('/prepare')){const record={id:'draft',state:'draft',intent_digest:'exact',expires_at:'2026-10-28T12:15:00+08:00',intent:{account_id:aid,account_name:'户',plan_id:pid,delivery_entry:'chengfang',action:'material_chase',batch,notes:[]}};app.state.api_actions=[record];return {record};}if(path.endsWith('/confirm')){app.state.api_actions[0].state='verified';return {record:app.state.api_actions[0]};}return {ok:true};}};
 const panel=new Panel(app);panel.render();return {doc,app,panel,calls};
}
const settle=()=>new Promise(r=>setTimeout(r,5));

test('both unlimited choices require confirmation and send explicit flags with null limits',async()=>{
 const f=fixture(),d=f.doc,change=selector=>{const e=d.querySelector(selector);e.checked=true;e.dispatchEvent(new d.defaultView.Event('change',{bubbles:true}));};
 change('#api-auto-budget-unlimited');change('#api-auto-batches-unlimited');
 assert.equal(d.querySelector('#api-auto-cap').disabled,true);assert.equal(d.querySelector('#api-auto-batches').disabled,true);
 assert.equal(d.querySelector('#api-auto-enable').disabled,true);change('#api-auto-checked');
 assert.equal(d.querySelector('#api-auto-enable').disabled,false);f.panel.render();
 assert.equal(d.querySelector('#api-auto-budget-unlimited').checked,true);
 d.querySelector('#api-auto-enable').click();await settle();
 const body=f.calls[0].body;assert.equal(body.active_budget_unlimited,true);assert.equal(body.daily_batches_unlimited,true);
 assert.equal(body.active_budget_cap,null);assert.equal(body.max_new_batches_per_day,null);assert.equal(body.confirmed,true);
 assert.equal(f.calls.length,1);
});

test('saved unlimited authority renders without a null ceiling and clearing a choice resets confirmation',()=>{
 const f=fixture(),d=f.doc;f.app.state.api_auto=[{account_id:aid,plan_id:pid,enabled:true,budget_cap:null,batch_cap:null,budget_unlimited:true,batches_unlimited:true,day_batches_reserved:502}];
 f.panel.render();assert.match(d.body.textContent,/在投预算上限 无上限/);assert.match(d.body.textContent,/502 \/ 无上限/);assert.doesNotMatch(d.body.textContent,/null/);
 f.app.state.api_auto[0].enabled=false;f.panel.render();
 const c=d.querySelector('#api-auto-checked');c.checked=true;c.dispatchEvent(new d.defaultView.Event('change',{bubbles:true}));
 const unlimited=d.querySelector('#api-auto-budget-unlimited');unlimited.checked=false;unlimited.dispatchEvent(new d.defaultView.Event('change',{bubbles:true}));
 assert.equal(d.querySelector('#api-auto-checked').checked,false);assert.equal(d.querySelector('#api-auto-cap').disabled,false);assert.equal(d.querySelector('#api-auto-enable').disabled,true);
});

test('account selection defaults to its explicitly bound parent even when another plan is first',()=>{
 const f=fixture(),model=f.app.state.monitoring.accounts[0];
 model.plans.unshift({plan_id:'8200358026205459',entry_label:'乘方',name:'甄选号'});
 f.app.state.api_auto=[{account_id:aid,plan_id:pid,enabled:true}];f.panel.pid='';f.panel.render();
 assert.equal(f.doc.querySelector('#api-workflow-plan').value,pid);assert.match(f.doc.body.textContent,/已绑定主计划/);
 f.panel.pid='8200358026205459';f.panel.render();assert.match(f.doc.body.textContent,/切换查看不会更改自动追投绑定/);
 assert.equal(f.calls.length,0);
});

test('automatic chase summary shows the editable range and no 24-hour historical exclusion',()=>{
 const f=fixture(),html=f.panel.autoHTML({id:aid,scene:'live',profile:{features:{f5:{batch_size:5,batch_size_min:2,batch_size_max:5}},safety:{}}});
 assert.match(html,/每批 2–5 条/);assert.match(html,/达到最少数量即可组批/);
 assert.match(html,/已结束素材不因24小时内追投过而排除/);
});

test('browser pause and budget records render pending and verified details without a chase batch',()=>{
 const f=fixture();
 f.app.state.api_actions=[
  {id:'web-pause',state:'unknown',intent:{account_id:aid,plan_id:pid,task_id:'8201785420228590',execution_channel:'browser',operation:'browser_rule',action:'pause_task',before:true,after:false,notes:[]}},
  {id:'web-budget',state:'verified',intent:{account_id:aid,plan_id:pid,task_id:'8049430468207480',execution_channel:'browser',operation:'browser_rule',action:'task_budget',before:'500.00',after:'600.00',notes:[]}}
 ];
 assert.doesNotThrow(()=>f.panel.render());
 assert.match(f.doc.querySelector('.api-recovery').textContent,/Chrome网页补位 · 暂停任务 8201785420228590.*投放中 → 已暂停/s);
 assert.equal(f.doc.querySelector('#api-confirm-exact'),null);
 f.doc.querySelector('[data-api-review="web-budget"]').click();
 assert.match(f.doc.querySelector('#api-exact-review').textContent,/Chrome网页补位 · 增加任务预算 8049430468207480.*500.00 → 600.00 元/s);
 assert.equal(f.doc.querySelector('#api-confirm-exact'),null);assert.equal(f.calls.length,0);
});

test('browser rule duration and ROI use ordinary before and after values, and incomplete chase details do not crash',()=>{
 const {intentHTML}=require('../static/official-workflow.js');
 assert.match(intentHTML({execution_channel:'browser',operation:'browser_rule',action:'task_duration',before:2,after:3}),/2 → 3 小时/);
 assert.match(intentHTML({execution_channel:'browser',operation:'browser_rule',action:'plan_roi',before:3,after:3.1}),/3 → 3.1 倍/);
 assert.match(intentHTML({execution_channel:'browser',action:'material_chase'}),/缺少批次明细/);
 assert.match(intentHTML({execution_channel:'browser',action:'material_chase',browser_batch:{}}),/缺少批次明细/);
 const html=intentHTML({execution_channel:'browser',action:'material_chase',browser_batch:{after:{chase_type:'控成本追投',daily_budget:'200',optimization_target:'直播间成交',bid_value:'20.88'},material_ids:[mid],task_name:'QCX_<script>'}});
 assert.match(html,/整批预算 200 元/);assert.match(html,new RegExp(mid));assert.match(html,/QCX_&lt;script&gt;/);
});

function manyBatches(f,count=13){
 const w=f.app.state.api_workflows[aid+':'+pid],base=w.batches[0];
 w.batches=Array.from({length:count},(_,i)=>({...structuredClone(base),key:'batch-'+i,
  material_ids:[String(8206994744541607698n+BigInt(i))],materials:[{...structuredClone(base.materials[0]),id:String(8206994744541607698n+BigInt(i)),name:'测试素材 '+i}]}));
 f.panel.render();return w;
}

test('candidate batches are collapsed and paged while preparation still targets the exact visible batch',async()=>{
 const f=fixture();manyBatches(f);const d=f.doc;
 assert.equal(d.querySelector('[data-api-details=batches]').open,false);
 assert.equal(d.querySelectorAll('.api-batch-item').length,5);assert.equal(d.querySelectorAll('.api-batch-materials[open]').length,0);
 d.querySelector('[data-api-details=batches]').open=true;d.querySelector('[data-api-batch-page=next]').click();
 assert.equal(d.querySelector('[data-api-batch]').dataset.apiBatch,'batch-5');assert.match(d.querySelector('.api-batch-pager').textContent,/6–10 \/ 13/);
 d.querySelector('.api-batch-materials').open=true;f.panel.render();
 assert.equal(d.querySelector('[data-api-details=batches]').open,true);assert.equal(d.querySelector('.api-batch-materials').open,true);
 assert.equal(d.querySelector('[data-api-batch]').dataset.apiBatch,'batch-5');assert.equal(f.calls.length,0);
 d.querySelector('[data-api-batch]').click();await settle();
 assert.deepEqual(f.calls[0].body,{account_id:aid,plan_id:pid,action:'material_chase',batch_key:'batch-5'});
 assert.equal(f.calls.length,1);assert.equal(d.querySelector('#api-confirm-exact').disabled,true);
});

test('candidate search retains exact long IDs and typing focus and never calls a platform API',()=>{
 const f=fixture(),w=manyBatches(f),d=f.doc;d.querySelector('[data-api-details=batches]').open=true;
 let input=d.querySelector('#api-batch-query');const id=w.batches[12].material_ids[0];input.focus();input.value=id;input.setSelectionRange(4,8);
 input.dispatchEvent(new d.defaultView.Event('input',{bubbles:true}));
 assert.equal(d.querySelectorAll('.api-batch-item').length,1);assert.equal(d.querySelector('[data-api-batch]').dataset.apiBatch,'batch-12');
 f.panel.render();input=d.querySelector('#api-batch-query');assert.equal(input.value,id);assert.equal(d.activeElement,input);
 assert.equal(input.selectionStart,4);assert.equal(input.selectionEnd,8);assert.equal(f.calls.length,0);
});

test('candidate status filtering and page size remain local and shrinking results clamp the current page',()=>{
 const f=fixture(),w=manyBatches(f),d=f.doc;
 f.app.state.api_actions=[{id:'existing',state:'verified',intent:{account_id:aid,plan_id:pid,action:'material_chase',batch:w.batches[0]}}];w.batches[1].ready=false;f.panel.render();
 input(d,'#api-batch-filter','existing','change');assert.equal(d.querySelectorAll('.api-batch-item').length,1);assert.equal(d.querySelector('[data-api-batch]').disabled,true);
 input(d,'#api-batch-filter','ready','change');assert.match(d.querySelector('.api-batch-pager').textContent,/1–5 \/ 11/);
 input(d,'#api-batch-filter','all','change');input(d,'#api-batch-size','10','change');assert.equal(d.querySelectorAll('.api-batch-item').length,10);
 d.querySelector('[data-api-batch-page=next]').click();assert.equal(d.querySelectorAll('.api-batch-item').length,3);
 w.batches=w.batches.slice(0,3);f.panel.render();assert.match(d.querySelector('.api-batch-pager').textContent,/第 1 \/ 1 页/);
 assert.equal(f.calls.length,0);
});

test('candidate filters and pages belong to their account and plan',()=>{
 const f=fixture();manyBatches(f);input(f.doc,'#api-batch-query','测试素材 12');
 f.app.account=()=>({id:'other',scene:'live'});f.app.state.monitoring.accounts.push({account_id:'other',backend:'official_api',plans:[{plan_id:pid,name:'另一户计划'}]});
 f.app.state.api_workflows['other:'+pid]={...f.app.state.api_workflows[aid+':'+pid],account_id:'other'};f.panel.render();
 assert.equal(f.doc.querySelector('#api-batch-query').value,'');assert.equal(f.doc.querySelectorAll('.api-batch-item').length,5);
 f.app.account=()=>({id:aid,scene:'live'});f.panel.render();assert.equal(f.doc.querySelector('#api-batch-query').value,'测试素材 12');
 assert.equal(f.doc.querySelector('[data-api-batch]').dataset.apiBatch,'batch-12');assert.equal(f.calls.length,0);
});

function recoveryRecord(state='submitted_pending_verification'){
 return {id:'stuck-original',state,created_at:'2026-11-10T12:40:41+08:00',readback_available:state==='submitted_pending_verification',hold_available:['unknown','submitted_pending_verification','rejected','draft'].includes(state),failure_detected:true,
  intent:{account_id:aid,account_name:'当前户',plan_id:pid,action:'task_budget',task_id:'8907424333962232',before:600,after:800,notes:[]},
  result:{message:'本笔等待核验',reply:{results:[{error_message:'FAULT-2001 <img src=x onerror=alert(1)>'}]}}};
}
test('recovery is visible without opening records and names exact failed task and values',()=>{
 const {doc,app,panel}=fixture();app.state.api_actions=[recoveryRecord()];panel.render();
 const recovery=doc.querySelector('.api-recovery');assert.equal(recovery.closest('details'),null);
 assert.match(recovery.textContent,/8907424333962232/);assert.match(recovery.textContent,/600 → 800/);
 assert.match(recovery.textContent,/结束明确失败原单/);assert.match(recovery.textContent,/FAULT-2001/);
 assert.equal(doc.querySelector('img'),null);
 doc.querySelector('[data-api-review]').click();assert.ok(doc.querySelector('#api-exact-review [data-api-hold]'));
});
test('manual cancellation works during preflight and unsaved edits without joining the busy form',async()=>{
 const {doc,app,panel,calls}=fixture();app.state.api_actions=[recoveryRecord('checking')];app.dirty=true;app.busy=true;
 panel.pending={label:'正在核验'};panel.render();
 const button=doc.querySelector('.api-recovery [data-api-cancel]');assert.equal(button.disabled,false);
 app.api=async(path,body)=>{calls.push({path,body});app.state.api_actions[0].state='cancelled';return {ok:true};};
 button.click();await settle();
 assert.deepEqual(calls,[{path:'/api/official-api/actions/cancel',body:{id:'stuck-original'}}]);
 assert.match(doc.querySelector('.api-recovery').textContent,/未提交单已取消/);
 assert.equal(app.dirty,true);assert.notEqual(panel.pending,null);
});
test('unknown action is skipped locally then explicitly resumed without any confirmation or ad replay',async()=>{
 const {doc,app,panel,calls}=fixture();app.state.api_actions=[recoveryRecord('unknown')];panel.render();
 app.api=async(path,body)=>{calls.push({path,body});app.state.api_task_holds=path.endsWith('/hold_task')?[{account_id:aid,plan_id:pid,task_id:'8907424333962232',action_id:'stuck-original'}]:[];return {ok:true};};
 assert.equal(doc.querySelector('#api-confirm-exact'),null);
 doc.querySelector('.api-recovery [data-api-hold]').click();await settle();
 assert.equal(app.state.api_actions[0].state,'unknown');assert.ok(doc.querySelector('[data-api-resume]'));
 panel.render();assert.ok(doc.querySelector('[data-api-resume]'));
 doc.querySelector('[data-api-resume]').click();await settle();
 assert.deepEqual(calls,[{path:'/api/official-api/actions/hold_task',body:{id:'stuck-original',account_id:aid}},
  {path:'/api/official-api/actions/resume_task',body:{account_id:aid,plan_id:pid,task_id:'8907424333962232'}}]);
 assert.match(doc.querySelector('.api-recovery').textContent,/已恢复/);
});
test('recovery times out visibly and a late response never leaks into another account',async()=>{
 const {doc,app,panel,calls}=fixture();app.options={recoveryTimeoutMs:10};app.state.api_actions=[recoveryRecord()];panel.render();
 app.api=(path,body)=>{calls.push({path,body});return new Promise(()=>{});};
 doc.querySelector('.api-recovery [data-api-recheck]').click();await new Promise(r=>setTimeout(r,25));
 assert.match(doc.querySelector('.api-recovery [role=alert]').textContent,/等待超时/);
 assert.equal(doc.querySelector('.api-recovery [data-api-recheck]').disabled,false);
 app.account=()=>({id:'other',scene:'live'});app.state.monitoring.accounts.push({account_id:'other',backend:'official_api',plans:[]});panel.render();
 assert.doesNotMatch(doc.querySelector('.api-recovery').textContent,/stuck-original|等待超时|8907424333962232/);
 assert.equal(calls.length,1);
});
test('an orphaned pending record or task hold remains accessible without a current plan reading',()=>{
 const {doc,app,panel}=fixture();app.state.monitoring.accounts[0].plans=[];app.state.api_actions=[recoveryRecord()];panel.render();
 assert.equal(doc.querySelector('#api-workflow-plan').value,pid);assert.ok(doc.querySelector('[data-api-hold]'));
 app.state.api_actions=[];app.state.api_task_holds=[{account_id:aid,plan_id:pid,task_id:'8907424333962232',action_id:'old'}];panel.render();
 assert.equal(doc.querySelector('#api-workflow-plan').value,pid);assert.ok(doc.querySelector('[data-api-resume]'));
});
test('rate panel distinguishes platform limits from local deferrals without blocking local forms',()=>{
 const {doc,app,panel,calls}=fixture();app.state.api_rate_control={shared_read_write:true,requests_last_minute:12,requests_last_second:0,requests_last_24h:99,metadata_cache_hits:7,counters:{rate_limited:2,locally_deferred:10},cooldowns:[{endpoint:'<img src=x>',until:1793193000,reason:'rate_limited'}]};panel.render();
 const rate=doc.querySelector('[data-api-details=rate]');assert.match(rate.textContent,/近1分钟12次/);assert.match(rate.textContent,/平台限流累计 2 次/);assert.match(rate.textContent,/本地拦截 10 次/);assert.match(rate.textContent,/重启保留冷却/);assert.equal(doc.querySelector('img'),null);assert.equal(doc.querySelector('#api-manual-action').disabled,false);assert.equal(calls.length,0);
 rate.open=true;panel.render();assert.equal(doc.querySelector('[data-api-details=rate]').open,true);
});
test('API scan is read only and preserves exact account and parent IDs',async()=>{const {doc,calls}=fixture();doc.querySelector('#api-scan-materials').click();await settle();assert.deepEqual(calls,[{path:'/api/official-api/workflow',body:{account_id:aid,plan_id:pid,materials:true}}]);});
test('batch preparation never auto-confirms or accepts a client budget',async()=>{const {doc,panel,calls}=fixture();doc.querySelector('[data-api-batch]').click();await settle();assert.deepEqual(calls[0].body,{account_id:aid,plan_id:pid,action:'material_chase',batch_key:'batch'});assert.equal(calls.length,1);const details=doc.querySelector('[data-api-details=records]');details.open=true;panel.render();assert.equal(doc.querySelector('[data-api-details=records]').open,true);assert.equal(doc.querySelector('#api-confirm-exact').disabled,true);assert.match(doc.body.textContent,/整批日预算 200/);assert.match(doc.body.textContent,new RegExp(mid));assert.equal(doc.querySelector('img'),null);});
test('explicit checkbox submits only the reviewed immutable digest',async()=>{const {doc,panel,calls}=fixture();doc.querySelector('[data-api-batch]').click();await settle();const c=doc.querySelector('#api-confirm-checked');c.checked=true;c.dispatchEvent(new doc.defaultView.Event('change',{bubbles:true}));panel.render();assert.equal(doc.querySelector('#api-confirm-checked').checked,true);doc.querySelector('#api-confirm-exact').click();await settle();assert.deepEqual(calls[1],{path:'/api/official-api/actions/confirm',body:{id:'draft',intent_digest:'exact',confirmed:true}});assert.equal(doc.querySelector('#api-confirm-exact'),null);});
test('switching accounts drops an approval checkbox',async()=>{const {doc,app,panel}=fixture();doc.querySelector('[data-api-batch]').click();await settle();panel.checked=true;app.account=()=>({id:'other',scene:'live'});app.state.monitoring.accounts.push({account_id:'other',backend:'official_api',plans:[]});panel.render();assert.equal(panel.checked,false);assert.equal(panel.review,null);});

test('in-flight confirmation stays visibly busy across a status refresh',async()=>{
 const {doc,app,panel}=fixture();doc.querySelector('[data-api-batch]').click();await settle();
 let resolve;app.api=()=>new Promise(r=>{resolve=r;});
 const c=doc.querySelector('#api-confirm-checked');c.checked=true;c.dispatchEvent(new doc.defaultView.Event('change',{bubbles:true}));
 doc.querySelector('#api-confirm-exact').click();
 assert.match(doc.querySelector('[role=status]').textContent,/正在重新核验/);
 panel.render();assert.equal(doc.querySelector('#api-confirm-exact').disabled,true);
 assert.equal(doc.querySelector('#api-confirm-checked').disabled,true);
 app.state.api_actions[0].state='verified';resolve({record:app.state.api_actions[0]});await settle();
 assert.equal(panel.pending,null);assert.equal(doc.querySelector('#api-confirm-exact'),null);
});
test('confirmation failure is shown beside the card and clears the checkbox',async()=>{
 const {doc,app,panel,calls}=fixture();doc.querySelector('[data-api-batch]').click();await settle();
 app.task=async(b,fn)=>{try{await fn();}catch{}};
 app.api=async()=>{throw new Error('素材 '+mid+' 当前投放状态为 DISABLE <img>，未提交');};
 const c=doc.querySelector('#api-confirm-checked');c.checked=true;c.dispatchEvent(new doc.defaultView.Event('change',{bubbles:true}));
 doc.querySelector('#api-confirm-exact').click();await settle();
 assert.match(doc.querySelector('#api-exact-review [role=alert]').textContent,new RegExp(mid));
 panel.render();assert.equal(doc.querySelector('#api-confirm-checked').checked,false);
 assert.equal(doc.querySelector('#api-confirm-exact').disabled,true);assert.equal(doc.querySelector('img'),null);
 assert.equal(calls.length,1);
});

function input(doc,id,value,type='input'){const el=doc.querySelector(id);el.value=value;el.dispatchEvent(new doc.defaultView.Event(type,{bubbles:true}));}
test('manual main ROI stays in its account and survives polling without submitting',async()=>{
 const {doc,panel,calls}=fixture();input(doc,'#api-manual-action','plan_roi','change');input(doc,'#api-manual-value','3.3');
 const details=doc.querySelector('[data-api-details=manual]');details.open=true;doc.querySelector('#api-manual-value').focus();panel.render();
 assert.equal(doc.querySelector('#api-manual-value').value,'3.3');assert.equal(doc.querySelector('[data-api-details=manual]').open,true);
 assert.equal(doc.activeElement.id,'api-manual-value');assert.equal(calls.length,0);
 doc.querySelector('#api-prepare-manual').click();await settle();
 assert.deepEqual(calls[0].body,{account_id:aid,plan_id:pid,action:'plan_roi',value:'3.3'});
 assert.equal(calls.length,1);
});
test('API off-air form prepares exact Beijing time and never starts it automatically',async()=>{
 const {doc,calls}=fixture();input(doc,'#api-manual-action','slowdown_schedule','change');input(doc,'#api-manual-value','200');
 input(doc,'#api-manual-off','2026-10-29T01:00');input(doc,'#api-manual-minutes','60','change');
 doc.querySelector('#api-prepare-manual').click();await settle();
 assert.deepEqual(calls[0].body,{account_id:aid,plan_id:pid,action:'slowdown_schedule',value:'200',off_at:'2026-10-29T01:00:00+08:00',duration_minutes:60});assert.equal(calls.length,1);
});
test('manual preparation shows pending and errors beside its button and releases after a bounded timeout',async()=>{
 const {doc,app,panel}=fixture();app.options={prepareTimeoutMs:10};app.task=async(b,fn)=>{try{await fn();}catch{}};
 let resolve;app.api=()=>new Promise(r=>{resolve=r;});
 input(doc,'#api-manual-action','slowdown_schedule','change');input(doc,'#api-manual-value','200');input(doc,'#api-manual-off','2026-10-29T02:00');
 doc.querySelector('[data-api-details=manual]').open=true;doc.querySelector('#api-prepare-manual').click();
 assert.match(doc.querySelector('#api-manual-feedback [role=status]').textContent,/正在准备核对单/);
 assert.match(doc.querySelector('#api-prepare-manual').textContent,/正在准备/);panel.render();
 await new Promise(r=>setTimeout(r,25));
 assert.match(doc.querySelector('#api-manual-feedback [role=alert]').textContent,/等待超时/);
 assert.equal(doc.querySelector('#api-prepare-manual').disabled,false);assert.equal(panel.pending,null);
 assert.equal(doc.querySelector('#api-manual-value').value,'200');assert.equal(doc.querySelector('#api-manual-off').value,'2026-10-29T02:00');
 resolve({record:{id:'late'}});await settle();assert.equal(panel.review,null);
});
test('busy backend response appears beside the manual button without clearing its inputs',async()=>{
 const {doc,app,panel}=fixture();app.task=async(b,fn)=>{try{await fn();}catch{}};
 app.api=async()=>{throw new Error('其他投放动作正在完成状态核验，本次未创建新单');};
 input(doc,'#api-manual-value','200');doc.querySelector('#api-prepare-manual').click();await settle();
 assert.match(doc.querySelector('#api-manual-feedback [role=alert]').textContent,/本次未创建新单/);
 assert.equal(doc.querySelector('#api-manual-value').value,'200');assert.equal(doc.querySelector('#api-prepare-manual').disabled,false);
 assert.equal(panel.review,null);
});
test('confirmed off-air schedule is visible beside the form and opens original record without a new request',()=>{
 const {doc,app,panel,calls}=fixture();app.state.api_actions=[{id:'original-schedule',state:'scheduled',intent:{account_id:aid,plan_id:pid,action:'slowdown_schedule',notes:[],after:{off_at:'2026-10-29T02:00:00+08:00',trigger_at:'2026-10-29T01:40:00+08:00',stop_at:'2026-10-29T02:10:00+08:00',budget:'200',duration_minutes:30}}}];panel.render();
 assert.match(doc.querySelector('[data-api-details=manual]').textContent,/已有已确认排程/);
 doc.querySelector('[data-api-details=manual] [data-api-review]').click();
 assert.equal(panel.review,'original-schedule');assert.equal(doc.querySelector('#api-confirm-exact'),null);assert.equal(calls.length,0);
});
test('task duration input sends absolute hours for the exact native task',async()=>{
 const {doc,app,panel,calls}=fixture();app.state.api_workflows[aid+':'+pid].tasks=[{id:'10001',scene:'MATERIAL_ADD_BUDGET',budget:100,duration:2,metrics:{spend:50,roi:4}}];panel.render();
 input(doc,'#api-task-duration-10001','3.5');doc.querySelector('[data-api-task-duration]').click();await settle();
 assert.deepEqual(calls[0].body,{account_id:aid,plan_id:pid,action:'task_duration',task_id:'10001',value:'3.5'});
});
test('ROI batch displays the resolved native target as a ratio rather than a yuan bid',()=>{
 const {batchHTML}=require('../static/official-workflow.js');const b={date_start:'2026-10-24',date_end:'2026-10-28',material_ids:[mid],materials:[],rule:{chase_type:'控成本追投'},native_contract:{budget:'300',budget_kind:'daily',goal_label:'净成交ROI',value_label:'净成交ROI目标（倍）',value:'2.5',notes:[]}};
 const html=batchHTML(b);assert.match(html,/净成交ROI目标（倍） 2.5/);assert.doesNotMatch(html,/成交出价/);
});

test('full auto starts with blank limits and saving them does not authorize creation',async()=>{
 const {doc,calls}=fixture();assert.equal(doc.querySelector('#api-auto-cap').value,'');
 assert.equal(doc.querySelector('#api-auto-batches').value,'');assert.equal(doc.querySelector('#api-auto-enable').disabled,true);
 doc.querySelector('#api-auto-save').click();await settle();
 assert.deepEqual(calls,[{path:'/api/official-api/auto/configure',body:{account_id:aid,plan_id:pid,profile_revision:undefined,enabled:false,active_budget_cap:null,max_new_batches_per_day:null,active_budget_unlimited:false,daily_batches_unlimited:false,confirmed:false}}]);
});
test('full auto authority names volume budget and duration and is distinct from per-card confirmation',async()=>{
 const {doc,app,panel,calls}=fixture();app.account=()=>({id:aid,scene:'live',revision:'scope-r1',profile:{auto_interval_seconds:600,features:{f5:{batch_size:5,lookback_days:5,rules:[{chase_type:'放量追投',total_budget:300,control_duration:2,material_spend_min:100,material_roi_min:2.3}]}}}});panel.render();
 assert.match(doc.body.textContent,/整批总预算 300 元，2 小时/);assert.match(doc.body.textContent,/实际创建的任务ID/);
 input(doc,'#api-auto-cap','900');input(doc,'#api-auto-batches','3');
 const c=doc.querySelector('#api-auto-checked');c.checked=true;c.dispatchEvent(new doc.defaultView.Event('change',{bubbles:true}));
 assert.equal(doc.querySelector('#api-auto-enable').disabled,false);panel.render();assert.equal(doc.querySelector('#api-auto-checked').checked,true);
 doc.querySelector('#api-auto-enable').click();await settle();
 assert.deepEqual(calls,[{path:'/api/official-api/auto/configure',body:{account_id:aid,plan_id:pid,profile_revision:'scope-r1',enabled:true,active_budget_cap:'900',max_new_batches_per_day:'3',active_budget_unlimited:false,daily_batches_unlimited:false,confirmed:true}}]);
});
test('editing automatic limits or saved rule revision requires a fresh scope check',()=>{
 const {doc,app,panel}=fixture();input(doc,'#api-auto-cap','900');input(doc,'#api-auto-batches','3');
 let c=doc.querySelector('#api-auto-checked');c.checked=true;c.dispatchEvent(new doc.defaultView.Event('change',{bubbles:true}));
 input(doc,'#api-auto-cap','1200');assert.equal(c.checked,false);assert.equal(doc.querySelector('#api-auto-enable').disabled,true);
 c.checked=true;c.dispatchEvent(new doc.defaultView.Event('change',{bubbles:true}));app.account=()=>({id:aid,scene:'live',revision:'changed'});panel.render();assert.equal(doc.querySelector('#api-auto-checked').checked,false);
});
test('enabled full auto can be disabled without any per-card confirm or ad write request',async()=>{
 const {doc,app,panel,calls}=fixture();app.state.api_auto=[{account_id:aid,plan_id:pid,enabled:true,budget_cap:'900',batch_cap:3,day_batches_reserved:1,message:'等待下一轮'}];panel.render();
 assert.match(doc.body.textContent,/已开启，无需逐批确认/);assert.equal(doc.querySelector('#api-auto-enable'),null);
 doc.querySelector('#api-auto-disable').click();await settle();assert.deepEqual(calls,[{path:'/api/official-api/auto/disable',body:{account_id:aid}}]);
});
test('all six feature authority is explicit and never silently enables F5 monetary authority',async()=>{
 const {doc,app,panel,calls}=fixture();app.account=()=>({id:aid,scene:'live',revision:'scope-r2',profile:{features:{f1:{enabled:true,cost_metric:'control_cost'},f5:{enabled:false}},main_interval_seconds:300}});panel.render();
 assert.equal(doc.querySelector('#api-rule-auto-enable').disabled,true);
 const c=doc.querySelector('#api-rule-auto-checked');c.checked=true;c.dispatchEvent(new doc.defaultView.Event('change',{bubbles:true}));panel.render();
 assert.equal(doc.querySelector('#api-rule-auto-checked').checked,true);doc.querySelector('#api-rule-auto-enable').click();await settle();
 assert.deepEqual(calls,[{path:'/api/official-api/rule-auto/configure',body:{account_id:aid,plan_id:pid,profile_revision:'scope-r2',features:['f1','f2','f3','f4','f5','f6'],enabled:true,confirmed:true}}]);
 assert.equal(doc.querySelector('#api-auto-cap').value,'');
});
test('six-feature status shows missing data and closing it targets only this account',async()=>{
 const {doc,app,panel,calls}=fixture();app.state.api_rule_auto=[{account_id:aid,plan_id:pid,enabled:true,features:['f1','f2','f3','f4','f5','f6'],message:'规则已授权',diagnostics:[{feature_id:'f1',reason:'缺少明确调控综合成本<img src=x>'}]}];panel.render();
 assert.match(doc.body.textContent,/缺少明确调控综合成本<img src=x>/);assert.equal(doc.querySelector('img'),null);
 doc.querySelector('#api-rule-auto-disable').click();await settle();assert.deepEqual(calls,[{path:'/api/official-api/rule-auto/disable',body:{account_id:aid}}]);
});
test('automatically stopped authority shows the actual failure without opening a new authorization',()=>{
 const {doc,app,panel,calls}=fixture();app.state.api_rule_auto=[{account_id:aid,plan_id:pid,enabled:false,state:'blocked',message:'平台51010超时<img src=x>',diagnostics:[]}];panel.render();
 const area=doc.querySelector('[data-api-details=rule-auto]');assert.match(area.textContent,/已停止/);assert.match(area.textContent,/平台51010超时<img src=x>/);assert.equal(doc.querySelector('img'),null);assert.equal(calls.length,0);
});
test('timed-out pause exposes only independent readback rather than confirmation',async()=>{
 const {doc,app,panel,calls}=fixture();app.state.api_actions=[{id:'timeout-pause',state:'unknown',readback_available:true,intent:{account_id:aid,plan_id:pid,action:'pause_task',task_id:'10001'},result:{native_code:51010,message:'等待回读'}}];panel.render();
 doc.querySelector('[data-api-recheck]').click();await settle();assert.deepEqual(calls,[{path:'/api/official-api/actions/recheck',body:{id:'timeout-pause'}}]);assert.equal(doc.querySelector('#api-confirm-exact'),null);
});

test('timed-out budget action offers exact readback without a submit control',async()=>{
 const {doc,app,panel,calls}=fixture();
 app.state.api_actions=[{id:'timeout-budget',state:'unknown',readback_available:true,intent:{account_id:aid,plan_id:pid,action:'task_budget',task_id:'10001'},result:{native_code:51010,message:'等待回读'}}];
 panel.render();doc.querySelector('[data-api-recheck]').click();await settle();
 assert.deepEqual(calls,[{path:'/api/official-api/actions/recheck',body:{id:'timeout-budget'}}]);
 assert.equal(doc.querySelector('#api-confirm-exact'),null);
});
