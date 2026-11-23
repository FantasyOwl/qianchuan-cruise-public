'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const source=fs.readFileSync(require.resolve('../extension/hybrid-dom.js'),'utf8');
const aid='1999999999999002',pid='8218616704149013',ids=['8533524212032260758','8303390901814024325'];
function fixture(path='/overall-prom/detail',options={}){
 const dom=new JSDOM(`<h1>千川乘方</h1>
<div>ID：${aid}\n</div><div>计划ID：${pid}\n预算(元)：每日500,000.00\n综合营销ROI目标：3.40\n</div>
 <button role="switch" aria-checked="true"></button><input placeholder="开始日期" value="2026-11-06"><input placeholder="结束日期" value="2026-11-10">
 <div class="main-vmok-plugin-radio-item main-vmok-plugin-radio-item--checked">视频</div><button data-chase>追投</button><table><thead><tr>${['','视频','审核状态','素材追投','整体消耗(元)','整体支付ROI'].map(s=>`<th>${s}</th>`).join('')}</tr></thead><tbody>
 ${ids.map((id,i)=>`<tr><td><input type="checkbox"></td><td>素材${i}\n素材ID: ${id}\n</td><td>审核通过</td><td>-</td><td>${400-i*100}</td><td>4.2</td></tr>`).join('')}</tbody></table>
 <div class="main-vmok-plugin-pagination"><span>共 2 条记录</span><input value="100条/页"><li class="main-vmok-plugin-page-turner__item--active">1</li></div>`,
 {url:'https://qianchuan.jinritemai.com'+path+'?aavid='+aid+'&adId='+pid+'&dateRange=2026-11-06,2026-11-10#?adDetailCreativeSubTab=1'});
 const w=dom.window;
 [...w.document.querySelectorAll('th')].find(e=>e.textContent==='整体消耗(元)').setAttribute('aria-sort','descending');
 Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});
 w.HTMLElement.prototype.getClientRects=function(){for(let e=this;e;e=e.parentElement)if(e.style?.display==='none')return [];return this.isConnected?[{width:100,height:30}]:[];};
 let submits=0,allowed=true,selectedState=[];
 if(options.controlledSelection){
  const tbody=w.document.querySelector('tbody'),markup=tbody.innerHTML;
  function redraw(selected){
   selectedState=selected.slice();tbody.innerHTML=markup;
   [...tbody.querySelectorAll('tr')].forEach((row,index)=>{
    const box=row.querySelector('input'),snapshot=selected.slice();box.checked=snapshot.includes(ids[index]);
    box.onchange=()=>{const next=box.checked?[...snapshot,ids[index]]:snapshot.filter(id=>id!==ids[index]);w.setTimeout(()=>redraw(next),10);};
   });
  }
  redraw([]);
 }
 w.document.querySelector('[data-chase]').onclick=()=>{
  const root=w.document.createElement('div');root.className='main-vmok-plugin-modal__wrap';
  root.innerHTML='<div>追投方式</div><button>控成本追投</button><button>放量追投</button><button>直播间成交</button>'
   +(options.delayMaterials?'':(options.controlledSelection?selectedState:ids).map(id=>`<p>素材ID: ${id}</p>`).join(''))
   +'<label>调控日预算<input type="number" step="0.01"></label><label>直播间成交出价<input type="number" step="0.01"></label><label>任务名称<input></label><button data-submit>提交</button><button data-cancel>取消</button>';
  root.querySelector('[data-submit]').onclick=()=>submits++;
  root.querySelector('[data-cancel]').onclick=()=>root.remove();w.document.body.append(root);
  if(options.delayMaterials)w.setTimeout(()=>root.insertAdjacentHTML('afterbegin',ids.map(id=>`<p>素材ID: ${id}</p>`).join('')),80);
 };
 const context=vm.createContext({URL,URLSearchParams,Date,crypto:require('node:crypto').webcrypto,document:w.document,location:w.location,
  getComputedStyle:w.getComputedStyle.bind(w),Event:w.Event,MouseEvent:w.MouseEvent,KeyboardEvent:w.KeyboardEvent,HTMLInputElement:w.HTMLInputElement,window:w,
  chrome:{runtime:{sendMessage:async message=>{if(message.action==='hybrid-wait'){await new Promise(r=>setTimeout(r,message.duration_ms));return {ok:true};}return {allow:allowed};}}},setTimeout:options.pageTimersBlocked?()=>{throw Error('page timer throttled');}:setTimeout,clearTimeout});
 vm.runInContext(source,context);
 const command={account_id:aid,plan_id:pid,group:'测试户',phase:'prepare',page:1,date_start:'2026-11-06',date_end:'2026-11-10',
  action:{id:'one',intent:{browser_batch:{material_ids:ids,page:1,date_start:'2026-11-06',date_end:'2026-11-10',task_name:'QCX_fixture',
   after:{chase_type:'控成本追投',optimization_target:'直播间成交',daily_budget:200,bid_value:20.88}}}}};
 return {dom,w,context,command,call:(phase,args={})=>context.hybridChaseDom(phase,{command,...args}),get submits(){return submits;},set allowed(v){allowed=v;}};
}

function taskFixture(name='QCX_fixture',ellipsisOnly=false){
 const f=fixture(),d=f.w.document;f.command.phase='verify';
 const labels=['','任务名称','素材','调控状态','调控预算(元)','综合营销ROI目标','支付ROI目标','出价','调控时长(小时)','追投方式'];
 const t=d.querySelector('table');t.querySelector('thead').innerHTML='<tr>'+labels.map(s=>`<th>${s}</th>`).join('')+'</tr>';
 t.querySelector('tbody').innerHTML=`<tr><td></td><td>${name}\nID: 8317812444501003</td><td><div data-auto-id="promotion-media-group"><div class="main-vmok-plugin-oc-promotion-media-group-detail"></div><span>共2条视频</span></div></td><td>调控中</td><td>200.00</td><td>-</td><td>-</td><td>20.88</td><td>长期投放</td><td>控成本追投</td></tr>`;
 d.querySelector('.main-vmok-plugin-pagination span').textContent='共 1 条记录';
 const open=()=>{
  if(d.querySelector('[data-proof-popover]'))return;
  const p=d.createElement('div');p.className='main-vmok-plugin-popover';p.dataset.proofPopover='true';p.textContent=ids.map(id=>'素材ID：'+id).join('\n');d.body.append(p);
 };
 if(ellipsisOnly){
  const list=d.createElement('div');list.className='main-vmok-plugin-oc-promotion-media-group-list';
  const more=d.createElement('div');more.className='main-vmok-plugin-oc-promotion-media-group-list-ellipsis';list.append(more);
  d.querySelector('.main-vmok-plugin-oc-promotion-media-group-detail').append(list);more.addEventListener('click',open);
 }else d.querySelector('.main-vmok-plugin-oc-promotion-media-group-detail').addEventListener('mouseenter',open);
 d.querySelector('.main-vmok-plugin-oc-promotion-media-group-detail').addEventListener('mouseleave',()=>d.querySelector('[data-proof-popover]')?.remove());
 return f;
}
test('native task material readback opens its thumbnail ellipsis rather than the inert count label',async()=>{
 const f=taskFixture('QCX_fixture',true),result=await f.call('verify');
 assert.equal(result.ok,true,result.message);assert.deepEqual([...result.observation.rows[0].material_ids],ids);
 assert.equal(result.observation.rows[0].id,'8317812444501003');assert.equal(f.submits,0);
});
test('current Chengfang detail reads exact IDs and payment ROI without needing a foreground mouse',async()=>{
 const f=fixture(),v=await f.call('read');assert.equal(v.ok,true,v.message);
 assert.equal(v.observation.path,'/overall-prom/detail');assert.equal(v.observation.parent_budget,'500000.00');
 assert.equal(v.observation.delivery_entry,'chengfang');assert.equal(v.observation.rows[0].id,ids[0]);
 assert.equal(v.observation.rows[0].roi,'4.2');assert.equal(f.submits,0);
});

test('a collapsed account label can use fresh API scope only with the exact visible parent and URL',async()=>{
 const f=fixture(),d=f.w.document;d.querySelector('div').remove();
 f.command.account_binding={kind:'official_api_material_scope',account_id:aid,plan_id:pid,verified_at:new Date().toISOString()};
 assert.equal((await f.call('identity')).ok,true);
 d.querySelector('div').textContent='计划ID：8086606404052108';
 assert.equal((await f.call('identity')).ok,false);assert.equal(f.submits,0);
});

test('missing, foreign or expired API scope cannot stand in for a hidden account label',async()=>{
 for(const binding of [null,{account_id:'8086606404052108',plan_id:pid,verified_at:new Date().toISOString()},
   {account_id:aid,plan_id:pid,verified_at:new Date(Date.now()-600001).toISOString()}]){
  const f=fixture();f.w.document.querySelector('div').remove();f.command.account_binding=binding?{kind:'official_api_material_scope',...binding}:null;
  assert.equal((await f.call('identity')).ok,false);assert.equal(f.submits,0);
 }
});

test('visible foreign account IDs still stop a command even with a matching API scope',async()=>{
 const f=fixture();f.w.document.querySelector('div').textContent='ID：8086606404052108';
 f.command.account_binding={kind:'official_api_material_scope',account_id:aid,plan_id:pid,verified_at:new Date().toISOString()};
 const result=await f.call('identity');assert.equal(result.ok,false);assert.match(result.message,/账户ID与本户不符/);assert.equal(f.submits,0);
});

test('legacy all-domain tabs and its checked plan switch work without ARIA and ignore unrelated switches',async()=>{
 const f=fixture('/uni-prom/detail'),d=f.w.document;f.command.phase='materials';d.querySelector('h1').textContent='全域投放';
 d.querySelector('[role=switch]').setAttribute('aria-checked','false');
 d.body.insertAdjacentHTML('afterbegin','<div class="col-status-switch"><div class="ovui-switch ovui-switch--checked"></div></div>');
 d.body.insertAdjacentHTML('beforeend','<div class="ovui-tabs__tab"><div class="ovui-tabs__tab-btn">素材</div></div><div class="ovui-tabs__tab ovui-tabs__tab--active"><div class="ovui-tabs__tab-btn">调控</div></div>');
 const legacy=d.querySelector('.ovui-tabs__tab');legacy.querySelector('div').onclick=()=>{d.querySelector('.ovui-tabs__tab--active').classList.remove('ovui-tabs__tab--active');legacy.classList.add('ovui-tabs__tab--active');};
 assert.equal((await f.call('view')).changed,true);assert.equal((await f.call('view')).changed,false);
 let read=await f.call('read');assert.equal(read.ok,true,read.message);assert.equal(read.observation.running,true);assert.equal(read.observation.delivery_entry,'uni_promotion');
 d.querySelector('.ovui-switch').classList.remove('ovui-switch--checked');d.querySelector('[role=switch]').setAttribute('aria-checked','true');
 read=await f.call('read');assert.equal(read.observation.running,false);assert.equal(f.submits,0);
});

