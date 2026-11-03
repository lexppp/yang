import concurrent.futures
from datetime import datetime,timedelta
import io
import json
import unittest
from unittest.mock import patch

from app import Bot, Store, Telegram, TZ
from engagement import useful
import test_app as core_tests
import test_community_runtime as community_tests
from test_accounts import Client


class ChatRewardFixture(unittest.TestCase):
    def setUp(self):
        core_tests.CoreTests.setUp(self)
        self.base=datetime.now(TZ).replace(hour=12,minute=0,second=0,microsecond=0)
        self.clock=patch('engagement.current_time',return_value=self.base);self.now=self.clock.start()
        self.unix_clock=patch('moderation.time.time',return_value=self.base.timestamp());self.unix_now=self.unix_clock.start()
    def tearDown(self):
        self.clock.stop();self.unix_clock.stop();core_tests.CoreTests.tearDown(self)
    def message(self,mid,text=None,gid=-1001,uid=1,at=None,**extra):
        return dict(chat={'id':gid,'type':'supergroup','title':'Test community'},message_id=mid,
                    **{'from':{'id':uid,'first_name':'小明 <Admin> & Alice'}},
                    date=int((at or self.base-timedelta(minutes=50)+timedelta(seconds=mid%100*15)).timestamp()),
                    text=text if text is not None else f'我对今天市场走势的有效观察，观点编号{mid}',**extra)
    def handle(self,mid,text=None,**extra):
        self.unix_now.return_value=self.now.return_value.timestamp()
        self.bot.handle({'message':self.message(mid,text,**extra)})
    def points(self,gid=-1001,uid=1):
        with self.store.db() as db:return db.execute('SELECT COALESCE(SUM(points),0) FROM ledger WHERE group_id=? AND user_id=?',(gid,uid)).fetchone()[0]


