/* Dedicated DOM adapter for both current Chengfang and all-domain details.
 * No fetch, platform tokens, hidden application state or coordinate clicks.
 */
async function hybridChaseDom(phase, request) {
  const c=request.command, key='__qc_hybrid_editor_v1', navigationKey='__qc_hybrid_table_navigation_v1', searchKey='__qc_hybrid_task_search_v1';
  let currentHomeProof;
  const pause=async duration_ms=>{
    const reply=await chrome.runtime.sendMessage({action:'hybrid-wait',run_id:c.run_id,command_id:c.id,duration_ms});
    if(reply?.ok!==true)throw Error('后台等待已中断'+(reply?.message?'：'+String(reply.message).slice(0,180):'')+'，保留原单，不提交');
  };
  const text=e=>(e?.innerText||e?.textContent||'').trim();
  const visible=e=>!!e?.isConnected&&e.getClientRects().length>0&&getComputedStyle(e).display!=='none'&&getComputedStyle(e).visibility!=='hidden';
  const enabled=e=>visible(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true';
  const norm=s=>String(s).replace(/[\s()（）]/g,'').replace(/元$/,'');
  const number=s=>{const v=String(s).replace(/[,，]/g,'').trim();return /^\d+(?:\.\d+)?$/.test(v)?v:null;};
  const same=(a,b)=>a.length===b.length&&[...a].sort().every((v,i)=>v===[...b].sort()[i]);
  function selectionBox(row){
    const boxes=[...row.cells[0].querySelectorAll('input[type="checkbox"]')];
    if(boxes.length!==1)throw Error('素材左侧选择框未唯一明确（左侧'+boxes.length+'个，整行'+row.querySelectorAll('input[type="checkbox"]').length+'个）');
    return boxes[0];
  }
  function auditPassed(value,mid){
    if(/^审核通过(?:可优化|审核建议)*$/.test(String(value).replace(/\s/g,'')))return true;
    if(/^(?:审核不通过|审核拒绝|审核中|待审核|未审核|审核失败)(?:\s|$)/.test(value))return false;
    throw Error('素材审核状态未明确 '+mid+'（'+(value||'空值').slice(0,60)+'）');
  }
  function auditCell(cell,mid){
    const value=text(cell);
    if(!cell||cell.querySelector('.cell-async-loading')||!value||value==='-'){
      cell?.scrollIntoView?.({block:'center',inline:'nearest'});
      throw Error('等待素材审核状态异步加载 '+mid+'；未提交');
    }
    return auditPassed(value,mid);
  }
  function chaseCell(cell,mid){
    if(!cell||cell.querySelector('.cell-async-loading')){
      cell?.scrollIntoView?.({block:'center',inline:'nearest'});
      throw Error('等待素材追投状态异步加载 '+mid+'；未提交');
    }
    const value=text(cell).replace(/查看详情/g,'').trim();
    if(!value)throw Error('素材追投状态未加载 '+mid);
    return value;
  }
  const apiBatch=c.action?.intent?.browser_batch?.selection_source==='official_api';
  const nativeOwner=value=>/^\d{6,30}$/.test(value||'')||c.executor_kind==='ego'&&/^zlp_\d{6,30}$/.test(value||'');
  const exact=(root,label)=>[...root.querySelectorAll('button,[role="button"],[role="option"],label,span,div')]
    .filter(e=>enabled(e)&&text(e)===label&&![...e.children].some(ch=>text(ch)===label));
  const homeLinks=()=>{
    const labels=location.pathname==='/uni-prom/detail'?['推直播间','推商品']:['推直播间'];
    const match=e=>enabled(e)&&labels.includes(text(e))&&!e.closest('table')&&![...e.children].some(ch=>labels.includes(text(ch)))&&(text(e)==='推直播间'||!!e.closest('.ovui-breadcrumb,.c-promotion-breadcrumb'));
    return [...document.querySelectorAll('a,button,[role="button"],span,div')].filter(match);
  };
  const click=(root,label)=>{const all=exact(root,label);if(all.length!==1)throw Error('控件不唯一：'+label);all[0].click();};
  const clickNative=e=>{
    if(!enabled(e))throw Error('页面控件不可用');
    e.scrollIntoView?.({block:'nearest'});
    for(const type of ['mousedown','mouseup'])e.dispatchEvent(new MouseEvent(type,{bubbles:true,cancelable:true,view:window}));
    e.click();
  };
  async function chooseRadio(root,label){
    const matches=exact(root,label);
    const radios=[...new Set(matches.map(e=>e.closest('[role="radio"],[class*="radio-item"]')).filter(e=>e&&root.contains(e)&&enabled(e)))];
    // The ROI explanation repeats the goal in a plain text tag. Only the
    // native radio (or a real button in older layouts) may select a goal.
    const candidates=radios.length?radios:matches.filter(e=>e.matches('button,[role="button"],label:has(input[type="radio"])'));
    if(candidates.length!==1)throw Error('追投选项不唯一：'+label);
    const target=candidates[0],selected=()=>target.getAttribute('aria-checked')==='true'||/--checked|--active/.test(target.className);
    if(!selected())clickNative(target);
    if(radios.length){
      const deadline=Date.now()+2500;
      while(!selected()&&Date.now()<deadline)await pause(100);
      if(!selected())throw Error('追投选项尚未选中：'+label);
    }
  }
  const roots=()=>[...document.querySelectorAll('[role="dialog"],[aria-modal="true"],[class*="-modal__wrap"],.ovui-modal,.ovui-drawer,.ovui-modal__wrap,.oc-modal-wrap,[class*="-drawer__wrap"],[class*="popover-inner"],[class*="popover__content"],[class*="popper-content"]')]
    .filter(e=>visible(e)&&[...e.querySelectorAll('input,button,textarea,select')].some(visible)).filter((e,i,a)=>!a.some(x=>x!==e&&x.contains(e)));
  const editorInfo=()=>{const editors=roots();return {editor_open:editors.length>0,editor_summary:editors.map(r=>text(r.querySelector('h1,h2,h3,[class*="title"]')||r).split('\n')[0].slice(0,80)).join('；')};};
  async function dismissKnownNotice(){
    let closed=false;
    const noticeRoots=[...document.querySelectorAll('[role="dialog"],[aria-modal="true"],[class*="-modal__wrap"],.ovui-modal,.ovui-drawer,.ovui-modal__wrap,.oc-modal-wrap,[class*="-drawer__wrap"],[class*="popover-inner"],[class*="popover__content"],[class*="popper-content"]')]
      .filter(e=>visible(e)).filter((e,i,a)=>!a.some(x=>x!==e&&x.contains(e)));
    for(const root of noticeRoots){
      const wording=norm(text(root));
      const starNotice=wording.includes('投放千川星选素材')&&wording.includes('补充优质素材拓消耗');
      const upgradeNotice=root.classList.contains('all-shop-upgrade-modal-wrap')||(wording.includes('全店托管重磅升级')&&wording.includes('单号单账户升级多号多账户投放'));
      const expiredAuthNotice=wording.includes('以下投放权限已失效')&&wording.includes('相关千川乘方及全域投放不可用');
      if(!starNotice&&!upgradeNotice&&!expiredAuthNotice)continue;
      const editing=[...root.querySelectorAll('input,textarea,select,[contenteditable="true"]')].some(e=>visible(e)&&!['checkbox','hidden'].includes(e.type));
      if(editing)continue;
      let buttons=[...root.querySelectorAll('[aria-label="关闭"],[aria-label="关闭弹窗"],[aria-label="close" i],[title="关闭"],[class*="modal__close"],[class*="modal-close"]')].filter(enabled);
      buttons=[...new Set(buttons.map(e=>e.closest('button,[role="button"],[class*="modal__close"],[class*="modal-close"]')||e))].filter(enabled);
      buttons=buttons.filter(e=>!buttons.some(other=>other!==e&&other.contains(e)));
      if(buttons.length!==1)throw Error('已识别平台提示弹窗，关闭入口未唯一明确；保留页面，未点击投放入口');
      buttons[0].click();
      for(let n=0;n<20&&visible(root);n++)await pause(100);
      if(visible(root))throw Error('平台提示弹窗尚未关闭；未改投放');
      closed=true;
    }
    return closed;
  }
  function officialParentAnchor(){
    const a=c.parent_anchor,age=Date.now()-Date.parse(a?.captured_at);
    return a?.kind==='unique_official_plan_anchor'&&a.account_id===c.account_id&&a.plan_id===c.plan_id&&/^\d{6,30}$/.test(a.aweme_id||'')&&Number.isFinite(age)&&age>=0&&age<=600000?a:null;
  }
  function identity(){
    const u=new URL(location.href),body=text(document.body);
    const home=['/uni-prom','/overall-prom'].includes(u.pathname)&&request.home_context;
    if(u.origin!=='https://qianchuan.jinritemai.com'||!home&&!['/uni-prom/detail','/overall-prom/detail'].includes(u.pathname)
      ||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==c.account_id
      ||!home&&(u.searchParams.getAll('adId').length!==1||u.searchParams.get('adId')!==c.plan_id)
      ||home&&u.searchParams.has('adId')&&(u.searchParams.getAll('adId').length!==1||!['',c.plan_id].includes(u.searchParams.get('adId'))))throw Error('页面账户或计划网址不符');
    const labels=[...document.querySelectorAll('span,div,p')].filter(e=>visible(e)&&!e.closest('table,[role="dialog"],[aria-modal="true"],.ovui-breadcrumb,.c-promotion-breadcrumb,.oc-breadcrumb-select,.aweme-info,.content-info-container')&&text(e).length<100).map(e=>text(e).replace(/\s/g,''));
    const accounts=new Set(labels.map(s=>s.match(/^(?:账户)?ID[：:](\d{6,30})$/)?.[1]).filter(Boolean));
    const accountVisible=accounts.size?accounts.size===1&&accounts.has(c.account_id):new RegExp('(?:^|\\n)\\s*(?:账户)?ID[：:]\\s*'+c.account_id+'(?!\\d)','m').test(body);
    if(accounts.size&&!accountVisible)throw Error('可见账户ID与本户不符');
    if(home){
      const b=request.home_context,source=new URL(b.detail_url||'about:blank'),age=Date.now()-Date.parse(b.captured_at);
      if(b.aweme_source==='official_plan_list'&&officialParentAnchor()?.aweme_id!==b.aweme_id)throw Error('首页抖音号缺少新鲜官方计划对应关系');
      const sourceLimit=c.executor_kind==='ego'&&c.direct_home&&c.execution_scope==='f5_create_only'?180000:90000;
      if(b.account_id!==c.account_id||b.plan_id!==c.plan_id||b.group!==c.group||!nativeOwner(b.aweme_id)||!Number.isFinite(age)||age<0||age>sourceLimit||source.origin!==u.origin||!['/uni-prom/detail','/overall-prom/detail'].includes(source.pathname)||source.searchParams.getAll('aavid').length!==1||source.searchParams.get('aavid')!==c.account_id||source.searchParams.getAll('adId').length!==1||source.searchParams.get('adId')!==c.plan_id)throw Error('首页母计划对应关系无效或过期');
      const rows=[];
      for(const row of document.querySelectorAll('tr,[role="row"],.ovui-table__row')){
        if(!visible(row)||roots().some(r=>r.contains(row)))continue;
        const plans=new Set(),explicitOwners=new Set();let owner=false;
        if(c.executor_kind==='ego'&&/^\d{6,30}$/.test(row.getAttribute('data-row-key')||''))plans.add(row.getAttribute('data-row-key'));
        for(const e of [row,...row.querySelectorAll('[id],[data-plan-id],[data-ad-id],[data-aweme-id],a[href]')]){
          const named=e.id?.match(/^ad-name-(\d{6,30})$/);if(named)plans.add(named[1]);
          for(const key of ['data-plan-id','data-ad-id']){const id=e.getAttribute(key);if(/^\d{6,30}$/.test(id||''))plans.add(id);}
          const explicitOwner=e.getAttribute('data-aweme-id');if(nativeOwner(explicitOwner))explicitOwners.add(explicitOwner);
          if(e.id===b.aweme_id||explicitOwner===b.aweme_id)owner=true;
          if(e.matches('a[href]'))try{
            const link=new URL(e.getAttribute('href'),location.href),pid=link.searchParams.get('adId');
            if(link.origin===u.origin&&['/uni-prom/detail','/overall-prom/detail'].includes(link.pathname)&&(!link.searchParams.has('aavid')||link.searchParams.get('aavid')===c.account_id)&&/^\d{6,30}$/.test(pid||''))plans.add(pid);
          }catch{}
        }
        for(const m of text(row).matchAll(/计划\s*ID[：:]\s*(\d{6,30})/g))plans.add(m[1]);
        if(plans.size>1||plans.size===1&&!plans.has(c.plan_id)||explicitOwners.size&&(explicitOwners.size!==1||!explicitOwners.has(b.aweme_id)))continue;
        if(plans.has(c.plan_id)||owner)rows.push({row,row_plan_id:plans.has(c.plan_id)?c.plan_id:null,row_aweme_id:owner?b.aweme_id:null});
      }
      if(rows.length!==1)throw Error('首页母计划行未唯一对应');
      const words=text(rows[0].row),budget=[...words.matchAll(/每日预算\s*([\d,.]+)\s*元/g)];
      const cost=words.includes('控成本投放')||c.executor_kind==='ego'&&b.parent_bid_type==='SMART_BID_CUSTOM'&&/综合营销ROI目标\s*[\d.]+/.test(words)&&!words.includes('放量投放');
      if(budget.length!==1||Number(budget[0][1].replace(/,/g,''))!==Number(b.parent_budget)||!/(?:^|\s)投放中(?:\s|$)/.test(words)||!cost)throw Error('首页母计划预算、状态或控成本设置已变化');
      currentHomeProof={account_id:c.account_id,plan_id:c.plan_id,group:c.group,path:u.pathname,url:u.href,aweme_id:b.aweme_id,
        row_plan_id:rows[0].row_plan_id,row_aweme_id:rows[0].row_aweme_id,parent_budget:Number(budget[0][1].replace(/,/g,'')).toFixed(2),
        running:true,parent_bid_type:'SMART_BID_CUSTOM',delivery_entry:b.delivery_entry,captured_at:new Date().toISOString()};
    }else{
      const plans=new Set(labels.map(s=>s.match(/^计划ID[：:](\d{6,30})$/)?.[1]).filter(Boolean));
      if(!plans.size)for(const m of body.matchAll(/计划\s*ID[：:]\s*(\d{6,30})(?!\d)/g))plans.add(m[1]);
      if(plans.size!==1||!plans.has(c.plan_id))throw Error('等待可见母计划ID');
    }
    // Narrow layouts collapse the account label to an avatar. The immutable
    // API material query already binds the advertiser and parent; require
    // that fresh scope plus this exact visible parent ID and URL instead.
    if(!accountVisible){
      const b=c.account_binding,age=Date.now()-Date.parse(b?.verified_at);
      if(b?.kind!=='official_api_material_scope'||b.account_id!==c.account_id||b.plan_id!==c.plan_id||!Number.isFinite(age)||age<0||age>600000)throw Error('等待可见账户ID或新鲜API归属核验');
    }
    return u;
  }
  function labelInput(root,label){
    const found=new Set();
    for(const e of [...root.querySelectorAll('label,span,div,p')].filter(e=>visible(e)&&norm(text(e))===norm(label)&&![...e.children].some(ch=>norm(text(ch))===norm(label)))){
      for(let box=e,n=0;box&&box!==root&&n<6;box=box.parentElement,n++){
        const inputs=[...box.querySelectorAll('input')].filter(i=>visible(i)&&i.type!=='checkbox');
        if(inputs.length===1){found.add(inputs[0]);break;}
        if(inputs.length>1)break;
      }
    }
    return found.size===1?[...found][0]:null;
  }
  function chaseBidInput(root){
    const found=new Set(['直播间成交出价','我的出价','目标转化成本','转化出价','出价'].map(label=>labelInput(root,label)).filter(Boolean));
    return found.size===1?[...found][0]:null;
  }
  function set(input,value){
    if(!input||!enabled(input))throw Error('未找到可用输入框');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,String(value));
    input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.dispatchEvent(new Event('blur',{bubbles:true}));
  }
  async function choose(input,label){
    if(!input)throw Error('筛选字段缺失');
    if(norm(input.value)===norm(label)||label==='全部'&&(!input.value||norm(input.value).startsWith('全部')))return false;
    clickNative(input);let options=[];
    for(let n=0;n<25&&!options.length;n++){
      if(['10条/页','100条/页'].includes(label)){
        const wanted=label.split('条')[0],match=new RegExp('^'+wanted+'(?:条/页)?$');
        options=[...document.querySelectorAll('[role="option"],li,div,span')].filter(e=>enabled(e)&&match.test(norm(text(e)))&&![...e.children].some(ch=>norm(text(ch))===norm(text(e))));
        options=options.filter(e=>{
          for(let p=e.parentElement,k=0;p&&p!==document.body&&k<6;p=p.parentElement,k++){
            if(text(p).length>500)break;
            const labels=[...p.querySelectorAll('[role="option"],li,div,span')].filter(visible).map(x=>norm(text(x)));
            if(['10','20','50','100'].filter(v=>v!==wanted&&(labels.includes(v)||labels.includes(v+'条/页'))).length>=2)return true;
          }return false;
        });
      }else{
        options=[...document.querySelectorAll('[role="option"],[class*="select-option"],[class*="select__option"],[class*="option-item"],[class*="select-item"],[class*="cascader-panel__item-label"]')].filter(e=>enabled(e)&&(norm(text(e))===norm(label)||label==='全部'&&norm(text(e))==='全部包括已删除'));
        options=options.filter(e=>!options.some(x=>x!==e&&e.contains(x)));
      }
      if(!options.length)await pause(100);
    }
    if(options.length!==1)throw Error('未找到唯一筛选选项：'+label);
    clickNative(options[0]);return true;
  }
  async function chooseTaskState(input,label){
    if(c.executor_kind!=='ego')return choose(input,label);
    input.setAttribute('data-qcx-task-state',c.id);
    try{
      const result=await chrome.runtime.sendMessage({action:'hybrid-native-task-state',run_id:c.run_id,command_id:c.id,label});
      if(result?.ok!==true)throw Error('任务状态原生选择失败：'+String(result?.message||'未回传结果').slice(0,200));
      const current=labelInput(document.body,'投放状态：');
      if(!current||(label==='全部'?!norm(current.value).startsWith('全部'):current.value!==label))throw Error('任务状态选中结果未核验');
      return true;
    }finally{input.removeAttribute('data-qcx-task-state');}
  }
  function spendSort(){
    const headers=[...document.querySelectorAll('th')].filter(e=>visible(e)&&norm(text(e))==='整体消耗');
    if(headers.length!==1)return {ready:false,arrow:null};
    const h=headers[0],arrow=h.querySelector('[class*="sorter"][class*="down"],[class*="sort"][class*="desc"],[aria-label="降序"]');
    return {ready:h.getAttribute('aria-sort')==='descending'||!!h.querySelector('[class*="sorter"][class*="down"][class*="active"],[class*="sort"][class*="desc"][class*="active"]'),arrow};
  }
  function table(kind){
    // Native pagination updates its number before replacing the old rows.
    // A visible loading mask means those rows still belong to the prior view.
    if([...document.querySelectorAll('.table-container.loading,[class*="spin__block-mask"],[aria-busy="true"]')].some(e=>visible(e)&&!roots().some(r=>r.contains(e))))throw Error('等待数据表加载完成，未读取旧行');
    const candidates=new Map();
    for(const tr of document.querySelectorAll('table thead tr')){
      if(!visible(tr)||roots().some(r=>r.contains(tr)))continue;
      const h=[...tr.cells].map(x=>norm(text(x)));
      if(['materials','material_ids'].includes(kind)?!h.includes('视频')||!h.includes('审核状态')||!h.includes('素材追投')||kind==='materials'&&(!h.includes('整体消耗')||!h.includes('整体支付ROI'))
        :!h.includes('任务名称')||!h.includes('调控状态')||!h.includes('调控预算'))continue;
      candidates.set(JSON.stringify(h),h);
    }
    if(candidates.size!==1)throw Error('等待唯一完整数据表及明确指标口径');
    const h=[...candidates.values()][0];
    const rows=[...document.querySelectorAll('table tbody tr')].filter(r=>visible(r)&&r.cells.length===h.length&&!roots().some(x=>x.contains(r)));
    return {headers:h,rows,cell:(row,label)=>row.cells[h.indexOf(label)]};
  }
  function pagination(){
    const boxes=[...document.querySelectorAll('[class*="pagination"]')].filter(e=>visible(e)&&/共\s*\d+\s*条记录/.test(text(e))&&[...e.querySelectorAll('input')].some(i=>/^\d+\s*条\s*\/\s*页$/.test(i.value)));
    const box=boxes.find(e=>!boxes.some(x=>x!==e&&e.contains(x)));
    if(!box){
      // The legacy task page removes its paginator for a confirmed empty
      // search. This is absence evidence only, never a creation receipt.
      if(['verify','lookup'].includes(c.phase)||c.direct_home&&c.phase==='tasks'||c.rule_job){
        const t=table('tasks');
        const zero=[...document.querySelectorAll('table thead tr')].filter(e=>visible(e)&&/^共\s*0\s*条任务(?:\s|$)/.test(text(e)));
        if(!t.rows.length&&zero.length===1)return {box:null,input:null,total:0,page:1,size:10,has_next:false};
      }
      throw Error('等待分页总数和每页条数');
    }
    const total=Number(text(box).match(/共\s*(\d+)\s*条记录/)[1]);
    const input=[...box.querySelectorAll('input')].filter(i=>/^\d+\s*条\s*\/\s*页$/.test(i.value));
    const active=[...box.querySelectorAll('[aria-current="page"],[class*="item--active"],[class*="item-active"]')].filter(x=>/^\d+$/.test(text(x)));
    if(input.length!==1||active.length!==1)throw Error('页码不明确');
    const size=Number(input[0].value.match(/^\d+/)[0]),page=Number(text(active[0]));
    return {box,input:input[0],total,page,size,has_next:page*size<total};
  }
  function rowSignature(kind){
    const t=table(kind),label=kind==='materials'?'视频':'任务名称';
    return t.rows.map(row=>text(t.cell(row,label)).match(/(?:素材ID|ID)[：:]\s*(\d{6,30})/)?.[1]||'').join(',');
  }
  function observe(kind){
    const u=identity();if(roots().length)throw Error('当前有编辑窗口，保留输入');
    const t=table(kind),p=pagination(),body=text(document.body),b=body.match(/预算[（(]元[)）][：:]?\s*(?:每日)?\s*([\d,.]+)/);
    const navigation=globalThis[navigationKey];
    if(navigation?.kind===kind&&navigation.page===p.page){
      if(rowSignature(kind)===navigation.previous)throw Error('等待翻页后新数据加载，页码变化不能代替数据变化');
      delete globalThis[navigationKey];
    }
    if(kind==='materials'&&!spendSort().ready)throw Error('等待整体消耗降序生效');
    const legacySwitches=[...document.querySelectorAll('.col-status-switch .ovui-switch')].filter(visible);
    const switches=legacySwitches.length?legacySwitches:[...document.querySelectorAll('[role="switch"]')].filter(e=>visible(e)&&!e.closest('table')&&!roots().some(r=>r.contains(e)));
    const dates=[...document.querySelectorAll('input')].filter(i=>visible(i)&&/^(?:请选择)?(开始日期|结束日期)$/.test(i.placeholder));
    if(!b||switches.length!==1||dates.length!==2)throw Error('母计划预算、开关或日期未明确');
    const rows=t.rows.map(row=>{
      const cell=label=>text(t.cell(row,label));
      if(['materials','material_ids'].includes(kind)){
        const ids=[...cell('视频').matchAll(/素材ID[：:]\s*(\d{6,30})/g)];if(ids.length!==1)throw Error('素材ID不唯一');
        if(kind==='material_ids'){
          const box=selectionBox(row),approved=auditCell(t.cell(row,'审核状态'),ids[0][1]),chase=chaseCell(t.cell(row,'素材追投'),ids[0][1]);
          return {id:ids[0][1],approved,selectable:!box.disabled,chase_status:chase,captured_at:new Date().toISOString()};
        }
        const roi=number(cell('整体支付ROI')),spend=number(cell('整体消耗'));
        if(spend===null)throw Error('素材消耗未加载');
        const box=selectionBox(row);
        const chase=chaseCell(t.cell(row,'素材追投'),ids[0][1]);
        return {id:ids[0][1],name:cell('视频').split('\n')[0].slice(0,200),spend,roi,chase_status:chase,
          approved:auditCell(t.cell(row,'审核状态'),ids[0][1]),selectable:!box.disabled};
      }
      const ids=[...cell('任务名称').matchAll(/(?:^|\n)\s*ID[：:]\s*(\d{6,30})/g)];
      if(ids.length!==1||number(cell('调控预算'))===null)throw Error('任务ID或预算未明确');
      return {id:ids[0][1],budget:number(cell('调控预算')),status:cell('调控状态').split('\n')[0].replace(/保$/,'').trim()};
    });
    if(rows.length!==Math.min(p.size,Math.max(0,p.total-(p.page-1)*p.size)))throw Error('等待表格行加载完整');
    const headings=[...document.querySelectorAll('h1')].filter(visible).map(text);
    const entry=headings.includes('千川乘方')?'chengfang':headings.includes('全域投放')||u.pathname==='/uni-prom/detail'?'uni_promotion':null;
    if(!entry||!/(?:综合营销|净成交|整体支付|支付)ROI目标[：:]\s*[\d.]+/.test(body))throw Error('母计划入口或控成本优化目标未明确');
    return {account_id:c.account_id,plan_id:c.plan_id,path:u.pathname,group:c.group,captured_at:new Date().toISOString(),
      page:p.page,page_size:p.size,total:p.total,has_next:p.has_next,rows,parent_budget:number(b[1]),
      running:legacySwitches.length?/\bovui-switch--checked\b/.test(switches[0].className):switches[0].getAttribute('aria-checked')==='true',date_start:dates[0].value,date_end:dates[1].value,delivery_entry:entry,parent_bid_type:'SMART_BID_CUSTOM'};
  }
  function materialSearch(){
    const inputs=[...document.querySelectorAll('input')].filter(i=>visible(i)&&!roots().some(r=>r.contains(i))&&/输入视频名称\s*\/?\s*ID|视频名称.*ID|素材ID/.test(i.placeholder));
    if(inputs.length!==1)throw Error('素材ID搜索框不唯一，未扫描素材列表');
    return inputs[0];
  }
  async function searchExact(input,mid,search_kind){
    if(c.executor_kind==='ego'){
      const marker=String(c.id)+'-'+search_kind;input.setAttribute('data-qcx-native-search',marker);
      try{
        const reply=await chrome.runtime.sendMessage({action:'hybrid-native-search',run_id:c.run_id,command_id:c.id,material_id:mid,search_kind});
        if(reply?.ok!==true)throw Error('Ego原生素材ID搜索未完成，未提交');
      }finally{input.removeAttribute('data-qcx-native-search');}
    }else{
      set(input,mid);await pause(600);input.focus();
      for(const type of ['keydown','keypress','keyup'])input.dispatchEvent(new KeyboardEvent(type,{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
    }
  }
  async function findMaterial(mid){
    const input=materialSearch();await searchExact(input,mid,'main');
    const expires=Date.parse(c.expires_at),deadline=Number.isFinite(expires)?Math.min(Date.now()+20000,expires-10000):Date.now()+20000;let last='等待素材ID搜索返回',emptyConfirmed=false;
    while(Date.now()<deadline){
      identity();
      emptyConfirmed=false;
      try{
        const t=table('material_ids');
        emptyConfirmed=!t.rows.length&&[...document.querySelectorAll('table thead tr,th,span')].some(e=>visible(e)&&/^共\s*0\s*(?:个视频|条记录)$/.test(text(e)));
        const matches=t.rows.filter(row=>new RegExp('素材ID[：:]\\s*'+mid+'(?=\\s|$)').test(text(t.cell(row,'视频'))));
        if(matches.length===1&&t.rows.length===1&&pagination().total===1){
          const tags=[...t.cell(matches[0],'视频').querySelectorAll('[data-auto-id="oc-tag"].prefix-tag')];
          if(tags.some(e=>visible(e)&&text(e)==='已删除'))throw Error('本批素材已删除 '+mid+'；未提交');
          t.cell(matches[0],'审核状态')?.scrollIntoView?.({block:'center',inline:'nearest'});
          const obs=observe('material_ids');return {row:matches[0],proof:obs.rows.find(r=>r.id===mid),obs};
        }
        last='本批素材ID未在搜索结果唯一出现：'+mid;
      }catch(error){if(/^本批素材已删除 /.test(error.message))throw error;last=error.message;}
      await pause(150);
    }
    if(emptyConfirmed&&input.value===mid)throw Error('本批素材ID搜索为空 '+mid+'；未提交');
    throw Error(last+'；素材ID '+mid+'；未提交');
  }
  function videoPicker(){
    const found=roots().filter(r=>text(r).includes('添加视频')&&[...r.querySelectorAll('input')].some(i=>visible(i)&&/视频名称.*ID/.test(i.placeholder)));
    if(found.length!==1)throw Error('添加视频选择器尚未唯一就绪');return found[0];
  }
  async function cancelVideoPicker(root){
    // Current Aurora pickers expose only the header Close button, while /uni-prom
    // ovui-modal pickers render a div.ovui-modal__close-icon without role=button.
    // Never use Confirm to finish a partially selected, retired batch.
    const buttons=nodes=>[...new Set(nodes.map(e=>e.closest('button,[role="button"]')).filter(e=>e&&root.contains(e)&&enabled(e)))];
    let candidates=buttons(exact(root,'取消'));
    if(!candidates.length){
      const closeNodes=[...root.querySelectorAll('[aria-label="关闭"],[aria-label="close" i],[title="关闭"],[class*="modal__close"],[class*="modal-close"]')].filter(enabled);
      candidates=[...new Set(closeNodes.map(e=>e.closest('button,[role="button"],[class*="modal__close"],[class*="modal-close"]')||e))].filter(e=>e&&root.contains(e)&&enabled(e));
      candidates=candidates.filter(e=>!candidates.some(other=>other!==e&&other.contains(e)));
    }
    if(candidates.length!==1)throw Error('素材选择器取消或关闭入口不唯一');
    candidates[0].click();
    for(let n=0;n<20&&visible(root);n++)await pause(100);
    if(visible(root))throw Error('素材选择器尚未关闭，保留原表单');
  }
  function pickerTableRows(root){
    if([...root.querySelectorAll('.table-container.loading,[class*="spin__block-mask"],[aria-busy="true"]')].some(visible))throw Error('添加视频列表仍在加载');
    const headers=new Map();
    for(const tr of root.querySelectorAll('table thead tr'))if(visible(tr)){
      const h=[...tr.cells].map(e=>norm(text(e)));if(h.includes('视频')&&h.includes('审核状态'))headers.set(JSON.stringify(h),h);
    }
    if(headers.size!==1)throw Error('添加视频表格未唯一明确');
    const h=[...headers.values()][0];
    return {h,rows:[...root.querySelectorAll('table tbody tr')].filter(r=>visible(r)&&r.cells.length===h.length)};
  }
  function pickerRows(root,mid){
    const {h,rows}=pickerTableRows(root);
    return rows.map(r=>({row:r,id:text(r.cells[h.indexOf('视频')]).match(/素材ID[：:]\s*(\d{6,30})/)?.[1]})).filter(r=>!mid||r.id===mid)
      .map(r=>({...r,audit_status:text(r.row.cells[h.indexOf('审核状态')]),approved:auditCell(r.row.cells[h.indexOf('审核状态')],r.id)}));
  }
  const boxDisabled=b=>Boolean(b.disabled||b.getAttribute('aria-disabled')==='true'||b.closest('[aria-disabled="true"],[class*="checkbox--disabled"],[class*="checkbox-disabled"]'));
  async function addPickerMaterial(mid,count){
    let root=videoPicker();const inputs=[...root.querySelectorAll('input')].filter(i=>visible(i)&&/视频名称.*ID/.test(i.placeholder));
    if(inputs.length!==1)throw Error('添加视频ID搜索框不唯一');
    const input=inputs[0];await searchExact(input,mid,'picker');await pause(250);
    const deadline=Date.now()+12000,retryAt=Date.now()+6000;let last='等待添加视频ID搜索结果',retried=false;
    while(Date.now()<deadline){
      identity();
      try{
        root=videoPicker();
        if(!retried&&Date.now()>=retryAt){
          const current=[...root.querySelectorAll('input')].filter(i=>visible(i)&&/视频名称.*ID/.test(i.placeholder));
          if(current.length!==1)throw Error('添加视频ID搜索框不唯一');
          await searchExact(current[0],mid,'picker');await pause(250);retried=true;root=videoPicker();
        }
        const query=[...root.querySelectorAll('input')].filter(i=>visible(i)&&/视频名称.*ID/.test(i.placeholder));
        if(query.length!==1||query[0].value!==mid)throw Error('添加视频ID搜索值尚未同步');
        const allCount=pickerTableRows(root).rows.length;
        const rows=allCount===1?pickerRows(root,mid):[];
        if(allCount===1&&rows.length===1){
          const box=selectionBox(rows[0].row);
          if(box.checked)throw Error('添加视频存在未核对的已有勾选，保留选择');
          if(!rows[0].approved)throw Error('添加视频审核未通过 '+mid+'（'+rows[0].audit_status.slice(0,60)+'）');
          if(boxDisabled(box))throw Error('添加视频左侧选择框已禁用 '+mid);
          let diag={rows:1,all:1,checked:false,disabled:false,total:'none'};
          for(let attempt=0;attempt<3;attempt++){
            if(attempt===0){box.scrollIntoView?.({block:'center',inline:'nearest'});box.click();}
            else{
              let curRows=[];const settle=Date.now()+800;
              while(Date.now()<settle){
                try{root=videoPicker();curRows=pickerRows(root,mid);if(curRows.length===1)break;}catch{}
                await pause(100);identity();
              }
              if(curRows.length===1){
                if(!curRows[0].approved)throw Error('添加视频审核未通过 '+mid+'（'+curRows[0].audit_status.slice(0,60)+'）');
                const cur=selectionBox(curRows[0].row);
                if(boxDisabled(cur))throw Error('添加视频左侧选择框已禁用 '+mid);
                if(!cur.checked){
                  curRows[0].row.scrollIntoView?.({block:'center',inline:'nearest'});
                  const wrapper=cur.closest('label,[role="checkbox"],[class*="checkbox"]');
                  if(attempt===1&&wrapper&&wrapper!==cur&&enabled(wrapper))wrapper.click();
                  else if(enabled(cur))clickNative(cur);
                  else cur.click();
                }
              }
            }
            const until=Date.now()+2000;
            while(Date.now()<until){
              await pause(100);identity();
              try{
                root=videoPicker();
                const allNow=pickerTableRows(root).rows.length;
                const matched=pickerRows(root,mid);
                const curBox=matched[0]?selectionBox(matched[0].row):null;
                const checked=Boolean(curBox?.checked);
                const total=text(root).match(/已选\s*(\d+)\s*\/\s*\d+/);
                diag={rows:matched.length,all:allNow,checked,disabled:curBox?boxDisabled(curBox):false,total:total?total[1]:'none'};
                if(checked&&total){if(Number(total[1])!==count)throw Error('添加视频未跨搜索保留完整勾选，未提交');return {id:mid,approved:true,selectable:true,captured_at:new Date().toISOString()};}
              }catch(error){if(/未跨搜索/.test(error.message))throw error;}
            }
          }
          throw Error(`添加视频勾选未同步，未提交（素材 ${mid}：rows=${diag.rows}/${diag.all} checked=${diag.checked} disabled=${diag.disabled} total=${diag.total}）`);
        }
        last='添加视频ID未在搜索结果唯一出现：'+mid;
      }catch(error){
        if(/^添加视频(?:审核未通过|左侧选择框已禁用)|审核或可选|未跨搜索|勾选未同步/.test(error.message))throw error;last=error.message;
      }
      await pause(150);
    }
    throw Error(last+'；未提交');
  }
  async function chaseForm(){
    for(let n=0;n<40;n++){
      const root=roots().find(r=>text(r).includes('追投方式')&&text(r).includes('任务名称'));
      if(root)return root;await pause(150);
    }
    throw Error('原生追投表单未打开');
  }
  // Task rules use the same visible table, navigation and identity checks as
  // F5. A rule edit is bound to a single native task and immutable other inputs.
  const ruleKey='__qc_hybrid_rule_editor_v1';
  function ruleRows(){
    const t=table('tasks');return t.rows.map(row=>{
      const cell=label=>text(t.cell(row,label)),id=cell('任务名称').match(/(?:^|\n)\s*ID[：:]\s*(\d{6,30})/)?.[1];
      if(!id)throw Error('任务ID未明确');
      const status=cell('调控状态').split('\n')[0].trim(),mode=cell('追投方式'),raw=cell('调控时长小时').split('\n')[0].trim(),duration=number(raw);
      const metrics={};for(const [key,label] of Object.entries({spend:'调控消耗',roi:'调控支付ROI',comprehensive_cost:'调控综合成本',orders:'调控成交订单数'})){
        const n=number(cell(label));if(n!==null)metrics[key]=Number(n);
      }
      const created=cell('创建时间').replace(/\s+/g,' '),context={control_type:['控成本追投','放量追投'].includes(mode)?'material_chase':'unknown',
        control_delivery_mode:mode==='控成本追投'?'cost_control':mode==='放量追投'?'volume':'unknown',
        observed_duration_mode:duration!==null?'fixed':raw==='长期投放'?'unlimited':'unknown',observed_duration_hours:duration===null?null:Number(duration)};
      if(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(created))context.created_at=created.replace(' ','T')+'+08:00';
      const edits=[...t.cell(row,'调控状态').querySelectorAll('iconpark-icon[name="oc-icon-light-edit"]')];
      const pauses=[...t.cell(row,'调控状态').querySelectorAll('iconpark-icon[name="iconPause"]')];
      const actions=[];if(edits.length===1){actions.push('control_budget_up');if(duration!==null)actions.push('control_duration_up');}
      if(status==='调控中'&&pauses.length===1)actions.push('control_pause');
      return {row,edit:edits[0],pause:pauses[0],surface:{task_id:id,account_id:c.account_id,plan_id:c.plan_id,name:cell('任务名称').split('\n')[0],
        budget:number(cell('调控预算')),duration_minutes:duration===null?null:String(Number(duration)*60),running:status==='调控中'?true:['已暂停','调控暂停','调控结束','已结束'].includes(status)?false:null,
        status,actions,metrics,context,metric_basis:metrics.roi!==undefined?'control_payment':'unknown',
        material_count:Number(cell('素材').match(/共\s*(\d+)\s*条/)?.[1]||(cell('素材').includes('素材ID')?1:0)),captured_at:new Date().toISOString()}};
    });
  }
  function rulePlan(){
    const found=[];for(const e of document.querySelectorAll('span,div')){
      if(!visible(e)||![...e.children].every(ch=>!/(?:综合营销|净成交|整体支付)ROI目标[：:]$/.test(text(ch))))continue;
      const m=text(e).match(/^(综合营销|净成交|整体支付)ROI目标[：:]$/);if(!m)continue;
      const box=e.parentElement,icons=box.querySelectorAll('iconpark-icon[name="oc-icon-light-edit"]');
      const values=text(box).match(/ROI目标[：:]\s*([\d.]+)\s*$/);
      if(icons.length===1&&values)found.push({icon:icons[0],surface:{account_id:c.account_id,plan_id:c.plan_id,target_roi:number(values[1]),roi_basis:({'综合营销':'marketing','净成交':'net','整体支付':'overall_payment'})[m[1]],actions:['roi_target_up','roi_target_down'],captured_at:new Date().toISOString()}});
    }return found.length===1?found[0]:null;
  }
  function ruleObservation(){
    const v=observe('tasks');v.rule_rows=ruleRows().map(x=>x.surface);v.plan_surface=rulePlan()?.surface||null;
    const quota=text(document.body).match(/今日总预算\s*([\d,.]+)\s*元[，,\s]*今日可用预算\s*([\d,.]+)\s*元/);
    if(quota)for(const s of v.rule_rows)if(s.context.control_delivery_mode==='cost_control')s.context.native_available_budget=Number(quota[2].replace(/,/g,''));
    return v;
  }
  const ruleFields={account_id:'账户ID',plan_id:'母计划ID',task_id:'任务ID',name:'任务名称',budget:'调控预算',duration_minutes:'调控时长',running:'投放状态',material_count:'素材数量',target_roi:'ROI目标',roi_basis:'ROI口径'};
  function ruleValues(s){return Object.fromEntries(Object.keys(ruleFields).map(k=>{
    let v=s?.[k];
    if(['duration_minutes','target_roi'].includes(k)&&v==null)v=null;
    if(['budget','duration_minutes','target_roi'].includes(k)&&v!==null&&v!==undefined){
      const parsed=number(v);v=parsed===null?'invalid':Number(parsed).toFixed(2);
    }return [k,v];
  }));}
  function ruleBinding(s){return JSON.stringify(ruleValues(s));}
  function ruleTarget(){const i=c.action.intent;return i.action==='plan_roi'?rulePlan():ruleRows().find(x=>x.surface.task_id===i.task_id);}
  function ruleCancel(){const p=globalThis[ruleKey];delete globalThis[ruleKey];if(!p)return;
    if(p.input?.isConnected)set(p.input,p.rawBefore);
    if(p.root?.isConnected){const b=exact(p.root,'取消').filter(e=>e.matches('button'));if(b.length===1)b[0].click();}
  }
  function quotaRejection(root,i){
    if(i?.action!=='task_budget'||i.browser_surface?.context?.control_delivery_mode!=='cost_control')return null;
    const m=text(root).match(/当前最多可再追加预算\s*([\d,.]+)\s*元/),available=m?Number(m[1].replace(/,/g,'')):null;
    if(available===null||Number(i.after)-Number(i.before)<=available)return null;
    return {kind:'native_budget_insufficient',task_id:i.task_id,name:i.browser_surface.name,material_count:i.browser_surface.material_count,
      before:String(i.before),after:String(i.after),available,captured_at:new Date().toISOString(),message:'不得超过当前可用预算，请重新填写'};
  }
  function namedChaseEditor(){
    const editors=roots();
    if(editors.length!==1)return null;
    const root=editors[0],words=text(root);
    if(!words.includes('新建调控任务')||!words.includes('追投方式')||!words.includes('追投素材'))return null;
    const name=labelInput(root,'任务名称')?.value;
    return /^QCX_\d{4}_[0-9a-f]{16}$/.test(name||'')?{root,name}:null;
  }
  async function cancelChaseEditor(root){
    const cancel=[...new Set(exact(root,'取消').map(e=>e.closest('button,[role="button"]')).filter(e=>e&&root.contains(e)&&enabled(e)))];
    if(cancel.length!==1)throw Error('原窗口取消入口不唯一');
    cancel[0].click();
    for(let n=0;n<20&&visible(root);n++)await pause(100);
    if(visible(root))throw Error('原追投窗口仍未关闭');
  }
  async function recoverEditor(){
    const u=new URL(location.href);
    if(c.executor_kind!=='ego'||u.origin!=='https://qianchuan.jinritemai.com'||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==c.account_id)throw Error('窗口恢复账户不符');
    const p=globalThis[key];
    const proof=request.editor_proof,editor=proof&&namedChaseEditor();
    const plan=u.searchParams.getAll('adId');
    const pageMatches=['/uni-prom/detail','/overall-prom/detail'].includes(u.pathname)?plan.length===1&&plan[0]===c.plan_id:
      ['/uni-prom','/overall-prom'].includes(u.pathname)&&plan.length<=1&&(!plan[0]||plan[0]===c.plan_id);
    const proofMatches=pageMatches&&editor&&editor.name===proof.task_name&&proof.account_id===c.account_id&&proof.plan_id===c.plan_id;
    const cleanupIds=[...(c.cleanup_action_ids||[]),...(phase==='cleanup'&&c.action?.id?[c.action.id]:[])];
    const trackedProof=proofMatches&&p?.root===editor.root&&p.action_id===proof.action_id&&p.fields?.length&&p.ids;
    if(p?.root?.isConnected&&(cleanupIds.includes(p.action_id)||trackedProof)){
      if(p.home_context&&(p.home_context.account_id!==c.account_id||p.home_context.plan_id!==c.plan_id))throw Error('原追投窗口母计划不符');
      if(!p.home_context&&u.searchParams.get('adId')!==c.plan_id)throw Error('原追投窗口母计划不符');
      if(p.fields?.some(f=>!f.input.isConnected||f.input.value!==f.value)||p.audience_check&&!p.audience_check())throw Error('原窗口输入已变化，保留手动编辑');
      if(p.ids&&p.batch&&!same(p.ids(),p.batch.material_ids))throw Error('原窗口素材已变化，保留手动编辑');
      // Cancel a child picker, never confirm partial materials into the form.
      if(p.picker_open){let picker;try{picker=videoPicker();}catch{}if(picker)await cancelVideoPicker(picker);p.picker_open=false;}
      await cancelChaseEditor(p.root);delete globalThis[key];return {ok:true,cleaned:true,recovered_action_id:p.action_id};
    }
    if(proofMatches){
      const {root}=editor,after=proof.after,words=text(root),ids=[...words.matchAll(/素材ID[：:]\s*(\d{6,30})/g)].map(m=>m[1]);
      const goal=after.optimization_target==='综合ROI'?'综合营销ROI':after.optimization_target;
      const fields=after.chase_type==='放量追投'?[[labelInput(root,'调控总预算'),after.total_budget],[labelInput(root,'调控时长'),after.control_duration]]:
        [[labelInput(root,'调控日预算'),after.daily_budget],[goal==='直播间成交'?chaseBidInput(root):labelInput(root,goal+'目标'),after.bid_value]];
      const knownTargets=['综合营销ROI','净成交ROI','支付ROI','直播间成交'];
      const selectedMode=after.chase_type==='放量追投'||after.chase_type==='控成本追投'&&knownTargets.includes(goal);
      if(selectedMode&&!after.audience&&!/编辑定向|修改定向|删除定向/.test(words)&&same(ids,proof.material_ids)
          &&fields.every(([input,value])=>input&&number(input.value)!==null&&number(value)!==null&&Number(input.value)===Number(value))){
        await cancelChaseEditor(root);if(p?.root===root)delete globalThis[key];
        return {ok:true,cleaned:true,recovered_action_id:proof.action_id};
      }
    }
    const i=c.action?.intent;
    if(c.rule_job&&c.rule_phase==='verify'&&i?.action==='task_budget'){
      identity();
      const candidates=roots().filter(r=>text(r).includes('编辑素材追投')&&text(r).includes('不得超过当前可用预算'));
      if(candidates.length===1){const root=candidates[0],proof=quotaRejection(root,i),name=root.querySelector('input[placeholder*="任务名称"]'),count=text(root).match(/已添加[：:]\s*(\d+)\s*\/\s*20/);
        const field=labelInput(root,'调控日预算');
        if(proof&&name?.value===proof.name&&Number(count?.[1])===proof.material_count&&Number(field?.value)===Number(i.after)){
          const cancel=exact(root,'取消').filter(e=>e.matches('button'));if(cancel.length!==1)throw Error('原失败表单取消入口不唯一');cancel[0].click();
          for(let n=0;n<20&&visible(root);n++)await pause(100);
          if(visible(root))throw Error('原失败表单尚未关闭');delete globalThis[ruleKey];return {ok:true,cleaned:true,validation_rejection:proof};
        }
      }
    }
    if(await dismissKnownNotice())return {ok:true,cleaned:true};
    return {ok:true,cleaned:false};
  }
  async function ruleAction(){
    const i=c.action?.intent;
    if(phase==='rule_read')return {ok:true,observation:ruleObservation()};
    if(phase==='rule_cleanup'){ruleCancel();return {ok:true,submitted:false};}
    if(!i||i.execution_channel!=='browser'||!['pause_task','task_budget','task_duration','plan_roi'].includes(i.action))throw Error('规则动作无效');
    const target=ruleTarget();if(!target)throw Error('当前页未找到原任务 '+(i.task_id||i.plan_id));
    if(ruleBinding(target.surface)!==ruleBinding(i.browser_surface)){
      const before=ruleValues(i.browser_surface),current=ruleValues(target.surface);
      throw Error('原任务设置已变化：'+Object.keys(ruleFields).filter(k=>before[k]!==current[k]).map(k=>ruleFields[k]+' '+String(before[k])+' → '+String(current[k])).join('；'));
    }
    const wanted={pause_task:'control_pause',task_budget:'control_budget_up',task_duration:'control_duration_up'}[i.action];
    if(wanted&&!target.surface.actions.includes(wanted))throw Error('原任务的编辑入口当前不可用');
    if(phase==='rule_hover'){
      const button=i.action==='pause_task'?target.pause:target.edit;
      const cell=button?.closest('td,[role="cell"]');
      if(!cell||!visible(cell))throw Error('原任务调控状态单元格未明确');
      if(!/^[a-zA-Z0-9_-]+$/.test(c.id))throw Error('调控命令标识无效');
      cell.setAttribute('data-qcx-rule-hover',c.id);
      cell.scrollIntoView?.({block:'center',inline:'center'});
      return {ok:true,hover_selector:'[data-qcx-rule-hover="'+c.id+'"]'};
    }
    if(phase==='rule_prepare'){
      if(roots().length||globalThis[ruleKey])throw Error('当前有编辑窗口，保留输入');
      const observation=ruleObservation(),p={id:c.action.id,nonce:crypto.randomUUID(),at:Date.now(),before:target.surface};
      if(i.action==='pause_task')p.button=target.pause;
      else{
        const entry=i.action==='plan_roi'?target.icon:target.edit;if(!entry)throw Error('没有唯一原生编辑入口');clickNative(entry);
        let found=[];
        for(let n=0;n<40;n++){
          found=roots().filter(r=>i.action==='plan_roi'?/ROI/.test(text(r)):/编辑素材追投/.test(text(r)));
          if(found.length===1)break;await pause(100);
        }
        if(found.length!==1)throw Error('未找到唯一原任务编辑表单');p.root=found[0];
        if(i.action!=='plan_roi'){
          const name=p.root.querySelector('input[placeholder*="任务名称"]'),count=text(p.root).match(/已添加[：:]\s*(\d+)\s*\/\s*20/);
          if(name?.value!==target.surface.name||!count||Number(count[1])!==target.surface.material_count){
            const cancel=exact(p.root,'取消').filter(e=>e.matches('button'));if(cancel.length===1)cancel[0].click();throw Error('原任务名称或素材数量与编辑表单不一致，已取消');
          }
        }
        const label=i.action==='task_duration'?'调控时长':i.action==='task_budget'?(target.surface.context.control_delivery_mode==='cost_control'?'调控日预算':'调控总预算'):({'marketing':'综合营销ROI目标','net':'净成交ROI目标','overall_payment':'整体支付ROI目标'})[target.surface.roi_basis];
        p.input=labelInput(p.root,label);
        if(!p.input||number(p.input.value)!==String(Number(i.before))&&Number(p.input.value)!==Number(i.before)){
          globalThis[ruleKey]=p;ruleCancel();throw Error('编辑表单原值或字段未核对一致');
        }
        p.rawBefore=p.input.value;p.other=[...p.root.querySelectorAll('input')].filter(x=>x!==p.input).map(x=>({element:x,value:x.value,checked:x.checked}));
        if(quotaRejection(p.root,i)){globalThis[ruleKey]=p;ruleCancel();throw Error('平台可追加预算不足，本条未提交，继续处理暂停任务');}
        const b=[...p.root.querySelectorAll('button')].filter(e=>enabled(e)&&['提交','确定','确认','保存'].includes(text(e)));
        if(b.length!==1){globalThis[ruleKey]=p;ruleCancel();throw Error('原生提交按钮不唯一');}p.button=b[0];
      }
      if(!p.button)throw Error('没有本任务暂停入口');globalThis[ruleKey]=p;
      if(p.input){
        set(p.input,i.after);await pause(100);
        if(Number(p.input.value)!==Number(i.after)||!p.input.checkValidity()){ruleCancel();throw Error('目标值未通过原生表单校验，已取消');}
      }
      return {ok:true,prepared:true,nonce:p.nonce,observation};
    }
    if(phase!=='rule_submit')throw Error('规则阶段无效');
    const p=globalThis[ruleKey];
    if(!p||p.id!==c.action.id||p.nonce!==request.nonce||Date.now()-p.at>60000||Date.now()>=Date.parse(c.expires_at))throw Error('原表单准备已过期');
    const unchanged=()=>ruleBinding(ruleTarget()?.surface||{})===ruleBinding(p.before)&&p.button.isConnected&&p.other?.every(x=>x.element.isConnected&&x.element.value===x.value&&x.element.checked===x.checked)!==false;
    if(!unchanged())throw Error('非目标字段或任务设置已变化');
    if(p.input){set(p.input,i.after);await pause(100);if(Number(p.input.value)!==Number(i.after)||!p.input.checkValidity())throw Error('目标值未通过原生校验');}
    if(p.root&&quotaRejection(p.root,i)){ruleCancel();throw Error('平台可追加预算不足，本条未提交，继续处理暂停任务');}
    const permit=await chrome.runtime.sendMessage({action:'rule-final-check',id:c.action.id,nonce:p.nonce});
    if(permit?.allow!==true||!unchanged())throw Error('最终网页提交许可被拒绝');
    p.button.click();
    if(p.root){
      for(let n=0;n<20&&visible(p.root);n++){
        await pause(100);
        if(text(p.root).includes('不得超过当前可用预算')){const proof=quotaRejection(p.root,i);if(proof){ruleCancel();return {ok:true,submitted:false,rejected:true,observation:{validation_rejection:proof}};}}
      }
      if(visible(p.root)){ruleCancel();throw Error('提交后表单仍未关闭，已取消遗留窗口；原提交只回读，不重发');}
    }
    if(i.action==='pause_task'){
      await pause(100);
      const confirms=roots().filter(r=>/暂停/.test(text(r))&&!r.querySelector('input:not([type="checkbox"])')).flatMap(r=>[...r.querySelectorAll('button')].filter(b=>enabled(b)&&['确定','确认'].includes(text(b))));
      if(confirms.length===1){identity();if(Date.now()<Date.parse(c.expires_at))confirms[0].click();}
    }
    delete globalThis[ruleKey];return {ok:true,submitted:true};
  }
  async function finishChaseForm(root,batch,obs,selected,selectedIds,fromHome=false){
    const pickerProof=[];
    if(apiBatch&&(fromHome||batch.material_ids.length>1)){
      const add=exact(root,'添加视频');if(add.length!==1)throw Error('原生添加视频入口不唯一');
      globalThis[key].picker_open=true;add[0].click();
      let ready=false;for(let n=0;n<40;n++){try{videoPicker();ready=true;break;}catch{await pause(100);}}
      if(!ready)throw Error('添加视频选择器未打开');
      let count=0;for(const mid of batch.material_ids)if(!selectedIds.includes(mid))pickerProof.push(await addPickerMaterial(mid,++count));
      click(videoPicker(),'确定');root=await chaseForm();globalThis[key].root=root;globalThis[key].picker_open=false;
    }
    const ids=()=>[...text(root).matchAll(/素材ID[：:]\s*(\d{6,30})/g)].map(m=>m[1]);
    // The native dialog mounts before its asynchronous material cards.
    // Validate the full immutable set after those cards finish appearing.
    const materialDeadline=Date.now()+5000;
    while(!same(ids(),batch.material_ids)&&Date.now()<materialDeadline)await pause(100);
    if(!same(ids(),batch.material_ids))throw Error('表单素材集合不一致：期望'+batch.material_ids.length+'条，实际加载'+ids().length+'条；未提交');
    const after=batch.after;await chooseRadio(root,after.chase_type);await pause(250);
    let audience;
    if(after.chase_type==='控成本追投'&&after.audience){
      if(typeof hybridAudienceDom!=='function')throw Error('当前执行脚本不支持定向，未提交');
      audience=await hybridAudienceDom(root,after.audience,{text,visible,exact,pause,clickNative,roots,chaseForm});
      root=audience.root;globalThis[key].root=root;
    }
    const fields=[];globalThis[key].fields=fields;
    const fill=(label,value)=>{const input=labelInput(root,label);set(input,value);fields.push({input,value:String(value)});};
    const fillBid=value=>{
      const input=chaseBidInput(root);if(!input)throw Error('直播间成交出价字段未唯一明确');
      set(input,value);fields.push({input,value:String(value)});
    };
    if(after.chase_type==='放量追投'){fill('调控总预算',after.total_budget);fill('调控时长',after.control_duration);}
    else{
      const goal=after.optimization_target==='综合ROI'?'综合营销ROI':after.optimization_target;
      await chooseRadio(root,goal);await pause(250);
      fill('调控日预算',after.daily_budget);
      if(goal==='直播间成交')fillBid(after.bid_value);else fill(goal+'目标',after.bid_value);
    }
    fill('任务名称',batch.task_name);
    const nativeBudget=()=>{
      const quota=text(root).match(/当前最多可再追加预算\s*([\d,.]+)\s*元/);
      if(quota&&Number(quota[1].replace(/,/g,''))<Number(after.daily_budget??after.total_budget))
        throw Error('平台当前可追加追投预算'+quota[1]+'元，小于本批预算'+(after.daily_budget??after.total_budget)+'元；母计划'+c.plan_id+'，未提交');
    };
    nativeBudget();
    const submits=[...root.querySelectorAll('button')].filter(e=>enabled(e)&&['提交','确认','创建','开始追投'].includes(text(e)));
    if(submits.length!==1||fields.some(f=>f.input.value!==f.value||!f.input.checkValidity())||!same(ids(),batch.material_ids))throw Error('原生表单参数尚未核验');
    const nonce=crypto.randomUUID(),prev=globalThis[key]||{},api_search=prev.api_search,home_context=prev.home_context||(fromHome?request.home_context:undefined);
    globalThis[key]={root,fields,selected,selected_ids:fromHome?null:selectedIds,ids,batch,button:submits[0],nonce,action_id:c.action.id,api_search,nativeBudget,...(home_context?{home_context}:{}),...(audience?{audience_check:audience.check}:{})};
    // Detail identity is the source of the binding. The actual home row is
    // independently read again after all picker/form work, immediately before
    // requesting a submit permit. Never relabel the historical detail time.
    if(fromHome)identity();
    const observation=fromHome?{entry_source:'home_plus',selection_source:'official_api',preflight:request.detail_preflight,binding:request.home_context,home:c.executor_kind==='ego'&&c.direct_home?currentHomeProof:request.home_proof,form_material_ids:ids()}:obs;
    if(fromHome&&request.detail_preflight?.kind==='direct_home_identity')observation.picker_materials=pickerProof;
    if(audience)observation.audience=audience.proof;
    return {ok:true,prepared:true,nonce,observation};
  }
  try {
    if(phase==='home_identity'){
      const u=identity();if(roots().length)throw Error('当前有编辑窗口，保留输入');
      const body=text(document.body),budget=body.match(/预算[（(]元[)）][：:]?\s*(?:每日)?\s*([\d,.]+)/);
      const headers=[...document.querySelectorAll('.oc-promotion-key-info-bar')].filter(e=>visible(e)&&new RegExp('计划\\s*ID[：:]\\s*'+c.plan_id+'(?!\\d)').test(text(e)));
      if(headers.length>1)throw Error('母计划状态栏未唯一明确');
      const states=headers.length?[...new Set([...headers[0].querySelectorAll('span,div')].filter(e=>visible(e)&&/^(?:投放中|已暂停|已结束|已删除|已冻结|审核中)$/.test(text(e))).map(text))]:[];
      if(states.length===1&&states[0]!=='投放中')throw Error('母计划当前'+states[0]+'，未打开追投');
      const legacy=[...document.querySelectorAll('.col-status-switch .ovui-switch')].filter(visible);
      const switches=legacy.length?legacy:[...document.querySelectorAll('[role="switch"]')].filter(e=>visible(e)&&!e.closest('table'));
      const running=headers.length?states.length===1&&states[0]==='投放中':switches.length===1&&(/\bovui-switch--checked\b/.test(switches[0].className)||switches[0].getAttribute('aria-checked')==='true');
      const heads=[...document.querySelectorAll('h1')].filter(visible).map(text),entry=heads.includes('千川乘方')?'chengfang':heads.includes('全域投放')||u.pathname==='/uni-prom/detail'?'uni_promotion':null;
      if(!budget||!number(budget[1])||!entry||!running||!/(?:综合营销|净成交|整体支付|支付)ROI目标[：:]\s*[\d.]+/.test(body))throw Error('主页入口的母计划预算、状态或投放方式未明确');
      const owners=[...new Set([...body.matchAll(/抖音号\s*ID[：:]\s*((?:zlp_)?\d{6,30})/g)].map(m=>m[1]).filter(nativeOwner))],anchor=officialParentAnchor();
      if(owners.length>1||owners.length===1&&anchor&&/^\d+$/.test(owners[0])&&owners[0]!==anchor.aweme_id)throw Error('可见抖音号与官方计划对应关系冲突');
      const owner=owners[0]||anchor?.aweme_id;if(!owner||homeLinks().length!==1)throw Error('主页入口或母计划对应关系未唯一明确；未搜索素材');
      return {ok:true,preflight_ready:true,home_supported:true,observation:{kind:'direct_home_identity',account_id:c.account_id,plan_id:c.plan_id,group:c.group,path:u.pathname,
        parent_budget:number(budget[1]),running:true,delivery_entry:entry,parent_bid_type:'SMART_BID_CUSTOM',captured_at:new Date().toISOString()},
        binding:{account_id:c.account_id,plan_id:c.plan_id,group:c.group,aweme_id:owner,aweme_source:owners.length?'visible_detail':'official_plan_list',detail_url:location.href,
          captured_at:new Date().toISOString(),parent_budget:number(budget[1]),delivery_entry:entry,parent_bid_type:'SMART_BID_CUSTOM'}};
    }
    if(phase==='task_inventory'){
      const obs=observe('tasks'),t=table('tasks');
      if(!t.headers.includes('素材'))throw Error('在投任务清单缺少素材列，未搜索素材页');
      for(const target of document.querySelectorAll('[data-auto-id="promotion-media-group"],[data-auto-id="promotion-media-group"] [data-auto-id="oc-media"]'))for(const type of ['mouseout','mouseleave'])target.dispatchEvent(new MouseEvent(type,{bubbles:type==='mouseout',view:window}));
      await pause(250);
      for(let index=0;index<t.rows.length;index++){
        const cell=t.cell(t.rows[index],'素材'),value=text(cell),count=value.match(/共\s*(\d+)\s*条视频/),read=e=>[...text(e).matchAll(/素材ID[：:]\s*(\d{6,30})/g)].map(m=>m[1]);
        const roomGroup=cell.querySelector('[data-auto-id="promotion-media-group"]'),roomInfo=roomGroup?.querySelector('[class*="media-group-info"]');
        const roomLabels=roomInfo?[...roomInfo.querySelectorAll('[class*="typography-value-int"]')].map(text):[];
        const liveRoom=!count&&roomGroup?.querySelectorAll('[data-auto-id="oc-media"]').length===1&&!roomGroup.querySelector('[class*="media-thumb-video-icon"]')
          &&roomLabels.length===2&&!!roomLabels[0]&&/^ID[：:]\s*\d{6,30}$/.test(roomLabels[1])&&!/素材ID/.test(value);
        if(liveRoom){obs.rows[index].material_ids=[];obs.rows[index].material_type='LIVE_ROOM';continue;}
        let ids=read(cell),from_api=false;
        const apiIds=c.inventory_api_materials?.[obs.rows[index].id];
        // “共0条视频”是已知的过期渲染（2026-11-11 实页显示0条、实为5条）。只有官方API
        // 任务清单能补齐该行；不得仅凭单元格断言任务没有素材。
        const want=count?Number(count[1]):null;
        const apiOk=Array.isArray(apiIds)&&apiIds.length>0&&apiIds.length<=20&&new Set(apiIds).size===apiIds.length&&apiIds.every(id=>/^\d{6,30}$/.test(String(id)));
        if(apiOk&&(want===null||want===0||want===apiIds.length)&&ids.length!==apiIds.length){ids=apiIds.map(String);from_api=true;}
        if(!from_api&&count&&Number(count[1])>0&&ids.length!==Number(count[1])){
          const group=cell.querySelector('[data-auto-id="promotion-media-group"]'),detail=group?.querySelector('[class*="media-group-detail"]'),more=group?.querySelector('[class*="media-group-list-ellipsis"]');
          const list=group?.querySelector('[class*="media-group-list"]'),links=[...cell.querySelectorAll('span,button,[role="button"]')].filter(e=>enabled(e)&&new RegExp('^共'+count[1]+'条视频$').test(text(e))&&![...e.children].some(ch=>text(ch)===text(e)));
          const media=!more?group?.querySelector('[data-auto-id="oc-media"]'):null;
          const targets=[...new Set([detail,group,list,media,more,links.length===1?links[0]:null].filter(Boolean))];if(!targets.length)throw Error('在投任务素材明细未加载，未搜索素材页');
          const before=new Map([...document.querySelectorAll('[role="tooltip"],[class*="popover"],[class*="popper"]')].filter(visible).map(e=>[e,text(e)]));
          if(c.executor_kind==='ego'){
            const target=more||media;if(!target)throw Error('在投任务原生缩略图未加载');
            const marker=c.id+'-inventory-'+obs.rows[index].id;target.setAttribute('data-qcx-inventory',marker);
            try{const reply=await chrome.runtime.sendMessage({action:'hybrid-native-inventory-hover',run_id:c.run_id,command_id:c.id,task_id:obs.rows[index].id,open_more:!!more});if(reply?.ok!==true)throw Error('Ego在投素材明细未展开，任务'+obs.rows[index].id+'：'+String(reply?.message||'原生读取未完成').slice(0,200));if(reply.fresh_hover_task===obs.rows[index].id)before.clear();}
            finally{target.removeAttribute('data-qcx-inventory');}
          }else{
            for(const target of targets)for(const type of ['mouseover','mouseenter'])target.dispatchEvent(new MouseEvent(type,{bubbles:type==='mouseover',view:window}));
            if(more)clickNative(more);else if(!detail&&links.length===1)links[0].click();
          }
          try{const until=Date.now()+3000;while(Date.now()<until){
            const candidates=[...document.querySelectorAll('[role="tooltip"],[class*="popover"],[class*="popper"]')].filter(e=>visible(e)&&(!before.has(e)||before.get(e)!==text(e))).map(read).filter(a=>a.length===Number(count[1])&&new Set(a).size===a.length);
            const unique=new Map(candidates.map(a=>[[...a].sort().join(','),a]));if(unique.size===1){ids=[...unique.values()][0];break;}await pause(100);
          }}finally{for(const target of targets)for(const type of ['mouseout','mouseleave'])target.dispatchEvent(new MouseEvent(type,{bubbles:type==='mouseout',view:window}));await pause(150);}
        }
        if(from_api&&ids.length&&new Set(ids).size===ids.length){obs.rows[index].material_ids=ids;obs.rows[index].material_type='VIDEO';continue;}
        if(count?(Number(count[1])===0||ids.length!==Number(count[1])):!ids.length&&!/直播间画面/.test(value))throw Error('在投任务视频ID未完整核对，任务'+obs.rows[index].id+'，素材列“'+value.slice(0,100)+'”，读到'+ids.length+'条，API任务素材清单亦不可用；未搜索素材页');
        if(new Set(ids).size!==ids.length)throw Error('在投任务素材ID重复');obs.rows[index].material_ids=ids;obs.rows[index].material_type=ids.length?'VIDEO':'LIVE_ROOM';
      }
      return {ok:true,observation:obs};
    }
    if(['identity','view'].includes(phase)){
      const u=new URL(location.href);
      const detail=['/uni-prom/detail','/overall-prom/detail'].includes(u.pathname)&&u.searchParams.getAll('adId').length===1&&u.searchParams.get('adId')===c.plan_id;
      const home=['/uni-prom','/overall-prom'].includes(u.pathname)&&request.home_context?.account_id===c.account_id&&request.home_context?.plan_id===c.plan_id;
      if(u.origin==='https://qianchuan.jinritemai.com'&&(detail||home)&&u.searchParams.getAll('aavid').length===1&&u.searchParams.get('aavid')===c.account_id)await dismissKnownNotice();
    }
    if(phase==='editor_guard')return {ok:true,...editorInfo(),editor_task_name:c.executor_kind==='ego'?(namedChaseEditor()?.name||null):null};
    if(phase==='editor_recover')return await recoverEditor();
    if(phase==='cleanup'&&globalThis[key]?.home_context&&c.executor_kind==='ego'){
      return await recoverEditor();
    }
    identity();
    if(phase.startsWith('rule_'))return await ruleAction();
    if(phase==='identity')return {ok:true,...editorInfo()};
    if(phase==='home_go'){
      if(roots().length)throw Error('当前有编辑窗口，保留输入');
      const selected=text(document.body).match(/已选\s*(\d+)\s*个?\s*视频/);
      if(selected&&Number(selected[1])>0)throw Error('页面已有手动选择，保留原选择');
      const links=homeLinks();
      if(links.length!==1)return {ok:false,message:'推直播间面包屑未唯一明确',fallback:true};
      const link=links[0].closest('a[href]');
      if(link){const next=new URL(link.href,location.href);if(link.target==='_blank'||next.origin!==location.origin||!['/uni-prom','/overall-prom'].includes(next.pathname))return {ok:false,message:'推直播间链接范围未核验',fallback:true};}
      if(c.executor_kind==='ego'){
        if(!/^[-\w]{1,80}$/.test(c.id||''))throw Error('首页导航命令标识无效');
        const target=link||links[0];target.setAttribute('data-qcx-home-link',c.id);
        return {ok:true,native_selector:'[data-qcx-home-link="'+c.id+'"]'};
      }
      clickNative(link||links[0]);return {ok:true,navigated:true};
    }
    if(phase==='view'){
      if(roots().length)throw Error('当前有编辑窗口，保留输入');
      const label=['tasks','lookup','verify'].includes(c.phase)?'调控':'素材';
      const tabs=[...document.querySelectorAll('[role="tab"],.ovui-tabs__tab')].filter(e=>visible(e)&&text(e)===label);
      if(tabs.length!==1)throw Error('详情标签不唯一');
      const selected=tabs[0].getAttribute('aria-selected')==='true'||/\bovui-tabs__tab--active\b/.test(tabs[0].className);
      if(!selected){clickNative(tabs[0].querySelector('.ovui-tabs__tab-btn')||tabs[0]);return {ok:true,changed:true};}
      if(label==='素材'){
        const videos=exact(document.body,'视频').filter(e=>/radio/.test(e.className)||e.getAttribute('role')==='radio');
        if(videos.length!==1)throw Error('视频素材类型控件未唯一明确');
        if(!/--checked|--active/.test(videos[0].className)&&videos[0].getAttribute('aria-checked')!=='true'){
          clickNative(videos[0]);return {ok:true,changed:true};
        }
        if(apiBatch)return {ok:true,changed:false};
        const p=pagination();if(p.size!==100){await choose(p.input,'100条/页');return {ok:true,changed:true};}
        const sort=spendSort();if(!sort.ready){if(!sort.arrow)throw Error('整体消耗降序控件未明确');clickNative(sort.arrow);return {ok:true,changed:true};}
      }else{
        const tool=exact(document.body,'素材/人群追投').filter(e=>/radio-item/.test(e.className));
        if(tool.length===1&&!/checked/.test(tool[0].className)){tool[0].click();return {ok:true,changed:true};}
        for(const label of ['追投方式：','优化目标：']){const input=labelInput(document.body,label);if(input?.value&&!norm(input.value).startsWith('全部')){await choose(input,'全部');return {ok:true,changed:true};}}
        if([...document.querySelectorAll('button')].some(b=>visible(b)&&/^更多筛选\s*\d+/.test(text(b))))throw Error('请清空额外任务筛选后再核对在投预算');
        const state=labelInput(document.body,'投放状态：'),wanted=c.phase==='tasks'?'调控中':'全部';
        if(!state)throw Error('任务状态筛选字段未明确');
        if(state&&(wanted==='全部'?!norm(state.value).startsWith('全部'):state.value!==wanted)){const changed=await chooseTaskState(state,wanted);if(changed)return {ok:true,changed:true};}
        const search=[...document.querySelectorAll('input')].filter(i=>visible(i)&&i.placeholder==='输入任务名称或ID搜索');
        if(search.length!==1)throw Error('任务搜索框不唯一');
        const wantedNeedle=c.phase==='lookup'?c.lookup_id:c.phase==='verify'?c.action.intent.browser_batch.task_name:'';
        const signature=[c.run_id,c.phase,wantedNeedle,c.document_cycle||0].join(':');
        let searchState=globalThis[searchKey];
        if(searchState?.signature!==signature)searchState=globalThis[searchKey]={signature,full_scan:false};
        const needle=searchState.full_scan?'':wantedNeedle;
        if(search[0].value!==needle||!searchState.committed){
          if(c.executor_kind==='ego'&&['lookup','verify'].includes(c.phase)){
            search[0].setAttribute('data-qcx-task-search',c.id);
            try{const reply=await chrome.runtime.sendMessage({action:'hybrid-native-task-search',run_id:c.run_id,command_id:c.id,needle});if(reply?.ok!==true)throw Error('原任务原生搜索未完成');}
            finally{search[0].removeAttribute('data-qcx-task-search');}
          }else{
          set(search[0],needle);
          // Commit the input after its native debounce. Clicking the custom
          // icon before that commit can search the preceding keyword.
          await pause(600);search[0].focus();
          for(const type of ['keydown','keypress','keyup'])search[0].dispatchEvent(new KeyboardEvent(type,{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
          }
          searchState.committed=true;
          return {ok:true,changed:true};
        }
        if(c.phase==='verify'&&needle&&!searchState.full_scan){
          let empty=false;try{empty=pagination().total===0;}catch{/* Still loading is not an empty search. */}
          if(empty){
            // A just-created task may be absent from native name search while
            // already present in the complete list. The same exact task name
            // and immutable material set are still required by verify().
            searchState.full_scan=true;searchState.committed=false;
            return {ok:true,changed:true};
          }
        }
        if(c.executor_kind==='ego'&&(c.rule_job&&(['scan','lifetime'].includes(c.rule_phase)||c.page_size===100)||!c.rule_job&&c.phase==='tasks'&&(c.page===1||c.inventory_page_size===10))){
          // Native hover reads take longer than a metric-only rule scan.
          // Bound each lease to ten task rows and retain complete pagination.
          const size=!c.rule_job&&c.phase==='tasks'&&c.inventory_page_size===10?10:100;
          const p=pagination();if(p.size!==size&&p.input){await choose(p.input,size+'条/页');return {ok:true,changed:true};}
        }
      }
      return {ok:true,changed:false};
    }
    if(phase==='page'){
      if(roots().length)throw Error('当前有编辑窗口，保留输入');
      const p=pagination();if(p.page===c.page)return {ok:true,changed:false};
      // A name search or a changed task list may shrink to one page while
      // the server retains an earlier full-list cursor. Read the actual page;
      // its exact task/ID receipt or last-page evidence repairs that cursor.
      if(c.phase==='verify'&&c.page>Math.max(1,Math.ceil(p.total/p.size)))return {ok:true,changed:false,cursor_reconciled:true};
      if(!p.box)throw Error('任务搜索为空，未找到指定页；未提交');
      const kind=['materials','prepare','dry_prepare'].includes(c.phase)?'materials':'tasks';
      const previous=rowSignature(kind);
      const numbers=[...p.box.querySelectorAll('li,button,[role="button"]')].filter(e=>enabled(e)&&text(e)===String(c.page)&&!/disabled/.test(e.className));
      if(numbers.length===1){
        globalThis[navigationKey]={kind,page:c.page,previous};clickNative(numbers[0]);return {ok:true,changed:true};
      }
      const icon=p.box.querySelector(c.page>p.page?'[class*="next-icon"]':'[class*="prev-icon"]');
      const button=icon?.closest('li,button');
      if(!button||!enabled(button)||/disabled/.test(button.className))throw Error('无法翻到指定页');
      globalThis[navigationKey]={kind,page:p.page+(c.page>p.page?1:-1),previous};
      clickNative(button);return {ok:true,changed:true};
    }
    if(phase==='read')return {ok:true,observation:observe(['materials','prepare','dry_prepare'].includes(c.phase)?'materials':'tasks')};
    if(phase==='prepare'){
      if(request.home_context){
        const owned=globalThis[key],batch=c.action.intent.browser_batch;
        if(!apiBatch||!request.detail_preflight||!request.home_proof||owned?.action_id!==c.action.id||!owned.root?.isConnected||!roots().includes(owned.root))throw Error('首页空任务窗口未绑定本批，保留输入');
        return await finishChaseForm(owned.root,batch,request.detail_preflight,[],[],true);
      }
      if(roots().length)throw Error('当前有编辑窗口，保留输入');
      const batch=c.action.intent.browser_batch;
      let obs,t;
      if(apiBatch){
        const deadline=Date.now()+30000;let last='等待素材表就绪';
        while(Date.now()<deadline){
          identity();if(roots().length)throw Error('当前有编辑窗口，保留输入');
          try{t=table('material_ids');materialSearch();break;}catch(error){last=error.message;}
          await pause(150);
        }
        if(!t)throw Error(last+'；等待素材表超时，未提交');
      }else t=table('materials');
      if(!apiBatch){obs=observe('materials');if(obs.page!==batch.page||obs.date_start!==batch.date_start||obs.date_end!==batch.date_end)throw Error('本批日期或页码不符');}
      const selected=[],selectedIds=[];
      for(const row of t.rows){const box=selectionBox(row);if(box.checked)throw Error('页面已有手动选择，保留原选择');}
      const manual=text(document.body).match(/已选\s*(\d+)\s*个?\s*视频/);if(manual&&Number(manual[1])>0)throw Error('页面已有手动选择，保留原选择');
      globalThis[key]={selected,selected_ids:selectedIds,root:null,action_id:c.action.id};
      const proofs=[],runningIds=[];
      if(apiBatch)globalThis[key].api_search={previous:materialSearch().value};
      if(apiBatch){
        const clears=exact(document.body,'清空').filter(e=>!e.closest('table')&&!roots().some(r=>r.contains(e)));
        if(clears.length>1)throw Error('素材筛选清空入口不唯一');
        if(clears.length===1){
          clears[0].click();await pause(250);let ready=false;
          for(let n=0;n<60;n++){try{table('material_ids');materialSearch();ready=true;break;}catch{await pause(100);}}
          if(!ready)throw Error('清空素材查看筛选后表格尚未就绪');
        }
      }
      for(const mid of batch.material_ids){
        identity();
        if(apiBatch){
          const found=await findMaterial(mid),box=selectionBox(found.row);
          if(box.checked)throw Error('页面已有手动选择，保留原选择');
          if(!found.proof)throw Error('本批素材状态尚未核对 '+mid);
          if(!found.proof.approved)throw Error('本批素材审核未通过 '+mid);
          if(!found.proof.selectable)throw Error('本批素材左侧选择框已禁用 '+mid);
          if(!batch.allow_running_materials&&['调控中','追投中'].includes(found.proof.chase_status))runningIds.push(mid);
          proofs.push(found.proof);obs=found.obs;
          if(request.preflight_only||runningIds.length)continue;
          // Native bulk selection hides the main search toolbar. Check every
          // ID first, select only the last seed, then add the rest in the
          // native dialog's persistent-search video picker.
          if(mid!==batch.material_ids.at(-1))continue;
          box.click();selected.push(box);selectedIds.push(mid);
          let synchronized=false;const deadline=Date.now()+5000;
          while(Date.now()<deadline){
            await pause(100);identity();
            try{
              const current=table('material_ids').rows.find(r=>new RegExp('素材ID[：:]\\s*'+mid+'(?=\\s|$)').test(text(r)));
              if(current?.querySelector('input[type="checkbox"]')?.checked){synchronized=true;break;}
            }catch{/* Selection can redraw the native row under a loading mask. */}
          }
          if(!synchronized)throw Error('素材勾选未同步到页面，未提交');
          continue;
        }
        // Each selection may redraw the native table. Re-find live nodes and
        // let its controlled selection state settle before choosing the next.
        const live=table('materials');
        const candidates=live.rows.filter(r=>new RegExp('素材ID[：:]\\s*'+mid+'(?=\\s|$)').test(text(live.cell(r,'视频'))));
        if(candidates.length!==1)throw Error('本批素材不在当前页');
        const box=candidates[0].querySelector('input[type="checkbox"]');if(!box||box.disabled||!box.isConnected)throw Error('素材不能勾选');
        box.click();selected.push(box);selectedIds.push(mid);
        const deadline=Date.now()+2500;let synchronized=false;
        do{
          await pause(100);identity();
          const current=table('materials'),checked=current.rows.filter(r=>r.querySelector('input[type="checkbox"]')?.checked)
            .map(r=>text(current.cell(r,'视频')).match(/素材ID[：:]\s*(\d{6,30})/)?.[1]);
          synchronized=same(checked,selectedIds);
        }while(!synchronized&&Date.now()<deadline);
        if(!synchronized)throw Error('素材勾选未同步到页面，已停止本批，未提交');
      }
      if(runningIds.length)throw Error('提交前素材条件已变化：'+runningIds.map(mid=>'素材正在追投 '+mid).join('；'));
      if(apiBatch){
        const count=text(document.body).match(/已选\s*(\d+)\s*个?\s*视频/);
        if(count&&Number(count[1])!==selectedIds.length)throw Error('素材勾选未跨搜索保留，未提交');
        obs={...obs,selection_source:'official_api',selected_materials:proofs};
        if(obs.date_start!==batch.date_start||obs.date_end!==batch.date_end)throw Error('本批日期不符');
      }
      if(request.preflight_only){
        if(!apiBatch)throw Error('首页入口只支持本批API名单');
        const owners=[...new Set([...text(document.body).matchAll(/抖音号\s*ID[：:]\s*((?:zlp_)?\d{6,30})/g)].map(m=>m[1]).filter(nativeOwner))];
        const anchor=officialParentAnchor();
        if(owners.length>1||owners.length===1&&anchor&&/^\d+$/.test(owners[0])&&owners[0]!==anchor.aweme_id)throw Error('可见抖音号与官方计划对应关系冲突，未进入首页');
        const owner=owners[0]||anchor?.aweme_id;
        const links=homeLinks();
        return {ok:true,preflight_ready:true,home_supported:!!owner&&links.length===1,home_diagnostic:{owner_count:owners.length,link_count:links.length,official_anchor:!!anchor},observation:obs,
          binding:owner?{account_id:c.account_id,plan_id:c.plan_id,group:c.group,aweme_id:owner,aweme_source:owners.length?'visible_detail':'official_plan_list',detail_url:location.href,
            captured_at:new Date().toISOString(),parent_budget:obs.parent_budget,delivery_entry:obs.delivery_entry,parent_bid_type:obs.parent_bid_type}:null};
      }
      let entries=[];
      for(let n=0;n<40;n++){
        entries=exact(document.body,'追投').filter(e=>!e.closest('table')&&!roots().some(r=>r.contains(e)));
        if(entries.length===1)break;await pause(100);
      }
      if(entries.length!==1)throw Error('批量追投入口不唯一');entries[0].click();
      let root=await chaseForm();globalThis[key].root=root;
      return await finishChaseForm(root,batch,obs,selected,selectedIds);
    }
    if(phase==='submit'){
      const p=globalThis[key];if(!p||p.submitted||p.nonce!==request.nonce||p.action_id!==c.action.id)throw Error('网页提交令牌不符');
      if(p.audience_check&&!p.audience_check())throw Error('提交前定向条件已变化，未提交');
      p.nativeBudget?.();
      if(p.fields.some(f=>!visible(f.input)||f.input.value!==f.value)||!same(p.ids(),p.batch.material_ids)||!enabled(p.button))throw Error('提交前字段变化');
      const allow=await chrome.runtime.sendMessage({action:'hybrid-final-check',nonce:p.nonce,id:p.action_id});
      if(allow?.allow!==true)throw Error('网页最终许可未通过');
      identity();if(p.fields.some(f=>f.input.value!==f.value)||!same(p.ids(),p.batch.material_ids)||p.audience_check&&!p.audience_check())throw Error('最终参数已变化');
      p.submitted=true;p.button.click();if(c.executor_kind!=='ego'||!visible(p.root))delete globalThis[key];
      return {ok:true,submitted:true};
    }
    if(phase==='verify'){
      const obs=observe('tasks'),t=table('tasks'),b=c.action.intent.browser_batch;
      const rows=t.rows.filter(r=>text(t.cell(r,'任务名称')).split('\n').map(x=>x.trim()).includes(b.task_name));
      if(!rows.length)return {ok:true,observation:obs};
      if(rows.length!==1)throw Error('原创建任务尚未唯一回读，未重复创建');
      const row=rows[0],cell=label=>text(t.cell(row,label));
      if(cell('追投方式')!==b.after.chase_type||Number(number(cell('调控预算')))!==Number(b.after.daily_budget??b.after.total_budget))throw Error('回读预算或追投方式不符');
      if(b.after.chase_type==='放量追投'){
        if(Number(number(cell('调控时长小时').split('\n')[0]))!==Number(b.after.control_duration))throw Error('回读时长不一致');
      }else{
        const goal=b.after.optimization_target==='综合ROI'?'综合营销ROI':b.after.optimization_target;
        const label=goal==='直播间成交'?'出价':goal+'目标';
        if(Number(number(cell(label)))!==Number(b.after.bid_value))throw Error('回读目标或出价不一致');
      }
      const material=t.cell(row,'素材');let ids=[...text(material).matchAll(/素材ID[：:]\s*(\d{6,30})/g)].map(m=>m[1]);
      if(!same(ids,b.material_ids)){
        if(/^共\s*0\s*条视频$/.test(text(material)))throw Error('原任务素材明细暂显示0条，等待平台明细；保留原单，不重复创建');
        const links=[...material.querySelectorAll('button,[role="button"],span,div')].filter(e=>enabled(e)&&new RegExp('^共'+b.material_ids.length+'条视频$').test(text(e))&&!Array.from(e.children).some(ch=>text(ch)===text(e)));
        if(links.length!==1)throw Error('需展开原任务素材以核验ID');
        const group=material.querySelector('[data-auto-id="promotion-media-group"]'),detail=group?.querySelector('[class*="media-group-detail"]');
        const list=group?.querySelector('[class*="media-group-list"]'),more=group?.querySelector('[class*="media-group-list-ellipsis"]');
        const targets=[...new Set([detail,group,list,more,links[0]].filter(Boolean))];
        for(const target of targets)for(const type of ['mouseover','mouseenter'])target.dispatchEvent(new MouseEvent(type,{bubbles:type==='mouseover',view:window}));
        // The five-video native group opens from the thumbnail ellipsis;
        // clicking its count label does not open the material ID popover.
        if(more)clickNative(more);else if(!detail)links[0].click();
        const deadline=Date.now()+5000;
        try{
          while(Date.now()<deadline){
            const popovers=[...document.querySelectorAll('[role="tooltip"],[class*="popover"],[class*="popper"]')].filter(visible);
            const sets=popovers.map(p=>[...text(p).matchAll(/素材ID[：:]\s*(\d{6,30})/g)].map(m=>m[1])).filter(m=>same(m,b.material_ids));
            if(sets.length){ids=sets[0];break;}await pause(100);
          }
        }finally{for(const target of targets)for(const type of ['mouseout','mouseleave'])target.dispatchEvent(new MouseEvent(type,{bubbles:type==='mouseout',view:window}));}
        if(!same(ids,b.material_ids))throw Error('原任务素材ID尚未全部回读');
      }
      const id=obs.rows.find(r=>r.id===cell('任务名称').match(/ID[：:]\s*(\d+)/)?.[1])?.id;
      if(!id)throw Error('原任务ID缺失');
      return {ok:true,observation:{...obs,rows:[{id,name:b.task_name,material_ids:ids,after:b.after,status:cell('调控状态').split('\n')[0]}]}};
    }
    if(phase==='cleanup'){
      const p=globalThis[key];if(p){
        if(p.picker_open){
          let picker;try{picker=videoPicker();}catch{}
          if(picker)click(picker,'确定');
          p.root=await chaseForm();p.picker_open=false;
        }
        if(p.root?.isConnected){const cancel=exact(p.root,'取消');if(cancel.length===1)cancel[0].click();}
        if(p.root)for(let n=0;n<30&&visible(p.root);n++)await pause(100);
        if(p.root&&visible(p.root))throw Error('本次追投窗口尚未关闭，未提交');
        if(p.selected_ids){
          for(const mid of p.selected_ids){
            if(p.api_search){
              // The main ID search is hidden while any seed is checked.
              // Clear our visible seed before attempting another search.
              const current=table('material_ids'),rows=current.rows.filter(r=>new RegExp('素材ID[：:]\\s*'+mid+'(?=\\s|$)').test(text(current.cell(r,'视频'))));
              const row=rows.length===1?rows[0]:(await findMaterial(mid)).row,box=row.querySelector('input[type="checkbox"]');
              if(box?.checked){box.click();await pause(100);}
              continue;
            }
            const current=table('materials'),rows=current.rows.filter(r=>new RegExp('素材ID[：:]\\s*'+mid+'(?=\\s|$)').test(text(current.cell(r,'视频'))));
            const box=rows.length===1?rows[0].querySelector('input[type="checkbox"]'):null;
            if(box?.checked){box.click();await pause(100);}
          }
          let current;for(let n=0;n<30;n++){try{current=table(p.api_search?'material_ids':'materials');break;}catch{await pause(100);}}
          if(!current)throw Error('本次素材勾选清理后表格尚未就绪');
          if(current.rows.some(r=>r.querySelector('input[type="checkbox"]')?.checked&&p.selected_ids.includes(text(current.cell(r,'视频')).match(/素材ID[：:]\s*(\d{6,30})/)?.[1])))throw Error('本次素材勾选尚未清理，请保留页面重试清理');
        }else for(const box of p.selected||[])if(box.isConnected&&box.checked){box.click();await pause(100);}
        if(p.api_search){
          let input;for(let n=0;n<30;n++){try{input=materialSearch();break;}catch{await pause(100);}}
          if(!input)throw Error('本次勾选清理后搜索栏尚未恢复');
          set(input,p.api_search.previous);await pause(600);input.focus();
          for(const type of ['keydown','keypress','keyup'])input.dispatchEvent(new KeyboardEvent(type,{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
        }
        delete globalThis[key];
      }
      return {ok:true};
    }
    throw Error('不支持的网页阶段');
  } catch(error){return {ok:false,message:error.message,submitted:false,...(phase==='identity'?editorInfo():{})};}
}
