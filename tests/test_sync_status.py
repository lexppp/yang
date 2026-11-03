from contextlib import closing
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app import AppServer, Store, Bot
import sync_status
from test_accounts import Client, ROOT_PASSWORD, ADMIN_PASSWORD

spec = importlib.util.spec_from_file_location('sync_worker', Path(__file__).resolve().parents[1]/'deploy/x_to_telegram.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)
        self.report=self.path/'public/status.json'
        self.patches=[patch.object(sync_status,'REPORT',self.report),
                      patch.object(sync_status,'timer_status',return_value={'state':'active','enabled':True,'next_run':''}),
                      patch.object(worker,'STATE',self.path/'private'),patch.object(worker,'PUBLIC',self.report)]
        for item in self.patches:item.start()
    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()
    def publish(self):
        worker.STATE.mkdir(mode=0o700)
        with closing(worker.connect(worker.STATE/'forward.sqlite3')) as db:
            now=time.time()
            post={'id':'123','published':now,'text':'中文 日本語 <script>alert(1)</script>',
                  'images':['https://pbs.twimg.com/media/a.jpg'],'url':'https://x.com/CoinWOfficial/status/123'}
            worker.initialize(db, [], now-100)
            worker.cycle(db,[post],lambda *_:{'message_id':321},now)
            with db:
                worker.put(db,'last_check',now)
                worker.put(db,'last_success',now)
                worker.put(db,'PRIVATE_TOKEN','must-not-leak')
        worker.publish_status()
    def test_publication_preserves_private_db_and_excludes_secrets(self):
        self.publish()
        self.assertEqual(self.report.stat().st_mode&0o777,0o644)
        self.assertEqual(self.report.parent.stat().st_mode&0o777,0o755)
        self.assertEqual(worker.STATE.stat().st_mode&0o777,0o700)
        data=sync_status.snapshot()
        self.assertTrue(data['available'])
        self.assertEqual(data['counts']['sent'],1)
        self.assertEqual(data['posts'][0]['deliveries'][0]['message_url'],'https://t.me/coinwofficialchannel/321')
        self.assertNotIn('must-not-leak',self.report.read_text())
    def test_missing_corrupt_and_stale_reports_are_distinct(self):
        self.assertEqual(sync_status.snapshot()['reason'],'not_configured')
        self.report.parent.mkdir()
        self.report.write_text('broken')
        self.assertEqual(sync_status.snapshot()['reason'],'report_unavailable')
        self.report.unlink();self.publish()
        data=json.loads(self.report.read_text());data['updated_at']=time.time()-2000;data['secret']='must-not-leak'
        data['last_error']='https://api.telegram.org/bot-secret';self.report.write_text(json.dumps(data))
        snapshot=sync_status.snapshot()
        self.assertTrue(snapshot['stale'])
        self.assertEqual(snapshot['last_error'],'unknown_error')
        self.assertNotIn('must-not-leak',json.dumps(snapshot))
    def test_publish_failure_and_clear_after_success(self):
        self.publish()
        worker.publish_status('telegram_uncertain_post_123')
        self.assertEqual(sync_status.snapshot()['last_error'],'telegram_uncertain_post_123')
        with closing(worker.connect(worker.STATE/'forward.sqlite3')) as db:
            with db:worker.put(db,'last_error','')
        worker.publish_status()
        self.assertEqual(sync_status.snapshot()['last_error'],'')
    def test_http_auth_permission_revocation_and_read_only(self):
        self.publish()
        store=Store(self.path/'app.sqlite3')
        server=AppServer(('127.0.0.1',0),store,ROOT_PASSWORD,Bot(store,None),True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            root=Client('http://127.0.0.1:'+str(server.server_port))
            self.assertEqual(root.request('/api/sync/status')[0],401)
            root.login()
            self.assertEqual(root.request('/api/sync/status')[1]['counts']['sent'],1)
            code,data=root.request('/api/admins/create',{'username':'syncuser','name':'同步管理员','password':ADMIN_PASSWORD,'permissions':[]})
            self.assertEqual(code,200)
            admin=Client(root.base);admin.login('syncuser',ADMIN_PASSWORD)
            self.assertEqual(admin.request('/api/sync/status')[0],403)
            root.request('/api/admins/update',{'id':data['user']['id'],'permissions':['sync.view']})
            admin.login('syncuser',ADMIN_PASSWORD)
            self.assertEqual(admin.request('/api/sync/status')[0],200)
            self.assertEqual(admin.request('/api/sync/status',{})[0],404)
            root.request('/api/admins/update',{'id':data['user']['id'],'permissions':[]})
            self.assertEqual(admin.request('/api/sync/status')[0],401)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
