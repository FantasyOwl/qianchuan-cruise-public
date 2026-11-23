'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const source=fs.readFileSync(require.resolve('../extension/hybrid-home-dom.js'),'utf8');
const aid='1999999999999002',pid='8218616704149013',aweme='79987870170';

test('Ego locates a delayed parent row before hovering without refreshing or opening an ad form',async()=>{
 let row,waits=0;const f=fixture('/overall-prom',{onWait:(d,n)=>{waits=n;if(n===230)d.querySelector('tbody').append(row);}});
 f.command.executor_kind='ego';row=f.doc.querySelector('tr');row.remove();
 const result=await f.call('locate');assert.equal(result.ok,true,result.message);assert.equal(waits,230);
 assert.equal(f.doc.querySelector(result.hover_selector),row.querySelector('[data-qcx-home-hover]'));
 assert.equal(f.calls.chase,0);assert.equal(f.calls.newPlan,0);assert.equal(f.calls.submit,0);
});

test('locating a homepage first applies only its native cost viewing filter',async()=>{
 const f=fixture(),row=f.doc.querySelector('tr');row.remove();f.command.executor_kind='ego';
 f.doc.body.insertAdjacentHTML('afterbegin','<div><button>放量投放</button><button data-cost>控成本投放</button></div>');
 f.doc.querySelector('[data-cost]').onclick=()=>f.doc.querySelector('tbody').append(row);
 const result=await f.call('locate');assert.equal(result.ok,true,result.message);assert.equal(f.calls.chase,0);assert.equal(f.calls.submit,0);
});

test('view controls mounted after the route changes are applied once while awaiting the exact parent',async()=>{
 let row,clicks=0;const f=fixture('/overall-prom',{onWait:(d,n)=>{
   if(n===20){d.body.insertAdjacentHTML('afterbegin','<div role="tablist"><button role="tab" aria-selected="false" data-live>直播</button><button role="tab" aria-selected="true">商品</button></div>');
     d.querySelector('[data-live]').onclick=()=>{clicks++;d.querySelector('tbody').append(row);};}
 }});
 f.command.executor_kind='ego';row=f.doc.querySelector('tr');row.remove();
 const result=await f.call('locate');assert.equal(result.ok,true,result.message);assert.equal(clicks,1);assert.equal(f.calls.chase,0);assert.equal(f.calls.submit,0);
});

test('legacy home tabs without ARIA roles switch only the viewing scope from goods to live',async()=>{
 const f=fixture(),row=f.doc.querySelector('tr');row.remove();f.command.executor_kind='ego';
 f.doc.body.insertAdjacentHTML('afterbegin','<div><div class="tab"><div class="oc-popover"><span><span class="tab-label" data-live>推直播间</span></span></div></div><div class="tab active-tab"><span class="tab-label">推商品</span></div></div>');
 let clicks=0;f.doc.querySelector('[data-live]').onclick=()=>{clicks++;f.doc.querySelector('tbody').append(row);};
 const result=await f.call('locate');assert.equal(result.ok,true,result.message);assert.equal(clicks,1);assert.equal(f.calls.chase,0);assert.equal(f.calls.newPlan,0);assert.equal(f.calls.submit,0);
});

test('a verified parent can be hovered before lazy tools appear but cannot open an unverified tool',async()=>{
 const f=fixture();f.command.executor_kind='ego';f.doc.querySelector('[data-chase]').parentElement.remove();
 const result=await f.call('locate');assert.equal(result.ok,true,result.message);assert.equal(f.doc.querySelector(result.hover_selector).tagName,'TR');
 const opened=await f.call('open');assert.equal(opened.ok,false);assert.equal(f.calls.chase,0);assert.equal(f.calls.submit,0);
});

test('two plans cannot redirect the bound plus to the first or a conflicting anchor row',async()=>{
 const f=fixture(),d=f.doc,original=d.querySelector('tr');original.setAttribute('data-plan-id',pid);
 const other=original.cloneNode(true);other.setAttribute('data-plan-id','8200358026205459');other.setAttribute('data-aweme-id','42359868022');
 let foreign=0;other.querySelector('[data-chase]').onclick=()=>foreign++;original.before(other);
 let result=await f.call('open');assert.equal(result.opened,true,result.message);assert.equal(f.calls.chase,1);assert.equal(foreign,0);assert.equal(result.home.row_plan_id,pid);
 const conflict=fixture();conflict.doc.querySelector('tr').setAttribute('data-plan-id',pid);conflict.doc.querySelector('tr').setAttribute('data-aweme-id','42359868022');
 result=await conflict.call('open');assert.equal(result.ok,false);assert.equal(conflict.calls.chase,0);
});

