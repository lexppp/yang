#!/usr/bin/env python3
"""Update invitation and photo ID commands; preserve server config and business data."""
from datetime import datetime, timezone
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

SOURCE_REVISION = '7f0305c64e0a8ddf801ca4aca4462cc29e83f049'
FILES = {'app.py': '2a75df79ff82e8fa4871ecbe640accf7d09edc999dada8c5c7a844562d633516', 'static/app.js': '6f898d30329dcdd1d9e34bc6edc705ab0b2f9b2a603016862ddc7914317739dc'}


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
        raise ValueError('Run: sudo python3 update_link.py')
    root = Path('/opt/coinw')
    if set(FILES) != {'app.py','static/app.js'} or SOURCE_REVISION == 'TO_BE_PINNED':
        raise ValueError('This updater has not been prepared for release.')
    if any(not (root/name).is_file() for name in FILES):
        raise ValueError('Existing CoinW installation was not found.')
    print('Downloading and verifying /link and /fileid update. Account, Token and point settings are preserved.')
    content = {name:fetch_verified(name,checksum) for name,checksum in FILES.items()}
    compile(content['app.py'], 'app.py', 'exec')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    backup = Path('/var/lib/coinw/code-backups')/stamp
    backup.mkdir(parents=True, mode=0o700)
    for name in FILES:
        saved = backup/name
        saved.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        shutil.copy2(root/name,saved)
    was_active = subprocess.run(['systemctl','is-active','--quiet','coinw']).returncode == 0
    subprocess.run(['systemctl','stop','coinw'],check=True)
    try:
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
            replace_file(root/name,(backup/name).read_bytes())
        if was_active:
            subprocess.run(['systemctl','start','coinw'],check=False)
        raise
    print('Update installed. Core service passed the local health check.')
    print('In your TEST group send /link. /ink remains a compatible alias using the same invite link.')
    print('In PRIVATE chat, send the bot a photo or reply to a photo with /fileid. Copy its ID into Welcome image.')
    print('Refresh https://panel.chloehub.top. Existing custom welcome text is preserved.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nUpdate cancelled; previous code was preserved or restored.',file=sys.stderr)
        sys.exit(130)
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        print('Update did not complete: '+str(error),file=sys.stderr)
        sys.exit(1)
