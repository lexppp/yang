#!/usr/bin/env python3
"""Install in-group verification and real announcement scheduling; preserve configuration and data."""
from datetime import datetime, timezone
from contextlib import closing
import sqlite3
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

SOURCE_REVISION = '6a35a1652924e73e8cf8632b56da447a485656f9'
FILES = {'app.py': '6a8432e34442180359eb8a4e1c229a356f39f4a423037bd674b4c0f2bb865bd9', 'auth.py': '5c6794b53e40be069b6c51c1708a9abc3d29a7fa08ac7f37bbbfeea7faa98835', 'community_runtime.py': '56fb92bdf1efd2bd6b09180a1077fa5c74ca442677ba81ac793c568c54cd9179', 'static/index.html': '68b41340c3f4db532b33ecfdc711cf07cb83cecb04b747f2bf431570d8a5033b', 'static/app.js': 'c755d3abedd1d07f7fac4adf94850445f441f500752838ca9f80416e974f57fc', 'static/access-policy.js': 'b4b2cb0a03228a70eb49e6fa0653477ad3e9fe89ef57151a600ede96a59270c9', 'static/style.css': '747030eccd671ac7e34f212c20cb25892d8a7825c3097b7b89b4e36c25ccc554', 'static/live-announcements.js': 'a23281b822bbced884412b6bd2c4745650778c52607830614869bb8aecfa00ee'}


def fetch_verified(relative, expected):
    url = 'https://raw.githubusercontent.com/lexppp/yang/'+SOURCE_REVISION+'/'+relative
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(2_000_000)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Download checksum mismatch: '+relative+'. No update was installed.')
    return data


def replace_file(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.coinw-update-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if os.geteuid() != 0:
        raise ValueError('Run: sudo python3 update_community.py')
    root = Path('/opt/coinw')
    if set(FILES) != {'app.py','auth.py','community_runtime.py','static/index.html','static/app.js','static/access-policy.js','static/style.css','static/live-announcements.js'} or SOURCE_REVISION == 'TO_BE_PINNED':
        raise ValueError('This updater has not been prepared for release.')
    if any(not (root/name).is_file() for name in ('app.py','auth.py','static/index.html')):
        raise ValueError('Existing CoinW installation was not found.')
    print('Downloading and verifying group verification and announcement update. Account, Token and point settings are preserved.')
    content = {name:fetch_verified(name,checksum) for name,checksum in FILES.items()}
    for name,data in content.items():
        if name.endswith('.py'):
            compile(data,name,'exec')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    backup = Path('/var/lib/coinw/code-backups')/stamp
    backup.mkdir(parents=True, mode=0o700)
    existing={name:(root/name).is_file() for name in FILES}
    for name in FILES:
        if not existing[name]:
            continue
        saved = backup/name
        saved.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        shutil.copy2(root/name,saved)
    was_active = subprocess.run(['systemctl','is-active','--quiet','coinw']).returncode == 0
    subprocess.run(['systemctl','stop','coinw'],check=True)
    try:
        # Consistent private SQLite backup after stopping only our own service.
        config=Path('/etc/coinw/coinw.env')
        if config.is_file():
            for line in config.read_text().splitlines():
                if line.startswith('DATABASE_PATH='):
                    database=Path(line.split('=',1)[1].strip().strip(chr(34)).strip(chr(39)))
                    if not database.is_absolute():
                        database=root/database
                    if database.is_file():
                        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(backup/'database.sqlite3')) as target:
                            source.backup(target)
                            target.execute('PRAGMA journal_mode=DELETE')
                        os.chmod(backup/'database.sqlite3',0o600)
                    break
        for name,data in content.items():
            replace_file(root/name,data)
        subprocess.run(['systemctl','start','coinw'],check=True)
        for _ in range(50):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=1) as response:
                    if json.load(response).get('ok'):
                        break
            except OSError:
                time.sleep(.2)
        else:
            raise ValueError('Updated application failed the local readiness check.')
    except BaseException:
        subprocess.run(['systemctl','stop','coinw'],check=False)
        for name in FILES:
            if existing[name]:
                replace_file(root/name,(backup/name).read_bytes())
            else:
                (root/name).unlink(missing_ok=True)
        if was_active:
            subprocess.run(['systemctl','start','coinw'],check=False)
        raise
    print('Update installed. Core service passed the local health check.')
    print('In dashboard group settings, set verification minutes. New members verify INSIDE the group; timeout removes the prompt and keeps them muted, never kicks.')
    print('Grant bot rights: restrict members, delete messages, invite users; pin messages for pinned announcements. Verification needs a supergroup. Test with a non-admin member.')
    print('Refresh https://panel.chloehub.top and sign in again. Announcement management is now a real server feature. All Telegram text remains English.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nUpdate cancelled; previous code was preserved or restored.',file=sys.stderr)
        sys.exit(130)
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        print('Update did not complete: '+str(error),file=sys.stderr)
        sys.exit(1)
