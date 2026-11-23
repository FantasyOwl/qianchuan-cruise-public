'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const source=fs.readFileSync(require.resolve('../extension/hybrid-probe-dom.js'),'utf8'),aid='1999999999999002',pid='8218616704149013';
function fixture(){
 const dom=new JSDOM(`<div>ID：${aid}</div><div>计划ID：${pid}</div><a data-home href="/overall-prom?aavid=${aid}">推直播间</a><table><tbody><tr data-row-key="native-row"><td>投放中</td><td><button>追投</button><a href="/overall-prom/detail?aavid=${aid}&adId=${pid}&access_token=never-return-this">素材</a></td></tr></tbody></table><input placeholder="请输入计划ID" value="never-return-input-value"><input type="password" value="password-secret"><input type="hidden" value="hidden-secret"><textarea>textarea-secret</textarea><div contenteditable="true">editable-secret</div><div id="empty"><script>script-secret</script></div>`,{url:'https://qianchuan.jinritemai.com/overall-prom/detail?aavid='+aid+'&adId='+pid});
 const w=dom.window,d=w.document;Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.id==='empty'||['SCRIPT','INPUT'].includes(this.tagName)?'':this.textContent;}});
 w.HTMLElement.prototype.getClientRects=function(){for(let e=this;e;e=e.parentElement)if(e.style?.display==='none'||['SCRIPT','STYLE'].includes(e.tagName))return [];return this.isConnected?[{}]:[];};
 let clicks=0;d.querySelector('[data-home]').onclick=e=>{e.preventDefault();clicks++;};
 const c={phase:'dom_probe',preview:true,account_id:aid,plan_id:pid,group:'示例分组A',probe:{selectors:['tr','input','#empty']}};
 const context=vm.createContext({URL,URLSearchParams,Date,document:d,location:w.location,getComputedStyle:w.getComputedStyle.bind(w)});vm.runInContext(source,context);
 return {d,c,call:phase=>context.hybridProbeDom(phase,{command:c}),get clicks(){return clicks;}};
}
test('a rendered probe captures native row metadata and sanitized links without input values or hidden state',()=>{
 const f=fixture(),r=f.call('inspect');assert.equal(r.ok,true,r.message);const raw=JSON.stringify(r);
 for(const secret of ['never-return-this','never-return-input-value','password-secret','hidden-secret','textarea-secret','editable-secret','script-secret'])assert.equal(raw.includes(secret),false,secret);
 assert.equal(r.observation.rows.tr[0].attrs['data-row-key'],'native-row');assert.equal(r.observation.rows.input.length,1);assert.equal(r.observation.rows['#empty'][0].text,'');assert.equal(f.clicks,0);
});
test('only an explicit read-only command may return home and it never clicks a chase button',()=>{
 const f=fixture();let chase=0;f.d.querySelector('button').onclick=()=>chase++;
 assert.equal(f.call('home').navigated,true);assert.equal(f.clicks,1);assert.equal(chase,0);
 f.c.preview=false;assert.equal(f.call('home').ok,false);assert.equal(f.clicks,1);
});
test('foreign account labels, existing editors and manual selections prevent probe navigation',()=>{
 for(const type of ['account','editor','selection']){
  const f=fixture();if(type==='account')f.d.querySelector('body>div').textContent='ID：8086606404052108';
  if(type==='editor')f.d.body.insertAdjacentHTML('beforeend','<div role="dialog"><input value="人工输入"></div>');
  if(type==='selection')f.d.querySelector('td').innerHTML='<input type="checkbox" checked>';
  assert.equal(f.call('home').ok,false,type);assert.equal(f.clicks,0);
 }
});
test('a probe bounds each selector to eight visible nodes and cannot run supplied JavaScript',()=>{
 const f=fixture();for(let n=0;n<30;n++)f.d.querySelector('tbody').append(f.d.querySelector('tr').cloneNode(true));
 assert.equal(f.call('inspect').observation.rows.tr.length,8);f.c.probe.selectors=['(()=>{throw Error("executed")})()'];
 const r=f.call('inspect');assert.equal(r.ok,false);assert.equal(f.clicks,0);
});
