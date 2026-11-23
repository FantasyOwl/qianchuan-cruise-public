/* Reimplementation of the original cruise's task operations. No browser IO,
 * platform API, credentials or heuristic cross-row fallback. The caller owns
 * approval, document binding, reload and independent readback. */
async function cruiseTaskDom(phase, request) {
  const identity=request?.identity,intent=request?.intent,key='__qc_cruise_task_editor_v1';
  const txt=e=>(e?.innerText??e?.textContent??'').trim();
  const shown=e=>Boolean(e?.isConnected&&e.getClientRects().length&&getComputedStyle(e).display!=='none'&&getComputedStyle(e).visibility!=='hidden');
  const usable=e=>Boolean(e&&!e.disabled&&e.getAttribute('aria-disabled')!=='true');
  const fail=code=>({ok:false,code,submitted:false});
  const canon=(v,zero=false)=>{
    const s=String(v??'').trim();if(!/^(?:\d+|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d{1,2})?$/.test(s))return null;
    const [a,b='']=s.replaceAll(',','').split('.'),n=BigInt(a)*100n+BigInt((b+'00').slice(0,2));
    return n<(zero?0n:1n)||n>100000000000000n?null:`${n/100n}.${String(n%100n).padStart(2,'0')}`;
  };
  const cents=v=>{const [a,b]=v.split('.');return BigInt(a)*100n+BigInt(b);};
  const fromCents=n=>`${n/100n}.${String(n%100n).padStart(2,'0')}`;
  const minutes=v=>{const n=canon(v);return n?fromCents(cents(n)*60n):null;};
  const hours=v=>fromCents(cents(v)/60n).replace(/\.00$/,'').replace(/(\.\d)0$/,'$1');
  const normalized=s=>s.replace(/[\s()（）]/g,'').replace(/元$/,'');
  let url;try{url=new URL(location.href);}catch{return fail('page_identity_invalid');}
  if(!identity||identity.scene!=='live'||identity.path!=='/uni-prom/detail'||typeof identity.group!=='string'||!identity.group||
      !/^\d{6,30}$/.test(identity.accountId||'')||!/^\d{6,30}$/.test(identity.planId||'')||
      url.origin!=='https://qianchuan.jinritemai.com'||url.pathname!==identity.path||
      url.searchParams.getAll('aavid').length!==1||url.searchParams.get('aavid')!==identity.accountId||
      url.searchParams.getAll('adId').length!==1||url.searchParams.get('adId')!==identity.planId)return fail('page_identity_changed');
  if(!new RegExp('(?:^|\\n)\\s*ID[：:]\\s*'+identity.accountId+'(?=\\s|$)','m').test(txt(document.body)))return fail('account_label_missing');
  const headers=[...document.querySelectorAll('.oc-promotion-key-info-bar')].filter(shown);
  const parentText=headers.length===1?txt(headers[0]):headers.length===0?txt(document.body):'';
  const parentIds=[...parentText.matchAll(/计划ID[：:]\s*(\d{6,30})(?=\s|$)/g)];
  if(parentIds.length!==1||parentIds[0][1]!==identity.planId)return fail('parent_plan_label_missing');
  const roots=()=>[...document.querySelectorAll('[role="dialog"],[role="alertdialog"],.ovui-modal__wrap,.oc-modal-wrap,.ovui-modal,.ovui-popover,[class*="popover-inner"],[class*="modal-content"]')]
    .filter(shown).filter(e=>txt(e)&&e.querySelector('input,button'));
  const buttons=(root,names)=>[...root.querySelectorAll('button,[role="button"]')].filter(e=>usable(e)&&names.includes(txt(e)||e.getAttribute('aria-label')));
  const edits=cell=>{
    if(!cell)return [];
    return [...new Set([...cell.querySelectorAll('.edit-icon,iconpark-icon[name="oc-icon-light-edit"],.oc-promotion-data-edit')]
      .map(e=>{const b=e.closest('button,[role="button"]');return b&&cell.contains(b)?b:e;}).filter(usable))];
  };
  function readRows(){
    const groups=new Map();
    for(const table of [...document.querySelectorAll('table')].filter(shown)){
      if(table.closest('[role="dialog"],[aria-modal="true"]'))continue;
      const host=table.closest('.ovui-table,.oc-table,[data-auto-id="oc-table"],[data-cruise-control-table]')||table;
      if(!groups.has(host))groups.set(host,[]);groups.get(host).push(table);
    }
    const result=new Map(),duplicates=new Set();
    for(const tables of groups.values()){
      const own=new Map(),all=new Map();
      for(const table of tables){
        for(const row of table.querySelectorAll('thead tr,tr')){
          if(!row.querySelector('th'))continue;
          const h=[...row.cells].map(c=>normalized(txt(c))),named=h.filter(Boolean);
          if(!h.includes('任务名称')||!h.includes('调控状态')||!h.includes('调控预算')||new Set(named).size!==named.length)continue;
          const serialized=JSON.stringify(h);all.set(serialized,h);
          if(own.has(table)&&JSON.stringify(own.get(table))!==serialized)own.set(table,null);else if(!own.has(table))own.set(table,h);
        }
      }
      if(all.size!==1)continue;
      for(const table of tables){
        const h=own.has(table)?own.get(table):[...all.values()][0];if(!h)continue;
        for(const row of table.rows){
          if(!shown(row)||row.querySelector('th')||row.cells.length!==h.length)continue;
          const cell=name=>row.cells[h.indexOf(name)]||null;
          const nameCell=cell('任务名称'),lines=txt(nameCell).split('\n').map(s=>s.trim()).filter(Boolean);
          const ids=[...txt(nameCell).matchAll(/(?:^|\n)\s*ID[：:]\s*(\d{6,30})\s*(?=\n|$)/g)];
          if(ids.length!==1)continue;
          const task=ids[0][1],at=lines.findIndex(s=>new RegExp('^ID[：:]\\s*'+task+'$').test(s));if(at<1)continue;
          const budgetCell=cell('调控预算'),durationCell=cell('调控时长小时');
          const budget=canon(txt(budgetCell).replace(/元$/,'').trim(),true);
          const duration=minutes(txt(durationCell).replace(/小时$/,'').trim());
          const status=txt(cell('调控状态')).split('\n')[0].trim();
          const known=({'调控中':'调控中','调控暂停':'已暂停','调控结束':'调控结束'})[status]||null;
          const running=known==='调控中'?true:known?false:null;
          const budgetEdits=edits(budgetCell),durationEdits=edits(durationCell);
          const selection=[...row.querySelectorAll('input[type="checkbox"]')].filter(usable);
          const pause=[...document.querySelectorAll('[data-auto-id="bar-groups-group-item-btn-pause"]')];
          const stops=buttons(row,['结束调控','终止调控']).filter(shown);
          const actions=[];
          if(budget!==null&&budgetEdits.length===1)actions.push('control_budget_up');
          if(duration!==null&&durationEdits.length===1)actions.push('control_duration_up');
          if(running===true&&selection.length===1&&pause.length===1)actions.push('control_pause');
          if(known!==null&&known!=='调控结束'&&stops.length===1)actions.push('control_stop');
          const metrics={};
          for(const [field,label] of Object.entries({spend:'调控消耗',roi:'调控支付ROI',payment_orders:'调控成交订单数',gmv:'调控成交金额',comprehensive_cost:'调控综合成本',net_roi:'调控净成交ROI',net_gmv:'调控净成交金额'})){
            const parsed=canon(txt(cell(label)),true);if(parsed!==null&&(field!=='payment_orders'||cents(parsed)%100n===0n))metrics[field]=Number(parsed);
          }
          if(metrics.payment_orders!==undefined)metrics.orders=metrics.payment_orders;
          const context={native_status:status,data_at:null,control_delivery_mode:({'控成本追投':'cost_control','放量追投':'volume'})[txt(cell('追投方式'))]||'unknown',
            observed_duration_mode:duration!==null?'fixed':txt(durationCell)==='长期投放'?'unlimited':'unknown'};
          if(duration!==null)context.observed_duration_hours=Number(duration)/60;
          const created=txt(cell('创建时间')).replace(/\s+/g,' ');
          if(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(created)&&Number.isFinite(Date.parse(created.replace(' ','T')+'+08:00')))context.created_at=created.replace(' ','T')+'+08:00';
          const material=txt(cell('素材')).match(/(?:^|\n)\s*素材ID[：:]\s*(\d{6,30})\s*(?=\n|$)/);if(material)context.material_id=material[1];
          const surface={target_kind:'control_task',account_id:identity.accountId,plan_id:identity.planId,task_id:task,
            name:lines[at-1].slice(0,200),budget,duration_minutes:duration,running,status:known,actions:actions.sort(),
            path:url.pathname,group:identity.group,captured_at:new Date().toISOString(),
            metric_basis:metrics.roi!==undefined?'control_payment':'unknown',metrics,context};
          if(result.has(task))duplicates.add(task);else result.set(task,{row,host:row.closest('.ovui-table,.oc-table,[data-auto-id="oc-table"],[data-cruise-control-table]')||table,
            budgetButton:budgetEdits[0],durationButton:durationEdits[0],checkbox:selection[0],pauseButton:pause[0],stopButton:stops[0],surface});
        }
      }
    }
    for(const id of duplicates)result.delete(id);
    return result;
  }
  const binding=s=>Object.fromEntries(Object.keys(s).filter(k=>!['captured_at','data_at','metrics','context','metric_basis','tab_id','document_id'].includes(k)).sort().map(k=>[k,s[k]]));
  const same=s=>JSON.stringify(binding(s))===JSON.stringify(binding(intent.surface));
  const current=()=>readRows().get(intent?.task_id);
  const rowSelection=()=>[...document.querySelectorAll('tbody input[type="checkbox"]')].filter(e=>e.checked);
  const pageStillSame=()=>{
    try{
      const fresh=new URL(location.href),h=[...document.querySelectorAll('.oc-promotion-key-info-bar')].filter(shown);
      const parent= h.length===1?txt(h[0]):h.length===0?txt(document.body):'';
      const ids=[...parent.matchAll(/计划ID[：:]\s*(\d{6,30})(?=\s|$)/g)];
      return fresh.origin===url.origin&&fresh.pathname===identity.path&&fresh.searchParams.getAll('aavid').length===1&&
        fresh.searchParams.get('aavid')===identity.accountId&&fresh.searchParams.getAll('adId').length===1&&fresh.searchParams.get('adId')===identity.planId&&
        new RegExp('(?:^|\\n)\\s*ID[：:]\\s*'+identity.accountId+'(?=\\s|$)','m').test(txt(document.body))&&ids.length===1&&ids[0][1]===identity.planId;
    }catch{return false;}
  };
  if(phase==='inspect'){
    if(roots().length)return {ok:true,surfaces:[],code:'editor_open',control_table_visible:false};
    const visibleTable=[...document.querySelectorAll('table')].filter(shown).some(t=>{
      const names=[...t.querySelectorAll('th')].map(h=>normalized(txt(h)));return ['任务名称','调控状态','调控预算'].every(h=>names.includes(h));
    });
    return {ok:true,surfaces:[...readRows().values()].map(r=>r.surface),control_table_visible:visibleTable,code:visibleTable?'task_table_visible':'task_table_not_visible'};
  }
  const fields={control_budget_up:'budget',control_duration_up:'duration_minutes',control_pause:'running',control_stop:'status'};
  if(!intent||intent.target_kind!=='control_task'||intent.account_id!==identity.accountId||intent.plan_id!==identity.planId||
      !/^\d{6,30}$/.test(intent.task_id||'')||!request.id||!request.intent_digest||fields[intent.action]!==intent.field||!intent.surface)return fail('command_invalid');
  if(phase==='abort'){
    const p=globalThis[key];if(p&&(p.id!==request.id||p.digest!==request.intent_digest))return fail('prepared_identity_changed');
    clear();return {ok:true,submitted:false};
  }
  let item=current();if(!item||!same(item.surface)||!item.surface.actions.includes(intent.action))return fail('setting_changed');
  if(intent.before!==item.surface[intent.field])return fail('before_value_changed');
  if(intent.field==='budget'||intent.field==='duration_minutes'){
    const after=canon(intent.after);if(after!==intent.after||cents(after)<=cents(intent.before))return fail('target_value_invalid');
    if(intent.field==='duration_minutes'&&cents(after)%3000n!==0n)return fail('duration_requires_half_hours');
  }else if(intent.field==='running'&&(intent.before!==true||intent.after!==false))return fail('target_value_invalid');
  else if(intent.field==='status'&&intent.after!=='调控结束')return fail('target_value_invalid');
  function form(){
    const found=[];
    for(const root of roots()){
      const content=txt(root),duration=intent.field==='duration_minutes';
      if(duration?!/调控时长/.test(content):!/调控(?:日)?预算/.test(content))continue;
      const inputs=[...root.querySelectorAll('input')].filter(e=>shown(e)&&usable(e)&&!e.readOnly&&['text','number','tel',''].includes(e.type));
      if(inputs.length!==1)continue;
      if(duration&&!/小时/.test(content+' '+(inputs[0].getAttribute('aria-label')||'')))continue;
      const value=duration?minutes(inputs[0].value):canon(inputs[0].value,true);
      const confirms=buttons(root,['确定','确认','保存']).filter(shown);
      if(value===intent.before&&confirms.length===1)found.push({root,input:inputs[0],button:confirms[0],rawBefore:inputs[0].value});
    }
    const unique=found.filter((f,n)=>found.findIndex(v=>v.input===f.input)===n);return unique.length===1?unique[0]:null;
  }
  function clear(restore=true){
    const p=globalThis[key];delete globalThis[key];if(!p)return;
    if(restore&&p.input?.isConnected){
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(p.input,p.rawBefore);
      p.input.dispatchEvent(new Event('input',{bubbles:true}));p.input.dispatchEvent(new Event('change',{bubbles:true}));
    }
    if(p.root?.isConnected){const cancel=buttons(p.root,['取消']).filter(shown);if(cancel.length===1)cancel[0].click();}
    if(p.selectedByUs&&p.checkbox?.isConnected&&p.checkbox.checked)p.checkbox.click();
  }
  if(phase==='prepare'){
    if(roots().length||globalThis[key])return fail('another_editor_open');
    if(!Number.isFinite(Date.parse(request.expires_at))||Date.now()>=Date.parse(request.expires_at))return fail('approval_expired');
    const base={id:request.id,digest:request.intent_digest,nonce:crypto.randomUUID(),at:Date.now(),submitted:false};
    if(intent.action==='control_pause'){
      if(rowSelection(item).length)return fail('another_task_selected');
      item.checkbox.click();
      if(!item.checkbox.checked||rowSelection(item).length!==1||rowSelection(item)[0]!==item.checkbox||!usable(item.pauseButton)){
        if(item.checkbox.checked)item.checkbox.click();return fail('exclusive_selection_failed');
      }
      globalThis[key]={...base,button:item.pauseButton,checkbox:item.checkbox,selectedByUs:true};
    }else if(intent.action==='control_stop')globalThis[key]={...base,button:item.stopButton};
    else{
      const entry=intent.field==='budget'?item.budgetButton:item.durationButton;if(!entry||!usable(entry))return fail('entry_not_supported');
      entry.click();let f=null;
      for(let n=0;n<25&&!f;n++){f=form();if(!f)await new Promise(resolve=>setTimeout(resolve,100));}
      if(!f)return fail('editor_not_supported');
      globalThis[key]={...base,...f};
    }
    item=current();if(!item||!same(item.surface)){clear();return fail('setting_changed');}
    return {ok:true,prepared:true,submit_token:base.nonce,surface:item.surface};
  }
  if(phase!=='submit')return fail('phase_invalid');
  const p=globalThis[key];
  if(!p||p.id!==request.id||p.digest!==request.intent_digest||p.nonce!==request.submit_token||p.submitted||
      Date.now()-p.at>60000||!Number.isFinite(Date.parse(request.expires_at))||Date.now()>=Date.parse(request.expires_at))return fail('prepared_editor_expired');
  if(!p.button?.isConnected||!usable(p.button))return fail('editor_changed');
  if(intent.action==='control_pause'&&(!p.checkbox.checked||rowSelection(item).length!==1||rowSelection(item)[0]!==p.checkbox))return fail('exclusive_selection_changed');
  if(p.input){
    if(!shown(p.root)||!shown(p.input)||!p.root.contains(p.button)||p.input.value!==p.rawBefore)return fail('editor_changed');
    const value=intent.field==='duration_minutes'?hours(intent.after):intent.after;
    for(const a of ['min','max']){const bound=p.input.getAttribute(a)||p.input.getAttribute('aria-value'+a);
      if(bound!==null&&bound!==''&&Number.isFinite(Number(bound))&&(a==='min'?Number(value)<Number(bound):Number(value)>Number(bound)))return fail('input_range_rejected');}
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(p.input,value);
    p.input.dispatchEvent(new Event('input',{bubbles:true}));p.input.dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise(resolve=>setTimeout(resolve,50));
    if(!p.input.isConnected||p.input.value!==value||!p.input.checkValidity()){clear();return fail('input_validation_rejected');}
  }
  let permit;try{permit=await chrome.runtime.sendMessage({action:'cruise-final-check',id:request.id,nonce:p.nonce});}
  catch{clear();return fail('final_permission_missing');}
  item=current();
  const expectedInput=p.input?(intent.field==='duration_minutes'?hours(intent.after):intent.after):null;
  if(permit?.allow!==true||!pageStillSame()||Date.now()>=Date.parse(request.expires_at)||Date.now()-p.at>60000||
      !item||!same(item.surface)||!p.button.isConnected||!usable(p.button)||
      p.input&&(!p.root.isConnected||!shown(p.root)||!p.root.contains(p.input)||!p.root.contains(p.button)||p.input.value!==expectedInput||!p.input.checkValidity())||
      intent.action==='control_pause'&&(!p.checkbox.checked||rowSelection(item).length!==1||rowSelection(item)[0]!==p.checkbox)){
    clear();return fail('final_permission_rejected');
  }
  p.submitted=true;
  try{
    p.button.click();
    if(intent.action==='control_pause'||intent.action==='control_stop'){
      await new Promise(resolve=>setTimeout(resolve,100));
      const candidates=roots().filter(r=>(intent.action==='control_pause'?/暂停/.test(txt(r)):/结束调控|终止调控/.test(txt(r))))
        .map(root=>({root,confirms:buttons(root,['确定','确认']).filter(shown)})).filter(x=>x.confirms.length===1);
      const unique=candidates.filter((c,n)=>candidates.findIndex(x=>x.confirms[0]===c.confirms[0])===n);
      // Only a unique confirmation for the exclusively selected task is known.
      if(unique.length===1&&unique.every(x=>!x.root.querySelector('input:not([type="checkbox"])'))){
        item=current();const selected=intent.action!=='control_pause'||item&&p.checkbox.checked&&rowSelection(item).length===1&&rowSelection(item)[0]===p.checkbox;
        if(pageStillSame()&&Date.now()<Date.parse(request.expires_at)&&item&&same(item.surface)&&selected)unique[0].confirms[0].click();
      }
    }
  }catch{delete globalThis[key];return {ok:false,submitted:true,code:'submit_result_unknown'};}
  delete globalThis[key];
  await new Promise(resolve=>setTimeout(resolve,100));
  return {ok:true,submitted:true,editor_open:roots().length>0,code:'submit_attempted_requires_reload_readback'};
}
if(typeof module!=='undefined')module.exports={cruiseTaskDom};
