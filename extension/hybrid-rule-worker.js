/* F1-F4/F6 share the same tab/session mutex as F5. No second browser and no
 * platform network transport. A lost submit response only creates read jobs. */
let hybridRulePermit=null;
async function rulePost(name,body,config){
  if(!['next','result','begin','final'].includes(name)||!validObserverToken(config.token))throw Error('规则通信范围无效');
  const response=await fetch('http://127.0.0.1:8877/api/cruise/hybrid-rules/worker-'+name,{method:'POST',
    headers:{'Content-Type':'application/json','X-Monitor-Token':config.token},body:JSON.stringify(body),
    signal:AbortSignal.timeout(15000),redirect:'error',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
  const reply=await response.json();if(!response.ok)throw Error(reply.error||'规则网页请求失败');return reply;
}
async function hybridRuleFinalCheck(message,sender){
  const p=hybridRulePermit;
  if(!p||p.checking||p.consumed||message.id!==p.c.action.id||message.nonce!==p.nonce||sender.id!==chrome.runtime.id||sender.tab?.id!==p.tabId||sender.frameId!==0||sender.documentId!==p.documentId)return {allow:false};
  p.checking=true;
  try{
    if(!await hybridCheckpoint(p.config,p.session,p.policy,true)||!await hybridTab(p.tabId,p.c,p.policy))return {allow:false};
    const reply=await rulePost('final',{run_id:p.c.run_id,action_id:p.c.action.id,submit_token:p.token},p.config);
    if(hybridRulePermit!==p||reply.allow!==true||!await hybridCheckpoint(p.config,p.session,p.policy,true)||!await hybridTab(p.tabId,p.c,p.policy))return {allow:false};
    p.consumed=true;return {allow:true};
  }finally{p.checking=false;}
}
async function runHybridRules(){
  if(browserControlBusy||inFlight)return false;
  browserControlBusy=true;let worked=false;
  try{
    const config=await chrome.storage.local.get(OBSERVER_KEYS),session=await browserControlSession();
    if(!session||!validObserverToken(config.token)||!await observerCheckpoint(config))return false;
    const policy=await readObserverPolicy(config.token),until=Date.now()+60000;
    for(let n=0;n<6&&Date.now()<until;n++){
      const current=await browserControlSession(),reply=await rulePost('next',{protocol:1,worker_id:current.worker_id,epoch:current.epoch,armed:current.armed===true,revision:policy.revision,extension_version:chrome.runtime.getManifest().version},config),c=reply.command;
      if(!c)return worked;worked=true;let tabId,prepared=false,began=false;
      try{
        tabId=await hybridLocate(c,config,current,policy);
        const observation=await hybridRead(tabId,c,config,current,policy);
        if(c.rule_phase==='dry_prepare'){
          const form=await hybridInject(tabId,'rule_prepare',c,{},observation.document_id);prepared=true;
          if(form.result?.prepared!==true)throw Error(form.result?.message||'原任务预演表单未核对一致');
          await hybridInject(tabId,'rule_cleanup',c,{},form.documentId);prepared=false;
          await rulePost('result',{run_id:c.run_id,id:c.id,lease:c.lease,ok:true,message:'原任务表单已核对并取消',
            observation:{...form.result.observation,tab_id:tabId,document_id:form.documentId}},config);
        }else if(c.rule_phase==='prepare'){
          if(!await hybridCheckpoint(config,current,policy,true))throw Error('网页执行已关闭');
          const form=await hybridInject(tabId,'rule_prepare',c,{},observation.document_id);prepared=true;
          if(form.result?.prepared!==true)throw Error(form.result?.message||'原任务表单核验失败');
          const begin=await rulePost('begin',{run_id:c.run_id,id:c.id,lease:c.lease,action_id:c.action.id,
            observation:{...form.result.observation,tab_id:tabId,document_id:form.documentId}},config);
          if(begin.submit_once!==true)throw Error('未取得规则提交许可');began=true;
          hybridRulePermit={c,tabId,documentId:form.documentId,nonce:form.result.nonce,token:begin.submit_token,config,session:current,policy,checking:false,consumed:false};
          const result=await hybridInject(tabId,'rule_submit',c,{nonce:form.result.nonce},form.documentId);
          hybridRulePermit=null;
          if(result.result?.submitted!==true)throw Error(result.result?.message||'原任务结果待独立核验');
        }else await rulePost('result',{run_id:c.run_id,id:c.id,lease:c.lease,ok:true,message:'已读取原任务',observation},config);
      }catch(error){
        hybridRulePermit=null;
        if(prepared&&tabId)try{await hybridInject(tabId,'rule_cleanup',c);}catch{}
        if(!began)try{await rulePost('result',{run_id:c.run_id,id:c.id,lease:c.lease,ok:false,message:String(error.message).slice(0,300),observation:null},config);}catch{}
        break;
      }
    }
    return worked;
  }catch{return worked;}finally{hybridRulePermit=null;browserControlBusy=false;}
}
