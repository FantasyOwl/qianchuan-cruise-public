/* Anonymous fixtures mirror the observed board headings; no browser or account IO. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../extension/collector.js'),'utf8');
const acct='1234567';
const heads=['素材名称/ID','创建时间','投放状态','素材追投','整体支付ROI','整体消耗(元)','整体成交金额(元)','整体成交订单数','整体展现次数','整体点击次数'];
function row(id='9000001',changes={}){
  const values=[`测试素材\n\nID：${id}\n\n自选视频`,'2026-10-16 21:34:06','投放中','调控中','3.74','280.79','1,049.29','8','2,180','64'];
  for(const [key,value] of Object.entries(changes))values[heads.indexOf(key)]=value;
  return {cells:values.map(innerText=>({innerText})),getClientRects:()=>[1]};
}
function table(rows=[row()],titles=heads){return {rows,querySelectorAll:()=>titles.map(innerText=>({innerText}))};}
function collect(options={}){
  const labels=[['整体消耗(元)','500.00'],['整体成交金额(元)','1,000.00'],['整体支付ROI','2.00']].map(([label,value])=>({
    children:[],innerText:label,parentElement:{innerText:`${label}\n${value}`,parentElement:null},closest:()=>null,getClientRects:()=>[1]}));
  for(const [label,value] of Object.entries(options.filters||{'数据周期:':'整场直播','投放类型:':'控成本投放'})){
    const input={value,getClientRects:()=>[1]};
    labels.push({children:[],innerText:label,parentElement:{innerText:label,parentElement:null,querySelectorAll:()=>[input]},closest:()=>null,getClientRects:()=>[1]});
  }
  const ctx={URL,URLSearchParams,Date,Number,Set,getComputedStyle:()=>({visibility:'visible',display:'block'}),
    location:{href:`https://qianchuan.jinritemai.com/board-next?aavid=${acct}&live_room_id=8000001`+(options.query||'')},
    document:{body:{innerText:options.text??'开播时间：2026-10-24 05:58:49'},querySelectorAll:s=>s==='body *'?labels:s==='table'?(options.tables||[table()]):[]}};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  return ctx.collectVisibleQianchuan({accountId:acct,path:'/board-next',role:'board',scene:'live'});
}
test('board materials retain own metrics, identities, scope and unverified facts',()=>{
  const value=collect(),c=value.snapshots[1];assert.equal(value.diagnostics.visible_creatives,1);
  assert.equal(c.entity_id,'9000001');assert.equal(c.display_name,'测试素材');assert.equal(c.scope,'creative');
  assert.deepEqual(JSON.parse(JSON.stringify(c.metrics)),{spend:280.79,gmv:1049.29,roi:3.74,payment_orders:8,impressions:2180,clicks:64,orders:8});
  assert.equal(c.metric_basis,'overall_payment');assert.equal(c.source.segment,'material_cost_control');
  assert.equal(c.source.metric_period,'live_session');assert.equal(c.source.period_start,'2026-10-24T05:58:49+08:00');
  assert.equal(c.source.metric_date,'2026-10-24');assert.equal(c.source.data_at,null);assert.equal(c.source.coverage,'visible_only');
  assert.equal(c.context.created_at,'2026-10-16T21:34:06+08:00');assert.equal(c.context.live_room_id,'8000001');
  assert.equal(c.context.content_format,'short_video');assert.equal(c.context.creative_type,'unknown');
  assert.equal(c.context.spend_ledger,'overall');assert.equal(c.control.running,true);assert.equal(c.control.history_known,false);
  assert.equal(c.context.parent_plan_id,undefined);assert.equal(c.context.creative_parent_verified,null);
  for(const key of ['base_spend','active_heated_creatives','ctr'])assert.equal(c.metrics[key],undefined);
  assert.equal(c.context.control_type,undefined);assert.ok(c.quality.issues.some(x=>x.includes('不代表全量')));
});
test('room picture, totals, hidden rows and similarly sized plan tables are excluded',()=>{
  const room=row('9000002',{'素材名称/ID':'直播间\n抖音号ID：8000001\n直播间画面'}),total=row('9000003',{'素材名称/ID':'共2881条素材'}),hidden=row('9000004');
  hidden.getClientRects=()=>[];
  const value=collect({tables:[table([room,total,hidden]),table([row('9000005')],heads.map(h=>h==='素材名称/ID'?'计划':h))]});
  assert.equal(value.diagnostics.visible_creatives,0);assert.equal(value.snapshots.length,1);
});
test('duplicate material tables collapse equal rows and exclude conflicting rows',()=>{
  const a=table();assert.equal(collect({tables:[a,a]}).diagnostics.visible_creatives,1);
  for(const changes of [{'整体消耗(元)':'999.99'},{'投放状态':'已暂停'},{'创建时间':'2026-10-17 00:00:00'}]){
    const value=collect({tables:[a,table([row('9000001',changes)])]});
    assert.equal(value.diagnostics.visible_creatives,0);assert.ok(value.diagnostics.issues.some(s=>s.includes('冲突')));
  }
});
test('period must explicitly match the whole live session',()=>{
  for(const period of ['今天','最近30分钟','',null]){
    const filters={'投放类型:':'控成本投放'};if(period!==null)filters['数据周期:']=period;
    const value=collect({filters});assert.equal(value.diagnostics.visible_creatives,0);assert.ok(value.diagnostics.issues.some(s=>s.includes('统计周期')));
  }
  assert.equal(collect({text:'10-24 06:50更新',query:'&dr=2026-10-24,2026-10-24'}).diagnostics.visible_creatives,0);
});
test('delivery filters use distinct segments and unknown filters are rejected',()=>{
  for(const [label,segment] of [['控成本投放','material_cost_control'],['放量投放','material_volume'],['全部','material_all']])
    assert.equal(collect({filters:{'数据周期:':'整场直播','投放类型:':label}}).snapshots[1].source.segment,segment);
  for(const label of ['', '未知投放'])assert.equal(collect({filters:{'数据周期:':'整场直播','投放类型:':label}}).diagnostics.visible_creatives,0);
});
test('missing values stay missing and explicit zero is retained',()=>{
  const c=collect({tables:[table([row('9000001',{'整体支付ROI':'-','整体点击次数':'--'})])]}).snapshots[1];
  assert.equal(c.metrics.roi,undefined);assert.equal(c.metrics.clicks,undefined);assert.equal(c.metric_basis,'unknown');
  const zero=collect({tables:[table([row('9000001',{'整体支付ROI':'0.00','整体消耗(元)':'0.00','整体成交金额(元)':'0.00','整体成交订单数':'0'})])]}).snapshots[1];
  assert.equal(zero.metrics.roi,0);assert.equal(zero.metrics.orders,0);assert.equal(zero.metric_basis,'overall_payment');
});
test('invalid counts, malformed dates and unknown delivery status do not become facts',()=>{
  const c=collect({tables:[table([row('9000001',{'整体点击次数':'1.5','创建时间':'2026-02-30 12:00:00','投放状态':'审核中','素材名称/ID':'测试图文\nID：9000001\n图文'})])]}).snapshots[1];
  assert.equal(c.metrics.clicks,undefined);assert.equal(c.context.created_at,undefined);assert.equal(c.control.running,null);
  assert.equal(c.context.content_format,'image_text');assert.equal(c.context.compliance_ok,undefined);
  assert.equal(collect({tables:[table([row('invalid')])]}).diagnostics.visible_creatives,0);
});
test('ambiguous headers and truncated rows never borrow other table headings',()=>{
  const titles=[...heads];titles[4]='整体消耗(元)';assert.equal(collect({tables:[table([row()],titles)]}).diagnostics.visible_creatives,0);
  const r=row();r.cells.pop();assert.equal(collect({tables:[table([r])]}).diagnostics.visible_creatives,0);
});
