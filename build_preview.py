"""Bundle a self-contained, credential-free frontend preview. No backend required."""
from pathlib import Path
import sys
hosted = "--hosted" in sys.argv

root = Path(__file__).resolve().parent
source = root/'static'
front = root/'frontend'
html = (source/'index.html').read_text()
style = (source/'style.css').read_text()+'\n'+(front/'preview.css').read_text()+'\n'+(front/'features.css').read_text()+'\n'+(front/'announcements.css').read_text()+'\n'+(source/'access.css').read_text()
script = (source/'app.js').read_text()
start = script.index('async function api(path, body) {')
end = script.index('\nfunction toast(', start)
script = script[:start]+'async function api(path, body) { return window.previewApi(path, body); }\n'+script[end:]
script = script.replace('社群设置已保存，对后续事件生效','社群设置已保存在本地演示')
script = script.replace('由机器人事件自动同步','示例数据 · 尚未关联 Telegram')
script = script.replace('每 15 秒自动同步','本地演示状态')
script = script.replace("if(csrf&&!document.hidden","if(csrf&&!['draws','points','ranking','invites','announcements'].includes(page)&&!document.querySelector('dialog[open]')&&!document.hidden")
script = script.replace('累计积分与邀请记录始终保留。','每月重置可用积分，历史流水与邀请记录保留。')
script = script.replace('总积分','本月积分')
html = html.replace('<head>', '<head><link rel="icon" href="data:,">')
html = html.replace('<title>CoinW · Community OS</title>', '<title>CoinW 社群管理 · 前端交互预览</title>')
html = html.replace('<link rel="stylesheet" href="/style.css">', '<style>'+style+'</style>')
for name in ['access-policy.js','app.js','access.js','live-announcements.js']:
    html = html.replace('<script src="/'+name+'" defer></script>', '')
html = html.replace('<link rel="stylesheet" href="/access.css">','')
html = html.replace('每 15 秒自动同步','本地演示状态')
html = html.replace('演示工作空间 <span>当前为示例数据，未连接真实 Telegram 社群。</span>', '前端交互预览 <span>本地账号与权限演示；当前为示例数据，尚未关联 Telegram。</span>')
html = html.replace('数据持久化存储','演示设置保存在当前浏览器')
html = html.replace('需授予机器人邀请成员权限，并撤销其他直接入群链接；直接添加成员或其他管理员批准可绕过验证。','开启后，新成员需要通过验证才能加入社群。当前为前端演示，开关不会操作真实群。')
html = html.replace('HTTPS 图片地址或 Telegram file_id','可填写图片地址；此处展示欢迎卡片的布局预览')
html = html.replace('支持 {name} 和 {group} 占位符','{name} 为用户昵称，{group} 为社群名称')
account_script=(front/'hosted-access.js').read_text() if hosted else ('/*\n'+(front/'vendor/noble-hashes.LICENSE').read_text()+'\n*/\n'+(front/'vendor/pbkdf2.js').read_text())+'\n'+(front/'preview-compat.js').read_text()+'\n'+(front/'access-model.js').read_text()
html = html.replace('</body>', '<script>\n'+(source/'access-policy.js').read_text()+'\n'+(front/'telegram-en.js').read_text()+'\n'+(front/'demo.js').read_text()+'\n'+(front/'announcement-model.js').read_text()+'\n'+account_script+'\n'+(front/'xlsx.js').read_text()+'\n'+script+'\n'+(front/'interactions.js').read_text()+'\n'+(front/'features.js').read_text()+'\n'+(front/'announcements.js').read_text()+'\n'+(source/'access.js').read_text()+'\n</script></body>')
if hosted:
    html = html.replace('本地账号与权限演示；当前为示例数据，尚未关联 Telegram。','真实服务器账号登录 · 业务操作为本浏览器模拟数据 · TG 尚未关联')
    html = html.replace('<meta name="viewport"', '<meta name="robots" content="noindex,nofollow"><meta name="viewport"')
(front/('hosted.html' if hosted else 'index.html')).write_text(html)
print('Generated frontend/'+('hosted.html (server authentication)' if hosted else 'index.html (offline demonstration)'))
