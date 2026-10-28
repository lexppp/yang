from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import Bot, Store, TelegramError, moderation_reason
from group_links import disconnect
from test_app import FakeTelegram
import test_community_runtime as community_tests
from test_accounts import Client


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.group=dict(block_links=True,block_photos=False,block_ads=True,keywords='代投,稳赚,刷单')
    def test_screenshot_ads_are_detected_without_using_names_or_model(self):
        messages=[
            {'text':'WTB\nI need sellers from any country except African countries to complete TNG tasks. I need 30–50 accounts per day.'},
            {'text':'Allocating capital to @FALCON_TRADING_GUIDANCE has enabled my account to scale from $8,000 to $21,000 in three weeks.'},
            {'text':'全新项目2026新搬木，有执行力的来，稳定赚钱不接头。每天收入3500+ 老板：@leishiyan','reply_markup':{'inline_keyboard':[[{'text':'公群担保','url':'https://t.me/example'}]]}},
            {'text':'全新项目2026','forward_origin':{'type':'hidden_user','sender_user_name':'Deleted account'}},
            {'text':'有时间就行，日入3000+'},
            {'text':'帮忙收钱，每日3000+'},
            {'text':'几分钟搞1k'},
            {'text':'天天暴死累活拿死工资活该你穷！黑产内部黑u项目，一天五位数随便拿，看简历安排'},
            {'text':'新手小白也能一个月提车。看我简介，直接带你上岸！一天稳赚3W'},
            {'text':'赚取报酬','reply_markup':{'inline_keyboard':[[{'text':'Contact me','url':'tg://user?id=123'}]]}},
        ]
        for message in messages:
            with self.subTest(message=message):self.assertIsNotNone(moderation_reason(self.group,message))
    def test_hidden_links_captions_mentions_and_new_domains(self):
        for message in [
            {'text':'Contact me','entities':[{'type':'text_link','url':'https://example.top','offset':0,'length':10}]},
            {'caption':'Please contact @SOME_ADVERTISER','photo':[{'file_id':'test'}]},
            {'text':'Get started on example.top'},
            {'text':'ＷＷＷ．example．cc'},
            {'text':'刷\u200b单，稳\u2060赚'},
            {'text':'Invite','entities':[{'type':'text_mention','user':{'id':123},'offset':0,'length':6}]},
            {'text':'Welcome','via_bot':{'id':999},'reply_markup':{'inline_keyboard':[[{'text':'Register','web_app':{'url':'https://example.com'}}]]}},
        ]:
            with self.subTest(message=message):self.assertIsNotNone(moderation_reason(self.group,message))
    def test_normal_discussion_whitelist_and_commands_are_allowed(self):
        group={**self.group,'allowed_mentions':'@CoinWCommunityHubBot, @official_support id:123'}
        for message in [
            {'text':'Small buys only, keeping the rest for a confirmed breakout'},
            {'text':'My trading results today: +5%. I will reduce position size.'},
            {'text':'CoinW有什么活动？我想了解风险管理和跟单功能'},
            {'text':'KYC verification failed. Can the official support team help?'},
            {'text':'I bought Bitcoin on CoinW yesterday.'},
            {'text':'Please ask @OFFICIAL_SUPPORT for help.'},
            {'text':'Member','entities':[{'type':'text_mention','user':{'id':123}}]},
            {'text':'/link@CoinWCommunityHubBot'}, {'text':'/ink'}, {'text':'coinw'},
        ]:
            with self.subTest(message=message):self.assertIsNone(moderation_reason(group,message))
    def test_rule_switches_and_chinese_keyword_separators(self):
        group={**self.group,'block_links':False,'block_mentions':False,'block_forwards':False,'block_buttons':False,'block_ads':False}
        self.assertIsNone(moderation_reason(group,{'text':'@spam example.top 日入3000','forward_origin':{},'reply_markup':{'inline_keyboard':[[{'url':'https://spam.top'}]]}}))
        self.assertIsNotNone(moderation_reason({**self.group,'keywords':'刷单，代投\n买号'},{'text':'买号请联系'}))
        self.assertIsNone(moderation_reason({**self.group,'keywords':'ad'},{'text':'Trading can be risky'}))


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'guard.sqlite3')
        self.chat={'id':-1001234567890,'title':'Test','type':'supergroup'}
        self.group=self.store.group(self.chat);self.api=FakeTelegram();self.bot=Bot(self.store,self.api)
        self.user={'id':22,'first_name':'Member'}
        self.clock=patch('moderation.time.time',return_value=2000000000);self.now=self.clock.start()
    def tearDown(self):self.clock.stop();self.temp.cleanup()
    def message(self,text,mid=1,**extra):
        return {'chat':self.chat,'from':self.user,'text':text,'message_id':mid,**extra}
    def handle(self,text,mid=1,**extra):self.bot.handle({'message':self.message(text,mid,**extra)})
    def calls(self,method):return [p for m,p in self.api.calls if m==method]
    def jobs(self):
        with self.store.db() as db:return [dict(r) for r in db.execute('SELECT * FROM moderation_jobs ORDER BY kind')]
    def test_first_deleted_second_muted_with_dedup_and_no_kicking(self):
        self.handle('WTB 50 accounts per day',1)
        self.assertEqual(len(self.calls('deleteMessage')),1);self.assertFalse(self.calls('restrictChatMember'))
        self.handle('日入3000+',2);self.handle('日入3000+',2)
        self.assertEqual(len(self.calls('deleteMessage')),2);self.assertEqual(len(self.calls('restrictChatMember')),1)
        restriction=self.calls('restrictChatMember')[0]
        self.assertFalse(restriction['permissions']['can_send_messages'])
        self.assertEqual(restriction['until_date'],2000000000+86400)
        self.assertFalse(self.calls('banChatMember'))
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM moderation_violations').fetchone()[0],2)
    def test_delete_and_mute_failures_persist_and_retry_after_restart(self):
        self.api.fail='deleteMessage';self.handle('日入3000+',1)
        self.assertEqual(self.jobs()[0]['status'],'pending')
        self.bot.guard.tick();self.assertEqual(len(self.calls('deleteMessage')),1)
        self.api.fail=None;self.now.return_value+=31
        Bot(Store(self.store.path),self.api).guard.tick()
        self.assertEqual(self.jobs()[0]['status'],'done')
        self.api.fail='restrictChatMember';self.handle('日入3000+',2)
        self.assertEqual([j for j in self.jobs() if j['kind']=='mute'][0]['status'],'pending')
        self.api.fail=None;self.now.return_value+=31;self.bot.guard.tick()
        self.assertEqual([j for j in self.jobs() if j['kind']=='mute'][0]['status'],'done')
    def test_edited_ads_are_moderated_without_multiple_strikes_for_same_message(self):
        self.handle('Hello community',1)
        update={'edited_message':self.message('Contact @SPAM_ACCOUNT',1)}
        self.bot.handle(update);self.bot.handle(update)
        self.assertEqual(len(self.calls('deleteMessage')),1)
        self.assertFalse(self.calls('restrictChatMember'))
    def test_admin_and_anonymous_admin_and_official_channel_are_exempt(self):
        original=self.api.call
        self.api.call=lambda method,**params: {'status':'administrator'} if method=='getChatMember' else original(method,**params)
        self.handle('Official event https://coinw.com',1)
        self.handle('Official event',2,sender_chat=self.chat)
        self.handle('Official event',3,sender_chat={'id':-10099999},is_automatic_forward=True)
        self.assertFalse(self.calls('deleteMessage'));self.assertFalse(self.calls('restrictChatMember'))
    def test_external_channel_is_deleted_without_muting_synthetic_user(self):
        self.handle('Visit our channel',1,sender_chat={'id':-10099999},**{'from':{'id':1087968824,'is_bot':True}})
        self.assertEqual(len(self.calls('deleteMessage')),1)
        self.assertFalse(self.calls('getChatMember'));self.assertFalse(self.calls('restrictChatMember'))
    def test_flood_and_long_repeats_and_group_isolation(self):
        for n in range(9):self.handle('Hello '+str(n),n+1)
        self.assertEqual(self.calls('deleteMessage')[0]['message_id'],9)
        self.now.return_value+=11
        text='This is an identical lengthy text posted over and over again'
        for n in range(3):self.handle(text,100+n)
        self.assertEqual(self.calls('deleteMessage')[-1]['message_id'],102)
        other=self.store.group({'id':-1009876543210,'title':'Other'})
        self.bot.guard.inspect(other,{'message_id':200,'text':'日入3000+'},self.user['id'])
        self.assertFalse(any(p['chat_id']==other['id'] for p in self.calls('restrictChatMember')))
    def test_strike_window_and_delete_only_setting(self):
        self.handle('日入3000+',1);self.now.return_value+=86401;self.handle('日入3000+',2)
        self.assertFalse(self.calls('restrictChatMember'))
        with self.store.db() as db:db.execute('UPDATE groups SET spam_mute_minutes=0')
        self.handle('日入3000+',3);self.assertFalse(self.calls('restrictChatMember'))
    def test_stronger_existing_mute_or_later_admin_promotion_is_not_overridden(self):
        original=self.api.call
        self.handle('日入3000+',1)
        self.api.call=lambda method,**params: {'status':'restricted','can_send_messages':False,'until_date':0} if method=='getChatMember' else original(method,**params)
        self.handle('日入3000+',2);self.assertFalse(self.calls('restrictChatMember'))
        self.api.call=original;self.api.fail='restrictChatMember';self.handle('日入3000+',3)
        calls=len(self.calls('restrictChatMember'));self.api.fail=None
        self.api.call=lambda method,**params: {'status':'administrator'} if method=='getChatMember' else original(method,**params)
        self.now.return_value+=31;self.bot.guard.tick()
        self.assertEqual(len(self.calls('restrictChatMember')),calls)
    def test_expired_mute_is_not_turned_into_permanent_mute(self):
        self.handle('日入3000+',1);self.api.fail='restrictChatMember';self.handle('日入3000+',2)
        self.api.fail=None;self.now.return_value+=86401;self.bot.guard.tick()
        self.assertEqual([j for j in self.jobs() if j['kind']=='mute'][0]['status'],'cancelled')
        self.assertEqual(len(self.calls('restrictChatMember')),1)
    def test_detached_or_migrated_group_does_not_replay_old_actions(self):
        self.api.fail='deleteMessage';self.handle('日入3000+',1)
        disconnect(self.store,self.group['id']);self.api.fail=None;self.now.return_value+=31;self.bot.guard.tick()
        self.assertEqual(len(self.calls('deleteMessage')),1)
        other=self.store.group({'id':-1234,'title':'Old'});self.api.fail='deleteMessage'
        self.bot.guard.inspect(other,{'message_id':77,'text':'日入3000+'},22)
        self.store.migrate_group(-1234,-1002222222222);self.api.fail=None;self.now.return_value+=31;self.bot.guard.tick()
        self.assertFalse(any(p['chat_id']==-1002222222222 for p in self.calls('deleteMessage')))
    def test_not_found_counts_as_done_and_rate_limit_waits(self):
        original=self.api.call
        def limited(method,**params):
            if method=='deleteMessage':raise TelegramError('Too Many Requests',{'retry_after':120})
            return original(method,**params)
        self.api.call=limited;self.handle('日入3000+',1)
        self.assertGreaterEqual(self.jobs()[0]['next_attempt']-self.now.return_value,120)
        def gone(method,**params):
            if method=='deleteMessage':raise TelegramError('Bad Request: message to delete not found')
            return original(method,**params)
        self.api.call=gone;self.now.return_value+=121;self.bot.guard.tick()
        self.assertEqual(self.jobs()[0]['status'],'done')
    def test_failed_admin_lookup_does_not_lose_ads_or_delete_an_administrator(self):
        self.api.fail='getChatMember';self.handle('日入3000+',1)
        self.assertFalse(self.calls('deleteMessage'));self.assertEqual(self.jobs()[0]['status'],'pending')
        self.api.fail=None;self.now.return_value+=31
        Bot(Store(self.store.path),self.api).guard.tick()
        self.assertEqual(len(self.calls('deleteMessage')),1)
        self.api.fail='getChatMember';self.handle('Official event https://coinw.com',2)
        original=self.api.call;self.api.fail=None
        self.api.call=lambda method,**params: {'status':'administrator'} if method=='getChatMember' else original(method,**params)
        self.now.return_value+=31;self.bot.guard.tick()
        self.assertEqual(len(self.calls('deleteMessage')),1)
        with self.store.db() as db:self.assertFalse(db.execute('SELECT 1 FROM moderation_violations WHERE message_id=2').fetchone())


