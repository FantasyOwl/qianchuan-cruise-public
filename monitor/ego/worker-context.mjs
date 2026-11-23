import path from 'node:path';

// Only a local launcher selects the private configuration. The website cannot
// select credentials, a different account, or the runtime file.
export function workerPaths(root,configPath){
  if(configPath===undefined)return {configPath:path.join(root,'data/ego-executor-private.json'),statePath:path.join(root,'data/ego-executor-runtime.json'),accountId:null,spaceName:'千川巡航常驻'};
  const base=path.join(root,'data/ego-workers'),resolved=path.resolve(configPath),relative=path.relative(base,resolved);
  const parts=relative.split(path.sep);
  if(parts.length!==2||!/^\d{6,30}$/.test(parts[0])||parts[1]!=='private.json')throw Error('本户执行配置路径无效');
  return {configPath:resolved,statePath:path.join(base,parts[0],'runtime.json'),accountId:parts[0],spaceName:'千川巡航 · '+parts[0]};
}

export function requireAccount(paths,config,command){
  if(paths.accountId&&(config.account_id!==paths.accountId||command&&command.account_id!==paths.accountId))throw Error('网页命令不属于本户执行器');
}

export function detailPath(entry){
  if(entry==='chengfang')return '/overall-prom/detail';
  if(entry==='uni_promotion')return '/uni-prom/detail';
  return null;
}

export function requireSpace(paths,space){
  if(paths.accountId&&space&&space.name!==paths.spaceName)throw Error('EGO_USER_CONTROL: 本户Space对应关系不符，已暂停本户');
}
