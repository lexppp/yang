"""Standalone admin/reader UI, account permissions and forced logout checks."""
from datetime import datetime,timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright,expect
from ui_auth import preview_login,PREVIEW_PASSWORD

root=Path(__file__).resolve().parents[1];out=root/'data/frontend-v4';out.mkdir(parents=True,exist_ok=True)
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(root/'frontend')))
threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:'+str(server.server_port)
READER_PASSWORD='reader-test-fixture-password-2026'
try:
 with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    context=browser.new_context(viewport={'width':1440,'height':1100})
    page=context.new_page();errors=[];requests=[]
    page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
    page.goto(base+'/index.html')
    page.locator('#login').wait_for(state='visible')
    page.wait_for_function("()=>document.querySelector('#login-form').dataset.mode==='setup'")
    page.screenshot(path=str(out/'setup.png'),full_page=True)
    preview_login(page)
    page.locator('[data-page=accounts]').click()
    page.locator('#create-account').wait_for()
    page.locator('#create-account').click()
    f=page.locator('#account-form')
    f.locator('[name=username]').fill('community_reader')
    f.locator('[name=name]').fill('社群只读管理员')
    f.locator('[name=password]').fill(READER_PASSWORD)
    for control in f.locator('[name=permission]').all():control.uncheck()
    for permission in ['groups.view','points.view','invites.view']:
        f.locator('[value="'+permission+'"]').check()
    page.locator('#account-dialog').evaluate('(d)=>d.scrollTop=0')
    page.screenshot(path=str(out/'permissions.png'),full_page=True)
    f.locator('button[type=submit]').click()
    page.locator('#account-dialog').wait_for(state='hidden')
    expect(page.locator('#content')).to_contain_text('community_reader')
    expect(page.locator('#content')).to_contain_text('创建管理员')
    page.evaluate('window.scrollTo(0,0)')
    page.locator('#toast').evaluate('(el)=>el.hidden=true')
    page.screenshot(path=str(out/'administrators.png'),full_page=True)
    serialized=page.evaluate("()=>localStorage.getItem('coinw-preview-accounts-v1')")
    assert PREVIEW_PASSWORD not in serialized and READER_PASSWORD not in serialized
    assert 'password_hash' in serialized
    # A second page shares account data but has its own session.
    reader=context.new_page();reader.on('pageerror',lambda e:errors.append(str(e)))
    reader.goto(base+'/index.html');preview_login(reader,'community_reader',READER_PASSWORD)
    expect(reader.locator('[data-page=accounts]')).to_be_hidden()
    expect(reader.locator('[data-page=announcements]')).to_be_hidden()
    expect(reader.locator('[data-page=groups]')).to_be_visible()
    reader.locator('[data-page=ranking]').click()
    expect(reader.locator('#manual-points')).to_be_hidden()
    expect(reader.locator('[data-adjust-user]').first).to_be_hidden()
    reader.locator('[data-page=invites]').click()
    expect(reader.locator('#export-invites')).to_be_hidden()
    expect(reader.locator('[data-revoke]').first).to_be_hidden()
    result=reader.evaluate("""async()=>{const requests=[['/api/admins'],['/api/admins/create',{username:'hacker',password:'something-at-least-12',role:'superadmin'}],['/api/points/adjust',{group_id:-10010001,user_id:999,delta:500,reason:'forged'}],['/api/settings',{group_id:-10010001,checkin_points:100}],['/api/announcements/save',{}]];return Promise.all(requests.map(async([p,b])=>{try{await previewApi(p,b);return false;}catch(e){return e.status===403;}}));}""")
    assert all(result),result
    reader.evaluate("location.hash='accounts'")
    expect(reader.locator('#page-title')).not_to_contain_text('管理员与权限')
    reader.set_viewport_size({'width':390,'height':844})
    assert reader.evaluate('document.documentElement.scrollWidth<=innerWidth')
    reader.screenshot(path=str(out/'reader-mobile.png'),full_page=True)
    # Updating permissions must force the reader's old session to log out.
    page.locator('[data-edit-account]').click()
    page.locator('#account-form [value="points.view"]').uncheck()
    page.locator('#account-form button[type=submit]').click()
    expect(reader.locator('#login')).to_be_visible()
    preview_login(reader,'community_reader',READER_PASSWORD)
    expect(reader.locator('[data-page=ranking]')).to_be_hidden()
    # Disabled account cannot use or restore an old session.
    page.locator('[data-toggle-account]').click()
    expect(reader.locator('#login')).to_be_visible()
    reader.fill('#username','community_reader');reader.fill('#password',READER_PASSWORD)
    reader.locator('#login-form button').click()
    expect(reader.locator('#login-error')).to_contain_text('已停用')
    page.locator('[data-toggle-account]').click()
    # No-permission admin can sign in but sees only the waiting screen.
    created=page.evaluate("""()=>previewApi('/api/admins/create',{username:'pending_admin',name:'等待授权',password:'empty-role-fixture-password',permissions:[]})""")
    expect(page.locator('#app')).to_be_visible()
    reader.reload();preview_login(reader,'pending_admin','empty-role-fixture-password')
    expect(reader.locator('.permission-empty')).to_be_visible()
    # Root changes their own password; there is no default/public super password.
    page.locator('#my-password').click()
    pw=page.locator('#account-password-form')
    pw.locator('[name=current_password]').fill(PREVIEW_PASSWORD)
    pw.locator('[name=password]').fill('new-root-test-fixture-password')
    pw.locator('[name=confirm_password]').fill('new-root-test-fixture-password')
    pw.locator('button[type=submit]').click()
    expect(page.locator('#login')).to_be_visible()
    preview_login(page,'admin','new-root-test-fixture-password')
    page.locator('#logout').click()
    expect(page.locator('#login')).to_be_visible()
    page.screenshot(path=str(out/'login.png'),full_page=True)
    assert not errors,errors
    assert all(r.startswith(base) or r.startswith(('data:','blob:')) for r in requests),requests
    assert not any('/api/' in r for r in requests),requests
    print('PASS: first-owner setup, account/password login, salted password hashes, account creation, fine-grained rights UI, read-only navigation/actions, direct API denial, no self-escalation, permission/disable forced logout across tabs, empty-role waiting screen, own-password change, logout, mobile. No real backend or Telegram requests.')
    browser.close()
finally:
 server.shutdown();server.server_close()
