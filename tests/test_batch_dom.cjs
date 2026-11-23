'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const source=fs.readFileSync(require.resolve('../extension/batch-dom.js'),'utf8');
const aid='1999999999999002',pid='8218616704149013',ids=['8204031517311756221','8103844353694828102','8422644343410380385'];
function fixture(){
 const detail={tb:'creative',cc:JSON.stringify({p:'1',ps:'100',sf:'stat_cost',st:'desc'})};
 const url='https://qianchuan.jinritemai.com/uni-prom/detail?aavid='+aid+'&adId='+pid+'&dr=2026-10-22,2026-10-26#uniDetail='+encodeURIComponent(JSON.stringify(detail));
 const body=`<div>ID：${aid}\n</div><div class="oc-promotion-key-info-bar">计划ID：${pid}</div>
 <input placeholder="请选择开始日期" value="2026-10-22"><input placeholder="请选择结束日期" value="2026-10-26"><div class="ovui-pagination"><input value="100条/页"><button class="ovui-pagination-item-active">1</button><button>2</button></div>
 <button data-auto-id="bar-groups-group-item-btn-videoChase">追投</button>
 <table><thead><tr>${['','视频','审核状态','素材追投','整体消耗(元)','整体支付ROI'].map(h=>'<th>'+h+(h==='整体消耗(元)'?'<span class="ovui-th__sorter-down ovui-th__sorter-down--active"></span>':'')+'</th>').join('')}</tr></thead><tbody>
 ${ids.map((id,i)=>`<tr><td><input type="checkbox"></td><td>素材${i}\n素材ID: ${id}</td><td>审核通过</td><td>-</td><td>${500-i*100}</td><td>4.2</td></tr>`).join('')}</tbody></table>`;
 const dom=new JSDOM(body,{url}),w=dom.window;let submissions=0,permit=true;
 Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});
 w.HTMLElement.prototype.getClientRects=function(){return this.isConnected&&this.style.display!=='none'?[{width:100,height:30}]:[];};
 w.document.querySelector('[data-auto-id]').onclick=()=>{
  const root=w.document.createElement('div');root.className='ovui-modal';
  const selected=[...w.document.querySelectorAll('tbody tr')].filter(r=>r.querySelector('input').checked).map(r=>r.textContent.match(/素材ID:\s*(\d+)/)[1]);
  root.innerHTML=`<h2>追投方式</h2><button>控成本追投</button><button>放量追投</button><div>${selected.map(id=>'<p>素材ID: '+id+'</p>').join('')}</div>
   <label>优化目标<input value="直播间成交"></label><label>调控日预算<input type="number" step="0.01"></label><label>出价<input type="number" step="0.01"></label>
   <label>任务名称<input></label><button data-save>确定</button><button data-cancel>取消</button>`;
  root.querySelector('[data-save]').onclick=()=>submissions++;root.querySelector('[data-cancel]').onclick=()=>root.remove();w.document.body.append(root);
 };
 const context=vm.createContext({URL,URLSearchParams,Date,crypto:require('node:crypto').webcrypto,document:w.document,location:w.location,
  getComputedStyle:w.getComputedStyle.bind(w),Event:w.Event,MouseEvent:w.MouseEvent,window:w,HTMLInputElement:w.HTMLInputElement,HTMLTextAreaElement:w.HTMLTextAreaElement,
  chrome:{runtime:{sendMessage:async()=>({allow:permit})}},setTimeout,clearTimeout});
 vm.runInContext(source,context);
 const req={identity:{accountId:aid,planId:pid,group:'示例分组A',path:'/uni-prom/detail',role:'detail',scene:'live'},id:'draft-one',intent_digest:'digest-one',
  expires_at:new Date(Date.now()+60000).toISOString(),intent:{target_kind:'material_batch',account_id:aid,plan_id:pid,material_ids:ids,date_start:'2026-10-22',date_end:'2026-10-26',surface:{page:1},
   task_name:'QC_native_fixture',after:{chase_type:'控成本追投',optimization_target:'直播间成交',daily_budget:200,bid_value:36.88}}};
 const call=(phase,r=req)=>context.batchMaterialDom(phase,r);
 return {dom,w,req,call,get submissions(){return submissions;},set permit(v){permit=v;}};
}
test('native material table reads exact long IDs, ROI and its own five-day range',async()=>{
 const f=fixture(),r=await f.call('inspect');assert.equal(r.surface.date_start,'2026-10-22');assert.equal(r.surface.page_size,100);
 assert.deepEqual(Array.from(r.surface.materials,r=>r.id),ids);assert.equal(r.surface.materials[0].spend,500);assert.equal(f.submissions,0);
});
test('readonly setup clicks the actual 100-row option instead of accepting a forged URL setting',async()=>{
 const f=fixture(),input=f.w.document.querySelector('.ovui-pagination input');input.value='10条/页';let optionClicks=0;
 input.onclick=()=>{if(f.w.document.querySelector('[data-options]'))return;const menu=f.w.document.createElement('ul');menu.dataset.options='';
  menu.innerHTML=['10','20','50','100'].map(n=>`<li role="option">${n}条/页</li>`).join('');
  for(const li of menu.children)li.onclick=()=>{input.value=li.textContent;if(li.textContent==='100条/页')optionClicks++;
   // Render a new native row set, as an actual asynchronous page response would.
   f.w.document.querySelector('tbody tr td:nth-child(2)').textContent='新素材\n素材ID: 8149322630159633231';menu.remove();};
  f.w.document.body.append(menu);};
 const r=await f.call('configure_view');assert.equal(r.ok,true,r.code);assert.equal(optionClicks,1);assert.equal(r.before.page_size,10);assert.equal(r.after.page_size,100);assert.equal(f.submissions,0);
 assert.equal((await f.call('inspect')).surface.page_size,100);
});
test('actual pager and native descending marker must agree with requested metadata',async()=>{
 const f=fixture();f.w.document.querySelector('.ovui-pagination-item-active').className='';
 assert.equal((await f.call('inspect')).surface,null);
 const g=fixture();g.w.document.querySelector('.ovui-th__sorter-down').className='ovui-th__sorter-down';assert.equal((await g.call('inspect')).surface,null);
});
test('readonly setup refuses to overwrite an existing editor or manual selection',async()=>{
 const f=fixture();f.w.document.querySelector('tbody input').checked=true;
 assert.equal((await f.call('configure_view')).code,'view_editor_or_selection_open');assert.equal(f.w.document.querySelector('tbody input').checked,true);assert.equal(f.submissions,0);
});
test('ten-row page, wrong parent, mismatched date and ascending data cannot certify scan',async()=>{
 for(const change of [f=>f.w.document.querySelector('input[value="100条/页"]').value='10条/页',f=>f.w.document.querySelector('.oc-promotion-key-info-bar').textContent='计划ID：1234567',
   f=>f.w.document.querySelector('input[placeholder="请选择开始日期"]').value='2026-10-23',f=>f.w.document.querySelector('tbody tr td:nth-child(5)').textContent='1']){
  const f=fixture();change(f);assert.equal((await f.call('inspect')).surface,null);assert.equal(f.submissions,0);
 }
});
test('existing user editor and selection survive preparation refusal',async()=>{
 const f=fixture();f.w.document.querySelector('tbody input').checked=true;
 assert.equal((await f.call('prepare')).code,'existing_editor_or_selection');assert.equal(f.w.document.querySelector('tbody input').checked,true);assert.equal(f.submissions,0);
});
test('prepare selects exact batch, fills whole-batch amount and never submits',async()=>{
 const f=fixture(),r=await f.call('prepare');assert.equal(r.ok,true,r.code);assert.equal(r.prepared,true);assert.equal(f.submissions,0);
 assert.equal(f.w.document.querySelector('input[type="number"]').value,'200');assert.equal(r.surface.editor_open,false);
});
test('prepare waits for the native toolbar after the selected rows render',async()=>{
 const f=fixture(),entry=f.w.document.querySelector('[data-auto-id]');entry.remove();let scheduled=false;
 for(const box of f.w.document.querySelectorAll('tbody input'))box.addEventListener('click',()=>{
  if(!scheduled&&f.w.document.querySelectorAll('tbody input:checked').length===ids.length){scheduled=true;setTimeout(()=>f.w.document.body.append(entry),25);}
 });
 const r=await f.call('prepare');assert.equal(r.ok,true,r.code);assert.equal(scheduled,true);assert.equal(f.submissions,0);
});
test('native selected-count toolbar locates its unique chase entry without a data attribute',async()=>{
 const f=fixture(),entry=f.w.document.querySelector('[data-auto-id]');entry.removeAttribute('data-auto-id');
 const bar=f.w.document.createElement('div');bar.innerHTML='<span>已选3个 视频</span><button>删除</button><button>排除</button><button>添加标签</button>';bar.append(entry);f.w.document.body.append(bar);
 const r=await f.call('prepare');assert.equal(r.ok,true,r.code);assert.equal(f.submissions,0);
});
test('native submit label still requires the one-use permission and submits once',async()=>{
 const f=fixture(),entry=f.w.document.querySelector('[data-auto-id]'),open=entry.onclick;
 entry.onclick=()=>{open();f.w.document.querySelector('[data-save]').textContent='提交';};
 const p=await f.call('prepare');assert.equal(p.ok,true,p.code);assert.equal(f.submissions,0);
 const request={...f.req,submit_token:p.submit_token};assert.equal((await f.call('submit',request)).submitted,true);
 assert.equal((await f.call('submit',request)).submitted,false);assert.equal(f.submissions,1);
});
test('final permission permits one submission and replay cannot submit again',async()=>{
 const f=fixture(),p=await f.call('prepare');assert.equal(p.ok,true,p.code);
 const req={...f.req,submit_token:p.submit_token};assert.equal((await f.call('submit',req)).submitted,true);assert.equal(f.submissions,1);
 assert.equal((await f.call('submit',req)).submitted,false);assert.equal(f.submissions,1);
});
test('revoked permission and changed drawer ID block submission and clear owned selection',async()=>{
 for(const revoke of [f=>f.permit=false,f=>f.w.document.querySelector('.ovui-modal p').textContent='素材ID: 99999999']){
  const f=fixture(),p=await f.call('prepare');revoke(f);const r=await f.call('submit',{...f.req,submit_token:p.submit_token});
  assert.equal(r.submitted,false);assert.equal(f.submissions,0);assert.equal(f.w.document.querySelectorAll('tbody input:checked').length,0);
 }
});
test('a missing native form field fails before submit and never switches to an API',async()=>{
 const f=fixture();const old=f.w.document.querySelector('[data-auto-id]').onclick;
 f.w.document.querySelector('[data-auto-id]').onclick=()=>{old();f.w.document.querySelector('.ovui-modal label').remove();};
 const r=await f.call('prepare');assert.equal(r.ok,false);assert.equal(f.submissions,0);
});
test('task readback requires the unique generated name, all material IDs and exact batch parameters',async()=>{
 const f=fixture();f.w.document.querySelector('table').remove();const table=f.w.document.createElement('table');
 table.innerHTML=`<thead><tr>${['任务名称','调控预算(元)','追投方式','素材','优化目标','出价'].map(h=>'<th>'+h+'</th>').join('')}</tr></thead>
  <tbody><tr><td>QC_native_fixture\nID：8325113491300243</td><td>200</td><td>控成本追投</td><td>${ids.map(v=>'素材ID: '+v).join('\n')}</td><td>直播间成交</td><td>36.88</td></tr></tbody>`;
 f.w.document.body.append(table);assert.equal((await f.call('verify')).observation.task_id,'8325113491300243');
 table.querySelector('tbody td:nth-child(4)').textContent='素材ID: '+ids[0];assert.equal((await f.call('verify')).observation,null);assert.equal(f.submissions,0);
});
