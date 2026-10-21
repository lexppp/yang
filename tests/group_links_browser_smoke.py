"""Real dashboard flow for auditable missing attribution and reversible group removal."""
import os
from pathlib import Path
import tempfile
import threading
from playwright.sync_api import sync_playwright,expect
from app import AppServer,Bot,Store
from test_group_links import GroupTelegram

os.environ.pop('UI_MODE',None)
with tempfile.TemporaryDirectory() as directory:
    store=Store(Path(directory)/'browser.sqlite3')
    gid=-1001234567890
    group=store.group({'id':gid,'title':'CoinW Test Community','type':'supergroup'})
    api=GroupTelegram();bot=Bot(store,api)
    store.member(gid,{'id':1,'first_name':'Inviter','username':'inviter_fixture'})
    bot.invite(gid,{'id':1})
    bot.community.arrive({**group,'verify':0},{'id':2,'first_name':'Verified member'})
    password='group-browser-owner-fixture'
    server=AppServer(('127.0.0.1',0),store,password,bot,False)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1100})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            base='http://127.0.0.1:'+str(server.server_port)
            def login(target,username,password):
                target.goto(base);target.fill('#username',username);target.fill('#password',password)
                target.locator('#login-form button[type=submit]').click()
                expect(target.locator('.stat-value').first).to_be_visible()
            login(page,'admin',password)
            page.locator('[data-page=invites]').click()
            expect(page.locator('#content')).to_contain_text('来源缺失')
            page.locator('[data-attribute-invite]').click()
            f=page.locator('#attribute-form')
            f.locator('[name=invite_id]').select_option(str(store.snapshot()['invites'][0]['id']))
            f.locator('[name=reason]').fill('Confirmed the inviter and the joining member.')
            f.locator('button[type=submit]').click()
            expect(page.locator('#attribute-dialog')).not_to_be_visible()
            expect(page.locator('#content')).to_contain_text('已计入首次有效邀请')
            assert store.snapshot()['ranking'][0]['total']==20
            page.locator('[data-page=groups]').click()
            page.locator('[data-disconnect-group]').click()
            expect(page.locator('#disconnect-target')).to_contain_text(str(gid))
            assert not page.locator('#disconnect-form [name=leave]').is_checked()
            page.locator('#disconnect-form button[type=submit]').click()
            expect(page.locator('#disconnect-dialog')).not_to_be_visible()
            expect(page.locator('[data-disconnect-group]')).to_have_count(0)
            expect(page.locator('#manage-disconnected')).to_be_visible()
            assert not any(m=='leaveChat' for m,_ in api.calls)
            page.locator('#manage-disconnected').click()
            expect(page.locator('#disconnected-results')).to_contain_text('CoinW Test Community')
            page.locator('[data-restore-group]').click()
            expect(page.locator('[data-disconnect-group]')).to_be_visible()
            page.locator('#disconnected-dialog [data-close-connection]').click()
            # Permission boundaries also hide both actions in a normal administrator's UI.
            page.evaluate("""()=>api('/api/admins/create',{username:'settings_operator',name:'Operator',password:'group-browser-operator-fixture',permissions:['groups.manage','invites.manage']})""")
            bot.community.arrive({**group,'verify':0},{'id':3,'first_name':'Another missing record'})
            reader=browser.new_page();reader.on('pageerror',lambda e:errors.append(str(e)))
            login(reader,'settings_operator','group-browser-operator-fixture')
            reader.locator('[data-page=groups]').click()
            expect(reader.locator('[data-disconnect-group]')).to_have_count(0)
            expect(reader.locator('#manage-disconnected')).not_to_be_visible()
            reader.locator('[data-page=invites]').click()
            expect(reader.locator('[data-attribute-invite]')).to_have_count(0)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            browser.close()
            print('PASS: missing-source diagnosis, explicit/audited attribution + reward, disconnect without leaving, restore with history, administrator permission boundaries, mobile overflow. No real Telegram calls.')
    finally:
        server.shutdown();server.server_close();thread.join()
