/* Extended frontend screens: monthly accounts, exports, configurable rewards, Lucky Draw. */
let rankingMonth='',inviteMonth='',rankSearch='',inviteView='links',drawImage='',drawStatus='',selectedDrawId='',drawActor=999;
const fullDate=s=>new Date(s).toLocaleString('sv-SE',{timeZone:'Asia/Shanghai',hour12:false});
const groupTitle=id=>data.groups.find(g=>g.id===id)?.title||String(id);
const person=(gid,uid)=>data.members.find(m=>m.group_id===gid&&m.user_id===uid)||{user_id:uid,name:String(uid),username:''};
const score=(gid,uid,month=data.month)=>data.ledger.filter(l=>l.group_id===gid&&l.user_id===uid&&l.month===month).reduce((sum,l)=>sum+l.points,0);
const monthOptions=selected=>data.months.map(m=>`<option value="${m}" ${m===selected?'selected':''}>${m}${m===data.month?' · 本月':' · 已归档'}</option>`).join('');
const monthNotice=()=>`<div class="month-notice"><div><span class="notice-symbol">◷</span><span><b>每月清零 · 历史保留</b><small>每月 1 日 00:00（UTC+8）重置所有用户可用积分，上月余额不结转。</small></span></div><span>下次重置 <b>${esc(data.next_month.slice(0,10))} 00:00</b></span></div>`;
const saveDownload=(blob,name)=>{const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);};
function addScreen(pageId,label,icon){pages[pageId]=[label,pageId==='draws'?'把每一份参与，变成值得期待的惊喜。':'定义签到与邀请的价值，让激励规则清晰一致。'];const b=document.createElement('button');b.className='nav-item';b.dataset.page=pageId;b.innerHTML=`<span>${icon}</span> ${label}`;document.querySelector('nav').appendChild(b);}
addScreen('points','积分设置','⚙');addScreen('draws','Lucky Draw','♜');
const beforeFeaturesStats=stats;
stats=function(){return beforeFeaturesStats().replace('累计积分','本月可用积分').replace('签到与邀请贡献，持续沉淀','每月 1 日清零 · 历史流水保留');};
ranking=function(){
 rankingMonth=rankingMonth||data.month;const history=rankingMonth!==data.month;
 const rows=data.members.map(m=>({...m,balance:score(m.group_id,m.user_id,rankingMonth)})).filter(m=>(!group||String(m.group_id)===group)&&`${m.name} ${m.username} ${m.user_id}`.toLowerCase().includes(rankSearch.toLowerCase())).sort((a,b)=>b.balance-a.balance);
 return monthNotice()+`<div class="feature-header"><div class="view-tabs"><button class="${!history?'selected':''}" data-rank-current>本月排行榜</button><button class="${history?'selected':''}" data-rank-history>历史记录</button></div><span class="tag">${history?'归档只读':'当前可用余额'}</span></div>`+panel(history?'◫ 历史积分档案':'♜ 本月积分排行榜',history?'查看该月收支后的月末余额，历史月份不可修改。':'签到、邀请、人工调整与抽奖消费均计入本月余额。',`<div class="toolbar"><label class="inline-label">积分月份<select id="ranking-month">${monthOptions(rankingMonth)}</select></label><input id="rank-search" value="${esc(rankSearch)}" placeholder="搜索用户、用户名或 Telegram ID" aria-label="搜索积分用户"><span class="tag">${rows.length} 位用户</span></div>`+table(['排名','用户 / Telegram ID','社群',history?'月末积分':'可用积分','操作'],rows.map((r,i)=>`<tr><td><span class="rank-number ${i<3?'top':''}">#${i+1}</span></td><td><b>${esc(r.name)}</b><small>${esc(r.username?'@'+r.username:'')} · ${r.user_id}</small></td><td>${esc(groupTitle(r.group_id))}</td><td class="purple points-value">${num(r.balance)}</td><td><button class="text-button" data-user-history="${r.user_id}" data-gid="${r.group_id}">流水</button>${!history?`<button class="text-button" data-adjust-user="${r.user_id}" data-gid="${r.group_id}">± 调整积分</button>`:''}</td></tr>`))+`<div class="table-foot"><span>${history?'此为历史月末积分，不计入本月可用余额。':'人工加减分必须填写原因；积分不能扣为负数。'}</span><span>UTC+8</span></div>`,!history?'<button class="primary" id="manual-points">± 手动加减积分</button>':'');
};
function monthlyInviteSummary(){
 const month=inviteMonth||data.month,joins=data.joins.filter(j=>j.month===month&&(!group||String(j.group_id)===group));const map=new Map();
 for(const i of data.invites.filter(i=>!group||String(i.group_id)===group))map.set(i.group_id+':'+i.user_id,{...person(i.group_id,i.user_id),count:0,points:0,last:''});
 for(const j of joins){const key=j.group_id+':'+j.inviter_id;if(!map.has(key))map.set(key,{...person(j.group_id,j.inviter_id),count:0,points:0,last:''});const row=map.get(key);row.count++;row.points+=j.points;if(j.created>row.last)row.last=j.created;}
 return {joins,summary:[...map.values()].sort((a,b)=>b.count-a.count)};
}
invites=function(){
 inviteMonth=inviteMonth||data.month;
 const {joins,summary}=monthlyInviteSummary();
 return `<div class="mini-stats"><div><span>当月有效邀请</span><b>${num(joins.length)}<small>人</small></b></div><div><span>有邀请贡献的用户</span><b>${num(summary.filter(s=>s.count).length)}<small>位</small></b></div><div><span>当月邀请奖励</span><b>${num(joins.reduce((s,j)=>s+j.points,0))}<small>积分</small></b></div></div>`+panel('↗ 邀请管理','按实际成功入群时间统计，支持汇总与逐人明细核对。',`<div class="toolbar"><label class="inline-label">统计月份<select id="invite-month">${monthOptions(inviteMonth)}</select></label><input id="invite-search" placeholder="搜索邀请人、被邀请人或用户名" aria-label="搜索邀请"><select id="invite-status" aria-label="邀请状态"><option value="">全部状态</option><option value="1">有效链接</option><option value="0">已撤销</option></select></div><div class="view-tabs invite-tabs"><button data-invite-view="links" class="${inviteView==='links'?'selected':''}">专属链接</button><button data-invite-view="summary" class="${inviteView==='summary'?'selected':''}">当月邀请汇总</button><button data-invite-view="details" class="${inviteView==='details'?'selected':''}">入群时间明细</button></div><div id="invite-results"></div>`,`<button class="primary" id="export-invites">↓ 导出 ${inviteMonth} Excel</button>`);
};
drawInvites=function(){
 const query=($('#invite-search')?.value||'').toLowerCase(),status=$('#invite-status')?.value;
 const {joins,summary}=monthlyInviteSummary();let headers,rows;
 if(inviteView==='details'){
  headers=['邀请人','被邀请用户','社群','成功入群时间（UTC+8）','计数','奖励积分'];
  rows=joins.filter(j=>`${person(j.group_id,j.inviter_id).name} ${person(j.group_id,j.inviter_id).username} ${j.invitee_name} ${j.invitee_id}`.toLowerCase().includes(query)).sort((a,b)=>new Date(b.created)-new Date(a.created)).map(j=>`<tr><td>${esc(person(j.group_id,j.inviter_id).name)}<small>${j.inviter_id}</small></td><td>${esc(j.invitee_name)}<small>${j.invitee_id}</small></td><td>${esc(groupTitle(j.group_id))}</td><td>${fullDate(j.created)}</td><td>1</td><td class="positive">+${j.points}</td></tr>`);
 }else if(inviteView==='summary'){
  headers=['邀请用户','社群','当月邀请数量','最近入群时间（UTC+8）','邀请奖励'];
  rows=summary.filter(r=>`${r.name} ${r.username} ${r.user_id}`.toLowerCase().includes(query)).map(r=>`<tr><td>${esc(r.name)}<small>@${esc(r.username)} · ${r.user_id}</small></td><td>${esc(groupTitle(r.group_id))}</td><td class="purple points-value">${r.count}</td><td>${r.last?fullDate(r.last):'暂无'}</td><td class="positive">+${num(r.points)}</td></tr>`);
 }else{
  headers=['邀请用户','专属链接','当月 / 历史邀请','状态','操作'];
  rows=data.invites.filter(r=>(!group||String(r.group_id)===group)&&(status===''||String(r.active)===status)&&`${r.name} ${r.username} ${r.url}`.toLowerCase().includes(query)).map(r=>{const count=joins.filter(j=>j.group_id===r.group_id&&j.inviter_id===r.user_id).length;return `<tr><td><b>${esc(r.name)}</b><small>@${esc(r.username)} · ${esc(groupTitle(r.group_id))}</small></td><td><span class="link-cell">${esc(r.url)}</span><button class="text-button" data-copy="${r.id}">复制链接 ⧉</button></td><td class="purple">${count} / ${r.count}</td><td><span class="badge ${r.active?'':'off'}">${r.active?'有效':'已撤销'}</span></td><td>${r.active?`<button class="danger" data-revoke="${r.id}">撤销</button>`:'—'}</td></tr>`;});
 }
 $('#invite-results').innerHTML=table(headers,rows)+`<div class="table-foot"><span>${inviteMonth} · ${rows.length} 条记录</span><span>Excel 包含邀请汇总与入群明细两张工作表</span></div>`;
 if($('#invite-status'))$('#invite-status').hidden=inviteView!=='links';
};
function exportInvites(){
 const {joins,summary}=monthlyInviteSummary();
 const workbook=makeExcelWorkbook([
  {name:'用户邀请汇总',widths:[14,25,20,22,30,16,25,18],rows:[['统计月份','社群名称','邀请人ID','邀请人昵称','用户名','邀请数量','最近入群时间(UTC+8)','邀请奖励积分'],...summary.map(r=>[inviteMonth,groupTitle(r.group_id),String(r.user_id),r.name,r.username,r.count,r.last?fullDate(r.last):'',r.points])]},
  {name:'入群明细',widths:[14,25,20,22,26,20,26,28,14,16],rows:[['统计月份','社群名称','邀请人ID','邀请人昵称','邀请人用户名','被邀请人ID','被邀请人昵称','入群时间(UTC+8)','邀请数量','奖励积分'],...joins.map(j=>[inviteMonth,groupTitle(j.group_id),String(j.inviter_id),person(j.group_id,j.inviter_id).name,person(j.group_id,j.inviter_id).username,String(j.invitee_id),j.invitee_name,fullDate(j.created),1,j.points])]}
 ]);saveDownload(workbook,`CoinW-邀请数据-${inviteMonth}.xlsx`);toast('Excel 已导出：用户邀请汇总 + 入群时间明细');
}
function pointsScreen(){
 const groups=selectedGroups();
 return monthNotice()+panel('⚙ 积分奖励设置','每个社群独立配置，保存后仅影响新的签到和成功邀请。',`<div class="reward-cards">${groups.map(g=>`<form class="reward-card" data-reward-gid="${g.id}"><div class="reward-title"><span class="group-avatar">C</span><div><h3>${esc(g.title)}</h3><small>${g.id}</small></div><span class="badge">独立规则</span></div><div class="reward-inputs"><label>每日签到奖励<div class="number-input"><input name="checkin_points" type="number" min="0" max="10000" value="${g.checkin_points}" required><span>积分 / 次</span></div><small>每天每人一次，发送 coinw 领取</small></label><label>成功邀请奖励<div class="number-input"><input name="invite_points" type="number" min="0" max="10000" value="${g.invite_points}" required><span>积分 / 人</span></div><small>新用户成功入群后发放，重复加入不奖励</small></label></div><div class="reward-footer"><small>修改奖励不回算已有积分</small><button class="primary" type="submit">保存规则 ↗</button></div></form>`).join('')}</div>`);
}
function createDrawForm(){
 return `<form id="draw-create-form" class="draw-create"><h3>创建新活动</h3><label>发布社群<select name="group_id" required>${data.groups.map(g=>`<option value="${g.id}" ${String(g.id)===group?'selected':''}>${esc(g.title)}</option>`).join('')}</select></label><label>活动名称<input name="title" required maxlength="80" placeholder="e.g. CoinW Weekend Lucky Draw"></label><label>奖励内容<textarea name="prize" required maxlength="300" rows="2" placeholder="e.g. 100 USDT, VIP membership or limited merchandise"></textarea></label><div class="form-grid"><label>参与消耗积分<input name="entry_cost" type="number" min="0" max="10000" value="50" required></label><label>中奖人数<input name="winner_count" type="number" min="1" max="100" value="1" required></label></div><label class="upload-area"><span>▧</span><b>添加活动图片</b><small>选择 PNG / JPG / WebP，最大 1MB，可选</small><input id="draw-image" type="file" accept="image/png,image/jpeg,image/webp"></label><button type="button" id="remove-draw-image" class="text-button" hidden>移除已选图片</button><label class="image-language-confirm draw-image-language"><input type="checkbox" id="draw-image-en-confirm"> 我确认配图内文字为英文或无文字</label><div class="draw-form-footer"><span id="draw-create-error" role="alert"></span><button class="primary" type="submit">＋ 保存活动草稿</button></div><p class="form-hint">先保存草稿，再确认发送并置顶。当前为前端演示，不会发送到真实群。</p></form>`;
}
function poster(draw,interactive=false){
 const image=draw?.image||drawImage,title=draw?.title||$('#draw-create-form [name=title]')?.value||'CoinW Community Lucky Draw',prize=draw?.prize||$('#draw-create-form [name=prize]')?.value||'Add your prizes and give your community something to look forward to.',cost=draw?.entry_cost??Number($('#draw-create-form [name=entry_cost]')?.value??50),count=draw?.winner_count??Number($('#draw-create-form [name=winner_count]')?.value??1);
 return `<div class="tg-pinned">⌖ Pinned announcement <span>PREVIEW</span></div><div class="draw-poster">${image?`<img class="draw-poster-image" src="${esc(image)}" alt="Lucky draw image">`:'<div class="draw-cover"><span>✦</span><small>COINW COMMUNITY</small><b>LUCKY DRAW</b><p>A little luck. A lot of connection.</p><span class="cover-orbit">◌</span></div>'}<div class="draw-poster-body"><small>CoinW Management</small><h3>🎁 ${esc(telegramEnglish.valid(title)?title:'Please enter an English activity name.')}</h3><p class="draw-prize">${esc(telegramEnglish.valid(prize)?prize:'Please describe your prizes in English.').replaceAll('\n','<br>')}</p><div class="draw-meta"><span>Entry cost <b>${cost} points</b></span><span>Winners <b>${count}</b></span></div><p class="form-hint">One entry per member. Entry cost is deducted from your current monthly balance.</p>${draw?`<p class="draw-participant-count">Participants: ${draw.entries.length}${draw.status==='ended'?' · Draw ended':''}</p>`:''}<button type="button" class="draw-join-button" ${interactive&&draw?.status==='active'?`data-enter-draw="${draw.id}"`:'disabled'}>${draw?.status==='ended'?'This draw has ended':'🎟 Join for '+cost+' points'}</button></div></div>`;
}
function drawsScreen(){
 const ds=data.draws.filter(d=>(!group||String(d.group_id)===group)&&(!drawStatus||d.status===drawStatus));
 return `<div class="draw-overview"><div><span class="eyebrow">A LITTLE LUCK. A LOT OF CONNECTION.</span><h2>让社群互动，多一份期待。</h2><p>图文活动 · 积分参与 · 随机开奖 · 中奖名单</p></div><div class="draw-status-box"><b>${data.draws.filter(d=>d.status==='active').length}</b><span>进行中活动</span></div></div>`+panel('♜ Lucky Draw 管理','配置活动，预览群公告，再发送并置顶。',`<div class="draw-create-grid">${createDrawForm()}<div class="draw-preview-area"><div class="preview-label"><span>群公告实时预览</span><span class="tag">TELEGRAM PREVIEW</span></div><div id="new-draw-poster">${poster(null)}</div></div></div>`,'<span class="tag amber">TG 待关联</span>')+`<div class="draw-list-head"><h2>活动记录 <span>${ds.length}</span></h2><select id="draw-status-filter" aria-label="活动状态"><option value="">全部活动</option><option value="draft" ${drawStatus==='draft'?'selected':''}>草稿</option><option value="active" ${drawStatus==='active'?'selected':''}>进行中</option><option value="ended" ${drawStatus==='ended'?'selected':''}>已开奖</option></select></div><div class="draw-records">${ds.length?ds.map(d=>`<article class="draw-record"><div class="draw-record-top"><span class="badge ${d.status==='active'?'':'off'}">${{draft:'草稿',active:'进行中',ended:'已开奖'}[d.status]}</span><span class="tag amber">${d.status==='draft'?'尚未发布':'演示公告 · TG 尚未同步'}</span></div><h3>${esc(d.title)}</h3><p>${esc(d.prize)}</p><div class="draw-record-meta"><span>${esc(groupTitle(d.group_id))}</span><span>${d.entry_cost} 积分 / 人</span><span>中奖 ${d.winner_count} 位</span><span>已参与 ${d.entries.length} 人</span></div><div class="draw-record-actions">${d.status==='draft'?`<button class="primary" data-publish-draw="${d.id}">↗ 发送并置顶（演示）</button>`:`<button class="button" data-preview-draw="${d.id}">查看公告与参与</button>`}${d.status==='active'?`<button class="primary" data-finish-draw="${d.id}">✦ 立即开奖</button>`:''}${d.status==='ended'?`<button class="primary" data-winners-draw="${d.id}">♜ 查看中奖名单</button>`:''}</div></article>`).join(''):empty('还没有活动。保存第一场抽奖，给社群一个小惊喜。')}</div>`;
}
function makeDialog(id,content){const dialog=document.createElement('dialog');dialog.id=id;dialog.innerHTML=content;document.body.appendChild(dialog);dialog.addEventListener('click',e=>{if(e.target.closest('[data-close-dialog]'))dialog.close();});return dialog;}
const adjustDialog=makeDialog('adjust-dialog',`<form id="adjust-form"><div class="modal-heading"><div><span class="eyebrow">POINTS ADJUSTMENT</span><h2>手动调整积分</h2></div><button type="button" class="icon-button" data-close-dialog aria-label="关闭">×</button></div><div class="form-section"><label>社群<select name="group_id"></select></label><label>用户<select name="user_id"></select></label><div id="adjust-balance" class="balance-summary"></div><div class="form-grid"><label>调整类型<select name="direction"><option value="1">增加积分</option><option value="-1">减少积分</option></select></label><label>积分数量<input name="amount" type="number" min="1" max="1000000" value="10" required></label></div><label>调整原因<textarea name="reason" rows="3" maxlength="200" required placeholder="如：活动奖励、积分补发、违规扣分"></textarea></label><p class="form-hint">仅调整当前月份，操作会保留流水。扣减后余额不得低于零。</p></div><div class="modal-footer"><span id="adjust-error" role="alert"></span><button type="submit" class="primary">确认调整</button></div></form>`);
const historyDialog=makeDialog('history-dialog','<div class="modal-heading"><div><span class="eyebrow">POINTS HISTORY</span><h2 id="history-title">积分流水</h2></div><button class="icon-button" data-close-dialog aria-label="关闭">×</button></div><div id="history-body"></div>');
const drawDialog=makeDialog('draw-dialog','<div class="modal-heading"><div><span class="eyebrow">GROUP ANNOUNCEMENT PREVIEW</span><h2 id="draw-dialog-title">群公告与参与</h2></div><button class="icon-button" data-close-dialog aria-label="关闭">×</button></div><div id="draw-dialog-body"></div>');
function updateAdjustUsers(uid){const f=$('#adjust-form'),gid=Number(f.elements.group_id.value);f.elements.user_id.innerHTML=data.members.filter(m=>m.group_id===gid).map(m=>`<option value="${m.user_id}">${esc(m.name)} · ${m.user_id}</option>`).join('');if(uid)f.elements.user_id.value=String(uid);updateBalance();}
function updateBalance(){const f=$('#adjust-form'),b=score(Number(f.elements.group_id.value),Number(f.elements.user_id.value));$('#adjust-balance').innerHTML=`本月可用积分 <b>${num(b)}</b><span>调整后预计 <b>${num(b+Number(f.elements.amount.value||0)*Number(f.elements.direction.value))}</b></span>`;}
function openAdjust(gid,uid){const f=$('#adjust-form');f.reset();$('#adjust-error').textContent='';f.elements.group_id.innerHTML=selectedGroups().map(g=>`<option value="${g.id}">${esc(g.title)}</option>`).join('');if(gid)f.elements.group_id.value=String(gid);updateAdjustUsers(uid);adjustDialog.showModal();}
function showHistory(gid,uid){const user=person(gid,uid);$('#history-title').textContent=user.name+' · '+rankingMonth+' 积分流水';const rows=data.ledger.filter(l=>l.group_id===gid&&l.user_id===uid&&l.month===rankingMonth).sort((a,b)=>new Date(b.created)-new Date(a.created));$('#history-body').innerHTML=table(['时间（UTC+8）','类型','积分变动','原因'],rows.map(l=>`<tr><td>${fullDate(l.created)}</td><td>${esc(l.kind)}</td><td class="${l.points>=0?'positive':'negative'}">${l.points>0?'+':''}${l.points}</td><td>${esc(l.reason)}</td></tr>`))+`<div class="table-foot"><span>${rankingMonth===data.month?'本月可用余额':'该月月末积分'} ${num(score(gid,uid,rankingMonth))}</span><span>历史记录保留</span></div>`;historyDialog.showModal();}
function showDraw(id,winners=false){
 const d=data.draws.find(d=>d.id===id);if(!d)return;selectedDrawId=id;
 $('#draw-dialog-title').textContent=winners?'中奖名单':'群公告与积分参与';
 if(winners){$('#draw-dialog-body').innerHTML=`<div class="winner-banner"><span>♜</span><h3>${esc(d.title)}</h3><p>${esc(d.prize)}</p><small>开奖时间 ${fullDate(d.ended)}（UTC+8） · 中奖结果已锁定</small></div>`+table(['中奖用户','Telegram ID','用户名'],d.winners.map(w=>`<tr><td>${esc(w.name)}</td><td>${w.user_id}</td><td>@${esc(w.username)}</td></tr>`))+'<p class="dialog-note">前端演示结果；实物或代币奖励需由运营人员核对后发放。</p>';}
 else{
 const ms=data.members.filter(m=>m.group_id===d.group_id);if(!ms.some(m=>m.user_id===drawActor))drawActor=999;
 $('#draw-dialog-body').innerHTML=`<div class="dialog-note">当前只模拟公告、置顶及积分参与，尚未发送真实 Telegram。发布群：${esc(groupTitle(d.group_id))}</div><div class="sim-user"><label>模拟参与用户<select id="draw-sim-user">${ms.map(m=>`<option value="${m.user_id}" ${m.user_id===drawActor?'selected':''}>${esc(m.name)} · ${num(score(d.group_id,m.user_id))} 积分</option>`).join('')}</select></label></div><div class="announcement-body">${poster(d,true)}</div>`;
 }
 if(!drawDialog.open)drawDialog.showModal();
}
const featureBaseRender=render;
render=function(){
 featureBaseRender();
 if(page==='points')$('#content').innerHTML=pointsScreen();
 if(page==='draws'){drawImage='';$('#content').innerHTML=drawsScreen();}
 if(page==='ranking'){
  $('#ranking-month').onchange=e=>{rankingMonth=e.target.value;render();};
  $('#rank-search').oninput=e=>{const pos=e.target.selectionStart;rankSearch=e.target.value;render();$('#rank-search').focus();$('#rank-search').setSelectionRange(pos,pos);};
 }
 if(page==='invites')$('#invite-month').onchange=e=>{inviteMonth=e.target.value;render();};
 const insight=$('.preview-insight>span');if(insight){const gs=selectedGroups();insight.textContent=gs.length===1?`签到 +${gs[0].checkin_points} · 邀请 +${gs[0].invite_points} · 每月重置积分`:'各群独立奖励规则 · 每月清零 · 历史记录保留';}
 if(drawDialog.open&&selectedDrawId&&$('#draw-sim-user'))showDraw(selectedDrawId);
};
$('#adjust-form [name=group_id]').onchange=()=>updateAdjustUsers();$('#adjust-form').addEventListener('input',updateBalance);
document.addEventListener('input',e=>{if(e.target.closest('#draw-create-form')&&e.target.id!=='draw-image')$('#new-draw-poster').innerHTML=poster(null);});
document.addEventListener('change',async e=>{
 if(e.target.id==='draw-status-filter'){drawStatus=e.target.value;render();}
 if(e.target.id==='draw-sim-user'){drawActor=Number(e.target.value);}
 if(e.target.id==='draw-image'){
  const file=e.target.files[0];if(!file)return;
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>1048576){e.target.value='';toast('请选择 1MB 以内的 PNG、JPG 或 WebP 图片');return;}
  const reader=new FileReader();reader.onload=()=>{if(!$('#draw-image')||$('#draw-image')!==e.target)return;drawImage=reader.result;$('#remove-draw-image').hidden=false;$('#new-draw-poster').innerHTML=poster(null);};reader.onerror=()=>toast('读取图片失败，请重新选择');reader.readAsDataURL(file);
 }
});
document.addEventListener('submit',async e=>{
 const f=e.target;
 if(f.id!=='adjust-form'&&f.id!=='draw-create-form'&&!f.dataset.rewardGid)return;
 e.preventDefault();const button=e.submitter;button.disabled=true;
 try{
  if(f.id==='adjust-form'){await api('/api/points/adjust',{group_id:Number(f.elements.group_id.value),user_id:Number(f.elements.user_id.value),delta:Number(f.elements.amount.value)*Number(f.elements.direction.value),reason:f.elements.reason.value});adjustDialog.close();toast('积分已调整，已保留操作流水');}
  else if(f.id==='draw-create-form'){await api('/api/draws/create',{group_id:Number(f.elements.group_id.value),title:f.elements.title.value,prize:f.elements.prize.value,entry_cost:Number(f.elements.entry_cost.value),winner_count:Number(f.elements.winner_count.value),image:drawImage,image_english_confirmed:!!$('#draw-image-en-confirm')?.checked});drawImage='';toast('活动草稿已保存，确认后可发送并置顶演示公告');}
  else{await api('/api/settings',{group_id:Number(f.dataset.rewardGid),checkin_points:Number(f.elements.checkin_points.value),invite_points:Number(f.elements.invite_points.value)});toast('奖励规则已保存，对后续签到与邀请生效');}
  await refresh();
 }catch(error){const label=f.id==='adjust-form'?$('#adjust-error'):f.id==='draw-create-form'?$('#draw-create-error'):null;if(label)label.textContent=error.message;else toast(error.message);}finally{button.disabled=false;}
});
document.addEventListener('click',async e=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.id==='manual-points')openAdjust(Number(group)||null);
 if(b.dataset.adjustUser)openAdjust(Number(b.dataset.gid),Number(b.dataset.adjustUser));
 if(b.dataset.userHistory)showHistory(Number(b.dataset.gid),Number(b.dataset.userHistory));
 if(b.hasAttribute('data-rank-current')){rankingMonth=data.month;render();}
 if(b.hasAttribute('data-rank-history')){rankingMonth=data.months.find(m=>m!==data.month)||data.month;render();}
 if(b.dataset.inviteView){inviteView=b.dataset.inviteView;render();}
 if(b.id==='export-invites')exportInvites();
 if(b.id==='remove-draw-image'){drawImage='';$('#draw-image').value='';b.hidden=true;$('#new-draw-poster').innerHTML=poster(null);}
 if(b.dataset.previewDraw)showDraw(b.dataset.previewDraw);
 if(b.dataset.winnersDraw)showDraw(b.dataset.winnersDraw,true);
 let operation,drawId;
 if(b.dataset.publishDraw){if(!confirm('确认发布此活动的演示公告并预览置顶？当前未关联 TG，不会发送到真实群。'))return;operation='publish';drawId=b.dataset.publishDraw;}
 if(b.dataset.enterDraw){operation='enter';drawId=b.dataset.enterDraw;}
 if(b.dataset.finishDraw){if(!confirm('立即随机开奖？中奖结果锁定后不能重抽。'))return;operation='finish';drawId=b.dataset.finishDraw;}
 if(operation){b.disabled=true;try{await api('/api/draws/'+operation,{id:drawId,user_id:drawActor});await refresh();if(operation==='finish')showDraw(drawId,true);else showDraw(drawId);toast(operation==='publish'?'演示公告已生成，真实 TG 发送待关联':operation==='enter'?'参与成功，已扣除本月积分':'开奖完成，结果已锁定');}catch(error){toast(error.message);}finally{b.disabled=false;}}
});
// Add an invite-success demonstration to the existing bot simulator.
$('#bot-demo-dialog .chat-actions').insertAdjacentHTML('beforeend','<button class="button" data-command="invite_success">＋ 模拟成功邀请</button>');

if(pages[location.hash.slice(1)])page=location.hash.slice(1);
