/* Original-cruise task transport. Runs under the main worker's single busy
 * lock and session switch. Only localhost requests and existing scoped tabs. */
let cruiseControlPermit=null,cruiseJobCursor='';
const CRUISE_RECEIPT='qc_cruise_receipt_v1';
const CRUISE_PATHS=new Set(['worker-observe','worker-claim','worker-begin','worker-receipt']);

async function cruiseControlPost(path,body,token){
  if(!CRUISE_PATHS.has(path)||!validObserverToken(token))throw Error('cruise_protocol');
  const response=await fetch('http://127.0.0.1:8877/api/cruise/actions/'+path,{
    method:'POST',headers:{'Content-Type':'application/json','X-Monitor-Token':token},body:JSON.stringify(body),
    signal:AbortSignal.timeout(25000),redirect:'error',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
  if(!response.ok)throw Error('cruise_local_rejected');
  return response.json();
}
function cruiseControlMetricDate(pageUrl){
  try{
    const url=new URL(pageUrl),dates=url.searchParams.getAll('dr');
    const match=dates.length===1&&/^(\d{4}-\d{2}-\d{2}),\1$/.exec(dates[0]);
    if(match&&new Date(match[1]+'T00:00:00Z').toISOString().slice(0,10)===match[1])return match[1];
  }catch{/* Capture time is never substituted for a missing statistics date. */}
  return null;
}
function cruiseControlSurface(surface,tabId,documentId,pageUrl){
  const metrics={},context={...surface.context},metricDate=cruiseControlMetricDate(pageUrl);
  for(const key of ['spend','comprehensive_cost','roi','orders'])if(Object.hasOwn(surface.metrics||{},key))metrics[key]=surface.metrics[key];
  if(metricDate!==null)context.metric_date=metricDate;
  return {...surface,metrics,context,tab_id:tabId,document_id:documentId};
}
async function cruiseControlInject(tabId,phase,request,documentId){
  const target=documentId?{tabId,documentIds:[documentId]}:{tabId,frameIds:[0]};
  const results=await chrome.scripting.executeScript({target,world:'ISOLATED',func:cruiseTaskDom,args:[phase,request]});
  if(results.length!==1||results[0].frameId!==0||typeof results[0].documentId!=='string'||!results[0].documentId)throw Error('cruise_document_missing');
  if(documentId&&results[0].documentId!==documentId)throw Error('cruise_document_changed');
  return results[0];
}
async function cruiseControlInventory(config,session,policy){
  const surfaces=[],seen=new Set(),conflicts=new Set();
  const tabs=await chrome.tabs.query({url:'https://qianchuan.jinritemai.com/*'});
  for(const tab of tabs){
    const id=identity(tab,policy);
    if(!id||id.role!=='detail'||id.scene!=='live')continue;
    if(!await browserControlCheckpoint(config,session,policy))return null;
    const expected={account_id:id.accountId,path:id.path,group:id.group,plan_id:id.planId};
    const fresh=await browserControlTab(tab.id,expected,policy);if(!fresh)continue;
    try{
      const result=await cruiseControlInject(tab.id,'inspect',{identity:fresh.identity});
      const after=await browserControlTab(tab.id,expected,policy);
      if(!after||after.tab.url!==fresh.tab.url||!await browserControlCheckpoint(config,session,policy))continue;
      if(result.result?.ok!==true||!Array.isArray(result.result.surfaces))continue;
      for(const s of result.result.surfaces){
        if(s.target_kind!=='control_task'||s.account_id!==id.accountId||s.plan_id!==id.planId)continue;
        const key=[s.account_id,s.plan_id,s.task_id].join(':');
        if(seen.has(key))conflicts.add(key);seen.add(key);
        surfaces.push(cruiseControlSurface(s,tab.id,result.documentId,fresh.tab.url));
      }
    }catch{/* A missing task table never becomes a main-plan command. */}
  }
  return surfaces.filter(s=>!conflicts.has([s.account_id,s.plan_id,s.task_id].join(':'))).slice(0,1000);
}
async function cruiseControlFinalCheck(message,sender){
  const p=cruiseControlPermit;
  if(!p||p.checking||p.consumed||message.id!==p.id||message.nonce!==p.nonce||sender.id!==chrome.runtime.id||
      sender.tab?.id!==p.surface.tab_id||sender.frameId!==0||sender.documentId!==p.surface.document_id||
      Date.now()>=Date.parse(p.expires_at))return {allow:false};
  p.checking=true;
  try{
    if(!await browserControlCheckpoint(p.config,p.session,p.policy))return {allow:false};
    const fresh=await browserControlTab(p.surface.tab_id,p.surface,p.policy);
    if(!fresh||cruiseControlMetricDate(fresh.tab.url)!==p.metric_date)return {allow:false};
    if(cruiseControlPermit!==p||p.consumed)return {allow:false};
    p.consumed=true;return {allow:true};
  }finally{p.checking=false;}
}
async function cruiseControlReadback(command,config,session,policy){
  const s=command.intent.surface;
  if(!await browserControlCheckpoint(config,session,policy)||!await browserControlTab(s.tab_id,s,policy))return null;
  await chrome.tabs.reload(s.tab_id,{bypassCache:true});
  for(let n=0;n<40;n++){
    if(!await browserControlCheckpoint(config,session,policy))return null;
    const fresh=await browserControlTab(s.tab_id,s,policy);if(!fresh)return null;
    if(fresh.tab.status==='complete')try{
      const result=await cruiseControlInject(s.tab_id,'inspect',{identity:fresh.identity});
      const after=await browserControlTab(s.tab_id,s,policy);
      if(!after||after.tab.url!==fresh.tab.url||!await browserControlCheckpoint(config,session,policy))return null;
      const row=result.result?.ok===true?result.result.surfaces?.find(r=>r.target_kind==='control_task'&&
        r.account_id===command.intent.account_id&&r.plan_id===command.intent.plan_id&&r.task_id===command.intent.task_id):null;
      if(row&&result.documentId!==s.document_id)return {
        account_id:row.account_id,plan_id:row.plan_id,task_id:row.task_id,tab_id:s.tab_id,document_id:s.document_id,
        readback_document_id:result.documentId,field:command.intent.field,value:row[command.intent.field],captured_at:row.captured_at,reloaded:true};
    }catch{/* Read again only. A mutation is never retried. */}
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  return null;
}
async function cruiseControlSendReceipt(receipt,config){
  await chrome.storage.session.set({[CRUISE_RECEIPT]:receipt});
  if(!await observerCheckpoint(config))throw Error('cruise_observer_stopped');
  const result=await cruiseControlPost('worker-receipt',receipt,config.token);
  await chrome.storage.session.remove(CRUISE_RECEIPT);
  await chrome.storage.local.set({lastControlState:{at:new Date().toISOString(),armed:true,code:result.state,action_id:receipt.id,target_kind:'control_task'}});
  return result;
}
async function cruiseControlFlushReceipt(config){
  const pending=(await chrome.storage.session.get(CRUISE_RECEIPT))[CRUISE_RECEIPT];
  if(!pending)return false;
  await cruiseControlSendReceipt(pending,config);return true;
}
function cruiseControlJob(job,policy){
  if(job?.job_kind!==undefined&&!['control','plan_data','material_scan','live_home','account_entry'].includes(job.job_kind))return null;
  if(!job||typeof job.id!=='string'||!/^[0-9a-f]{24}$/.test(job.id)||!['main','auto'].includes(job.lane)||
      typeof job.url!=='string'||job.url.length>16000)return null;
  const account=policy.accounts.find(a=>a.id===job.account_id);if(!account)return null;
  try{
    const u=new URL(job.url);
    if(u.origin!=='https://qianchuan.jinritemai.com'||u.username||u.password||
        u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==account.id)return null;
    if(job.job_kind==='account_entry')return (account.scene==='live'&&u.pathname==='/uni-prom'&&u.searchParams.getAll('dr').length===1&&validReadDate(u.searchParams.get('dr'))||account.scene==='product'&&u.pathname==='/overall-prom'&&u.searchParams.getAll('dateRange').length===1&&validReadDate(u.searchParams.get('dateRange')))?{account_id:account.id,path:u.pathname,group:account.tab_group}:null;
    if(job.job_kind==='live_home')return account.scene==='live'&&job.lane==='main'&&u.pathname==='/uni-prom'&&u.searchParams.getAll('dr').length===1&&validReadDate(u.searchParams.get('dr'))?{account_id:account.id,path:u.pathname,group:account.tab_group}:null;
    if(job.job_kind==='plan_data'&&account.scene==='product'){
      if(u.pathname!=='/overall-prom'||job.lane!=='main'||u.searchParams.getAll('dateRange').length!==1||!validReadDate(u.searchParams.get('dateRange')))return null;
      const fields=new URLSearchParams(u.hash.slice(1).replace(/^\?/,''));
      if(fields.has('uniVideoTab')&&(fields.getAll('uniVideoTab').length!==1||!['1','2'].includes(fields.get('uniVideoTab'))))return null;
      return {account_id:account.id,path:u.pathname,group:account.tab_group};
    }
    if(account.scene!=='live'||u.pathname!=='/uni-prom/detail'||!/^\d{6,30}$/.test(job.plan_id||'')||
        u.searchParams.getAll('adId').length!==1||u.searchParams.get('adId')!==job.plan_id)return null;
    const fragments=new URLSearchParams(u.hash.slice(1));if(fragments.getAll('uniDetail').length!==1)return null;
    const detail=JSON.parse(fragments.get('uniDetail'));
    const view=job.job_kind==='plan_data'?'data':job.lane==='main'?'uni_task_center':'creative';
    if(job.job_kind==='material_scan'){
      const dates=u.searchParams.get('dr')?.split(',');
      const days=dates?.length===2?(Date.parse(dates[1]+'T00:00:00Z')-Date.parse(dates[0]+'T00:00:00Z'))/86400000:NaN;
      if(job.lane!=='auto'||!Number.isInteger(days)||days<2||days>4)return null;
    }
    if(job.job_kind==='plan_data'&&(job.lane!=='main'||u.searchParams.getAll('dr').length!==1||!validReadDate(u.searchParams.get('dr'))))return null;
    if(!detail||typeof detail!=='object'||Array.isArray(detail)||detail.tb!==view)return null;
    return {account_id:account.id,path:u.pathname,group:account.tab_group,plan_id:job.plan_id};
  }catch{return null;}
}
function validReadDate(value){
 const m=/^(\d{4}-\d{2}-\d{2}),\1$/.exec(value||'');
 try{return Boolean(m&&new Date(m[1]+'T00:00:00Z').toISOString().slice(0,10)===m[1]);}catch{return false;}
}
function cruiseReadReached(value,target){
 try{const a=new URL(value),b=new URL(target);
  if(a.origin!==b.origin||a.pathname!==b.pathname||a.searchParams.getAll('aavid').length!==1||a.searchParams.get('aavid')!==b.searchParams.get('aavid'))return false;
  if(b.pathname==='/uni-prom')return a.searchParams.getAll('dr').length===1&&a.searchParams.get('dr')===b.searchParams.get('dr');
  if(b.pathname==='/overall-prom'){
   const af=new URLSearchParams(a.hash.slice(1).replace(/^\?/,'')),bf=new URLSearchParams(b.hash.slice(1).replace(/^\?/,''));
   return a.searchParams.getAll('dateRange').length===1&&a.searchParams.get('dateRange')===b.searchParams.get('dateRange')&&
    ['uniVideoTab','page','pageSize'].every(key=>!bf.has(key)||af.getAll(key).length===1&&af.get(key)===bf.get(key));
  }
  if(a.searchParams.getAll('adId').length!==1||a.searchParams.get('adId')!==b.searchParams.get('adId'))return false;
  if(b.searchParams.has('dr')&&(a.searchParams.getAll('dr').length!==1||a.searchParams.get('dr')!==b.searchParams.get('dr')))return false;
  const af=new URLSearchParams(a.hash.slice(1)),bf=new URLSearchParams(b.hash.slice(1));
  if(af.getAll('uniDetail').length!==1)return false;
  const actualDetail=JSON.parse(af.get('uniDetail')),wantedDetail=JSON.parse(bf.get('uniDetail'));
  if(actualDetail.tb!==wantedDetail.tb)return false;
  if(wantedDetail.tb==='creative'&&wantedDetail.cc){
    const ac=JSON.parse(actualDetail.cc||'{}'),bc=JSON.parse(wantedDetail.cc);
    if(!['p','ps','sf','st'].every(key=>!Object.hasOwn(bc,key)||String(ac[key])===String(bc[key])))return false;
  }
  return true;
 }catch{return false;}
}
async function cruiseReadTab(tabId,expected,job,policy){
 if(!['plan_data','material_scan','live_home'].includes(job.job_kind))return browserControlTab(tabId,expected,policy);
 const tab=await chrome.tabs.get(tabId);let id=identity(tab,policy);
 if(!id&&expected.path==='/uni-prom/detail')try{
  const u=new URL(tab.url),account=policy.accounts.find(a=>a.id===expected.account_id&&a.scene==='live');
  // Read-only recovery of a broken detail URL. No executable plan surface is
  // created here; the registered full target is independently checked after navigation.
  if(account&&u.origin==='https://qianchuan.jinritemai.com'&&u.pathname==='/uni-prom/detail'&&
    u.searchParams.getAll('aavid').length===1&&u.searchParams.get('aavid')===account.id&&
    u.searchParams.getAll('adId').length===1&&u.searchParams.get('adId')==='')
   id={accountId:account.id,scene:'live',path:u.pathname,role:'live',group:account.tab_group};
 }catch{}
 if(!id||id.accountId!==expected.account_id||id.role==='board')return null;
 const group=await chrome.tabGroups.get(tab.groupId);if(group.title!==expected.group||id.group!==expected.group)return null;
 if(expected.path==='/overall-prom'&&id.role!=='product')return null;
 if(expected.path==='/uni-prom'&&!['live','detail'].includes(id.role))return null;
 if(expected.path==='/uni-prom/detail'&&!(id.role==='live'||id.role==='detail'&&id.planId===expected.plan_id))return null;
 return {tab,identity:id};
}
async function cruiseReadCheckpoint(config,policy){
 return await observerScopeCheckpoint(config,policy);
}
async function runObserverReadJobs(){
 if(inFlight||browserControlBusy)return;
 browserControlBusy=true;
 try{
  const config=await chrome.storage.local.get(OBSERVER_KEYS);
  if(!await observerCheckpoint(config))return;
  const policy=await readObserverPolicy(config.token);
  if(typeof batchConfigurePages==='function')await batchConfigurePages(config,policy);
  if(typeof batchObserve==='function')await batchObserve(config,await browserControlSession(),policy);
  if(typeof liveCloseObserve==='function')await liveCloseObserve(config,await browserControlSession(),policy);
  await cruiseControlJobs(config,null,policy);
 }finally{browserControlBusy=false;}
}
async function cruiseControlJobs(config,session,policy){
  if(!await cruiseReadCheckpoint(config,policy))return;
  const response=await fetch('http://127.0.0.1:8877/api/cruise/worker-jobs',{
    headers:{'X-Monitor-Token':config.token},signal:AbortSignal.timeout(10000),redirect:'error',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
  if(!response.ok)throw Error('cruise_jobs_unavailable');
  const data=await response.json();
  if(data?.protocol!==1||data.revision!==policy.revision||data.navigation_allowed!==true||!Array.isArray(data.jobs)||data.jobs.length>1000)return;
  const jobs=data.jobs.filter(job=>cruiseControlJob(job,policy));if(!jobs.length)return;
  const previous=jobs.findIndex(job=>job.id===cruiseJobCursor),job=jobs[(previous+1)%jobs.length];
  cruiseJobCursor=job.id;const expected=cruiseControlJob(job,policy);
  if(job.job_kind==='account_entry'){
    if(typeof accountEntryRun==='function')await accountEntryRun(job,config,policy);
    return;
  }
  let ok=false,message='未找到该账户、分组及父计划唯一的现有页面';
  if(!await cruiseReadCheckpoint(config,policy))return;
  const tabs=await chrome.tabs.query({url:'https://qianchuan.jinritemai.com/*'}),matches=[];
  for(const tab of tabs){
    const fresh=await cruiseReadTab(tab.id,expected,job,policy);if(fresh)matches.push(fresh);
  }
  if(!matches.length&&typeof accountEntryEnsureTab==='function'){
    const account=policy.accounts.find(a=>a.id===job.account_id);let entered;
    try{entered=await accountEntryEnsureTab(config,policy,account,job.url,job.id);}catch{entered={code:'entry_failed'};}
    if(entered.code==='navigation_ready'||entered.code==='loading'){
      if(entered.tab&&await cruiseReadCheckpoint(config,policy)){
        if(!accountEntryUrl(entered.tab.url,account))return;
        if(!cruiseReadReached(entered.tab.url,job.url)&&entered.code==='navigation_ready'){
          if(!await accountEntrySameTab(entered.tab,account))return;
          await chrome.tabs.update(entered.tab.id,{url:job.url});
        }
        const fresh=await cruiseReadTab(entered.tab.id,expected,job,policy);if(fresh)matches.push(fresh);
      }
    }else message=ACCOUNT_ENTRY_MESSAGES[entered.code]||message;
  }
  if(matches.length===1){
    try{
      if(!await cruiseReadCheckpoint(config,policy))return;
      const fresh=await cruiseReadTab(matches[0].tab.id,expected,job,policy);if(!fresh)return;
      const checked=await browserControlInject(fresh.tab.id,'inspect_navigation',{identity:fresh.identity});
      if(checked.result?.ok!==true)throw Error('read_identity_unready');
      if(checked.result.code==='editor_open')throw Error('read_editor_open');
      if(checked.result.code!=='navigation_ready')throw Error('read_identity_unready');
      let target=job.url;
      if(expected.path==='/overall-prom'){
        const actual=new URL(fresh.tab.url),wanted=new URL(job.url);actual.searchParams.set('dateRange',wanted.searchParams.get('dateRange'));
        const fragments=new URLSearchParams(actual.hash.slice(1).replace(/^\?/,''));
        const wantedFields=new URLSearchParams(wanted.hash.slice(1).replace(/^\?/,''));
        if(wantedFields.has('uniVideoTab')){
          fragments.set('uniVideoTab',wantedFields.get('uniVideoTab'));
          fragments.set('page','1');fragments.set('pageSize','50');
        }
        if(fragments.has('adDrawerDateRange'))fragments.set('adDrawerDateRange',wanted.searchParams.get('dateRange'));
        actual.hash=(actual.hash.startsWith('#?')?'?':'')+fragments.toString();target=actual.href;
      }else if(expected.path==='/uni-prom/detail'&&fresh.identity.planId===expected.plan_id){
        const actual=new URL(fresh.tab.url),wanted=new URL(job.url);
        if(wanted.searchParams.has('dr'))actual.searchParams.set('dr',wanted.searchParams.get('dr'));
        const fragments=new URLSearchParams(actual.hash.slice(1)),requested=new URLSearchParams(wanted.hash.slice(1));
        const detail=JSON.parse(fragments.get('uniDetail')||'{}');
        detail.tb=JSON.parse(requested.get('uniDetail')).tb;
        if(job.job_kind==='material_scan'){
          const currentCc=JSON.parse(detail.cc||'{}'),requestedCc=JSON.parse(JSON.parse(requested.get('uniDetail')).cc||'{}');
          for(const key of ['p','ps','sf','st'])if(Object.hasOwn(requestedCc,key))currentCc[key]=requestedCc[key];
          detail.cc=JSON.stringify(currentCc);
        }
        fragments.set('uniDetail',JSON.stringify(detail));actual.hash=fragments.toString();target=actual.href;
      }
      if(!cruiseReadReached(fresh.tab.url,target)){
        await chrome.tabs.update(fresh.tab.id,{url:target});
        // Wait for the requested location before refreshing its date/view.
        let reached=false;
        for(let n=0;n<40;n++){
          if(!await cruiseReadCheckpoint(config,policy))return;
          const moved=await cruiseReadTab(fresh.tab.id,expected,job,policy);if(!moved)throw Error('job_scope_changed');
          if(cruiseReadReached(moved.tab.url,target)&&moved.tab.status==='complete'){reached=true;break;}
          await new Promise(resolve=>setTimeout(resolve,250));
        }
        if(!reached)throw Error('job_navigation_unconfirmed');
      }
      if(!await cruiseReadCheckpoint(config,policy)||!await cruiseReadTab(fresh.tab.id,expected,job,policy))return;
      await chrome.tabs.reload(fresh.tab.id,{bypassCache:true});ok=true;
      message='已刷新该账户现有页面，等待新数据；未修改投放';
    }catch(error){message=error?.message==='read_editor_open'?'当前页面有编辑窗口，未刷新以保留输入':error?.message==='read_identity_unready'?'页面账户标识核验尚未通过，未刷新；请等待页面加载后重新扫描':'页面导航或刷新未完成；未改投放，也未新建标签';}
  }
  if(!await cruiseReadCheckpoint(config,policy))return;
  const receipt=await fetch('http://127.0.0.1:8877/api/cruise/worker-job-result',{
    method:'POST',headers:{'Content-Type':'application/json','X-Monitor-Token':config.token},body:JSON.stringify({id:job.id,ok,message}),
    signal:AbortSignal.timeout(10000),redirect:'error',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
  if(!receipt.ok)throw Error('cruise_job_receipt_rejected');
}
async function runCruiseControl(config,session,policy){
  let command=null,prepared=false,permitIssued=false;
  try{
    if(!await browserControlCheckpoint(config,session,policy))return;
    if(await cruiseControlFlushReceipt(config))return;
    const surfaces=await cruiseControlInventory(config,session,policy);if(surfaces===null)return;
    if(!await browserControlCheckpoint(config,session,policy))return;
    await cruiseControlPost('worker-observe',{protocol:1,worker_id:session.worker_id,epoch:session.epoch,armed:true,revision:policy.revision,surfaces},config.token);
    if(!await browserControlCheckpoint(config,session,policy))return;
    const claimed=await cruiseControlPost('worker-claim',{worker_id:session.worker_id,epoch:session.epoch},config.token);
    command=claimed.command;
    if(!command){
      await chrome.storage.local.set({lastCruiseState:{at:new Date().toISOString(),tasks:surfaces.length,code:claimed.blocked?'blocked_unknown':'waiting_for_single_approval'}});
      if(!claimed.blocked)await cruiseControlJobs(config,session,policy);
      return;
    }
    const s=command.intent?.surface;
    if(command.intent?.target_kind!=='control_task'||!s||!await browserControlCheckpoint(config,session,policy))throw Error('cruise_command_invalid');
    const fresh=await browserControlTab(s.tab_id,s,policy);if(!fresh||fresh.identity.role!=='detail')throw Error('cruise_tab_changed');
    const metricDate=cruiseControlMetricDate(fresh.tab.url);
    if(s.context?.metric_date!==undefined&&s.context.metric_date!==metricDate)throw Error('cruise_statistics_date_changed');
    const args={identity:fresh.identity,intent:command.intent,id:command.id,intent_digest:command.intent_digest,expires_at:command.expires_at};
    const result=await cruiseControlInject(s.tab_id,'prepare',args,s.document_id);
    if(result.result?.ok!==true||result.result.prepared!==true)throw Error('cruise_prepare_rejected');
    prepared=true;
    const afterPrepare=await browserControlTab(s.tab_id,s,policy);
    if(!await browserControlCheckpoint(config,session,policy)||!afterPrepare||cruiseControlMetricDate(afterPrepare.tab.url)!==metricDate)throw Error('cruise_switch_changed');
    const observation=cruiseControlSurface(result.result.surface,s.tab_id,s.document_id,fresh.tab.url);
    const begun=await cruiseControlPost('worker-begin',{id:command.id,lease:command.lease,observation},config.token);
    if(begun.submit_once!==true)throw Error('cruise_begin_rejected');
    permitIssued=true;
    cruiseControlPermit={id:command.id,nonce:result.result.submit_token,surface:s,expires_at:command.expires_at,metric_date:metricDate,config,session,policy,checking:false,consumed:false};
    if(!await browserControlCheckpoint(config,session,policy))throw Error('cruise_switch_changed');
    const resultAfter=await cruiseControlInject(s.tab_id,'submit',{...args,submit_token:result.result.submit_token},s.document_id);
    cruiseControlPermit=null;
    const didSubmit=resultAfter.result?.submitted===true;
    const after=didSubmit&&resultAfter.result.editor_open===false?await cruiseControlReadback(command,config,session,policy):null;
    const receipt={id:command.id,lease:command.lease,submitted:didSubmit,observation:after,
      message:after?'刷新后重新读取同一父计划下任务设置，等待本机比对':didSubmit?'任务已尝试提交，结果尚未核验；不会重试':'任务提交前检查未通过，未再次点击'};
    command=null;await cruiseControlSendReceipt(receipt,config);
  }catch{
    cruiseControlPermit=null;
    if(command){
      if(prepared&&!permitIssued)try{
        const s=command.intent.surface,fresh=await browserControlTab(s.tab_id,s,policy);
        if(fresh)await cruiseControlInject(s.tab_id,'abort',{identity:fresh.identity,intent:command.intent,id:command.id,intent_digest:command.intent_digest},s.document_id);
      }catch{/* Only the owned task editor can be cancelled. */}
      try{await cruiseControlSendReceipt({id:command.id,lease:command.lease,submitted:permitIssued,observation:null,
        message:permitIssued?'任务提交或回读结果不明，停止且不自动重试':'任务提交前核对未完成，未发送修改'},config);}catch{/* Persisted receipt is retried; mutation never is. */}
    }
    await chrome.storage.local.set({lastCruiseState:{at:new Date().toISOString(),code:'control_round_incomplete'}});
  }finally{cruiseControlPermit=null;}
}
