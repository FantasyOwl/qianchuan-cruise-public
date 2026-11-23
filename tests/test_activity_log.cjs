'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const {ActivityPanel,describe,rowHTML,runtimeHTML,clock,money}=require('../static/activity-log.js');
const aid='1999999999999002',other='1999999999999001',pid='8218616704149013',tid='8533524212032260758';
const record=(patch={})=>({id:'action-1',state:'verified',created_at:'2026-11-10T09:10:30+08:00',updated_at:'2026-11-10T09:10:33+08:00',
  intent:{account_id:aid,plan_id:pid,task_id:tid,action:'task_budget',before:'500',after:'600',rule_check:{feature_id:'f2'}},
  result:{message:'已按实际任务ID回读核验'},...patch});
const settle=async()=>{for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));};

test('verified budget and pause logs show exact IDs, old/new values and rule',()=>{
  const row=describe(record());assert.equal(row.taskId,tid);assert.equal(row.type,'修改');
  assert.deepEqual(row.lines,['调控预算 500.00元 → 600.00元']);assert.match(row.reason,/F2/);
  assert.equal(row.status,'已核验');assert.equal(clock(row.at),'2026-11-10 09:10:30');
  const pause=describe(record({intent:{account_id:aid,plan_id:pid,task_id:tid,action:'pause_task',before:'PROCESSING',after:'暂停'}}));
  assert.equal(pause.lines[0],'投放状态 投放中 → 已暂停');
  assert.equal(money(null),'未记录');assert.equal(money(false),'未记录');assert.equal(money(0),'0.00元');
});

test('unverified and rejected requests never describe requested values as completed changes',()=>{
  for(const state of ['draft','unknown','cancelled','rejected','submitted_pending_verification']) {
    const row=describe(record({state}));assert.match(row.lines[0],/^申请调控预算/);
    assert.notEqual(row.group,'verified');assert.notEqual(row.status,'已核验');
  }
  const recovered=describe(record({result:{phase:'timeout_readback',message:'当前值一致，原请求不重发'}}));
  assert.equal(recovered.status,'当前值已核验');assert.match(recovered.lines[0],/^申请/);
  assert.equal(recovered.at,'2026-11-10T09:10:30+08:00');
});
test('targeted chase history shows the original selected gender ages and fan exclusion',()=>{
 const row=describe(record({intent:{account_id:aid,plan_id:pid,action:'material_chase',execution_channel:'browser',after:{daily_budget:'200'},browser_batch:{after:{audience:{gender:'female',ages:['18-23','31-40'],exclude_fans:true}}}}}));
 assert.match(row.lines.join('\n'),/性别 女；年龄 18-23、31-40；抖音号粉丝 排除/);
 assert.match(rowHTML(row),/定向/);
});

test('chase logs distinguish shared daily budgets and total budgets with pause deadlines',()=>{
  const base={account_id:aid,plan_id:pid,action:'material_chase',material_ids:[tid,'8303390901814024325']};
  const daily=describe(record({intent:{...base,after:{daily_budget:'300',total_budget:null,bid:20.88,optimization_target:'直播间成交'}},result:{task_id:tid}}));
  assert.equal(daily.taskId,tid);assert.equal(daily.type,'新建');assert.match(daily.lines.join('\n'),/整批日预算 300.00元/);
  assert.match(daily.lines.join('\n'),/追投方式 控成本追投/);assert.match(daily.lines.join('\n'),/20.88元/);
  const volume=describe(record({intent:{...base,after:{total_budget:'200',daily_budget:null,optimization_target:'放量追投'},auto_stop_hours:'3'},
    followups:[{task_id:tid,due_at:'2026-11-10T12:10:00+08:00',state:'verified',result:{message:'原任务停止已核对'}}]}));
  assert.match(volume.lines.join('\n'),/整批总预算 200.00元/);assert.match(volume.lines.join('\n'),/追投方式 放量追投/);
  assert.match(rowHTML(volume),/定时暂停 · 已核对任务停止/);assert.match(rowHTML(volume),/约定时点 2026-11-10 12:10:00/);
});

test('duration, ROI and slowdown use explicit units and do not assert native verification',()=>{
  const duration=describe(record({intent:{action:'task_duration',plan_id:pid,task_id:tid,before:1.5,after:2}}));
  assert.equal(duration.lines[0],'调控总时长 1.5小时 → 2小时');
  const legacy=describe(record({intent:{action:'control_duration_up',plan_id:pid,task_id:tid,before:90,after:120}}));
  assert.equal(legacy.lines[0],duration.lines[0]);
  const roi=describe(record({intent:{action:'plan_roi',plan_id:pid,before:3.2,after:3.3}}));
  assert.equal(roi.lines[0],'主计划ROI目标 3.2 → 3.3');
  const slow=describe(record({state:'active_acknowledged',intent:{action:'slowdown_schedule',plan_id:pid,after:{budget:200,duration_minutes:30,trigger_at:'2026-11-10T23:40:00+08:00'}}}));
  assert.equal(slow.status,'官方已受理控量');assert.equal(slow.group,'acknowledged');assert.match(slow.lines.join('\n'),/持续30分钟/);
});

