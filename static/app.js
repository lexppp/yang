'use strict';
const $ = (q) => document.querySelector(q);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = n => Number(n || 0).toLocaleString('en-US');
const date = s => s ? new Date(s).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '暂无活动';
let currentUser=null;
let csrf='', data=null, page='overview', group='', toastTimer, countdownTimer, loading=false;
const pages = {overview:['数据总览','每一份互动都有价值，让社群增长清晰可见。'],groups:['社群管理','一个工作空间，管理所有社群的规则与互动。'],ranking:['积分排行榜','记录每一份参与，让贡献被看见。'],invites:['邀请管理','从一次分享，到一次真实的社群连接。'],logs:['活动日志','签到、入群与社群守护，每一步都有迹可循。']};
async function api(path, body) {
  const r=await fetch(path, body===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});
  const result=await r.json();
  if(!r.ok){if(r.status===401 && path!='/api/login')showLogin();throw new Error(result.error || '请求失败');}
  return result;
}
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,4500);}
function showLogin(){document.querySelectorAll('dialog[open]').forEach(d=>d.close());$('#login').hidden=false;$('#app').hidden=true;csrf='';currentUser=null;data=null;window.onAccessLogout?.();window.updateLoginMode?.();}
async function enter(session){currentUser=session.user;csrf=session.csrf;$('#login').hidden=true;$('#app').hidden=false;await refresh();}
async function refresh(){
  if(loading||!csrf)return;loading=true;$('#refresh').disabled=true;
  try{data=await api('/api/snapshot'+(group?'?group='+encodeURIComponent(group):''));currentUser=data.current_user||currentUser;
    $('#demo-banner').hidden=!data.demo;$('#bot-status').textContent=data.bot_status;
    $('#nav-count').textContent=data.groups.length;
    $('#today').textContent=new Date().toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'long',day:'numeric'});
    $('#synced').textContent='· '+date(data.server_time);
    const select=$('#group-filter');
    select.innerHTML='<option value="">全部社群</option>'+data.groups.map(g=>`<option value="${g.id}">${esc(g.title)}</option>`).join('');select.value=group;
    render();
  }catch(e){toast(e.message);}finally{loading=false;$('#refresh').disabled=false;}
}
const empty=(text)=>`<div class="empty"><div class="empty-symbol">◇</div>${esc(text)}</div>`;
const panel=(title,subtitle,body,action='')=>`<section class="panel"><div class="panel-head"><div><h2>${title}</h2>${subtitle?`<p>${subtitle}</p>`:''}</div>${action}</div>${body}</section>`;
function table(headers, rows){return rows.length?`<div class="table-scroll"><table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`:empty('暂无记录，社群产生互动后会在这里显示。');}
function selectedGroups(){return data.groups.filter(g=>!group || String(g.id)===group);}
function stats(){
 const gs=selectedGroups(), sum=k=>gs.reduce((a,g)=>a+g[k],0);
 const items=[['已连接社群',gs.filter(g=>g.active).length,'▦','已获取管理员权限的社群'],['已记录成员',sum('users'),'♧','接入后观察到的成员，非全群历史人数'],['累计积分',sum('credits'),'✧','签到与邀请贡献，持续沉淀'],['今日签到',sum('checkins'),'✓','每天 00:00（UTC+8）刷新']];
 return '<div class="stats">'+items.map(([label,value,icon,foot])=>`<div class="stat"><div class="stat-top">${label}<span class="stat-icon">${icon}</span></div><div class="stat-value">${num(value)}</div><div class="stat-foot">${foot}</div></div>`).join('')+'</div>';
}
function groupsPanel(){
 const gs=selectedGroups();const max=Math.max(1,...gs.map(g=>g.messages));
 const rows=gs.map(g=>`<tr><td><div class="group-cell"><span class="group-avatar">${esc(g.title.slice(0,1))}</span><div><b>${esc(g.title)}</b><small>${g.id}</small></div></div></td><td><span class="badge ${g.active?'':'off'}">${g.active?'已连接':'待授权'}</span></td><td>${num(g.users)}</td><td>${num(g.messages)}<progress class="progress" value="${g.messages}" max="${max}" aria-label="消息活跃度"></progress></td><td class="purple">${num(g.credits)}</td><td>${date(g.last_activity)}</td><td><button class="text-button" data-view="${g.id}">查看 ↗</button><button class="text-button" data-settings="${g.id}">设置</button></td></tr>`);
 return panel('▦ 社群概览','规则、活跃与积分，在同一处掌握。',table(['社群名称','状态','已记录成员','累计消息','总积分','最近活跃','操作'],rows)+`<div class="table-foot"><span>共 ${gs.length} 个社群 · ${gs.filter(g=>g.active).length} 个已连接</span><span>由机器人事件自动同步</span></div>`,'<span class="tag">COMMUNITIES</span>');
}
function overview(){
 const logs=data.logs.slice(0,6);
 const activity=logs.length?'<div class="activity-list">'+logs.map(l=>`<div class="activity-row"><span class="activity-icon">${l.kind==='签到'?'✓':l.kind==='内容拦截'?'◇':'↗'}</span><div><b>${esc(l.kind)}${l.user_id?' · '+l.user_id:''}</b><small>${date(l.created)}</small></div><span class="activity-points">${esc(l.detail.startsWith('+')?l.detail:'')}</span></div>`).join('')+'</div>':empty('等待第一条社群动态');
 return stats()+`<div class="overview-middle">${panel('◷ 最近动态','社群正在发生的每一点变化',activity,'<button class="text-button" data-go="logs">全部记录 ↗</button>')}<section class="panel reset-card"><h2>◷ 下次签到刷新</h2><div class="countdown" id="countdown">00:00:00</div><p>北京时间每日零点，开启新一天的签到。<br>累计积分与邀请记录始终保留。</p><div class="reset-foot">下次刷新 <span id="reset-date"></span> · UTC+8</div></section></div>`+groupsPanel();
}
function ranking(){
 const rank=(key,title,subtitle)=>{
  const rows=[...data.ranking].filter(r=>r[key]>0).sort((a,b)=>b[key]-a[key]).slice(0,50).map((r,i)=>`<tr><td><span class="rank-number ${i<3?'top':''}">#${i+1}</span></td><td><b>${esc(r.name)}</b><small>${esc(r.username?'@'+r.username:r.user_id)} · ${esc(data.groups.find(g=>g.id===r.group_id)?.title || '')}</small></td><td class="purple">${num(r[key])}</td></tr>`);
  return panel(title,subtitle,table(['排名','用户 / 社群','积分'],rows));
 };
 return stats()+`<div class="rank-grid">${rank('monthly','◫ 本月排行榜',data.month+' · 每个自然月独立统计')}${rank('total','♜ 累计排行榜','保留所有签到与有效邀请积分')}</div>`;
}
function invites(){
 return stats()+panel('↗ 邀请链接','成员在群内发送 /ink 即可生成自己的专属链接。',`<div class="toolbar"><input id="invite-search" placeholder="搜索用户、用户名或邀请链接" aria-label="搜索邀请"><select id="invite-status" aria-label="邀请状态"><option value="">全部状态</option><option value="1">有效链接</option><option value="0">已撤销</option></select><span class="tag">${data.invites.length} 条链接</span></div><div id="invite-results"></div>`,'<a class="button" href="/api/export'+(group?'?group='+encodeURIComponent(group):'')+'">↓ 导出积分 CSV</a>');
}
function drawInvites(){
 const query=($('#invite-search')?.value||'').toLowerCase(),status=$('#invite-status')?.value;
 const rows=data.invites.filter(r=>(status===''||String(r.active)===status)&&`${r.name} ${r.username} ${r.url}`.toLowerCase().includes(query)).map(r=>`<tr><td><b>${esc(r.name)}</b><small>${esc(r.username?'@'+r.username:r.user_id)}</small></td><td><span class="link-cell">${esc(r.url)}</span><button class="text-button" data-copy="${r.id}">复制链接 ⧉</button></td><td class="purple">${num(r.count)} <small>按用户去重</small></td><td><span class="badge ${r.active?'':'off'}">${r.active?'有效':'已撤销'}</span></td><td>${date(r.created)}</td><td>${r.active?`<button class="danger" data-revoke="${r.id}">撤销</button>`:'—'}</td></tr>`);
 $('#invite-results').innerHTML=table(['邀请人','专属链接','累计有效邀请','状态','创建时间','操作'],rows)+`<div class="table-foot"><span>显示 ${rows.length} 条记录 · 最多展示最近 500 条链接</span><span>撤销后保留历史贡献</span></div>`;
}
function logs(){return panel('≡ 活动日志','最近 100 条事件 · Telegram API 失败也会记录在此',table(['时间（UTC+8）','事件','社群 / 用户','详情'],data.logs.map(l=>`<tr><td>${date(l.created)}</td><td><span class="tag">${esc(l.kind)}</span></td><td>${esc(data.groups.find(g=>g.id===l.group_id)?.title || '系统')}<small>${l.user_id || '管理员 / 系统'}</small></td><td>${esc(l.detail)}</td></tr>`)));}
function render(){
 clearInterval(countdownTimer);
 const info=pages[page]||pages.overview;
 $('#page-title').innerHTML=info[0]+'<span class="title-dot">.</span>';$('#page-description').textContent=info[1];$('#breadcrumb').textContent=info[0];
 document.querySelectorAll('[data-page]').forEach(el=>el.classList.toggle('active',el.dataset.page===page));
 let detail='';
 if(group){const g=data.groups.find(g=>String(g.id)===group);if(g)detail=`<div class="detail-banner"><div><h2>${esc(g.title)}</h2><p>${g.id} · 累计消息 ${num(g.messages)} · 有效邀请 ${num(g.invites)}</p></div><button class="primary" data-settings="${g.id}">⚙ 社群设置</button></div>`;}
 let content=page==='overview'?overview():page==='groups'?stats()+groupsPanel():page==='ranking'?ranking():page==='invites'?invites():logs();
 if(!data.groups.length)content=empty('尚未连接社群。将机器人添加到群组并授予管理员权限后，社群会自动出现。');
 $('#content').innerHTML=detail+content;
 if(page==='invites'&&data.groups.length){drawInvites();$('#invite-search').addEventListener('input',drawInvites);$('#invite-status').addEventListener('change',drawInvites);}
 if($('#countdown')){const tick=()=>{let seconds=Math.max(0,Math.floor((new Date(data.next_reset)-Date.now())/1000));$('#countdown').textContent=[Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60].map(v=>String(v).padStart(2,'0')).join(':');$('#reset-date').textContent=new Date(data.next_reset).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit'})+' 00:00';};tick();countdownTimer=setInterval(tick,1000);}
}
function openSettings(id){const g=data.groups.find(g=>g.id===Number(id));if(!g)return;const f=$('#settings-form');$('#settings-title').textContent=g.title;$('#settings-error').textContent='';for(const el of f.elements){if(!el.name)continue;if(el.type==='checkbox')el.checked=!!g[el.name];else el.value=el.name==='group_id'?g.id:g[el.name];}$('#settings-dialog').showModal();}
function navigate(target){page=target;location.hash=target;if(data)render();}
document.addEventListener('click',async e=>{
 const el=e.target.closest('button,a');if(!el)return;
 if(el.dataset.page||el.dataset.go){e.preventDefault();navigate(el.dataset.page||el.dataset.go);}
 if(el.dataset.settings)openSettings(el.dataset.settings);
 if(el.dataset.view){group=el.dataset.view;page='groups';location.hash=page;await refresh();}
 if(el.dataset.copy){const r=data.invites.find(r=>r.id===Number(el.dataset.copy));try{await navigator.clipboard.writeText(r.url);toast('邀请链接已复制');}catch{toast('复制受浏览器限制，请手动选择链接');}}
 if(el.dataset.revoke){if(!confirm('撤销此邀请链接？已邀请人数和历史积分将保留。'))return;el.disabled=true;try{await api('/api/revoke',{id:Number(el.dataset.revoke)});toast('链接已撤销');await refresh();}catch(err){toast(err.message);el.disabled=false;}}
});
$('#login-form').addEventListener('submit',async e=>{
 e.preventDefault();const form=e.currentTarget,button=e.submitter||form.querySelector('button[type="submit"]');
 if(form.dataset.submitting==='true'||!form.reportValidity())return;
 form.dataset.submitting='true';const setup=form.dataset.mode==='setup';
 if(button){button.disabled=true;button.setAttribute('aria-busy','true');button.textContent=setup?'正在创建账号…':'正在登录…';}
 $('#login-error').textContent='';
 try{
  const result=await api(setup?'/api/setup':'/api/login',{username:$('#username').value.trim(),password:$('#password').value,confirm_password:$('#confirm-password')?.value});
  await enter(result);
  if($('#app').hidden)throw new Error('登录状态未能保留，请允许站点 Cookie，或下载页面后用系统浏览器打开。');
  $('#password').value='';if($('#confirm-password'))$('#confirm-password').value='';
 }catch(error){$('#login-error').textContent=error.message||'登录未完成，请重试。';if(setup&&error.status===403)window.updateLoginMode?.();}
 finally{form.dataset.submitting='false';if(button){button.disabled=false;button.removeAttribute('aria-busy');button.innerHTML=form.dataset.mode==='setup'?'创建并进入 <span>↗</span>':'安全登录 <span>↗</span>';}}
});
$('#settings-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,body={};for(const el of f.elements){if(!el.name||el.disabled)continue;body[el.name]=el.type==='checkbox'?el.checked:el.type==='number'||el.type==='hidden'?Number(el.value):el.value.trim();}e.submitter.disabled=true;try{await api('/api/settings',body);$('#settings-dialog').close();toast('社群设置已保存，对后续事件生效');await refresh();}catch(err){$('#settings-error').textContent=err.message;}finally{e.submitter.disabled=false;}});
$('#close-settings').onclick=()=>$('#settings-dialog').close();$('#refresh').onclick=refresh;
$('#group-filter').onchange=async e=>{group=e.target.value;await refresh();};
const logout=async()=>{try{await api('/api/logout',{});clearInterval(countdownTimer);showLogin();}catch(err){toast(err.message);}};
$('#logout').onclick=logout;$('#logout-mobile').onclick=logout;
window.addEventListener('hashchange',()=>{const next=location.hash.slice(1);if(pages[next]&&next!==page){page=next;if(data)render();}});
page=pages[location.hash.slice(1)]?location.hash.slice(1):'overview';
api('/api/session').then(enter).catch(showLogin);
setInterval(()=>{if(csrf&&!document.hidden&&!$('#settings-dialog').open&&!$('#invite-search')?.value)refresh();},15000);
