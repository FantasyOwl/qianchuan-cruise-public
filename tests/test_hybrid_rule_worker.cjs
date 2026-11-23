'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../extension/hybrid-rule-worker.js'),'utf8');
function fixture(options={}){
  let offered=false,began=false,verified=false,submit=0,cleanup=0;const posts=[],session={worker_id:'worker',epoch:'epoch',armed:!options.closed};
  const c={id:'command',lease:'lease',run_id:'run',phase:'rule_tasks',rule_phase:options.preview?'dry_prepare':'prepare',preview:!!options.preview,account_id:'account',plan_id:'plan',action:{id:'action'},expires_at:new Date(Date.now()+60000).toISOString()};
  const config={token:'token'},policy={revision:'revision'};
  const context={browserControlBusy:!!options.busy,inFlight:false,OBSERVER_KEYS:['token'],AbortSignal,Date,
    validObserverToken:v=>v==='token',observerCheckpoint:async()=>true,browserControlSession:async()=>session,readObserverPolicy:async()=>policy,
    hybridCheckpoint:async(_c,s,_p,write)=>!write||s.armed,hybridTab:async()=>({id:11}),hybridLocate:async()=>11,hybridRead:async()=>({document_id:'doc',rows:[]}),
    chrome:{runtime:{id:'extension',getManifest:()=>({version:'1.9.0'})},storage:{local:{get:async()=>config}}},
    hybridInject:async(tab,phase,command,args)=>{
      if(phase==='rule_cleanup'){cleanup++;return {result:{ok:true}};}
      if(phase==='rule_prepare')return {documentId:'doc',result:{prepared:true,nonce:'nonce',observation:{rows:[]}}};
      if(phase==='rule_submit'){
        const permit=await context.hybridRuleFinalCheck({id:'action',nonce:'nonce'},{id:'extension',tab:{id:11},frameId:0,documentId:'doc'});
        if(!permit.allow)throw Error('final rejected');submit++;return {documentId:'doc',result:{submitted:true}};
      }throw Error('wrong phase');
    },fetch:async(url,init)=>{
      assert.match(url,/^http:\/\/127\.0\.0\.1:8877\/api\/cruise\/hybrid-rules\/worker-(next|begin|final|result)$/);
      const name=url.split('-').at(-1),body=JSON.parse(init.body);posts.push({name,body});let value={ok:true};
      if(name==='next')value.command=!offered?(offered=true,c):began&&!verified?(verified=true,{...c,id:'read2',phase:'rule_verify',rule_phase:'verify'}):null;
      if(name==='begin'){began=true;if(options.lostBegin)throw Error('response lost');value={ok:true,submit_once:true,submit_token:'token'};}
      if(name==='final'){if(options.revoke)session.armed=false;value={ok:true,allow:true};}
      return {ok:true,json:async()=>value};
    }};
  vm.createContext(context);vm.runInContext(source,context);
  return {context,posts,run:()=>context.runHybridRules(),stats:()=>({submit,cleanup})};
}
test('rule worker submits once and next phase only independently reads original action',async()=>{
  const f=fixture();assert.equal(await f.run(),true);assert.equal(f.stats().submit,1);assert.equal(f.posts.filter(x=>x.name==='begin').length,1);assert.equal(f.posts.filter(x=>x.name==='final').length,1);assert.equal(f.posts.at(-2).body.id,'read2');assert.equal(f.context.browserControlBusy,false);
});
test('shared F5 mutex prevents any parallel rule tab access',async()=>{const f=fixture({busy:true});assert.equal(await f.run(),false);assert.equal(f.posts.length,0);});
test('closed execution session cannot prepare a rule edit',async()=>{const f=fixture({closed:true});await f.run();assert.equal(f.stats().submit,0);assert.equal(f.posts.some(x=>x.name==='begin'),false);});
test('lost begin response never clicks and cleanup only cancels our editor',async()=>{const f=fixture({lostBegin:true});await f.run();assert.equal(f.stats().submit,0);assert.equal(f.stats().cleanup,1);assert.equal(f.posts.filter(x=>x.name==='begin').length,1);});
test('session revoked during final callback prevents native click',async()=>{const f=fixture({revoke:true});await f.run();assert.equal(f.stats().submit,0);assert.equal(f.posts.filter(x=>x.name==='final').length,1);});
test('unsolicited final callbacks cannot obtain permits',async()=>{const f=fixture();assert.equal((await f.context.hybridRuleFinalCheck({id:'action',nonce:'nonce'},{id:'extension',tab:{id:11},frameId:0,documentId:'doc'})).allow,false);assert.equal(f.posts.length,0);});
test('unarmed native form preview cancels without begin or final permit',async()=>{const f=fixture({closed:true,preview:true});await f.run();assert.equal(f.stats().submit,0);assert.equal(f.stats().cleanup,1);assert.equal(f.posts.some(x=>x.name==='begin'||x.name==='final'),false);});
