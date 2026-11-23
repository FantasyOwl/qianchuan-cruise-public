/* Run with node --test tests/test_extension.cjs. No browser or account is accessed. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.resolve(__dirname,'../extension');
const code=fs.readFileSync(path.join(root,'collector.js'),'utf8');
const acct='1999999999999002';

function context({query='?aavid='+acct+'&dr=2026-10-22,2026-10-22',labels=[['整体消耗(元)','1,234.50'],['整体成交金额(元)','2,469.00'],['整体支付ROI','2.00']],selected=[],text='10-22 00:15更新\n今日直播',tables=[]}={}){
  function el(label,value){
    const parent={innerText:`${label}\n${value}`,parentElement:null};
    return{children:[],innerText:label,parentElement:parent,closest:()=>null,getClientRects:()=>[1]};
  }
  const leaves=labels.map(([k,v])=>el(k,v));
  const dates=selected.map(text=>({innerText:text,getClientRects:()=>[1]}));
  const ctx={URL,URLSearchParams,Intl,Date,Number,Set,getComputedStyle:()=>({visibility:'visible',display:'block'}),
    location:{href:'https://qianchuan.jinritemai.com/uni-prom'+query},
    document:{body:{innerText:text},querySelectorAll:selector=>selector==='body *'?leaves:selector==='table'?tables:dates}};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  return ctx;
}

test('reads explicitly labelled amounts with a verified account and selected date',()=>{
  const ctx=context();const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'});
  assert.equal(value.snapshot.metrics.spend,1234.5);assert.equal(value.snapshot.metrics.roi,2);
  assert.equal(value.snapshot.metric_basis,'overall_payment');assert.equal(value.snapshot.source.metric_date,'2026-10-22');
});
test('a generic today navigation label does not establish a statistics date',()=>{
  const ctx=context({query:'?aavid='+acct});
  const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'});
  assert.match(value.error,/统计日期/);
});
test('fractional spans in current Qianchuan cards are joined without losing precision',()=>{
  const ctx=context({labels:[['整体消耗','96,573\n.60'],['综合成本','96,573\n.60'],['综合营销ROI','3\n.37'],['净成交金额','325,828\n.10']]});
  const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'});
  assert.equal(value.snapshot.metrics.spend,96573.6);assert.equal(value.snapshot.metrics.marketing_roi,3.37);
  assert.equal(value.snapshot.metric_basis,'marketing');assert.equal(value.snapshot.metrics.roi,undefined);
});
test('a cross-midnight board is preserved as a live session and is not labelled daily',()=>{
  const ctx=context({query:'?aavid='+acct,text:'开播时间：2026-10-21 06:04:49\n今日直播'});
  const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'board',scene:'live'});
  assert.equal(value.snapshot.source.metric_period,'live_session');
  assert.equal(value.snapshot.source.metric_date,'2026-10-21');
  assert.equal(value.snapshot.source.period_start,'2026-10-21T06:04:49+08:00');
});

test('a deal-order label does not establish created orders or paid orders',()=>{
  const ctx=context({labels:[['整体消耗','100'],['成交订单数','7']],text:'开播时间：2026-10-21 06:04:49'});
  const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'board',scene:'live'});
  assert.equal(value.snapshot.metrics.funnel_orders,7);
  assert.equal(value.snapshot.metrics.funnel_created_orders,undefined);
  assert.equal(value.snapshot.metrics.funnel_payments,undefined);
});
test('two different values for the same metric fail closed',()=>{
  const ctx=context({labels:[['整体消耗(元)','100.00'],['整体消耗(元)','200.00'],['整体成交金额(元)','400.00']]});
  const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'});
  assert.equal(value.snapshot.metrics.spend,undefined);assert.equal(value.snapshot.metric_basis,'unknown');
});

function withModalCards(ctx,values){
  const prior=ctx.document.querySelectorAll;
  const modal=values.map(([label,value])=>({children:[],innerText:label,
    parentElement:{innerText:label+'\n'+value,parentElement:null},
    closest:selector=>selector.includes('[role="dialog"]')||selector.includes('[aria-modal="true"]')?{}:null,
    getClientRects:()=>[1]}));
  ctx.document.querySelectorAll=selector=>selector==='body *'?[...prior(selector),...modal]:prior(selector);
  return ctx;
}

test('plan drawer values do not remove or replace the page account overview',()=>{
  const ctx=withModalCards(context({labels:[['整体消耗','988.26'],['综合成本','988.26'],['净成交金额','2934.69'],['综合营销ROI','2.97']]}),
    [['整体消耗','122.78'],['综合成本','135.35'],['净成交金额','156.90'],['综合营销ROI','1.28']]);
  const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'}).snapshot;
  assert.equal(value.metrics.spend,988.26);assert.equal(value.metrics.comprehensive_cost,988.26);
  assert.equal(value.metrics.marketing_roi,2.97);assert.equal(value.metrics.net_gmv,2934.69);
  assert.equal(value.metric_basis,'marketing');assert.ok(!value.quality.issues.some(s=>s.includes('多个不同值')));
});

test('a drawer alone cannot establish missing account performance',()=>{
  const ctx=withModalCards(context({labels:[['千川可用余额(元)','1000']]}),
    [['整体消耗','122.78'],['整体成交金额','156.90'],['整体支付ROI','1.28']]);
  const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'}).snapshot;
  assert.equal(value.metrics.balance,1000);assert.equal(value.metrics.spend,undefined);
  assert.equal(value.metrics.gmv,undefined);assert.equal(value.metrics.roi,undefined);assert.equal(value.metric_basis,'unknown');
});

test('equal amounts cannot let a drawer supply the missing account ROI and amount',()=>{
  const ctx=withModalCards(context({labels:[['整体消耗','100']]}),
    [['整体消耗','100'],['整体成交金额','300'],['整体支付ROI','3']]);
  const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'}).snapshot;
  assert.equal(value.metrics.spend,100);assert.equal(value.metrics.roi,undefined);assert.equal(value.metrics.gmv,undefined);
  assert.equal(value.metric_basis,'unknown');
});

test('a selected drawer date cannot establish an unidentified page statistics date',()=>{
  const ctx=context({query:'?aavid='+acct}),prior=ctx.document.querySelectorAll;
  ctx.document.querySelectorAll=selector=>selector==='body *'||selector==='table'?prior(selector):[
    {value:'2026-10-25',getClientRects:()=>[1],closest:()=>({})}];
  assert.match(ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'}).error,/统计日期/);
});
test('a changed account is rejected before any document is read',()=>{
  const ctx=context({query:'?aavid=999'});Object.defineProperty(ctx.document,'body',{get(){throw new Error('must not read');}});
  assert.match(ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'}).error,/身份/);
});
function planTables(spend='120.00\n基础 100.00',marketingGoal=null){
  const heads=['计划','投放状态','投放设置','投放类型','综合营销ROI','整体消耗(元)','综合成本(元)','净成交金额(元)','净成交订单数','整体成交订单数','整体成交金额(元)'];
  const values=['测试计划\nID：8923217003292041','投放中\n保','每日预算\n50000元\n综合营销ROI目标\n4.50','乘方投放','3.00\n基础 3.00',spend,'120.00\n基础 100.00','360.00\n基础 300.00','12\n基础 10','15\n基础 13','450.00\n基础 390.00'];
  if(marketingGoal!==null){heads.push('营销目标');values.push(marketingGoal);}
  const row={cells:values.map(innerText=>({innerText})),getClientRects:()=>[1]};
  return [{rows:[],querySelectorAll:()=>heads.map(innerText=>({innerText}))},{rows:[row],querySelectorAll:()=>[]}];
}
test('visible plan rows retain separate net and payment ledgers, budgets and status',()=>{
  const ctx=context({tables:planTables()});
  const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'});
  assert.equal(value.snapshots.length,2);
  assert.notEqual(value.snapshot,value.snapshots[0]);
  assert.deepEqual(JSON.parse(JSON.stringify(value.snapshot)),JSON.parse(JSON.stringify(value.snapshots[0])));
  const p=value.snapshots[1];assert.equal(p.scope,'plan');assert.equal(p.promotion_type,'chengfang');
  assert.equal(p.metrics.spend,120);assert.equal(p.metrics.base_spend,100);
  assert.equal(p.metrics.orders,12);assert.equal(p.metrics.base_orders,10);assert.equal(p.metrics.payment_orders,15);
  assert.equal(p.metrics.base_gmv,390);assert.equal(p.metrics.base_net_gmv,300);
  assert.equal(p.metrics.budget,50000);assert.equal(p.metrics.target_roi,4.5);assert.equal(p.control.running,true);
  assert.equal(p.metric_basis,'marketing');assert.equal(p.metrics.roi,undefined);
  assert.equal(p.context.plan_roi_basis,'marketing');assert.equal(p.context.plan_roi_mode,'roi_target');
  assert.equal(p.context.plan_roi_observed_at,p.source.captured_at);assert.equal(p.context.plan_roi_config_verified,null);
  assert.equal(p.context.plan_roi_step,undefined);
  assert.equal(p.context.plan_budget_period,'day');assert.equal(p.context.plan_budget_period_verified,null);
});
test('inconsistent duplicate plan tables are excluded rather than picked arbitrarily',()=>{
  const a=planTables(),b=planTables('999.00\n基础 100.00');
  const ctx=context({tables:[...a,b[1]]});
  const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'});
  assert.equal(value.diagnostics.visible_plans,0);assert.equal(value.diagnostics.issues.length,1);
});

test('plan rows never inherit the independently progressing account overview clock',()=>{
  for(const page of ['/uni-prom','/overall-prom']){
    const ctx=context({tables:planTables()});ctx.location.href=ctx.location.href.replace('/uni-prom',page);
    const value=ctx.collectVisibleQianchuan({accountId:acct,path:page,role:'live',scene:'live'});
    assert.ok(value.snapshot.source.data_at);
    const plan=value.snapshots.find(s=>s.scope==='plan');assert.equal(plan.source.data_at,null);
    assert.equal(plan.metrics.spend,120);assert.equal(plan.metrics.base_spend,100);
    assert.ok(plan.quality.issues.some(x=>x.includes('账户概览时间不能代替计划时间')));
    assert.equal(plan.context.plan_roi_observed_at,plan.source.captured_at);
  }
});

test('explicit plan marketing goal and status time are not inferred from the account role',()=>{
  for(const [label,goal] of [['推直播间','LIVE_PROM_GOODS'],['推商品','VIDEO_PROM_GOODS']]){
    const ctx=context({tables:planTables(undefined,label)});
    const value=ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'}).snapshots[1];
    assert.equal(value.context.plan_marketing_goal,goal);assert.equal(value.context.plan_status_at,value.source.captured_at);
    assert.equal(value.context.plan_delivery_state,'unknown');
  }
  const ctx=context({tables:planTables()});
  assert.equal(ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'}).snapshots[1].context.plan_marketing_goal,'unknown');
});

function collectPlans(tables){
  const ctx=context({tables});
  return ctx.collectVisibleQianchuan({accountId:acct,path:'/uni-prom',role:'live',scene:'live'}).snapshots.filter(s=>s.scope==='plan');
}
test('native product subview is retained on both overview and plan rows',()=>{
 const ctx=context({query:'?aavid='+acct+'&dateRange=2026-10-26,2026-10-26#?uniVideoTab=2',tables:planTables()});
 ctx.location.href=ctx.location.href.replace('/uni-prom','/overall-prom');
 const data=ctx.collectVisibleQianchuan({accountId:acct,path:'/overall-prom',role:'product',scene:'product'});
 assert.equal(data.snapshot.context.observed_plan_view,'2');
 assert.equal(data.snapshots.find(s=>s.scope==='plan').context.observed_plan_view,'2');
});
test('plan numeric columns retain all visible ROI, paid amounts, net order cost and traffic fields separately',()=>{
 const heads=['计划','投放状态','投放设置','整体消耗(元)','整体支付ROI','净成交ROI','综合营销ROI','用户实际支付金额(元)','用户实际支付净成交金额(元)','净成交订单成本(元)','净成交金额结算率','整体点击率','整体转化率','整体点击次数','整体展示次数'];
 const values=['计划甲\nID：8923217003292041','投放中','每日预算\n50000元','100.00','3.20','2.90','2.70','310.00','280.00','10.00','90.00%','2.00%','5.00%','20','1000'];
 const table={rows:[{cells:values.map(innerText=>({innerText})),getClientRects:()=>[1]}],querySelectorAll:()=>heads.map(innerText=>({innerText}))};
 const p=collectPlans([table])[0];
 assert.equal(p.metrics.roi,3.2);assert.equal(p.metrics.net_roi,2.9);assert.equal(p.metrics.marketing_roi,2.7);
 assert.equal(p.metrics.official_actual_paid,310);assert.equal(p.metrics.official_actual_net_paid,280);
 assert.equal(p.metrics.net_conversion_cost,10);assert.equal(p.metrics.net_settlement_rate,90);
 assert.equal(p.metrics.ctr,2);assert.equal(p.metrics.cvr,5);assert.equal(p.metrics.clicks,20);assert.equal(p.metrics.impressions,1000);
});
test('missing ROI keeps the unique native column basis while the value remains absent',()=>{
  const tables=planTables();tables[1].rows[0].cells[4].innerText='-\n基础 -';
  tables[1].rows[0].cells[7].innerText='0.00\n基础 0.00';
  tables[1].rows[0].cells[8].innerText='0\n基础 0';
  const plan=collectPlans(tables)[0];
  assert.equal(plan.metric_basis,'marketing');assert.equal(plan.metrics.marketing_roi,undefined);
  assert.equal(plan.metrics.base_marketing_roi,undefined);assert.equal(plan.metrics.orders,0);
  assert.equal(plan.metrics.base_orders,0);assert.equal(plan.metrics.target_roi,4.5);
  assert.equal(plan.context.plan_roi_basis,'marketing');assert.equal(plan.source.data_at,null);
});
test('a later numeric ROI does not change the same uniquely defined plan basis',()=>{
  const missing=planTables();missing[1].rows[0].cells[4].innerText='--';
  const first=collectPlans(missing)[0],later=collectPlans(planTables())[0];
  assert.equal(first.entity_id,later.entity_id);assert.equal(first.metric_basis,later.metric_basis);
  assert.equal(first.source.url,later.source.url);assert.equal(first.metrics.marketing_roi,undefined);
  assert.equal(later.metrics.marketing_roi,3);
});
test('observed ROI setting basis is independent from the selected performance column',()=>{
  const tables=planTables();tables[1].rows[0].cells[2].innerText='每日预算\n50000元\n净成交ROI目标\n3.85';
  const plan=collectPlans(tables)[0];assert.equal(plan.metric_basis,'marketing');
  assert.equal(plan.metrics.target_roi,3.85);assert.equal(plan.metrics.net_roi_goal,3.85);
  assert.equal(plan.metrics.payment_roi_goal,undefined);assert.equal(plan.context.plan_roi_basis,'net');
  assert.equal(plan.context.plan_roi_config_verified,null);
});
test('ambiguous ROI settings do not borrow a performance value as the plan setting',()=>{
  for(const extra of ['净成交ROI目标\n3.85','综合营销ROI目标\n4.60']){
    const tables=planTables();tables[1].rows[0].cells[2].innerText+='\n'+extra;
    const plan=collectPlans(tables)[0];assert.equal(plan.metric_basis,'marketing');
    assert.equal(plan.metrics.target_roi,undefined);assert.equal(plan.context.plan_roi_basis,undefined);
  }
});
function ownTable(heads,values){
  return {rows:[{cells:values.map(innerText=>({innerText})),getClientRects:()=>[1]}],
    querySelectorAll:selector=>selector==='thead th'?heads.map(innerText=>({innerText})):[]};
}
function planShape(){
  const tables=planTables();return {heads:tables[0].querySelectorAll().map(e=>e.innerText),
    values:tables[1].rows[0].cells.map(e=>e.innerText)};
}
test('two missing ROI columns remain ambiguous even when one plan setting is explicit',()=>{
  const {heads,values}=planShape();values[4]='-';heads.push('整体支付ROI');values.push('-');
  const plan=collectPlans([ownTable(heads,values)])[0];assert.equal(plan.metric_basis,'unknown');
  assert.equal(plan.metrics.orders,undefined);assert.equal(plan.metrics.marketing_roi,undefined);
  assert.equal(plan.metrics.roi,undefined);assert.equal(plan.context.plan_roi_basis,'marketing');
});
test('equal-width tables use their own headings instead of the first table headings',()=>{
  const a=planShape(),b=planShape();[b.heads[4],b.heads[8]]=[b.heads[8],b.heads[4]];
  [b.values[4],b.values[8]]=[b.values[8],b.values[4]];b.values[0]='第二计划\nID：8333124404530653';
  const plans=collectPlans([ownTable(a.heads,a.values),ownTable(b.heads,b.values)]);
  assert.equal(plans.length,2);assert.equal(plans[1].metrics.marketing_roi,3);assert.equal(plans[1].metrics.orders,12);
});
test('a headerless body with conflicting equal-width headings is not assigned arbitrarily',()=>{
  const a=planTables(),b=planShape();[b.heads[4],b.heads[8]]=[b.heads[8],b.heads[4]];
  const secondHeader={rows:[],querySelectorAll:()=>b.heads.map(innerText=>({innerText}))};
  assert.equal(collectPlans([a[0],a[1],secondHeader]).length,0);
});
test('an unrelated table with its own headers never borrows plan headings of equal width',()=>{
  const header=planTables()[0],foreign=planShape();foreign.heads[0]='不相关名称';
  assert.equal(collectPlans([header,ownTable(foreign.heads,foreign.values)]).length,0);
});
test('background collection only injects into matching group, accounts and overview pages',async()=>{
  const tabs=[
    {id:1,groupId:10,url:`https://qianchuan.jinritemai.com/uni-prom?aavid=${acct}`},
    {id:2,groupId:20,url:`https://qianchuan.jinritemai.com/uni-prom?aavid=${acct}`},
    {id:3,groupId:10,url:'https://qianchuan.jinritemai.com/uni-prom?aavid=999'},
    {id:4,groupId:10,url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${acct}&adId=123`},
    {id:5,groupId:10,url:`https://qianchuan.jinritemai.com/board-next?aavid=${acct}`},
    {id:6,groupId:10,url:'https://qianchuan.jinritemai.com/overall-prom?aavid=1999999999999001'}];
  const injected=[], requests=[];
  const event={addListener:()=>{}};
  const ctx={URL,Map,Set,Date,AbortSignal,console,chrome:{
    storage:{local:{get:async()=>({token:'t'.repeat(43),enabled:true}),set:async()=>{}}},
    permissions:{contains:async()=>true},
    tabGroups:{query:async()=>[{id:10,title:'示例分组A'}],get:async id=>({id,title:id===10?'示例分组A':'其他'})},
    tabs:{query:async()=>tabs,get:async id=>tabs.find(t=>t.id===id)},
    scripting:{executeScript:async opts=>{injected.push(opts.target.tabId);return[{result:{snapshot:{account_id:opts.args[0].accountId}}}];}},
    action:{setBadgeText:async()=>{},setBadgeBackgroundColor:async()=>{}},alarms:{create:async()=>{},onAlarm:event},
    runtime:{onInstalled:event,onStartup:event,onMessage:event}},
    fetch:async(url,options)=>{if(url.endsWith('/api/collector-config'))return{ok:true,json:async()=>({protocol:2,revision:'a'.repeat(64),accounts:[{id:acct,scene:'live',tab_group:'示例分组A'},{id:'1999999999999001',scene:'product',tab_group:'示例分组A'}]})};requests.push(url);return{ok:true,json:async()=>({ok:true})};}};
  ctx.importScripts=(...names)=>names.forEach(name=>vm.runInContext(fs.readFileSync(path.join(root,name),'utf8'),ctx));
  vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(root,'background.js'),'utf8'),ctx);
  await ctx.collect();
  assert.deepEqual(injected,[1,5,6]);
  assert.ok(requests.every(url=>url.startsWith('http://127.0.0.1:8877/')));
  assert.equal(requests.length,2);
});
