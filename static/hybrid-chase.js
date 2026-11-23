'use strict';
(function(global){
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time=v=>v?new Date(v).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):'—';
  const currentAdapter=v=>/^\d+\.\d+\.\d+$/.test(v||'')&&v.split('.').map(Number).reduce((n,x,i)=>n||Math.sign(x-[1,8,0][i]),0)>=0;
  function queueHTML(executor,accounts,open=false){
    const rows=executor?.scheduler?.queue;if(!Array.isArray(rows))return '';
    const phases={waiting_api:'等待API取数或核验',api_inventory:'API核对任务与预算',tasks:'核对在投任务',lookup:'按ID回读',scan:'检查调控规则',lifetime:'读取累计消耗',prepare:'准备表单',verify:'核验原提交',api_materials:'API读取素材',materials:'读取素材',dry_prepare:'不提交预演'};
    return `<details id="ego-queue" ${open?'open':''}><summary>多户调度队列 · ${new Set(rows.map(r=>r.account_id)).size}户</summary><p class="muted">${executor.mode==='account_parallel'?'各户独立执行，同户内按阶段顺序处理；结果回读及待执行的暂停优先。':'结果回读优先，其次是待执行的暂停；其余按账户轮流执行一个网页阶段，再交给下一户。'}各户规则、额度和周期分别保留。</p><table><thead><tr><th>账户</th><th>任务阶段</th><th>排队状态</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(accounts?.find(a=>a.id===r.account_id)?.name||r.account_id)}</td><td>${r.kind==='rules'?'规则调控':'F5追投'} · ${esc(phases[r.phase]||r.phase)}</td><td>${esc(r.reason||((executor.account_workers||[]).some(w=>w.account_id===r.account_id&&w.running)||executor.busy&&executor.scheduler.last_account_id===r.account_id?'当前阶段执行中':'等待本户轮次'))}</td></tr>`).join('')||'<tr><td colspan="3">当前没有已就绪的网页阶段；API取数和各户周期独立等待。</td></tr>'}</tbody></table></details>`;
  }
  function poolHTML(executor,draft,busy,appState={},currentAccountId=null){
    const friendly=message=>{
      const raw=String(message||'');
      if(/EGO_USER_CONTROL|Control of this task space is with the user/.test(raw))return 'Ego已交给用户控制，归还智能体后自动继续';
      if(/page\.(?:waitForFunction|goto|reload).*timed out|EGO_PAGE_UNRESPONSIVE/.test(raw))return '千川页面加载超时，当前阶段未完成';
      if(/首页母计划行未唯一对应|首页本主计划|首页追投入口/.test(raw))return '等待首页显示本户主计划，尚未打开追投表单';
      if(/审核状态.*加载|审核状态.*未明确/.test(raw))return '素材审核状态尚未加载完成';
      if(/命令.*过期|租约.*过期|文档或时间无效/.test(raw))return '页面处理超时，本轮核验已过期';
      if(/原追投窗口母计划不符/.test(raw))return '遗留窗口的主计划身份未通过核验';
      if(/素材明细.*未展开/.test(raw))return '在投任务的素材明细读取未完成';
      return raw;
    };
    const chase=w=>(appState.hybrid_chase||[]).find(r=>r.account_id===w.account_id&&r.run_id&&!['completed','cancelled'].includes(r.state));
    const error=w=>w.error||chase(w)?.last_error?.raw_message||chase(w)?.last_error?.title;
    const currentError=w=>w.error||(chase(w)?.last_error&&(!chase(w).last_error.phase||chase(w).last_error.phase===chase(w).phase)?error(w):null);
    const progress=w=>{const r=chase(w);return r?.last_error&&(!r.last_error.phase||r.last_error.phase===r.phase)?r.last_error.title||r.message||r.phase:r?.message||r?.phase;};
    const queue=w=>(executor.scheduler?.queue||[]).filter(r=>r.account_id===w.account_id);
    const state=w=>w.paused?'本户已暂停':w.suspended?(w.automatic_control_return?'正在核对Ego控制归属，归还后自动继续':'等待恢复浏览器控制'):!executor.enabled?'常驻已暂停':w.busy?'正在处理本户网页任务':currentError(w)?'上次执行失败，等待重试':queue(w).some(r=>r.ready)?'已排队，等待本户阶段':queue(w).length?(queue(w)[0].reason||'等待下一阶段'):w.running?'正在检查执行连接':w.current?'暂无待执行网页任务':'等待连接';
    const row=w=>`<tr class="${w.account_id===currentAccountId?'is-current-account':''}"><td>${esc(w.name||w.account_id)}<br><small>${esc(w.account_id)}</small></td><td>${state(w)}${w.space_id?' · Space '+esc(w.space_id):''}<br><small>${w.current?'心跳已连接':'心跳未连接'}</small></td><td class="ego-progress-cell">${esc(friendly(w.message))}${chase(w)?'<br>F5：'+esc(friendly(progress(w))):''}${w.seen_at?'<br>最近通信 '+esc(time(w.seen_at)):''}${error(w)?`<p class="muted">上次失败：${esc(friendly(error(w)))}</p><details><summary>技术详情</summary><p class="ego-tech-detail">${esc(error(w))}</p></details>`:''}</td><td><button class="secondary" data-ego-account="${esc(w.account_id)}" data-operation="${w.paused?'start':'pause'}" ${busy?'disabled':''}>${w.paused?'启动本户':'暂停本户'}</button><button class="secondary" data-ego-account="${esc(w.account_id)}" data-operation="restart" ${busy?'disabled':''} title="结束本户旧脚本并立即启动；已提交原单保留核验">重启本户</button>${w.suspended?`<button data-ego-account="${esc(w.account_id)}" data-operation="resume" ${busy?'disabled':''}>恢复本户浏览器控制</button>`:''}</td></tr>`;
    return `<div class="ego-pool"><h3>账户独立执行</h3><div class="ego-pool-toolbar"><label>执行方式 <select id="ego-pool-mode"><option value="serial" ${draft.mode==='serial'?'selected':''}>单执行器轮流处理</option><option value="account_parallel" ${draft.mode==='account_parallel'?'selected':''}>每户独立并行</option></select></label><label>最多同时处理 <select id="ego-pool-limit">${[1,2,3,4,5,6,7,8].map(n=>`<option value="${n}" ${draft.max_parallel===n?'selected':''}>${n}户</option>`).join('')}</select></label><button id="ego-pool-save" class="secondary" ${busy?'disabled':''}>应用执行方式</button></div>
      <p class="muted">切换执行方式前先暂停常驻并等待当前阶段结束。各户使用独立页面、队列和重试；共用API频控及操作账本，不增加授权额度。提高并发不会绕过平台限流。</p>
      ${executor.control_return_error?`<p class="muted">控制归属同步暂未成功：${esc(executor.control_return_error)}。保留暂停，程序将自动重试检查。</p>`:''}
      ${executor.mode==='account_parallel'?`<div class="table-scroll ego-workers-wrap"><table id="ego-account-workers"><thead><tr><th>账户</th><th>运行状态</th><th>当前进展</th><th>操作</th></tr></thead><tbody>${(executor.account_workers||[]).map(row).join('')||'<tr><td colspan="4">启动常驻后，为有授权或原单待核验的账户建立执行单元。</td></tr>'}</tbody></table></div>`:''}</div>`;
  }
  class Panel {
    constructor(app){this.app=app;this.root=app.$('#hybrid-chase-panel');this.forms=new Map();this.busy=false;if(this.root)this.bind();}
    render(){
      if(!this.root)return;
      const a=this.app.account();this.root.hidden=!a||a.scene!=='live';if(this.root.hidden)return;
      const focused=this.root.ownerDocument.activeElement;
      const focus=focused?.id==='hybrid-excluded-ids'&&this.aid===a.id?{start:focused.selectionStart,end:focused.selectionEnd,scroll:focused.scrollTop}:null;
      const oldText=this.root.querySelector('#hybrid-excluded-ids');
      if(oldText&&this.aid&&this.forms.has(this.aid))this.forms.get(this.aid).excluded=oldText.value;
      const state=(this.app.state.hybrid_chase||[]).find(s=>s.account_id===a.id);
      const executor=this.app.state.ego_executor,ego=executor?.provider==='ego';
      const queueOpen=this.root.querySelector('#ego-queue')?.open;
      const rules=(this.app.state.hybrid_rules||[]).find(s=>s.account_id===a.id);
      const apiControl=this.app.state.execution_strategy?.mode==='api_control_browser_chase';
      const deferrals=(this.app.state.api_material_deferrals||[]).filter(s=>s.account_id===a.id);
      const saved=state?.mode||'api_only';let form=this.forms.get(a.id);
      const exclusions=(this.app.state.api_material_exclusions||[]).find(s=>s.account_id===a.id)?.material_ids||[];
      if(!form){form={mode:saved,excluded:exclusions.join('\n'),exclusionDirty:false};this.forms.set(a.id,form);}
      if(!form.exclusionDirty)form.excluded=exclusions.join('\n');
      const keep=this.root.querySelector('#hybrid-mode')?.value;
      if(this.aid===a.id&&keep)form.mode=keep;
      this.aid=a.id;
      if(!this.poolDirty)this.poolDraft={mode:executor?.mode||'serial',max_parallel:executor?.max_parallel||3};
      const feedback=this.feedback?.aid===a.id&&(this.busy||this.feedback.updated_at===state?.updated_at)?this.feedback.message:null;
      const error=state?.last_error,active=state?.run_id&&!['completed','cancelled'].includes(state.state);
      const authority=(this.app.state.api_auto||[]).find(g=>g.account_id===a.id);
      const apiData=saved==='api_data_browser';
      const apiAdapter=version=>{const n=String(version||'').split('.').map(Number);return n.length===3&&n.every(Number.isInteger)&&(n[0]>1||n[0]===1&&n[1]>=10);};
      const canTest=(authority?.budget_unlimited||authority?.budget_cap)&&(authority?.batches_unlimited||authority?.batch_cap)&&saved!=='api_only'&&state?.bridge?.current&&state.bridge.armed&&currentAdapter(state.bridge.version)&&(!apiData||apiAdapter(state.bridge.version))&&!active;
      const canStart=authority?.enabled&&authority.execution_mode!=='single_test'&&canTest;
      this.root.innerHTML=`<div class="section-heading"><div><h2>API与网页互补调控</h2><p>F1–F6沿用本户已授权规则。F5另受原在投预算上限和每日批数约束；网页通过${ego?'Ego Lite常驻执行器':'原Chrome'}执行。</p></div></div>
        ${executor||ego?`<section class="chase-zone chase-zone-global"><div class="zone-header"><span class="zone-pill global">全局 · 多户共用</span><h3>${ego?'Ego Lite 执行器与多户并行状态':'浏览器执行器（全局开关）'}</h3></div>`:''}
        ${executor?`<div class="hybrid-progress"><strong>浏览器执行器：${ego?'Ego Lite 常驻':'Chrome 扩展'}</strong>
        <p>${executor.suspended&&/Control of this task space|EGO_USER_CONTROL/.test(executor.message||'')?'浏览器已由用户接管，所有网页调控暂停。处理完页面后，点击下方“恢复浏览器控制”。':esc(executor.message)}${executor.seen_at?' · 最近通信 '+esc(time(executor.seen_at)):''}</p>
        <p>常驻${executor.running?'运行中':'已暂停'} · 网页执行${executor.suspended?'已暂停':executor.armed?'已开启':'关闭'}${executor.suspended?' · 浏览器控制权待恢复':''}${executor.busy?' · 当前阶段执行中':''}</p>
        <button id="ego-start" class="secondary" ${this.busy||!executor.installed?'disabled':''}>启动Ego常驻</button>
        <button id="ego-arm" ${this.busy||!ego||!executor.enabled?'disabled':''}>${executor.armed?'关闭网页执行':'开启网页执行'}</button>
        <button id="ego-pause" class="secondary" ${this.busy||!executor.enabled?'disabled':''}>暂停常驻</button>
        <button id="ego-resume" class="secondary" ${this.busy||!executor.suspended?'disabled':''}>恢复浏览器控制</button>
        <p class="muted">这是所有网页通道账户共用的执行开关，各户仍按自己的规则、启用状态和额度运行。原扩展停止领取网页任务；提交结果不明仍只核验原单。启动常驻保留执行关闭，开启后才按已授权规则修改投放。</p>
        ${executor.error?`<details><summary>执行器技术详情</summary><p role="alert">${esc(executor.error)}</p></details>`:''}</div>`:''}
        ${ego?queueHTML(executor,this.app.state.accounts,queueOpen):''}
        ${ego?poolHTML(executor,this.poolDraft,this.busy,this.app.state,a.id):''}
        ${executor||ego?'</section>':''}
        <section class="chase-zone chase-zone-account"><div class="zone-header"><span class="zone-pill account">当前账户</span><h3>${esc(a.name||a.id)} · 追投通道与运行进度</h3></div><div class="chase-account-grid"><div class="chase-account-main">
        ${state?.isolated_readbacks?.length?`<details class="hybrid-isolated"><summary>原单后台核验 · ${esc(state.isolated_readbacks.length)} 笔 · 保留预算 ${esc(state.isolated_readbacks.reduce((v,r)=>v+Number(r.reserved_budget),0).toFixed(2))} 元</summary><p>这些原单未判定成功或失败，预算和素材继续占用；不会重复追投。其他独立调控按原规则和剩余额度继续。</p>${state.isolated_readbacks.map(r=>`<p>原单 ${esc(r.action_id)} · 保留 ${esc(r.reserved_budget)} 元<br>暂不重复追投素材：${esc(r.material_ids.join('、'))}</p>`).join('')}</details>`:''}
        <div class="hybrid-controls"><label>调控通道<select id="hybrid-mode">
          <option value="api_only" ${form.mode==='api_only'?'selected':''}>仅API</option>
          ${apiControl?'':`<option value="api_preferred" ${form.mode==='api_preferred'?'selected':''}>API优先，限流时网页补位</option>`}
          <option value="api_data_browser" ${form.mode==='api_data_browser'?'selected':''}>${apiControl?'API读取与调控，Ego仅新建追投':'API取数、网页追投（F5免网页全量扫描）'}</option>
          ${apiControl?'':`<option value="browser_only" ${form.mode==='browser_only'?'selected':''}>仅网页调控</option>`}
        </select></label><button id="hybrid-save" ${this.busy?'disabled':''}>保存通道</button>
        <button id="hybrid-preview" class="secondary" ${this.busy?'disabled':''}>只读验证网页</button>
        <button id="hybrid-start" ${this.busy||!canStart?'disabled':''}>立即按规则追投</button>
        <button id="hybrid-start-test" class="secondary" ${this.busy||!canTest?'disabled':''}>测试一条追投</button>
        <button id="hybrid-retry" class="secondary" ${this.busy||!state?.retry_available?'disabled':''}>重试当前阶段</button>
        <button id="hybrid-stop" class="secondary" ${this.busy?'disabled':''}>停止本轮补位</button></div>
        <p class="muted">${apiControl?'分工：API负责素材数据、在投任务、预算、状态、暂停及ROI调整，并核验原追投结果；Ego只填表创建追投。API限流或缺字段时等待，不安排网页扫描任务。':`保存通道不会开启已关闭的授权。API读取限流或明确40100拒绝后，网页重新读数并判断F1–F4/F6；提交结果不明只核验原单。${ego?'当前由Ego常驻执行。':'规则调控网页补位需要扩展1.9.0。'}`}</p>
        ${apiData?`<p class="muted">F5由API按本户近3–5天的消耗降序扫完门槛以上范围，再按每批数量分组。网页只按素材ID核对本批、填写并提交追投，随后独立核验。API素材报表限流时等待冷却，不切回网页全量扫描${ego?'。':'；需要扩展1.10.0。'}</p>`:''}
        ${apiControl?'':`<div class="hybrid-rule-progress"><strong>F1–F4 / F6 调控补位</strong>
        <p>${esc(rules?.message||'尚未启动规则网页补位')}</p>
        ${rules?`<p class="muted">已扫描 ${esc(rules.pages_read||0)} 页 / ${esc(rules.tasks_read||0)} 条任务 · 已修改并核验 ${esc(rules.completed||0)} 条${rules.paused?' · 已暂停自动重试':''}${rules.preview?' · 不提交验证':''}</p>`:''}
        <button id="hybrid-rules-preview" class="secondary" ${this.busy?'disabled':''}>只读检查调控规则</button>
        <button id="hybrid-rules-retry" class="secondary" ${this.busy||!rules||['completed','cancelled'].includes(rules.state)?'disabled':''}>重试调控补位</button>
        <button id="hybrid-rules-stop" class="secondary" ${this.busy||!rules||['completed','cancelled'].includes(rules.state)?'disabled':''}>停止本轮调控补位</button>
        <p class="muted">F4累计低消耗会另读创建至今的范围；F6仍须同直播间官方近15/30分钟ROI及在线人数，缺值跳过。</p>
        ${rules?.diagnostics?.length?`<details><summary>查看本轮未执行原因</summary>${rules.diagnostics.map(x=>`<p>${esc(x.feature_id?.toUpperCase())} ${esc(x.entity_id||'')}：${esc(x.reason||x.reasons?.join('；'))}</p>`).join('')}</details>`:''}</div>`}
        <p class="muted">“立即按规则追投”会按本户原F5规则和额度创建实际任务，须先保存网页通道并${ego?'开启上方的网页执行':'开启原扩展的网页执行'}。</p>
        <p class="muted">“测试一条追投”只新建一条任务，素材数量、整批预算和出价沿用本户F5设置；完成后停止，连续自动追投关闭。</p>
        <p class="muted">${ego?(state?.bridge?.current?'Ego Lite '+esc(state.bridge.version)+' 已连接 · '+(state.bridge.armed?'网页执行已开启':'网页执行关闭，仍可做不提交的验证'):'等待 Ego Lite 常驻连接；可在上方启动或查看具体错误'):state?.bridge?.current?'原Chrome扩展 '+esc(state.bridge.version)+' 已连接 · '+(state.bridge.armed?'网页执行已开启':'网页执行关闭，仍可做不提交的验证'):'等待原Chrome加载1.8.0扩展；原配对码和站点权限沿用'}</p>
        ${state?.bridge?.current&&!currentAdapter(state.bridge.version)?'<p class="muted">后台标签页等待和准备步骤超时修复已准备为1.8.0，请重新加载原扩展后继续验证。配对码和网站权限沿用。</p>':''}
        <p role="status" aria-live="polite">${esc(feedback||state?.message||'尚未开始网页补位')}</p>
        ${state?.material_source==='official_api'&&state.date_start?`<p><strong>近几天累计消耗</strong> · ${esc(state.date_start)} 至 ${esc(state.date_end)}<br>消耗大于 ${esc(state.spend_floor??'—')} 元的有效素材 <strong>${esc(state.spend_threshold_materials??'—')}</strong> 条 · 进一步筛选ROI和排除名单后 ${esc(state.matched_materials??'—')} 条候选<br>统计期有消耗 ${esc(state.material_total??'—')} 条，本轮读取 ${esc(state.materials_read||0)} 条；末页包含低于门槛的素材，读取数不是符合数。</p>`:''}
        ${state?.material_source==='official_api'?`<p class="muted">素材数据来源：官方API · 已读 ${esc(state.pages_read||0)} 页 / ${esc(state.materials_read||0)} 条${state.material_captured_at?' · 取数时间 '+esc(time(state.material_captured_at)):''} · ${state.eligible_range_complete?'消耗门槛以上范围已扫完':'等待本轮完整取数'} · 另有 ${esc(state.unbatched_materials||0)} 条不足整批</p>`:''}
        ${state?.last_progress_at?`<p class="muted">最近实际进展 ${esc(time(state.last_progress_at))} · 未提交轮次10分钟无进展会自动结束，其他巡航继续；结果不明的提交只回读。</p>`:''}
        ${state?.reserved_tasks?.length?`<details><summary>未核实历史任务：保留额度后继续</summary>${state.reserved_tasks.map(r=>`<p>任务 ${esc(r.id)} · 保留 ${esc(r.budget)} 元占用</p>`).join('')}</details>`:''}
        <details class="hybrid-deferrals"><summary>临时暂缓素材 · ${esc(new Set(deferrals.flatMap(r=>r.material_ids)).size)} 条</summary>
        <p class="muted">同一素材ID在提交前连续失败2次，临时暂缓15分钟；明确审核不通过、审核拒绝、审核失败或已删除素材单次即暂缓12小时；到期自动重试。其余合格素材按数量区间重新组批，达到最少数量即可继续。不改本户永久排除清单。</p>
        ${deferrals.map(r=>`<p>${esc(r.material_ids.join('、'))}<br>${esc(r.reason)} · ${esc(time(r.until_at))} 后恢复</p>`).join('')||'<p>暂无临时暂缓素材。</p>'}
        <button id="hybrid-deferrals-clear" class="secondary" ${this.busy||!deferrals.length?'disabled':''}>清除本户临时暂缓</button></details>
        ${state?.readback_only?`<div class="hybrid-progress"><p><strong>原任务只读复核</strong> · ${esc(state.state==='completed'?'本轮已完成':state.state==='cancelled'?'本轮已停止':'程序核验中')} · 母计划 ${esc(state.plan_id)}</p><p>原任务 ID：${esc(state.readback_result?.task_id||'核验中')} · 本轮创建追投 0 条</p><p class="muted">最近回传 ${esc(time(state.updated_at))}</p></div>`:state?.run_id?`<div class="hybrid-progress"><p><strong>${state.preview?'只读预演 · 不提交投放':state.single_test?'单条测试 · 最多一条任务':'正式补位'}</strong> · ${esc(state.state==='completed'?'本轮已完成':state.state==='cancelled'?'本轮已停止':state.phase_label||state.phase||'等待执行')} · 母计划 ${esc(state.plan_id)}</p><p>已核对任务 <strong>${esc(state.tasks_read??0)}</strong>${state.task_total!==null&&state.task_total!==undefined?' / '+esc(state.task_total):''} 条 · 已扫描素材 <strong>${esc(state.pages_read)} 页 / ${esc(state.materials_read)} 条</strong> · 待处理批次 ${esc(state.batches_ready)} · 已提交并核验 ${esc(state.batches_completed)} 批</p><p class="muted">当前第 ${esc(state.current_page??1)} 页 · 最近回传 ${esc(time(state.updated_at))}${state.preview&&state.state==='completed'?' · 本轮没有提交广告':''}</p></div>`:''}
        ${error&&active?`<div class="hybrid-error" role="alert"><p><strong>${esc(error.phase_label)}遇到问题：${esc(error.title)}</strong></p><p>${esc(error.hint)}</p><p>本阶段连续失败 ${esc(error.attempt)} 次${state.auto_retry_paused?' · 已暂停重复重试，请处理原因后重试当前阶段':state.retry_at?' · 最早 '+esc(time(state.retry_at))+' 自动重试':''}；可手动重试当前阶段。</p><details><summary>技术详情</summary><p>${esc(error.raw_message)}</p></details></div>`:''}</div><div class="chase-account-side"><div class="hybrid-exclusions"><label for="hybrid-excluded-ids"><strong>排除追投的素材 ID</strong></label><textarea id="hybrid-excluded-ids" rows="6" placeholder="每行一个素材ID，也可用逗号或空格分隔">${esc(form.excluded)}</textarea><div class="hybrid-exclusion-actions"><button id="hybrid-exclusions-save" class="secondary" ${this.busy?'disabled':''}>保存本户排除素材</button><span class="muted">已保存 ${esc(exclusions.length)} 条${form.exclusionDirty?' · 有未保存修改':''}</span></div><p class="muted">仅对当前账户生效，API和网页追投共用；保存后不再新追这些素材，不会暂停已有任务。清空后保存可取消排除。</p></div></div></div></section>`;
      if(focus){const input=this.root.querySelector('#hybrid-excluded-ids');input.focus({preventScroll:true});input.setSelectionRange(focus.start,focus.end);input.scrollTop=focus.scroll;}
    }
    bind(){
      this.root.addEventListener('input',e=>{if(e.target.id==='hybrid-excluded-ids'){const form=this.forms.get(this.aid);form.excluded=e.target.value;form.exclusionDirty=true;}});
      this.root.addEventListener('change',e=>{if(e.target.id==='hybrid-mode')this.forms.get(this.aid).mode=e.target.value;if(e.target.id==='ego-pool-mode'||e.target.id==='ego-pool-limit'){this.poolDraft={mode:this.root.querySelector('#ego-pool-mode').value,max_parallel:Number(this.root.querySelector('#ego-pool-limit').value)};this.poolDirty=true;}});
      this.root.addEventListener('click',async e=>{
        const b=e.target.closest('button');if(!b||this.busy)return;
        if(b.id==='ego-pool-save'||b.dataset.egoAccount){
          this.busy=true;
          try{
            await this.app.api('/api/ego-executor/'+(b.id==='ego-pool-save'?'pool':'account'),b.id==='ego-pool-save'?this.poolDraft:{account_id:b.dataset.egoAccount,operation:b.dataset.operation});
            this.poolDirty=false;await this.app.refresh();
          }catch(error){this.feedback={aid:this.app.accountId,message:error.message};}
          finally{this.busy=false;this.render();}return;
        }
        if(['ego-start','ego-arm','ego-pause','ego-resume'].includes(b.id)){
          const current=this.app.state.ego_executor||{};this.busy=true;
          try{
            await this.app.api('/api/ego-executor/'+(b.id==='ego-resume'?'resume':'configure'),b.id==='ego-resume'?{}:{provider:'ego',enabled:b.id!=='ego-pause',armed:b.id==='ego-arm'?!current.armed:false});
            await this.app.refresh();
          }catch(error){this.feedback={aid:this.app.accountId,message:error.message};}
          finally{this.busy=false;this.render();}
          return;
        }
        const action={'hybrid-save':'configure','hybrid-preview':'preview','hybrid-stop':'stop','hybrid-retry':'retry','hybrid-start':'start','hybrid-start-test':'start-test','hybrid-exclusions-save':'material-exclusions','hybrid-deferrals-clear':'deferrals-clear','hybrid-rules-preview':'rules-preview','hybrid-rules-retry':'rules-retry','hybrid-rules-stop':'rules-stop'}[b.id];if(!action)return;
        const aid=this.app.accountId,updated_at=(this.app.state.hybrid_chase||[]).find(s=>s.account_id===aid)?.updated_at;this.busy=true;this.feedback={aid,updated_at,message:'正在处理本户网页补位设置…'};this.render();
        try{
          const payload={account_id:aid};if(action==='configure')payload.mode=this.forms.get(aid).mode;
          if(action==='material-exclusions')payload.material_ids=[...new Set(this.forms.get(aid).excluded.split(/[\s,，;；、]+/).filter(Boolean))];
          const reply=await this.app.api('/api/official-api/'+(action==='deferrals-clear'?'material-deferrals/clear':action.startsWith('rules-')?'hybrid-rules/'+action.slice(6):action==='material-exclusions'?action:'hybrid/'+action),payload,{signal:AbortSignal.timeout(15000)});
          if(action==='material-exclusions'){
            const ids=reply.material_ids||payload.material_ids;
            const rows=this.app.state.api_material_exclusions||=[];
            const previous=rows.find(s=>s.account_id===aid);if(previous)previous.material_ids=ids;else rows.push({account_id:aid,material_ids:ids});
            this.forms.get(aid).excluded=ids.join('\n');this.forms.get(aid).exclusionDirty=false;
          }
          this.feedback={aid,updated_at,message:{'deferrals-clear':'已清除临时暂缓；永久排除、去重规则和自动授权未变。','rules-preview':'已安排规则只读检查，不提交投放。','rules-retry':'已重试原调控阶段；结果不明只回读。','rules-stop':'已停止新调控；已提交原单仍保留核验。',configure:'本户通道已保存；原规则与额度保持不变。',preview:'已安排只读验证，等待原Chrome扩展读取；不会提交广告。',stop:'已停止未提交的补位任务；已提交部分仍保留独立核验。',retry:'已安排重试当前阶段；已提交单只核验，不重复创建。',start:'已启动本轮真实追投，按本户原规则和额度创建并核验任务。','start-test':'已启动单条测试；只创建一条任务，完成后停止连续追投。','material-exclusions':'本户排除素材已保存，API与网页追投共用。'}[action]};
          await this.app.refresh();
        }catch(error){this.feedback={aid,updated_at,message:error.message};}
        finally{if(action==='material-exclusions')this.feedback={aid,updated_at,message:this.feedback?.message||'本户排除素材已保存。'};this.busy=false;this.render();}
      });
    }
  }
  global.QianchuanHybrid={Panel};
  if(typeof module==='object'&&module.exports)module.exports={Panel};
})(typeof window==='object'?window:globalThis);
