importScripts('access.js','policy.js','collector.js','control-dom.js','control-worker.js','cruise-dom.js','account-entry-dom.js','account-entry-worker.js','cruise-worker.js','batch-dom.js','batch-worker.js','hybrid-dom.js','hybrid-home-dom.js','hybrid-probe-dom.js','hybrid-worker.js','hybrid-rule-worker.js','live-close-dom.js','live-close-worker.js');
let inFlight = false;

async function ensureObserverSchedule(){
  const alarm=await chrome.alarms.get('observe');
  if(!alarm||alarm.periodInMinutes!==1)await chrome.alarms.create('observe',{when:Date.now()+1000,periodInMinutes:1});
}
async function boundedObserverRead(args){
  let timer;
  try{
    return await Promise.race([chrome.scripting.executeScript(args),new Promise((_,reject)=>{
      timer=setTimeout(()=>reject(Error('页面只读采集超时，继续处理其他任务')),10000);
    })]);
  }finally{if(timer)clearTimeout(timer);}
}
async function observeTick(){
  if(browserControlBusy||inFlight)return;
  // Execution jobs have their own fresh observations. A stalled dashboard
  // collector must not prevent either worker heartbeat or an API-ID preview.
  if(typeof runHybridRules==='function'&&await runHybridRules())return;
  if(typeof runHybridChase==='function'&&await runHybridChase())return;
  await collect();
  if(typeof runObserverReadJobs==='function')await runObserverReadJobs();
  await runBrowserControl();
}

function identity(tab,policy) {
  try {
    const u=new URL(tab.url),accountId=u.searchParams.get('aavid');
    const account=policy.accounts.find(a=>a.id===accountId);
    if(u.origin!=='https://qianchuan.jinritemai.com'||!account||u.searchParams.getAll('aavid').length!==1)return null;
    const scene=account.scene,path=u.pathname;
    const board=path==='/board-next'&&scene==='live';
    const manage=scene==='live'?path==='/uni-prom':path==='/overall-prom';
    const planId=u.searchParams.get('adId');
    const detail=scene==='live'&&path==='/uni-prom/detail'&&u.searchParams.getAll('adId').length===1&&/^\d{6,30}$/.test(planId||'');
    if(!board&&!manage&&!detail)return null;
    const role=board?'board':detail?'detail':scene;
    return {accountId,scene,path,role,...(detail?{planId}:{}),group:account.tab_group,slot:accountId+':'+role+(detail?':'+planId:'')};
  }catch{return null;}
}

async function post(path,data,token){
  if(!['/api/ingest','/api/heartbeat'].includes(path))throw new Error('本地接收路径不在白名单内');
  const response=await fetch(`http://127.0.0.1:8877${path}`,{
    method:'POST',headers:{'Content-Type':'application/json','X-Monitor-Token':token},
    body:JSON.stringify(data),signal:AbortSignal.timeout(10000),redirect:'error',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'
  });
  const result=await response.json();if(!response.ok)throw new Error('本机未接受采集结果');return result;
}

