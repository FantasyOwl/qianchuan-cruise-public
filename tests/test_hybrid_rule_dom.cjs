'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const {JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom');
const source=fs.readFileSync(require.resolve('../extension/hybrid-dom.js'),'utf8');
const aid='1999999999999002',pid='8218616704149013',tid='8126540111342844';
function fixture({mode='cost_control',missingRoi=false,wrongCount=false,allow=true,plan=false,nativeRemaining=null,rejectOnSubmit=false}={}){
  const columns=['','任务名称','素材','调控状态','调控预算(元)','调控时长(小时)','追投方式','创建时间','调控消耗','调控支付ROI','调控综合成本'];
  const dom=new JSDOM(`<h1>千川乘方</h1><div>ID：${aid}\n</div><div>计划ID：${pid}\n预算(元)：每日500,000.00\n</div>
    <div class="parent-goal"><span>综合营销ROI目标：</span><span>3.40</span><iconpark-icon name="oc-icon-light-edit"></iconpark-icon></div>
    <button role="switch" aria-checked="true"></button><input placeholder="开始日期" value="2026-11-11"><input placeholder="结束日期" value="2026-11-11">
    <table><thead><tr>${columns.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody><tr>
    <td><input type="checkbox"></td><td>原追投任务\nID: ${tid}\n</td><td>共5条视频</td><td>调控中\n<iconpark-icon name="iconPause"></iconpark-icon><iconpark-icon name="oc-icon-light-edit"></iconpark-icon></td>
    <td>200.00</td><td>${mode==='volume'?'2.0\n预计 11-11 18:00 结束':'长期投放'}</td><td>${mode==='volume'?'放量追投':'控成本追投'}</td><td>2026-11-11\n08:00:00</td><td>50.00</td><td>${missingRoi?'-':'4.00'}</td><td>50.00</td></tr></tbody></table>
    <div class="pagination"><span>共 1 条记录</span><input value="100条/页"><li class="page-item--active">1</li></div>`,{url:`https://qianchuan.jinritemai.com/overall-prom/detail?aavid=${aid}&adId=${pid}`});
  const w=dom.window;
  Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});
  w.HTMLElement.prototype.getClientRects=function(){return this.isConnected?[{width:100,height:30}]:[];};
  let submits=0,cancels=0,finals=0;
  function form(isPlan=false){const r=w.document.createElement('div');r.className='main-vmok-plugin-modal__wrap';
    r.innerHTML=isPlan?'<label>综合营销ROI目标<input type="number" step="0.01" value="3.4"></label>':`<h3>编辑素材追投</h3><p>已添加：${wrongCount?4:5}/20</p><label>${mode==='volume'?'调控总预算':'调控日预算'}<input type="number" step="0.01" value="200"></label><label>调控时长<input type="number" step="0.5" value="2"></label><label>我的出价<input value="20.88"></label><input placeholder="任务名称" value="原追投任务">`;
    r.innerHTML+='<button data-submit>提交</button><button data-cancel>取消</button>';
    if(nativeRemaining!==null)r.innerHTML+=`<p>当前最多可再追加预算 ${nativeRemaining} 元</p>`;
    r.querySelector('[data-submit]').onclick=()=>{if(rejectOnSubmit){r.insertAdjacentHTML('beforeend','<p>当前最多可再追加预算 1 元</p><p>不得超过当前可用预算，请重新填写</p>');return;}submits++;r.remove();};r.querySelector('[data-cancel]').onclick=()=>{cancels++;r.remove();};w.document.body.append(r);
  }
  w.document.querySelector('tbody iconpark-icon[name="oc-icon-light-edit"]').onclick=()=>form();
  w.document.querySelector('.parent-goal iconpark-icon').onclick=()=>form(true);
  w.document.querySelector('iconpark-icon[name="iconPause"]').onclick=()=>{const r=w.document.createElement('div');r.className='ovui-modal';r.innerHTML='<p>确认暂停该任务</p><button>确认</button>';r.querySelector('button').onclick=()=>{submits++;r.remove();};w.document.body.append(r);};
  const context={window:w,document:w.document,location:w.location,getComputedStyle:w.getComputedStyle,Event:w.Event,MouseEvent:w.MouseEvent,KeyboardEvent:w.KeyboardEvent,HTMLInputElement:w.HTMLInputElement,crypto:require('node:crypto').webcrypto,URL,URLSearchParams,Date,setTimeout:()=>{throw Error('page timers unavailable');},chrome:{runtime:{sendMessage:async m=>{if(m.action==='hybrid-wait')return {ok:true};if(m.action==='rule-final-check'){finals++;return {allow};}throw Error('wrong channel');}}}};
  vm.createContext(context);vm.runInContext(source,context);
  const c={id:'command',run_id:'run',account_id:aid,plan_id:pid,group:'测试户',phase:'tasks',rule_phase:'prepare',rule_job:true,page:1,date_start:'2026-11-11',date_end:'2026-11-11',expires_at:new Date(Date.now()+60000).toISOString()};
  const invoke=(phase,args={})=>context.hybridChaseDom(phase,{command:c,...args});
  return {w,c,context,invoke,stats:()=>({submits,cancels,finals})};
}
async function prepare(f,kind,after){
  const read=await f.invoke('rule_read');assert.equal(read.ok,true,read.message);
  const s=kind==='plan_roi'?read.observation.plan_surface:read.observation.rule_rows[0];
  const before=kind==='pause_task'?true:kind==='task_duration'?'2':kind==='plan_roi'?'3.40':'200';
  f.c.action={id:'action',intent:{execution_channel:'browser',action:kind,task_id:tid,before,after,browser_surface:s}};
  return f.invoke('rule_prepare');
}

