'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const {Panel}=require('../static/hybrid-chase.js');

test('API control strategy removes browser rule fallback and explains official task readback',async()=>{
 const f=fixture();f.app.state.execution_strategy={mode:'api_control_browser_chase',version:1};
 f.app.state.hybrid_chase[0].mode='api_data_browser';f.panel.render();
 assert.equal(f.$('#hybrid-rules-preview'),null);assert.equal(f.$('#hybrid-rules-retry'),null);
 assert.deepEqual([...f.$('#hybrid-mode').options].map(o=>o.value),['api_only','api_data_browser']);
 assert.match(f.$('#hybrid-chase-panel').textContent,/Ego只填表创建追投/);assert.deepEqual(f.calls,[]);
});
const aid='1999999999999002';

test('API waiting shows current progress instead of a historical prepare failure',()=>{
 const f=fixture();
 Object.assign(f.app.state.hybrid_chase[0],{run_id:'active',state:'waiting',phase:'api_inventory',message:'API任务读取等待：计划列表40100冷却',last_error:{phase:'prepare',title:'本批API素材数据已过期',raw_message:'旧准备失败'}});
 f.app.state.ego_executor={provider:'ego',enabled:true,armed:true,mode:'account_parallel',max_parallel:3,account_workers:[{account_id:aid,name:'示例分组A',current:true,error:null,message:'等待API',space_id:13}]};
 f.panel.render();const row=f.$('#ego-account-workers tbody tr');
 assert.doesNotMatch(row.children[1].textContent,/执行失败/);
 assert.match(row.children[2].textContent,/F5：API任务读取等待：计划列表40100冷却/);
 assert.match(row.querySelector('details').textContent,/旧准备失败/);
 assert.deepEqual(f.calls,[]);
});

test('a connected heartbeat cannot hide a failed stage and raw timeout URLs stay in technical details',()=>{
 const f=fixture(),raw='page.waitForFunction timed out after 10000ms on page p1; last URL was "https://qianchuan.jinritemai.com/overall-prom?aavid='+aid+'"';
 f.app.state.ego_executor={provider:'ego',enabled:true,armed:true,mode:'account_parallel',max_parallel:3,account_workers:[{account_id:aid,current:true,running:false,busy:false,message:raw,error:raw}]};
 f.panel.render();const row=f.$('#ego-account-workers tbody tr');assert.match(row.cells[1].textContent,/上次执行失败，等待重试/);assert.match(row.cells[1].textContent,/心跳已连接/);
 assert.match(row.cells[2].textContent,/千川页面加载超时/);assert.match(row.querySelector('details').textContent,/page.waitForFunction/);
 assert.equal(row.cells[2].firstChild.textContent.includes('https://'),false);assert.equal(row.cells[1].textContent.includes('已连接，等待任务'),false);
});

test('a successful rule read cannot hide the active F5 failure on the same account',()=>{
 const f=fixture();f.app.state.hybrid_chase=[{account_id:aid,run_id:'active-f5',state:'waiting',phase:'tasks',message:'等待重试',last_error:{title:'首页母计划行未唯一对应',raw_message:'首页母计划行未唯一对应（可见行0）'}}];
 f.app.state.ego_executor={provider:'ego',enabled:true,armed:true,mode:'account_parallel',max_parallel:3,account_workers:[{account_id:aid,current:true,running:false,busy:false,message:'Ego 常驻阶段完成',error:null}]};
 f.panel.render();const row=f.$('#ego-account-workers tbody tr');assert.match(row.cells[1].textContent,/上次执行失败/);assert.match(row.cells[2].textContent,/F5：等待首页/);
});

test('parallel account controls act on their own account and escape errors',async()=>{
 const f=fixture();f.app.state.ego_executor={provider:'ego',enabled:true,armed:true,installed:true,running:true,mode:'account_parallel',max_parallel:3,account_workers:[
 {account_id:aid,name:'示例分组A',current:true,running:true,message:'读取任务'},
 {account_id:'8531163845726695',name:'示例分组B',suspended:true,message:'<img src=x>接管',error:'<img src=x>错误'}]};f.panel.render();
 assert.equal(f.$('#ego-account-workers img'),null);assert.match(f.$('#ego-account-workers').textContent,/等待恢复浏览器控制/);
 f.$('[data-ego-account="8531163845726695"][data-operation="resume"]').click();await settle();
 assert.deepEqual(f.calls,[{path:'/api/ego-executor/account',payload:{account_id:'8531163845726695',operation:'resume'}}]);
 assert.equal(f.$('#ego-arm').textContent,'关闭网页执行');
});

