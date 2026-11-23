import test from 'node:test';
import assert from 'node:assert/strict';
import {ensureResidentSpace} from '../monitor/ego/resident-space.mjs';
const aid='8531163845726695',paths={accountId:aid,spaceName:'千川巡航 · '+aid};
function fixture(spaces){const calls=[];return {calls,sdk:{listTaskSpaces:async()=>spaces,taskSpace:async id=>(calls.push(['open',id]),{spaceId:typeof id==='number'?id:21}),claimTaskSpace:async id=>(calls.push(['claim',id]),{spaceId:id}),takeOverTaskSpace:async id=>(calls.push(['takeover',id]),{spaceId:id})}};}
test('an idle account resumes its own finished Space without creating a duplicate',async()=>{
 const f=fixture([{id:11,name:paths.spaceName,ownership:'user'}]),state={spaceId:11,resumeGeneration:4,finishedByUs:true};
 const task=await ensureResidentSpace(paths,state,4,f.sdk);assert.equal(task.spaceId,11);assert.deepEqual(f.calls,[['claim',11]]);assert.equal(state.finishedByUs,false);
});
test('idle heartbeat respects manual control and a closed Space until an explicit resume',async()=>{
 const f=fixture([{id:11,name:paths.spaceName,ownership:'user'}]),state={spaceId:11,resumeGeneration:4,finishedByUs:false};
 await assert.rejects(ensureResidentSpace(paths,state,4,f.sdk),/用户控制/);assert.deepEqual(f.calls,[]);
 await ensureResidentSpace(paths,state,5,f.sdk);assert.deepEqual(f.calls,[['claim',11]]);
 const missing=fixture([]);await assert.rejects(ensureResidentSpace(paths,state,5,missing.sdk),/已关闭/);assert.deepEqual(missing.calls,[]);
 await ensureResidentSpace(paths,state,6,missing.sdk);assert.deepEqual(missing.calls,[['open',paths.spaceName]]);
});
test('an already running account keeps the same Space; a foreign ID never gains control',async()=>{
 const f=fixture([{id:11,name:paths.spaceName,ownership:'agent'}]);await ensureResidentSpace(paths,{spaceId:11,resumeGeneration:4},4,f.sdk);assert.deepEqual(f.calls,[['open',11]]);
 const other=fixture([{id:11,name:'other account',ownership:'agent'}]);await assert.rejects(ensureResidentSpace(paths,{spaceId:11},5,other.sdk),/对应关系不符/);assert.deepEqual(other.calls,[]);
});
test('initial startup reuses a unique canonical Space and rejects ambiguous names',async()=>{
 const f=fixture([{id:11,name:paths.spaceName,ownership:'agent'}]);await ensureResidentSpace(paths,{},1,f.sdk);assert.deepEqual(f.calls,[['open',11]]);
 const duplicate=fixture([{id:11,name:paths.spaceName,ownership:'agent'},{id:12,name:paths.spaceName,ownership:'agent'}]);await assert.rejects(ensureResidentSpace(paths,{},1,duplicate.sdk),/不唯一/);assert.deepEqual(duplicate.calls,[]);
 const owned=fixture([{id:11,name:paths.spaceName,ownership:'user'}]);await assert.rejects(ensureResidentSpace(paths,{},0,owned.sdk),/用户控制/);assert.deepEqual(owned.calls,[]);
});
