'use strict';
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const basisNames = {unknown:'口径未确认',overall_payment:'整体支付 ROI',net:'净成交 ROI',marketing:'综合营销 ROI',settled_7d:'7日结算 ROI',control_payment:'调控支付 ROI'};
const scopeNames = {account:'账户汇总',live:'直播大屏',plan:'计划',creative:'素材',control:'调控任务',product:'商品',keyword:'关键词'};
const kindNames = {browser_dom:'浏览器可见数据',legacy_db:'旧数据库',import:'结构化导入',demo:'演示'};
const segmentNames = {plan_card_overall:'计划整体',plan_card_base:'基础投放',plan_operation_log:'计划操作日志'};
const fmt = (n, digits=2) => typeof n === 'number' && Number.isFinite(n) ? n.toLocaleString('zh-CN',{minimumFractionDigits:digits,maximumFractionDigits:digits}) : '未采集';
const date = value => value ? new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '未采集';
let state = null, history = [], currentPage = 'observe', formsInitialized = false, rulesLoaded = false, busy = false;
let accountRegistry=null, accountDraft=[];
let historyRequest = 0;

async function api(path, body) {
  const options = {headers:{}};
  if (body !== undefined) {
    options.method = 'POST'; options.headers = {'Content-Type':'application/json','X-Monitor-CSRF':state?.csrf || ''};
    options.body = JSON.stringify(body);
  }
  const res = await fetch(path,options);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `请求失败 ${res.status}`);
  return data;
}

function notice(text,error=false) {
  const el=$('#notice');el.textContent=text;el.classList.toggle('error',error);el.hidden=false;
  clearTimeout(notice.timer);notice.timer=setTimeout(()=>el.hidden=true,6500);
}

async function action(button, fn) {
  button.disabled=true;
  try {await fn();} catch(error) {notice(error.message,true);} finally {button.disabled=false;}
}

async function load() {
  if(busy)return;
  busy=true;
  try {
    state=await api('/api/status');
    renderExecutionMode();
    if(typeof syncBrowserControl==='function')syncBrowserControl(state.browser_control);
    else if(!formsInitialized&&typeof loadBrowserControl==='function')await loadBrowserControl();
    $('#clock').textContent=date(state.now);
    const account=$('#account-select').value;
    $('#account-select').innerHTML=state.settings.accounts.map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');
    if(state.settings.accounts.some(a=>a.id===account))$('#account-select').value=account;
    renderSeries();renderLegacy();renderConnect();
    await readHistory();
    if(!formsInitialized){renderForms();formsInitialized=true;await loadAccountRegistry();}
  }catch(error){notice(`本地服务连接失败：${error.message}`,true);$('#health-banner').innerHTML='<strong>监测服务未连接</strong><p>请检查“启动只读监测.cmd”是否正在运行。</p>';}finally{busy=false;}
}

function renderExecutionMode(){
  const label=$('#execution-mode-label');if(label)label.textContent=state?.execution_available===true?'逐条确认执行已连接':'只读观察';
}
function selectedRows(){return state.latest.filter(x=>x.account_id===$('#account-select').value).sort((a,b)=>b.source.captured_at.localeCompare(a.source.captured_at));}
function seriesId(s){return [s.scope,s.entity_id,s.metric_basis,s.source.kind,s.source.series_key].join('|');}
function currentSnapshot(){return selectedRows().find(s=>seriesId(s)===$('#series-select').value);}
async function readHistory(){
  const ticket=++historyRequest,s=currentSnapshot();
  if(!s){history=[];renderObserve();return;}
  const params=new URLSearchParams({account_id:s.account_id,series_key:s.source.series_key});
  const data=await api('/api/history?'+params);
  if(ticket!==historyRequest||seriesId(currentSnapshot()||{source:{}})!==seriesId(s))return;
  history=data.snapshots;renderObserve();
}
function renderSeries(){
  const selected=$('#series-select').value;
  const rows=selectedRows();
  $('#series-select').innerHTML=rows.length?rows.map(s=>`<option value="${esc(seriesId(s))}">${esc(scopeNames[s.scope] || s.scope)}${!['account','live'].includes(s.scope)?` ${esc(s.display_name||s.entity_id)}`:''}${segmentNames[s.source.segment]?` / ${esc(segmentNames[s.source.segment])}`:''} / ${esc(basisNames[s.metric_basis])} / ${esc(kindNames[s.source.kind])} / ${esc(s.source.dataset_id||'旧来源')} / ${esc(s.source.metric_date)}</option>`).join(''):'<option value="">尚无可用数据</option>';
  if(rows.some(s=>seriesId(s)===selected))$('#series-select').value=selected;
  renderObserve();
}

function metric(label,value,caption,isDate=false){
  const missing=value===null||value===undefined;
  return `<dl class="metric"><dt>${esc(label)}</dt><dd class="${missing?'empty':''}">${esc(missing?'未采集':isDate?value:fmt(value))}</dd><small>${esc(caption)}</small></dl>`;
}

function renderObserve(){
  const s=currentSnapshot(), accountId=$('#account-select').value;
  const evaluation=state.evaluation;
  $('#collection-health').innerHTML=collectionHealthHTML(evaluation.collection_health,state.monitor_health,accountId);
  const entity=s?evaluation.entities.find(e=>e.latest_snapshot_id===s.id):null;
  const ext=state.diagnostics.extension;
  const extSeen=ext?.last_seen||ext?.last_ingest_at;
  const connected=state.monitor_health?.browser_communication?state.monitor_health.browser_communication.state==='recent':extSeen&&Number.isFinite(new Date(extSeen).getTime())&&Math.abs(Date.now()-new Date(extSeen).getTime())<180000;
  const banner=$('#health-banner');
  const ready=entity?.quality==='fresh';banner.classList.toggle('good',ready);
  banner.innerHTML=ready?`<strong>数据时效与基本窗口检查通过</strong><p>${esc(evaluation.summary)} 所有规则建议仍需复核。</p>`:
    `<strong>${s?'证据不完整，规则判断受限':'尚未取得此账户的可用快照'}</strong><p>${esc(s?(entity?.issues?.slice(0,3).join('；')||'需要补全数据口径及小时样本'):connected?'采集器已连接，但当前页面没有通过校验的数据。请查看数据接入页的具体原因。':'请连接只读浏览器采集器，或导入该账户的结构化报告。旧程序历史指标不会移入当前账户。')}</p>`;
  const m=s?.metrics||{};
  const observedPlanId=s?.scope==='plan'?s.entity_id:s?.source.parent_entity_id;
  const recentOperation=(evaluation.recent_plan_operations||[]).find(r=>r.account_id===s?.account_id&&r.plan_id===observedPlanId&&r.synthetic===(s?.source.kind==='demo'));
  const base=s?.scope==='plan'&&(m.control_spend>0||['base_spend','base_comprehensive_cost','base_gmv','base_net_gmv','base_orders'].some(k=>m[k]!=null));
  const roiKey=base?{overall_payment:'base_roi',net:'base_net_roi',marketing:'base_marketing_roi'}[s?.metric_basis]:{overall_payment:'roi',net:'net_roi',marketing:'marketing_roi',settled_7d:'settled_roi',control_payment:'roi'}[s?.metric_basis];
  const costKey=base?(s?.metric_basis==='marketing'?'base_comprehensive_cost':'base_spend'):s?.metric_basis==='marketing'?'comprehensive_cost':s?.metric_basis==='control_payment'?'control_spend':'spend';
  const amountKey=base?(s?.metric_basis==='marketing'||s?.metric_basis==='net'?'base_net_gmv':'base_gmv'):s?.metric_basis==='marketing'||s?.metric_basis==='net'?'net_gmv':s?.metric_basis==='control_payment'?'control_gmv':'gmv';
  $('#metrics').innerHTML=metric(base?'基础成本（元）':'消耗（元）',m[costKey],s?'只使用所选来源，不跨账户加总':'等待采集')+
    metric(amountKey?.includes('net_gmv')?(base?'基础净成交（元）':'净成交金额（元）'):(base?'基础成交（元）':'成交金额（元）'),m[amountKey],s?basisNames[s.metric_basis]:'口径尚未确认')+
    metric(s?(base?'基础 ':'')+basisNames[s.metric_basis]:'ROI',roiKey?m[roiKey]:null,'缺少同口径字段时不推算替代值')+
    metric('数据更新至',s?.source.data_at?new Date(s.source.data_at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):null,s?`统计日期 ${s.source.metric_date}`:'等待明确的数据时间',true);
  const samples=s?history.filter(x=>x.account_id===s.account_id&&seriesId(x)===seriesId(s)&&x.source.metric_date===s.source.metric_date):[];
  $('#coverage').innerHTML=[['来源',s?kindNames[s.source.kind]:'未连接'],['采集时间',s?date(s.source.captured_at):'未采集'],['数据时间',s?date(s.source.data_at):'未采集'],['统计日期',s?.source.metric_date||'未采集'],['同日样本',String(samples.length)],['完整小时窗口',String(entity?.hourly?.filter(w=>w.complete).length||0)],['范围',s?(s.source.coverage==='report'?'报告声明范围':'当前页面可见汇总'):'等待采集'],['近期操作（历史未核全）',recentOperation?date(recentOperation.last_observed_change_at):'暂无可用观察'],['变更历史',s?.control.history_known?'已声明':'未知'],['成本保障',s?.control.guarantee_verified?'已声明核验（需复核）':'未核验']].map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');
  renderChart(samples,costKey,amountKey);
  const analysis=evaluation.monitoring_analysis?.entities.find(e=>e.snapshot_id===s?.id);
  const account=state.settings.accounts.find(a=>a.id===accountId);
  renderRoi(analysis,account,s);renderObjectAnalysis(analysis);
  $('#official-report-observation').hidden=!s?.official_report_evidence;
  $('#official-report-observation').innerHTML=officialReportHTML(s?.official_report_evidence);
  const planLogs=(evaluation.observed_plan_operations||[]).filter(row=>row.account_id===s?.account_id&&row.plan_id===observedPlanId&&row.synthetic===(s?.source.kind==='demo'));
  $('#plan-operation-observation').hidden=!planLogs.length;
  $('#plan-operation-observation').innerHTML=planOperationHTML(planLogs);
  const alerts=evaluation.alerts.map(a=>scopedAlert(a,accountId)).filter(Boolean);
  $('#alert-count').textContent=`${alerts.length} 条策略或数据告警`;
  $('#alerts').innerHTML=alerts.length?alerts.map(alertHTML).join(''):'<div class="empty-box">暂无可判断的投放告警。数据接入与样本完整后，程序会按策略检查。没有告警不代表账户健康。</div>';
  const recommendations=evaluation.recommendations.filter(a=>a.account_id===accountId);
  $('#outcome-reviews').innerHTML=outcomeHTML(evaluation.outcome_review,accountId);
  $('#emergency-analysis').innerHTML=emergencyHTML((evaluation.emergency_analysis||[]).filter(o=>o.account_id===accountId));
  $('#maintenance-review').innerHTML=maintenanceHTML((evaluation.maintenance_review||[]).filter(o=>o.account_id===accountId));
  $('#lifecycle-review').innerHTML=lifecycleHTML((evaluation.lifecycle_reviews||[]).filter(o=>o.account_id===accountId));
  $('#roi-recovery-review').innerHTML=roiRecoveryHTML((evaluation.roi_recovery_reviews||[]).filter(o=>o.account_id===accountId));
  $('#live-exclusion-review').innerHTML=liveExclusionHTML((evaluation.live_exclusion_reviews||[]).filter(o=>o.account_id===accountId));
  $('#product-carrying-review').innerHTML=productCarryingHTML(evaluation.product_carrying,accountId);
  renderMigrations(evaluation.budget_migrations||[],accountId);
  $('#recommendations').innerHTML=recommendations.length?recommendations.map(recommendationHTML).join(''):'<div class="empty-box">尚无满足触发证据的调控建议。请先确认目标口径，并积累连续小时数据。</div>';
  const coverage=state.metric_coverage?.accounts.find(a=>a.account_id===accountId);
  const states={fresh:'数据时间有效',unverified:'已观察，待校验',stale:'历史或过期',missing:'未采集'};
  $('#metric-coverage').innerHTML=coverage?`<table><thead><tr><th>层级</th><th>指标</th><th>当前数据</th><th>对象数</th></tr></thead><tbody>${coverage.metrics.map(r=>`<tr><td>${esc(r.group)}</td><td>${esc(r.label)}</td><td>${esc(states[r.data_status])}</td><td>${r.objects_observed}</td></tr>`).join('')}</tbody></table>`:'<p>尚无覆盖清单。</p>';
}

