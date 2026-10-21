from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import time
import unittest
from app import Store, Bot, AppServer, TZ
from test_app import FakeTelegram
from test_accounts import Client


class CommunityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'community.sqlite3')
        self.group=self.store.group({'id':-1001,'title':'Community'})
        self.api=FakeTelegram();self.bot=Bot(self.store,self.api)
        self.user={'id':2,'first_name':'Member'}
    def tearDown(self):
        self.temp.cleanup()
    def arrival(self):
        self.bot.community.arrive({**self.group,'verify_minutes':1},self.user,inviter=1)
        with self.store.db() as db:
            return dict(db.execute('SELECT * FROM member_arrivals').fetchone())
    def plan(self,**changes):
        return dict(title='Announcement',group_id=-1001,body='Hello CoinW community!',image='',pin=False,mode='now',activate=True,**changes)
    def test_service_message_and_membership_event_deduplicate_but_rejoin_welcomes(self):
        with self.store.db() as db:db.execute('UPDATE groups SET verify=0')
        chat={'id':-1001,'type':'supergroup','title':'Community'}
        self.bot.handle({'message':{'chat':chat,'date':1000,'message_id':33,'new_chat_members':[self.user]}})
        self.bot.handle({'chat_member':{'chat':chat,'date':1000,'old_chat_member':{'status':'left'},'new_chat_member':{'status':'member','user':self.user}}})
        self.assertEqual(sum(m=='sendMessage' for m,_ in self.api.calls),1)
        self.bot.handle({'message':{'chat':chat,'message_id':34,'left_chat_member':self.user}})
        self.bot.handle({'message':{'chat':chat,'date':1002,'message_id':35,'new_chat_members':[self.user]}})
        self.assertEqual(sum(m=='sendMessage' for m,_ in self.api.calls),2)
    def test_bad_welcome_photo_falls_back_to_text_and_still_verifies(self):
        self.group['welcome_photo']='invalid_photo_fixture';self.api.fail='sendPhoto'
        row=self.arrival()
        self.assertEqual(row['status'],'pending')
        texts=[p['text'] for m,p in self.api.calls if m=='sendMessage']
        self.assertTrue(any('Welcome' in text for text in texts))
        self.assertTrue(any('please verify' in text for text in texts))
        self.assertTrue(all(p['chat_id']==-1001 for m,p in self.api.calls if m=='sendMessage'))
    def test_timeout_survives_restart_deletes_prompt_and_keeps_mute_without_kicking(self):
        row=self.arrival();self.assertLessEqual(row['expires']-time.time(),60)
        with self.store.db() as db:db.execute('UPDATE member_arrivals SET expires=?',(time.time()-1,))
        Bot(Store(self.store.path),self.api).expire()
        self.assertTrue(any(m=='deleteMessage' and p['message_id']==row['prompt_id'] for m,p in self.api.calls))
        self.assertFalse(any(m in ('banChatMember','unbanChatMember','declineChatJoinRequest') for m,_ in self.api.calls))
        self.assertFalse(any(m=='restrictChatMember' and p['permissions'].get('can_send_messages') for m,p in self.api.calls))
        self.assertEqual(self.store.snapshot()['ranking'],[])
    def test_success_restores_group_defaults_and_awards_once(self):
        row=self.arrival()
        callback={'id':'test_callback','from':self.user,'data':f"gv:{row['nonce']}:{row['answer']}"}
        self.bot.callback(callback);self.bot.callback(callback)
        unmutes=[p for m,p in self.api.calls if m=='restrictChatMember' and p['permissions'].get('can_send_messages')]
        self.assertEqual(len(unmutes),1);self.assertTrue(unmutes[0]['permissions']['can_send_photos'])
        self.assertEqual(self.store.snapshot()['ranking'][0]['total'],20)
    def test_existing_member_restrictions_are_preserved_after_verification(self):
        original=self.api.call
        self.api.call=lambda m,**p: {'status':'restricted','can_send_messages':True,'can_send_photos':False,'can_invite_users':False} if m=='getChatMember' else original(m,**p)
        row=self.arrival()
        self.bot.callback({'id':'cb','from':self.user,'data':f"gv:{row['nonce']}:{row['answer']}"})
        restored=[p['permissions'] for m,p in self.api.calls if m=='restrictChatMember'][-1]
        self.assertTrue(restored['can_send_messages']);self.assertFalse(restored['can_send_photos']);self.assertFalse(restored['can_invite_users'])
    def test_administrators_receive_welcome_but_are_not_muted(self):
        original=self.api.call
        self.api.call=lambda m,**p: {'status':'administrator'} if m=='getChatMember' else original(m,**p)
        row=self.arrival()
        self.assertEqual(row['status'],'not_required')
        self.assertFalse(any(m=='restrictChatMember' for m,_ in self.api.calls))
        self.assertTrue(any(m=='sendMessage' and 'Welcome' in p['text'] for m,p in self.api.calls))
    def test_failed_prompt_cleanup_is_persisted_and_retried(self):
        row=self.arrival();self.api.fail='deleteMessage'
        with self.store.db() as db:db.execute('UPDATE member_arrivals SET expires=?',(time.time()-1,))
        self.bot.expire()
        with self.store.db() as db:
            self.assertIsNotNone(db.execute('SELECT prompt_id FROM member_arrivals').fetchone()[0])
            db.execute('UPDATE member_arrivals SET cleanup_after=0')
        self.api.fail=None;self.bot.expire()
        with self.store.db() as db:self.assertIsNone(db.execute('SELECT prompt_id FROM member_arrivals').fetchone()[0])
    def test_invitation_attribution_can_arrive_after_service_message(self):
        link=self.bot.invite(-1001,{'id':1})
        self.bot.community.arrive(self.group,self.user,source_time=1000)
        self.bot.community.arrive(self.group,self.user,source_time=1000,inviter=self.bot.community.inviter(-1001,link))
        with self.store.db() as db:row=dict(db.execute('SELECT * FROM member_arrivals').fetchone())
        self.bot.callback({'id':'cb','from':self.user,'data':f"gv:{row['nonce']}:{row['answer']}"})
        self.assertEqual(self.store.snapshot()['ranking'][0]['user_id'],1)
    def test_announcement_photo_topic_and_pin_are_real_api_calls(self):
        body=self.plan();body.update(image='photo_fixture',image_english_confirmed=True,topic_id=123,pin=True)
        self.bot.community.save_announcement(body,{'username':'admin'})
        photo=next(p for m,p in self.api.calls if m=='sendPhoto')
        self.assertEqual(photo['message_thread_id'],123);self.assertEqual(photo['caption'],body['body'])
        self.assertTrue(any(m=='pinChatMessage' for m,_ in self.api.calls))
        self.assertEqual(self.store.snapshot()['announcement_deliveries'][0]['status'],'sent')
    def test_daily_multiple_times_restart_and_no_duplicate_delivery(self):
        body=self.plan();body.update(mode='daily',start_date='2030-01-01',times=['09:00','15:00'])
        self.bot.community.save_announcement(body,{'username':'admin'})
        at=datetime(2030,1,1,9,0,2,tzinfo=TZ)
        self.bot.community.process_announcements(at)
        Bot(self.store,self.api).community.process_announcements(at)
        self.bot.community.process_announcements(at.replace(hour=15))
        self.assertEqual(sum(m=='sendMessage' for m,_ in self.api.calls),2)
        self.assertEqual(len(self.store.snapshot()['announcement_deliveries']),2)
    def test_pin_failure_does_not_resend_announcement(self):
        self.api.fail='pinChatMessage';body=self.plan();body['pin']=True
        self.bot.community.save_announcement(body,{'username':'admin'})
        self.bot.community.process_announcements()
        self.assertEqual(sum(m=='sendMessage' for m,_ in self.api.calls),1)
        self.assertEqual(self.store.snapshot()['announcement_deliveries'][0]['status'],'sent_pin_failed')
    def test_failed_or_uncertain_send_is_not_automatically_retried(self):
        self.api.fail='sendMessage'
        self.bot.community.save_announcement(self.plan(),{'username':'admin'})
        Bot(self.store,self.api).community.process_announcements()
        self.assertEqual(sum(m=='sendMessage' for m,_ in self.api.calls),1)
        self.assertEqual(self.store.snapshot()['announcement_deliveries'][0]['status'],'unknown')
    def test_chinese_copy_and_unconfirmed_image_are_rejected(self):
        body=self.plan();body['body']='请参加活动'
        self.assertRaises(ValueError,self.bot.community.save_announcement,body,{'username':'admin'})
        body=self.plan();body['image']='photo_fixture'
        self.assertRaises(ValueError,self.bot.community.save_announcement,body,{'username':'admin'})
        self.assertEqual(self.api.calls,[])
    def test_pause_cancel_and_missed_time(self):
        body=self.plan();body.update(mode='daily',start_date='2030-01-01',times=['09:00'])
        pid=self.bot.community.save_announcement(body,{'username':'admin'})['id']
        self.bot.community.announcement_action('pause',pid)
        self.bot.community.process_announcements(datetime(2030,1,1,9,0,1,tzinfo=TZ))
        self.assertEqual(self.api.calls,[])
        self.bot.community.announcement_action('resume',pid)
        self.bot.community.process_announcements(datetime(2030,1,1,10,0,tzinfo=TZ))
        self.assertEqual(self.store.snapshot()['announcement_deliveries'][0]['status'],'missed')
        self.assertEqual(self.api.calls,[])
        self.bot.community.announcement_action('cancel',pid)
        self.bot.community.process_announcements(datetime(2030,1,2,9,0,1,tzinfo=TZ))
        self.assertEqual(self.api.calls,[])


class CommunityHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'http.sqlite3')
        self.store.group({'id':-1001,'title':'Test community'})
        self.bot=Bot(self.store,FakeTelegram())
        self.server=AppServer(('127.0.0.1',0),self.store,'temporary-test-password',self.bot,False)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.client=Client(self.base);self.assertEqual(self.client.login(password='temporary-test-password')[0],200)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def test_settings_timeout_and_announcement_api(self):
        self.assertEqual(self.client.request('/api/settings',{'group_id':-1001,'verify_minutes':2})[0],200)
        self.assertEqual(self.client.request('/api/settings',{'group_id':-1001,'verify_minutes':0})[0],400)
        code,result=self.client.request('/api/announcements/save',{'group_id':-1001,'title':'Test','body':'Hello community!','mode':'now','activate':True,'pin':False})
        self.assertEqual(code,200,result)
        state=self.client.request('/api/snapshot')[1]
        self.assertEqual(state['groups'][0]['verify_minutes'],2)
        self.assertEqual(state['announcement_deliveries'][0]['status'],'sent')
    def test_read_only_announcement_admin_cannot_publish_or_change_timeout(self):
        code,row=self.client.request('/api/admins/create',{'username':'ann_reader','name':'Reader','password':'reader-test-password','permissions':['announcements.view']})
        self.assertEqual(code,200,row)
        reader=Client(self.base);self.assertEqual(reader.login('ann_reader','reader-test-password')[0],200)
        self.assertEqual(reader.request('/api/announcements/save',{'title':'Forbidden'})[0],403)
        self.assertEqual(reader.request('/api/settings',{'group_id':-1001,'verify_minutes':1})[0],403)
        self.assertNotIn('verify_minutes',reader.request('/api/snapshot')[1]['groups'][0])
