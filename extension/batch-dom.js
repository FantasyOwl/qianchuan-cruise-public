/* Native page adapter. No platform requests. Only the immutable approved batch
 * can reach submit; reading, sorting and page-size setup cannot create tasks. */
async function batchMaterialDom(phase,request){
 const id=request.identity,key='__qc_batch_prepared_v1',viewKey='__qc_batch_view_pending_v1';
 const text=e=>(e?.innerText||e?.textContent||'').trim();
 const shown=e=>!!e&&e.isConnected&&getComputedStyle(e).display!=='none'&&getComputedStyle(e).visibility!=='hidden'&&e.getClientRects().length>0;
 const enabled=e=>shown(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true';
 const norm=s=>s.replace(/[\s()（）]/g,'').replace(/元$/,'');
 const roots=()=>[...document.querySelectorAll('.ovui-modal,.ovui-drawer,[role="dialog"],[aria-modal="true"]')].filter(shown).filter((e,i,a)=>!a.some(x=>x!==e&&x.contains(e)));
 const exact=(root,label)=>[...root.querySelectorAll('button,[role="button"],label,.ovui-radio,.ovui-radio-button,span,div')].filter(e=>enabled(e)&&text(e)===label&&!Array.from(e.children).some(c=>text(c)===label));
 const clickExact=(root,label)=>{const all=exact(root,label);if(all.length!==1)throw Error('ambiguous_'+label);all[0].click();};
 const numeric=s=>{const t=s.replace(/[,，]/g,'').trim();return /^\d+(?:\.\d+)?$/.test(t)?Number(t):null;};
 const sameIds=(a,b)=>a.length===b.length&&[...a].sort().every((v,i)=>v===[...b].sort()[i]);
 const pageInputs=()=>[...document.querySelectorAll('input')].filter(e=>shown(e)&&!e.closest('.ovui-modal,.ovui-drawer,[role="dialog"]')&&/^\d+\s*条\s*\/\s*页$/.test(e.value));
 const pageRoot=input=>{
  let candidate=null;
  for(let e=input?.parentElement,n=0;e&&e!==document.body&&n<10;e=e.parentElement,n++)if(/pagination/i.test(String(e.className))){
    candidate=e;if([...e.querySelectorAll('[aria-current],[class*="active"],[class*="current"],[class*="selected"]')].some(x=>/^\d+$/.test(text(x))))return e;
  }
  return candidate;
 };
 const actualPage=input=>{
  const root=pageRoot(input);if(!root)return null;
  const found=[...root.querySelectorAll('[aria-current], [aria-selected="true"], [class*="active"], [class*="current"], [class*="selected"]')]
   .filter(e=>shown(e)&&/^\d+$/.test(text(e))&&(/active|current|selected/.test(String(e.className))||['true','page'].includes(e.getAttribute('aria-current'))||e.getAttribute('aria-selected')==='true'));
  const values=[...new Set(found.map(e=>Number(text(e))))];return values.length===1?values[0]:null;
 };
 const spendHeader=()=>[...document.querySelectorAll('th')].filter(e=>shown(e)&&!e.closest('.ovui-modal,.ovui-drawer')&&norm(text(e))==='整体消耗');
 const descending=()=>{const h=spendHeader();return h.length===1&&(h[0].getAttribute('aria-sort')==='descending'||!!h[0].querySelector('.ovui-th__sorter-down--active'));};
 const clickNative=e=>{
  if(!enabled(e))throw Error('readonly_control_disabled');
  e.scrollIntoView?.({block:'nearest'});
  for(const type of ['mousedown','mouseup'])e.dispatchEvent(new MouseEvent(type,{bubbles:true,cancelable:true,view:window}));
  e.click();
 };
 function identityOk(){
  try{const u=new URL(location.href),parent=[...document.querySelectorAll('.oc-promotion-key-info-bar')].filter(shown);
   const content=parent.length===1?text(parent[0]):text(document.body);
   const ids=[...content.matchAll(/计划ID[：:]\s*(\d{6,30})/g)];
   return u.origin==='https://qianchuan.jinritemai.com'&&u.pathname==='/uni-prom/detail'&&u.searchParams.getAll('aavid').length===1&&u.searchParams.get('aavid')===id.accountId&&
    u.searchParams.getAll('adId').length===1&&u.searchParams.get('adId')===id.planId&&ids.length===1&&ids[0][1]===id.planId&&
    new RegExp('ID[：:]\\s*'+id.accountId+'(?=\\s|$)').test(text(document.body));
  }catch{return false;}
 }
 function rows(){
  const tables=[...document.querySelectorAll('table')].filter(t=>shown(t)&&!t.closest('.ovui-modal,.ovui-drawer,[role="dialog"],[aria-modal="true"]'));
  const headerSets=new Map();
  for(const t of tables)for(const tr of t.querySelectorAll('thead tr')){
   const h=[...tr.cells].map(c=>norm(text(c))),named=h.filter(Boolean);
   if(['视频','素材追投','整体消耗','整体支付ROI','审核状态'].every(k=>h.includes(k))&&new Set(named).size===named.length)headerSets.set(JSON.stringify(h),h);
  }
  if(headerSets.size!==1)return [];
  const h=[...headerSets.values()][0],result=new Map(),duplicates=new Set();
  for(const t of tables)for(const row of t.querySelectorAll('tbody tr')){
   if(!shown(row)||row.cells.length!==h.length)continue;
   const cell=k=>row.cells[h.indexOf(k)];const found=[...text(cell('视频')).matchAll(/素材ID[：:]\s*(\d{6,30})/g)];if(found.length!==1)continue;
   const mid=found[0][1],boxes=[...row.querySelectorAll('input[type="checkbox"]')].filter(e=>!e.disabled);
   const name=text(cell('视频')).split('\n').find(v=>v.trim()&&!v.includes('素材ID'))||mid;
   const data={id:mid,name:name.slice(0,200),spend:numeric(text(cell('整体消耗'))),roi:numeric(text(cell('整体支付ROI'))),
    chase_status:text(cell('素材追投')).replace(/查看详情/g,'').trim(),approved:text(cell('审核状态'))==='审核通过',selectable:boxes.length===1};
   if(result.has(mid))duplicates.add(mid);else result.set(mid,{data,row,checkbox:boxes[0]});
  }
  for(const mid of duplicates)result.delete(mid);return [...result.values()];
 }
 function read(owned=false){
  if(!identityOk())return null;
  const start=[...document.querySelectorAll('input[placeholder="请选择开始日期"]')].filter(shown),end=[...document.querySelectorAll('input[placeholder="请选择结束日期"]')].filter(shown);
  if(start.length!==1||end.length!==1||!/^\d{4}-\d\d-\d\d$/.test(start[0].value)||!/^\d{4}-\d\d-\d\d$/.test(end[0].value))return null;
  const u=new URL(location.href);if(u.searchParams.get('dr')!==start[0].value+','+end[0].value)return null;
  let detail;try{detail=JSON.parse(new URLSearchParams(u.hash.slice(1)).get('uniDetail'));}catch{return null;}
  if(detail?.tb!=='creative')return null;
  const cc=typeof detail.cc==='string'?JSON.parse(detail.cc||'{}'):detail.cc||{};
  const sizes=pageInputs(),page=sizes.length===1?actualPage(sizes[0]):null;
  if(sizes.length!==1||!/^100\s*条\s*\/\s*页$/.test(sizes[0].value)||String(cc.ps)!=='100'||page===null||String(page)!==String(cc.p||'1'))return null;
  const list=rows();if(!list.length)return null;
  const totals=[...new Set([...text(document.body).matchAll(/共\s*([\d,]+)\s*条记录/g)].map(m=>Number(m[1].replace(/,/g,''))))];
  if(totals.length===1&&list.length!==Math.min(100,Math.max(0,totals[0]-(page-1)*100)))return null;
  const pending=globalThis[viewKey];
  if(pending&&pending.page===page){
   if(list.map(r=>r.data.id).join(',')===pending.ids)return null;
   delete globalThis[viewKey];
  }
  if(cc.sf!=='stat_cost'||cc.st!=='desc'||!descending()||list.some((r,i)=>i>0&&r.data.spend!==null&&list[i-1].data.spend!==null&&r.data.spend>list[i-1].data.spend))return null;
  const editors=roots(),ours=globalThis[key]?.root;
  return {account_id:id.accountId,plan_id:id.planId,path:'/uni-prom/detail',group:id.group,captured_at:new Date().toISOString(),
   date_start:start[0].value,date_end:end[0].value,page,page_size:100,sort:'spend_desc',
   materials:list.map(r=>r.data),editor_open:editors.some(e=>!owned||e!==ours)};
 }
 const fail=code=>({ok:false,submitted:false,code});
 if(phase==='configure_view'){
  if(!identityOk())return fail('view_identity_changed');
  if(roots().length||rows().some(r=>r.checkbox?.checked))return fail('view_editor_or_selection_open');
  let d;try{d=JSON.parse(new URLSearchParams(new URL(location.href).hash.slice(1)).get('uniDetail'));}catch{return fail('view_range_unavailable');}
  const cc=JSON.parse(d?.cc||'{}'),range=new URL(location.href).searchParams.get('dr')?.split(',');
  const days=range?.length===2?(Date.parse(range[1]+'T00:00:00Z')-Date.parse(range[0]+'T00:00:00Z'))/86400000:NaN;
  if(d?.tb!=='creative'||String(cc.ps)!=='100'||cc.sf!=='stat_cost'||cc.st!=='desc'||![2,3,4].includes(days))return fail('view_not_requested');
  const input=pageInputs();if(input.length!==1)return fail('view_page_size_ambiguous');
  const before={page_size:Number(input[0].value.match(/^\d+/)[0]),page:actualPage(input[0]),descending:descending(),rows:rows().length};
  if(before.page_size!==100||before.page!==Number(cc.p||1))globalThis[viewKey]={page:Number(cc.p||1),ids:rows().map(r=>r.data.id).join(',')};
  const finish=code=>({ok:code==='view_configured',code,before,after:{page_size:pageInputs().length===1?Number(pageInputs()[0].value.match(/^\d+/)[0]):null,page:pageInputs().length===1?actualPage(pageInputs()[0]):null,descending:descending(),rows:rows().length}});
  try{
   if(before.page_size!==100){
    clickNative(input[0]);let options=[];
    for(let n=0;n<25&&!options.length;n++){
     const nodes=[...document.querySelectorAll('[role="option"],li,div,span')].filter(e=>enabled(e)&&/^100(?:条\/页)?$/.test(text(e).replace(/\s/g,''))&&!Array.from(e.children).some(c=>text(c).replace(/\s/g,'')===text(e).replace(/\s/g,'')));
     options=nodes.filter(e=>{
      for(let p=e.parentElement,k=0;p&&p!==document.body&&k<6;p=p.parentElement,k++){
       if(text(p).length>500)break;
       const labels=[...p.querySelectorAll('[role="option"],li,div,span')].filter(shown).map(x=>text(x).replace(/\s/g,''));
       if(['10','20','50'].filter(v=>labels.includes(v)||labels.includes(v+'条/页')).length>=2)return true;
      }return false;
     });
     if(!options.length)await new Promise(r=>setTimeout(r,100));
    }
    if(options.length!==1)return finish('view_100_option_unavailable');clickNative(options[0]);
    for(let n=0;n<40&&!pageInputs().some(e=>/^100\s*条\s*\/\s*页$/.test(e.value));n++)await new Promise(r=>setTimeout(r,100));
    if(!pageInputs().some(e=>/^100\s*条\s*\/\s*页$/.test(e.value)))return finish('view_page_size_not_applied');
   }
   if(!descending()){
    const h=spendHeader(),arrow=h.length===1?h[0].querySelector('.ovui-th__sorter-down'):null;
    if(!arrow)return finish('view_sort_control_unavailable');clickNative(arrow);
    for(let n=0;n<30&&!descending();n++)await new Promise(r=>setTimeout(r,100));
    if(!descending())return finish('view_sort_not_applied');
   }
   const wanted=Number(cc.p||1),size=pageInputs()[0];let current=actualPage(size);
   for(let attempt=0;attempt<3&&current!==wanted;attempt++){
    const root=pageRoot(size);if(!root||current===null)return finish('view_active_page_unavailable');
    const direct=[...root.querySelectorAll('button,li,div,span')].filter(e=>enabled(e)&&text(e)===String(wanted)&&!Array.from(e.children).some(c=>text(c)===String(wanted)));
    if(direct.length!==1)return finish('view_target_page_unavailable');clickNative(direct[0]);
    for(let n=0;n<30&&actualPage(pageInputs()[0])===current;n++)await new Promise(r=>setTimeout(r,100));
    current=actualPage(pageInputs()[0]);
   }
   for(let n=0;n<30&&!read();n++)await new Promise(r=>setTimeout(r,100));
   return finish(read()?'view_configured':'view_rows_not_verified');
  }catch{return finish('view_control_failed');}
 }
 if(phase==='inspect'){const surface=read();return {ok:true,surface,code:surface?'material_table_visible':'material_range_or_table_unavailable'};}
 const intent=request.intent;
 if(!intent||intent.target_kind!=='material_batch'||intent.account_id!==id.accountId||intent.plan_id!==id.planId||
   !Array.isArray(intent.material_ids)||intent.material_ids.length<1||intent.material_ids.length>20||new Set(intent.material_ids).size!==intent.material_ids.length||
   intent.material_ids.some(v=>typeof v!=='string'||!/^\d{6,30}$/.test(v))||!request.id||!request.intent_digest)return fail('invalid_batch_command');
 if(phase==='verify'){
  if(!identityOk()||roots().length)return {ok:true,observation:null};
  const tables=[...document.querySelectorAll('table')].filter(shown),headers=new Map();
  for(const table of tables)for(const tr of table.querySelectorAll('thead tr')){
   const h=[...tr.cells].map(c=>norm(text(c)));
   if(['任务名称','调控预算','追投方式'].every(k=>h.includes(k)))headers.set(JSON.stringify(h),h);
  }
  if(headers.size!==1)return {ok:true,observation:null};
  const h=[...headers.values()][0],found=[];
  for(const table of tables)for(const row of table.querySelectorAll('tbody tr')){
   if(row.cells.length!==h.length||!shown(row))continue;
   const cell=k=>text(row.cells[h.indexOf(k)]),nameLines=cell('任务名称').split('\n').map(s=>s.trim());
   if(!nameLines.includes(intent.task_name))continue;
   const taskIds=[...cell('任务名称').matchAll(/(?:^|\n)\s*ID[：:]\s*(\d{6,30})/g)];
   const mids=[...cell('素材').matchAll(/素材ID[：:]\s*(\d{6,30})/g)].map(m=>m[1]);
   if(taskIds.length!==1||!sameIds(mids,intent.material_ids)||cell('追投方式')!==intent.after.chase_type)continue;
   const amount=numeric(cell('调控预算').replace(/元$/,''));if(amount===null)continue;
   const after={chase_type:cell('追投方式')};
   if(after.chase_type==='放量追投'){
    after.total_budget=amount;after.control_duration=numeric(cell('调控时长小时').replace(/小时$/,''));
   }else{after.daily_budget=amount;after.bid_value=numeric(cell('出价'));after.optimization_target=cell('优化目标');}
   if(Object.keys(intent.after).some(k=>String(after[k])!==String(intent.after[k])))continue;
   found.push({account_id:id.accountId,plan_id:id.planId,task_id:taskIds[0][1],task_name:intent.task_name,material_ids:mids,after,captured_at:new Date().toISOString()});
  }
  return {ok:true,observation:found.length===1?found[0]:null};
 }
 const field=(root,pattern)=>{
  const candidates=[];
  for(const input of root.querySelectorAll('input:not([type="checkbox"]):not([type="radio"]),textarea')){
   if(!enabled(input)||input.readOnly)continue;
   let host=input.parentElement;
   for(let n=0;n<7&&host&&root.contains(host);n++,host=host.parentElement){
    if(text(host).length>400)break;
    if(pattern.test(text(host))&&[...host.querySelectorAll('input:not([type="checkbox"]):not([type="radio"]),textarea')].filter(enabled).length===1){candidates.push(input);break;}
   }
  }
  const unique=[...new Set(candidates)];return unique.length===1?unique[0]:null;
 };
 const setValue=(input,value)=>{
  if(!input)throw Error('missing_native_field');
  Object.getOwnPropertyDescriptor(input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(input,String(value));
  input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.dispatchEvent(new Event('blur',{bubbles:true}));
 };
 const drawerIds=root=>[...text(root).matchAll(/素材ID[：:]\s*(\d{6,30})/g)].map(m=>m[1]);
 const selected=()=>rows().filter(r=>r.checkbox?.checked).map(r=>r.data.id);
 function batchEntry(){
  const marked=[...document.querySelectorAll('[data-auto-id="bar-groups-group-item-btn-videoChase"]')].filter(e=>enabled(e)&&text(e)==='追投');
  if(marked.length===1)return marked[0];if(marked.length>1)return null;
  const count=intent.material_ids.length,anchors=[...document.querySelectorAll('span,div')].filter(e=>shown(e)&&new RegExp('^已选'+count+'个(?:视频|素材)$').test(text(e).replace(/\s/g,'')));
  const found=new Set();
  for(const anchor of anchors)for(let root=anchor.parentElement,n=0;root&&root!==document.body&&n<6;root=root.parentElement,n++){
   if(text(root).length>500||root.querySelector('table'))break;
   if(!text(root).includes('删除')||!text(root).includes('排除'))continue;
   const entries=exact(root,'追投');if(entries.length===1)found.add(entries[0]);
  }
  return found.size===1?[...found][0]:null;
 }
 async function abort(){
  const p=globalThis[key];delete globalThis[key];if(!p||p.id!==request.id)return;
  if(p.root?.isConnected){const cancels=exact(p.root,'取消');if(cancels.length===1)cancels[0].click();}
  for(const mid of p.selected||[]){const r=rows().find(r=>r.data.id===mid);if(r?.checkbox?.checked)r.checkbox.click();}
 }
 if(phase==='abort'){await abort();return {ok:true,submitted:false};}
 if(phase==='prepare'){
  if(globalThis[key]||roots().length||selected().length)return fail('existing_editor_or_selection');
  const before=read();if(!before||before.editor_open||before.date_start!==intent.date_start||before.date_end!==intent.date_end||before.page!==intent.surface.page)return fail('range_or_page_changed');
  const all=rows(),targets=intent.material_ids.map(mid=>all.find(r=>r.data.id===mid));
  if(targets.some(r=>!r?.checkbox||!enabled(r.checkbox)))return fail('batch_ids_not_on_current_page');
  globalThis[key]={id:request.id,digest:request.intent_digest,selected:[],at:Date.now(),nonce:crypto.randomUUID()};
  try{
   for(const r of targets){r.checkbox.click();globalThis[key].selected.push(r.data.id);}
   if(!sameIds(selected(),intent.material_ids))throw Error('exclusive_selection_failed');
   let entry=null;
   for(let n=0;n<40&&!entry;n++){
    if(!identityOk()||!sameIds(selected(),intent.material_ids))throw Error('exclusive_selection_changed');
    entry=batchEntry();if(!entry)await new Promise(r=>setTimeout(r,100));
   }
   if(!entry)throw Error('batch_entry_unavailable');entry.click();
   let root=null;
   for(let n=0;n<40&&!root;n++){
    const rs=roots().filter(e=>text(e).includes('追投方式')&&text(e).includes('任务名称'));
    if(rs.length===1)root=rs[0];else await new Promise(r=>setTimeout(r,100));
   }
   if(!root)throw Error('native_creation_form_unavailable');globalThis[key].root=root;
   if(!sameIds(drawerIds(root),intent.material_ids))throw Error('native_batch_ids_mismatch');
   clickExact(root,intent.after.chase_type);await new Promise(r=>setTimeout(r,150));
   const inputs=[];
   function fill(pattern,value){const input=field(root,pattern);if(!input)throw Error('native_field_missing:'+pattern.source);setValue(input,value);inputs.push({input,value:String(value)});}
   if(intent.after.chase_type==='放量追投'){
    fill(/调控总预算/,intent.after.total_budget);fill(/调控时长/,intent.after.control_duration);
   }else{
    const option=intent.after.optimization_target;
    const currentTarget=field(root,/优化目标/);
    if(currentTarget){
     if(currentTarget.value!==option){currentTarget.click();await new Promise(r=>setTimeout(r,100));clickExact(document.body,option);await new Promise(r=>setTimeout(r,100));}
     if(currentTarget.value!==option)throw Error('native_optimization_target_unavailable');
    }else if(!text(root).includes(option))throw Error('native_optimization_target_unavailable');
    fill(/调控日预算|日预算/,intent.after.daily_budget);
    fill(/目标转化成本|转化出价|出价|ROI目标/,intent.after.bid_value);
   }
   fill(/任务名称/,intent.task_name);await new Promise(r=>setTimeout(r,150));
   const confirms=[...root.querySelectorAll('button')].filter(e=>enabled(e)&&['确定','确认','创建','提交','开始追投','立即追投'].includes(text(e)));
   if(confirms.length!==1||inputs.some(v=>v.input.value!==v.value||!v.input.checkValidity())||!sameIds(drawerIds(root),intent.material_ids))throw Error('native_creation_form_validation_failed');
   Object.assign(globalThis[key],{inputs,button:confirms[0]});
   const fresh=read(true);if(!fresh||fresh.editor_open)throw Error('native_identity_changed');
   return {ok:true,prepared:true,submit_token:globalThis[key].nonce,surface:fresh};
  }catch(e){
   const root=globalThis[key]?.root;
   const schema=root?{buttons:[...root.querySelectorAll('button')].filter(shown).map(text).slice(0,8),fields:[...root.querySelectorAll('input')].filter(shown).map(i=>({placeholder:i.getAttribute('placeholder')||'',label:text(i.parentElement).slice(0,60)})).slice(0,6)}:null;
   await abort();return fail(String(e.message)+(schema?' | '+JSON.stringify(schema).slice(0,350):''));
  }
 }
 if(phase!=='submit')return fail('phase_invalid');
 const p=globalThis[key];
 const valid=()=>p&&p.id===request.id&&p.digest===request.intent_digest&&p.nonce===request.submit_token&&!p.submitted&&Date.now()-p.at<60000&&Date.now()<Date.parse(request.expires_at)&&identityOk()&&enabled(p.button)&&
  sameIds(drawerIds(p.root),intent.material_ids)&&sameIds(selected(),intent.material_ids)&&p.inputs.every(v=>enabled(v.input)&&v.input.value===v.value&&v.input.checkValidity());
 if(!valid()){await abort();return fail('native_batch_changed');}
 let allow;try{allow=await chrome.runtime.sendMessage({action:'batch-final-check',id:request.id,nonce:p.nonce});}catch{}
 if(allow?.allow!==true||!valid()){await abort();return fail('batch_final_permission_rejected');}
 p.submitted=true;
 try{p.button.click();return {ok:true,submitted:true,code:'submitted_pending_task_readback'};}
 catch{return {ok:false,submitted:true,code:'submit_outcome_unknown'};}
 finally{delete globalThis[key];}
}
if(typeof module==='object')module.exports={batchMaterialDom};
