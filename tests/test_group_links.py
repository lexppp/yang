from pathlib import Path
import tempfile
import threading
import time
import unittest
from app import Store,Bot,AppServer,TelegramError
from group_links import disconnect,resolve
from test_app import FakeTelegram
from test_accounts import Client

class GroupTelegram(FakeTelegram):
    def __init__(self):
        super().__init__();self.migrations={}
    def call(self,method,**params):
        if method=='getChat' and params['chat_id'] in self.migrations:
            self.calls.append((method,params))
            raise TelegramError('getChat: group chat was upgraded',{'migrate_to_chat_id':self.migrations[params['chat_id']]})
        if method=='getMe':
            self.calls.append((method,params));return {'id':9999,'username':'GroupFixtureBot'}
        if method=='getChatMember' and params['user_id']==9999:
            self.calls.append((method,params));return {'status':'administrator'}
        result=super().call(method,**params)
        if method=='createChatInviteLink':return {'invite_link':'https://t.me/+fixture_'+str(len(self.calls))}
        return result


class GroupLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'groups.sqlite3')
        self.old=-12345;self.new=-1001234567890
        self.old_group=self.store.group({'id':self.old,'title':'Old community','type':'group'})
        self.new_group=self.store.group({'id':self.new,'title':'New community','type':'supergroup'})
        self.api=GroupTelegram();self.bot=Bot(self.store,self.api)
    def tearDown(self):self.temp.cleanup()
    def test_migration_merges_history_deduplicates_rewards_and_keeps_new_settings(self):
        for gid in (self.old,self.new):
            self.store.member(gid,{'id':1,'first_name':'Inviter'})
            self.store.member(gid,{'id':2,'first_name':'Member'})
            self.store.checkin(gid,1)
        self.store.referral(self.old,2,1)
        self.store.referral(self.new,2,3)
        with self.store.db() as db:
            db.execute('UPDATE groups SET messages=10 WHERE id=?',(self.old,))
            db.execute("UPDATE groups SET messages=4,welcome='Custom current welcome' WHERE id=?",(self.new,))
            db.execute('INSERT INTO invites(group_id,user_id,url,created) VALUES(?,?,?,?)',(self.old,1,'https://t.me/+old_fixture','2026-01-01'))
        self.store.migrate_group(self.old,self.new);self.store.migrate_group(self.old,self.new)
        state=self.store.snapshot()
        self.assertEqual(len(state['groups']),1);self.assertEqual(state['groups'][0]['messages'],14)
        self.assertEqual(state['groups'][0]['welcome'],'Custom current welcome')
        self.assertEqual(state['groups'][0]['credits'],25);self.assertEqual(state['groups'][0]['invites'],1)
        self.assertEqual(state['invites'][0]['group_id'],self.new)
        self.assertEqual(self.store.group({'id':self.old,'title':'Stale title'})['title'],'New community')
    def test_telegram_migration_events_are_handled_and_idempotent(self):
        self.bot.handle({'message':{'chat':{'id':self.old,'title':'Old community','type':'group'},'migrate_to_chat_id':self.new}})
        self.bot.handle({'message':{'chat':{'id':self.new,'title':'New community','type':'supergroup'},'migrate_from_chat_id':self.old}})
        self.assertEqual(len(self.store.snapshot()['groups']),1)
        with self.store.db() as db:self.assertEqual(resolve(db,self.old),self.new)
    def test_old_link_is_attributed_only_when_telegram_proves_upgrade(self):
        link=self.bot.invite(self.old,{'id':1})
        self.assertIsNone(self.bot.community.inviter(self.new,link))
        self.api.migrations[self.old]=self.new
        self.assertEqual(self.bot.community.inviter(self.new,link),1)
        self.assertEqual(self.store.snapshot()['invites'][0]['group_id'],self.new)
    def test_request_link_captures_source_before_in_group_verification(self):
        link=self.bot.invite(self.new,{'id':1})
        self.bot.join_request({'from':{'id':2,'first_name':'Member'},'invite_link':{'invite_link':link}},self.new_group)
        with self.store.db() as db:row=dict(db.execute('SELECT * FROM member_arrivals WHERE group_id=?',(self.new,)).fetchone())
        self.assertEqual(row['inviter_id'],1);self.assertEqual(row['invite_url'],link)
        self.assertEqual(self.store.snapshot()['ranking'],[])
        self.bot.callback({'id':'cb','from':{'id':2},'data':f"gv:{row['nonce']}:{row['answer']}"})
        self.assertEqual(self.store.snapshot()['groups'][0 if self.store.snapshot()['groups'][0]['id']==self.new else 1]['invites'],1)
        messages=[p for m,p in self.api.calls if m=='sendMessage']
        self.assertTrue(all(p['chat_id']==self.new for p in messages))
    def test_legacy_direct_link_is_preserved_and_new_link_requests_are_automatic(self):
        with self.store.db() as db:db.execute('INSERT INTO invites(group_id,user_id,url,created) VALUES(?,?,?,?)',(self.new,1,'https://t.me/+legacy_fixture','2026-01-01'))
        link=self.bot.invite(self.new,{'id':1})
        self.assertNotEqual(link,'https://t.me/+legacy_fixture')
        self.assertEqual(self.bot.invite(self.new,{'id':1}),link)
        self.assertTrue(next(p for m,p in self.api.calls if m=='createChatInviteLink')['creates_join_request'])
    def test_proven_source_can_be_repaired_after_verification_without_double_award(self):
        link=self.bot.invite(self.old,{'id':1})
        self.bot.community.arrive({**self.new_group,'verify':0},{'id':2,'first_name':'Member'},invite_url=link)
        self.api.migrations[self.old]=self.new
        self.bot.community.repair_referrals();self.bot.community.repair_referrals()
        self.assertEqual(self.store.snapshot()['ranking'][0]['total'],20)
        self.assertTrue(self.store.snapshot()['joins'][0]['counted'])
    def test_manual_attribution_is_explicit_audited_and_cannot_double_credit(self):
        self.bot.invite(self.new,{'id':1})
        invite=next(i for i in self.store.snapshot()['invites'] if i['user_id']==1)
        self.bot.community.arrive({**self.new_group,'verify':0},{'id':2,'first_name':'Member'})
        body={'group_id':self.new,'user_id':2,'invite_id':invite['id'],'reason':'Confirmed with both members'}
        self.assertTrue(self.bot.community.manual_attribution(body,{'username':'owner'})['credited'])
        self.assertRaises(ValueError,self.bot.community.manual_attribution,body,{'username':'owner'})
        self.assertEqual(self.store.snapshot()['ranking'][0]['total'],20)
        self.assertTrue(any(l['kind']=='人工补记邀请' and 'owner' in l['detail'] for l in self.store.snapshot()['logs']))
    def test_disconnect_is_persistent_ignores_updates_and_pauses_plans(self):
        self.bot.community.save_announcement({'group_id':self.new,'title':'Later','body':'Hello community','mode':'daily','times':['09:00'],'start_date':'2030-01-01','activate':True},{'username':'owner'})
        self.store.checkin(self.new,1);disconnect(self.store,self.new)
        self.api.calls.clear()
        self.bot.handle({'message':{'chat':{'id':self.new,'title':'New community','type':'supergroup'},'from':{'id':2},'text':'coinw','message_id':1}})
        self.bot.handle({'my_chat_member':{'chat':{'id':self.new,'title':'New community','type':'supergroup'},'new_chat_member':{'status':'administrator'}}})
        self.assertEqual(self.api.calls,[])
        self.assertNotIn(self.new,[g['id'] for g in self.store.snapshot()['groups']])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT SUM(points) FROM ledger WHERE group_id=?',(self.new,)).fetchone()[0],5)
            self.assertEqual(db.execute('SELECT status FROM announcement_plans').fetchone()[0],'paused')
            self.assertEqual(db.execute('SELECT disconnected FROM groups WHERE id=?',(self.new,)).fetchone()[0],1)


class GroupLinkHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'http.sqlite3')
        self.gid=-1001234567890;self.store.group({'id':self.gid,'title':'Test community','type':'supergroup'})
        self.api=GroupTelegram();self.bot=Bot(self.store,self.api)
        self.server=AppServer(('127.0.0.1',0),self.store,'temporary-test-password',self.bot,False)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.client=Client(self.base);self.assertEqual(self.client.login(password='temporary-test-password')[0],200)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.worker.join();self.temp.cleanup()
    def test_disconnect_restore_and_optional_leave(self):
        self.assertEqual(self.client.request('/api/groups/disconnect',{'group_id':self.gid})[0],200)
        self.assertFalse(any(m=='leaveChat' for m,_ in self.api.calls))
        self.assertEqual(self.client.request('/api/groups/disconnected')[1]['groups'][0]['id'],self.gid)
        self.assertEqual(self.client.request('/api/groups/restore',{'group_id':self.gid})[0],200)
        self.assertEqual(len(self.client.request('/api/snapshot')[1]['groups']),1)
        self.assertEqual(self.client.request('/api/groups/disconnect',{'group_id':self.gid,'leave':True})[0],200)
        self.assertTrue(any(m=='leaveChat' for m,_ in self.api.calls))
    def test_settings_admin_does_not_implicitly_get_disconnect_or_manual_rewards(self):
        self.client.request('/api/admins/create',{'username':'settings_admin','name':'Operator','password':'ordinary-test-password','permissions':['groups.manage','invites.manage']})
        operator=Client(self.base);self.assertEqual(operator.login('settings_admin','ordinary-test-password')[0],200)
        self.assertEqual(operator.request('/api/groups/disconnect',{'group_id':self.gid})[0],403)
        self.assertEqual(operator.request('/api/groups/disconnected')[0],403)
        self.assertEqual(operator.request('/api/invites/attribute',{})[0],403)
    def test_leave_option_type_is_validated_before_any_telegram_action(self):
        self.assertEqual(self.client.request('/api/groups/disconnect',{'group_id':self.gid,'leave':'yes'})[0],400)
        self.assertFalse(any(m=='leaveChat' for m,_ in self.api.calls))
        self.assertEqual(len(self.client.request('/api/snapshot')[1]['groups']),1)
