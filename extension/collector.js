/* Passive DOM observation only. No clicks, event dispatch, navigation, fetch, or storage access. */
function collectVisibleQianchuan(expected) {
  const url = new URL(location.href);
  if (url.origin !== 'https://qianchuan.jinritemai.com' || url.searchParams.get('aavid') !== expected.accountId || url.searchParams.getAll('aavid').length!==1 || url.pathname !== expected.path ||
      (expected.role==='detail'&&(url.searchParams.getAll('adId').length!==1||url.searchParams.get('adId')!==expected.planId))) {
    return {error: '页面身份已改变，跳过采集'};
  }
  const visible = el => {
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && el.getClientRects().length > 0;
  };
  const numeric = value => {
    const match = value.trim().match(/^(?:¥|￥)?\s*(\d[\d,]*(?:\.\d+)?)\s*(万|亿)?\s*(?:元|人|次|单|%)?(?:\s|$)/);
    if (!match) return null;
    let n = Number(match[1].replaceAll(',', ''));
    if (match[2] === '万') n *= 10000;
    if (match[2] === '亿') n *= 100000000;
    return Number.isFinite(n) ? n : null;
  };
  const text = document.body.innerText;
  const lines = text.split('\n').map(x => x.trim()).filter(Boolean);
  const issues = ['仅采集当前渲染内容，未覆盖分页计划、素材、退款及库存全量数据'];
  const fields = {
    spend: ['整体消耗(元)', '整体消耗（元）', '整体消耗'],
    comprehensive_cost: ['综合成本(元)', '综合成本（元）', '综合成本'],
    gmv: ['整体成交金额(元)', '整体成交金额（元）', '整体成交金额'],
    roi: ['整体支付ROI'],
    net_roi: ['净成交ROI', '全域净成交ROI'],
    net_gmv: ['净成交金额(元)', '净成交金额（元）', '净成交金额'],
    marketing_roi: ['综合营销ROI'],
    budget: ['千川日预算(元)', '千川日预算（元）'],
    balance: ['千川账户余额(元)', '千川账户余额（元）', '千川广告余额(元)', '千川广告余额（元）', '千川可用余额(元)'],
    orders: ['整体成交订单数', '整体成交订单量'],
    payment_orders:['整体成交订单数','整体成交订单量'],net_orders:['净成交订单数'],
    payment_conversion_cost:['整体成交订单成本(元)','整体成交订单成本（元）','整体成交订单成本'],
    net_conversion_cost:['净成交订单成本(元)','净成交订单成本（元）','净成交订单成本'],
    net_settlement_rate:['净成交金额结算率'],settled_roi:['7日结算ROI'],
    official_actual_paid:['用户实际支付金额(元)','用户实际支付金额（元）','用户实际支付金额'],
    official_actual_net_paid:['用户实际支付净成交金额(元)','用户实际支付净成交金额（元）','用户实际支付净成交金额'],
    refund_rate_1h: ['1小时内退款率'],
    online_users: ['当前在线人数', '实时在线人数'],
    viewers: ['累计观看人数', '直播间整体观看人数'],
    ctr: ['整体点击率'], cvr: ['整体转化率'],
    gpm:['广告GPM'],ecpm:['eCPM'],live_gpm:expected.role==='board'?['GPM(元)']:[],
    watch_to_payment_rate:expected.role==='board'?['观看成交转化率']:[],
    funnel_exposures:expected.role==='board'?['直播间整体曝光次数']:[],
    funnel_entries:expected.role==='board'?['直播间观看次数']:[],
    funnel_product_clicks:expected.role==='board'?['商品点击次数']:[],
    funnel_orders:expected.role==='board'?['成交订单数']:[],
    funnel_created_orders:expected.role==='board'?['创建订单数','下单订单数']:[],
    funnel_payments:expected.role==='board'?['支付订单数','整体支付订单数']:[]
  };
  // Only page-level leaf labels outside tables and dialogs establish an
  // overview metric. A plan drawer keeps its own identity and numeric scope.
  const labels = [...document.querySelectorAll('body *')].filter(el =>
    el.children.length === 0 && !el.closest('table,[role="table"],[role="dialog"],[aria-modal="true"],input,textarea,select') && visible(el));
  let detailPlanId=null;
  if(expected.role==='detail'){
    const ids=[...new Set(labels.map(el=>(el.innerText||'').trim().match(/^计划ID[：:]\s*(\d{6,30})$/)?.[1]).filter(Boolean))];
    if(ids.length!==1||ids[0]!==expected.planId||url.searchParams.get('adId')!==expected.planId)
      return {error:'计划详情的页面ID与读取前身份不一致，未采入'};
    detailPlanId=ids[0];
  }
  const readCards=(cardLabels,cardIssues,definitions=fields,boundary=null)=>{
  const found={};
  for (const [field, aliases] of Object.entries(definitions)) {
    const candidates = [];
    for (const el of cardLabels) {
      const label = el.innerText?.trim();
      if (!aliases.includes(label)) continue;
      let parent = el.parentElement;
      const scopes = [];
      for (let level=0;level<14&&parent;level++,parent=parent.parentElement) {
        scopes.push(parent);if(parent===boundary)break;
      }
      for (const scope of scopes) {
        if (!scope || scope.innerText.length > 500) continue;
        const parts = scope.innerText.split('\n').map(x => x.trim()).filter(Boolean);
        const i = parts.indexOf(label);
        const sample = parts.slice(i + 1, i + 4).filter(x => !['重要', '主要', '核心'].includes(x));
        // Current Qianchuan cards render integer and fractional digits in separate spans.
        let value = sample[0] || '';
        if (/^\d[\d,]*$/.test(value) && /^\.\d+$/.test(sample[1] || '')) value += sample[1];
        const n = numeric(value);
        if (n !== null) { candidates.push(n); break; }
      }
    }
    const distinct = [...new Set(candidates)];
    if (distinct.length === 1) found[field] = distinct[0];
    else if (distinct.length > 1) cardIssues.push(`${aliases[0]}出现多个不同值，未自动选择`);
  }
  return found;
  };
  const metrics=readCards(labels,issues);
  let detailPerformance=null;
  if(detailPlanId){
    const sections=[...document.querySelectorAll('.overview-data-section')].filter(section=>visible(section)&&
      [...section.querySelectorAll('h3')].filter(visible).some(h=>h.innerText.trim()==='数据概览'));
    if(sections.length===1){
      const section=sections[0],checks=[...section.querySelectorAll('input[type="checkbox"]')].filter(input=>
        visible(input)&&input.closest('label')?.innerText.trim()==='只看基础数据');
      if(checks.length===1&&typeof checks[0].checked==='boolean'){
        const localIssues=[],localLabels=[...section.querySelectorAll('*')].filter(el=>
          el.children.length===0&&!el.closest('table,[role="table"],input,textarea,select')&&visible(el));
        const cm=readCards(localLabels,localIssues,{...fields,payment_orders:fields.orders,net_orders:['净成交订单数']},section);
        delete cm.balance;delete cm.budget;
        for(const count of ['orders','payment_orders','net_orders'])if(cm[count]!=null&&!Number.isInteger(cm[count]))delete cm[count];
        if(cm.refund_rate_1h>100)delete cm.refund_rate_1h;
        const basis=cm.roi!=null&&cm.spend!=null&&cm.gmv!=null?'overall_payment':
          cm.net_roi!=null&&cm.spend!=null&&cm.net_gmv!=null?'net':
          cm.marketing_roi!=null&&cm.comprehensive_cost!=null&&cm.net_gmv!=null?'marketing':'unknown';
        delete cm.orders;
        const orders=basis==='overall_payment'?cm.payment_orders:basis==='net'||basis==='marketing'?cm.net_orders:null;
        if(orders!=null)cm.orders=orders;
        const base=checks[0].checked,observed={};
        if(base){
          const mapping={spend:'base_spend',gmv:'base_gmv',net_gmv:'base_net_gmv',comprehensive_cost:'base_comprehensive_cost',
            roi:'base_roi',net_roi:'base_net_roi',marketing_roi:'base_marketing_roi',orders:'base_orders'};
          for(const [field,to] of Object.entries(mapping))if(cm[field]!=null)observed[to]=cm[field];
        }else Object.assign(observed,cm);
        if(Object.keys(observed).length)detailPerformance={metrics:observed,basis,
          segment:base?'plan_card_base':'plan_card_overall',issues:localIssues,
          note:base?'只看基础数据已选中，卡片值仅写入base_*字段，不作为总体业绩':
            '只看基础数据未选中，卡片值为该计划总体观察，不从总体相减推算基础或调控'};
      }
    }
  }
  const capturedAt = new Date();
  const fragmentFields=new URLSearchParams(url.hash.slice(1).replace(/^\?/,''));
  const observedPlanView=url.pathname==='/overall-prom'&&fragmentFields.getAll('uniVideoTab').length===1&&
    ['1','2'].includes(fragmentFields.get('uniVideoTab'))?fragmentFields.get('uniVideoTab'):null;
  const chinaParts = new Date(capturedAt.getTime()+8*3600000).toISOString().slice(0,10);
  const rangeValue = url.searchParams.get('dr') || url.searchParams.get('dateRange') || '';
  if(expected.role==='detail'&&/^\d{4}-\d\d-\d\d,\d{4}-\d\d-\d\d$/.test(rangeValue)&&rangeValue.split(',')[0]!==rangeValue.split(',')[1])
    return {snapshots:[],diagnostics:{issues:['当前是跨天素材统计范围，由独立批量扫描读取，不作为今天主计划数据']}};
  let metricDate = rangeValue.split(',');
  let period = 'day';
  let periodStart = null;
  if (metricDate.length !== 2 || metricDate[0] !== metricDate[1] || !/^\d{4}-\d{2}-\d{2}$/.test(metricDate[0])) {
    // A static navigation label saying “今日直播” does not establish the selected date.
    const selected = [...document.querySelectorAll('[aria-selected="true"],[aria-checked="true"],input[placeholder*="日期"],[aria-label*="日期"] input')]
      .filter(el=>visible(el)&&!el.closest?.('[role="dialog"],[aria-modal="true"]'));
    const dates = selected.map(el => (el.value || el.innerText || '').trim());
    const explicitDates = [...new Set(dates.filter(x => /^\d{4}-\d{2}-\d{2}$/.test(x)))];
    metricDate = explicitDates.length === 1 ? explicitDates[0] : dates.some(x => x === '今日' || x === '今天') ? chinaParts : '';
  } else metricDate = metricDate[0];
  if (expected.role === 'board') {
    const start = text.match(/开播时间[：:]\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})/);
    if (start) {
      period = 'live_session';periodStart=`${start[1]}T${start[2]}+08:00`;metricDate=start[1];
      issues.push('直播大屏为本场累计，不能当作日累计或直接拼接账户小时序列');
    }
  }
  let dataAt = null;
  // Require an explicit page update label; never use collection time as freshness evidence.
  const update = text.match(/(?:更新于[：:\s]*)?(\d{2})-(\d{2})\s+(\d{2}):(\d{2})\s*(?:更新|已更新)/)
    || text.match(/更新于[：:\s]*(\d{2})-(\d{2})\s+(\d{2}):(\d{2})/);
  if (update) {
    let year = Number(chinaParts.slice(0,4));
    let date = new Date(`${year}-${update[1]}-${update[2]}T${update[3]}:${update[4]}:00+08:00`);
    if (date - capturedAt > 86400000) date = new Date(`${year-1}-${update[1]}-${update[2]}T${update[3]}:${update[4]}:00+08:00`);
    if (Number.isFinite(date.getTime())) dataAt = date.toISOString();
  }
  let basis = 'unknown';
  if (metrics.roi != null && metrics.spend != null && metrics.gmv != null) basis = 'overall_payment';
  else if (metrics.net_roi != null && metrics.spend != null && metrics.net_gmv != null) basis = 'net';
  else if (metrics.marketing_roi != null && metrics.comprehensive_cost != null && metrics.net_gmv != null) basis = 'marketing';
  if (!metricDate) return {error: '无法确认统计日期，请保留明确显示当日数据的页面'};
  // Plan performance cards must never be reported as account performance.
  if(detailPlanId)for(const key of Object.keys(metrics))if(!['balance','budget'].includes(key))delete metrics[key];
  if(detailPlanId)basis='unknown';
  if (!dataAt) issues.push('页面未显示可识别的数据更新时间');
  if (basis === 'unknown') issues.push('没有成套的同口径ROI、金额与消耗');
  const detailClockIssue='直播计划详情顶部时间不能认证明细独立指标更新时间';
  const snapshot = {account_id: expected.accountId, entity_id: expected.role === 'board' ? `${expected.accountId}:board` : expected.accountId,
    scope: expected.role === 'board' ? 'live' : 'account', scene: expected.scene,
    promotion_type: 'unknown', metric_basis: basis, metrics,
    source: {kind:'browser_dom',url: `${url.origin}${url.pathname}?aavid=${expected.accountId}`,
      captured_at:capturedAt.toISOString(),data_at:detailPlanId?null:dataAt,
      ...(detailPlanId&&dataAt?{reported_detail_header_data_at:dataAt}:{}),
      metric_date:metricDate,metric_period:period,period_start:periodStart,coverage:'visible_only'},
    quality:{issues},control:{history_known:false,guarantee_verified:false,lifecycle:'unknown'},
    ...(observedPlanView?{context:{observed_plan_view:observedPlanView}}:{})};
  const planSnapshots = [], planIssues = [], creativeSnapshots = [], detailSnapshots=[];
  if(detailPlanId)snapshot.quality.issues.push('计划详情顶部仅观察账户余额/日预算，不含账户业绩汇总',detailClockIssue);
  if (expected.role !== 'board' && !detailPlanId) {
    const tables = [...document.querySelectorAll('table')];
    const cleanHeader = s => s.replace(/[\s()（）]/g,'').replace(/元$/,'');
    const headersByTable=new Map(),distinctHeaders=new Map(),foreignHeaders=new Set();
    for(const table of tables){
      const heads=[...table.querySelectorAll('thead th')].map(th=>cleanHeader(th.innerText||''));
      if(!heads.length)continue;
      const named=heads.filter(Boolean);
      if(!heads.includes('计划')||!heads.includes('投放状态')||!heads.includes('整体消耗')||new Set(named).size!==named.length){
        foreignHeaders.add(table);continue;
      }
      headersByTable.set(table,heads);distinctHeaders.set(JSON.stringify(heads),heads);
    }
    const headerLists=[...distinctHeaders.values()];
    const seenPlans = new Map(), conflicts = new Set();
    const cellNumber = (text, base=false) => {
      const parts = text.split('\n').map(s=>s.trim()).filter(Boolean);
      const value = base ? parts.find(s=>/^基础\s+/.test(s))?.replace(/^基础\s+/,'') : parts[0];
      return value ? numeric(value) : null;
    };
    const columns = {
      spend:'整体消耗', comprehensive_cost:'综合成本', gmv:'整体成交金额', net_gmv:'净成交金额',
      roi:'整体支付ROI', net_roi:'净成交ROI', marketing_roi:'综合营销ROI', settled_gmv:'7日结算金额',
      payment_orders:'整体成交订单数', net_orders:'净成交订单数',refund_rate_1h:'1小时内退款率',
      payment_conversion_cost:'整体成交订单成本',net_conversion_cost:'净成交订单成本',net_settlement_rate:'净成交金额结算率',
      settled_roi:'7日结算ROI',refund_rate_pre_ship:'发货前退款率',
      official_actual_paid:'用户实际支付金额',official_actual_net_paid:'用户实际支付净成交金额',
      ctr:['整体点击率','点击率'],cvr:['整体转化率','转化率'],clicks:['点击次数','整体点击次数'],impressions:['展示次数','整体展示次数'],
      cpm:['千次展示成本','千次展现成本'],ecpm:['eCPM','ecpm'],gpm:['广告GPM','GPM']
    };
    for (const table of tables) for (const row of [...table.rows]) {
      if (!visible(row)) continue;
      const cells = [...row.cells].map(cell=>cell.innerText || '');
      if(foreignHeaders.has(table))continue;
      const own=headersByTable.get(table),candidates=own?[own]:headerLists.filter(h=>h.length===cells.length);
      if(candidates.length!==1||candidates[0].length!==cells.length)continue;
      const heads=candidates[0];
      const index = label => {const values=Array.isArray(label)?label:[label],matches=heads.map((v,i)=>values.includes(v)?i:-1).filter(i=>i>=0);return matches.length===1?matches[0]:-1;};
      const planText = cells[index('计划')] || '';
      const id = planText.match(/(?:^|\n)\s*ID[：:]\s*(\d{6,})\b/);
      if (!id) continue;
      const pm = {}, fields = {};
      for (const [key, title] of Object.entries(columns)) {
        const at = index(title);
        if (at<0) continue;
        const top=cellNumber(cells[at]);
        if (top!==null&&(!['payment_orders','net_orders','clicks','impressions'].includes(key)||Number.isInteger(top))&&
            (!['ctr','cvr','refund_rate_1h','refund_rate_pre_ship','net_settlement_rate'].includes(key)||top<=100)) pm[key]=top;
        fields[key]=title;
        const baseKey={spend:'base_spend',comprehensive_cost:'base_comprehensive_cost',gmv:'base_gmv',net_gmv:'base_net_gmv',roi:'base_roi',net_roi:'base_net_roi',marketing_roi:'base_marketing_roi'}[key];
        if (baseKey) { const b=cellNumber(cells[at],true);if(b!==null)pm[baseKey]=b; }
      }
      let planBasis='unknown';
      if (pm.roi!=null&&pm.spend!=null&&pm.gmv!=null)planBasis='overall_payment';
      else if(pm.net_roi!=null&&pm.spend!=null&&pm.net_gmv!=null)planBasis='net';
      else if(pm.marketing_roi!=null&&pm.comprehensive_cost!=null&&pm.net_gmv!=null)planBasis='marketing';
      if(planBasis==='unknown'){
        // A missing cell value does not erase an unambiguous column definition.
        // Keep the ROI value absent; never turn the displayed dash into zero.
        const declared=[['overall_payment','整体支付ROI'],['net','净成交ROI'],['marketing','综合营销ROI']]
          .filter(([,title])=>index(title)>=0);
        if(declared.length===1)planBasis=declared[0][0];
      }
      const costTitle=planBasis==='marketing'?'综合订单成本':'整体成交订单成本';
      if(index(costTitle)>=0){const cost=cellNumber(cells[index(costTitle)]);if(cost!==null)pm.conversion_cost=cost;}
      const orderTitle=planBasis==='overall_payment'?'整体成交订单数':planBasis==='net'||planBasis==='marketing'?'净成交订单数':null;
      if(orderTitle&&index(orderTitle)>=0){
        const cell=cells[index(orderTitle)],n=cellNumber(cell),b=cellNumber(cell,true);
        if(n!==null)pm.orders=n;if(b!==null)pm.base_orders=b;
      }
      const settingsText=cells[index('投放设置')] || '';
      const budget=settingsText.match(/每日预算\s*([\d,.]+)\s*元/);
      if(budget)pm.budget=Number(budget[1].replaceAll(',',''));
      const goals=new Map();
      for(const [goalBasis,label] of [['overall_payment','整体支付ROI目标'],['net','净成交ROI目标'],['marketing','综合营销ROI目标']]){
        for(const match of settingsText.matchAll(new RegExp(label+'\\s*(\\d+(?:\\.\\d+)?)(?=\\s|$)','g'))){
          const value=Number(match[1]);if(Number.isFinite(value))goals.set(goalBasis+':'+value,{basis:goalBasis,value});
        }
      }
      const observedGoal=goals.size===1?[...goals.values()][0]:null;
      if(observedGoal){
        pm.target_roi=observedGoal.value;
        if(observedGoal.basis==='net')pm.net_roi_goal=observedGoal.value;
        else if(observedGoal.basis==='overall_payment')pm.payment_roi_goal=observedGoal.value;
      }
      const status=cells[index('投放状态')] || '';
      const running=/^投放中(?:\n|$)/.test(status)?true:/^(?:已暂停|已结束|未投放)(?:\n|$)/.test(status)?false:null;
      const type=cells[index('投放类型')] || '';
      const promotion=/乘方/.test(type)?'chengfang':/全域/.test(type)?'universal':/标准/.test(type)?'standard':/搜索/.test(type)?'search':'unknown';
      const goalIndex=index('营销目标'),goalText=goalIndex>=0?cells[goalIndex].trim():'';
      const planGoal=goalText==='推直播间'?'LIVE_PROM_GOODS':goalText==='推商品'?'VIDEO_PROM_GOODS':'unknown';
      const lines=planText.split('\n').map(s=>s.trim()).filter(Boolean),nameAt=lines.findIndex(s=>/^ID[：:]/.test(s));
      const planName=nameAt>0?lines[nameAt-1].slice(0,150):id[1];
      const ps={...snapshot,entity_id:id[1],scope:'plan',display_name:planName,promotion_type:promotion,
        metric_basis:planBasis,metrics:pm,source:{...snapshot.source,data_at:null},
        quality:{issues:['仅当前渲染计划行，未翻页且未核验全量及操作历史','基础与总体台账分别保存，未核验范围前不通过相减推算调控台账','计划行没有独立指标更新时间，账户概览时间不能代替计划时间']},
        control:{history_known:false,guarantee_verified:false,lifecycle:'unknown',running},context:{plan_id:id[1],plan_marketing_goal:planGoal,
          ...(observedPlanView?{observed_plan_view:observedPlanView}:{}),
          ...(running!==null?{plan_status_at:capturedAt.toISOString()}:{}),plan_delivery_state:'unknown',
          ...(observedGoal?{plan_roi_basis:observedGoal.basis,plan_roi_mode:'roi_target',plan_roi_observed_at:capturedAt.toISOString()}:{}),
          ...(pm.budget!=null?{plan_budget_period:'day'}:{}),plan_roi_config_verified:null,plan_budget_period_verified:null}};
      const fingerprint=JSON.stringify({metrics:pm,basis:planBasis,type:promotion,status:running});
      if(seenPlans.has(id[1])){if(seenPlans.get(id[1]).fingerprint!==fingerprint)conflicts.add(id[1]);}
      else seenPlans.set(id[1],{snapshot:ps,fingerprint});
    }
    for(const [id,entry] of seenPlans)if(!conflicts.has(id))planSnapshots.push(entry.snapshot);
    if(conflicts.size)planIssues.push(`${conflicts.size}条计划在重复表格中出现冲突，未采入`);
  }
  // Read only explicitly scoped detail tables. Dates and parent IDs belong to
  // each view; a drawer cannot inherit the account overview's range or clock.
  const detailNumber=(value,count=false,percent=false)=>{
    value=(value||'').trim();
    if(!/^(?:¥|￥)?\s*\d[\d,]*(?:\.\d+)?\s*(?:元|次|单|%)?$/.test(value))return null;
    const n=numeric(value);
    return n===null||(count&&!Number.isInteger(n))||(percent&&n>100)?null:n;
  };
  const dateTime=value=>{
    value=(value||'').trim().replace(/\s+/g,'T');
    if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(value))return null;
    const d=new Date(value+'+08:00');
    return Number.isFinite(d.getTime())&&new Date(d.getTime()+8*3600000).toISOString().slice(0,19)===value?value+'+08:00':null;
  };
  const readRows=(root,title)=>{
    const tables=[...root.querySelectorAll('table')].filter(visible),byTable=new Map(),unique=new Map();
    for(const table of tables){
      const candidates=[...table.querySelectorAll('thead tr')].map(r=>[...r.cells].map(c=>c.innerText.replace(/[\s()（）]/g,'').replace(/元$/,'')))
        .filter(h=>h.includes(title));
      for(const heads of candidates){
        const named=heads.filter(Boolean);if(new Set(named).size!==named.length)continue;
        byTable.set(table,heads);unique.set(JSON.stringify(heads),heads);
      }
    }
    const result=[];
    for(const table of tables){
      const own=byTable.get(table),heads=own||(unique.size===1?[...unique.values()][0]:null);
      if(!heads)continue;
      for(const row of [...table.rows]){
        if(!visible(row)||row.cells.length!==heads.length)continue;
        const cells=[...row.cells].map(c=>c.innerText||'');
        result.push({cell:label=>{const index=heads.indexOf(label);return index<0?'':cells[index];}});
      }
    }
    return result;
  };
  const addRows=(rows,kind,parentId,source)=>{
    const seen=new Map(),conflicts=new Set();
    for(const {cell} of rows){
      const nameText=cell(kind==='control'?'任务名称':'商品');
      const ids=[...nameText.matchAll(/(?:^|\n)\s*ID[：:]\s*(\d{6,30})\s*(?=\n|$)/g)];
      if(ids.length!==1)continue;
      const id=ids[0][1],cm={};
      const columns=kind==='control'?{
        spend:'调控消耗',gmv:'调控成交金额',roi:'调控支付ROI',net_gmv:'调控净成交金额',net_roi:'调控净成交ROI',
        impressions:'调控展示次数',clicks:'调控点击次数',ctr:'调控点击率',cvr:'调控转化率',payment_orders:'调控成交订单数',
        payment_conversion_cost:'调控成交订单成本',refund_rate_1h:'调控1小时内退款率',budget:'调控预算',bid:'出价',
        net_roi_goal:'净成交ROI目标',payment_roi_goal:'支付ROI目标'
      }:{spend:'整体消耗',comprehensive_cost:'综合成本',marketing_roi:'综合营销ROI',net_gmv:'净成交金额',
        net_orders:'净成交订单数',gmv:'整体成交金额',conversion_cost:'综合订单成本',refund_rate_1h:'1小时内退款率'};
      for(const [field,label] of Object.entries(columns)){
        const n=detailNumber(cell(label),['impressions','clicks','payment_orders','net_orders'].includes(field),['ctr','cvr','refund_rate_1h'].includes(field));
        if(n!==null)cm[field]=n;
      }
      if(!Object.keys(cm).length)continue;
      const context={parent_plan_id:parentId,...(kind==='product'?{product_id:id,product_plan_mapping_verified:null,product_metrics_verified:null}:{control_type:'unknown'})};
      let running=null;
      if(kind==='control'){
        const state=cell('调控状态').split('\n').map(s=>s.trim()).filter(Boolean)[0];
        running=state==='调控中'?true:['调控暂停','调控结束'].includes(state)?false:null;
        context.control_delivery_mode={'控成本追投':'cost_control','放量追投':'volume'}[cell('追投方式').trim()]||'unknown';
        const created=dateTime(cell('创建时间'));if(created)context.created_at=created;
        const duration=cell('调控时长小时').trim();
        context.observed_duration_mode=duration==='长期投放'?'unlimited':'unknown';
        const hours=detailNumber(duration);
        if(hours!==null&&hours>0){context.observed_duration_mode='fixed';context.observed_duration_hours=hours;}
        const material=cell('素材').match(/(?:^|\n)\s*素材ID[：:]\s*(\d{6,30})\s*(?:\n|$)/);
        if(material)context.material_id=material[1];
        if(cm.payment_orders!=null)cm.orders=cm.payment_orders;
      }else if(cm.net_orders!=null)cm.orders=cm.net_orders;
      const nameLines=nameText.split('\n').map(s=>s.trim()).filter(Boolean);
      const child={...snapshot,entity_id:id,scope:kind,display_name:(nameLines[0]||id).slice(0,150),metrics:cm,context,
        metric_basis:kind==='control'?(cm.roi!=null&&cm.spend!=null&&cm.gmv!=null?'control_payment':'unknown'):
          (cm.marketing_roi!=null&&cm.comprehensive_cost!=null&&cm.net_gmv!=null?'marketing':'unknown'),
        source:{...source,parent_entity_id:parentId},
        quality:{issues:['仅当前渲染明细，受筛选和分页限制，不认证完整清单或配置历史',
          kind==='control'?'任务支付ROI、净成交ROI和两种目标分别保存；不回填账户目标或认证原生任务状态':'商品明细仅属于当前计划；没有库存或独立更新时间时保持缺失',
          ...(kind==='control'?[detailClockIssue]:[]),
          ...(!source.data_at?['页面未显示明细独立数据更新时间']:[])]},
        control:{history_known:false,guarantee_verified:false,lifecycle:'unknown',running}};
      const fingerprint=JSON.stringify(child);
      if(seen.has(id)){if(seen.get(id).fingerprint!==fingerprint)conflicts.add(id);}
      else seen.set(id,{snapshot:child,fingerprint});
    }
    for(const [id,entry] of seen)if(!conflicts.has(id))detailSnapshots.push(entry.snapshot);
    if(conflicts.size)planIssues.push(`${conflicts.size}条${kind==='control'?'调控任务':'商品'}明细出现重复冲突，未采入`);
  };
  if(detailPlanId){
    const header=text.split('任务名称')[0],pm={...(detailPerformance?.metrics||{})},context={plan_id:detailPlanId};
    // Only the three native log columns below are inspected. Names, IPs and
    // arbitrary log content never enter the observation payload.
    const logRows=new Map(),logConflicts=new Set(),logHeaders=new Map(),logDefinitions=new Map(),foreignLogTables=new Set();
    const logTables=[...document.querySelectorAll('table')].filter(visible);
    for(const table of logTables){
      const headerRows=[...table.querySelectorAll('thead tr')];
      const allHeads=headerRows.map(row=>[...row.cells].map(cell=>(cell.innerText||'').replace(/\s/g,'')));
      const definitions=allHeads
        .filter(heads=>['操作时间','操作类型','操作内容'].every(label=>heads.includes(label)));
      const unique=new Map(definitions.map(heads=>[JSON.stringify(heads),heads]));
      if(unique.size!==1){if(allHeads.some(heads=>heads.some(Boolean)))foreignLogTables.add(table);continue;}
      const heads=[...unique.values()][0],named=heads.filter(Boolean);
      if(new Set(named).size!==named.length){foreignLogTables.add(table);continue;}
      logHeaders.set(table,heads);logDefinitions.set(JSON.stringify(heads),heads);
    }
    for(const table of logTables){
      if(foreignLogTables.has(table))continue;
      const heads=logHeaders.get(table)||(logDefinitions.size===1?[...logDefinitions.values()][0]:null);
      if(!heads)continue;
      for(const row of [...table.rows]){
        if(!visible(row)||row.cells.length!==heads.length)continue;
        const cell=label=>(row.cells[heads.indexOf(label)].innerText||'').trim();
        if(cell('操作类型')!=='修改')continue;
        const occurred=dateTime(cell('操作时间'));
        if(!occurred||new Date(occurred)>capturedAt)continue;
        const content=cell('操作内容').replace(/\r/g,'');
        const match=content.match(/^操作内容[：:]\s*素材追投[，,]\s*ID[：:]\s*(\d{6,30})\s*\n\s*调控预算[：:]\s*([\d,]+(?:\.\d+)?)元\s*->\s*([\d,]+(?:\.\d+)?)元$/);
        if(!match)continue;
        if([match[2],match[3]].some(value=>!/^(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$/.test(value)))continue;
        const before=detailNumber(match[2]),after=detailNumber(match[3]);
        if(before===null||after===null||before>1e15||after>1e15)continue;
        const operation={occurred_at:occurred,kind:'control_budget_change',control_id:match[1],before_budget:before,after_budget:after};
        const key=occurred+':'+match[1],signature=JSON.stringify(operation);
        if(logRows.has(key)&&logRows.get(key).signature!==signature)logConflicts.add(key);
        else logRows.set(key,{operation,signature});
      }
    }
    const operations=[...logRows].filter(([key])=>!logConflicts.has(key)).map(([,entry])=>entry.operation)
      .sort((a,b)=>b.occurred_at.localeCompare(a.occurred_at)||a.control_id.localeCompare(b.control_id)).slice(0,200);
    if(operations.length)planSnapshots.push({...snapshot,entity_id:detailPlanId,scope:'plan',display_name:detailPlanId,
      metrics:{},metric_basis:'unknown',context:{plan_id:detailPlanId,observed_plan_operations:operations},
      source:{...snapshot.source,data_at:null,segment:'plan_operation_log'},
      quality:{issues:['仅已显示的素材追投预算修改日志，不认证完整历史、当前计划配置或执行结果',detailClockIssue]},
      control:{history_known:false,guarantee_verified:false,lifecycle:'unknown',running:null,last_change_at:operations[0].occurred_at}});
    if(logConflicts.size)planIssues.push('可见操作日志前后值冲突，未采入冲突行');
    const budget=header.match(/预算[（(]元[）)][：:]\s*每日\s*([\d,.]+)/);
    const goal=header.match(/净成交ROI目标[：:]\s*([\d.]+)/);
    if(budget){const n=detailNumber(budget[1]);if(n!==null){pm.budget=n;context.plan_budget_period='day';context.plan_budget_period_verified=null;}}
    if(goal){const n=detailNumber(goal[1]);if(n!==null){pm.net_roi_goal=n;Object.assign(context,{plan_roi_basis:'net',plan_roi_mode:'roi_target',plan_roi_observed_at:capturedAt.toISOString(),plan_roi_config_verified:null});}}
    const aweme=[...new Set(labels.map(el=>(el.innerText||'').trim().match(/^抖音号ID[：:]\s*(\d{6,30})$/)?.[1]).filter(Boolean))];
    if(aweme.length===1)context.aweme_id=aweme[0];
    if(Object.keys(pm).length)planSnapshots.push({...snapshot,entity_id:detailPlanId,scope:'plan',display_name:detailPlanId,
      metrics:pm,metric_basis:detailPerformance?.basis||'unknown',context,
      source:{...snapshot.source,...(detailPerformance?{segment:detailPerformance.segment}:{})},
      quality:{issues:['仅计划详情可见观察，净成交目标不作为支付考核线，不认证配置历史或全量范围',detailClockIssue,
        ...(detailPerformance?[detailPerformance.note,...detailPerformance.issues]:['数据概览卡片或基础视图开关未明确，仅保存可见配置'])]},
      control:{history_known:false,guarantee_verified:false,lifecycle:'unknown',running:null}});
    addRows(readRows(document.body,'任务名称'),'control',detailPlanId,snapshot.source);
  }
  if(url.pathname==='/overall-prom')for(const dialog of [...document.querySelectorAll('[role="dialog"]')].filter(d=>typeof d.querySelectorAll==='function'&&visible(d))){
    const ids=[...new Set([...dialog.querySelectorAll('*')].filter(e=>e.children.length===0&&!e.closest('table')&&visible(e))
      .map(e=>(e.innerText||'').trim().match(/^ID[：:]\s*(\d{6,30})$/)?.[1]).filter(Boolean))];
    const inputs=[...dialog.querySelectorAll('input')].filter(visible);
    const starts=[...new Set(inputs.filter(e=>/开始日期/.test(e.getAttribute('placeholder')||'')).map(e=>e.value.trim()))];
    const ends=[...new Set(inputs.filter(e=>/结束日期/.test(e.getAttribute('placeholder')||'')).map(e=>e.value.trim()))];
    if(ids.length!==1||starts.length!==1||ends.length!==1||starts[0]!==ends[0]||!dateTime(starts[0]+' 00:00:00')){
      planIssues.push('商品弹层的计划ID或独立统计日期不明确，未采入商品行');continue;
    }
    addRows(readRows(dialog,'商品'),'product',ids[0],{...snapshot.source,metric_date:starts[0],metric_period:'day',period_start:null,data_at:null});
  }
  if (expected.role === 'board' && url.pathname === '/board-next') {
    const cleanHeader = s => s.replace(/[\s()（）]/g,'').replace(/元$/,'');
    const tables = [...document.querySelectorAll('table')].map(table => ({table,
      heads:[...table.querySelectorAll('thead th')].map(th=>cleanHeader(th.innerText||''))}))
      .filter(({heads})=>heads.includes('素材名称/ID')&&heads.includes('整体消耗'));
    if (tables.length) {
      const selection = label => {
        const values = new Set();
        for (const leaf of labels.filter(el=>el.innerText?.trim()===label)) {
          let parent=leaf.parentElement;
          for(let level=0;level<6&&parent;level++,parent=parent.parentElement){
            if(parent.innerText.length>180)break;
            const inputs=[...parent.querySelectorAll('input')].filter(visible);
            if(inputs.length===1){values.add(inputs[0].value.trim());break;}
          }
        }
        return values.size===1?[...values][0]:null;
      };
      const materialPeriod=selection('数据周期:');
      const delivery=selection('投放类型:');
      const segment={'控成本投放':'material_cost_control','放量投放':'material_volume','全部':'material_all'}[delivery];
      if(materialPeriod!=='整场直播'||period!=='live_session'||!periodStart)
        planIssues.push('素材表统计周期未确认是整场直播，未采入素材行');
      else if(!segment)planIssues.push('素材表投放类型筛选不明确，未采入素材行');
      else {
        const seen=new Map(),conflicts=new Set();
        const columns={spend:'整体消耗',gmv:'整体成交金额',roi:'整体支付ROI',
          payment_orders:'整体成交订单数',impressions:'整体展现次数',clicks:'整体点击次数'};
        for(const {table,heads} of tables){
          // Never use a similarly sized table's headings for a material row.
          if(new Set(heads).size!==heads.length)continue;
          for(const row of [...table.rows]){
            if(!visible(row))continue;
            const cells=[...row.cells].map(cell=>cell.innerText||'');
            if(cells.length!==heads.length)continue;
            const at=title=>heads.indexOf(title),nameText=cells[at('素材名称/ID')];
            // An aweme ID on the live-room picture is not a material ID.
            const id=nameText.match(/(?:^|\n)\s*ID[：:]\s*(\d{6,})\s*(?:\n|$)/);
            if(!id)continue;
            const cm={};
            for(const [field,title] of Object.entries(columns)){
              const index=at(title);if(index<0)continue;
              const value=cells[index].trim();
              if(!/^(?:¥|￥)?\s*\d[\d,]*(?:\.\d+)?\s*(?:元|次|单)?$/.test(value))continue;
              const n=numeric(value);
              if(n!==null&&(!['payment_orders','impressions','clicks'].includes(field)||Number.isInteger(n)))cm[field]=n;
            }
            if(!Object.keys(cm).length)continue;
            if(cm.payment_orders!=null)cm.orders=cm.payment_orders;
            const lines=nameText.split('\n').map(s=>s.trim()).filter(Boolean),idAt=lines.findIndex(s=>/^ID[：:]/.test(s));
            const status=at('投放状态')>=0?cells[at('投放状态')].trim():'';
            const running=status==='投放中'?true:['已暂停','已结束','未投放'].includes(status)?false:null;
            const context={material_id:id[1],creative_type:'unknown',creative_parent_verified:null,
              content_format:lines.includes('自选视频')?'short_video':lines.includes('图文')?'image_text':'unknown',
              spend_ledger:'overall'};
            const liveRoom=url.searchParams.get('live_room_id');
            if(liveRoom&&/^\d+$/.test(liveRoom))context.live_room_id=liveRoom;
            const created=at('创建时间')>=0?cells[at('创建时间')].trim():'';
            if(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(created)){
              const value=new Date(created.replace(' ','T')+'+08:00');
              if(Number.isFinite(value.getTime())&&new Date(value.getTime()+8*3600000).toISOString().slice(0,19)===created.replace(' ','T'))
                context.created_at=created.replace(' ','T')+'+08:00';
            }
            const creative={...snapshot,entity_id:id[1],scope:'creative',display_name:(idAt>0?lines[idAt-1]:id[1]).slice(0,150),
              metric_basis:cm.roi!=null&&cm.spend!=null&&cm.gmv!=null?'overall_payment':'unknown',metrics:cm,
              source:{...snapshot.source,segment},context,
              quality:{issues:['仅当前渲染素材行，未翻页，不代表全量素材或追投配额',
                `素材表筛选：${delivery}；统计周期：整场直播；素材类型和投放状态仍受页面筛选限制`,
                '总体数据不替代基础/追投/起量独立台账；未确认母计划关系或素材用途资格',
                ...(!dataAt?['页面未显示可识别的数据更新时间']:[])]},
              control:{history_known:false,guarantee_verified:false,lifecycle:'unknown',running}};
            const fingerprint=JSON.stringify(creative);
            if(seen.has(id[1])){if(seen.get(id[1]).fingerprint!==fingerprint)conflicts.add(id[1]);}
            else seen.set(id[1],{snapshot:creative,fingerprint});
          }
        }
        for(const [id,entry] of seen)if(!conflicts.has(id))creativeSnapshots.push(entry.snapshot);
        if(conflicts.size)planIssues.push(`${conflicts.size}条素材在重复表格中出现冲突，未采入`);
      }
    }
  }
  // Some observation bridges replace repeated object references with a
  // circular-reference marker. Return independent JSON values for each view.
  const snapshots=[snapshot,...planSnapshots,...creativeSnapshots,...detailSnapshots].filter(s=>Object.keys(s.metrics).length||
    (s.source.segment==='plan_operation_log'&&s.context.observed_plan_operations?.length));
  if(!snapshots.length)return {error:'当前可见区域没有可校验的指标；未把空值写成0'};
  return {snapshot:snapshots[0],snapshots:JSON.parse(JSON.stringify(snapshots)),diagnostics:{visible_plans:new Set(planSnapshots.map(s=>s.entity_id)).size,visible_plan_operations:planSnapshots.reduce((n,s)=>n+(s.context.observed_plan_operations?.length||0),0),visible_creatives:creativeSnapshots.length,visible_controls:detailSnapshots.filter(s=>s.scope==='control').length,visible_products:detailSnapshots.filter(s=>s.scope==='product').length,issues:planIssues,coverage:'visible_only'}};
}