function planOperationHTML(rows){
  if(!rows?.length)return '';
  return `<h2>已观察的计划操作</h2><p class="footnote">仅保留页面可见的追投预算修改；历史未核全，也不认证平台执行结果。操作时间与读取时间分别保存。</p><table><thead><tr><th>操作时间</th><th>追投任务 ID</th><th>预算调整（元）</th><th>读取时间</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(date(row.occurred_at))}</td><td>${esc(row.control_id)}</td><td>${esc(fmt(row.before_budget))} → ${esc(fmt(row.after_budget))}</td><td>${esc(date(row.observed_at))}</td></tr>`).join('')}</tbody></table>`;
}

function renderRoi(analysis,account,s){
  const rows=analysis?.hourly||[],valid=rows.filter(w=>w.complete&&typeof w.roi==='number');
  const status={fresh:'数据时间有效',stale:'历史或过期',unverified:'数据待核验'}[analysis?.data_status]||'等待数据';
  $('#roi-caption').textContent=`${valid.length} 个完整小时 · ${status}`;
  if(!valid.length){$('#roi-chart').innerHTML='<div class="chart-empty"><strong>分时 ROI 等待数据</strong><p>需要同一来源、同一日或同一场次的连续累计金额与明确数据时间。</p></div>';return;}
  const target=account?.roi_basis===s?.metric_basis?account.target_roi:null;
  const width=700,height=230,pad=44,max=Math.max(...valid.map(w=>w.roi),target||0,1)*1.1;
  const first=new Date(rows[0].end).getTime(),last=new Date(rows.at(-1).end).getTime();
  const x=w=>pad+(new Date(w.end).getTime()-first)/(last-first||1)*(width-pad-14),y=v=>height-30-v/max*(height-48);
  let line='',connected=false;
  rows.forEach(w=>{if(!w.complete||typeof w.roi!=='number'){connected=false;return;}line+=`${connected?' L':' M'}${x(w).toFixed(2)} ${y(w.roi).toFixed(2)}`;connected=true;});
  const targetLine=typeof target==='number'?`<line x1="${pad}" x2="${width-12}" y1="${y(target)}" y2="${y(target)}" stroke="#946322" stroke-dasharray="5 4"/><text x="${width-12}" y="${y(target)-6}" text-anchor="end" font-size="11" fill="#946322">用户目标 ${esc(fmt(target))}</text>`:'';
  let grid='';for(let i=0;i<4;i++){const value=max*i/3;grid+=`<line x1="${pad}" x2="${width-12}" y1="${y(value)}" y2="${y(value)}" stroke="#e1eaee"/><text x="${pad-7}" y="${y(value)+4}" text-anchor="end" font-size="10" fill="#627b86">${esc(fmt(value,1))}</text>`;}
  const dots=valid.map(w=>`<circle cx="${x(w)}" cy="${y(w.roi)}" r="3" fill="${w.sample_sufficient?'#167985':'#81949e'}"><title>${esc(date(w.end))} ROI ${esc(fmt(w.roi))} · ${w.sample_sufficient?'达到数值样本门槛':'样本不足，仅观察'}</title></circle>`).join('');
  $('#roi-chart').innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="按完整小时差分计算的同口径ROI曲线">${grid}${targetLine}<path d="${line}" fill="none" stroke="#167985" stroke-width="2.5"/>${dots}<text x="${pad}" y="${height-4}" font-size="11" fill="#627b86">${esc(date(rows[0].end))}</text><text x="${width-12}" y="${height-4}" text-anchor="end" font-size="11" fill="#627b86">${esc(date(rows.at(-1).end))}</text></svg>`;
}

function renderObjectAnalysis(a){
  const panel=$('#object-analysis');panel.hidden=!a;if(!a)return;
  const lifecycle={cold:'冷启动',growing:'成长期',mature:'成熟期',declining:'衰退期',unknown:'未知',growing_candidate:'成长期候选',mature_candidate:'成熟期候选',declining_candidate:'衰退候选'};
  const direction={rising:'上升',falling:'下滑',stable:'持平',mixed:'消耗与在线变化不同步',unknown:'证据不足'};
  const role={hit:'爆品',lead:'引流品',profit:'利润品',new:'新品',unknown:'未分类'};
  const test={unverified:'商品统计范围未核验',target_missing:'用户目标未设置或口径不符',insufficient:'样本不足',sample_met_roi_qualified:'样本达到本地门槛且ROI达标，待复核',sample_met_roi_low:'样本达到本地门槛但ROI未达标',not_new_product:'未标为新品'};
  const status={fresh:'数据时间有效',stale:'历史或过期数据',unverified:'数据待核验'}[a.data_status]||'状态未知';
  let html=`<h2>所选对象分析</h2><p class="footnote">${esc(status)}；数据更新至 ${esc(date(a.data_at))}。${esc(a.data_status_reason||'')} 下列结论对应这次观测。</p>`;
  if(a.spend_trend)html+=spendTrendHTML(a.spend_trend);
  if(a.creative_spend_share)html+=creativeSpendShareHTML(a.creative_spend_share);
  if(a.budget)html+=`<p>${a.budget.ledger==='base'?'基础计划':'当前对象'}预算进度：${esc(fmt(a.budget.progress_percent))}${a.budget.progress_percent==null?'':'%'}；同口径累计消耗 ${esc(fmt(a.budget.spend))} 元 / 预算 ${esc(fmt(a.budget.budget))} 元。${esc(a.budget.reason)}</p>`;
  if(a.lifecycle)html+=`<p>声明生命周期：${esc(lifecycle[a.lifecycle.declared]||'未知')}；观察候选：${esc(lifecycle[a.lifecycle.candidate]||'未知')}。候选不写回策略标签。</p>${evidenceHTML(a.lifecycle.evidence)}`;
  if(a.traffic)html+=`<p>同场次流速：${esc(direction[a.traffic.direction])}。${esc(a.traffic.reason)}</p><p>付费占比 ${esc(fmt(a.traffic.paid_share))}%；自然占比 ${esc(fmt(a.traffic.natural_share))}%；停留 ${esc(fmt(a.traffic.watch_duration))} 秒；互动率 ${esc(fmt(a.traffic.interaction_rate))}%；下单率 ${esc(fmt(a.traffic.order_rate))}%。</p>`;
  if(a.traffic?.quality)html+=trafficQualityHTML(a.traffic.quality);
  if(a.product)html+=`<p>商品角色：${esc(role[a.product.role]||'未分类')}；同口径成交贡献 ${esc(fmt(a.product.account_gmv_contribution_percent))}%；库存 ${esc(fmt(a.product.stock,0))}；按上一完整小时订单速度可承接 ${esc(fmt(a.product.stock_coverage_hours))} 小时。</p><p>新品测试：${esc(test[a.product.new_product_test])}。</p><p class="footnote">${esc(a.product.note)}</p>`;
  if(a.funnel){html+='<h3>转化漏斗</h3><table><thead><tr><th>阶段</th><th>观察数量</th><th>至下一阶段</th></tr></thead><tbody>'+a.funnel.stages.map((stage,i)=>{const rate=a.funnel.rates[i];return`<tr><td>${esc(stage.label)}</td><td>${esc(fmt(stage.value,0))}</td><td>${rate?rate.percent==null?esc(rate.reason):`${esc(fmt(rate.percent))}%`:'—'}</td></tr>`;}).join('')+'</tbody></table><p class="footnote">'+esc(a.funnel.note)+'</p>';}
  if(a.funnel?.additional_observations?.length)html+=a.funnel.additional_observations.map(o=>`<p>${esc(o.label)}：${esc(fmt(o.value,0))}。</p>`).join('');
  if(a.refund)html+=`<p>1小时退款率 ${esc(fmt(a.refund.rate_1h))}%；发货前退款率 ${esc(fmt(a.refund.rate_pre_ship))}%；发货前退款率一小时变化 ${esc(fmt(a.refund.pre_ship_change_pp))} 个百分点。</p>`;
  html+=`<p class="footnote">统计周期：${esc({live_session:'本场累计',day:'日累计',unknown:'周期未确认'}[a.metric_period]||'周期未确认')}；数据时间 ${esc(date(a.data_at))}。缺失值不填零，描述性分析不授予调控资格。</p>`;
  panel.innerHTML=html;
}

function creativeSpendShareHTML(s){
  const names={observed:'关联数值已核对',historical:'历史比值',missing:'缺少证据',conflict:'范围或数值矛盾',unverified:'数据待核验'};
  return `<h3>单素材消耗占比</h3><p>${s.synthetic?'合成观察；':''}${esc(names[s.status]||'状态待核验')}；${s.is_current?'当前指标时间有效':'当前时效未确认'}。${s.share_percent==null?'占比暂不可计算':`占比 ${esc(fmt(s.share_percent))}%`}。</p><p>${esc(s.ledger==='base'?'基础广告消耗':s.ledger==='overall'?'总体广告消耗':'消耗台账待确认')}：素材 ${esc(fmt(s.creative_spend))} 元 / 母计划 ${esc(fmt(s.parent_spend))} 元；母计划 ${esc(s.parent_plan_id||'待关联')}。</p><p class="footnote">素材数据时间 ${esc(date(s.creative_data_at))}；母计划数据时间 ${esc(date(s.parent_data_at))}。${esc(s.reason||'')} ${esc(s.note||'')}</p>`;
}

function spendTrendHTML(t){
  const names={spend:'消耗',base_spend:'基础消耗',comprehensive_cost:'综合成本',base_comprehensive_cost:'基础综合成本',control_spend:'追加投入消耗'};
  return `<h3>消耗分时（独立观察）</h3><p>${esc(names[t.field]||'成本口径待核验')}；${t.is_current?'当前指标时间有效':'历史或最新时间待核验'}。最近两窗按实际采样时长折算的增速：${t.change_percent==null?esc(t.comparison_reason||'证据不足'):`${esc(fmt(t.change_percent))}%`}。</p><table><thead><tr><th>观察区间</th><th>金额（元）</th><th>折合元/小时</th></tr></thead><tbody>${(t.windows||[]).slice(-6).map(w=>`<tr><td>${esc(date(w.observed_start||w.start))} 至 ${esc(date(w.observed_end||w.end))}</td><td>${w.complete?esc(fmt(w.cost)):esc(w.reason||'证据不足')}</td><td>${w.complete?esc(fmt(w.average_cost_per_hour)):'—'}</td></tr>`).join('')}</tbody></table><p class="footnote">${esc(t.note||'消耗趋势不授予调控资格。')}</p>`;
}

function trafficQualityHTML(q){
  const names={insufficient:'证据不足',suspected_broadening:'疑似流量泛化，需复核',mixed_decline:'部分质量指标下降，不能单独归因',no_material_decline:'未达到本地联合下降条件'};
  return `<h3>流量质量配对窗口</h3><p>${q.is_current?'当前在播观察':'历史或未核验当前在播'}；${esc(names[q.state]||'状态待核验')}。</p><p class="footnote">当前 ${esc(date(q.window?.start))} 至 ${esc(date(q.window?.end))}；基线 ${esc(date(q.baseline_window?.start))} 至 ${esc(date(q.baseline_window?.end))}。去重用户样本 ${esc(fmt(q.sample_users,0))} / 基线 ${esc(fmt(q.baseline_sample_users,0))}。</p><table><thead><tr><th>指标</th><th>当前</th><th>基线</th><th>相对变化</th></tr></thead><tbody>${(q.comparisons||[]).map(c=>`<tr><td>${esc(c.label)}</td><td>${esc(fmt(c.value))} ${esc(c.unit)}</td><td>${esc(fmt(c.baseline))} ${esc(c.unit)}</td><td>${c.relative_change==null?esc(c.reason||'未具备可比证据'):`${esc(fmt(c.relative_change*100))}%`}</td></tr>`).join('')}</tbody></table>${[...(q.missing||[]),...(q.current_blockers||[])].map(v=>`<p class="blocked">${esc(v)}</p>`).join('')}<p class="footnote">相对下降20%、每窗100用户、15至60分钟等长相邻窗口为本地观察条件。${esc(q.note)}不直接收紧定向或更改ROI。</p>`;
}

function scopedAlert(a,accountId){
  const related=['aweme_universal_conflict','live_universal_standard_conflict','live_plan_status_conflict'].includes(a.rule)?a.evidence?.observed_plans:null;
  if(!related)return !a.account_id||a.account_id===accountId?a:null;
  const own=related.filter(p=>p.account_id===accountId);if(!own.length&&a.account_id!==accountId)return null;
  const clone=JSON.parse(JSON.stringify(a));clone.account_id=accountId;clone.entity_id=own[0]?.plan_id||a.entity_id;
  clone.evidence.observed_plans=own;clone.evidence.other_account_conflicting_plan_count=related.length-own.length;
  if(a.account_id!==accountId)delete clone.source_key;
  return clone;
}

function productCarryingHTML(value,accountId){
  const inventory=(value?.inventory||[]).filter(o=>o.account_id===accountId),plans=(value?.plans||[]).filter(o=>o.account_id===accountId);
  if(!inventory.length&&!plans.length)return '<p class="muted">尚无商品库存和当前讲解的核对数据，不能判断承接正常。</p>';
  const names={unverified:'待核对',stock_empty:'可售件数为零',stock_positive_rate_unknown:'可售件数已声明，销量速率不足',coverage_below_policy:'库存覆盖低于已声明策略',coverage_within_policy:'覆盖达到已声明策略',coverage_known_policy_missing:'覆盖仅作描述，尚无核对阈值',unmatched:'尚未关联库存',conflict:'来源声明矛盾',same_product:'来源声明讲解同一商品',product_mismatch_review:'商品不同，品类错配待核对',category_mismatch:'来源声明品类不同',context_conflict:'商品或品类声明矛盾'};
  return inventory.map(o=>`<article class="recommendation"><h3>商品 ${esc(o.product_id||o.entity_id)}</h3><p>${esc(names[o.state]||o.state)}；可售 ${esc(fmt(o.available_stock,0))} 件，每小时支付 ${esc(fmt(o.units_per_hour))} 件，覆盖 ${esc(fmt(o.stock_coverage_hours))} 小时；声明阈值 ${esc(fmt(o.minimum_stock_hours))} 小时。</p><p>库存时刻 ${esc(date(o.inventory_at))}；业绩时刻 ${esc(date(o.performance_data_at))}。${o.clock_current?'库存时效有效':'库存时效待核对'}，两种时间不补填。</p><p class="footnote">订单数不当作支付件数；广告归因销量不替代全商品销量，SKU单点不证明整商品无货。</p></article>`).join('')+plans.map(o=>`<article class="recommendation"><h3>计划 ${esc(o.plan_id)}</h3><p>唯一投放商品 ${esc(o.product_id||'未核对')}；当前讲解 ${esc(o.explained_product_id||'未核对')}。${esc(names[o.alignment_state]||o.alignment_state)}；库存关联 ${esc(names[o.inventory_state]||o.inventory_state)}。</p><p>映射时刻 ${esc(date(o.mapping_at))}；讲解开始 ${esc(date(o.explanation_started_at))}，核对时刻 ${esc(date(o.explanation_observed_at))}。${o.hold_growth?'相关计划增量建议保持阻断，先复核承接。':'本条未形成增量否决，不代表承接正常或获得执行资格。'}</p>${(o.reasons||[]).map(r=>`<p class="blocked">${esc(r)}</p>`).join('')}<p class="footnote">讲解及映射均为来源声明；不识别音视频，不自动换品、改品类或启停计划。</p></article>`).join('');
}

