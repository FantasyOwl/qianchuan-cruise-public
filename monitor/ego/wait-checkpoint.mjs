// Polling waits may share a recent read lease check. Native input boundaries
// and the monetary final permit continue to check independently every time.
export function waitCheckpoint(check,{clock=Date.now,maxAge=1000}={}){
  let checked=null,pending=null;
  return async()=>{
    const now=clock();
    if(checked!==null&&now>=checked&&now-checked<maxAge)return;
    if(pending)return pending;
    pending=Promise.resolve().then(check).then(()=>{checked=clock();},error=>{checked=null;throw error;});
    try{await pending;}finally{pending=null;}
  };
}