class ModerationHttpTests(unittest.TestCase):
    setUp=community_tests.CommunityHttpTests.setUp
    tearDown=community_tests.CommunityHttpTests.tearDown
    def test_settings_permissions_validation_and_permission_diagnostics(self):
        settings=dict(group_id=-1001,block_mentions=True,block_buttons=True,block_forwards=True,block_sender_chat=True,block_repeats=True,block_flood=True,allowed_mentions='@Official_support',spam_strikes=2,spam_mute_minutes=1440)
        self.assertEqual(self.client.request('/api/settings',settings)[0],200)
        self.assertEqual(self.client.request('/api/snapshot')[1]['groups'][0]['allowed_mentions'],'@Official_support')
        for key,value in [('spam_strikes',0),('spam_strikes',True),('spam_mute_minutes',10081),('spam_mute_minutes',-1),('block_mentions','true'),('allowed_mentions',[])]:
            self.assertEqual(self.client.request('/api/settings',{'group_id':-1001,key:value})[0],400)
        original=self.bot.api.call
        def rights(method,**params):
            if method=='getMe':return {'id':999,'username':'FixtureBot'}
            if method=='getChatMember':return {'status':'administrator','can_delete_messages':True,'can_restrict_members':False}
            return original(method,**params)
        self.bot.api.call=rights
        code,status=self.client.request('/api/moderation/status',{'group_id':-1001})
        self.assertEqual(code,200,status);self.assertTrue(status['delete_messages']);self.assertFalse(status['restrict_members'])
        self.assertEqual(self.client.request('/api/moderation/status',{'group_id':-1009999})[0],400)
        self.client.request('/api/admins/create',dict(username='viewer',name='Reader',password='disposable-reader-password',permissions=['dashboard.view']))
        viewer=Client(self.base);self.assertEqual(viewer.login('viewer','disposable-reader-password')[0],200)
        self.assertEqual(viewer.request('/api/settings',settings)[0],403)
        self.assertEqual(viewer.request('/api/moderation/status',{'group_id':-1001})[0],403)
        self.assertNotIn('spam_strikes',viewer.request('/api/snapshot')[1]['groups'][0])


if __name__=='__main__':unittest.main()
