const test=require('node:test'),assert=require('node:assert/strict');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const {LiveClosePanel,trigger}=require('../static/live-close.js');
const aid='1999999999999002',pid='8218616704149013';
function fixture(){
 const dom=new JSDOM('<section id="live-close-panel"></section>',{url:'http://127.0.0.1:8877/cruise'}),calls=[];
 const app={doc:dom.window.document,accountId:aid,state:{live_close:{workers:[{worker_id:'worker',epoch:'epoch',current:true,armed:true,surfaces:[{account_id:aid,plan_id:pid,busy:true}]}],actions:[]}},account(){return {id:this.accountId,scene:this.accountId==='product'?'product':'live'};},api:async(path,body)=>{calls.push({path,body});},note:()=>{},task:async(button,fn)=>{await fn();},refresh:async()=>panel.render()};
 const panel=new LiveClosePanel(app),$=s=>app.doc.querySelector(s),edit=(s,v,type='input')=>{const el=$(s);if(el.type==='checkbox')el.checked=v;else el.value=v;el.dispatchEvent(new dom.window.Event(type,{bubbles:true}));};panel.render();return {dom,app,panel,calls,$,edit};
}
test('cross midnight trigger uses explicit Beijing time',()=>{assert.equal(trigger('2026-10-27T00:10'),'2026-10-26T15:50:00.000Z');});
test('defaults empty budget/time, polling preserves edits, accounts stay separate',()=>{
 const f=fixture();assert.equal(f.$('#close-budget').value,'');assert.equal(f.$('#close-off').value,'');assert.equal(f.$('#close-prepare').disabled,true);
 f.edit('#close-plan','worker:'+pid);f.edit('#close-off','2026-10-27T00:10');f.edit('#close-budget','200');f.panel.render();assert.equal(f.$('#close-budget').value,'200');assert.match(f.$('#close-preview').textContent,/23:50/);assert.match(f.$('#close-source').textContent,/不会结束原任务/);
 f.app.accountId='second';f.panel.render();assert.equal(f.$('#close-budget').value,'');f.app.accountId=aid;f.panel.render();assert.equal(f.$('#close-budget').value,'200');assert.equal(f.calls.length,0);
 f.app.accountId='product';f.panel.render();assert.equal(f.$('#live-close-panel').hidden,true);
});
test('prepare sends only a draft and concrete confirmation remains separate',async()=>{
 const f=fixture();f.edit('#close-plan','worker:'+pid);f.edit('#close-off','2026-10-27T00:10');f.edit('#close-budget','200');f.$('#close-prepare').click();await new Promise(r=>setImmediate(r));
 assert.equal(f.calls.length,1);assert.equal(f.calls[0].path,'/api/cruise/close/create');assert.equal(f.calls[0].body.off_at,'2026-10-27T00:10:00+08:00');assert.equal(f.calls[0].body.duration_minutes,30);
 const row={id:'one',worker_id:'worker',epoch:'epoch',state:'draft',expires_at:new Date(Date.now()+900000).toISOString(),intent_digest:'digest',intent:{account_id:aid,plan_id:pid,off_at:'2026-10-27T00:10:00+08:00',trigger_at:'2026-10-26T23:50:00+08:00',duration_minutes:30,budget:'200.00'}};
 f.app.state.live_close.actions=[row];f.panel.render();assert.equal(f.$('[data-close-approve]').disabled,true);f.edit('[data-close-check]',true,'change');f.panel.render();assert.equal(f.$('[data-close-check]').checked,true);f.$('[data-close-approve]').click();await new Promise(r=>setImmediate(r));assert.deepEqual(f.calls[1],{path:'/api/cruise/close/approve',body:{id:'one',intent_digest:'digest',confirmed:true}});
 row.state='approved';f.panel.render();assert.equal(f.$('[data-close-check]'),null);assert.match(f.$('#close-actions').textContent,/等待到点执行/);
 row.state='blocked';row.receipt={message:'保留原任务'};f.panel.render();assert.match(f.$('#close-actions').textContent,/保留原任务/);assert.equal(f.$('[data-close-approve]'),null);
});
test('disabled execution and unidentified native entry are distinct visible blockers',()=>{
 const f=fixture();f.app.state.live_close.workers[0].armed=false;f.panel.render();assert.match(f.$('#close-source').textContent,/执行模式关闭/);assert.equal(f.$('#close-plan').options.length,2);assert.equal(f.$('#close-prepare').disabled,true);f.edit('#close-plan','worker:'+pid);f.edit('#close-off','2026-10-27T02:00');f.edit('#close-budget','200');assert.equal(f.$('#close-prepare').disabled,true);
 f.app.state.live_close.workers[0].armed=true;f.app.state.live_close.workers[0].surfaces=[];f.panel.render();assert.match(f.$('#close-source').textContent,/入口尚未核验/);assert.equal(f.$('#close-prepare').disabled,true);assert.equal(f.calls.length,0);
});
