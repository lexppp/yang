/* Local scheduling simulation. Actual unattended delivery requires a server-side worker. */
(() => {
 const key='coinw-announcements-v1',TZ=28800000,MAX_AGE=60000;
 const id=()=>crypto.randomUUID?.()||Date.now()+'-'+Math.random().toString(36).slice(2);
 const localDate=ms=>new Date(ms+TZ).toISOString().slice(0,10);
 const parse=(day,time)=>new Date(day+'T'+time+':00+08:00').getTime();
 const today=()=>localDate(Date.now());
 let state;try{state=JSON.parse(localStorage.getItem(key));}catch{}
 if(!state||state.version!==1)state={version:1,plans:[],deliveries:[]};
 const originalApi=window.previewApi;
 let groups=[];
 function save(){try{localStorage.setItem(key,JSON.stringify(state));}catch(e){if(e.name==='QuotaExceededError')throw new Error('浏览器空间不足，请减小图片后再试。');}}
 function change(fn){const before=JSON.stringify(state);try{const result=fn();save();return result;}catch(e){state=JSON.parse(before);throw e;}}
 function topics(gid){const index=groups.findIndex(g=>g.id===Number(gid));return index>=0&&index<2?[{id:1,name:'General'},{id:index===0?12:19,name:'Announcements'},{id:index===0?36:28,name:'Community Events'}]:[];}
 function next(plan,after){
  if(plan.mode!=='daily')return new Date(plan.once_at).getTime()>after?plan.once_at:null;
  let day=localDate(after);if(day<plan.start_date)day=plan.start_date;
  for(let n=0;n<3;n++){
   if(plan.end_date&&day>plan.end_date)return null;
   for(const time of plan.times){const candidate=parse(day,time);if(candidate>after)return new Date(candidate).toISOString();}
   day=localDate(parse(day,'00:00')+86400000);
  }return null;
 }
 function englishPayload(plan){
  telegramEnglish.require(plan.body,'公告正文');
  if(plan.image&&!plan.image_english_confirmed)throw new Error('请确认图片内无中文，且可见文字为英文。');
  const maximum=plan.image?1024:4096;
  if(!plan.body.trim()||plan.body.length>maximum)throw new Error('公告正文需为 1–'+maximum+' 个字符');
  return {chat_id:plan.group_id,message_thread_id:plan.topic_id||undefined,text:plan.body,pin:plan.pin,has_image:!!plan.image};
 }
 function validate(body,activate){
  const g=groups.find(g=>g.id===Number(body.group_id));if(!g)throw new Error('请选择社群');
  const mode=body.mode;if(!['now','once','daily'].includes(mode))throw new Error('请选择发布方式');
  const title=String(body.title||'').trim();if(!title||title.length>100)throw new Error('请输入 1–100 字内部公告名称');
  const topic_id=body.topic_id===null?null:Number(body.topic_id);
  if(topic_id!==null&&!topics(g.id).some(t=>t.id===topic_id))throw new Error('话题不属于所选社群，请重新选择');
  const image=body.image||'';if(image&&(!/^data:image\/(png|jpeg|webp);base64,[a-zA-Z0-9+/=]+$/.test(image)||image.length>1500000))throw new Error('图片请选择 1MB 内的 PNG/JPG/WebP');
  const plan={title,group_id:g.id,topic_id,body:String(body.body||''),image,image_english_confirmed:body.image_english_confirmed===true,pin:body.pin===true,mode,times:[],start_date:'',end_date:'',once_at:null};
  englishPayload(plan);
  if(mode==='once'){
   const time=new Date(String(body.once_local||'')+':00+08:00');if(!Number.isFinite(time.getTime()))throw new Error('请选择有效的定时发布时间');
   if(activate&&time.getTime()<=Date.now())throw new Error('定时发布时间必须晚于当前时间（UTC+8）');plan.once_at=time.toISOString();
  }
  if(mode==='now')plan.once_at=new Date(Date.now()+1).toISOString();
  if(mode==='daily'){
   if(!Array.isArray(body.times)||body.times.length<1||body.times.length>24||!body.times.every(t=>/^([01]\d|2[0-3]):[0-5]\d$/.test(t)))throw new Error('每天可发布 1–24 次，请填写每次的有效时间');
   if(new Set(body.times).size!==body.times.length)throw new Error('同一天的发布时间不能重复');
   const validDate=d=>/^\d{4}-\d{2}-\d{2}$/.test(d)&&Number.isFinite(parse(d,'00:00'))&&localDate(parse(d,'00:00'))===d;
   if(!validDate(body.start_date)||body.end_date&&!validDate(body.end_date))throw new Error('请选择有效起止日期');
   if(body.end_date&&body.end_date<body.start_date)throw new Error('结束日期不能早于开始日期');
   plan.times=[...body.times].sort();plan.start_date=body.start_date;plan.end_date=body.end_date||'';
   if(activate&&!next(plan,Date.now()))throw new Error('此时间范围已无未来的发布时段');
  }
  return plan;
 }
 function run(at=Date.now()){
  let changed=false;
  for(const plan of state.plans){
   if(plan.status!=='scheduled'||!plan.next_run||new Date(plan.next_run).getTime()>at)continue;
   const due=plan.next_run,slot=plan.id+':'+due;
   const existing=state.deliveries.some(d=>d.slot===slot);
   if(!existing){
    let status=at-new Date(due).getTime()<=MAX_AGE?'simulated':'missed',error='';
    try{englishPayload(plan);}catch(e){status='blocked';error=e.message;}
    state.deliveries.unshift({id:id(),slot,plan_id:plan.id,group_id:plan.group_id,topic_id:plan.topic_id,title:plan.title,body:plan.body,pin:plan.pin,scheduled_at:due,processed_at:new Date(at).toISOString(),status,error});
   }
   // Do not send a backlog when the page is reopened. Advance straight to the next future slot.
   plan.next_run=next(plan,at);if(!plan.next_run)plan.status='completed';changed=true;
  }
  if(changed){state.deliveries=state.deliveries.slice(0,500);save();}return changed;
 }
 window.previewApi=async(path,body)=>{
  const url=new URL(path,'https://preview.invalid');
  if(url.pathname==='/api/snapshot'){
   const original=await originalApi(path,body);groups=original.groups;run();
   const gid=Number(url.searchParams.get('group'))||null;
   return {...original,announcements:JSON.parse(JSON.stringify(state.plans.filter(p=>!gid||p.group_id===gid))),announcement_deliveries:JSON.parse(JSON.stringify(state.deliveries.filter(d=>!gid||d.group_id===gid))),forum_topics:groups.map(g=>({group_id:g.id,topics:topics(g.id)}))};
  }
  if(!url.pathname.startsWith('/api/announcements/')){
   // Enforce the English boundary for existing outgoing templates and new draw copy.
   if(url.pathname==='/api/settings'&&'welcome' in body)telegramEnglish.require(body.welcome,'欢迎词');
   if(url.pathname==='/api/draws/create'){telegramEnglish.require(body.title,'抽奖活动名称');telegramEnglish.require(body.prize,'抽奖奖励内容');if(body.image&&!body.image_english_confirmed)throw new Error('请确认抽奖配图不含中文。');}
   if(url.pathname==='/api/draws/publish'){const current=await originalApi('/api/snapshot');const d=current.draws.find(d=>d.id===body.id);if(d){telegramEnglish.require(d.title,'原活动名称');telegramEnglish.require(d.prize,'原活动奖励');if(d.image&&!d.image_english_confirmed)throw new Error('请重新创建并确认原活动图片不含中文。');}}
   return originalApi(path,body);
  }
  return change(()=>{
   if(url.pathname==='/api/announcements/save'){
    const active=body.activate===true,content=validate(body,active);let plan=body.id?state.plans.find(p=>p.id===body.id):null;
    if(body.id&&!plan)throw new Error('公告任务不存在');if(plan&&plan.status==='completed')throw new Error('已完成任务不可修改，可新建任务');
    const base=plan||{id:id(),created:new Date().toISOString()};plan={...base,...content,status:active?'scheduled':'draft',next_run:active?next(content,Date.now()):null};
    if(body.id)state.plans[state.plans.findIndex(p=>p.id===body.id)]=plan;else state.plans.unshift(plan);
    if(active&&plan.mode==='now'){plan.next_run=plan.once_at;run(new Date(plan.once_at).getTime());}
    return {ok:true,id:plan.id};
   }
   const plan=state.plans.find(p=>p.id===body.id);if(!plan)throw new Error('公告任务不存在');
   if(url.pathname==='/api/announcements/pause'){if(plan.status!=='scheduled')throw new Error('仅可暂停运行中的任务');plan.status='paused';plan.next_run=null;return {ok:true};}
   if(url.pathname==='/api/announcements/resume'){
    if(!['paused','draft'].includes(plan.status))throw new Error('当前任务不能启动');englishPayload(plan);
    if(plan.mode==='now')plan.once_at=new Date(Date.now()+1).toISOString();
    plan.next_run=next(plan,Date.now());if(!plan.next_run)throw new Error('原发布时间已过，请编辑为未来时间后再启用');plan.status='scheduled';
    if(plan.mode==='now')run(new Date(plan.once_at).getTime());return {ok:true};
   }
   if(url.pathname==='/api/announcements/cancel'){if(plan.status==='completed')throw new Error('此任务已完成');plan.status='cancelled';plan.next_run=null;return {ok:true};}
   throw new Error('不支持此公告操作');
  });
 };
 window.announcementPreview={run:()=>state.plans.some(p=>p.status==='scheduled'&&p.next_run&&new Date(p.next_run).getTime()<=Date.now())?change(()=>run()):false,topics,today,reset(){try{localStorage.removeItem(key);}catch{}}};
})();
