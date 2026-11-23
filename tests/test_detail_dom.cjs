/* Anonymous, observed table shapes; no browser, transport, or account operations. */
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../extension/collector.js'),'utf8');
const account='1234567',plan='8339456325564332585';
const controlHeads=['','任务名称','素材','调控状态','调控预算(元)','净成交ROI目标','支付ROI目标','出价','调控时长(小时)','追投方式','创建时间','调控消耗','调控支付ROI','调控净成交金额','调控展示次数','调控点击次数','调控点击率','调控转化率','调控成交订单数','调控成交金额','调控净成交ROI'];
const controlValues=['','测试任务\nID: 8618530283142212632','测试素材\n素材ID：8533340473204213496','调控中','1,500.00','2.90','-','36.66','长期投放','控成本追投','2026-10-23\n07:14:44','881.69','2.61','1,778.87','3,489','194','5.56%','9.28%','18','2,298.47','2.02'];
const productHeads=['','商品','操作','审核状态','综合成本(元)','综合营销ROI','综合订单成本(元)','净成交金额(元)','整体消耗(元)','净成交订单数','1小时内退款率','整体成交金额(元)'];
const productValues=['','测试商品\n猜喜入选\nID: 8114449214728034245','编辑 删除','审核通过','1,476.85','2.42','50.93','3,567.10','1,476.85','29','6.79%','3,826.90'];
const visible=o=>Object.assign(o,{getClientRects:()=>[1]});
const leaf=(innerText,value)=>visible({children:[],innerText,closest:()=>null,parentElement:value===undefined?null:{innerText:innerText+'\n'+value,parentElement:null}});
const row=values=>visible({cells:values.map(innerText=>({innerText}))});
function tables(heads,values,extra=[]){
  const header=row(heads),summary=row(['共9条任务',...heads.slice(1).map(()=>'-')]);
  const headerTable=visible({rows:[header,summary],querySelectorAll:()=>[header,summary]});
  const bodyTable=visible({rows:[row(values),...extra.map(row)],querySelectorAll:()=>[]});
  return [headerTable,bodyTable];
}
function collect(options={}){
  const product=options.product===true,bodyTables=options.tables||tables(controlHeads,controlValues);
  const labels=options.labels||[leaf('计划ID：'+plan),leaf('千川可用余额(元)','1000'),leaf('整体消耗','9999'),leaf('整体支付ROI','9')];
  const dialogTables=options.dialogTables||tables(productHeads,productValues);
  const inputs=[visible({value:options.start||'2026-10-23',getAttribute:()=> '请选择开始日期'}),visible({value:options.end||options.start||'2026-10-23',getAttribute:()=> '请选择结束日期'})];
  const dialog=visible({querySelectorAll:s=>s==='*'?[leaf('ID：'+(options.parent||plan))]:s==='input'?inputs:s==='table'?dialogTables:[]});
  const cardLabels=(options.cardLabels||[]).map(([label,value])=>leaf(label,value));
  const cardInput=visible({checked:options.baseChecked===undefined?false:options.baseChecked,closest:()=>({innerText:'只看基础数据'})});
  const cardSection=visible({innerText:(options.cardLabels||[]).map(pair=>pair.join('\n')).join('\n'),
    parentElement:options.outerCardText?{innerText:options.outerCardText,parentElement:null}:null,
    querySelectorAll:s=>s==='h3'?[leaf('数据概览')]:s==='*'?cardLabels:
    s==='input[type="checkbox"]'?(options.missingCardToggle?[]:[cardInput]):[]});
  for(const label of cardLabels)label.parentElement.parentElement=cardSection;
  const body={innerText:'10-24 07:00更新\n预算(元)：\n每日500,000.00\n净成交ROI目标：\n3.00\n任务名称',querySelectorAll:s=>s==='table'?bodyTables:[]};
  if(options.bodyText!==undefined)body.innerText=options.bodyText;
  const ctx={URL,URLSearchParams,Date,Number,Set,getComputedStyle:()=>({visibility:'visible',display:'block'}),
    location:{href:`https://qianchuan.jinritemai.com/${product?'overall-prom':'uni-prom/detail'}?aavid=${account}&dr=2026-10-24,2026-10-24&adId=${options.urlPlan||plan}`+(options.query||'')},
    document:{body,querySelectorAll:s=>s==='body *'?labels:s==='table'?(product?[]:bodyTables):s==='[role="dialog"]'?(product?[dialog]:[]):
      s==='.overview-data-section'&&options.cardLabels?(options.duplicateCards?[cardSection,cardSection]:[cardSection]):[]}};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  return ctx.collectVisibleQianchuan({accountId:account,scene:product?'product':'live',path:product?'/overall-prom':'/uni-prom/detail',role:product?'product':'detail',planId:plan});
}

