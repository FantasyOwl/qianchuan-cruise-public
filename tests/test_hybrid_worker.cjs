'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../extension/hybrid-worker.js'),'utf8');
function fixture(options={}){
 const config={token:'t'.repeat(43)},session={worker_id:'worker',epoch:'epoch',armed:!options.preview},posts=[];
 const policy={revision:'rev',accounts:[{id:'1999999999999002',scene:'live',tab_group:'原账户'}]};
 const command={id:'read-lease',lease:'lease',run_id:'run',expires_at:new Date(Date.now()+60000).toISOString(),account_id:policy.accounts[0].id,plan_id:'8218616704149013',group:'原账户',page:1,date_start:'2026-11-06',date_end:'2026-11-10',
  phase:options.preview?'dry_prepare':'prepare',action:{id:'action',intent:{browser_batch:{task_name:'QCX_test'}}}};
 let offered=false,verifyPending=false,beginAccepted=false,clicks=0,cleanups=0,navigations=0;
 let tab={id:10,groupId:3,status:'complete',discarded:!!options.discarded,url:'https://qianchuan.jinritemai.com/overall-prom/detail?aavid=1999999999999002&adId=8218616704149013&dateRange=2026-11-06%2C2026-11-10'};
 if(options.pageHash)tab.url+='#?adDetailCustomCreativePage=2&adDetailCustomCreativePageSize=20&adDetailCustomCreativeSortType=asc';
 const stored={qc_hybrid_page_10:{key:'run:materials:2026-11-06:2026-11-10:0',documentId:options.changedDocument?'old-document':'fresh-document'}};
 const context={URL,URLSearchParams,Date,AbortSignal,setTimeout,clearTimeout,hybridChaseDom:()=>{},OBSERVER_KEYS:['token'],browserControlBusy:false,inFlight:false,
  validObserverToken:v=>v===config.token,observerCheckpoint:async()=>true,observerScopeCheckpoint:async()=>true,
  browserControlCheckpoint:async()=>session.armed,browserControlSession:async()=>session,readObserverPolicy:async()=>policy,
  chrome:{runtime:{id:'extension',getManifest:()=>({version:'1.7.2'})},storage:{local:{get:async()=>config,set:async()=>{}},session:{get:async k=>({[k]:stored[k]}),set:async v=>Object.assign(stored,v)}},
   tabs:{get:async()=>tab,query:async()=>[tab],update:async(id,p)=>{assert.equal('active' in p,false);navigations++;tab={...tab,...p,discarded:false,status:'complete'};},reload:async()=>{navigations++;tab={...tab,discarded:false,status:'complete'};}},
   tabGroups:{get:async()=>({title:'原账户'})}},
  fetch:async(url,init)=>{
   assert.equal(new URL(url).origin,'http://127.0.0.1:8877');const name=url.split('/').at(-1),body=JSON.parse(init.body);posts.push({name,body});let value={ok:true};
   if(name==='worker-next'){
    value.command=!offered?(offered=true,command):verifyPending?(verifyPending=false,{...command,id:'verify-lease',phase:'verify'}):null;
   }
   if(name==='worker-begin'){
    beginAccepted=true;verifyPending=true;
    if(options.lostBegin)throw Error('begin response lost');
    value={ok:true,submit_once:true,submit_token:'server-token'};
   }
   if(name==='worker-final'){value={allow:true};if(options.revokeDuringFinal)session.armed=false;}
   if(name==='worker-result'&&options.runningFilter&&!body.ok)value.continue_round=true;
   if(name==='worker-result'&&beginAccepted&&body.id==='read-lease')return {ok:false,json:async()=>({error:'old command no longer valid'})};
   return {ok:true,status:200,json:async()=>value};
  }};
 vm.createContext(context);vm.runInContext(source,context);
 const locate=context.hybridLocate,realInject=context.hybridInject,read=context.hybridRead;
 context.hybridLocate=async()=>10;
 context.hybridRead=async()=>({document_id:'fresh-document'});
 let identityCalls=0;
 context.hybridInject=async(tabId,phase,c)=>{
  if(phase==='identity'){
   identityCalls++;
   if(options.identityDelayed&&identityCalls===1||options.identityMissing&&!navigations)return {documentId:'fresh-document',result:{ok:false,message:'等待可见账户ID',editor_open:!!options.editorOpen}};
   if(options.accessDenied||options.staleDocument&&identityCalls===1)throw Error('Cannot access contents of the page. Extension manifest must request permission to access the respective host.');
   return {documentId:'fresh-document',result:{ok:true,editor_open:!!options.editorOpen}};
  }
  if(phase==='prepare')return {documentId:'fresh-document',result:options.runningFilter?{prepared:false,message:'提交前素材条件已变化：素材正在追投 8533524212032260758'}:{prepared:true,nonce:'nonce',observation:{}}};
  if(phase==='cleanup'){cleanups++;return {result:{ok:!options.cleanupFails}};}
  if(phase==='verify')return {documentId:'independent-document',result:{ok:true,observation:{verified:true}}};
  if(phase==='submit'){
   const message={id:'action',nonce:'nonce'},sender={id:'extension',tab:{id:10},frameId:0,documentId:'fresh-document'};
   assert.equal((await context.hybridFinalCheck(message,{...sender,documentId:'wrong-document'})).allow,false);
   const granted=await context.hybridFinalCheck(message,sender);
   assert.equal((await context.hybridFinalCheck(message,sender)).allow,false);
   if(granted.allow){clicks++;if(options.lostSubmit)throw Error('script result lost after click');}
   return {result:{submitted:granted.allow}};
  }
  throw Error('unexpected injection '+phase);
 };
 return {context,posts,command,realInject,read:()=>read(10,command,config,session,policy),get tab(){return tab;},get clicks(){return clicks;},get cleanups(){return cleanups;},get navigations(){return navigations;},locate:(extra={})=>locate({...command,phase:'materials',...extra},config,session,policy),run:()=>context.runHybridChase()};
}

