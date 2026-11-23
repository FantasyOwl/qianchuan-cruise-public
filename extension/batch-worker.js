/* Shared busy lock and explicit per-batch confirmation; no platform API IO. */
let batchPermit=null;
let batchViewChecks=[];
const BATCH_RECEIPT='qc_batch_receipt_v1';
async function batchPost(path,body,token){
 if(!['worker-observe','worker-claim','worker-begin','worker-receipt','worker-readback-jobs','worker-readback-result'].includes(path)||!validObserverToken(token))throw Error('batch_protocol');
 const r=await fetch('http://127.0.0.1:8877/api/cruise/batch/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-Monitor-Token':token},body:JSON.stringify(body),
  signal:AbortSignal.timeout(25000),redirect:'error',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
 if(r.status===404)return {unsupported:true};
 if(!r.ok)throw Error('batch_local_rejected');return r.json();
}
async function batchInject(tabId,phase,request,documentId){
 const result=await chrome.scripting.executeScript({target:documentId?{tabId,documentIds:[documentId]}:{tabId,frameIds:[0]},world:'ISOLATED',func:batchMaterialDom,args:[phase,request]});
 if(result.length!==1||result[0].frameId!==0||!result[0].documentId||documentId&&result[0].documentId!==documentId)throw Error('batch_document_changed');return result[0];
}
async function batchObserve(config,session,policy){
 if(!session||!await observerScopeCheckpoint(config,policy))return null;
 const surfaces=[],seen=new Set(),duplicates=new Set();
 for(const tab of await chrome.tabs.query({url:'https://qianchuan.jinritemai.com/*'})){
  const identityNow=identity(tab,policy);if(identityNow?.role!=='detail'||identityNow.scene!=='live')continue;
  const expected={account_id:identityNow.accountId,plan_id:identityNow.planId,path:identityNow.path,group:identityNow.group};
  if(!await observerScopeCheckpoint(config,policy)||!await browserControlTab(tab.id,expected,policy))continue;
  try{
   const r=await batchInject(tab.id,'inspect',{identity:identityNow}),fresh=await browserControlTab(tab.id,expected,policy);
   if(r.result?.ok!==true||!r.result.surface||!fresh||fresh.tab.url!==tab.url)continue;
   const s={...r.result.surface,tab_id:tab.id,document_id:r.documentId};const key=s.account_id+':'+s.plan_id;
   if(seen.has(key))duplicates.add(key);seen.add(key);surfaces.push(s);
  }catch{}
 }
 if(!await observerScopeCheckpoint(config,policy))return null;
 const armed=await browserControlCheckpoint(config,session,policy);
 const filtered=surfaces.filter(s=>!duplicates.has(s.account_id+':'+s.plan_id));
 const accepted=await batchPost('worker-observe',{protocol:1,worker_id:session.worker_id,epoch:session.epoch,revision:policy.revision,armed,surfaces:filtered,view_checks:batchViewChecks},config.token);
 if(accepted.unsupported)return null;
 return filtered;
}
async function batchConfigurePages(config,policy){
 if(!await observerScopeCheckpoint(config,policy))return;
 const r=await fetch('http://127.0.0.1:8877/api/cruise/worker-jobs',{headers:{'X-Monitor-Token':config.token},signal:AbortSignal.timeout(10000),redirect:'error',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
 if(!r.ok)return;const nav=await r.json();
 if(nav.navigation_allowed!==true||nav.revision!==policy.revision)return;
 const checks=[];
 for(const tab of await chrome.tabs.query({url:'https://qianchuan.jinritemai.com/*'})){
  const current=identity(tab,policy);if(current?.role!=='detail'||current.scene!=='live')continue;
  try{
   const d=JSON.parse(new URLSearchParams(new URL(tab.url).hash.slice(1)).get('uniDetail')||'{}');if(d.tb!=='creative')continue;
   const expected={account_id:current.accountId,plan_id:current.planId,path:current.path,group:current.group};
   if(!await observerScopeCheckpoint(config,policy)||!await browserControlTab(tab.id,expected,policy))continue;
   const configured=await batchInject(tab.id,'configure_view',{identity:current});
   if(!await observerScopeCheckpoint(config,policy)||!await browserControlTab(tab.id,expected,policy))continue;
   const v=configured.result||{};
   checks.push({account_id:current.accountId,plan_id:current.planId,tab_id:tab.id,code:/^[a-z_]+$/.test(v.code||'')?v.code:'view_control_failed',before:v.before||null,after:v.after||null});
  }catch{checks.push({account_id:current.accountId,plan_id:current.planId,tab_id:tab.id,code:'view_control_failed',before:null,after:null});}
 }
 batchViewChecks=checks;
}
async function batchFinalCheck(message,sender){
 const p=batchPermit;
 if(!p||p.consumed||p.checking||p.id!==message.id||p.nonce!==message.nonce||sender.id!==chrome.runtime.id||sender.frameId!==0||sender.tab?.id!==p.surface.tab_id||sender.documentId!==p.surface.document_id||Date.now()>=Date.parse(p.expires_at))return {allow:false};
 p.checking=true;
 try{
  if(!await browserControlCheckpoint(p.config,p.session,p.policy)||!await browserControlTab(p.surface.tab_id,p.surface,p.policy))return {allow:false};
  if(batchPermit!==p||p.consumed)return {allow:false};p.consumed=true;return {allow:true};
 }finally{p.checking=false;}
}
async function batchSendReceipt(value,config){
 await chrome.storage.session.set({[BATCH_RECEIPT]:value});
 if(!await observerCheckpoint(config))throw Error('batch_observer_stopped');
 const result=await batchPost('worker-receipt',value,config.token);
 await chrome.storage.session.remove(BATCH_RECEIPT);
 await chrome.storage.local.set({lastControlState:{at:new Date().toISOString(),armed:true,code:result.state,target_kind:'material_batch'}});return result;
}
async function batchFlushReceipt(config){
 const value=(await chrome.storage.session.get(BATCH_RECEIPT))[BATCH_RECEIPT];if(!value)return false;
 await batchSendReceipt(value,config);return true;
}
async function batchReadback(command,config,policy){
 const s=command.intent.surface;
 if(!await observerScopeCheckpoint(config,policy))return null;
 const fresh=await browserControlTab(s.tab_id,s,policy);if(!fresh)return null;
 const u=new URL(fresh.tab.url),fields=new URLSearchParams(u.hash.slice(1));
 const detail=JSON.parse(fields.get('uniDetail')||'{}');detail.tb='uni_task_center';fields.set('uniDetail',JSON.stringify(detail));u.hash=fields.toString();
 await chrome.tabs.update(s.tab_id,{url:u.href});
 for(let n=0;n<40;n++){
  if(!await observerScopeCheckpoint(config,policy))return null;
  const tab=await browserControlTab(s.tab_id,s,policy);if(!tab)return null;
  if(tab.tab.status==='complete')break;
  await new Promise(r=>setTimeout(r,250));
 }
 await chrome.tabs.reload(s.tab_id,{bypassCache:true});
 for(let n=0;n<40;n++){
  if(!await observerScopeCheckpoint(config,policy))return null;
  const tab=await browserControlTab(s.tab_id,s,policy);if(!tab)return null;
  if(tab.tab.status==='complete')try{
   const r=await batchInject(s.tab_id,'verify',{identity:tab.identity,intent:command.intent,id:command.id,intent_digest:command.intent_digest});
   const after=await browserControlTab(s.tab_id,s,policy);
   if(after&&after.tab.url===tab.tab.url&&r.documentId!==s.document_id&&r.result?.observation)return {...r.result.observation,tab_id:s.tab_id,document_id:s.document_id,readback_document_id:r.documentId,reloaded:true};
  }catch{}
  await new Promise(r=>setTimeout(r,250));
 }
 return null;
}
async function runBatchControl(config,session,policy){
 if(!await browserControlCheckpoint(config,session,policy))return false;
 if(await batchObserve(config,session,policy)===null)return false;
 if(await batchRecheck(config,session,policy))return true;
 if(!await browserControlCheckpoint(config,session,policy))return false;
 const claim=await batchPost('worker-claim',{worker_id:session.worker_id,epoch:session.epoch},config.token);
 if(claim.blocked)return true;
 const command=claim.command;if(!command)return false;
 const surface=command.intent.surface;let prepared=false,began=false,receiptAttempted=false;
 const args={intent:command.intent,id:command.id,intent_digest:command.intent_digest,expires_at:command.expires_at};
 try{
  if(!await browserControlCheckpoint(config,session,policy))throw Error('batch_switch_changed');
  const fresh=await browserControlTab(surface.tab_id,surface,policy);if(!fresh)throw Error('batch_scope_changed');args.identity=fresh.identity;
  const r=await batchInject(surface.tab_id,'prepare',args,surface.document_id);
  if(r.result?.ok!==true||r.result.prepared!==true)throw Error(r.result?.code||'batch_prepare_failed');prepared=true;
  if(!await browserControlCheckpoint(config,session,policy)||!await browserControlTab(surface.tab_id,surface,policy))throw Error('batch_switch_changed');
  const begin=await batchPost('worker-begin',{id:command.id,lease:command.lease,observation:{...r.result.surface,tab_id:surface.tab_id,document_id:surface.document_id}},config.token);
  if(begin.submit_once!==true)throw Error('batch_begin_failed');began=true;
  batchPermit={id:command.id,nonce:r.result.submit_token,surface,expires_at:command.expires_at,config,session,policy,consumed:false,checking:false};
  if(!await browserControlCheckpoint(config,session,policy))throw Error('batch_switch_changed');
  const submitted=await batchInject(surface.tab_id,'submit',{...args,submit_token:r.result.submit_token},surface.document_id);
  batchPermit=null;
  const didSubmit=submitted.result?.submitted===true;
  const readback=didSubmit?await batchReadback(command,config,policy):null;
  receiptAttempted=true;
  await batchSendReceipt({id:command.id,lease:command.lease,submitted:didSubmit,observation:readback,
   message:didSubmit?'已单次点击本批创建，等待独立任务回读；结果不明不重试':submitted.result?.code||'本批未提交'},config);
 }catch(error){
  batchPermit=null;
  if(receiptAttempted)return true;
  if(prepared&&!began)try{await batchInject(surface.tab_id,'abort',args,surface.document_id);}catch{}
  await batchSendReceipt({id:command.id,lease:command.lease,submitted:began,observation:null,message:String(error?.message||'批量创建未完成').slice(0,480)},config);
 }finally{batchPermit=null;}
 return true;
}
async function batchRecheck(config,session,policy){
 if(!await observerScopeCheckpoint(config,policy))return false;
 const jobs=await batchPost('worker-readback-jobs',{worker_id:session.worker_id,epoch:session.epoch},config.token);
 if(jobs.unsupported||!jobs.commands?.length)return false;
 const command=jobs.commands[0],observation=await batchReadback(command,config,policy);
 if(!await observerScopeCheckpoint(config,policy))return true;
 await batchPost('worker-readback-result',{id:command.id,worker_id:session.worker_id,epoch:session.epoch,observation},config.token);
 return true;
}