test('native remaining quota rejects a budget increment before any final permit',async()=>{
 const f=fixture({nativeRemaining:14.96});const r=await prepare(f,'task_budget','300');
 assert.equal(r.ok,false);assert.match(r.message,/可追加预算不足/);assert.deepEqual(f.stats(),{submits:0,cancels:1,finals:0});
});
test('native quota compares the increment, not the whole target budget',async()=>{
 const f=fixture({nativeRemaining:100});const r=await prepare(f,'task_budget','300');
 assert.equal(r.ok,true,r.message);const result=await f.invoke('rule_submit',{nonce:r.nonce});assert.equal(result.submitted,true);assert.equal(f.stats().submits,1);
});
test('a quota change at native submit records rejection and cancels without reading through an open editor',async()=>{
 const f=fixture({rejectOnSubmit:true}),r=await prepare(f,'task_budget','300');assert.equal(r.ok,true,r.message);
 const result=await f.invoke('rule_submit',{nonce:r.nonce});assert.equal(result.ok,true,result.message);assert.equal(result.rejected,true);assert.equal(result.submitted,false);
 assert.equal(result.observation.validation_rejection.available,1);assert.deepEqual(f.stats(),{submits:0,cancels:1,finals:1});
});
test('legacy rejected budget editor is cancelled only with exact original identity',async()=>{
 const f=fixture();const r=await prepare(f,'task_budget','300');assert.equal(r.ok,true,r.message);
 const root=f.w.document.querySelector('.main-vmok-plugin-modal__wrap');root.insertAdjacentHTML('beforeend','<p>当前最多可再追加预算 1 元</p><p>不得超过当前可用预算，请重新填写</p>');
 delete f.context.__qc_hybrid_rule_editor_v1;f.c.executor_kind='ego';f.c.rule_phase='verify';
 const recovered=await f.invoke('editor_recover');assert.equal(recovered.cleaned,true,recovered.message);assert.equal(recovered.validation_rejection.available,1);assert.equal(f.stats().submits,0);assert.equal(f.stats().cancels,1);
});
test('retired homepage picker cleanup uses owned action scope without expiring detail identity',async()=>{
 const f=fixture();f.w.history.replaceState({},'',`/overall-prom?aavid=${aid}`);f.c.executor_kind='ego';f.c.cleanup_action_ids=['retired'];
 const root=f.w.document.createElement('div');root.setAttribute('role','dialog');root.innerHTML='<h3>新建调控任务</h3><button>取消</button>';f.w.document.body.append(root);root.querySelector('button').onclick=()=>root.remove();
 f.context.__qc_hybrid_editor_v1={action_id:'retired',root,home_context:{account_id:aid,plan_id:pid},picker_open:true};
 const r=await f.invoke('editor_recover');assert.equal(r.cleaned,true,r.message);assert.equal(root.isConnected,false);assert.equal(f.stats().submits,0);
});
test('an unowned homepage editor is preserved',async()=>{
 const f=fixture();f.w.history.replaceState({},'',`/overall-prom?aavid=${aid}`);f.c.executor_kind='ego';f.c.cleanup_action_ids=['different'];
 const root=f.w.document.createElement('div');root.setAttribute('role','dialog');root.innerHTML='<button>取消</button>';f.w.document.body.append(root);
 f.context.__qc_hybrid_editor_v1={action_id:'retired',root,home_context:{account_id:aid,plan_id:pid}};
 const r=await f.invoke('editor_recover');assert.equal(r.cleaned,false);assert.equal(root.isConnected,true);
});