test('API candidate preparation only activates the material view and never reads or pages a full material list',async()=>{
 const f=fixture();f.command.action.intent.browser_batch.selection_source='official_api';const injected=[];
 f.context.hybridInject=async(tab,phase)=>{injected.push(phase);return {documentId:'api-selection-document',result:{ok:true,changed:false}};};
 assert.equal((await f.read()).document_id,'api-selection-document');assert.deepEqual(injected,['view']);
 assert.equal(f.clicks,0);assert.deepEqual(f.posts,[]);
});
test('unarmed preview fills and cancels without requesting a submission permit',async()=>{
 const f=fixture({preview:true});await f.run();assert.equal(f.clicks,0);assert.equal(f.cleanups,1);
 assert.equal(f.posts.some(p=>['worker-begin','worker-final'].includes(p.name)),false);
 assert.equal(f.posts.find(p=>p.name==='worker-result').body.ok,true);
});
test('a failed cleanup cannot be reported as a successful preview',async()=>{
 const f=fixture({preview:true}),inject=f.context.hybridInject;
 f.context.hybridInject=async(tab,phase,...args)=>phase==='cleanup'?{result:{ok:false,message:'本次勾选尚未清理'}}:inject(tab,phase,...args);
 await f.run();const result=f.posts.find(p=>p.name==='worker-result');
 assert.equal(result.body.ok,false);assert.match(result.body.message,/尚未清理/);assert.equal(f.clicks,0);
 assert.equal(f.posts.some(p=>['worker-begin','worker-final'].includes(p.name)),false);
});
test('a regrouped running-material filter continues in the same wake only after its own selection is cleared',async()=>{
 for(const cleanupFails of [false,true]){
  const f=fixture({preview:true,runningFilter:true,cleanupFails});await f.run();
  assert.equal(f.posts.filter(p=>p.name==='worker-next').length,cleanupFails?1:2);
  assert.equal(f.clicks,0);assert.equal(f.posts.some(p=>['worker-begin','worker-final'].includes(p.name)),false);
 }
});
test('lost begin acknowledgement only reads the original result on the next wake',async()=>{
 const f=fixture({lostBegin:true});await f.run();await f.run();assert.equal(f.clicks,0);
 assert.equal(f.posts.filter(p=>p.name==='worker-begin').length,1);
 assert.equal(f.posts.filter(p=>p.name==='worker-final').length,0);
 assert.equal(f.posts.filter(p=>p.name==='worker-result'&&p.body.id==='verify-lease').length,1);
});
test('lost response after click cannot submit twice and a final permit binds the exact document',async()=>{
 const f=fixture({lostSubmit:true});await f.run();await f.run();assert.equal(f.clicks,1);
 assert.equal(f.posts.filter(p=>p.name==='worker-begin').length,1);
 assert.equal(f.posts.filter(p=>p.name==='worker-final').length,1);
 assert.equal(f.posts.filter(p=>p.name==='worker-result'&&p.body.id==='verify-lease').length,1);
 assert.equal(f.context.browserControlBusy,false);
});

