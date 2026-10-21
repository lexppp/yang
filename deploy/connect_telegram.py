#!/usr/bin/env python3
"""Enable the existing core Telegram backend without mixing demo business records.

Run interactively on the user's server with sudo. Tokens never enter command lines.
"""
import getpass
from contextlib import closing
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


BUSINESS_TABLES = ('groups', 'members', 'ledger', 'invites', 'referrals', 'challenges', 'logs',
                   'member_arrivals','forum_topics','announcement_plans','announcement_deliveries')


def read_settings(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def telegram_preflight(token):
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise ValueError('Token format is invalid. Copy it from the official @BotFather.')
    def call(method):
        request = urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,
                                         data=b'{}', headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
        except urllib.error.HTTPError:
            raise ValueError('Telegram rejected the token. Check it in @BotFather; no settings were changed.') from None
        except (OSError, ValueError):
            raise ValueError('Cannot reach Telegram securely. Check the server network; no settings were changed.') from None
        if not result.get('ok'):
            raise ValueError('Telegram API check failed; no settings were changed.')
        return result['result']
    identity = call('getMe')
    if call('getWebhookInfo').get('url'):
        raise ValueError('This bot already has a webhook. The old deployment was preserved. Resolve it before connecting here.')
    return identity


def prepare_database(source, destination):
    """Preserve accounts, clear samples only in a NEW live database, never reset live data."""
    source, destination = Path(source), Path(destination)
    if destination.exists():
        with closing(sqlite3.connect(destination)) as existing:
            row = existing.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
            if not row or row[0] != 'live':
                raise ValueError('The destination is not a live database. No records were removed.')
            if not existing.execute("SELECT 1 FROM administrators WHERE role='superadmin'").fetchone():
                raise ValueError('The live database has no owner account. No records were removed.')
        return False
    if not source.is_file():
        raise ValueError('Current account database was not found. Installation stopped.')
    with closing(sqlite3.connect(source)) as original:
        mode = original.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
        if not mode or mode[0] != 'demo':
            raise ValueError('The source is not the expected demo database. Installation stopped.')
        if not original.execute("SELECT 1 FROM administrators WHERE role='superadmin'").fetchone():
            raise ValueError('Source owner account was not found. Installation stopped.')
        fd, temporary = tempfile.mkstemp(prefix='.coinw-live-', suffix='.sqlite3', dir=destination.parent)
        os.close(fd)
        try:
            with closing(sqlite3.connect(temporary)) as fresh:
                original.backup(fresh)
                # The copied source uses WAL. Commit into a single closed file before renaming.
                fresh.execute('PRAGMA journal_mode=DELETE')
                with fresh:
                    existing={r[0] for r in fresh.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    for table in BUSINESS_TABLES:
                        if table in existing:
                            fresh.execute('DELETE FROM '+table)
                    fresh.execute('DELETE FROM meta')
                    fresh.execute("INSERT INTO meta VALUES('mode','live')")
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return True


def write_settings(path, settings):
    fd, temporary = tempfile.mkstemp(prefix='.coinw-env-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            for key, value in settings.items():
                # systemd EnvironmentFile quoting; never interpolate values in a shell.
                escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('`','\\`').replace('$','\\$')
                output.write(f'{key}="{escaped}"\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if os.geteuid() != 0 or not sys.stdin.isatty():
        raise ValueError('Run interactively in the server terminal: sudo python3 connect_telegram.py')
    environment_path = Path('/etc/coinw/coinw.env')
    settings = read_settings(environment_path)
    token = getpass.getpass('Paste your BotFather Token (input is hidden): ').strip()
    identity = telegram_preflight(token)
    print('Verified Telegram bot: @'+identity['username'])
    print('Switching to the real CORE dashboard: groups, verification, sign-in, invitations, announcements, accounts.')
    print('Monthly reset, manual points, Excel invitations, and drawings still need their real backend.')
    source = Path(settings.get('DATABASE_PATH', '/var/lib/coinw/demo.sqlite3'))
    destination = Path('/var/lib/coinw/community.sqlite3')
    backup = Path('/var/lib/coinw/backups')/time.strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    previous = environment_path.read_bytes()
    was_active = subprocess.run(['systemctl','is-active','--quiet','coinw']).returncode == 0
    subprocess.run(['systemctl','stop','coinw'], check=True)
    changed = False
    try:
        shutil.copy2(environment_path, backup/'coinw.env')
        if source.is_file():
            with closing(sqlite3.connect(source)) as old, closing(sqlite3.connect(backup/'accounts-and-data.sqlite3')) as saved:
                old.backup(saved)
                saved.execute('PRAGMA journal_mode=DELETE')
            os.chmod(backup/'accounts-and-data.sqlite3', 0o600)
        created = prepare_database(source, destination)
        account = pwd.getpwnam('coinw')
        os.chown(destination, account.pw_uid, account.pw_gid)
        os.chmod(destination, 0o600)
        settings.update(DEMO='0', UI_MODE='live', TG_BOT_TOKEN=token,
                        DATABASE_PATH=str(destination), HOST='127.0.0.1', PORT='8000', COOKIE_SECURE='1')
        write_settings(environment_path, settings)
        changed = True
        subprocess.run(['systemctl','start','coinw'], check=True)
        for _ in range(50):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=1) as response:
                    if json.load(response).get('ok'):
                        break
            except OSError:
                time.sleep(.2)
        else:
            raise ValueError('The application failed its local readiness check.')
        print('Core live mode started. Your existing administrator accounts and passwords were preserved.')
        print('Live business records start empty.' if created else 'Existing live records were preserved.')
        print('Refresh https://panel.chloehub.top and sign in again. Check the Telegram status there.')
        print('Next: add @'+identity['username']+' to a TEST group as administrator, then send coinw and /link.')
        print('Health is verified locally; actual Telegram polling and real-group actions still need the acceptance checks.')
    except BaseException:
        if changed:
            subprocess.run(['systemctl','stop','coinw'], check=False)
            fd = os.open(environment_path, os.O_WRONLY|os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as restore:
                restore.write(previous)
        if was_active:
            subprocess.run(['systemctl','start','coinw'], check=False)
        raise


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nConnection cancelled. Existing service configuration was preserved or restored.', file=sys.stderr)
        sys.exit(130)
    except (ValueError, OSError, sqlite3.Error, subprocess.CalledProcessError) as error:
        # Only local paths / fixed error text can reach here; API URLs and Token are never printed.
        print('Connection was not completed: '+str(error), file=sys.stderr)
        sys.exit(1)
