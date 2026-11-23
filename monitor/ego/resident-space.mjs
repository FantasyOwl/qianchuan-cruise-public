import {requireSpace} from './worker-context.mjs';

// Establish the real account Space even while API work has no browser command.
// A heartbeat alone cannot prove that the resident agent has started.
export async function ensureResidentSpace(paths,state,resumeGeneration,sdk){
  const spaces=await sdk.listTaskSpaces();
  let old=state.spaceId===undefined?null:spaces.find(s=>s.id===state.spaceId);
  requireSpace(paths,old);
  if(!old){
    if(state.spaceId!==undefined&&state.resumeGeneration===resumeGeneration)throw Error('EGO_USER_CONTROL: 常驻 Space 已关闭，请在巡航控制台恢复浏览器控制');
    const named=spaces.filter(s=>s.name===paths.spaceName);
    if(named.length>1)throw Error('EGO_USER_CONTROL: 本户常驻空间不唯一，保留现有页面');
    old=named[0];requireSpace(paths,old);
  }
  let task;
  if(old&&old.ownership!=='agent'){
    const permitted=state.finishedByUs===true&&old.ownership==='user'||Number.isInteger(state.resumeGeneration)&&state.resumeGeneration!==resumeGeneration;
    if(!permitted)throw Error('EGO_USER_CONTROL: 常驻 Space 当前由用户控制，请在巡航控制台点恢复浏览器控制');
    task=old.ownership==='agentDelegatedToUser'?await sdk.takeOverTaskSpace(old.id):await sdk.claimTaskSpace(old.id);
  }else task=await sdk.taskSpace(old?old.id:paths.spaceName);
  state.spaceId=task.spaceId;state.resumeGeneration=resumeGeneration;state.finishedByUs=false;
  return task;
}
