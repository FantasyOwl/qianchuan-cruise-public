const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

test('visible plan logs display before and after values with observation limits and escaped IDs',()=>{
  const p=page(),html=p.run(`planOperationHTML([{occurred_at:'2026-10-25T00:00:47+08:00',control_id:'<img src=x>',before_budget:1200,after_budget:0,observed_at:'2026-10-25T00:25:47+08:00'}])`);
  assert.match(html,/历史未核全/);assert.match(html,/不认证平台执行结果/);assert.match(html,/1,200.00/);
  assert.match(html,/0.00/);assert.match(html,/读取时间/);assert.match(html,/&lt;img/);assert.ok(!html.includes('<img'));
  assert.equal(p.run('planOperationHTML([])'),'');
});

test('smart control preview distinguishes local deadline from parent-scoped stop and escapes native text',()=>{
  const p=page(),html=p.run(`smartControlHTML({account_id:'9990001',parent_plan_id:'8012367229158357',budget:'100.00',local_duration_policy:{duration_minutes:30},create_request:{documented_endpoint:'<create>',body_json:'{"ad_id":8012367229158357,"budget":100}'},stop_request:{documented_endpoint:'<stop>',body_json:'{"ad_id":8012367229158357,"opt_type":"DISABLE"}'},execution_blockers:['<img src=x>']})`);
  assert.match(html,/持续 30 分钟/);assert.match(html,/尚未注册截止/);assert.match(html,/母计划ad_id/);
  assert.match(html,/创建接口不接收duration/);assert.match(html,/8012367229158357/);
  assert.match(html,/&lt;create&gt;/);assert.match(html,/&lt;img/);assert.ok(!html.includes('<img'));
  assert.match(html,/不填入真实账户设置/);assert.match(html,/不含执行地址/);
});

test('growth policy displays local defaults without changing saved account targets or configuration',()=>{
  const p=page();p.run(`state={settings:{accounts:[],rules:{growth_cost_max_ratio:1.05},ai:{}},ai_key_present:false};renderForms()`);
  const html=p.elements.get('#rule-settings').innerHTML;
  assert.match(html,/data-rule="growth_roi_max_ratio" value="1.2"/);
  assert.match(html,/data-rule="growth_cost_max_ratio" value="1.05"/);
  assert.match(html,/data-rule="growth_spend_max_ratio" value="1.3"/);
  assert.match(html,/不是官方成本保障限制/);
  assert.equal(p.run(`state.settings.rules.growth_roi_max_ratio`),undefined);
});

test('creative share preserves zero and explains missing ratios without inventing percentages',()=>{
  const p=page();let html=p.run(`creativeSpendShareHTML({status:'observed',is_current:true,share_percent:0,creative_spend:0,parent_spend:100,ledger:'base',parent_plan_id:'p1'})`);
  assert.match(html,/单素材消耗占比/);assert.match(html,/占比 0.00%/);assert.match(html,/基础广告消耗/);
  html=p.run(`creativeSpendShareHTML({status:'missing',share_percent:null,reason:'母计划消耗为零 <img src=x>',parent_plan_id:'<script>bad</script>'})`);
  assert.match(html,/暂不可计算/);assert.match(html,/&lt;img/);assert.ok(!html.includes('<img'));assert.ok(!html.includes('<script>'));assert.ok(!html.includes('NaN%'));
});

test('object panel labels historical creative share and keeps the monitoring limits visible',()=>{
  const p=page();p.run(`renderObjectAnalysis({data_status:'stale',creative_spend_share:{status:'historical',share_percent:25,is_current:false,ledger:'overall',creative_spend:25,parent_spend:100,note:'不表示素材全集覆盖、ROI、利润或调控资格。'}})`);
  const html=p.elements.get('#object-analysis').innerHTML;
  assert.match(html,/历史比值/);assert.match(html,/当前时效未确认/);assert.match(html,/占比 25.00%/);assert.match(html,/不表示素材全集覆盖/);
});

test('object analysis names the historical daily clock reason instead of showing a fresh current period',()=>{
  const p=page();p.run(`renderObjectAnalysis({data_status:'stale',data_status_reason:'日累计统计日期不是今天 <img src=x>',data_at:'2026-10-21T23:59:00+08:00',metric_period:'day',spend_trend:{is_current:false,windows:[],comparison_reason:'历史观察'}})`);
  const html=p.elements.get('#object-analysis').innerHTML;
  assert.match(html,/历史或过期数据/);assert.match(html,/日累计统计日期不是今天/);assert.match(html,/历史或最新时间待核验/);assert.match(html,/&lt;img/);assert.ok(!html.includes('<img'));
});

test('account registry waits for a capable collector before allowing read-scope expansion',()=>{
  const p=page();const html=p.run(`accountRegistryHTML({accounts:[{id:'101'}],collector_ready_for_scope_changes:false},[{id:'101',name:'直播甲',scene:'live',tab_group:'甲组',observe:true}])`);
  assert.match(html,/data-field="id"[^>]*disabled/);assert.match(html,/data-field="tab_group"[^>]*disabled/);assert.match(html,/data-field="name"[^>]*value="直播甲"/);assert.match(html,/启用监测/);assert.ok(!html.includes('data-account='));
});

test('account registry supports distinct groups and escapes user names without carrying targets into new accounts',()=>{
  const p=page();const html=p.run(`accountRegistryHTML({accounts:[],collector_ready_for_scope_changes:true},[{id:'303',name:'<img src=x>',scene:'live',tab_group:'乙组',observe:false}])`);
  assert.match(html,/乙组/);assert.match(html,/&lt;img/);assert.ok(!html.includes('<img'));assert.ok(!html.includes('target_roi'));assert.ok(!html.includes('checked'));
});

test('API entry stays selectable without the browser collector and preserves the chosen family',()=>{
  const p=page();const html=p.run(`accountRegistryHTML({accounts:[{id:'101'}],collector_ready_for_scope_changes:false},[{id:'101',name:'直播甲',scene:'live',tab_group:'甲组',observe:true,delivery_entry:'both'}])`);
  assert.match(html,/data-field="delivery_entry">/);
  assert.match(html,/<option value="both" selected>全域与乘方<\/option>/);
  assert.match(html,/value="chengfang"/);
  assert.match(html,/data-field="tab_group"[^>]*disabled/);
  assert.ok(!html.includes('target_roi'));
});
test('authorized API mode allows adding account IDs without a Chrome collector',()=>{
  const p=page();const html=p.run(`accountRegistryHTML({accounts:[],api_connected:true,scope_changes_available:true,collector_ready_for_scope_changes:false},[{id:'303',name:'API户',scene:'live',tab_group:'API账户',observe:true}])`);
  assert.doesNotMatch(html,/disabled/);assert.match(html,/API账户/);
});

