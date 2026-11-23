/* Persisted server jobs; each wake performs bounded work in existing tabs. */
let hybridPermit=null;
let hybridWaitContext=null;
const hybridPageVisits=new Map();
const hybridHomeContexts=new Map();
const HYBRID_PATHS=new Set(['worker-next','worker-result','worker-begin','worker-final']);
async function hybridPost(name,body,config){
  if(!HYBRID_PATHS.has(name)||!validObserverToken(config.token))throw Error('网页补位通信校验失败');
  const response=await fetch('http://127.0.0.1:8877/api/cruise/hybrid/'+name,{
    method:'POST',headers:{'Content-Type':'application/json','X-Monitor-Token':config.token},body:JSON.stringify(body),
    signal:AbortSignal.timeout(15000),redirect:'error',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
  const data=await response.json();if(!response.ok)throw Error(data.error||'网页补位本机请求未完成');return data;
}
async function hybridTab(tabId,c,policy){
  const account=policy.accounts.find(a=>a.id===c.account_id&&a.scene==='live'&&a.tab_group===c.group);
  if(!account)return null;
  const tab=await chrome.tabs.get(tabId),u=new URL(tab.url);
  const home=hybridHomeContexts.get(tabId),homeBound=home?.command_id===c.id&&home.binding.account_id===c.account_id&&home.binding.plan_id===c.plan_id&&home.binding.group===c.group&&['prepare','dry_prepare'].includes(c.phase)&&c.action?.intent?.browser_batch?.selection_source==='official_api';
  const homePath=homeBound&&['/uni-prom','/overall-prom'].includes(u.pathname);
  if(u.origin!=='https://qianchuan.jinritemai.com'||!homePath&&!['/uni-prom/detail','/overall-prom/detail'].includes(u.pathname)
    ||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==c.account_id
    ||!homePath&&(u.searchParams.getAll('adId').length!==1||u.searchParams.get('adId')!==c.plan_id)
    ||homePath&&u.searchParams.has('adId')&&(u.searchParams.getAll('adId').length!==1||!['',c.plan_id].includes(u.searchParams.get('adId'))))return null;
  const group=await chrome.tabGroups.get(tab.groupId);return group.title===c.group?tab:null;
}
async function hybridInject(tabId,phase,c,args={},documentId){
  const config=await chrome.storage.local.get(OBSERVER_KEYS),session=await browserControlSession(),policy=await readObserverPolicy(config.token);
  const cleanupMs=phase==='cleanup'&&c.action?.intent?.browser_batch?.selection_source==='official_api'?60000:15000;
  const context={tabId,phase,c,documentId,config,session,policy,expires:['cleanup','rule_cleanup'].includes(phase)?Date.now()+cleanupMs:Date.parse(c.expires_at)};
  if(!Number.isFinite(context.expires))throw Error('网页等待范围缺少有效期');
  hybridWaitContext=context;
  try{
    const home=hybridHomeContexts.get(tabId),bound=home?.command_id===c.id?{home_context:home.binding,detail_preflight:home.preflight,home_proof:home.proof}:{};
    const homeActor=phase==='home_open'||phase==='home_inspect';
    // The actor is serialized into an isolated page world, so a helper from
    // the service-worker scope would not be available there.
    if(phase==='prepare'&&c.action?.intent?.browser_batch?.after?.audience){
      const loaded=await chrome.scripting.executeScript({target:documentId?{tabId,documentIds:[documentId]}:{tabId,frameIds:[0]},world:'ISOLATED',injectImmediately:true,files:['hybrid-audience-dom.js']});
      if(loaded.length!==1||loaded[0].frameId!==0||!loaded[0].documentId||documentId&&loaded[0].documentId!==documentId)throw Error('定向脚本加载时网页文档已切换');
      documentId=loaded[0].documentId;
    }
    const results=await chrome.scripting.executeScript({target:documentId?{tabId,documentIds:[documentId]}:{tabId,frameIds:[0]},
      world:'ISOLATED',injectImmediately:true,func:homeActor?hybridHomeDom:hybridChaseDom,args:[homeActor?phase.slice(5):phase,{command:c,...bound,...args,...(homeActor?{binding:home?.binding}:{})}]});
    if(results.length!==1||results[0].frameId!==0||!results[0].documentId||documentId&&results[0].documentId!==documentId)throw Error('网页文档已切换');
    return results[0];
  }finally{if(hybridWaitContext===context)hybridWaitContext=null;}
}
async function hybridWait(message,sender){
  const p=hybridWaitContext;
  const matches=()=>p&&hybridWaitContext===p&&message?.action==='hybrid-wait'&&sender.id===chrome.runtime.id&&sender.tab?.id===p.tabId&&sender.frameId===0&&
    typeof sender.documentId==='string'&&(!p.documentId||sender.documentId===p.documentId)&&
    message.run_id===p.c.run_id&&message.command_id===p.c.id&&Number.isInteger(message.duration_ms)&&message.duration_ms>=1&&message.duration_ms<=600&&Date.now()<p.expires;
  if(!matches())return {ok:false};
  if(!p.documentId)p.documentId=sender.documentId;
  const allowed=async()=>matches()&&await hybridCheckpoint(p.config,p.session,p.policy,p.phase==='prepare'&&p.c.phase==='prepare'||p.phase==='rule_submit'||p.phase==='rule_prepare'&&!p.c.preview)&&await hybridTab(p.tabId,p.c,p.policy);
  if(!await allowed())return {ok:false};
  // Page timers are throttled in long-hidden tabs. Keep these short, bounded
  // waits in the extension worker; the page still uses normal DOM controls.
  await new Promise(r=>setTimeout(r,message.duration_ms));
  return {ok:!!await allowed()};
}
async function hybridCheckpoint(config,session,policy,write=false){
  return write?browserControlCheckpoint(config,session,policy):observerScopeCheckpoint(config,policy);
}
async function hybridLocate(c,config,session,policy){
  const matches=[];
  for(const tab of await chrome.tabs.query({url:'https://qianchuan.jinritemai.com/*'})){
    const u=new URL(tab.url);
    if(u.searchParams.get('aavid')!==c.account_id||!['/uni-prom','/overall-prom','/uni-prom/detail','/overall-prom/detail'].includes(u.pathname))continue;
    if((await chrome.tabGroups.get(tab.groupId)).title===c.group)matches.push(tab);
  }
  const exact=matches.filter(t=>new URL(t.url).searchParams.get('adId')===c.plan_id);
  const tab=exact.length===1?exact[0]:exact.length===0&&matches.length===1?matches[0]:null;
  if(!tab)throw Error('原Chrome需有本户唯一投放页面及对应分组；不新开浏览器');
  if(!await hybridCheckpoint(config,session,policy))throw Error('本户读取范围已改变');
  let u=new URL(tab.url),recover=tab.discarded===true||tab.status!=='complete';
  if(!['/uni-prom/detail','/overall-prom/detail'].includes(u.pathname)||u.searchParams.get('adId')!==c.plan_id){
    u.pathname='/uni-prom/detail';u.searchParams.set('adId',c.plan_id);
  }else if(!recover){
    try{
      const readOnly=['tasks','lookup','materials','verify','rule_verify','rule_tasks'].includes(c.phase),until=Date.now()+(readOnly?5000:0);
      let checked;
      do{
        if(!await hybridCheckpoint(config,session,policy))throw Error('本户读取范围已改变');
        checked=await hybridInject(tab.id,'identity',c);
        if(checked.result?.editor_open){
          // A submitted native drawer can take a moment to close. Never close
          // or reload a real editor, even while its identity header is absent.
          if(c.phase!=='verify'||Date.now()>=until)throw Error('当前页面有编辑窗口，保留输入'+(checked.result.editor_summary?'（'+checked.result.editor_summary+'）':''));
        }else if(checked.result?.ok===true)break;
        else if(!readOnly||!/等待可见(?:账户|母计划)ID/.test(checked.result?.message||''))throw Error(checked.result?.message||'等待账户标识');
        if(Date.now()>=until){recover=true;break;}
        await new Promise(r=>setTimeout(r,200));
      }while(true);
    }catch(error){
      // Chrome error/discarded documents can report this same message even
      // when the extension has its existing site permission. Recover only a
      // read stage; never refresh over an editable or submitted create step.
      if(!['tasks','lookup','materials','verify','rule_verify','rule_tasks'].includes(c.phase)||!/Cannot access contents of the page|manifest must request permission/.test(String(error.message)))throw error;
      recover=true;
    }
  }
  const recoveryKey='qc_hybrid_recovery_'+tab.id;
  if(recover){
    if(!['tasks','lookup','materials','verify','rule_verify','rule_tasks'].includes(c.phase))throw Error('页面尚未恢复，保留本批编辑步骤，未自动刷新');
    const previous=(await chrome.storage.session.get(recoveryKey))[recoveryKey];
    const cycle=c.recovery_cycle||0;
    if(previous?.run_id===c.run_id&&previous.phase===c.phase&&(previous.cycle||0)===cycle)throw Error('当前读取阶段已自动刷新原投放页一次，页面仍不可读；已保留页面，请核对登录状态后重试');
  }
  const material=['materials','prepare','dry_prepare'].includes(c.phase),start=material||c.rule_range?c.date_start:c.date_end;
  const visitKey=[c.run_id,c.phase,start,c.date_end,c.document_cycle||0].join(':');
  const actualRange=u.searchParams.get(u.pathname==='/overall-prom/detail'?'dateRange':'dr');
  const visitStorageKey='qc_hybrid_page_'+tab.id;
  const visit=hybridPageVisits.get(tab.id)||(await chrome.storage.session.get(visitStorageKey))[visitStorageKey];
  if(!recover&&visit?.key===visitKey&&actualRange===start+','+c.date_end){
    const current=await hybridInject(tab.id,'identity',c);
    if(current.documentId===visit.documentId&&current.result?.ok&&current.result.editor_open!==true)return tab.id;
  }
  u.searchParams.set(u.pathname==='/overall-prom/detail'?'dateRange':'dr',start+','+c.date_end);
  const fields=new URLSearchParams(u.hash.slice(1).replace(/^\?/,''));
  if(u.pathname==='/overall-prom/detail'){
    fields.set('adDetailTab',material?'creative':'uni_task_center');
    // Keep existing pagination here. The DOM adapter clicks the actual page
    // and waits for new row IDs; rewriting the hash can expose stale rows
    // under an already-changed page number before the native request ends.
    u.hash='?'+fields.toString();
  }else{
    const detail=JSON.parse(fields.get('uniDetail')||'{}');detail.tb=material?'creative':'uni_task_center';
    fields.set('uniDetail',JSON.stringify(detail));u.hash=fields.toString();
  }
  if(!await hybridCheckpoint(config,session,policy))throw Error('读取范围已改变，未刷新');
  const before=await chrome.tabs.get(tab.id),group=await chrome.tabGroups.get(before.groupId);
  if(before.url!==tab.url||group.title!==c.group)throw Error('标签或分组已变化，未刷新原页面');
  if(!recover){const guard=await hybridInject(tab.id,'identity',c);if(guard.result?.editor_open)throw Error('当前页面有编辑窗口，保留输入'+(guard.result.editor_summary?'（'+guard.result.editor_summary+'）':''));}
  if(recover)await chrome.storage.session.set({[recoveryKey]:{run_id:c.run_id,phase:c.phase,cycle:c.recovery_cycle||0,at:Date.now()}});
  if(u.href!==tab.url)await chrome.tabs.update(tab.id,{url:u.href});
  else await chrome.tabs.reload(tab.id,{bypassCache:true});
  // A routed page may redirect to the current Chengfang layout. All subsequent
  // work revalidates its actual URL and visible account/parent labels.
  const deadline=Date.now()+45000;let waiting='等待页面加载';
  while(Date.now()<deadline){
    if(!await hybridCheckpoint(config,session,policy))throw Error('读取范围已改变');
    const fresh=await hybridTab(tab.id,c,policy);
    if(fresh?.status==='complete'){
      const state=await hybridInject(tab.id,'identity',c);
      if(state.result?.ok&&state.result.editor_open!==true){
        const value={key:visitKey,documentId:state.documentId};hybridPageVisits.set(tab.id,value);
        await chrome.storage.session.set({[visitStorageKey]:value});return tab.id;
      }
      waiting=state.result?.message||waiting;
    }
    await new Promise(r=>setTimeout(r,250));
  }
  throw Error('等待本户详情加载，未改投放：'+waiting);
}
async function hybridRead(tabId,c,config,session,policy){
  let last='等待页面加载';
  const deadline=Date.now()+45000;
  while(Date.now()<deadline){
    if(!await hybridCheckpoint(config,session,policy)||!await hybridTab(tabId,c,policy))throw Error('网页读取身份已变化');
    const view=await hybridInject(tabId,'view',c);
    if(view.result?.ok&&view.result.changed===false){
      if(c.action?.intent?.browser_batch?.selection_source==='official_api'&&['prepare','dry_prepare'].includes(c.phase)){
        // The server has already scanned the API metric range. Selection in
        // prepare searches only the immutable IDs, never walks the table.
        return {document_id:view.documentId};
      }
      const page=await hybridInject(tabId,'page',c);
      if(page.result?.ok&&page.result.changed===false){
        const read=await hybridInject(tabId,c.rule_job?'rule_read':'read',c);
        if(read.result?.ok){
          const obs=read.result.observation;
          if(c.phase!=='tasks'||obs.rows.every(row=>row.status==='调控中'))return {...obs,tab_id:tabId,document_id:read.documentId};
          last='等待调控中筛选结果加载';
        }
        last=read.result?.message||last;
      }else last=page.result?.message||last;
    }else last=view.result?.message||last;
    await new Promise(r=>setTimeout(r,300));
  }
  throw Error(last);
}
async function hybridFinalCheck(message,sender){
  const p=hybridPermit;
  if(!p||p.checking||p.consumed||message.id!==p.c.action.id||message.nonce!==p.nonce||sender.id!==chrome.runtime.id
    ||sender.tab?.id!==p.tabId||sender.frameId!==0||sender.documentId!==p.documentId)return {allow:false};
  p.checking=true;
  try{
    if(!await hybridCheckpoint(p.config,p.session,p.policy,true)||!await hybridTab(p.tabId,p.c,p.policy))return {allow:false};
    const result=await hybridPost('worker-final',{run_id:p.c.run_id,action_id:p.c.action.id,submit_token:p.submit_token},p.config);
    if(hybridPermit!==p||result.allow!==true||!await hybridCheckpoint(p.config,p.session,p.policy,true)||!await hybridTab(p.tabId,p.c,p.policy))return {allow:false};
    p.consumed=true;return {allow:true};
  }finally{p.checking=false;}
}
function hybridFormObservation(form,tabId){
  const v=form.result.observation;
  return v.entry_source==='home_plus'?{...v,home:{...v.home,tab_id:tabId,document_id:form.documentId}}:{...v,tab_id:tabId,document_id:form.documentId};
}
async function hybridPrepareForm(tabId,c,observation,config,session,policy){
  if(c.preferred_entry!=='home_plus'||c.action?.intent?.browser_batch?.selection_source!=='official_api')return hybridInject(tabId,'prepare',c,{},observation.document_id);
  const pre=await hybridInject(tabId,'prepare',c,{preflight_only:true},observation.document_id);
  if(pre.result?.preflight_ready!==true)throw Error(pre.result?.message||'本批素材状态核对未完成');
  const cleaned=await hybridInject(tabId,'cleanup',c,{},pre.documentId);
  if(cleaned.result?.ok!==true)throw Error('首页跳转前的查看条件尚未清理');
  const detailForm=async(documentId,reason)=>{
    const form=await hybridInject(tabId,'prepare',c,{},documentId);form.result.entry_diagnostic=reason;return form;
  };
  if(pre.result.home_supported!==true)return detailForm(pre.documentId,'详情备用：抖音号标识'+(pre.result.home_diagnostic?.owner_count??'未知')+'个、推直播间入口'+(pre.result.home_diagnostic?.link_count??'未知')+'个');
  const home={command_id:c.id,binding:{...pre.result.binding,tab_id:tabId,document_id:pre.documentId},
    preflight:{...pre.result.observation,tab_id:tabId,document_id:pre.documentId}};
  hybridHomeContexts.set(tabId,home);
  const go=await hybridInject(tabId,'home_go',c,{},pre.documentId);
  if(go.result?.fallback===true){hybridHomeContexts.delete(tabId);return detailForm(pre.documentId,'详情备用：'+go.result.message);}
  if(go.result?.navigated!==true)throw Error(go.result?.message||'尚未返回推直播间首页');
  let reached=false;const deadline=Date.now()+20000;
  while(Date.now()<deadline){
    if(!await hybridCheckpoint(config,session,policy,c.phase==='prepare'))throw Error('首页追投范围或执行模式已变化');
    const tab=await hybridTab(tabId,c,policy);
    if(tab?.status==='complete'&&['/uni-prom','/overall-prom'].includes(new URL(tab.url).pathname)){reached=true;break;}
    await new Promise(r=>setTimeout(r,200));
  }
  if(!reached)throw Error('原标签尚未回到本户首页，未提交');
  const opened=await hybridInject(tabId,'home_open',c);
  if(opened.result?.fallback===true){
    // Only an unopened, unambiguous failure may use the old entry. An open
    // editor, lost click or unknown submit never falls through to a new task.
    const detail=await hybridLocate(c,config,session,policy);hybridHomeContexts.delete(tabId);
    if(detail!==tabId)throw Error('备用入口不能更换原标签');
    const read=await hybridRead(tabId,c,config,session,policy);
    return detailForm(read.document_id,'详情备用：'+opened.result.message);
  }
  if(opened.result?.opened!==true)throw Error(opened.result?.message||'首页空追投窗口未打开');
  home.proof=opened.result.home;
  return hybridInject(tabId,'prepare',c,{},opened.documentId);
}
async function hybridProbe(c,config,session,policy){
 if(c.phase!=='dom_probe'||c.preview!==true||c.action||!c.probe)throw Error('诊断任务不能包含广告动作');
 if(!policy.accounts.some(a=>a.id===c.account_id&&a.scene==='live'&&a.tab_group===c.group))throw Error('只读诊断账户未登记');
 const tabs=[];
 for(const t of await chrome.tabs.query({url:'https://qianchuan.jinritemai.com/*'})){
  const u=new URL(t.url);
  if(u.origin!=='https://qianchuan.jinritemai.com'||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==c.account_id||!['/uni-prom','/overall-prom','/uni-prom/detail','/overall-prom/detail'].includes(u.pathname))continue;
  if(u.pathname.endsWith('/detail')&&(u.searchParams.getAll('adId').length!==1||u.searchParams.get('adId')!==c.plan_id))continue;
  if((await chrome.tabGroups.get(t.groupId)).title===c.group)tabs.push(t);
 }
 const exact=tabs.filter(t=>new URL(t.url).searchParams.get('adId')===c.plan_id),tab=exact.length===1?exact[0]:tabs.length===1?tabs[0]:null;
 if(!tab)throw Error('只读诊断需有本户唯一原标签，不新开浏览器');
 const scope=async()=>{
  if(!await hybridCheckpoint(config,session,policy))throw Error('只读诊断读取范围已变化');
  const fresh=await chrome.tabs.get(tab.id),u=new URL(fresh.url);
  if(u.origin!=='https://qianchuan.jinritemai.com'||!['/uni-prom','/overall-prom','/uni-prom/detail','/overall-prom/detail'].includes(u.pathname)||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==c.account_id||u.pathname.endsWith('/detail')&&(u.searchParams.getAll('adId').length!==1||u.searchParams.get('adId')!==c.plan_id)||(await chrome.tabGroups.get(fresh.groupId)).title!==c.group||Date.now()>=Date.parse(c.expires_at))throw Error('只读诊断标签范围已变化');
  return fresh;
 };
 const inject=async phase=>{
  await scope();const r=await chrome.scripting.executeScript({target:{tabId:tab.id,frameIds:[0]},world:'ISOLATED',injectImmediately:true,func:hybridProbeDom,args:[phase,{command:c}]});
  if(r.length!==1||r[0].frameId!==0||!r[0].documentId||r[0].result?.ok!==true)throw Error(r[0]?.result?.message||'只读结构采集未完成');
  await scope();return r[0];
 };
 if(c.probe.target==='home'){
  await inject('home');let reached=false;
  for(let n=0;n<50;n++){
   const t=await scope();if(t.status==='complete'&&['/uni-prom','/overall-prom'].includes(new URL(t.url).pathname)){reached=true;break;}
   await new Promise(r=>setTimeout(r,200));
  }
  if(!reached)throw Error('只读返回首页尚未完成');
  await new Promise(r=>setTimeout(r,1500));
 }
 const r=await inject('inspect');return {...r.result.observation,tab_id:tab.id,document_id:r.documentId};
}
async function runHybridChase(){
  if(browserControlBusy||inFlight)return false;
  browserControlBusy=true;let worked=false;
  try{
    const config=await chrome.storage.local.get(OBSERVER_KEYS),session=await browserControlSession();
    if(!session||!validObserverToken(config.token)||!await observerCheckpoint(config))return false;
    const policy=await readObserverPolicy(config.token),until=Date.now()+60000;
    for(let step=0;step<8&&Date.now()<until;step++){
      const current=await browserControlSession();
      const reply=await hybridPost('worker-next',{protocol:1,worker_id:current.worker_id,epoch:current.epoch,armed:current.armed===true,revision:policy.revision,extension_version:chrome.runtime.getManifest().version},config);
      const c=reply.command;if(!c)return worked;worked=true;
      let tabId,prepared=false,began=false;
      try{
        if(c.phase==='dom_probe'){
          const value=await hybridProbe(c,config,current,policy);
          await hybridPost('worker-result',{run_id:c.run_id,id:c.id,lease:c.lease,ok:true,message:'只读结构已回传，未填写或提交广告',observation:value},config);continue;
        }
        tabId=await hybridLocate(c,config,current,policy);
        const observation=await hybridRead(tabId,c,config,current,policy);
        if(c.phase==='dry_prepare'){
          prepared=true;const form=await hybridPrepareForm(tabId,c,observation,config,current,policy);
          if(form.result?.prepared!==true)throw Error(form.result?.message||'表单预演未通过');
          const cleaned=await hybridInject(tabId,'cleanup',c,{},form.documentId);
          if(cleaned.result?.ok!==true)throw Error('预演后的表单或勾选尚未清理：'+(cleaned.result?.message||'等待清理结果'));
          prepared=false;
          await hybridPost('worker-result',{run_id:c.run_id,id:c.id,lease:c.lease,ok:true,message:'表单已预演并取消，未提交广告；'+(form.result.observation.entry_source==='home_plus'?'首页素材追投＋入口':form.result.entry_diagnostic||'详情入口'),
            observation:hybridFormObservation(form,tabId)},config);
        }else if(c.phase==='prepare'){
          if(!await hybridCheckpoint(config,current,policy,true))throw Error('执行模式关闭，未填写追投');
          prepared=true;const form=await hybridPrepareForm(tabId,c,observation,config,current,policy);
          if(form.result?.prepared!==true)throw Error(form.result?.message||'追投表单核验失败');
          const begin=await hybridPost('worker-begin',{run_id:c.run_id,id:c.id,lease:c.lease,action_id:c.action.id,
            observation:hybridFormObservation(form,tabId)},config);
          if(begin.submit_once!==true)throw Error('未取得本批提交许可');began=true;
          hybridPermit={c,tabId,documentId:form.documentId,nonce:form.result.nonce,submit_token:begin.submit_token,config,session:current,policy,consumed:false,checking:false};
          const result=await hybridInject(tabId,'submit',c,{nonce:form.result.nonce},form.documentId);
          hybridPermit=null;
          if(result.result?.submitted!==true)throw Error(result.result?.message||'网页提交结果待核验');
          // Next job only reads the original name. A lost response cannot cause
          // this create command to be issued again.
        }else{
          let value=observation;
          if(c.phase==='verify'){
            const verified=await hybridInject(tabId,'verify',c);
            if(verified.result?.ok!==true)throw Error(verified.result?.message||'原提交尚未核验');
            value={...verified.result.observation,tab_id:tabId,document_id:verified.documentId};
          }
          await hybridPost('worker-result',{run_id:c.run_id,id:c.id,lease:c.lease,ok:true,message:'已读取页面',observation:value},config);
        }
      }catch(error){
        hybridPermit=null;
        let cleaned=true,result;
        if(prepared&&tabId)try{cleaned=(await hybridInject(tabId,'cleanup',c)).result?.ok===true;}catch{cleaned=false;}
        if(!began)try{result=await hybridPost('worker-result',{run_id:c.run_id,id:c.id,lease:c.lease,ok:false,message:String(error.message).slice(0,300),observation:null},config);}catch{}
        await chrome.storage.local.set({lastHybridState:{at:new Date().toISOString(),message:began?'本批只核验原提交，不重复创建':String(error.message).slice(0,300)}});
        // A verified business filter can regroup immediately after our own
        // selection is cleared. Technical failures and uncertain submits wait.
        if(cleaned&&result?.continue_round===true&&!began)continue;
        break;
      }finally{if(tabId)hybridHomeContexts.delete(tabId);}
    }
    return worked;
  }catch{return worked;}finally{hybridPermit=null;browserControlBusy=false;}
}
