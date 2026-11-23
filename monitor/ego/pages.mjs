export function accountOf(address){try{const u=new URL(address);return u.origin==='https://qianchuan.jinritemai.com'&&u.searchParams.getAll('aavid').length===1?u.searchParams.get('aavid'):null;}catch{return null;}}

export async function resolveAccountPage(task,state,aid){
  const entries=await task.pages();
  // Labels are scoped to a claim. After finish/adopt the same label may name
  // another account; the live URL is the authority, never the cached label.
  let found=null;
  const mapped=entries.find(p=>p.label===state.pages[aid]);
  if(mapped&&accountOf(await mapped.url())===aid)found=mapped;
  if(!found){
    const tabs=(await task.tabs()).filter(t=>accountOf(t.url)===aid);
    if(tabs.length>1)throw Error('Ego 本户页面不唯一，请在常驻 Space 保留一个账户标签');
    if(tabs.length)found=tabs[0].label?task.page(tabs[0].label):await task.adopt(tabs[0].page);
    else if(entries.length===1&&/^(?:about:blank|chrome:\/\/newtab\/?)$/.test(await entries[0].url()))found=entries[0];
    else found=await task.newPage();
  }
  for(const other of Object.keys(state.pages))if(other!==aid&&state.pages[other]===found.label)delete state.pages[other];
  state.pages[aid]=found.label;
  return found;
}
