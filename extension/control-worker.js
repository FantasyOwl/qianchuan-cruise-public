/* Browser executor transport. No platform API, credentials or new tabs.
 * The session switch is off on extension reload and browser restart.
 */
let browserControlBusy=false, browserControlPermit=null, browserControlInit=null, browserControlSwitchVersion=0;
let browserControlSwitchTail=Promise.resolve(),browserControlDesiredArmed=null;
const CONTROL_SESSION='qc_control_session_v1', CONTROL_RECEIPT='qc_control_receipt_v1';
const CONTROL_PATHS=new Set(['worker-observe','worker-claim','worker-begin','worker-receipt']);

async function browserControlSession(){
  if(!chrome.storage.session)return null;
  if(!browserControlInit)browserControlInit=(async()=>{
    let value=(await chrome.storage.session.get(CONTROL_SESSION))[CONTROL_SESSION];
    if(!value){
      let id=(await chrome.storage.local.get('control_worker_id')).control_worker_id;
      if(typeof id!=='string'||!/^[0-9a-f-]{36}$/.test(id)){id=crypto.randomUUID();await chrome.storage.local.set({control_worker_id:id});}
      value={worker_id:id,epoch:crypto.randomUUID(),armed:false,observer_epoch:null};
      await chrome.storage.session.set({[CONTROL_SESSION]:value});
    }
  })();
  await browserControlInit;
  return (await chrome.storage.session.get(CONTROL_SESSION))[CONTROL_SESSION];
}
async function browserControlPost(path,body,token){
  if(!CONTROL_PATHS.has(path)||!validObserverToken(token))throw Error('control_protocol');
  const response=await fetch('http://127.0.0.1:8877/api/browser-actions/'+path,{
    method:'POST',headers:{'Content-Type':'application/json','X-Monitor-Token':token},body:JSON.stringify(body),
    signal:AbortSignal.timeout(25000),redirect:'error',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
  if(!response.ok)throw Error('control_local_rejected');
  return response.json();
}
async function browserControlCheckpoint(config,session,policy){
  if(browserControlDesiredArmed===false||!session?.armed||!await observerCheckpoint(config))return false;
  const current=await browserControlSession();
  if(!current?.armed||current.epoch!==session.epoch||current.worker_id!==session.worker_id||current.observer_epoch!==(config.epoch||''))return false;
  return (await readObserverPolicy(config.token)).revision===policy.revision&&await observerCheckpoint(config);
}
async function browserControlTab(tabId,expected,policy){
  const tab=await chrome.tabs.get(tabId),id=identity(tab,policy);
  if(!id||id.role==='board'||id.accountId!==expected.account_id||id.path!==expected.path||
      (id.role==='detail'&&id.planId!==expected.plan_id))return null;
  const group=await chrome.tabGroups.get(tab.groupId);
  return group.title===expected.group&&id.group===expected.group?{tab,identity:id}:null;
}
async function browserControlInject(tabId,phase,request,documentId){
  const target=documentId?{tabId,documentIds:[documentId]}:{tabId,frameIds:[0]};
  const result=await chrome.scripting.executeScript({target,world:'ISOLATED',func:browserPlanDom,args:[phase,request]});
  if(result.length!==1||result[0].frameId!==0||typeof result[0].documentId!=='string'||!result[0].documentId)throw Error('control_document_missing');
  if(documentId&&result[0].documentId!==documentId)throw Error('control_document_changed');
  return result[0];
}
async function browserControlInventory(config,session,policy){
  const groups=new Map();
  for(const title of new Set(policy.accounts.map(a=>a.tab_group)))for(const g of await chrome.tabGroups.query({title}))if(g.title===title)groups.set(g.id,title);
  const tabs=await chrome.tabs.query({url:'https://qianchuan.jinritemai.com/*'}),surfaces=[],conflicts=new Set(),seen=new Set();
  for(const tab of tabs){
    const id=identity(tab,policy);
    if(!id||!['live','product','detail'].includes(id.role)||groups.get(tab.groupId)!==id.group)continue;
    if(!await browserControlCheckpoint(config,session,policy))return null;
    const expected={account_id:id.accountId,path:id.path,group:id.group,...(id.planId?{plan_id:id.planId}:{})};
    const fresh=await browserControlTab(tab.id,expected,policy);if(!fresh)continue;
    try{
      const result=await browserControlInject(tab.id,'inspect',{identity:fresh.identity});
      if(!await browserControlTab(tab.id,expected,policy)||!await browserControlCheckpoint(config,session,policy))continue;
      if(result.result?.ok!==true||!Array.isArray(result.result.surfaces))continue;
      for(const s of result.result.surfaces){
        const key=s.account_id+':'+s.plan_id;
        if(seen.has(key))conflicts.add(key);seen.add(key);
        surfaces.push({...s,tab_id:tab.id,document_id:result.documentId});
      }
    }catch{/* A closed or changed page is not an alternative execution target. */}
  }
  return surfaces.filter(s=>!conflicts.has(s.account_id+':'+s.plan_id)).slice(0,1000);
}
async function setBrowserControlArmed(armed){
  const switchVersion=++browserControlSwitchVersion;browserControlPermit=null;browserControlDesiredArmed=armed===true;
  if(typeof cruiseControlPermit!=='undefined')cruiseControlPermit=null;
  if(typeof batchPermit!=='undefined')batchPermit=null;
  if(typeof hybridPermit!=='undefined')hybridPermit=null;
  if(typeof liveClosePermit!=='undefined')liveClosePermit=null;
  const operation=browserControlSwitchTail.then(async()=>{
    const config=await chrome.storage.local.get(OBSERVER_KEYS),session=await browserControlSession();
    if(!session)throw Error('control_session_unavailable');
    if(armed&&(!validObserverToken(config.token)||!await observerCheckpoint(config)))throw Error('control_observer_required');
    if(switchVersion!==browserControlSwitchVersion)return {ok:false,armed:false};
    const next={...session,armed:armed===true,epoch:crypto.randomUUID(),observer_epoch:config.epoch||''};
    await chrome.storage.session.set({[CONTROL_SESSION]:next});browserControlPermit=null;
    await chrome.storage.local.set({lastControlState:{at:new Date().toISOString(),armed:next.armed,code:next.armed?'waiting_for_single_approval':'paused'}});
    if(!next.armed&&validObserverToken(config.token)&&await observerCheckpoint(config)){
      const policy=await readObserverPolicy(config.token);
      await browserControlPost('worker-observe',{protocol:1,worker_id:next.worker_id,epoch:next.epoch,armed:false,revision:policy.revision,surfaces:[]},config.token);
      if(typeof cruiseControlPost==='function')await cruiseControlPost('worker-observe',{protocol:1,worker_id:next.worker_id,epoch:next.epoch,armed:false,revision:policy.revision,surfaces:[]},config.token);
      if(typeof batchPost==='function')await batchPost('worker-observe',{protocol:1,worker_id:next.worker_id,epoch:next.epoch,armed:false,revision:policy.revision,surfaces:[]},config.token);
      if(typeof liveClosePost==='function')await liveClosePost('worker-observe',{protocol:1,worker_id:next.worker_id,epoch:next.epoch,armed:false,revision:policy.revision,surfaces:[]},config.token);
    }
    return {ok:switchVersion===browserControlSwitchVersion,armed:next.armed};
  });
  browserControlSwitchTail=operation.catch(()=>{});return operation;
}
async function browserControlFinalCheck(message,sender){
  const p=browserControlPermit;
  if(!p||p.checking||p.consumed||message.id!==p.id||message.nonce!==p.nonce||sender.id!==chrome.runtime.id||sender.tab?.id!==p.surface.tab_id||
      sender.frameId!==0||sender.documentId!==p.surface.document_id||Date.now()>=Date.parse(p.expires_at))return {allow:false};
  p.checking=true;
  try{
    if(!await browserControlCheckpoint(p.config,p.session,p.policy)||!await browserControlTab(p.surface.tab_id,p.surface,p.policy))return {allow:false};
    if(browserControlPermit!==p||p.consumed)return {allow:false};
    p.consumed=true;return {allow:true};
  }finally{p.checking=false;}
}
async function browserControlReadback(command,config,session,policy){
  const s=command.intent.surface;
  if(!await browserControlCheckpoint(config,session,policy)||!await browserControlTab(s.tab_id,s,policy))return null;
  await chrome.tabs.reload(s.tab_id,{bypassCache:true});
  for(let n=0;n<40;n++){
    if(!await browserControlCheckpoint(config,session,policy))return null;
    const fresh=await browserControlTab(s.tab_id,s,policy);if(!fresh)return null;
    if(fresh.tab.status==='complete'){
      try{
        const result=await browserControlInject(s.tab_id,'inspect',{identity:fresh.identity});
        const row=result.result?.ok===true?result.result.surfaces?.find(r=>r.plan_id===command.intent.plan_id&&r.account_id===command.intent.account_id):null;
        if(row&&result.documentId!==s.document_id){
          return {account_id:row.account_id,plan_id:row.plan_id,tab_id:s.tab_id,document_id:s.document_id,
            readback_document_id:result.documentId,field:command.intent.field,value:row[command.intent.field],
            captured_at:row.captured_at,reloaded:true};
        }
      }catch{/* Only read again after reload; never repeat the mutation. */}
    }
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  return null;
}
async function browserControlSendReceipt(receipt,config){
  await chrome.storage.session.set({[CONTROL_RECEIPT]:receipt});
  if(!await observerCheckpoint(config))throw Error('control_observer_stopped');
  const result=await browserControlPost('worker-receipt',receipt,config.token);
  await chrome.storage.session.remove(CONTROL_RECEIPT);
  await chrome.storage.local.set({lastControlState:{at:new Date().toISOString(),armed:true,code:result.state,action_id:receipt.id}});
  return result;
}
async function runBrowserControl(){
  if(browserControlBusy||inFlight)return;
  browserControlBusy=true;let config,session,policy,command=null,permitIssued=false,prepared=false;
  try{
    config=await chrome.storage.local.get(OBSERVER_KEYS);session=await browserControlSession();
    if(!session?.armed||!validObserverToken(config.token)||!await observerCheckpoint(config))return;
    policy=await readObserverPolicy(config.token);
    if(!await browserControlCheckpoint(config,session,policy))return;
    const pending=(await chrome.storage.session.get(CONTROL_RECEIPT))[CONTROL_RECEIPT];
    if(pending){await browserControlSendReceipt(pending,config);return;}
    if(typeof cruiseControlFlushReceipt==='function'&&await cruiseControlFlushReceipt(config))return;
    if(typeof batchFlushReceipt==='function'&&await batchFlushReceipt(config))return;
    if(typeof liveCloseFlush==='function'&&await liveCloseFlush(config))return;
    if(typeof runLiveClose==='function'&&await runLiveClose(config,session,policy))return;
    if(typeof runBatchControl==='function'&&await runBatchControl(config,session,policy))return;
    const surfaces=await browserControlInventory(config,session,policy);if(surfaces===null)return;
    if(!await browserControlCheckpoint(config,session,policy))return;
    await browserControlPost('worker-observe',{protocol:1,worker_id:session.worker_id,epoch:session.epoch,armed:true,revision:policy.revision,surfaces},config.token);
    if(!await browserControlCheckpoint(config,session,policy))return;
    const claimed=await browserControlPost('worker-claim',{worker_id:session.worker_id,epoch:session.epoch},config.token);
    command=claimed.command;
    if(!command){
      await chrome.storage.local.set({lastControlState:{at:new Date().toISOString(),armed:true,code:claimed.blocked?'blocked_unknown':'waiting_for_single_approval',plans:surfaces.length}});
      if(!claimed.blocked&&typeof runCruiseControl==='function')await runCruiseControl(config,session,policy);
      return;
    }
    const s=command.intent.surface;
    if(!await browserControlCheckpoint(config,session,policy))throw Error('control_switch_changed');
    const fresh=await browserControlTab(s.tab_id,s,policy);if(!fresh)throw Error('control_tab_changed');
    const args={identity:fresh.identity,intent:command.intent,id:command.id,intent_digest:command.intent_digest,expires_at:command.expires_at};
    const result=await browserControlInject(s.tab_id,'prepare',args,s.document_id);
    if(result.result?.ok!==true||result.result.prepared!==true)throw Error('control_prepare_rejected');
    prepared=true;
    if(!await browserControlCheckpoint(config,session,policy)||!await browserControlTab(s.tab_id,s,policy))throw Error('control_switch_changed');
    const observation={...result.result.surface,tab_id:s.tab_id,document_id:s.document_id};
    const begun=await browserControlPost('worker-begin',{id:command.id,lease:command.lease,observation},config.token);
    if(begun.submit_once!==true)throw Error('control_begin_rejected');
    permitIssued=true;
    browserControlPermit={id:command.id,nonce:result.result.submit_token,surface:s,expires_at:command.expires_at,config,session,policy,checking:false,consumed:false};
    if(!await browserControlCheckpoint(config,session,policy))throw Error('control_switch_changed');
    const submitted=await browserControlInject(s.tab_id,'submit',{...args,submit_token:result.result.submit_token},s.document_id);
    browserControlPermit=null;
    const didSubmit=submitted.result?.submitted===true;
    const observationAfter=didSubmit&&submitted.result.editor_open===false?await browserControlReadback(command,config,session,policy):null;
    const receipt={id:command.id,lease:command.lease,submitted:didSubmit,observation:observationAfter,
      message:observationAfter?'刷新后重新读取页面设置，等待本机比对':didSubmit?'已尝试提交，尚未核对最终设置；不会重试':'提交前核对未通过，没有再次点击'};
    command=null; // A failed acknowledgement must resend this exact receipt, never reconstruct it.
    await browserControlSendReceipt(receipt,config);
  }catch{
    browserControlPermit=null;
    if(command&&config){
      if(prepared&&!permitIssued){
        try{
          const s=command.intent.surface,fresh=await browserControlTab(s.tab_id,s,policy);
          if(fresh)await browserControlInject(s.tab_id,'abort',{identity:fresh.identity,intent:command.intent,id:command.id,intent_digest:command.intent_digest},s.document_id);
        }catch{/* No unrelated or global cancel button. */}
      }
      try{await browserControlSendReceipt({id:command.id,lease:command.lease,submitted:permitIssued,observation:null,
        message:permitIssued?'提交或回读结果不明，已停止且不自动重试':'提交前核对未完成，未发送修改'},config);}catch{/* The persisted receipt can be resent without repeating the action. */}
    }
    await chrome.storage.local.set({lastControlState:{at:new Date().toISOString(),armed:session?.armed===true,code:'control_round_incomplete'}});
  }finally{browserControlPermit=null;browserControlBusy=false;}
}
