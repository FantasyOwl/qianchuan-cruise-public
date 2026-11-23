const token = document.querySelector('#token');
const status = document.querySelector('#status');
async function show(){
  const value=await chrome.storage.local.get(['token','enabled','lastState']);
  token.value=value.token||'';
  const access=await observerHasAccess();
  status.textContent=value.enabled&&access?'只读采集已开启':value.enabled?'站点访问未授权，采集未运行':'采集已暂停';
  document.querySelector('#details').textContent=value.lastState?JSON.stringify(value.lastState,null,2):'尚无采集记录';
  const session=(await chrome.storage.session.get('qc_control_session_v1')).qc_control_session_v1;
  const last=(await chrome.storage.local.get('lastControlState')).lastControlState;
  const labels={waiting_for_single_approval:'等待8877确认单或已授权F5任务',paused:'已暂停',verified:'刷新后设置已核对',unknown:'结果不明，停止后续执行',blocked_unknown:'有结果不明的动作待核对',rejected:'提交前未通过核对',control_round_incomplete:'本轮核对或回读未完成'};
  document.querySelector('#control-status').textContent=(session?.armed?'执行模式已开启':'执行模式关闭')+(last?'；'+(session?.armed?'':'上次状态：')+(labels[last.code]||'等待检查'):'');
  if(value.enabled&&access)await checkLocalConnection();
}
async function ensureObserverAlarm(){
  const alarm=await chrome.alarms.get('observe');
  if(alarm?.periodInMinutes===1)return false;
  // Chrome can drop an unpacked extension's alarms on reload. Restore its
  // existing reader schedule without touching the execution session switch.
  await chrome.alarms.create('observe',{when:Date.now()+1000,periodInMinutes:1});
  return true;
}
document.querySelector('#save').addEventListener('click',async()=>{
  const pairingCode=token.value.trim();
  if(!validObserverToken(pairingCode)){status.textContent='配对码格式不正确';return;}
  try{
    const granted=await chrome.permissions.request({origins:[...READONLY_HOSTS]});
    if(!granted){await chrome.storage.local.set({enabled:false,epoch:observerEpoch()});status.textContent='未授权站点访问，采集保持暂停';return;}
    await chrome.runtime.sendMessage({action:'control-stop'});
    await chrome.storage.local.set({token:pairingCode,enabled:true,epoch:observerEpoch()});
    status.textContent='正在读取指定页面…';
    await chrome.runtime.sendMessage({action:'collect'});
    await show();
  }catch{status.textContent='授权或本地连接未完成，请查看本机接入页';}
});
document.querySelector('#stop').addEventListener('click',async()=>{
  await chrome.storage.local.set({enabled:false,epoch:observerEpoch()});
  await chrome.runtime.sendMessage({action:'control-stop'});await show();
  status.textContent='采集已暂停，不再启动新读取；已发送的本地请求可能已完成。';
});
document.querySelector('#control-enable').addEventListener('click',async()=>{
  if(!document.querySelector('#control-consent').checked){document.querySelector('#control-status').textContent='请先核对执行模式说明并勾选';return;}
  const result=await chrome.runtime.sendMessage({action:'control-start'});
  if(!result?.ok){document.querySelector('#control-status').textContent='需先开启只读采集并确认站点访问；执行未开启';return;}
  await show();
});
document.querySelector('#control-stop').addEventListener('click',async()=>{await chrome.runtime.sendMessage({action:'control-stop'});await show();});
async function checkLocalConnection(){
  const out=document.querySelector('#control-status');out.textContent='正在检查本机连接…';
  try{
    const value=await chrome.storage.local.get(['token','enabled','control_worker_id']);
    if(!value.enabled||!validObserverToken(value.token)||!await observerHasAccess())throw Error('请先开启只读采集并确认已有站点访问');
    const restored=await ensureObserverAlarm();
    const session=(await chrome.storage.session.get('qc_control_session_v1')).qc_control_session_v1;
    const options={headers:{'X-Monitor-Token':value.token},signal:AbortSignal.timeout(10000),credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer'};
    const response=await fetch('http://127.0.0.1:8877/api/collector-config',options);
    if(!response.ok)throw Error('本机采集配置连接失败（HTTP '+response.status+'）');
    const policy=await response.json();if(policy?.protocol!==2||typeof policy.revision!=='string')throw Error('本机采集配置格式不符');
    const version=chrome.runtime.getManifest().version;
    const worker_id=session?.worker_id||value.control_worker_id||crypto.randomUUID();
    const result=await fetch('http://127.0.0.1:8877/api/cruise/hybrid/worker-ping',{
      ...options,method:'POST',headers:{'Content-Type':'application/json','X-Monitor-Token':value.token},
      body:JSON.stringify({protocol:1,worker_id,epoch:session?.epoch||'popup-'+crypto.randomUUID(),armed:session?.armed===true,revision:policy.revision,extension_version:version})});
    if(!result.ok)throw Error('本机执行桥连接失败（HTTP '+result.status+'）');
    const reply=await result.json();if(reply.ok!==true||reply.ad_changes!==0)throw Error('本机连接检查返回不符');
    out.textContent=(reply.armed?'执行模式已开启':'执行模式关闭')+'；本机连接正常（扩展'+reply.extension_version+'）'+(restored?'；已恢复定时读取':'；定时读取已设置');
  }catch(error){out.textContent='连接检查失败：'+String(error.message||'请检查本机8877服务');}
}
document.querySelector('#control-check').addEventListener('click',checkLocalConnection);
show();
