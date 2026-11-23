'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const {CruiseConsole,surfaceChoices,guaranteeHTML}=require('../static/cruise.js');
const live='1999999999999002',product='1999999999999001',plan='8218616704149013',task='8618530283142212632';
const copy=value=>JSON.parse(JSON.stringify(value));
const field=(key,label,defaultValue=null)=>({key,label,default:defaultValue,type:'number',options:[]});
const catalog=[{id:'f1',name:'低支付ROI暂停',description:'任务规则',fields:[field('min_control_cost','成本下限',50),field('max_roi','ROI上限')]},{id:'f2',name:'控成本增加',description:'绝对预算',fields:[],row_fields:[field('min_control_cost','成本下限'),field('target_control_budget','目标调控预算')]},{id:'f5',name:'自动化追投',description:'候选',fields:[],row_fields:[field('material_roi_min','素材ROI')]},{id:'f6',name:'ROI目标调整',description:'诊断',fields:[field('min_overall_roi','综合ROI')]}];
function profile(){return {version:1,enabled:false,main_interval_seconds:300,auto_interval_seconds:600,operation_start:'07:00',operation_end:'01:00',snapshot_max_age_seconds:180,broadcast_protection:{enabled:false,start:'01:00',end:'07:00'},safety:{max_actions_per_round:20,max_actions_per_day:500,high_spend_no_orders:500,duplicate_guard_minutes:1440},features:{f1:{enabled:false,min_control_cost:50,max_roi:null},f2:{enabled:false,rules:[]},f5:{enabled:false,rules:[]},f6:{enabled:false,min_overall_roi:null}}};}
function surface(account=live){return {account_id:account,plan_id:account===live?plan:'8937389604663702250',name:account===live?'直播计划':'商品图文计划',budget:'1500.00',target_roi:'3.60',roi_basis:'net',running:true,captured_at:'2026-10-25T14:00:00Z',actions:['budget_up','pause_plan','roi_target_up']};}
function state(){return {ok:true,csrf:'local-csrf',accounts:[{id:live,name:'直播户',scene:'live',profile:profile(),bindings:{},revision:'live-rev',target_roi:null,lanes:{main:{running:false},auto:{running:false}}},{id:product,name:'图文户',scene:'product',profile:profile(),bindings:{},revision:'product-rev',target_roi:null,lanes:{main:{running:false},auto:{running:false}}}],feature_catalog:catalog,logs:[],browser_control:{workers:[{worker_id:'worker',epoch:'epoch',armed:true,current:true,surfaces:[surface(),surface(product)]}],actions:[],execution_available:true},task_control:{workers:[],actions:[],execution_available:false}};}
function actionRecord(queue='main'){return {id:'action-id',state:'draft',intent_digest:'exact-digest',created_at:'2026-10-25T14:00:00Z',expires_at:'2026-10-25T14:02:00Z',intent:{account_id:live,plan_id:plan,...(queue==='task'?{task_id:task,task_name:'追投任务',field:'budget'}:{plan_name:'直播计划',field:'budget'}),action:queue==='task'?'control_budget_up':'budget_up',before:'1500.00',after:'1800.00',reason:'稳定后逐步调整'}};}
function fixture(){const dom=new JSDOM(fs.readFileSync(require.resolve('../static/cruise.html'),'utf8'),{url:'http://127.0.0.1:8877/cruise'});const backend=state(),calls=[];let record=actionRecord();const fetcher=async(path,options={})=>{const body=options.body?JSON.parse(options.body):undefined;calls.push({path,options,body});if(path==='/api/cruise?view=console'&&body===undefined)return {ok:true,json:async()=>copy(backend)};if(path.endsWith('/reconcile-preview'))return {ok:true,json:async()=>({ok:true,id:body.id,reconciliation_digest:'reconcile-digest',expires_at:'2026-10-25T14:04:00Z',intent:actionRecord(path.includes('/cruise/actions/')?'task':'main').intent,evidence:{value:'1800.00',match:'target',surface:{captured_at:'2026-10-25T14:03:00Z'}}})};if(path.endsWith('/reconcile-confirm')){for(const queue of ['browser_control','task_control'])for(const action of backend[queue].actions)if(action.id===body.id)action.state='reconciled_target';}if(path.endsWith('/create')||path.endsWith('/prepare-rule')){const queue=path.includes('/cruise/actions/')?'task':'main';record=actionRecord(queue);backend[queue==='task'?'task_control':'browser_control'].actions.push(record);return {ok:true,json:async()=>copy(record)};}if(path.endsWith('/save')||path.endsWith('/save-auto')){const a=backend.accounts.find(a=>a.id===body.account_id);a.profile=copy(body.profile);a.bindings=copy(body.bindings);a.revision+='-saved';}if(path.endsWith('/approve')){record.state='approved';}return {ok:true,json:async()=>({ok:true})};};const app=new CruiseConsole(dom.window.document,fetcher,{poll:false});const $=selector=>dom.window.document.querySelector(selector);function edit(selector,value,type='input'){const el=$(selector);assert.ok(el,selector);if(el.type==='checkbox')el.checked=value;else el.value=value;el.dispatchEvent(new dom.window.Event(type,{bubbles:true}));}function click(selector){const el=$(selector);assert.ok(el,selector);el.click();}return {dom,backend,calls,app,$,edit,click};}
const settle=async()=>{for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));};

