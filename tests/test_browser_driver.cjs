const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {randomUUID}=require('node:crypto');
let JSDOM;
try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const {browserPlanDom}=require('../extension/control-dom.js');
const ACCOUNT='1000001',PLAN='2000001';

function page(options={}){
  const dom=new JSDOM(`<!doctype html><html><body><p>ID：${ACCOUNT}</p>\n<table><tbody><tr><td><div id="ad-name-${PLAN}">明确计划\nID：${PLAN}\n<button type="button" role="switch" aria-checked="true"></button></div></td>
    <td class="status">投放中</td><td><div class="promotionSettingCell-jBalQW"><div class="budgetCell-fRJMeY">每日预算\n<span class="budget">1000元</span><iconpark-icon name="oc-icon-light-edit"></iconpark-icon></div>
    <div>综合营销ROI目标\n<span class="roi">3.00</span><button type="button" data-roi-edit-button="true" aria-label="编辑综合营销ROI目标"></button></div></div></td></tr></tbody></table></body></html>`,
    {url:`https://qianchuan.jinritemai.com/overall-prom?aavid=${ACCOUNT}`,runScripts:'outside-only'});
  const w=dom.window,d=w.document,events={writes:0,opens:0,authorizations:0};
  w.HTMLElement.prototype.getClientRects=function(){return this.hidden||this.style.display==='none'?[]:[{x:0,y:0,width:100,height:20}];};
  Object.defineProperty(w.crypto,'randomUUID',{value:randomUUID});
  const timer=w.setTimeout.bind(w);w.setTimeout=(fn,ms)=>timer(fn,Math.min(ms,1));
  w.chrome={runtime:{sendMessage:async msg=>{events.authorizations++;return options.authorize?options.authorize(msg,d):{allow:true};}}};
  const body=d.body,row=d.querySelector('tr');
  function editor(field){
    events.opens++;
    const root=d.createElement('div');root.setAttribute('role','dialog');
    root.innerHTML=`<h2>${field==='budget'?'调整每日预算':'综合营销ROI目标'}</h2><input type="number" min="0.01" max="1000000000000" step="0.01"><button type="button">确定</button><button type="button">取消</button>`;
    const input=root.querySelector('input');input.value=field==='budget'?'1000':'3.00';
    root.querySelectorAll('button')[0].addEventListener('click',()=>{events.writes++;row.querySelector('.'+(field==='budget'?'budget':'roi')).textContent=input.value+(field==='budget'?'元':'');root.remove();});
    root.querySelectorAll('button')[1].addEventListener('click',()=>root.remove());
    if(options.form)options.form(root,input);
    body.append(root);
  }
  row.querySelector('iconpark-icon').addEventListener('click',()=>editor('budget'));
  row.querySelector('[data-roi-edit-button]').addEventListener('click',()=>editor('roi'));
  row.querySelector('[role="switch"]').addEventListener('click',()=>{events.writes++;row.querySelector('[role="switch"]').setAttribute('aria-checked','false');row.querySelector('.status').textContent='已暂停';});
  const execute=w.eval('('+browserPlanDom.toString()+')');
  const identity={accountId:ACCOUNT,scene:'product',path:'/overall-prom',group:'明确分组'};
  const run=(phase,args={})=>execute(phase,{identity,...args});
  async function command(action='budget_up',after='1100.00'){
    const inspected=await run('inspect'),surface=inspected.surfaces[0];
    return {id:'command-1',intent_digest:'digest-1',expires_at:new Date(Date.now()+120000).toISOString(),
      intent:{account_id:ACCOUNT,plan_id:PLAN,plan_name:'明确计划',action,
        field:action==='pause_plan'?'running':action.startsWith('roi_')?'target_roi':'budget',
        before:action==='pause_plan'?true:action.startsWith('roi_')?'3.00':'1000.00',after,
        setting_roi_basis:'marketing',surface:{...surface,tab_id:7,document_id:'old-document'}}};
  }
  return {dom,w,d,events,row,run,command,close:()=>dom.window.close()};
}

test('read-only navigation distinguishes hover text from unfinished forms and keeps execution guards',async()=>{
 const p=page();try{
  const tip=p.d.createElement('div');tip.className='ovui-popover';tip.textContent='整体消耗指标说明';p.d.body.append(tip);
  assert.equal((await p.run('inspect_navigation')).code,'navigation_ready');
  assert.equal((await p.run('inspect')).code,'editor_open');
  tip.innerHTML='<input value="未保存的预算">';assert.equal((await p.run('inspect_navigation')).code,'editor_open');
  tip.innerHTML='<button>确认暂停</button>';assert.equal((await p.run('inspect_navigation')).code,'editor_open');
  tip.remove();const drawer=p.d.createElement('div');drawer.className='ovui-drawer';drawer.textContent='未提交追投';p.d.body.append(drawer);
  assert.equal((await p.run('inspect_navigation')).code,'editor_open');
  drawer.remove();p.d.querySelector('p').textContent='ID：9999999';
  assert.equal((await p.run('inspect_navigation')).code,'account_label_missing');
  assert.equal(p.events.writes,0);assert.equal(p.events.opens,0);
 }finally{p.close();}
});

