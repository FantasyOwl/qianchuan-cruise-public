'use strict';
(function(global){
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const clock=v=>Number.isFinite(Date.parse(v))?new Date(v).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):'待设置';
 const states={draft:'待核对具体排程',approved:'已确认，等待到点执行',claimed:'到点核对原生页面',submitting:'已发出一次提交许可',verified:'刷新后控量设置已核验',unknown:'提交结果未核实，已阻止后续提交',blocked:'起量／调速占用，未执行控量',rejected:'核对未通过，未执行',expired:'排程核对单已过期',missed:'错过执行窗口，未补执行',cancelled:'已取消'};
 function trigger(value){const ms=Date.parse(value+'+08:00');return Number.isFinite(ms)?new Date(ms-20*60000).toISOString():null;}
 class LiveClosePanel{
  constructor(app){this.app=app;this.root=app.doc.querySelector('#live-close-panel');this.inputs=new Map();this.checks=new Map();this.bind();}
  values(){const id=this.app.accountId;if(!this.inputs.has(id))this.inputs.set(id,{plan:'',off:'',budget:'',minutes:'30'});return this.inputs.get(id);}
  choices(){return (this.app.state?.live_close?.workers||[]).filter(w=>w.current).flatMap(w=>(w.surfaces||[]).filter(s=>s.account_id===this.app.accountId).map(s=>({worker:w,surface:s,key:w.worker_id+':'+s.plan_id})));}
  render(){
   if(!this.root)return;const a=this.app.account();this.root.hidden=a?.scene!=='live'||Boolean(this.app.state?.monitoring?.accounts?.some(m=>m.account_id===a?.id&&m.backend==='official_api')); if(this.root.hidden)return;
   const f=this.values(),choices=this.choices(),selected=choices.find(c=>c.key===f.plan),actions=(this.app.state?.live_close?.actions||[]).filter(r=>r.intent.account_id===a.id);
   // Only replace the form when changing accounts; polling updates status without destroying input focus.
   if(this.accountId!==a.id){this.accountId=a.id;this.root.innerHTML=`<div class="section-heading"><div><h2>下播前控量</h2><p>按本户具体下播时间，提前20分钟执行“一键控量”。提前确认排程，到点执行一次。</p></div><span class="state-tag">北京时间</span></div><div class="form-grid"><label>直播大计划<select id="close-plan"></select></label><label>下播日期与时间<input id="close-off" type="datetime-local" step="60"></label><label>控量总预算（元）<input id="close-budget" type="number" min="0.01" step="0.01" placeholder="请单独设置"></label><label>控量持续时长<select id="close-minutes"><option value="30">30分钟（0.5小时）</option><option value="60">60分钟（1小时）</option></select></label></div><p id="close-preview" class="muted"></p><p id="close-source" class="capability-note"></p><div class="button-row"><button id="close-read-home" class="secondary">读取本户直播首页入口</button><button id="close-prepare">准备具体排程</button></div><p class="muted">起量仍在进行时保留原任务并报告冲突。执行时须保持本机服务、原Chrome与执行开关开启；错过2分钟执行窗口不补执行。</p><div id="close-actions"></div>`;
    this.root.querySelector('#close-off').value=f.off;this.root.querySelector('#close-budget').value=f.budget;this.root.querySelector('#close-minutes').value=f.minutes;
   }
   const select=this.root.querySelector('#close-plan'),options='<option value="">选择已核验的直播首页计划</option>'+choices.map(c=>`<option value="${esc(c.key)}">计划 ${esc(c.surface.plan_id)}${c.surface.aweme_id?' · 抖音号 '+esc(c.surface.aweme_id):''}${c.surface.busy?' · 调速占用／入口不可用':''}</option>`).join('');
   if(select.innerHTML!==options){select.innerHTML=options;select.value=f.plan;}this.summary();
   const workers=(this.app.state?.live_close?.workers||[]).filter(w=>w.current);
   this.root.querySelector('#close-source').textContent=!choices.length?(workers.length&&!workers.some(w=>w.armed)?'新版扩展已接通，但当前执行模式关闭。只读刷新仍可使用；准备和确认排程需在扩展开启执行。':workers.some(w=>w.armed)?'执行已开启，但本户直播首页计划与一键调速入口尚未核验，暂不能准备排程。':'等待读取本户直播首页的一键调速入口，并开启扩展执行开关。'):!workers.some(w=>w.armed)?'本户直播首页与计划已连接；当前执行模式关闭，计划仍可查看，暂不能准备或确认排程。':selected?.surface.busy?'本计划当前有起量／调速任务。可预先安排未来控量；到点仍占用则报告冲突，不会结束原任务。':'已核验本户直播首页；控量预算、日期和时长须单独核对。';
   this.root.querySelector('#close-prepare').disabled=!selected?.worker.armed||!f.off||!f.budget||!trigger(f.off);
   const check=workers.flatMap(w=>w.view_checks||[]).find(c=>c.account_id===a.id),notes={home_plan_identity_missing:'已找到一键调速入口，但尚未取得明确计划ID；不会把抖音号ID当成计划ID。',home_speed_entry_missing:'当前首页未读到一键调速入口，暂不能准备排程。',editor_open:'当前首页有编辑窗口，保留输入，未读取或提交控量。',home_identity_missing:'首页账户身份尚未核验，暂不能准备排程。',home_read_failed:'首页读取未完成，等待原Chrome下一次回传。'};
   if(!choices.length&&workers.some(w=>w.armed)&&check&&notes[check.code])this.root.querySelector('#close-source').textContent=notes[check.code];
   const box=this.root.querySelector('#close-actions');box.innerHTML=actions.map(r=>{
    const v=r.intent,valid=r.state==='draft'&&Date.parse(r.expires_at)>Date.now(),armed=workers.some(w=>w.armed&&w.worker_id===r.worker_id&&w.epoch===r.epoch);if(!valid||!armed)this.checks.delete(r.id);
    return `<article class="proposal close-schedule"><strong>${esc(states[r.state]||r.state)}</strong><p>账户 ${esc(v.account_id)} · 计划 ${esc(v.plan_id)}${v.aweme_id?' · 抖音号 '+esc(v.aweme_id):''}</p><p>下播 ${esc(clock(v.off_at))} → 控量开始 ${esc(clock(v.trigger_at))}</p><p>总预算 ${esc(v.budget)} 元 · 持续 ${esc(v.duration_minutes)} 分钟 · 预计结束 ${esc(clock(new Date(Date.parse(v.trigger_at)+v.duration_minutes*60000).toISOString()))}</p><p class="muted">到点占用时保留原起量任务，报告冲突。${valid?'本核对单有效至 '+esc(clock(r.expires_at)):'排程只执行一次；未核验结果不重试。'}</p>${r.receipt?.message?`<p role="status">${esc(r.receipt.message)}</p>`:''}${valid?`<label class="inline-check"><input type="checkbox" data-close-check="${esc(r.id)}" ${this.checks.get(r.id)?'checked':''}>我已核对本户计划、抖音号、下播时间、控量预算与时长，确认到点执行</label><div class="button-row"><button data-close-approve="${esc(r.id)}" ${this.checks.get(r.id)&&armed?'':'disabled'}>确认排程，到点执行</button></div>`:''}${['draft','approved'].includes(r.state)?`<button class="secondary" data-close-cancel="${esc(r.id)}">取消此排程</button>`:''}</article>`;
   }).join('')||'<p class="empty">本户尚无下播控量排程。填写参数、准备并确认后才会定时执行。</p>';
  }
  summary(){const f=this.values(),t=trigger(f.off),p=this.root?.querySelector('#close-preview');if(p)p.textContent=t?`下播 ${clock(f.off+'+08:00')} · 提前20分钟：${clock(t)} · 控量持续 ${f.minutes} 分钟`:'请设置具体下播日期和时间，支持跨午夜。';}
  bind(){
   this.root?.addEventListener('input',e=>this.edit(e));this.root?.addEventListener('change',e=>this.edit(e));
   this.root?.addEventListener('click',e=>{const button=e.target.closest('button');if(!button||button.disabled)return;this.app.task(button,async()=>{
    if(button.id==='close-read-home'){const r=await this.app.api('/api/cruise/close/read-home',{account_id:this.app.accountId});this.app.note(r.note);}
    else if(button.id==='close-prepare'){
     const f=this.values(),c=this.choices().find(c=>c.key===f.plan);if(!c||!c.worker.armed)throw Error('直播首页计划连接或执行开关未就绪，请核对当前状态');
     await this.app.api('/api/cruise/close/create',{worker_id:c.worker.worker_id,epoch:c.worker.epoch,account_id:this.app.accountId,plan_id:c.surface.plan_id,off_at:f.off+(f.off.length===16?':00':'')+'+08:00',budget:f.budget,duration_minutes:Number(f.minutes)});
     this.app.note('已准备下播控量排程，请逐项核对后确认。此时尚未安排执行。');
    }else if(button.dataset.closeApprove){
     const id=button.dataset.closeApprove,r=(this.app.state?.live_close?.actions||[]).find(r=>r.id===id&&r.intent.account_id===this.app.accountId);if(!r||!this.checks.get(id))throw Error('请先核对这条具体排程');
     await this.app.api('/api/cruise/close/approve',{id,intent_digest:r.intent_digest,confirmed:true});this.checks.delete(id);this.app.note('已确认具体排程，到点由原Chrome执行一次控量。');
    }else if(button.dataset.closeCancel){await this.app.api('/api/cruise/close/cancel',{id:button.dataset.closeCancel});this.checks.delete(button.dataset.closeCancel);}
    else return;await this.app.refresh();
   });});
  }
  edit(e){const fields={'close-plan':'plan','close-off':'off','close-budget':'budget','close-minutes':'minutes'};
   if(fields[e.target.id]){this.values()[fields[e.target.id]]=e.target.value;this.summary();const f=this.values();this.root.querySelector('#close-prepare').disabled=!this.choices().some(c=>c.key===f.plan&&c.worker.armed)||!f.off||!f.budget||!trigger(f.off);}
   if(e.target.dataset.closeCheck){const id=e.target.dataset.closeCheck;this.checks.set(id,e.target.checked);const button=[...this.root.querySelectorAll('[data-close-approve]')].find(b=>b.dataset.closeApprove===id);if(button){const r=(this.app.state?.live_close?.actions||[]).find(r=>r.id===id);button.disabled=!e.target.checked||!(this.app.state?.live_close?.workers||[]).some(w=>w.current&&w.armed&&w.worker_id===r?.worker_id&&w.epoch===r?.epoch);}}
  }
 }
 const api={LiveClosePanel,trigger};global.QianchuanLiveClose=api;if(typeof module==='object')module.exports=api;
})(typeof window==='object'?window:globalThis);