const logHeads=['操作时间','操作类型','操作内容','操作人','IP地址'];
const logValues=['2026-10-24 00:00:47','修改','操作内容：素材追投，ID：8344933447404222\n调控预算：1200.00元 -> 1000.00元','private operator','private IP'];
test('visible log columns retain explicit task budget values without operator or IP',()=>{
  const result=collect({tables:tables(logHeads,logValues)}),p=result.snapshots.find(s=>s.source.segment==='plan_operation_log');
  assert.equal(p.context.observed_plan_operations.length,1);
  assert.equal(result.diagnostics.visible_plans,1);assert.equal(result.diagnostics.visible_plan_operations,1);
  assert.deepEqual(JSON.parse(JSON.stringify(p.context.observed_plan_operations[0])),{
    occurred_at:'2026-10-24T00:00:47+08:00',kind:'control_budget_change',control_id:'8344933447404222',before_budget:1200,after_budget:1000});
  assert.equal(p.control.last_change_at,'2026-10-24T00:00:47+08:00');assert.equal(p.control.history_known,false);
  assert.equal(p.source.data_at,null);assert.deepEqual(JSON.parse(JSON.stringify(p.metrics)),{});
  assert.equal(result.snapshots.find(s=>s.scope==='plan'&&!s.source.segment).metrics.budget,500000);
  assert.ok(!JSON.stringify(result).includes('private'));
});

test('log rows retain zero, deduplicate repeated rows and preserve newest operation time',()=>{
  const older=[...logValues];older[0]='2026-10-23 23:59:47';older[2]=older[2].replace('1000.00','0.00');
  const p=collect({tables:tables(logHeads,logValues,[logValues,older])}).snapshots.find(s=>s.source.segment==='plan_operation_log');
  assert.equal(p.context.observed_plan_operations.length,2);assert.equal(p.context.observed_plan_operations[1].after_budget,0);
  assert.equal(p.control.last_change_at,'2026-10-24T00:00:47+08:00');
});

test('conflicting log rows are omitted rather than arbitrarily selecting budget values',()=>{
  const conflict=[...logValues];conflict[2]=conflict[2].replace('1000.00','900.00');
  const result=collect({tables:tables(logHeads,logValues,[conflict])}),p=result.snapshots.find(s=>s.scope==='plan');
  assert.equal(p.context.observed_plan_operations,undefined);assert.equal(p.control.last_change_at,undefined);
  assert.ok(result.diagnostics.issues.some(x=>x.includes('操作日志前后值冲突')));
});

test('future malformed or unsupported logs never establish an observed change',()=>{
  const variants=[{at:'2099-01-01 00:00:00'},{at:'2026-02-30 00:00:00'},
    {type:'查看'},{content:logValues[2]+'\n操作人：private'},
    {content:logValues[2].replace('素材追投','一键起量')},{content:logValues[2].replace('1200.00','-1200.00')},
    {content:logValues[2].replace('1200.00','1,20.00')}];
  for(const variant of variants){const values=[...logValues];values[0]=variant.at||values[0];values[1]=variant.type||values[1];values[2]=variant.content||values[2];
    const p=collect({tables:tables(logHeads,values)}).snapshots.find(s=>s.scope==='plan');
    assert.equal(p.context.observed_plan_operations,undefined);assert.equal(p.control.last_change_at,undefined);
  }
});

