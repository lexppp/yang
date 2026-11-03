#!/usr/bin/env python3
"""Install independent X forwarding on the existing Ubuntu CoinW server."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path('/opt/coinw-x-forward')
STATE = Path('/var/lib/coinw-x-forward')
UNITS = Path('/etc/systemd/system')
MARKER = '# Managed by CoinW X forward installer v1\n'
SERVICE = MARKER + '''[Unit]
Description=CoinW X to Telegram forwarding
After=network-online.target docker.service coinw.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/coinw-x-forward
EnvironmentFile=/etc/coinw/coinw.env
ExecStart=/usr/bin/python3 /opt/coinw-x-forward/x_to_telegram.py run
TimeoutStartSec=8min
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/coinw-x-forward
CapabilityBoundingSet=
MemoryMax=128M
'''
TIMER = MARKER + '''[Unit]
Description=Check CoinW X posts every 10 minutes

[Timer]
OnBootSec=2min
OnUnitInactiveSec=10min
AccuracySec=10s
Unit=coinw-x-forward.service

[Install]
WantedBy=timers.target
'''


def run(args, **kwargs):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, **kwargs)
    if result.returncode:
        raise ValueError('Command failed: ' + args[0] + '. Existing credentials were not displayed.')
    return result.stdout.decode().strip()


def health(url, expected):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        body = response.read(4096)
    healthy = json.loads(body).get('ok') is True if expected == 'bot' else body.strip() == b'ok'
    if not healthy:
        raise ValueError('Existing service health check failed.')


def atomic(path, body, mode):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), mode)
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def existing_token():
    # Only select the existing bot binding; never copy the complete application environment.
    settings = Path('/etc/coinw/coinw.env')
    info = settings.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('Existing coinw.env must be a root-owned private regular file.')
    for line in settings.read_text().splitlines():
        if line.startswith('TG_BOT_TOKEN='):
            token = line.split('=', 1)[1].strip().strip('"').strip("'")
            if re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
                return token
    raise ValueError('No usable existing Telegram token found; original settings were preserved.')


def install():
    source = Path(__file__).resolve().with_name('x_to_telegram.py')
    body = source.read_bytes()
    compile(body, str(source), 'exec')
    for name in ('coinw-x-forward.service', 'coinw-x-forward.timer'):
        path = UNITS / name
        if path.exists() and not path.read_text().startswith(MARKER):
            raise ValueError('Unmanaged unit with the same name exists; left unchanged.')
    health('http://127.0.0.1:8000/health', 'bot')
    health('http://127.0.0.1:1200/healthz', 'rsshub')
    label = run(['docker', '--host', 'unix:///var/run/docker.sock', 'inspect', '--format',
                 '{{index .Config.Labels "io.coinw.component"}}', 'coinw-rsshub-trial'])
    if label != 'rsshub-trial':
        raise ValueError('Expected owned RSSHub container not found.')
    if not Path('/etc/coinw-rsshub/x.env').is_file():
        raise ValueError('Existing X session is not configured; no credentials changed.')
    environment = dict(os.environ, TG_BOT_TOKEN=existing_token())
    print(run(['/usr/bin/python3', str(source), 'preflight'], env=environment))
    ROOT.mkdir(mode=0o755, exist_ok=True)
    STATE.mkdir(mode=0o700, exist_ok=True)
    if ROOT.is_symlink() or STATE.is_symlink():
        raise ValueError('Install and state paths must be real directories.')
    # Quiesce only this component during upgrades. Never stop the main bot or RSSHub.
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'coinw-x-forward.timer']).returncode == 0
    old = {path: path.read_bytes() if path.exists() else None for path in
           (ROOT / 'x_to_telegram.py', UNITS / 'coinw-x-forward.service', UNITS / 'coinw-x-forward.timer')}
    try:
        for name in ('coinw-x-forward.timer', 'coinw-x-forward.service'):
            if (UNITS / name).exists():
                run(['systemctl', 'stop', name])
        atomic(ROOT / 'x_to_telegram.py', body, 0o644)
        # Initialize once only; upgrades preserve the original activation and dedup state.
        print(run(['/usr/bin/python3', str(ROOT / 'x_to_telegram.py'), 'initialize'], env=environment))
        atomic(UNITS / 'coinw-x-forward.service', SERVICE.encode(), 0o644)
        atomic(UNITS / 'coinw-x-forward.timer', TIMER.encode(), 0o644)
        run(['systemd-analyze', 'verify', str(UNITS / 'coinw-x-forward.service'), str(UNITS / 'coinw-x-forward.timer')])
        run(['systemctl', 'daemon-reload'])
        # Keep the authenticated container intact and enable its restart policy.
        run(['docker', '--host', 'unix:///var/run/docker.sock', 'update', '--restart=unless-stopped', 'coinw-rsshub-trial'])
        run(['systemctl', 'enable', 'docker'])
        run(['systemctl', 'enable', '--now', 'coinw-x-forward.timer'])
        run(['systemctl', 'start', 'coinw-x-forward.service'])
        run(['systemctl', 'is-active', '--quiet', 'coinw-x-forward.timer'])
        health('http://127.0.0.1:8000/health', 'bot')
    except BaseException:
        subprocess.run(['systemctl', 'stop', 'coinw-x-forward.timer'], capture_output=True)
        for path, previous in old.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, previous, 0o644)
        subprocess.run(['systemctl', 'daemon-reload'], capture_output=True)
        if active:
            subprocess.run(['systemctl', 'start', 'coinw-x-forward.timer'], capture_output=True)
        raise
    print('Installed. Source @CoinWOfficial -> @coinwofficialchannel, every 10 minutes.')
    print('Existing bot, application database and X session preserved. Historical posts were not sent.')
    print('Status: sudo python3 /opt/coinw-x-forward/x_to_telegram.py status')


if __name__ == '__main__':
    os.umask(0o077)
    try:
        if os.geteuid() != 0:
            raise ValueError('Run with sudo python3 install_x_forward.py')
        install()
    except (OSError, ValueError, subprocess.SubprocessError):
        # No exception repr: it may contain credentials or token-bearing URLs.
        print('Installation stopped. Check service health, existing bot permission and RSSHub login. No credential values were printed.', file=sys.stderr)
        sys.exit(1)