test('pool settings survive polling without corrupting the selected chase channel',async()=>{
 const f=fixture();f.app.state.ego_executor={provider:'ego',installed:true,mode:'serial',max_parallel:3};f.panel.render();
 f.$('#hybrid-mode').value='api_data_browser';f.$('#hybrid-mode').dispatchEvent(new f.dom.window.Event('change',{bubbles:true}));
 f.$('#ego-pool-mode').value='account_parallel';f.$('#ego-pool-mode').dispatchEvent(new f.dom.window.Event('change',{bubbles:true}));
 f.$('#ego-pool-limit').value='2';f.$('#ego-pool-limit').dispatchEvent(new f.dom.window.Event('change',{bubbles:true}));
 f.panel.render();assert.equal(f.$('#ego-pool-limit').value,'2');assert.equal(f.$('#hybrid-mode').value,'api_data_browser');
 f.$('#ego-pool-save').click();await settle();assert.deepEqual(f.calls,[{path:'/api/ego-executor/pool',payload:{mode:'account_parallel',max_parallel:2}}]);
 assert.equal(f.app.state.api_auto,undefined);
});

test('restart is an explicit single-account operation without changing any grant',async()=>{
 const f=fixture();f.app.state.ego_executor={provider:'ego',enabled:true,armed:true,installed:true,mode:'account_parallel',max_parallel:3,account_workers:[{account_id:aid,name:'本户',current:true}]};f.panel.render();
 f.$(`[data-ego-account="${aid}"][data-operation="restart"]`).click();await settle();
 assert.deepEqual(f.calls,[{path:'/api/ego-executor/account',payload:{account_id:aid,operation:'restart'}}]);
});

test('a user-controlled browser is shown as paused with the explicit recovery control',()=>{
 const f=fixture();f.app.state.ego_executor={provider:'ego',enabled:true,armed:true,installed:true,running:true,suspended:true,message:'Control of this task space is with the user'};f.panel.render();
 assert.match(f.$('.hybrid-progress').textContent,/网页执行已暂停/);assert.match(f.$('.hybrid-progress').textContent,/所有网页调控暂停/);assert.equal(f.$('#ego-resume').disabled,false);assert.equal(f.calls.length,0);
});
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));};
function fixture(){
 const dom=new JSDOM('<section id="hybrid-chase-panel"></section>'),calls=[];
 const app={accountId:aid,account:()=>({id:app.accountId,scene:'live'}),state:{hybrid_chase:[{account_id:aid,mode:'api_only',message:'等待',pages_read:0,materials_read:0,batches_ready:0,batches_completed:0}]},
  $:s=>dom.window.document.querySelector(s),api:async(path,payload)=>{calls.push({path,payload});return {ok:true};},refresh:async()=>panel.render()};
 const panel=new Panel(app);panel.render();return {app,panel,calls,dom,$:app.$};
}

test('Ego resident controls preserve off state, have a separate local authority, and escape errors',async()=>{
 const f=fixture();f.app.state.ego_executor={provider:'ego',enabled:true,armed:false,installed:true,running:true,suspended:true,message:'待恢复',error:'<img src=x onerror=alert(1)>'};f.panel.render();
 assert.match(f.$('#ego-arm').textContent,/开启网页执行/);assert.equal(f.$('.hybrid-progress img'),null);
 f.$('#ego-resume').click();await settle();assert.deepEqual(f.calls[0],{path:'/api/ego-executor/resume',payload:{}});
 f.$('#ego-arm').click();await settle();assert.deepEqual(f.calls[1],{path:'/api/ego-executor/configure',payload:{provider:'ego',enabled:true,armed:true}});
 assert.equal(f.app.state.api_auto,undefined);
});