test('visible identity tolerates wrapped labels and repeated same plan tooltip but rejects a different visible account',async()=>{
 const f=fixture(),d=f.w.document;
 d.querySelectorAll('body>div')[0].textContent='账户 ID ：\n'+aid;
 d.body.insertAdjacentHTML('beforeend',`<span>计划 ID：${pid}</span>`);
 assert.equal((await f.call('identity')).ok,true);
 d.querySelectorAll('body>div')[0].textContent='ID：8929253702525378';
 const result=await f.call('identity');assert.equal(result.ok,false);assert.match(result.message,/账户ID/);assert.equal(f.submits,0);
});

test('an empty visible drawer wrapper with hidden inputs is not an editor but real edits remain protected even without a header',async()=>{
 const f=fixture(),d=f.w.document;d.body.insertAdjacentHTML('beforeend','<div class="ovui-modal__wrap"><div style="display:none"><input><button>提交</button></div></div>');
 assert.equal((await f.call('identity')).editor_open,false);
 d.querySelector('.ovui-modal__wrap div').style.display='block';
 d.querySelectorAll('body>div')[0].remove();
 const result=await f.call('identity');assert.equal(result.ok,false);assert.equal(result.editor_open,true);assert.equal(f.submits,0);
});

test('native page-number click waits for new row IDs instead of accepting old rows under a new page number',async()=>{
 const f=fixture(),d=f.w.document;f.command.phase='materials';f.command.page=2;
 const box=d.querySelector('.main-vmok-plugin-pagination');box.querySelector('span').textContent='共 102 条记录';
 const next=d.createElement('li');next.className='main-vmok-plugin-page-turner__item';next.textContent='2';box.append(next);
 next.onclick=()=>{box.querySelector('li').classList.remove('main-vmok-plugin-page-turner__item--active');next.classList.add('main-vmok-plugin-page-turner__item--active');};
 assert.equal((await f.call('page')).changed,true);
 const stale=await f.call('read');assert.equal(stale.ok,false);assert.match(stale.message,/新数据/);
 for(const row of d.querySelectorAll('tbody tr'))row.cells[1].textContent=row.cells[1].textContent.replace(/832553379340384442/g,'828438245744054742');
 const fresh=await f.call('read');assert.equal(fresh.ok,true,fresh.message);assert.equal(fresh.observation.page,2);
 assert.equal(f.submits,0);
});

test('loading masks reject complete-looking old rows even when the route already shows the requested page',async()=>{
 const f=fixture(),d=f.w.document;f.command.phase='materials';
 const table=d.querySelector('table'),container=d.createElement('div');container.className='table-container loading';table.replaceWith(container);container.append(table);
 let read=await f.call('read');assert.equal(read.ok,false);assert.match(read.message,/加载完成/);
 container.classList.remove('loading');assert.equal((await f.call('read')).ok,true);
 const mask=d.createElement('div');mask.className='ovui-spin__block-mask';d.body.append(mask);
 assert.equal((await f.call('read')).ok,false);mask.style.display='none';assert.equal((await f.call('read')).ok,true);assert.equal(f.submits,0);
});

test('marketing ROI selection ignores its identically named explanatory tag and checks the native radio state',async()=>{
 const f=fixture(),d=f.w.document,original=d.querySelector('[data-chase]').onclick;
 f.command.action.intent.browser_batch.after.optimization_target='综合ROI';f.command.action.intent.browser_batch.after.bid_value=2.3;
 let radioClicks=0,tagClicks=0;
 d.querySelector('[data-chase]').onclick=()=>{
  original();const root=d.querySelector('.main-vmok-plugin-modal__wrap');
  root.insertAdjacentHTML('afterbegin','<div class="ovui-radio-item" data-goal>综合营销ROI</div><span data-formula>综合营销ROI</span><label>综合营销ROI目标<input type="number" step="0.01"></label>');
  root.querySelector('[data-goal]').onclick=e=>{radioClicks++;e.currentTarget.classList.add('ovui-radio-item--checked');};
  root.querySelector('[data-formula]').onclick=()=>tagClicks++;
 };
 const prepared=await f.call('prepare');assert.equal(prepared.prepared,true,prepared.message);
 assert.equal(radioClicks,1);assert.equal(tagClicks,0);assert.equal(f.submits,0);
 assert.equal(d.querySelector('[data-goal]')?.classList.contains('ovui-radio-item--checked'),true);
 await f.call('cleanup');assert.equal(d.querySelector('.main-vmok-plugin-modal__wrap'),null);
});
test('all-domain detail path is also accepted but wrong account is rejected',async()=>{
 const f=fixture('/uni-prom/detail');assert.equal((await f.call('read')).ok,true);
 f.command.account_id='999';assert.equal((await f.call('read')).ok,false);
});
test('native form is filled from the immutable batch and only a granted final permit clicks submit',async()=>{
 const f=fixture(),prepared=await f.call('prepare');assert.equal(prepared.prepared,true,prepared.message);
 assert.equal(f.submits,0);f.allowed=false;assert.equal((await f.call('submit',{nonce:prepared.nonce})).submitted,false);
 assert.equal(f.submits,0);f.allowed=true;assert.equal((await f.call('submit',{nonce:prepared.nonce})).submitted,true);
 assert.equal(f.submits,1);assert.equal((await f.call('submit',{nonce:prepared.nonce})).submitted,false);assert.equal(f.submits,1);
});

test('a dialog that mounts before its material cards waits for the exact full ID set before filling',async()=>{
 const f=fixture('/overall-prom/detail',{delayMaterials:true});
 const prepared=await f.call('prepare');assert.equal(prepared.prepared,true,prepared.message);assert.equal(f.submits,0);
 await f.call('cleanup');assert.equal(f.w.document.querySelector('[class*="-modal__wrap"]'),null);
});

test('each native checkbox selection survives a controlled-table redraw and cancel clears only this batch',async()=>{
 const f=fixture('/overall-prom/detail',{controlledSelection:true});
 const prepared=await f.call('prepare');assert.equal(prepared.prepared,true,prepared.message);
 assert.equal(f.w.document.querySelectorAll('tbody input:checked').length,2);assert.equal(f.submits,0);
 const cancelled=await f.call('cleanup');assert.equal(cancelled.ok,true,cancelled.message);
 assert.equal(f.w.document.querySelectorAll('tbody input:checked').length,0);
 assert.equal(f.w.document.querySelector('[class*="-modal__wrap"]'),null);
});

test('a hidden page with unusable timers still selects, fills and cancels using bounded extension waits',async()=>{
 const f=fixture('/overall-prom/detail',{controlledSelection:true,pageTimersBlocked:true});
 const prepared=await f.call('prepare');assert.equal(prepared.prepared,true,prepared.message);
 assert.equal(f.submits,0);const cleaned=await f.call('cleanup');assert.equal(cleaned.ok,true,cleaned.message);
 assert.equal(f.w.document.querySelectorAll('tbody input:checked').length,0);
});
test('changed fields and existing human selections prevent an ad submission',async()=>{
 const f=fixture();f.w.document.querySelector('tbody input').checked=true;
 assert.equal((await f.call('prepare')).ok,false);assert.equal(f.submits,0);
 const g=fixture(),prepared=await g.call('prepare');g.w.document.querySelector('.main-vmok-plugin-modal__wrap input').value='999';
 assert.equal((await g.call('submit',{nonce:prepared.nonce})).ok,false);assert.equal(g.submits,0);
});
test('marketing ROI is never substituted when the payment ROI header is absent',async()=>{
 const f=fixture();const h=[...f.w.document.querySelectorAll('th')].find(e=>e.textContent==='整体支付ROI');h.textContent='综合营销ROI';
 assert.equal((await f.call('read')).ok,false);assert.equal(f.submits,0);
});
test('incomplete rows do not masquerade as an empty complete scan',async()=>{
 const f=fixture();f.w.document.querySelector('tbody tr').remove();assert.equal((await f.call('read')).ok,false);
});

test('task search activates the input keyup handler and requires a clear status filter',async()=>{
 const f=fixture(),d=f.w.document;
 d.body.insertAdjacentHTML('beforeend','<button role="tab" aria-selected="true">调控</button><label>投放状态：<input value="全部"></label><input placeholder="输入任务名称或ID搜索">');
 f.command.phase='lookup';f.command.lookup_id='8721442247401132';let searched=null;
 const input=d.querySelector('input[placeholder="输入任务名称或ID搜索"]');input.addEventListener('keyup',e=>{if(e.key==='Enter')searched=input.value;});
 const result=await f.call('view');assert.equal(result.ok,true,result.message);assert.equal(searched,f.command.lookup_id);
 d.querySelector('label').remove();assert.equal((await f.call('view')).ok,false);
});

test('Ego task status uses native selection and verifies the replacement control after redraw',async()=>{
 const f=fixture(),d=f.w.document;f.command.phase='tasks';f.command.executor_kind='ego';f.command.id='state-read';f.command.run_id='state-round';
 d.body.insertAdjacentHTML('beforeend','<button role="tab" aria-selected="true">调控</button><label>投放状态：<input value="全部（包括已删除）"></label><input placeholder="输入任务名称或ID搜索">');
 const before=d.querySelector('label input'),messages=[];
 f.context.chrome.runtime.sendMessage=async m=>{messages.push(m);assert.equal(m.action,'hybrid-native-task-state');assert.equal(m.label,'调控中');assert.equal(before.getAttribute('data-qcx-task-state'),f.command.id);const fresh=before.cloneNode();fresh.removeAttribute('data-qcx-task-state');fresh.value='调控中';before.replaceWith(fresh);return {ok:true};};
 const result=await f.call('view');assert.equal(result.ok,true,result.message);assert.equal(result.changed,true);assert.equal(messages.length,1);assert.equal(d.querySelector('label input').value,'调控中');assert.equal(f.submits,0);
});

