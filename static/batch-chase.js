'use strict';
(function(global){
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const amount=v=>typeof v==='object'?JSON.stringify(v):String(v??'—');
 const deadline=v=>Number.isFinite(Date.parse(v))?new Date(v).toLocaleString('zh-CN',{hour12:false}):'未提供';
 const settings=v=>v.chase_type==='放量追投'?`放量追投 · 整批总预算 ${v.total_budget} 元 · 时长 ${v.control_duration} 小时`:
  `控成本追投 · 整批日预算 ${v.daily_budget} 元 · ${v.optimization_target} · 出价/倍率 ${v.bid_value}`;
 const repeatMode=v=>v?.allow_running_materials===true?'允许调控中素材再次追投':'不重复追投调控中素材';
 class BatchChasePanel{
  constructor(app){this.app=app;this.root=app.doc.querySelector('#batch-chase-panel');this.reviewId=null;this.checked=false;this.bind();}
  render(){
   if(!this.root)return;const a=this.app.account(),state=this.app.state?.batch_control;
   if(this.accountId!==a?.id){this.accountId=a?.id;this.reviewId=null;this.checked=false;}
   this.root.hidden=a?.scene!=='live'||this.app.feature!=='f5'||this.app.state?.monitoring?.accounts?.some(m=>m.account_id===a?.id&&m.backend==='official_api');if(this.root.hidden)return;
   const previews=(state?.previews||[]).filter(p=>p.account_id===a.id),actions=(state?.actions||[]).filter(p=>p.intent.account_id===a.id);
   const checks=(state?.view_checks||[]).filter(p=>p.account_id===a.id);
   const viewStates={view_configured:'原生分页、排序及素材行已核验',view_100_option_unavailable:'未找到唯一的100条选项',view_page_size_not_applied:'分页选择尚未生效',view_sort_not_applied:'消耗降序尚未生效',view_active_page_unavailable:'尚未核验真实当前页码',view_target_page_unavailable:'尚未找到目标页码按钮',view_rows_not_verified:'等待素材行加载完成',view_editor_or_selection_open:'已有编辑窗口或手动选择，保留输入',view_control_failed:'原生控件操作未完成'};
   const candidates=previews.flatMap(p=>p.batches.map(c=>({...c,worker_id:p.worker_id,epoch:p.epoch,armed:p.armed})));
   const connected=(state?.workers||[]).some(w=>w.current&&w.armed);
   const navigation=(this.app.state?.logs||[]).find(l=>l.account_id===a.id&&l.event==='collection_navigation'&&l.detail?.lane==='auto');
   const readProblem=!previews.length&&navigation?.detail?.ok===false&&Date.parse(navigation.at)>=Date.parse(a.lanes?.auto?.last_at||'1970-01-01')?navigation.detail.message:null;
   const stages={draft:'待你确认',expired:'审核已过期，未执行',cancelled:'已取消',rejected:'核验未通过',approved:'已确认待执行',claimed:'执行核对中',submitting:'已进入单次提交',unknown:'创建结果待核验',verified:'已核验创建'};
   const review=actions.find(a=>a.id===this.reviewId&&['draft','expired'].includes(a.state));
   const reviewExpired=review&&(review.state==='expired'||review.expires_at&&Date.parse(review.expires_at)<=Date.now());
   if(reviewExpired)this.checked=false;
   this.root.innerHTML=`<h2>近几天素材 · 批量追投</h2><p>近3–5天含今天，按整体消耗降序，每页100条。每批1–20条；预算为整批任务预算，不乘素材数量。消耗上限留空时，本区按不限制上限筛选。</p>
    <p class="capability-note">执行连接：${connected?'已开启，每一批仍须你单独确认':'关闭，当前仅扫描；不会准备或提交追投'}。本户批次记录 ${actions.length} 条，已核验创建 ${actions.filter(a=>a.state==='verified').length} 条。</p>
    <p class="capability-note">确认单保留15分钟供你核对；确认时重新检查最新数据。过期记录会保留，可重新核验并准备。</p>
    <p class="capability-note">本户已保存选择：${esc(repeatMode(a.profile?.features?.f5))}。开启后每批仍单独确认，待执行或结果不明的同素材不会重复下单。</p>
    ${readProblem?`<p class="capability-note" role="status">本次素材读取未完成：${esc(readProblem)}。处理后点“扫描本户近几天素材”重读；本次未创建追投。</p>`:''}
    <p>${previews.length?'已收到指定范围素材页':'等待原Chrome素材读取；打开的编辑窗口会保留，程序暂不切页。'}</p>
    ${checks.map(c=>`<p class="capability-note">${esc(viewStates[c.code]||'等待页面读取条件')}；每页条数 ${esc(c.before?.page_size??'未确认')} → ${esc(c.after?.page_size??'未确认')}；实际第 ${esc(c.after?.page??'未确认')} 页；已读取 ${esc(c.after?.rows??0)} 条。</p>`).join('')}
    <button id="batch-read" class="secondary" ${this.app.dirty?'disabled':''}>扫描本户近几天素材</button>
    ${previews.map(p=>`<p>${esc(p.date_start)} ～ ${esc(p.date_end)} · 第${esc(p.page)}页 · 读取${esc(p.scanned||0)}条 · 每批${esc(p.batch_size_min??p.batch_size)}–${esc(p.batch_size_max??p.batch_size)}条</p>${p.notes.map(n=>`<p class="capability-note">${esc(n)}</p>`).join('')}`).join('')}
    ${candidates.map(c=>`<article class="proposal"><div><strong>一批 ${c.material_ids.length} 条素材</strong><p>母计划 ${esc(c.plan_id)} · ${esc(c.date_start)} ～ ${esc(c.date_end)}</p><p>素材ID：${c.material_ids.map(esc).join('、')}</p><p>${c.materials.map(r=>`${esc(r.name)}：消耗${esc(r.spend)}，整体支付ROI ${esc(r.roi)}，状态${esc(r.chase_status||'未提供')}`).join('<br>')}</p><p>整批设置：${esc(settings(c.after))}</p><p>本批选择：${esc(repeatMode(c))}</p></div><button data-batch-prepare="${esc(c.key)}" data-batch-worker="${esc(c.worker_id)}" data-batch-epoch="${esc(c.epoch)}" data-batch-plan="${esc(c.plan_id)}" ${this.app.dirty||!c.armed?'disabled':''}>准备这一批</button></article>`).join('')||'<p>当前没有凑齐的一批素材。</p>'}
    ${review?`<aside class="proposal" aria-label="核对这一批追投"><div><strong>${reviewExpired?'这批确认单已过期':'确认创建这一批追投'}</strong><p>账户 ${esc(review.intent.account_id)} · 母计划 ${esc(review.intent.plan_id)}</p><p>扫描范围 ${esc(review.intent.date_start)} ～ ${esc(review.intent.date_end)}</p><p>完整素材ID：${review.intent.material_ids.map(esc).join('、')}</p><p>整批设置：${esc(settings(review.intent.after))}</p><p>本批选择：${esc(repeatMode(review.intent))}</p><p>任务名 ${esc(review.intent.task_name)}</p><p>${review.intent.after.chase_type==='放量追投'?'千川页面提示：放量追投不支持成本保障。':'控成本的保障资格以平台核验为准；本单不认证赔付。'}</p>${reviewExpired?`<p role="status">审核已过期，未执行。重新核验会生成新单，仍需你再次确认。</p><button data-batch-renew="${esc(review.id)}">重新核验并准备</button>`:`<p>审核截止：${esc(deadline(review.expires_at))}。点击确认后重新核验，并另计2分钟执行许可。</p><label><input id="batch-confirm-check" type="checkbox" ${this.checked?'checked':''}>我已核对这一批全部素材ID、模式和整批金额</label><button id="batch-approve" ${this.checked?'':'disabled'}>确认并执行这一批</button><button data-batch-cancel="${esc(review.id)}" class="secondary">取消这一批</button>`}</div></aside>`:''}
    <h3>批量追投记录</h3>${actions.map(a=>`<p>${esc(a.id.slice(0,8))} · ${esc(stages[a.state]||a.state)} · ${a.intent.material_ids.length}条 · 素材ID ${a.intent.material_ids.map(esc).join('、')}${a.state==='draft'?` · 审核截止 ${esc(deadline(a.expires_at))}`:''}${a.state==='unknown'?' · 创建结果尚未核验，已停止继续提交，不会重试。':''}${a.state==='unknown'?` <button data-batch-recheck="${esc(a.id)}">重新读取创建结果</button>`:''}${['draft','expired'].includes(a.state)?` <button data-batch-review="${esc(a.id)}">${a.state==='expired'?'查看过期单':'核对这一批'}</button>`:''}</p>`).join('')||'<p>暂无批量调控单。扫描不会创建投放。</p>'}`;
  }
  bind(){
   if(!this.root)return;
   this.root.addEventListener('change',e=>{if(e.target.id==='batch-confirm-check'){this.checked=e.target.checked;this.root.querySelector('#batch-approve').disabled=!this.checked;}});
   this.root.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
    if(b.dataset.batchReview){this.reviewId=b.dataset.batchReview;this.checked=false;this.render();return;}
    void this.app.task(b,async()=>{
     if(b.id==='batch-read'){
      if(this.app.dirty)throw Error('先保存本户参数');await this.app.api('/api/cruise/scan',{account_id:this.app.accountId,lane:'auto'});
      this.app.note('已请求近几天素材扫描，原Chrome按每页100条、消耗降序读取；不提交投放。');
     }else if(b.dataset.batchPrepare){
      if(this.app.dirty)throw Error('先保存本户参数');
      const d=await this.app.api('/api/cruise/batch/create',{worker_id:b.dataset.batchWorker,epoch:b.dataset.batchEpoch,account_id:this.app.accountId,plan_id:b.dataset.batchPlan,candidate_key:b.dataset.batchPrepare});
      this.reviewId=d.id;this.checked=false;
     }else if(b.dataset.batchRenew){
      if(this.app.dirty)throw Error('先保存本户参数');
      this.checked=false;
      const d=await this.app.api('/api/cruise/batch/renew',{id:b.dataset.batchRenew});
      this.reviewId=d.id;this.app.note('已按最新数据重新准备，尚未确认执行；请核对新确认单。');
     }else if(b.id==='batch-approve'){
      if(this.app.dirty)throw Error('规则草稿尚未保存，请重新核对');
      const d=this.app.state.batch_control.actions.find(a=>a.id===this.reviewId);
      if(!this.checked||!d||d.state!=='draft')throw Error('请重新核对这一批');
      await this.app.api('/api/cruise/batch/approve',{id:d.id,intent_digest:d.intent_digest,confirmed:true});this.checked=false;this.reviewId=null;
     }else if(b.dataset.batchRecheck){await this.app.api('/api/cruise/batch/recheck',{id:b.dataset.batchRecheck});this.app.note('已请求只读核验新任务；不会重新创建。');}else if(b.dataset.batchCancel){await this.app.api('/api/cruise/batch/cancel',{id:b.dataset.batchCancel});this.reviewId=null;this.checked=false;}
     await this.app.refresh();
    });
   });
  }
 }
 global.QianchuanBatch={BatchChasePanel};if(typeof module==='object')module.exports={BatchChasePanel};
})(typeof window==='object'?window:globalThis);