class ChatRewardTests(ChatRewardFixture):
    def test_exact_ten_characters_and_not_nine_or_padding_or_mechanical_repetition(self):
        self.assertFalse(useful('一二三四五六七八九',10));self.assertTrue(useful('一二三四五六七八九十',10))
        for text in ('一二三四五六七八九    !!!',' '*100,'😂'*100,'1234567890','哈哈'*20,'abc'*20):self.assertFalse(useful(text,10),text)
        self.handle(1,'一二三四五六七八九');self.handle(2,'一二三四五六七八九十')
        self.assertEqual(self.points(),5)
        self.assertFalse(any(m=='sendMessage' for m,_ in self.api.calls))
    def test_ten_valid_messages_cap_at_fifty_and_checkin_invites_are_separate(self):
        for mid in range(1,13):self.handle(mid)
        self.assertEqual(self.points(),50)
        self.store.checkin(-1001,1,self.base);self.store.referral(-1001,2,1)
        self.assertEqual(self.points(),75)
        with self.store.db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM ledger WHERE kind='chat'").fetchone()[0],10)
    def test_concurrent_rewards_do_not_exceed_cap_or_double_credit(self):
        def award(mid):
            message=self.message(mid);self.bot.chat_rewards.observe(self.group,message)
            return self.bot.chat_rewards.award(self.group,message)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:amounts=list(pool.map(award,list(range(1,31))+[1,2,3]))
        self.assertEqual(sum(amounts),50);self.assertEqual(self.points(),50)
    def test_replayed_update_daily_normalized_duplicates_and_restart_are_idempotent(self):
        self.handle(1,'Today I prefer a smaller position size')
        self.handle(1,'Today I prefer a smaller position size')
        self.handle(2,'TODAY I PREFER A SMALLER POSITION SIZE!!!')
        Bot(Store(self.store.path),self.api).handle({'message':self.message(1,'Today I prefer a smaller position size')})
        self.assertEqual(self.points(),5)
    def test_midnight_utc8_resets_only_daily_cap_and_preserves_history(self):
        day1=self.base.replace(hour=23,minute=40)
        self.now.return_value=day1+timedelta(minutes=19)
        for mid in range(1,11):self.handle(mid,at=day1+timedelta(minutes=mid))
        nextday=(self.base+timedelta(days=1)).replace(hour=0,minute=1);self.now.return_value=nextday
        self.handle(50,'一二三四五六七八九十',at=nextday)
        self.assertEqual(self.points(),55)
        with self.store.db() as db:
            days=db.execute("SELECT substr(period,1,10) day,SUM(points) earned FROM ledger WHERE kind='chat' GROUP BY day ORDER BY day").fetchall()
        self.assertEqual([r['earned'] for r in days],[50,5])
    def test_groups_are_isolated_and_upgrade_merges_daily_caps_with_physical_ids(self):
        other=self.store.group({'id':-1002,'title':'Other group'})
        for mid in range(1,9):
            self.handle(mid,f'旧群市场观察和风险管理观点编号{mid}')
            message=self.message(mid,f'新群交易经验及仓位管理观点编号{mid}',gid=-1002)
            self.bot.chat_rewards.observe(other,message);self.bot.chat_rewards.award(other,message)
        self.assertEqual(self.points(),40);self.assertEqual(self.points(-1002),40)
        self.store.migrate_group(-1001,-1002)
        self.assertEqual(self.points(-1002),50)
        self.assertEqual(len([r for r in self.store.snapshot()['ranking'] if r['user_id']==1]),1)
        self.handle(99,'这是一条升级之后的新发言和市场观点',gid=-1002)
        self.assertEqual(self.points(-1002),50)
    def test_ads_forwards_commands_inline_bots_and_photos_without_caption_do_not_earn(self):
        cases=[('这是刷单广告，今天每日收入3000+',{}),('Read more https://example.top',{}),('/unknown_command_with_long_text',{}),('这是用户转发来的交易观点和消息',{'forward_origin':{'type':'hidden_user'}}),('这是一条来自内联机器人的代发消息',{'via_bot':{'id':999}})]
        for mid,(text,extra) in enumerate(cases,1):self.handle(mid,text,**extra)
        self.handle(10,'',photo=[{'file_id':'fixture'}]);self.assertEqual(self.points(),0)
    def test_administrator_exemption_from_deletion_does_not_grant_ad_rewards(self):
        original=self.api.call
        self.api.call=lambda method,**params:{'status':'administrator'} if method=='getChatMember' else original(method,**params)
        self.handle(1,'An official promotion https://coinw.com')
        self.handle(2,'This is my personal view on position sizing')
        self.assertEqual(self.points(),5);self.assertFalse(any(m=='deleteMessage' for m,_ in self.api.calls))
    def test_edits_never_add_points_and_ad_or_short_edits_revoke_existing_rewards(self):
        self.handle(1,'你好');self.bot.handle({'edited_message':self.message(1)})
        self.assertEqual(self.points(),0)
        self.handle(2);self.bot.handle({'edited_message':self.message(2,'编辑后仍然符合字符数量的正常观点')})
        self.assertEqual(self.points(),5)
        self.bot.handle({'edited_message':self.message(2,'编辑成广告，日入3000+')});self.assertEqual(self.points(),0)
        self.handle(3);self.bot.handle({'edited_message':self.message(3,'太短')});self.assertEqual(self.points(),0)
    def test_flood_reverses_the_whole_burst_even_if_last_message_is_short_and_filter_disabled(self):
        with self.store.db() as db:db.execute('UPDATE groups SET block_flood=0')
        for mid in range(1,9):self.handle(mid,at=self.base+timedelta(seconds=mid))
        self.assertEqual(self.points(),40)
        self.handle(9,'hi',at=self.base+timedelta(seconds=9))
        self.assertEqual(self.points(),0)
        with self.store.db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM ledger WHERE kind='chat_reversal'").fetchone()[0],8)
    def test_fast_duplicate_spam_reverses_first_reward_and_rollback_is_idempotent(self):
        self.handle(1,'一二三四五六七八九十',at=self.base)
        self.handle(2,'一二三四五六七八九十',at=self.base+timedelta(seconds=1))
        self.bot.chat_rewards.exclude(-1001,1,2,'重复刷屏')
        self.assertEqual(self.points(),0)
        with self.store.db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM ledger WHERE kind='chat_reversal'").fetchone()[0],1)
    def test_regular_messages_backlogged_after_restart_are_not_treated_as_a_burst(self):
        for mid in range(1,11):self.handle(mid,at=self.base-timedelta(minutes=11-mid))
        self.assertEqual(self.points(),50)
        self.assertFalse(any(m=='deleteMessage' for m,_ in self.api.calls))
    def test_pending_verification_and_disabled_reward_do_not_earn(self):
        self.bot.community.arrive(self.group,{'id':1,'first_name':'Member'})
        self.handle(1);self.assertEqual(self.points(),0)
        with self.store.db() as db:db.execute("UPDATE member_arrivals SET status='verified'");db.execute('UPDATE groups SET chat_rewards_enabled=0')
        self.handle(2);self.assertEqual(self.points(),0)
    def test_transient_admin_lookup_failure_does_not_drop_clean_rewards_or_rankings(self):
        self.api.fail='getChatMember'
        self.handle(1);self.handle(2,'/Rankings')
        self.assertEqual(self.points(),5)
        self.assertTrue(any(method=='sendMessage' and 'Your rank: #1' in params['text'] for method,params in self.api.calls))