function liveExclusionHTML(rows){
  if(!rows?.length)return '<p class="muted">尚无明确的直播计划互斥状态数据；不能据此判定全部账户已满足互斥。</p>';
  const states={enabled:'观察到开启',disabled:'观察到关闭',system_paused:'来源声明系统暂停',not_delivering:'来源声明当前未投放',status_conflict:'当前状态来源冲突',insufficient:'状态证据不足'};
  const overlaps={none_observed:'未观察到正向冲突，不代表全量通过',multiple_universal_switches:'近时点多个全域开关开启',enabled_overlap_unverified:'全域与标准开关重叠，实际投放待核对',delivery_overlap_declared:'来源声明近时点实际投放状态重叠',system_pause_observed:'标准来源声明已系统暂停，不追加停用',delivery_not_active:'标准来源声明未实际投放'};
  return rows.map(o=>`<article class="recommendation"><h3>${esc(states[o.state]||'待核对')}</h3><p>计划 ${esc(o.plan_id)}；抖音号 ${esc(o.aweme_id||'未核对')}；${esc(o.promotion_type)}。${esc(overlaps[o.overlap]||'互斥状态待核对')}。</p><p>状态观测 ${esc(date(o.status_at))}；业绩更新 ${esc(date(o.performance_data_at))}。${o.is_current?'状态时效有效':'历史或未核对状态时效'}，两种时间不互相替代。</p>${(o.missing||[]).map(v=>`<p class="blocked">${esc(v)}</p>`).join('')}<p class="footnote">120秒是本地近时点观察窗口。全域关闭后不自动恢复标准计划；当前实际投放及平台互斥未认证。${esc(o.note)}</p></article>`).join('');
}

function roiRecoveryHTML(rows){
  if(!rows?.length)return '<p class="muted">尚无明确关联的ROI下调恢复链；建议不会自动记为已应用。</p>';
  const names={verify_episode:'核对根报告与连续来源',observing_after_change:'等待变更后完整小时',target_missing:'用户考核线缺失或口径不符',recovery_not_qualified:'当前跑量或效果尚未恢复',ready_return_step:'复核下一步回调',verify_return_path:'输入网格没有合适回调路径',returned_needs_review:'已报告返回原设置，效果仍需复核',complete:'报告和窗口已复核返回原目标'};
  return rows.map(r=>`<article class="recommendation"><h3>${esc(names[r.state]||'阶段待核对')}</h3><p>计划 ${esc(r.entity_id)}；根报告 ${esc(r.root_event_id)}；${r.is_current?'当前同日来源':'历史或证据待核对'}，数据更新 ${esc(date(r.data_at))}。</p><p>原计划目标 ${roiValue(r.original_plan_goal)}；当前设置 ${roiValue(r.current_plan_goal)}；下一步候选 ${roiValue(r.next_goal)}。</p><p>已交叉复核报告 ${(r.step_history||[]).length} 条；观察小时消耗 ${esc(fmt(r.current_window?.ad_spend))} 元，ROI ${roiValue(r.current_window?.roi)}。${r.completed_at?'归档窗口截止 '+esc(date(r.completed_at)):r.observation_due_at?'观察截止 '+esc(date(r.observation_due_at)):'每一步后至少观察一小时。'}</p>${r.cross_day?`<p>跨日按独立日台账分段核算；来源声明核对的午夜边界 ${(r.daily_boundaries||[]).length} 对。${(r.daily_boundaries||[]).map(v=>`${esc(v.closing_metric_date)} 封账 → ${esc(v.opening_metric_date)} 零累计起点`).join('；')} 当日已报告变更 ${esc(r.reported_changes_on_current_day??'待核对')} 次，昨日次数不计入今日；日界声明不代表平台认证。</p>`:''}${[...(r.missing||[]),...(r.candidate_blockers||[])].map(v=>`<p class="blocked">${esc(v)}</p>`).join('')}<p class="footnote">候选未应用，不跳步、不超过原目标，零基线不计算相对增长率。${esc(r.note)}</p></article>`).join('');
}

function lifecycleHTML(rows){
  if(!rows?.length)return '<p class="muted">尚无可复核的计划生命周期来源；不能据此判断全部计划健康。</p>';
  const stages={cold:'冷启动',growing:'成长期',mature:'成熟期',declining:'衰退期',unknown:'未确认'};
  const states={cold_observing:'冷启动：等待测试样本',cold_ready:'冷启动：预算前置样本就绪',growing:'按成长期检查稳定放量',mature:'按成熟期复核出价与后续观察',declining:'按衰退期顺序维护',stage_conflict:'阶段证据冲突',unverified:'阶段证据不足'};
  return rows.map(r=>`<article class="recommendation"><h3>${esc(states[r.state]||'阶段待核对')}</h3><p>计划 ${esc(r.plan_id||r.entity_id)}；声明 ${esc(stages[r.declared_stage]||'未确认')}；年龄 ${esc(fmt(r.age_hours))} 小时。${r.is_current?'当前同日来源':'历史或证据待核对'}，数据更新 ${esc(date(r.data_at))}。</p><p>${esc(r.focus)}</p><p>当日测试消耗 ${esc(fmt(r.daily_test_spend))} 元 / 门槛 ${esc(fmt(r.cold_min_test_spend))} 元；当前预算 ${esc(fmt(r.budget))} 元；3秒完播率 ${esc(fmt(r.completion_3s))}${r.completion_3s==null?'':'%'}。</p>${[...(r.conflicts||[]),...(r.missing||[])].map(x=>`<p class="blocked">${esc(x)}</p>`).join('')}${r.bid_cap_status==='user_interpretation_pending'?'<p class="blocked">冷启动出价上限尚未配置，不自动计算出价。</p>':r.bid_cap_status==='user_confirmed_1_4'?`<p>用户已确认成本出价上限=目标成本×1.4；当前已声明出价 ${esc(fmt(r.cold_bid_review?.observed_bid))} 元，上限 ${esc(fmt(r.cold_bid_review?.ceiling))} 元。${r.cold_bid_review?.state==='above_user_cap'?'出价超过用户上限，暂停追加放量并人工核对。':r.cold_bid_review?.state==='within_user_cap'?'当前声明出价未超过用户上限，仍须核对权限与成本保障。':r.cold_bid_review?.state==='bid_missing'?'未提供当前成本出价，仅展示策略上限。':'阶段、标准成本出价类型或目标成本未核对，上限不计算。'} 上限不是建议新出价，不自动调价。</p>`:''}<p class="footnote">300元仅是用户初始日预算策略，不自动跳涨补齐；预算前置样本就绪仍须核对通用风险、限额和冷却。${esc(r.note)}</p></article>`).join('');
}

function maintenanceHTML(rows){
  if(!rows?.length)return '<p class="muted">尚无明确标为衰退期的计划维护记录；不能据此判断全部计划健康。</p>';
  const names={verify_decline:'核对本轮衰退与来源',refresh_required:'第一步：补充新素材',verify_refresh_report:'核对补充报告与完整清单',observing_refresh:'等待补充后完整两小时',target_missing:'用户目标或口径缺失',recovered_observation:'观察到恢复，暂不推进预算交接',inconclusive:'效果尚不明确，继续复核',bid_review_required:'第二步：复核微调出价方案',handover_ready:'第三步：可选择接收计划复核预算交接'};
  return rows.map(m=>`<article class="recommendation"><h3>${esc(names[m.state]||'阶段待核验')} <span class="badge">仅复核</span></h3><p>计划 ${esc(m.entity_id)}；${m.is_current?'当前同日来源':'历史或未核验来源'}；数据更新至 ${esc(date(m.data_at))}。${m.is_primary_plan===true?'主力计划':m.is_primary_plan===false?'明确非主力':'主力角色未知'}。</p><p>${esc(m.note)}</p><p>补充报告 ${esc(m.refresh_event_id||'尚无')}；已核对新增素材 ${esc(fmt(m.added_material_count,0))} 个；观察截止 ${esc(date(m.post_observation_end))}；出价方案复核时间 ${esc(date(m.bid_review_at))}。</p>${(m.missing||[]).map(v=>`<p class="blocked">${esc(v)}</p>`).join('')}${evidenceHTML(m.post_refresh_windows||[])}<p class="footnote">报告与完整清单仅在本地交叉核对。出价方案复核不表示已经调价；不直接关停主力，不自动交接预算。</p></article>`).join('');
}

function evidenceHTML(e){return `<details class="evidence"><summary>查看数据佐证</summary><pre>${esc(JSON.stringify(e,null,2))}</pre></details>`;}

function officialReportHTML(e){
  if(!e)return '';
  const lab={OVERALL_PROJECT:'乘方计划',UNI_PROJECT:'全域计划',ALL_DATA:'整体期间',OVER_ALL_DATA:'乘方期间',UNI_DATA:'全域期间',ALL:'全部',LIVE_PROM_GOODS:'直播间带货',VIDEO_PROM_GOODS:'商品带货',QIANCHUAN:'千川PC',ECP_AWEME:'小店随心推'};
  const q=e.query||{};
  return `<h3>官方账户报表原值</h3><p>账户 ${esc(e.account_id)}；${esc(lab[q.adlab_scene]||q.adlab_scene)} / ${esc(lab[q.data_period]||q.data_period)} / ${esc(lab[q.marketing_goal]||q.marketing_goal)} / ${esc(lab[q.order_platform]||q.order_platform)}。</p><p class="footnote">查询 ${esc(date(e.start_time))} 至 ${esc(date(e.end_time))}；采集 ${esc(date(e.captured_at))}；数据更新 ${esc(date(e.data_at))}。未知更新时间保持未采集。</p><table><thead><tr><th>来源指标</th><th>观察原值</th></tr></thead><tbody>${(e.values||[]).map(v=>`<tr><td>${esc(v.label)}</td><td>${esc(fmt(v.value))} ${esc(v.unit)}</td></tr>`).join('')}</tbody></table>${(e.limits||[]).map(v=>`<p class="footnote">${esc(v)}</p>`).join('')}`;
}

function officialPreviewHTML(data){
  return `<p>已校验 ${esc(data.received_reports)} 条结果，规范化为 ${esc(data.normalized_snapshots)} 个观察快照。尚未保存，也未调用平台接口。</p>${(data.preview||[]).map(officialReportHTML).join('')}`;
}