test('account scheduling queue shows each account and its own reason without changing caps',()=>{
 const f=fixture();f.app.state.accounts=[{id:aid,name:'示例分组A'},{id:'cotton',name:'示例分组C'}];
 f.app.state.ego_executor={provider:'ego',enabled:true,armed:true,installed:true,running:true,scheduler:{queue:[
  {account_id:aid,kind:'chase',phase:'prepare',ready:true,reason:''},{account_id:'cotton',kind:'rules',phase:'prepare',ready:false,reason:'<img src=x>等待原单'}]}};
 f.panel.render();assert.match(f.$('#ego-queue').textContent,/2户/);assert.match(f.$('#ego-queue').textContent,/示例分组C/);
 assert.match(f.$('#ego-queue').textContent,/等待原单/);assert.equal(f.$('#ego-queue img'),null);assert.equal(f.calls.length,0);
});
test('isolated original receipts expose held budget and escaped IDs without stopping controls or granting new quota',()=>{
 const f=fixture();f.app.state.hybrid_chase[0].isolated_readbacks=[{action_id:'original',reserved_budget:'300',material_ids:['8449335665835544833','<img src=x>']}];f.panel.render();
 assert.match(f.$('.hybrid-isolated').textContent,/保留预算 300.00 元/);assert.match(f.$('.hybrid-isolated').textContent,/其他独立调控/);assert.equal(f.$('.hybrid-isolated img'),null);assert.equal(f.calls.length,0);
});
test('temporary recovery deferrals are visible, escaped and cleared per account without touching permanent exclusions',async()=>{
 const f=fixture();f.app.state.api_material_deferrals=[{account_id:aid,material_ids:['8449335665835544833'],reason:'<img src=x onerror=alert(1)>',until_at:'2026-11-11T22:00:00+08:00'}];f.panel.render();
 assert.match(f.$('.hybrid-deferrals').textContent,/8449335665835544833/);assert.equal(f.$('.hybrid-deferrals img'),null);
 f.$('#hybrid-deferrals-clear').click();await settle();assert.deepEqual(f.calls,[{path:'/api/official-api/material-deferrals/clear',payload:{account_id:aid}}]);
 assert.equal(f.app.state.api_material_exclusions,undefined);
});
test('rule fallback controls use their own endpoint with no budget or grant changes',async()=>{
 const f=fixture();f.app.state.hybrid_rules=[{account_id:aid,id:'run',state:'waiting',phase:'scan',message:'等待任务表',tasks_read:22,pages_read:1,completed:0,diagnostics:[{feature_id:'f6',reason:'缺少近15/30分钟窗口'}]}];f.panel.render();
 assert.match(f.$('.hybrid-rule-progress').textContent,/22 条任务/);assert.match(f.$('.hybrid-rule-progress').textContent,/缺少近15\/30分钟窗口/);
 f.$('#hybrid-rules-retry').click();await settle();assert.deepEqual(f.calls,[{path:'/api/official-api/hybrid-rules/retry',payload:{account_id:aid}}]);
 f.$('#hybrid-rules-preview').click();await settle();assert.deepEqual(f.calls[1],{path:'/api/official-api/hybrid-rules/preview',payload:{account_id:aid}});
});
test('transport selection survives polling and saves only its account without enabling monetary authority',async()=>{
 const f=fixture(),input=f.$('#hybrid-mode');input.value='api_preferred';input.dispatchEvent(new f.dom.window.Event('change',{bubbles:true}));
 f.panel.render();assert.equal(f.$('#hybrid-mode').value,'api_preferred');f.$('#hybrid-save').click();await settle();
 assert.deepEqual(f.calls,[{path:'/api/official-api/hybrid/configure',payload:{account_id:aid,mode:'api_preferred'}}]);
});