test('native search commits Enter even when a custom search icon is present',async()=>{
 const f=fixture(),d=f.w.document;f.command.phase='lookup';f.command.lookup_id='8422872052435758';
 d.body.insertAdjacentHTML('beforeend','<button role="tab" aria-selected="true">调控</button><label>投放状态：<input value="全部"></label><div><div><input placeholder="输入任务名称或ID搜索"><div class="search-icon-area"><iconpark-icon name="oc-icon-search"></iconpark-icon></div></div></div>');
 const input=d.querySelector('input[placeholder="输入任务名称或ID搜索"]');let needle=null,iconClicks=0;
 input.onkeyup=e=>{if(e.key==='Enter')needle=input.value;};d.querySelector('iconpark-icon').onclick=()=>iconClicks++;
 assert.equal((await f.call('view')).changed,true);assert.equal(needle,f.command.lookup_id);assert.equal(iconClicks,0);assert.equal(f.submits,0);
});

test('explicit legacy zero-task search is empty evidence with no paginator, never a creation receipt',async()=>{
 const f=taskFixture(),d=f.w.document;d.querySelector('tbody').innerHTML='';d.querySelector('.main-vmok-plugin-pagination').remove();
 d.querySelector('thead').insertAdjacentHTML('beforeend','<tr><th>共0条任务</th></tr>');
 const read=await f.call('verify');assert.equal(read.ok,true,read.message);assert.equal(read.observation.total,0);assert.equal(read.observation.rows.length,0);assert.equal(f.submits,0);
 d.querySelector('thead tr:last-child').remove();assert.equal((await f.call('verify')).ok,false);
});
test('a task search shrinking from page 49 to one page reads the actual original receipt instead of paging forever',async()=>{
 const f=taskFixture(),d=f.w.document;f.command.page=49;
 d.querySelector('tbody td:nth-child(3)').textContent=ids.map(id=>'素材ID：'+id).join('\n');
 const p=await f.call('page');assert.equal(p.ok,true,p.message);assert.equal(p.changed,false);assert.equal(p.cursor_reconciled,true);
 const result=await f.call('verify');assert.equal(result.ok,true,result.message);assert.equal(result.observation.rows[0].name,'QCX_fixture');assert.equal(result.observation.page,1);assert.equal(f.submits,0);
});
test('the Ego readback view commits the original task search through the native bridge before reading a shrunken list',async()=>{
 const f=taskFixture(),d=f.w.document;f.command.executor_kind='ego';f.command.page=49;const calls=[];
 d.querySelector('tbody td:nth-child(3)').textContent=ids.map(id=>'素材ID：'+id).join('\n');
 d.body.insertAdjacentHTML('beforeend','<button role="tab" aria-selected="true">调控</button><label>投放状态：<input value="全部"></label><input placeholder="输入任务名称或ID搜索">');
 f.context.chrome.runtime.sendMessage=async m=>{if(m.action==='hybrid-native-task-search'){calls.push(m.needle);d.querySelector('input[placeholder="输入任务名称或ID搜索"]').value=m.needle;}return {ok:true};};
 assert.equal((await f.call('view')).changed,true);assert.equal((await f.call('view')).changed,false);assert.deepEqual(calls,['QCX_fixture']);
 assert.equal((await f.call('page')).changed,false);const result=await f.call('verify');assert.equal(result.ok,true,result.message);assert.equal(result.observation.rows[0].id,'8317812444501003');assert.equal(f.submits,0);
});

test('zero-result name search falls back once to the complete list while retaining exact task verification',async()=>{
 const f=taskFixture(),d=f.w.document;const body=d.querySelector('tbody');
 body.querySelector('td:nth-child(3)').textContent=ids.map(id=>'素材ID：'+id).join('\n');
 const original=body.innerHTML;
 d.body.insertAdjacentHTML('beforeend','<button role="tab" aria-selected="true">调控</button><label>投放状态：<input value="全部"></label><input placeholder="输入任务名称或ID搜索">');
 const search=d.querySelector('input[placeholder="输入任务名称或ID搜索"]');let fullListQueries=0;
 search.onkeyup=e=>{if(e.key!=='Enter')return;if(search.value){body.innerHTML='';d.querySelector('.main-vmok-plugin-pagination').style.display='none';d.querySelector('thead').insertAdjacentHTML('beforeend','<tr data-empty><th>共0条任务</th></tr>');}else{fullListQueries++;body.innerHTML=original;d.querySelector('[data-empty]')?.remove();d.querySelector('.main-vmok-plugin-pagination').style.display='';}};
 assert.equal((await f.call('view')).changed,true);
 assert.equal((await f.call('view')).changed,true);
 assert.equal((await f.call('view')).changed,true);
 assert.equal((await f.call('view')).changed,false);assert.equal(fullListQueries,1);
 const verified=await f.call('verify');assert.equal(verified.ok,true,verified.message);assert.equal(verified.observation.rows[0].id,'8317812444501003');assert.equal(f.submits,0);
});

test('an explicitly labelled conversion bid can be filled after choosing live-room conversion',async()=>{
 const f=fixture(),original=f.w.document.querySelector('[data-chase]').onclick;
 f.w.document.querySelector('[data-chase]').onclick=()=>{original();const label=f.w.document.querySelector('.main-vmok-plugin-modal__wrap label:nth-of-type(2)');label.firstChild.textContent='目标转化成本';};
 const prepared=await f.call('prepare');assert.equal(prepared.prepared,true,prepared.message);assert.equal(f.submits,0);
});

test('100 rows option is selected from its page-size menu even when the label is only 100',async()=>{
 const f=fixture(),d=f.w.document;f.command.phase='materials';
 d.body.insertAdjacentHTML('beforeend','<button role="tab" aria-selected="true">素材</button><div data-menu style="display:none"><span>10</span><span>20</span><span>50</span><span>100</span></div><button>100</button>');
 const input=d.querySelector('.main-vmok-plugin-pagination input'),menu=d.querySelector('[data-menu]');input.value='10条/页';
 input.onclick=()=>menu.style.display='block';menu.lastChild.onclick=()=>{input.value='100条/页';menu.style.display='none';};
 const result=await f.call('view');assert.equal(result.ok,true,result.message);assert.equal(result.changed,true);assert.equal(input.value,'100条/页');assert.equal(f.submits,0);
});

test('native inventory selects ten tasks per page without changing the material scan page size',async()=>{
 const f=taskFixture(),d=f.w.document;f.command.phase='tasks';f.command.executor_kind='ego';f.command.inventory_page_size=10;
 d.body.insertAdjacentHTML('beforeend','<button role="tab" aria-selected="true">调控</button><label>投放状态：<input value="调控中"></label><input placeholder="输入任务名称或ID搜索"><div data-size-menu style="display:none"><span>10</span><span>20</span><span>50</span><span>100</span></div>');
 const field=d.querySelector('.main-vmok-plugin-pagination input'),menu=d.querySelector('[data-size-menu]');field.value='100条/页';
 field.onclick=()=>menu.style.display='block';menu.firstChild.onclick=()=>{field.value='10条/页';menu.style.display='none';};
 assert.equal((await f.call('view')).changed,true);
 const result=await f.call('view');assert.equal(result.ok,true,result.message);assert.equal(result.changed,true);assert.equal(field.value,'10条/页');assert.equal(f.submits,0);
});

test('material scan verifies native descending state and can activate its exact down arrow',async()=>{
 const f=fixture(),d=f.w.document;f.command.phase='materials';
 d.body.insertAdjacentHTML('beforeend','<button role="tab" aria-selected="true">素材</button>');
 const header=[...d.querySelectorAll('th')].find(e=>e.textContent==='整体消耗(元)');header.removeAttribute('aria-sort');
 header.insertAdjacentHTML('beforeend','<span class="main-vmok-plugin-th__sorter-down"></span>');header.lastChild.onclick=()=>header.setAttribute('aria-sort','descending');
 assert.equal((await f.call('read')).ok,false);
 assert.equal((await f.call('view')).changed,true);assert.equal((await f.call('read')).ok,true);assert.equal(f.submits,0);
});

test('actual video selection wins over a stale route value that refers to titles',async()=>{
 const f=fixture(),d=f.w.document;f.command.phase='materials';
 d.body.insertAdjacentHTML('beforeend','<button role="tab" aria-selected="true">素材</button><div class="main-vmok-plugin-radio-item main-vmok-plugin-radio-item--checked" data-title>标题</div>');
 const video=d.querySelector('.main-vmok-plugin-radio-item');video.className='main-vmok-plugin-radio-item';
 video.onclick=()=>{video.classList.add('main-vmok-plugin-radio-item--checked');d.querySelector('[data-title]').classList.remove('main-vmok-plugin-radio-item--checked');};
 const result=await f.call('view');assert.equal(result.changed,true,result.message);
 assert.match(video.className,/--checked/);assert.match(f.w.location.hash,/adDetailCreativeSubTab=1/);
 assert.equal((await f.call('view')).changed,false);assert.equal(f.submits,0);
});

test('current native conversion field labelled my bid is accepted only after selecting live-room conversion',async()=>{
 const f=fixture(),original=f.w.document.querySelector('[data-chase]').onclick;
 f.w.document.querySelector('[data-chase]').onclick=()=>{original();const label=f.w.document.querySelector('.main-vmok-plugin-modal__wrap label:nth-of-type(2)');label.firstChild.textContent='我的出价';};
 const prepared=await f.call('prepare');assert.equal(prepared.prepared,true,prepared.message);assert.equal(f.submits,0);
});

test('native task materials are independently read from the hover group with exact IDs',async()=>{
 const f=taskFixture(),result=await f.call('verify');assert.equal(result.ok,true,result.message);
 assert.equal(result.observation.rows[0].id,'8317812444501003');
 assert.deepEqual(Array.from(result.observation.rows[0].material_ids),ids);
 assert.equal(f.w.document.querySelector('[data-proof-popover]'),null);assert.equal(f.submits,0);
});

test('a task page without the original name returns only scan evidence, not a completion receipt',async()=>{
 const f=taskFixture('other-task'),result=await f.call('verify');assert.equal(result.ok,true,result.message);
 assert.equal(result.observation.rows[0].budget,'200.00');assert.equal(result.observation.rows[0].name,undefined);assert.equal(f.submits,0);
});

test('a native task showing zero videos remains pending instead of being accepted or opened as the wrong batch',async()=>{
 const f=taskFixture(),d=f.w.document;d.querySelector('tbody td:nth-child(3)').textContent='共0条视频';
 const result=await f.call('verify');assert.equal(result.ok,false);assert.match(result.message,/素材明细暂显示0条/);assert.equal(f.submits,0);
});

