/* Ego resident round. All business commands come from the existing action ledger.
 * Node holds local credentials and final permits; the website never receives them.
 */
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createHash } from 'node:crypto';
import {resolveAccountPage} from './pages.mjs';
import {nativeMaterialSearch,nativeInventoryHover,nativeTaskSearch,nativeTaskStateFilter} from './native-search.mjs';
import {actorRoute,actorExpression} from './actor-source.mjs';
import {workerPaths,requireAccount,detailPath,requireSpace} from './worker-context.mjs';
import {nativeDetailScope} from './native-route.mjs';
import {openBoundHome} from './home-entry.mjs';
import {ensureResidentSpace} from './resident-space.mjs';
import {waitCheckpoint} from './wait-checkpoint.mjs';

const root=globalThis.__qcxReleaseRoot||fileURLToPath(new URL('../../',import.meta.url));
const paths=workerPaths(root,globalThis.__qcxWorkerConfigPath);
const privateConfig=JSON.parse(await fs.readFile(paths.configPath,'utf8'));
requireAccount(paths,privateConfig);
const statePath=paths.statePath;
let state;try{state=JSON.parse(await fs.readFile(statePath,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;state={pages:{},visits:{}};}
const save=()=>fs.writeFile(statePath,JSON.stringify(state,null,2),'utf8');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function post(operation,body){
  const r=await fetch(privateConfig.base+'/api/ego-executor/worker/'+operation,{method:'POST',headers:{'Content-Type':'application/json','X-Qianchuan-Ego':privateConfig.token},body:JSON.stringify(body),redirect:'error',signal:AbortSignal.timeout(15000)});
  const value=await r.json();if(!r.ok)throw Error(value.error||'Ego 本机连接未完成');return value;
}
const event=(message,extra={})=>post('event',{message,...extra});
const actorSources=globalThis.__qcxReleaseActors;
const domSource=actorSources?.detail??(await fs.readFile(path.join(root,'extension/hybrid-audience-dom.js'),'utf8')+'\n'+await fs.readFile(path.join(root,'extension/hybrid-dom.js'),'utf8'));
const homeSource=actorSources?.home??await fs.readFile(path.join(root,'extension/hybrid-home-dom.js'),'utf8');
const probeSource=actorSources?.probe??await fs.readFile(path.join(root,'extension/hybrid-probe-dom.js'),'utf8');
let task,page,c,kind,began=false,prepared=false,home=null,permit=null;
const lease=()=>({run_id:c.run_id,id:c.id,lease:c.lease});
const gateway=(operation,body)=>post(operation,{kind,body});
async function checkpoint(){return gateway('checkpoint',lease());}
const checkWait=waitCheckpoint(checkpoint);

async function documentBinding(){
  const tree=await page.cdp('Page.getFrameTree');const frame=tree.frameTree.frame;
  if(!frame.id||!frame.loaderId)throw Error('Ego 页面文档身份未加载');
  const tab_id=parseInt(createHash('sha256').update(frame.id).digest('hex').slice(0,12),16);
  const world=await page.cdp('Page.createIsolatedWorld',{frameId:frame.id,worldName:'qcx_ego_executor_v1'});
  return {tab_id,document_id:frame.loaderId,contextId:world.executionContextId};
}
async function actor(phase,args={}){
  if(c?.delivery_entry)await page.evaluate(nativeDetailScope,{aid:c.account_id,pid:c.plan_id,entry:c.delivery_entry,repair:true});
  state.trace=(state.trace||[]).slice(-59);state.trace.push({phase,at:new Date().toISOString(),account_id:c.account_id,run_id:c.run_id,action_id:c.action?.id});await save();
  const bind=await documentBinding();
  await page.cdp('Runtime.enable');
  const binding='qcxEgoPortV1';
  await page.cdp('Runtime.addBinding',{name:binding,executionContextId:bind.contextId});
  const bootstrap=`(()=>{if(!globalThis.__qcEgoRpc){let seq=0;const pending=new Map();globalThis.__qcEgoRpc={pending,send(message){return new Promise((resolve,reject)=>{const id=String(++seq);pending.set(id,{resolve,reject});${binding}(JSON.stringify({id,message}));});},reply(id,value){const p=pending.get(id);if(p){pending.delete(id);p.resolve(value);}}};}globalThis.__qcEgoChrome={runtime:{sendMessage:message=>globalThis.__qcEgoRpc.send(message)}};})()`;
  await page.cdp('Runtime.evaluate',{expression:bootstrap,contextId:bind.contextId,returnByValue:true});
  const isHome=actorRoute(phase).source==='home';
  const request={command:c,...(home?{home_context:home.binding,detail_preflight:home.preflight,home_proof:home.proof}:{}),...args,...(isHome?{binding:home?.binding}:{})};
  const expression=actorExpression(phase,{detail:domSource,home:homeSource,probe:probeSource},request);
  const callKey='actor_'+Date.now()+'_'+Math.random().toString(16).slice(2);
  const install=`(()=>{globalThis.__qcEgoActorResults??=Object.create(null);const key=${JSON.stringify(callKey)};globalThis.__qcEgoActorResults[key]={done:false};${expression}.then(value=>globalThis.__qcEgoActorResults[key]={done:true,value},error=>globalThis.__qcEgoActorResults[key]={done:true,error:String(error.message||error)});})()`;
  const launched=await page.cdp('Runtime.evaluate',{expression:install,contextId:bind.contextId,returnByValue:true});
  if(launched.exceptionDetails)throw Error(launched.exceptionDetails.exception?.description||launched.exceptionDetails.text);
  const until=Math.min(Date.now()+105000,Date.parse(c.expires_at)+5000);let value;
  while(Date.now()<until){
    for(const e of await page.events()){
      if(e.method!=='Runtime.bindingCalled'||e.params?.name!==binding||e.params.executionContextId!==bind.contextId)continue;
      let call;try{call=JSON.parse(e.params.payload);}catch{continue;}
      let response={ok:false,allow:false};const m=call.message;
      try{
        if(m?.action==='hybrid-wait'&&m.run_id===c.run_id&&m.command_id===c.id&&Number.isInteger(m.duration_ms)&&m.duration_ms>=1&&m.duration_ms<=600){
          await checkWait();await sleep(m.duration_ms);await checkWait();response={ok:true};
        }else if(m?.action==='hybrid-native-search'){
          response=await nativeMaterialSearch(page,c,m,checkpoint);
        }else if(m?.action==='hybrid-native-inventory-hover'){
          response=await nativeInventoryHover(page,c,m,checkpoint);
        }else if(m?.action==='hybrid-native-task-search'){
          response=await nativeTaskSearch(page,c,m,checkpoint);
        }else if(m?.action==='hybrid-native-task-state'){
          response=await nativeTaskStateFilter(page,c,m,checkpoint);
        }else if(['hybrid-final-check','rule-final-check'].includes(m?.action)&&permit&&!permit.consumed&&m.id===c.action.id&&m.nonce===permit.nonce){
          const allowed=await gateway('final',{run_id:c.run_id,action_id:c.action.id,submit_token:permit.token});
          if(allowed.allow===true){permit.consumed=true;response={allow:true};}
        }
      }catch(error){response={ok:false,allow:false,message:error.message.slice(0,250)};}
      await page.cdp('Runtime.evaluate',{expression:`globalThis.__qcEgoRpc.reply(${JSON.stringify(call.id)},${JSON.stringify(response)})`,contextId:bind.contextId,returnByValue:true});
    }
    const polled=await page.cdp('Runtime.evaluate',{expression:`globalThis.__qcEgoActorResults[${JSON.stringify(callKey)}]`,contextId:bind.contextId,returnByValue:true});
    const progress=polled.result?.value;
    if(progress?.done){
      await page.cdp('Runtime.evaluate',{expression:`delete globalThis.__qcEgoActorResults[${JSON.stringify(callKey)}]`,contextId:bind.contextId,returnByValue:true});
      if(progress.error)throw Error(progress.error);value=progress.value;break;
    }
    await sleep(20);
  }
  if(!value)throw Error('Ego 页面阶段超过命令时限，原单保留');
  return {result:value,...bind};
}
function requireOK(r){if(r.result?.ok!==true)throw Error(r.result?.message||'Ego 页面阶段未完成');return r;}
function observation(r){return {...r.result.observation,tab_id:r.tab_id,document_id:r.document_id};}
function formObservation(r){const v=r.result.observation;return v.entry_source==='home_plus'?{...v,home:{...v.home,tab_id:r.tab_id,document_id:r.document_id}}:{...v,tab_id:r.tab_id,document_id:r.document_id};}
async function pageForCommand(){
  const found=await resolveAccountPage(task,state,c.account_id);await save();
  return found;
}
async function locate(){
  page=await pageForCommand();
  const address=await page.url();let u,forceFresh=false;
  const material=c.phase==='materials'||!c.direct_home&&['prepare','dry_prepare'].includes(c.phase),start=material||c.rule_range?c.date_start:c.date_end;
  const key=[c.run_id,c.phase,c.rule_phase||'',start,c.date_end,c.document_cycle||0,c.recovery_cycle||0,c.execution_scope||''].join(':');
  if(address==='about:blank'){
    if(paths.accountId&&!detailPath(c.delivery_entry))throw Error('等待本主计划明确的API入口，不猜测全域或乘方');
    u=new URL('https://qianchuan.jinritemai.com'+(detailPath(c.delivery_entry)||'/uni-prom/detail'));
  }
  else{
    u=new URL(address);
    if(u.origin!=='https://qianchuan.jinritemai.com'||u.searchParams.get('aavid')!==c.account_id)throw Error('Ego 本户标签已被用户导航到其他账户，已保留页面');
    let guard=await actor('editor_guard');
    if(guard.result?.editor_open){
      let recovered=requireOK(await actor('editor_recover'));
      if(recovered.result.validation_rejection){state.validationRejections??={};state.validationRejections[c.action.id]=recovered.result.validation_rejection;forceFresh=true;await save();}
      if(recovered.result.cleaned)guard=await actor('editor_guard');
      if(guard.result?.editor_open&&guard.result.editor_task_name&&kind==='chase'){
        const {editor}=await gateway('editor-proof',{...lease(),task_name:guard.result.editor_task_name});
        if(editor){
          recovered=requireOK(await actor('editor_recover',{editor_proof:editor}));
          if(recovered.result.cleaned){
            state.editorRecovery={account_id:c.account_id,plan_id:c.plan_id,action_id:editor.action_id,at:new Date().toISOString()};
            await save();guard=await actor('editor_guard');
          }
        }
      }
      if(guard.result?.editor_open)throw Error('当前编辑窗口归属或内容无法核对，保留输入');
    }
    if(!['/uni-prom/detail','/overall-prom/detail'].includes(u.pathname))u.pathname=u.pathname.startsWith('/overall-prom')?'/overall-prom/detail':'/uni-prom/detail';
  }
  if(detailPath(c.delivery_entry))u.pathname=detailPath(c.delivery_entry);
  u.searchParams.set('aavid',c.account_id);u.searchParams.set('adId',c.plan_id);
  u.searchParams.set(u.pathname==='/overall-prom/detail'?'dateRange':'dr',start+','+c.date_end);
  const fields=new URLSearchParams(u.hash.slice(1).replace(/^\?/,''));
  if(u.pathname==='/overall-prom/detail')fields.set('adDetailTab',c.execution_scope==='f5_create_only'?'data':material?'creative':'uni_task_center');
  else{let detail;try{detail=JSON.parse(fields.get('uniDetail')||'{}');}catch{throw Error('Ego 原页面查看参数异常');}detail.tb=c.execution_scope==='f5_create_only'?'data':material?'creative':'uni_task_center';fields.set('uniDetail',JSON.stringify(detail));}
  u.hash='?'+fields.toString();
  const actual=new URL(address);
  const mustNavigate=forceFresh||state.visits[c.account_id]!==key||actual.searchParams.get('adId')!==c.plan_id||!actual.pathname.endsWith('/detail');
  if(mustNavigate){
    await checkpoint();
    if(address===u.href)await page.reload({timeout:30000,waitUntil:'domcontentloaded'});
    else{
      await page.goto(u.href,{timeout:30000,waitUntil:'domcontentloaded'});
      if(c.phase==='verify'||c.rule_phase==='verify')await page.reload({timeout:30000,waitUntil:'domcontentloaded'});
    }
    state.visits[c.account_id]=key;await save();
  }
  const ready=arg=>{const u=new URL(location.href);if(u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==arg.aid||u.searchParams.getAll('adId').length!==1||![arg.pid,...(arg.entry?['']:[])].includes(u.searchParams.get('adId')))return false;const ids=new Set([...document.querySelectorAll('span,div,p')].filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden').map(e=>e.innerText.replace(/\s/g,'').match(/^计划ID[：:](\d{6,30})$/)?.[1]).filter(Boolean));return ids.size===1&&ids.has(arg.pid);};
  const target={aid:c.account_id,pid:c.plan_id,entry:c.delivery_entry};
  try{await page.waitForFunction(ready,target,{timeout:18000});}
  catch(error){
    const flags=await page.evaluate(()=>({login:/登录|扫码登录|验证码/.test(document.body.innerText.slice(0,2500)),editor:[...document.querySelectorAll('[role="dialog"],[aria-modal="true"],.ovui-modal,.ovui-drawer')].some(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&[...e.querySelectorAll('input,button')].some(i=>i.getClientRects().length))}));
    if(flags.login)throw Error('登录状态需要用户确认，请在常驻 Space 登录千川后恢复浏览器控制');
    if(flags.editor)throw Error('当前页面有编辑窗口，保留输入');
    await checkpoint();await page.reload({timeout:30000,waitUntil:'domcontentloaded'});
    await page.waitForFunction(ready,target,{timeout:18000});
  }
  const normalized=await page.evaluate(nativeDetailScope,{...target,repair:true});
  if(!normalized.ready)throw Error('本户可见主计划或API入口不唯一，未修改查看网址');
  const identityDeadline=Date.now()+10000;
  for(;;){
    const guard=await actor('identity');
    if(guard.result?.ok){if(guard.result.editor_open)throw Error('当前页面有编辑窗口，保留输入');break;}
    if(!/^等待可见账户ID或新鲜API归属核验|^等待可见母计划ID/.test(guard.result?.message||'')||Date.now()>=identityDeadline)requireOK(guard);
    await checkpoint();await sleep(200);
  }
}
async function read(){
  if(c.direct_home&&['prepare','dry_prepare'].includes(c.phase))return {};
  const deadline=Math.min(Date.now()+45000,Date.parse(c.expires_at)-5000);let last='等待页面读取';
  while(Date.now()<deadline){
    await checkpoint();
    const view=await actor('view');
    if(view.result?.ok&&view.result.changed===false){
      if(c.action?.intent?.browser_batch?.selection_source==='official_api'&&['prepare','dry_prepare'].includes(c.phase))return {document_id:view.document_id,tab_id:view.tab_id};
      const p=await actor('page');
      if(p.result?.ok&&p.result.changed===false){
        const r=await actor(c.rule_job?'rule_read':c.phase==='verify'?'verify':c.direct_home&&c.phase==='tasks'?'task_inventory':'read');
        if(r.result?.ok){
          const obs=observation(r);
          if(c.rule_job&&c.rule_phase==='verify'&&c.action?.intent?.task_id){
            const target=obs.rule_rows?.find(row=>row.task_id===c.action.intent.task_id);
            // Keep only native target evidence for a failed independent read;
            // never store final permits, credentials, or the whole page here.
            state.lastRuleVerification={account_id:c.account_id,plan_id:c.plan_id,action_id:c.action.id,
              task_id:c.action.intent.task_id,at:new Date().toISOString(),page:obs.page,total:obs.total,has_next:obs.has_next,
              target:target?{task_id:target.task_id,status:target.status,running:target.running,budget:target.budget}:null};
            await save();
          }
          if(c.rule_job||c.phase!=='tasks'||obs.rows.every(x=>x.status==='调控中'))return obs;
        }
        last=r.result?.message||last;
      }else last=p.result?.message||last;
    }else last=view.result?.message||last;
    await sleep(200);
  }
  throw Error(last);
}
async function prepareForm(){
  if(c.direct_home&&c.preferred_entry!=='home_plus')throw Error('主页直投命令缺少主页入口；不回素材页预搜');
  if(c.preferred_entry!=='home_plus'||c.action?.intent?.browser_batch?.selection_source!=='official_api')return requireOK(await actor('prepare'));
  const pre=requireOK(await actor(c.direct_home?'home_identity':'prepare',{preflight_only:true}));
  if(!pre.result.preflight_ready)throw Error('API 候选素材尚未逐条核对');
  if(!c.direct_home)requireOK(await actor('cleanup'));
  if(pre.result.home_supported!==true)return requireOK(await actor('prepare'));
  home={binding:{...pre.result.binding,tab_id:pre.tab_id,document_id:pre.document_id},preflight:observation(pre)};
  const navigation=requireOK(await actor('home_go'));
  if(navigation.result.native_selector)await page.click('loc=css:'+navigation.result.native_selector,{label:'返回本户投放首页'});
  await page.waitForFunction(()=>['/uni-prom','/overall-prom'].includes(location.pathname),undefined,{timeout:10000});
  await event('正在等待首页主计划列表加载（最多60秒）',{busy:true,...(paths.accountId?{phase:'home_wait'}:{})});
  const located=await actor('home_locate');
  if(located.result?.ok!==true&&located.result?.diagnostic){state.homeLocateFailure={at:new Date().toISOString(),account_id:c.account_id,...located.result.diagnostic};await save();}
  requireOK(located);
  if(state.homeLocateFailure&&!state.homeLocateFailure.resolved_at){state.homeLocateFailure.resolved_at=new Date().toISOString();await save();}
  const opened=await openBoundHome(page,actor,located);
  if(opened.result?.opened!==true)throw Error(opened.result?.message||'首页追投表单未打开');
  home.proof=opened.result.home;
  await event('首页已找到本户主计划，正在核对本批素材并填写追投表单',{busy:true,...(paths.accountId?{phase:'prepare'}:{})});
  return requireOK(await actor('prepare'));
}

try{
  const next=await post('next',{});c=next.command;kind=next.kind;
  requireAccount(paths,privateConfig,c);
  if(c||!next.paused){
    task=await ensureResidentSpace(paths,state,next.resume_generation,{listTaskSpaces,taskSpace,claimTaskSpace,takeOverTaskSpace});
    await save();
  }
  if(c){
    if(c.execution_scope==='f5_create_only'&&(c.rule_job||!['prepare','dry_prepare'].includes(c.phase)))throw Error('本执行器仅新建追投；任务读取、核验及其他调控由API完成');
    c.executor_kind='ego';
    await event('正在处理：'+(c.group||c.account_id)+'（'+c.account_id+'） · '+(c.rule_phase||c.phase)+(c.action?.id?' · 单号'+c.action.id:''),{busy:true,connected:true,...(paths.accountId?{space_id:task.spaceId,phase:c.rule_phase||c.phase}:{} )});
    await locate();
    if(c.phase==='dom_probe'){
      if(c.probe?.target==='home'){requireOK(await actor('probe_home'));await page.waitForFunction(()=>['/uni-prom','/overall-prom'].includes(location.pathname),undefined,{timeout:10000});}
      const p=requireOK(await actor('probe_inspect'));await gateway('result',{...lease(),ok:true,message:'Ego 只读结构已回传',observation:observation(p)});
    }else{
      const obs=await read();
      const preparing=c.rule_job?['prepare','dry_prepare'].includes(c.rule_phase):['prepare','dry_prepare'].includes(c.phase);
      if(preparing){
        prepared=true;
        if(c.rule_job&&c.action?.intent?.task_id){
          // Native status edit/pause icons are display:none until the exact
          // task row is hovered. DOM clicks alone only worked by chance when
          // the human pointer happened to remain over that row.
          const target=requireOK(await actor('rule_hover'));
          await page.hover('loc=css:'+target.result.hover_selector,{label:'显示原追投任务调控按钮'});
        }
        const form=c.rule_job?requireOK(await actor('rule_prepare')):await prepareForm();
        if(!form.result.prepared)throw Error('原生表单尚未准备成功');
        const v=c.rule_job?observation(form):formObservation(form);
        if(c.preview){
          requireOK(await actor(c.rule_job?'rule_cleanup':'cleanup'));prepared=false;
          await gateway('result',{...lease(),ok:true,message:'Ego 常驻已填表并取消；'+(v.entry_source==='home_plus'?'首页素材追投＋入口':'原生详情入口'),observation:v});
        }else{
          const begin=await gateway('begin',{...lease(),action_id:c.action.id,observation:v});
          if(begin.submit_once!==true)throw Error('未取得原台账的单次提交许可');
          began=true;permit={token:begin.submit_token,nonce:form.result.nonce,consumed:false};
          const submitted=requireOK(await actor(c.rule_job?'rule_submit':'submit',{nonce:form.result.nonce}));
          if(c.rule_job&&submitted.result.rejected&&submitted.result.observation?.validation_rejection){
            state.validationRejections??={};state.validationRejections[c.action.id]=submitted.result.observation.validation_rejection;await save();
          }else if(submitted.result.submitted!==true)throw Error('原提交结果待核验，不重发');
          prepared=false;
        }
      }else{
        if(c.rule_job&&c.rule_phase==='verify'&&state.validationRejections?.[c.action?.id])obs.validation_rejection=state.validationRejections[c.action.id];
        await gateway('result',{...lease(),ok:true,message:'Ego 常驻已读取原生页面',observation:obs});
        if(c.rule_job&&c.rule_phase==='verify'&&state.validationRejections?.[c.action?.id]){delete state.validationRejections[c.action.id];await save();}
      }
    }
    await event(began?'本次仅提交一次，下一阶段独立回读原结果':'Ego 常驻阶段完成，继续等待其他账户任务',{busy:false,connected:true,error:null});
  }
  else if(!next.paused){await event('本户 Ego 空间已就绪，等待 API 候选或原单核验',{busy:false,connected:true,...(paths.accountId?{space_id:task.spaceId,phase:'waiting_api'}:{})});}
}catch(error){
  const message=String(error.message).slice(0,350);
  const suspended=/Control of this task space is with the user|EGO_USER_CONTROL|EGO_TASK_SPACE|EGO_PERMISSION|EGO_USER|login|登录|验证码|人机验证/.test(message);
  if(!suspended&&page&&/素材审核状态异步加载|素材审核状态未明确/.test(message))try{
    state.materialReadFailure={at:new Date().toISOString(),account_id:c.account_id,message,dom:await page.evaluate(()=>({
      path:location.pathname,visibility:document.visibilityState,
      inputs:[...document.querySelectorAll('input')].filter(i=>/视频名称.*ID/.test(i.placeholder)).map(i=>({value:i.value,visible:!!i.getClientRects().length})),
      headers:[...document.querySelectorAll('table thead tr')].filter(r=>r.getClientRects().length).map(r=>[...r.cells].map(c=>c.innerText)),
      rows:[...document.querySelectorAll('table tbody tr')].filter(r=>r.getClientRects().length&&/素材ID/.test(r.innerText)).slice(0,2).map(r=>[...r.cells].slice(0,10).map((c,i)=>({i,text:c.innerText.slice(0,180),loading:!!c.querySelector('.cell-async-loading')})))
    }))};
  }catch{}
  state.trace=(state.trace||[]).slice(-59);state.trace.push({phase:'failed',at:new Date().toISOString(),message});await save();
  if(prepared&&page&&c&&!suspended&&!permit?.consumed)try{await actor(c.rule_job?'rule_cleanup':'cleanup');}catch{}
  if(c&&!began&&!suspended)try{await gateway('result',{...lease(),ok:false,message,observation:null});}catch{}
  if(suspended&&task&&!/Control of this task space is with the user/.test(message))try{await task.handOff();}catch{}
  await event(began?'原提交待独立核验，不重复创建':message,{error:message,busy:false,...(suspended?{suspended:true}:{})});
}
