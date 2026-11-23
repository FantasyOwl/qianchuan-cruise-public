// Re-hover only an already verified parent when native rendering moved its
// tool. No retry follows an opened editor, other failure, or a control stop.
export async function openBoundHome(page,actor,initial){
  let located=initial,opened;
  for(let attempt=0;attempt<3;attempt++){
    if(attempt)located=await actor('home_locate');
    if(located.result?.ok!==true)throw Error(located.result?.message||'首页主计划未重新核对');
    if(!located.result.hover_selector)throw Error('首页追投入口尚未核对，未打开表单');
    await page.hover(located.result.hover_locator||'loc=css:'+located.result.hover_selector,{label:'显示本户素材追投加号'});
    opened=await actor('home_open');
    if(opened.result?.opened===true)return opened;
    if(opened.result?.fallback!==true||opened.result?.submitted!==false||!/^首页素材追投加号未唯一明确/.test(opened.result?.message||''))return opened;
  }
  return opened;
}