function officialReportTemplate(account){
  return {format:'qianchuan_account_report_v1',reports:[{endpoint:'/open_api/v1.0/qianchuan/report/all_promotion/get/',
    query:{advertiser_id:account.id,start_time:'填写实际查询开始时间',end_time:'填写实际查询结束时间',adlab_scene:'OVERALL_PROJECT',
      data_period:'ALL_DATA',marketing_goal:account.scene==='live'?'LIVE_PROM_GOODS':'VIDEO_PROM_GOODS',order_platform:'QIANCHUAN',
      fields:['stat_cost_for_roi2','total_prepay_and_pay_order_roi2']},
    captured_at:'填写实际采集时间（ISO 8601）',data_at:null,response:{code:0,data:{}}}]};
}
function customReportTemplate(account){
  const topic=account.scene==='live'?'SITE_PROMOTION_POST_DATA_VIDEO':'SITE_PROMOTION_PRODUCT_AD';
  return {format:'qianchuan_custom_report_review_v1',configuration:{endpoint:'/open_api/v1.0/qianchuan/report/uni_promotion/config/get/',
    query:{advertiser_id:account.id,data_topics:[topic]},response:{code:0,data:{custom_config_datas:[]}}},
    query:{advertiser_id:account.id,data_topic:topic,dimensions:[],metrics:[],filters:[],order_by:[],
      start_time:'填写实际查询开始时间',end_time:'填写实际查询结束时间',page_size:10},
    captured_at:'填写实际取得结果的时间',data_at:null,
    pages:[{endpoint:'/open_api/v1.0/qianchuan/report/uni_promotion/data/get/',page:1,response:{code:0,data:{rows:[],page_info:{}}}}]};
}
function creativeRotationTemplate(account){
  const binding=account.scene==='product'?{product_id:''}:{};
  return {format:'qianchuan_creative_rotation_registry_v1',duplicate_inventory:[{account_id:account.id,observed_at:'',source:'',verified:false,complete:false,groups:[{group_id:'',active_count:null}]}],plans:[{
    account_id:account.id,plan_id:'',parent_snapshot_id:'',observed_at:'',inventory_verified:false,inventory_complete:false,
    inventory_source:'',duplicate_groups_verified:false,multi_aweme:null,
    capacity:{unit:'configured_entries',active_count:0,maximum:0,verified:false,source:''},
    slots:[{snapshot_id:'',material_type:'VIDEO',material_id:'',native:{video_id:'',image_mode:'VIDEO_VERTICAL'},
      duplicate_group_id:'',mapping_verified:false,delete_class:'UNKNOWN',deletion_eligibility_verified:false,...binding}],
    pool:[{id:'',material_type:'VIDEO',native:{video_id:'',image_mode:'VIDEO_VERTICAL'},duplicate_group_id:'',
      enabled:false,reviewed:false,reviewed_at:'',review_source:'',queued_at:'',...binding}]}]};
}
function creativeRotationHTML(data){
  const stages={add:'添加新素材',verify_add:'独立核对新配置',cooldown:'同计划冷却',retire:'退旧（删除计划下自提素材）',verify_retire:'独立核对退旧结果',observe:'观察素材更新效果'};
  return `<p>本地草稿 ${esc((data.drafts||[]).length)} 个；候选由已声明审核的队列选择。尚未保存登记或草稿，未提交平台，未发送AI。持续编排与实际提交尚未接通。</p>${(data.drafts||[]).map(d=>`<article class="recommendation"><h3>账户 ${esc(d.account_id)} · 计划 ${esc(d.plan_id)} <span class="badge">${d.local_eligible?'满足声明的草稿条件':'阻断'}</span></h3>${d.selection?`<p>弱项 ${esc(d.selection.snapshot_id)}；原素材 ${esc(d.selection.old_material_id||d.selection.old_native?.title||'未确认')}；候选 ${esc(d.selection.candidate_id)}。</p><details><summary>查看新旧素材与用途</summary><pre>${esc(JSON.stringify(d.selection,null,2))}</pre></details>`:''}${d.blockers?.length?`<p class="blocked">${esc(d.blockers.join('；'))}</p>`:''}${d.stages?.length?`<ol>${d.stages.map(s=>`<li>${esc(stages[s.action]||s.action)}：${s.minimum_seconds?`至少 ${esc(s.minimum_seconds/60)} 分钟；`:''}${esc(s.requires||'每步均待新鲜证据与未来执行器验证，尚未执行')}</li>`).join('')}</ol>`:''}${d.requests?Object.entries(d.requests).map(([key,r])=>`<details><summary>${key==='add'?'添加请求结构':'退旧请求结构'} · ${r.synthetic_only?'合成示意，无执行地址':'仅本地预览'}</summary><p>${esc(r.documented_endpoint)}</p><pre>${esc(r.body_json)}</pre></details>`).join(''):''}<p class="footnote">不暂停母计划；删除不等同暂停创意。未知结果不会自动重试或解除锁定。</p></article>`).join('')}${(data.limits||[]).map(v=>`<p class="footnote">${esc(v)}</p>`).join('')}`;
}
function customReviewHTML(data){
  const cols=data.columns||[],rows=data.rows||[];
  return `<h3>${esc(data.topic_label)} · 账户 ${esc(data.account_id)}</h3><p>收到 ${esc(data.received_rows)} / 声明 ${esc(data.reported_rows)} 行；分页 ${esc(data.received_pages)} / ${esc(data.reported_pages)}。${data.pagination_exhausted?'所提供查询的分页已到末页':'分页尚未读完'}，不认证全账户覆盖。</p><p>查询 ${esc(data.query?.start_time)} 至 ${esc(data.query?.end_time)}；数据更新时间 ${esc(data.data_at||'未提供')}；缺失单元格 ${esc(data.missing_cells)} 个。</p><details><summary>查看实际查询与动态限制</summary><pre>${esc(JSON.stringify({query:data.query,query_limits:data.query_limits},null,2))}</pre></details><div class="table-wrap"><table><thead><tr>${cols.map(c=>`<th>${esc(c.name)}<small>${esc(c.field)} · ${esc(c.unit)}</small></th>`).join('')}</tr></thead><tbody>${rows.slice(0,50).map(r=>`<tr>${cols.map(c=>`<td><code>${r.cells?.[c.field]===null||r.cells?.[c.field]===undefined?'未返回':esc(r.cells[c.field])}</code></td>`).join('')}</tr>`).join('')}</tbody></table></div>${rows.length>50?'<p>页面仅显示前50行；下载核对结果可查看本次全部行。</p>':''}<p>尚未保存观察快照，未调用平台接口，未发送AI分析。</p>${(data.limits||[]).map(s=>`<p class="footnote">${esc(s)}</p>`).join('')}`;
}
function creativeWorkflowSaveHTML(data){
  return (data.drafts||[]).filter(d=>d.local_eligible&&d.synthetic_only===false&&d.intent_digest).map(d=>`<button class="quiet" data-rotation-save="${esc(d.intent_digest)}" data-account="${esc(d.account_id)}" data-plan="${esc(d.plan_id)}">保存计划 ${esc(d.plan_id)} 为本地待接通流程</button>`).join('');
}
function materialReadbackTemplate(account){
  return {format:'qianchuan_material_readback_v1',query:{account_id:account.id,plan_id:'',material_type:'VIDEO',start_date:'',end_date:'',page_size:10,max_pages:5},
    target:{native:{video_id:''},desired_presence:'present',...(account.scene==='product'?{product_id:''}:{})},
    request_started_at:'',received_at:'',detail_response:{code:null,data:{}},material_responses:[{code:null,data:{}}]};
}
function controlCreationHTML(data){
  return `<h3>合成一键起量创建预览</h3><p>虚构账户 ${esc(data.account_id)} · 母计划 ${esc(data.parent_plan_id)}。使用直播间购买，初始预算100元、时长1小时；保留用户策略的不支持成本保障提示。</p><p>原生创建客户端默认关闭；此合成示例没有执行地址，不发送请求、不保存快照或创建记录。新任务ID回执也不能代替可投状态核对，不会自动解除母计划占用。</p><details><summary>查看当前官方创建请求结构</summary><p>${esc(data.request?.documented_endpoint)}</p><pre>${esc(data.request?.body_json)}</pre></details><p class="blocked">${esc(data.chase_blocker)}</p>`;
}