test('a resumed service worker reuses its verified document instead of refreshing the first page again',async()=>{
 const f=fixture();assert.equal(await f.locate(),10);assert.equal(f.navigations,0);
 const changed=fixture({changedDocument:true});assert.equal(await changed.locate(),10);assert.equal(changed.navigations,1);
});

test('route changes preserve native page state so the DOM adapter can click and verify pagination',async()=>{
 const f=fixture({changedDocument:true,pageHash:true});await f.locate();assert.equal(f.navigations,1);
 const hash=new URLSearchParams(new URL(f.tab.url).hash.slice(2));
 assert.equal(hash.get('adDetailCustomCreativePage'),'2');assert.equal(hash.get('adDetailCustomCreativePageSize'),'20');
 assert.equal(hash.get('adDetailCustomCreativeSortType'),'asc');assert.equal(f.clicks,0);
});

test('closing execution while the final server check is in flight prevents the native click',async()=>{
 const f=fixture({revokeDuringFinal:true});await f.run();await f.run();assert.equal(f.clicks,0);
 assert.equal(f.posts.filter(p=>p.name==='worker-final').length,1);
});

test('an unreadable document is refreshed once in its original background tab then revalidated',async()=>{
 const f=fixture({staleDocument:true});assert.equal(await f.locate(),10);assert.equal(f.navigations,1);assert.equal(f.clicks,0);
 const denied=fixture({accessDenied:true});await assert.rejects(denied.locate(),/Cannot access/);assert.equal(denied.navigations,1);
 await assert.rejects(denied.locate(),/已自动刷新/);assert.equal(denied.navigations,1);
});

test('a discarded background tab reloads before injection while an editor or create step is preserved',async()=>{
 const discarded=fixture({discarded:true});assert.equal(await discarded.locate(),10);assert.equal(discarded.navigations,1);
 const editor=fixture({editorOpen:true});await assert.rejects(editor.locate(),/编辑窗口/);assert.equal(editor.navigations,0);
 const create=fixture({accessDenied:true});await assert.rejects(create.locate({phase:'prepare'}),/Cannot access/);assert.equal(create.navigations,0);
 const sleepingCreate=fixture({discarded:true});await assert.rejects(sleepingCreate.locate({phase:'prepare'}),/未自动刷新/);assert.equal(sleepingCreate.navigations,0);
});

test('a delayed identity waits before refreshing and missing read identity recovers only its existing tab',async()=>{
 const delayed=fixture({identityDelayed:true});assert.equal(await delayed.locate(),10);assert.equal(delayed.navigations,0);
 const absent=fixture({identityMissing:true});assert.equal(await absent.locate(),10);assert.equal(absent.navigations,1);assert.equal(absent.clicks,0);
 const editor=fixture({identityMissing:true,editorOpen:true});await assert.rejects(editor.locate(),/编辑窗口/);assert.equal(editor.navigations,0);
 const create=fixture({identityMissing:true});await assert.rejects(create.locate({phase:'prepare'}),/账户ID/);assert.equal(create.navigations,0);
});

test('manual recovery cycles may refresh once again without ever clicking a submission',async()=>{
 const f=fixture({accessDenied:true});await assert.rejects(f.locate(),/Cannot access/);
 await assert.rejects(f.locate(),/已自动刷新/);assert.equal(f.navigations,1);
 await assert.rejects(f.locate({recovery_cycle:1}),/Cannot access/);assert.equal(f.navigations,2);assert.equal(f.clicks,0);
});