test('owned retired video picker with only Aurora header close cancels without confirming selected IDs',async()=>{
 const f=fixture(),d=f.w.document;f.w.history.replaceState({},'',`/overall-prom?aavid=${aid}`);f.c.executor_kind='ego';f.c.cleanup_action_ids=['retired'];
 const root=d.createElement('div');root.setAttribute('role','dialog');root.innerHTML='<h3>新建调控任务</h3><button><span>取消</span></button>';d.body.append(root);root.querySelector('button').onclick=()=>root.remove();
 const picker=d.createElement('div');picker.setAttribute('role','dialog');picker.innerHTML='<h3>添加视频</h3><input placeholder="输入视频名称/ID后回车搜索"><button aria-label="关闭"><span aria-label="关闭"><span aria-label="close"></span></span></button><button data-confirm>确定</button>';d.body.append(picker);
 let confirmed=0,closed=0;picker.querySelector('[data-confirm]').onclick=()=>confirmed++;picker.querySelector('button').onclick=()=>{closed++;picker.remove();};
 f.context.__qc_hybrid_editor_v1={action_id:'retired',root,home_context:{account_id:aid,plan_id:pid},picker_open:true};
 const r=await f.invoke('editor_recover');assert.equal(r.cleaned,true,r.message);assert.equal(closed,1);assert.equal(confirmed,0);assert.equal(root.isConnected,false);assert.equal(f.stats().submits,0);
});

test('ambiguous picker close buttons preserve both owned forms and never confirm',async()=>{
 const f=fixture(),d=f.w.document;f.w.history.replaceState({},'',`/overall-prom?aavid=${aid}`);f.c.executor_kind='ego';f.c.cleanup_action_ids=['retired'];
 const root=d.createElement('div');root.setAttribute('role','dialog');root.innerHTML='<button>取消</button>';d.body.append(root);let clicks=0;root.querySelector('button').onclick=()=>clicks++;
 const picker=d.createElement('div');picker.setAttribute('role','dialog');picker.innerHTML='<h3>添加视频</h3><input placeholder="输入视频名称/ID后回车搜索"><button aria-label="关闭"></button><button aria-label="关闭"></button><button>确定</button>';d.body.append(picker);for(const b of picker.querySelectorAll('button'))b.onclick=()=>clicks++;
 f.context.__qc_hybrid_editor_v1={action_id:'retired',root,home_context:{account_id:aid,plan_id:pid},picker_open:true};
 const r=await f.invoke('editor_recover');assert.equal(r.ok,false);assert.match(r.message,/入口不唯一/);assert.equal(clicks,0);assert.equal(root.isConnected,true);assert.equal(picker.isConnected,true);
});

test('a manually changed field prevents even the owned picker header close',async()=>{
 const f=fixture(),d=f.w.document;f.w.history.replaceState({},'',`/overall-prom?aavid=${aid}`);f.c.executor_kind='ego';f.c.cleanup_action_ids=['retired'];
 const root=d.createElement('div');root.setAttribute('role','dialog');root.innerHTML='<input value="人工已改"><button>取消</button>';d.body.append(root);
 const picker=d.createElement('div');picker.setAttribute('role','dialog');picker.innerHTML='<h3>添加视频</h3><input placeholder="输入视频名称/ID后回车搜索"><button aria-label="关闭"></button>';d.body.append(picker);let clicks=0;picker.querySelector('button').onclick=()=>clicks++;
 f.context.__qc_hybrid_editor_v1={action_id:'retired',root,home_context:{account_id:aid,plan_id:pid},picker_open:true,fields:[{input:root.querySelector('input'),value:'原值'}]};
 const r=await f.invoke('editor_recover');assert.equal(r.ok,false);assert.match(r.message,/保留手动编辑/);assert.equal(clicks,0);assert.equal(picker.isConnected,true);
});

