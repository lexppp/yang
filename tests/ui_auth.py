"""Credentials here are only isolated disposable browser test fixtures."""
from playwright.sync_api import expect
PREVIEW_PASSWORD='browser-test-only-password-2026'

def preview_login(page,username='admin',password=PREVIEW_PASSWORD):
    page.locator('#login').wait_for(state='visible')
    page.wait_for_function("()=>['setup','login'].includes(document.querySelector('#login-form').dataset.mode)")
    page.fill('#username',username)
    page.fill('#password',password)
    if page.locator('#login-form').get_attribute('data-mode')=='setup':
        page.fill('#confirm-password',password)
    page.locator('#login-form button[type=submit]').click()
    page.locator('#app').wait_for(state='visible')
    page.wait_for_function("()=>document.querySelector('#content').children.length>0")

def renew_preview_session(page):
    page.evaluate("""async password=>{const result=await previewApi('/api/login',{username:'admin',password});await enter(result);}""",PREVIEW_PASSWORD)