test('supervisor state matches the current process without claiming fresh metrics or continuous coverage',()=>{
  const p=page();const html=p.run(`supervisionHTML({state:'child_running',current_process_supervised:true,launch_count:2,restart_count:1,last_exit_code:7,note:'进程守护状态不证明接口可用、指标新鲜或7×24连续覆盖。'})`);
  assert.match(html,/当前服务由守护器托管/);assert.match(html,/重新启动 1 次/);assert.match(html,/最近退出码 7/);assert.match(html,/不证明接口可用/);assert.match(html,/停止守护监测.cmd/);
});

test('stale supervisor records do not masquerade as management of the current service',()=>{
  const p=page();const html=p.run(`supervisionHTML({state:'manager_not_observed',current_process_supervised:false,last_exit_code:'<img src=x>',note:'<script>x</script>'})`);
  assert.match(html,/当前服务未确认由守护器托管/);assert.match(html,/未观察到活跃守护器/);assert.ok(!html.includes('<img'));assert.ok(!html.includes('<script>'));
});

test('independent spend table preserves zero, actual times and an unavailable growth baseline',()=>{
  const p=page();const html=p.run(`spendTrendHTML({field:'comprehensive_cost',is_current:false,change_percent:null,comparison_reason:'前窗消耗为零',windows:[{complete:true,start:'2026-10-22T10:00:00+08:00',end:'2026-10-22T11:00:00+08:00',observed_start:'2026-10-22T10:04:00+08:00',observed_end:'2026-10-22T11:00:00+08:00',cost:0,average_cost_per_hour:0}],note:'净额变化原因及归属时段未核验'})`);
  assert.match(html,/综合成本/);assert.match(html,/历史或最新时间待核验/);assert.match(html,/前窗消耗为零/);assert.match(html,/10:04/);assert.match(html,/>0.00</);assert.match(html,/净额变化原因及归属时段未核验/);assert.ok(!html.includes('ROI达标'));
});

test('spend windows keep invalid periods visible and escape diagnostic text',()=>{
  const p=page();p.run(`renderObjectAnalysis({data_status:'fresh',spend_trend:{windows:[{complete:false,reason:'<img src=x>'}],note:'<script>bad</script>'}})`);
  const html=p.elements.get('#object-analysis').innerHTML;
  assert.match(html,/消耗分时（独立观察）/);assert.match(html,/证据不足/);assert.match(html,/&lt;img/);assert.ok(!html.includes('<img'));assert.ok(!html.includes('<script>'));
});

test('product carrying shows item units and separate clocks without treating orders as stock',()=>{
  const p=page();const html=p.run(`productCarryingHTML({inventory:[{account_id:'a',product_id:'p',state:'coverage_below_policy',available_stock:0,units_per_hour:24,stock_coverage_hours:0,minimum_stock_hours:1,clock_current:true,inventory_at:'2026-10-22T12:00:00+08:00',performance_data_at:null}],plans:[]},'a')`);
  assert.match(html,/可售 0 件/);assert.match(html,/每小时支付 24.00 件/);assert.match(html,/覆盖 0.00 小时/);assert.match(html,/业绩时刻 未采集/);assert.match(html,/订单数不当作支付件数/);assert.match(html,/两种时间不补填/);
});

test('product carrying filters account evidence and does not promise automatic category change',()=>{
  const p=page();const html=p.run(`productCarryingHTML({plans:[{account_id:'a',plan_id:'<img src=x>',hold_growth:true,alignment_state:'category_mismatch',reasons:['<script>bad</script>']},{account_id:'other',plan_id:'PRIVATE_OTHER'}]},'a')`);
  assert.ok(!html.includes('PRIVATE_OTHER'));assert.ok(!html.includes('<img'));assert.ok(!html.includes('<script>'));assert.match(html,/相关计划增量建议保持阻断/);assert.match(html,/不自动换品/);
});

test('cross-day ROI view shows separate daily ledgers and zero changes today',()=>{
  const p=page();const html=p.run(`roiRecoveryHTML([{state:'ready_return_step',cross_day:true,is_current:true,reported_changes_on_current_day:0,daily_boundaries:[{closing_metric_date:'2026-10-21',opening_metric_date:'2026-10-22'}]}])`);
  assert.match(html,/独立日台账分段/);assert.match(html,/2026-10-21 封账 → 2026-10-22 零累计起点/);assert.match(html,/当日已报告变更 0 次/);assert.match(html,/昨日次数不计入今日/);assert.match(html,/不代表平台认证/);
});

test('missing cross-day boundary evidence stays visible and source dates are escaped',()=>{
  const p=page();const html=p.run(`roiRecoveryHTML([{state:'verify_episode',cross_day:true,daily_boundaries:[{closing_metric_date:'<img src=x>',opening_metric_date:'next'}],missing:['缺少次日零累计起点']}])`);
  assert.match(html,/缺少次日零累计起点/);assert.ok(!html.includes('<img'));assert.match(html,/&lt;img/);assert.ok(!html.includes('已执行'));
});

test('collection health separates communication, collector rounds and missing business data',()=>{
  const p=page();const html=p.run(`collectionHealthHTML({accounts:[{account_id:'a',state:'data_time_missing',observed_sources:1,clock_current_sources:0}],sources:[{account_id:'a',entity_id:'own',scope:'plan',captured_at:'2026-10-22T10:00:00+08:00',data_at:null,capture_state:'recent',data_state:'missing',progress:'unknown'}]},{collector:{state:'ok'},browser_communication:{state:'recent'},history:{retained_cycles:2,error_cycles:1,restart_transitions:1,previous_unfinished_cycles:1,observed_gaps:[]}},'a')`);
  assert.match(html,/缺少指标更新时间/);assert.match(html,/后台轮次完成/);assert.match(html,/通信不证明指标更新/);assert.match(html,/不补造停机期间/);assert.match(html,/不能推断零消耗/);
});

test('collection health filters other account identifiers and escapes diagnostic data',()=>{
  const p=page();const html=p.run(`collectionHealthHTML({accounts:[],sources:[{account_id:'a',entity_id:'<img src=x>',scope:'plan'},{account_id:'other',entity_id:'PRIVATE'}]},{collector:{state:'error',last_error_type:'<script>bad</script>'}},'a')`);
  assert.ok(!html.includes('PRIVATE'));assert.ok(!html.includes('<img'));assert.ok(!html.includes('<script>'));assert.match(html,/&lt;img/);
});

test('cold cost bid cap uses the confirmed 1.4 ratio without proposing an automatic bid',()=>{
  const p=page();const html=p.run(`lifecycleHTML([{state:'cold_observing',declared_stage:'cold',is_current:true,bid_cap_status:'user_confirmed_1_4',cold_bid_review:{observed_bid:15,ceiling:14,state:'above_user_cap'}}])`);
  assert.match(html,/目标成本×1.4/);assert.match(html,/15.00/);assert.match(html,/14.00/);assert.match(html,/暂停追加放量/);assert.match(html,/不自动调价/);assert.ok(!html.includes('待用户确认'));
});

