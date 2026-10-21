import concurrent.futures
import http.cookiejar
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from app import AppServer, Bot, Store, TZ, Telegram, TelegramError, moderation_reason, seed_demo, is_english_text


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.fail = None

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == self.fail:
            raise TelegramError(method + ': mocked error')
        if method == 'getChatMember':
            return {'status':'member'}
        if method == 'createChatInviteLink':
            return {'invite_link': 'https://t.me/+test_invite'}
        return True


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'test.sqlite3')
        self.group = self.store.group({'id':-1001,'title':'Test group'})
        self.user = {'id':1, 'first_name':'Alice', 'username':'alice'}
        self.store.member(-1001, self.user)
        self.api = FakeTelegram()
        self.bot = Bot(self.store, self.api)

    def tearDown(self):
        self.temp.cleanup()

    def message(self, text, **extra):
        return {'message':dict(chat={'id':-1001,'type':'supergroup','title':'Test group'},
                               message_id=10, **{'from':self.user}, text=text, **extra)}

    def test_daily_unique_concurrent(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _:self.store.checkin(-1001,1),range(16)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.store.snapshot()['ranking'][0]['total'],5)

    def test_utc8_midnight_and_monthly_history(self):
        self.assertEqual(self.store.checkin(-1001,1,datetime.fromisoformat('2026-09-30T15:59:59+00:00')),5)
        self.assertIsNone(self.store.checkin(-1001,1,datetime.fromisoformat('2026-09-30T23:00:00+08:00')))
        self.assertEqual(self.store.checkin(-1001,1,datetime.fromisoformat('2026-09-30T16:00:00+00:00')),5)
        self.assertEqual(self.store.snapshot()['ranking'][0]['total'],10)
        with self.store.db() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT period FROM ledger ORDER BY created')],['2026-09-30','2026-10-01'])

    def test_group_isolation(self):
        self.store.group({'id':-1002, 'title':'Second'})
        self.store.checkin(-1001,1)
        self.store.checkin(-1002,1)
        self.assertEqual(len(self.store.snapshot()['ranking']),2)
        self.assertEqual(len(self.store.snapshot(-1002)['ranking']),1)

    def test_invite_reward_deduplicates_and_rejects_self(self):
        self.assertFalse(self.store.referral(-1001,1,1))
        self.assertTrue(self.store.referral(-1001,2,1))
        self.assertFalse(self.store.referral(-1001,2,3))
        self.assertEqual(self.store.snapshot()['ranking'][0]['total'],20)

    def test_coinw_case_insensitive_and_repeat_response(self):
        self.bot.handle(self.message(' CoinW '))
        self.bot.handle(self.message('coinw'))
        messages=[params['text'] for method,params in self.api.calls if method=='sendMessage']
        self.assertIn('earned 5',messages[0])
        self.assertIn('already checked in',messages[1])

    def test_link_and_legacy_ink_reuse_same_link(self):
        self.bot.handle(self.message('/link'))
        self.bot.handle(self.message('/link@TestBot'))
        self.bot.handle(self.message('/ink'))
        self.bot.handle(self.message('/ink@TestBot'))
        self.assertEqual(sum(method=='createChatInviteLink' for method,_ in self.api.calls),1)
        self.assertTrue(next(p for m,p in self.api.calls if m=='createChatInviteLink')['creates_join_request'])
        replies=[p['text'] for method,p in self.api.calls if method=='sendMessage']
        self.assertEqual(len(replies),4)
        self.assertTrue(all('https://t.me/+test_invite' in text for text in replies))

    def test_old_default_welcome_migrates_but_custom_text_is_preserved(self):
        custom_id=-1002
        self.store.group({'id':custom_id,'title':'Custom group'})
        with self.store.db() as db:
            db.execute("UPDATE groups SET welcome=? WHERE id=-1001",('Welcome, {name}, to {group}! Send coinw to check in and /ink to get your invite link.',))
            db.execute('UPDATE groups SET welcome=? WHERE id=?',('My custom welcome: /ink',custom_id))
        reopened=Store(self.store.path)
        with reopened.db() as db:
            values={r['id']:r['welcome'] for r in db.execute('SELECT id,welcome FROM groups')}
        self.assertIn('/link',values[-1001])
        self.assertEqual(values[custom_id],'My custom welcome: /ink')

    def private_message(self, **fields):
        return {'message':dict(chat={'id':1,'type':'private'},message_id=27,**{'from':self.user},**fields)}

    def test_private_photo_returns_largest_photo_id_without_downloading(self):
        self.bot.handle(self.private_message(photo=[
            {'file_id':'large_photo_fixture','width':1280,'height':960},
            {'file_id':'small_photo_fixture','width':90,'height':67}]))
        self.assertEqual(len(self.api.calls),1)
        method,params=self.api.calls[0]
        self.assertEqual(method,'sendMessage')
        self.assertIn('\nlarge_photo_fixture\n',params['text'])
        self.assertNotIn('small_photo_fixture',params['text'])
        self.assertTrue(is_english_text(params['text']))
        self.assertEqual(self.store.snapshot()['ranking'],[])

    def test_private_fileid_reply_and_caption_return_photo_id(self):
        self.bot.handle(self.private_message(text='/fileid@TestBot',reply_to_message={
            'photo':[{'file_id':'replied_photo_fixture','width':800,'height':600}]}))
        self.bot.handle(self.private_message(caption='/fileid',photo=[
            {'file_id':'caption_photo_fixture','width':800,'height':600}]))
        texts=[p['text'] for method,p in self.api.calls if method=='sendMessage']
        self.assertIn('replied_photo_fixture',texts[0])
        self.assertIn('caption_photo_fixture',texts[1])

    def test_fileid_without_photo_or_with_image_document_shows_instructions(self):
        self.bot.handle(self.private_message(text='/fileid'))
        self.bot.handle(self.private_message(document={'mime_type':'image/png','file_id':'document_not_photo_fixture'}))
        texts=[p['text'] for method,p in self.api.calls if method=='sendMessage']
        self.assertEqual(len(texts),2)
        self.assertTrue(all('rather than sending it as a file' in text for text in texts))
        self.assertTrue(all(is_english_text(text) for text in texts))
        self.assertTrue(all('document_not_photo_fixture' not in text for text in texts))

    def test_group_photo_does_not_echo_file_id(self):
        self.bot.handle(self.message('',photo=[{'file_id':'private_media_fixture','width':800,'height':600}]))
        self.assertFalse(any(method=='sendMessage' for method,_ in self.api.calls))

    def test_link_caption_hidden_entity_and_photo_file(self):
        self.assertEqual(moderation_reason(self.group,{'caption':'https://evil.com'}),'包含链接')
        self.assertEqual(moderation_reason(self.group,{'text':'click','entities':[{'type':'text_link','url':'https://evil.com'}]}),'包含链接')
        self.group['block_photos']=1
        self.assertEqual(moderation_reason(self.group,{'document':{'mime_type':'image/png'}}),'包含图片')
        self.assertEqual(moderation_reason(self.group,{'photo':[{}]}),'包含图片')
        self.assertEqual(moderation_reason(self.group,{'text':'稳赚不赔'}),'命中广告关键词')

    def test_edit_moderation(self):
        update=self.message('https://evil.com')
        self.bot.handle({'edited_message':update['message']})
        self.assertTrue(any(m=='deleteMessage' for m,_ in self.api.calls))
        self.assertEqual(self.store.snapshot()['groups'][0]['messages'],0)

    def request_join(self):
        link=self.bot.invite(-1001,self.user)
        new={'id':2,'first_name':'Bob'}
        self.bot.join_request({'from':new,'user_chat_id':222,'invite_link':{'invite_link':link}},self.group)
        with self.store.db() as db:
            row=dict(db.execute('SELECT * FROM challenges').fetchone())
        return new,row

    def test_verification_bound_to_user_and_approval(self):
        user,row=self.request_join()
        self.assertEqual(len(self.store.snapshot()['ranking']),0)
        self.bot.callback({'id':'cb','from':{'id':3},'data':f"v:{row['nonce']}:{row['answer']}"})
        self.assertFalse(any(m=='approveChatJoinRequest' for m,_ in self.api.calls))
        self.bot.callback({'id':'cb2','from':user,'data':f"v:{row['nonce']}:{row['answer']}"})
        self.assertEqual(self.store.snapshot()['ranking'][0]['total'],20)
        self.bot.callback({'id':'cb3','from':user,'data':f"v:{row['nonce']}:{row['answer']}"})
        self.assertEqual(self.store.snapshot()['ranking'][0]['total'],20)

    def test_wrong_answer_and_expiry(self):
        user,row=self.request_join()
        self.bot.callback({'id':'cb','from':user,'data':f"v:{row['nonce']}:999"})
        self.assertTrue(any(m=='declineChatJoinRequest' for m,_ in self.api.calls))
        self.assertEqual(len(self.store.snapshot()['ranking']),0)
        user,row=self.request_join()
        with self.store.db() as db:
            db.execute('UPDATE challenges SET expires=?',(time.time()-1,))
        self.bot.expire()
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM challenges').fetchone()[0],0)
        self.assertEqual(len(self.store.snapshot()['ranking']),0)

    def test_approval_failure_has_no_reward(self):
        self.api.fail='approveChatJoinRequest'
        with self.assertRaises(TelegramError):
            self.bot.approve(-1001,{'id':2},1)
        self.assertEqual(len(self.store.snapshot()['ranking']),0)

    def test_settings_apply_to_future_points(self):
        self.store.checkin(-1001,1,datetime(2026,10,1,tzinfo=TZ))
        with self.store.db() as db:
            db.execute('UPDATE groups SET checkin_points=15 WHERE id=-1001')
        self.store.checkin(-1001,1,datetime(2026,10,2,tzinfo=TZ))
        self.assertEqual(self.store.snapshot()['ranking'][0]['total'],20)

    def test_store_survives_restart(self):
        self.store.checkin(-1001,1)
        self.assertEqual(Store(self.store.path).snapshot()['ranking'][0]['total'],5)

    def test_welcome_photo_and_template(self):
        self.group['welcome_photo']='https://example.com/welcome.png'
        self.bot.welcome(self.group,self.user)
        method,params=self.api.calls[-1]
        self.assertEqual(method,'sendPhoto')
        self.assertIn('Alice',params['caption'])
        self.assertIn('Test group',params['caption'])

    def test_english_only_at_telegram_boundary(self):
        from unittest.mock import patch
        api=Telegram('not-a-real-token')
        with patch('urllib.request.urlopen') as request:
            for params in [{'text':'欢迎'}, {'caption':'请签到'}, {'text':'Hello','reply_markup':{'inline_keyboard':[[{'text':'参加'}]]}}]:
                with self.assertRaises(TelegramError):
                    api.call('sendMessage',**params)
            request.assert_not_called()
        self.assertTrue(is_english_text('Welcome! 👋 100 USDT'))

    def test_english_welcome_with_chinese_display_names(self):
        self.group['title']='官方中文社群'
        self.bot.welcome(self.group,{'first_name':'张三','id':12})
        outgoing=self.api.calls[-1][1]['text']
        self.assertTrue(is_english_text(outgoing))
        self.assertIn('new member',outgoing)
        self.assertIn('the community',outgoing)

    def test_custom_chinese_welcome_is_not_sent(self):
        self.group['welcome']='欢迎加入，请签到'
        with self.assertRaises(TelegramError):
            self.bot.welcome(self.group,self.user)
        self.assertEqual(self.api.calls,[])

    def test_admin_moderation_exemption(self):
        original=self.api.call
        self.api.call=lambda method,**params: {'status':'administrator'} if method=='getChatMember' else original(method,**params)
        self.bot.handle(self.message('https://example.com'))
        self.assertFalse(any(m=='deleteMessage' for m,_ in self.api.calls))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.store=Store(Path(cls.temp.name)/'http.sqlite3')
        seed_demo(cls.store)
        cls.bot=Bot(cls.store,None)
        cls.server=AppServer(('127.0.0.1',0),cls.store,'a-test-password-123',cls.bot,True)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def setUp(self):
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf=''

    def request(self,path,body=None,headers=None):
        headers=dict(headers or {})
        if body is not None:
            headers.update({'Content-Type':'application/json','X-CSRF-Token':self.csrf})
        request=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with self.client.open(request) as r:
                return r.status,r.read(),r.headers
        except urllib.error.HTTPError as e:
            return e.code,e.read(),e.headers

    def login(self):
        status,body,headers=self.request('/api/login',{'username':'admin','password':'a-test-password-123'})
        self.assertEqual(status,200)
        self.csrf=json.loads(body)['csrf']
        self.assertIn('HttpOnly',headers['Set-Cookie'])

    def test_auth_required_and_no_env_leak(self):
        self.assertEqual(self.request('/api/snapshot')[0],401)
        self.assertEqual(self.request('/.env')[0],401)
        self.assertEqual(self.request('/health')[0],200)
        self.assertEqual(self.request('/')[0],200)

    def test_login_bad_password_and_logout(self):
        self.assertEqual(self.request('/api/login',{'username':'admin','password':'bad'})[0],401)
        self.login()
        self.assertEqual(self.request('/api/session')[0],200)
        self.assertEqual(self.request('/api/logout',{})[0],200)
        self.assertEqual(self.request('/api/snapshot')[0],401)

    def test_origin_and_csrf(self):
        self.assertEqual(self.request('/api/login',{'username':'admin','password':'a-test-password-123'},{'Origin':'https://evil.test'})[0],403)
        self.login()
        self.csrf='bad'
        self.assertEqual(self.request('/api/settings',{'group_id':-10010001,'checkin_points':10})[0],403)

    def test_settings_snapshot_and_validation(self):
        self.login()
        self.assertEqual(self.request('/api/settings',{'group_id':-10010001,'checkin_points':12,'welcome':'Hi {name}','verify':True})[0],200)
        status,body,_=self.request('/api/snapshot?group=-10010001')
        self.assertEqual(status,200)
        snapshot=json.loads(body)
        self.assertEqual(next(g for g in snapshot['groups'] if g['id']==-10010001)['checkin_points'],12)
        self.assertEqual(len(snapshot['ranking']),8)
        self.assertEqual(self.request('/api/settings',{'group_id':-10010001,'checkin_points':-2})[0],400)
        self.assertEqual(self.request('/api/settings',{'group_id':-10010001,'verify':'false'})[0],400)
        self.assertEqual(self.request('/api/settings',{'group_id':-10010001,'welcome_photo':'file:///etc/passwd'})[0],400)
        self.assertEqual(self.request('/api/settings',{'group_id':999,'checkin_points':10})[0],400)

    def test_csv_and_revoke(self):
        self.login()
        status,body,headers=self.request('/api/export')
        self.assertEqual(status,200)
        self.assertIn('text/csv',headers['Content-Type'])
        self.assertIn(b'monthly,total',body)
        status,body,_=self.request('/api/snapshot')
        invite=json.loads(body)['invites'][0]
        self.assertEqual(self.request('/api/revoke',{'id':invite['id']})[0],200)
        self.assertEqual(self.request('/api/revoke',{'id':invite['id']})[0],200)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT active FROM invites WHERE id=?',(invite['id'],)).fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
