"""Welcome/link expiration uses persisted IDs; never remove unrelated messages or links."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import Bot, Store, TelegramError
from group_links import disconnect
from test_app import FakeTelegram
import test_community_runtime as community_tests


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'cleanup.sqlite3')
        self.group=self.store.group({'id':-1001234567890,'title':'Community','type':'supergroup'})
        self.user={'id':2,'first_name':'Member'}
        self.api=FakeTelegram();self.bot=Bot(self.store,self.api)
        self.clock=patch('community_runtime.time.time',return_value=2000000000)
        self.now=self.clock.start()
    def tearDown(self):
        self.clock.stop();self.temp.cleanup()
    def tasks(self):
        with self.store.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM bot_message_cleanup ORDER BY message_id')]
    def command(self, text='/link'):
        self.bot.handle({'message':{'chat':{'id':self.group['id'],'title':'Community','type':'supergroup'},
                                   'from':self.user,'message_id':900,'text':text}})
    def deleted(self):
        return [p for m,p in self.api.calls if m=='deleteMessage']

    def test_text_welcome_and_link_expire_independently_after_restart_without_revoking(self):
        self.bot.welcome(self.group,self.user)
        self.command()
        rows=self.tasks();self.assertEqual([r['kind'] for r in rows],['welcome','link'])
        welcome,link=rows
        restarted=Bot(Store(self.store.path),self.api)
        self.now.return_value=2000000119;restarted.community.cleanup_messages()
        self.assertEqual(self.deleted(),[])
        self.now.return_value=2000000120;restarted.community.cleanup_messages()
        self.assertEqual(self.deleted(),[{'chat_id':self.group['id'],'message_id':link['message_id']}])
        self.assertEqual(len(self.tasks()),1)
        self.now.return_value=2000000240;restarted.community.cleanup_messages();restarted.community.cleanup_messages()
        self.assertEqual(len(self.deleted()),2)
        self.assertEqual(self.deleted()[-1]['message_id'],welcome['message_id'])
        self.assertEqual(self.tasks(),[])
        self.assertFalse(any(m=='revokeChatInviteLink' for m,_ in self.api.calls))
        with self.store.db() as db:self.assertEqual(db.execute('SELECT active FROM invites').fetchone()[0],1)
        self.assertFalse(any(p['message_id']==900 for p in self.deleted()))

    def test_photo_and_failed_photo_fallback_are_both_scheduled(self):
        self.bot.community.welcome({**self.group,'welcome_photo':'photo_fixture'},self.user)
        self.assertEqual(len(self.tasks()),1)
        self.assertEqual(self.api.calls[-1][0],'sendPhoto')
        self.api.fail='sendPhoto'
        self.bot.community.welcome({**self.group,'welcome_photo':'bad_fixture'},self.user)
        self.assertEqual(len(self.tasks()),2)
        self.assertEqual(self.api.calls[-1][0],'sendMessage')
        self.now.return_value+=241;self.bot.community.cleanup_messages()
        self.assertEqual(len(self.deleted()),2)

    def test_admin_or_verification_disabled_still_gets_expiring_welcome(self):
        original=self.api.call
        self.api.call=lambda method,**params: {'status':'administrator'} if method=='getChatMember' else original(method,**params)
        self.bot.community.arrive(self.group,self.user)
        self.bot.community.arrive({**self.group,'verify':False},{'id':3,'first_name':'Other'})
        self.assertEqual([r['kind'] for r in self.tasks()],['welcome','welcome'])
        self.now.return_value+=241;self.bot.community.cleanup_messages()
        self.assertEqual(len(self.deleted()),2)

    def test_legacy_ink_custom_duration_and_zero_retention_setting(self):
        with self.store.db() as db:
            db.execute('UPDATE groups SET link_delete_minutes=1,welcome_delete_minutes=0 WHERE id=?',(self.group['id'],))
        group=self.store.snapshot()['groups'][0]
        self.bot.welcome(group,self.user);self.command('/ink@CoinWCommunityHubBot')
        row=self.tasks()[0];self.assertEqual(row['delete_at']-row['created_at'],60)
        self.assertEqual(row['kind'],'link')
        with self.store.db() as db:db.execute('UPDATE groups SET link_delete_minutes=0')
        self.command()
        self.assertEqual(len(self.tasks()),1)
        # Changing the setting only affects future sends, not previously scheduled messages.
        self.now.return_value+=61;self.bot.community.cleanup_messages()
        self.assertEqual(len(self.deleted()),1)

    def test_failed_delete_waits_then_retries_after_restart(self):
        self.bot.welcome(self.group,self.user)
        self.api.fail='deleteMessage';self.now.return_value+=241
        self.bot.community.cleanup_messages();self.bot.community.cleanup_messages()
        self.assertEqual(len(self.deleted()),1)
        self.assertEqual(self.tasks()[0]['attempts'],1)
        restarted=Bot(Store(self.store.path),self.api)
        self.api.fail=None;self.now.return_value+=31;restarted.community.cleanup_messages()
        self.assertEqual(len(self.deleted()),2);self.assertEqual(self.tasks(),[])
        self.assertTrue(any(r['kind']=='自动清理重试' for r in self.store.snapshot()['logs']))

    def test_rate_limit_retry_after_is_respected(self):
        self.bot.welcome(self.group,self.user)
        original=self.api.call
        def limited(method,**params):
            if method=='deleteMessage':raise TelegramError('Too Many Requests',{'retry_after':120})
            return original(method,**params)
        self.api.call=limited;self.now.return_value+=241
        self.bot.community.cleanup_messages()
        self.assertGreaterEqual(self.tasks()[0]['next_attempt']-self.now.return_value,120)

    def test_already_deleted_message_is_done_and_expired_window_is_not_retried(self):
        self.bot.welcome(self.group,self.user)
        original=self.api.call
        def gone(method,**params):
            if method=='deleteMessage':raise TelegramError('Bad Request: message to delete not found')
            return original(method,**params)
        self.api.call=gone;self.now.return_value+=241
        self.bot.community.cleanup_messages();self.assertEqual(self.tasks(),[])
        self.api.call=original;self.bot.welcome(self.group,self.user)
        self.now.return_value+=48*3600
        self.bot.community.cleanup_messages();self.bot.community.cleanup_messages()
        self.assertEqual(self.tasks()[0]['status'],'failed')
        self.assertEqual(self.deleted(),[])

    def test_other_group_announcement_and_verification_messages_are_not_removed(self):
        self.bot.community.arrive(self.group,self.user)
        other=self.store.group({'id':-1009876543210,'title':'Other','type':'supergroup'})
        self.bot.welcome({**other,'welcome_delete_minutes':10},self.user)
        self.bot.community.save_announcement(dict(title='News',group_id=self.group['id'],body='Community news',mode='now',activate=True,pin=False),{'username':'admin'})
        with self.store.db() as db:prompt=db.execute('SELECT prompt_id FROM member_arrivals').fetchone()[0]
        expected=[r for r in self.tasks() if r['group_id']==self.group['id']]
        self.now.return_value+=241;self.bot.community.cleanup_messages()
        self.assertEqual(self.deleted(),[{'chat_id':self.group['id'],'message_id':expected[0]['message_id']}])
        self.assertNotEqual(self.deleted()[0]['message_id'],prompt)
        self.assertEqual(self.tasks()[0]['group_id'],other['id'])

    def test_disconnect_cancels_cleanup_and_migration_never_retargets_message_ids(self):
        self.bot.welcome(self.group,self.user);disconnect(self.store,self.group['id'])
        self.now.return_value+=241;self.bot.community.cleanup_messages()
        self.assertEqual(self.deleted(),[]);self.assertEqual(self.tasks()[0]['status'],'cancelled')
        old=self.store.group({'id':-12345,'title':'Old','type':'group'})
        self.bot.welcome(old,self.user);mid=self.tasks()[-1]['message_id']
        self.store.migrate_group(old['id'],-1005555555555)
        self.now.return_value+=241;self.bot.community.cleanup_messages()
        self.assertIn({'chat_id':old['id'],'message_id':mid},self.deleted())
        self.assertFalse(any(p['chat_id']==-1005555555555 for p in self.deleted()))


class CleanupHttpTests(unittest.TestCase):
    setUp=community_tests.CommunityHttpTests.setUp
    tearDown=community_tests.CommunityHttpTests.tearDown
    def test_cleanup_settings_validation_persistence_and_permissions(self):
        settings={'group_id':-1001,'welcome_delete_minutes':3,'link_delete_minutes':1}
        self.assertEqual(self.client.request('/api/settings',settings)[0],200)
        state=self.client.request('/api/snapshot')[1]
        self.assertEqual(state['groups'][0]['welcome_delete_minutes'],3)
        self.assertEqual(Store(self.store.path).snapshot()['groups'][0]['link_delete_minutes'],1)
        for value in (-1,1441,True,'2',1.5):
            for key in ('welcome_delete_minutes','link_delete_minutes'):
                self.assertEqual(self.client.request('/api/settings',{'group_id':-1001,key:value})[0],400)
        self.assertEqual(self.client.request('/api/settings',{'group_id':-1001,'welcome_delete_minutes':0})[0],200)
        self.client.request('/api/admins/create',{'username':'points_only','name':'Points','password':'disposable-test-password','permissions':['points.configure']})
        from test_accounts import Client
        reader=Client(self.base);self.assertEqual(reader.login('points_only','disposable-test-password')[0],200)
        self.assertEqual(reader.request('/api/settings',settings)[0],403)
        for key in ('welcome_delete_minutes','link_delete_minutes'):
            self.assertNotIn(key,reader.request('/api/snapshot')[1]['groups'][0])


if __name__=='__main__':
    unittest.main()
