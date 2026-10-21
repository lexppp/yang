/* Enhancements for the standalone preview only. */
const demoDialog=document.createElement('dialog');
demoDialog.id='bot-demo-dialog';
demoDialog.innerHTML=`<div class="modal-heading"><div><span class="eyebrow">TRY YOUR COMMUNITY BOT</span><h2>体验群内互动</h2></div><button class="icon-button" id="close-bot-demo" aria-label="关闭">×</button></div><div class="chat-group"><span class="group-avatar">C</span><div><b id="chat-group-title">CoinW Official</b><small>机器人互动演示 · 所有操作仅在本地生效</small></div><span class="badge">演示</span></div><div id="chat-history" class="chat-history" aria-live="polite"><div class="chat-message bot-message"><small>CoinW Management</small><p>Welcome to the CoinW community! 👋<br>Try checking in, getting your invite link, or posting a link.</p></div></div><div class="chat-actions"><button class="primary" data-command="coinw">✓ 签到 coinw</button><button class="button" data-command="/ink">↗ 获取邀请链接</button><button class="button" data-command="https://example.com">◇ 测试链接拦截</button></div><p class="chat-note">修改社群规则后，再发送一条消息看看效果。</p>`;
document.body.appendChild(demoDialog);
const demoButton=document.createElement('button');demoButton.className='primary';demoButton.id='try-bot';demoButton.textContent='▷ 体验机器人';$('.heading-actions').appendChild(demoButton);
let demoGroup=null;
demoButton.onclick=()=>{demoGroup=Number(group)||data.groups[0].id;$('#chat-group-title').textContent=data.groups.find(g=>g.id===demoGroup).title;demoDialog.showModal();};
$('#close-bot-demo').onclick=()=>demoDialog.close();
demoDialog.addEventListener('click',async e=>{
 const button=e.target.closest('[data-command]');if(!button)return;
 const command=button.dataset.command;let result;try{result=window.previewActions.simulate(demoGroup,command);}catch(error){toast(error.message);return;}
 $('#chat-history').insertAdjacentHTML('beforeend',`<div class="chat-message user-message"><small>体验用户</small><p>${esc(command)}</p></div><div class="chat-message bot-message"><small>CoinW Management</small><p>${esc(result.text).replaceAll('\n','<br>')}</p></div>`);
 $('#chat-history').scrollTop=$('#chat-history').scrollHeight;await refresh();
});
function resetPreview(){if(confirm('重置前端演示？将恢复初始示例数据和社群设置。'))window.previewActions.reset();}
$('#logout').onclick=resetPreview;$('#logout-mobile').onclick=resetPreview;
$('#logout').innerHTML='<span class="avatar">↻</span><span>重置演示数据<small>恢复初始预览状态</small></span><span>↺</span>';
$('#logout-mobile').setAttribute('aria-label','重置演示数据');
document.addEventListener('click',e=>{
 const link=e.target.closest('a[href^="/api/export"]');if(!link)return;e.preventDefault();
 const url=URL.createObjectURL(new Blob([window.previewActions.csv(Number(group)||null)],{type:'text/csv;charset=utf-8'}));
 const download=document.createElement('a');download.href=url;download.download='coinw-demo-credits.csv';download.click();setTimeout(()=>URL.revokeObjectURL(url),5000);toast('演示积分数据已导出');
});
const firstSection=$('#settings-form .form-section');
firstSection.insertAdjacentHTML('beforeend','<div class="welcome-preview"><span class="eyebrow">欢迎消息预览</span><div class="welcome-picture" id="welcome-picture"><span>✦</span><b>Welcome to CoinW</b><small>连接社群，发现更多可能。</small></div><p id="welcome-copy"></p></div>');
function updateWelcome(){const f=$('#settings-form'),name=telegramEnglish.group({title:$('#settings-title').textContent}),template=f.elements.welcome.value;$('#welcome-copy').textContent=telegramEnglish.valid(template)?template.replaceAll('{name}','new member').replaceAll('{group}',name):'Please write your welcome message in English.';$('#welcome-picture').hidden=!f.elements.welcome_photo.value;}
$('#settings-form').addEventListener('input',updateWelcome);
new MutationObserver(()=>{if($('#settings-dialog').open)updateWelcome();}).observe($('#settings-dialog'),{attributes:true,attributeFilter:['open']});
const baseRender=render;
render=function(){baseRender();if(page==='overview'&&data.groups.length){const middle=$('.overview-middle');middle?.insertAdjacentHTML('beforebegin',`<div class="preview-insight"><div><span class="insight-icon">✦</span><span>让每一次互动，都成为社群增长的起点。</span></div><span>签到激励 <b>+5</b><i>·</i> 邀请奖励 <b>+20</b><i>·</i> 全天候社群守护</span></div>`);}if(page==='invites'){const subtitle=$('#content .panel-head p');if(subtitle)subtitle.textContent='专属链接、有效邀请与奖励记录。当前展示不可用于真实入群的演示链接。';}};
