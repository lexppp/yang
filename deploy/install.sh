#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo 'Run with sudo bash deploy/install.sh'
  exit 1
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
python3 -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12 or later required"'
command -v systemctl >/dev/null
test -s frontend/hosted.html

# Ask on the server only. Preserve existing bootstrap settings on repeat runs.
if [[ ! -f /etc/coinw/coinw.env ]]; then
  read -r -s -p 'Set website admin password (at least 12 characters): ' coinw_admin_password
  echo
  read -r -s -p 'Repeat website admin password: ' coinw_password_again
  echo
  if [[ ${#coinw_admin_password} -lt 12 || "$coinw_admin_password" != "$coinw_password_again" ]]; then
    echo 'Passwords must match and contain at least 12 characters.'
    exit 1
  fi
  # EnvironmentFile accepts quoted values. Restrict characters to avoid ambiguities.
  if [[ ! "$coinw_admin_password" =~ ^[A-Za-z0-9_@%+=:,./!?-]+$ ]]; then
    echo 'Use English letters, digits, or these symbols: _ @ % + = : , . / ! ? -'
    exit 1
  fi
fi

if ! id -u coinw >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/coinw --shell /usr/sbin/nologin coinw
fi
install -d -m 0755 /opt/coinw /opt/coinw/static /opt/coinw/frontend
install -d -m 0700 /etc/coinw
install -d -o coinw -g coinw -m 0700 /var/lib/coinw
install -m 0644 app.py auth.py /opt/coinw/
install -m 0644 static/* /opt/coinw/static/
install -m 0644 frontend/hosted.html /opt/coinw/frontend/

if [[ ! -f /etc/coinw/coinw.env ]]; then
  umask 077
  {
    printf 'ADMIN_USERNAME=admin\nADMIN_PASSWORD=%s\n' "$coinw_admin_password"
    printf '%s\n' 'DEMO=1' 'UI_MODE=hosted-preview' 'TG_BOT_TOKEN=' \
      'DATABASE_PATH=/var/lib/coinw/demo.sqlite3' 'HOST=127.0.0.1' 'PORT=8000' 'COOKIE_SECURE=1'
  } > /etc/coinw/coinw.env
fi
unset coinw_admin_password coinw_password_again
install -m 0644 deploy/coinw.service /etc/systemd/system/coinw.service
systemctl daemon-reload
systemctl enable coinw >/dev/null
systemctl restart coinw
python3 - <<'PY'
import json, time, urllib.request
for _ in range(50):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=1) as response:
            assert json.load(response)['ok']
        print('Service ready. Login username: admin. Use your chosen password after HTTPS is configured.')
        print('The application listens on 127.0.0.1:8000. Do not open port 8000 to the Internet.')
        break
    except (OSError, AssertionError):
        time.sleep(.2)
else:
    raise SystemExit('Service did not become ready. Check: sudo journalctl -u coinw -n 30 --no-pager')
PY
