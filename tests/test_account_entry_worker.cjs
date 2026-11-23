'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../extension/account-entry-worker.js'),'utf8'),aid='8531163845726695',pid='8245872445590219';
const home=`https://qianchuan.jinritemai.com/uni-prom?aavid=${aid}&dr=2026-10-27%2C2026-10-27`,detail=`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${aid}&adId=${pid}`;
function fixture(options={}){
 const account={id:aid,scene:'live',tab_group:'示例分组B'},config={token:'t'.repeat(43)},policy={revision:'rev',accounts:[account]},saved={},tabs=new Map(),groups=options.noGroup?[]:[{id:20,title:'示例分组B',windowId:2}],calls=[],receipts=[];
 if(!options.noTab)tabs.set(10,{id:10,groupId:20,url:options.url||`https://qianchuan.jinritemai.com/overall-prom?aavid=${aid}`,status:'complete'});
 let allowed=true,nextTab=100,guard=options.editor?'editor_open':'navigation_ready';
 const ctx={URL,Date,AbortSignal,observerScopeCheckpoint:async()=>allowed,accountEntryDom:()=>{},
  chrome:{storage:{local:{get:async key=>({[key]:saved[key]}),set:async v=>Object.assign(saved,v)}},
   tabGroups:{query:async()=>options.ambiguous?[...groups,{id:21,title:'示例分组B',windowId:3}]:groups,get:async id=>{const g=groups.find(g=>g.id===id);if(!g)throw Error('missing group');return {...g};},update:async(id,v)=>{calls.push('group-title');Object.assign(groups.find(g=>g.id===id),v);}},
   tabs:{query:async()=>[...tabs.values()].map(t=>({...t})),get:async id=>{if(!tabs.has(id))throw Error('closed');return {...tabs.get(id)};},
    create:async v=>{calls.push('create');const t={id:nextTab++,groupId:-1,status:'complete',...v};tabs.set(t.id,t);if(options.stopAfterCreate)allowed=false;return {...t};},
    group:async v=>{calls.push('group');let gid=v.groupId;if(gid===undefined){gid=30;groups.push({id:gid,title:'',windowId:2});}for(const id of v.tabIds)tabs.get(id).groupId=gid;return gid;},
    update:async(id,v)=>{calls.push('navigate');Object.assign(tabs.get(id),v);return {...tabs.get(id)};}},
   scripting:{executeScript:async v=>{
    const [phase]=v.args;calls.push(phase);const t=tabs.get(v.target.tabId);
    let result={code:guard};
    if(phase==='discover')result=t.url.includes('/detail')?{code:'ready',plan_id:pid}:{code:'detail_open_requested',next_url:detail};if(options.moveDuringGuard&&phase==='guard')t.url='https://qianchuan.jinritemai.com/uni-prom?aavid=999999999';
    return [{frameId:0,documentId:'native-document',result}];
   }}},
  fetch:async(url,init)=>{assert.equal(url,'http://127.0.0.1:8877/api/cruise/worker-job-result');assert.equal(init.credentials,'omit');receipts.push(JSON.parse(init.body));return {ok:true};}};
 vm.createContext(ctx);vm.runInContext(source,ctx);
 const job={id:'a'.repeat(24),job_kind:'account_entry',account_id:aid,lane:'main',url:home};
 return {ctx,config,policy,account,job,tabs,groups,saved,calls,receipts,set allowed(v){allowed=v;},run:()=>ctx.accountEntryRun(job,config,policy),ensure:()=>ctx.accountEntryEnsureTab(config,policy,account,home,job.id)};
}
test('program reuses the ID-matched tab, enters live view, then verifies the real parent without an ad command',async()=>{
 const f=fixture();await f.run();assert.equal(f.tabs.get(10).url,home);assert.equal(f.receipts.at(-1).entry.code,'loading');
 await f.run();assert.equal(f.tabs.get(10).url,detail);await f.run();
 const r=f.receipts.at(-1);assert.equal(r.ok,true);assert.equal(r.entry.plan_id,pid);assert.equal(r.entry.group,'示例分组B');assert.equal(r.entry.document_id,'native-document');
 assert.equal(f.calls.includes('create'),false);assert.equal(f.calls.filter(c=>c==='navigate').length,2);
});
test('missing account creates exactly one inactive tab in the existing Chrome group and reuses it',async()=>{
 const f=fixture({noTab:true});await f.run();await f.run();await f.run();
 assert.equal(f.calls.filter(c=>c==='create').length,1);assert.equal(f.tabs.size,1);const tab=[...f.tabs.values()][0];assert.equal(tab.groupId,20);assert.equal(tab.active,false);assert.equal(tab.windowId,2);
 assert.equal(f.receipts.at(-1).ok,true);
});
test('another account is never reused or navigated',async()=>{
 const f=fixture({url:'https://qianchuan.jinritemai.com/uni-prom?aavid=999999999'}),original=f.tabs.get(10).url;await f.run();
 assert.equal(f.tabs.get(10).url,original);assert.equal(f.calls.filter(c=>c==='create').length,1);
});
test('editor, ambiguous group and disabled observation prevent navigation or new tabs',async()=>{
 for(const options of [{editor:true},{ambiguous:true}]){const f=fixture(options);await f.run();assert.equal(f.calls.includes('navigate'),false);assert.equal(f.calls.includes('create'),false);assert.equal(f.receipts.at(-1).ok,false);}
 const f=fixture({noTab:true});f.allowed=false;await f.run();assert.equal(f.calls.length,0);assert.equal(f.receipts.length,0);
});
test('login redirect retains the managed tab and cannot cause a new-tab loop',async()=>{
 const f=fixture({noTab:true});await f.run();const t=[...f.tabs.values()][0];t.url='https://qianchuan.jinritemai.com/account-center/login';await f.run();await f.run();
 assert.equal(f.calls.filter(c=>c==='create').length,1);assert.equal(f.receipts.at(-1).entry.code,'identity_unready');
});
test('scope interruption after creation resumes grouping the same owned tab',async()=>{
 const f=fixture({noTab:true,noGroup:true,stopAfterCreate:true});await f.run();assert.equal(f.tabs.size,1);assert.equal(f.calls.includes('group'),false);
 f.allowed=true;await f.run();assert.equal(f.tabs.size,1);assert.equal(f.calls.filter(c=>c==='create').length,1);assert.equal([...f.tabs.values()][0].groupId,30);
});

test('user account navigation during guard is never overwritten',async()=>{
 const f=fixture({moveDuringGuard:true});await f.run();assert.equal(f.calls.includes('navigate'),false);assert.equal(f.calls.includes('create'),false);
 assert.equal(f.receipts.at(-1).entry.code,'scope_changed');assert.equal(new URL(f.tabs.get(10).url).searchParams.get('aavid'),'999999999');
});
