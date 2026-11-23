/* Main-plan DOM adapter. Executed only in this extension's ISOLATED world.
 * No fetch, cookies, storage or page application state. Preparation opens an
 * editor; only a one-use worker authorization may click its final control.
 */
async function browserPlanDom(phase, request) {
  const identity=request?.identity, intent=request?.intent, key='__qc_confirmed_plan_editor_v1';
  const text=e=>(e?.innerText??e?.textContent??'').trim();
  const shown=e=>Boolean(e?.isConnected&&e.getClientRects().length&&getComputedStyle(e).display!=='none'&&getComputedStyle(e).visibility!=='hidden');
  const usable=e=>Boolean(e&&!e.disabled&&e.getAttribute('aria-disabled')!=='true');
  const amount=value=>{
    const raw=String(value??'').trim();
    if(!/^(?:\d+|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d{1,2})?$/.test(raw))return null;
    const s=raw.replaceAll(',','');
    const [a,b='']=s.split('.');const cents=BigInt(a)*100n+BigInt((b+'00').slice(0,2));
    return cents>0n&&cents<=100000000000000n?`${cents/100n}.${String(cents%100n).padStart(2,'0')}`:null;
  };
  const labels={overall_payment:'整体支付ROI目标',net:'净成交ROI目标',marketing:'综合营销ROI目标'};
  const editorRoots=()=>[...document.querySelectorAll('[role="dialog"],[role="alertdialog"],[class*="popover-inner"],[class*="modal-content"],.ovui-popover:not(.ovui-popover--hide)')].filter(shown).filter(e=>text(e));
  const fail=code=>({ok:false,code,submitted:false});
  let url;
  try{url=new URL(location.href);}catch{return fail('page_identity_invalid');}
  if(!identity||url.origin!=='https://qianchuan.jinritemai.com'||url.searchParams.getAll('aavid').length!==1||
      url.searchParams.get('aavid')!==identity.accountId||url.pathname!==identity.path||
      !['/overall-prom','/uni-prom','/uni-prom/detail'].includes(url.pathname))return fail('page_identity_changed');
  if(!new RegExp('(?:^|\\n)\\s*ID[：:]\\s*'+identity.accountId+'(?=\\s|$)','m').test(text(document.body)))return fail('account_label_missing');

  if(phase==='inspect_navigation'){
    // A text-only hover tip is not an unfinished edit. Keep the stricter
    // execution/editor inventory below unchanged; this check only permits
    // read-only navigation of the already verified account tab.
    const blocking=[...document.querySelectorAll('.ovui-modal,.ovui-drawer,[role="dialog"],[role="alertdialog"],[aria-modal="true"],[class*="popover-inner"],[class*="modal-content"],.ovui-popover:not(.ovui-popover--hide)')]
      .filter(shown).filter(e=>{
        if(e.matches('.ovui-modal,.ovui-drawer,[role="dialog"],[role="alertdialog"],[aria-modal="true"],[class*="modal-content"]'))return true;
        return [...e.querySelectorAll('input,textarea,select,[contenteditable="true"],button,[role="button"],[role="grid"],[role="menu"]')].some(shown);
      });
    return {ok:true,code:blocking.length?'editor_open':'navigation_ready'};
  }

  function readRow(anchor){
    const id=anchor.id.match(/^ad-name-(\d{6,20})$/)?.[1],row=anchor.closest('tr');
    if(!id||!row||!shown(row)||row.closest('[role="dialog"],[aria-modal="true"]'))return null;
    const planLines=text(anchor).split('\n').map(s=>s.trim()).filter(Boolean);
    const at=planLines.findIndex(s=>new RegExp('^ID[：:]\\s*'+id+'$').test(s));
    if(at<1)return null;
    const cells=[...row.cells];
    const settings=cells.filter(c=>/每日预算/.test(text(c))&&/ROI目标/.test(text(c)));
    if(settings.length!==1)return null;
    const setting=settings[0],content=text(setting),budgets=[...content.matchAll(/每日预算\s*([\d,.]+)\s*元/g)];
    const budget=budgets.length===1?amount(budgets[0][1]):null,goals=[];
    for(const [basis,label] of Object.entries(labels))for(const m of content.matchAll(new RegExp(label+'\\s*(\\d+(?:\\.\\d+)?)','g')))goals.push({basis,value:amount(m[1])});
    const goal=goals.length===1&&goals[0].value?goals[0]:null;
    const switches=[...anchor.querySelectorAll('button[role="switch"]')].filter(usable);
    const switchValue=switches.length===1?switches[0].getAttribute('aria-checked'):null;
    const status=cells.map(c=>text(c)).find(t=>/^(投放中|已暂停|暂停投放|未投放|已结束)(\n|$)/.test(t));
    const running=switchValue==='true'&&/^投放中(\n|$)/.test(status||'')?true:
      switchValue==='false'&&/^(已暂停|暂停投放|未投放|已结束)(\n|$)/.test(status||'')?false:null;
    // Known hover edit controls can have zero size until the pointer is on the
    // row. Only these exact, labelled main-plan entry points are supported.
    const budgetButtons=[...setting.querySelectorAll('iconpark-icon[name="oc-icon-light-edit"]')].filter(e=>{
      const wrap=e.closest('[class^="budgetCell-"],[class*=" budgetCell-"]');
      return wrap&&setting.contains(wrap)&&/每日预算/.test(text(wrap))&&!e.closest('button[data-roi-edit-button]');
    });
    const roiButtons=goal?[...setting.querySelectorAll('button[data-roi-edit-button="true"]')].filter(b=>usable(b)&&b.getAttribute('aria-label')==='编辑'+labels[goal.basis]):[];
    const actions=[];
    if(budget&&budgetButtons.length===1)actions.push('budget_down','budget_up');
    if(goal&&roiButtons.length===1)actions.push('roi_target_down','roi_target_up');
    if(running===true&&switches.length===1)actions.push('pause_plan');
    return {row,switchButton:switches[0],budgetButton:budgetButtons[0],roiButton:roiButtons[0],surface:{
      account_id:identity.accountId,plan_id:id,path:url.pathname,group:identity.group,name:planLines[at-1].slice(0,200),
      budget,target_roi:goal?.value??null,roi_basis:goal?.basis??'unknown',running,actions:actions.sort(),captured_at:new Date().toISOString()}};
  }
  function readLiveHeader(){
    const id=url.searchParams.get('adId');
    if(url.pathname!=='/uni-prom/detail'||url.searchParams.getAll('adId').length!==1||!/^\d{6,20}$/.test(id||''))return null;
    const headers=[...document.querySelectorAll('.oc-promotion-key-info-bar')].filter(shown);
    if(headers.length!==1)return null;
    const header=headers[0],info=header.querySelector('.oc-promotion-key-info-bar-info-con');
    if(!info)return null;
    const ids=[...text(info).matchAll(/计划ID[：:]\s*(\d+)/g)];
    if(ids.length!==1||ids[0][1]!==id)return null;
    const fields=[...info.querySelectorAll('.oc-promotion-key-info-bar-key-value-con')],goals=[];
    let budget=null,budgetButton=null;
    for(const field of fields){
      const label=text(field.querySelector('.key-con-item')).replace(/[:：]$/,''),value=text(field.querySelector('.value-con-item > [data-auto-id="oc-typography"] .oc-typography-value-int'));
      const buttons=[...field.querySelectorAll('.oc-promotion-key-info-bar-edit-icon')];
      if(label==='预算(元)'&&/^每日[\d,.]+$/.test(value)){
        budget=amount(value.slice(2));if(buttons.length===1)budgetButton=buttons[0];
      }
      for(const [basis,title] of Object.entries(labels))if(label===title){
        const parsed=amount(value);if(parsed)goals.push({basis,value:parsed,button:buttons.length===1?buttons[0]:null});
      }
    }
    const goal=goals.length===1?goals[0]:null,actions=[];
    if(budget&&budgetButton)actions.push('budget_down','budget_up');
    if(goal?.button)actions.push('roi_target_down','roi_target_up');
    const switches=[...header.querySelectorAll('.col-status-switch [data-auto-id="oc-switch"] > .ovui-switch[data-e2e="switch"]')]
      .filter(e=>usable(e)&&!e.classList.contains('ovui-switch--disabled'));
    const switchButton=switches.length===1?switches[0]:null,checked=switchButton?.classList.contains('ovui-switch--checked');
    const running=checked===true&&/^投放中(\n|$)/.test(text(info))?true:checked===false&&/^(已暂停|暂停投放|未投放|已结束)(\n|$)/.test(text(info))?false:null;
    if(running===true&&switchButton)actions.push('pause_plan');
    return {row:header,budgetButton,roiButton:goal?.button,switchButton,surface:{account_id:identity.accountId,plan_id:id,path:url.pathname,group:identity.group,
      name:'计划 '+id,budget,target_roi:goal?.value??null,roi_basis:goal?.basis??'unknown',running,actions:actions.sort(),captured_at:new Date().toISOString()}};
  }
  function rows(){
    const result=new Map(),conflicts=new Set();
    if(url.pathname==='/uni-prom/detail'){
      const header=readLiveHeader();if(header)result.set(header.surface.plan_id,header);return result;
    }
    for(const a of document.querySelectorAll('[id^="ad-name-"]')){
      const r=readRow(a);if(!r)continue;
      if(result.has(r.surface.plan_id))conflicts.add(r.surface.plan_id);else result.set(r.surface.plan_id,r);
    }
    for(const id of conflicts)result.delete(id);
    return result;
  }
  const same=surface=>intent&&['account_id','plan_id','path','group','name','budget','target_roi','roi_basis','running','actions']
    .every(k=>JSON.stringify(surface[k])===JSON.stringify(intent.surface[k]));
  const current=()=>rows().get(intent?.plan_id);
  if(phase==='inspect'){
    if(editorRoots().length)return {ok:true,surfaces:[],code:'editor_open'};
    return {ok:true,surfaces:[...rows().values()].map(r=>r.surface)};
  }
  if(!intent||!request.id||!request.intent_digest||!['budget_up','budget_down','roi_target_up','roi_target_down','pause_plan'].includes(intent.action))return fail('command_invalid');
  let row=current();
  if(!row||!same(row.surface)||!row.surface.actions.includes(intent.action))return fail('setting_changed');

  function findForm(){
    const roots=editorRoots(),candidates=[];
    for(const root of roots){
      const inputs=[...root.querySelectorAll('input')].filter(i=>shown(i)&&usable(i)&&!i.readOnly&&['text','number','tel',''].includes(i.type));
      if(inputs.length!==1||amount(inputs[0].value)!==intent.before)continue;
      const rootText=text(root),matches=intent.field==='budget'?/(?:每日|日)预算/.test(rootText)&&!/调控预算|调控日预算/.test(rootText):
        rootText.includes(labels[intent.setting_roi_basis])||(intent.setting_roi_basis==='net'&&/净成交[\s\S]*ROI目标|ROI目标[\s\S]*净成交/.test(rootText));
      if(!matches)continue;
      const buttons=[...root.querySelectorAll('button')].filter(b=>shown(b)&&usable(b)&&['确定','确认','保存'].includes(text(b)||b.getAttribute('aria-label')));
      if(buttons.length!==1)continue;
      candidates.push({root,input:inputs[0],button:buttons[0]});
    }
    const unique=candidates.filter((c,i)=>candidates.findIndex(x=>x.input===c.input)===i);
    return unique.length===1?unique[0]:null;
  }
  function clearPrepared(restore=true){
    const p=globalThis[key];delete globalThis[key];
    if(restore&&p?.input?.isConnected){
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(p.input,intent.before);
      p.input.dispatchEvent(new Event('input',{bubbles:true}));p.input.dispatchEvent(new Event('change',{bubbles:true}));
      const cancels=[...p.root.querySelectorAll('button')].filter(b=>shown(b)&&usable(b)&&text(b)==='取消');
      if(cancels.length===1)cancels[0].click();
    }
  }
  if(phase==='prepare'){
    if(editorRoots().length||globalThis[key])return fail('another_editor_open');
    const nonce=crypto.randomUUID();
    if(intent.action==='pause_plan'){
      globalThis[key]={id:request.id,digest:request.intent_digest,nonce,button:row.switchButton,prepared_at:Date.now(),submitted:false};
      return {ok:true,prepared:true,submit_token:nonce,surface:row.surface};
    }
    const entry=intent.field==='budget'?row.budgetButton:row.roiButton;
    if(!entry||!usable(entry))return fail('entry_not_supported');
    entry.click();
    let form=null;
    for(let n=0;n<40&&!form;n++){form=findForm();if(!form)await new Promise(resolve=>setTimeout(resolve,100));}
    if(!form)return fail('editor_not_supported');
    row=current();if(!row||!same(row.surface))return fail('setting_changed');
    globalThis[key]={...form,id:request.id,digest:request.intent_digest,nonce,prepared_at:Date.now(),submitted:false};
    return {ok:true,prepared:true,submit_token:nonce,surface:{...row.surface,captured_at:new Date().toISOString()}};
  }
  if(phase==='abort'){clearPrepared();return {ok:true,submitted:false};}
  if(phase!=='submit')return fail('phase_invalid');
  const prepared=globalThis[key];
  if(!prepared||prepared.id!==request.id||prepared.digest!==request.intent_digest||prepared.nonce!==request.submit_token||prepared.submitted||
      Date.now()-prepared.prepared_at>60000||Date.now()>=Date.parse(request.expires_at))return fail('prepared_editor_expired');
  if(!prepared.button?.isConnected||!usable(prepared.button))return fail('editor_changed');
  if(intent.field!=='running'){
    if(!shown(prepared.root)||!shown(prepared.input)||!prepared.root.contains(prepared.input)||!prepared.root.contains(prepared.button)||amount(prepared.input.value)!==intent.before)return fail('editor_changed');
    const value=amount(intent.after);if(value!==intent.after)return fail('amount_invalid');
    for(const attr of ['min','max']){
      const declared=prepared.input.getAttribute(attr)||prepared.input.getAttribute('aria-value'+attr);
      if(declared&&Number.isFinite(Number(declared))&&(attr==='min'?Number(value)<Number(declared):Number(value)>Number(declared)))return fail('input_range_rejected');
    }
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(prepared.input,value);
    prepared.input.dispatchEvent(new Event('input',{bubbles:true}));prepared.input.dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise(resolve=>setTimeout(resolve,100));
    if(!prepared.input.isConnected||prepared.input.value!==value||!prepared.input.checkValidity()||!usable(prepared.button)){clearPrepared();return fail('input_validation_rejected');}
  }
  let permit;
  try{permit=await chrome.runtime.sendMessage({action:'control-final-check',id:request.id,nonce:prepared.nonce});}
  catch{clearPrepared();return fail('final_permission_missing');}
  row=current();
  if(permit?.allow!==true||!row||!same(row.surface)||!prepared.button.isConnected||!usable(prepared.button)){
    clearPrepared();return fail('final_permission_rejected');
  }
  prepared.submitted=true;
  try{prepared.button.click();}
  catch{delete globalThis[key];return {ok:false,submitted:true,code:'submit_result_unknown'};}
  delete globalThis[key];
  for(let n=0;n<20&&editorRoots().length;n++)await new Promise(resolve=>setTimeout(resolve,100));
  return {ok:true,submitted:true,code:editorRoots().length?'platform_editor_still_open':'submit_attempted',editor_open:editorRoots().length>0};
}
if(typeof module!=='undefined')module.exports={browserPlanDom};
