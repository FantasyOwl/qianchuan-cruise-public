// Keep detail identity checks on the detail reader; only native homepage
// controls belong to the home reader, even though both use a home_ prefix.
export function actorRoute(phase){
  if(phase.startsWith('probe_'))return {source:'probe',namespace:'hybridProbeDom',phase:phase.slice(6)};
  if(phase.startsWith('home_')&&!['home_go','home_identity'].includes(phase))return {source:'home',namespace:'hybridHomeDom',phase:phase.slice(5)};
  return {source:'detail',namespace:'hybridChaseDom',phase};
}

export function actorExpression(phase,sources,request){
  const route=actorRoute(phase);
  return `(async()=>{const chrome=globalThis.__qcEgoChrome;${sources[route.source]}\nreturn await ${route.namespace}(${JSON.stringify(route.phase)},${JSON.stringify(request)});})()`;
}