test('API-data browser mode is independently selectable and explains the metric source and 1.10 adapter requirement',async()=>{
 const f=fixture(),input=f.$('#hybrid-mode');input.value='api_data_browser';input.dispatchEvent(new f.dom.window.Event('change',{bubbles:true}));
 f.$('#hybrid-save').click();await settle();
 assert.deepEqual(f.calls[0],{path:'/api/official-api/hybrid/configure',payload:{account_id:aid,mode:'api_data_browser'}});
 Object.assign(f.app.state.hybrid_chase[0],{mode:'api_data_browser',material_source:'official_api',pages_read:3,materials_read:249,eligible_range_complete:true,
   bridge:{current:true,version:'1.9.2',armed:true}});
 f.app.state.api_auto=[{account_id:aid,enabled:true,budget_cap:'50000',batch_cap:100}];f.panel.render();
 assert.match(f.panel.root.textContent,/官方API · 已读 3 页 \/ 249 条/);
 assert.match(f.panel.root.textContent,/不切回网页全量扫描/);assert.match(f.panel.root.textContent,/1.10.0/);
 assert.equal(f.$('#hybrid-start').disabled,true);
 f.app.state.hybrid_chase[0].bridge.version='1.10.0';f.panel.render();assert.equal(f.$('#hybrid-start').disabled,false);
});
test('read-only preview cannot post an ad confirmation or a client supplied budget',async()=>{
 const f=fixture();f.$('#hybrid-preview').click();await settle();
 assert.deepEqual(f.calls,[{path:'/api/official-api/hybrid/preview',payload:{account_id:aid}}]);
 assert.match(f.$('[role="status"]').textContent,/不会提交广告/);
});

test('rechecking an existing task shows a read-only result instead of zero creations disguised as a chase run',()=>{
 const f=fixture();Object.assign(f.app.state.hybrid_chase[0],{run_id:'readback',readback_only:true,state:'completed',plan_id:'8582197091416415',
  readback_result:{task_id:'8422872052435758',independently_verified:true,ad_changes:0}});f.panel.render();
 assert.match(f.$('.hybrid-progress').textContent,/原任务只读复核/);assert.match(f.$('.hybrid-progress').textContent,/8422872052435758/);
 assert.match(f.$('.hybrid-progress').textContent,/本轮创建追投 0 条/);assert.deepEqual(f.calls,[]);
});
test('switching accounts drops the previous account transport draft and escapes runtime messages',()=>{
 const f=fixture();f.$('#hybrid-mode').value='browser_only';f.panel.render();
 f.app.accountId='999';f.app.state.hybrid_chase.push({account_id:'999',mode:'api_only',message:'<img src=x onerror=bad()>'});f.panel.render();
 assert.equal(f.$('#hybrid-mode').value,'api_only');assert.equal(f.$('img'),null);
});

test('live worker progress replaces the initial request receipt instead of hiding a failure',async()=>{
 const f=fixture();f.app.state.hybrid_chase[0].updated_at='2026-11-11T00:10:00+08:00';f.panel.render();
 f.$('#hybrid-preview').click();await settle();assert.match(f.$('[role=status]').textContent,/已安排/);
 Object.assign(f.app.state.hybrid_chase[0],{run_id:'run',plan_id:'8582197091416415',state:'waiting',phase:'tasks',phase_label:'核对在投任务及预算',preview:true,
  updated_at:'2026-11-11T00:11:00+08:00',message:'Chrome未允许扩展读取当前投放标签',tasks_read:0,task_total:null,current_page:1,retry_available:true,
  retry_at:'2026-11-11T00:16:00+08:00',last_error:{phase_label:'核对在投任务及预算',title:'Chrome未允许读取',hint:'核对网站访问权限',attempt:1,raw_message:'denied <img src=x>'}});
 f.panel.render();assert.match(f.$('[role=status]').textContent,/Chrome未允许/);
 assert.match(f.$('[role=alert]').textContent,/网站访问权限/);assert.match(f.$('[role=alert]').textContent,/00:16:00/);
 assert.equal(f.$('img'),null);assert.equal(f.$('#hybrid-retry').disabled,false);
 f.$('#hybrid-retry').click();await settle();assert.deepEqual(f.calls.at(-1),{path:'/api/official-api/hybrid/retry',payload:{account_id:aid}});
 assert.equal(f.calls.some(c=>/confirm|begin|final/.test(c.path)),false);
});

