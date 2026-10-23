#!/usr/bin/env python3
"""Install persistent welcome and invite message cleanup; preserve configuration and data."""
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

SOURCE_REVISION = 'd14b00669676fdcb1c92aef97add58e9e954f795'
FILES = {'app.py': '0ce0d55b753ecc7ceda91106e39cf760a83ef19ebaabe71a855f982f22f965b8', 'auth.py': '4c9158bf33036408a2c78b706f9c7a7a5ee614d29019005c5e49568f3ea22090', 'community_runtime.py': 'e23451c956334f94b3c3837f8227260a5b0f044f91fbf91b5931a45a2afe6360', 'group_links.py': 'cf90467ab21b6ab86b68ee25986252c504d67aacf93149fb7b2cb6f1a9d15a41', 'static/index.html': '9a316c8232a6c0bf11034586cdbf2d7b0a576101b156513407b2c3eedefd11fe', 'static/app.js': '2d6c1482563e471b45c83ebdd3e36dae48513a20f6b61f5188d9ee9c86f7ff4b', 'static/access-policy.js': 'b1437bb69bec8f3f243f5f5b7955fccca659f3c7357d3353114600841fef1f74', 'static/style.css': '747030eccd671ac7e34f212c20cb25892d8a7825c3097b7b89b4e36c25ccc554', 'static/live-announcements.js': 'a23281b822bbced884412b6bd2c4745650778c52607830614869bb8aecfa00ee'}


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
        raise ValueError('Run: sudo python3 update_cleanup.py')
    root = Path('/opt/coinw')
    if set(FILES) != {'app.py','auth.py','community_runtime.py','group_links.py','static/index.html','static/app.js','static/access-policy.js','static/style.css','static/live-announcements.js'} or SOURCE_REVISION == 'TO_BE_PINNED':
        raise ValueError('This updater has not been prepared for release.')
    if any(not (root/name).is_file() for name in ('app.py','auth.py','static/index.html')):
        raise ValueError('Existing CoinW installation was not found.')
    print('Downloading and verifying welcome and invite message cleanup update. Account, Token and point settings are preserved.')
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
    print('Open a NEW browser tab: https://panel.chloehub.top/?v=message-cleanup and sign in again.')
    print('Group settings: welcome deletion defaults to 4 minutes; personal invite reply deletion defaults to 2 minutes. Set 0 to keep messages.')
    print('New welcome photos/text and /link or /ink replies are scheduled persistently. Restarts resume cleanup; temporary deletion errors are retried and logged.')
    print('Only future messages are scheduled. Old versions did not save historical message IDs; remove those old messages manually.')
    print('Deleting the reply does NOT revoke the invite link or change points. Verification messages keep their separate timeout; announcements are preserved.')
    print('For a real-group check, set both durations to 1 minute and send /link, then test a new member welcome. Check activity logs for any deletion failures.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nUpdate cancelled; previous code was preserved or restored.',file=sys.stderr)
        sys.exit(130)
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        print('Update did not complete: '+str(error),file=sys.stderr)
        sys.exit(1)
