import test from 'node:test';import assert from 'node:assert/strict';
import {nativeMaterialSearch,nativeInventoryHover,nativeTaskSearch,nativeTaskStateFilter} from '../monitor/ego/native-search.mjs';
function fixture(){
 const calls=[],c={id:'command-one',run_id:'run-one',phase:'prepare',account_id:'8929253702525378',plan_id:'8582197091416415',
  action:{intent:{browser_batch:{selection_source:'official_api',material_ids:['8460213559110212151']}}}};
 const m={action:'hybrid-native-search',command_id:c.id,run_id:c.run_id,search_kind:'main',material_id:'8460213559110212151'};
 const page={evaluate:async(fn,args)=>{calls.push(['validate',args]);return true;},fill:async(...args)=>calls.push(['fill',...args]),press:async(...args)=>calls.push(['press',...args])};
 return {c,m,page,calls,checkpoint:async()=>calls.push(['checkpoint'])};
}

test('task state selection binds the owning portal and survives a controlled input replacement',async()=>{
 const {createRequire}=await import('node:module'),require=createRequire(import.meta.url),{JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom');
 for(const preopened of [false,true]){
  const f=fixture();f.c.phase='tasks';const key='actual-status';
  const dom=new JSDOM('<div data-auto-id="oc-row-cascader" data-e2e="'+key+'"><div><div class="input__prefix">投放状态：</div><input readonly value="全部（包括已删除）" data-qcx-task-state="'+f.c.id+'"></div></div><div data-e2e="'+key+'_popover_content" style="display:'+(preopened?'block':'none')+'"><div class="cascader-panel__item-label">调控中</div><div class="cascader-panel__item-label">全部（包括已删除）</div></div><div data-e2e="foreign_popover_content"><div class="cascader-panel__item-label">调控中</div></div>',{url:'https://qianchuan.jinritemai.com/overall-prom/detail?aavid='+f.c.account_id+'&adId='+f.c.plan_id});
  const w=dom.window,d=w.document;Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});
  w.HTMLElement.prototype.getClientRects=function(){for(let e=this;e;e=e.parentElement)if(e.style?.display==='none')return [];return this.isConnected?[{}]:[];};
  const previous={document:global.document,location:global.location,getComputedStyle:global.getComputedStyle};
  f.page.evaluate=async(fn,args)=>{Object.assign(global,{document:d,location:w.location,getComputedStyle:w.getComputedStyle.bind(w)});try{return fn(args);}finally{Object.assign(global,previous);}};
  let opened=0,selected=0;
  f.page.click=async selector=>{f.calls.push(['click',selector]);const e=d.querySelector(selector.replace('loc=css:',''));
   assert.ok(e);if(e.tagName==='INPUT'){opened++;d.querySelector('[data-e2e="actual-status_popover_content"]').style.display='block';}
   else{selected++;assert.equal(e.closest('[data-e2e]').getAttribute('data-e2e'),'actual-status_popover_content');const old=d.querySelector('input'),fresh=old.cloneNode();fresh.removeAttribute('data-qcx-task-state');fresh.value='调控中';old.replaceWith(fresh);e.parentElement.style.display='none';}
  };
  await nativeTaskStateFilter(f.page,f.c,{run_id:f.c.run_id,command_id:f.c.id,label:'调控中'},async()=>{});
  assert.equal(opened,preopened?0:1);assert.equal(selected,1);assert.equal(d.querySelector('input').value,'调控中');assert.equal(d.querySelector('[data-qcx-task-state-option]'),null);
 }
});

test('native task state rejects arbitrary values, foreign commands and editing phases',async()=>{
 const f=fixture();f.c.phase='tasks';const m={run_id:f.c.run_id,command_id:f.c.id,label:'调控中'};
 for(const change of [{label:'已暂停'},{label:'预算'},{run_id:'other'},{command_id:'other'}])await assert.rejects(nativeTaskStateFilter(f.page,f.c,{...m,...change},f.checkpoint));
 assert.equal(f.calls.length,0);f.c.phase='prepare';await assert.rejects(nativeTaskStateFilter(f.page,f.c,m,f.checkpoint));assert.equal(f.calls.length,0);
});
test('native material search validates scope and uses native fill and Enter between lease checks',async()=>{
 const f=fixture();assert.deepEqual(await nativeMaterialSearch(f.page,f.c,f.m,f.checkpoint),{ok:true});
 assert.deepEqual(f.calls.map(c=>c[0]),['checkpoint','validate','fill','press','checkpoint']);
 assert.equal(f.calls[2][2],f.m.material_id);assert.equal(f.calls[3][2],'Enter');
 assert.ok(f.calls[2][1].includes('data-qcx-native-search="command-one-main"'));
});
test('foreign IDs, stale commands, arbitrary kinds, and rule jobs never reach browser input',async()=>{
 for(const change of [{material_id:'8937389604663702250'},{run_id:'other'},{command_id:'other'},{search_kind:'budget'}]){
  const f=fixture();await assert.rejects(nativeMaterialSearch(f.page,f.c,{...f.m,...change},f.checkpoint));assert.equal(f.calls.length,0);
 }
 const f=fixture();f.c.rule_job=true;await assert.rejects(nativeMaterialSearch(f.page,f.c,f.m,f.checkpoint));assert.equal(f.calls.length,0);
});
test('a changed page or non-search field cannot receive even a permitted material ID',async()=>{
 const f=fixture();f.page.evaluate=async()=>false;await assert.rejects(nativeMaterialSearch(f.page,f.c,f.m,f.checkpoint),/输入框不符/);
 assert.deepEqual(f.calls.map(c=>c[0]),['checkpoint']);
});