test('Ego rule and F5 inventory scans choose 100 rows after clearing the old search',async()=>{
 for(const ruleJob of [true,false]){
 const f=fixture();f.c.executor_kind='ego';f.c.rule_phase='scan';f.c.rule_job=ruleJob;
 const tab=f.w.document.createElement('div');tab.setAttribute('role','tab');tab.setAttribute('aria-selected','true');tab.textContent='调控';f.w.document.body.append(tab);
 const filters=f.w.document.createElement('section');filters.innerHTML='<label>投放状态：<input value="调控中"></label><input placeholder="输入任务名称或ID搜索" value="8642296033334962">';f.w.document.body.append(filters);
 const input=f.w.document.querySelector('.pagination input');input.value='10条/页';
 input.onclick=()=>{const menu=f.w.document.createElement('div');menu.innerHTML=['10','20','50','100'].map(n=>`<span role="option">${n}条/页</span>`).join('');for(const option of menu.children)option.onclick=()=>{input.value=option.textContent;menu.remove();};f.w.document.body.append(menu);};
 const cleared=await f.invoke('view');assert.equal(cleared.ok,true,cleared.message);assert.equal(filters.querySelector('[placeholder]').value,'');assert.equal(input.value,'10条/页');
 const result=await f.invoke('view');assert.equal(result.ok,true,result.message);assert.equal(result.changed,true);assert.equal(input.value,'100条/页');assert.equal(f.stats().submits,0);
 }
});

test('pre-navigation editor check is read-only and never substitutes for account identity',async()=>{
 const f=fixture();f.w.document.querySelector('body > div').remove();
 assert.equal((await f.invoke('editor_guard')).ok,true);
 const read=await f.invoke('rule_read');assert.equal(read.ok,false);assert.match(read.message,/账户ID/);assert.equal(f.stats().submits,0);
});