function page(){
  const elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',hidden:false,addEventListener(){},classList:{toggle(){}}});return elements.get(id);};
  const context=vm.createContext({document:{querySelector:element,querySelectorAll:()=>[]},Date,Number,String,JSON,Math,URLSearchParams,setTimeout,clearTimeout});
  const source=fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8').replace('load();setInterval(load,15000);','');
  vm.runInContext(source,context);
  return {context,elements,run:code=>vm.runInContext(code,context)};
}

test('execution badge follows the runtime connection without claiming automatic control',()=>{
  const p=page();p.run('state={execution_available:true};renderExecutionMode()');
  assert.equal(p.context.document.querySelector('#execution-mode-label').textContent,'逐条确认执行已连接');
  p.run('state={execution_available:false};renderExecutionMode()');
  assert.equal(p.context.document.querySelector('#execution-mode-label').textContent,'只读观察');
});

test('rule recommendation prepares approval only for the exact armed account plan and supported action',()=>{
  const p=page();p.run(`state={evaluation:{recommendations:[]},browser_control:{workers:[{armed:true,surfaces:[{account_id:'123',plan_id:'789',actions:['budget_up']}]}]}}`);
  const proposal=`{id:'r1',account_id:'123',entity_id:'789',scope:'plan',action:'budget_up',priority:'P2',before:1000,after:1100,eligible:true,blockers:[]}`;
  assert.match(p.run(`recommendationHTML(${proposal})`),/data-browser-recommendation="r1"/);
  for(const patch of ["account_id:'456'","entity_id:'999'","action:'roi_target_up'","eligible:false","scope:'control'"]){
    assert.ok(!p.run(`recommendationHTML({...${proposal},${patch}})`).includes('data-browser-recommendation'));
  }
  p.run(`state.browser_control.workers[0].armed=false`);
  assert.ok(!p.run(`recommendationHTML(${proposal})`).includes('data-browser-recommendation'));
});

test('approval UI is inactive without a worker and preserves the explicit confirmation step',()=>{
  const p=page(),root=p.context.document.querySelector('#browser-control');
  root.querySelector=p.context.document.querySelector;
  const source=fs.readFileSync(require('node:path').join(__dirname,'../static/browser-control.js'),'utf8').replace(/\nloadBrowserControl\(\);\s*$/,'');
  vm.runInContext(source,p.context);
  p.run(`browserControlState={execution_available:false,workers:[],actions:[]};renderBrowserControl()`);
  assert.match(root.innerHTML,/尚未连接/);assert.match(root.innerHTML,/id="browser-prepare"[^>]*disabled/);
  p.run(`showBrowserApproval({id:'draft1',expires_at:'2026-10-25T06:02:00Z',intent:{account_id:'123',plan_id:'789',plan_name:'<img src=x>',action_label:'上调日预算',before:'1000.00',after:'1100.00',setting_roi_basis:'marketing',reason:'已复核',origin:'manual_review'}})`);
  const approval=p.context.document.querySelector('#browser-approval').innerHTML;
  assert.match(approval,/确认并执行这一条/);assert.match(approval,/type="checkbox"/);assert.ok(!approval.includes('checked'));
  assert.match(approval,/1000.00 → 1100.00/);assert.match(approval,/未认证规则达标/);
  assert.match(approval,/&lt;img/);assert.ok(!approval.includes('<img'));
});

test('ROI graph does not connect an incomplete hour or invent an unset target',()=>{
  const p=page();
  p.run(`renderRoi({data_status:'stale',hourly:[{end:'2026-10-21T10:00:00+08:00',complete:true,roi:2,sample_sufficient:true},{end:'2026-10-21T11:00:00+08:00',complete:false},{end:'2026-10-21T12:00:00+08:00',complete:true,roi:3,sample_sufficient:false}]},{roi_basis:'overall_payment',target_roi:null},{metric_basis:'overall_payment'});`);
  const html=p.elements.get('#roi-chart').innerHTML;
  const path=html.match(/<path d="([^"]*)"/)[1];
  assert.equal((path.match(/M/g)||[]).length,2);
  assert.equal((path.match(/L/g)||[]).length,0);
  assert.ok(!html.includes('用户目标'));
  assert.match(p.elements.get('#roi-caption').textContent,/历史或过期/);
});

test('ROI target line is drawn only for the configured matching basis',()=>{
  const p=page();
  const graph=`{data_status:'fresh',hourly:[{end:'2026-10-21T10:00:00+08:00',complete:true,roi:2,sample_sufficient:true}]}`;
  p.run(`renderRoi(${graph},{roi_basis:'overall_payment',target_roi:3},{metric_basis:'marketing'});`);
  assert.ok(!p.elements.get('#roi-chart').innerHTML.includes('用户目标'));
  p.run(`renderRoi(${graph},{roi_basis:'overall_payment',target_roi:3},{metric_basis:'overall_payment'});`);
  assert.match(p.elements.get('#roi-chart').innerHTML,/用户目标 3\.00/);
});

test('expired product conclusions clearly name their data time and preserve zero stock',()=>{
  const p=page();
  p.run(`renderObjectAnalysis({data_status:'stale',data_at:'2026-10-21T10:00:00+08:00',metric_period:'unknown',product:{role:'new',stock:0,new_product_test:'sample_met_roi_qualified',note:'历史样本'}});`);
  const html=p.elements.get('#object-analysis').innerHTML;
  assert.match(html,/历史或过期数据/);assert.match(html,/2026/);assert.match(html,/库存 0/);assert.match(html,/周期未确认/);
});

test('missing funnel stages display their reason and local evidence text is escaped',()=>{
  const p=page();
  p.run(`renderObjectAnalysis({data_status:'unverified',metric_period:'live_session',lifecycle:{declared:'unknown',candidate:'unknown',evidence:{note:'<img src=x onerror=evil()>'}},funnel:{stages:[{label:'曝光',value:100}],rates:[{percent:null,reason:'同群体尚未核验'}],note:'描述性观察'}});`);
  const html=p.elements.get('#object-analysis').innerHTML;
  assert.match(html,/同群体尚未核验/);assert.match(html,/数据待核验/);assert.ok(!html.includes('<img'));assert.match(html,/&lt;img/);
});

test('local outcome display does not turn an empty report list into an execution',()=>{
  const p=page();assert.match(p.run('outcomeHTML({items:[]})'),/尚无操作报告/);
  assert.match(p.run('outcomeHTML({items:[]})'),/不会被自动记为已应用/);
});