test('direct-home commands can only search inside the add-video picker, never the material page',async()=>{
 const f=fixture();f.c.direct_home=true;
 await assert.rejects(nativeMaterialSearch(f.page,f.c,f.m,f.checkpoint),/不属于当前批次/);assert.equal(f.calls.length,0);
 assert.deepEqual(await nativeMaterialSearch(f.page,f.c,{...f.m,search_kind:'picker'},f.checkpoint),{ok:true});
});

test('inventory opens only a validated native thumbnail within a direct-home read lease',async()=>{
 const f=fixture();f.c.phase='tasks';f.c.direct_home=true;f.page.hover=async()=>f.calls.push(['hover']);f.page.click=async()=>f.calls.push(['click']);
 const m={run_id:f.c.run_id,command_id:f.c.id,task_id:'8504100832334306',open_more:true};
 assert.deepEqual(await nativeInventoryHover(f.page,f.c,m,f.checkpoint),{ok:true,fresh_hover_task:m.task_id});assert.deepEqual(f.calls.map(c=>c[0]),['checkpoint','validate','hover','hover','click','checkpoint']);
});

test('inventory clears a blocking previous popover and binds shifted columns by header',async()=>{
 const {createRequire}=await import('node:module'),require=createRequire(import.meta.url),{JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom');
 for(const shifted of [false,true]){
  const f=fixture();f.c.phase='tasks';f.c.direct_home=true;const id='8504100832334306',marker=f.c.id+'-inventory-'+id;
  const headers=['',...(shifted?['序号']:[]),'任务名称','素材','调控状态'];
  const dom=new JSDOM('<table><thead><tr>'+headers.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody><tr><td></td>'+(shifted?'<td>1</td>':'')+'<td>原任务\nID: '+id+'</td><td><div class="media-group-list-ellipsis" data-qcx-inventory="'+marker+'">…</div></td><td>调控中</td></tr></tbody></table>',{url:'https://qianchuan.jinritemai.com/uni-prom/detail?aavid='+f.c.account_id+'&adId='+f.c.plan_id});
  Object.defineProperty(dom.window.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});dom.window.HTMLElement.prototype.getClientRects=()=>[{}];dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  const previous={document:global.document,location:global.location};let covered=true;const moves=[];
  f.page.evaluate=async(fn,args)=>{global.document=dom.window.document;global.location=dom.window.location;try{return fn(args);}finally{global.document=previous.document;global.location=previous.location;}};
  f.page.hover=async selector=>{moves.push(selector);if(selector.includes('inventory-clear'))covered=false;else assert.equal(covered,false,'the previous popover must be dismissed first');};f.page.click=async()=>{};
  await nativeInventoryHover(f.page,f.c,{run_id:f.c.run_id,command_id:f.c.id,task_id:id,open_more:true},async()=>{});
  assert.equal(moves.length,2);assert.match(moves[0],/inventory-clear/);
 }
});

test('inventory rejects a stale command or invalid target before any native hover',async()=>{
 for(const change of [{command_id:'foreign'},{task_id:'budget'},{open_more:'true'}]){
  const f=fixture();f.c.phase='tasks';f.c.direct_home=true;const m={run_id:f.c.run_id,command_id:f.c.id,task_id:'8504100832334306',open_more:true,...change};
  await assert.rejects(nativeInventoryHover(f.page,f.c,m,f.checkpoint));assert.equal(f.calls.length,0);
 }
 const f=fixture();f.c.phase='tasks';f.c.direct_home=true;f.page.evaluate=async()=>false;
 await assert.rejects(nativeInventoryHover(f.page,f.c,{run_id:f.c.run_id,command_id:f.c.id,task_id:'8504100832334306',open_more:true},f.checkpoint),/缩略图不符/);
 assert.deepEqual(f.calls.map(c=>c[0]),['checkpoint']);
});
test('original task search only enters the immutable name in a scoped read filter',async()=>{
 const f=fixture();f.c.phase='verify';f.c.action.intent.browser_batch.task_name='QCX_original';
 const m={run_id:f.c.run_id,command_id:f.c.id,needle:'QCX_original'};
 assert.deepEqual(await nativeTaskSearch(f.page,f.c,m,f.checkpoint),{ok:true});assert.equal(f.calls[2][2],'QCX_original');
 assert.deepEqual(f.calls.map(v=>v[0]),['checkpoint','validate','fill','press','checkpoint']);
 for(const change of [{needle:'other'},{command_id:'old'},{run_id:'old'}])await assert.rejects(nativeTaskSearch(f.page,f.c,{...m,...change},f.checkpoint));
 f.page.evaluate=async()=>false;await assert.rejects(nativeTaskSearch(f.page,f.c,m,f.checkpoint),/只读输入框不符/);
});
test('rule readback can search only its original task ID, never a rule editing command',async()=>{
 const f=fixture();Object.assign(f.c,{rule_job:true,rule_phase:'verify',phase:'lookup',lookup_id:'8504100832334306'});f.c.action.intent.task_id=f.c.lookup_id;
 const m={run_id:f.c.run_id,command_id:f.c.id,needle:f.c.lookup_id};
 assert.deepEqual(await nativeTaskSearch(f.page,f.c,m,f.checkpoint),{ok:true});
 f.c.lookup_id='8335340424647304';await assert.rejects(nativeTaskSearch(f.page,f.c,{...m,needle:f.c.lookup_id},f.checkpoint));
 f.c.lookup_id=f.c.action.intent.task_id;f.c.rule_phase='prepare';await assert.rejects(nativeTaskSearch(f.page,f.c,m,f.checkpoint));
});