function smartControlHTML(data){
  return `<h3>合成一键控量预览</h3><p>虚构账户 ${esc(data.account_id)} · 母计划 ${esc(data.parent_plan_id)}。示例预算 ${esc(data.budget)} 元，仅用于回放，不填入真实账户设置。</p><p>本地要求持续 ${esc(data.local_duration_policy?.duration_minutes)} 分钟；尚未注册截止，未启动计时器。创建接口不接收duration，不能据此声称会自动到期。</p><p>停止请求按母计划ad_id执行，不使用起量或追投任务ID。回执不证明实际停止，当前没有原生状态读回。</p>${[['创建请求结构',data.create_request],['要求的停止请求结构',data.stop_request]].map(([label,r])=>`<details><summary>${label} · 不含执行地址</summary><p>${esc(r?.documented_endpoint)}</p><pre>${esc(r?.body_json)}</pre></details>`).join('')}${(data.execution_blockers||[]).map(s=>`<p class="blocked">${esc(s)}</p>`).join('')}<p class="footnote">合成预览不保存快照、截止或提交记录，不修改用户目标，不调用账户接口。</p>`;
}
function creationReadbackTemplate(account){
  return {format:'qianchuan_control_creation_review_v1',attempt:{created_at:'',state:'unknown',context:{
    scope:'control_create',entity_id:null,account_id:account.id,parent_plan_id:'',action:'one_click_boost',control_type:'one_click_boost',
    contract:'control_create',contract_version:'2026-10-23-live-boost-v1',marketing_goal:'LIVE_PROM_GOODS',
    requested_external_action:'AD_CONVERT_TYPE_LIVE_SUCCESSORDER_PAY',request_body_json:'',request_digest:'',
    requested_budget:null,requested_duration_hours:null,cooldown_seconds:3600},result:{submitted:false,business_accepted:false,created_task_id:null,parent_plan_id:'',creation_receipt:{origin_verified:false}}},
    query_context:{account_id:account.id,parent_plan_id:'',marketing_goal:'LIVE_PROM_GOODS',scene:'SMART_BOOST',task_id:'',window_start:'',window_end:'',request_started_at:'',received_at:'',max_pages:5,page_size:10},raw_pages:[]};
}
function creationReadbackHTML(data){
  const r=data.comparison||{},labels={matches_requested_configuration:'返回配置与创建意图匹配',differs_requested_configuration:'返回配置与创建意图不同',inconclusive:'核对证据不足'};
  return `<h3>${data.synthetic?'合成示例 · ':''}创建后新任务核对</h3><p>账户 ${esc(r.account_id)} · 母计划 ${esc(r.parent_plan_id)} · 新任务 ${esc(r.task_id)}。</p><p>${esc(labels[r.status]||'核对状态未知')}；返回任务状态 ${esc(r.reported_task_status||'未提供')}。</p><p>预算 ${esc(r.observed_budget??'未知')} / 请求 ${esc(r.expected_budget??'未知')} 元；时长 ${esc(r.observed_duration_hours??'未知')} / 请求 ${esc(r.expected_duration_hours??'未知')} 小时。</p><p>收到 ${esc(r.pages_received)} / ${esc(r.reported_total_pages)} 页；${r.pagination_exhausted?'已读到本次查询末页':'还有未读页'}，不认证全账户任务清单。</p><p>创建时间原文 ${esc(r.reported_create_time||'未提供')}；未认证平台时钟。即使文件显示“结束”，也不会解除占用或改变本机尝试状态。</p>${(r.blockers||[]).map(s=>`<p class="blocked">${esc(s)}</p>`).join('')}${(data.limits||[]).map(s=>`<p class="footnote">${esc(s)}</p>`).join('')}<p>没有调用原生接口、写入观察快照或提交调控。${esc(data.note||'')}</p>`;
}
function controlDeadlineHTML(data){
  return `<h3>合成追投截止恢复</h3><p>虚构账户 ${esc(data.account_id)} · 母计划 ${esc(data.parent_plan_id)} · 素材 ${esc(data.material_id)}。预留 ${esc(data.duration_hours)} 小时，截止 ${esc(date(data.due_at))}。</p><p>任务ID ${esc(data.task_id??'未取得')}；没有原生创建合同或结束请求，不会调用真实客户端。</p>${(data.timeline||[]).map(r=>`<p>${esc(date(r.at))} · ${r.due?'到期，仍需核对任务身份':'未到期'} · ${esc(r.note)}</p>`).join('')}<p>临时库已清理；真实监测库、创建尝试和投放均未改变。直播控成本追投创建仍受阻，不能据此认为可靠截止已经上线。</p><p class="footnote">${esc(data.note||'')}</p>`;
}
function chaseCreationTemplate(account){
  return {format:'qianchuan_material_chase_creation_v1',account_id:account.id,proposal_id:'',creative_snapshot_id:'',parent_plan_id:'',parent_snapshot_id:'',conversion_bid:null,
    material:{material_id:'',native:{video_id:''},snapshot_id:'',material_type:'VIDEO',material_status:'',audit_status:'',is_delete:null,mapping_verified:false,observed_at:''},
    audience:{aweme_fan_accounts:[],aweme_fan_behaviors:[]},targeting_review:{reviewed:false,observed_at:'',parent_snapshot_id:'',method:'reviewed_fan_audience'}};
}
function chaseCreationHTML(data){
  const d=data.draft;
  return `<h3>${data.synthetic?'合成示例 · ':''}素材追投创建预览</h3><p>${data.prepared?'本地配置已准备，仍无执行资格':'当前来源或设置不足'}；未创建任务、未注册截止、未调用原生接口。</p>${d?`<p>账户 ${esc(d.account_id)} · 母计划 ${esc(d.parent_plan_id)} · 原生素材 ${esc(d.material_id)}。</p><p>预算 ${esc(d.budget)} 元；独立成交出价 ${esc(d.conversion_bid)} 元；账户考核ROI ${esc(d.account_assessment_target)}。考核目标不传成任务ROI或出价。</p><p>请求采用控成本直播间成交及已登记达人定向；本地截止要求 ${esc(d.requested_local_duration_hours)} 小时，接口不发送duration，仍未接通可靠截止调度。</p><details><summary>查看准确原生请求文本（没有执行地址）</summary><pre>${esc(d.body_json)}</pre></details>`:''}${[...(data.blockers||[]),...(data.execution_blockers||[])].map(s=>`<p class="blocked">${esc(s)}</p>`).join('')}${(data.limits||[]).map(s=>`<p class="footnote">${esc(s)}</p>`).join('')}<p>${esc(data.note||'登记只作当前来源核对，不保存投放快照、创建或截止记录。')}</p>`;
}
function materialReadbackHTML(data){
  const states={present:'返回配置含目标原生标识',not_observed:'返回配置未见目标原生标识',ambiguous:'配置存在范围冲突',unavailable:'未能确定该配置范围'};
  return `<h3>账户 ${esc(data.account_id)} · 计划 ${esc(data.plan_id)}</h3><p>${esc(states[data.configuration?.state]||'配置状态未知')}。这里只比较原生标识或静态标题，不核对封面和画幅等完整设置。</p><p>收到 ${esc(data.received_pages)} / ${esc(data.reported_pages)} 页，声明 ${esc(data.reported_rows)} 行；${data.pagination_exhausted?'已到所提供查询末页':'查询分页尚未到末页'}。列表仅覆盖查询日期内有消耗的素材，未命中不证明已删除。</p><p>匹配列表观察 ${esc(data.matched_observations)} 条；具体商品/抖音用途的可用状态尚未认证，不解除流程占用。</p>${data.material_observations?.length?`<div class="table-wrap"><table><thead><tr><th>平台素材ID</th><th>投放状态</th><th>审核状态</th><th>删除标记</th><th>素材选择</th></tr></thead><tbody>${data.material_observations.map(r=>`<tr><td>${esc(r.material_id)}</td><td>${esc(r.material_status||'未提供')}</td><td>${esc(r.audit_status||'未提供')}</td><td>${esc(r.is_delete==null?'未提供':r.is_delete?'是':'否')}</td><td>${esc(r.material_select_type||'未提供')}</td></tr>`).join('')}</tbody></table></div>`:''}<p>计划修改时间原文 ${esc(data.configuration?.modify_time_raw||'未提供')}；不认证平台状态时钟或执行归因。</p>${[...(data.configuration?.notes||[]),...(data.limits||[])].map(s=>`<p class="footnote">${esc(s)}</p>`).join('')}<p>核对未保存观察快照或流程记录，未发送AI，未调用平台。</p>`;
}
function creativeWorkflowsHTML(data){
  const labels={awaiting_native_integration:'等待原生素材接入',ready_add:'合成：待添加',add_claimed:'添加已占用，等待核对',add_unknown:'添加结果未知，保持占用',verify_add:'合成：待核对添加',cooldown:'合成：同计划冷却',ready_retire:'合成：待重新评估退旧',retire_claimed:'退旧已占用，等待核对',retire_unknown:'退旧结果未知，保持占用',verify_retire:'合成：待核对退旧',observe:'合成：观察更新后数据',completed_synthetic:'合成流程完成',cancelled_before_add:'添加前已取消'};
  return `<p>真实素材提交尚未接通；这里的保存、取消和占用均发生在本机。</p>${(data.workflows||[]).map(w=>`<article class="recommendation"><h3>账户 ${esc(w.account_id)} · 计划 ${esc(w.plan_id)}</h3><p>${esc(labels[w.state]||w.state)} · 版本 ${esc(w.version)}；更新 ${esc(date(w.updated_at))}。</p><p>母计划预留：${w.parent_reservation_held?'保留':'已释放'}；重复数量预留：${w.group_reservation_held?'保留':'已释放'}。真实投放动作 0 次。</p>${w.last_blockers?.length?`<p class="blocked">${esc(w.last_blockers.join('；'))}</p>`:''}${w.state==='awaiting_native_integration'?`<button class="quiet" data-workflow-cancel="${esc(w.id)}">取消添加前流程并释放本地预留</button>`:''}<details><summary>查看阶段记录</summary><pre>${esc(JSON.stringify(w.journal||[],null,2))}</pre></details></article>`).join('')||'<p class="muted">没有已保存的素材流程。</p>'}`;
}
function creativeWorkflowReplayHTML(data){
  return `<h3>素材流程断点恢复回放 <span class="badge">全部合成</span></h3><p>阶段保存到临时 SQLite，并在每一步重新打开。临时库已清理，真实流程未改变；合成动作 ${esc(data.synthetic_calls?.length||0)} 次。</p>${creativeWorkflowsHTML({workflows:[{...data.workflow,journal:data.journal}]})}<ol>${(data.timeline||[]).map(p=>`<li>${esc(date(p.at))} · ${esc(p.state)} · 版本 ${esc(p.version)}</li>`).join('')}</ol><p>${esc(data.note)}</p>`;
}
async function loadCreativeWorkflows(){
  const ticket=++workflowListTicket,data=await api('/api/creative-workflows');
  if(ticket===workflowListTicket)$('#creative-workflows').innerHTML=creativeWorkflowsHTML(data);
}
function outcomeHTML(report,accountId){
  const items=(report?.items||[]).filter(i=>!accountId||i.account_id===accountId);
  if(!items.length)return '<p class="muted">尚无操作报告。现有建议不会被自动记为已应用。</p>';
  const names={observed:'同源窗口已交叉核对',insufficient:'证据不足',pending_window:'等待变更后窗口',not_applied:'未声明已应用',invalid:'报告无效',duration_pending:'按报告等待到期',expired_pending_verification:'报告时长已到，任务状态待核验',maintenance_review:'转入素材维护顺序复核',roi_recovery_review:'零消耗基线转入ROI恢复复核'};
  return items.map(i=>`<article class="recommendation"><h3>${esc(names[i.review_status]||i.review_status)} <span class="badge">${i.source==='simulation'?'合成模拟':i.source==='manual_verified_report'?'人工报告':'来源未确认'}</span></h3><p>对象 ${esc(i.entity_id||'未知')}；记录 ${esc(i.event_id)}；动作 ${esc(i.action||'未知')}。</p><p>${esc(i.note)}</p>${i.comparison?`<p>观察窗口内消耗速度变化 ${esc(fmt(i.comparison.ad_spend_change_percent))}%；ROI ${esc(fmt(i.comparison.roi_before))} → ${esc(fmt(i.comparison.roi_after))}。变化不证明因果或利润。</p>`:''}${i.expiry?`<p>报告到期时间 ${esc(date(i.expiry.due_at))}；未确认任务停止。</p>${(i.expiry.policy_warnings||[]).map(w=>`<p class="blocked">${esc(w)}</p>`).join('')}`:''}${i.blockers?.length?`<p class="blocked">${esc(i.blockers.join('；'))}</p>`:''}${(i.candidates||[]).map(c=>`<p>${esc(c.action_label)}：${esc(fmt(c.before))} → ${esc(fmt(c.after))}；${c.historical?'历史观察，':''}仅供复核，没有可执行资格。</p>`).join('')}${evidenceHTML(i.evidence)}</article>`).join('');
}
function alertHTML(a){return `<article class="alert-row ${esc(a.priority.toLowerCase())}"><h3><span class="priority">${esc(a.priority)}</span>${esc(a.title)}</h3><p>对象 ${esc(a.entity_id||'数据接入')} · ${a.evidence?.observation_kind==='plan_status'?'计划状态观察（不评价ROI）':esc(basisNames[a.source_basis]||a.source_basis||'数据校验')}</p>${evidenceHTML(a.evidence)}${a.risk?`<p>风险：${esc(a.risk)}</p>`:''}${basisHTML(a.policy_basis)}</article>`;}
function emergencyHTML(rows){
  if(!rows?.length)return '<p class="muted">没有取得主力计划或账户级应急观测。未出现告警不表示风险已经排除。</p>';
  const names={triggered:'需核验',insufficient:'证据不足',observed:'已观察，未触发该条件'};
  return rows.map(o=>`<article class="recommendation"><h3>对象 ${esc(o.entity_id)} <span class="badge">仅观测</span></h3><p>${o.is_current?'数据时间有效':'历史或未校验，不代表当前状态'}；数据更新至 ${esc(date(o.data_at))}。</p>${o.primary_spend_stop?`<p>主力停耗：${esc(names[o.primary_spend_stop.state]||o.primary_spend_stop.state)}；基础活跃段消耗 ${esc(fmt(o.primary_spend_stop.baseline_spend))} 元，已观察不增长 ${esc(fmt(o.primary_spend_stop.flat_minutes))} 分钟。</p><p>${esc(o.primary_spend_stop.note||(o.primary_spend_stop.missing||[]).join('；'))}</p><p>短窗观测不证明断流原因，不直接启停主力计划。</p>`:''}${flowStopHTML(o.primary_flow_stop)}${o.roi_trend?`<p>账户ROI持续下滑：${esc(names[o.roi_trend.state]||o.roi_trend.state)}。${esc((o.roi_trend.missing||[]).join('；'))}</p>`:''}${(o.scores||[]).map(s=>`<p>${esc(s.label)} ${esc(fmt(s.value))}；用户阈值 ${esc(fmt(s.user_threshold))}；较上次明确观测变化 ${esc(fmt(s.change))}。${esc(names[s.state]||s.state)}。</p><p>${esc((s.missing||[]).join('；'))} 阈值不是官方封禁或赔付线。</p>`).join('')}${evidenceHTML(o)}</article>`).join('');
}
function flowStopHTML(flow){
  if(!flow)return '';
  const names={triggered:'需立即核验',insufficient:'证据不足',observed:'已观察，未触发该条件'};
  return `<p>主力展示停滞：${esc(names[flow.state]||flow.state)}；基线展示增量 ${esc(fmt(flow.baseline_impressions,0))} 次，不增长时长 ${esc(fmt(flow.flat_minutes))} 分钟。</p><p>${esc(flow.note||(flow.missing||[]).join('；'))} 展示基线至少1000次为本地保护线，观测不证明断流原因。</p>`;
}
function parameterHTML(parameters){
  const labels={budget:'测试预算（元）',duration_hours:'投放时长（小时）',duration_minutes:'投放时长（分钟）',optimization_goal:'优化目标',cost_guarantee:'成本保障',mode:'投放模式',target_roi:'任务ROI目标',parent_plan_id:'母计划ID',material_id:'素材ID',differentiated_targeting_verified:'差异化目标/定向已人工核验'};
  Object.assign(labels,{roi_recovery_root_id:'原始ROI下调报告',original_plan_goal:'原计划ROI设置',step_number:'回调步骤',
    observed_daily_budget:'观察到的日预算（元）',strategy_initial_daily_budget:'用户初始日预算策略（元）'});
  const values={live_purchase:'直播间购买',cost_control:'控成本',not_supported_user_rule:'用户策略：不支持保障，盈亏自负'};
  const entries=Object.entries(parameters||{});
  if(!entries.length)return '';
  return `<dl class="action-parameters">${entries.map(([k,v])=>`<div><dt>${esc(labels[k]||k)}</dt><dd>${esc(v==null?'尚未确认':typeof v==='boolean'?(v?'是':'否'):typeof v==='object'?JSON.stringify(v):values[v]||v)}</dd></div>`).join('')}</dl>`;
}
function basisHTML(basis){
  if(!basis)return '<p class="blocked">依据尚未附齐，需先复核适用规则。</p>';
  return `<details class="evidence"><summary>策略与官方资料依据</summary><p>${esc(basis.strategy.source)} · ${esc(basis.strategy.section)}；本地规则版本 ${esc(basis.ruleset_version)}。</p><p>${esc(basis.strategy.note)}</p>${(basis.official_references||[]).map(r=>`<p><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a>；来源 ${esc(r.source_label||r.title)}；核验 ${esc(r.checked_at)}；${esc(r.verification)}。${esc(r.application)}。${esc(r.limit||'按适用产品与当前权限另行核对')}。</p>`).join('')}${(basis.unverified||[]).map(r=>`<p class="blocked">${esc(r)}</p>`).join('')}</details>`;
}
function recommendationHTML(r){
  const status=['pause_plan','resume_plan','pause_control','pause_creative'].includes(r.action);
  const label=status?'状态':r.action.startsWith('budget_')||r.action==='control_budget_up'?'预算':r.action.startsWith('roi_target_')?'ROI 目标':'出价';
  const preview=state?.evaluation.recommendations.some(x=>x.id===r.id)?`<button class="quiet" data-preview="${esc(r.id)}">查看离线请求校验</button><pre class="request-preview" hidden></pre>`:'';
  const canPrepare=r.eligible&&r.scope==='plan'&&(state?.browser_control?.workers||[]).some(w=>w.armed&&w.surfaces.some(s=>s.account_id===r.account_id&&s.plan_id===r.entity_id&&s.actions.includes(r.action)));
  const prepare=canPrepare?`<button class="quiet" data-browser-recommendation="${esc(r.id)}">准备逐条执行</button>`:'';
  const roi=r.action.startsWith('roi_target_');
  const value=v=>status?(v===true?'开启':v===false?'关闭':'未确认'):roi?roiValue(v):fmt(v);
  return `<article class="recommendation"><h3><span class="priority">${esc(r.priority)}</span>${esc(r.action_label||r.action)}${r.change_pct!=null?` ${r.change_pct>0?'+':''}${fmt(r.change_pct*100,roi?2:0)}%`:''} <span class="badge">待复核建议</span></h3><p>证据对象 ${esc(r.entity_id)}${r.target_id?`；操作对象 ${esc(r.target_type||r.scope)} / ${esc(r.target_id)}`:''}${r.before!=null?`；${label} ${value(r.before)} → ${value(r.after)}`:''}</p>${roi?planRoiHTML(r.evidence):''}<p>${esc(r.reason)}。预期目标：${esc(r.expected_goal)}</p>${parameterHTML(r.parameters)}<p class="${r.eligible?'':'blocked'}">${esc(r.eligible?(canPrepare?'规则条件满足，可准备逐条审批；尚未执行':'已满足声明的离线模拟条件；实际执行不可用，浏览器适配未就绪'):`阻断原因：${r.blockers.join('；')}`)}</p>${evidenceHTML(r.evidence)}${(r.policy_basis?.risk_notes||[r.risk]).map(v=>`<p>风险：${esc(v)}</p>`).join('')}${basisHTML(r.policy_basis)}${preview}${prepare}</article>`;
}

function roiValue(v){return v==null?'待核对':esc(Number(v).toLocaleString('zh-CN',{maximumFractionDigits:6}));}
function planRoiHTML(evidence){
  const c=evidence?.plan_roi_configuration;if(!c)return '<p class="blocked">计划当前ROI设置及输入规则尚未核对。</p>';
  return `<p>用户考核目标 ${roiValue(evidence.strategy_target_roi)}；计划当前设置 ${roiValue(c.observed_plan_goal)}。${c.after==null?'未生成具体新值。':'实际微调 '+esc(fmt(c.actual_change_pct*100))+'%。'}</p><p class="footnote">声明输入步长 ${roiValue(c.step)}；允许范围 ${roiValue(c.minimum)} 至 ${roiValue(c.maximum)}；配置观测 ${esc(date(c.observed_at))}。输入规则是来源声明，平台合同尚未认证。</p>`;
}