test('expiry shows pending verification and a reported duration warning',()=>{
  const p=page();const html=p.run(`outcomeHTML({items:[{source:'manual_verified_report',review_status:'expired_pending_verification',event_id:'reported',entity_id:'task',action:'one_click_control',note:'人工报告不是平台执行回执',expiry:{due_at:'2026-10-22T16:00:00+08:00',policy_warnings:['报告时长超过策略']},evidence:{}}]})`);
  assert.match(html,/人工报告/);assert.match(html,/未确认任务停止/);assert.match(html,/报告时长超过策略/);
});

test('outcome restore candidate remains a historical simulation review with escaped text',()=>{
  const p=page();const html=p.run(`outcomeHTML({items:[{source:'simulation',review_status:'observed',event_id:'sim',entity_id:'<script>bad</script>',action:'bid_down',note:'合成',candidates:[{action_label:'复核回调',before:95,after:100,historical:true}],evidence:{}}]})`);
  assert.match(html,/合成模拟/);assert.match(html,/历史观察/);assert.match(html,/没有可执行资格/);assert.ok(!html.includes('<script>'));
});

test('migration review names both budget steps without claiming execution',()=>{
  const p=page();const html=p.run(`migrationHTML([{id:'local',state:'ready_raise',source:{entity_id:'old',before:2000,after:1800},target:{entity_id:'new',before:2000,after:2200},amount:200,total_before:4000,total_after:4000,note:'<unsafe>',next_step:{action:'budget_up'},blockers:[]}],true)`);
  assert.match(html,/等待追加新计划/);assert.match(html,/没有真实执行资格/);assert.match(html,/2,000\.00 → 1,800\.00/);
  assert.match(html,/取消本地交接计划/);assert.ok(!html.includes('<unsafe>'));assert.match(html,/&lt;unsafe&gt;/);
});

test('synthetic completed migration cannot cancel a production workflow',()=>{
  const p=page();const html=p.run(`migrationHTML([{id:'demo',state:'completed_reported',note:'人工报告不是平台回执',blockers:[]}])`);
  assert.match(html,/报告及观察已对照/);assert.ok(!html.includes('data-cancel-migration'));
});

test('action parameters expose budget duration optimization goal and parent with escaped values',()=>{
  const p=page();const html=p.run(`parameterHTML({budget:100,duration_hours:1,optimization_goal:'live_purchase',parent_plan_id:'<unsafe>',cost_guarantee:'not_supported_user_rule'})`);
  assert.match(html,/测试预算/);assert.match(html,/投放时长（小时）/);assert.match(html,/直播间购买/);
  assert.match(html,/盈亏自负/);assert.ok(!html.includes('<unsafe>'));assert.match(html,/母计划ID/);
});

test('provenance separates user strategy from dated official references and unknown qualification',()=>{
  const p=page();const html=p.run(`basisHTML({ruleset_version:'local',strategy:{source:'用户目标文件',section:'预算调控',note:'20%为用户策略'},official_references:[{url:'https://open.oceanengine.com/docs/test',title:'公开合同',checked_at:'2026-10-22',verification:'仅请求定义',application:'不证明账户权限'}],unverified:['保障资格尚未认证']})`);
  assert.match(html,/20%为用户策略/);assert.match(html,/2026-10-22/);assert.match(html,/不证明账户权限/);assert.match(html,/保障资格尚未认证/);
});

test('structured report preserves section order evidence time and missing-data conclusion',()=>{
  const p=page();const html=p.run(`reportHTML({generated_at:'2026-10-22T08:00:00+08:00',data_mode:'observations',account:{id:'allowed'},core_conclusions:['<unsafe>证据不足'],prioritized_actions:[],evidence_summary:{source_count:1,offline_eligible_actions:0},evidence_and_risks:{risks:['没有执行许可'],sources:[{data_at:null,metric_period:'live_session',period_start:'2026-10-21T06:00:00+08:00'}],alerts:[],metrics_not_currently_verified:[],outcome_reviews:[],budget_migrations:[]}})`);
  assert.ok(html.indexOf('核心结论')<html.indexOf('分级行动指令'));assert.ok(html.indexOf('分级行动指令')<html.indexOf('数据佐证与风险提示'));
  assert.match(html,/不表示账户健康/);assert.match(html,/period_start/);assert.ok(!html.includes('<unsafe>'));
});

test('emergency view preserves missing scores and historical time without claiming current shutdown',()=>{
  const p=page();const html=p.run(`emergencyHTML([{entity_id:'<unsafe>',is_current:false,data_at:null,primary_spend_stop:{state:'insufficient',missing:['缺分钟证据']},scores:[{label:'口碑分',value:0,user_threshold:null,change:null,state:'insufficient',missing:['未核对阈值单位']}]}])`);
  assert.match(html,/历史或未校验/);assert.match(html,/缺分钟证据/);assert.match(html,/口碑分 0\.00/);assert.match(html,/用户阈值 未采集/);
  assert.match(html,/不直接启停主力计划/);assert.match(html,/不是官方封禁或赔付线/);assert.ok(!html.includes('<unsafe>'));
});

test('empty emergency observation is not interpreted as risk cleared',()=>{
  const p=page();assert.match(p.run('emergencyHTML([])'),/未出现告警不表示风险已经排除/);
});

test('flow stop is separate from cost billing and marks the local impression baseline',()=>{
  const p=page();const html=p.run(`flowStopHTML({state:'triggered',baseline_impressions:1000,flat_minutes:10,missing:[]})`);
  assert.match(html,/展示停滞/);assert.match(html,/1,000 次/);assert.match(html,/本地保护线/);assert.match(html,/不证明断流原因/);
});

test('quality renderer preserves zero and shows relative percentage with both windows',()=>{
  const p=page();const html=p.run(`trafficQualityHTML({state:'suspected_broadening',is_current:true,window:{start:'2026-10-22T15:30:00+08:00',end:'2026-10-22T16:00:00+08:00'},baseline_window:{start:'2026-10-22T15:00:00+08:00',end:'2026-10-22T15:30:00+08:00'},sample_users:100,baseline_sample_users:200,comparisons:[{label:'下单用户占比',unit:'%',value:0,baseline:8,relative_change:-1}],note:'不证明原因'})`);
  assert.match(html,/当前在播观察/);assert.match(html,/疑似流量泛化/);assert.match(html,/0\.00/);assert.match(html,/-100\.00%/);
  assert.match(html,/2026/);assert.match(html,/本地观察条件/);assert.match(html,/不直接收紧定向或更改ROI/);
});

test('missing quality baseline does not display a numerical decline',()=>{
  const p=page();const html=p.run(`trafficQualityHTML({state:'insufficient',is_current:false,comparisons:[{label:'平均停留',unit:'秒',value:0,baseline:null,relative_change:null,reason:'基线缺失'}],missing:['口径未核对'],current_blockers:['在播未确认']})`);
  assert.match(html,/基线缺失/);assert.match(html,/口径未核对/);assert.match(html,/在播未确认/);assert.match(html,/历史或未核验/);
  assert.ok(!html.includes('-100.00%'));
});

