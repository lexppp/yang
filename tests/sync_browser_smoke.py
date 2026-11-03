"""Real dashboard + HTTP API; only source/Telegram delivery fixtures are simulated."""
import json
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect
from app import AppServer, Store, Bot
import sync_status

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    report=root/'status.json'
    now=time.time()
    payload={'version':1,'updated_at':now,'enabled_at':now-600,'last_check':now,'last_success':now,
        'last_delivery':now,'next_fetch':now+600,'counts':{'baseline':21,'sent':1,'pending':1},
        'delivery_counts':{'sent':1,'uncertain':1},'last_error':'telegram_uncertain_post_222',
        'posts':[{'id':'111','published':now,'text':'中文 日本語 <script>throw new Error("XSS")</script>',
                  'images':1,'status':'sent','deliveries':[{'part':0,'status':'sent','message_id':123}]},
                 {'id':'222','published':now,'text':'待核对帖子','images':0,'status':'pending',
                  'deliveries':[{'part':0,'status':'uncertain','message_id':None}]}]}
    report.write_text(json.dumps(payload))
    server=AppServer(('127.0.0.1',0),Store(root/'app.sqlite3'),'browser-sync-only-password',None,True)
    server.bot=Bot(server.store,None)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with patch.object(sync_status,'REPORT',report),patch.object(sync_status,'timer_status',return_value={'state':'active','enabled':True,'next_run':''}),sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1080})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto('http://127.0.0.1:'+str(server.server_port)+'/#sync')
            page.locator('#login').wait_for(state='visible')
            page.fill('#username','admin');page.fill('#password','browser-sync-only-password')
            page.locator('#login-form button[type=submit]').click()
            expect(page.locator('#page-title')).to_contain_text('X → Telegram 同步')
            expect(page.locator('#sync-screen')).to_contain_text('发送已暂停核对')
            expect(page.locator('#sync-screen')).to_contain_text('历史基线')
            expect(page.locator('#sync-records')).to_contain_text('<script>throw new Error("XSS")</script>')
            expect(page.locator('#sync-records a[href="https://t.me/coinwofficialchannel/123"]')).to_be_visible()
            page.select_option('#sync-filter','uncertain')
            expect(page.locator('#sync-records tbody tr')).to_have_count(1)
            page.fill('#sync-search','not-present');expect(page.locator('#sync-records')).to_contain_text('没有符合条件')
            page.fill('#sync-search','');page.select_option('#sync-filter','all')
            Path('data').mkdir(exist_ok=True)
            page.screenshot(path='data/sync-desktop.png',full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path='data/sync-mobile.png',full_page=True)
            page.reload();expect(page.locator('#sync-screen')).to_contain_text('发送已暂停核对')
            payload['updated_at']=now-3000;report.write_text(json.dumps(payload))
            page.click('#refresh');expect(page.locator('#sync-screen')).to_contain_text('状态报告已超过20分钟')
            report.unlink();page.click('#refresh');expect(page.locator('#sync-screen')).to_contain_text('同步状态尚未接入')
            page.click('[data-page="overview"]');expect(page.locator('.context-bar')).to_be_visible()
            page.click('#logout-mobile');expect(page.locator('#login')).to_be_visible()
            assert not errors,errors
            browser.close()
            print('Sync browser passed: desktop/mobile, deep link, empty groups, history/filter/search, XSS escaping, message links, stale/missing report, navigation/logout; 0 page errors.')
    finally:
        server.shutdown();server.server_close();thread.join()
