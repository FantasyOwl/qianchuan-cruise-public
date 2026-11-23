import test from 'node:test';import assert from 'node:assert/strict';
import {resolveAccountPage,accountOf} from '../monitor/ego/pages.mjs';
const url=aid=>'https://qianchuan.jinritemai.com/uni-prom?aavid='+aid;
test('reused label cannot bind cotton to the bra page after a new claim',async()=>{
 const bra={label:'p2',url:async()=>url('bra')},cotton={label:'p3',url:async()=>url('cotton')};let adopted=0;
 const task={pages:async()=>[bra],tabs:async()=>[{label:'p2',url:url('bra')},{url:url('cotton'),page:{native:true}}],adopt:async()=>{adopted++;return cotton;}};
 const state={pages:{cotton:'p2',bra:'p2'}};assert.equal(await resolveAccountPage(task,state,'cotton'),cotton);assert.equal(adopted,1);assert.equal(state.pages.cotton,'p3');assert.equal(state.pages.bra,'p2');
});
test('a new mapping removes aliases for other accounts',async()=>{
 const p={label:'p1',url:async()=>url('cotton')};const task={pages:async()=>[p],tabs:async()=>[{label:'p1',url:url('cotton')}],page:()=>p};
 const state={pages:{cotton:'p2',bra:'p1'}};await resolveAccountPage(task,state,'cotton');assert.deepEqual(state.pages,{cotton:'p1'});
});
test('ambiguous native account tabs remain blocked instead of guessed',async()=>{
 const task={pages:async()=>[],tabs:async()=>[{url:url('cotton')},{url:url('cotton')}]};await assert.rejects(resolveAccountPage(task,{pages:{}},'cotton'),/不唯一/);
});
test('foreign origins and duplicate account parameters never identify an account',()=>{
 assert.equal(accountOf('https://evil.example/uni-prom?aavid=cotton'),null);assert.equal(accountOf(url('cotton')+'&aavid=bra'),null);
});
