"""Regression: chat preview restrictions must not leave owner setup stuck."""
import hashlib
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright,expect

root=Path(__file__).resolve().parents[1]
out=root/'data/login-fix';out.mkdir(parents=True,exist_ok=True)
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(root/'frontend')))
threading.Thread(target=server.serve_forever,daemon=True).start()
base='http://127.0.0.1:'+str(server.server_port)
no_subtle="Object.defineProperty(crypto,'subtle',{value:undefined});Object.defineProperty(crypto,'randomUUID',{value:undefined});"
no_session="Object.defineProperty(window,'sessionStorage',{get(){throw new DOMException('Storage denied','SecurityError')}});"
no_local="Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Storage denied','SecurityError')}});"
cases={'normal':'','no_subtle':no_subtle,'no_session_storage':no_session,'no_local_storage':no_local,'no_submitter':'','chat_preview':no_subtle+no_session+no_local}
password='test-fixture-password-for-preview'
try:
 with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    for name,script in cases.items():
        context=browser.new_context(viewport={'width':1280,'height':900});page=context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        if script:page.add_init_script(script)
        page.goto(base+'/index.html')
        page.wait_for_function("()=>document.querySelector('#login-form').dataset.mode==='setup'")
        # Show explicit native-validation errors instead of silently ignoring the click.
        page.fill('#username','用户名不正确');page.fill('#password','123');page.fill('#confirm-password','123')
        page.locator('#login-form button').click()
        expect(page.locator('#login-error')).not_to_be_empty()
        page.fill('#username','preview_owner');page.fill('#password',password);page.fill('#confirm-password',password)
        if name=='no_submitter':page.locator('#login-form').evaluate("f=>f.dispatchEvent(new SubmitEvent('submit',{bubbles:true,cancelable:true}))")
        else:page.locator('#login-form button').click()
        expect(page.locator('#app')).to_be_visible(timeout=20000)
        expect(page.locator('#login-error')).to_be_empty()
        assert page.evaluate('currentUser.role')=='superadmin'
        # The fallback must match the existing native PBKDF2 parameters and stored hashes.
        actual=page.evaluate("async()=>Array.from(await coinwPbkdf2(new TextEncoder().encode('compat-test-vector'),new Uint8Array(16),120000)).map(b=>b.toString(16).padStart(2,'0')).join('')")
        expected=hashlib.pbkdf2_hmac('sha256',b'compat-test-vector',bytes(16),120000,32).hex()
        assert actual==expected
        page.locator('[data-page=accounts]').click();page.locator('#create-account').wait_for()
        page.locator('#create-account').click();f=page.locator('#account-form')
        f.locator('[name=username]').fill('test_reader');f.locator('[name=name]').fill('预览兼容性测试')
        f.locator('[name=password]').fill('reader-fixture-password-2026')
        f.locator('button[type=submit]').click();expect(page.locator('#account-dialog')).not_to_be_visible(timeout=20000)
        expect(page.locator('#content')).to_contain_text('test_reader')
        # Logout/login still validates the password when storage is memory-only.
        page.locator('#logout').click();expect(page.locator('#login')).to_be_visible()
        page.wait_for_function("()=>document.querySelector('#login-form').dataset.mode==='login'")
        if name=='chat_preview':
            expect(page.locator('#auth-preview-note')).to_contain_text('本次打开')
            page.screenshot(path=str(out/'chat-preview-login.png'),full_page=True)
        page.fill('#username','preview_owner');page.fill('#password',password)
        page.locator('#login-form button').click();expect(page.locator('#app')).to_be_visible(timeout=20000)
        assert not errors,errors
        print(name+': PASS (owner creation, account management, logout/login, PBKDF2 parity)')
        context.close()
    browser.close()
finally:
 server.shutdown();server.server_close()
