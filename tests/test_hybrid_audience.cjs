'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const {hybridAudienceDom}=require('../extension/hybrid-audience-dom.js');
function fixture({broken=false,asyncAge=false,entry='设置定向'}={}){
 const dom=new JSDOM('<div id="form"><section><span>定向人群</span><p id="summary"></p><button>'+entry+'</button></section><button id="submit">提交</button></div><div id="panel" role="dialog" hidden><div>设置定向</div></div>');
 const d=dom.window.document,form=d.querySelector('#form'),panel=d.querySelector('#panel');let submits=0;
 let committedAge=[0],pendingAge=null;
 for(const [label,choices,type] of [['性别',['不限','男','女'],'radio'],['年龄',['不限','18-23','24-30','31-40','41-49','50+'],'checkbox'],['抖音号粉丝',['不限','排除'],'radio']]){
  const group=d.createElement('section');group.innerHTML='<span>'+label+'</span><div>'+choices.map((c,i)=>'<label><input type="'+type+'" name="'+label+'" '+(i===0?'checked':'')+'>'+c+'</label>').join('')+'</div>';
  panel.append(group);
  for(const input of group.querySelectorAll('input'))input.addEventListener('click',()=>{
   if(label==='年龄'){
    if(asyncAge){const index=[...group.querySelectorAll('input')].indexOf(input);pendingAge=index===0?[0]:committedAge.includes(index)?committedAge.filter(v=>v!==index):[...committedAge.filter(v=>v!==0),index];}
    else if(input.parentElement.textContent==='不限'){for(const e of group.querySelectorAll('input'))e.checked=e===input;}else group.querySelector('input').checked=false;
   }
  });
 }
 const save=d.createElement('button');save.textContent='确定';panel.append(save);
 form.querySelector('section button').onclick=()=>{form.hidden=true;panel.hidden=false;};
 save.onclick=()=>{panel.hidden=true;form.hidden=false;d.querySelector('#summary').textContent=broken?'未保存': [...panel.querySelectorAll('input:checked')].map(e=>e.parentElement.textContent).join(' ')+' 抖音号粉丝';};
 d.querySelector('#submit').onclick=()=>submits++;
 const text=e=>e.textContent.trim(),visible=e=>e.isConnected&&!e.hidden&&!e.closest('[hidden]');
 const exact=(root,label)=>[...root.querySelectorAll('button,span,div,label')].filter(e=>visible(e)&&text(e)===label&&![...e.children].some(c=>text(c)===label));
 const env={text,visible,exact,pause:async()=>{if(pendingAge){committedAge=pendingAge;pendingAge=null;const g=[...panel.querySelectorAll('section')].find(g=>g.firstElementChild.textContent==='年龄');[...g.querySelectorAll('input')].forEach((e,i)=>e.checked=committedAge.includes(i));}},clickNative:e=>e.click(),roots:()=>panel.hidden?[]:[panel],chaseForm:async()=>form};
 return {dom,d,form,panel,env,submits:()=>submits};
}
test('gender, multiple age bands and fan exclusion are checked and saved without submitting an ad',async()=>{
 const f=fixture(),value={gender:'female',ages:['18-23','31-40'],exclude_fans:true};
 const r=await hybridAudienceDom(f.form,value,f.env);assert.equal(r.check(),true);assert.equal(r.proof.gender,'female');assert.deepEqual(r.proof.ages,value.ages);assert.equal(r.proof.exclude_fans,true);assert.equal(f.submits(),0);
 f.d.querySelector('#summary').textContent='用户改动';assert.equal(r.check(),false);
});
test('unlimited options and unsupported ages cannot become an accidental targeted submit',async()=>{
 const f=fixture(),r=await hybridAudienceDom(f.form,{gender:'any',ages:[],exclude_fans:false},f.env);assert.equal(r.check(),true);assert.equal(f.submits(),0);
 await assert.rejects(hybridAudienceDom(f.form,{gender:'any',ages:['0-17'],exclude_fans:false},f.env),/规则无效/);
});
test('a native form that does not retain the targeting summary stops before ad submission',async()=>{
 const f=fixture({broken:true});await assert.rejects(hybridAudienceDom(f.form,{gender:'female',ages:['18-23'],exclude_fans:true},f.env),/摘要未显示/);assert.equal(f.submits(),0);
});
test('native plus entry and deferred checkbox commits retain all selected age bands',async()=>{
 const f=fixture({asyncAge:true,entry:'+ 设置定向'}),value={gender:'female',ages:['18-23','31-40'],exclude_fans:true};
 const r=await hybridAudienceDom(f.form,value,f.env);assert.deepEqual(r.proof.ages,value.ages);assert.equal(r.check(),true);assert.equal(f.submits(),0);
});