test('unrelated own headers and conflicting split headers do not lend log column meanings',()=>{
  const foreign=visible({rows:[row(logValues)],querySelectorAll:()=>[row(['a','b','c','d','e'])]});
  let p=collect({tables:[tables(logHeads,logValues)[0],foreign]}).snapshots.find(s=>s.scope==='plan');
  assert.equal(p.context.observed_plan_operations,undefined);
  const second=tables(['操作类型','操作时间','操作内容','操作人','IP地址'],logValues)[0];
  p=collect({tables:[tables(logHeads,logValues)[0],second,tables(logHeads,logValues)[1]]}).snapshots.find(s=>s.scope==='plan');
  assert.equal(p.context.observed_plan_operations,undefined);
});

test('product account tables cannot borrow the live plan operation log reader',()=>{
  const result=collect({product:true,tables:tables(logHeads,logValues)});
  assert.ok(result.snapshots.every(s=>!s.context?.observed_plan_operations));
});

test('log-only view needs no fabricated budget or performance number to retain its rows',()=>{
  const result=collect({tables:tables(logHeads,logValues),bodyText:'操作日志'});
  const log=result.snapshots.find(s=>s.source.segment==='plan_operation_log');
  assert.ok(log);assert.deepEqual(JSON.parse(JSON.stringify(log.metrics)),{});assert.equal(log.metric_basis,'unknown');
  assert.equal(log.source.data_at,null);assert.equal(log.context.observed_plan_operations.length,1);
  assert.equal(result.snapshots.filter(s=>s.scope==='plan').length,1);
});
test('control details keep exact IDs, own payment ledger and separate ROI goals',()=>{
  const value=collect(),c=value.snapshots.find(x=>x.scope==='control');
  assert.equal(value.diagnostics.visible_controls,1);assert.equal(c.entity_id,'8618530283142212632');
  assert.equal(c.source.parent_entity_id,plan);assert.equal(c.context.parent_plan_id,plan);
  assert.equal(c.context.material_id,'8533340473204213496');assert.equal(c.metric_basis,'control_payment');
  assert.equal(c.metrics.spend,881.69);assert.equal(c.metrics.roi,2.61);assert.equal(c.metrics.net_roi,2.02);
  assert.equal(c.metrics.net_roi_goal,2.9);assert.equal(c.metrics.payment_roi_goal,undefined);assert.equal(c.metrics.target_roi,undefined);
  assert.equal(c.metrics.budget,1500);assert.equal(c.metrics.bid,36.66);assert.equal(c.metrics.ctr,5.56);assert.equal(c.metrics.cvr,9.28);
  assert.equal(c.context.created_at,'2026-10-23T07:14:44+08:00');assert.equal(c.source.metric_date,'2026-10-24');
  assert.equal(c.context.control_type,'unknown');assert.equal(c.context.control_delivery_mode,'cost_control');
  assert.equal(c.context.observed_duration_mode,'unlimited');assert.equal(c.context.observed_duration_hours,undefined);
  assert.equal(c.control.running,true);assert.equal(c.control.history_known,false);
});
test('detail-page performance never leaks into an account performance snapshot',()=>{
  const a=collect().snapshots.find(x=>x.scope==='account');assert.equal(a.metrics.balance,1000);
  assert.equal(a.metrics.spend,undefined);assert.equal(a.metrics.roi,undefined);assert.equal(a.metric_basis,'unknown');
  const p=collect().snapshots.find(x=>x.scope==='plan');assert.equal(p.entity_id,plan);
  assert.equal(p.metrics.budget,500000);assert.equal(p.metrics.net_roi_goal,3);assert.equal(p.metrics.target_roi,undefined);
  assert.equal(p.context.plan_roi_basis,'net');assert.equal(p.context.plan_roi_config_verified,null);assert.equal(p.metrics.balance,undefined);
});
test('detail header clock is provenance only for balance configuration and task rows',()=>{
  const result=collect();
  for(const scope of ['account','plan','control']){
    const row=result.snapshots.find(s=>s.scope===scope);
    assert.equal(row.source.data_at,null);
    assert.equal(row.source.reported_detail_header_data_at,'2026-10-23T23:00:00.000Z');
    assert.ok(row.quality.issues.includes('直播计划详情顶部时间不能认证明细独立指标更新时间'));
  }
  assert.ok(result.snapshots.find(s=>s.scope==='plan').context.plan_roi_observed_at);
});
test('changed task counters do not obtain a new data clock from an unchanged header',()=>{
  const before=collect().snapshots.find(s=>s.scope==='control');
  const values=[...controlValues];values[11]='901.99';
  const after=collect({tables:tables(controlHeads,values)}).snapshots.find(s=>s.scope==='control');
  assert.notEqual(before.metrics.spend,after.metrics.spend);
  assert.equal(after.source.data_at,null);assert.equal(before.source.data_at,null);
  assert.equal(after.source.reported_detail_header_data_at,before.source.reported_detail_header_data_at);
});
const planCards=[['整体消耗(元)','120.00'],['整体支付ROI','2\n.50'],['整体成交金额(元)','300.00'],
  ['净成交ROI','2.00'],['净成交金额(元)','240.00'],['整体成交订单数','3'],['净成交订单数','2']];
