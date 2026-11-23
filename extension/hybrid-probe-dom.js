/* Bounded rendered-DOM diagnostics. No eval, fetch, cookies, input values,
 * form preparation, application internals or advertising submission. */
function hybridProbeDom(phase,request){
 const c=request.command,txt=e=>typeof e?.innerText==='string'?e.innerText.trim():'';
 const shown=e=>!!e?.isConnected&&e.getClientRects().length>0&&getComputedStyle(e).display!=='none'&&getComputedStyle(e).visibility!=='hidden';
 const roots='[role="dialog"],[aria-modal="true"],[class*="-modal__wrap"],.ovui-modal,.ovui-drawer,[class*="modal-content"]';
 const u=new URL(location.href),origin='https://qianchuan.jinritemai.com';
 const fail=message=>({ok:false,message,submitted:false});
 try{
  if(c.phase!=='dom_probe'||c.preview!==true||u.origin!==origin||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==c.account_id||!['/uni-prom','/overall-prom','/uni-prom/detail','/overall-prom/detail'].includes(u.pathname))throw Error('只读诊断网址范围不符');
  const labels=[...document.querySelectorAll('span,div,p')].filter(e=>shown(e)&&!e.closest('table,'+roots)&&txt(e).length<100).map(e=>txt(e).replace(/\s/g,''));
  const accounts=new Set(labels.map(s=>s.match(/^(?:账户)?ID[：:](\d{6,30})$/)?.[1]).filter(Boolean));
  if(accounts.size&&(accounts.size!==1||!accounts.has(c.account_id)))throw Error('只读诊断可见账户ID冲突');
  const detail=u.pathname.endsWith('/detail');
  if(detail){
   const plans=new Set([...txt(document.body).matchAll(/计划\s*ID[：:]\s*(\d{6,30})/g)].map(m=>m[1]));
   if(u.searchParams.getAll('adId').length!==1||u.searchParams.get('adId')!==c.plan_id||plans.size!==1||!plans.has(c.plan_id))throw Error('只读诊断母计划不符');
  }
  if(phase==='home'){
   if(!detail)return {ok:true,at_home:true};
   if([...document.querySelectorAll(roots+',table input[type="checkbox"]:checked')].some(shown))throw Error('保留现有编辑或手动选择，未切页');
   const links=[...document.querySelectorAll('a,button,[role="button"],span,div')].filter(e=>shown(e)&&txt(e)==='推直播间'&&!e.closest('table')&&![...e.children].some(ch=>txt(ch)==='推直播间'));
   if(links.length!==1)throw Error('只读返回首页入口不唯一');
   const a=links[0].closest('a[href]');
   if(a){const link=new URL(a.href,u);if(a.target==='_blank'||link.origin!==origin||!['/uni-prom','/overall-prom'].includes(link.pathname))throw Error('只读返回首页链接未核验');}
   (a||links[0]).click();return {ok:true,navigated:true};
  }
  if(phase!=='inspect'||!Array.isArray(c.probe?.selectors)||c.probe.selectors.length>6)throw Error('只读诊断阶段无效');
  const safeUrl=value=>{
   try{const link=new URL(value,u),q=new URLSearchParams();
    for(const k of ['aavid','adId','awemeId','aweme_id','uid','dr','dateRange'])if(link.searchParams.has(k))q.set(k,link.searchParams.get(k).slice(0,80));
    return {origin:link.origin,path:link.pathname,query:q.toString(),query_keys:[...link.searchParams.keys()].slice(0,16),hash_keys:[...new URLSearchParams(link.hash.slice(1).replace(/^\?/,'' )).keys()].slice(0,16)};
   }catch{return {path:'invalid'};}
  };
  const meta=e=>{
   const attrs={};for(const k of ['id','class','role','title','name','placeholder','aria-label','data-auto-id','data-row-key','data-id','data-key','data-uid','data-aweme-id','data-plan-id','data-ad-id']){
    const v=e.getAttribute(k);if(v)attrs[k]=v.slice(0,130);
   }
   return {tag:e.tagName,attrs,text:txt(e).slice(0,80),...(e.matches('a[href]')?{link:safeUrl(e.getAttribute('href'))}:{})};
  };
  const rows=Object.create(null);
  for(const selector of c.probe.selectors){
   if(typeof selector!=='string'||selector.length>180)throw Error('只读选择器无效');
   rows[selector]=[...document.querySelectorAll(selector)].filter(e=>shown(e)&&!e.closest(roots)&&!e.matches('input[type="password"],input[type="hidden"],textarea,[contenteditable="true"]')).slice(0,8).map(e=>({...meta(e),text:txt(e).slice(0,500),children:[...e.querySelectorAll('a[href],button,iconpark-icon,input,[id],[data-row-key],[data-auto-id]')].filter(n=>shown(n)&&!n.matches('input[type="password"],input[type="hidden"]')).slice(0,10).map(meta)}));
  }
  while(JSON.stringify(rows).length>42000){const key=Object.keys(rows).sort((a,b)=>JSON.stringify(rows[b]).length-JSON.stringify(rows[a]).length)[0];if(!rows[key].length)break;rows[key].pop();}
  const clean=safeUrl(u.href),url=clean.origin+clean.path+(clean.query?'?'+clean.query:'');
  return {ok:true,observation:{account_id:c.account_id,plan_id:c.plan_id,group:c.group,path:u.pathname,url,captured_at:new Date().toISOString(),rows}};
 }catch(error){return fail(error.message);}
}
