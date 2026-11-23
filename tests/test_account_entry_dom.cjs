'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const source=fs.readFileSync(require.resolve('../extension/account-entry-dom.js'),'utf8'),aid='8531163845726695',pid='8245872445590219';
function fixture(body='',path='/uni-prom',scene='live'){
 const dom=new JSDOM(`<div>ID：${aid}</div>${body}`,{url:`https://qianchuan.jinritemai.com${path}?aavid=${aid}`}),w=dom.window;
 Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});w.HTMLElement.prototype.getClientRects=function(){return this.isConnected&&this.style.display!=='none'?[{}]:[];};
 const ctx=vm.createContext({URL,document:w.document,location:w.location,getComputedStyle:w.getComputedStyle.bind(w)});vm.runInContext(source,ctx);
 return {dom,w,call:(phase='discover')=>ctx.accountEntryDom(phase,{account:{id:aid,scene,tab_group:'示例分组B'},job_id:'entry-one'})};
}
test('native cost view and material entry are readonly and detail ID is verified after navigation',()=>{
 const f=fixture('<div><button>放量投放</button><button id="cost">控成本投放</button></div><table><tbody><tr><td>\n投放中\n</td><td><button id="material">素材 ›</button><button id="ad-create">追投</button></td></tr></tbody></table>');let views=0,opens=0,ads=0;
 f.w.document.querySelector('#cost').onclick=()=>views++;f.w.document.querySelector('#material').onclick=()=>opens++;f.w.document.querySelector('#ad-create').onclick=()=>ads++;
 assert.equal(f.call().code,'view_switch_requested');assert.equal(views,1);assert.equal(opens,0);
 assert.equal(f.call().code,'detail_open_requested');assert.equal(opens,1);assert.equal(ads,0);
 f.dom.reconfigure({url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${aid}&adId=${pid}`});
 f.w.document.body.insertAdjacentHTML('beforeend',`<div class="oc-promotion-key-info-bar">计划ID：${pid}</div>`);
 assert.equal(f.call().code,'ready');assert.equal(f.call().plan_id,pid);assert.equal(ads,0);
});
test('editor, manual row selection and mismatched account prevent native navigation',()=>{
 for(const body of ['<div role="dialog"><input></div>','<table><tbody><tr><td><input type="checkbox" checked></td></tr></tbody></table>']){
  const f=fixture(body);assert.equal(f.call('guard').code,'editor_open');
 }
 const f=fixture();f.dom.reconfigure({url:'https://qianchuan.jinritemai.com/uni-prom?aavid=999999999'});assert.equal(f.call().code,'identity_unready');
});
test('only same-account native detail links are returned and multiple running plans do not select arbitrarily',()=>{
 const link=id=>`<tr><td>\n投放中\n</td><td><a href="/uni-prom/detail?aavid=${aid}&adId=${id}">素材</a></td></tr>`;
 const f=fixture('<table><tbody>'+link(pid)+'</tbody></table>');assert.equal(new URL(f.call().next_url).searchParams.get('adId'),pid);
 const g=fixture('<table><tbody>'+link(pid)+link('8651948521238353')+'</tbody></table>');assert.equal(g.call().code,'multiple_running_plans');
 const h=fixture('<table><tbody><tr><td>\n投放中\n</td><td><a href="https://evil.example/"><span>素材</span></a></td></tr></tbody></table>');let clicks=0;h.w.document.querySelector('a').onclick=()=>clicks++;
 assert.equal(h.call().code,'native_entry_missing');assert.equal(clicks,0);
});
test('an aweme DOM id is never used as the parent plan ID',()=>{
 const f=fixture('<table><tbody><tr id="79987870170"><td>\n投放中\n</td><td>抖音号</td></tr></tbody></table>');assert.equal(f.call().code,'native_entry_missing');assert.equal(f.call().plan_id,undefined);
});
test('product entry needs its own account label and does not invent a live parent',()=>{
 const f=fixture('','/overall-prom','product');const r=f.call();assert.equal(r.code,'ready');assert.equal(r.plan_id,null);
 f.w.document.body.textContent='正在加载';assert.equal(f.call().code,'loading');
});
