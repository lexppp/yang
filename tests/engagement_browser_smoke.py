"""Native settings and dashboard totals exercise the same bot reward/ranking backend."""
import os
from pathlib import Path
import tempfile
import threading
import time
from playwright.sync_api import sync_playwright,expect
from app import AppServer,Bot,Store
from test_app import FakeTelegram

os.environ.pop('UI_MODE',None)
with tempfile.TemporaryDirectory() as directory:
    store=Store(Path(directory)/'engagement.sqlite3')
    chat={'id':-1001,'title':'CoinW Test Community','type':'supergroup'};store.group(chat)
    api=FakeTelegram();bot=Bot(store,api);password='disposable-engagement-browser-password'
    server=AppServer(('127.0.0.1',0),store,password,bot,False)
    server.accounts.create(server.accounts.user(1),dict(username='settings',name='Settings operator',password='disposable-settings-password',permissions=['groups.manage']))
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    def message(mid,text):return {'message':{'chat':chat,'from':{'id':1,'first_name':'小明'},'message_id':mid,'date':int(time.time())-120+mid*20,'text':text}}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1100})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto('http://127.0.0.1:'+str(server.server_port))
            page.fill('#username','admin');page.fill('#password',password);page.locator('#login-form button[type=submit]').click()
            expect(page.locator('.stat-value').first).to_be_visible()
            page.locator('[data-settings]').first.click()
            f=page.locator('#settings-form')
            expect(f.locator('[name=chat_rewards_enabled]')).to_be_checked()
            for name,value in [('chat_min_chars','10'),('chat_reward_points','5'),('chat_daily_cap','50')]:expect(f.locator(f'[name={name}]')).to_have_value(value)
            expect(f).to_contain_text('/Rankings')
            f.locator('[name=chat_rewards_enabled]').uncheck();f.locator('[type=submit]').click()
            expect(page.locator('#settings-dialog')).not_to_be_visible();expect(page.locator('#refresh')).to_be_enabled()
            bot.handle(message(1,'这是一条已经满足十个字符的有效发言'))
            assert store.snapshot()['ranking']==[]
            page.locator('[data-settings]').first.click();f.locator('[name=chat_rewards_enabled]').check();f.locator('[type=submit]').click()
            expect(page.locator('#settings-dialog')).not_to_be_visible();expect(page.locator('#refresh')).to_be_enabled()
            bot.handle(message(2,'这是开启积分之后的一条新社群观点'))
            bot.handle(message(3,'/Rankings'))
            assert any(m=='sendMessage' and 'Your rank: #1' in params['text'] and '5 points' in params['text'] for m,params in api.calls)
            page.locator('#refresh').click();expect(page.locator('#refresh')).to_be_enabled()
            page.locator('[data-page=ranking]').click()
            expect(page.locator('#content')).to_contain_text('小明')
            assert store.snapshot()['ranking'][0]['total']==5
            page.locator('#group-filter').select_option('-1001');expect(page.locator('#refresh')).to_be_enabled()
            page.locator('[data-settings]').first.click();page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('#close-settings').click();page.set_viewport_size({'width':1440,'height':1100})
            page.locator('#logout').click();page.fill('#username','settings');page.fill('#password','disposable-settings-password');page.locator('#login-form button[type=submit]').click()
            page.locator('[data-page=groups]').click();page.locator('[data-settings]').first.click()
            for name in ('chat_rewards_enabled','chat_min_chars','chat_reward_points','chat_daily_cap'):expect(f.locator(f'[name={name}]')).to_be_disabled()
            assert not errors,errors
            browser.close()
            print('PASS: default 10 chars / 5 points / daily 50, enable/disable save, bot credit + /Rankings, dashboard totals, permissions and mobile layout. No real Telegram calls.')
    finally:
        server.shutdown();server.server_close();worker.join()
