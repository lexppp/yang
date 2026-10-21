import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from app import Store,Bot,AppServer,seed_demo
from auth import Accounts

ROOT_PASSWORD='temporary-server-test-password'
ADMIN_PASSWORD='temporary-operator-test-password'
class Client:
    def __init__(self,base):
        self.base=base;self.csrf=''
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(self,path,body=None,csrf=True):
        headers={}
        if body is not None:headers={'Content-Type':'application/json','X-CSRF-Token':self.csrf if csrf else ''}
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            r=self.opener.open(req)
        except urllib.error.HTTPError as e:r=e
        with r:
            raw=r.read()
            return r.status,json.loads(raw) if 'application/json' in r.headers.get('Content-Type','') else raw
    def login(self,username='admin',password=ROOT_PASSWORD):
        code,data=self.request('/api/login',{'username':username,'password':password})
        if code==200:self.csrf=data['csrf']
        return code,data

class AccountHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'auth.sqlite3');seed_demo(self.store)
        self.server=AppServer(('127.0.0.1',0),self.store,ROOT_PASSWORD,Bot(self.store,None),True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.root=Client(self.base);self.assertEqual(self.root.login()[0],200)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def make_admin(self,permissions=None,username='operator'):
        code,data=self.root.request('/api/admins/create',{'username':username,'name':'社群运营','password':ADMIN_PASSWORD,'permissions':permissions or []})
        self.assertEqual(code,200,data)
        client=Client(self.base);self.assertEqual(client.login(username,ADMIN_PASSWORD)[0],200)
        return data['user'],client
    def test_login_requires_username_and_credentials_are_not_exposed(self):
        anon=Client(self.base)
        self.assertEqual(anon.request('/api/login',{'password':ROOT_PASSWORD})[0],401)
        user,client=self.make_admin(['groups.view'])
        code,data=self.root.request('/api/admins');self.assertEqual(code,200)
        serialized=json.dumps(data)
        for value in [ROOT_PASSWORD,ADMIN_PASSWORD,'password_hash','salt']:
            self.assertNotIn(value,serialized)
        self.assertEqual(client.request('/api/admins')[0],403)
        self.assertEqual(client.request('/api/admin-audit')[0],403)
    def test_read_only_admin_cannot_mutate_or_escalate(self):
        user,client=self.make_admin(['groups.view','invites.view'])
        self.assertEqual(client.request('/api/settings',{'group_id':-10010001,'welcome':'Welcome!'})[0],403)
        self.assertEqual(client.request('/api/revoke',{'id':1})[0],403)
        self.assertEqual(client.request('/api/points/adjust',{'group_id':-10010001,'delta':999})[0],403)
        self.assertEqual(client.request('/api/draws/create',{})[0],403)
        self.assertEqual(client.request('/api/announcements/save',{})[0],403)
        self.assertEqual(client.request('/api/admins/create',{'username':'new_root','role':'superadmin','password':ADMIN_PASSWORD})[0],403)
        self.assertEqual(client.request('/api/admins/update',{'id':user['id'],'permissions':['admins.manage']})[0],403)
        snapshot=client.request('/api/snapshot')[1]
        self.assertEqual(snapshot['ranking'],[]);self.assertEqual(snapshot['logs'],[])
        self.assertTrue(snapshot['invites'])
    def test_reward_permission_does_not_grant_group_settings(self):
        user,client=self.make_admin(['points.configure'])
        self.assertIn('points.view',user['permissions'])
        self.assertEqual(client.request('/api/settings',{'group_id':-10010001,'checkin_points':12})[0],200)
        self.assertEqual(client.request('/api/settings',{'group_id':-10010001,'checkin_points':99,'welcome':'Forged change'})[0],403)
        snap=client.request('/api/snapshot')[1]
        g=next(g for g in snap['groups'] if g['id']==-10010001)
        self.assertEqual(g['checkin_points'],12);self.assertNotIn('welcome',g)
        self.assertEqual(snap['invites'],[])
        self.assertEqual(client.request('/api/export')[0],403)
    def test_permissions_update_revokes_existing_session(self):
        user,client=self.make_admin(['groups.view'])
        self.assertEqual(self.root.request('/api/admins/update',{'id':user['id'],'permissions':['points.view']})[0],200)
        self.assertEqual(client.request('/api/snapshot')[0],401)
        code,data=client.login('operator',ADMIN_PASSWORD);self.assertEqual(code,200)
        self.assertEqual(data['user']['permissions'],['points.view'])
    def test_disable_and_enable_revoke_sessions(self):
        user,client=self.make_admin(['dashboard.view'])
        self.assertEqual(self.root.request('/api/admins/update',{'id':user['id'],'active':False})[0],200)
        self.assertEqual(client.request('/api/session')[0],401)
        self.assertEqual(client.login('operator',ADMIN_PASSWORD)[0],401)
        self.assertEqual(self.root.request('/api/admins/update',{'id':user['id'],'active':True})[0],200)
        self.assertEqual(client.request('/api/session')[0],401)
        self.assertEqual(client.login('operator',ADMIN_PASSWORD)[0],200)
    def test_reset_password_invalidates_old_password_and_session(self):
        user,client=self.make_admin(['groups.view']);new='a-new-operator-password-2026'
        self.assertEqual(self.root.request('/api/admins/reset-password',{'id':user['id'],'password':new})[0],200)
        self.assertEqual(client.request('/api/session')[0],401)
        self.assertEqual(client.login('operator',ADMIN_PASSWORD)[0],401)
        self.assertEqual(client.login('operator',new)[0],200)
        audit=self.root.request('/api/admin-audit')[1]['audit']
        self.assertIn('重置管理员密码',[r['action'] for r in audit]);self.assertNotIn(new,json.dumps(audit))
    def test_superadmin_is_protected_and_cannot_be_delegated(self):
        self.assertEqual(self.root.request('/api/admins/update',{'id':1,'active':False})[0],403)
        self.assertEqual(self.root.request('/api/admins/reset-password',{'id':1,'password':ADMIN_PASSWORD})[0],403)
        self.assertEqual(self.root.request('/api/admins/create',{'username':'secondroot','role':'superadmin','password':ADMIN_PASSWORD})[0],403)
        self.assertEqual(self.root.request('/api/admins/create',{'username':'escalate','password':ADMIN_PASSWORD,'permissions':['admins.manage']})[0],400)
        self.assertEqual(len(self.root.request('/api/admins')[1]['admins']),1)
    def test_change_own_password_and_persist_bootstrap(self):
        new='a-new-root-password-for-test'
        self.assertEqual(self.root.request('/api/account/password',{'current_password':'wrong-password','password':new})[0],403)
        self.assertEqual(self.root.request('/api/account/password',{'current_password':ROOT_PASSWORD,'password':new})[0],200)
        self.assertEqual(self.root.request('/api/session')[0],401)
        self.assertEqual(self.root.login(password=ROOT_PASSWORD)[0],401)
        self.assertEqual(self.root.login(password=new)[0],200)
        reopened=Accounts(self.store,'a-different-bootstrap-password')
        self.assertIsNotNone(reopened.authenticate('admin',new))
        self.assertIsNone(reopened.authenticate('admin',ROOT_PASSWORD))
    def test_empty_permissions_csrf_and_export_gate(self):
        user,client=self.make_admin([])
        self.assertEqual(client.request('/api/snapshot')[1]['ranking'],[])
        self.assertEqual(self.root.request('/api/admins/update',{'id':user['id'],'permissions':['points.export']},csrf=False)[0],403)
        self.assertEqual(self.root.request('/api/admins/update',{'id':user['id'],'permissions':['points.export']})[0],200)
        client.login('operator',ADMIN_PASSWORD)
        self.assertEqual(client.request('/api/export')[0],200)
    def test_duplicate_username_and_short_password_are_rejected(self):
        self.make_admin(['groups.view'])
        self.assertEqual(self.root.request('/api/admins/create',{'username':'OPERATOR','password':ADMIN_PASSWORD})[0],400)
        self.assertEqual(self.root.request('/api/admins/create',{'username':'shortpass','password':'123'})[0],400)
        self.assertEqual(len(self.root.request('/api/admins')[1]['admins']),2)

if __name__=='__main__':unittest.main()
