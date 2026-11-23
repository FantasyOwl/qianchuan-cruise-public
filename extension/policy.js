/* Read scope comes only from the authenticated local assistant. No platform API. */
async function readObserverPolicy(token){
  const response=await fetch('http://127.0.0.1:8877/api/collector-config',{
    headers:{'X-Monitor-Token':token},signal:AbortSignal.timeout(10000),
    redirect:'error',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'
  });
  if(!response.ok)throw new Error('本机采集配置未就绪');
  const value=await response.json();
  if(value?.protocol!==2||typeof value.revision!=='string'||!/^[0-9a-f]{64}$/.test(value.revision)||!Array.isArray(value.accounts)||value.accounts.length>100)throw new Error('采集配置格式无效');
  const ids=new Set();
  const accounts=value.accounts.map(a=>{
    if(!a||typeof a.id!=='string'||!/^[0-9]{1,30}$/.test(a.id)||ids.has(a.id)||!['live','product'].includes(a.scene)||typeof a.tab_group!=='string'||!a.tab_group.trim()||a.tab_group.length>100||/[\x00-\x1f]/.test(a.tab_group))throw new Error('账户采集配置无效');
    ids.add(a.id);return {id:a.id,scene:a.scene,tab_group:a.tab_group};
  });
  return {protocol:2,revision:value.revision,accounts};
}
async function observerScopeCheckpoint(config,policy){
  if(!await observerCheckpoint(config))return false;
  const current=await readObserverPolicy(config.token);
  return current.revision===policy.revision && await observerCheckpoint(config);
}
