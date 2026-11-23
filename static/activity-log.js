'use strict';
(function (global) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const items = value => Array.isArray(value) ? value : [];
  const categories = {
    budget: '预算调整', pause: '暂停投放', duration: '时长调整', roi: 'ROI目标调整',
    chase: '新建素材追投', boost: '一键起量', slowdown: '下播控量',
  };
  const actionCategories = {
    task_budget:'budget', plan_budget:'budget', budget_up:'budget', budget_down:'budget', control_budget_up:'budget',
    pause_task:'pause', pause_plan:'pause', control_pause:'pause', control_stop:'pause',
    task_duration:'duration', control_duration_up:'duration', plan_roi:'roi', roi_target_up:'roi', roi_target_down:'roi',
    material_chase:'chase', auto_chase:'chase', boost:'boost', slowdown_schedule:'slowdown',
  };
  const stateNames = {
    verified:'已核验', draft:'待确认', approved:'已确认待执行', checking:'执行前核验中', claimed:'执行前核验中',
    submitting:'提交中', unknown:'结果待确认', submitted_pending_verification:'已提交待核验',
    cancelled:'已取消', rejected:'未完成', expired:'确认已过期', blocked:'条件未通过', missed:'已错过排程',
    scheduled:'已确认待排程', active_acknowledged:'官方已受理控量', completed_acknowledged:'官方已受理停止控量',
    reconciled_target:'当前值与目标一致', reconciled_before:'当前值与原值一致', reconciled_other:'当前值已回读',
  };

  function clock(value) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return '时间未记录';
    const parts = Object.fromEntries(new Intl.DateTimeFormat('zh-CN', {
      timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit',
      hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23',
    }).formatToParts(date).map(p => [p.type,p.value]));
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
  }

  function money(value) {
    if (value === null || value === undefined || value === '' || typeof value === 'boolean') return '未记录';
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(2)}元` : '未记录';
  }

  function numeric(value, suffix='') {
    if (value === null || value === undefined || value === '' || typeof value === 'boolean' || !Number.isFinite(Number(value))) return '未记录';
    return `${Number(value)}${suffix}`;
  }

  function describe(record) {
    const intent = record.intent || {}, result = record.result || {};
    const action = intent.action, category = actionCategories[action] || 'other';
    const verified = record.state === 'verified';
    const readback = result.phase === 'timeout_readback' || String(record.state).startsWith('reconciled_');
    const pending = ['draft','approved','claimed','checking','submitting','unknown','submitted_pending_verification','scheduled'].includes(record.state);
    const acknowledged = ['active_acknowledged','completed_acknowledged'].includes(record.state);
    const group = verified ? 'verified' : acknowledged ? 'acknowledged' : pending ? 'pending' : 'failed';
    const object = intent.task_scene === 'SMART_BOOST' || action === 'boost' ? '一键起量'
      : intent.task_id || ['material_chase','auto_chase'].includes(action) ? '素材追投' : '主计划';
    const taskId = intent.task_id ?? result.task_id ?? result.reply?.id;
    const lines = [];
    const prefix = verified && !readback ? '' : '申请';
    if (category === 'budget') lines.push(`${prefix}${intent.task_id ? '调控预算' : '主计划日预算'} ${money(intent.before)} → ${money(intent.after)}`);
    else if (category === 'pause') lines.push(`${prefix}投放状态 ${['PROCESSING','ENABLE',true].includes(intent.before) ? '投放中' : String(intent.before ?? '未记录')} → ${action==='control_stop' ? '已结束' : '已暂停'}`);
    else if (category === 'duration') {
      const divisor = action === 'control_duration_up' ? 60 : 1;
      lines.push(`${prefix}调控总时长 ${numeric(intent.before == null ? null : Number(intent.before)/divisor,'小时')} → ${numeric(intent.after == null ? null : Number(intent.after)/divisor,'小时')}`);
    } else if (category === 'roi') lines.push(`${prefix}主计划ROI目标 ${numeric(intent.before)} → ${numeric(intent.after)}`);
    else if (category === 'chase' || category === 'boost') {
      const after = intent.after || {}, body = intent.body || {};
      const total = after.total_budget ?? (category === 'boost' ? after.budget : null);
      const budget = after.daily_budget ?? total ?? body.budget;
      lines.push(`${prefix}新建${object}，${total != null ? '整批总预算' : '整批日预算'} ${money(budget)}`);
      if (category==='chase') lines.push(`追投方式 ${total != null ? '放量追投' : '控成本追投'}`);
      const goal = after.optimization_target ?? intent.native_contract?.goal_label;
      if (goal) lines.push(`优化目标 ${goal}`);
      if (after.bid != null) lines.push(`成交出价 ${money(after.bid)}`);
      if (after.roi2_goal != null) lines.push(`ROI目标 ${numeric(after.roi2_goal)}`);
      if (after.duration != null) lines.push(`调控时长 ${numeric(after.duration,'小时')}`);
      const audience=intent.browser_batch?.after?.audience;
      if(audience)lines.push(`定向：性别 ${{any:'不限',male:'男',female:'女'}[audience.gender]||'未记录'}；年龄 ${items(audience.ages).length?audience.ages.join('、'):'不限'}；抖音号粉丝 ${audience.exclude_fans?'排除':'不限'}`);
      if (intent.auto_stop_hours != null) lines.push(`计划在创建后${numeric(intent.auto_stop_hours,'小时')}暂停`);
      if (items(intent.material_ids).length) lines.push(`素材ID ${intent.material_ids.map(String).join('、')}`);
      if (body.name) lines.push(`任务名称 ${body.name}`);
    } else if (category === 'slowdown') {
      const after = intent.after || {};
      lines.push(`下播前20分钟控量，总预算 ${money(after.budget)}，持续${numeric(after.duration_minutes,'分钟')}`);
      if (after.trigger_at) lines.push(`控量开始 ${clock(after.trigger_at)}`);
      if (after.off_at) lines.push(`下播时间 ${clock(after.off_at)}`);
      if (after.stop_at) lines.push(`控量结束 ${clock(after.stop_at)}`);
    } else lines.push(intent.action_label || '调控请求');
    let status = stateNames[record.state] || '状态待核对';
    if (verified && readback) status = '当前值已核验';
    const feature = intent.rule_check?.feature_id || (intent.auto_authority ? 'f5' : null);
    return {
      id:String(record.id), at:record.created_at, updatedAt:record.updated_at,
      category, type:['chase','boost'].includes(category) ? '新建' : category === 'slowdown' ? '排程' : '修改',
      object, taskId:taskId == null ? '' : String(taskId), planId:String(intent.plan_id ?? ''),
      planName:String(intent.plan_name ?? ''), account:String(intent.account_name || intent.account_id || ''),
      lines, status, group, message:result.message || record.receipt?.message || '',
      reason:feature ? `触发 ${String(feature).toUpperCase()}，${intent.reason || '按本户保存规则执行'}` : intent.reason || '',
      followups:items(record.followups).map(f => ({
        at:f.due_at, taskId:f.task_id,
        status:f.state==='verified' ? '已核对任务停止' : f.state==='scheduled' ? '等待到点暂停' : stateNames[f.state] || '状态待核对',
        message:f.result?.message || '',
      })),
      record,
    };
  }

  function rowHTML(row, open=false) {
    const details = `${row.id}:${row.updatedAt || ''}`;
    return `<tr><td class="activity-time">${escape(clock(row.at))}</td><td>${escape(row.type)}</td>
      <td class="activity-content"><strong>${escape(row.object)}${row.taskId ? ' · ID '+escape(row.taskId) : ''}</strong>
      ${row.lines.map(line => `<div>${escape(line)}</div>`).join('')}
      <small>母计划 ${escape(row.planId)}${row.planName ? ' · '+escape(row.planName) : ''}</small>
      ${row.reason ? `<small>${escape(row.reason)}</small>` : ''}<small>执行通道 ${row.record.intent?.execution_channel==='browser'?'Chrome网页补位':row.record.backend==='official_api'?'官方API':'浏览器'}</small></td>
      <td class="activity-result"><span class="activity-badge ${row.group}">${escape(row.status)}</span>
      ${row.message ? `<p>${escape(row.message)}</p>` : ''}
      ${row.followups.map(f => `<p>定时暂停 · ${escape(f.status)}<small>约定时点 ${escape(clock(f.at))} · 任务 ${escape(f.taskId)}</small>${escape(f.message)}</p>`).join('')}
      ${row.updatedAt && row.updatedAt !== row.at ? `<small>结果更新 ${escape(clock(row.updatedAt))}</small>` : ''}
      <details data-activity-detail="${escape(details)}" ${open ? 'open' : ''}><summary>原始详情</summary><pre>${escape(JSON.stringify(row.record,null,2))}</pre></details></td></tr>`;
  }

  const eventNames = {
    rule_auto_scan:'自动规则巡查', auto_execution:'自动追投巡查', scan_finished:'巡查完成',
    scan_deferred:'等待重试', scan_error:'读取失败', profile_saved:'保存规则', automatic_rules_updated:'更新自动规则',
    lane_started:'启动巡航', lane_stopped:'停止巡航', auto_enabled:'开启自动追投', auto_disabled:'关闭自动追投',
    rule_auto_authorized:'自动规则授权', rule_auto_disabled:'关闭自动规则', rule_auto_recovered:'恢复自动规则',
    account_entry_requested:'请求进入账户', live_home_requested:'请求读取直播计划',
    hybrid_requested:'请求网页补位', hybrid_mode_changed:'修改追投通道', hybrid_verified:'网页追投已核验',
    hybrid_retried:'重试网页阶段',
    hybrid_material_skipped:'跳过单条素材并补足批次',
    hybrid_rules_started:'网页规则检查开始', hybrid_rules_finished:'网页规则检查完成', hybrid_rule_verified:'网页调控已核验',
    hybrid_rule_unsubmitted_released:'结束失败草稿，释放账户',
    api_task_held:'人工跳过任务', api_task_resumed:'恢复任务调控',
    api_action_resolved:'结束明确失败原单', api_action_cancelled:'取消未提交单',
  };

  function runtimeHTML(logs, openIds=new Set()) {
    if (!logs.length) return '<p class="empty">本户尚无巡航运行记录。</p>';
    return `<table class="activity-runtime"><thead><tr><th>时间</th><th>运行事件</th><th>运行说明</th></tr></thead><tbody>${logs.map(log => {
      const detail = log.detail && typeof log.detail === 'object' ? log.detail : {};
      let message = typeof log.detail==='string' ? log.detail : detail.message || detail.summary;
      if (!message) message = ({profile_saved:'本户规则已保存',lane_started:'本户巡航已启动',lane_stopped:'本户巡航已停止'})[log.event]
        || (detail.enabled === true ? '自动授权已开启' : detail.enabled === false ? '自动授权已关闭' : '已记录本次运行事件');
      const diagnostics = items(detail.diagnostics).map(d=>({...d,reason:d.reason || items(d.reasons).join('；')})).filter(d => d.reason);
      return `<tr><td class="activity-time">${escape(clock(log.at))}</td><td>${escape(eventNames[log.event] || '运行记录')}</td>
        <td class="activity-content"><div>${escape(message)}</div>
        ${diagnostics.length ? `<details><summary>本轮规则检查</summary>${diagnostics.map(d => `<p>${escape(d.feature_id?.toUpperCase() || '检查')}${d.entity_id?' · 任务 '+escape(d.entity_id):''}${d.before!=null&&d.after!=null?' · '+escape(d.before)+' → '+escape(d.after):''} · ${escape(d.reason)}</p>`).join('')}</details>` : ''}
        <details data-runtime-detail="${escape(log.id)}" ${openIds.has(String(log.id)) ? 'open' : ''}><summary>原始详情</summary><pre>${escape(JSON.stringify(log.detail,null,2))}</pre></details></td></tr>`;
    }).join('')}</tbody></table>`;
  }

  class ActivityPanel {
    constructor(app) {
      this.app=app;
      this.root=app.$('#activity-log');
      this.accountId=null;
      this.records=[];
      this.requestSerial=0;
      this.lastFetch=0;
      if (this.root) this.bind();
    }

    render() {
      if (!this.root || !this.app.accountId) return;
      if (this.accountId !== this.app.accountId) {
        this.accountId=this.app.accountId;
        this.filters={action:'all',state:'verified',day:'',keyword:''};
        this.page=1; this.total=0; this.pages=1; this.lastFetch=0; this.error=''; this.loadingKey='';
        this.requestSerial++;
        this.records=[];
        this.root.innerHTML=`<div class="activity-filters">
          <label>操作内容<select data-activity-filter="action"><option value="all">全部操作</option>${Object.entries(categories).map(([key,label]) => `<option value="${key}">${label}</option>`).join('')}</select></label>
          <label>执行结果<select data-activity-filter="state"><option value="all">全部结果</option><option value="verified" selected>已核验</option><option value="pending">待确认或待核验</option><option value="acknowledged">官方已受理</option><option value="failed">未完成或已取消</option></select></label>
          <label>发起日期<input type="date" data-activity-filter="day"></label>
          <label class="activity-search">搜索计划、任务或素材ID<input type="search" maxlength="100" data-activity-search placeholder="输入ID或计划名称"></label>
          <button type="button" data-activity-search-button class="secondary">查询</button><button type="button" data-activity-today class="secondary">今天</button><button type="button" data-activity-reset class="secondary">重置</button>
          </div><p class="activity-caption">默认查看本户已核验的调控单，按发起时间排列。可切换结果查看待处理或未完成记录；结果更新时间单独显示。</p>
          <p data-activity-status role="status"></p><div data-activity-table class="table-scroll"></div>
          <div class="activity-pagination"><button type="button" data-activity-prev class="secondary">上一页</button><span data-activity-page></span><button type="button" data-activity-next class="secondary">下一页</button></div>`;
      }
      this.renderRows();
      if (this.app.view==='ledger') void this.refresh();
    }

    isAPI() {
      return items(this.app.state?.monitoring?.accounts).some(a => a.account_id===this.accountId && a.backend==='official_api')
        || items(this.app.state?.api_actions).some(r => r.intent?.account_id===this.accountId);
    }

    async refresh(force=false) {
      if (!this.root || !this.accountId) return;
      if (!this.isAPI()) {
        this.records=this.app.records();
        this.renderRows();
        return;
      }
      const accountId=this.accountId;
      const key=JSON.stringify([accountId,this.filters,this.page]);
      if (this.loadingKey===key || !force && this.loadedKey===key && Date.now()-this.lastFetch<15000) return;
      const serial=++this.requestSerial;
      if (this.loadedKey!==key) this.records=[];
      this.loadingKey=key; this.error=''; this.renderRows();
      const query=new URLSearchParams({account_id:accountId,page:String(this.page),page_size:'20',...this.filters});
      try {
        const data=await this.app.api('/api/official-api/actions/history?'+query);
        if (serial!==this.requestSerial || accountId!==this.app.accountId) return;
        if (data.account_id!==accountId || !Array.isArray(data.records)
            || data.records.some(r => r.intent?.account_id!==accountId)) throw new Error('返回记录的账户不一致');
        this.records=data.records; this.page=data.page; this.pages=data.pages; this.total=data.total;
        this.loadedKey=JSON.stringify([accountId,this.filters,this.page]); this.lastFetch=Date.now();
      } catch (error) {
        if (serial===this.requestSerial) this.error=error.message;
      } finally {
        if (serial===this.requestSerial) {this.loadingKey=''; this.renderRows();}
      }
    }

    renderRows() {
      const table=this.root.querySelector('[data-activity-table]');
      if (!table) return;
      const open=new Set([...table.querySelectorAll('details[data-activity-detail][open]')].map(d => d.dataset.activityDetail));
      let rows=this.records.filter(r => r.intent?.account_id===this.accountId).map(describe);
      if (!this.isAPI()) {
        rows=rows.filter(r => (this.filters.action==='all' || r.category===this.filters.action)
          && (this.filters.state==='all' || r.group===this.filters.state)
          && (!this.filters.day || clock(r.at).startsWith(this.filters.day))
          && (!this.filters.keyword || [r.taskId,r.planId,r.planName,r.id].some(v => v.includes(this.filters.keyword))));
        this.total=rows.length; this.pages=Math.max(1,Math.ceil(rows.length/20)); this.page=Math.min(this.page,this.pages);
        rows=rows.slice((this.page-1)*20,this.page*20);
      }
      table.innerHTML=rows.length ? `<table class="activity-table"><thead><tr><th>发起时间</th><th>操作类型</th><th>操作内容</th><th>执行结果</th></tr></thead><tbody>${rows.map(r => rowHTML(r,open.has(`${r.id}:${r.updatedAt || ''}`))).join('')}</tbody></table>`
        : '<p class="empty">本户当前筛选条件下没有调控记录。</p>';
      const status=this.root.querySelector('[data-activity-status]');
      status.textContent=this.error ? `读取记录失败，${this.error}` : this.loadingKey ? '正在读取本户调控记录…' : `共 ${this.total} 条调控记录`;
      status.classList.toggle('error',Boolean(this.error));
      this.root.querySelector('[data-activity-page]').textContent=`第 ${this.page} / ${this.pages} 页`;
      this.root.querySelector('[data-activity-prev]').disabled=Boolean(this.loadingKey) || this.page<=1;
      this.root.querySelector('[data-activity-next]').disabled=Boolean(this.loadingKey) || this.page>=this.pages;
    }

    updateFilters() {
      for (const select of this.root.querySelectorAll('[data-activity-filter]')) this.filters[select.dataset.activityFilter]=select.value;
      this.filters.keyword=this.root.querySelector('[data-activity-search]').value.trim();
      this.page=1;
      void this.refresh(true);
    }

    bind() {
      this.root.addEventListener('change',event => {if (event.target.dataset.activityFilter) this.updateFilters();});
      this.root.addEventListener('keydown',event => {if (event.key==='Enter' && event.target.matches('[data-activity-search]')) this.updateFilters();});
      this.root.addEventListener('click',event => {
        const button=event.target.closest('button');
        if (!button) return;
        if (button.hasAttribute('data-activity-search-button')) this.updateFilters();
        if (button.hasAttribute('data-activity-prev') && this.page>1) {this.page--;void this.refresh(true);}
        if (button.hasAttribute('data-activity-next') && this.page<this.pages) {this.page++;void this.refresh(true);}
        if (button.hasAttribute('data-activity-today')) {
          this.root.querySelector('[data-activity-filter="day"]').value=clock(new Date().toISOString()).slice(0,10);
          this.updateFilters();
        }
        if (button.hasAttribute('data-activity-reset')) {
          for (const field of this.root.querySelectorAll('[data-activity-filter]')) field.value=field.tagName==='SELECT' ? 'all' : '';
          this.root.querySelector('[data-activity-filter="state"]').value='verified';
          this.root.querySelector('[data-activity-search]').value='';
          this.updateFilters();
        }
      });
    }
  }
  const exports={ActivityPanel,describe,runtimeHTML,clock,money,rowHTML};
  if (typeof module==='object' && module.exports) module.exports=exports;
  global.QianchuanActivity=exports;
})(typeof window==='object' ? window : globalThis);