test('real-layout inventory is read-only, exact to the account, and separates setting basis',async()=>{
  const p=page();try{
    const result=await p.run('inspect');assert.equal(result.ok,true);assert.equal(result.surfaces.length,1);
    const s=result.surfaces[0];assert.equal(s.account_id,ACCOUNT);assert.equal(s.plan_id,PLAN);assert.equal(s.budget,'1000.00');assert.equal(s.target_roi,'3.00');assert.equal(s.roi_basis,'marketing');assert.equal(s.running,true);
    assert.equal(p.events.opens,0);assert.equal(p.events.writes,0);
    p.dom.reconfigure({url:`https://qianchuan.jinritemai.com/overall-prom?aavid=9999999`});
    assert.equal((await p.run('inspect')).ok,false);assert.equal(p.events.writes,0);
  }finally{p.close();}
});
test('preparation opens only the selected editor and never commits a modification',async()=>{
  const p=page();try{const c=await p.command(),prepared=await p.run('prepare',c);
    assert.equal(prepared.prepared,true);assert.equal(p.events.opens,1);assert.equal(p.events.writes,0);
    assert.equal(prepared.surface.budget,'1000.00');assert.equal(p.d.querySelector('input').value,'1000');
  }finally{p.close();}
});
test('budget and ROI each commit once only after the final worker authorization',async()=>{
  for(const [action,after,field] of [['budget_up','1100.00','budget'],['roi_target_up','3.15','roi']]){
    const p=page();try{
      const c=await p.command(action,after),prepared=await p.run('prepare',c),request={...c,submit_token:prepared.submit_token};
      const out=await p.run('submit',request);assert.equal(out.submitted,true);assert.equal(out.editor_open,false);
      assert.equal(p.events.writes,1);assert.equal(p.events.authorizations,1);assert.equal(p.row.querySelector('.'+field).textContent,after+(field==='budget'?'元':''));
      assert.equal((await p.run('submit',request)).submitted,false);assert.equal(p.events.writes,1);
    }finally{p.close();}
  }
});
test('pause uses the exact plan switch once, without any bulk selection',async()=>{
  const p=page();try{const c=await p.command('pause_plan',false),prepared=await p.run('prepare',c);
    assert.equal(p.events.writes,0);assert.equal((await p.run('submit',{...c,submit_token:prepared.submit_token})).submitted,true);
    assert.equal(p.events.writes,1);assert.equal(p.row.querySelector('[role="switch"]').getAttribute('aria-checked'),'false');
  }finally{p.close();}
});
test('revoked final authorization restores the owned input and cancels without submission',async()=>{
  const p=page({authorize:async()=>({allow:false})});try{const c=await p.command(),prepared=await p.run('prepare',c);
    const out=await p.run('submit',{...c,submit_token:prepared.submit_token});assert.equal(out.submitted,false);
    assert.equal(p.events.writes,0);assert.equal(p.d.querySelector('[role="dialog"]'),null);assert.equal(p.row.querySelector('.budget').textContent,'1000元');
  }finally{p.close();}
});
test('value change during final authorization rejects the stale action',async()=>{
  const p=page({authorize:async(msg,d)=>{d.querySelector('.budget').textContent='1200元';return {allow:true};}});
  try{const c=await p.command(),prepared=await p.run('prepare',c);const out=await p.run('submit',{...c,submit_token:prepared.submit_token});assert.equal(out.submitted,false);assert.equal(p.events.writes,0);}
  finally{p.close();}
});
test('an existing unrelated editor prevents opening or submitting another action',async()=>{
  const p=page();try{const c=await p.command();const e=p.d.createElement('div');e.setAttribute('role','dialog');e.textContent='用户正在编辑';p.d.body.append(e);
    assert.equal((await p.run('prepare',c)).code,'another_editor_open');assert.equal(p.events.opens,0);assert.equal(p.events.writes,0);
  }finally{p.close();}
});
test('ambiguous and mismatched editors are never filled or confirmed',async()=>{
  for(const form of [(root,input)=>input.value='2000',root=>root.append(root.querySelector('input').cloneNode()),root=>root.querySelector('h2').textContent='调控日预算']){
    const p=page({form});try{const c=await p.command(),out=await p.run('prepare',c);assert.equal(out.ok,false);assert.equal(p.events.writes,0);}
    finally{p.close();}
  }
});
test('unsupported ranges, stale commands and wrong nonces do not click a final button',async()=>{
  const p=page({form:(root,input)=>input.max='1050'});try{const c=await p.command(),prepared=await p.run('prepare',c);
    for(const extra of [{submit_token:'wrong'},{submit_token:prepared.submit_token,expires_at:new Date(Date.now()-1).toISOString()},{submit_token:prepared.submit_token}]){
      assert.equal((await p.run('submit',{...c,...extra})).submitted,false);
    }
    assert.equal(p.events.writes,0);
  }finally{p.close();}
});
test('duplicate plan nodes and malformed settings are not executable surfaces',async()=>{
  const p=page();try{p.row.querySelector('.budget').textContent='1,,000元';assert.equal((await p.run('inspect')).surfaces[0].budget,null);
    p.row.parentNode.append(p.row.cloneNode(true));assert.equal((await p.run('inspect')).surfaces.length,0);assert.equal(p.events.writes,0);
  }finally{p.close();}
});