function apiSearchFixture(options={}){
 const f=fixture(),d=f.w.document,batch=f.command.action.intent.browser_batch;
 batch.material_ids=[...ids];batch.selection_source='official_api';batch.allow_running_materials=false;
 if(options.extraToggle)for(const row of d.querySelectorAll('tbody tr'))row.cells[3].insertAdjacentHTML('beforeend','<input data-other-toggle type="checkbox" checked>');
 if(options.noSelectionControl)for(const row of d.querySelectorAll('tbody tr'))row.cells[0].innerHTML='';
 if(options.disabledFirst)d.querySelector('tbody tr input').disabled=true;
 if(options.extraMaterial){const row=d.querySelector('tbody tr').cloneNode(true),extra='8102143537225650053';row.cells[1].textContent='额外素材\n素材ID: '+extra+'\n';d.querySelector('tbody').append(row);batch.material_ids.push(extra);}
 if(options.noMetrics)for(const row of d.querySelectorAll('table tr')){row.lastChild.remove();row.lastChild.remove();}
 d.body.insertAdjacentHTML('beforeend','<input data-material-search placeholder="输入视频名称/ID后回车搜索"><div data-selection-count>已选0个视频</div><button role="tab" aria-selected="true">素材</button>');
 const tbody=d.querySelector('tbody'),templates=new Map([...tbody.rows].map(row=>[row.cells[1].textContent.match(/素材ID: (\d+)/)[1],row.outerHTML]));
 const selected=new Set(),queries=[],pickerQueries=[],search=d.querySelector('[data-material-search]');let filtered=!!options.filtered,clears=0,otherChanges=0;
 d.addEventListener('change',e=>{if(e.target.matches('[data-other-toggle]'))otherChanges++;});
 let loading;
 if(options.selectionLoads){const table=d.querySelector('table');loading=d.createElement('div');loading.className='table-container';table.replaceWith(loading);loading.append(table);}
 const render=()=>{
  const list=(search.value?templates.has(search.value)?[search.value]:[]:[...templates.keys()]).filter(id=>id!==options.missingMaterial&&(!filtered||id!==ids[0]));
  tbody.innerHTML=list.map(id=>templates.get(id)).join('');
  for(const row of tbody.rows){const id=row.cells[1].textContent.match(/素材ID: (\d+)/)[1],box=row.cells[0].querySelector('input');if(!box)continue;box.checked=selected.has(id);
   box.onchange=()=>{if(box.checked)selected.add(id);else selected.delete(id);search.style.display=selected.size?'none':'';d.querySelector('[data-selection-count]').textContent='已选'+selected.size+'个视频';if(loading){loading.classList.add('loading');f.w.setTimeout(()=>loading.classList.remove('loading'),250);}};}
  d.querySelector('.main-vmok-plugin-pagination span').textContent='共 '+list.length+' 条记录';
  d.querySelector('[data-empty-materials]')?.remove();
  if(!list.length){d.querySelector('thead').insertAdjacentHTML('beforeend','<tr data-empty-materials><th>共0个视频</th></tr>');d.querySelector('.main-vmok-plugin-pagination').style.display='none';}
  else d.querySelector('.main-vmok-plugin-pagination').style.display='';
 };
 if(options.filtered){const clear=d.createElement('button');clear.textContent='清空';clear.onclick=()=>{filtered=false;clears++;clear.remove();render();};d.body.append(clear);}
 search.onkeyup=e=>{if(e.key==='Enter'){queries.push(search.value);if(options.clearSelection){selected.clear();d.querySelector('[data-selection-count]').textContent='已选0个视频';}render();}};
 const original=d.querySelector('[data-chase]').onclick;
 d.querySelector('[data-chase]').onclick=()=>{
  original();const root=d.querySelector('.main-vmok-plugin-modal__wrap');for(const p of root.querySelectorAll('p'))p.remove();root.insertAdjacentHTML('afterbegin',[...selected].map(id=>`<p>素材ID: ${id}</p>`).join(''));
  const add=d.createElement('button');add.textContent='添加视频';root.append(add);
  add.onclick=()=>{
   const parent=[...root.childNodes],chosen=new Set();root.innerHTML='<h2>添加视频</h2><input data-picker-search placeholder="输入视频名称/ID后回车搜索"><table><thead><tr><th></th><th>视频</th><th>视频类型</th><th>审核状态</th><th>标签</th></tr></thead><tbody></tbody></table><div data-picker-count>已选 0 / 19</div><button data-picker-confirm>确定</button>';
   const input=root.querySelector('[data-picker-search]'),body=root.querySelector('tbody');
   const renderPicker=()=>{
    const rows=[...templates.keys()].filter(id=>!selected.has(id)&&(!input.value||input.value===id)&&id!==options.pickerMissing);
    body.innerHTML=rows.map(id=>`<tr><td><input type="checkbox"></td><td>候选素材\n素材ID: ${id}</td><td>自选投放视频</td><td>${options.pickerBlocked===id?'审核中':'审核通过'}</td><td>-</td></tr>`).join('');
    for(const row of body.rows){const id=row.cells[1].textContent.match(/素材ID: (\d+)/)[1],box=row.querySelector('input');box.checked=chosen.has(id);box.onchange=()=>{if(box.checked)chosen.add(id);else chosen.delete(id);root.querySelector('[data-picker-count]').textContent='已选 '+chosen.size+' / 19';};}
   };
   input.onkeyup=e=>{if(e.key==='Enter'){pickerQueries.push(input.value);if(options.clearSelection)chosen.clear();if(options.ignoreFirstPickerSearch&&pickerQueries.length===1){body.innerHTML='';return;}renderPicker();}};
   root.querySelector('[data-picker-confirm]').onclick=()=>{root.replaceChildren(...parent);for(const p of root.querySelectorAll('p'))p.remove();let values=[...selected,...chosen];if(options.dropPickerMaterial)values=values.slice(0,-1);root.insertAdjacentHTML('afterbegin',values.map(id=>`<p>素材ID: ${id}</p>`).join(''));};
   renderPicker();
  };
 };
 render();
 return {...f,dom:f.dom,w:f.w,command:f.command,queries,pickerQueries,selected,call:f.call,get otherChanges(){return otherChanges;},get clears(){return clears;},get submits(){return f.submits;}};
}

test('API candidates use one seed and add exact IDs in the native picker while the main search is hidden',async()=>{
 const f=apiSearchFixture(),result=await f.call('prepare');
 assert.equal(result.prepared,true,result.message);assert.equal(result.observation.selection_source,'official_api');
 assert.deepEqual([...result.observation.selected_materials].map(r=>r.id),ids);
 assert.deepEqual(f.queries,ids);assert.equal(f.submits,0);
 assert.deepEqual(f.pickerQueries,[ids[0]]);assert.deepEqual([...f.selected],[ids.at(-1)]);
 assert.equal(f.w.document.querySelector('[data-material-search]').style.display,'none');
 assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.selected.size,0);
 assert.equal(f.w.document.querySelector('[data-material-search]').value,'');assert.equal(f.submits,0);
});

test('an explicit native remaining-budget limit blocks before issuing or consuming a submit permit',async()=>{
 const f=fixture();f.w.document.querySelector('[data-chase]').addEventListener('click',()=>{
  f.w.document.querySelector('.main-vmok-plugin-modal__wrap').insertAdjacentHTML('beforeend','<p>素材追投每日总预算 20000 元，当前最多可再追加预算 100 元</p>');
 });
 const result=await f.call('prepare');assert.equal(result.ok,false);assert.match(result.message,/平台当前可追加追投预算100元，小于本批预算200元/);assert.match(result.message,new RegExp(pid));assert.equal(f.submits,0);
 assert.equal((await f.call('cleanup')).ok,true);
});

test('home preflight checks the whole API batch without selecting a seed or opening a create form',async()=>{
 const f=apiSearchFixture(),d=f.w.document;
 d.body.insertAdjacentHTML('afterbegin','<div>抖音号ID：79987870170</div><button>推直播间</button>');
 const result=await f.call('prepare',{preflight_only:true});
 assert.equal(result.preflight_ready,true,result.message);assert.equal(result.home_supported,true);
 assert.deepEqual(f.queries,ids);assert.equal(f.selected.size,0);assert.equal(d.querySelector('.main-vmok-plugin-modal__wrap'),null);
 assert.equal(result.binding.aweme_id,'79987870170');assert.deepEqual([...result.observation.selected_materials].map(r=>r.id),ids);
 assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.submits,0);
});

test('direct home identity needs no material table or main ID search before adding the complete API batch',async()=>{
 const f=apiSearchFixture(),d=f.w.document;d.body.insertAdjacentHTML('afterbegin','<div>抖音号ID：79987870170</div><button>推直播间</button>');
 f.command.executor_kind='ego';f.command.direct_home=true;f.command.execution_scope='f5_create_only';
 d.querySelector('table').remove();d.querySelector('.main-vmok-plugin-pagination').remove();d.querySelector('[data-material-search]').remove();
 const pre=await f.call('home_identity');assert.equal(pre.ok,true,pre.message);assert.equal(pre.observation.kind,'direct_home_identity');assert.equal(f.queries.length,0);
 const historical=new Date(Date.now()-110000).toISOString();pre.binding.captured_at=historical;pre.observation.captured_at=historical;
 d.querySelector('[data-chase]').click();const root=d.querySelector('.main-vmok-plugin-modal__wrap');
 d.body.insertAdjacentHTML('beforeend','<table><tbody><tr data-plan-id="'+pid+'"><td>投放中 每日预算500000.00元 控成本投放</td></tr></tbody></table>');
 f.dom.reconfigure({url:'https://qianchuan.jinritemai.com/overall-prom?aavid='+aid});
 const context=vm.createContext({URL,URLSearchParams,Date,crypto:require('node:crypto').webcrypto,document:d,location:f.w.location,getComputedStyle:f.w.getComputedStyle.bind(f.w),
  Event:f.w.Event,MouseEvent:f.w.MouseEvent,KeyboardEvent:f.w.KeyboardEvent,HTMLInputElement:f.w.HTMLInputElement,window:f.w,chrome:{runtime:{sendMessage:async m=>{
   if(m?.action==='hybrid-native-search'){const inp=d.querySelector('[data-picker-search]');if(inp){inp.value=m.material_id;inp.onkeyup?.({key:'Enter'});}}
   return {ok:true,allow:false};
  }}}});vm.runInContext(source,context);
 context.__qc_hybrid_editor_v1={root,action_id:f.command.action.id,selected:[],selected_ids:null};
 const args={command:f.command,home_context:pre.binding,detail_preflight:pre.observation,home_proof:{path:'/overall-prom'}};
 const result=await context.hybridChaseDom('prepare',args);assert.equal(result.prepared,true,result.message);
 assert.equal(f.queries.length,0);assert.deepEqual(f.pickerQueries,ids);assert.deepEqual([...result.observation.picker_materials].map(p=>p.id),ids);
 assert.ok(result.observation.picker_materials.every(p=>p.approved===true&&p.selectable===true));assert.equal(f.submits,0);
 assert.equal(result.observation.binding.captured_at,historical);assert.equal(result.observation.preflight.captured_at,historical);
 assert.equal(result.observation.home.row_plan_id,pid);assert.ok(Date.now()-Date.parse(result.observation.home.captured_at)<1000);
 d.querySelector('tr').setAttribute('data-aweme-id','12345678900');
 const blocked=await context.hybridChaseDom('submit',{...args,nonce:result.nonce});assert.equal(blocked.ok,false);assert.match(blocked.message,/首页母计划行未唯一对应/);assert.equal(f.submits,0);
 assert.equal((await context.hybridChaseDom('cleanup',args)).ok,true);
});

