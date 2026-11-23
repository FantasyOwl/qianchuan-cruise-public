import test from 'node:test';
import assert from 'node:assert/strict';
import {waitCheckpoint} from '../monitor/ego/wait-checkpoint.mjs';

test('polling shares only a recent successful lease check and checks again after one second',async()=>{
 let at=0,calls=0;const check=waitCheckpoint(async()=>{calls++;},{clock:()=>at});
 await check();at=200;await check();at=999;await check();assert.equal(calls,1);
 at=1000;await check();assert.equal(calls,2);at=10;await check();assert.equal(calls,3);
});
test('a rejected lease is never cached and overlapping waits share the same check',async()=>{
 let calls=0;const check=waitCheckpoint(async()=>{calls++;if(calls===1)throw Error('本户已暂停');});
 await assert.rejects(check(),/本户已暂停/);await Promise.all([check(),check()]);assert.equal(calls,2);
});
