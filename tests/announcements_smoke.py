"""Announcement UI, UTC+8 scheduling, English output, and no real delivery."""
import base64
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright,expect
from ui_auth import preview_login, renew_preview_session

root=Path(__file__).resolve().parents[1]
out=root/'data/frontend-v3';out.mkdir(parents=True,exist_ok=True)
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(root/'frontend')))
threading.Thread(target=server.serve_forever,daemon=True).start()
base='http://127.0.0.1:'+str(server.server_port)
try:
 with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1100})
    page.clock.install(time=datetime(2026,10,21,7,0,tzinfo=timezone.utc))
    errors=[];requests=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:requests.append(r.url))
    page.goto(base+'/index.html#announcements')
    preview_login(page)
    page.locator('#announcement-form').wait_for()
    snapshot=lambda:page.evaluate("()=>previewApi('/api/snapshot')")
    run=lambda:page.evaluate('()=>announcementPreview.run()')
    def clock(iso):page.clock.set_fixed_time(datetime.fromisoformat(iso))
    f=page.locator('#announcement-form')
    f.locator('[name=title]').fill('每日社群活动提醒')
    f.locator('[name=topic_id]').select_option('12')
    f.locator('[name=body]').fill('Hello CoinW community! 👋\nOur weekly rewards event is now live. Check in with coinw and invite your friends to earn points.\nStay active and enjoy the rewards!')
    f.locator('[name=once_local]').fill('2026-10-21T15:01')
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lL0AAAAASUVORK5CYII=')
    f.locator('#announcement-image').set_input_files({'name':'announcement.png','mimeType':'image/png','buffer':png})
    expect(page.locator('#announcement-poster img')).to_be_visible()
    f.locator('[data-intent=activate]').click()
    expect(page.locator('#announcement-error')).to_contain_text('确认图片')
    assert not snapshot()['announcements']
    f.locator('[name=image_english_confirmed]').check()
    f.locator('[name=body]').fill('欢迎加入 CoinW')
    expect(page.locator('#announcement-poster')).not_to_contain_text('欢迎')
    f.locator('[data-intent=activate]').click()
    expect(page.locator('#announcement-error')).to_contain_text('英文')
    assert not snapshot()['announcements']
    f.locator('[name=body]').fill('Hello CoinW community! 👋\nOur weekly rewards event is now live. Check in with coinw and invite your friends to earn points.\nStay active and enjoy the rewards!')
    # Capture a realistic English card before replacing the image with a no-photo preview.
    f.locator('#remove-announcement-image').click()
    page.locator('#toast').evaluate('(el)=>el.hidden=true')
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(out/'announcement-composer.png'),full_page=True)
    page.locator('#content > .panel').first.screenshot(path=str(out/'announcement-panel.png'))
    f.locator('[data-intent=activate]').click()
    assert len(snapshot()['announcements'])==1
    plan=snapshot()['announcements'][0]
    assert plan['topic_id']==12 and plan['pin']
    assert plan['next_run']=='2026-10-21T07:01:00.000Z'
    clock('2026-10-21T07:01:00+00:00');assert run()
    assert len(snapshot()['announcement_deliveries'])==1
    assert snapshot()['announcement_deliveries'][0]['status']=='simulated'
    assert not run()
    page.reload();page.locator('#announcement-form').wait_for()
    assert len(snapshot()['announcement_deliveries'])==1
    # Daily schedule, three times, topic-scoped and unpinned.
    f=page.locator('#announcement-form')
    f.locator('[name=title]').fill('每日三次公告')
    f.locator('[name=topic_id]').select_option('36')
    f.locator('[name=body]').fill('Daily community reminder: check in and invite friends to earn points!')
    f.locator('[name=pin]').uncheck()
    f.locator('[name=mode][value=daily]').check()
    page.locator('#announcement-frequency').select_option('3')
    for i,value in enumerate(['15:02','15:03','20:00']):page.locator('.announcement-time').nth(i).fill(value)
    f.locator('[name=start_date]').fill('2026-10-21')
    f.locator('[name=end_date]').fill('2026-10-22')
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(out/'announcement-daily-schedule.png'),full_page=True)
    f.locator('[data-intent=activate]').click()
    daily=next(p for p in snapshot()['announcements'] if p['mode']=='daily')
    page.locator(f'[data-plan-id="{daily["id"]}"][data-announcement-action=pause]').click()
    clock('2026-10-21T07:02:10+00:00');assert not run()
    page.locator(f'[data-plan-id="{daily["id"]}"][data-announcement-action=resume]').click()
    daily=next(p for p in snapshot()['announcements'] if p['mode']=='daily')
    assert daily['next_run']=='2026-10-21T07:03:00.000Z'
    clock('2026-10-21T07:03:00+00:00');assert run()
    deliveries=snapshot()['announcement_deliveries']
    assert len(deliveries)==2
    assert deliveries[0]['topic_id']==36 and not deliveries[0]['pin']
    clock('2026-10-21T13:00:00+00:00');assert run()
    assert snapshot()['announcement_deliveries'][0]['status']=='missed'
    assert next(p for p in snapshot()['announcements'] if p['mode']=='daily')['next_run']=='2026-10-22T07:02:00.000Z'
    page.locator('#refresh').click()
    # An invalid duplicated daily time is rejected without creating a task.
    f=page.locator('#announcement-form');f.locator('[name=title]').fill('重复时段测试')
    f.locator('[name=body]').fill('English announcement.')
    f.locator('[name=mode][value=daily]').check()
    for i in range(3):page.locator('.announcement-time').nth(i).fill('22:00')
    f.locator('[data-intent=activate]').click()
    expect(page.locator('#announcement-error')).to_contain_text('不能重复')
    assert len(snapshot()['announcements'])==2
    # Non-forum destination cannot retain the previous topic, immediate delivery is unpinned.
    f.locator('[name=group_id]').select_option('-10010003')
    expect(f.locator('[name=topic_id] option')).to_have_count(1)
    f.locator('[name=title]').fill('普通群立即公告')
    f.locator('[name=mode][value=now]').check()
    f.locator('[name=pin]').uncheck()
    f.locator('[data-intent=activate]').click()
    immediate=next(p for p in snapshot()['announcements'] if p['mode']=='now')
    assert immediate['topic_id'] is None and immediate['status']=='completed'
    # Positive image delivery, selected topic integrity, and cancellation.
    photo_result=page.evaluate("""async image=>previewApi('/api/announcements/save',{title:'Image example',group_id:-10010001,topic_id:12,body:'Community event starts today!',image,image_english_confirmed:true,pin:true,mode:'now',activate:true})""",'data:image/png;base64,'+base64.b64encode(png).decode())
    photo_plan=next(p for p in snapshot()['announcements'] if p['id']==photo_result['id'])
    assert photo_plan['image'].startswith('data:image/png') and photo_plan['image_english_confirmed']
    assert any(d['plan_id']==photo_plan['id'] and d['status']=='simulated' and d['pin'] for d in snapshot()['announcement_deliveries'])
    rejected=page.evaluate("""async()=>{try{await previewApi('/api/announcements/save',{title:'Invalid topic',group_id:-10010003,topic_id:12,body:'English content.',mode:'now',activate:true});return false;}catch{return true;}}""")
    assert rejected
    cancelled=page.evaluate("""async()=>{const result=await previewApi('/api/announcements/save',{title:'Cancel test',group_id:-10010001,topic_id:null,body:'This message will be cancelled.',mode:'once',once_local:'2026-10-22T09:00',activate:true});await previewApi('/api/announcements/cancel',{id:result.id});return result.id;}""")
    assert next(p for p in snapshot()['announcements'] if p['id']==cancelled)['status']=='cancelled'
    # English bot messages, welcomes, and Lucky Draw content.
    page.locator('#try-bot').click()
    page.locator('[data-command=coinw]').click()
    page.locator('[data-command="/ink"]').click()
    page.locator('[data-command=invite_success]').click()
    assert page.locator('.bot-message p').evaluate_all("els=>els.every(el=>telegramEnglish.valid(el.textContent))")
    page.locator('#close-bot-demo').click()
    page.locator('[data-page=groups]').click()
    page.locator('[data-settings]').first.click()
    assert page.locator('#welcome-copy').evaluate('(el)=>telegramEnglish.valid(el.textContent)')
    page.locator('#close-settings').click()
    page.locator('[data-page=draws]').click()
    assert page.locator('.draw-poster').evaluate('(el)=>telegramEnglish.valid(el.textContent)')
    # Block Chinese draw copy, including API calls that bypass HTML validation.
    result=page.evaluate("async()=>{try{await previewApi('/api/draws/create',{group_id:-10010001,title:'中文活动',prize:'100 USDT',entry_cost:10,winner_count:1});return false;}catch{return true;}}")
    assert result
    page.locator('[data-page=announcements]').click()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(out/'announcement-mobile.png'),full_page=True)
    assert not errors,errors
    assert not any('/api/' in r for r in requests),requests
    assert all(r.startswith(base) or r.startswith(('data:','blob:')) for r in requests),requests
    print('PASS: English-only announcements and bot/draw previews, photo confirmation, group/topic binding, UTC+8 one-off and daily schedules, pause/resume, no duplicate execution after reload, skip missed slots, non-forum routing, immediate/unpinned mode, duplicate-time rejection, persistence, mobile layout. No Telegram/backend API requests or JS page errors.')
    browser.close()
finally:
 server.shutdown();server.server_close()
