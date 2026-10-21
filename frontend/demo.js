/* Standalone frontend model. All transactions and Telegram messages are demonstrations. */
(() => {
  const key='coinw-ui-preview-v2',TZ=8*3600000;
  const stamp=(minutes=0)=>new Date(Date.now()-minutes*60000).toISOString();
  const monthOf=value=>new Date(new Date(value).getTime()+TZ).toISOString().slice(0,7);
  const monthNow=()=>monthOf(new Date());
  const localDay=()=>new Date(Date.now()+TZ).toISOString().slice(0,10);
  const id=()=>globalThis.crypto?.randomUUID?.()||Date.now()+'-'+Math.random().toString(36).slice(2);
  function shiftedMonth(offset){const d=new Date(Date.now()+TZ);return new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+offset,1)).toISOString().slice(0,7);}
  function seed(){
    const names=['CoinW Official','CoinW 官方中文社群','CoinW 日本コミュニティー','CoinW Global Community','CoinW 越南社区','CoinW Türkiye'];
    const users=[406,389,310,450,286,222],messages=[13339,2610,1960,1898,3124,2382];
    const people=[['Hash','CoinW100'],['Aalam','Token2n34Forge'],['Mohd','Mohdking29'],['Brahma','BrahmaEcho'],['Patrick','gratisinsect'],['Charles','charlestolbertking'],['Chloe','chloe_yeung'],['Jade','Jade_warwick'],['体验用户','preview_member']];
    const groups=names.map((title,i)=>({id:-10010001-i,title,active:1,users:users[i],messages:messages[i],credits:0,checkins:126-i*13,invites:0,last_activity:stamp(i*3),welcome:telegramEnglish.welcome,welcome_photo:'',block_links:1,block_photos:i===1?1:0,block_ads:1,keywords:'代投,稳赚,刷单',verify:1,checkin_points:5,invite_points:20,language:'en'}));
    const members=groups.flatMap(g=>people.map(([name,username],i)=>({group_id:g.id,user_id:i===8?999:100+i,name,username})));
    const ledger=[];
    groups.forEach((g,j)=>people.forEach(([name,username],i)=>{
      for(const offset of [-2,-1,0]){const month=shiftedMonth(offset);ledger.push({id:id(),group_id:g.id,user_id:i===8?999:100+i,points:i===8?120:Math.max(30,3600-i*420-j*43-offset*60),kind:'示例期初',reason:'演示月份积分，不代表真实用户数据',month,created:month+'-01T00:00:00+08:00'});}
    }));
    const invites=groups.flatMap((g,j)=>people.slice(0,8).map(([name,username],i)=>({id:j*8+i+1,group_id:g.id,user_id:100+i,name,username,url:`demo://coinw/invite/${j}/${username}`,active:i===7?0:1,created:stamp(720+i*160+j*40)})));
    const joins=[];
    for(const g of groups){for(const offset of [-2,-1,0]){const month=shiftedMonth(offset);for(let i=0;i<8;i++){for(let k=0;k<Math.max(0,6-i);k++){
      const joined=month+`-${String(2+k*2).padStart(2,'0')}T${String(9+i).padStart(2,'0')}:30:00+08:00`;
      // Never show a future event in the current month.
      if(new Date(joined)>new Date())continue;
      joins.push({id:id(),group_id:g.id,inviter_id:100+i,invitee_id:200000+joins.length,invitee_name:['Alex','Mia','Liam','Emma','Noah','Olivia'][k]+' '+(joins.length+1),created:joined,month,points:g.invite_points});
      ledger.push({id:id(),group_id:g.id,user_id:100+i,points:g.invite_points,kind:'邀请奖励',reason:'示例有效邀请',month,created:joined});
    }}}}
    const kinds=[['签到','+5 积分'],['邀请入群','新成员通过验证，+20 积分'],['内容拦截','已删除包含链接的消息'],['签到','+5 积分'],['验证成功','新成员通过入群验证'],['设置更新','已更新中文群欢迎词']];
    const logs=kinds.map(([kind,detail],i)=>({id:id(),group_id:groups[i%6].id,user_id:i===5?null:100+i,kind,detail,created:stamp(i*4+1)}));
    return {version:2,groups,members,ledger,invites,joins,logs,checks:{},draws:[]};
  }
  let state;
  try{state=JSON.parse(localStorage.getItem(key));if(state?.version!==2)state=null;}catch{}
  if(!state){state=seed();try{const old=JSON.parse(localStorage.getItem('coinw-ui-preview-v1'));if(old?.groups){for(const g of state.groups){const legacy=old.groups.find(o=>o.id===g.id);if(legacy)for(const k of ['welcome','welcome_photo','block_links','block_photos','block_ads','keywords','verify','checkin_points','invite_points'])if(k in legacy)g[k]=legacy[k];}state.checks=old.checks||{};}}catch{}}
  for(const g of state.groups){if(['欢迎 {name} 加入 {group}！','欢迎 {name} 加入 {group}！\n发送 coinw 完成今日签到，发送 /ink 获取专属邀请链接。'].includes(g.welcome))g.welcome=telegramEnglish.welcome;g.language='en';}
  function save(){try{localStorage.setItem(key,JSON.stringify(state));}catch(e){if(e.name==='QuotaExceededError')throw new Error('浏览器存储空间不足，请使用更小的图片或移除旧演示图片。');}}
  function mutate(fn){const previous=JSON.stringify(state);try{const result=fn();save();return result;}catch(e){state=JSON.parse(previous);throw e;}}
  const member=(gid,uid)=>state.members.find(m=>m.group_id===gid&&m.user_id===uid);
  const balance=(gid,uid,month=monthNow())=>state.ledger.filter(l=>l.group_id===gid&&l.user_id===uid&&l.month===month).reduce((a,l)=>a+l.points,0);
  const post=(gid,uid,points,kind,reason)=>state.ledger.push({id:id(),group_id:gid,user_id:uid,points,kind,reason,month:monthNow(),created:stamp()});
  const event=(gid,kind,detail,uid=999)=>{state.logs.unshift({id:id(),group_id:gid,user_id:uid,kind,detail,created:stamp()});state.logs=state.logs.slice(0,100);};
  function requireGroup(gid){const g=state.groups.find(g=>g.id===Number(gid));if(!g)throw new Error('请选择有效社群');return g;}
  function integer(value,min,max,label){if(!Number.isInteger(value)||value<min||value>max)throw new Error(label+'超出有效整数范围');return value;}
  function snapshot(gid){
    const month=monthNow(),filter=rows=>rows.filter(r=>!gid||r.group_id===gid);
    const groups=state.groups.map(g=>({...g,credits:state.ledger.filter(l=>l.group_id===g.id&&l.month===month).reduce((sum,l)=>sum+l.points,0),invites:state.joins.filter(j=>j.group_id===g.id).length}));
    const ranking=filter(state.members).map(m=>({...m,monthly:balance(m.group_id,m.user_id),total:balance(m.group_id,m.user_id)}));
    const invites=filter(state.invites).map(i=>({...i,count:state.joins.filter(j=>j.group_id===i.group_id&&j.inviter_id===i.user_id).length,monthlyCount:state.joins.filter(j=>j.group_id===i.group_id&&j.inviter_id===i.user_id&&j.month===month).length}));
    const local=new Date(Date.now()+TZ),next=new Date(Date.UTC(local.getUTCFullYear(),local.getUTCMonth(),local.getUTCDate()+1)-TZ);
    return JSON.parse(JSON.stringify({groups,ranking,invites,members:filter(state.members),ledger:filter(state.ledger),joins:filter(state.joins),logs:filter(state.logs),draws:filter(state.draws),months:[...new Set([month,...filter(state.ledger).map(l=>l.month),...filter(state.joins).map(l=>l.month)])].sort().reverse(),demo:true,bot_status:'前端预览 · Telegram 未关联',month,server_time:stamp(),next_reset:next.toISOString(),next_month:shiftedMonth(1)+'-01T00:00:00+08:00'}));
  }
  window.previewApi=async(path,body)=>{
    const url=new URL(path,'https://preview.invalid');
    if(['/api/login','/api/session'].includes(url.pathname))return {csrf:'preview-only',demo:true};
    if(url.pathname==='/api/logout')return {ok:true};
    if(url.pathname==='/api/snapshot')return snapshot(Number(url.searchParams.get('group'))||null);
    return mutate(()=>{
      if(url.pathname==='/api/settings'){
        const g=requireGroup(body.group_id);
        for(const k of ['checkin_points','invite_points'])if(k in body)integer(body[k],0,10000,'奖励积分');
        for(const field of ['welcome','welcome_photo','block_links','block_photos','block_ads','keywords','verify','checkin_points','invite_points'])if(field in body)g[field]=body[field];
        event(g.id,'积分/规则设置','已保存，仅影响后续新奖励');return {ok:true};
      }
      if(url.pathname==='/api/revoke'){const invite=state.invites.find(r=>r.id===body.id);if(!invite)throw new Error('未找到邀请链接');invite.active=0;event(invite.group_id,'邀请链接','已在本地演示中撤销链接');return {ok:true};}
      if(url.pathname==='/api/points/adjust'){
        requireGroup(body.group_id);if(!member(body.group_id,body.user_id))throw new Error('该用户不属于所选社群');
        integer(body.delta,-1000000,1000000,'调整积分');if(!body.delta)throw new Error('调整积分不能为零');
        const reason=String(body.reason||'').trim();if(!reason||reason.length>200)throw new Error('请输入 1–200 字调整原因');
        if(balance(body.group_id,body.user_id)+body.delta<0)throw new Error('可用积分不足，扣减后不能为负数');
        post(body.group_id,body.user_id,body.delta,body.delta>0?'手动加分':'手动扣分',reason);event(body.group_id,'人工调分',`${body.delta>0?'+':''}${body.delta} 积分 · ${reason}`,body.user_id);return {ok:true};
      }
      if(url.pathname==='/api/draws/create'){
        const g=requireGroup(body.group_id),title=String(body.title||'').trim(),prize=String(body.prize||'').trim();
        if(!title||title.length>80||!prize||prize.length>300)throw new Error('请填写活动名称和奖励，名称最多 80 字，奖励最多 300 字');
        integer(body.entry_cost,0,10000,'参与积分');integer(body.winner_count,1,100,'中奖人数');
        const image=body.image||'';
        if(image&&(!/^data:image\/(png|jpeg|webp);base64,[a-zA-Z0-9+/=]+$/.test(image)||image.length>1500000))throw new Error('活动图片请使用不超过 1MB 的 PNG、JPG 或 WebP 文件');
        const draw={id:id(),group_id:g.id,title,prize,entry_cost:body.entry_cost,winner_count:body.winner_count,image,image_english_confirmed:body.image_english_confirmed===true,status:'draft',created:stamp(),entries:[],winners:[],sync_status:'pending_connection'};
        state.draws.unshift(draw);event(g.id,'抽奖创建',title+' · 已保存草稿');return {ok:true,id:draw.id};
      }
      const draw=state.draws.find(d=>d.id===body.id);if(!draw)throw new Error('活动不存在');
      if(url.pathname==='/api/draws/publish'){
        if(draw.status!=='draft')throw new Error('活动已经发布，不能重复发送');
        draw.status='active';draw.published=stamp();event(draw.group_id,'抽奖演示发布',draw.title+' · 已生成群公告及置顶预览，尚未发送真实 TG');return {ok:true};
      }
      if(url.pathname==='/api/draws/enter'){
        if(draw.status!=='active')throw new Error('活动尚未开始或已结束');
        if(draw.entries.some(e=>e.user_id===body.user_id))throw new Error('该用户已参与，每场活动仅可参与一次');
        if(!member(draw.group_id,body.user_id))throw new Error('请选择该群的参与用户');
        if(balance(draw.group_id,body.user_id)<draw.entry_cost)throw new Error('本月可用积分不足，请先签到、邀请或由管理员增加积分');
        post(draw.group_id,body.user_id,-draw.entry_cost,'抽奖参与',draw.title);
        draw.entries.push({user_id:body.user_id,created:stamp(),month:monthNow(),cost:draw.entry_cost});event(draw.group_id,'抽奖参与',draw.title+' · -'+draw.entry_cost+' 积分',body.user_id);return {ok:true};
      }
      if(url.pathname==='/api/draws/finish'){
        if(draw.status!=='active')throw new Error('活动不能重复开奖');
        if(draw.entries.length<draw.winner_count)throw new Error('参与人数少于中奖人数，请等待更多用户参与');
        const pool=[...draw.entries];
        if(!globalThis.crypto?.getRandomValues)throw new Error('当前浏览器不支持安全随机开奖，请使用新版浏览器');
        for(let i=pool.length-1;i>0;i--){const n=i+1,limit=Math.floor(4294967296/n)*n;let r;do{const buffer=new Uint32Array(1);crypto.getRandomValues(buffer);r=buffer[0];}while(r>=limit);const j=r%n;[pool[i],pool[j]]=[pool[j],pool[i]];}
        draw.winners=pool.slice(0,draw.winner_count).map(e=>({...e,...member(draw.group_id,e.user_id)}));draw.status='ended';draw.ended=stamp();event(draw.group_id,'抽奖开奖',draw.title+' · 选出 '+draw.winners.length+' 位中奖用户（演示）');return {ok:true};
      }
      throw new Error('前端演示暂不支持此操作');
    });
  };
  window.previewActions={
    reset(){try{localStorage.removeItem(key);localStorage.removeItem('coinw-ui-preview-v1');}catch{}location.reload();},
    simulate(gid,command){return mutate(()=>{
      const g=requireGroup(gid),uid=999;g.messages++;g.last_activity=stamp();
      if(command==='coinw'){
        const k=g.id+':'+localDay();if(state.checks[k])return {text:'✅ You have already checked in today. Come back tomorrow to earn more points.\n⏰ Next check-in: 00:00 (UTC+8)'};
        state.checks[k]=true;post(g.id,uid,g.checkin_points,'签到奖励','每日签到');g.checkins++;event(g.id,'签到',`+${g.checkin_points} 积分`);
        return {text:`✅ Check-in successful! You earned ${g.checkin_points} points.\n💎 Available points this month: ${balance(g.id,uid)}\nPoints reset on the first day of each month at 00:00 (UTC+8).`};
      }
      if(command==='/ink'){
        let invite=state.invites.find(r=>r.group_id===g.id&&r.user_id===uid&&r.active);
        if(!invite){invite={...member(g.id,uid),id:Date.now(),url:`demo://coinw/invite/${Math.abs(g.id)}/preview_member`,active:1,created:stamp()};state.invites.unshift(invite);event(g.id,'邀请链接','体验用户已生成专属链接');}
        const count=state.joins.filter(j=>j.group_id===g.id&&j.inviter_id===uid&&j.month===monthNow()).length;
        return {text:`🔗 Your personal invite link\n${invite.url}\n\n👥 Successful invites this month: ${count}\n💎 Reward per successful invite: ${g.invite_points} points\nThis is a demo link and cannot be used to join a real group.`};
      }
      if(command==='invite_success'){
        if(!state.invites.some(i=>i.group_id===g.id&&i.user_id===uid&&i.active))throw new Error('请先发送 /ink 获取当前群邀请链接');
        const invitee=500000+state.joins.length;state.joins.push({id:id(),group_id:g.id,inviter_id:uid,invitee_id:invitee,invitee_name:'演示新成员 '+invitee,created:stamp(),month:monthNow(),points:g.invite_points});
        post(g.id,uid,g.invite_points,'邀请奖励','演示成员成功入群');event(g.id,'邀请入群',`新成员通过验证，+${g.invite_points} 积分`);
        return {text:`👥 A new member has joined in this demo.\nYou earned ${g.invite_points} invite points. Your monthly invite report has been updated.`};
      }
      if(command==='https://example.com'){const deleted=Boolean(g.block_links);event(g.id,deleted?'内容拦截':'消息',deleted?'已删除包含链接的演示消息':'链接拦截已关闭');return {text:deleted?'🛡️ An external link was detected. The message has been removed.':'Link blocking is disabled. This message is allowed.'};}
      throw new Error('未知演示指令');
    });},
    csv(gid){const rows=snapshot(gid).ranking;const cell=v=>'"'+String(v).replace(/^[=+@-]/,"'$&").replaceAll('"','""')+'"';return '\ufeff'+[['用户','用户名','社群ID','本月可用积分'],...rows.map(r=>[r.name,r.username,r.group_id,r.monthly])].map(r=>r.map(cell).join(',')).join('\r\n');}
  };
})();