test('background waits bind the active command, top-frame document and account, then close with the injection',async()=>{
 const f=fixture(),sender={id:'extension',tab:{id:10},frameId:0,documentId:'fresh-document'};
 const message={action:'hybrid-wait',run_id:'run',command_id:'read-lease',duration_ms:1};
 f.context.chrome.scripting={executeScript:async()=>{
  for(const invalid of [{...sender,id:'website'},{...sender,frameId:1},{...sender,documentId:'other-document'},{...sender,tab:{id:11}}])assert.equal((await f.context.hybridWait(message,invalid)).ok,false);
  for(const invalid of [{...message,duration_ms:601},{...message,run_id:'different-run'},{...message,command_id:'other-command'}])assert.equal((await f.context.hybridWait(invalid,sender)).ok,false);
  assert.equal((await f.context.hybridWait(message,sender)).ok,true);
  return [{frameId:0,documentId:'fresh-document',result:{ok:true}}];
 }};
 await f.realInject(10,'prepare',f.command,{},'fresh-document');
 assert.equal((await f.context.hybridWait(message,sender)).ok,false);assert.equal(f.clicks,0);assert.deepEqual(f.posts,[]);
});

test('revoking execution during a background wait interrupts preparation before the next page action',async()=>{
 const f=fixture(),session=await f.context.browserControlSession();
 f.context.setTimeout=(resolve,ms)=>{session.armed=false;return setTimeout(resolve,ms);};
 f.context.chrome.scripting={executeScript:async()=>{
  const wait=await f.context.hybridWait({action:'hybrid-wait',run_id:'run',command_id:'read-lease',duration_ms:1},{id:'extension',tab:{id:10},frameId:0,documentId:'fresh-document'});
  assert.equal(wait.ok,false);return [{frameId:0,documentId:'fresh-document',result:{ok:true}}];
 }};
 await f.realInject(10,'prepare',f.command,{},'fresh-document');assert.equal(f.clicks,0);assert.deepEqual(f.posts,[]);
});

function homeFixture(options={}){
 const f=fixture({preview:options.preview!==false,lostBegin:options.lostBegin,lostSubmit:options.lostSubmit});
 f.command.preferred_entry='home_plus';f.command.action.intent.browser_batch.selection_source='official_api';
 const inject=f.context.hybridInject,locate=f.context.hybridLocate,phases=[];let wentHome=false;
 f.context.hybridLocate=async(...args)=>{
  if(wentHome&&options.fallback){await f.context.chrome.tabs.update(10,{url:'https://qianchuan.jinritemai.com/overall-prom/detail?aavid=1999999999999002&adId=8218616704149013'});}
  return locate(...args);
 };
 f.context.hybridInject=async(tab,phase,c,args={},documentId)=>{
  phases.push(phase+(args.preflight_only?':preflight':''));
  if(phase==='prepare'&&args.preflight_only)return {documentId:'source-document',result:{preflight_ready:true,home_supported:!options.unsupported,home_diagnostic:{owner_count:1,link_count:options.unsupported?0:1},observation:{account_id:c.account_id},binding:{account_id:c.account_id,plan_id:c.plan_id,group:c.group}}};
  if(phase==='home_go'){
   wentHome=true;await f.context.chrome.tabs.update(tab,{url:'https://qianchuan.jinritemai.com/overall-prom?aavid='+c.account_id});
   return {result:{navigated:true}};
  }
  if(phase==='home_open')return {documentId:'fresh-document',result:options.fallback?{fallback:true}:options.openUnknown?{ok:false,message:'首页出现未识别窗口，保留输入'}:{opened:true,home:{path:'/overall-prom'}}};
  const r=await inject(tab,phase,c,args,documentId);
  if(phase==='prepare'&&wentHome&&!options.fallback)r.result.observation={entry_source:'home_plus',selection_source:'official_api',preflight:{document_id:'source-document'},binding:{},home:{path:'/overall-prom'},form_material_ids:['12345678']};
  return r;
 };
 return {...f,context:f.context,command:f.command,posts:f.posts,phases,run:f.run,get clicks(){return f.clicks;},get cleanups(){return f.cleanups;},get navigations(){return f.navigations;}};
}
test('a closed-execution home preview preflights without a seed, navigates the original tab, fills and cancels',async()=>{
 const f=homeFixture();await f.run();const result=f.posts.find(p=>p.name==='worker-result');
 assert.equal(result.body.ok,true,result.body.message);assert.equal(result.body.observation.entry_source,'home_plus');
 assert.equal(result.body.observation.home.document_id,'fresh-document');assert.equal(result.body.observation.preflight.document_id,'source-document');
 assert.equal('document_id' in result.body.observation,false);assert.equal(f.clicks,0);assert.equal(f.cleanups,2);
 assert.deepEqual(f.phases,['prepare:preflight','cleanup','home_go','home_open','prepare','cleanup']);
 assert.equal(f.posts.some(p=>['worker-begin','worker-final'].includes(p.name)),false);assert.equal(f.navigations,1);
});
test('home fallback is permitted only before a task opens; unknown windows never trigger a second entry',async()=>{
 const fallback=homeFixture({fallback:true});await fallback.run();assert.equal(fallback.navigations,2);
 assert.equal(fallback.posts.find(p=>p.name==='worker-result').body.ok,true);assert.equal(fallback.clicks,0);
 const unknown=homeFixture({openUnknown:true});await unknown.run();assert.equal(unknown.navigations,1);
 assert.equal(unknown.phases.filter(p=>p==='prepare').length,0);assert.equal(unknown.posts.find(p=>p.name==='worker-result').body.ok,false);
 assert.equal(unknown.clicks,0);
});