test('native hover survives replacement of the parent row and stays on the exact material tool',async()=>{
 for(const kind of ['aweme','zlp']){
  const f=fixture('/uni-prom'),d=f.doc;f.command.executor_kind='ego';
  const row=d.querySelector('tr'),tool=d.querySelector('[data-chase]').parentElement;tool.className=kind==='aweme'?'assistTask-LRtjCS':'assist-task';
  if(kind==='zlp'){
   row.removeAttribute('data-aweme-id');f.binding.aweme_id='zlp_7777777';
   row.cells[0].insertAdjacentHTML('beforeend','<span id="zlp_7777777"></span>');
  }
  const result=await f.call('locate');assert.equal(result.ok,true,result.message);assert.match(result.hover_locator,/^xpath=/);
  const replacement=row.cloneNode(true);replacement.querySelectorAll('[data-qcx-home-hover]').forEach(e=>e.removeAttribute('data-qcx-home-hover'));row.replaceWith(replacement);
  assert.equal(d.querySelector(result.hover_selector),null);
  const found=d.evaluate(result.hover_locator.slice(6),d,null,f.dom.window.XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,null);
  assert.equal(found.snapshotLength,1);assert.match(found.snapshotItem(0).textContent,/素材\/人群追投/);
  const foreign=replacement.cloneNode(true);foreign.setAttribute('data-aweme-id','12345678900');foreign.querySelector('[id]')?.remove();replacement.before(foreign);
  assert.equal(d.evaluate(result.hover_locator.slice(6),d,null,f.dom.window.XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,null).snapshotLength,1);
  assert.equal(f.calls.chase,0);assert.equal(f.calls.submit,0);
 }
});

