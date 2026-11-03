#!/usr/bin/env python3
"""Upgrade the existing dashboard and forwarding status publisher without changing credentials."""
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

SOURCE_REVISION = 'bedf5639c64e22cefb1ba42f12b072412941c182'
FILES = {'app.py': '9183f4ae73270e318ab9707bd9937321157252981968ef8f36927c309c1840a8', 'auth.py': 'a178a8bfe314a8723424dde6d5029f121c3cb2ccd089b75e45351abcfbe22505', 'sync_status.py': '913f47ca43c35daf5e339af6595003a1a34693210c74afc27d8138698f1304af', 'community_runtime.py': '9cf59d84f51068070000876d8f7de85f52f702a589bfe2e4478093e637044d85', 'group_links.py': '31e69bccad9303718213d101c3c0a11ad1fa9133454a6068476bd5b981c28cca', 'moderation.py': '2bfb59db8f9a42a935268230432d2a6f3b15d111350ec143f7e6daa3c8dc09c1', 'engagement.py': '0343d48bfce234b300886406890e1d98045f2a5d70e97b0b7bd97842192c2622', 'static/index.html': '852217d8b5de19253357aa8f1fe95810c9156c659d63b070c50f18d78fd475a3', 'static/app.js': 'e4dc753734d4b557977e6aeb27e0852a98d12df58f06ab84162dd07f9797cbe5', 'static/access.js': 'e0b4657e7f290ce57543a36e6bb95bc14149d7d5da59ef39d98736b0322c6fee', 'static/access-policy.js': '4f520a3286233cc53158470349daebc95004725c6e6b0f8f98e54c7697898887', 'static/access.css': '247bbf01384c228ebb33d1c895e9fddf505175480319356e0c0abcbe6364c212', 'static/style.css': 'cc9a64ebf3e76af253d7cd244e54d9ea01aa16cb80448a4fe48084ec4d899bfb', 'static/live-announcements.js': '6e9a85d404e2de408e201835adc274584541e498c1a51e119c2d3e930b66dfc0', 'static/sync.js': '4948ecc9aec483633ed747739d244feb2da8f026fd1f14a1209a884472f5a58d'}
FORWARD_FILES = {'install_x_forward.py': '6407ef31c761a06eff1c406f8ebdc37e8b352cbfc6ad3e040a5bd2a636aaabde', 'x_to_telegram.py': '0836f87d3107a0f7a55efd9ea8cc8bc49bc3e1505651d86c60701712a4616b87'}
APP_ROOT = Path('/opt/coinw')
BACKUPS = Path('/var/lib/coinw/code-backups')
CONFIG = Path('/etc/coinw/coinw.env')


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
        raise ValueError('Run: sudo python3 update_sync_dashboard.py')
    root = APP_ROOT
    if not FILES or not FORWARD_FILES or SOURCE_REVISION == 'TO_BE_PINNED':
        raise ValueError('This updater has not been prepared for release.')
    if any(not (root/name).is_file() for name in ('app.py','auth.py','static/index.html')):
        raise ValueError('Existing CoinW installation was not found.')
    print('Downloading verified sync dashboard update. Existing accounts, bot, X session and forwarding history are preserved.')
    content = {name:fetch_verified(name,checksum) for name,checksum in FILES.items()}
    for name,data in content.items():
        if name.endswith('.py'):
            compile(data,name,'exec')
    # Upgrade only the independent forwarding component, retaining its activation and history.
    forwarding = {name:fetch_verified('deploy/'+name,checksum) for name,checksum in FORWARD_FILES.items()}
    with tempfile.TemporaryDirectory(prefix='coinw-sync-update-') as directory:
        for name,body in forwarding.items():
            compile(body,name,'exec')
            Path(directory,name).write_bytes(body)
        subprocess.run(['/usr/bin/python3',str(Path(directory)/'install_x_forward.py')],check=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    backup = BACKUPS/stamp
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
        config=CONFIG
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
        with urllib.request.urlopen('http://127.0.0.1:8000/sync.js',timeout=3) as response:
            if response.read(200000) != content['static/sync.js']:
                raise ValueError('Sync page asset readiness check failed.')
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
    print('Sync dashboard installed. Website health check passed.')
    print('Open https://panel.chloehub.top/?v=x-sync#sync in a new tab and sign in again.')
    print('Superadmins can view it immediately; ordinary administrators need the new sync.view permission.')
    print('Only application code was restarted. Existing bot credentials, database and forwarding state were preserved.')



if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nUpdate cancelled; previous code was preserved or restored.',file=sys.stderr)
        sys.exit(130)
    except (OSError,ValueError,subprocess.CalledProcessError):
        print('Update did not complete. Existing site code was preserved or restored; check the preceding fixed status messages. No credential values were printed.',file=sys.stderr)
        sys.exit(1)
