import io
import json
import unittest
from unittest.mock import patch

from app import Store, Telegram
import test_app as core_tests
import test_community_runtime as community_tests


class WelcomeTests(unittest.TestCase):
    setUp=core_tests.CoreTests.setUp
    tearDown=core_tests.CoreTests.tearDown
    def test_chinese_japanese_english_and_mixed_templates_with_original_names(self):
        for template in ('欢迎 {name} 加入 {group}！','{name}さん、{group}へようこそ！','Welcome {name} to {group}!','欢迎 / ようこそ / Welcome, {name}! 💜 {group}'):
            with self.subTest(template=template):
                self.bot.welcome({**self.group,'title':'CoinW日本社区','welcome':template},{'id':3,'first_name':'太郎小明'})
                text=self.api.calls[-1][1]['text']
                self.assertIn('太郎小明',text);self.assertIn('CoinW日本社区',text)
        self.assertEqual(self.api.calls[0][1]['text'],'欢迎 太郎小明 加入 CoinW日本社区！')
    def test_placeholder_like_names_are_not_recursively_replaced(self):
        self.bot.welcome({**self.group,'welcome':'{name} / {group}','title':'日本群'},{'id':3,'first_name':'太郎{group}'})
        self.assertEqual(self.api.calls[-1][1]['text'],'太郎{group} / 日本群')
    def test_photo_and_failed_photo_fallback_keep_language_and_cleanup(self):
        group={**self.group,'welcome':'{name}さん、{group}へようこそ！','welcome_photo':'fixture_photo','title':'日本群'}
        self.bot.community.welcome(group,{'id':3,'first_name':'太郎'})
        self.assertEqual(self.api.calls[-1][1]['caption'],'太郎さん、日本群へようこそ！')
        self.api.fail='sendPhoto'
        self.bot.community.welcome(group,{'id':4,'first_name':'花子'})
        self.assertEqual(self.api.calls[-1][1]['text'],'花子さん、日本群へようこそ！')
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM bot_message_cleanup').fetchone()[0],2)
    def test_real_telegram_transport_accepts_unicode_welcome_without_private_flag_leaking(self):
        # No actual Telegram connection: inspect the exact outgoing HTTP payload at the boundary.
        for method,field in [('sendMessage','text'),('sendPhoto','caption')]:
            params={field:'太郎さん，欢迎加入 CoinW日本群！💜','chat_id':-1001}
            if method=='sendPhoto':params['photo']='fixture_photo'
            with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps({'ok':True,'result':{'message_id':1}}).encode())) as request:
                self.assertEqual(Telegram('disposable-fixture-token').call(method,_welcome=True,**params)['message_id'],1)
                sent=json.loads(request.call_args.args[0].data)
                self.assertEqual(sent[field],params[field]);self.assertNotIn('_welcome',sent)


class WelcomeHttpTests(unittest.TestCase):
    setUp=community_tests.CommunityHttpTests.setUp
    tearDown=community_tests.CommunityHttpTests.tearDown
    def test_three_groups_save_independent_languages_and_survive_restart(self):
        templates={-1001:'{name}さん、{group}へようこそ！',-1002:'欢迎 {name} 加入 {group}！',-1003:'Welcome {name} to {group}!'}
        for gid,text in templates.items():
            self.store.group({'id':gid,'title':'社群'+str(gid)})
            code,result=self.client.request('/api/settings',{'group_id':gid,'welcome':text})
            self.assertEqual(code,200,result)
        restarted=Store(self.store.path)
        saved={g['id']:g['welcome'] for g in restarted.snapshot()['groups']}
        self.assertEqual(saved,templates)
        self.assertEqual(self.client.request('/api/settings',{'group_id':-1001,'spam_mute_minutes':60})[0],200)
        self.assertEqual(self.store.snapshot(-1001)['groups'][0]['welcome'],templates[-1001])
    def test_settings_errors_name_the_field_and_do_not_partially_save(self):
        cases=[('welcome_photo','file:///etc/private'),('welcome_photo','这是图片'),('welcome_photo','https://'),('welcome','字'*901),('spam_strikes',0),('spam_mute_minutes',-1),('checkin_points',-1),('invite_points',None),('verify_minutes',0),('allowed_mentions',[])]
        for field,value in cases:
            with self.subTest(field=field,value_type=type(value).__name__):
                code,result=self.client.request('/api/settings',{'group_id':-1001,'welcome':'ようこそ！',field:value})
                self.assertEqual(code,400,result);self.assertEqual(result['field'],field)
                self.assertNotIn('输入无效，请检查字段',result['error'])
                self.assertNotEqual(self.store.snapshot(-1001)['groups'][0]['welcome'],'ようこそ！')
        code,result=self.client.request('/api/settings',{'group_id':-1001,'welcome':'欢迎 / ようこそ / Welcome 💜','welcome_photo':'fixture_photo'})
        self.assertEqual(code,200,result)
    def test_changing_other_rules_does_not_resubmit_legacy_image_settings(self):
        with self.store.db() as db:db.execute("UPDATE groups SET welcome='ようこそ！',welcome_photo='http://legacy.example/photo.png'")
        code,result=self.client.request('/api/settings',{'group_id':-1001,'spam_strikes':3})
        self.assertEqual(code,200,result)
        row=self.store.snapshot(-1001)['groups'][0]
        self.assertEqual(row['welcome'],'ようこそ！');self.assertEqual(row['welcome_photo'],'http://legacy.example/photo.png')
        self.assertEqual(row['spam_strikes'],3)


if __name__=='__main__':unittest.main()
