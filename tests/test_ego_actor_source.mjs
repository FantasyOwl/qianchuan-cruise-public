import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import {actorExpression} from '../monitor/ego/actor-source.mjs';
const require=createRequire(import.meta.url);
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const sources={detail:fs.readFileSync(new URL('../extension/hybrid-dom.js',import.meta.url),'utf8'),home:fs.readFileSync(new URL('../extension/hybrid-home-dom.js',import.meta.url),'utf8'),probe:fs.readFileSync(new URL('../extension/hybrid-probe-dom.js',import.meta.url),'utf8')};

test('the resident actor checks detail identity before home navigation without a material table',async()=>{
 const aid='1999999999999002',pid='8218616704149013';
 const dom=new JSDOM(`<h1>千川乘方</h1><div>ID：${aid}</div><div>计划ID：${pid}\n预算(元)：每日500,000.00\n综合营销ROI目标：3.40</div><div>抖音号ID：79987870170</div><button role="switch" aria-checked="true"></button><a href="/overall-prom?aavid=${aid}">推直播间</a>`,{url:`https://qianchuan.jinritemai.com/overall-prom/detail?aavid=${aid}&adId=${pid}`});
 const w=dom.window;Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});w.HTMLElement.prototype.getClientRects=function(){return this.isConnected?[{width:100,height:30}]:[];};
 const context=vm.createContext({URL,Date,document:w.document,location:w.location,window:w,getComputedStyle:w.getComputedStyle.bind(w),Event:w.Event,MouseEvent:w.MouseEvent});
 const request={command:{account_id:aid,plan_id:pid,group:'测试户',direct_home:true}};
 const result=await vm.runInContext(actorExpression('home_identity',sources,request),context);
 assert.equal(result.ok,true,result.message);assert.equal(result.observation.kind,'direct_home_identity');assert.equal(result.binding.plan_id,pid);
 const wrongHome=await vm.runInContext(actorExpression('home_open',sources,request),context);
 assert.equal(wrongHome.ok,false);assert.match(wrongHome.message,/首页账户或入口不符/);
});

test('the native parent status outranks an unrelated enabled switch and reports a paused parent',async()=>{
 for(const state of ['投放中','已暂停']){
  const aid='8929253702525378',pid='8582197091416415';
  const dom=new JSDOM(`<div>ID：${aid}</div><div class="oc-promotion-key-info-bar"><span>${state}</span><span>计划ID：${pid}</span><span>抖音号ID：zlp_7777777</span><div>预算(元)：每日300,000.00</div><div>净成交ROI目标：2.13</div></div><div class="col-status-switch"><div class="ovui-switch ovui-switch--checked"></div></div><button role="switch" aria-checked="false"></button><a href="/uni-prom?aavid=${aid}">推直播间</a>`,{url:`https://qianchuan.jinritemai.com/uni-prom/detail?aavid=${aid}&adId=${pid}`});
  const w=dom.window;Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});w.HTMLElement.prototype.getClientRects=function(){return this.isConnected?[{width:100,height:30}]:[];};
  const context=vm.createContext({URL,Date,document:w.document,location:w.location,window:w,getComputedStyle:w.getComputedStyle.bind(w),Event:w.Event,MouseEvent:w.MouseEvent});
  const result=await vm.runInContext(actorExpression('home_identity',sources,{command:{account_id:aid,plan_id:pid,group:'示例分组C',executor_kind:'ego',direct_home:true}}),context);
  assert.equal(result.ok,state==='投放中',result.message);
  if(state==='已暂停')assert.match(result.message,/母计划当前已暂停/);else assert.equal(result.binding.aweme_id,'zlp_7777777');
 }
});

test('Ego home navigation returns only a validated native target without synthetic clicking',async()=>{
 const aid='1999999999999002',pid='8218616704149013';
 const dom=new JSDOM(`<div>ID：${aid}</div><div>计划ID：${pid}</div><a href="/overall-prom?aavid=${aid}">推直播间</a>`,{url:`https://qianchuan.jinritemai.com/overall-prom/detail?aavid=${aid}&adId=${pid}`});
 const w=dom.window;Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});w.HTMLElement.prototype.getClientRects=function(){return this.isConnected?[{}]:[];};
 let clicks=0;w.document.querySelector('a').onclick=e=>{clicks++;e.preventDefault();};
 const context=vm.createContext({URL,Date,document:w.document,location:w.location,window:w,getComputedStyle:w.getComputedStyle.bind(w),Event:w.Event,MouseEvent:w.MouseEvent});
 const result=await vm.runInContext(actorExpression('home_go',sources,{command:{id:'native-cmd',account_id:aid,plan_id:pid,group:'测试户',executor_kind:'ego'}}),context);
 assert.equal(result.ok,true,result.message);assert.equal(clicks,0);assert.equal(w.document.querySelector(result.native_selector).tagName,'A');
});