test('native lazy plus waits for the exact tool but never opens after parent budget changes',async()=>{
 for(const changeBudget of [false,true]){
  let button;const f=fixture('/uni-prom',{onWait:(d,n)=>{if(n===3){d.querySelector('.assist-task').append(button);if(changeBudget)d.querySelector('td').textContent='投放中 每日预算600000元 控成本投放';}}});
  f.command.executor_kind='ego';button=f.doc.querySelector('[data-chase]');button.parentElement.className='assist-task';button.remove();
  const r=await f.call('open');assert.equal(r.ok,!changeBudget,r.message);assert.equal(f.calls.chase,changeBudget?0:1);assert.equal(f.calls.submit,0);
 }
});
function fixture(path='/overall-prom',options={}){
 const d=new JSDOM(`<div>ID：${aid}</div><button data-new>＋投放直播间</button><table><tbody><tr data-aweme-id="${aweme}"><td>投放中 每日预算500000元 控成本投放</td><td><div><span>一键调速</span><button data-speed>＋</button></div><div><span>素材/人群追投</span><button data-chase title="创建任务">＋</button></div></td></tr></tbody></table>`,{url:'https://qianchuan.jinritemai.com'+path+'?aavid='+aid});
 const w=d.window,doc=w.document;Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});
 w.HTMLElement.prototype.getClientRects=function(){return this.isConnected?[{}]:[];};
 w.SVGElement.prototype.getClientRects=w.HTMLElement.prototype.getClientRects;
 const calls={chase:0,speed:0,newPlan:0,submit:0};doc.querySelector('[data-new]').onclick=()=>calls.newPlan++;doc.querySelector('[data-speed]').onclick=()=>calls.speed++;
 doc.querySelector('[data-chase]').onclick=()=>{
  calls.chase++;const root=doc.createElement('div');root.setAttribute('role','dialog');root.innerHTML=options.unknown?'<h2>未知窗口</h2>':'<h2>调控工具</h2><button>素材/人群追投</button><p>追投方式</p><button>添加视频</button><span>已添加：0/20</span><button data-submit>提交</button>';
  if(options.existing)root.insertAdjacentHTML('beforeend','<p>素材ID：1234567890</p>');
  root.querySelector('[data-submit]')?.addEventListener('click',()=>calls.submit++);doc.body.append(root);
 };
 const command={id:'cmd',run_id:'run',account_id:aid,plan_id:pid,group:'示例分组A',action:{id:'preview'},account_binding:{kind:'official_api_material_scope',account_id:aid,plan_id:pid,verified_at:new Date().toISOString()}};
 const binding={account_id:aid,plan_id:pid,group:'示例分组A',aweme_id:aweme,parent_budget:'500000',delivery_entry:'chengfang',parent_bid_type:'SMART_BID_CUSTOM',captured_at:new Date().toISOString(),detail_url:'https://qianchuan.jinritemai.com/overall-prom/detail?aavid='+aid+'&adId='+pid};
 let waits=0;const context=vm.createContext({URL,Date,document:doc,location:w.location,window:w,MouseEvent:w.MouseEvent,getComputedStyle:w.getComputedStyle.bind(w),chrome:{runtime:{sendMessage:async()=>{waits++;if(options.onWait)options.onWait(doc,waits);return {ok:true};}}}});vm.runInContext(source,context);
 return {dom:d,doc,command,binding,calls,context,call:phase=>context.hybridHomeDom(phase,{command,binding})};
}
test('both live home layouts open only the matched material plus into an empty native form',async()=>{
 for(const path of ['/overall-prom','/uni-prom']){
  const f=fixture(path),r=await f.call('open');assert.equal(r.opened,true,r.message);assert.equal(r.home.row_aweme_id,aweme);
  assert.equal(f.calls.chase,1);assert.equal(f.calls.speed,0);assert.equal(f.calls.newPlan,0);assert.equal(f.calls.submit,0);
  assert.equal(f.context.__qc_hybrid_editor_v1.action_id,'preview');
 }
});
test('a paused parent stops immediately with its real status instead of retrying or opening a chase',async()=>{
 let waits=0;const f=fixture('/overall-prom',{onWait:()=>waits++});f.command.direct_home=true;
 f.doc.querySelector('tr td').textContent='已暂停 直播间未开播 每日预算500000元 控成本投放';
 const result=await f.call('open');assert.equal(result.ok,false);assert.match(result.message,/已暂停或直播间未开播/);
 assert.equal(waits,0);assert.equal(f.calls.chase,0);assert.equal(f.calls.submit,0);
});
test('direct plan ID works while duplicate, conflicting or name-only rows safely fall back before opening',async()=>{
 const direct=fixture();direct.doc.querySelector('tr').removeAttribute('data-aweme-id');direct.doc.querySelector('tr').setAttribute('data-plan-id',pid);
 assert.equal((await direct.call('inspect')).home.row_plan_id,pid);
 for(const kind of ['duplicate','conflicting','nameOnly','ambiguousPlus']){
  const f=fixture(),row=f.doc.querySelector('tr');
  if(kind==='duplicate')row.parentElement.append(row.cloneNode(true));
  if(kind==='conflicting')row.setAttribute('data-plan-id','8356939714484742');
  if(kind==='nameOnly')row.removeAttribute('data-aweme-id');
  if(kind==='ambiguousPlus')f.doc.querySelector('[data-chase]').parentElement.append(f.doc.querySelector('[data-chase]').cloneNode(true));
  const result=await f.call('open');assert.equal(result.fallback,true,kind+result.message);assert.equal(f.calls.chase,0);assert.equal(f.calls.submit,0);
 }
});
test('wrong account, expired binding or changed mother budget never clicks an entry',async()=>{
 for(const kind of ['account','stale','budget']){
  const f=fixture();if(kind==='account')f.doc.querySelector('body>div').textContent='ID：8356939714484742';
  if(kind==='stale')f.binding.captured_at=new Date(Date.now()-91000).toISOString();if(kind==='budget')f.binding.parent_budget='100';
  const result=await f.call('open');assert.equal(result.ok,false);assert.equal(result.fallback,false);assert.equal(f.calls.chase,0);
 }
});
test('a collapsed account header needs fresh API ownership and still requires the exact mother row',async()=>{
 const f=fixture();f.doc.querySelector('body>div').remove();assert.equal((await f.call('inspect')).ok,true);
 f.command.account_binding.verified_at=new Date(Date.now()-601000).toISOString();assert.equal((await f.call('open')).ok,false);assert.equal(f.calls.chase,0);
});
test('existing manual drafts, unknown popups and nonempty new forms are preserved without fallback or submission',async()=>{
 const manual=fixture();manual.doc.body.insertAdjacentHTML('beforeend','<div role="dialog"><input value="人工草稿"></div>');
 const r=await manual.call('open');assert.equal(r.fallback,false);assert.equal(manual.calls.chase,0);assert.equal(manual.doc.querySelector('input').value,'人工草稿');
 for(const option of [{unknown:true},{existing:true}]){
  const f=fixture('/overall-prom',option),result=await f.call('open');assert.equal(result.ok,false);assert.equal(result.fallback,false);
  assert.equal(f.calls.chase,1);assert.equal(f.calls.submit,0);assert.equal(f.doc.querySelector('[role=dialog]').isConnected,true);
 }
});

