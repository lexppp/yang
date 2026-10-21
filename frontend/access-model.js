/* Offline account/role demonstration. Real site security is enforced by auth.py, not localStorage. */
(() => {
 const KEY='coinw-preview-accounts-v1',SESSION='coinw-preview-session-v1';
 const baseApi=window.previewApi;
 const local=previewStorage.local,tab=previewStorage.session;
 const fail=(message,status=400)=>{const e=new Error(message);e.status=status;throw e;};
 const hex=bytes=>[...bytes].map(b=>b.toString(16).padStart(2,'0')).join('');
 const unhex=value=>new Uint8Array(value.match(/../g).map(s=>parseInt(s,16)));
 const fresh=()=>({version:1,users:[],audit:[]});
 function read(){try{return JSON.parse(local.getItem(KEY))||fresh();}catch{return fresh();}}
 function save(state){try{local.setItem(KEY,JSON.stringify(state));}catch{fail('浏览器禁止保存本地账号，请更换浏览器或使用真实服务器版本。');}}
 const clean=user=>({id:user.id,username:user.username,name:user.name,role:user.role,active:user.active,version:user.version,created:user.created,permissions:user.role==='superadmin'?Object.keys(ACCESS_POLICY):[...user.permissions]});
 const username=value=>{if(typeof value!=='string'||! /^[a-zA-Z][a-zA-Z0-9_.-]{2,31}$/.test(value))fail('用户名须为 3–32 位英文字符，以字母开头');return value.toLowerCase();};
 const passwordValid=value=>{if(typeof value!=='string'||value.length<12||value.length>256)fail('密码长度须为 12–256 位');};
 async function hash(password,salt){return hex(await previewCrypto.derive(password,unhex(salt)));}
 async function digest(password){passwordValid(password);const salt=hex(previewCrypto.randomBytes(16));return {salt,password_hash:await hash(password,salt)};}
 function session(){let item;try{item=JSON.parse(tab.getItem(SESSION));}catch{}const user=read().users.find(u=>u.id===item?.user_id);if(!item||!user||!user.active||item.expires<=Date.now()||user.version!==item.version)fail('请重新登录，账号状态或权限可能已更新',401);return {user:clean(user),session:item};}
 function makeSession(user){const item={user_id:user.id,version:user.version,expires:Date.now()+43200000,csrf:hex(previewCrypto.randomBytes(24))};tab.setItem(SESSION,JSON.stringify(item));return {user:clean(user),csrf:item.csrf,demo:true};}
 const permissions=values=>{if(!Array.isArray(values)||values.some(p=>!(p in ACCESS_POLICY)))fail('权限配置无效，不能授予超级管理员权限');const result=new Set(values);values.filter(p=>!p.endsWith('.view')).forEach(p=>result.add(p.split('.')[0]+'.view'));return [...result].sort();};
 const audit=(state,actor,action,target,detail)=>{state.audit.unshift({id:previewCrypto.uuid(),actor:actor.username,actor_id:actor.id,action,target_id:target.id,target:target.username,detail,created:new Date().toISOString()});state.audit=state.audit.slice(0,100);};
 function redact(data,user){
  const value={...data,current_user:user};
  for(const [field,p] of Object.entries({ranking:'points.view',ledger:'points.view',invites:'invites.view',joins:'invites.view',draws:'draws.view',announcements:'announcements.view',announcement_deliveries:'announcements.view',forum_topics:'announcements.view',logs:'logs.view'}))if(!accessAllowed(user,p))value[field]=[];
  if(!['groups.view','points.view','invites.view','draws.view'].some(p=>accessAllowed(user,p)))value.members=[];
  value.groups=value.groups.map(g=>{const copy={...g};if(!accessAllowed(user,'groups.view'))for(const k of ['welcome','welcome_photo','block_links','block_photos','block_ads','keywords','verify','language'])delete copy[k];if(!accessAllowed(user,'points.view')){delete copy.checkin_points;delete copy.invite_points;}return copy;});return value;
 }
 window.previewApi=async(path,body)=>{
  const route=new URL(path,'https://preview.invalid').pathname;
  if(route==='/api/auth-status')return {needs_setup:!read().users.some(u=>u.role==='superadmin'),offline_preview:true,account_storage:local.persistent?'persistent':'temporary',session_storage:tab.persistent?'persistent':'temporary'};
  if(route==='/api/setup'){
   const state=read();if(state.users.length)fail('超级管理员已创建，请直接登录',403);if(body.password!==body.confirm_password)fail('两次输入的密码不一致');
   const user={id:1,username:username(body.username),name:'超级管理员',role:'superadmin',active:true,version:1,permissions:Object.keys(ACCESS_POLICY),created:new Date().toISOString(),...await digest(body.password)};state.users.push(user);save(state);return makeSession(user);
  }
  if(route==='/api/login'){
   const attempts=JSON.parse(tab.getItem('coinw-login-attempts')||'[]').filter(t=>t>Date.now()-300000);if(attempts.length>=8)fail('尝试过多，请 5 分钟后重试',429);
   const user=read().users.find(u=>u.username===String(body.username).toLowerCase());
   const actual=await hash(String(body.password||''),user?.salt||'00000000000000000000000000000000');
   if(!user||!user.active||actual!==user.password_hash){tab.setItem('coinw-login-attempts',JSON.stringify([...attempts,Date.now()]));fail('用户名或密码错误，或账号已停用',401);}
   tab.removeItem('coinw-login-attempts');return makeSession(user);
  }
  if(route==='/api/logout'){tab.removeItem(SESSION);return {ok:true};}
  const {user,session:item}=session();
  if(route==='/api/session')return {user,csrf:item.csrf,demo:true};
  if(accessRequirements(route,body).some(p=>!accessAllowed(user,p)))fail('无权执行此操作，请联系超级管理员授权',403);
  if(route==='/api/admins')return {admins:read().users.map(clean),permissions:ACCESS_POLICY};
  if(route==='/api/admin-audit')return {audit:read().audit};
  if(route==='/api/admins/create'){
   if(body.role&&body.role!=='admin')fail('只能创建普通管理员',403);
   const state=read(),name=username(body.username);if(state.users.some(u=>u.username===name))fail('该用户名已存在');
   const display=String(body.name||name).trim();if(!display||display.length>60)fail('显示名称须为 1–60 字');
   const row={id:Math.max(...state.users.map(u=>u.id))+1,username:name,name:display,role:'admin',active:true,version:1,permissions:permissions(body.permissions||[]),created:new Date().toISOString(),...await digest(body.password)};
   state.users.push(row);audit(state,user,'创建管理员',row,'权限：'+row.permissions.join(', '));save(state);return {ok:true,user:clean(row)};
  }
  if(['/api/admins/update','/api/admins/reset-password','/api/account/password'].includes(route)){
   const state=read(),target=state.users.find(u=>u.id===(route==='/api/account/password'?user.id:Number(body.id)));if(!target)fail('账号不存在');
   if(route!=='/api/account/password'&&target.role==='superadmin')fail('超级管理员不能通过普通管理员入口修改',403);
   if(body.role&&body.role!=='admin')fail('不能提升为超级管理员',403);
   if(route==='/api/admins/update'){
    if('permissions' in body)target.permissions=permissions(body.permissions);
    if('active' in body){if(typeof body.active!=='boolean')fail('账号状态无效');target.active=body.active;}
    if('name' in body){if(!String(body.name).trim()||String(body.name).length>60)fail('显示名称无效');target.name=String(body.name).trim();}
    target.version++;audit(state,user,'更新管理员',target,(target.active?'启用':'停用')+'；权限：'+target.permissions.join(', '));
   }else{
    if(route==='/api/account/password'&&await hash(String(body.current_password||''),target.salt)!==target.password_hash)fail('当前密码不正确',403);
    Object.assign(target,await digest(body.password));target.version++;audit(state,user,route==='/api/account/password'?'修改本人密码':'重置管理员密码',target,'旧会话已失效；不记录密码');
   }
   save(state);return {ok:true,reauth_required:route==='/api/account/password'};
  }
  return route==='/api/snapshot'?redact(await baseApi(path,body),user):baseApi(path,body);
 };
 const actions=window.previewActions;
 const oldReset=actions.reset;actions.reset=()=>{if(session().user.role!=='superadmin')fail('仅超级管理员可重置演示业务数据',403);oldReset();};
 const simulate=actions.simulate;actions.simulate=(gid,command)=>{const p=command==='coinw'?'points.adjust':command==='/ink'||command==='invite_success'?'invites.manage':'groups.manage';if(!accessAllowed(session().user,p))fail('当前账号无权操作此演示功能',403);return simulate(gid,command);};
 const csv=actions.csv;actions.csv=gid=>{if(!accessAllowed(session().user,'points.export'))fail('没有导出权限',403);return csv(gid);};
})();