test('quality content and decline reasons are escaped in selected object analysis',()=>{
  const p=page();p.run(`renderObjectAnalysis({data_status:'fresh',traffic:{quality:{state:'insufficient',is_current:false,comparisons:[{label:'<script>bad</script>',value:null,baseline:null,unit:'%',reason:'<unsafe>'}],missing:['<img src=x onerror=bad>'],note:'<unsafe>'}}});`);
  const html=p.elements.get('#object-analysis').innerHTML;
  assert.match(html,/流量质量配对窗口/);assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<img'));
  assert.match(html,/&lt;unsafe&gt;/);
});

test('maintenance renderer shows ordered review without claiming a bid mutation',()=>{
  const p=page();const html=p.run(`maintenanceHTML([{state:'handover_ready',entity_id:'plan',is_current:true,is_primary_plan:true,data_at:'2026-10-22T16:00:00+08:00',refresh_event_id:'reported',added_material_count:1,post_observation_end:'2026-10-22T14:00:00+08:00',bid_review_at:'2026-10-22T14:10:00+08:00',missing:[],post_refresh_windows:[],note:'先素材，再复核出价，最后预算'}])`);
  assert.match(html,/第三步/);assert.match(html,/主力计划/);assert.match(html,/2026/);assert.match(html,/出价方案复核不表示已经调价/);
  assert.match(html,/不直接关停主力/);assert.match(html,/不自动交接预算/);
});

test('maintenance empty and unknown role preserve missing evidence and zero count',()=>{
  const p=page();assert.match(p.run('maintenanceHTML([])'),/不能据此判断全部计划健康/);
  const html=p.run(`maintenanceHTML([{state:'verify_decline',entity_id:'plan',is_current:false,is_primary_plan:null,added_material_count:0,missing:['需要衰退起点'],post_refresh_windows:[]}])`);
  assert.match(html,/主力角色未知/);assert.match(html,/需要衰退起点/);assert.match(html,/历史或未核验来源/);assert.match(html,/新增素材 0 个/);
});

test('maintenance IDs and missing reasons are escaped',()=>{
  const p=page();const html=p.run(`maintenanceHTML([{state:'verify_refresh_report',entity_id:'<script>bad</script>',refresh_event_id:'<unsafe>',missing:['<img src=x onerror=bad>'],post_refresh_windows:[],note:'<unsafe>'}])`);
  assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<img'));assert.match(html,/&lt;unsafe&gt;/);
});

test('live exclusion view separates status time from missing performance data and never promises clearance',()=>{
  const p=page();const html=p.run(`liveExclusionHTML([{state:'system_paused',plan_id:'p',aweme_id:'a',promotion_type:'standard',overlap:'system_pause_observed',status_at:'2026-10-22T16:00:00+08:00',performance_data_at:null,is_current:true,missing:[]}])`);
  assert.match(html,/来源声明系统暂停/);assert.match(html,/不追加停用/);assert.match(html,/业绩更新 未采集/);
  assert.match(html,/全域关闭后不自动恢复/);assert.match(html,/120秒是本地/);
  assert.match(p.run('liveExclusionHTML([])'),/不能据此判定全部账户已满足互斥/);
});

test('live exclusion missing identifiers and reasons remain escaped',()=>{
  const p=page();const html=p.run(`liveExclusionHTML([{state:'status_conflict',plan_id:'<unsafe>',aweme_id:null,promotion_type:'universal',overlap:'none_observed',missing:['<missing>']}])`);
  assert.match(html,/未核对/);assert.match(html,/状态来源冲突/);assert.ok(!html.includes('<unsafe>'));assert.match(html,/&lt;missing&gt;/);
});

test('related live conflicts are shown to either account without leaking foreign plan IDs',()=>{
  const p=page();const value=p.run(`scopedAlert({account_id:'123',entity_id:'own',rule:'live_universal_standard_conflict',source_key:'foreign-series',evidence:{observed_plans:[{account_id:'123',plan_id:'own'},{account_id:'456',plan_id:'other'}]}},'456')`);
  assert.equal(value.entity_id,'other');assert.equal(value.account_id,'456');assert.equal(value.source_key,undefined);
  assert.equal(value.evidence.other_account_conflicting_plan_count,1);assert.equal(value.evidence.observed_plans.length,1);
  assert.ok(!JSON.stringify(value).includes('"own"'));
});

test('ROI recovery review preserves the original setting without claiming a candidate was applied',()=>{
  const p=page();const html=p.run(`roiRecoveryHTML([{entity_id:'plan',root_event_id:'root',state:'ready_return_step',is_current:true,original_plan_goal:3.5,current_plan_goal:3.31,next_goal:3.5,step_history:[{},{}],current_window:{ad_spend:0,roi:null},missing:[],candidate_blockers:[],note:'人工报告不是平台认证'}])`);
  assert.match(html,/原计划目标 3.5；当前设置 3.31；下一步候选 3.5/);
  assert.match(html,/观察小时消耗 0.00 元/);assert.match(html,/ROI 待核对/);assert.match(html,/候选未应用/);assert.match(html,/人工报告不是平台认证/);
});

test('ROI recovery missing and historical evidence stays escaped and does not imply execution',()=>{
  const p=page();assert.match(p.run('roiRecoveryHTML([])'),/建议不会自动记为已应用/);
  const html=p.run(`roiRecoveryHTML([{entity_id:'<unsafe>',root_event_id:'<root>',state:'observing_after_change',is_current:false,original_plan_goal:null,current_plan_goal:null,next_goal:null,missing:['<missing>']}])`);
  assert.match(html,/等待变更后完整小时/);assert.match(html,/历史或证据待核对/);assert.match(html,/下一步候选 待核对/);
  assert.ok(!html.includes('<unsafe>'));assert.match(html,/&lt;missing&gt;/);
});

test('cold lifecycle review preserves zeros and does not turn the initial budget into an action',()=>{
  const p=page();const html=p.run(`lifecycleHTML([{state:'cold_ready',declared_stage:'cold',plan_id:'plan',age_hours:24,is_current:true,daily_test_spend:240,cold_min_test_spend:200,budget:300,completion_3s:0,bid_cap_status:'user_interpretation_pending',missing:[],conflicts:[]}])`);
  assert.match(html,/预算前置样本就绪/);assert.match(html,/240.00 元 \/ 门槛 200.00/);assert.match(html,/3秒完播率 0.00%/);
  assert.match(html,/300元仅是用户初始日预算策略，不自动跳涨补齐/);assert.match(html,/上限尚未配置/);
});

