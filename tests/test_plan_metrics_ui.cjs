'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const {compareRows,filterRows}=require('../static/plan-metrics.js');
const catalog=[{key:'spend',label:'整体消耗',unit:'元'},{key:'roi',label:'整体支付ROI',unit:'倍'},{key:'marketing_roi',label:'综合营销ROI',unit:'倍'}];
function row(id,spend,roi){return {plan_id:id,name:'计划'+id,metrics:{spend,...(roi===undefined?{}:{roi})},metric_basis:'overall_payment',metric_date:'2026-10-26',captured_at:'2026-10-26T10:00:00+08:00',data_at:null,read_fresh:true,segment:'overall'};}
function fixture(){
 const dom=new JSDOM('<div id="account-metrics"></div><span id="connection-status"></span><input id="main-after" value="1234"><div id="plan-table"></div>',{url:'http://127.0.0.1:8877/cruise',runScripts:'outside-only'});
 dom.window.eval(fs.readFileSync(require.resolve('../static/plan-metrics.js'),'utf8'));
 const calls=[],app={doc:dom.window.document,accountId:'a',state:{now:'2026-10-26T10:00:00+08:00',monitoring:{metric_catalog:catalog,accounts:[{account_id:'a',plans:[row('85216864032339650046',2,3),row('83012570839665583872',100,0),row('85103848212538195265',10)],dates:['2026-10-26'],data_connected:true,execution_plan_count:0,summary:{metrics:{spend:112,roi:2.5},metric_date:'2026-10-26',captured_at:'2026-10-26T10:00:00+08:00',data_at:null,is_today:true,read_fresh:true}},{account_id:'b',plans:[row('other',999,99)],dates:['2026-10-26']}] }},choices:()=>[],task:async(b,fn)=>fn(),api:async(path,body)=>{calls.push({path,body});return {note:'只读刷新'};},note:()=>{},refresh:async()=>{}};
 const view=new dom.window.QianchuanMetrics.PlanMetrics(app);view.render();
 const $=s=>dom.window.document.querySelector(s),ids=()=>[...dom.window.document.querySelectorAll('tbody tr .name-cell small')].map(e=>e.textContent);
 return {dom,app,view,$,ids,calls};
}
test('numeric sorting is not lexical, keeps exact IDs, and missing values are last in either direction',()=>{
 const rows=[row('a',2),row('b',100),row('c',10),row('d',null),row('e',0)];
 assert.deepEqual(rows.slice().sort((a,b)=>compareRows(a,b,'m:spend','desc')).map(r=>r.plan_id),['b','c','a','e','d']);
 assert.deepEqual(rows.slice().sort((a,b)=>compareRows(a,b,'m:spend','asc')).map(r=>r.plan_id),['e','a','c','b','d']);
});
test('ROI sort retains genuine zero and never borrows another ROI basis',()=>{
 const f=fixture();f.$('[data-metric-sort="m:roi"]').click();
 assert.deepEqual(f.ids(),['85216864032339650046','83012570839665583872','85103848212538195265']);
 f.$('[data-metric-sort="m:roi"]').click();assert.deepEqual(f.ids(),['83012570839665583872','85216864032339650046','85103848212538195265']);
 assert.match(f.$('#plan-table').textContent,/—/);
});
test('account monitoring remains connected when no execution surface exists',()=>{
 const f=fixture();assert.match(f.$('#connection-status').textContent,/账户数据已连接.*0个计划可调控/);
 assert.match(f.$('#account-metrics').textContent,/112/);assert.match(f.$('#account-metrics').textContent,/指标更新 未提供/);
});
test('sorting and polling preserve advertisement form inputs and per-account sort preferences',()=>{
 const f=fixture();f.$('[data-metric-sort="m:roi"]').click();f.view.render();assert.equal(f.$('#main-after').value,'1234');
 f.app.accountId='b';f.view.render();assert.deepEqual(f.ids(),['other']);f.app.accountId='a';f.view.render();
 assert.deepEqual(f.ids(),['85216864032339650046','83012570839665583872','85103848212538195265']);
 assert.equal(JSON.parse(f.dom.window.localStorage.getItem('qc_metric_view_a')).sort,'m:roi');
});
test('search, dates and overall/base scope filter read-only rows independently',()=>{
 const rows=[row('a',1),{...row('b',2),metric_date:'2026-10-25'},{...row('c',3),segment:'plan_card_base'}];
 const prefs={date:'2026-10-26',segment:'overall',sort:'m:spend',direction:'desc'};
 assert.deepEqual(filterRows(rows,prefs).map(r=>r.plan_id),['a']);
 assert.deepEqual(filterRows(rows,{...prefs,segment:'base'}).map(r=>r.plan_id),['c']);
 assert.deepEqual(filterRows(rows,{...prefs,date:'all',search:'b'}).map(r=>r.plan_id),['b']);
});
test('column selection hides only that column and survives later renders',()=>{
 const f=fixture(),input=f.$('[data-metric-column="roi"]');input.checked=false;input.dispatchEvent(new f.dom.window.Event('change',{bubbles:true}));
 f.view.render();assert.equal(f.$('[data-metric-sort="m:roi"]'),null);assert.ok(f.$('[data-metric-sort="m:spend"]'));
});
test('switching to API cannot hide rows with an unavailable saved date or base filter',()=>{
 const f=fixture();f.app.state.monitoring.accounts[0].backend='official_api';
 Object.assign(f.view.prefs(),{date:'2026-10-25',date_mode:'manual',segment:'base'});
 f.view.render();assert.equal(f.$('#metric-date').value,'2026-10-26');
 assert.equal(f.$('#metric-segment').value,'overall');assert.equal(f.ids().length,3);
});
test('refresh requests only the chosen account data and never creates or approves an ad action',async()=>{
 const f=fixture();f.$('#refresh-plan-data').click();await new Promise(r=>setImmediate(r));
 assert.deepEqual(JSON.parse(JSON.stringify(f.calls)),[{path:'/api/cruise/refresh-data',body:{account_id:'a'}}]);
});