test('the active task inventory collects material IDs once from the task table without opening material search',async()=>{
 const f=taskFixture(),d=f.w.document;f.command.phase='tasks';
 d.querySelector('tbody td:nth-child(3)').textContent=ids.map(id=>'素材ID：'+id).join('\n');
 const result=await f.call('task_inventory');assert.equal(result.ok,true,result.message);
 assert.deepEqual([...result.observation.rows[0].material_ids],ids);assert.equal(f.submits,0);
});

test('fresh scoped API task IDs fill the native inventory without hovering thumbnails or searching materials',async()=>{
 const f=taskFixture('QCX_fixture',true);f.command.phase='tasks';f.command.inventory_api_materials={'8317812444501003':ids};
 f.w.document.querySelector('[class*="media-group-detail"]').remove();
 const result=await f.call('task_inventory');assert.equal(result.ok,true,result.message);assert.deepEqual([...result.observation.rows[0].material_ids],ids);assert.equal(f.submits,0);
});

test('a live-room avatar task retains its budget without treating the room ID as a video ID',async()=>{
 const f=taskFixture(),d=f.w.document;f.command.phase='tasks';
 d.querySelector('tbody td:nth-child(3)').innerHTML='<div data-auto-id="promotion-media-group"><div class="oc-promotion-media-group-detail"><div data-auto-id="oc-media"><div class="oc-media-thumb-container"></div></div></div><div class="oc-promotion-media-group-info"><span class="typography-value-int">示例店铺官方旗舰店</span><span class="typography-value-int">ID：8508172445614005</span></div></div>';
 const result=await f.call('task_inventory');assert.equal(result.ok,true,result.message);
 const row=result.observation.rows[0];assert.equal(row.material_type,'LIVE_ROOM');assert.deepEqual([...row.material_ids],[]);assert.equal(row.budget,'200.00');assert.equal(row.status,'调控中');assert.equal(f.submits,0);
});

test('an unknown or video-marked task cannot pass as an empty live-room task',async()=>{
 for(const markup of ['-', '<div data-auto-id="promotion-media-group"><div data-auto-id="oc-media"><i class="oc-media-thumb-video-icon"></i></div><div class="oc-promotion-media-group-info"><span class="typography-value-int">视频</span><span class="typography-value-int">ID：8508172445614005</span></div></div>']){
  const f=taskFixture();f.command.phase='tasks';f.w.document.querySelector('tbody td:nth-child(3)').innerHTML=markup;
  const result=await f.call('task_inventory');assert.equal(result.ok,false);assert.match(result.message,/视频ID未完整核对/);assert.equal(f.submits,0);
 }
});

test('direct home inventory opens each task thumbnail group once instead of searching its videos',async()=>{
 const f=taskFixture('QCX_fixture',true);f.command.phase='tasks';const result=await f.call('task_inventory');
 assert.equal(result.ok,true,result.message);assert.deepEqual([...result.observation.rows[0].material_ids],ids);assert.equal(f.submits,0);
});

test('small active groups reveal IDs from their thumbnail hover even without an ellipsis',async()=>{
 const f=taskFixture(),d=f.w.document;f.command.phase='tasks';const old=d.querySelector('[class*="media-group-detail"]'),detail=old.cloneNode(false);old.replaceWith(detail);
 detail.innerHTML='<div class="main-vmok-plugin-oc-promotion-media-group-list"><div data-auto-id="oc-media"></div></div>';
 const media=detail.querySelector('[data-auto-id="oc-media"]');media.onmouseenter=()=>{if(d.querySelector('[data-small-popup]'))return;const p=d.createElement('div');p.className='main-vmok-plugin-popover';p.dataset.smallPopup='1';p.textContent=ids.map(id=>'素材ID：'+id).join('\n');d.body.append(p);};
 media.onmouseleave=()=>d.querySelector('[data-small-popup]')?.remove();
 const result=await f.call('task_inventory');assert.equal(result.ok,true,result.message);assert.deepEqual([...result.observation.rows[0].material_ids],ids);assert.equal(d.querySelector('[data-small-popup]'),null);assert.equal(f.submits,0);
});

test('a stale “共0条视频” cell takes the official API task list instead of blocking the page',async()=>{
 const apiIds=['8017550748539135433','8143094202365810575'],f=taskFixture(),d=f.w.document;f.command.phase='tasks';
 d.querySelector('tbody td:nth-child(3)').innerHTML='<div data-auto-id="promotion-media-group"><div class="main-vmok-plugin-oc-promotion-media-group-detail"></div><span>共0条视频</span></div>';
 f.command.inventory_api_materials={'8317812444501003':apiIds.slice()};
 const result=await f.call('task_inventory');assert.equal(result.ok,true,result.message);
 const row=result.observation.rows[0];assert.deepEqual([...row.material_ids],apiIds);assert.equal(row.material_type,'VIDEO');assert.equal(row.budget,'200.00');assert.equal(f.submits,0);
});

test('a zero-read page cell is completed by the API task list when the page count is larger',async()=>{
 const apiIds=['8017550748539135433','8143094202365810575','8426541184012215100','8125350664547520085','8778115529300891912'],f=taskFixture(),d=f.w.document;f.command.phase='tasks';
 d.querySelector('tbody td:nth-child(3)').innerHTML='<div data-auto-id="promotion-media-group"><div class="main-vmok-plugin-oc-promotion-media-group-detail"></div><span>共5条视频</span></div>';
 f.command.inventory_api_materials={'8317812444501003':apiIds.slice()};
 const result=await f.call('task_inventory');assert.equal(result.ok,true,result.message);
 assert.deepEqual([...result.observation.rows[0].material_ids],apiIds);assert.equal(f.submits,0);
});

test('a stale zero cell without an API task list still refuses to assert the task has no videos',async()=>{
 const f=taskFixture(),d=f.w.document;f.command.phase='tasks';
 d.querySelector('tbody td:nth-child(3)').innerHTML='<div data-auto-id="promotion-media-group"><div class="main-vmok-plugin-oc-promotion-media-group-detail"></div><span>共0条视频</span></div>';
 const result=await f.call('task_inventory');assert.equal(result.ok,false);assert.match(result.message,/视频ID未完整核对/);assert.equal(f.submits,0);
});

test('a complete page read is not overridden by a different API task list',async()=>{
 const f=taskFixture('QCX_fixture',true);f.command.phase='tasks';
 f.command.inventory_api_materials={'8317812444501003':['8017550748539135433','8143094202365810575','8426541184012215100']};
 const result=await f.call('task_inventory');assert.equal(result.ok,true,result.message);
 assert.deepEqual([...result.observation.rows[0].material_ids],ids);assert.equal(f.submits,0);
});

test('a confirmed empty active task list is complete inventory for direct home creation',async()=>{
 const f=taskFixture();f.command.phase='tasks';f.command.direct_home=true;const d=f.w.document;
 d.querySelector('tbody').replaceChildren();d.querySelector('.main-vmok-plugin-pagination').remove();d.querySelector('thead').insertAdjacentHTML('beforeend','<tr><th>共0条任务</th></tr>');
 const result=await f.call('task_inventory');assert.equal(result.ok,true,result.message);assert.equal(result.observation.total,0);assert.equal(result.observation.rows.length,0);
});

test('a direct breadcrumb anchor is a supported home entry and its normal click is used',async()=>{
 const f=apiSearchFixture(),d=f.w.document;let clicks=0;
 d.body.insertAdjacentHTML('afterbegin','<div>抖音号ID：79987870170</div><a href="/overall-prom?aavid='+aid+'">推直播间</a>');
 d.querySelector('a').onclick=e=>{e.preventDefault();clicks++;};
 const pre=await f.call('prepare',{preflight_only:true});assert.equal(pre.home_supported,true,pre.message);
 assert.equal(pre.home_diagnostic.link_count,1);assert.equal(pre.home_diagnostic.owner_count,1);assert.equal(clicks,0);
 assert.equal((await f.call('cleanup')).ok,true);const go=await f.call('home_go');assert.equal(go.navigated,true,go.message);
 assert.equal(clicks,1);assert.equal(f.submits,0);
});

test('a missing home breadcrumb is reported explicitly without guessing a row or creating a task',async()=>{
 const f=apiSearchFixture(),d=f.w.document;d.body.insertAdjacentHTML('afterbegin','<div>抖音号ID：79987870170</div>');
 const pre=await f.call('prepare',{preflight_only:true});assert.equal(pre.home_supported,false);
 assert.equal(pre.home_diagnostic.link_count,0);assert.equal(pre.home_diagnostic.owner_count,1);
 assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.selected.size,0);assert.equal(f.submits,0);
});

test('a collapsed detail UID uses only a fresh same-parent official anchor, while visible conflicts stop',async()=>{
 for(const kind of ['fresh','stale','foreign','visibleConflict']){
  const f=apiSearchFixture(),d=f.w.document;
  d.body.insertAdjacentHTML('afterbegin','<a href="/overall-prom?aavid='+aid+'">推直播间</a>');
  f.command.parent_anchor={kind:'unique_official_plan_anchor',account_id:aid,plan_id:pid,aweme_id:'79987870170',captured_at:new Date(Date.now()-(kind==='stale'?601000:0)).toISOString()};
  if(kind==='foreign')f.command.parent_anchor.plan_id='8356939714484742';
  if(kind==='visibleConflict')d.body.insertAdjacentHTML('afterbegin','<div>抖音号ID：11111111111</div>');
  const pre=await f.call('prepare',{preflight_only:true});
  if(kind==='fresh'){assert.equal(pre.home_supported,true,pre.message);assert.equal(pre.binding.aweme_source,'official_plan_list');}
  else if(kind==='visibleConflict'){assert.equal(pre.ok,false);assert.match(pre.message,/冲突/);}
  else assert.equal(pre.home_supported,false);
  assert.equal(f.selected.size,0);assert.equal(f.submits,0);assert.equal((await f.call('cleanup')).ok,true);
 }
});

