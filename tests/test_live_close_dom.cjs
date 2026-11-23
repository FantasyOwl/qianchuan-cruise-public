const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const source=fs.readFileSync(require.resolve('../extension/live-close-dom.js'),'utf8'),aid='1999999999999002',pid='8218616704149013';
function fixture(){
 const dom=new JSDOM(`<div>ID：${aid}</div><table><tbody><tr data-plan-id="${pid}"><td>投放中 </td><td>每日预算 500000元</td><td><button data-speed>一键调速</button></td></tr></tbody></table>`,{url:`https://qianchuan.jinritemai.com/uni-prom?aavid=${aid}`}),w=dom.window;let submitted=0,ended=0,permit=true,onPermit=null;
 Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});w.HTMLElement.prototype.getClientRects=function(){return this.isConnected&&this.style.display!=='none'?[{}]:[];};
 w.document.querySelector('[data-speed]').onclick=()=>{
  const root=w.document.createElement('div');root.className='ovui-modal';root.innerHTML='<h2>新建调控任务</h2><p>调控方式</p><button data-boost>一键起量</button><button data-control>一键控量</button><label>调控预算(元)<input type="number" step="0.01"></label><label>控量时长<input type="number" value="0.5" min="0.5" max="24" step="0.5"></label><button data-end>结束起量</button><button data-save>提交</button><button data-cancel>取消</button>';
  root.querySelector('[data-control]').onclick=e=>{e.currentTarget.className='selected';};root.querySelector('[data-boost]').onclick=()=>{throw Error('must never choose boost');};
  root.querySelector('[data-end]').onclick=()=>ended++;root.querySelector('[data-save]').onclick=()=>submitted++;root.querySelector('[data-cancel]').onclick=()=>root.remove();w.document.body.append(root);
 };
 const context=vm.createContext({URL,Date,document:w.document,location:w.location,crypto:require('node:crypto').webcrypto,getComputedStyle:w.getComputedStyle.bind(w),Event:w.Event,HTMLInputElement:w.HTMLInputElement,setTimeout,chrome:{runtime:{sendMessage:async()=>{if(onPermit)onPermit();return {allow:permit};}}}});vm.runInContext(source,context);
 const req={identity:{scene:'live',role:'live',accountId:aid,group:'示例分组A'},id:'schedule-one',intent_digest:'digest-one',expires_at:new Date(Date.now()+60000).toISOString(),intent:{target_kind:'live_close',action:'one_click_control',account_id:aid,plan_id:pid,group:'示例分组A',trigger_at:new Date(Date.now()-1000).toISOString(),budget:'200.00',duration_minutes:30}};
 return {w,dom,req,call:(phase,r=req)=>context.liveCloseDom(phase,r),get submitted(){return submitted;},get ended(){return ended;},set permit(v){permit=v;},set onPermit(v){onPermit=v;}};
}
test('reads exact homepage plan and identifies active boost without ending it',async()=>{
 const f=fixture();let r=await f.call('inspect');assert.equal(r.surfaces[0].plan_id,pid);assert.equal(r.surfaces[0].parent_budget,'500000.00');assert.equal(r.surfaces[0].busy,false);
 f.w.document.querySelector('tr').append('一键起量 进行中');r=await f.call('inspect');assert.equal(r.surfaces[0].control_mode,'boost');assert.equal((await f.call('prepare')).code,'plan_busy_or_not_running');assert.equal(f.ended,0);assert.equal(f.submitted,0);
});
test('sets native control mode budget duration then submits once after permit',async()=>{
 const f=fixture(),p=await f.call('prepare');assert.equal(p.prepared,true,p.code);assert.equal(f.submitted,0);assert.equal(f.ended,0);
 const r={...f.req,submit_token:p.submit_token};assert.equal((await f.call('submit',r)).submitted,true);assert.equal(f.submitted,1);assert.equal((await f.call('submit',r)).submitted,false);assert.equal(f.submitted,1);
});
test('preserves an existing modal and refuses wrong account or early time',async()=>{
 const f=fixture();f.w.document.body.insertAdjacentHTML('beforeend','<div role="dialog"><input value="keep"></div>');assert.equal((await f.call('prepare')).code,'preserve_existing_editor');assert.equal(f.w.document.querySelector('[role="dialog"] input').value,'keep');
 const g=fixture();g.req.intent.trigger_at=new Date(Date.now()+60000).toISOString();assert.equal((await g.call('prepare')).code,'schedule_outside_window');assert.equal(g.w.document.querySelector('.ovui-modal'),null);
 const h=fixture();h.w.document.body.firstElementChild.textContent='ID：100';assert.equal((await h.call('inspect')).ok,false);
});
test('changed budget duration status and identity after final await cancel submission',async()=>{
 for(const change of [f=>{f.w.document.querySelector('.ovui-modal input').value='201';},f=>{f.w.document.querySelector('tr').append('一键起量 进行中');},f=>{f.w.document.querySelector('tr td').textContent='已暂停';},f=>{f.w.document.body.firstElementChild.textContent='ID：100';}]){
  const f=fixture(),p=await f.call('prepare');assert.equal(p.prepared,true,p.code);f.onPermit=()=>change(f);const r=await f.call('submit',{...f.req,submit_token:p.submit_token});assert.equal(r.submitted,false);assert.equal(f.submitted,0);assert.equal(f.ended,0);
 }
});
test('requires final permit and verified control mode, never clicks boost or end',async()=>{
 const f=fixture();f.permit=false;const p=await f.call('prepare');assert.equal(p.prepared,true,p.code);assert.equal((await f.call('submit',{...f.req,submit_token:p.submit_token})).submitted,false);assert.equal(f.submitted,0);assert.equal(f.ended,0);
 const g=fixture(),original=g.w.document.querySelector('[data-speed]').onclick;g.w.document.querySelector('[data-speed]').onclick=()=>{original();g.w.document.querySelector('[data-control]').onclick=()=>{};};assert.equal((await g.call('prepare')).code,'native_control_mode_unverified');assert.equal(g.submitted,0);
});
test('ambiguous plan IDs and changed parent budget cannot prepare',async()=>{
 const f=fixture();f.w.document.querySelector('tr').insertAdjacentHTML('beforeend','<td data-ad-id="123456789"></td>');assert.equal((await f.call('inspect')).surfaces.length,0);
 const g=fixture();g.w.document.querySelector('tr td:nth-child(2)').textContent='每日预算100元';assert.equal((await g.call('prepare')).prepared,undefined);assert.equal(g.submitted,0);
});
test('60-minute setting uses its own readonly native stepper once',async()=>{
 const f=fixture(),entry=f.w.document.querySelector('[data-speed]'),original=entry.onclick;f.req.intent.duration_minutes=60;
 entry.onclick=()=>{original();const input=f.w.document.querySelector('.ovui-modal label:nth-of-type(2) input');input.readOnly=true;const step=f.w.document.createElement('button');step.textContent='+';step.onclick=()=>input.value='1';input.parentElement.append(step);};
 const p=await f.call('prepare');assert.equal(p.prepared,true,p.code);assert.equal(f.w.document.querySelector('.ovui-modal label:nth-of-type(2) input').value,'1');assert.equal(f.submitted,0);
});
test('active boost still advertises its explicit plan when the create entry is replaced',async()=>{
 const f=fixture();f.w.document.querySelector('[data-speed]').remove();f.w.document.querySelector('tr').append('一键起量 进行中');const r=await f.call('inspect');assert.equal(r.surfaces.length,1);assert.equal(r.surfaces[0].busy,true);assert.equal(r.surfaces[0].control_mode,'boost');assert.equal((await f.call('prepare')).prepared,undefined);assert.equal(f.submitted,0);
});
test('an aweme row key alone never becomes a plan and its bounded DOM check explains why',async()=>{
 const f=fixture(),row=f.w.document.querySelector('tr');row.removeAttribute('data-plan-id');row.setAttribute('data-row-key','79987870170');const r=await f.call('inspect');assert.equal(r.surfaces.length,0);assert.equal(r.code,'home_plan_identity_missing');assert.equal(r.diagnostics.entry_count,1);assert.equal(r.diagnostics.samples[0].ids[0].row,'79987870170');assert.equal(f.submitted,0);
});
test('a rendered plan label is distinct from an aweme label and ambiguity remains rejected',async()=>{
 const f=fixture(),row=f.w.document.querySelector('tr');row.removeAttribute('data-plan-id');row.append('\n抖音号ID：79987870170\n计划ID：'+pid+'\n');const r=await f.call('inspect');assert.equal(r.surfaces[0].plan_id,pid);row.setAttribute('data-ad-id','1234567');assert.equal((await f.call('inspect')).surfaces.length,0);
});
const parent=(changes={})=>({account_id:aid,plan_id:pid,aweme_id:'79987870170',group:'示例分组A',detail_url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${aid}&adId=${pid}`,captured_at:new Date().toISOString(),...changes});
test('homepage aweme row uses only its fresh explicit detail mapping, retaining both IDs',async()=>{
 const f=fixture(),row=f.w.document.querySelector('tr');row.removeAttribute('data-plan-id');row.insertAdjacentHTML('afterbegin','<td><div id="79987870170">示例店铺\n</div></td>');f.req.parents=[parent()];
 const r=await f.call('inspect');assert.equal(r.surfaces[0].plan_id,pid);assert.equal(r.surfaces[0].aweme_id,'79987870170');assert.equal(f.submitted,0);
 f.req.intent.aweme_id='79987870170';const p=await f.call('prepare');assert.equal(p.prepared,true,p.code);assert.equal(f.submitted,0);
});
test('stale conflicting and foreign detail mappings cannot identify a homepage row',async()=>{
 for(const maps of [[parent({captured_at:new Date(Date.now()-601000).toISOString()})],[parent(),parent({plan_id:'1234567',detail_url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${aid}&adId=1234567`})],[parent({account_id:'100'})]]){
  const f=fixture(),row=f.w.document.querySelector('tr');row.removeAttribute('data-plan-id');row.insertAdjacentHTML('afterbegin','<td id="79987870170">抖音号</td>');f.req.parents=maps;assert.equal((await f.call('inspect')).surfaces.length,0);assert.equal(f.submitted,0);
 }
});
test('execution rechecks explicit plan and aweme labels in the same detail header',async()=>{
 const f=fixture();f.dom.reconfigure({url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${aid}&adId=${pid}`});f.w.document.body.innerHTML=`<p>ID：${aid}</p><div class="oc-promotion-key-info-bar">投放中\n计划ID：${pid}\n<span>抖音号ID：79987870170</span></div>`;
 const r=await f.call('inspect_parent',{identity:{scene:'live',role:'detail',accountId:aid,planId:pid,group:'示例分组A'}});assert.equal(r.parent.aweme_id,'79987870170');assert.equal(r.parent.plan_id,pid);
 f.w.document.querySelector('span').textContent='抖音号ID：79987870171';const changed=await f.call('inspect_parent',{identity:{scene:'live',role:'detail',accountId:aid,planId:pid,group:'示例分组A'}});assert.equal(changed.parent.aweme_id,'79987870171');assert.equal(f.submitted,0);
});
