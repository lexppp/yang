#!/usr/bin/env python3
"""Install qualified-message rewards and /Rankings; preserve accounts and business data."""
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

SOURCE_REVISION = '5610470539d054d0edcbccec75a06453274b648e'
FILES = {'app.py': '95f021dc7d7eab98264199027aced52a7f4b1b14ad029296f200afe3487cb6b5', 'auth.py': '3cb8b830b4cd8f7ccf156012bb4f2897ad93a0a5bc354b7f2db6bd0263e86ff9', 'community_runtime.py': '9cf59d84f51068070000876d8f7de85f52f702a589bfe2e4478093e637044d85', 'group_links.py': '31e69bccad9303718213d101c3c0a11ad1fa9133454a6068476bd5b981c28cca', 'moderation.py': '2bfb59db8f9a42a935268230432d2a6f3b15d111350ec143f7e6daa3c8dc09c1', 'engagement.py': '0343d48bfce234b300886406890e1d98045f2a5d70e97b0b7bd97842192c2622', 'static/index.html': '2dab751860f61fc92aff664f78361da80ba6fb7ba6d81e3c9c0c6aba74af6bc3', 'static/app.js': '9bd6ba0e63c3a356dba3f84027e8f51aff3b5cd895b84eb132af327f8a059537', 'static/access-policy.js': '2176ad19f2bf39238a8b0fa402b1d7bd8f936a4a23465ab3190fbaed59c7dddf', 'static/access.js': 'a2bb1aae11101903e84e997fbcf1b075ab3560cf31065fa9fade22ddc054f485', 'static/style.css': '8773f7899da7c9894cbaf86354d03748f609d71f9a9d0ec021c57cae5a5cf481', 'static/live-announcements.js': '6e9a85d404e2de408e201835adc274584541e498c1a51e119c2d3e930b66dfc0'}


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
        raise ValueError('Run: sudo python3 update_chat_rewards.py')
    root = Path('/opt/coinw')
    if set(FILES) != {'community_runtime.py', 'static/live-announcements.js', 'app.py', 'static/access.js', 'static/style.css', 'static/index.html', 'auth.py', 'group_links.py', 'engagement.py', 'static/access-policy.js', 'static/app.js', 'moderation.py'} or SOURCE_REVISION == 'TO_BE_PINNED':
        raise ValueError('This updater has not been prepared for release.')
    if any(not (root/name).is_file() for name in ('app.py','auth.py','static/index.html')):
        raise ValueError('Existing CoinW installation was not found.')
    print('Downloading and verifying qualified-message rewards and group rankings update. Account, Token and point settings are preserved.')
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
    print('Open a NEW browser tab: https://panel.chloehub.top/?v=chat-rewards and sign in again.')
    print('Default chat rewards: at least 10 letters/numbers, +5 points per qualifying message, at most 50 chat points per member per group per UTC+8 calendar day.')
    print('Check-in and invitation rewards are separate. Group settings > point rules can enable/disable or adjust chat rewards; points.configure permission is required.')
    print('Send /Rankings in a connected group to see its all-time Top 10, your own rank and today chat points. Commands are case-insensitive and support @botname suffixes.')
    print('Whitespace padding, emoji-only text, duplicate posts, forwarded/bot content and ads do not earn rewards. Flooding and edits to ads can reverse chat points with an audit log.')
    print('No per-message reward reply is sent. Totals update in the dashboard and /Rankings. Old chat history is not scanned or backfilled.')
    print('Existing accounts, Token, points, welcome messages, announcement plans and moderation settings are preserved. First test in a TEST group without flooding.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nUpdate cancelled; previous code was preserved or restored.',file=sys.stderr)
        sys.exit(130)
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        print('Update did not complete: '+str(error),file=sys.stderr)
        sys.exit(1)