test('unknown lifecycle evidence does not imply health and is escaped',()=>{
  const p=page();assert.match(p.run('lifecycleHTML([])'),/不能据此判断全部计划健康/);
  const html=p.run(`lifecycleHTML([{state:'stage_conflict',declared_stage:'unknown',plan_id:'<unsafe>',age_hours:null,is_current:false,daily_test_spend:0,cold_min_test_spend:null,budget:0,completion_3s:null,missing:['<missing>'],conflicts:['<conflict>']}])`);
  assert.match(html,/阶段证据冲突/);assert.match(html,/历史或证据待核对/);assert.match(html,/3秒完播率 未采集/);
  assert.ok(!html.includes('<unsafe>'));assert.match(html,/&lt;conflict&gt;/);assert.match(html,/&lt;missing&gt;/);
});

test('ROI tuning shows user threshold versus actual plan setting with decimal change',()=>{
  const p=page();const html=p.run(`recommendationHTML({action:'roi_target_down',priority:'P2',entity_id:'plan',before:3.5,after:3.32,change_pct:-.05142857142857143,eligible:false,blockers:['待复核'],evidence:{strategy_target_roi:2,plan_roi_configuration:{observed_plan_goal:3.5,step:.01,minimum:.01,maximum:100,after:3.32,actual_change_pct:-.05142857142857143}}})`);
  assert.match(html,/用户考核目标 2；计划当前设置 3.5/);assert.match(html,/ROI 目标 3.5 → 3.32/);
  assert.match(html,/-5.14%/);assert.match(html,/声明输入步长 0.01/);assert.match(html,/平台合同尚未认证/);
});

test('missing ROI configuration does not invent a new value or truncate six-place goals',()=>{
  const p=page();const html=p.run(`recommendationHTML({action:'roi_target_up',priority:'P1',entity_id:'<unsafe>',before:3.123456,after:null,change_pct:null,eligible:false,blockers:['<missing>'],evidence:{strategy_target_roi:null,plan_roi_configuration:{observed_plan_goal:3.123456,step:null,minimum:null,maximum:null,after:null}}})`);
  assert.match(html,/ROI 目标 3.123456 → 待核对/);assert.match(html,/用户考核目标 待核对/);assert.match(html,/未生成具体新值/);
  assert.ok(!html.includes('<unsafe>'));assert.ok(!html.includes('<missing>'));
});

test('official report display keeps query bounds and separate paid versus deal amounts',()=>{
  const p=page();const html=p.run(`officialReportHTML({account_id:'allowed',query:{adlab_scene:'OVERALL_PROJECT',data_period:'ALL_DATA',marketing_goal:'LIVE_PROM_GOODS',order_platform:'QIANCHUAN'},start_time:'2026-10-22T00:00:00+08:00',end_time:'2026-10-22T23:59:59+08:00',captured_at:'2026-10-22T08:00:00+08:00',data_at:null,values:[{label:'用户实际支付金额',value:320,unit:'元'},{label:'整体成交金额',value:400,unit:'元'},{label:'整体支付ROI',value:4.25,unit:'倍'}],limits:['不能推定ROI分子']})`);
  assert.match(html,/乘方计划/);assert.match(html,/整体期间/);assert.match(html,/千川PC/);assert.match(html,/320\.00/);assert.match(html,/400\.00/);assert.match(html,/4\.25/);
  assert.match(html,/未知更新时间保持未采集/);assert.match(html,/不能推定ROI分子/);
});

test('official report preserves raw rate units and missing or zero values',()=>{
  const p=page();const html=p.run(`officialReportHTML({values:[{label:'1小时内退款率原值（单位待核）',value:.05,unit:'来源原值'},{label:'整体消耗',value:0,unit:'元'},{label:'金额未返回',value:null,unit:'元'}]})`);
  assert.match(html,/0\.05 来源原值/);assert.match(html,/0\.00 元/);assert.match(html,/未采集 元/);assert.ok(!html.includes('0.05%'));
});

test('official preview remains unsaved and escapes source text',()=>{
  const p=page();const html=p.run(`officialPreviewHTML({received_reports:1,normalized_snapshots:1,preview:[{account_id:'<unsafe>',values:[{label:'<script>bad</script>',value:0,unit:'元'}],limits:['<img src=x onerror=bad>']}]})`);
  assert.match(html,/尚未保存/);assert.match(html,/未调用平台接口/);assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<img'));assert.match(html,/&lt;unsafe&gt;/);
});

test('official blank template cannot masquerade as collected data or credentials',()=>{
  const p=page();const result=JSON.parse(p.run(`JSON.stringify(officialReportTemplate({id:'allowed',scene:'product'}))`));
  assert.equal(result.format,'qianchuan_account_report_v1');assert.equal(result.reports[0].query.marketing_goal,'VIDEO_PROM_GOODS');
  assert.equal(result.reports[0].data_at,null);assert.deepEqual(result.reports[0].response.data,{});
  assert.match(result.reports[0].captured_at,/填写/);assert.ok(!JSON.stringify(result).includes('token'));
});

test('custom native report template has no invented dimensions, metrics, or collected data',()=>{
  const p=page(),out=JSON.parse(p.run(`JSON.stringify(customReportTemplate({id:'123',scene:'product'}))`));
  assert.equal(out.format,'qianchuan_custom_report_review_v1');assert.equal(out.query.advertiser_id,'123');
  assert.equal(out.query.data_topic,'SITE_PROMOTION_PRODUCT_AD');assert.deepEqual(out.query.dimensions,[]);
  assert.deepEqual(out.query.metrics,[]);assert.deepEqual(out.configuration.response.data.custom_config_datas,[]);
  assert.equal(out.data_at,null);assert.match(out.captured_at,/填写/);assert.deepEqual(out.pages[0].response.data.rows,[]);
});

test('custom native report preserves opaque cells, native units and missing values in escaped HTML',()=>{
  const p=page(),html=p.run(`customReviewHTML({account_id:'<unsafe>',topic_label:'素材',received_rows:2,reported_rows:2,received_pages:1,reported_pages:1,pagination_exhausted:true,missing_cells:1,columns:[{field:'id',name:'<script>x</script>',unit:'维度原值'},{field:'m',name:'原金额',unit:'分'}],rows:[{cells:{id:'{"value":8012367229158357}',m:'{"opaque_amount":0}'}},{cells:{id:'<img src=x>',m:null}}],limits:['不得换算']})`);
  assert.match(html,/8012367229158357/);assert.match(html,/分/);assert.match(html,/opaque_amount/);assert.match(html,/未返回/);
  assert.match(html,/不认证全账户覆盖/);assert.match(html,/尚未保存观察快照/);assert.match(html,/数据更新时间 未提供/);
  assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<img'));assert.ok(!html.includes('<unsafe>'));
});

test('custom native report preview clearly separates partial pages and caps rendering',()=>{
  const p=page(),html=p.run(`customReviewHTML({received_rows:51,reported_rows:100,received_pages:1,reported_pages:2,pagination_exhausted:false,columns:[{field:'m',name:'指标',unit:'百分比'}],rows:Array.from({length:51},(_,i)=>({cells:{m:'cell-'+i}}))})`);
  assert.match(html,/分页尚未读完/);assert.match(html,/前50行/);assert.ok(html.includes('cell-49'));assert.ok(!html.includes('cell-50'));
});

