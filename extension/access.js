/* Optional site access is granted only by the user's popup click. */
const READONLY_HOSTS = Object.freeze(['https://qianchuan.jinritemai.com/*','http://127.0.0.1:8877/*']);
const OBSERVER_KEYS = ['token','enabled','epoch'];
const validObserverToken = value => typeof value==='string' && /^[A-Za-z0-9_-]{32,100}$/.test(value);
const observerEpoch = () => crypto.randomUUID();
async function observerHasAccess(){
  try{return await chrome.permissions.contains({origins:[...READONLY_HOSTS]});}catch{return false;}
}
async function observerCheckpoint(initial){
  const current=await chrome.storage.local.get(OBSERVER_KEYS);
  return current.enabled===true && current.token===initial.token && (current.epoch||'')===(initial.epoch||'') && await observerHasAccess();
}