function reportHTML(report){
  const e=report.evidence_and_risks;
  return `<p class="muted">报告生成于 ${esc(date(report.generated_at))}；${esc({empty:'暂无来源',synthetic_only:'合成回放',mixed_evidence:'合成与观察混合证据',observations:'本地观察'}[report.data_mode]||'来源待核验')}；账户 ${esc(report.account.id)}。数据时间见每个独立来源。</p><h3>核心结论</h3><ul>${report.core_conclusions.map(c=>`<li>${esc(c)}</li>`).join('')}</ul><h3>分级行动指令</h3>${report.prioritized_actions.map(i=>recommendationHTML({id:i.id,priority:i.priority,entity_id:i.object.evidence_entity_id,target_id:i.object.target_id,target_type:i.object.target_type,scope:i.object.scope,action:i.action.type,action_label:i.action.label,before:i.action.before,after:i.action.after,change_pct:i.action.change_pct,parameters:i.action.parameters,reason:i.reason,expected_goal:i.expected_goal,eligible:i.offline_eligible,blockers:i.blockers,evidence:i.evidence,policy_basis:i.policy_basis})).join('')||'<p>未取得足够证据生成行动建议，不表示账户健康。</p>'}<h3>数据佐证与风险提示</h3><p>来源 ${esc(report.evidence_summary.source_count)} 个；离线条件满足 ${esc(report.evidence_summary.offline_eligible_actions)} 条。真实账户执行不可用。</p>${e.risks.map(v=>`<p>${esc(v)}</p>`).join('')}${evidenceHTML({独立来源与时间:e.sources,分级告警:e.alerts,缺失或待核验指标:e.metrics_not_currently_verified,应急观测:e.emergency_observations,流量质量:e.traffic_quality_observations,持续采集:e.collection_health,后台轮次:e.monitor_runtime,商品承接:e.product_carrying,直播互斥复核:e.live_exclusion_reviews,ROI恢复复核:e.roi_recovery_reviews,生命周期复核:e.lifecycle_reviews,衰退维护:e.maintenance_reviews,操作报告复核:e.outcome_reviews,预算交接:e.budget_migrations})}`;
}
function downloadText(text,filename,type){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type}));a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
$('#report-view').addEventListener('click',event=>action(event.target,async()=>{
  const account=$('#account-select').value,report=await api('/api/report?'+new URLSearchParams({account_id:account}));
  if(account!==$('#account-select').value)return;
  $('#review-report').innerHTML=reportHTML(report);
}));
$('#report-json').addEventListener('click',event=>action(event.target,async()=>{
  const account=$('#account-select').value,report=await api('/api/report?'+new URLSearchParams({account_id:account}));
  downloadText(JSON.stringify(report,null,2),`千川复核报告-${account}.json`,'application/json');
}));
$('#report-markdown').addEventListener('click',event=>action(event.target,async()=>{
  const account=$('#account-select').value,res=await fetch('/api/report?'+new URLSearchParams({account_id:account,format:'markdown'}));
  if(!res.ok){const error=await res.json();throw new Error(error.error||'报告导出失败');}
  downloadText(await res.text(),`千川复核报告-${account}.md`,'text/markdown');
}));

function migrationHTML(plans,allowCancel=false){
  const names={ready_reduce:'等待缩减旧计划',pending_source_review:'等待旧计划观察',ready_raise:'等待追加新计划',pending_target_review:'等待新计划观察',completed_reported:'报告及观察已对照',held:'暂停交接待核对',expired:'已跨日，需重建',cancelled:'本地计划已取消',blocked:'条件未满足'};
  if(!plans.length)return '<p class="muted">尚无本地预算交接计划。建立计划不会操作账户。</p>';
  return plans.map(p=>`<article class="recommendation"><h3>${esc(names[p.state]||p.state)} <span class="badge">${p.evidence_kind==='synthetic'?'合成模拟':'仅本地复核'}</span></h3>${p.source&&p.target?`<p>旧计划 ${esc(p.source.entity_id)}：${fmt(p.source.before)} → ${fmt(p.source.after)}；新计划 ${esc(p.target.entity_id)}：${fmt(p.target.before)} → ${fmt(p.target.after)}。</p><p>交接额度 ${fmt(p.amount)} 元；两侧预算总额 ${fmt(p.total_before)} → ${fmt(p.total_after)} 元。</p>`:''}<p>${esc(p.note)}</p>${p.next_step?`<p>下一复核阶段：${esc(p.next_step.action==='budget_down'?'缩减旧计划':'追加新计划')}。没有真实执行资格。</p>`:''}${p.blockers?.length?`<p class="blocked">${esc(p.blockers.join('；'))}</p>`:''}${allowCancel&&['ready_reduce','pending_source_review','ready_raise','pending_target_review','held'].includes(p.state)?`<button class="quiet" data-cancel-migration="${esc(p.id)}">取消本地交接计划</button>`:''}</article>`).join('');
}
function renderMigrations(plans,accountId){
  const rows=selectedRows().filter(s=>s.scope==='plan');
  for(const selector of ['#migration-source','#migration-target']){
    const el=$(selector),selected=el.value;
    el.innerHTML='<option value="">选择已观察计划</option>'+rows.map(s=>`<option value="${esc(s.id)}">${esc(s.display_name||s.entity_id)} / ${esc(basisNames[s.metric_basis])} / ${esc(s.source.dataset_id||s.source.series_key)}</option>`).join('');
    if(rows.some(s=>s.id===selected))el.value=selected;
  }
  $('#budget-migrations').innerHTML=migrationHTML(plans.filter(p=>p.account_id===accountId),true);
}

