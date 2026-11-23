import test from 'node:test';import assert from 'node:assert/strict';import {createRequire} from 'node:module';
import {nativeDetailScope} from '../monitor/ego/native-route.mjs';
const require=createRequire(import.meta.url);let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const aid='8929253702525378',pid='8582197091416415';
function fixture({url='https://qianchuan.jinritemai.com/uni-prom/detail?aavid='+aid+'&adId=',text='计划ID：'+pid}={}){
 const {window:w}=new JSDOM('<p>'+text+'</p>',{url,runScripts:'outside-only'});w.HTMLElement.prototype.getClientRects=()=>[{}];
 Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});w.history.replaceState({keep:'native'},'');
 return {w,call:args=>w.eval('('+nativeDetailScope.toString()+')')({aid,pid,entry:'uni_promotion',repair:true,...args})};
}
test('empty native adId can only be restored from the exact visible parent and API family',()=>{
 const f=fixture();assert.deepEqual(JSON.parse(JSON.stringify(f.call())),{ready:true,changed:true});assert.equal(new URL(f.w.location.href).searchParams.get('adId'),pid);assert.equal(f.w.history.state.keep,'native');
});
test('wrong, duplicate, foreign or ambiguous identities never change the viewing URL',()=>{
 for(const args of [ {url:'https://qianchuan.jinritemai.com/uni-prom/detail?aavid='+aid+'&adId=99999999'},
 {url:'https://qianchuan.jinritemai.com/uni-prom/detail?aavid='+aid+'&adId=&adId='+pid},
 {url:'https://evil.example/uni-prom/detail?aavid='+aid+'&adId='},
 {text:'计划ID：99999999'}, {text:'计划ID：'+pid+'</p><p>计划ID：99999999'}]){
  const f=fixture(args),before=f.w.location.href;assert.equal(f.call().ready,false);assert.equal(f.w.location.href,before);
 }
 const f=fixture();assert.equal(f.call({entry:'chengfang'}).ready,false);assert.equal(f.call({entry:undefined}).ready,false);
});
