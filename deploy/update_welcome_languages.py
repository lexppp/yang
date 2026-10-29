#!/usr/bin/env python3
"""Enable multilingual welcomes and precise settings errors; preserve configuration and data."""
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

SOURCE_REVISION = 'c3f9fe38abb57dd852c7776b23cdc9f60a9ae933'
FILES = {'app.py': 'f4655c95b772e5f97b99f08bda9382dedd257ed85c3d5cb75165c6abdd94a3c5', 'auth.py': '8fa0e1e0b03cca422821a579d6fe38e4dc4633b09def97537c35ecc89a1d3686', 'community_runtime.py': '57f96befc280e84b8746e061bcac03f92b629a06ca7d8ec80b2915a295452abf', 'group_links.py': '073279ffa588828756f18ee38a256ef6d0832b67e28a74d0d679eef48c03b434', 'moderation.py': 'e8695a07dd51bb925834cb73cbe8babf53c3a368444b49603580d3257297270f', 'static/index.html': '1fe559106368c35ee38ede46c2a052ecf1b1d94f8205db799a86db6ea27b579d', 'static/app.js': 'c6f220ed078b655df6aa3d7136fcd7fc0df17727445365eb21bf0b4b20d193c9', 'static/access-policy.js': '9a51b76bc78a268a4cab2091c9e393f9c783c96e3da6777383a8363148f4451a', 'static/access.js': 'fd26beddf278581c440a70f6b9ff191f31c558e818853c1c61f6b8f14f94b42a', 'static/style.css': '8773f7899da7c9894cbaf86354d03748f609d71f9a9d0ec021c57cae5a5cf481', 'static/live-announcements.js': 'a23281b822bbced884412b6bd2c4745650778c52607830614869bb8aecfa00ee'}


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
        raise ValueError('Run: sudo python3 update_welcome_languages.py')
    root = Path('/opt/coinw')
    if set(FILES) != {'static/style.css', 'static/index.html', 'static/app.js', 'group_links.py', 'app.py', 'community_runtime.py', 'static/access-policy.js', 'auth.py', 'moderation.py', 'static/access.js', 'static/live-announcements.js'} or SOURCE_REVISION == 'TO_BE_PINNED':
        raise ValueError('This updater has not been prepared for release.')
    if any(not (root/name).is_file() for name in ('app.py','auth.py','static/index.html')):
        raise ValueError('Existing CoinW installation was not found.')
    print('Downloading and verifying multilingual welcome and settings validation update. Account, Token and point settings are preserved.')
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
    print('Open a NEW browser tab: https://panel.chloehub.top/?v=welcome-languages and sign in again.')
    print('Each group now supports Chinese, Japanese, English and mixed welcome text. Original member names and group titles are preserved.')
    print('The {name} and {group} placeholders still work. Welcome photos and text fallback support these languages and retain automatic cleanup.')
    print('Only changed settings are submitted; invalid images or numbers are identified beside the exact field.')
    print('Previously saved welcome text, images, accounts, Token, points, links and anti-spam settings are preserved.')
    print('If your previous save failed, paste your unsaved welcome text again and SAVE. Test a new member in each language group.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nUpdate cancelled; previous code was preserved or restored.',file=sys.stderr)
        sys.exit(130)
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        print('Update did not complete: '+str(error),file=sys.stderr)
        sys.exit(1)