test('parent lifetime display does not equate watcher with scheduler or continuous coverage',()=>{
  const p=page(),html=p.run(`parentGuardHTML({mode:'parent_handle',watcher_alive:true})`);
  assert.match(html,/实际父进程退出后，本服务自行退出/);assert.match(html,/不证明外层后台任务正在运行/);
  assert.match(p.run(`parentGuardHTML({mode:'standalone',watcher_alive:false})`),/当前未确认启动/);
});

test('collection health includes actual parent watcher evidence separately from supervisor state',()=>{
  const p=page(),html=p.run(`collectionHealthHTML({accounts:[],sources:[]},{parent_guard:{mode:'parent_handle',watcher_alive:true}},'123')`);
  assert.match(html,/父进程退出保护/);assert.match(html,/不证明全量覆盖或7×24连续运行/);
});

test('creative pause renders boolean state instead of a numeric bid',()=>{
  const p=page(),html=p.run(`recommendationHTML({action:'pause_creative',scope:'creative',target_type:'creative_status',entity_id:'asset',priority:'P2',before:true,after:false,eligible:true,blockers:[],evidence:{spend_delta:0,clicks_delta:0},parameters:{parent_plan_id:'parent'}})`);
  assert.match(html,/状态 开启 → 关闭/);assert.ok(!html.includes('出价'));assert.match(html,/实际执行不可用/);
});

test('plan and task status previews preserve unknown state and do not imply a budget change',()=>{
  const p=page();for(const action of ['pause_plan','resume_plan','pause_control']){
    const html=p.run(`recommendationHTML({action:'${action}',entity_id:'<unsafe>',priority:'P1',before:true,after:null,eligible:false,blockers:['待核验']})`);
    assert.match(html,/状态 开启 → 未确认/);assert.ok(!html.includes('预算'));assert.ok(!html.includes('出价'));assert.ok(!html.includes('<unsafe>'));
  }
});

test('rotation registry template cannot be mistaken for reviewed assets or complete scope',()=>{
  const p=page(),out=JSON.parse(p.run(`JSON.stringify(creativeRotationTemplate({id:'123',scene:'product'}))`));
  const plan=out.plans[0];assert.equal(plan.account_id,'123');assert.equal(plan.inventory_complete,false);
  assert.equal(plan.multi_aweme,null);assert.equal(plan.pool[0].reviewed,false);assert.equal(plan.slots[0].material_id,'');
  assert.equal(plan.pool[0].native.video_id,'');assert.equal(plan.pool[0].product_id,'');
  assert.equal(out.duplicate_inventory[0].complete,false);assert.equal(out.duplicate_inventory[0].groups[0].active_count,null);
});

test('rotation preview renders long wire ID text without parsing or executing native JSON',()=>{
  const p=page(),html=p.run(`creativeRotationHTML({drafts:[{account_id:'123',plan_id:'2001',local_eligible:true,selection:{snapshot_id:'<unsafe>',old_material_id:'8012367229158357',candidate_id:'pool'},requests:{add:{documented_endpoint:'<path>',body_json:'{"aweme_item_id":8731940833522106597}',synthetic_only:true}},stages:[{action:'cooldown',minimum_seconds:3600}]}]})`);
  assert.match(html,/8012367229158357/);assert.match(html,/8731940833522106597/);assert.match(html,/至少 60 分钟/);
  assert.match(html,/合成示意，无执行地址/);assert.ok(!html.includes('<unsafe>'));assert.ok(!html.includes('<path>'));
});

test('rotation preview clearly labels blocked scope and unfinished workflow with escaped values',()=>{
  const p=page(),html=p.run(`creativeRotationHTML({drafts:[{account_id:'<script>',plan_id:'2001',local_eligible:false,blockers:['<img src=x>'],stages:[]}],limits:['<limit>']})`);
  assert.match(html,/持续编排与实际提交尚未接通/);assert.match(html,/阻断/);assert.match(html,/不会自动重试或解除锁定/);
  assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<img'));assert.ok(!html.includes('<limit>'));
});

test('workflow save is offered only for a qualified non-synthetic intent and never submits material',()=>{
  const p=page();let html=p.run(`creativeWorkflowSaveHTML({drafts:[{local_eligible:true,synthetic_only:false,intent_digest:'abc',account_id:'123',plan_id:'<plan>'},{local_eligible:true,synthetic_only:true,intent_digest:'synthetic',plan_id:'bad'},{local_eligible:false,synthetic_only:false,intent_digest:'blocked',plan_id:'bad'}]})`);
  assert.match(html,/本地待接通流程/);assert.match(html,/data-rotation-save="abc"/);assert.ok(!html.includes('<plan>'));
  assert.ok(!html.includes('synthetic'));assert.ok(!html.includes('blocked'));
});

test('workflow status keeps unknown holds and limits cancellation to before native integration',()=>{
  const p=page(),html=p.run(`creativeWorkflowsHTML({workflows:[{id:'<cancel>',account_id:'<account>',plan_id:'2001',state:'awaiting_native_integration',parent_reservation_held:true,group_reservation_held:true,version:1,journal:[{event:'<script>'}]},{id:'unknown',state:'add_unknown',parent_reservation_held:true,group_reservation_held:true,last_blockers:['<img>']}]})`);
  assert.match(html,/真实素材提交尚未接通/);assert.match(html,/添加结果未知，保持占用/);assert.match(html,/真实投放动作 0 次/);
  assert.equal((html.match(/data-workflow-cancel=/g)||[]).length,1);assert.ok(!html.includes('<cancel>'));assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<img>'));
});

test('durable replay labels synthetic completion and temporary storage instead of real execution',()=>{
  const p=page(),html=p.run(`creativeWorkflowReplayHTML({workflow:{state:'completed_synthetic',account_id:'999',plan_id:'2001'},journal:[],timeline:[{at:null,state:'<state>',version:9}],synthetic_calls:['add','retire'],note:'<note>'})`);
  assert.match(html,/全部合成/);assert.match(html,/临时库已清理，真实流程未改变/);assert.match(html,/合成流程完成/);assert.match(html,/合成动作 2 次/);
  assert.ok(!html.includes('<state>'));assert.ok(!html.includes('<note>'));
});

test('native material template contains no fake successful response target or timestamps',()=>{
  const p=page(),template=JSON.parse(p.run(`JSON.stringify(materialReadbackTemplate({id:'123',scene:'product'}))`));
  assert.equal(template.query.account_id,'123');assert.equal(template.query.plan_id,'');assert.equal(template.query.start_date,'');
  assert.equal(template.target.native.video_id,'');assert.equal(template.target.product_id,'');assert.equal(template.request_started_at,'');
  assert.equal(template.detail_response.code,null);assert.equal(template.material_responses[0].code,null);
});

