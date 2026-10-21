/* Hosted demonstration: real server accounts; explicitly simulated local business data. */
(() => {
 const localBusiness=window.previewApi;let token='';
 const remoteRoutes=new Set(['/api/login','/api/logout','/api/session','/api/auth-status','/api/admins','/api/admin-audit','/api/admins/create','/api/admins/update','/api/admins/reset-password','/api/account/password']);
 async function request(path,body){
  const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(body)});
  let result;try{result=await response.json();}catch{throw new Error('服务器返回异常，请稍后再试');}
  if(!response.ok){if(response.status===401&&token){token='';if(typeof showLogin==='function')showLogin();}const e=new Error(result.error||'请求失败');e.status=response.status;throw e;}
  if(result.csrf)token=result.csrf;if(path==='/api/logout')token='';return result;
 }
 function filter(data,user){
  const value={...data,current_user:user,bot_status:'部署演示站 · TG 未关联'};
  for(const [key,permission] of Object.entries({ranking:'points.view',ledger:'points.view',invites:'invites.view',joins:'invites.view',logs:'logs.view',draws:'draws.view',announcements:'announcements.view',announcement_deliveries:'announcements.view',forum_topics:'announcements.view'}))if(!accessAllowed(user,permission))value[key]=[];
  if(!['groups.view','points.view','invites.view','draws.view'].some(p=>accessAllowed(user,p)))value.members=[];
  value.groups=value.groups.map(g=>{const result={...g};if(!accessAllowed(user,'groups.view'))for(const key of ['welcome','welcome_photo','block_links','block_photos','block_ads','keywords','verify','language'])delete result[key];if(!accessAllowed(user,'points.view')){delete result.checkin_points;delete result.invite_points;}return result;});
  return value;
 }
 window.previewApi=async(path,body)=>{
  const route=new URL(path,location.origin).pathname;
  if(remoteRoutes.has(route))return request(path,body);
  if(route==='/api/setup'){const error=new Error('服务器超级管理员由部署环境创建，请使用部署时配置的账号登录');error.status=403;throw error;}
  const {user}=await request('/api/session');
  if(accessRequirements(route,body).some(permission=>!accessAllowed(user,permission))){const e=new Error('无权执行此操作');e.status=403;throw e;}
  const result=await localBusiness(path,body);return route==='/api/snapshot'?filter(result,user):result;
 };
})();
