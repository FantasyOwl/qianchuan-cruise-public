/* Open the bound live parent's material chase from its home-row plus.
 * This adapter never selects the large new-plan button or submits a task.
 */
async function hybridHomeDom(phase,request){
  const c=request.command,b=request.binding,origin='https://qianchuan.jinritemai.com';
  const text=e=>(e?.innerText||e?.textContent||'').trim();
  const visible=e=>!!e?.isConnected&&e.getClientRects().length>0&&getComputedStyle(e).display!=='none'&&getComputedStyle(e).visibility!=='hidden';
  const enabled=e=>visible(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true';
  const norm=s=>String(s).replace(/\s/g,'');
  const viewedFilters=new WeakSet();
  const nativeOwner=value=>/^\d{6,30}$/.test(value||'')||c.executor_kind==='ego'&&/^zlp_\d{6,30}$/.test(value||'');
  const roots=()=>[...document.querySelectorAll('[role="dialog"],[aria-modal="true"],[class*="-modal__wrap"],.ovui-modal,.ovui-drawer,[class*="modal-content"]')].filter(visible).filter((e,i,a)=>!a.some(other=>other!==e&&other.contains(e)));
  const exact=(root,label)=>[...root.querySelectorAll('button,[role="button"],span,div,a')].filter(e=>enabled(e)&&text(e)===label&&![...e.children].some(ch=>text(ch)===label));
  const click=e=>{
    if(!enabled(e))throw Error('首页创建入口不可用');
    e.scrollIntoView?.({block:'nearest'});
    for(const type of ['mousedown','mouseup'])e.dispatchEvent(new MouseEvent(type,{bubbles:true,cancelable:true,view:window}));
    if(typeof e.click==='function')e.click();else e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));
  };
  const pause=async duration_ms=>{
    const result=await chrome.runtime.sendMessage({action:'hybrid-wait',run_id:c.run_id,command_id:c.id,duration_ms});
    if(result?.ok!==true)throw Error('首页等待已中断'+(result?.message?'：'+String(result.message).slice(0,180):'')+'，未提交');
  };
  const fail=(message,fallback=false)=>({ok:false,message,fallback,submitted:false});
  function scope(){
    const u=new URL(location.href),source=new URL(b?.detail_url||'about:blank'),age=Date.now()-Date.parse(b?.captured_at);
    if(u.origin!==origin||!['/uni-prom','/overall-prom'].includes(u.pathname)||u.searchParams.getAll('aavid').length!==1||u.searchParams.get('aavid')!==c.account_id||u.searchParams.has('adId')&&(u.searchParams.getAll('adId').length!==1||!['',c.plan_id].includes(u.searchParams.get('adId'))))throw Error('首页账户或入口不符');
    const sourceLimit=c.executor_kind==='ego'&&c.direct_home&&c.execution_scope==='f5_create_only'?180000:90000;
    if(b?.account_id!==c.account_id||b?.plan_id!==c.plan_id||b?.group!==c.group||!nativeOwner(b?.aweme_id)||!Number.isFinite(age)||age<0||age>sourceLimit||source.origin!==origin||!['/uni-prom/detail','/overall-prom/detail'].includes(source.pathname)||source.searchParams.getAll('aavid').length!==1||source.searchParams.get('aavid')!==c.account_id||source.searchParams.getAll('adId').length!==1||source.searchParams.get('adId')!==c.plan_id)throw Error('首页缺少新鲜的母计划对应关系');
    if(b.aweme_source==='official_plan_list'){
      const a=c.parent_anchor,at=Date.now()-Date.parse(a?.captured_at);
      if(a?.kind!=='unique_official_plan_anchor'||a.account_id!==c.account_id||a.plan_id!==c.plan_id||a.aweme_id!==b.aweme_id||!Number.isFinite(at)||at<0||at>600000)throw Error('首页抖音号缺少新鲜官方计划对应关系');
    }
    const labels=[...document.querySelectorAll('span,div,p')].filter(e=>visible(e)&&!e.closest('table,[role="dialog"],[aria-modal="true"],.ovui-breadcrumb,.c-promotion-breadcrumb,.oc-breadcrumb-select,.aweme-info,.content-info-container')&&text(e).length<100).map(e=>norm(text(e)));
    const accounts=new Set(labels.map(s=>s.match(/^(?:账户)?ID[：:](\d{6,30})$/)?.[1]).filter(Boolean));
    if(accounts.size&&(accounts.size!==1||!accounts.has(c.account_id)))throw Error('首页可见账户ID冲突');
    if(!accounts.size){
      const proof=c.account_binding,at=Date.now()-Date.parse(proof?.verified_at);
      if(proof?.kind!=='official_api_material_scope'||proof.account_id!==c.account_id||proof.plan_id!==c.plan_id||!Number.isFinite(at)||at<0||at>600000)throw Error('首页账户栏收起且API归属证明已失效');
    }
    return u;
  }
  function boundRows(){
    const matches=[];
    for(const row of document.querySelectorAll('tr,[role="row"],.ovui-table__row')){
      if(!visible(row)||row.closest('[role="dialog"],[aria-modal="true"],[class*="-modal__wrap"],.ovui-modal'))continue;
      const plans=new Set(),explicitOwners=new Set();let owner=false;
      if(c.executor_kind==='ego'&&/^\d{6,30}$/.test(row.getAttribute('data-row-key')||''))plans.add(row.getAttribute('data-row-key'));
      for(const e of [row,...row.querySelectorAll('[id],[data-plan-id],[data-ad-id],[data-aweme-id],a[href]')]){
        const named=e.id?.match(/^ad-name-(\d{6,30})$/);if(named)plans.add(named[1]);
        for(const key of ['data-plan-id','data-ad-id']){const value=e.getAttribute(key);if(/^\d{6,30}$/.test(value||''))plans.add(value);}
        const explicitOwner=e.getAttribute('data-aweme-id');if(nativeOwner(explicitOwner))explicitOwners.add(explicitOwner);
        if(e.id===b.aweme_id||explicitOwner===b.aweme_id)owner=true;
        if(e.matches('a[href]'))try{
          const link=new URL(e.getAttribute('href'),location.href),pid=link.searchParams.get('adId');
          if(link.origin===origin&&['/uni-prom/detail','/overall-prom/detail'].includes(link.pathname)&&(!link.searchParams.has('aavid')||link.searchParams.get('aavid')===c.account_id)&&/^\d{6,30}$/.test(pid||''))plans.add(pid);
        }catch{}
      }
      for(const m of text(row).matchAll(/计划\s*ID[：:]\s*(\d{6,30})/g))plans.add(m[1]);
      if(plans.size>1||plans.size===1&&!plans.has(c.plan_id)||explicitOwners.size&&(explicitOwners.size!==1||!explicitOwners.has(b.aweme_id)))continue;
      if(plans.has(c.plan_id)||owner)matches.push({row,row_plan_id:plans.has(c.plan_id)?c.plan_id:null,row_aweme_id:owner?b.aweme_id:null});
    }
    return matches;
  }
  function proof(item){
    const words=text(item.row),budgets=[...words.matchAll(/每日预算\s*([\d,.]+)\s*元/g)];
    if(budgets.length!==1)throw Error('首页母计划预算未明确');
    const raw=budgets[0][1].replace(/,/g,'');if(!/^\d+(?:\.\d{1,2})?$/.test(raw)||Number(raw)<=0)throw Error('首页母计划预算无效');
    const running=/(?:^|\s)投放中(?:\s|$)/.test(words);
    if(!running&&/(?:^|\s)(?:已暂停|直播间未开播)(?:\s|$)/.test(words))throw Error('首页母计划已暂停或直播间未开播，未打开追投');
    const cost=words.includes('控成本投放')||c.executor_kind==='ego'&&b.parent_bid_type==='SMART_BID_CUSTOM'&&/综合营销ROI目标\s*[\d.]+/.test(words)&&!words.includes('放量投放');
    if(!running||!cost)throw Error('首页母计划未明确处于控成本投放中');
    if(Number(raw)!==Number(b.parent_budget))throw Error('首页母计划预算与刚才的详情核对不一致');
    const u=scope();
    return {account_id:c.account_id,plan_id:c.plan_id,group:c.group,path:u.pathname,url:u.href,aweme_id:b.aweme_id,
      row_plan_id:item.row_plan_id,row_aweme_id:item.row_aweme_id,parent_budget:Number(raw).toFixed(2),running:true,
      parent_bid_type:'SMART_BID_CUSTOM',delivery_entry:b.delivery_entry,captured_at:new Date().toISOString()};
  }
  function plus(item){
    const labels=exact(item.row,'素材/人群追投');if(labels.length!==1)return null;
    const label=labels[0];
    for(let box=label.parentElement,n=0;box&&box!==item.row&&n<4;box=box.parentElement,n++){
      const words=text(box);if(words.includes('一键调速')||words.includes('搜索抢首屏'))continue;
      let found=[...box.querySelectorAll('[title="创建任务"],[aria-label="创建任务"],[data-icon="plus"],[name*="plus" i],[name*="add" i],[class*="icon-plus"],[class*="icon_add"]')].filter(e=>visible(e)&&!e.contains(label));
      found.push(...[...box.querySelectorAll('span,button,[role="button"]')].filter(e=>visible(e)&&/^[+＋]$/.test(text(e))));
      found=[...new Set(found)].filter(e=>!found.some(other=>other!==e&&e.contains(other)));
      if(found.length===1)return found[0];if(found.length>1)return null;
    }
    return null;
  }
  function diagnostic(rows){
    const all=[...document.querySelectorAll('tr,[role="row"],.ovui-table__row')].filter(e=>visible(e)&&!e.closest('[role="dialog"],[aria-modal="true"]'));
    const ids=[...new Set(all.flatMap(row=>[row,...row.querySelectorAll('[id],[data-plan-id],[data-ad-id],[data-aweme-id],[data-row-key]')].flatMap(e=>[e.id,e.getAttribute('data-plan-id'),e.getAttribute('data-ad-id'),e.getAttribute('data-aweme-id'),e.getAttribute('data-row-key')]).filter(v=>v&&/^(?:ad-name-)?\d{6,30}$/.test(v))))].slice(0,8);
    return '可见行'+all.length+'、匹配'+rows.length+'、标识'+(ids.join(',')||'无');
  }
  function chooseCostView(){
    // This is the list's read-only delivery-mode filter, never an ad editor.
    const cost=exact(document.body,'控成本投放').filter(e=>!e.closest('tr,[role="row"],.ovui-table__row,form,[role="dialog"],[aria-modal="true"]'));
    const candidates=cost.filter(e=>{for(let box=e.parentElement,n=0;box&&box!==document.body&&n<5;box=box.parentElement,n++){
      if(text(box).length>600)break;if(text(box).includes('放量投放'))return true;
    }return false;});
    if(candidates.length===1&&!viewedFilters.has(candidates[0])){viewedFilters.add(candidates[0]);click(candidates[0]);}
  }
  function chooseLiveView(){
    // Native marketing tabs are viewing filters. Never infer them from old
    // marGoal hash values, whose meaning differs between native page versions.
    for(const list of document.querySelectorAll('[role="tablist"]')){
      if(!visible(list)||list.closest('tr,form,[role="dialog"],[aria-modal="true"]'))continue;
      const tabs=[...list.querySelectorAll('[role="tab"]')].filter(visible);
      const live=tabs.filter(e=>['直播','推直播间'].includes(text(e))),goods=tabs.filter(e=>['商品','推商品'].includes(text(e)));
      if(live.length===1&&goods.length===1&&live[0].getAttribute('aria-selected')==='false'&&!viewedFilters.has(live[0])){viewedFilters.add(live[0]);click(live[0]);}
    }
    const labels=[...document.querySelectorAll('.tab-label')].filter(e=>visible(e)&&!e.closest('tr,form,[role="dialog"],[aria-modal="true"]'));
    const live=labels.filter(e=>text(e)==='推直播间'&&e.closest('.tab')),goods=labels.filter(e=>text(e)==='推商品'&&e.closest('.tab'));
    if(live.length===1&&goods.length===1){
      const tab=live[0].closest('.tab'),other=goods[0].closest('.tab');
      const sameGroup=tab.parentElement===other.parentElement||tab.parentElement?.classList.contains('oc-space-item')&&tab.parentElement.parentElement===other.parentElement?.parentElement;
      if(sameGroup&&!tab.classList.contains('active-tab')&&!viewedFilters.has(live[0])){viewedFilters.add(live[0]);click(live[0]);}
    }
  }
  try{
    scope();
    if(roots().length)return fail('首页已有编辑窗口，保留输入');
    if(['locate','open'].includes(phase)&&boundRows().length!==1){chooseLiveView();chooseCostView();}
    let rows,home,last;
    const attempts=phase==='locate'&&c.executor_kind==='ego'?400:80,deadline=Date.now()+60000;
    for(let n=0;n<attempts&&(phase!=='locate'||Date.now()<deadline);n++){
      scope();if(roots().length)return fail('首页已有编辑窗口，保留输入');
      rows=boundRows();
      if(['locate','open'].includes(phase)&&rows.length!==1&&n%5===0){chooseLiveView();chooseCostView();}
      if(rows.length===1){
        try{home=proof(rows[0]);break;}catch(error){
          if(!/预算未明确|未明确处于控成本投放中/.test(error.message))throw error;last=error.message;
        }
      }
      await pause(150);
    }
    if(!home)return {...fail((last||'首页母计划行未唯一对应')+'（'+diagnostic(rows)+'）'+(c.direct_home?'；本模式不回素材页预搜':'，改用详情入口'),true),diagnostic:{path:location.pathname,
      tabs:[...document.querySelectorAll('[role="tab"],[class*="radio-item"]')].filter(e=>visible(e)&&['直播','商品','推直播间','推商品','控成本投放','放量投放'].includes(text(e))).map(e=>({text:text(e),selected:e.getAttribute('aria-selected')||e.getAttribute('aria-checked'),class:String(e.className)})).slice(0,12),
      filters:[...document.querySelectorAll('input')].filter(e=>visible(e)&&e.type!=='password'&&!e.closest('table,form,[role="dialog"]')).map(e=>({placeholder:e.placeholder,value:e.value})).slice(0,10),
      rows:[...document.querySelectorAll('tr')].filter(visible).slice(0,4).map(e=>({key:e.getAttribute('data-row-key'),text:text(e).slice(0,160)}))}};
    if(phase==='locate'){
      const labels=exact(rows[0].row,'素材/人群追投');
      if(!/^[-\w]{1,80}$/.test(c.id||''))return fail('首页查看命令标识无效');
      // Some native rows mount their tools only on mouse enter. Hovering an
      // exactly verified parent is harmless; open() must still independently
      // identify the single material tool and plus before any dialog is opened.
      const target=labels.length===1?labels[0].parentElement:rows[0].row;
      target.setAttribute('data-qcx-home-hover',c.id);
      let hover_locator;
      if(c.executor_kind==='ego'&&/^\d{6,30}$/.test(c.plan_id)){
        // Native table refreshes replace transient DOM markers. Anchor the
        // read-only hover to the verified parent and exact material-tool text.
        // open() still independently proves the parent and unique plus.
        const pid=c.plan_id,owner=b.aweme_id;
        const row="//*[self::tr or @role='row' or contains(concat(' ',normalize-space(@class),' '),' ovui-table__row ')][@data-plan-id='"+pid+"' or @data-ad-id='"+pid+"' or @data-row-key='"+pid+"' or @data-aweme-id='"+owner+"' or .//*[@id='ad-name-"+pid+"' or @id='"+owner+"' or @data-aweme-id='"+owner+"']]";
        hover_locator="xpath="+row+(labels.length===1?"//*[self::button or @role='button' or self::span or self::div or self::a][normalize-space(.)='素材/人群追投' and not(*[normalize-space(.)='素材/人群追投'])]/parent::*":"");
      }
      return {ok:true,home,hover_selector:'[data-qcx-home-hover="'+c.id+'"]',...(hover_locator?{hover_locator}:{})};
    }
    if(phase==='inspect')return {ok:true,home};
    if(phase!=='open')return fail('不支持的首页入口阶段');
    let target=plus(rows[0]);
    for(let n=0;!target&&c.executor_kind==='ego'&&n<20;n++){
      await pause(100);scope();if(roots().length)return fail('首页已有编辑窗口，保留输入');
      rows=boundRows();if(rows.length!==1)return fail('首页母计划行已变化，未打开追投');
      home=proof(rows[0]);target=plus(rows[0]);
    }
    if(!target)return fail('首页素材追投加号未唯一明确'+(c.direct_home?'；本模式不回素材页预搜':'，改用详情入口'),true);
    if(/Disabled|not-allowed|disabled/.test(target.className||''))throw Error('平台当前不允许新建追投，请核对任务数量上限或原生提示');
    click(target);
    for(let n=0;n<40;n++){
      scope();
      const dialogs=roots();
      const root=dialogs.find(r=>text(r).includes('调控工具')&&text(r).includes('素材/人群追投'));
      if(root){
        // The verified plus opened this root; retain ownership even if later
        // option/form rendering fails, so only our own empty editor can cancel.
        globalThis.__qc_hybrid_editor_v1={root,selected:[],selected_ids:null,action_id:c.action.id,home_context:b};
        const tools=exact(root,'素材/人群追投');if(tools.length!==1)return fail('首页任务工具未唯一明确，保留窗口');
        click(tools[0]);
        for(let i=0;i<30;i++){
          scope();
          const current=roots().find(r=>text(r).includes('追投方式')&&exact(r,'添加视频').length===1);
          if(current){
            const ids=[...text(current).matchAll(/素材ID[：:]\s*(\d{6,30})/g)];
            if(ids.length)return fail('首页新窗口已有素材，保留原窗口');
            if(!/已添加[：:]?\s*0\s*\/\s*20/.test(text(current)))return fail('首页新窗口未明确为空素材任务，保留原窗口');
            globalThis.__qc_hybrid_editor_v1={root:current,selected:[],selected_ids:null,action_id:c.action.id,home_context:b};
            return {ok:true,opened:true,home:proof(rows[0])};
          }
          await pause(100);
        }
        return fail('首页素材追投表单尚未就绪，保留窗口');
      }
      if(dialogs.length&&n>=20)return fail('首页出现未识别窗口，保留输入');
      await pause(150);
    }
    return fail('首页创建任务窗口未打开');
  }catch(error){return fail(error.message);}
}
