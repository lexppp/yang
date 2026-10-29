"""Real settings UI: independent multilingual groups, field errors and changed-only requests."""
import os
from pathlib import Path
import tempfile
import threading
from playwright.sync_api import sync_playwright,expect
from app import AppServer,Bot,Store
from test_app import FakeTelegram

os.environ.pop('UI_MODE',None)
with tempfile.TemporaryDirectory() as directory:
    store=Store(Path(directory)/'welcome.sqlite3')
    groups=[(-1001,'CoinW日本コミュニティー','{name}さん、{group}へようこそ！'),
            (-1002,'CoinW中文社区','欢迎 {name} 加入 {group}！'),
            (-1003,'CoinW Global','Welcome {name} to {group}! 💜')]
    for gid,title,_ in groups:store.group({'id':gid,'title':title})
    bot=Bot(store,FakeTelegram());password='disposable-welcome-browser-password'
    server=AppServer(('127.0.0.1',0),store,password,bot,False)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1100})
            errors=[];posts=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:posts.append(request.post_data_json) if request.url.endswith('/api/settings') and request.method=='POST' else None)
            page.goto('http://127.0.0.1:'+str(server.server_port))
            page.fill('#username','admin');page.fill('#password',password)
            page.locator('#login-form button[type=submit]').click()
            expect(page.locator('.stat-value').first).to_be_visible()
            for gid,title,text in groups:
                page.locator(f'[data-settings="{gid}"]').first.click()
                expect(page.locator('#settings-title')).to_have_text(title)
                expect(page.locator('#settings-dialog')).to_contain_text('支持中文、日文、英文')
                page.locator('#settings-form [name=welcome]').fill(text)
                page.locator('#settings-form button[type=submit]').click()
                expect(page.locator('#settings-dialog')).not_to_be_visible()
                expect(page.locator('#refresh')).to_be_enabled()
                assert posts[-1]=={'group_id':gid,'welcome':text},posts[-1]
                assert next(g for g in store.snapshot()['groups'] if g['id']==gid)['welcome']==text
            # Other modules can be changed without resubmitting a group's language or image.
            page.locator('[data-settings="-1001"]').first.click()
            expect(page.locator('#settings-form [name=welcome]')).to_have_value(groups[0][2])
            page.locator('#settings-form [name=spam_strikes]').fill('3')
            page.locator('#settings-form button[type=submit]').click()
            expect(page.locator('#settings-dialog')).not_to_be_visible()
            expect(page.locator('#refresh')).to_be_enabled()
            assert posts[-1]=={'group_id':-1001,'spam_strikes':3},posts[-1]
            page.locator('[data-settings="-1001"]').first.click()
            page.locator('#settings-form [name=welcome_photo]').fill('file:///not-a-public-image')
            page.locator('#settings-form button[type=submit]').click()
            expect(page.locator('#settings-error-welcome_photo')).to_contain_text('HTTPS图片地址')
            expect(page.locator('#settings-form [name=welcome_photo]')).to_have_attribute('aria-invalid','true')
            assert page.locator('#settings-form [name=welcome_photo]').evaluate('(el)=>el===document.activeElement')
            expect(page.locator('#settings-dialog')).to_be_visible()
            page.locator('#settings-form [name=welcome_photo]').fill('fixture_photo_id')
            page.locator('#settings-form button[type=submit]').click()
            expect(page.locator('#settings-dialog')).not_to_be_visible()
            expect(page.locator('#refresh')).to_be_enabled()
            assert next(g for g in store.snapshot()['groups'] if g['id']==-1001)['welcome']==groups[0][2]
            # Save without edits is a no-op, including after the last request failed then recovered.
            page.locator('[data-settings="-1001"]').first.click();count=len(posts)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('#settings-form button[type=submit]').click()
            expect(page.locator('#settings-dialog')).not_to_be_visible()
            assert len(posts)==count
            assert not errors,errors
            browser.close()
            print('PASS: independent Chinese/Japanese/English welcomes, changed-only saves, field-specific error/focus and retry, no-op save, mobile layout. No real Telegram calls.')
    finally:
        server.shutdown();server.server_close();worker.join()
