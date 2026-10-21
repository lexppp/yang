"""Exercise the real native dashboard with mocked Telegram transport, never real groups."""
import os
from pathlib import Path
import tempfile
import threading
from playwright.sync_api import sync_playwright, expect
from app import AppServer, Bot, Store
from test_app import FakeTelegram

os.environ.pop('UI_MODE',None)
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    store=Store(Path(directory)/'browser.sqlite3')
    store.group({'id':-1001,'title':'CoinW Test Community'})
    api=FakeTelegram();bot=Bot(store,api)
    bot.community.observe_topic({'chat':{'id':-1001},'message_thread_id':123,'forum_topic_created':{'name':'Community News'}})
    password='disposable-community-browser-fixture'
    server=AppServer(('127.0.0.1',0),store,password,bot,False)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1100})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:'+str(server.server_port))
            page.fill('#username','admin');page.fill('#password',password)
            page.locator('#login-form button[type=submit]').click()
            expect(page.locator('.stat-value').first).to_be_visible()
            page.locator('[data-settings]').first.click()
            expect(page.locator('#settings-dialog')).to_contain_text('超时自动删除验证消息')
            page.locator('#settings-form [name=verify_minutes]').fill('2')
            page.locator('#settings-form button[type=submit]').click()
            expect(page.locator('#settings-dialog')).not_to_be_visible()
            assert store.snapshot()['groups'][0]['verify_minutes']==2
            page.locator('[data-page=announcements]').click()
            f=page.locator('#live-announcement-form')
            expect(f).to_be_visible()
            f.locator('[name=title]').fill('Community news')
            f.locator('[name=body]').fill('Hello CoinW community! This is a scheduled community update.')
            f.locator('[name=topic_id]').fill('123')
            f.locator('[name=image]').fill('photo_id_test_fixture')
            f.locator('[name=image_english_confirmed]').check()
            f.locator('[data-activate="1"]').click()
            expect(page.locator('#content')).to_contain_text('已发送')
            assert any(m=='sendPhoto' and params['message_thread_id']==123 for m,params in api.calls)
            assert any(m=='pinChatMessage' for m,_ in api.calls)
            # Daily multi-slot plans are persisted; browser code does not run the scheduler.
            f.locator('[name=title]').fill('Daily reminders')
            f.locator('[name=body]').fill('Check in with coinw and earn your daily points!')
            f.locator('[name=mode]').select_option('daily')
            f.locator('[name=start_date]').fill('2030-01-01')
            page.locator('#ann-frequency').select_option('2')
            f.locator('[data-activate="1"]').click()
            expect(page.locator('#content')).to_contain_text('每天 2 次')
            page.locator('[data-ann-action=pause]').first.click()
            expect(page.locator('#content')).to_contain_text('已暂停')
            # Backend English guard is visible to the operator.
            f.locator('[name=title]').fill('Language check')
            f.locator('[name=body]').fill('欢迎参加活动')
            f.locator('[data-activate="1"]').click()
            expect(page.locator('#ann-error')).to_contain_text('must use English')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            output=root/'data/community-update';output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/'announcement-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1100})
            page.screenshot(path=str(output/'announcement-desktop.png'),full_page=True)
            assert not errors,errors
            browser.close()
            print('PASS: real login, verification timeout UI/save, announcement nav/form, photo + topic + pin API calls, multi-slot schedule, pause, English validation, mobile overflow. No real Telegram calls.')
    finally:
        server.shutdown();server.server_close();worker.join()
