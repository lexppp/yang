"""Real server accounts with the complete, explicitly simulated hosted frontend."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    password = secrets.token_urlsafe(24)
    environment = dict(os.environ, ADMIN_USERNAME='admin', ADMIN_PASSWORD=password,
                       DEMO='1', UI_MODE='hosted-preview', TG_BOT_TOKEN='',
                       DATABASE_PATH=directory+'/hosted.sqlite3', HOST='127.0.0.1',
                       PORT=str(port), COOKIE_SECURE='0')
    process = subprocess.Popen([sys.executable, 'app.py'], cwd=root, env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = 'http://127.0.0.1:'+str(port)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError('Hosted application exited during startup')
            try:
                with urllib.request.urlopen(base+'/health', timeout=1) as response:
                    assert json.load(response)['ok']
                break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Hosted application readiness timed out')
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path='/usr/bin/chromium',
                                        headless=True, args=['--no-sandbox'])
            owner = browser.new_page(viewport={'width':1440,'height':1000})
            errors = []
            owner.on('pageerror', lambda e: errors.append(str(e)))
            response = owner.goto(base)
            assert "connect-src 'self'" in response.headers['content-security-policy']
            expect(owner.locator('#login-form')).to_have_attribute('data-mode', 'login')
            owner.fill('#username', 'admin'); owner.fill('#password', password)
            owner.locator('#login-form button[type=submit]').click()
            expect(owner.locator('.stat-value').first).to_be_visible()
            expect(owner.locator('#demo-banner')).to_contain_text('真实服务器账号登录')
            assert owner.evaluate("localStorage.getItem('coinw-preview-accounts-v1')") is None
            for nav in ['ranking','points','invites','draws','announcements','accounts']:
                owner.locator('[data-page='+nav+']').click()
                expect(owner.locator('#content')).not_to_be_empty()
            owner.evaluate("""()=>previewApi('/api/admins/create',{
              username:'hosted_reader', name:'Read only',password:'disposable-hosted-reader-fixture',
              permissions:['dashboard.view','points.view']})""")
            reader = browser.new_page()
            reader.on('pageerror', lambda e: errors.append(str(e)))
            reader.goto(base)
            reader.fill('#username','hosted_reader')
            reader.fill('#password','disposable-hosted-reader-fixture')
            reader.locator('#login-form button[type=submit]').click()
            expect(reader.locator('.stat-value').first).to_be_visible()
            expect(reader.locator('[data-page=accounts]')).to_be_hidden()
            reader.locator('[data-page=ranking]').click()
            expect(reader.locator('#manual-points')).to_be_hidden()
            denial = reader.evaluate("""async()=>{try{await previewApi('/api/points/adjust',{
              group_id:-10010001,user_id:100,delta:20,reason:'forbidden'});return 0;}
              catch(e){return e.status;}}""")
            assert denial == 403
            owner.evaluate("""async()=>{const {admins}=await previewApi('/api/admins');
              await previewApi('/api/admins/update',{
                id:admins.find(a=>a.username==='hosted_reader').id,active:false});}""")
            reader.evaluate("()=>previewApi('/api/session').catch(()=>{})")
            expect(reader.locator('#login')).to_be_visible()
            owner.reload()
            expect(owner.locator('#app')).to_be_visible()
            owner.locator('#logout').click()
            expect(owner.locator('#login')).to_be_visible()
            assert not errors, errors
            browser.close()
            print('PASS hosted UI: server login, all modules, account creation, read-only rights, denied adjustment, disabled-session logout, reload and logout. No Telegram calls.')
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
