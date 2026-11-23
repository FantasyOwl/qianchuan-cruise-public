/* Preconfirmed one-time schedule transport. Shares the existing executor lock. */
let liveClosePermit=null;
const LIVE_CLOSE_RECEIPT='qc_live_close_receipt_v1';
async function liveClosePost(name,body,token){
 if(!['worker-observe','worker-parents','worker-claim','worker-begin','worker-receipt'].includes(name)||!validObserverToken(token))throw Error('close_protocol');
 const r=await fetch('http://127.0.0.1:8877/api/cruise/close/'+name,{method:'POST',headers:{'Content-Type':'application/json','X-Monitor-Token':token},body:JSON.stringify(body),signal:AbortSignal.timeout(25000),credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer'});
 if(r.status===404)return {unsupported:true};if(!r.ok)throw Error('close_local_rejected');return r.json();
}
async function liveCloseInject(tabId,phase,request,documentId){
 const list=await chrome.scripting.executeScript({target:documentId?{tabId,documentIds:[documentId]}:{tabId,frameIds:[0]},world:'ISOLATED',func:liveCloseDom,args:[phase,request]});
 if(list.length!==1||list[0].frameId!==0||!list[0].documentId||documentId&&list[0].documentId!==documentId)throw Error('close_document_changed');return list[0];
}
async function liveCloseScope(tabId,v,policy,homeOnly=false){
 const tab=await chrome.tabs.get(tabId),id=identity(tab,policy);
 if(!id||id.scene!=='live'||id.accountId!==v.account_id||id.group!==v.group||!['live','detail'].includes(id.role)||id.role==='detail'&&id.planId!==v.plan_id||homeOnly&&id.role!=='live')return null;
 const group=await chrome.tabGroups.get(tab.groupId);return group.title===v.group?{tab,identity:id}:null;
}
async function liveCloseObserve(config,session,policy){
 if(!session||!await observerScopeCheckpoint(config,policy))return null;
 const mapping=await liveClosePost('worker-parents',{revision:policy.revision},config.token);
 if(mapping.unsupported)return null;const parents=Array.isArray(mapping.parents)?mapping.parents:[];
 const surfaces=[],keys=new Set(),duplicates=new Set(),checks=[];
 for(const tab of await chrome.tabs.query({url:'https://qianchuan.jinritemai.com/*'})){
  const id=identity(tab,policy);if(id?.role!=='live'||id.scene!=='live')continue;
  if((await chrome.tabGroups.get(tab.groupId)).title!==id.group)continue;
  try{
   const r=await liveCloseInject(tab.id,'inspect',{identity:id,parents});
   if(!await observerScopeCheckpoint(config,policy))return null;
   const fresh=await chrome.tabs.get(tab.id);if(fresh.url!==tab.url||(await chrome.tabGroups.get(fresh.groupId)).title!==id.group)continue;
   checks.push({account_id:id.accountId,tab_id:tab.id,code:r.result?.code||'home_read_failed',detail:r.result?.diagnostics||{}});
   for(const s of r.result?.surfaces||[]){const k=s.account_id+':'+s.plan_id;if(keys.has(k))duplicates.add(k);keys.add(k);surfaces.push({...s,tab_id:tab.id,document_id:r.documentId});}
  }catch{checks.push({account_id:id.accountId,tab_id:tab.id,code:'home_read_failed',detail:{}});}
 }
 for(let n=checks.length-1;JSON.stringify(checks).length>90000&&n>=0;n--)checks[n].detail={};
 const armed=await browserControlCheckpoint(config,session,policy);
 const result=await liveClosePost('worker-observe',{protocol:1,worker_id:session.worker_id,epoch:session.epoch,revision:policy.revision,armed,surfaces:surfaces.filter(s=>!duplicates.has(s.account_id+':'+s.plan_id)),view_checks:checks},config.token);
 if(result.unsupported)return null;
 for(const alarm of result.alarms||[])if(/^[0-9a-f]{32}$/.test(alarm.id)&&Date.parse(alarm.at)>Date.now())await chrome.alarms.create('live-close-'+alarm.id,{when:Date.parse(alarm.at)});
 return result;
}
async function liveCloseFinalCheck(message,sender){
 const p=liveClosePermit;
 if(!p||p.consumed||p.checking||p.id!==message.id||p.nonce!==message.nonce||sender.id!==chrome.runtime.id||sender.frameId!==0||sender.tab?.id!==p.surface.tab_id||sender.documentId!==p.surface.document_id||Date.now()>=Date.parse(p.expires_at))return {allow:false};
 p.checking=true;
 try{
  if(!await browserControlCheckpoint(p.config,p.session,p.policy)||!await liveCloseScope(p.surface.tab_id,p.intent,p.policy,true))return {allow:false};
  if(liveClosePermit!==p||p.consumed)return {allow:false};p.consumed=true;return {allow:true};
 }finally{p.checking=false;}
}
async function liveCloseReceipt(value,config){
 await chrome.storage.session.set({[LIVE_CLOSE_RECEIPT]:value});
 if(!await observerCheckpoint(config))throw Error('close_observer_stopped');
 const result=await liveClosePost('worker-receipt',value,config.token);
 if(result.unsupported)throw Error('close_receipt_unavailable');
 await chrome.storage.session.remove(LIVE_CLOSE_RECEIPT);return result;
}
async function liveCloseFlush(config){
 const value=(await chrome.storage.session.get(LIVE_CLOSE_RECEIPT))[LIVE_CLOSE_RECEIPT];if(!value)return false;
 await liveCloseReceipt(value,config);return true;
}
async function runLiveClose(config,session,policy){
 if(!await browserControlCheckpoint(config,session,policy))return false;
 if(await liveCloseObserve(config,session,policy)===null)return false;
 const claimed=await liveClosePost('worker-claim',{worker_id:session.worker_id,epoch:session.epoch},config.token);
 if(claimed.blocked)return true;
 const command=claimed.command;if(!command)return false;
 const v=command.intent;let surface=null,prepared=false,began=false,args=null,receiptAttempted=false;
 try{
  if(v.target_kind!=='live_close'||v.action!=='one_click_control')throw Error('close_command_invalid');
  const candidates=[];
  for(const t of await chrome.tabs.query({url:'https://qianchuan.jinritemai.com/*'})){const s=await liveCloseScope(t.id,v,policy);if(s)candidates.push(s);}
  const homes=candidates.filter(c=>c.identity.role==='live'),selected=homes.length?homes:candidates;
  if(selected.length!==1)throw Error('close_page_not_unique');let current=selected[0];
  const guards=await chrome.scripting.executeScript({target:{tabId:current.tab.id,frameIds:[0]},world:'ISOLATED',func:()=>{
   const visible=e=>e.isConnected&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';
   return ![...document.querySelectorAll('[role="dialog"],[role="alertdialog"],.ovui-modal,.ovui-drawer,[class*="modal-content"],input[type="checkbox"]:checked,[role="checkbox"][aria-checked="true"]')].some(visible);
  }});
  if(guards.length!==1||guards[0].frameId!==0||guards[0].result!==true)throw Error('已有编辑窗口或手动选择，保留页面，未提交控量');
  let parent=null;
  if(v.aweme_id){
   const detail=new URL(v.detail_url);
   if(detail.origin!=='https://qianchuan.jinritemai.com'||detail.pathname!=='/uni-prom/detail'||detail.searchParams.getAll('aavid').length!==1||detail.searchParams.get('aavid')!==v.account_id||detail.searchParams.getAll('adId').length!==1||detail.searchParams.get('adId')!==v.plan_id)throw Error('close_parent_route_invalid');
   const today=new Date(Date.now()+8*3600000).toISOString().slice(0,10);detail.searchParams.set('dr',today+','+today);
   if(!await browserControlCheckpoint(config,session,policy))throw Error('close_switch_changed');
   if(current.tab.url!==detail.href)await chrome.tabs.update(current.tab.id,{url:detail.href});
   for(let n=0;n<40&&!parent;n++){
    if(!await browserControlCheckpoint(config,session,policy))throw Error('close_switch_changed');
    const verified=await liveCloseScope(current.tab.id,v,policy);if(!verified)throw Error('close_parent_scope_changed');current=verified;
    if(verified.identity.role==='detail'&&verified.tab.status==='complete'){
     const check=await liveCloseInject(current.tab.id,'inspect_parent',{identity:verified.identity,canonical_detail_url:v.detail_url});
     if(check.result?.parent?.aweme_id===v.aweme_id&&check.result.parent.plan_id===v.plan_id)parent=check.result.parent;
     else if(check.result?.code==='preserve_existing_editor')throw Error('保留已有编辑窗口，未提交控量');
    }
    if(!parent)await new Promise(r=>setTimeout(r,250));
   }
   if(!parent)throw Error('当前计划与抖音号对应关系未核验，未提交控量');
  }
  const target=new URL(v.home_url);
  if(target.origin!=='https://qianchuan.jinritemai.com'||target.pathname!=='/uni-prom'||target.searchParams.getAll('aavid').length!==1||target.searchParams.get('aavid')!==v.account_id)throw Error('close_home_invalid');
  const today=new Date(Date.now()+8*3600000).toISOString().slice(0,10);target.searchParams.set('dr',today+','+today);
  if(!await browserControlCheckpoint(config,session,policy))throw Error('close_switch_changed');
  if(current.tab.url!==target.href)await chrome.tabs.update(current.tab.id,{url:target.href});
  for(let n=0;n<40;n++){
   if(!await browserControlCheckpoint(config,session,policy))throw Error('close_switch_changed');
   const s=await liveCloseScope(current.tab.id,v,policy,true);if(!s)throw Error('close_scope_changed');current=s;
   if(s.tab.status==='complete'){
    const ready=await liveCloseInject(current.tab.id,'inspect',{identity:s.identity,parents:parent?[parent]:[]});
    if(ready.result?.code==='editor_open')throw Error('保留已有编辑窗口，未提交控量');
    if(ready.result?.surfaces?.some(row=>row.plan_id===v.plan_id&&(!v.aweme_id||row.aweme_id===v.aweme_id)))break;
   }
   await new Promise(r=>setTimeout(r,250));
  }
  args={identity:current.identity,id:command.id,intent:v,intent_digest:command.intent_digest,expires_at:command.expires_at,parents:parent?[parent]:[]};
  const r=await liveCloseInject(current.tab.id,'prepare',args);
  if(!r.result?.prepared)throw Error(r.result?.code||'close_prepare_failed');prepared=true;
  surface={...r.result.surface,tab_id:current.tab.id,document_id:r.documentId};
  if(!await browserControlCheckpoint(config,session,policy)||!await liveCloseScope(surface.tab_id,v,policy,true))throw Error('close_switch_changed');
  const permission=await liveClosePost('worker-begin',{id:command.id,lease:command.lease,worker_id:session.worker_id,epoch:session.epoch,observation:surface},config.token);
  if(permission.submit_once!==true)throw Error('close_begin_failed');began=true;
  liveClosePermit={id:command.id,nonce:r.result.submit_token,surface,intent:v,expires_at:command.expires_at,config,session,policy,checking:false,consumed:false};
  const submitted=await liveCloseInject(surface.tab_id,'submit',{...args,submit_token:r.result.submit_token},surface.document_id);liveClosePermit=null;
  const didSubmit=submitted.result?.submitted===true;let observation=null;
  if(didSubmit&&await observerScopeCheckpoint(config,policy)&&await liveCloseScope(surface.tab_id,v,policy,true)){
   await chrome.tabs.reload(surface.tab_id,{bypassCache:true});
   for(let n=0;n<30&&!observation;n++){
    if(!await observerScopeCheckpoint(config,policy))break;
    const fresh=await liveCloseScope(surface.tab_id,v,policy,true);if(!fresh)break;
    if(fresh.tab.status==='complete')try{
     const read=await liveCloseInject(surface.tab_id,'inspect',{identity:fresh.identity,parents:parent?[parent]:[]});
     const candidate=read.result?.surfaces?.find(s=>s.plan_id===v.plan_id&&s.control_mode==='control'&&s.control_budget===v.budget&&s.control_minutes===v.duration_minutes&&s.control_created_at);
     if(candidate&&read.documentId!==surface.document_id)observation={...candidate,tab_id:surface.tab_id,document_id:read.documentId};
    }catch{}
    if(!observation)await new Promise(r=>setTimeout(r,250));
   }
  }
  receiptAttempted=true;
  await liveCloseReceipt({id:command.id,lease:command.lease,submitted:didSubmit,observation,message:didSubmit?'已单次提交控量，等待独立状态核验；不自动重试':submitted.result?.code||'未提交控量'},config);
 }catch(e){
  liveClosePermit=null;
  // An acknowledgement may be lost after the backend commits. Preserve the
  // exact persisted receipt; never replace it with an exception-shaped one.
  if(receiptAttempted)return true;
  if(prepared&&!began&&surface)try{await liveCloseInject(surface.tab_id,'abort',args,surface.document_id);}catch{}
  await liveCloseReceipt({id:command.id,lease:command.lease,submitted:began,observation:null,message:String(e.message||'控量核对未完成').slice(0,300)},config);
 }finally{liveClosePermit=null;}
 return true;
}
