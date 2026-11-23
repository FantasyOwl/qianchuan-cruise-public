// Read-only ID search via Ego's native input path. Never budget or submit fields.
export async function nativeMaterialSearch(page,c,m,checkpoint){
  const batch=c.action?.intent?.browser_batch;
  if(c.rule_job||c.direct_home&&m.search_kind!=='picker'||!['prepare','dry_prepare'].includes(c.phase)||batch?.selection_source!=='official_api'
    ||m.run_id!==c.run_id||m.command_id!==c.id||!['main','picker'].includes(m.search_kind)
    ||!/^\d{6,30}$/.test(m.material_id||'')||!batch.material_ids?.includes(m.material_id)
    ||!/^[a-zA-Z0-9_-]{1,128}$/.test(c.id||''))throw Error('原生搜索不属于当前批次');
  await checkpoint();
  const marker=c.id+'-'+m.search_kind,selector='input[data-qcx-native-search="'+marker+'"]';
  const valid=await page.evaluate(({selector,account,plan,kind})=>{
    const u=new URL(location.href),inputs=[...document.querySelectorAll(selector)];
    return u.origin==='https://qianchuan.jinritemai.com'&&u.searchParams.getAll('aavid').length===1&&u.searchParams.get('aavid')===account
      &&(['/uni-prom/detail','/overall-prom/detail'].includes(u.pathname)?u.searchParams.getAll('adId').length===1&&u.searchParams.get('adId')===plan
        :kind==='picker'&&['/uni-prom','/overall-prom'].includes(u.pathname))
      &&inputs.length===1&&inputs[0].getClientRects().length>0&&!inputs[0].disabled
      &&inputs[0].type!=='password'&&/视频名称.*ID|输入视频名称\s*\/?\s*ID/.test(inputs[0].placeholder);
  },{selector,account:c.account_id,plan:c.plan_id,kind:m.search_kind});
  if(!valid)throw Error('原生搜索的页面或素材ID输入框不符');
  await page.fill('loc=css:'+selector,m.material_id);
  await page.press('loc=css:'+selector,'Enter');
  await checkpoint();
  return {ok:true};
}

