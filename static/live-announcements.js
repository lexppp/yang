/* Real server-persisted announcements. The server validates permissions and English output. */
(() => {
pages.announcements=['公告管理','图文公告、指定话题、定时发布与每日多时段，任务由服务器持续执行。'];
const nav=document.createElement('button');nav.className='nav-item';nav.dataset.page='announcements';nav.innerHTML='<span>◩</span> 公告管理';document.querySelector('nav').append(nav);
const localInput=ms=>new Date(ms+28800000).toISOString().slice(0,16);
const today=()=>localInput(Date.now()).slice(0,10);
const fresh=()=>({id:null,title:'',group_id:Number(group)||data.groups[0]?.id,topic_id:'',body:'',image:'',image_english_confirmed:false,pin:true,mode:'now',once_at:localInput(Date.now()+3600000),times:['09:00','15:00','21:00'],start_date:today(),end_date:''});
const states={draft:'草稿',scheduled:'已启用',paused:'已暂停',completed:'本轮已处理',cancelled:'已取消'};
const outcomes={sending:'发送结果待确认',sent:'已发送',sent_pin_failed:'已发送，置顶失败',unknown:'发送未确认，请到群里核对',missed:'已错过，未补发'};
let draft=null,busy=false;
const manage=()=>accessAllowed(currentUser,'announcements.manage');
function capture(){const f=$('#live-announcement-form');if(!f||!draft)return;for(const key of ['title','body','image','once_at','start_date','end_date','mode'])draft[key]=f.elements[key].value;draft.group_id=Number(f.elements.group_id.value);draft.topic_id=f.elements.topic_id.value;draft.pin=f.elements.pin.checked;draft.image_english_confirmed=f.elements.image_english_confirmed.checked;draft.times=[...f.querySelectorAll('[data-ann-time]')].map(i=>i.value);}
function times(){return draft.times.map((v,i)=>`<label>第 ${i+1} 次<input type="time" data-ann-time value="${esc(v)}" required></label>`).join('');}
function topicOptions(){return (data.forum_topics||[]).filter(t=>t.group_id===Number(draft.group_id)).map(t=>`<option value="${t.topic_id}">${esc(t.name)}</option>`).join('');}
function screen(){
 draft=draft||fresh();const d=draft, plans=data.announcements||[],history=data.announcement_deliveries||[];
 const editor=manage()?panel('◩ 创建公告','正文仅发送英文，任务和发布记录保存在服务器。',`<form id="live-announcement-form" class="live-ann-form">
 <div class="form-grid"><label>后台名称（仅后台可见）<input name="title" maxlength="100" value="${esc(d.title)}" required></label><label>目标社群<select name="group_id" required>${data.groups.map(g=>`<option value="${g.id}" ${g.id===Number(d.group_id)?'selected':''}>${esc(g.title)}</option>`).join('')}</select></label></div>
 <label>话题 ID（留空为默认聊天）<input name="topic_id" type="number" min="1" list="ann-topic-list" value="${esc(d.topic_id||'')}" placeholder="例如 123"><datalist id="ann-topic-list">${topicOptions()}</datalist></label><p class="form-hint">机器人观察到的话题会出现在候选项。若目标话题尚未记录，在话题里发一条消息并刷新看板；也可手填话题 ID（不要填链接最后的消息 ID）。普通群留空。</p>
 <label>英文公告正文<textarea name="body" rows="6" maxlength="4096" required placeholder="Hello CoinW community! Join our latest event.">${esc(d.body)}</textarea></label>
 <label>公告图片（可选）<input name="image" maxlength="1000" value="${esc(d.image)}" placeholder="HTTPS 图片直链，或私聊机器人发送照片取得的 file_id"></label><label class="ann-check"><input name="image_english_confirmed" type="checkbox" ${d.image_english_confirmed?'checked':''}> 图片为英文或无文字；系统不识别图片内文字。有图片时正文最多1024字。</label>
 <label class="ann-check"><input name="pin" type="checkbox" ${d.pin?'checked':''}> 发布后置顶（机器人需有置顶权限）</label>
 <div class="form-grid"><label>发布方式<select name="mode"><option value="now" ${d.mode==='now'?'selected':''}>立即发送</option><option value="once" ${d.mode==='once'?'selected':''}>定时一次</option><option value="daily" ${d.mode==='daily'?'selected':''}>每天重复</option></select></label><label id="ann-once" ${d.mode==='once'?'':'hidden'}>发布时间（UTC+8）<input name="once_at" type="datetime-local" value="${esc(d.once_at)}" ${d.mode==='once'?'required':''}></label></div>
 <section id="ann-daily" ${d.mode==='daily'?'':'hidden'}><div class="form-grid"><label>开始日期<input name="start_date" type="date" value="${esc(d.start_date)}"></label><label>结束日期（可选）<input name="end_date" type="date" value="${esc(d.end_date||'')}"></label></div><label>每天发送次数<select id="ann-frequency">${Array.from({length:24},(_,i)=>`<option value="${i+1}" ${d.times.length===i+1?'selected':''}>${i+1} 次</option>`).join('')}</select></label><div class="form-grid" id="ann-times">${times()}</div></section>
 <p class="form-hint">所有时间为北京时间 UTC+8。关闭电脑后服务器仍会发布；错过超过5分钟的任务不补发。发送未确认时不会自动重发，请先到群里检查。</p>
 <div id="ann-error" role="alert" class="negative"></div><div class="live-ann-actions"><button type="submit" class="button" data-activate="0">保存草稿</button><button type="submit" class="primary" data-activate="1">${d.mode==='now'?'发送公告':'启用定时'}</button><button type="button" class="text-button" id="ann-reset">＋ 新建</button></div></form>`):'';
 return editor+panel('◷ 发布计划','可查看状态、编辑、暂停或取消。',table(['名称 / 内容','目标','安排（UTC+8）','状态 / 下次执行','操作'],plans.map(p=>`<tr><td><b>${esc(p.title)}</b><small>${esc(p.body.slice(0,70))}</small>${p.last_error?`<small class="negative">${esc(p.last_error)}</small>`:''}</td><td>${esc(data.groups.find(g=>g.id===p.group_id)?.title||p.group_id)}<small>话题 ${p.topic_id||'默认'}${p.pin?' · 置顶':''}</small></td><td>${p.mode==='daily'?`每天 ${p.times.length} 次<small>${esc(p.times.join(' · '))}</small>`:p.mode==='once'?'定时一次':'立即发布'}</td><td>${states[p.status]||esc(p.status)}<small>${p.next_run?date(p.next_run):'—'}</small></td><td>${manage()?`<button class="text-button" data-ann-edit="${p.id}">编辑</button>${['draft','scheduled','paused'].includes(p.status)?`<button class="text-button" data-ann-action="${p.status==='scheduled'?'pause':'resume'}" data-ann-id="${p.id}">${p.status==='scheduled'?'暂停':'启用'}</button><button class="text-button negative" data-ann-action="cancel" data-ann-id="${p.id}">取消</button>`:''}`:'只读'}</td></tr>`)))+
 panel('≡ 实际发布记录','发送和置顶分别记录。未确认的结果需要到 Telegram 群里核对。',table(['公告','计划时间','结果','消息 ID / 置顶','详情'],history.map(h=>`<tr><td>${esc(h.title)}</td><td>${date(h.scheduled_at)}</td><td>${outcomes[h.status]||esc(h.status)}</td><td>${h.message_id||'—'}<small>${{pinned:'置顶成功',failed:'置顶失败'}[h.pin_status]||'未置顶'}</small></td><td>${esc(h.error)}</td></tr>`)));
}
const originalRender=render;render=function(){originalRender();if(page==='announcements')$('#content').innerHTML=screen();};
document.addEventListener('input',e=>{if(e.target.closest('#live-announcement-form'))capture();});
document.addEventListener('change',e=>{
 if(!e.target.closest('#live-announcement-form'))return;capture();
 if(e.target.name==='mode'){$('#ann-once').hidden=draft.mode!=='once';$('#ann-daily').hidden=draft.mode!=='daily';$('#live-announcement-form [name=once_at]').required=draft.mode==='once';$('#live-announcement-form [data-activate="1"]').textContent=draft.mode==='now'?'发送公告':'启用定时';}
 if(e.target.name==='group_id'){$('#ann-topic-list').innerHTML=topicOptions();$('#live-announcement-form [name=topic_id]').value='';draft.topic_id='';}
 if(e.target.id==='ann-frequency'){draft.times=Array.from({length:Number(e.target.value)},(_,i)=>String(Math.floor(i*24/Number(e.target.value))).padStart(2,'0')+':00');$('#ann-times').innerHTML=times();}
});
document.addEventListener('submit',async e=>{
 if(e.target.id!=='live-announcement-form')return;e.preventDefault();if(busy)return;capture();const f=e.target,b=e.submitter;busy=true;b.disabled=true;$('#ann-error').textContent='';
 try{await api('/api/announcements/save',{...draft,topic_id:draft.topic_id?Number(draft.topic_id):null,activate:b.dataset.activate==='1'});draft=null;await refresh();toast('公告已保存，请查看发布状态和记录');}catch(error){$('#ann-error').textContent=error.message;}finally{busy=false;b.disabled=false;}
});
document.addEventListener('click',async e=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.id==='ann-reset'){draft=fresh();render();}
 if(b.dataset.annEdit){const p=data.announcements.find(p=>p.id===Number(b.dataset.annEdit));draft={...p,image_english_confirmed:!!p.image,once_at:p.once_at?localInput(new Date(p.once_at).getTime()):localInput(Date.now()+3600000)};render();}
 if(b.dataset.annAction){b.disabled=true;try{await api('/api/announcements/'+b.dataset.annAction,{id:Number(b.dataset.annId)});await refresh();toast('公告状态已更新');}catch(error){toast(error.message);}finally{b.disabled=false;}}
});
})();