async function collect(){
  if(inFlight||browserControlBusy)return;inFlight=true;let config;
  try{
    config=await chrome.storage.local.get(OBSERVER_KEYS);
    if(!validObserverToken(config.token)||config.enabled!==true)return;
    if(!await observerHasAccess()){
      await chrome.storage.local.set({enabled:false,lastState:{at:new Date().toISOString(),count:0,issues:['站点权限未授予或已撤回，请在扩展弹窗重新确认']}});return;
    }
    const policy=await readObserverPolicy(config.token),groups=new Map();
    for(const title of new Set(policy.accounts.map(a=>a.tab_group))){
      for(const group of await chrome.tabGroups.query({title}))if(group.title===title)groups.set(group.id,title);
    }
    const tabs=(await chrome.tabs.query({url:'https://qianchuan.jinritemai.com/*'}))
      .filter(t=>{const id=identity(t,policy);return id&&groups.get(t.groupId)===id.group;})
      .sort((a,b)=>(b.lastAccessed||0)-(a.lastAccessed||0));
    const roles=new Set(),bundles=[],issues=[];
    for(const original of tabs){
      const id=identity(original,policy);if(roles.has(id.slot))continue;
      try{
        if(!await observerScopeCheckpoint(config,policy))return;
        const fresh=await chrome.tabs.get(original.id),current=identity(fresh,policy),group=await chrome.tabGroups.get(fresh.groupId);
        if(group.title!==id.group||!current||current.slot!==id.slot||current.path!==id.path){issues.push('标签分组或账户变化，跳过');continue;}
        if(!await observerScopeCheckpoint(config,policy))return;
        const results=await boundedObserverRead({target:{tabId:fresh.id},func:collectVisibleQianchuan,args:[current]});
        roles.add(current.slot);
        const data=results[0]?.result;
        const snapshots=Array.isArray(data?.snapshots)?data.snapshots:data?.snapshot?[data.snapshot]:[];
        if(snapshots.length)bundles.push({tabId:fresh.id,identity:current,snapshots});
        else issues.push(`${id.accountId}/${id.role}: ${data?.error||'未得到可识别数据'}`);
        if(data?.diagnostics?.issues)issues.push(...data.diagnostics.issues);
      }catch{issues.push(`${id.accountId}/${id.role}: 页面关闭或读取失败`);}
    }
    if(!await observerScopeCheckpoint(config,policy))return;
    const snapshots=[];
    for(const bundle of bundles){
      try{
        const tab=await chrome.tabs.get(bundle.tabId),current=identity(tab,policy),group=await chrome.tabGroups.get(tab.groupId);
        if(group.title===bundle.identity.group&&current&&current.slot===bundle.identity.slot&&current.path===bundle.identity.path)snapshots.push(...bundle.snapshots);
        else {roles.delete(bundle.identity.slot);issues.push('读取后页面离开原白名单或分组，丢弃本轮结果');}
      }catch{roles.delete(bundle.identity.slot);issues.push('读取后页面已关闭，丢弃本轮结果');}
    }
    for(const a of policy.accounts)for(const role of a.scene==='live'?['live','board']:['product']){
      const hasDetail=role==='live'&&[...roles].some(slot=>slot.startsWith(a.id+':detail:'));
      if(!roles.has(a.id+':'+role)&&!hasDetail)issues.push(`缺少 ${role} 页面（账户 ${a.id}，分组 ${a.tab_group}）`);
    }
    for(let i=0;i<snapshots.length;i+=100){
      if(!await observerScopeCheckpoint(config,policy))return;
      await post('/api/ingest',{snapshots:snapshots.slice(i,i+100),collector_protocol:2,policy_revision:policy.revision},config.token);
    }
    if(!await observerScopeCheckpoint(config,policy))return;
    const extensionVersion=chrome.runtime.getManifest?.().version;
    await post('/api/heartbeat',{tab_count:roles.size,issues:issues.slice(0,10),collector_protocol:2,policy_revision:policy.revision,
      browser_control_protocol:1,...(typeof extensionVersion==='string'?{extension_version:extensionVersion}:{})},config.token);
    if(!await observerScopeCheckpoint(config,policy))return;
    await chrome.storage.local.set({lastState:{at:new Date().toISOString(),count:snapshots.length,configuredAccounts:policy.accounts.length,issues:issues.slice(0,10)}});
    await chrome.action.setBadgeText({text:issues.length?'!':snapshots.length>999?'999+':String(snapshots.length)});
    await chrome.action.setBadgeBackgroundColor({color:issues.length?'#96612b':'#136b77'});
  }catch{
    if(config&&!await observerCheckpoint(config))return;
    await chrome.storage.local.set({lastState:{at:new Date().toISOString(),count:0,issues:['本地配置、接收或页面读取未完成，请查看本机8877接入状态']}});
    await chrome.action.setBadgeText({text:'离线'});
  }finally{inFlight=false;}
}

chrome.runtime.onInstalled.addListener(async()=>{
  const value=await chrome.storage.local.get(['enabled']);
  if(typeof value.enabled!=='boolean')await chrome.storage.local.set({enabled:false,epoch:observerEpoch()});
  await chrome.alarms.create('observe',{periodInMinutes:1});
});
chrome.runtime.onStartup.addListener(async()=>{await chrome.alarms.create('observe',{periodInMinutes:1});});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='observe')return observeTick().catch(()=>{});else if(/^live-close-[0-9a-f]{32}$/.test(alarm.name))return runBrowserControl().catch(()=>{});});
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(message?.action==='hybrid-wait'){
    if(typeof hybridWait!=='function'){sendResponse({ok:false});return;}
    hybridWait(message,sender).then(sendResponse).catch(()=>sendResponse({ok:false}));return true;
  }
  if(message?.action==='hybrid-final-check'){hybridFinalCheck(message,sender).then(sendResponse).catch(()=>sendResponse({allow:false}));return true;}
  if(message?.action==='rule-final-check'){hybridRuleFinalCheck(message,sender).then(sendResponse).catch(()=>sendResponse({allow:false}));return true;}
  if(message?.action==='live-close-final-check'){
    liveCloseFinalCheck(message,sender).then(sendResponse).catch(()=>sendResponse({allow:false}));return true;
  }
  if(message?.action==='batch-final-check'){
    batchFinalCheck(message,sender).then(sendResponse).catch(()=>sendResponse({allow:false}));return true;
  }
  if(message?.action==='cruise-final-check'){
    cruiseControlFinalCheck(message,sender).then(sendResponse).catch(()=>sendResponse({allow:false}));return true;
  }
  if(message?.action==='control-final-check'){
    browserControlFinalCheck(message,sender).then(sendResponse).catch(()=>sendResponse({allow:false}));return true;
  }
  if(sender.id!==chrome.runtime.id||sender.url!==chrome.runtime.getURL('popup.html'))return;
  if(message.action==='collect'){collect().then(()=>sendResponse({ok:true}));return true;}
  if(message.action==='control-start'){
    setBrowserControlArmed(true).then(result=>{sendResponse(result);runBrowserControl();}).catch(()=>sendResponse({ok:false}));return true;
  }
  if(message.action==='control-stop'){
    setBrowserControlArmed(false).then(sendResponse).catch(()=>sendResponse({ok:false}));return true;
  }
  if(message.action==='control-check'){runBrowserControl().then(()=>sendResponse({ok:true}));return true;}
});
// The worker can restart without onInstalled/onStartup (including unpacked
// extension reloads). Recreate a dropped alarm at every worker initialization.
ensureObserverSchedule().catch(()=>{});