function renderChart(rows,costKey,amountKey){
  const points=rows.filter(s=>s.source.data_at&&s.metrics[costKey]!=null&&s.metrics[amountKey]!=null).sort((a,b)=>new Date(a.source.data_at)-new Date(b.source.data_at));
  $('#trend-caption').textContent=points.length?`${points.length} 个可比较样本`:'';
  if(points.length<2){$('#chart').innerHTML='<div class="chart-empty"><strong>趋势等待接入</strong><p>取得至少两个同日期、同口径且带数据时间的快照后展示。</p></div>';return;}
  const width=700,height=230,pad=44,max=Math.max(...points.map(p=>Math.max(p.metrics[costKey],p.metrics[amountKey])),1);
  const first=new Date(points[0].source.data_at).getTime(),last=new Date(points.at(-1).source.data_at).getTime();
  const x=p=>pad+(new Date(p.source.data_at)-first)/(last-first||1)*(width-pad-14);
  const y=v=>height-30-v/max*(height-48);
  const line=key=>points.map((p,i)=>`${i?'L':'M'}${x(p).toFixed(2)} ${y(p.metrics[key]).toFixed(2)}`).join(' ');
  let grid='';for(let i=0;i<4;i++){const v=max*i/3,yy=y(v);grid+=`<line x1="${pad}" y1="${yy}" x2="${width-12}" y2="${yy}" stroke="#e1eaee"/><text x="${pad-7}" y="${yy+4}" text-anchor="end" fill="#627b86" font-size="10">${esc(fmt(v,0))}</text>`;}
  const timeLabel=p=>new Date(p.source.data_at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,hour:'2-digit',minute:'2-digit'});
  $('#chart').innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="同口径累计消耗与成交趋势">${grid}<path d="${line(costKey)}" fill="none" stroke="#174d66" stroke-width="2.5"/><path d="${line(amountKey)}" fill="none" stroke="#167985" stroke-width="2.5"/><text x="${pad}" y="${height-4}" font-size="11" fill="#627b86">${esc(timeLabel(points[0]))}</text><text x="${width-12}" y="${height-4}" text-anchor="end" font-size="11" fill="#627b86">${esc(timeLabel(points.at(-1)))}</text></svg>`;
}

function renderLegacy(){
  const l=state.diagnostics.legacy;
  const html=l?`<p class="muted">最后检查 ${esc(date(l.updated_at))}；原程序未被修改。</p><ul class="problem-list">${(l.issues||[]).map(i=>`<li>${esc(i)}</li>`).join('')}</ul><p class="muted">旧库最近指标时间：${esc(date(l.last_metrics_at))}；白名单快照 ${esc(l.scoped_snapshots)} 条；排除其他账户 ${esc((l.excluded_accounts||[]).length)} 个。</p>`:'<p class="muted">正在进行首次只读检查。</p>';
  $('#legacy-status').innerHTML=html;$('#legacy-connect').innerHTML=html;
  $('#legacy-log').textContent=(l?.log_tail||[]).join('\n')||'暂无日志片段';
}

function supervisionHTML(v){
  const status={starting:'正在启动',child_running:'子进程运行记录',backoff:'等待恢复',stopped:'已停止',port_in_use:'端口被占用，未接管',manager_not_observed:'未观察到活跃守护器',supervisor_error:'守护器出错',stop_failed:'停止未确认',not_managed:'未启用守护器'};
  return `<p><strong>${v.current_process_supervised?'当前服务由守护器托管':'当前服务未确认由守护器托管'}</strong>；${esc(status[v.state]||v.state||'状态未知')}。已启动 ${esc(v.launch_count??0)} 次，重新启动 ${esc(v.restart_count??0)} 次；最近退出码 ${esc(v.last_exit_code??'未记录')}。</p><p class="footnote">${esc(v.note||'进程记录不证明指标更新或连续覆盖。')}停止请使用“停止守护监测.cmd”。Windows后台任务状态请用“查看后台状态.cmd”核验；进程托管不能代替后台任务、Chrome登录或设备在线证明。</p>`;
}

function parentGuardHTML(v){
  if(!v)return '';
  return `<p>父进程退出保护：${v.mode==='parent_handle'&&v.watcher_alive?'已启动；实际父进程退出后，本服务自行退出':'当前未确认启动'}。不结束其他进程，也不证明外层后台任务正在运行。</p>`;
}

function collectionHealthHTML(data,runtime,accountId){
  const states={partial_observation:'部分来源时间有效，覆盖待核验',missing:'尚无真实来源',synthetic_only:'仅有合成回放',expired:'观察或指标已过期',data_time_missing:'缺少指标更新时间',data_conflict:'指标时间存在矛盾',recent:'近期已收到',invalid_time:'时刻无效',conflicting_time:'同次采集时刻矛盾',historical:'历史统计日期',date_mismatch:'日期不一致',ok:'后台轮次完成',error:'后台轮次失败',running:'后台轮次进行中',starting:'等待首轮完成',stalled:'后台轮次超时',unchanged:'指标时刻未前进',advancing:'指标时刻有前进',regressed:'指标时刻回退',unknown:'无法比较'};
  const a=data?.accounts?.find(v=>v.account_id===accountId),rows=(data?.sources||[]).filter(v=>v.account_id===accountId);
  const c=runtime?.collector,b=runtime?.browser_communication,h=runtime?.history;
  const supervision=(runtime?.supervision?supervisionHTML(runtime.supervision):'')+parentGuardHTML(runtime?.parent_guard);
  return supervision+`<p>${esc(states[a?.state]||'尚无来源检查结果')}；当前时间有效来源 ${esc(a?.clock_current_sources??0)} / 已观察真实来源 ${esc(a?.observed_sources??0)}。</p><p>只检查已取得的数据，不证明全量覆盖或7×24连续运行；没有当前数据不能推断零消耗或账户健康。</p>${c?`<p>后台：${esc(states[c.state]||c.state)}；上次开始 ${esc(date(c.last_cycle_started_at))}，完成 ${esc(date(c.last_cycle_finished_at))}。${c.last_error_type?`失败类型 ${esc(c.last_error_type)}。`:''}</p>`:''}${b?`<p>浏览器通信：${esc(states[b.state]||b.state)}；最后通信 ${esc(date(b.last_seen_at))}。通信不证明指标更新。</p>`:''}${h?`<p>保留后台轮次 ${esc(h.retained_cycles)}；失败 ${esc(h.error_cycles)}，跨重启 ${esc(h.restart_transitions)}，旧轮次未完成 ${esc(h.previous_unfinished_cycles)}。已记录长间隔 ${(h.observed_gaps||[]).length} 次；不补造停机期间的数据。</p>`:''}${rows.map(s=>`<p>${esc(scopeNames[s.scope]||s.scope)} ${esc(s.entity_id)}${s.synthetic?'（合成）':''}：采集 ${esc(date(s.captured_at))}（${esc(states[s.capture_state]||s.capture_state)}）；指标 ${esc(date(s.data_at))}（${esc(states[s.data_state]||s.data_state)}）；${esc(states[s.progress]||s.progress)}。指标时刻未前进不等于无消耗。</p>`).join('')}`;
}

function renderConnect(){
  const ext=state.diagnostics.extension;
  if(ext){const health=state.monitor_health?.browser_communication;const seen=health?.last_seen_at||ext.last_seen||ext.last_ingest_at;const stale=health?health.state!=='recent':!seen||!Number.isFinite(new Date(seen).getTime())||Math.abs(Date.now()-new Date(seen).getTime())>180000;
    const mode=ext.collector==='codex_plugin'?'当前会话通过已有谷歌插件读取；会话外不承诺自动采集':'独立只读扩展采集';
    $('#extension-state').textContent=`${mode}；${stale?'最近通信已过期或时刻无效':'最近通信已收到，指标更新另行核验'}；最后通信 ${date(seen)}。${(ext.issues||[]).join('；')}`;
  }else $('#extension-state').textContent='尚未连接浏览器采集器；当前不会读取 Chrome 页面。';
}

const ruleLabels={max_age_seconds:['数据过期阈值（秒）',60,3600,60],min_spend:['每小时最小消耗样本（元）',0,1000000,10],min_orders:['每小时最小订单样本',1,10000,1],roi_low_ratio:['低 ROI / 目标 ROI',.1,1,.05],budget_step_up:['单次预算上调比例',.01,.2,.01],budget_step_down:['单次预算下调比例',.1,.3,.01],daily_budget_up_cap:['日累计加预算 / 原预算',.01,.5,.05],cooldown_minutes:['调整间隔（分钟）',60,1440,30],spend_surge_ratio:['小时消耗突增倍数',1.1,10,.1]};
Object.assign(ruleLabels,{growth_roi_max_ratio:['三小时ROI最高/最低上限（本地策略）',1,1.2,.01,1.2],growth_cost_max_ratio:['三小时转化成本最高/最低上限（本地策略）',1,1.2,.01,1.2],growth_spend_max_ratio:['三小时消耗速度最高/最低上限（本地策略）',1,1.3,.01,1.3]});
function accountRegistryHTML(view,draft){
  const locked=!(view?.scope_changes_available??view?.collector_ready_for_scope_changes);
  return draft.map((a,i)=>`<div class="registry-row" data-registry-row="${i}"><label>账户ID<input data-field="id" value="${esc(a.id)}" ${locked?'disabled':''} ${(view?.accounts||[]).some(v=>v.id===a.id)?'readonly':''} inputmode="numeric" placeholder="数字账户ID"></label><label>显示名称<input data-field="name" value="${esc(a.name)}" maxlength="80"></label><label>账户场景<select data-field="scene" ${locked?'disabled':''}><option value="live" ${a.scene==='live'?'selected':''}>直播户</option><option value="product" ${a.scene==='product'?'selected':''}>图文/短视频户</option></select></label><label>API投放入口<select data-field="delivery_entry">${Object.entries({uni_promotion:"全域",chengfang:"乘方",both:"全域与乘方"}).map(([value,label])=>`<option value="${value}" ${(a.delivery_entry||"uni_promotion")===value?"selected":""}>${label}</option>`).join("")}</select></label><label>Chrome分组<input data-field="tab_group" value="${esc(a.tab_group)}" ${locked?'disabled':''} maxlength="100" placeholder="完全一致的分组名称"></label><label class="check"><input data-field="observe" type="checkbox" ${a.observe?'checked':''} ${locked?'disabled':''}>启用监测</label><button class="quiet" data-remove-account="${i}" ${locked?'disabled':''}>移出配置</button></div>`).join('');
}
function renderAccountRegistry(){
  $('#accounts-editor').innerHTML=accountRegistryHTML(accountRegistry,accountDraft);
  $('#account-add').disabled=!(accountRegistry?.scope_changes_available??accountRegistry?.collector_ready_for_scope_changes)||accountDraft.length>=100;
  $('#accounts-message').textContent=accountRegistry?.api_connected?'官方API已授权，可按账户ID新增监测；选择场景和入口后保存。API访问范围仍由平台授权核验，无需Chrome投放页面。':accountRegistry?.collector_ready_for_scope_changes?'已收到支持账户配置的采集器通信。勾选启用并保存后，才读取新增范围；移出配置不删除历史数据。':'采集器尚未确认支持账户配置。请先在Chrome重新加载新版扩展，再点“重新载入”。当前可修改显示名称和API投放入口；Chrome采集范围保持原值。';
}
async function loadAccountRegistry(){
  accountRegistry=await api('/api/accounts');accountDraft=accountRegistry.accounts.map(a=>({...a}));renderAccountRegistry();
}
function readAccountDraft(){
  return [...document.querySelectorAll('[data-registry-row]')].map(row=>Object.fromEntries(['id','name','scene','tab_group','observe','delivery_entry'].map(k=>{const el=row.querySelector(`[data-field="${k}"]`);return [k,k==='observe'?el.checked:k==='tab_group'?el.value:el.value.trim()];})));
}
$('#account-add').addEventListener('click',()=>{
  if(!(accountRegistry?.scope_changes_available??accountRegistry?.collector_ready_for_scope_changes))return;
  accountDraft=readAccountDraft();accountDraft.push({id:'',name:'',scene:'live',tab_group:accountRegistry?.api_connected?'API账户':'',observe:false,delivery_entry:'uni_promotion'});renderAccountRegistry();
});
$('#accounts-editor').addEventListener('click',event=>{
  const button=event.target.closest('[data-remove-account]');if(!button||!(accountRegistry?.scope_changes_available??accountRegistry?.collector_ready_for_scope_changes))return;
  accountDraft=readAccountDraft();accountDraft.splice(Number(button.dataset.removeAccount),1);renderAccountRegistry();
});
$('#accounts-reload').addEventListener('click',event=>action(event.target,loadAccountRegistry));
$('#accounts-save').addEventListener('click',event=>action(event.target,async()=>{
  if(!accountRegistry)throw new Error('请先载入账户配置');
  await api('/api/accounts',{accounts:readAccountDraft(),expected_revision:accountRegistry.revision});
  await load();renderForms();await loadAccountRegistry();notice('已保存账户接入配置；没有提交平台账户操作。');
}));

function renderForms(){
  $('#account-settings').innerHTML=state.settings.accounts.map(a=>`<div class="account-form" data-account="${esc(a.id)}"><div><strong>${esc(a.name)}</strong><small>${esc(a.id)}</small></div><label>目标 ROI<input class="target-roi" type="number" min="0.01" max="10000" step="0.01" placeholder="尚未设置" value="${a.target_roi??''}"></label><label>目标口径<select class="target-basis">${Object.entries(basisNames).map(([k,v])=>`<option value="${k}" ${a.roi_basis===k?'selected':''}>${v}</option>`).join('')}</select></label></div>`).join('');
  $('#rule-settings').innerHTML=Object.entries(ruleLabels).map(([k,[label,min,max,step,fallback]])=>`<label>${esc(label)}<input type="number" data-rule="${k}" value="${state.settings.rules[k]??fallback}" min="${min}" max="${max}" step="${step}"></label>`).join('')+'<p class="footnote">三小时稳定性按最高/最低值检查，消耗速度按实际采样时长折算。ROI与成本默认上限1.2、消耗速度1.3，均为可收紧的本地策略默认值，不是官方成本保障限制。</p>';
  const ai=state.settings.ai;$('#ai-enabled').checked=ai.enabled;$('#ai-endpoint').value=ai.endpoint;$('#ai-model').value=ai.model;
  $('#ai-key-state').textContent=state.ai_key_present?'API 密钥环境变量已设置。':'API 密钥尚未设置；外部分析不会发起。';
  $('#ai-destination').textContent=ai.endpoint?`已保存的发送地址：${ai.endpoint}`:'尚未保存发送地址。请先保存本地策略。';
}

async function official(){
  if(rulesLoaded)return;
  const data=await api('/rules.json');
  $('#official-rules').innerHTML=data.rules.map(r=>`<div class="source-rule"><h3>${esc(r.title)} <span class="badge">${esc(r.status)}</span></h3><p>${esc(r.finding)}</p><p>${esc(r.limit)}</p><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.source_label)}</a></div>`).join('');rulesLoaded=true;
}

async function audit(){const data=await api('/api/audit');$('#audit-table').innerHTML=data.rows.length?`<table><thead><tr><th>时间</th><th>事件</th><th>记录</th></tr></thead><tbody>${data.rows.map(r=>`<tr><td>${esc(date(r.created_at))}</td><td>${esc(r.event)}</td><td><details><summary>查看详情</summary><pre>${esc(JSON.stringify(r.details,null,2))}</pre></details></td></tr>`).join('')}</tbody></table>`:'<p>暂无记录。</p>';}

document.querySelectorAll('.nav').forEach(button=>button.addEventListener('click',async()=>{
  currentPage=button.dataset.page;document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!==`page-${currentPage}`);document.querySelectorAll('.nav').forEach(n=>n.classList.toggle('active',n===button));$('#breadcrumb').textContent=`示例分组A / ${button.textContent}`;window.scrollTo(0,0);
  try{if(currentPage==='strategy')await official();if(currentPage==='audit')await audit();}catch(error){notice(error.message,true);}
}));
$('#account-select').addEventListener('change',()=>{$('#review-report').innerHTML='';renderSeries();readHistory().catch(e=>notice(e.message,true));});$('#series-select').addEventListener('change',()=>{renderObserve();readHistory().catch(e=>notice(e.message,true));});
$('#recommendations').addEventListener('click',event=>{const button=event.target.closest('[data-preview]');if(!button)return;action(button,async()=>{const result=await api('/api/action-preview',{recommendation_id:button.dataset.preview});const output=button.parentElement.querySelector('.request-preview');output.hidden=false;output.textContent=JSON.stringify(result,null,2);});});
$('#migration-plan').addEventListener('click',event=>action(event.target,async()=>{
  const source=$('#migration-source').value,target=$('#migration-target').value;
  if(!source||!target||source===target)throw new Error('请选择两个不同的计划快照');
  const result=await api('/api/migration-plan',{source_snapshot_id:source,target_snapshot_id:target});
  $('#migration-result').hidden=false;$('#migration-result').textContent=JSON.stringify(result.plan,null,2);
  await load();notice(result.persisted?'已建立本地交接计划；账户未操作。':'条件未满足，交接计划未保存。');
}));
$('#budget-migrations').addEventListener('click',event=>{const button=event.target.closest('[data-cancel-migration]');if(!button)return;action(button,async()=>{await api('/api/migration-cancel',{id:button.dataset.cancelMigration});await load();notice('仅取消本地计划；未撤销或提交账户操作。');});});
$('#refresh').addEventListener('click',event=>action(event.target,async()=>{await api('/api/refresh',{});await load();notice('已重新只读检查本地数据。');}));
$('#save-settings').addEventListener('click',event=>action(event.target,async()=>{
  const rules={};document.querySelectorAll('[data-rule]').forEach(el=>{if(!el.value||!el.checkValidity())throw new Error('策略参数格式或范围不正确');rules[el.dataset.rule]=Number(el.value);});
  const accounts=[...document.querySelectorAll('[data-account]')].map(el=>{const input=el.querySelector('.target-roi');if(!input.checkValidity())throw new Error('目标ROI格式不正确');return{id:el.dataset.account,target_roi:input.value?Number(input.value):null,roi_basis:el.querySelector('.target-basis').value};});
  const ai={enabled:$('#ai-enabled').checked,endpoint:$('#ai-endpoint').value.trim(),model:$('#ai-model').value.trim()};
  await api('/api/settings',{rules,accounts,ai});await load();renderForms();notice('已保存到本助手；账户未修改。');
}));
async function demo(name){const data=await api('/api/simulate',{scenario:name});const el=$('#demo-result');el.hidden=false;el.innerHTML=`<p class="muted">合成数据回放；模拟时间 ${esc(date(data.simulation_at))}；不会写入真实监测序列、操作报告或交接计划。</p><p>${esc(data.evaluation.summary)}</p>${data.evaluation.alerts.map(alertHTML).join('')}${data.evaluation.recommendations.map(recommendationHTML).join('')||'<p>未触发建议。</p>'}${(data.evaluation.monitoring_analysis?.entities||[]).filter(o=>o.traffic?.quality).map(o=>trafficQualityHTML(o.traffic.quality)).join('')}${data.evaluation.emergency_analysis?.length?emergencyHTML(data.evaluation.emergency_analysis):''}${data.evaluation.outcome_review?.items.length?outcomeHTML(data.evaluation.outcome_review):''}${data.evaluation.live_exclusion_reviews?.length?liveExclusionHTML(data.evaluation.live_exclusion_reviews):''}${data.evaluation.product_carrying?.plans?.length?productCarryingHTML(data.evaluation.product_carrying,data.snapshots[0].account_id):''}${data.evaluation.roi_recovery_reviews?.length?roiRecoveryHTML(data.evaluation.roi_recovery_reviews):''}${data.evaluation.lifecycle_reviews?.length?lifecycleHTML(data.evaluation.lifecycle_reviews):''}${data.evaluation.maintenance_review?.length?maintenanceHTML(data.evaluation.maintenance_review):''}${data.evaluation.budget_migrations?.length?migrationHTML(data.evaluation.budget_migrations):''}${data.creative_rotation?creativeRotationHTML(data.creative_rotation):''}${data.creative_workflow?creativeWorkflowReplayHTML(data.creative_workflow):''}${data.control_creation?controlCreationHTML(data.control_creation):''}${data.creation_readback?creationReadbackHTML(data.creation_readback):''}${data.control_deadline?controlDeadlineHTML(data.control_deadline):''}${data.material_chase_creation?chaseCreationHTML(data.material_chase_creation):''}${data.smart_control?smartControlHTML(data.smart_control):''}`;}
$('#demo-quality').addEventListener('click',e=>action(e.target,()=>demo('traffic_quality')));
$('#demo-emergency').addEventListener('click',e=>action(e.target,()=>demo('emergency_stop')));
$('#demo-migration').addEventListener('click',e=>action(e.target,()=>demo('budget_migration')));
$('#demo-low').addEventListener('click',e=>action(e.target,()=>demo('low_roi')));$('#demo-growth').addEventListener('click',e=>action(e.target,()=>demo('stable_growth')));
$('#demo-outcome').addEventListener('click',e=>action(e.target,()=>demo('bid_drop_recovery')));
$('#show-token').addEventListener('click',e=>action(e.target,async()=>{const p=await api('/api/pairing');$('#pair-token').value=p.token;$('#pair-token').hidden=false;$('#copy-token').hidden=false;}));
$('#copy-token').addEventListener('click',e=>action(e.target,async()=>{await navigator.clipboard.writeText($('#pair-token').value);notice('已复制本地配对码。');}));
$('#import-file').addEventListener('change',event=>{const file=event.target.files[0];if(!file)return;action(event.target,async()=>{if(file.size>2000000)throw new Error('文件不能超过2MB');const data=JSON.parse(await file.text());const isReport=Array.isArray(data.events);const result=await api(isReport?'/api/event-import':'/api/import',data);$('#import-result').textContent=`已校验 ${result.received} 条${isReport?'人工或模拟报告':'观察快照'}，新增 ${result.added} 条；没有执行账户操作。`;await load();});});
$('#export').addEventListener('click',e=>action(e.target,async()=>{const account=$('#account-select').value;const result=await api('/api/history?'+new URLSearchParams({account_id:account}));const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({snapshots:result.snapshots,window_hours:result.window_hours},null,2)],{type:'application/json'}));a.download=`千川观察快照-${account}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}));
$('#audit-refresh').addEventListener('click',e=>action(e.target,audit));
$('#ai-preview').addEventListener('click',e=>action(e.target,async()=>{const data=await api('/api/ai-preview');$('#ai-result').hidden=false;$('#ai-result').textContent=JSON.stringify(data,null,2);}));
$('#ai-run').addEventListener('click',e=>action(e.target,async()=>{const data=await api('/api/ai-analyze',{});$('#ai-result').hidden=false;$('#ai-result').textContent=JSON.stringify(data,null,2);notice('AI 分析已通过本地复核；账户执行次数为0。');}));
load();setInterval(load,15000);
if(typeof location!=='undefined'&&location.hash==='#account-registry')document.querySelector('.nav[data-page="strategy"]').click();