test('rule hover identifies the visible status cell while native edit icons remain hidden',async()=>{
 const f=fixture();const read=await f.invoke('rule_read'),s=read.observation.rule_rows[0];
 f.c.action={id:'action',intent:{execution_channel:'browser',action:'task_budget',task_id:tid,before:'200',after:'300',browser_surface:s}};
 f.w.document.querySelector('tbody iconpark-icon[name="oc-icon-light-edit"]').style.display='none';
 const target=await f.invoke('rule_hover');assert.equal(target.ok,true,target.message);
 assert.equal(f.w.document.querySelector(target.hover_selector).tagName,'TD');assert.equal(f.stats().submits,0);
});
test('native task rule reader keeps exact task, payment ROI, duration units and parent ROI basis',async()=>{
  const f=fixture({mode:'volume'}),r=await f.invoke('rule_read');assert.equal(r.ok,true,r.message);
  const s=r.observation.rule_rows[0];assert.equal(s.task_id,tid);assert.equal(s.duration_minutes,'120');assert.equal(s.metrics.roi,4);assert.equal(s.context.control_type,'material_chase');assert.equal(r.observation.plan_surface.roi_basis,'marketing');
});
test('missing payment ROI stays missing and does not become marketing ROI',async()=>{
  const f=fixture({missingRoi:true}),r=await f.invoke('rule_read');assert.equal(r.ok,true);assert.equal(r.observation.rule_rows[0].metric_basis,'unknown');assert.equal(r.observation.rule_rows[0].metrics.roi,undefined);
});
test('native task budget opens its status edit icon and changes only budget after final permit',async()=>{
  const f=fixture(),p=await prepare(f,'task_budget','300');assert.equal(p.prepared,true,p.message);
  assert.equal(f.w.document.querySelector('input[value="20.88"]').value,'20.88');
  assert.equal((await f.invoke('rule_submit',{nonce:p.nonce})).submitted,true);assert.deepEqual(f.stats(),{submits:1,cancels:0,finals:1});
});
test('volume duration edits total native hours without changing budget',async()=>{
  const f=fixture({mode:'volume'}),p=await prepare(f,'task_duration','3');assert.equal(p.prepared,true,p.message);
  assert.equal((await f.invoke('rule_submit',{nonce:p.nonce})).submitted,true);assert.equal(f.stats().submits,1);
});
test('pause targets only the original row and a unique native confirmation',async()=>{
  const f=fixture(),p=await prepare(f,'pause_task',false);assert.equal(p.prepared,true,p.message);
  assert.equal((await f.invoke('rule_submit',{nonce:p.nonce})).submitted,true);assert.equal(f.stats().submits,1);
});
test('changed material count cancels the editor before any update',async()=>{
  const f=fixture({wrongCount:true}),p=await prepare(f,'task_budget','300');assert.equal(p.ok,false);assert.match(p.message,/素材数量/);assert.equal(f.stats().cancels,1);assert.equal(f.stats().submits,0);
});
test('changes to other fields reject submit; cleanup restores only our value and cancels',async()=>{
  const f=fixture(),p=await prepare(f,'task_budget','300');f.w.document.querySelector('input[value="20.88"]').value='10';
  const r=await f.invoke('rule_submit',{nonce:p.nonce});assert.equal(r.ok,false);assert.match(r.message,/非目标字段/);await f.invoke('rule_cleanup');assert.equal(f.stats().submits,0);assert.equal(f.stats().cancels,1);
});
test('revoked final permission and forged nonce never click submit',async()=>{
  const f=fixture({allow:false}),p=await prepare(f,'task_budget','300');
  assert.equal((await f.invoke('rule_submit',{nonce:'wrong'})).ok,false);assert.equal(f.stats().finals,0);
  assert.equal((await f.invoke('rule_submit',{nonce:p.nonce})).ok,false);await f.invoke('rule_cleanup');assert.equal(f.stats().submits,0);
});
test('ROI update binds exact native marketing goal and does not change budget',async()=>{
  const f=fixture(),p=await prepare(f,'plan_roi','3.5');assert.equal(p.prepared,true,p.message);assert.equal((await f.invoke('rule_submit',{nonce:p.nonce})).submitted,true);assert.equal(f.stats().submits,1);
});
test('presentation metadata and harmless currency formatting do not change the native contract',async()=>{
 const f=fixture();const r=await f.invoke('rule_read'),s=r.observation.rule_rows[0];
 f.c.action={id:'action',intent:{execution_channel:'browser',action:'pause_task',task_id:tid,before:true,after:false,browser_surface:{...s,budget:'200',page:7,extra_display_hint:'保'}}};
 const p=await f.invoke('rule_prepare');assert.equal(p.prepared,true,p.message);await f.invoke('rule_cleanup');assert.equal(f.stats().submits,0);
});
test('a real changed budget names the exact differing field before opening an editor',async()=>{
 const f=fixture();const r=await f.invoke('rule_read'),s=r.observation.rule_rows[0];
 f.c.action={id:'action',intent:{execution_channel:'browser',action:'task_budget',task_id:tid,before:'100',after:'300',browser_surface:{...s,budget:'100'}}};
 const p=await f.invoke('rule_prepare');assert.equal(p.ok,false);assert.match(p.message,/调控预算 100.00 → 200.00/);assert.equal(f.stats().submits,0);
});
test('an omitted optional duration is the same unlimited duration as explicit null',async()=>{
 const f=fixture();const r=await f.invoke('rule_read'),s={...r.observation.rule_rows[0]};delete s.duration_minutes;
 f.c.action={id:'action',intent:{execution_channel:'browser',action:'pause_task',task_id:tid,before:true,after:false,browser_surface:s}};
 const p=await f.invoke('rule_prepare');assert.equal(p.prepared,true,p.message);await f.invoke('rule_cleanup');assert.equal(f.stats().submits,0);
});
test('normalizing an absent duration never accepts a changed actual fixed duration',async()=>{
 const f=fixture({mode:'volume'});const r=await f.invoke('rule_read'),s={...r.observation.rule_rows[0]};delete s.duration_minutes;
 f.c.action={id:'action',intent:{execution_channel:'browser',action:'pause_task',task_id:tid,before:true,after:false,browser_surface:s}};
 const p=await f.invoke('rule_prepare');assert.equal(p.ok,false);assert.match(p.message,/调控时长 null → 120.00/);assert.equal(f.stats().submits,0);
});