test('all displayed text and raw JSON are escaped; raw data stays inside disclosure',()=>{
  const r=record({intent:{...record().intent,plan_name:'<img src=x onerror=bad()>',reason:'<script>bad()</script>'},result:{message:'<svg onload=bad()>'}});
  const dom=new JSDOM('<table><tbody>'+rowHTML(describe(r))+'</tbody></table>');
  const doc=dom.window.document;
  assert.equal(doc.querySelectorAll('img,script,svg').length,0);
  assert.equal(doc.querySelectorAll('details pre').length,1);
  assert.match(doc.querySelector('details pre').textContent,/<script>bad\(\)<\/script>/);
  assert.equal(doc.querySelector('details').open,false);
});

test('runtime logs use Chinese event names and explain waiting without exposing JSON by default',()=>{
  const logs=[{id:1,at:'2026-11-10T09:00:00+08:00',event:'rule_auto_scan',detail:{state:'waiting',message:'接口限流，等待冷却后重试',diagnostics:[{feature_id:'f1',reason:'未达到消耗门槛'}]}}];
  const doc=new JSDOM(runtimeHTML(logs,new Set(['1']))).window.document;
  assert.match(doc.querySelector('tbody').textContent,/自动规则巡查/);assert.match(doc.querySelector('tbody').textContent,/接口限流/);
  assert.equal(doc.querySelector('details[data-runtime-detail]').open,true);
  assert.equal(doc.querySelectorAll('pre:not(details pre)').length,0);
  const plain=new JSDOM(runtimeHTML([{id:2,event:'scan_error',detail:'读取失败，请稍后重试'}])).window.document;
  assert.match(plain.querySelector('tbody div').textContent,/读取失败，请稍后重试/);
});

function fixture(api) {
  const dom=new JSDOM('<div id="activity-log"></div>');const calls=[];
  const app={accountId:aid,view:'ledger',state:{monitoring:{accounts:[{account_id:aid,backend:'official_api'},{account_id:other,backend:'official_api'}]}},
    $:s=>dom.window.document.querySelector(s),records:()=>[],api:async path=>{calls.push(path);return api(path);}};
  const panel=new ActivityPanel(app);panel.render();
  return {dom,app,panel,calls,$:app.$};
}

test('browser rule completion displays actual task, budget and missing-field reasons',()=>{
 const html=runtimeHTML([{id:7,event:'hybrid_rules_finished',detail:{message:'已检查34条，本轮未修改',diagnostics:[
  {feature_id:'f1',reasons:['缺少明确调控综合成本']},{feature_id:'f2',entity_id:tid,before:'300',after:'500',reason:'单次增幅最多50%'}]}}]);
 const doc=new JSDOM(html).window.document;
 assert.match(doc.body.textContent,/网页规则检查完成/);assert.match(doc.body.textContent,/缺少明确调控综合成本/);
 assert.match(doc.body.textContent,new RegExp(tid));assert.match(doc.body.textContent,/300 → 500/);
});
const response=(account=aid,records=[record()],extra={})=>({account_id:account,records,total:records.length,page:1,pages:1,...extra});

test('filters and pagination use read-only account-scoped history and survive routine rendering',async()=>{
  const f=fixture(async path=>{const q=new URL(path,'http://local').searchParams;return response(aid,[record()],{page:Number(q.get('page')),pages:3,total:45});});
  await settle();assert.equal(f.$('tbody strong').textContent,'素材追投 · ID '+tid);
  assert.equal(new URL(f.calls[0],'http://local').searchParams.get('state'),'verified');
  f.$('[data-activity-search]').value=tid;f.$('[data-activity-filter="action"]').value='budget';
  f.$('[data-activity-filter="day"]').value='2026-11-10';f.panel.updateFilters();await settle();
  let query=new URL(f.calls.at(-1),'http://local').searchParams;
  assert.equal(query.get('account_id'),aid);assert.equal(query.get('keyword'),tid);assert.equal(query.get('day'),'2026-11-10');
  f.$('[data-activity-next]').click();await settle();query=new URL(f.calls.at(-1),'http://local').searchParams;
  assert.equal(query.get('page'),'2');assert.equal(query.get('action'),'budget');
  f.$('details[data-activity-detail]').open=true;f.$('[data-activity-search]').value='尚未提交的搜索';
  const count=f.calls.length;f.panel.render();await settle();assert.equal(f.calls.length,count);
  assert.equal(f.$('[data-activity-search]').value,'尚未提交的搜索');assert.equal(f.$('details[data-activity-detail]').open,true);
  assert.equal(f.$('[data-activity-page]').textContent,'第 2 / 3 页');
});

test('a late response cannot leak another account into the current view',async()=>{
  let resolveFirst;
  const f=fixture(path=>new URL(path,'http://local').searchParams.get('account_id')===aid
    ? new Promise(resolve=>{resolveFirst=resolve;}) : Promise.resolve(response(other,[record({intent:{...record().intent,account_id:other,task_id:'999'}})])));
  f.app.accountId=other;f.panel.render();await settle();
  resolveFirst(response());await settle();
  assert.match(f.$('[data-activity-table]').textContent,/ID 999/);assert.doesNotMatch(f.$('[data-activity-table]').textContent,new RegExp(tid));
  assert.equal(f.panel.accountId,other);
});

test('mismatched and failed responses show a readable error without stale mismatched rows',async()=>{
  let fail=false;
  const f=fixture(async()=>fail ? response(other,[record()]) : response());await settle();
  fail=true;f.$('[data-activity-filter="action"]').value='pause';f.panel.updateFilters();await settle();
  assert.match(f.$('[data-activity-status]').textContent,/读取记录失败，返回记录的账户不一致/);
  assert.equal(f.$('tbody'),null);
});
