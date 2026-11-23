'use strict';
// Local approval UI. This page never clicks the platform or calls its APIs.
let browserControlState=null, browserControlDraft=null, browserControlReconciliation=null;
const browserActionNames={budget_up:'上调主计划日预算',budget_down:'下调主计划日预算',roi_target_up:'上调主计划ROI设置',roi_target_down:'下调主计划ROI设置',pause_plan:'暂停主计划'};
const browserStateNames={draft:'待逐条确认',approved:'已确认，等待单条执行',claimed:'已占用，正在核对页面',submitting:'正在提交并等待读回',verified:'刷新后设置已核对',rejected:'未执行或提交前核对失败',unknown:'结果不明，停止后续提交',cancelled:'已取消',expired:'审批已过期'};
Object.assign(browserStateNames,{reconciled_target:'当前值与目标一致，已解除队列暂停',reconciled_before:'当前值与原值一致，已解除队列暂停',reconciled_other:'当前值为其他值，已解除队列暂停'});
const browserControls=()=>document.querySelector('#browser-control');
function browserControlChoices(){
  return (browserControlState?.workers||[]).filter(w=>w.armed).flatMap(w=>w.surfaces.filter(s=>s.actions.length).map(s=>({worker:w,surface:s})));
}
function browserConnectionText(){
  return browserControlState?.execution_available?(state?.csrf?'浏览器执行器已连接；每次只处理你确认的一条。':'正在读取本地会话，暂不能准备动作。'):'浏览器执行适配器尚未连接，当前不能提交账户修改。';
}
function syncBrowserControl(value){
  const root=browserControls();if(!root||!value)return;
  const editing=Boolean(browserControlDraft||browserControlReconciliation||['#browser-plan','#browser-after','#browser-reason'].some(s=>String(root.querySelector(s)?.value||'').trim()));
  browserControlState=value;
  if(!editing){renderBrowserControl();return;}
  const note=root.querySelector('#browser-connection-note');if(note)note.textContent=browserConnectionText();
  const prepare=root.querySelector('#browser-prepare');if(prepare)prepare.disabled=!value.execution_available||!state?.csrf;
}
function showBrowserApproval(d){
  browserControlDraft=d;const i=d.intent;
  browserControls().querySelector('#browser-approval').innerHTML=`<article class="recommendation"><h3>请核对这一条</h3><p>账户 ${esc(i.account_id)} · ${esc(i.plan_name)} · 计划 ${esc(i.plan_id)}</p><p><strong>${esc(i.action_label)}：${esc(i.before)} → ${esc(i.after)}</strong></p><p>计划设置口径：${esc(basisNames[i.setting_roi_basis]||i.setting_roi_basis)}；原因：${esc(i.reason)}</p><p>来源：${i.origin==='rule_recommendation'?'当前规则建议，将在执行前重新判断':'人工指定动作，未认证规则达标'}。</p><p>审批有效至 ${esc(date(d.expires_at))}。提交前会再次核对页面；当前值变化时退回。</p><label><input type="checkbox" id="browser-confirm-check">我已核对账户、计划、前后值及本次投放风险</label><div class="button-row"><button id="browser-confirm">确认并执行这一条</button><button class="quiet" data-browser-cancel="${esc(d.id)}">取消</button></div></article>`;
}
function showBrowserReconciliation(d){
  browserControlReconciliation=d;
  const i=d.intent,e=d.evidence;
  const description={target:'与原目标值一致',before:'与原设置值一致',other:'与原值和目标均不相同'}[e.match];
  browserControls().querySelector('#browser-reconciliation').innerHTML=`<article class="recommendation"><h3>核对中断后的当前设置</h3><p>账户 ${esc(i.account_id)} · 计划 ${esc(i.plan_id)} · ${esc(i.action_label)}</p><p>原调控单 ${esc(i.before)} → ${esc(i.after)}；刷新后当前值 <strong>${esc(e.value)}</strong>（${esc(description)}）。</p><p>读取 ${esc(date(e.surface.captured_at))}；核对单有效至 ${esc(date(d.expires_at))}。</p><p>这次核对不证明原提交是否成功或变化由谁造成。确认只解除后续队列暂停，不重试原动作；此计划从核对时起保留1小时冷却。</p><label><input type="checkbox" id="browser-reconcile-check">我已核对上述当前设置，同意解除队列暂停</label><button id="browser-reconcile-confirm">确认当前设置并解除暂停</button></article>`;
}
function browserResolutionText(a){
  if(!a.resolution)return '';
  return `<p>独立核对 ${esc(date(a.resolution.resolved_at))}：当时当前值 ${esc(a.resolution.evidence.value)}。原提交及变化归因仍未知；原动作不重试，此计划从核对时起冷却1小时。</p>`;
}
function browserActionCard(a){
  return `<article class="recommendation"><h3>${esc(browserStateNames[a.state]||a.state)} · ${esc(a.intent.action_label)}</h3><p>账户 ${esc(a.intent.account_id)} · ${esc(a.intent.plan_name)} · 计划 ${esc(a.intent.plan_id)}</p><p>${esc(a.intent.before)} → ${esc(a.intent.after)}；原因：${esc(a.intent.reason)}</p><p>创建 ${esc(date(a.created_at))}；审批截止 ${esc(date(a.expires_at))}。</p>${a.state==='verified'?'<p>刷新后页面设置与目标一致；这不证明投放效果或变化因果。</p>':''}${a.receipt?`<p>${esc(a.receipt.message)}</p>`:''}${browserResolutionText(a)}${a.state==='unknown'?`<p>请先刷新原计划标签，等待执行器上报新文档的设置，再独立核对；不再次提交原修改。</p><button class="quiet" data-browser-reconcile="${esc(a.id)}">读取刷新后的当前设置</button>`:''}${a.state==='draft'?`<button class="quiet" data-browser-review="${esc(a.id)}">继续核对这一条</button>`:''}${['draft','approved'].includes(a.state)?`<button class="quiet" data-browser-cancel="${esc(a.id)}">取消这条未占用动作</button>`:''}</article>`;
}
function renderBrowserControl(){
  const root=browserControls();if(!root)return;
  browserControlDraft=null;browserControlReconciliation=null;
  const choices=browserControlChoices();
  const available=browserControlState?.execution_available&&Boolean(state?.csrf);
  root.innerHTML=`<p id="browser-connection-note">${browserConnectionText()}</p>
  ${!browserControlState?.workers?.length?'<p>请在现有Chrome重新加载原扩展至1.3.0，打开“千川多账户监测与确认调控”，核对说明后单独开启逐条执行。配对码沿用；开启模式不批准任何投放动作，每条仍在这里确认。</p>':''}
  <p class="footnote">账户考核目标与计划当前ROI设置分别保留。这里的手动调控单需要你指定动作和原因，不代表程序已判定ROI达标。规则自动建议仍按原有数据和目标条件判断。</p>
  <div class="form-grid"><label>明确账户与主计划<select id="browser-plan"><option value="">选择已核验的浏览器计划</option>${choices.map((c,i)=>`<option value="${i}">${esc(c.surface.account_id)} / ${esc(c.surface.name)} / ${esc(c.surface.plan_id)}</option>`).join('')}</select></label>
  <label>本次动作<select id="browser-action"><option value="">先选择计划</option></select></label>
  <label>新值<input id="browser-after" inputmode="decimal" autocomplete="off" placeholder="不自动填入建议值"></label>
  <label>调控原因<input id="browser-reason" maxlength="500" autocomplete="off" placeholder="说明本次具体原因"></label></div>
  <p id="browser-before" class="footnote"></p><button id="browser-prepare" class="quiet" ${available?'':'disabled'}>准备这一条调控单</button>
  <div id="browser-approval" aria-live="polite"></div>
  <div id="browser-reconciliation" aria-live="polite"></div>
  <div>${(browserControlState?.actions||[]).map(browserActionCard).join('')||'<p class="muted">没有调控单。</p>'}</div>`;
  root.querySelector('#browser-plan').addEventListener('change',()=>{
    const choice=choices[Number(root.querySelector('#browser-plan').value)];
    const selected=root.querySelector('#browser-plan').value!==''?choice:null;
    root.querySelector('#browser-action').innerHTML=selected?selected.surface.actions.map(a=>`<option value="${esc(a)}">${esc(browserActionNames[a])}</option>`).join(''):'<option value="">先选择计划</option>';
    root.querySelector('#browser-before').textContent=selected?`当前日预算 ${selected.surface.budget??'未知'}；计划ROI设置 ${selected.surface.target_roi??'未知'}（${basisNames[selected.surface.roi_basis]||'未知口径'}）；状态 ${selected.surface.running===true?'在投':selected.surface.running===false?'暂停':'未知'}；读取 ${date(selected.surface.captured_at)}。`:'';
  });
  root.querySelector('#browser-prepare').addEventListener('click',event=>action(event.currentTarget,async()=>{
    const select=root.querySelector('#browser-plan');if(select.value==='')throw new Error('请选择明确账户和计划');
    const choice=choices[Number(select.value)], actionName=root.querySelector('#browser-action').value;
    browserControlDraft=await api('/api/browser-actions/create',{worker_id:choice.worker.worker_id,epoch:choice.worker.epoch,
      account_id:choice.surface.account_id,plan_id:choice.surface.plan_id,action:actionName,
      after:actionName==='pause_plan'?false:root.querySelector('#browser-after').value.trim(),reason:root.querySelector('#browser-reason').value.trim()});
    showBrowserApproval(browserControlDraft);
  }));
}
async function loadBrowserControl(){
  if(!browserControls())return;
  try{browserControlState=await api('/api/browser-actions');renderBrowserControl();}
  catch(error){browserControls().textContent='调控队列读取失败：'+error.message;}
}
document.querySelector('#browser-control-refresh')?.addEventListener('click',event=>action(event.currentTarget,loadBrowserControl));
browserControls()?.addEventListener('click',event=>{
  const reconcile=event.target.closest('[data-browser-reconcile]');
  if(reconcile)return action(reconcile,async()=>{
    await loadBrowserControl();
    const a=browserControlState?.actions.find(a=>a.id===reconcile.dataset.browserReconcile&&a.state==='unknown');
    if(!a)throw new Error('此动作已不处于结果不明状态，请查看最新记录');
    const choices=browserControlState.workers.filter(w=>w.armed&&w.worker_id===a.worker_id).flatMap(w=>w.surfaces.filter(s=>s.account_id===a.intent.account_id&&s.plan_id===a.intent.plan_id).map(s=>({worker:w,surface:s})));
    if(choices.length!==1)throw new Error('请在原Chrome开启执行连接，刷新原计划页并等待当前设置上报');
    const d=await api('/api/browser-actions/reconcile-preview',{id:a.id,worker_id:choices[0].worker.worker_id,epoch:choices[0].worker.epoch});
    showBrowserReconciliation(d);
  });
  const reconcileConfirm=event.target.closest('#browser-reconcile-confirm');
  if(reconcileConfirm)return action(reconcileConfirm,async()=>{
    if(!document.querySelector('#browser-reconcile-check')?.checked||!browserControlReconciliation)throw new Error('先核对当前设置并勾选确认');
    const d=browserControlReconciliation;
    await api('/api/browser-actions/reconcile-confirm',{id:d.id,reconciliation_digest:d.reconciliation_digest,confirmed:true});
    browserControlReconciliation=null;await loadBrowserControl();notice('当前设置已独立核对，原动作不重试；此计划冷却1小时。');
  });
  const review=event.target.closest('[data-browser-review]');
  if(review){const draft=browserControlState?.actions.find(a=>a.id===review.dataset.browserReview&&a.state==='draft');if(draft)showBrowserApproval(draft);return;}
  const cancel=event.target.closest('[data-browser-cancel]');
  if(cancel)return action(cancel,async()=>{await api('/api/browser-actions/cancel',{id:cancel.dataset.browserCancel});browserControlDraft=null;await loadBrowserControl();});
  const confirm=event.target.closest('#browser-confirm');if(!confirm)return;
  action(confirm,async()=>{
    if(!document.querySelector('#browser-confirm-check')?.checked||!browserControlDraft)throw new Error('先核对这一条并勾选确认');
    const d=browserControlDraft;
    await api('/api/browser-actions/approve',{id:d.id,intent_digest:d.intent_digest,confirmed:true});
    browserControlDraft=null;await loadBrowserControl();notice('仅此条已确认，等待浏览器再次核对并执行。');
  });
});
document.querySelector('#recommendations')?.addEventListener('click',event=>{
  const button=event.target.closest('[data-browser-recommendation]');if(!button)return;
  action(button,async()=>{
    const recommendation=state?.evaluation.recommendations.find(r=>r.id===button.dataset.browserRecommendation);
    if(!recommendation)throw new Error('当前规则建议已变化');
    await loadBrowserControl();
    const choices=browserControlChoices().filter(c=>c.surface.account_id===recommendation.account_id&&c.surface.plan_id===recommendation.entity_id&&c.surface.actions.includes(recommendation.action));
    if(choices.length!==1)throw new Error('需要唯一且已核验的浏览器计划执行页面');
    const d=await api('/api/browser-actions/create-from-recommendation',{recommendation_id:recommendation.id,worker_id:choices[0].worker.worker_id,epoch:choices[0].worker.epoch});
    showBrowserApproval(d);browserControls().scrollIntoView({behavior:'smooth',block:'center'});
  });
});
loadBrowserControl();
