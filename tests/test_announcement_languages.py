from datetime import datetime
import io
import json
import unittest
from unittest.mock import patch

from app import Bot, Store, Telegram
from community_runtime import TZ
import test_community_runtime as community_tests
from test_accounts import Client


class AnnouncementLanguageTests(unittest.TestCase):
    setUp=community_tests.CommunityTests.setUp
    tearDown=community_tests.CommunityTests.tearDown
    plan=community_tests.CommunityTests.plan
    def test_multiple_languages_send_as_text_or_photo_without_confirmation(self):
        for index,text in enumerate(('中文社区邀请活动，欢迎参加！','日本語のお知らせです。ぜひご参加ください！',
                                    'Welcome to the community!','Объявление сообщества','إعلان المجتمع','한국어 공지입니다',
                                    '🎁 中文 / 日本語 / English 💜')):
            with self.subTest(text=text):
                image='fixture_photo_id' if index%2 else ''
                body=self.plan();body.update(body=text,image=image,topic_id=24094,pin=True,image_english_confirmed=False)
                result=self.bot.community.save_announcement(body,{'username':'admin'})
                delivery=next(row for row in self.store.snapshot()['announcement_deliveries'] if row['plan_id']==result['id'])
                self.assertEqual(delivery['status'],'sent');self.assertEqual(delivery['pin_status'],'pinned')
                method='sendPhoto' if image else 'sendMessage'
                sent=[p for m,p in self.api.calls if m==method][-1]
                self.assertEqual(sent['caption' if image else 'text'],text)
                self.assertEqual(sent['message_thread_id'],24094)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM bot_message_cleanup').fetchone()[0],0)
    def test_real_http_payload_supports_multilingual_announcement_without_private_option(self):
        self.bot.api=Telegram('disposable-fixture-token')
        sent=[]
        def transport(request,**kwargs):
            sent.append((request.full_url.rsplit('/',1)[-1],json.loads(request.data)))
            result={'message_id':len(sent)} if sent[-1][0] in ('sendPhoto','sendMessage') else True
            return io.BytesIO(json.dumps({'ok':True,'result':result}).encode())
        with patch('urllib.request.urlopen',side_effect=transport):
            for image in ('','fixture_photo_id'):
                body=self.plan();body.update(body='🎁 社群邀请活动！日本語でも配信できます。',image=image,pin=True,topic_id=123)
                result=self.bot.community.save_announcement(body,{'username':'admin'})
                delivery=next(r for r in self.store.snapshot()['announcement_deliveries'] if r['plan_id']==result['id'])
                self.assertEqual(delivery['status'],'sent')
        self.assertEqual([m for m,_ in sent],['sendMessage','pinChatMessage','sendPhoto','pinChatMessage'])
        for method,payload in sent:
            self.assertNotIn('_announcement',payload);self.assertNotIn('_welcome',payload)
            if method in ('sendMessage','sendPhoto'):
                self.assertIn('日本語',payload['text' if method=='sendMessage' else 'caption'])
                self.assertEqual(payload['message_thread_id'],123)
    def test_chinese_draft_edit_and_one_time_schedule_survive_restart_without_duplicates(self):
        body=self.plan();body.update(body='中文公告草稿',activate=False)
        pid=self.bot.community.save_announcement(body,{'username':'admin'})['id']
        self.assertEqual(self.api.calls,[])
        body.update(id=pid,body='修改后的中文公告 💜',mode='once',once_at='2030-01-01T10:00:00+08:00',activate=True)
        self.bot.community.save_announcement(body,{'username':'admin'})
        restarted=Bot(Store(self.store.path),self.api)
        restarted.community.process_announcements(datetime(2030,1,1,10,0,1,tzinfo=TZ))
        restarted.community.process_announcements(datetime(2030,1,1,10,0,2,tzinfo=TZ))
        messages=[p['text'] for m,p in self.api.calls if m=='sendMessage']
        self.assertEqual(messages,['修改后的中文公告 💜'])
    def test_japanese_daily_photo_schedule_survives_restart_and_keeps_pin_topic(self):
        body=self.plan();body.update(body='毎日のコミュニティニュース 💜',image='fixture_photo',pin=True,topic_id=456,
                                     mode='daily',start_date='2030-01-01',times=['09:00','15:00'])
        self.bot.community.save_announcement(body,{'username':'admin'})
        self.bot.community.process_announcements(datetime(2030,1,1,9,0,1,tzinfo=TZ))
        restarted=Bot(Store(self.store.path),self.api)
        restarted.community.process_announcements(datetime(2030,1,1,9,0,2,tzinfo=TZ))
        restarted.community.process_announcements(datetime(2030,1,1,15,0,1,tzinfo=TZ))
        photos=[p for m,p in self.api.calls if m=='sendPhoto']
        self.assertEqual(len(photos),2)
        self.assertTrue(all(p['caption']==body['body'] and p['message_thread_id']==456 for p in photos))
        self.assertEqual(sum(m=='pinChatMessage' for m,_ in self.api.calls),2)
    def test_language_does_not_bypass_content_lengths_and_invalid_image_validation(self):
        for changes in (dict(body='   '),dict(body='中'*4097),dict(body='日'*1025,image='fixture_photo'),dict(image='file:///etc/private'),dict(image='图片说明')):
            body=self.plan();body.update(changes)
            with self.subTest(changes=list(changes)):
                self.assertRaises(ValueError,self.bot.community.save_announcement,body,{'username':'admin'})
        self.assertEqual(self.api.calls,[])
        body=self.plan();body.update(body='日'*1024,image='fixture_photo')
        self.bot.community.save_announcement(body,{'username':'admin'})
        self.assertEqual(len(next(p['caption'] for m,p in self.api.calls if m=='sendPhoto')),1024)


class AnnouncementLanguageHttpTests(unittest.TestCase):
    setUp=community_tests.CommunityHttpTests.setUp
    tearDown=community_tests.CommunityHttpTests.tearDown
    def test_multilingual_http_save_and_read_only_admin_cannot_publish(self):
        body=dict(group_id=-1001,title='中文邀请活动',body='🎁 社群邀友有奖活动开启！ようこそ！',image='fixture_photo',topic_id=123,pin=True,mode='now',activate=True)
        code,result=self.client.request('/api/announcements/save',body)
        self.assertEqual(code,200,result)
        row=self.client.request('/api/snapshot')[1]['announcements'][0]
        self.assertEqual(row['body'],body['body']);self.assertEqual(row['title'],body['title'])
        self.assertEqual(self.client.request('/api/snapshot')[1]['announcement_deliveries'][0]['status'],'sent')
        self.client.request('/api/admins/create',dict(username='reader',name='Reader',password='disposable-reader-password',permissions=['announcements.view']))
        reader=Client(self.base);self.assertEqual(reader.login('reader','disposable-reader-password')[0],200)
        self.assertEqual(reader.request('/api/announcements/save',body)[0],403)


if __name__=='__main__':unittest.main()
