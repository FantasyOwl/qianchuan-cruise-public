'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/browser-control.js'),'utf8').replace(/\nloadBrowserControl\(\);\s*$/,'');
function page(){
  const dom=new JSDOM('<div id="browser-control"></div><button id="browser-control-refresh"></button><div id="recommendations"></div>',{runScripts:'outside-only'});
  const w=dom.window,calls=[];
  w.esc=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
  w.date=x=>x;w.basisNames={marketing:'综合营销ROI'};w.state={csrf:'test-only'};w.notice=()=>{};
  const intent={account_id:'123',plan_id:'789',plan_name:'<img src=x>',action_label:'上调主计划日预算',before:'1000.00',after:'1100.00',reason:'人工核对',surface:{tab_id:7}};
  const queue={execution_available:true,workers:[{worker_id:'worker_a',epoch:'epoch_b',armed:true,surfaces:[{account_id:'123',plan_id:'789',actions:['budget_up']}]}],actions:[{id:'unknown-one',state:'unknown',worker_id:'worker_a',intent}]};
  const preview={id:'unknown-one',reconciliation_digest:'digest-one',intent,expires_at:'expires',evidence:{match:'target',value:'1100.00',surface:{captured_at:'captured'}}};
  w.api=async(path,body)=>{calls.push({path,body});if(path.endsWith('reconcile-preview'))return preview;if(path.endsWith('reconcile-confirm'))queue.actions[0].state='reconciled_target';return queue;};
  w.action=(_button,fn)=>{w.pending=Promise.resolve().then(fn).catch(error=>{w.lastError=error.message;});return w.pending;};
  vm.runInContext(source,dom.getInternalVMContext());
  const run=code=>vm.runInContext(code,dom.getInternalVMContext());
  run('browserControlState=null');
  return {dom,w,calls,queue,preview,run,async load(){await w.loadBrowserControl();},click(selector){w.document.querySelector(selector).click();return w.pending;}};
}
test('recovery reads a fresh preview before a separate unchecked confirmation and never approves a mutation',async()=>{
  const p=page();try{
    await p.load();await p.click('[data-browser-reconcile]');
    assert.deepEqual(p.calls.map(c=>c.path),['/api/browser-actions','/api/browser-actions','/api/browser-actions/reconcile-preview']);
    assert.equal(p.calls[2].body.id,'unknown-one');assert.equal(p.calls[2].body.epoch,'epoch_b');
    assert.equal(p.w.document.querySelector('#browser-reconcile-check').checked,false);
    await p.click('#browser-reconcile-confirm');assert.match(p.w.lastError,/勾选确认/);
    assert.equal(p.calls.length,3);
    p.w.document.querySelector('#browser-reconcile-check').checked=true;
    await p.click('#browser-reconcile-confirm');
    assert.deepEqual(JSON.parse(JSON.stringify(p.calls[3].body)),{id:'unknown-one',reconciliation_digest:'digest-one',confirmed:true});
    assert.equal(p.calls.some(c=>/\/(approve|worker-begin|worker-claim)$/.test(c.path)),false);
    assert.match(p.w.document.querySelector('#browser-control').textContent,/原值|目标一致/);
  }finally{p.dom.window.close();}
});
test('recovery review escapes evidence and states original submission remains unknown',async()=>{
  const p=page();try{
    await p.load();p.preview.evidence.value='<script>bad</script>';
    p.w.showBrowserReconciliation(p.preview);
    const panel=p.w.document.querySelector('#browser-reconciliation');
    assert.equal(panel.querySelector('script'),null);assert.match(panel.textContent,/不证明原提交/);
    assert.match(panel.textContent,/1小时/);assert.match(panel.textContent,/<script>bad/);
    assert.match(p.w.browserActionCard({...p.queue.actions[0],state:'reconciled_other',resolution:{resolved_at:'time',evidence:{value:'1050.00'}}}),/归因仍未知/);
  }finally{p.dom.window.close();}
});
test('other worker cannot provide the recovery preview',async()=>{
  const p=page();try{
    p.queue.workers[0].worker_id='another_worker';await p.load();await p.click('[data-browser-reconcile]');
    assert.match(p.w.lastError,/原Chrome/);
    assert.equal(p.calls.some(c=>c.path.endsWith('reconcile-preview')),false);
  }finally{p.dom.window.close();}
});
test('status sync discovers a newly connected worker without a manual reload or approval',async()=>{
  const p=page();try{
    p.run('browserControlState={execution_available:false,workers:[],actions:[]};renderBrowserControl()');
    assert.equal(p.w.document.querySelector('#browser-prepare').disabled,true);
    p.w.syncBrowserControl(p.queue);
    assert.equal(p.w.document.querySelector('#browser-prepare').disabled,false);
    assert.match(p.w.document.querySelector('#browser-connection-note').textContent,/已连接/);
    assert.equal(p.w.document.querySelector('#browser-plan').options.length,2);
    assert.equal(p.calls.length,0);
  }finally{p.dom.window.close();}
});
test('status sync preserves typed intent and the checked single-action approval while reflecting disconnection',async()=>{
  const p=page();try{
    await p.load();
    const reason=p.w.document.querySelector('#browser-reason');reason.value='正在核对当前计划';
    p.w.document.querySelector('#browser-plan').value='0';
    p.w.showBrowserApproval({id:'draft-one',expires_at:'expires',intent:p.queue.actions[0].intent});
    p.w.document.querySelector('#browser-confirm-check').checked=true;
    p.w.syncBrowserControl({...p.queue,execution_available:false,workers:[]});
    assert.equal(p.w.document.querySelector('#browser-reason'),reason);
    assert.equal(reason.value,'正在核对当前计划');
    assert.equal(p.w.document.querySelector('#browser-confirm-check').checked,true);
    assert.equal(p.w.document.querySelector('#browser-prepare').disabled,true);
    assert.match(p.w.document.querySelector('#browser-connection-note').textContent,/尚未连接/);
    assert.equal(p.calls.filter(c=>c.body).length,0);
  }finally{p.dom.window.close();}
});
test('status sync preserves a pending independent reconciliation and never confirms it automatically',async()=>{
  const p=page();try{
    await p.load();p.w.showBrowserReconciliation(p.preview);
    p.w.document.querySelector('#browser-reconcile-check').checked=true;
    const panel=p.w.document.querySelector('#browser-reconciliation');
    p.w.syncBrowserControl(p.queue);
    assert.equal(p.w.document.querySelector('#browser-reconciliation'),panel);
    assert.equal(p.w.document.querySelector('#browser-reconcile-check').checked,true);
    assert.equal(p.calls.filter(c=>c.body).length,0);
  }finally{p.dom.window.close();}
});