test('material comparison distinguishes configuration from spend-filtered state and preserves exact ID strings',()=>{
  const p=page(),html=p.run(`materialReadbackHTML({account_id:'123',plan_id:'2001',configuration:{state:'present',modify_time_raw:'<clock>',notes:[]},received_pages:1,reported_pages:1,reported_rows:1,pagination_exhausted:true,matched_observations:1,material_observations:[{material_id:'8012367229158357',material_status:'<state>',audit_status:'PASS',is_delete:false,material_select_type:'CUSTOM'}]})`);
  assert.match(html,/返回配置含目标原生标识/);assert.match(html,/未命中不证明已删除/);assert.match(html,/不解除流程占用/);
  assert.match(html,/8012367229158357/);assert.match(html,/不认证平台状态时钟/);assert.ok(!html.includes('<clock>'));assert.ok(!html.includes('<state>'));
});

test('ambiguous material comparison escapes supplied notes and never labels a deletion successful',()=>{
  const p=page(),html=p.run(`materialReadbackHTML({configuration:{state:'ambiguous',notes:['<img>']},limits:['<script>'],material_observations:[],matched_observations:0,pagination_exhausted:false})`);
  assert.match(html,/配置存在范围冲突/);assert.match(html,/查询分页尚未到末页/);assert.match(html,/具体商品\/抖音用途的可用状态尚未认证/);
  assert.ok(!html.includes('<img>'));assert.ok(!html.includes('<script>'));assert.ok(!html.includes('删除成功'));
});

test('control creation demo preserves long IDs and separates acknowledgement from readiness',()=>{
  const p=page(),html=p.run(`controlCreationHTML({account_id:'9990001',parent_plan_id:'8012367229158357',request:{documented_endpoint:'<path>',body_json:'{"ad_id":8012367229158357}'},chase_blocker:'<unsafe> 长期有效'})`);
  assert.match(html,/8012367229158357/);assert.match(html,/初始预算100元、时长1小时/);assert.match(html,/没有执行地址/);
  assert.match(html,/新任务ID回执也不能代替可投状态核对/);assert.match(html,/默认关闭/);assert.match(html,/长期有效/);
  assert.ok(!html.includes('<path>'));assert.ok(!html.includes('<unsafe>'));
});

test('creation readback template carries no accepted task, body or native receipt',()=>{
  const p=page(),out=JSON.parse(p.run(`JSON.stringify(creationReadbackTemplate({id:'123',scene:'live'}))`));
  assert.equal(out.format,'qianchuan_control_creation_review_v1');assert.equal(out.attempt.context.account_id,'123');
  assert.equal(out.attempt.context.request_body_json,'');assert.equal(out.attempt.context.request_digest,'');
  assert.equal(out.attempt.result.created_task_id,null);assert.equal(out.attempt.result.business_accepted,false);
  assert.equal(out.attempt.result.creation_receipt.origin_verified,false);assert.deepEqual(out.raw_pages,[]);
});

test('creation readback preserves exact task identity and never turns file FROZEN into an unlock',()=>{
  const p=page(),html=p.run(`creationReadbackHTML({comparison:{account_id:'123',parent_plan_id:'8012367229158357',task_id:'8753264005940210',status:'matches_requested_configuration',reported_task_status:'FROZEN',observed_budget:'100.00',expected_budget:'100.00',observed_duration_hours:'1.0',expected_duration_hours:'1.0',pages_received:1,reported_total_pages:1,pagination_exhausted:true}})`);
  assert.match(html,/8012367229158357/);assert.match(html,/8753264005940210/);assert.match(html,/FROZEN/);
  assert.match(html,/不会解除占用或改变本机尝试状态/);assert.match(html,/未认证平台时钟/);
});

test('creation readback escapes supplied states clocks and blockers',()=>{
  const p=page(),html=p.run(`creationReadbackHTML({comparison:{task_id:'<task>',reported_task_status:'<script>',reported_create_time:'<clock>',blockers:['<img>']},limits:['<limit>'],note:'<note>'})`);
  for(const unsafe of ['<task>','<script>','<clock>','<img>','<limit>','<note>'])assert.ok(!html.includes(unsafe));
  assert.match(html,/&lt;task&gt;/);assert.match(html,/还有未读页/);
});

test('deadline demo distinguishes elapsed time from task identity and a successful stop',()=>{
  const p=page(),html=p.run(`controlDeadlineHTML({account_id:'9990001',parent_plan_id:'8012367229158357',material_id:'10001',duration_hours:2,task_id:null,timeline:[{due:true,note:'保持占用'}]})`);
  assert.match(html,/8012367229158357/);assert.match(html,/任务ID 未取得/);assert.match(html,/到期，仍需核对任务身份/);
  assert.match(html,/创建仍受阻/);assert.match(html,/不能据此认为可靠截止已经上线/);
});

test('deadline demo escapes notes and labels temporary storage without asserting a real stop',()=>{
  const p=page(),html=p.run(`controlDeadlineHTML({account_id:'<account>',task_id:'<task>',timeline:[{due:false,note:'<img>'}],note:'<script>'})`);
  for(const value of ['<account>','<task>','<img>','<script>'])assert.ok(!html.includes(value));
  assert.match(html,/临时库已清理/);assert.ok(!html.includes('关停成功'));
});

test('chase creation template has no bid approved material reviewed audience or invented clock',()=>{
  const p=page(),t=JSON.parse(p.run(`JSON.stringify(chaseCreationTemplate({id:'123'}))`));
  assert.equal(t.conversion_bid,null);assert.equal(t.material.audit_status,'');assert.equal(t.material.mapping_verified,false);
  assert.equal(t.material.is_delete,null);assert.equal(t.material.observed_at,'');assert.equal(t.targeting_review.reviewed,false);
  assert.deepEqual(t.audience.aweme_fan_accounts,[]);assert.equal(t.proposal_id,'');
});

test('chase creation renders exact long ID text and distinguishes assessment cost bid and local deadline',()=>{
  const p=page(),html=p.run(`chaseCreationHTML({prepared:true,draft:{material_id:'8753264005940210',body_json:'{"material_ids":[8753264005940210]}',budget:'100.00',conversion_bid:'10.00',account_assessment_target:'2.000001',requested_local_duration_hours:2},blockers:['<img>'],execution_blockers:['<script>']})`);
  assert.match(html,/8753264005940210/);assert.match(html,/2.000001/);assert.match(html,/独立成交出价 10.00/);
  assert.match(html,/接口不发送duration/);assert.match(html,/没有执行地址/);assert.match(html,/仍无执行资格/);
  assert.ok(!html.includes('<img>'));assert.ok(!html.includes('<script>'));
});