$('#demo-maintenance').addEventListener('click',e=>action(e.target,()=>demo('decline_maintenance')));
$('#demo-roi').addEventListener('click',e=>action(e.target,()=>demo('roi_tuning')));
$('#demo-cold').addEventListener('click',e=>action(e.target,()=>demo('cold_start')));
$('#demo-roi-recovery').addEventListener('click',e=>action(e.target,()=>demo('roi_recovery')));
$('#demo-roi-cross-day').addEventListener('click',e=>action(e.target,()=>demo('roi_recovery_cross_day')));
$('#demo-product-carrying').addEventListener('click',e=>action(e.target,()=>demo('product_carrying')));
$('#demo-live-exclusion').addEventListener('click',e=>action(e.target,()=>demo('live_exclusion')));
$('#demo-creative-idle').addEventListener('click',e=>action(e.target,()=>demo('creative_idle')));
$('#demo-creative-rotation').addEventListener('click',e=>action(e.target,()=>demo('creative_rotation')));
$('#demo-creative-workflow').addEventListener('click',e=>action(e.target,()=>demo('creative_workflow')));
$('#demo-control-creation').addEventListener('click',e=>action(e.target,()=>demo('control_creation')));
$('#demo-smart-control').addEventListener('click',e=>action(e.target,()=>demo('smart_control')));
$('#demo-creation-readback').addEventListener('click',e=>action(e.target,()=>demo('creation_readback')));
$('#demo-control-deadline').addEventListener('click',e=>action(e.target,()=>demo('control_deadline')));
$('#demo-material-chase-creation').addEventListener('click',e=>action(e.target,()=>demo('material_chase_creation')));

let pendingOfficialImport=null,officialPreviewTicket=0;
let customReviewTicket=0,pendingCustomReview=null;
let rotationPreviewTicket=0,pendingRotationPreview=null,pendingRotationDocument=null,workflowListTicket=0;
$('#rotation-template').addEventListener('click',e=>action(e.target,async()=>{
  const account=state.settings.accounts.find(a=>a.id===$('#account-select').value)||state.settings.accounts[0];
  downloadText(JSON.stringify(creativeRotationTemplate(account),null,2),'程序化素材替换空白登记.json','application/json');
}));
$('#rotation-file').addEventListener('change',event=>{
  const ticket=++rotationPreviewTicket,file=event.target.files[0];pendingRotationPreview=null;pendingRotationDocument=null;
  $('#rotation-download').hidden=true;$('#rotation-preview').innerHTML='';if(!file)return;
  action(event.target,async()=>{
    if(file.size>2*1024*1024)throw new Error('登记文件不能超过2MB');
    const document_text=await file.text();if(ticket!==rotationPreviewTicket)return;
    const result=await api('/api/creative-rotation-preview',{document_text});if(ticket!==rotationPreviewTicket)return;
    pendingRotationPreview=result;pendingRotationDocument=document_text;$('#rotation-preview').innerHTML=creativeRotationHTML(result)+creativeWorkflowSaveHTML(result);$('#rotation-download').hidden=false;
  });
});
$('#rotation-download').addEventListener('click',e=>action(e.target,async()=>{
  if(!pendingRotationPreview)throw new Error('请先生成本次草稿');
  downloadText(JSON.stringify(pendingRotationPreview,null,2),'程序化素材替换本地草稿.json','application/json');
}));
$('#rotation-preview').addEventListener('click',event=>{
  const button=event.target.closest('[data-rotation-save]');if(!button)return;
  action(button,async()=>{
    if(!pendingRotationDocument)throw new Error('请先重新预览当前素材登记');
    await api('/api/creative-workflow-create',{document_text:pendingRotationDocument,account_id:button.dataset.account,plan_id:button.dataset.plan,intent_digest:button.dataset.rotationSave});
    await loadCreativeWorkflows();
  });
});
$('#workflows-refresh').addEventListener('click',e=>action(e.target,loadCreativeWorkflows));
$('#creative-workflows').addEventListener('click',event=>{
  const button=event.target.closest('[data-workflow-cancel]');if(!button)return;
  action(button,async()=>{await api('/api/creative-workflow-cancel',{id:button.dataset.workflowCancel});await loadCreativeWorkflows();});
});
$('#custom-report-template').addEventListener('click',e=>action(e.target,async()=>{
  const account=state.settings.accounts.find(a=>a.id===$('#account-select').value)||state.settings.accounts[0];
  downloadText(JSON.stringify(customReportTemplate(account),null,2),'千川明细核对空白模板.json','application/json');
}));
let materialReviewTicket=0,pendingMaterialReview=null;
let creationReadbackTicket=0,pendingCreationReadback=null;
$('#creation-readback-template').addEventListener('click',e=>action(e.target,async()=>{
  const account=state.settings.accounts.find(a=>a.id===$('#account-select').value)||state.settings.accounts[0];
  downloadText(JSON.stringify(creationReadbackTemplate(account),null,2),'创建后新任务核对空白模板.json','application/json');
}));
$('#creation-readback-file').addEventListener('change',event=>{
  const ticket=++creationReadbackTicket,file=event.target.files[0];pendingCreationReadback=null;
  $('#creation-readback-download').hidden=true;$('#creation-readback-result').innerHTML='';if(!file)return;
  action(event.target,async()=>{
    if(file.size>2*1024*1024)throw new Error('新任务核对文件不能超过2MB');
    const document_text=await file.text();if(ticket!==creationReadbackTicket)return;
    const result=await api('/api/control-creation-review',{document_text});if(ticket!==creationReadbackTicket)return;
    pendingCreationReadback=result;$('#creation-readback-result').innerHTML=creationReadbackHTML(result);$('#creation-readback-download').hidden=false;
  });
});
$('#creation-readback-download').addEventListener('click',e=>action(e.target,async()=>{
  if(!pendingCreationReadback)throw new Error('请先核对本次新任务');
  downloadText(JSON.stringify(pendingCreationReadback,null,2),'创建后新任务本地核对.json','application/json');
}));
$('#material-readback-template').addEventListener('click',e=>action(e.target,async()=>{
  const account=state.settings.accounts.find(a=>a.id===$('#account-select').value)||state.settings.accounts[0];
  downloadText(JSON.stringify(materialReadbackTemplate(account),null,2),'素材配置与状态核对空白模板.json','application/json');
}));
$('#material-readback-file').addEventListener('change',event=>{
  const ticket=++materialReviewTicket,file=event.target.files[0];pendingMaterialReview=null;
  $('#material-readback-download').hidden=true;$('#material-readback-result').innerHTML='';if(!file)return;
  action(event.target,async()=>{
    if(file.size>2*1024*1024)throw new Error('素材核对文件不能超过2MB');
    const document_text=await file.text();if(ticket!==materialReviewTicket)return;
    const result=await api('/api/material-readback-review',{document_text});if(ticket!==materialReviewTicket)return;
    pendingMaterialReview=result;$('#material-readback-result').innerHTML=materialReadbackHTML(result);$('#material-readback-download').hidden=false;
  });
});
$('#material-readback-download').addEventListener('click',e=>action(e.target,async()=>{
  if(!pendingMaterialReview)throw new Error('请先核对本次素材结果');
  downloadText(JSON.stringify(pendingMaterialReview,null,2),'素材配置与状态本地核对.json','application/json');
}));
$('#custom-report-file').addEventListener('change',event=>{
  const ticket=++customReviewTicket,file=event.target.files[0];pendingCustomReview=null;
  $('#custom-report-download').hidden=true;$('#custom-report-preview').innerHTML='';
  if(!file)return;
  action(event.target,async()=>{
    if(file.size>2*1024*1024)throw new Error('明细包不能超过2MB');
    const document_text=await file.text();if(ticket!==customReviewTicket)return;
    const result=await api('/api/custom-report-review',{document_text});if(ticket!==customReviewTicket)return;
    pendingCustomReview=result;$('#custom-report-preview').innerHTML=customReviewHTML(result);$('#custom-report-download').hidden=false;
  });
});
$('#custom-report-download').addEventListener('click',e=>action(e.target,async()=>{
  if(!pendingCustomReview)throw new Error('请先核对当前明细包');
  downloadText(JSON.stringify(pendingCustomReview,null,2),'千川明细核对结果.json','application/json');
}));
$('#official-report-template').addEventListener('click',e=>action(e.target,async()=>{
  const account=state.settings.accounts.find(a=>a.id===$('#account-select').value)||state.settings.accounts[0];
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(officialReportTemplate(account),null,2)],{type:'application/json'}));
  a.download=`官方账户报表空白模板-${account.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}));
$('#official-report-file').addEventListener('change',event=>{
  const ticket=++officialPreviewTicket;pendingOfficialImport=null;$('#official-report-import').disabled=true;$('#official-report-import').hidden=true;$('#official-report-preview').innerHTML='';
  const file=event.target.files[0];event.target.value='';if(!file)return;
  action(event.target,async()=>{
    if(file.size>2000000)throw new Error('文件不能超过2MB');
    const document_text=await file.text();
    const result=await api('/api/official-report-preview',{document_text});
    if(ticket!==officialPreviewTicket)return;
    pendingOfficialImport={document_text,preview_digest:result.preview_digest};
    $('#official-report-preview').innerHTML=officialPreviewHTML(result);$('#official-report-import').disabled=false;$('#official-report-import').hidden=false;
  });
});
$('#official-report-import').addEventListener('click',event=>action(event.target,async()=>{
  if(!pendingOfficialImport)throw new Error('请先选择并预览当前报告');
  const payload=pendingOfficialImport;pendingOfficialImport=null;++officialPreviewTicket;
  $('#official-report-import').hidden=true;
  const result=await api('/api/official-report-import',payload);
  $('#official-report-file').value='';$('#official-report-preview').innerHTML=`<p>新增 ${esc(result.added)} 个观察快照。原始响应和登录资料未保存，账户操作次数为0。</p>`;
  await load();notice('官方报表结果已保存到本机观察库。');
}));

let chaseCreationTicket=0,pendingChaseCreation=null;
$('#chase-creation-template').addEventListener('click',e=>action(e.target,async()=>{
  const account=state.settings.accounts.find(a=>a.id===$('#account-select').value)||state.settings.accounts[0];
  downloadText(JSON.stringify(chaseCreationTemplate(account),null,2),'素材追投创建空白登记.json','application/json');
}));
$('#chase-creation-file').addEventListener('change',event=>{
  const ticket=++chaseCreationTicket,file=event.target.files[0];pendingChaseCreation=null;$('#chase-creation-download').hidden=true;$('#chase-creation-result').innerHTML='';if(!file)return;
  action(event.target,async()=>{
    if(file.size>2*1024*1024)throw new Error('追投登记文件不能超过2MB');
    const document_text=await file.text();if(ticket!==chaseCreationTicket)return;
    const result=await api('/api/chase-creation-preview',{document_text});if(ticket!==chaseCreationTicket)return;
    pendingChaseCreation=result;$('#chase-creation-result').innerHTML=chaseCreationHTML(result);$('#chase-creation-download').hidden=false;
  });
});
$('#chase-creation-download').addEventListener('click',e=>action(e.target,async()=>{
  if(!pendingChaseCreation)throw new Error('请先预览本次追投登记');
  downloadText(JSON.stringify(pendingChaseCreation,null,2),'素材追投创建本地预览.json','application/json');
}));
