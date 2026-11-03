from contextlib import ExitStack
import hashlib
import importlib.util
import io
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('sync_updater',Path(__file__).resolve().parents[1]/'deploy/update_sync_dashboard.py')
updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)


class UpdaterTests(unittest.TestCase):
    def test_download_rejects_checksum_mismatch(self):
        with patch.object(updater.urllib.request,'urlopen',return_value=io.BytesIO(b'corrupt')):
            with self.assertRaises(ValueError):updater.fetch_verified('app.py',hashlib.sha256(b'expected').hexdigest())

    def exercise(self, fail=False):
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            root=Path(directory)/'app';root.mkdir();(root/'static').mkdir()
            old={'app.py':b'# original app','auth.py':b'# original auth','static/index.html':b'old html','static/sync.js':b'old asset'}
            new={**old,'app.py':b'# new app','static/sync.js':b'new sync asset','sync_status.py':b'# new reader'}
            for name,body in old.items():(root/name).write_bytes(body)
            database=Path(directory)/'business.sqlite3'
            with sqlite3.connect(database) as db:db.execute('CREATE TABLE original(value)');db.execute("INSERT INTO original VALUES('keep-me')")
            config=Path(directory)/'coinw.env';config.write_text('DATABASE_PATH='+str(database)+'\nTG_BOT_TOKEN=private-placeholder\n')
            original_config=config.read_bytes()
            calls=[]
            def command(args,**kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args,0)
            def fetch(name,checksum):
                return b'# forwarding installer fixture' if name.startswith('deploy/') else new[name]
            def request(url,**kwargs):
                if url.endswith('/sync.js'):
                    return io.BytesIO(b'wrong asset' if fail else new['static/sync.js'])
                return io.BytesIO(b'{"ok":true}')
            for name,value in [('APP_ROOT',root),('BACKUPS',Path(directory)/'backups'),('CONFIG',config),
                               ('SOURCE_REVISION','a'*40),('FILES',{name:'hash' for name in new}),
                               ('FORWARD_FILES',{'install_x_forward.py':'hash'})]:
                stack.enter_context(patch.object(updater,name,value))
            stack.enter_context(patch.object(updater,'fetch_verified',side_effect=fetch))
            stack.enter_context(patch.object(updater.os,'geteuid',return_value=0))
            stack.enter_context(patch.object(updater.subprocess,'run',side_effect=command))
            stack.enter_context(patch.object(updater.urllib.request,'urlopen',side_effect=request))
            if fail:
                with self.assertRaises(ValueError):updater.main()
                for name,body in old.items():self.assertEqual((root/name).read_bytes(),body)
                self.assertFalse((root/'sync_status.py').exists())
            else:
                updater.main()
                for name,body in new.items():self.assertEqual((root/name).read_bytes(),body)
            self.assertEqual(config.read_bytes(),original_config)
            with sqlite3.connect(database) as db:self.assertEqual(db.execute('SELECT * FROM original').fetchall(),[('keep-me',)])
            backups=list((Path(directory)/'backups').glob('*/database.sqlite3'))
            self.assertEqual(len(backups),1)
            with sqlite3.connect(backups[0]) as db:self.assertEqual(db.execute('SELECT * FROM original').fetchall(),[('keep-me',)])
    def test_upgrade_keeps_credentials_database_and_consistent_backup(self):self.exercise()
    def test_readiness_failure_restores_old_application_files(self):self.exercise(fail=True)


if __name__=='__main__':unittest.main()