class RankingTests(ChatRewardFixture):
    def test_rankings_variants_group_isolation_and_original_names_are_html_safe(self):
        self.handle(1);self.api.calls.clear()
        self.bot.identity={'username':'CoinWCommunityHubBot'}
        for mid,command in enumerate(('/Rankings','/rankings','/RANKINGS@CoinWCommunityHubBot'),10):self.handle(mid,command,message_thread_id=123)
        replies=[p for m,p in self.api.calls if m=='sendMessage']
        self.assertEqual(len(replies),3)
        self.assertTrue(all('5 points' in r['text'] and 'Your rank: #1' in r['text'] for r in replies))
        self.assertIn('小明 &lt;Admin&gt; &amp; Alice',replies[0]['text']);self.assertEqual(replies[0]['message_thread_id'],123)
        self.assertEqual(replies[0]['parse_mode'],'HTML')
        self.handle(50,'/rankings@OtherBot');self.assertEqual(len([m for m,_ in self.api.calls if m=='sendMessage']),3)
        self.assertEqual(self.points(),5)
    def test_top_ten_own_rank_outside_top_ten_ties_and_no_other_group_leak(self):
        other=self.store.group({'id':-1002,'title':'Other'})
        for uid in range(1,13):
            self.store.member(-1001,{'id':uid,'first_name':f'Member {uid}'})
            with self.store.db() as db:db.execute("INSERT INTO ledger(group_id,user_id,points,kind,period,created) VALUES(?,?,?,'checkin','fixture',?)",(-1001,uid,100-uid,self.base.isoformat()))
        self.store.member(-1002,{'id':999,'first_name':'PrivateOtherGroup'})
        self.store.checkin(-1002,999,self.base)
        text=self.bot.chat_rewards.rankings(-1001,12)
        self.assertEqual(text.count('<a href='),10);self.assertIn('Your rank: #12',text)
        self.assertNotIn('PrivateOtherGroup',text);self.assertNotIn('Member 11',text)
        self.assertLess(len(text),4096)
    def test_empty_rankings_and_wire_payload_allow_unicode_without_private_flags(self):
        self.assertIn('No points yet',self.bot.chat_rewards.rankings(-1001,1))
        self.handle(1)
        self.bot.api=Telegram('disposable-fixture-token')
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps({'ok':True,'result':{'message_id':10}}).encode())) as request:
            self.bot.send(-1001,self.bot.chat_rewards.rankings(-1001,1),_user_content=True,parse_mode='HTML')
            sent=json.loads(request.call_args.args[0].data)
            self.assertIn('小明',sent['text']);self.assertNotIn('_user_content',sent)
        with self.store.db() as db:db.execute('UPDATE groups SET chat_rewards_enabled=0')
        self.assertIn('Chat rewards are currently disabled',self.bot.chat_rewards.rankings(-1001,1))


class ChatRewardHttpTests(unittest.TestCase):
    setUp=community_tests.CommunityHttpTests.setUp
    tearDown=community_tests.CommunityHttpTests.tearDown
    def test_settings_defaults_validation_permissions_and_snapshot_totals(self):
        group=self.client.request('/api/snapshot')[1]['groups'][0]
        self.assertEqual([group[k] for k in ('chat_rewards_enabled','chat_reward_points','chat_daily_cap','chat_min_chars')],[1,5,50,10])
        for key,value in [('chat_rewards_enabled','true'),('chat_reward_points',-1),('chat_reward_points',True),('chat_daily_cap',100001),('chat_min_chars',0)]:
            code,result=self.client.request('/api/settings',{'group_id':-1001,key:value})
            self.assertEqual(code,400,result);self.assertEqual(result['field'],key)
        self.client.request('/api/admins/create',dict(username='settings',name='Settings',password='disposable-settings-password',permissions=['groups.manage']))
        settings=Client(self.base);settings.login('settings','disposable-settings-password')
        self.assertEqual(settings.request('/api/settings',{'group_id':-1001,'chat_daily_cap':60})[0],403)
        self.assertNotIn('chat_daily_cap',settings.request('/api/snapshot')[1]['groups'][0])
        self.client.request('/api/admins/create',dict(username='points',name='Points',password='disposable-points-password',permissions=['points.configure']))
        points=Client(self.base);points.login('points','disposable-points-password')
        self.assertEqual(points.request('/api/settings',{'group_id':-1001,'chat_daily_cap':60})[0],200)
        self.assertEqual(self.store.snapshot()['groups'][0]['chat_daily_cap'],60)


if __name__=='__main__':unittest.main()