test('an asynchronously mounted homepage row is awaited instead of immediately taking the detail fallback',async()=>{
 let row;const f=fixture('/overall-prom',{onWait:(d,n)=>{if(n===3)d.querySelector('tbody').append(row);}});
 row=f.doc.querySelector('tr');row.remove();const r=await f.call('open');assert.equal(r.opened,true,r.message);assert.equal(f.calls.chase,1);assert.equal(f.calls.submit,0);
});
test('the homepage cost-control view filter is clicked only outside rows and forms',async()=>{
 let row;const f=fixture(),d=f.doc;row=d.querySelector('tr');row.remove();
 d.body.insertAdjacentHTML('afterbegin','<div><button>放量投放</button><button data-cost>控成本投放</button></div>');
 let filters=0;d.querySelector('[data-cost]').onclick=()=>{filters++;d.querySelector('tbody').append(row);};
 const r=await f.call('open');assert.equal(r.opened,true,r.message);assert.equal(filters,1);assert.equal(f.calls.newPlan,0);assert.equal(f.calls.speed,0);assert.equal(f.calls.submit,0);
});
test('an unavailable mother row returns bounded rendered ID diagnostics and never guesses by name',async()=>{
 const f=fixture();f.doc.querySelector('tr').removeAttribute('data-aweme-id');f.doc.querySelector('tr').setAttribute('data-row-key',aweme);
 const r=await f.call('open');assert.equal(r.fallback,true);assert.match(r.message,/可见行1、匹配0/);assert.match(r.message,new RegExp(aweme));assert.equal(f.calls.chase,0);
});
test('official UID ownership is required when the detail label was collapsed',async()=>{
 const f=fixture();f.binding.aweme_source='official_plan_list';let r=await f.call('open');assert.equal(r.ok,false);assert.equal(f.calls.chase,0);
 f.command.parent_anchor={kind:'unique_official_plan_anchor',account_id:aid,plan_id:pid,aweme_id:aweme,captured_at:new Date().toISOString()};
 r=await f.call('open');assert.equal(r.opened,true,r.message);assert.equal(f.calls.chase,1);assert.equal(f.calls.submit,0);
});

test('a tooltip wrapper delegates the click through its actual native add icon',async()=>{
 const f=fixture(),d=f.doc,button=d.querySelector('[data-chase]'),handler=button.onclick;
 const wrap=d.createElement('span');wrap.title='创建任务';
 wrap.innerHTML='<svg name="oc-icon-light-add"></svg>';wrap.querySelector('svg').addEventListener('click',handler);button.replaceWith(wrap);
 const r=await f.call('open');assert.equal(r.opened,true,r.message);assert.equal(f.calls.chase,1);assert.equal(f.calls.speed,0);assert.equal(f.calls.newPlan,0);assert.equal(f.calls.submit,0);
});

test('the native empty adId homepage parameter is accepted while foreign and duplicate values stop',async()=>{
 for(const suffix of ['&adId=','&adId=8356939714484742','&adId=&adId=']){
  const f=fixture();f.dom.reconfigure({url:'https://qianchuan.jinritemai.com/uni-prom?aavid='+aid+suffix});
  const r=await f.call('inspect');assert.equal(r.ok,suffix==='&adId=',r.message);assert.equal(f.calls.chase,0);
 }
});