test('scoped detail cards belong to the verified plan and never to its account balance snapshot',()=>{
  const result=collect({cardLabels:planCards}),p=result.snapshots.find(s=>s.scope==='plan'),a=result.snapshots.find(s=>s.scope==='account');
  assert.equal(p.entity_id,plan);assert.equal(p.metric_basis,'overall_payment');assert.equal(p.metrics.spend,120);assert.equal(p.metrics.roi,2.5);
  assert.equal(p.metrics.gmv,300);assert.equal(p.metrics.net_roi,2);assert.equal(p.metrics.net_gmv,240);
  assert.equal(p.metrics.orders,3);assert.equal(p.metrics.payment_orders,3);assert.equal(p.metrics.net_orders,2);
  assert.equal(p.metrics.budget,500000);assert.equal(p.metrics.net_roi_goal,3);assert.equal(p.metrics.target_roi,undefined);
  assert.equal(p.source.segment,'plan_card_overall');assert.equal(p.source.data_at,null);
  assert.equal(a.metrics.balance,1000);assert.equal(a.metrics.roi,undefined);assert.equal(a.metrics.spend,undefined);
});
test('base-only detail cards are separately segmented and cannot fill overall counters or ROI',()=>{
  const p=collect({cardLabels:planCards,baseChecked:true}).snapshots.find(s=>s.scope==='plan');
  assert.equal(p.source.segment,'plan_card_base');assert.equal(p.metrics.base_spend,120);assert.equal(p.metrics.base_roi,2.5);
  assert.equal(p.metrics.base_gmv,300);assert.equal(p.metrics.base_net_gmv,240);assert.equal(p.metrics.base_orders,3);
  for(const field of ['spend','roi','gmv','net_gmv','orders','balance'])assert.equal(p.metrics[field],undefined);
  assert.equal(p.metrics.budget,500000);assert.equal(p.source.data_at,null);
});
test('missing ambiguous or unknown base toggles keep configuration without borrowing unscoped cards',()=>{
  for(const options of [{missingCardToggle:true},{duplicateCards:true},{baseChecked:null}]){
    const p=collect({cardLabels:planCards,...options}).snapshots.find(s=>s.scope==='plan');
    assert.equal(p.metrics.budget,500000);assert.equal(p.metrics.spend,undefined);assert.equal(p.metrics.roi,undefined);
    assert.equal(p.source.segment,undefined);assert.equal(p.metric_basis,'unknown');
  }
});
test('detail cards retain matching order basis and do not coerce invalid counts or refunds',()=>{
  const cards=planCards.filter(([label])=>label!=='整体支付ROI').map(([label,value])=>[label,label==='整体成交订单数'?'3.5':value]);
  const p=collect({cardLabels:[...cards,['1小时内退款率','101%']]}).snapshots.find(s=>s.scope==='plan');
  assert.equal(p.metric_basis,'net');assert.equal(p.metrics.orders,2);assert.equal(p.metrics.payment_orders,undefined);
  assert.equal(p.metrics.refund_rate_1h,undefined);
});
test('ambiguous scoped card values remain missing and do not fall back to page-wide performance',()=>{
  const p=collect({cardLabels:[...planCards,['整体消耗(元)','999.00']]}).snapshots.find(s=>s.scope==='plan');
  assert.equal(p.metrics.spend,undefined);assert.equal(p.metrics.roi,2.5);assert.equal(p.metric_basis,'unknown');
  assert.ok(p.quality.issues.some(x=>x.includes('多个不同值')));
});
test('missing card values cannot climb outside the overview section and borrow another scope',()=>{
  const cards=planCards.map(([label,value])=>[label,label==='整体消耗(元)'?'-':value]);
  const p=collect({cardLabels:cards,outerCardText:'整体消耗(元)\n9999.00'}).snapshots.find(s=>s.scope==='plan');
  assert.equal(p.metrics.spend,undefined);assert.equal(p.metric_basis,'unknown');assert.equal(p.metrics.roi,2.5);
});
test('visible plan identity must agree with pre-read URL and cannot be ambiguous',()=>{
  assert.match(collect({urlPlan:'7777777'}).error,/身份/);
  assert.match(collect({labels:[leaf('计划ID：7777777')]}).error,/计划详情/);
  assert.match(collect({labels:[leaf('计划ID：'+plan),leaf('计划ID：7777777')]}).error,/计划详情/);
  assert.match(collect({query:'&aavid=7654321'}).error,/身份/);
});
test('a row with only a generic ID does not certify a material; fixed duration is distinct from unlimited',()=>{
  const values=[...controlValues];values[2]='某账号\nID：1234567890';values[8]='2.50';values[9]='放量追投';
  const c=collect({tables:tables(controlHeads,values)}).snapshots.find(x=>x.scope==='control');
  assert.equal(c.context.material_id,undefined);assert.equal(c.context.observed_duration_mode,'fixed');
  assert.equal(c.context.observed_duration_hours,2.5);assert.equal(c.context.control_delivery_mode,'volume');
});
test('task values do not become zeros for missing cells and invalid counts or percent values',()=>{
  const values=[...controlValues];values[5]='-';values[12]='-';values[15]='1.2';values[16]='101%';values[18]='0';
  const c=collect({tables:tables(controlHeads,values)}).snapshots.find(x=>x.scope==='control');
  assert.equal(c.metrics.net_roi_goal,undefined);assert.equal(c.metrics.roi,undefined);assert.equal(c.metrics.clicks,undefined);
  assert.equal(c.metrics.ctr,undefined);assert.equal(c.metrics.orders,0);assert.equal(c.metric_basis,'unknown');
});
test('duplicate task rows conflict instead of arbitrarily choosing metrics or state',()=>{
  const values=[...controlValues];values[11]='999.00';
  const value=collect({tables:tables(controlHeads,controlValues,[values])});
  assert.equal(value.diagnostics.visible_controls,0);assert.ok(value.diagnostics.issues.some(x=>x.includes('冲突')));
});
test('product drawers have independent dates, parent IDs, marketing basis and missing clocks',()=>{
  const value=collect({product:true}),p=value.snapshots.find(x=>x.scope==='product');
  assert.equal(value.diagnostics.visible_products,1);assert.equal(p.entity_id,'8114449214728034245');
  assert.equal(p.context.parent_plan_id,plan);assert.equal(p.source.parent_entity_id,plan);
  assert.equal(p.source.metric_date,'2026-10-23');assert.equal(p.source.data_at,null);assert.equal(p.source.metric_period,'day');
  assert.equal(p.metric_basis,'marketing');assert.equal(p.metrics.marketing_roi,2.42);assert.equal(p.metrics.roi,undefined);
  assert.equal(p.metrics.net_orders,29);assert.equal(p.metrics.payment_orders,undefined);assert.equal(p.metrics.stock,undefined);
  assert.equal(p.context.product_plan_mapping_verified,null);assert.equal(p.context.product_metrics_verified,null);
});
test('ambiguous, multi-day or invalid product periods never inherit the account date',()=>{
  for(const options of [{start:'2026-10-22',end:'2026-10-24'},{start:'2026-02-30'},{parent:'invalid'}]){
    const value=collect({product:true,...options});assert.equal(value.diagnostics.visible_products,0);
    assert.ok(value.diagnostics.issues.some(x=>x.includes('统计日期')));
  }
});
test('a product aggregate header and another plan cannot be read as product records',()=>{
  const changed=[...productValues];changed[1]='共1个商品';
  assert.equal(collect({product:true,dialogTables:tables(productHeads,changed)}).diagnostics.visible_products,0);
});
