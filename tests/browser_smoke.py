"""Optional browser verification. Requires Playwright and a local Chromium binary."""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
shots = root/'data'
shots.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as directory:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    password = secrets.token_urlsafe(24)
    environment = dict(os.environ, ADMIN_PASSWORD=password, DEMO='1', TG_BOT_TOKEN='',
                       DATABASE_PATH=directory+'/browser.sqlite3', HOST='127.0.0.1', PORT=str(port), COOKIE_SECURE='0')
    process = subprocess.Popen([sys.executable, 'app.py'], cwd=root, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = 'http://127.0.0.1:'+str(port)
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError('Application exited during startup')
            try:
                with urllib.request.urlopen(base+'/health', timeout=1) as response:
                    assert json.load(response)['ok']
                break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Application readiness timed out')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
            page = browser.new_page(viewport={'width':1440,'height':1080}, device_scale_factor=1)
            errors=[]
            page.on('pageerror', lambda error:errors.append(str(error)))
            page.goto(base)
            page.locator('#login').wait_for(state='visible')
            page.fill('#username','admin')
            page.fill('#password',password)
            page.locator('#login-form button').click()
            page.locator('.stat-value').first.wait_for(state='visible')
            assert page.locator('#demo-banner').is_visible()
            assert page.locator('tbody tr').count()==4
            page.screenshot(path=str(shots/'dashboard-desktop.png'),full_page=True)
            page.locator('[data-settings]').first.click()
            page.locator('textarea[name=welcome]').fill('Welcome, {name}, to {group}!')
            page.locator('input[name=checkin_points]').fill('9')
            page.locator('#settings-form button[type=submit]').click()
            page.locator('#settings-dialog').wait_for(state='hidden')
            assert page.locator('#toast').inner_text().startswith('社群设置已保存')
            page.locator('[data-page=ranking]').click()
            assert page.locator('.rank-grid table').count()==2
            page.screenshot(path=str(shots/'rankings-desktop.png'),full_page=True)
            page.locator('[data-page=invites]').click()
            page.fill('#invite-search','chloe')
            assert page.locator('#invite-results tbody tr').count()==4
            page.fill('#invite-search','not-a-member')
            assert page.locator('#invite-results table').count()==0
            page.fill('#invite-search','')
            page.once('dialog',lambda dialog:dialog.accept())
            page.locator('[data-revoke]').first.click()
            page.wait_for_function("() => document.querySelector('#toast').textContent === '链接已撤销'")
            page.select_option('#invite-status','0')
            assert page.locator('#invite-results tbody tr').count()==1
            page.select_option('#group-filter','-10010001')
            page.wait_for_function("() => document.querySelector('.detail-banner h2')?.textContent === 'CoinW Official'")
            page.locator('[data-page=overview]').click()
            assert page.locator('.stats .stat-value').first.inner_text()=='1'
            page.set_viewport_size({'width':390,'height':844})
            page.locator('#toast').wait_for(state='hidden')
            page.screenshot(path=str(shots/'dashboard-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile document overflow'
            page.locator('[data-page=logs]').click()
            page.locator('tbody tr').first.wait_for(state='visible')
            page.locator('#logout-mobile').click()
            page.locator('#login').wait_for(state='visible')
            assert not errors, errors
            browser.close()
            print('Browser smoke passed: login, desktop dashboard, settings, rankings, invite filters/revoke, group filter, mobile layout, logs, logout. 0 page errors.')
            print('Screenshots saved in data/.')
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
