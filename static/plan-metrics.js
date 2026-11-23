'use strict';
(function(global){
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const BASIS={overall_payment:'整体支付',net:'净成交',marketing:'综合营销',unknown:'口径未确认'};
const fmt=v=>v===null||v===undefined?'—':typeof v==='number'?v.toLocaleString('zh-CN',{maximumFractionDigits:2}):String(v);
const stamp=v=>{if(!v)return '未提供';const d=new Date(v);return Number.isNaN(d.getTime())?'未提供':d.toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'});};
const defaults=['spend','roi','net_roi','marketing_roi','gmv','net_gmv','payment_orders','net_orders','refund_rate_1h','budget'];
function compareRows(a,b,key,direction){
 const av=key.startsWith('m:')?a.metrics?.[key.slice(2)]:a[key],bv=key.startsWith('m:')?b.metrics?.[key.slice(2)]:b[key];
 const missing=v=>v===null||v===undefined||typeof v==='number'&&!Number.isFinite(v);
 if(missing(av)!==missing(bv))return missing(av)?1:-1;
 const result=missing(av)?0:typeof av==='number'&&typeof bv==='number'?av-bv:String(av).localeCompare(String(bv),'zh-CN');
 return (direction==='asc'?result:-result)||String(a.plan_id).localeCompare(String(b.plan_id));
}
function filterRows(rows,prefs){
 const term=(prefs.search||'').trim().toLowerCase();
 return rows.filter(r=>(!term||(r.name+' '+r.plan_id).toLowerCase().includes(term))&&
  (!prefs.date||prefs.date==='all'||!r.metric_date||r.metric_date===prefs.date)&&
  (prefs.segment==='all'||prefs.segment==='base'?prefs.segment==='all'||r.segment==='plan_card_base':r.segment!=='plan_card_base'))
  .sort((a,b)=>compareRows(a,b,prefs.sort,prefs.direction));
}
class PlanMetrics{
 constructor(app){this.app=app;this.doc=app.doc;this.preferences=new Map();this.catalog=[];this.accountId=null;this.bind();}
 model(){return this.app.state?.monitoring?.accounts?.find(a=>a.account_id===this.app.accountId);}
 prefs(){const aid=this.app.accountId;if(!this.preferences.has(aid)){
   let saved;try{saved=JSON.parse(global.localStorage?.getItem('qc_metric_view_'+aid)||'null');}catch{}
   this.preferences.set(aid,{sort:'m:spend',direction:'desc',columns:defaults.slice(),search:'',segment:'overall',date:null,date_mode:'auto',
    ...(saved&&['asc','desc'].includes(saved.direction)&&Array.isArray(saved.columns)?saved:{})});
  }return this.preferences.get(aid);}
 persist(){try{global.localStorage?.setItem('qc_metric_view_'+this.app.accountId,JSON.stringify(this.prefs()));}catch{}}
 summary(){
  const root=this.doc.querySelector('#account-metrics');if(!root)return;
  const model=this.model(),s=model?.summary,keys=['spend','roi','net_roi','marketing_roi','gmv','net_gmv','orders','balance'];
  if(model?.backend==='official_api'){
   root.innerHTML='<div class="section-heading"><div><h2>账户数据 · 官方API</h2><p>两种入口分别保留；有消耗的推广入口自动高亮置前。</p></div></div><div class="metric-entry-grid">'+ (model.summary_by_entry||[]).map(row=>{const hasActivity=['spend','gmv','net_gmv','orders'].some(k=>Number(row.metrics?.[k])>0);return `<section class="metric-entry ${hasActivity?'metric-entry--active':'metric-entry--quiet'}"><div class="metric-entry-head"><h3>${esc(row.entry_label)}${row.data_status==='no_rows'?' · 无匹配数据':''}</h3><span class="metric-entry-stamp">统计日期 ${esc(row.metric_date)} · 读取 ${esc(stamp(row.captured_at))}${row.read_fresh?'':' · 已过期'}</span></div><div class="metric-summary">${keys.filter(k=>row.metrics?.[k]!==undefined).map(k=>`<div><span>${esc(this.catalog.find(d=>d.key===k)?.label||k)}</span><strong>${esc(fmt(row.metrics[k]))}</strong></div>`).join('')}</div><p class="metric-provenance">统计日期 ${esc(row.metric_date)}；读取 ${esc(stamp(row.captured_at))}；业务更新时间未提供。${row.read_fresh?'':'本次读取已过期。'}</p></section>`;}).join('')+'</div><p class="metric-provenance">两种入口分别保留。空记录保持缺值，不相加或平均ROI。</p>';return;
  }
  if(!s){root.innerHTML='<h2>账户数据</h2><p>尚未收到本户账户汇总。计划配置和数据采集分别显示。</p>';return;}
  const rows=keys.filter(k=>s.metrics?.[k]!==undefined).map(k=>{const d=this.catalog.find(x=>x.key===k)||{label:k,unit:''};
   return `<div><span>${esc(d.label)}${d.unit?'（'+esc(d.unit)+'）':''}</span><strong>${esc(fmt(s.metrics[k]))}</strong></div>`;}).join('');
  root.innerHTML=`<div class="section-heading"><h2>账户数据</h2><span class="state-tag ${model.data_connected?'ready':''}">${model.data_connected?'采集已连接':'观察已过期'}</span></div><div class="metric-summary">${rows}</div><p class="metric-provenance">统计日期 ${esc(s.metric_date||'未确认')}；指标更新 ${esc(stamp(s.data_at))}；读取 ${esc(stamp(s.captured_at))}。${s.is_today?'':'该汇总不是今天的数据。'}${s.read_fresh?'':'本次读取已过期。'} 当前页面汇总保留来源，未将不同推广视图金额相加。</p>`;
 }
 render(){
  this.catalog=this.app.state?.monitoring?.metric_catalog||[];this.summary();
  const root=this.doc.querySelector('#plan-table');if(!root)return;
  const model=this.model();if(!model)return this.fallback(root);
  const connection=this.doc.querySelector('#connection-status');
  if(connection){connection.classList.toggle('connected',model.data_connected);connection.textContent=model.backend==='official_api'?`官方API ${model.data_connected?'数据已连接':'数据待更新'} · ${(model.plans||[]).length}个计划 · API逐单确认`:`${model.data_connected?'账户数据已连接':'账户数据待更新'} · ${model.execution_plan_count}个计划可调控`;}
  const p=this.prefs(),all=model.plans||[],dates=model.dates||[];
  const today=(this.app.state.now||'').slice(0,10);
  if(p.date===null||p.date_mode!=='manual')p.date=dates.includes(today)?today:dates[0]||today||'all';
  if(model.backend==='official_api'){
   // API cache currently contains only its queried day and overall metrics.
   // Do not silently apply an invisible date/base filter saved by Chrome UI.
   if(p.date!=='all'&&!dates.includes(p.date)){p.date=dates.includes(today)?today:dates[0]||today||'all';p.date_mode='auto';}
   if(p.segment==='base')p.segment='overall';
  }
  const allowed=new Set(this.catalog.map(d=>d.key));p.columns=p.columns.filter(k=>allowed.has(k));
  if(!p.columns.length)p.columns=defaults.filter(k=>allowed.has(k));
  const rows=filterRows(all,p),available=new Set(all.flatMap(r=>Object.keys(r.metrics||{}))),shown=this.catalog.filter(d=>p.columns.includes(d.key));
  const old=root.querySelector('#metric-search'),focused=old===this.doc.activeElement,selection=focused?[old.selectionStart,old.selectionEnd]:null;
  const opened=root.querySelector('#metric-columns')?.open,scroll=root.querySelector('.metrics-grid')?.scrollLeft||0;
  const th=(key,label)=>`<th aria-sort="${p.sort===key?(p.direction==='asc'?'ascending':'descending'):'none'}"><button data-metric-sort="${esc(key)}">${esc(label)}${p.sort===key?(p.direction==='asc'?' ↑':' ↓'):''}</button></th>`;
  root.innerHTML=`<div class="metric-tools"><label>搜索计划<input id="metric-search" placeholder="计划名称或ID" value="${esc(p.search)}"></label><label>统计日期<select id="metric-date"><option value="all" ${p.date==='all'?'selected':''}>全部已读日期</option>${[...new Set([today,...dates].filter(Boolean))].sort().reverse().map(d=>`<option value="${esc(d)}" ${p.date===d?'selected':''}>${esc(d)}${d===today?'（今天）':''}</option>`).join('')}</select></label><label>数据范围<select id="metric-segment"><option value="overall" ${p.segment==='overall'?'selected':''}>总体数据</option><option value="base" ${p.segment==='base'?'selected':''}>仅基础卡片</option><option value="all" ${p.segment==='all'?'selected':''}>全部范围（分别列出）</option></select></label><button id="refresh-plan-data" class="secondary">读取本户今天数据</button></div>
   <details id="metric-columns" ${opened?'open':''}><summary>显示指标列（${shown.length}项）</summary><div class="metric-column-choices">${this.catalog.map(d=>`<label><input type="checkbox" data-metric-column="${esc(d.key)}" ${p.columns.includes(d.key)?'checked':''}>${esc(d.label)}${available.has(d.key)?'':' · 未读到'}</label>`).join('')}</div></details>
   <p class="metric-provenance">${rows.length}条计划观察；点击任一列名切换升序/降序，缺失值始终放最后。${esc(model.coverage_note)}${p.date!==today&&p.date!=='all'?' 当前展示日期不是今天。':''}</p>
   <div class="metrics-grid"><table><thead><tr>${th('name','计划')}${shown.map(d=>th('m:'+d.key,d.label+(d.unit?'（'+d.unit+'）':''))).join('')}${th('metric_basis','指标口径')}${th('running','状态')}${th('metric_date','统计日期')}${th('captured_at','读取时间')}<th>指标更新时间</th><th>范围 / 执行配置</th></tr></thead><tbody>${rows.map(r=>`<tr><td class="name-cell">${esc(r.name)}${r.full_shop_managed?' · 全店托管':''}<small>${esc(r.plan_id)}${r.entry_label?" · "+esc(r.entry_label):""}</small></td>${shown.map(d=>`<td title="${r.metrics?.[d.key]===undefined?'当前观察未提供该指标':''}">${esc(fmt(r.metrics?.[d.key]))}</td>`).join('')}<td>${esc(BASIS[r.metric_basis]||'未确认')}</td><td>${['DELETE','DELETED'].includes(r.plan_status)?'已删除':r.plan_status==='FROZEN'?'已终止':r.running===true?'在投':r.running===false?'暂停/未投':'未确认'}</td><td>${esc(r.metric_date||'未确认')}</td><td>${esc(stamp(r.captured_at))}${r.read_fresh?'':' · 已过期'}</td><td>${esc(stamp(r.data_at))}</td><td>${r.segment==='plan_card_base'?'基础卡片':r.segment==='configuration'?'仅配置':'总体'} / ${r.execution_ready?'可准备调控':model.backend==='official_api'?'API数据（调控另核对）':'待执行器核验'}${r.configuration?.target_roi!=null?' · ROI目标 '+esc(fmt(r.configuration.target_roi))+'（'+esc(BASIS[r.configuration.roi_basis]||'口径未确认')+'）':''}</td></tr>`).join('')||`<tr><td colspan="${shown.length+7}">当前筛选下没有已读取的计划业绩。账户数据与计划数据分别采集。</td></tr>`}</tbody></table></div>`;
  root.querySelector('.metrics-grid').scrollLeft=scroll;
  if(focused){const input=root.querySelector('#metric-search');input.focus();if(selection)input.setSelectionRange(...selection);}
 }
 fallback(root){const choices=this.app.choices();root.innerHTML=choices.length?`<table><thead><tr><th>计划</th><th>日预算</th><th>ROI设置</th></tr></thead><tbody>${choices.map(c=>`<tr><td>${esc(c.surface.name)}<small>${esc(c.surface.plan_id)}</small></td><td>${esc(c.surface.budget)}</td><td>${esc(c.surface.target_roi)}</td></tr>`).join('')}</tbody></table>`:'<p class="empty">等待本户数据。</p>';}
 bind(){
  this.doc.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;
   if(b.dataset.metricSort){const p=this.prefs();p.direction=p.sort===b.dataset.metricSort&&p.direction==='desc'?'asc':'desc';p.sort=b.dataset.metricSort;this.persist();this.render();}
   if(['refresh-plan-data','refresh-api-account'].includes(b.id))void this.app.task(b,async()=>{this.prefs().date=(this.app.state.now||'').slice(0,10);this.prefs().date_mode='manual';this.persist();this.app.note('正在读取本户计划和今天报表，请稍候。');const d=await this.app.api('/api/cruise/refresh-data',{account_id:this.app.accountId});this.app.note(d.note||'已请求只读刷新今天数据，不修改投放。');await this.app.refresh();});
  });
  this.doc.addEventListener('input',event=>{if(event.target.id==='metric-search'){this.prefs().search=event.target.value;this.persist();this.render();}});
  this.doc.addEventListener('change',event=>{const el=event.target,p=this.prefs();
   if(el.id==='metric-date'){p.date=el.value;p.date_mode='manual';}else if(el.id==='metric-segment')p.segment=el.value;
   else if(el.dataset.metricColumn){p.columns=p.columns.filter(k=>k!==el.dataset.metricColumn);if(el.checked)p.columns.push(el.dataset.metricColumn);}else return;
   this.persist();this.render();
  });
 }
}
global.QianchuanMetrics={PlanMetrics,compareRows,filterRows};
if(typeof module==='object'&&module.exports)module.exports=global.QianchuanMetrics;
})(typeof window==='object'?window:globalThis);
