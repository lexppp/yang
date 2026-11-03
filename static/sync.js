/* Read-only X -> Telegram monitoring; permissions are checked by the API. */
(() => {
pages.sync=['X → Telegram 同步','来源、运行情况与每条帖子的发送记录，在这里查看。'];
const nav=document.createElement('button');nav.className='nav-item';nav.dataset.page='sync';nav.innerHTML='<span>⇄</span> X → Telegram 同步';document.querySelector('nav').append(nav);
let latest=null,busy=false,query='',filter='all';
const states={baseline:'启用前旧帖 · 不补发',pending:'等待发送',sent:'已发送',sending:'发送中',uncertain:'结果待核对',failed:'发送被拒绝',retry:'限流等待重试',unknown:'状态未知'};
const clock=value=>value?date(value*1000):'暂无记录';
function reason(code){
 if(!code)return '暂无错误';
 if(code.includes('uncertain')||code.startsWith('delivery_requires_inspection'))return '发送结果不确定，请对照 TG 频道核对。为避免重复，后续发送已暂停。';
 if(code.includes('rate_limited'))return 'Telegram 限流，等待允许的重试时间。';
 if(code.includes('rejected'))return 'Telegram 拒绝发送，请检查频道权限和图片可用性。需处理后恢复。';
 if(code.includes('rsshub')||code.includes('feed'))return '来源读取失败或没有有效帖子，系统会退避检查。请检查 RSSHub 和 X 登录态。';
 if(code==='bot_cannot_post')return '机器人缺少目标频道发帖权限。';
 if(code.includes('unexpected'))return '机器人身份或目标频道与配置不一致。';
 return '同步检查未完成，请在服务器查看该独立服务的日志。';
}
function outcome(p){
 const ds=p.deliveries.map(d=>d.status);
 return ['uncertain','failed','retry','sending'].find(s=>ds.includes(s))||p.status;
}
function records(){
 if(!latest?.available)return '';
 const list=latest.posts.filter(p=>(filter==='all'||outcome(p)===filter)&&`${p.id} ${p.text}`.toLowerCase().includes(query.toLowerCase()));
 const rows=list.map(p=>`<tr><td>${clock(p.published)}<small>${esc(p.id)}</small></td><td><div class="sync-excerpt">${esc(p.text||'图片帖子')}</div><small>${num(p.images)} 张图片</small><a class="text-button" href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">查看 X 原帖 ↗</a></td><td><span class="badge ${['sent','baseline'].includes(outcome(p))?'':'off'}">${esc(states[outcome(p)]||states.unknown)}</span><small>成功 ${p.deliveries.filter(d=>d.status==='sent').length} / 已尝试 ${p.deliveries.length} 段</small></td><td>${p.deliveries.length?p.deliveries.map(d=>`<div>第 ${d.part+1} 段 · ${esc(states[d.status]||states.unknown)} ${d.message_url?`<a class="text-button" href="${esc(d.message_url)}" target="_blank" rel="noopener noreferrer">TG 消息 ↗</a>`:''}</div>`).join(''):'尚未发送'}</td></tr>`);
 return list.length?table(['X 发布时间（UTC+8）','帖子内容','状态','分段投递记录'],rows):empty('没有符合条件的同步记录。');
}
function screen(s){
 const timerNames={active:'运行中',inactive:'已停止',failed:'调度失败',activating:'正在启动',not_installed:'未安装',unknown:'无法确认'};
 const timer=timerNames[s.timer.state]||'无法确认';
 if(!s.available)return panel('⇄ 同步状态尚未接入',`定时器：${timer}`,`<div class="empty">${s.reason==='not_configured'?'尚未收到转发工具的状态报告。更新转发工具后，后台才能显示真实记录。':'无法读取有效状态报告，请检查服务和文件权限。'}<p>网站没有发起 X 请求，也没有发送测试消息。</p></div>`);
 const counts=s.counts,delivery=s.delivery_counts;
 const blocked=delivery.uncertain+delivery.failed;
 let label=s.timer.state==='active'?'已启用 · 等待下一次检查':'调度状态：'+timer;
 if(s.last_error)label='需要关注：'+reason(s.last_error);
 if(blocked)label='发送已暂停核对：存在发送失败或结果不确定的记录';
 if(s.stale)label='状态报告已超过20分钟未更新，请检查转发服务';
 const cards=[['已发送帖子',counts.sent,'已完成所有分段的帖子'],['等待处理',counts.pending,'包含限流及需人工核对的帖子'],['历史基线',counts.baseline,'启用前旧帖，不补发'],['需核对分段',blocked,'失败或结果不确定，不自动重发']];
 return `<div class="stats">${cards.map(([title,value,foot])=>`<div class="stat"><div class="stat-top">${title}</div><div class="stat-value">${num(value)}</div><div class="stat-foot">${foot}</div></div>`).join('')}</div>`+
 panel('⇄ '+esc(label),'页面每15秒更新服务器状态；X 来源默认每10分钟检查一次。',`<div class="sync-route"><b>@${esc(s.source)}</b><span>→</span><b>@${esc(s.channel)}</b><span>机器人 @${esc(s.bot)}</span></div><dl class="sync-info"><div><dt>定时器</dt><dd>${esc(timer)} · ${s.timer.enabled===true?'已设开机启动':s.timer.enabled===false?'未设开机启动':'开机启动状态未知'}</dd></div><div><dt>启用时间</dt><dd>${clock(s.enabled_at)}</dd></div><div><dt>最近来源检查</dt><dd>${clock(s.last_check)}</dd></div><div><dt>最近检查成功</dt><dd>${clock(s.last_success)}</dd></div><div><dt>最近整帖发送成功</dt><dd>${clock(s.last_delivery)}</dd></div><div><dt>下次允许读取来源</dt><dd>${clock(s.next_fetch)}</dd></div><div><dt>下次调度时间</dt><dd>${esc(s.timer.next_run||'由服务器定时器安排')}</dd></div><div><dt>发送限流等待至</dt><dd>${s.next_send>s.server_time?clock(s.next_send):'无'}</dd></div><div><dt>状态报告更新时间</dt><dd>${clock(s.updated_at)}</dd></div></dl><p class="form-hint">仅转发首次启用后发布的新帖。没有发送记录时，仍需等待真实新帖验证端到端投递。</p>`)+
 panel('最近检查结果',s.last_error?'最近失败：'+clock(s.last_error_at):'记录来自转发服务',`<p class="sync-notice">${esc(reason(s.last_error))}</p><p class="form-hint">页面只读，不会触发额外抓取或补发。来源失败自动退避，发送结果不确定时需在服务器核对处理。</p>`)+
 panel('帖子与投递记录','最近100条已观察帖子；累计数量保留全部历史。',`<div class="toolbar"><input id="sync-search" aria-label="搜索同步帖子" placeholder="搜索正文或帖子 ID" value="${esc(query)}"><select id="sync-filter" aria-label="同步状态筛选">${[['all','全部状态'],...Object.entries(states).filter(([key])=>key!=='unknown')].map(([key,label])=>`<option value="${key}" ${filter===key?'selected':''}>${esc(label)}</option>`).join('')}</select></div><div id="sync-records">${records()}</div>`);
}
function paint(){
 if(page!=='sync'||!csrf||!accessAllowed(currentUser,'sync.view'))return;
 const slot=$('#sync-screen');if(!slot)return;
 slot.innerHTML=screen(latest);
 $('#sync-search')?.addEventListener('input',e=>{query=e.target.value;$('#sync-records').innerHTML=records();});
 $('#sync-filter')?.addEventListener('change',e=>{filter=e.target.value;$('#sync-records').innerHTML=records();});
}
async function load(){
 if(busy)return;busy=true;const session=csrf;
 try{const result=await api('/api/sync/status');if(csrf===session){latest=result;paint();}}
 catch(error){if(page==='sync'&&csrf===session&&$('#sync-screen'))$('#sync-screen').innerHTML=empty(error.message);}
 finally{busy=false;}
}
const before=render;
render=function(){
 before();
 $('.context-bar').hidden=page==='sync';
 if(page==='sync'){
  $('#manage-disconnected').hidden=true;
  $('#content').innerHTML='<div id="sync-screen">'+empty('正在读取同步状态…')+'</div>';
  if(latest)paint();load();
 }
};
const logout=window.onAccessLogout;
window.onAccessLogout=()=>{latest=null;query='';filter='all';logout?.();};
})();