test('the shared home filler adds every ID into an empty native picker and cancels without a detail table',async()=>{
 for(const rowIdentity of ['data-aweme-id="79987870170"','data-plan-id="'+pid+'"','href','text']){
 const f=apiSearchFixture(),d=f.w.document;
 d.body.insertAdjacentHTML('afterbegin','<div>抖音号ID：79987870170</div><button>推直播间</button>');
 const pre=await f.call('prepare',{preflight_only:true});assert.equal(pre.preflight_ready,true,pre.message);assert.equal((await f.call('cleanup')).ok,true);
 d.querySelector('[data-chase]').click();
 const root=d.querySelector('.main-vmok-plugin-modal__wrap');assert.equal(root.querySelector('p'),null);
 for(const table of d.querySelectorAll('table'))table.remove();d.querySelector('.main-vmok-plugin-pagination').remove();
 const identifier=rowIdentity==='href'?'<a href="/overall-prom/detail?aavid='+aid+'&adId='+pid+'">素材</a>':rowIdentity==='text'?'计划 ID：'+pid:'';
 d.body.insertAdjacentHTML('beforeend','<table><tbody><tr '+(['href','text'].includes(rowIdentity)?'':rowIdentity)+'><td>投放中 每日预算500000.00元 控成本投放 '+identifier+'</td></tr></tbody></table>');
 f.dom.reconfigure({url:'https://qianchuan.jinritemai.com/overall-prom?aavid='+aid+(rowIdentity.startsWith('data-aweme-id')?'&adId=':'')});
 const context=vm.createContext({URL,URLSearchParams,Date,crypto:require('node:crypto').webcrypto,document:d,location:f.w.location,
  getComputedStyle:f.w.getComputedStyle.bind(f.w),Event:f.w.Event,MouseEvent:f.w.MouseEvent,KeyboardEvent:f.w.KeyboardEvent,HTMLInputElement:f.w.HTMLInputElement,window:f.w,
  chrome:{runtime:{sendMessage:async()=>({ok:true,allow:false})}}});vm.runInContext(source,context);
 context.__qc_hybrid_editor_v1={root,action_id:f.command.action.id,selected:[],selected_ids:null};
 const args={command:f.command,home_context:pre.binding,detail_preflight:pre.observation,home_proof:{path:'/overall-prom'}};
 const result=await context.hybridChaseDom('prepare',args);assert.equal(result.prepared,true,result.message);
 assert.deepEqual(f.pickerQueries,ids);assert.deepEqual([...result.observation.form_material_ids],ids);assert.equal(result.observation.entry_source,'home_plus');
 assert.equal((await context.hybridChaseDom('cleanup',args)).ok,true);assert.equal(root.isConnected,false);assert.equal(f.submits,0);
 }
});

test('API ID selection needs no visible ROI/spend columns, native sort, or 100-row page change',async()=>{
 const f=apiSearchFixture({noMetrics:true}),d=f.w.document;
 d.querySelector('.main-vmok-plugin-pagination input').value='20条/页';
 const view=await f.call('view');assert.equal(view.changed,false,view.message);
 assert.equal(d.querySelector('.main-vmok-plugin-pagination input').value,'20条/页');assert.equal(f.queries.length,0);
 const result=await f.call('prepare');assert.equal(result.prepared,true,result.message);assert.equal(f.submits,0);
});

test('a video picker that forgets selections across searches cannot submit a smaller replacement batch',async()=>{
 const f=apiSearchFixture({clearSelection:true,extraMaterial:true}),result=await f.call('prepare');
 assert.equal(result.ok,false);assert.match(result.message,/未跨搜索保留/);
 assert.equal(f.submits,0);
 assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.selected.size,0);
});

test('a missing later picker ID identifies only that ID after a good selection and never submits a smaller batch',async()=>{
 const f=apiSearchFixture({extraMaterial:true,pickerMissing:ids[1]}),result=await f.call('prepare');
 assert.equal(result.ok,false);assert.match(result.message,new RegExp('添加视频ID未在搜索结果唯一出现：'+ids[1]));
 assert.deepEqual(f.pickerQueries,[...ids,ids[1]]);assert.equal(f.submits,0);
 assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.selected.size,0);
});

test('a transient picker response retries the same exact ID once and completes the original full set',async()=>{
 const f=apiSearchFixture({ignoreFirstPickerSearch:true}),result=await f.call('prepare');
 assert.equal(result.ok,true,result.message);assert.deepEqual(f.pickerQueries,[ids[0],ids[0]]);
 assert.deepEqual([...result.observation.selected_materials].map(m=>m.id).sort(),[...ids].sort());assert.equal(f.submits,0);
 assert.equal((await f.call('cleanup')).ok,true);
});

test('a later picker material under review is never checked or included after a good selected material',async()=>{
 const f=apiSearchFixture({extraMaterial:true,pickerBlocked:ids[1]}),result=await f.call('prepare');
 assert.equal(result.ok,false);assert.match(result.message,new RegExp('添加视频审核未通过 '+ids[1]+'（审核中）'));
 assert.equal(f.w.document.querySelector('[data-picker-count]').textContent,'已选 1 / 19');
 const blocked=f.w.document.querySelector('[data-picker-search]').parentElement.querySelector('tbody input');assert.equal(blocked.checked,false);
 assert.equal(f.submits,0);assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.selected.size,0);
});

test('a currently running material is rejected when the immutable API batch forbids repeated chase',async()=>{
 const f=apiSearchFixture(),search=f.w.document.querySelector('[data-material-search]'),handler=search.onkeyup;
 search.onkeyup=e=>{handler(e);for(const row of f.w.document.querySelectorAll('tbody tr'))row.cells[3].textContent='调控中';};
 const result=await f.call('prepare');assert.equal(result.ok,false);assert.match(result.message,/素材正在追投/);assert.equal(f.submits,0);
});

test('one read-only batch checks all IDs and reports every running material without selecting a seed',async()=>{
 const f=apiSearchFixture(),d=f.w.document,search=d.querySelector('[data-material-search]'),handler=search.onkeyup;
 search.onkeyup=e=>{handler(e);for(const row of d.querySelectorAll('tbody tr'))row.cells[3].textContent='调控中';};
 const result=await f.call('prepare');assert.equal(result.ok,false);
 for(const mid of ids)assert.ok(result.message.includes('素材正在追投 '+mid),result.message);
 assert.deepEqual(f.queries,ids);assert.equal(f.selected.size,0);assert.equal(f.submits,0);
 assert.equal(d.querySelector('.main-vmok-plugin-modal__wrap'),null);
 assert.equal((await f.call('cleanup')).ok,true);
});

test('an explicit native deleted-material badge stops before waiting on its intentionally blank audit',async()=>{
 const f=apiSearchFixture(),d=f.w.document,search=d.querySelector('[data-material-search]'),handler=search.onkeyup;
 search.onkeyup=e=>{handler(e);for(const row of d.querySelectorAll('tbody tr')){
  row.cells[1].insertAdjacentHTML('afterbegin','<div data-auto-id="oc-tag" class="prefix-tag">已删除</div>');row.cells[2].textContent='-';
 }};
 const result=await f.call('prepare');assert.equal(result.ok,false);assert.match(result.message,new RegExp('本批素材已删除 '+ids[0]));
 assert.equal(f.selected.size,0);assert.equal(f.submits,0);assert.equal((await f.call('cleanup')).ok,true);
});

test('a filename containing deleted text is not a native deleted-material badge',async()=>{
 const f=apiSearchFixture(),d=f.w.document,search=d.querySelector('[data-material-search]'),handler=search.onkeyup;
 search.onkeyup=e=>{handler(e);for(const row of d.querySelectorAll('tbody tr'))row.cells[1].insertAdjacentHTML('afterbegin','<span>已删除旧片-重新剪辑</span>');};
 const result=await f.call('prepare',{preflight_only:true});assert.equal(result.preflight_ready,true,result.message);assert.equal(f.submits,0);
});

test('API selection waits for the native material table to finish its initial asynchronous load',async()=>{
 const f=apiSearchFixture(),d=f.w.document,table=d.querySelector('table'),box=d.createElement('div');box.className='table-container loading';table.replaceWith(box);box.append(table);
 f.w.setTimeout(()=>box.classList.remove('loading'),300);
 const result=await f.call('prepare');assert.equal(result.prepared,true,result.message);assert.equal(f.submits,0);
 assert.deepEqual(f.queries,ids);
});

test('API exact-ID selection waits for asynchronous audit and chase cells after the row is already visible',async()=>{
 const f=apiSearchFixture(),d=f.w.document,search=d.querySelector('[data-material-search]'),handler=search.onkeyup;
 let completed=0;
 search.onkeyup=e=>{
  handler(e);if(e.key!=='Enter')return;
  const row=d.querySelector('tbody tr');if(!row)return;
  row.cells[2].innerHTML='<div class="cell-async-loading">-</div>';
  row.cells[3].innerHTML='<div class="cell-async-loading"></div>';
  f.w.setTimeout(()=>{row.cells[2].textContent='审核通过';},200);
  f.w.setTimeout(()=>{row.cells[3].textContent='已暂停';completed++;},450);
 };
 const result=await f.call('prepare',{preflight_only:true});
 assert.equal(result.preflight_ready,true,result.message);assert.equal(completed,ids.length);
 assert.deepEqual([...result.observation.selected_materials].map(r=>r.id),ids);assert.equal(f.submits,0);
});

test('unresolved audit placeholders remain blocked and are never treated as approval',async()=>{
 const f=fixture();f.w.document.querySelector('tbody tr').cells[2].innerHTML='<div class="cell-async-loading">-</div>';
 const result=await f.call('read');assert.equal(result.ok,false);assert.match(result.message,/等待素材审核状态异步加载/);assert.equal(f.submits,0);
});

test('API selection waits for each checked row to settle when the native table reloads after selection',async()=>{
 const f=apiSearchFixture({selectionLoads:true}),result=await f.call('prepare');
 assert.equal(result.prepared,true,result.message);assert.deepEqual([...f.selected],[ids.at(-1)]);assert.equal(f.submits,0);
 assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.selected.size,0);
});

test('a picker that omits one requested material cannot pass the final immutable ID set check',async()=>{
 const f=apiSearchFixture({dropPickerMaterial:true}),result=await f.call('prepare');
 assert.equal(result.ok,false);assert.match(result.message,/表单素材集合不一致/);assert.equal(f.submits,0);
 assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.selected.size,0);
});

