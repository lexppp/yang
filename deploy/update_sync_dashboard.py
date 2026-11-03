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

SOURCE_REVISION = 'TO_BE_PINNED'
FILES = {}
FORWARD_FILES = {}
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
