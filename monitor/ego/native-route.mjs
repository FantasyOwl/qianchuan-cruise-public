// The legacy native router sometimes clears adId after loading the correct
// visible parent. Repair only this viewing URL, never infer or switch a parent.
export function nativeDetailScope({aid,pid,entry,repair=false}){
  const u=new URL(location.href),expected=entry==='chengfang'?'/overall-prom/detail':entry==='uni_promotion'?'/uni-prom/detail':null;
  if(u.origin!=='https://qianchuan.jinritemai.com'||!['/uni-prom/detail','/overall-prom/detail'].includes(u.pathname)||expected&&u.pathname!==expected||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==aid||u.searchParams.getAll('adId').length!==1||!['',pid].includes(u.searchParams.get('adId')))return {ready:false,changed:false};
  const plans=new Set([...document.querySelectorAll('span,div,p')].filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden').map(e=>e.innerText.replace(/\s/g,'').match(/^计划ID[：:](\d{6,30})$/)?.[1]).filter(Boolean));
  if(plans.size!==1||!plans.has(pid))return {ready:false,changed:false};
  if(u.searchParams.get('adId')===pid)return {ready:true,changed:false};
  if(!expected)return {ready:false,changed:false};
  if(repair){u.searchParams.set('adId',pid);history.replaceState(history.state,'',u.href);}
  return {ready:true,changed:repair};
}