test('a missing breadcrumb records the actual fallback reason rather than claiming home success',async()=>{
 const f=homeFixture({unsupported:true});await f.run();const result=f.posts.find(p=>p.name==='worker-result');
 assert.equal(result.body.ok,true);assert.match(result.body.message,/详情备用：抖音号标识1个、推直播间入口0个/);
 assert.equal(result.body.observation.entry_source,undefined);assert.equal(f.phases.includes('home_go'),false);assert.equal(f.navigations,0);assert.equal(f.clicks,0);
});
test('home final permits accept only the bound live command, and lost begin or submit never creates twice',async()=>{
 for(const options of [{},{lostBegin:true},{lostSubmit:true}]){
  const f=homeFixture({preview:false,...options});await f.run();await f.run();
  assert.equal(f.clicks,options.lostBegin?0:1);assert.equal(f.posts.filter(p=>p.name==='worker-begin').length,1);
  assert.equal(f.posts.filter(p=>p.name==='worker-final').length,options.lostBegin?0:1);
  const policy=await f.context.readObserverPolicy();assert.equal(await f.context.hybridTab(10,f.command,policy),null);
 }
});

test('an unarmed DOM probe only navigates the existing tab and reads bounded metadata, without a write permit',async()=>{
 const f=fixture({preview:true});f.command.phase='dom_probe';f.command.action=null;f.command.preview=true;f.command.probe={target:'home',selectors:['tr']};
 f.context.hybridProbeDom=function(){};f.context.hybridLocate=async()=>{throw Error('a probe must not enter the create flow');};
 f.context.setTimeout=(done,ms)=>setTimeout(done,Math.min(ms,1));let scripts=0;
 f.context.chrome.scripting={executeScript:async args=>{
  assert.equal(args.func,f.context.hybridProbeDom);assert.equal(args.target.tabId,10);assert.equal(args.world,'ISOLATED');scripts++;
  const [phase,{command:c}]=args.args;
  if(phase==='home'){await f.context.chrome.tabs.update(10,{url:'https://qianchuan.jinritemai.com/overall-prom?aavid='+c.account_id});return [{frameId:0,documentId:'home-doc',result:{ok:true,navigated:true}}];}
  assert.equal(phase,'inspect');return [{frameId:0,documentId:'home-doc',result:{ok:true,observation:{account_id:c.account_id,plan_id:c.plan_id,group:c.group,path:'/overall-prom',rows:{tr:[]}}}}];
 }};
 await f.run();assert.equal(scripts,2);assert.equal(f.clicks,0);assert.equal(f.navigations,1);assert.equal(f.cleanups,0);
 const receipt=f.posts.find(p=>p.name==='worker-result');assert.equal(receipt.body.ok,true);assert.equal(receipt.body.observation.document_id,'home-doc');
 assert.equal(f.posts.some(p=>['worker-begin','worker-final'].includes(p.name)),false);
});
test('a diagnostic command cannot carry an advertising action or an unregistered account',async()=>{
 for(const foreign of [false,true]){
  const f=fixture({preview:true});f.command.phase='dom_probe';f.command.preview=true;f.command.probe={target:'home',selectors:['tr']};
  if(foreign){f.command.action=null;f.command.account_id='8086606404052108';}
  await f.run();const receipt=f.posts.find(p=>p.name==='worker-result');assert.equal(receipt.body.ok,false);assert.equal(f.clicks,0);
  assert.equal(f.posts.some(p=>['worker-begin','worker-final'].includes(p.name)),false);
 }
});