test('each F5 rule saves only gender adult ages and fan exclusion without changing other accounts',async()=>{
 const f=fixture();await f.app.start();f.app.feature='f5';
 f.app.draft.profile.features.f5.rules=[{chase_type:'控成本追投',material_roi_min:3}];f.app.renderFeature();
 assert.match(f.$('.chase-audience').textContent,/性别不限/);
 f.edit('[data-profile-path="features.f5.rules.0.audience.gender"]','female','change');
 f.edit('[data-audience-age="18-23"]',true,'change');
 f.edit('[data-audience-age="31-40"]',true,'change');
 f.edit('[data-profile-path="features.f5.rules.0.audience.exclude_fans"]',true,'change');
 assert.deepEqual(f.app.draft.profile.features.f5.rules[0].audience,{gender:'female',ages:['18-23','31-40'],exclude_fans:true});
 assert.equal(f.calls.some(c=>c.path.endsWith('/save')),false);
 await f.app.save();
 const saved=f.calls.find(c=>c.path.endsWith('/save'));
 assert.equal(saved.body.account_id,live);assert.equal(saved.body.profile.features.f5.rules[0].audience.gender,'female');
 assert.equal(f.backend.accounts.find(a=>a.id===product).profile.features.f5.rules.length,0);
 f.app.stop();
});

test('a stalled write releases the console controls without retrying or claiming cancellation',async()=>{
 const f=fixture();await f.app.start();const original=f.app.fetcher;f.app.options.writeTimeoutMs=20;let attempts=0;
 f.app.fetcher=(path,options)=>new Promise((resolve,reject)=>{attempts++;options.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true});});
 const button=f.$('#save-profile');await f.app.task(button,()=>f.app.api('/api/test-write',{account_id:live}));
 assert.equal(attempts,1);assert.equal(f.app.busy,false);assert.equal(button.disabled,false);assert.match(f.$('#notice').textContent,/后台可能仍在处理/);
 f.app.fetcher=original;assert.equal(await f.app.refresh(),true);f.app.stop();
});

test('a stalled status read times out without losing input or blocking the next refresh',async()=>{
 const f=fixture();await f.app.start();f.edit('#main-reason','保留这次输入');const before=f.app.state;
 const original=f.app.fetcher;f.app.options.readTimeoutMs=20;
 f.app.fetcher=(path,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true}));
 assert.equal(await f.app.refresh(),false);assert.equal(f.app.loading,false);assert.equal(f.app.state,before);
 assert.equal(f.$('#main-reason').value,'保留这次输入');assert.match(f.$('#status-read').textContent,/状态读取超时/);
 f.app.fetcher=original;assert.equal(await f.app.refresh(),true);assert.equal(f.$('#main-reason').value,'保留这次输入');assert.equal(f.$('#status-read').hidden,true);
});

