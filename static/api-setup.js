'use strict';
let apiState = null;
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function message(value) { $('notice').textContent=value; $('notice').hidden=false; }
async function post(path, body) {
  if(!apiState?.csrf)throw new Error('接入状态仍在加载，请稍后再操作');
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Monitor-CSRF':apiState.csrf},body:JSON.stringify(body)});
  const result=await response.json(); if(!response.ok)throw new Error(result.error||'请求失败');return result;
}
async function load() {
  const response=await fetch('/api/official-api');apiState=await response.json();if(!response.ok)throw new Error(apiState.error);
  $('callback').textContent=apiState.callback_url;
  $('stage').textContent=apiState.stage==='application_review'?`千川巡航应用已提交，最近核验为审核中（${new Date(apiState.application.checked_at).toLocaleString()}）。待后台显示App ID后继续配置。`:apiState.stage==='application_approved'?'应用审核已通过，等待保存本机凭据并授权账户':apiState.stage==='awaiting_app'?'尚未配置应用凭据':apiState.stage==='awaiting_authorization'?'应用配置已保存，等待账户授权':'账户授权已保存，可核验 API 读数';
  $('app-id').value=apiState.app_id; $('authorization-url').value=apiState.authorization_url;$('scope-ids').value=apiState.scope_ids.join(',');
  $('secret-state').textContent=apiState.secret_saved?'Secret 已加密保存':'';
  $('authorize').disabled=!apiState.configured;
  document.querySelector('#app-form button[type="submit"]').disabled=false;
  $('grants').innerHTML=apiState.grants.map(g=>`<div class="grant"><strong>${esc(g.label)}</strong> · Token 到期 ${esc(new Date(g.expires_at*1000).toLocaleString())} <button class="secondary" data-discover="${esc(g.id)}">查询授权账户</button><div>${g.accounts.map(a=>`${esc(a.name||a.id)}（${esc(a.id)} / ${esc(a.type)}）`).join('<br>')}</div></div>`).join('')||'<p class="muted">尚无账户授权连接</p>';
  const chosen=$('grant-select').value;$('grant-select').innerHTML=apiState.grants.map(g=>`<option value="${esc(g.id)}">${esc(g.label)}</option>`).join('');if(apiState.grants.some(g=>g.id===chosen))$('grant-select').value=chosen;
  $('read-account').disabled=!apiState.grants.length;
  if(apiState.last_authorization?.ok===false)message('授权回调：'+apiState.last_authorization.message);
  const rows=Object.values(apiState.readings);const num=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
  $('readings').innerHTML=rows.length?`<div class="table-scroll"><table><thead><tr><th>账户</th><th>投放入口</th><th>结果</th><th>日期</th><th>整体消耗</th><th>整体支付ROI</th><th>整体成交金额</th><th>订单数</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.account_id)}</td><td>${esc(r.entry_label||"旧版未区分")}</td><td>${r.data_status==="no_rows"?"无匹配数据":"已读取"}</td><td>${esc(r.date)}</td><td>${num(r.metrics.spend)}</td><td>${num(r.metrics.roi)}</td><td>${num(r.metrics.gmv)}</td><td>${num(r.metrics.orders)}</td></tr>`).join('')}</tbody></table></div><p class="muted">请求完成时间：${esc(rows.map(r=>new Date(r.captured_at).toLocaleString()).join('；'))}。接口未提供数据更新时间。</p>`:'';
}
async function run(fn) { try{await fn();}catch(error){message(error.message||'操作失败');} }
$('app-form').addEventListener('submit', event=>{event.preventDefault();run(async()=>{const secret=$('app-secret').value;try{await post('/api/official-api/configure',{app_id:$('app-id').value.trim(),secret,authorization_url:$('authorization-url').value.trim(),scope_ids:$('scope-ids').value.split(/[,，\s]+/).filter(Boolean)});message('应用配置已加密保存');await load();}finally{$('app-secret').value='';}});});
$('authorize').addEventListener('click',()=>run(async()=>{const result=await post('/api/official-api/authorize',{label:$('grant-label').value.trim()});window.location.assign(result.authorization_url);}));
$('grants').addEventListener('click', event=>{const button=event.target.closest('[data-discover]');if(button)run(async()=>{button.disabled=true;try{const result=await post('/api/official-api/accounts',{grant_id:button.dataset.discover});message(`官方返回 ${result.accounts.length} 个授权账户/管理主体；管理主体不等于全部下属投放户。`);await load();}finally{button.disabled=false;}});});
$('read-account').addEventListener('click',()=>run(async()=>{const button=$('read-account');button.disabled=true;try{await post('/api/official-api/read',{account_id:$('account-select').value,grant_id:$('grant-select').value});message('已按本户投放入口读取今天数据；全域与乘方分别展示');await load();}finally{button.disabled=!apiState.grants.length;}}));
run(async()=>{await load();const response=await fetch('/api/accounts');const data=await response.json();if(!response.ok)throw new Error(data.error);const rows=data.accounts||data.registry?.accounts||[];$('account-select').innerHTML=rows.map(a=>`<option value="${esc(a.id)}">${esc(a.name)} · ${esc(({uni_promotion:"全域",chengfang:"乘方",both:"全域与乘方"})[a.delivery_entry||"uni_promotion"])} · ${esc(a.id)}</option>`).join('');const outcome=new URLSearchParams(location.search).get('oauth');if(outcome)message(outcome==='connected'?'账户授权已保存。请查询授权账户并核验读数。':'授权未完成或会话失效，请重新从本页发起；无需提供Token。');if(outcome)history.replaceState(null,'','/api-setup');});
