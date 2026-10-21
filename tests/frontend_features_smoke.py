"""Verify the standalone UI without any Telegram or backend API access."""
import base64
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import zipfile
import xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright, expect
from ui_auth import preview_login, renew_preview_session

root=Path(__file__).resolve().parents[1]
output=root/'data/frontend-v2'
output.mkdir(parents=True,exist_ok=True)
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(root/'frontend')))
threading.Thread(target=server.serve_forever,daemon=True).start()
base='http://127.0.0.1:'+str(server.server_port)
try:
 with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1100},accept_downloads=True)
    page.clock.install(time=datetime(2026,10,21,7,0,tzinfo=timezone.utc))
    errors=[];requests=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:requests.append(r.url))
    page.goto(base+'/index.html')
    preview_login(page)
    page.locator('.stat-value').first.wait_for()
    page.select_option('#group-filter','-10010001')
    expect(page.locator('.detail-banner h2')).to_have_text('CoinW Official')
    snapshot=lambda:page.evaluate("() => previewApi('/api/snapshot?group=-10010001')")
    before=snapshot()
    assert before['month']=='2026-10'
    actor=lambda:next(m['monthly'] for m in snapshot()['ranking'] if m['user_id']==999)
    assert actor()==120
    page.locator('[data-page=ranking]').click()
    page.locator('[data-adjust-user="999"]').click()
    page.select_option('#adjust-form [name=direction]','1')
    page.fill('#adjust-form [name=amount]','50')
    page.fill('#adjust-form [name=reason]','活动补发奖励')
    page.locator('#adjust-form button[type=submit]').click()
    expect(page.locator('#adjust-dialog')).not_to_be_visible()
    assert actor()==170
    page.locator('[data-adjust-user="999"]').click()
    page.select_option('#adjust-form [name=direction]','-1')
    page.fill('#adjust-form [name=amount]','20')
    page.fill('#adjust-form [name=reason]','重复奖励修正')
    page.locator('#adjust-form button[type=submit]').click()
    assert actor()==150
    page.locator('[data-adjust-user="999"]').click()
    page.select_option('#adjust-form [name=direction]','-1')
    page.fill('#adjust-form [name=amount]','99999')
    page.fill('#adjust-form [name=reason]','余额不足测试')
    page.locator('#adjust-form button[type=submit]').click()
    expect(page.locator('#adjust-error')).to_contain_text('不能为负数')
    assert actor()==150
    page.locator('#adjust-dialog [data-close-dialog]').click()
    page.locator('[data-user-history="999"]').click()
    expect(page.locator('#history-body')).to_contain_text('活动补发奖励')
    expect(page.locator('#history-body')).to_contain_text('重复奖励修正')
    page.locator('#history-dialog [data-close-dialog]').click()
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(output/'ranking.png'),full_page=True)
    page.locator('[data-rank-history]').click()
    expect(page.locator('#ranking-month')).to_have_value('2026-09')
    expect(page.locator('[data-adjust-user]')).to_have_count(0)
    page.locator('[data-page=points]').click()
    reward=page.locator('[data-reward-gid="-10010001"]')
    reward.locator('[name=checkin_points]').fill('10')
    reward.locator('[name=invite_points]').fill('35')
    reward.locator('button[type=submit]').click()
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(output/'points-settings.png'),full_page=True)
    page.locator('#try-bot').click()
    page.locator('[data-command=coinw]').click()
    expect(page.locator('.bot-message').last).to_contain_text('earned 10 points')
    assert actor()==160
    page.locator('[data-command=coinw]').click()
    expect(page.locator('.bot-message').last).to_contain_text('already checked in')
    assert actor()==160
    page.locator('[data-command="/ink"]').click()
    page.locator('[data-command=invite_success]').click()
    expect(page.locator('.bot-message').last).to_contain_text('earned 35 invite points')
    assert actor()==195
    page.locator('#close-bot-demo').click()
    page.locator('[data-page=invites]').click()
    page.locator('[data-invite-view=details]').click()
    expect(page.locator('#invite-results')).to_contain_text('演示新成员')
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(output/'invites-details.png'),full_page=True)
    with page.expect_download() as info:
        page.locator('#export-invites').click()
    info.value.save_as(str(output/'CoinW-invites-2026-10.xlsx'))
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    def rows(tree):
        result=[]
        for row in tree.findall('.//s:sheetData/s:row',ns):
            result.append([''.join(c.itertext()) for c in row])
        return result
    with zipfile.ZipFile(output/'CoinW-invites-2026-10.xlsx') as archive:
        assert archive.testzip() is None
        a=rows(ET.fromstring(archive.read('xl/worksheets/sheet1.xml')))
        b=rows(ET.fromstring(archive.read('xl/worksheets/sheet2.xml')))
        assert sum(int(r[5]) for r in a[1:])==len(b)-1
        assert sum(int(r[7]) for r in a[1:])==sum(int(r[9]) for r in b[1:])
        assert all(r[7].startswith('2026-10') for r in b[1:])
        assert any('演示新成员' in r[6] and r[9]=='35' for r in b[1:])
        assert not any(ET.fromstring(archive.read(name)).findall('.//s:f',ns) for name in ['xl/worksheets/sheet1.xml','xl/worksheets/sheet2.xml'])
    page.locator('[data-page=draws]').click()
    page.fill('#draw-create-form [name=title]','CoinW Weekend Lucky Draw')
    page.fill('#draw-create-form [name=prize]','100 USDT prize pool + VIP membership')
    page.fill('#draw-create-form [name=entry_cost]','50')
    page.fill('#draw-create-form [name=winner_count]','2')
    image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lL0AAAAASUVORK5CYII=')
    page.locator('#draw-image').set_input_files({'name':'test.png','mimeType':'image/png','buffer':image})
    expect(page.locator('.draw-poster-image')).to_be_visible()
    page.locator('#remove-draw-image').click()
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(output/'lucky-draw-create.png'),full_page=True)
    page.locator('#draw-image').set_input_files({'name':'test.png','mimeType':'image/png','buffer':image})
    page.locator('#draw-image-en-confirm').check()
    page.locator('#draw-create-form button[type=submit]').click()
    expect(page.locator('.draw-record')).to_have_count(1)
    page.once('dialog',lambda d:d.accept())
    page.locator('[data-publish-draw]').click()
    expect(page.locator('#draw-dialog')).to_be_visible()
    expect(page.locator('.announcement-body img')).to_be_visible()
    page.locator('[data-enter-draw]').click()
    assert actor()==145
    page.locator('[data-enter-draw]').click()
    expect(page.locator('#toast')).to_contain_text('已参与')
    assert actor()==145
    page.select_option('#draw-sim-user','100')
    page.locator('[data-enter-draw]').click()
    assert len(snapshot()['draws'][0]['entries'])==2
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(output/'draw-announcement.png'),full_page=True)
    page.locator('#draw-dialog [data-close-dialog]').click()
    page.once('dialog',lambda d:d.accept())
    page.locator('[data-finish-draw]').click()
    expect(page.locator('#draw-dialog-title')).to_have_text('中奖名单')
    expect(page.locator('#draw-dialog tbody tr')).to_have_count(2)
    finished=snapshot()['draws'][0]
    assert len({r['user_id'] for r in finished['winners']})==2
    assert finished['status']=='ended'
    assert page.evaluate("async id=>{try{await previewApi('/api/draws/finish',{id});return false;}catch{return true;}}",finished['id'])
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(output/'draw-winners.png'),full_page=True)
    page.locator('#draw-dialog [data-close-dialog]').click()
    page.reload()
    page.locator('.draw-record').wait_for()
    assert snapshot()['draws'][0]['winners']==finished['winners']
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(output/'lucky-draw-mobile.png'),full_page=True)
    # Cross the UTC+8 month boundary without erasing any old ledger entries.
    page.clock.set_fixed_time(datetime(2026,10,31,15,59,59,tzinfo=timezone.utc))
    renew_preview_session(page)
    page.locator('#refresh').click()
    assert actor()==145
    ledger_count=len(snapshot()['ledger'])
    page.clock.set_fixed_time(datetime(2026,10,31,16,0,0,tzinfo=timezone.utc))
    page.locator('#refresh').click()
    after=snapshot()
    assert after['month']=='2026-11'
    assert all(m['monthly']==0 for m in after['ranking'])
    assert all(g['credits']==0 for g in after['groups'])
    assert len(after['ledger'])==ledger_count
    page.locator('[data-page=ranking]').click()
    page.select_option('#ranking-month','2026-10')
    expect(page.locator('[data-adjust-user]')).to_have_count(0)
    old=page.locator('tbody tr').filter(has=page.locator('[data-user-history="999"][data-gid="-10010001"]'))
    expect(old.locator('.points-value')).to_have_text('145')
    assert not errors,errors
    assert all(r.startswith(base) or r.startswith('data:') or r.startswith('blob:') for r in requests),requests
    assert not any('/api/' in r for r in requests),requests
    browser.close()
    print('PASS: monthly balances and history; manual +/- with reasons and negative-balance rejection; configurable check-in/invite rewards; deduplication; two-sheet XLSX count/time/points reconciliation; image upload; draft/publish demo; entry cost and duplicate protection; distinct locked winners and reload persistence; mobile; UTC+8 month rollover with historical balance preserved. Zero backend/Telegram API requests or page errors.')
    print('Artifacts:',output)
finally:
 server.shutdown()
 server.server_close()
