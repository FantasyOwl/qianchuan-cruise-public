/* Native one-click slowdown only. It never stops a running boost task. */
async function liveCloseDom(phase,request){
 const id=request.identity,key='__qc_live_close_v1',txt=e=>(e?.innerText||e?.textContent||'').trim();
 const shown=e=>!!e&&e.isConnected&&e.getClientRects().length>0&&getComputedStyle(e).display!=='none'&&getComputedStyle(e).visibility!=='hidden';
 const enabled=e=>{if(!shown(e))return false;for(let n=0;e&&n<4;n++,e=e.parentElement)if(e.disabled||e.getAttribute('aria-disabled')==='true'||/(?:^|[ _-])disabled(?:$|[ _-])/.test(String(e.className)))return false;return true;};
 const fail=code=>({ok:false,submitted:false,code});
 const money=v=>{const s=String(v??'').replace(/,/g,'').trim();return /^\d+(?:\.\d{1,2})?$/.test(s)&&Number(s)>0?Number(s).toFixed(2):null;};
 const roots=()=>[...document.querySelectorAll('[role="dialog"],[role="alertdialog"],.ovui-modal,.ovui-drawer,[aria-modal="true"],[class*="modal-content"]')].filter(shown).filter((e,i,a)=>!a.some(x=>x!==e&&x.contains(e)));
 const exact=(root,label)=>[...root.querySelectorAll('button,[role="button"],label,span,div')].filter(e=>enabled(e)&&txt(e)===label&&![...e.children].some(c=>txt(c)===label));
 let u;try{u=new URL(location.href);}catch{return fail('page_identity_invalid');}
 if(phase==='inspect_parent'){
  if(!id||id.scene!=='live'||id.role!=='detail'||u.origin!=='https://qianchuan.jinritemai.com'||u.pathname!=='/uni-prom/detail'||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==id.accountId||u.searchParams.getAll('adId').length!==1||u.searchParams.get('adId')!==id.planId||!new RegExp('ID[：:]\\s*'+id.accountId+'(?!\\d)').test(txt(document.body)))return fail('parent_identity_missing');
  if(roots().length)return fail('preserve_existing_editor');
  const headers=[...document.querySelectorAll('.oc-promotion-key-info-bar')].filter(shown);if(headers.length!==1)return fail('parent_header_missing');
  const plans=[...new Set([...txt(headers[0]).matchAll(/计划ID[：:]\s*(\d{6,30})(?=\s|$)/g)].map(m=>m[1]))];
  const owners=[...new Set([...([headers[0],...headers[0].querySelectorAll('*')].filter(shown).map(e=>txt(e).match(/^抖音号ID[：:]\s*(\d{6,30})$/)?.[1]).filter(Boolean)),...[...txt(headers[0]).matchAll(/抖音号ID[：:]\s*(\d{6,30})(?=\s|$)/g)].map(m=>m[1])])];
  if(plans.length!==1||plans[0]!==id.planId||owners.length!==1)return fail('parent_owner_ambiguous');
  return {ok:true,parent:{account_id:id.accountId,plan_id:id.planId,aweme_id:owners[0],group:id.group,detail_url:request.canonical_detail_url||u.href,captured_at:new Date().toISOString()}};
 }
 if(!id||id.scene!=='live'||u.origin!=='https://qianchuan.jinritemai.com'||u.pathname!=='/uni-prom'||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==id.accountId||
  !new RegExp('ID[：:]\\s*'+id.accountId+'(?!\\d)').test(txt(document.body)))return fail('home_identity_missing');
 const parents=(Array.isArray(request.parents)?request.parents:[]).filter(p=>{
  try{const link=new URL(p.detail_url),age=Date.now()-Date.parse(p.captured_at);return p.account_id===id.accountId&&p.group===id.group&&/^\d{6,30}$/.test(p.plan_id||'')&&/^\d{6,30}$/.test(p.aweme_id||'')&&age>=0&&age<=600000&&link.origin===u.origin&&link.pathname==='/uni-prom/detail'&&link.searchParams.getAll('aavid').length===1&&link.searchParams.get('aavid')===id.accountId&&link.searchParams.getAll('adId').length===1&&link.searchParams.get('adId')===p.plan_id;}catch{return false;}
 });
 const ownerFor=row=>parents.filter(p=>[row,...row.querySelectorAll('[id],[data-aweme-id]')].some(e=>e.id===p.aweme_id||e.getAttribute('data-aweme-id')===p.aweme_id));
 function planId(row){
  const explicit=new Set();
  for(const el of [row,...row.querySelectorAll('[id],[data-plan-id],[data-ad-id],[data-row-key],a[href]')]){
   const nodeId=el.id?.match(/^ad-name-(\d{6,30})$/);if(nodeId)explicit.add(nodeId[1]);
   for(const attr of ['data-plan-id','data-ad-id']){const v=el.getAttribute(attr);if(/^\d{6,30}$/.test(v||''))explicit.add(v);}
   if(el.matches('a[href]'))try{const link=new URL(el.getAttribute('href'),location.href),aid=link.searchParams.get('aavid'),pid=link.searchParams.get('adId');
    if(link.origin===u.origin&&link.pathname==='/uni-prom/detail'&&(!aid||aid===id.accountId)&&/^\d{6,30}$/.test(pid||''))explicit.add(pid);
   }catch{}
  }
  for(const m of txt(row).matchAll(/(?:^|\n)\s*计划ID[：:]\s*(\d{6,30})(?=\s|$)/g))explicit.add(m[1]);
  const owners=ownerFor(row);if(owners.length>1||explicit.size>1)return null;
  if(owners.length===1){if(explicit.size&&![...explicit].includes(owners[0].plan_id))return null;return owners[0].plan_id;}
  return explicit.size===1?[...explicit][0]:null;
 }
 const rowSelector='tr,[role="row"],.ovui-table__row';
 const entries=()=>[...document.querySelectorAll('button,[role="button"],a,span,div')].filter(e=>shown(e)&&txt(e)==='一键调速'&&![...e.children].some(c=>txt(c)==='一键调速'));
 function rows(){
  const found=new Map(),dupes=new Set();
  const available=entries(),candidates=new Set(available.map(e=>e.closest(rowSelector)).filter(Boolean));
  for(const anchor of document.querySelectorAll('[id^="ad-name-"],[data-plan-id],[data-ad-id],[id],[data-aweme-id]')){if(!anchor.matches('[id^="ad-name-"],[data-plan-id],[data-ad-id]')&&!parents.some(p=>anchor.id===p.aweme_id||anchor.getAttribute('data-aweme-id')===p.aweme_id))continue;const row=anchor.closest(rowSelector);if(row)candidates.add(row);}
  for(const row of candidates){
   if(!shown(row)||row.closest('[role="dialog"],.ovui-modal'))continue;
   const pid=planId(row);if(!pid)continue;
   const buttons=available.filter(e=>e.closest(rowSelector)===row),entry=buttons.length===1?buttons[0]:null;
   if(found.has(pid))dupes.add(pid);else found.set(pid,{row,entry});
  }
  for(const pid of dupes)found.delete(pid);return found;
 }
 function read(pid,item){
  const t=txt(item.row),bs=[...t.matchAll(/每日预算\s*([\d,.]+)\s*元/g)];if(bs.length!==1||!money(bs[0][1]))return null;
  const owners=ownerFor(item.row),owner=owners.length===1&&owners[0].plan_id===pid?owners[0]:null;
  const busy=!enabled(item.entry)||/(?:一键起量|一键控量|一键调速)[\s\S]{0,16}(?:进行中|调控中|起量中|控量中)|(?:结束|停止)(?:起量|控量|调控)/.test(t);
  const mode=!busy?'none':/一键控量[\s\S]{0,16}(?:进行中|调控中|控量中)/.test(t)?'control':/一键起量[\s\S]{0,16}(?:进行中|调控中|起量中)/.test(t)?'boost':'unknown';
  const cb=[...t.matchAll(/(?:控量预算|调控预算)\s*[（(]?元?[)）]?\s*[：:]?\s*([\d,.]+)/g)];
  const cm=[...t.matchAll(/控量时长\s*[：:]?\s*(\d+(?:\.\d+)?)\s*小时/g)];
  const created=t.match(/创建时间\s*[：:]?\s*(\d{4}-\d\d-\d\d)[\sT]+(\d\d:\d\d:\d\d)/);
  return {account_id:id.accountId,plan_id:pid,group:id.group,path:'/uni-prom',home_url:u.href,captured_at:new Date().toISOString(),parent_budget:money(bs[0][1]),running:/(?:^|\s)投放中(?:\s|$)/.test(t),busy,control_mode:mode,
   control_budget:cb.length===1?money(cb[0][1]):null,control_minutes:cm.length===1&&Number.isInteger(Number(cm[0][1])*60)?Number(cm[0][1])*60:null,control_created_at:created?created[1]+'T'+created[2]+'+08:00':null,
   aweme_id:owner?.aweme_id||null,detail_url:owner?.detail_url||null,parent_captured_at:owner?.captured_at||null};
 }
 if(phase==='inspect'){
  const open=roots().length>0,buttons=entries(),identified=rows(),surfaces=open?[]:[...identified].map(([pid,item])=>read(pid,item)).filter(Boolean);
  // Only bounded rendered DOM evidence. No application state, cookies, network
  // requests, credential fields or arbitrary script supplied by the server.
  const samples=[...new Set(buttons.map(e=>e.closest(rowSelector)).filter(Boolean))].slice(0,3).map(row=>({
   text:txt(row).slice(0,900),tag:row.tagName,classes:String(row.className).slice(0,160),
   ids:[row,...row.querySelectorAll('[id],[data-plan-id],[data-ad-id],[data-row-key]')].slice(0,12).map(e=>({id:e.id||'',plan:e.getAttribute('data-plan-id')||'',ad:e.getAttribute('data-ad-id')||'',row:e.getAttribute('data-row-key')||''})),
   links:[...row.querySelectorAll('a[href]')].slice(0,8).map(e=>{try{const l=new URL(e.getAttribute('href'),location.href);return {path:l.origin===u.origin?l.pathname:'other_origin',ad:l.searchParams.get('adId')||'',aweme:l.searchParams.get('awemeId')||''};}catch{return {path:'invalid',ad:'',aweme:''};}})
  }));
  return {ok:true,code:open?'editor_open':surfaces.length?'home_read':buttons.length?'home_plan_identity_missing':'home_speed_entry_missing',surfaces,
   diagnostics:{entry_count:buttons.length,row_count:document.querySelectorAll(rowSelector).length,plan_count:identified.size,samples:open?[]:samples}};
 }
 const v=request.intent;
 if(!v||v.target_kind!=='live_close'||v.action!=='one_click_control'||v.account_id!==id.accountId||v.group!==id.group||!/^\d{6,30}$/.test(v.plan_id||'')||![30,60].includes(v.duration_minutes)||!money(v.budget)||!request.id||!request.intent_digest)return fail('invalid_close_command');
 async function abort(){const p=globalThis[key];if(!p||p.id!==request.id)return;delete globalThis[key];if(p.root?.isConnected){const cancel=exact(p.root,'取消');if(cancel.length===1)cancel[0].click();}}
 if(phase==='abort'){await abort();return {ok:true,submitted:false};}
 const selected=e=>{for(let n=0;e&&n<3;n++,e=e.parentElement)if(e.getAttribute('aria-checked')==='true'||e.getAttribute('aria-selected')==='true'||/(?:^|[-_ ])(?:checked|selected|active)(?:$|[-_ ])/.test(String(e.className))||e.matches('label')&&e.querySelector('input[type="radio"]:checked'))return true;return false;};
 const field=(root,label)=>{
  const matches=[];
  for(const input of root.querySelectorAll('input')){
   if(!shown(input)||input.disabled||['checkbox','radio','hidden'].includes(input.type))continue;
   let host=input.parentElement;
   for(let n=0;host&&root.contains(host)&&n<7;n++,host=host.parentElement){
    if(txt(host).length>650)break;
    if(label.test(txt(host))&&[...host.querySelectorAll('input')].filter(e=>shown(e)&&!['checkbox','radio','hidden'].includes(e.type)).length===1){matches.push(input);break;}
   }
  }return matches.length===1?matches[0]:null;
 };
 const set=(input,value)=>{
  if(!input)throw Error('native_field_missing');
  if(Number(input.value)===Number(value))return;
  if(input.readOnly)throw Error('native_field_readonly');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,String(value));
  input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.dispatchEvent(new Event('blur',{bubbles:true}));
 };
 if(phase==='prepare'){
  if(roots().length||globalThis[key])return fail('preserve_existing_editor');
  const item=rows().get(v.plan_id),s=item&&read(v.plan_id,item);
  if(!s||!s.running||s.busy||Number(v.budget)>Number(s.parent_budget)||v.aweme_id&&s.aweme_id!==v.aweme_id)return fail('plan_busy_or_not_running');
  if(Date.now()<Date.parse(v.trigger_at)||Date.now()>=Date.parse(request.expires_at))return fail('schedule_outside_window');
  globalThis[key]={id:request.id,digest:request.intent_digest,at:Date.now(),nonce:crypto.randomUUID()};
  try{
   item.entry.click();let root=null;
   for(let n=0;n<40&&!root;n++){
    const rs=roots().filter(r=>txt(r).includes('调控方式')&&txt(r).includes('调控预算'));
    if(rs.length===1)root=rs[0];
    else if(!roots().length){const option=exact(document.body,'一键控量');if(option.length===1)option[0].click();}
    if(!root)await new Promise(r=>setTimeout(r,100));
   }
   if(!root)throw Error('native_speed_form_missing');globalThis[key].root=root;
   const control=exact(root,'一键控量');if(control.length!==1)throw Error('native_control_mode_missing');
   control[0].click();await new Promise(r=>setTimeout(r,150));
   if(!selected(control[0]))throw Error('native_control_mode_unverified');
   const budget=field(root,/调控预算/),duration=field(root,/控量时长/);
   set(budget,v.budget);
   if(duration?.readOnly&&Number(duration.value)*60!==v.duration_minutes){
    let host=duration.parentElement,step=null;
    for(let n=0;host&&root.contains(host)&&n<7;n++,host=host.parentElement){
     if(txt(host).length>650)break;
     const buttons=[...host.querySelectorAll('button,[role="button"]')].filter(e=>enabled(e)&&['+','＋'].includes(txt(e)));
     if(txt(host).includes('控量时长')&&buttons.length===1){step=buttons[0];break;}
    }
    if(!step||Number(duration.value)!==0.5||v.duration_minutes!==60)throw Error('native_duration_step_unverified');
    step.click();await new Promise(r=>setTimeout(r,150));
   }else set(duration,v.duration_minutes/60);
   await new Promise(r=>setTimeout(r,150));
   const submits=exact(root,'提交');if(submits.length!==1||!budget.checkValidity()||!duration.checkValidity()||money(budget.value)!==v.budget||Number(duration.value)*60!==v.duration_minutes)throw Error('native_close_fields_unverified');
   const fresh=rows().get(v.plan_id),after=fresh&&read(v.plan_id,fresh);
   if(!after||after.busy||!after.running||Number(v.budget)>Number(after.parent_budget))throw Error('native_plan_changed');
   Object.assign(globalThis[key],{budget,duration,control:control[0],button:submits[0]});
   return {ok:true,prepared:true,submit_token:globalThis[key].nonce,surface:after};
  }catch(e){await abort();return fail(e.message);}
 }
 if(phase!=='submit')return fail('unsupported_close_phase');
 const p=globalThis[key];
 const valid=()=>{
  const item=rows().get(v.plan_id),s=item&&read(v.plan_id,item),url=new URL(location.href);
  return p&&p.id===request.id&&p.digest===request.intent_digest&&p.nonce===request.submit_token&&!p.submitted&&Date.now()-p.at<60000&&Date.now()>=Date.parse(v.trigger_at)&&Date.now()<Date.parse(request.expires_at)&&enabled(p.button)&&selected(p.control)&&money(p.budget.value)===v.budget&&Number(p.duration.value)*60===v.duration_minutes&&
   p.root.contains(p.button)&&p.root.contains(p.budget)&&p.root.contains(p.duration)&&p.budget.checkValidity()&&p.duration.checkValidity()&&s?.running&&!s.busy&&(!v.aweme_id||s.aweme_id===v.aweme_id&&Date.now()-Date.parse(s.parent_captured_at)<=120000)&&Number(v.budget)<=Number(s.parent_budget)&&url.origin===u.origin&&url.searchParams.getAll('aavid').length===1&&url.searchParams.get('aavid')===id.accountId&&url.pathname==='/uni-prom'&&new RegExp('ID[：:]\\s*'+id.accountId+'(?!\\d)').test(txt(document.body));
 };
 if(!valid()){await abort();return fail('native_close_form_changed');}
 let permit;try{permit=await chrome.runtime.sendMessage({action:'live-close-final-check',id:request.id,nonce:p.nonce});}catch{}
 if(permit?.allow!==true||!valid()){await abort();return fail('close_final_check_rejected');}
 p.submitted=true;p.button.click();delete globalThis[key];return {ok:true,submitted:true};
}
if(typeof module==='object')module.exports={liveCloseDom};
