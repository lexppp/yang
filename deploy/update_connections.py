#!/usr/bin/env python3
"""Repair invitation attribution and manage group connections; preserve configuration and data."""
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

SOURCE_REVISION = '9fe7348b84a569271e7d247c10e688a0e266e417'
FILES = {'app.py': 'eda108c589fe5aacb71ee9819debe7bcc1a1000f1cd1179db6df5c2220128158', 'auth.py': '1fd72383d3bdb9ad31136bbdde648f667e638504124c0ef82d837b02b2d1dac7', 'community_runtime.py': 'b79753b857cc7ab9d69c6f2374f291b5c47fbc96e50a48b3d15ad59631c01755', 'group_links.py': 'ef5085fc598e01319f434a87e5a3a7824170ee41ef0fa9b313dcee4dcc4fe7b9', 'static/index.html': '27f9d47e85884648f6fd945e80fd7800fafe88e77addae2b5221e0de7a86ebec', 'static/app.js': 'cf7639c7d23d477def6e91f8c1e99b106d4b6c0dc1f7f750fa0b95415a312b80', 'static/access-policy.js': '62e8c1d2e305f129d68dd687c7da5d568b725b1723a4419cb7a64d48411abff7', 'static/style.css': '747030eccd671ac7e34f212c20cb25892d8a7825c3097b7b89b4e36c25ccc554', 'static/live-announcements.js': 'a23281b822bbced884412b6bd2c4745650778c52607830614869bb8aecfa00ee'}


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
        raise ValueError('Run: sudo python3 update_connections.py')
    root = Path('/opt/coinw')
    if set(FILES) != {'app.py','auth.py','community_runtime.py','group_links.py','static/index.html','static/app.js','static/access-policy.js','static/style.css','static/live-announcements.js'} or SOURCE_REVISION == 'TO_BE_PINNED':
        raise ValueError('This updater has not been prepared for release.')
    if any(not (root/name).is_file() for name in ('app.py','auth.py','static/index.html')):
        raise ValueError('Existing CoinW installation was not found.')
    print('Downloading and verifying invitation attribution and group connection update. Account, Token and point settings are preserved.')
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
    print('Open a NEW browser tab: https://panel.chloehub.top/?v=invite-connections and sign in again.')
    print('In the current supergroup, send /link to generate a new personal link. Join requests are automatically approved; verification stays INSIDE the group.')
    print('Invitations count only after actual joining and verification. Existing members rejoining cannot receive duplicate credit.')
    print('Invitation diagnostics show missing sources. Only confirm manual attribution after checking the real inviter; this grants points and is audited.')
    print('Group list now supports reversible disconnect. History is preserved, announcements pause, and the bot leaves only when explicitly selected.')
    print('Disconnect/restore and manual attribution each require their own administrator permission.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nUpdate cancelled; previous code was preserved or restored.',file=sys.stderr)
        sys.exit(130)
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        print('Update did not complete: '+str(error),file=sys.stderr)
        sys.exit(1)