test('API ID preparation clears native view filters without changing batch IDs or requesting a full scan',async()=>{
 const f=apiSearchFixture({filtered:true}),result=await f.call('prepare');
 assert.equal(result.prepared,true,result.message);assert.equal(f.clears,1);assert.deepEqual(f.queries,ids);assert.equal(f.submits,0);
 assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.selected.size,0);
});

test('a confirmed empty exact-ID result identifies only that unavailable candidate and cleans up',async()=>{
 const f=apiSearchFixture({missingMaterial:ids[0]}),result=await f.call('prepare');
 assert.equal(result.ok,false);assert.match(result.message,new RegExp('本批素材ID搜索为空 '+ids[0]));assert.equal(f.submits,0);
 assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.selected.size,0);
});

test('unrelated checked controls in a material row neither block selection nor change during a preview',async()=>{
 const f=apiSearchFixture({extraToggle:true}),result=await f.call('prepare');
 assert.equal(result.prepared,true,result.message);assert.equal(f.otherChanges,0);assert.equal(f.submits,0);
 assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.otherChanges,0);assert.equal(f.selected.size,0);
});

test('an unreadable leading selection control remains an inspection error instead of an unavailable material',async()=>{
 const f=apiSearchFixture({noSelectionControl:true}),result=await f.call('prepare');
 assert.equal(result.ok,false);assert.match(result.message,/素材左侧选择框未唯一明确/);assert.equal(f.submits,0);
});

test('an unknown audit label is an inspection error, not an invented rejected status',async()=>{
 const f=fixture();f.w.document.querySelector('tbody tr').cells[2].textContent='未知平台状态';
 const result=await f.call('read');assert.equal(result.ok,false);assert.match(result.message,/素材审核状态未明确.*未知平台状态/);assert.equal(f.submits,0);
});

test('known optimization advice beside an approved audit label is not a different audit state',async()=>{
 const f=fixture();f.w.document.querySelector('tbody tr').cells[2].textContent='审核通过可优化\n可优化\n审核建议';
 const result=await f.call('read');assert.equal(result.ok,true,result.message);assert.equal(f.submits,0);
});

test('an approval string mixed with an unknown or pending audit state cannot authorize selection',async()=>{
 for(const suffix of ['审核中','审核不通过','未知平台状态']){
  const f=fixture();f.w.document.querySelector('tbody tr').cells[2].textContent='审核通过'+suffix;
  const result=await f.call('read');assert.equal(result.ok,false);assert.equal(f.submits,0);
 }
});

test('an explicitly disabled leading checkbox identifies only its material as unavailable',async()=>{
 const f=apiSearchFixture({disabledFirst:true}),result=await f.call('prepare');
 assert.equal(result.ok,false);assert.match(result.message,new RegExp('本批素材左侧选择框已禁用 '+ids[0]));assert.equal(f.submits,0);
 assert.equal((await f.call('cleanup')).ok,true);assert.equal(f.selected.size,0);
});

test('the exact read-only Xingxuan promotional notice is closed without clicking its advertising CTA',async()=>{
 const f=fixture(),d=f.w.document;let cta=0;
 const root=d.createElement('div');root.className='main-vmok-plugin-modal__wrap';root.innerHTML='<h2>投放千川星选素材</h2><p>补充优质素材拓消耗</p><button aria-label="关闭">×</button><button data-cta>去投放</button>';
 root.querySelector('[aria-label]').onclick=()=>root.remove();root.querySelector('[data-cta]').onclick=()=>cta++;d.body.append(root);
 const result=await f.call('identity');assert.equal(result.ok,true,result.message);assert.equal(result.editor_open,false);assert.equal(root.isConnected,false);assert.equal(cta,0);assert.equal(f.submits,0);
});

test('a real editor containing promotional wording retains all entered values',async()=>{
 const f=fixture(),d=f.w.document;const root=d.createElement('div');root.className='main-vmok-plugin-modal__wrap';root.innerHTML='<h2>投放千川星选素材</h2><p>补充优质素材拓消耗</p><input value="人工草稿"><button aria-label="关闭">×</button>';
 root.querySelector('button').onclick=()=>root.remove();d.body.append(root);
 const result=await f.call('identity');assert.equal(result.editor_open,true);assert.equal(root.isConnected,true);assert.equal(root.querySelector('input').value,'人工草稿');assert.equal(f.submits,0);
});

test('unknown popups and an ambiguous notice close control are never dismissed',async()=>{
 for(const ambiguous of [false,true]){
  const f=fixture(),d=f.w.document;let clicks=0;const root=d.createElement('div');root.className='main-vmok-plugin-modal__wrap';
  root.innerHTML=ambiguous?'<h2>投放千川星选素材</h2><p>补充优质素材拓消耗</p><button aria-label="关闭">×</button><button aria-label="关闭">×</button>':'<h2>未识别窗口</h2><button aria-label="关闭">×</button>';
  for(const button of root.querySelectorAll('button'))button.onclick=()=>clicks++;d.body.append(root);
  const result=await f.call('identity');assert.equal(result.editor_open,true);assert.equal(clicks,0);assert.equal(root.isConnected,true);assert.equal(f.submits,0);
 }
});

test('editor_recover closes a /uni-prom ovui-modal video picker via its unique div.ovui-modal__close-icon without confirming',async()=>{
 const f=fixture('/uni-prom'),d=f.w.document;
 f.command.executor_kind='ego';f.command.cleanup_action_ids=['one'];f.command.action.intent.browser_batch.allow_running_materials=false;
 const form=d.createElement('div');form.className='ovui-modal__wrap';form.innerHTML='<button data-cancel><span>取消</span></button>';
 form.querySelector('[data-cancel]').onclick=()=>form.remove();
 const picker=d.createElement('div');picker.className='ovui-modal__wrap oc-modal-wrap assist-task-modal';
 picker.innerHTML='<div>添加视频</div><input placeholder="输入视频名称/ID后回车搜索"><div class="ovui-modal__close-icon"><div class="ovui-icon"><svg></svg></div></div><button data-confirm>确定</button>';
 let confirms=0;picker.querySelector('[data-confirm]').onclick=()=>confirms++;
 picker.querySelector('.ovui-modal__close-icon').onclick=()=>picker.remove();
 d.body.append(form,picker);
 f.context.__qc_hybrid_editor_v1={action_id:'one',root:form,fields:[],picker_open:true,home_context:{account_id:aid,plan_id:pid}};
 const result=await f.call('editor_recover');
 assert.equal(result.ok,true,result.message);assert.equal(result.cleaned,true);assert.equal(confirms,0);
 assert.equal(picker.isConnected,false);assert.equal(form.isConnected,false);assert.equal(f.submits,0);
});

test('editor_recover preserves home_context after direct_home prepare on /overall-prom without adId',async()=>{
 const f=apiSearchFixture(),d=f.w.document;d.body.insertAdjacentHTML('afterbegin','<div>抖音号ID：79987870170</div><button>推直播间</button>');
 d.querySelector('table').remove();d.querySelector('.main-vmok-plugin-pagination').remove();d.querySelector('[data-material-search]').remove();
 const pre=await f.call('home_identity');assert.equal(pre.ok,true,pre.message);
 d.querySelector('[data-chase]').click();const root=d.querySelector('.main-vmok-plugin-modal__wrap');
 d.body.insertAdjacentHTML('beforeend','<table><tbody><tr data-plan-id="'+pid+'"><td>投放中 每日预算500000.00元 控成本投放</td></tr></tbody></table>');
 f.dom.reconfigure({url:'https://qianchuan.jinritemai.com/overall-prom?aavid='+aid});
 f.command.executor_kind='ego';f.command.cleanup_action_ids=[f.command.action.id];
 const context=vm.createContext({URL,URLSearchParams,Date,crypto:require('node:crypto').webcrypto,document:d,location:f.w.location,getComputedStyle:f.w.getComputedStyle.bind(f.w),
  Event:f.w.Event,MouseEvent:f.w.MouseEvent,KeyboardEvent:f.w.KeyboardEvent,HTMLInputElement:f.w.HTMLInputElement,window:f.w,chrome:{runtime:{sendMessage:async m=>{
   if(m?.action==='hybrid-native-search'){const inp=d.querySelector('[data-picker-search]');if(inp){inp.value=m.material_id;inp.onkeyup?.({key:'Enter'});}}
   return {ok:true,allow:false};
  }}}});vm.runInContext(source,context);
 context.__qc_hybrid_editor_v1={root,action_id:f.command.action.id,selected:[],selected_ids:null,home_context:pre.binding};
 const args={command:f.command,home_context:pre.binding,detail_preflight:pre.observation,home_proof:{path:'/overall-prom'}};
 const prep=await context.hybridChaseDom('prepare',args);assert.equal(prep.prepared,true,prep.message);
 const rec=await context.hybridChaseDom('editor_recover',{command:f.command});
 assert.equal(rec.ok,true,rec.message);assert.equal(rec.cleaned,true);
 assert.equal(root.isConnected,false);assert.equal(f.submits,0);
});

test('Ego recovers an orphaned task form only with exact ledger evidence and a cancel button',async()=>{
 for(const variation of ['exact','changed-budget','changed-material','unproven','other-plan','human','audience']){
  const f=fixture('/overall-prom'),d=f.w.document;f.command.executor_kind='ego';
  f.dom.reconfigure({url:'https://qianchuan.jinritemai.com/overall-prom?aavid='+aid});
  const name='QCX_1118_0123456789abcdef',root=d.createElement('div');root.className='ovui-modal__wrap';
  root.innerHTML='<h2>新建调控任务</h2><div>追投方式</div><div>追投素材</div>'+ids.map((id,index)=>`<p>素材ID: ${variation==='changed-material'&&index===0?'8220850092912370541':id}</p>`).join('')+
    `<label>调控日预算<input value="${variation==='changed-budget'?500:300}"></label><label>综合营销ROI目标<input value="2.5"></label>`+
    `<label>任务名称<input value="${variation==='human'?'手工草稿':name}"></label><button data-cancel>取消</button><button data-submit>提交</button>`+
    (variation==='audience'?'<button>编辑定向</button>':'');
  let submits=0;root.querySelector('[data-submit]').onclick=()=>submits++;root.querySelector('[data-cancel]').onclick=()=>root.remove();d.body.append(root);
  assert.equal((await f.call('editor_guard')).editor_task_name,variation==='human'?null:name);
  const editor_proof={account_id:aid,plan_id:variation==='other-plan'?'123456789':pid,action_id:'old',task_name:name,material_ids:ids,
    after:{chase_type:'控成本追投',daily_budget:300,optimization_target:'综合营销ROI',bid_value:2.5}};
  const result=await f.call('editor_recover',variation==='unproven'?{}:{editor_proof});
  assert.equal(result.ok,true,result.message);assert.equal(result.cleaned,variation==='exact',variation);
  assert.equal(root.isConnected,variation!=='exact',variation);assert.equal(submits,0);
 }
});