test('completed preview is distinguished from real creations and keeps the original scope',()=>{
 const f=fixture();Object.assign(f.app.state.hybrid_chase[0],{run_id:'run',plan_id:'8582197091416415',state:'completed',phase_label:'预演并取消',preview:true,
  pages_read:4,materials_read:350,batches_ready:3,batches_completed:0,tasks_read:22,task_total:22});f.panel.render();
 assert.match(f.$('.hybrid-progress').textContent,/只读预演/);assert.match(f.$('.hybrid-progress').textContent,/4 页 \/ 350 条/);
 assert.match(f.$('.hybrid-progress').textContent,/本轮没有提交广告/);assert.match(f.$('.hybrid-progress').textContent,/已提交并核验 0 批/);
 assert.equal(f.$('#hybrid-retry').disabled,true);assert.equal(f.calls.length,0);
});

test('immediate real execution requires saved browser mode, old F5 authority and an armed bridge',async()=>{
 const f=fixture();assert.equal(f.$('#hybrid-start').disabled,true);
 f.app.state.api_auto=[{account_id:aid,enabled:true,budget_cap:'50000',batch_cap:100}];
 Object.assign(f.app.state.hybrid_chase[0],{mode:'api_preferred',bridge:{current:true,version:'1.8.0',armed:false}});f.panel.render();
 assert.equal(f.$('#hybrid-start').disabled,true);
 f.app.state.hybrid_chase[0].bridge.armed=true;f.panel.render();assert.equal(f.$('#hybrid-start').disabled,false);
 f.$('#hybrid-start').click();await settle();assert.deepEqual(f.calls,[{path:'/api/official-api/hybrid/start',payload:{account_id:aid}}]);
 assert.match(f.$('[role=status]').textContent,/本轮真实追投/);assert.equal(f.app.state.api_auto[0].budget_cap,'50000');
 Object.assign(f.app.state.hybrid_chase[0],{run_id:'preview',state:'working',preview:true});f.panel.render();
 assert.equal(f.$('#hybrid-start').disabled,true);
});

test('exclusion textarea preserves exact long IDs, focus and draft across polls and isolates accounts',async()=>{
 const f=fixture(),id='8206994744541607698',second='8350231211045693350';
 let input=f.$('#hybrid-excluded-ids');input.value=id+'，'+second+'\n'+id;
 input.dispatchEvent(new f.dom.window.Event('input',{bubbles:true}));input.focus();input.setSelectionRange(5,8);
 f.panel.render();input=f.$('#hybrid-excluded-ids');assert.equal(input.value,id+'，'+second+'\n'+id);
 assert.equal(f.dom.window.document.activeElement,input);assert.equal(input.selectionStart,5);assert.equal(input.selectionEnd,8);
 f.app.accountId='999';f.panel.render();assert.equal(f.$('#hybrid-excluded-ids').value,'');
 f.app.accountId=aid;f.panel.render();f.$('#hybrid-exclusions-save').click();await settle();
 assert.deepEqual(f.calls,[{path:'/api/official-api/material-exclusions',payload:{account_id:aid,material_ids:[id,second]}}]);
 assert.equal(f.$('#hybrid-excluded-ids').value,id+'\n'+second);assert.match(f.$('[role=status]').textContent,/已保存/);
 input=f.$('#hybrid-excluded-ids');input.value='';input.dispatchEvent(new f.dom.window.Event('input',{bubbles:true}));f.$('#hybrid-exclusions-save').click();await settle();
 assert.deepEqual(f.calls.at(-1).payload,{account_id:aid,material_ids:[]});
});

test('one-task test works with saved caps while continuous auto is disabled and sends no money override',async()=>{
 const f=fixture();f.app.state.api_auto=[{account_id:aid,enabled:false,budget_cap:'50000',batch_cap:100,execution_mode:'single_test'}];
 Object.assign(f.app.state.hybrid_chase[0],{mode:'api_preferred',bridge:{current:true,version:'1.8.0',armed:true}});f.panel.render();
 assert.equal(f.$('#hybrid-start-test').disabled,false);assert.equal(f.$('#hybrid-start').disabled,true);
 f.$('#hybrid-start-test').click();await settle();
 assert.deepEqual(f.calls,[{path:'/api/official-api/hybrid/start-test',payload:{account_id:aid}}]);
 assert.match(f.$('[role=status]').textContent,/只创建一条任务/);
});
