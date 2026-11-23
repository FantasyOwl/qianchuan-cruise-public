'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const load=()=>import('../monitor/ego/home-entry.mjs');
const located=()=>({result:{ok:true,hover_selector:'[data-qcx-home-hover="cmd"]',hover_locator:'xpath=//tr[@data-plan-id="8218616704149013"]'}});
const missing=()=>({result:{ok:false,opened:false,fallback:true,submitted:false,message:'首页素材追投加号未唯一明确；本模式不回素材页预搜'}});
test('native rerender can rehover the verified parent and opens exactly one form',async()=>{
 const {openBoundHome}=await load();let hovers=0,locates=0,opens=0;
 const page={hover:async selector=>{assert.match(selector,/8218616704149013/);hovers++;}};
 const actor=async phase=>{if(phase==='home_locate'){locates++;return located();}opens++;return opens===1?missing():{result:{ok:true,opened:true}};};
 const r=await openBoundHome(page,actor,located());assert.equal(r.result.opened,true);assert.equal(hovers,2);assert.equal(locates,1);assert.equal(opens,2);
});
test('persistent ambiguity remains bounded without a financial submit',async()=>{
 const {openBoundHome}=await load();let hovers=0,opens=0;
 const r=await openBoundHome({hover:async()=>hovers++},async phase=>phase==='home_locate'?located():(opens++,missing()),located());
 assert.equal(r.result.ok,false);assert.equal(hovers,3);assert.equal(opens,3);
});
test('unknown editors, changed parents and ownership stops do not retry opening',async()=>{
 const {openBoundHome}=await load();
 for(const failure of [{ok:false,fallback:false,submitted:false,message:'首页已有编辑窗口，保留输入'},{ok:false,fallback:true,submitted:false,message:'首页母计划行已变化，未打开追投'},{ok:false,fallback:true,message:'首页素材追投加号未唯一明确'}]){
  let opens=0;await openBoundHome({hover:async()=>{}},async()=>{opens++;return {result:failure};},located());assert.equal(opens,1);
 }
 let calls=0;
 await assert.rejects(openBoundHome({hover:async()=>{throw Error('EGO_USER_CONTROL');}},async()=>{calls++;},located()),/EGO_USER_CONTROL/);assert.equal(calls,0);
 await assert.rejects(openBoundHome({hover:async()=>{}},async phase=>phase==='home_locate'?{result:{ok:false,message:'账户不符'}}:missing(),located()),/账户不符/);
});
