/* Read-only account navigation. No ad editors, submits, platform IO or state reads. */
function accountEntryDom(phase,request){
 const a=request.account,text=e=>(e?.innerText||e?.textContent||'').trim();
 const shown=e=>!!e?.isConnected&&getComputedStyle(e).display!=='none'&&getComputedStyle(e).visibility!=='hidden'&&e.getClientRects().length>0;
 const enabled=e=>shown(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true';
 const out=(code,extra={})=>({code,...extra});let u;try{u=new URL(location.href);}catch{return out('identity_unready');}
 if(u.origin!=='https://qianchuan.jinritemai.com'||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==a.id||!['/uni-prom','/uni-prom/detail','/overall-prom'].includes(u.pathname))return out('identity_unready');
 if(!new RegExp('(?:^|\\s)ID[：:]\\s*'+a.id+'(?!\\d)').test(text(document.body)))return out('loading');
 if([...document.querySelectorAll('.ovui-modal,.ovui-drawer,[role="dialog"],[role="alertdialog"],[aria-modal="true"],.ovui-popover:not(.ovui-popover--hide)')].some(e=>shown(e)&&e.querySelector('input,textarea,button,[role="button"],[role="menu"]'))||[...document.querySelectorAll('table input[type="checkbox"]:checked,[role="row"] input[type="checkbox"]:checked')].some(shown))return out('editor_open');
 if(phase==='guard')return out('navigation_ready');
 if(a.scene==='product')return out(u.pathname==='/overall-prom'?'ready':'wrong_view',{plan_id:null});
 if(u.pathname==='/uni-prom/detail'){
  const parents=[...document.querySelectorAll('.oc-promotion-key-info-bar')].filter(shown),area=parents.length===1?text(parents[0]):text(document.body);
  const ids=[...area.matchAll(/计划ID[：:]\s*(\d{6,30})/g)].map(m=>m[1]);
  return ids.length===1&&u.searchParams.getAll('adId').length===1&&u.searchParams.get('adId')===ids[0]?out('ready',{plan_id:ids[0]}):out('loading');
 }
 if(u.pathname!=='/uni-prom')return out('wrong_view');
 const rows=[...document.querySelectorAll('tbody tr,.ovui-table-body-row,.ovui-table-row,[role="row"]')].filter(shown);
 const rowFor=e=>e.closest('tr,.ovui-table-body-row,.ovui-table-row,[role="row"]');
 const running=r=>r&&/(?:^|\s)投放中(?:\s|$)/.test(text(r));
 const leaf=(e,label)=>enabled(e)&&text(e).replace(/\s/g,'')===label&&![...e.children].some(c=>text(c).replace(/\s/g,'')===label);
 const key='__qc_account_entry_view_v1',state=globalThis[key]||{};
 if(state.job!==request.job_id)globalThis[key]={job:request.job_id};
 if(!globalThis[key].cost_view){
  const cost=[...document.querySelectorAll('button,[role="tab"],label,.ovui-radio-button,span,div')].filter(e=>leaf(e,'控成本投放')&&!rowFor(e)&&!e.closest('form,.ovui-form'));
  const candidates=cost.filter(e=>{for(let p=e.parentElement,n=0;p&&p!==document.body&&n<5;p=p.parentElement,n++){
   if(text(p).length>600)break;if(text(p).includes('放量投放'))return true;
  }return false;});
  globalThis[key].cost_view=true;
  if(candidates.length===1){candidates[0].click();return out('view_switch_requested');}
 }
 const links=new Map();
 for(const link of document.querySelectorAll('a[href]')){
  if(!enabled(link)||!running(rowFor(link)))continue;
  try{const v=new URL(link.getAttribute('href'),location.href),pid=v.searchParams.get('adId');
   if(v.origin===u.origin&&v.pathname==='/uni-prom/detail'&&v.searchParams.getAll('aavid').length===1&&v.searchParams.get('aavid')===a.id&&v.searchParams.getAll('adId').length===1&&/^\d{6,30}$/.test(pid||''))links.set(pid,v.href);
  }catch{}
 }
 if(links.size===1)return out('detail_open_requested',{next_url:[...links.values()][0]});
 if(links.size>1)return out('multiple_running_plans',{diagnostics:{candidate_count:links.size}});
 const safeMaterialLink=e=>{const a=e.closest('a[href]');if(!a)return true;try{const link=new URL(a.getAttribute('href'),location.href);return link.origin===u.origin&&['/uni-prom','/uni-prom/detail'].includes(link.pathname)&&link.searchParams.getAll('aavid').length===1&&link.searchParams.get('aavid')===request.account.id&&(link.pathname!=='/uni-prom/detail'||link.searchParams.getAll('adId').length===1&&/^\d{6,30}$/.test(link.searchParams.get('adId')||''));}catch{return false;}};
 const materials=[...document.querySelectorAll('a,button,[role="button"],span,div')].filter(e=>enabled(e)&&/^素材[›>]?$/u.test(text(e).replace(/\s/g,''))&&![...e.children].some(c=>text(c).replace(/\s/g,'')===text(e).replace(/\s/g,''))&&running(rowFor(e))&&safeMaterialLink(e));
 const diagnostics={row_count:rows.length,material_entry_count:materials.length,samples:rows.filter(running).slice(0,3).map(r=>text(r).slice(0,180))};
 if(materials.length===1){materials[0].click();return out('detail_open_requested',{diagnostics});}
 return out(materials.length>1?'multiple_running_plans':'native_entry_missing',{diagnostics});
}
if(typeof module==='object')module.exports={accountEntryDom};