export async function nativeTaskStateFilter(page,c,m,checkpoint){
  const wanted=c.phase==='tasks'?'调控中':'全部';
  if(!['tasks','lookup','verify'].includes(c.phase)||m.run_id!==c.run_id||m.command_id!==c.id||m.label!==wanted||!/^[a-zA-Z0-9_-]{1,128}$/.test(c.id||''))throw Error('任务状态筛选不属于当前只读命令');
  await checkpoint();const selector='input[data-qcx-task-state="'+c.id+'"]',optionSelector='[data-qcx-task-state-option="'+c.id+'"]';
  const binding=await page.evaluate(({selector,account,plan})=>{
    const u=new URL(location.href),inputs=[...document.querySelectorAll(selector)],e=inputs[0],prefix=e?.parentElement.querySelector('[class*="input__prefix"]'),anchor=e?.closest('[data-auto-id="oc-row-cascader"][data-e2e]');
    if(u.origin!=='https://qianchuan.jinritemai.com'||!['/uni-prom/detail','/overall-prom/detail'].includes(u.pathname)||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==account||u.searchParams.getAll('adId').length!==1||u.searchParams.get('adId')!==plan||inputs.length!==1||!e.getClientRects().length||e.disabled||!e.readOnly||e.closest('[role="dialog"],[aria-modal="true"],.ovui-modal,.ovui-drawer')||!/^投放状态[:：]$/.test((prefix?.innerText||'').replace(/\s/g,''))||!anchor)return null;
    const menuKey=anchor.getAttribute('data-e2e')+'_popover_content',menus=[...document.querySelectorAll('[data-e2e]')].filter(e=>e.getAttribute('data-e2e')===menuKey&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
    if(menus.length>1)return null;return {menuKey,fieldKey:anchor.getAttribute('data-e2e'),open:menus.length===1};
  },{selector,account:c.account_id,plan:c.plan_id});
  if(!binding)throw Error('任务状态筛选的账户、计划或只读菜单归属不符');
  const inspectMenu=async()=>page.evaluate(({key,label})=>{
    const norm=s=>String(s).replace(/[\s()（）]/g,''),menus=[...document.querySelectorAll('[data-e2e]')].filter(e=>e.getAttribute('data-e2e')===key&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
    return {open:menus.length,options:menus.length===1?[...menus[0].querySelectorAll('[class*="cascader-panel__item-label"]')].filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&(norm(e.innerText)===norm(label)||label==='全部'&&norm(e.innerText)==='全部包括已删除')).length:0};
  },{key:binding.menuKey,label:wanted});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  let menu=await inspectMenu();
  // A click receipt can precede the portal's next render. Do not toggle a
  // menu closed while it is still opening; poll in Node, outside tab timers.
  for(let n=0;!menu.open&&n<3;n++){await sleep(80);menu=await inspectMenu();}
  if(menu.open>1)throw Error('任务状态菜单未唯一绑定');
  if(!menu.open)await page.click('loc=css:'+selector,{label:'打开本户任务状态筛选'});
  const menuDeadline=Date.now()+4000;
  for(;;){menu=await inspectMenu();if(menu.open===1&&menu.options===1)break;if(menu.open>1||menu.options>1)throw Error('任务状态选项未唯一绑定');if(Date.now()>=menuDeadline)throw Error('等待任务状态菜单展开超时');await checkpoint();await sleep(80);}
  const marked=await page.evaluate(({key,label,id})=>{
    const norm=s=>String(s).replace(/[\s()（）]/g,''),menus=[...document.querySelectorAll('[data-e2e]')].filter(e=>e.getAttribute('data-e2e')===key&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
    const options=menus.length===1?[...menus[0].querySelectorAll('[class*="cascader-panel__item-label"]')].filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&(norm(e.innerText)===norm(label)||label==='全部'&&norm(e.innerText)==='全部包括已删除')):[];
    if(options.length!==1)return false;options[0].setAttribute('data-qcx-task-state-option',id);return true;
  },{key:binding.menuKey,label:wanted,id:c.id});
  if(!marked)throw Error('任务状态选项未唯一绑定');
  try{await checkpoint();await page.click('loc=css:'+optionSelector,{label:'选择本户任务查看状态'});
    const selectedDeadline=Date.now()+4000;
    while(!await page.evaluate(({key,label,account,plan})=>{
      const u=new URL(location.href);if(u.origin!=='https://qianchuan.jinritemai.com'||u.searchParams.get('aavid')!==account||u.searchParams.get('adId')!==plan)return false;
      const norm=s=>String(s).replace(/[\s()（）]/g,''),anchors=[...document.querySelectorAll('[data-auto-id="oc-row-cascader"][data-e2e]')].filter(e=>e.getAttribute('data-e2e')===key&&e.getClientRects().length),e=anchors.length===1?anchors[0].querySelector('input'):null;
      return !!e&&(norm(e.value)===norm(label)||label==='全部'&&norm(e.value)==='全部包括已删除');
    },{key:binding.fieldKey,label:wanted,account:c.account_id,plan:c.plan_id})){
      if(Date.now()>=selectedDeadline)throw Error('任务状态选中结果未核验');await checkpoint();await sleep(80);
    }
  }finally{await page.evaluate(selector=>{for(const e of document.querySelectorAll(selector))e.removeAttribute('data-qcx-task-state-option');},optionSelector);}
  await checkpoint();return {ok:true};
}

export async function nativeInventoryHover(page,c,m,checkpoint){
  if(c.rule_job||c.direct_home!==true||c.phase!=='tasks'||m.run_id!==c.run_id||m.command_id!==c.id||!/^\d{6,30}$/.test(m.task_id||'')||typeof m.open_more!=='boolean'||!/^[a-zA-Z0-9_-]{1,128}$/.test(c.id||''))throw Error('任务清单悬停不属于当前读取命令');
  await checkpoint();const selector='[data-qcx-inventory="'+c.id+'-inventory-'+m.task_id+'"]';
  const clearSelector='[data-qcx-inventory-clear="'+c.id+'-inventory-'+m.task_id+'"]';
  const valid=await page.evaluate(({selector,account,plan,task,more,marker})=>{
    const u=new URL(location.href),nodes=[...document.querySelectorAll(selector)],e=nodes[0],row=e?.closest('tr'),cell=e?.closest('td');
    const headers=new Map();for(const tr of document.querySelectorAll('table thead tr'))if(tr.getClientRects().length){const h=[...tr.cells].map(e=>(e.innerText||e.textContent||'').replace(/\s/g,''));if(h.includes('任务名称')&&h.includes('素材')&&h.length===row?.cells.length)headers.set(JSON.stringify(h),h);}
    const h=headers.size===1?[...headers.values()][0]:null;
    const valid=u.origin==='https://qianchuan.jinritemai.com'&&['/uni-prom/detail','/overall-prom/detail'].includes(u.pathname)&&u.searchParams.getAll('aavid').length===1&&u.searchParams.get('aavid')===account&&u.searchParams.getAll('adId').length===1&&u.searchParams.get('adId')===plan
      &&nodes.length===1&&!!e.getClientRects().length&&h&&cell?.cellIndex===h.indexOf('素材')&&new RegExp('(?:^|\\n)ID[：:]\\s*'+task+'(?=\\s|$)').test(row?.cells[h.indexOf('任务名称')]?.innerText||'')
      &&(more?String(e.className).includes('media-group-list-ellipsis'):e.getAttribute('data-auto-id')==='oc-media');
    if(!valid)return false;
    const clear=row.cells[0];if(h[0]!==''||!clear?.getClientRects().length)return false;
    row.scrollIntoView({block:'center',inline:'nearest'});clear.setAttribute('data-qcx-inventory-clear',marker);return true;
  },{selector,account:c.account_id,plan:c.plan_id,task:m.task_id,more:m.open_more,marker:c.id+'-inventory-'+m.task_id});
  if(!valid)throw Error('任务清单悬停的账户、任务或缩略图不符');
  // A real pointer move dismisses the prior overlay; synthetic mouseleave
  // leaves it intercepting the next task's thumbnail on long native tables.
  await page.hover('loc=css:'+clearSelector,{label:'移开上一条素材明细浮层'});
  await page.hover('loc=css:'+selector,{label:'查看本户在投素材清单'});
  if(m.open_more)await page.click('loc=css:'+selector,{label:'展开本户在投素材清单'});
  await checkpoint();return {ok:true,fresh_hover_task:m.task_id};
}

// Only the original task name/ID (or clearing that read filter) is accepted.
// This bridge cannot target form inputs or change advertising parameters.
export async function nativeTaskSearch(page,c,m,checkpoint){
  const expected=c.phase==='verify'?c.action?.intent?.browser_batch?.task_name:c.lookup_id;
  const ruleReadback=c.rule_job===true&&c.phase==='lookup'&&c.rule_phase==='verify'&&c.lookup_id===c.action?.intent?.task_id;
  if(c.rule_job&&!ruleReadback||!['verify','lookup'].includes(c.phase)||m.run_id!==c.run_id||m.command_id!==c.id||typeof expected!=='string'||!expected||expected.length>100||![expected,''].includes(m.needle)||!/^[a-zA-Z0-9_-]{1,128}$/.test(c.id||''))throw Error('原任务搜索不属于当前只读命令');
  await checkpoint();const selector='input[data-qcx-task-search="'+c.id+'"]';
  const valid=await page.evaluate(({selector,account,plan})=>{const u=new URL(location.href),nodes=[...document.querySelectorAll(selector)],e=nodes[0];
    return u.origin==='https://qianchuan.jinritemai.com'&&['/uni-prom/detail','/overall-prom/detail'].includes(u.pathname)&&u.searchParams.getAll('aavid').length===1&&u.searchParams.get('aavid')===account&&u.searchParams.getAll('adId').length===1&&u.searchParams.get('adId')===plan&&nodes.length===1&&e.getClientRects().length&&!e.disabled&&e.placeholder==='输入任务名称或ID搜索'&&!e.closest('[role="dialog"],[aria-modal="true"],.ovui-modal,.ovui-drawer');
  },{selector,account:c.account_id,plan:c.plan_id});
  if(!valid)throw Error('原任务搜索的账户、计划或只读输入框不符');
  await page.fill('loc=css:'+selector,m.needle);await page.press('loc=css:'+selector,'Enter');await checkpoint();return {ok:true};
}
