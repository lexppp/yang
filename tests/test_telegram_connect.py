"""Data separation must not discard admin credentials or existing live points."""
from pathlib import Path
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from app import Store, seed_demo
from auth import Accounts
from deploy.connect_telegram import prepare_database, read_settings, write_settings, telegram_preflight


class LiveMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root/'demo.sqlite3'
        self.destination = self.root/'live.sqlite3'
        self.store = Store(self.source)
        with self.store.db() as db:
            db.execute("INSERT INTO meta VALUES('mode','demo')")
        self.accounts = Accounts(self.store, 'migration-fixture-password')
        self.owner = self.accounts.authenticate('admin','migration-fixture-password')
        self.accounts.change_password(self.owner, {'current_password':'migration-fixture-password','password':'changed-fixture-password'})
        seed_demo(self.store)

    def tearDown(self):
        self.directory.cleanup()

    def test_new_live_preserves_current_password_and_removes_only_copied_samples(self):
        self.assertTrue(prepare_database(self.source,self.destination))
        live = Store(self.destination)
        self.assertTrue(Accounts(live).authenticate('admin','changed-fixture-password'))
        self.assertFalse(Accounts(live).authenticate('admin','migration-fixture-password'))
        self.assertEqual(live.snapshot()['groups'],[])
        self.assertTrue(self.store.snapshot()['groups'])
        with live.db() as db:
            self.assertTrue(db.execute('SELECT COUNT(*) FROM administrator_audit').fetchone()[0])

    def test_repeat_preserves_live_points(self):
        prepare_database(self.source,self.destination)
        live = Store(self.destination)
        live.group({'id':-2001,'title':'Actual test community'})
        live.checkin(-2001,999)
        self.assertFalse(prepare_database(self.source,self.destination))
        self.assertEqual(live.snapshot()['ranking'][0]['total'],5)

    def test_rejects_demo_destination_without_deleting_records(self):
        self.assertRaises(ValueError, prepare_database,self.source,self.source)
        self.assertTrue(self.store.snapshot()['groups'])

    def test_rejects_unexpected_source(self):
        with self.store.db() as db:
            db.execute("UPDATE meta SET value='live' WHERE key='mode'")
        self.assertRaises(ValueError,prepare_database,self.source,self.destination)
        self.assertFalse(self.destination.exists())

    def test_environment_quote_roundtrip_and_permissions(self):
        path=self.root/'service.env'
        # Existing installer restricts passwords to these safe characters.
        values={'ADMIN_PASSWORD':'a-b_c@123456!','TG_BOT_TOKEN':'1234:local_fixture','DEMO':'0'}
        write_settings(path,values)
        self.assertEqual(read_settings(path),values)
        self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_preflight_checks_identity_and_preserves_existing_webhook(self):
        replies = [io.BytesIO(json.dumps({'ok':True,'result':{'username':'FixtureBot'}}).encode()),
                   io.BytesIO(json.dumps({'ok':True,'result':{'url':'https://old.example/hook'}}).encode())]
        with patch('deploy.connect_telegram.urllib.request.urlopen',side_effect=replies) as call:
            with self.assertRaisesRegex(ValueError,'already has a webhook'):
                telegram_preflight('12345:local_token_fixture')
            self.assertEqual(call.call_count,2)
            self.assertTrue(call.call_args_list[0].args[0].full_url.endswith('/getMe'))
            self.assertTrue(call.call_args_list[1].args[0].full_url.endswith('/getWebhookInfo'))

    def test_preflight_valid_bot_returns_only_identity(self):
        replies = [io.BytesIO(json.dumps({'ok':True,'result':{'username':'FixtureBot'}}).encode()),
                   io.BytesIO(json.dumps({'ok':True,'result':{'url':''}}).encode())]
        with patch('deploy.connect_telegram.urllib.request.urlopen',side_effect=replies):
            self.assertEqual(telegram_preflight('12345:local_token_fixture')['username'],'FixtureBot')
