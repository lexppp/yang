/* Shared UI permission names; authorization is enforced independently by the server. */
window.ACCESS_POLICY={
 'dashboard.view':'数据总览',
 'groups.view':'查看社群','groups.manage':'修改社群设置','groups.disconnect':'解除与恢复社群连接',
 'points.view':'查看积分与历史','points.adjust':'手动增减积分','points.configure':'修改奖励规则','points.export':'导出积分',
 'invites.view':'查看邀请','invites.manage':'撤销邀请链接','invites.export':'导出邀请 Excel','invites.attribute':'补记邀请（含积分奖励）',
 'draws.view':'查看抽奖','draws.manage':'管理与发布抽奖',
 'announcements.view':'查看公告','announcements.manage':'编辑与发布公告',
 'logs.view':'查看活动日志'
};
window.ACCESS_PAGES={overview:'dashboard.view',groups:'groups.view',ranking:'points.view',points:'points.configure',invites:'invites.view',draws:'draws.view',announcements:'announcements.view',logs:'logs.view',accounts:'admins.manage'};
window.accessAllowed=(user,p)=>!!user&&(user.role==='superadmin'||(user.permissions||[]).includes(p));
window.accessRequirements=(path,body={})=>{
 if(['/api/groups/disconnect','/api/groups/restore','/api/groups/disconnected'].includes(path))return ['groups.disconnect'];
 if(path==='/api/invites/attribute')return ['invites.attribute'];
 if(path==='/api/settings')return [...(['welcome','welcome_photo','block_links','block_photos','block_ads','keywords','verify','verify_minutes','welcome_delete_minutes','link_delete_minutes','language'].some(k=>k in body)?['groups.manage']:[]),...(['checkin_points','invite_points'].some(k=>k in body)?['points.configure']:[])];
 if(path==='/api/export')return ['points.export'];if(path==='/api/revoke')return ['invites.manage'];
 if(path.startsWith('/api/points/'))return ['points.adjust'];if(path.startsWith('/api/draws/'))return ['draws.manage'];if(path.startsWith('/api/announcements/'))return ['announcements.manage'];
 if(path.startsWith('/api/admin'))return ['admins.manage'];return [];
};