test('ledger integrates readable API history and Chinese runtime logs without changing review controls',async()=>{
 const previous=global.QianchuanActivity;
 global.QianchuanActivity=require('../static/activity-log.js');
 try {
  const f=fixture();
  f.backend.monitoring={accounts:[{account_id:live,backend:'official_api'}]};
  f.backend.logs=[{id:7,account_id:live,at:'2026-11-10T09:10:33+08:00',event:'rule_auto_scan',detail:{message:'本轮已暂停1条任务'}}];
  f.backend.browser_control.actions=[actionRecord()];
  const original=f.app.api.bind(f.app);let historyCalls=0;
  f.app.api=async(path,...args)=>{
   if(!path.startsWith('/api/official-api/actions/history?'))return original(path,...args);
   historyCalls++;
   return {account_id:live,total:1,page:1,pages:1,records:[{id:'api-budget',state:'verified',created_at:'2026-11-10T09:00:00+08:00',
    intent:{account_id:live,plan_id:plan,task_id:task,action:'task_budget',before:'500',after:'600'}}]};
  };
  await f.app.start();assert.equal(historyCalls,0);
  f.app.setView('ledger');await settle();
  assert.match(f.$('#activity-log').textContent,/500.00元 → 600.00元/);
  assert.match(f.$('#runtime-logs').textContent,/自动规则巡查/);
  assert.match(f.$('#runtime-logs').textContent,/本轮已暂停1条任务/);
  assert.equal(f.$('#legacy-records').open,true);
  assert.ok(f.$('[data-review="action-id"]'));
  await f.app.refresh();assert.equal(historyCalls,1);
  const scripts=[...f.dom.window.document.querySelectorAll('script[src]')].map(e=>e.getAttribute('src'));
  assert.ok(scripts.indexOf('/activity-log.js')<scripts.indexOf('/cruise.js'));
 } finally {if(previous)global.QianchuanActivity=previous;else delete global.QianchuanActivity;}
});

test('auto scanning never presents disconnected creation as operational and exposes blockers',async()=>{
 const f=fixture();f.backend.accounts[0].lanes.auto={running:true,result:{diagnostics:[
  {feature_id:'f5',status:'creation_not_connected',reason:'仅候选'},
  {feature_id:'f5',status:'incomplete_rule',reason:'规则1未设置：素材消耗上限<script>bad</script>'}]}};
 await f.app.start();const lanes=f.$('#lane-controls');
 assert.match(lanes.textContent,/扫描中 · 创建未接通/);assert.match(lanes.textContent,/素材消耗上限/);
 assert.equal(lanes.querySelectorAll('script').length,0);assert.equal(lanes.querySelectorAll('.state-tag.ready').length,0);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/approve')||c.path.endsWith('/create')).length,0);
});

test('new native plan observations populate existing dropdown without losing typed values',async()=>{
 const f=fixture();f.backend.browser_control.workers=[];await f.app.start();assert.equal(f.$('#main-plan').options.length,1);
 f.edit('#main-reason','保持输入');f.backend.browser_control=state().browser_control;await f.app.refresh();
 assert.equal(f.$('#main-plan').options.length,2);assert.equal(f.$('#main-reason').value,'保持输入');
 assert.match(f.$('#main-before').textContent,/请选择本户计划/);
});

test('account choices preserve exact long IDs and exclude other accounts or stale workers',()=>{const s=state();assert.equal(surfaceChoices(s,'main',live).length,1);assert.equal(surfaceChoices(s,'main',product)[0].surface.plan_id,'8937389604663702250');s.browser_control.workers[0].current=false;assert.equal(surfaceChoices(s,'main',live).length,0);});