test('/uni-prom/detail accepts 推商品 breadcrumb for direct_home home_identity without material table',async()=>{
 const f=apiSearchFixture(),d=f.w.document;f.command.executor_kind='ego';
 f.dom.reconfigure({url:'https://qianchuan.jinritemai.com/uni-prom/detail?aavid='+aid+'&adId='+pid});
 d.body.insertAdjacentHTML('afterbegin','<div class="ovui-breadcrumb"><a class="ovui-breadcrumb-item"><span>推商品</span></a></div><div>抖音号ID：zlp_7777777</div>');
 d.querySelector('table').remove();d.querySelector('.main-vmok-plugin-pagination').remove();d.querySelector('[data-material-search]').remove();
 const pre=await f.call('home_identity');
 assert.equal(pre.ok,true,pre.message);assert.equal(pre.observation.kind,'direct_home_identity');assert.equal(pre.binding.aweme_id,'zlp_7777777');
});

test('editor_recover dismisses all-shop-upgrade-modal-wrap platform notice via its close icon without touching delivery',async()=>{
 const f=fixture(),d=f.w.document;f.command.executor_kind='ego';
 const root=d.createElement('div');
 root.className='tools-vmok-plugin-modal__wrap ocean-vmok-plugin-oc-modal-wrap all-shop-upgrade-modal-wrap';
 root.innerHTML='<div class="tools-vmok-plugin-modal"><div class="tools-vmok-plugin-modal__close-icon"><div class="tools-vmok-plugin-modal__close-icon-wrapper">×</div></div><h2>全店托管重磅升级</h2><p>单号单账户升级多号多账户投放</p><button>立即体验</button></div>';
 let ctaClicks=0;
 root.querySelector('button').onclick=()=>ctaClicks++;
 root.querySelector('.tools-vmok-plugin-modal__close-icon').onclick=()=>root.remove();
 d.body.append(root);
 const res=await f.call('editor_recover');
 assert.equal(res.ok,true,res.message);assert.equal(res.cleaned,true);
 assert.equal(root.isConnected,false);assert.equal(ctaClicks,0);assert.equal(f.submits,0);
});

test('identity ignores content-info-container right_id labels while still rejecting foreign account IDs',async()=>{
 const f=apiSearchFixture(),d=f.w.document;f.command.executor_kind='ego';
 f.dom.reconfigure({url:'https://qianchuan.jinritemai.com/uni-prom/detail?aavid='+aid+'&adId='+pid});
 d.body.insertAdjacentHTML('afterbegin','<div class="ovui-breadcrumb"><a class="ovui-breadcrumb-item"><span>推商品</span></a></div><div class="qc-navigator-account"><div class="shop-name">ID：'+aid+'</div></div><div>抖音号ID：zlp_7777777</div><div class="content-info-container"><div class="content-info-item"><div class="item_right"><div class="right_id">ID:79987870170</div></div></div><div class="content-info-item"><div class="item_right"><div class="right_id">ID:59187099378</div></div></div></div>');
 d.querySelector('table').remove();d.querySelector('.main-vmok-plugin-pagination').remove();d.querySelector('[data-material-search]').remove();
 const okRes=await f.call('home_identity');
 assert.equal(okRes.ok,true,okRes.message);
 d.body.insertAdjacentHTML('afterbegin','<div class="other-account">ID：8356939714484742</div>');
 const badRes=await f.call('home_identity');
 assert.equal(badRes.ok,false);assert.match(badRes.message,/可见账户ID与本户不符/);
});

test('dismissKnownNotice closes buttonless tools-vmok-plugin-modal__wrap expired-auth notice via close icon',async()=>{
 const f=apiSearchFixture(),d=f.w.document;f.command.executor_kind='ego';
 f.dom.reconfigure({url:'https://qianchuan.jinritemai.com/uni-prom/detail?aavid='+aid+'&adId='+pid});
 d.body.insertAdjacentHTML('afterbegin','<div class="ovui-breadcrumb"><a class="ovui-breadcrumb-item"><span>推商品</span></a></div><div>抖音号ID：zlp_7777777</div>');
 d.querySelector('table').remove();d.querySelector('.main-vmok-plugin-pagination').remove();d.querySelector('[data-material-search]').remove();
 const wrap=d.createElement('div');
 wrap.className='tools-vmok-plugin-modal__wrap ocean-vmok-plugin-oc-modal-wrap';
 wrap.innerHTML='<div class="tools-vmok-plugin-modal"><div class="tools-vmok-plugin-modal__close-icon">×</div><div>以下投放权限已失效，相关千川乘方及全域投放不可用</div><div class="content-info-container"><div class="right_id">ID:79987870170</div></div><div>我知道了</div></div>';
 wrap.querySelector('.tools-vmok-plugin-modal__close-icon').onclick=()=>wrap.remove();
 d.body.append(wrap);
 const res=await f.call('identity');
 assert.equal(res.ok,true,res.message);assert.equal(wrap.isConnected,false);assert.equal(f.submits,0);
});

test('a transient dropped picker checkbox click retries inside the modal and completes the batch',async()=>{
 const f=apiSearchFixture(),d=f.w.document;
 const addBtnHook=d.querySelector('[data-chase]').onclick;
 d.querySelector('[data-chase]').onclick=()=>{
  addBtnHook();
  const add=d.querySelector('.main-vmok-plugin-modal__wrap button:last-of-type');
  const openPicker=add.onclick;
  add.onclick=()=>{
   openPicker();
   const input=d.querySelector('[data-picker-search]'),prevKeyup=input.onkeyup;
   let droppedOnce=false;
   input.onkeyup=e=>{
    prevKeyup(e);
    const box=d.querySelector('[data-picker-search]')?.parentElement.querySelector('tbody input[type="checkbox"]');
    if(box&&!droppedOnce){
     const realClick=box.click.bind(box);
     box.click=()=>{if(!droppedOnce){droppedOnce=true;return;}realClick();};
    }
   };
  };
 };
 const result=await f.call('prepare');
 assert.equal(result.ok,true,result.message);assert.equal(result.prepared,true);
 assert.deepEqual([...result.observation.selected_materials].map(m=>m.id).sort(),[...ids].sort());
 assert.equal(f.submits,0);assert.equal((await f.call('cleanup')).ok,true);
});

test('a picker checkbox that never syncs across retries still fails closed without submitting',async()=>{
 const f=apiSearchFixture(),d=f.w.document;
 const addBtnHook=d.querySelector('[data-chase]').onclick;
 d.querySelector('[data-chase]').onclick=()=>{
  addBtnHook();
  const add=d.querySelector('.main-vmok-plugin-modal__wrap button:last-of-type');
  const openPicker=add.onclick;
  add.onclick=()=>{
   openPicker();
   const input=d.querySelector('[data-picker-search]'),prevKeyup=input.onkeyup;
   input.onkeyup=e=>{
    prevKeyup(e);
    const box=d.querySelector('[data-picker-search]')?.parentElement.querySelector('tbody input[type="checkbox"]');
    if(box){box.click=()=>{};}
   };
  };
 };
 const result=await f.call('prepare');
 assert.equal(result.ok,false);assert.match(result.message,/添加视频勾选未同步，未提交/);
 assert.equal(f.submits,0);assert.equal((await f.call('cleanup')).ok,true);
});

test('addPickerMaterial waits for unfiltered pre-search rows to narrow before clicking the checkbox',async()=>{
 const f=apiSearchFixture({extraMaterial:true}),d=f.w.document;
 let staleClicks=0;
 const addBtnHook=d.querySelector('[data-chase]').onclick;
 d.querySelector('[data-chase]').onclick=()=>{
  addBtnHook();
  const add=d.querySelector('.main-vmok-plugin-modal__wrap button:last-of-type');
  const openPicker=add.onclick;
  add.onclick=()=>{
   openPicker();
   const input=d.querySelector('[data-picker-search]'),prevKeyup=input.onkeyup;
   for(const box of d.querySelector('[data-picker-search]').parentElement.querySelectorAll('tbody input[type="checkbox"]')){
    box.addEventListener('click',()=>staleClicks++);
   }
   let delayedOnce=false;
   input.onkeyup=e=>{
    if(e.key==='Enter'&&!delayedOnce){
     delayedOnce=true;
     // Simulate async search response: keep 2 unfiltered rows visible for 450ms before narrowing to 1 row
     setTimeout(()=>prevKeyup(e),450);
     return;
    }
    prevKeyup(e);
   };
  };
 };
 const result=await f.call('prepare');
 assert.equal(result.ok,true,result.message);assert.equal(result.prepared,true);
 assert.equal(staleClicks,0);
 assert.deepEqual([...result.observation.selected_materials].map(m=>m.id).sort(),[...f.command.action.intent.browser_batch.material_ids].sort());
 assert.equal(f.submits,0);assert.equal((await f.call('cleanup')).ok,true);
});

test('a wrapper-disabled picker checkbox reports disabled material instead of checkbox sync failure',async()=>{
 const f=apiSearchFixture({extraMaterial:true}),d=f.w.document;
 const addBtnHook=d.querySelector('[data-chase]').onclick;
 d.querySelector('[data-chase]').onclick=()=>{
  addBtnHook();
  const add=d.querySelector('.main-vmok-plugin-modal__wrap button:last-of-type');
  const openPicker=add.onclick;
  add.onclick=()=>{
   openPicker();
   const input=d.querySelector('[data-picker-search]'),prevKeyup=input.onkeyup;
   input.onkeyup=e=>{
    prevKeyup(e);
    if(input.value===ids[1]){
     const cell=d.querySelector('[data-picker-search]')?.parentElement.querySelector('tbody tr td');
     if(cell)cell.innerHTML='<label class="ovui-checkbox ovui-checkbox--disabled" aria-disabled="true"><input type="checkbox"></label>';
    }
   };
  };
 };
 const result=await f.call('prepare');
 assert.equal(result.ok,false);assert.match(result.message,new RegExp('添加视频左侧选择框已禁用 '+ids[1]));
 assert.equal(f.submits,0);assert.equal((await f.call('cleanup')).ok,true);
});