test('Ego uses exact Aurora plan row keys with a fresh cost binding, never a different plan or ramp mode',async()=>{
 for(const variant of ['correct','foreign','ramp']){
  const f=fixture();f.command.executor_kind='ego';const row=f.doc.querySelector('tr');row.removeAttribute('data-aweme-id');
  row.setAttribute('data-row-key',variant==='foreign'?'8356939714484742':pid);
  row.cells[0].textContent='投放中 每日预算500000元 综合营销ROI目标 3.20'+(variant==='ramp'?' 放量投放':'');
  const r=await f.call('inspect');assert.equal(r.ok,variant==='correct',r.message);assert.equal(f.calls.chase,0);
 }
});

test('Ego accepts the exact native zlp owner only with the original detail binding and never opens a disabled plus',async()=>{
 const f=fixture('/uni-prom');f.command.executor_kind='ego';f.binding.aweme_id='zlp_7777777';f.binding.aweme_source='visible_detail';
 f.doc.querySelector('tr').setAttribute('data-aweme-id','zlp_7777777');
 assert.equal((await f.call('inspect')).ok,true);
 delete f.command.executor_kind;assert.equal((await f.call('inspect')).ok,false);
 f.command.executor_kind='ego';f.doc.querySelector('[data-chase]').className='icon-not-allowed';
 const r=await f.call('open');assert.equal(r.ok,false);assert.match(r.message,/平台当前不允许/);assert.equal(f.calls.chase,0);
});

test('a mounted empty native dialog can finish loading and ownership is recorded before filling',async()=>{
 const f=fixture('/uni-prom',{onWait:(d,n)=>{if(n===2){const r=d.querySelector('[role="dialog"]');r.innerHTML='<p>调控工具</p><button>素材/人群追投</button><p>追投方式</p><button>添加视频</button><p>已添加：0/20</p><button>取消</button>';}}});
 f.doc.querySelector('[data-chase]').onclick=()=>{f.calls.chase++;f.doc.body.insertAdjacentHTML('beforeend','<div role="dialog"></div>');};
 const r=await f.call('open');assert.equal(r.opened,true,r.message);assert.equal(f.context.__qc_hybrid_editor_v1.action_id,'preview');assert.equal(f.calls.chase,1);assert.equal(f.calls.submit,0);
});

test('/uni-prom ignores 自运营 ID inside breadcrumb selector, aweme-info, or content-info-container while still rejecting foreign account IDs',async()=>{
 const f=fixture('/uni-prom'),d=f.doc;
 d.body.insertAdjacentHTML('afterbegin','<div class="ovui-breadcrumb"><div class="oc-breadcrumb-select"><span>自运营</span><span>ID:96646191090</span></div></div><div class="aweme-info"><span>自运营</span><span class="oc-typography-value-int">ID:96646191090</span></div><div class="content-info-container"><div class="right_id">ID:79987870170</div></div>');
 assert.equal((await f.call('inspect')).ok,true);
 d.body.insertAdjacentHTML('afterbegin','<div class="shop-name">ID：8356939714484742</div>');
 const r=await f.call('inspect');assert.equal(r.ok,false);assert.match(r.message,/首页可见账户ID冲突/);
});

test('chooseLiveView switches oc-space-item wrapped 推直播间 tab before locating parent row',async()=>{
 const f=fixture('/uni-prom'),d=f.doc;
 const row=d.querySelector('tr');row.remove();
 d.body.insertAdjacentHTML('afterbegin','<div class="oc-space"><div class="oc-space-item"><div class="tab"><span class="tab-label">推直播间</span></div></div><div class="oc-space-item"><div class="tab active-tab"><span class="tab-label">推商品</span></div></div></div>');
 let switched=0;
 d.querySelector('.tab-label').onclick=()=>{
  switched++;
  d.querySelectorAll('.tab')[0].classList.add('active-tab');
  d.querySelectorAll('.tab')[1].classList.remove('active-tab');
  d.querySelector('tbody').append(row);
 };
 const r=await f.call('locate');
 assert.equal(r.ok,true,r.message);assert.equal(switched,1);assert.equal(f.calls.chase,0);
});
