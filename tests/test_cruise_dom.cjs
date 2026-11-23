'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const {cruiseTaskDom}=require('../extension/cruise-dom.js');
const account='1999999999999002',plan='8218616704149013',task='8618530283142212632';
const heads=['','任务名称','调控状态','调控预算(元)','调控时长(小时)','追投方式','创建时间','调控消耗','调控支付ROI','调控成交订单数','调控成交金额','调控综合成本'];
function fixture(options={}){
  const head='<thead><tr>'+heads.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead>';
  const row=id=>`<tr><td><input type="checkbox"></td><td><div>测试追投任务</div><div>ID：${id}</div>${options.stop?'<button class="stop">结束调控</button>':''}</td><td class="status">${options.status||'调控中'}</td><td class="budget"><span>1,500.00</span><iconpark-icon name="oc-icon-light-edit"></iconpark-icon></td><td class="duration"><span>${options.unlimited?'长期投放':'2'}</span><i class="edit-icon"></i></td><td>控成本追投</td><td>2026-10-25 07:14:44</td><td>881.69</td><td>2.61</td><td>18</td><td>2298.47</td><td>${options.costMissing?'-':'900.00'}</td></tr>`;
  const rows=row(task)+(options.duplicate?row(task):options.second?row('8533340473204213496'):'');
  const table=options.split?'<table>'+head+'</table><table><tbody>'+rows+'</tbody></table>':'<table>'+head+'<tbody>'+rows+'</tbody></table>';
  const dom=new JSDOM(`<pre>ID：${account}</pre><div class="oc-promotion-key-info-bar">投放中\n计划ID：${plan}</div><div class="ovui-table">${table}<button disabled data-auto-id="bar-groups-group-item-btn-pause">暂停</button></div>`,{
    url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${account}&adId=${plan}`,runScripts:'outside-only'});
  const w=dom.window,d=w.document,counters={save:0,pause:0,stop:0,confirm:0,permit:0};
  const blocks=new Set(['DIV','P','TR','TD','PRE','SECTION']);
  const rendered=n=>n.nodeType===3?n.textContent:n.nodeType!==1?'':[...n.childNodes].map(c=>(blocks.has(c.tagName)?'\n':'')+rendered(c)+(blocks.has(c.tagName)?'\n':'')).join('');
  Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return rendered(this).replace(/\n+/g,'\n').trim();}});
  w.HTMLElement.prototype.getClientRects=function(){return this.hidden||this.style.display==='none'?[]:[{}];};
  const visibleDialog=(title,body,onSave)=>{
    const dialog=d.createElement('div');dialog.setAttribute('role','dialog');dialog.innerHTML='<h3>'+title+'</h3>'+body+'<button>确定</button><button>取消</button>';
    dialog.querySelector('button').addEventListener('click',()=>{onSave(dialog);dialog.remove();});
    dialog.querySelectorAll('button')[1].addEventListener('click',()=>dialog.remove());d.body.append(dialog);return dialog;
  };
  for(const r of d.querySelectorAll('tbody tr')){
    r.querySelector('.budget iconpark-icon').addEventListener('click',()=>visibleDialog('调控预算','<input placeholder="请输入" value="'+(options.formBefore||'1500')+'">'+(options.extraInput?'<input value="1">':''),dialog=>{
      counters.save++;r.querySelector('.budget span').textContent=dialog.querySelector('input').value;
    }));
    r.querySelector('.duration .edit-icon').addEventListener('click',()=>visibleDialog('调控时长（小时）','<input type="number" min="0.5" step="0.5" value="2">',dialog=>{
      counters.save++;r.querySelector('.duration span').textContent=dialog.querySelector('input').value;
    }));
    r.querySelector('input[type=checkbox]').addEventListener('change',()=>{d.querySelector('[data-auto-id$="btn-pause"]').disabled=d.querySelectorAll('tbody input:checked').length!==1;});
    r.querySelector('.stop')?.addEventListener('click',()=>{counters.stop++;visibleDialog('结束调控任务 '+task,'',()=>{counters.confirm++;r.querySelector('.status').textContent='调控结束';});});
  }
  const pause=d.querySelector('[data-auto-id$="btn-pause"]');
  pause.addEventListener('click',()=>{
    counters.pause++;const r=d.querySelector('tbody input:checked')?.closest('tr');
    if(options.directPause){r.querySelector('.status').textContent='调控暂停';return;}
    const dialog=visibleDialog(options.unknownConfirm?'其他操作':'确认暂停调控任务 '+task,'',()=>{counters.confirm++;r.querySelector('.status').textContent='调控暂停';});
    if(options.doubleConfirm){const b=d.createElement('button');b.textContent='确认';dialog.append(b);}
  });
  w.chrome={runtime:{sendMessage:async message=>{counters.permit++;assert.equal(message.action,'cruise-final-check');if(options.onPermit)await options.onPermit({w,d,dom});return {allow:options.allow!==false};}}};
  const execute=w.eval('('+cruiseTaskDom.toString()+')'),identity={accountId:account,planId:plan,path:'/uni-prom/detail',group:'示例分组A',scene:'live'};
  const inspect=()=>execute('inspect',{identity});
  async function command(action='control_budget_up',after='1800.00'){
    const s=(await inspect()).surfaces.find(s=>s.task_id===task),field={control_budget_up:'budget',control_duration_up:'duration_minutes',control_pause:'running',control_stop:'status'}[action];
    // Python queues serialize fields in sorted order. Binding cannot depend on object insertion order.
    const surface=Object.fromEntries(Object.entries(JSON.parse(JSON.stringify(s))).sort(([a],[b])=>a.localeCompare(b)));
    return {identity,id:'command_one',intent_digest:'digest_one',expires_at:new Date(Date.now()+120000).toISOString(),
      intent:{target_kind:'control_task',account_id:account,plan_id:plan,task_id:task,action,field,before:s[field],after,surface}};
  }
  return {dom,w,d,counters,inspect,command,execute,identity,close:()=>w.close(),async prepare(c){return execute('prepare',c);},async submit(c,p){return execute('submit',{...c,submit_token:p.submit_token});}};
}
test('task inspection binds parent account and exact long task ID with separate task metrics',async()=>{
  const f=fixture({split:true});try{
    const s=(await f.inspect()).surfaces[0];assert.equal(s.task_id,task);assert.equal(s.plan_id,plan);assert.equal(s.budget,'1500.00');assert.equal(s.duration_minutes,'120.00');
    assert.equal(s.metric_basis,'control_payment');assert.equal(s.metrics.spend,881.69);assert.equal(s.metrics.comprehensive_cost,900);assert.equal(s.metrics.orders,18);assert.equal(s.context.data_at,null);
    assert.deepEqual(Array.from(s.actions),['control_budget_up','control_duration_up','control_pause']);
  }finally{f.close();}
});
test('missing comprehensive cost is not replaced by spend and unlimited duration is not adjustable',async()=>{
  const f=fixture({costMissing:true,unlimited:true});try{const s=(await f.inspect()).surfaces[0];assert.equal(s.metrics.comprehensive_cost,undefined);assert.equal(s.duration_minutes,null);assert.ok(!s.actions.includes('control_duration_up'));}finally{f.close();}
});
test('wrong account parent duplicate URL IDs and duplicate task rows are rejected',async()=>{
  for(const url of [`?aavid=9999999&adId=${plan}`,`?aavid=${account}&adId=7777777`,`?aavid=${account}&aavid=${account}&adId=${plan}`]){
    const f=fixture();try{f.dom.reconfigure({url:'https://qianchuan.jinritemai.com/uni-prom/detail'+url});assert.equal((await f.inspect()).ok,false);}finally{f.close();}
  }
  const f=fixture({duplicate:true});try{assert.equal((await f.inspect()).surfaces.length,0);}finally{f.close();}
});
test('preparation opens only the task budget form, then abort cancels without a save',async()=>{
  const f=fixture();try{const c=await f.command(),p=await f.prepare(c);assert.equal(p.prepared,true);assert.equal(f.counters.save,0);assert.equal(f.d.querySelector('[role=dialog] input').value,'1500');
    assert.equal((await f.execute('abort',c)).submitted,false);assert.equal(f.d.querySelector('[role=dialog]'),null);assert.equal(f.counters.save,0);
  }finally{f.close();}
});
test('approved task budget submits once after final permit and returns no fabricated verification',async()=>{
  const f=fixture();try{const c=await f.command(),p=await f.prepare(c),r=await f.submit(c,p);assert.equal(r.submitted,true);assert.equal(r.verified,undefined);assert.equal(f.counters.save,1);assert.equal(f.counters.permit,1);
    assert.equal((await f.submit(c,p)).ok,false);assert.equal(f.counters.save,1);assert.equal((await f.inspect()).surfaces[0].budget,'1800.00');
  }finally{f.close();}
});
test('duration is a separate command and converts exact minutes into explicit half-hour inputs',async()=>{
  const f=fixture();try{const c=await f.command('control_duration_up','150.00'),p=await f.prepare(c);assert.equal(p.prepared,true);assert.equal(f.counters.save,0);
    const r=await f.submit(c,p);assert.equal(r.submitted,true);assert.equal((await f.inspect()).surfaces[0].duration_minutes,'150.00');assert.equal((await f.inspect()).surfaces[0].budget,'1500.00');
  }finally{f.close();}
});
test('denied final permission restores owned input and never saves',async()=>{
  const f=fixture({allow:false});try{const c=await f.command(),p=await f.prepare(c),r=await f.submit(c,p);assert.equal(r.code,'final_permission_rejected');assert.equal(f.counters.save,0);assert.equal(f.d.querySelector('[role=dialog]'),null);}finally{f.close();}
});
test('changed input while final permit is pending is not submitted',async()=>{
  const f=fixture({onPermit:({d})=>{d.querySelector('[role=dialog] input').value='9999';}});try{const c=await f.command(),p=await f.prepare(c);assert.equal((await f.submit(c,p)).submitted,false);assert.equal(f.counters.save,0);}finally{f.close();}
});
test('account navigation during permission check prevents saving the prepared form',async()=>{
  const f=fixture({onPermit:({dom})=>dom.reconfigure({url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=9999999&adId=${plan}`})});
  try{const c=await f.command(),p=await f.prepare(c);assert.equal((await f.submit(c,p)).submitted,false);assert.equal(f.counters.save,0);}finally{f.close();}
});
test('task pause prepares only an exclusive checkbox then submits one pause and one explicit confirmation',async()=>{
  const f=fixture();try{const c=await f.command('control_pause',false),p=await f.prepare(c);assert.equal(p.prepared,true);assert.equal(f.counters.pause,0);assert.equal(f.d.querySelector('tbody input').checked,true);
    const r=await f.submit(c,p);assert.equal(r.submitted,true);assert.equal(f.counters.pause,1);assert.equal(f.counters.confirm,1);assert.equal((await f.inspect()).surfaces[0].running,false);
  }finally{f.close();}
});
test('existing user selection is not cleared or used for a bulk pause',async()=>{
  const f=fixture({second:true});try{const c=await f.command('control_pause',false);f.d.querySelectorAll('tbody input')[1].click();const p=await f.prepare(c);assert.equal(p.code,'another_task_selected');assert.equal(f.d.querySelectorAll('tbody input')[1].checked,true);assert.equal(f.counters.pause,0);}finally{f.close();}
});
test('selection changed during permission check does not pause a second task',async()=>{
  const f=fixture({second:true,onPermit:({d})=>d.querySelectorAll('tbody input')[1].click()});try{const c=await f.command('control_pause',false),p=await f.prepare(c);assert.equal((await f.submit(c,p)).submitted,false);assert.equal(f.counters.pause,0);assert.equal(f.d.querySelectorAll('tbody input')[1].checked,true);}finally{f.close();}
});
test('unknown or ambiguous second confirmation is left unconfirmed and never retried',async()=>{
  for(const opt of [{unknownConfirm:true},{doubleConfirm:true}]){const f=fixture(opt);try{const c=await f.command('control_pause',false),p=await f.prepare(c),r=await f.submit(c,p);assert.equal(r.submitted,true);assert.equal(r.editor_open,true);assert.equal(f.counters.confirm,0);assert.equal((await f.submit(c,p)).submitted,false);assert.equal(f.counters.pause,1);}finally{f.close();}}
});
test('stop is advertised only for a unique exact per-task end control',async()=>{
  const f=fixture({stop:true});try{const c=await f.command('control_stop','调控结束'),p=await f.prepare(c);assert.equal(f.counters.stop,0);assert.equal((await f.submit(c,p)).submitted,true);assert.equal(f.counters.stop,1);assert.equal(f.counters.confirm,1);assert.equal((await f.inspect()).surfaces[0].status,'调控结束');}finally{f.close();}
});
test('expired approval and non-increasing or non-half-hour target values cannot open an editor',async()=>{
  for(const [action,after] of [['control_budget_up','1500.00'],['control_budget_up','1800.001'],['control_duration_up','135.00'],['control_duration_up','60.00']]){
    const f=fixture();try{const c=await f.command(action,after);assert.equal((await f.prepare(c)).ok,false);assert.equal(f.d.querySelector('[role=dialog]'),null);}finally{f.close();}
  }
  const f=fixture();try{const c=await f.command();c.expires_at=new Date(Date.now()-1).toISOString();assert.equal((await f.prepare(c)).code,'approval_expired');}finally{f.close();}
});
test('wrong old value or ambiguous forms remain unsubmitted',async()=>{
  for(const opt of [{formBefore:'1400'},{extraInput:true}]){const f=fixture(opt);try{const c=await f.command();assert.equal((await f.prepare(c)).code,'editor_not_supported');assert.equal(f.counters.save,0);}finally{f.close();}}
});