test('API plan reading never offers an old browser worker as its execution adapter',()=>{
 const s=state();s.monitoring={accounts:[{account_id:live,backend:'official_api'}]};
 assert.equal(surfaceChoices(s,'main',live).length,0);assert.equal(surfaceChoices(s,'task',live).length,0);
 assert.equal(surfaceChoices(s,'main',product).length,1);
});
test('new console shows separate accounts and defaults to live/product main plan control',async()=>{const f=fixture();await f.app.start();assert.equal(f.$('#account-count').textContent,'2');assert.equal(f.$('#main-plan').options.length,2);assert.match(f.$('#main-plan').textContent,/直播计划/);assert.doesNotMatch(f.$('#main-plan').textContent,/商品图文计划/);assert.equal(f.$('#view-plans').hidden,false);assert.equal(f.$('#view-rules').hidden,true);assert.match(f.$('#assessment').textContent,/整体支付ROI.*暂未设置/);});
test('preparation sends only current account main plan and never approves automatically',async()=>{const f=fixture();await f.app.start();f.edit('#main-plan','worker:'+plan,'change');f.edit('#main-after','1800.00');f.edit('#main-reason','稳定后逐步调整');f.click('#prepare-main');await settle();const post=f.calls.find(c=>c.path.endsWith('/create'));assert.deepEqual(post.body,{worker_id:'worker',epoch:'epoch',account_id:live,plan_id:plan,action:'budget_up',after:'1800.00',reason:'稳定后逐步调整'});assert.equal(post.options.headers['X-Monitor-CSRF'],'local-csrf');assert.equal(f.calls.filter(c=>c.path.endsWith('/approve')).length,0);assert.equal(f.$('#approve-review').disabled,true);});
test('one explicit checked confirmation approves the reviewed digest exactly once',async()=>{const f=fixture();await f.app.start();const a=actionRecord();f.backend.browser_control.actions=[a];f.app.showReview(a,'main');f.click('#approve-review');await settle();assert.equal(f.calls.filter(c=>c.path.endsWith('/approve')).length,0);f.edit('#review-checked',true,'change');assert.equal(f.$('#approve-review').disabled,false);f.click('#approve-review');await settle();const approvals=f.calls.filter(c=>c.path.endsWith('/approve'));assert.equal(approvals.length,1);assert.deepEqual(approvals[0].body,{id:'action-id',intent_digest:'exact-digest',confirmed:true});assert.equal(f.$('#action-review').hidden,true);});
test('polling preserves typed main plan values, rule edits and the checked review',async()=>{const f=fixture();await f.app.start();f.edit('#main-plan','worker:'+plan,'change');f.edit('#main-after','1760');f.edit('#main-reason','保留本次输入');f.edit('[data-profile-path="features.f1.max_roi"]','2.5');const a=actionRecord();f.backend.browser_control.actions=[a];f.app.showReview(a,'main');f.edit('#review-checked',true,'change');await f.app.refresh();assert.equal(f.$('#main-after').value,'1760');assert.equal(f.$('#main-reason').value,'保留本次输入');assert.equal(f.$('[data-profile-path="features.f1.max_roi"]').value,'2.5');assert.equal(f.$('#review-checked').checked,true);assert.equal(f.app.dirty,true);});
test('dirty account switch requires explicit decision and never copies rules to another account',async()=>{const f=fixture();await f.app.start();f.edit('[data-profile-path="features.f1.max_roi"]','2.5');f.click(`[data-account="${product}"]`);await settle();assert.equal(f.app.accountId,live);assert.equal(f.$('#switch-prompt').hidden,false);assert.equal(f.calls.filter(c=>c.path.endsWith('/save')).length,0);f.click('[data-switch="discard"]');await settle();assert.equal(f.app.accountId,product);assert.equal(f.app.draft.profile.features.f1.max_roi,null);assert.match(f.$('#main-plan').textContent,/商品图文计划/);assert.doesNotMatch(f.$('#main-plan').textContent,/直播计划/);});
test('saving applies only current account with optimistic revision and keeps blank thresholds null',async()=>{const f=fixture();await f.app.start();f.edit('[data-profile-path="features.f1.max_roi"]','');f.edit('[data-profile-path="features.f1.min_control_cost"]','75');f.click('#save-profile');await settle();const save=f.calls.find(c=>c.path.endsWith('/save'));assert.equal(save.body.account_id,live);assert.equal(save.body.expected_revision,'live-rev');assert.equal(save.body.profile.features.f1.max_roi,null);assert.equal(save.body.profile.features.f1.min_control_cost,75);assert.equal(f.backend.accounts[1].profile.features.f1.min_control_cost,50);assert.equal(f.app.dirty,false);});
test('stale worker state disables preparation without discarding draft inputs',async()=>{const f=fixture();await f.app.start();f.edit('#main-plan','worker:'+plan,'change');f.edit('#main-after','1700');f.backend.browser_control.workers[0].current=false;await f.app.refresh();assert.equal(f.$('#main-after').value,'1700');assert.equal(f.$('#prepare-main').disabled,true);assert.match(f.$('#main-before').textContent,/过期/);assert.equal(f.calls.filter(c=>c.path.endsWith('/create')).length,0);});
test('F5 unsupported creation remains disabled and F6 describes diagnostics accurately',async()=>{const f=fixture();f.backend.accounts[0].lanes.auto.result={proposals:[{id:'candidate',feature_id:'f5',action:'auto_chase',parent_plan_id:plan,entity_id:task,object_type:'creative',native_dispatch_required:'material_chase_creation',reason:'命中条件'}],summary:'1条候选'};await f.app.start();f.click('[data-view=rules]');assert.equal(f.$('[data-proposal="candidate"]').disabled,true);f.click('[data-feature="f6"]');assert.match(f.$('#feature-editor').textContent,/不会把保存参数当作已调整计划ROI/);f.click('[data-feature="f5"]');assert.match(f.$('#feature-editor').textContent,/尚未接通创建追投/);});
test('blocked guarantee disables approval and official links reject script or foreign host URLs',async()=>{const f=fixture();await f.app.start();const a=actionRecord();a.cost_guarantee={status:'blocked',allowed:false,blocking_reasons:['ROI调整间隔不足'],sources:[{title:'官方规则',url:'https://school.jinritemai.com/example'},{title:'恶意链接',url:'javascript:alert(1)'},{title:'其他网址',url:'https://example.com'}]};f.app.showReview(a,'main');f.edit('#review-checked',true,'change');assert.equal(f.$('#approve-review').disabled,true);assert.match(f.$('#action-review').textContent,/ROI调整间隔不足/);const links=f.$('#action-review').querySelectorAll('a');assert.equal(links.length,1);assert.equal(links[0].hostname,'school.jinritemai.com');assert.doesNotMatch(guaranteeHTML(a.cost_guarantee),/href="javascript:/);});
test('poll status preserves approval checkbox but prevents duplicate approval after claim',async()=>{const f=fixture();await f.app.start();const a=actionRecord();f.backend.browser_control.actions=[a];f.app.showReview(a,'main');f.edit('#review-checked',true,'change');f.backend.browser_control.actions[0].state='claimed';await f.app.refresh();assert.equal(f.$('#review-checked').checked,true);assert.equal(f.$('#approve-review').disabled,true);assert.match(f.$('#review-current-state').textContent,/已占用/);assert.doesNotMatch(f.$('#action-ledger').textContent,/刷新后设置已核验/);});
test('task rule preparation keeps account and proposal ID and requires a separate digest confirmation',async()=>{const f=fixture();f.backend.accounts[0].lanes.main.result={proposals:[{id:'rule-candidate',feature_id:'f2',action:'control_budget_up',parent_plan_id:plan,entity_id:task,before:'1500.00',after:'1800.00',reason:'命中成本与ROI'}]};await f.app.start();f.click('[data-view=rules]');f.click('[data-proposal="rule-candidate"]');await settle();const prepare=f.calls.find(c=>c.path.endsWith('/prepare-rule'));assert.deepEqual(prepare.body,{account_id:live,proposal_id:'rule-candidate'});assert.equal(f.calls.filter(c=>c.path.endsWith('/approve')).length,0);f.edit('#review-checked',true,'change');f.click('#approve-review');await settle();assert.equal(f.calls.filter(c=>c.path==='/api/cruise/actions/approve').length,1);});
test('unsaved rules cannot start or scan and a clean start never approves an advertisement',async()=>{const f=fixture();await f.app.start();f.edit('[data-profile-path="features.f1.max_roi"]','2');f.click('[data-lane="main"]');await settle();assert.equal(f.calls.filter(c=>c.path==='/api/cruise/control').length,0);assert.match(f.$('#notice').textContent,/先保存/);f.click('#save-profile');await settle();f.click('[data-lane="main"]');await settle();const control=f.calls.find(c=>c.path==='/api/cruise/control');assert.deepEqual(control.body,{account_id:live,lane:'main',running:true});assert.equal(f.calls.filter(c=>c.path.endsWith('/approve')).length,0);});
test('rule rows use real null defaults and remove only the selected row',async()=>{const f=fixture();await f.app.start();f.click('[data-feature="f2"]');f.click('[data-add-rule="f2"]');f.click('[data-add-rule="f2"]');assert.equal(f.app.draft.profile.features.f2.rules.length,2);assert.equal(f.app.draft.profile.features.f2.rules[0].target_control_budget,null);f.edit('[data-profile-path="features.f2.rules.1.target_control_budget"]','2000');f.click('[data-remove-rule="0"]');assert.equal(f.app.draft.profile.features.f2.rules.length,1);assert.equal(f.app.draft.profile.features.f2.rules[0].target_control_budget,2000);});

test('unknown task action reads the exact original worker and requires separate current-value confirmation',async()=>{const f=fixture();const a=actionRecord('task');a.state='unknown';a.worker_id='task-worker';f.backend.task_control={workers:[{worker_id:'task-worker',epoch:'task-epoch',armed:true,current:true,surfaces:[{...surface(),task_id:task,actions:['control_budget_up']}]}],actions:[a]};await f.app.start();f.click('[data-view=ledger]');f.click('[data-reconcile="action-id"]');await settle();const preview=f.calls.find(c=>c.path.endsWith('/reconcile-preview'));assert.deepEqual(preview.body,{id:'action-id',worker_id:'task-worker',epoch:'task-epoch'});assert.equal(f.$('#confirm-reconciliation').disabled,true);assert.match(f.$('#action-review').textContent,/不重试原动作/);f.edit('#reconciliation-checked',true,'change');f.click('#confirm-reconciliation');await settle();const confirms=f.calls.filter(c=>c.path.endsWith('/reconcile-confirm'));assert.equal(confirms.length,1);assert.deepEqual(confirms[0].body,{id:'action-id',reconciliation_digest:'reconcile-digest',confirmed:true});assert.equal(f.calls.filter(c=>c.path.endsWith('/approve')||c.path.endsWith('/create')).length,0);assert.equal(f.$('#action-review').hidden,true);});
test('unknown result cannot be resolved with a worker from another account',async()=>{const f=fixture();const a=actionRecord();a.state='unknown';a.worker_id='worker';f.backend.browser_control.actions=[a];f.backend.browser_control.workers[0].surfaces=[surface(product)];await f.app.start();f.click('[data-view=ledger]');f.click('[data-reconcile="action-id"]');await settle();assert.equal(f.calls.filter(c=>c.path.endsWith('/reconcile-preview')).length,0);assert.match(f.$('#notice').textContent,/原Chrome刷新同一计划/);});
test('duration rule candidate displays queue values in total minutes',async()=>{const f=fixture();f.backend.accounts[0].lanes.main.result={proposals:[{id:'duration',feature_id:'f3',action:'control_duration_up',parent_plan_id:plan,entity_id:task,before:'120.00',after:'180.00',reason:'分别确认'}]};await f.app.start();f.click('[data-view=rules]');assert.match(f.$('#proposals').textContent,/120.00.*180.00 分钟（总时长）/);assert.doesNotMatch(f.$('#proposals').textContent,/180.00 小时/);});
test('a server-side lane start refreshes the profile revision before the next parameter save',async()=>{const f=fixture();await f.app.start();f.edit('#main-plan','worker:'+plan,'change');f.edit('#main-reason','保留主计划输入');f.backend.accounts[0].profile.enabled=true;f.backend.accounts[0].revision='started-revision';await f.app.refresh();assert.equal(f.app.revision,'started-revision');assert.equal(f.app.draft.profile.enabled,true);assert.equal(f.$('#main-reason').value,'保留主计划输入');f.edit('[data-profile-path="features.f1.max_roi"]','2.2');await f.app.save();const save=f.calls.find(c=>c.path.endsWith('/save'));assert.equal(save.body.expected_revision,'started-revision');assert.equal(save.body.profile.enabled,true);});
test('explicit reload can resolve a stale revision while background refresh preserves dirty rules',async()=>{const f=fixture();await f.app.start();f.edit('[data-profile-path="features.f1.max_roi"]','2.2');f.backend.accounts[0].profile.features.f1.max_roi=2.8;f.backend.accounts[0].revision='changed-elsewhere';await f.app.refresh();assert.equal(f.app.revision,'live-rev');assert.equal(f.$('[data-profile-path="features.f1.max_roi"]').value,'2.2');f.click('#reload-profile');await settle();assert.equal(f.app.revision,'changed-elsewhere');assert.equal(f.$('[data-profile-path="features.f1.max_roi"]').value,'2.8');assert.equal(f.app.dirty,false);});
test('a failed status reload leaves the current rule draft intact',async()=>{const f=fixture();await f.app.start();f.edit('[data-profile-path="features.f1.max_roi"]','2.2');f.app.fetcher=async()=>{throw new Error('连接中断');};f.click('#reload-profile');await settle();assert.equal(f.app.draft.profile.features.f1.max_roi,2.2);assert.equal(f.app.dirty,true);assert.match(f.$('#notice').textContent,/草稿已保留/);});

test('running material checkbox saves only its account and retains its value after refresh',async()=>{
 const f=fixture();f.backend.feature_catalog=copy(f.backend.feature_catalog);
 f.backend.feature_catalog.find(c=>c.id==='f5').fields=[{key:'allow_running_materials',label:'允许调控中素材再次追投',type:'boolean',default:false,help:'每批仍单独确认'}];
 for(const a of f.backend.accounts)a.profile.features.f5.allow_running_materials=false;
 await f.app.start();f.click('[data-feature="f5"]');const selector='[data-profile-path="features.f5.allow_running_materials"]';
 assert.equal(f.$(selector).type,'checkbox');assert.equal(f.$(selector).checked,false);
 f.edit(selector,true,'change');assert.equal(f.app.dirty,true);assert.equal(f.calls.filter(c=>c.path.endsWith('/save')).length,0);
 f.click('#save-profile');await settle();const save=f.calls.find(c=>c.path.endsWith('/save'));
 assert.equal(save.body.account_id,live);assert.equal(save.body.profile.features.f5.allow_running_materials,true);
 await f.app.refresh();assert.equal(f.$(selector).checked,true);
 f.click('[data-account="'+product+'"]');await settle();assert.equal(f.$(selector).checked,false);
 assert.equal(f.backend.accounts[1].profile.features.f5.allow_running_materials,false);
 f.click('[data-account="'+live+'"]');await settle();assert.equal(f.$(selector).checked,true);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/approve')||c.path.endsWith('/create')).length,0);
});

test('ID entry button sends only the selected account and displays its program status',async()=>{
 const f=fixture();f.backend.accounts[0].entry={state:'queued',message:'正在按账户ID自动进入'};
 await f.app.start();assert.match(f.$('#account-entry').textContent,/正在按账户ID自动进入/);
 f.click('#enter-account');await settle();assert.deepEqual(f.calls.find(c=>c.path.endsWith('/enter-account')).body,{account_id:live});
 assert.equal(f.calls.filter(c=>c.path.endsWith('/approve')||c.path.endsWith('/create')).length,0);
 f.click('[data-account="'+product+'"]');await settle();f.click('#enter-account');await settle();
 assert.equal(f.calls.filter(c=>c.path.endsWith('/enter-account')).at(-1).body.account_id,product);
});


test('F4 keeps the lifetime rule and saves a separate today duration, spend and ROI rule',async()=>{
 const f=fixture();f.backend.feature_catalog.push({id:'f4',name:'批量成本暂停',fields:[{key:'pause_rule',label:'暂停条件',type:'select',default:'cost_equals',options:[{value:'cost_equals',label:'成本等值'},{value:'low_spend',label:'创建满指定小时，累计消耗仍偏低'},{value:'daily_spend_roi',label:'今日时长＋消耗＋ROI'}]},field('min_runtime_minutes','原时长',30),field('control_cost_equals','原成本',0),field('low_spend_hours','创建满小时'),field('low_spend_max','累计消耗低于'),field('daily_runtime_hours','今日经过时长达到'),field('daily_spend_max','今日调控消耗低于'),field('daily_roi_max','今日调控支付ROI低于'),{key:'low_spend_scope',label:'范围',type:'select',default:'both',options:[{value:'both',label:'两种追投'}]}]});
 f.backend.accounts[0].profile.features.f4={enabled:false};await f.app.start();f.click('[data-feature="f4"]');
 assert.equal(f.$('[data-profile-path="features.f4.pause_rule"]').value,'cost_equals');
 assert.equal(f.$('[data-profile-path="features.f4.low_spend_hours"]'),null);
 f.edit('[data-profile-path="features.f4.pause_rule"]','low_spend','change');
 assert.equal(f.$('[data-profile-path="features.f4.low_spend_hours"]').value,'');assert.equal(f.$('[data-profile-path="features.f4.low_spend_max"]').value,'');
 assert.equal(f.$('[data-profile-path="features.f4.control_cost_equals"]'),null);assert.match(f.$('#feature-editor').textContent,/累计消耗/);
 assert.equal(f.$('[data-profile-path="features.f4.daily_roi_max"]'),null);
 assert.equal(f.calls.filter(c=>c.body).length,0);
 f.edit('[data-profile-path="features.f4.pause_rule"]','daily_spend_roi','change');
 assert.equal(f.$('[data-profile-path="features.f4.low_spend_hours"]'),null);
 for(const [key,value] of [['daily_runtime_hours',2],['daily_spend_max',20],['daily_roi_max',1.5]]){
   const selector=`[data-profile-path="features.f4.${key}"]`;
   assert.equal(f.$(selector).value,'');f.edit(selector,String(value));
 }
 f.click('#save-profile');await settle();
 const config=f.backend.accounts[0].profile.features.f4;
 assert.deepEqual([config.pause_rule,config.daily_runtime_hours,config.daily_spend_max,config.daily_roi_max],['daily_spend_roi',2,20,1.5]);
});

test('saving an account with active automatic authority explicitly updates its rules and never approves an ad',async()=>{
 const f=fixture();f.backend.api_rule_auto=[{account_id:live,enabled:true,profile_revision:'live-rev'}];await f.app.start();
 assert.match(f.$('#save-profile').textContent,/更新已开启/);f.app.draft.profile.features.f1.max_roi=2.5;f.app.markDirty();f.click('#save-profile');await settle();
 const call=f.calls.find(c=>c.path==='/api/cruise/save-auto');assert.ok(call);assert.equal(call.body.account_id,live);assert.equal(call.body.profile.features.f1.max_roi,2.5);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/approve')||c.path.endsWith('/confirm')).length,0);
});
