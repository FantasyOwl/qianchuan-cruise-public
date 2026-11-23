import test from 'node:test';import assert from 'node:assert/strict';import path from 'node:path';
import {workerPaths,requireAccount,detailPath,requireSpace} from '../monitor/ego/worker-context.mjs';
const root=path.resolve('test-root'),aid='1999999999999002';
test('blank account spaces route using the exact API parent family',()=>{
 assert.equal(detailPath('chengfang'),'/overall-prom/detail');assert.equal(detailPath('uni_promotion'),'/uni-prom/detail');assert.equal(detailPath('both'),null);
});
test('each account has its own runtime and space, keeping serial compatibility',()=>{
 const a=workerPaths(root,path.join(root,'data/ego-workers',aid,'private.json')),b=workerPaths(root,path.join(root,'data/ego-workers','8531163845726695','private.json'));
 assert.notEqual(a.statePath,b.statePath);assert.notEqual(a.spaceName,b.spaceName);assert.equal(a.accountId,aid);
 assert.equal(workerPaths(root).accountId,null);assert.match(workerPaths(root).statePath,/ego-executor-runtime/);
});
test('foreign account commands cannot reach a browser',()=>{
 const p=workerPaths(root,path.join(root,'data/ego-workers',aid,'private.json'));
 requireAccount(p,{account_id:aid},{account_id:aid});assert.throws(()=>requireAccount(p,{account_id:aid},{account_id:'8531163845726695'}),/不属于/);
 assert.throws(()=>requireAccount(p,{account_id:'8531163845726695'}),/不属于/);
});
test('an aliased runtime Space cannot take over another accounts pages',()=>{
 const p=workerPaths(root,path.join(root,'data/ego-workers',aid,'private.json'));
 requireSpace(p,{name:p.spaceName});assert.throws(()=>requireSpace(p,{name:'千川巡航 · 8531163845726695'}),/Space对应关系不符/);
});
test('configuration cannot select an arbitrary private file or escaped runtime path',()=>{
 for(const file of ['../../vault.json','data/ego-workers/../private.json','data/ego-workers/not-account/private.json','data/ego-workers/1999999999999002/other.json'])assert.throws(()=>workerPaths(root,path.resolve(root,file)),/路径无效/);
});
