/* Program-owned ID routing in the existing Chrome profile. Read-only only. */
const ACCOUNT_ENTRY_TABS='qc_account_entry_tabs_v1';
const ACCOUNT_ENTRY_MESSAGES={ready:'已按账户ID进入并核验页面，继续读取计划数据',loading:'账户页面正在加载，保留原标签继续核验',identity_unready:'账户页面身份未就绪；保留标签，不读取其他账户',editor_open:'当前页面有未保存编辑或选择，保留输入并暂停自动进入',view_switch_requested:'已切换控成本查看范围，等待原生计划列表',detail_open_requested:'已打开原生素材入口，等待真实计划ID',multiple_running_plans:'发现多个投放中的直播计划入口，请绑定要监测的计划；没有随意选择',native_entry_missing:'已进入账户，尚未找到唯一的在投直播计划素材入口',wrong_view:'当前账户查看范围尚未切换完成',tab_ambiguous:'配置分组在多个窗口中重名，无法确定账户标签窗口',scope_changed:'本户采集范围或会话已变化，已停止进入',entry_failed:'账户进入未完成，未修改投放'};
function accountEntryUrl(value,account,detailOnly=false){
 try{const u=new URL(value);if(u.origin!=='https://qianchuan.jinritemai.com'||u.username||u.password||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==account.id)return false;
  if(detailOnly)return account.scene==='live'&&u.pathname==='/uni-prom/detail'&&u.searchParams.getAll('adId').length===1&&/^\d{6,30}$/.test(u.searchParams.get('adId')||'');
  return ['/uni-prom','/uni-prom/detail','/overall-prom'].includes(u.pathname);
 }catch{return false;}
}
async function accountEntryInject(tabId,phase,account,jobId){
 const result=await chrome.scripting.executeScript({target:{tabId,frameIds:[0]},world:'ISOLATED',func:accountEntryDom,args:[phase,{account,job_id:jobId}]});
 if(result.length!==1||result[0].frameId!==0||!result[0].documentId)throw Error('entry_document_missing');return result[0];
}
async function accountEntrySameTab(tab,account){
 const now=await chrome.tabs.get(tab.id),group=await chrome.tabGroups.get(now.groupId);
 return now.url===tab.url&&accountEntryUrl(now.url,account)&&group.title===account.tab_group;
}
async function accountEntryEnsureTab(config,policy,account,target,jobId){
 if(!accountEntryUrl(target,account)||!await observerScopeCheckpoint(config,policy))return {code:'scope_changed'};
 const groups=(await chrome.tabGroups.query({title:account.tab_group})).filter(g=>g.title===account.tab_group);
 if(groups.length>1)return {code:'tab_ambiguous'};
 const saved=(await chrome.storage.local.get(ACCOUNT_ENTRY_TABS))[ACCOUNT_ENTRY_TABS]||{},record=saved[account.id];
 let tab=null;
 if(record?.group===account.tab_group)try{const candidate=await chrome.tabs.get(record.tab_id);
  if(!accountEntryUrl(candidate.url,account))return {code:'identity_unready',tab:candidate};
  if(candidate.groupId===groups[0]?.id)tab=candidate;
  else if(candidate.groupId===-1){
   if(!await observerScopeCheckpoint(config,policy))return {code:'scope_changed',tab:candidate};
   const gid=await chrome.tabs.group({tabIds:[candidate.id],...(groups.length?{groupId:groups[0].id}:{})});
   if(!groups.length){if(!await observerScopeCheckpoint(config,policy))return {code:'scope_changed',tab:candidate};await chrome.tabGroups.update(gid,{title:account.tab_group});}
   return {code:'loading',tab:await chrome.tabs.get(candidate.id)};
  }else return {code:'scope_changed',tab:candidate};
 }catch{}
 if(!tab){
  const candidates=(await chrome.tabs.query({url:'https://qianchuan.jinritemai.com/*'})).filter(t=>accountEntryUrl(t.url,account)&&groups.length===1&&t.groupId===groups[0].id);
  candidates.sort((a,b)=>Number(new URL(b.url).pathname==='/uni-prom/detail')-Number(new URL(a.url).pathname==='/uni-prom/detail')||(b.lastAccessed||0)-(a.lastAccessed||0));tab=candidates[0]||null;
 }
 if(!await observerScopeCheckpoint(config,policy))return {code:'scope_changed'};
 if(!tab){
  tab=await chrome.tabs.create({url:target,active:false,...(groups.length?{windowId:groups[0].windowId}:{})});
  saved[account.id]={tab_id:tab.id,group:account.tab_group};await chrome.storage.local.set({[ACCOUNT_ENTRY_TABS]:saved});
  if(!await observerScopeCheckpoint(config,policy))return {code:'scope_changed',tab};
  const groupId=await chrome.tabs.group({tabIds:[tab.id],...(groups.length?{groupId:groups[0].id}:{})});
  if(!groups.length){if(!await observerScopeCheckpoint(config,policy))return {code:'scope_changed',tab};await chrome.tabGroups.update(groupId,{title:account.tab_group});}
  return {code:'loading',tab:await chrome.tabs.get(tab.id)};
 }
 saved[account.id]={tab_id:tab.id,group:account.tab_group};await chrome.storage.local.set({[ACCOUNT_ENTRY_TABS]:saved});
 const group=await chrome.tabGroups.get(tab.groupId);if(group.title!==account.tab_group)return {code:'scope_changed',tab};
 const check=await accountEntryInject(tab.id,'guard',account,jobId);
 if(!await observerScopeCheckpoint(config,policy))return {code:'scope_changed',tab};
 if(!await accountEntrySameTab(tab,account))return {code:'scope_changed',tab};
 if(check.result?.code!=='navigation_ready')return {code:check.result?.code||'loading',tab,documentId:check.documentId};
 return {code:'navigation_ready',tab,documentId:check.documentId};
}
async function accountEntryRun(job,config,policy){
 const account=policy.accounts.find(a=>a.id===job.account_id);if(!account||!accountEntryUrl(job.url,account))return;
 let code='entry_failed',tab=null,documentId='',planId=null,verifiedUrl=null,diagnostics={};
 try{
  const ensured=await accountEntryEnsureTab(config,policy,account,job.url,job.id);tab=ensured.tab||null;code=ensured.code;documentId=ensured.documentId||'';
  if(code==='navigation_ready'){
   const current=new URL(tab.url),rightView=account.scene==='live'?['/uni-prom','/uni-prom/detail'].includes(current.pathname):current.pathname==='/overall-prom';
   if(!rightView){if(!await observerScopeCheckpoint(config,policy)||!await accountEntrySameTab(tab,account))return;await chrome.tabs.update(tab.id,{url:job.url});code='loading';}
   else{
    if(!await observerScopeCheckpoint(config,policy)||!await accountEntrySameTab(tab,account))return;
    const read=await accountEntryInject(tab.id,'discover',account,job.id);documentId=read.documentId;code=read.result?.code||'entry_failed';diagnostics=read.result?.diagnostics||{};
    if(read.result?.next_url){
     if(!accountEntryUrl(read.result.next_url,account,true)||!await observerScopeCheckpoint(config,policy)||!await accountEntrySameTab(tab,account))return;
     await chrome.tabs.update(tab.id,{url:read.result.next_url});code='detail_open_requested';
    }
    if(code==='ready'){
     const after=await chrome.tabs.get(tab.id),group=await chrome.tabGroups.get(after.groupId);
     if(after.url!==tab.url||group.title!==account.tab_group||!accountEntryUrl(after.url,account,account.scene==='live'))code='scope_changed';
     else{verifiedUrl=after.url;planId=read.result.plan_id??null;}
    }
   }
  }
 }catch{code='entry_failed';}
 if(!await observerScopeCheckpoint(config,policy))return;
 const body={id:job.id,ok:code==='ready',message:ACCOUNT_ENTRY_MESSAGES[code]||ACCOUNT_ENTRY_MESSAGES.entry_failed,
  entry:{code,account_id:account.id,group:account.tab_group,url:verifiedUrl,plan_id:planId,captured_at:new Date().toISOString(),tab_id:tab?.id||0,document_id:documentId,diagnostics}};
 const r=await fetch('http://127.0.0.1:8877/api/cruise/worker-job-result',{method:'POST',headers:{'Content-Type':'application/json','X-Monitor-Token':config.token},body:JSON.stringify(body),signal:AbortSignal.timeout(10000),redirect:'error',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
 if(!r.ok)throw Error('entry_receipt_rejected');
}
