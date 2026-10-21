"""Community OS — Python 3.12+, SQLite, Telegram long polling; no third party packages."""
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import random
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from auth import Accounts, AccessDenied, PERMISSIONS, allowed, required_permissions, redact_snapshot

ROOT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
log = logging.getLogger('community')
DEFAULT_WELCOME = 'Welcome, {name}, to {group}! Send coinw to check in and /link to get your invite link.'


def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))


def now():
    return datetime.now(TZ)


def is_english_text(text):
    return all(not unicodedata.category(c).startswith('L') or 'LATIN' in unicodedata.name(c, '') for c in str(text))


def english_label(value, fallback):
    return str(value) if value and is_english_text(value) else fallback


def validate_telegram_text(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('text', 'caption') and isinstance(item, str) and not is_english_text(item):
                raise TelegramError('Outgoing Telegram text must use English. No message was sent.')
            validate_telegram_text(item)
    elif isinstance(value, list):
        for item in value:
            validate_telegram_text(item)


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS groups(
              id INTEGER PRIMARY KEY, title TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
              welcome TEXT NOT NULL DEFAULT 'Welcome, {name}, to {group}! Send coinw to check in and /link to get your invite link.', welcome_photo TEXT NOT NULL DEFAULT '',
              block_links INTEGER NOT NULL DEFAULT 1, block_photos INTEGER NOT NULL DEFAULT 0,
              block_ads INTEGER NOT NULL DEFAULT 1, keywords TEXT NOT NULL DEFAULT '代投,稳赚,刷单',
              verify INTEGER NOT NULL DEFAULT 1, checkin_points INTEGER NOT NULL DEFAULT 5,
              invite_points INTEGER NOT NULL DEFAULT 20, messages INTEGER NOT NULL DEFAULT 0,
              last_activity TEXT, language TEXT NOT NULL DEFAULT 'en');
            CREATE TABLE IF NOT EXISTS members(
              group_id INTEGER, user_id INTEGER, name TEXT NOT NULL, username TEXT NOT NULL DEFAULT '',
              present INTEGER NOT NULL DEFAULT 1, last_activity TEXT,
              PRIMARY KEY(group_id,user_id));
            CREATE TABLE IF NOT EXISTS ledger(
              id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
              points INTEGER NOT NULL, kind TEXT NOT NULL, period TEXT NOT NULL, created TEXT NOT NULL,
              UNIQUE(group_id,user_id,kind,period));
            CREATE TABLE IF NOT EXISTS invites(
              id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
              url TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS referrals(
              group_id INTEGER, user_id INTEGER, inviter_id INTEGER, created TEXT NOT NULL,
              PRIMARY KEY(group_id,user_id));
            CREATE TABLE IF NOT EXISTS challenges(
              group_id INTEGER, user_id INTEGER, dm_id INTEGER, answer TEXT NOT NULL,
              nonce TEXT NOT NULL, expires REAL NOT NULL, inviter_id INTEGER,
              PRIMARY KEY(group_id,user_id));
            CREATE TABLE IF NOT EXISTS logs(
              id INTEGER PRIMARY KEY, group_id INTEGER, user_id INTEGER, kind TEXT, detail TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
            ''')
            # Update only the former built-in template. Preserve administrators' custom text.
            db.execute('UPDATE groups SET welcome=? WHERE welcome=?',
                       (DEFAULT_WELCOME, DEFAULT_WELCOME.replace('/link', '/ink')))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, gid, uid, kind, detail):
        with self.db() as db:
            db.execute('INSERT INTO logs(group_id,user_id,kind,detail,created) VALUES(?,?,?,?,?)',
                       (gid, uid, kind, detail[:1000], now().isoformat()))

    def group(self, chat):
        with self.db() as db:
            db.execute('INSERT INTO groups(id,title,welcome) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title',
                       (chat['id'], chat.get('title', str(chat['id'])), DEFAULT_WELCOME))
            return dict(db.execute('SELECT * FROM groups WHERE id=?', (chat['id'],)).fetchone())

    def member(self, gid, user, present=None):
        with self.db() as db:
            db.execute('''INSERT INTO members(group_id,user_id,name,username,present,last_activity) VALUES(?,?,?,?,?,?)
              ON CONFLICT(group_id,user_id) DO UPDATE SET name=excluded.name,username=excluded.username,
              present=COALESCE(?,members.present),last_activity=excluded.last_activity''',
                       (gid, user['id'], user.get('first_name', '用户'), user.get('username', ''),
                        1 if present is None else int(present), now().isoformat(),
                        None if present is None else int(present)))

    def checkin(self, gid, uid, at=None):
        at = at or now()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            g = db.execute('SELECT * FROM groups WHERE id=?', (gid,)).fetchone()
            points = g['checkin_points']
            result = db.execute('INSERT OR IGNORE INTO ledger(group_id,user_id,points,kind,period,created) VALUES(?,?,?,?,?,?)',
                                (gid, uid, points, 'checkin', at.astimezone(TZ).date().isoformat(), at.isoformat()))
            return points if result.rowcount else None

    def referral(self, gid, uid, inviter):
        if inviter is None or uid == inviter:
            return False
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            result = db.execute('INSERT OR IGNORE INTO referrals VALUES(?,?,?,?)', (gid, uid, inviter, now().isoformat()))
            if not result.rowcount:
                return False
            points = db.execute('SELECT invite_points FROM groups WHERE id=?', (gid,)).fetchone()[0]
            db.execute('INSERT INTO ledger(group_id,user_id,points,kind,period,created) VALUES(?,?,?,?,?,?)',
                       (gid, inviter, points, 'invite', str(uid), now().isoformat()))
            return True

    def snapshot(self, gid=None):
        with self.db() as db:
            groups = [dict(r) for r in db.execute('''SELECT g.*,
              (SELECT COUNT(*) FROM members m WHERE m.group_id=g.id AND m.present=1) users,
              (SELECT COALESCE(SUM(points),0) FROM ledger l WHERE l.group_id=g.id) credits,
              (SELECT COUNT(*) FROM referrals r WHERE r.group_id=g.id) invites,
              (SELECT COUNT(*) FROM ledger l WHERE l.group_id=g.id AND kind='checkin' AND period=?) checkins
              FROM groups g ORDER BY g.active DESC,g.title''', (now().date().isoformat(),))]
            where, args = ('WHERE group_id=?', [gid]) if gid is not None else ('', [])
            month = now().strftime('%Y-%m')
            ranking = [dict(r) for r in db.execute(f'''SELECT l.group_id,l.user_id,
              COALESCE(m.name,CAST(l.user_id AS TEXT)) name,COALESCE(m.username,'') username,
              SUM(points) total,SUM(CASE WHEN substr(created,1,7)=? THEN points ELSE 0 END) monthly
              FROM ledger l LEFT JOIN members m ON l.group_id=m.group_id AND l.user_id=m.user_id
              {'WHERE l.group_id=?' if gid is not None else ''}
              GROUP BY l.group_id,l.user_id ORDER BY total DESC''', [month, *args])]
            invites = [dict(r) for r in db.execute(f'''SELECT i.*,COALESCE(m.name,CAST(i.user_id AS TEXT)) name,
              COALESCE(m.username,'') username,
              (SELECT COUNT(*) FROM referrals r WHERE r.group_id=i.group_id AND r.inviter_id=i.user_id) count
              FROM invites i LEFT JOIN members m ON m.group_id=i.group_id AND m.user_id=i.user_id
              {'WHERE i.group_id=?' if gid is not None else ''} ORDER BY i.id DESC LIMIT 500''', args)]
            logs = [dict(r) for r in db.execute(f'SELECT * FROM logs {where} ORDER BY id DESC LIMIT 100', args)]
            members = [dict(r) for r in db.execute(f'SELECT * FROM members {where} ORDER BY last_activity DESC LIMIT 500', args)]
            return dict(groups=groups, ranking=ranking, invites=invites, logs=logs, members=members,
                        month=month, server_time=now().isoformat(),
                        next_reset=(now().replace(hour=0, minute=0, second=0, microsecond=0)+timedelta(days=1)).isoformat())


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token):
        self.token = token

    def call(self, method, **params):
        validate_telegram_text(params)
        req = urllib.request.Request('https://api.telegram.org/bot'+self.token+'/'+method,
                                     data=json.dumps(params).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                result = json.load(response)
        except urllib.error.HTTPError as e:
            # Never log request URLs; Telegram embeds the secret token in them.
            try:
                description = json.loads(e.read()).get('description', 'Telegram request failed')
            except (ValueError, OSError):
                description = 'Telegram request failed'
            raise TelegramError(f'{method}: {description}') from None
        except (urllib.error.URLError, TimeoutError):
            raise TelegramError(f'{method}: 网络连接失败') from None
        if not result.get('ok'):
            raise TelegramError(f'{method}: {result.get("description", "请求失败")}')
        return result['result']


class Bot:
    def __init__(self, store, api):
        self.store, self.api = store, api
        self.status = '未连接：请配置 TG_BOT_TOKEN'
        self.identity = None

    def send(self, gid, text, **kwargs):
        validate_telegram_text({'text': text, **kwargs})
        return self.api.call('sendMessage', chat_id=gid, text=text, **kwargs)

    def invite(self, gid, user):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM invites WHERE group_id=? AND user_id=? AND active=1 ORDER BY id DESC LIMIT 1', (gid, user['id'])).fetchone()
        if row:
            return row['url']
        result = self.api.call('createChatInviteLink', chat_id=gid, name=f"u{user['id']}", creates_join_request=True)
        with self.store.db() as db:
            db.execute('INSERT INTO invites(group_id,user_id,url,created) VALUES(?,?,?,?)',
                       (gid, user['id'], result['invite_link'], now().isoformat()))
        return result['invite_link']

    def welcome(self, group, user):
        template = group['welcome']
        if template == '欢迎 {name} 加入 {group}！':
            template = 'Welcome, {name}, to {group}!'
        text = template.replace('{name}', english_label(user.get('first_name'), 'new member')).replace('{group}', english_label(group['title'], 'the community'))
        validate_telegram_text({'caption': text})
        if group['welcome_photo']:
            self.api.call('sendPhoto', chat_id=group['id'], photo=group['welcome_photo'], caption=text[:1024])
        elif text:
            self.send(group['id'], text)

    def approve(self, gid, user, inviter):
        # Telegram approval must succeed before awarding anything.
        self.api.call('approveChatJoinRequest', chat_id=gid, user_id=user['id'])
        self.store.member(gid, user, True)
        self.store.referral(gid, user['id'], inviter)
        with self.store.db() as db:
            db.execute('DELETE FROM challenges WHERE group_id=? AND user_id=?', (gid, user['id']))
        self.store.event(gid, user['id'], '入群', '已批准入群申请；有效邀请仅奖励一次')

    def join_request(self, request, group):
        user, gid = request['from'], group['id']
        if user.get('is_bot'):
            self.api.call('declineChatJoinRequest', chat_id=gid, user_id=user['id'])
            return
        url = request.get('invite_link', {}).get('invite_link')
        with self.store.db() as db:
            row = db.execute('SELECT user_id FROM invites WHERE group_id=? AND url=? AND active=1', (gid, url)).fetchone()
        inviter = row[0] if row else None
        if not group['verify']:
            self.approve(gid, user, inviter)
            return
        a, b = random.randint(2, 9), random.randint(2, 9)
        answer, nonce = str(a+b), secrets.token_hex(6)
        choices = [a+b, a+b+1, a+b-1]
        random.shuffle(choices)
        with self.store.db() as db:
            db.execute('INSERT OR REPLACE INTO challenges VALUES(?,?,?,?,?,?,?)',
                       (gid, user['id'], request['user_chat_id'], answer, nonce, time.time()+240, inviter))
        self.send(request['user_chat_id'], f"Before joining {english_label(group['title'], 'the community')}, solve this: {a} + {b} = ?\nPlease answer within 4 minutes. If incorrect, submit a new join request.",
                  reply_markup={'inline_keyboard': [[{'text': str(c), 'callback_data': f'v:{nonce}:{c}'} for c in choices]]})
        self.store.event(gid, user['id'], '验证', '已发送私聊入群验证')

    def callback(self, cb):
        parts = cb.get('data', '').split(':')
        if len(parts) != 3 or parts[0] != 'v':
            return
        with self.store.db() as db:
            row = db.execute('SELECT * FROM challenges WHERE nonce=? AND user_id=?', (parts[1], cb['from']['id'])).fetchone()
        if not row or row['expires'] < time.time():
            self.api.call('answerCallbackQuery', callback_query_id=cb['id'], text='This verification has expired. Please submit a new join request.')
            return
        if not hmac.compare_digest(row['answer'], parts[2]):
            self.api.call('declineChatJoinRequest', chat_id=row['group_id'], user_id=row['user_id'])
            with self.store.db() as db:
                db.execute('DELETE FROM challenges WHERE nonce=?', (parts[1],))
            self.api.call('answerCallbackQuery', callback_query_id=cb['id'], text='Incorrect answer. Please submit a new join request.')
            return
        self.approve(row['group_id'], cb['from'], row['inviter_id'])
        self.api.call('answerCallbackQuery', callback_query_id=cb['id'], text='Verification successful. Welcome to the community!')

    def expire(self):
        with self.store.db() as db:
            rows = db.execute('SELECT * FROM challenges WHERE expires<?', (time.time(),)).fetchall()
        for row in rows:
            try:
                self.api.call('declineChatJoinRequest', chat_id=row['group_id'], user_id=row['user_id'])
            except TelegramError:
                pass  # Request may already have been handled by a human administrator.
            with self.store.db() as db:
                db.execute('DELETE FROM challenges WHERE nonce=?', (row['nonce'],))
            self.store.event(row['group_id'], row['user_id'], '验证超时', '未完成验证；未奖励邀请积分')

    def handle(self, update):
        if 'callback_query' in update:
            return self.callback(update['callback_query'])
        item = next((update[k] for k in ['message', 'edited_message', 'my_chat_member', 'chat_member', 'chat_join_request'] if k in update), None)
        if not item:
            return
        chat = item['chat']
        if chat['type'] not in ('group', 'supergroup'):
            if 'message' in update:
                self.send(chat['id'], 'Send coinw in a connected group to check in. Use /link to get your personal invite link for that group.')
            return
        group = self.store.group(chat)
        gid = chat['id']
        if 'my_chat_member' in update:
            active = int(item['new_chat_member']['status'] == 'administrator')
            with self.store.db() as db:
                db.execute('UPDATE groups SET active=? WHERE id=?', (active, gid))
            self.store.event(gid, None, '机器人权限', '管理员权限已启用' if active else '需要将机器人设为管理员')
            return
        if not group['active']:
            return
        if 'chat_join_request' in update:
            return self.join_request(item, group)
        if 'chat_member' in update:
            old, new = item['old_chat_member'], item['new_chat_member']
            user = new['user']
            present = new['status'] in ('member', 'administrator', 'creator') or (new['status'] == 'restricted' and new.get('is_member'))
            was_present = old['status'] in ('member', 'administrator', 'creator') or (old['status'] == 'restricted' and old.get('is_member'))
            self.store.member(gid, user, bool(present))
            if present and not was_present and not user.get('is_bot'):
                self.welcome(group, user)
                if group['verify'] and not item.get('via_join_request'):
                    self.store.event(gid, user['id'], '验证绕过', '直接入群未经过申请验证；请在 Telegram 撤销其他直接入群链接')
            return
        user = item.get('from')
        if not user or user.get('is_bot') or item.get('sender_chat'):
            return
        self.store.member(gid, user)
        text = item.get('text', item.get('caption', ''))
        with self.store.db() as db:
            db.execute('UPDATE groups SET messages=messages+?,last_activity=? WHERE id=?', (0 if 'edited_message' in update else 1, now().isoformat(), gid))
        # Group administrators are exempt from moderation, but can sign in.
        admin = self.api.call('getChatMember', chat_id=gid, user_id=user['id'])['status'] in ('administrator', 'creator')
        if not admin:
            reason = moderation_reason(group, item)
            if reason:
                self.api.call('deleteMessage', chat_id=gid, message_id=item['message_id'])
                self.store.event(gid, user['id'], '内容拦截', reason)
                return
        if 'edited_message' in update:
            return
        if text.strip().casefold() == 'coinw':
            points = self.store.checkin(gid, user['id'])
            reply = f'✅ Check-in successful! You earned {points} points.' if points is not None else '✅ You have already checked in today. Come back tomorrow to earn more points.'
            self.send(gid, reply+'\n⏰ Next check-in: 00:00 (UTC+8)', reply_parameters={'message_id': item['message_id']})
            if points is not None:
                self.store.event(gid, user['id'], '签到', f'+{points} 积分')
        elif re.fullmatch(r'/(?:link|ink)(?:@[A-Za-z0-9_]+)?', text.strip(), re.I):
            link = self.invite(gid, user)
            with self.store.db() as db:
                count = db.execute('SELECT COUNT(*) FROM referrals WHERE group_id=? AND inviter_id=?', (gid, user['id'])).fetchone()[0]
                earned = db.execute("SELECT COALESCE(SUM(points),0) FROM ledger WHERE group_id=? AND user_id=? AND kind='invite'", (gid,user['id'])).fetchone()[0]
            self.send(gid, f'🔗 Your personal invite link\n{link}\n\n👥 Successful invites: {count}\n💎 Invite points earned: {earned}\nEach successful invite earns {group["invite_points"]} points. Rejoining members do not earn additional rewards.', reply_parameters={'message_id': item['message_id']})

    def run(self):
        delay = 2
        while True:
            try:
                if self.identity is None:
                    self.identity = self.api.call('getMe')
                    hook = self.api.call('getWebhookInfo')
                    if hook.get('url'):
                        self.status = '已有 Webhook：请先确认旧部署并手动移除，当前长轮询已暂停'
                        time.sleep(20)
                        self.identity = None
                        continue
                with self.store.db() as db:
                    row = db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
                updates = self.api.call('getUpdates', offset=int(row[0]) if row else 0, timeout=25,
                                        allowed_updates=['message', 'edited_message', 'chat_member', 'my_chat_member', 'chat_join_request', 'callback_query'])
                self.status = '在线 @'+self.identity['username']
                for update in updates:
                    # Consume each update once; unique ledger constraints also protect retries.
                    try:
                        self.handle(update)
                    except TelegramError as e:
                        self.store.event(None, None, 'Telegram 错误', str(e))
                    except Exception:
                        self.store.event(None, None, '处理错误', '事件处理失败，请检查服务器日志和数据')
                        log.error('Update handling failed (%s)', update.get('update_id'))
                    with self.store.db() as db:
                        db.execute("INSERT OR REPLACE INTO meta VALUES('offset',?)", (str(update['update_id']+1),))
                self.expire()
                delay = 2
            except TelegramError as e:
                self.status = str(e)
                log.warning('%s', e)
                time.sleep(delay)
                delay = min(delay*2, 30)
            except Exception:
                self.status = '轮询内部错误，请检查存储及运行环境'
                log.error('Polling internal failure')
                time.sleep(5)


def moderation_reason(group, message):
    text = message.get('text', message.get('caption', ''))
    entities = message.get('entities', []) + message.get('caption_entities', [])
    if group['block_links'] and (any(e['type'] in ('url', 'text_link') for e in entities) or re.search(r'https?://|t\.me/|www\.|\b[\w-]+\.(?:com|net|org|io|xyz)\b', text, re.I)):
        return '包含链接'
    if group['block_photos'] and ('photo' in message or message.get('document', {}).get('mime_type', '').startswith('image/')):
        return '包含图片'
    words = [w.strip().casefold() for w in group['keywords'].split(',') if w.strip()]
    if group['block_ads'] and any(word in text.casefold() for word in words):
        return '命中广告关键词'
    return None


def seed_demo(store):
    with store.db() as db:
        if db.execute('SELECT COUNT(*) FROM groups').fetchone()[0]:
            return
    for index, title in enumerate(['CoinW Official', 'CoinW 官方中文社群', 'CoinW 日本コミュニティー', 'CoinW Global Community']):
        gid = -10010001-index
        store.group({'id': gid, 'title': title})
        with store.db() as db:
            db.execute('UPDATE groups SET messages=?,last_activity=? WHERE id=?', (13339-index*2104, now().isoformat(), gid))
        for i, name in enumerate(['Hash', 'Aalam', 'Chloe', 'Patrick', 'Jade', 'Kai', 'Issifou', 'Tolani']):
            uid = 100+i
            store.member(gid, {'id': uid, 'first_name': name, 'username': name.lower()+'_community'})
            with store.db() as db:
                db.execute('INSERT INTO ledger(group_id,user_id,points,kind,period,created) VALUES(?,?,?,?,?,?)', (gid, uid, 3985-i*391, 'demo', 'sample', now().isoformat()))
                # Clearly non-working examples, never real invite links.
                db.execute('INSERT INTO invites(group_id,user_id,url,created) VALUES(?,?,?,?)', (gid, uid, f'demo://invite/{index}/{uid}', now().isoformat()))
            store.checkin(gid, uid)
        store.event(gid, None, '演示', '示例数据；尚未连接真实 Telegram 社群')


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, store, password, bot, demo=False):
        self.store, self.bot, self.demo = store, bot, demo
        self.accounts = Accounts(store, password, os.getenv('ADMIN_USERNAME','admin'))
        self.sessions, self.attempts = {}, {}
        self.auth_lock = threading.Lock()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'CommunityOS'
    def log_message(self, fmt, *args):
        pass

    def respond(self, status, data, content_type='application/json; charset=utf-8', headers=None):
        body = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        if not headers or 'Content-Security-Policy' not in headers:
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except Exception:
            return None
        token = cookie.get('session')
        if token:
            with self.server.auth_lock:
                item = self.server.sessions.get(token.value)
                if item and item['expires'] > time.time():
                    user = self.server.accounts.user(item['user_id'])
                    if user and user['active'] and user['version'] == item['version']:
                        return {**item, 'user':user}
                    self.server.sessions.pop(token.value, None)
        return None

    def body(self):
        length = int(self.headers.get('Content-Length', 0))
        if not 0 < length <= 32768:
            raise ValueError('请求体大小无效')
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError('需要 JSON 对象')
        return data

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path)
        if path.path in ('/', '/style.css', '/app.js', '/access.js', '/access.css', '/access-policy.js'):
            name = {'/': 'index.html', '/style.css': 'style.css', '/app.js': 'app.js', '/access.js':'access.js', '/access.css':'access.css', '/access-policy.js':'access-policy.js'}[path.path]
            mime = {'index.html':'text/html; charset=utf-8','style.css':'text/css; charset=utf-8','app.js':'text/javascript; charset=utf-8','access.js':'text/javascript; charset=utf-8','access.css':'text/css; charset=utf-8','access-policy.js':'text/javascript; charset=utf-8'}[name]
            if path.path == '/' and os.getenv('UI_MODE') == 'hosted-preview':
                return self.respond(200,(ROOT/'frontend'/'hosted.html').read_bytes(),mime,{'Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
            return self.respond(200, (ROOT/'static'/name).read_bytes(), mime)
        if path.path == '/health':
            return self.respond(200, {'ok': True})
        if path.path == '/api/auth-status':
            return self.respond(200, {'needs_setup':False,'offline_preview':False})
        session = self.session()
        if not session:
            return self.respond(401, {'error': '请先登录'})
        if path.path == '/api/session':
            return self.respond(200, {'csrf': session['csrf'], 'demo': self.server.demo, 'user':session['user']})
        if path.path in ('/api/admins','/api/admin-audit'):
            try:
                return self.respond(200, {'admins':self.server.accounts.list(session['user']), 'permissions':PERMISSIONS} if path.path == '/api/admins' else {'audit':self.server.accounts.audits(session['user'])})
            except AccessDenied as error:
                return self.respond(403, {'error':str(error)})
        if any(not allowed(session['user'],p) for p in required_permissions(path.path)):
            return self.respond(403, {'error':'无权执行此操作'})
        if path.path not in ('/api/snapshot', '/api/export'):
            return self.respond(404, {'error':'接口不存在'})
        try:
            raw = urllib.parse.parse_qs(path.query).get('group', [''])[0]
            gid = int(raw) if raw else None
        except ValueError:
            return self.respond(400, {'error':'群 ID 无效'})
        data = self.server.store.snapshot(gid)
        data.update(bot_status=self.server.bot.status, demo=self.server.demo)
        data = redact_snapshot(data,session['user'])
        if path.path == '/api/export':
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['group_id', 'user_id', 'name', 'username', 'monthly', 'total'])
            for row in data['ranking']:
                # Prevent spreadsheet formula execution in user-controlled cells.
                writer.writerow([("'"+str(row[k])) if str(row[k]).startswith(('=', '+', '-', '@', '\t', '\r')) else row[k] for k in ['group_id','user_id','name','username','monthly','total']])
            return self.respond(200, ('\ufeff'+output.getvalue()).encode(), 'text/csv; charset=utf-8', {'Content-Disposition': 'attachment; filename="community-credits.csv"'})
        self.respond(200, data)

    def do_POST(self):
        # Same-origin JSON + CSRF token, no permissive CORS.
        origin = self.headers.get('Origin')
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get('Host'):
            return self.respond(403, {'error':'来源校验失败'})
        if not self.headers.get('Content-Type', '').startswith('application/json'):
            return self.respond(415, {'error':'需要 application/json'})
        try:
            data = self.body()
            if self.path == '/api/login':
                ip = (self.client_address[0],str(data.get('username','')).lower())
                with self.server.auth_lock:
                    recent = [t for t in self.server.attempts.get(ip, []) if t > time.time()-300]
                    if len(recent) >= 8:
                        return self.respond(429, {'error':'尝试过多，请 5 分钟后再试'})
                    self.server.attempts[ip] = recent+[time.time()]
                user = self.server.accounts.authenticate(data.get('username',''),data.get('password',''))
                if not user:
                    return self.respond(401, {'error':'用户名或密码错误，或账号已停用'})
                token = secrets.token_urlsafe(32)
                session = {'csrf':secrets.token_urlsafe(32), 'expires':time.time()+43200, 'user_id':user['id'], 'version':user['version']}
                with self.server.auth_lock:
                    self.server.sessions = {k:v for k,v in self.server.sessions.items() if v['expires'] > time.time()}
                    self.server.sessions[token] = session
                    self.server.attempts.pop(ip, None)
                secure = '; Secure' if os.getenv('COOKIE_SECURE') == '1' else ''
                return self.respond(200, {'csrf':session['csrf'], 'demo':self.server.demo, 'user':user}, 'application/json; charset=utf-8',
                                    {'Set-Cookie':f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}'})
            session = self.session()
            if not session:
                return self.respond(401, {'error':'请先登录'})
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf']):
                return self.respond(403, {'error':'CSRF 校验失败'})
            if self.path == '/api/logout':
                cookie = SimpleCookie(self.headers.get('Cookie',''))
                with self.server.auth_lock:
                    self.server.sessions.pop(cookie['session'].value, None)
                return self.respond(200, {'ok':True}, headers={'Set-Cookie':'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
            if any(not allowed(session['user'],p) for p in required_permissions(self.path,data)):
                return self.respond(403, {'error':'无权执行此操作'})
            if self.path.startswith('/api/admins/') or self.path == '/api/account/password':
                actor=session['user']
                if self.path == '/api/admins/create':
                    return self.respond(200, {'ok':True,'user':self.server.accounts.create(actor,data)})
                if self.path == '/api/admins/update':
                    return self.respond(200, {'ok':True,'user':self.server.accounts.update(actor,data)})
                if self.path == '/api/admins/reset-password':
                    self.server.accounts.reset_password(actor,data)
                    return self.respond(200, {'ok':True})
                if self.path == '/api/account/password':
                    self.server.accounts.change_password(actor,data)
                    return self.respond(200, {'ok':True,'reauth_required':True})
                return self.respond(404, {'error':'账号接口不存在'})
            if self.path == '/api/settings':
                gid = int(data['group_id'])
                allowed_fields = {'welcome','welcome_photo','block_links','block_photos','block_ads','keywords','verify','checkin_points','invite_points','language'}
                changes = {k:v for k,v in data.items() if k in allowed_fields}
                if not changes:
                    raise ValueError('没有可保存的设置')
                for key in ['block_links','block_photos','block_ads','verify']:
                    if key in changes and type(changes[key]) is not bool:
                        raise ValueError('开关设置无效')
                for key in ['checkin_points','invite_points']:
                    if key in changes and (type(changes[key]) is not int or not 0 <= changes[key] <= 10000):
                        raise ValueError('积分须为 0 至 10000 的整数')
                for key in ['welcome','welcome_photo','keywords','language']:
                    if key in changes and (not isinstance(changes[key], str) or len(changes[key]) > (900 if key=='welcome' else 1000)):
                        raise ValueError('文本格式或长度无效')
                if 'welcome' in changes and not is_english_text(changes['welcome']):
                    raise ValueError('Welcome text must use English')
                photo = changes.get('welcome_photo', '')
                if photo and not (photo.startswith('https://') or re.fullmatch(r'[\w-]+', photo)):
                    raise ValueError('图片请使用 HTTPS URL 或 Telegram file_id')
                if 'language' in changes and changes['language'] != 'en':
                    raise ValueError('机器人对外文案仅提供英文')
                with self.server.store.db() as db:
                    result = db.execute('UPDATE groups SET '+','.join(k+'=?' for k in changes)+' WHERE id=?', [*changes.values(), gid])
                    if not result.rowcount:
                        raise ValueError('社群不存在')
                self.server.store.event(gid, None, '设置更新', '管理员更新了社群规则')
            elif self.path == '/api/revoke':
                with self.server.store.db() as db:
                    invite = db.execute('SELECT * FROM invites WHERE id=?', (int(data['id']),)).fetchone()
                if not invite:
                    raise ValueError('链接不存在')
                if invite['active']:
                    if not self.server.demo:
                        if not self.server.bot.api:
                            raise ValueError('机器人尚未连接，无法撤销真实链接')
                        self.server.bot.api.call('revokeChatInviteLink', chat_id=invite['group_id'], invite_link=invite['url'])
                    with self.server.store.db() as db:
                        db.execute('UPDATE invites SET active=0 WHERE id=?', (invite['id'],))
                    self.server.store.event(invite['group_id'], invite['user_id'], '邀请链接', '已撤销链接，历史邀请保留')
            else:
                return self.respond(404, {'error':'接口不存在'})
            self.respond(200, {'ok': True})
        except AccessDenied as error:
            self.respond(403, {'error':str(error)})
        except (ValueError, KeyError, TypeError) as error:
            message = str(error) if isinstance(error,ValueError) and (self.path.startswith('/api/admins/') or self.path == '/api/account/password') else '输入无效，请检查字段、积分范围和图片地址'
            self.respond(400, {'error':message})
        except TelegramError as e:
            self.respond(502, {'error':str(e)})
        except Exception:
            log.error('API processing failure')
            self.respond(500, {'error':'服务处理失败，请检查服务器存储和运行环境'})


def main():
    load_env()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    password = os.getenv('ADMIN_PASSWORD', '')
    demo = os.getenv('DEMO') == '1'
    hosted = os.getenv('UI_MODE') == 'hosted-preview'
    if hosted and not demo:
        raise SystemExit('hosted-preview is a demonstration and requires DEMO=1. It cannot control real Telegram groups.')
    if hosted and not (ROOT/'frontend'/'hosted.html').exists():
        raise SystemExit('Run python build_preview.py --hosted before starting the hosted preview.')
    path = os.getenv('DATABASE_PATH', 'data/demo.sqlite3' if demo else 'data/community.sqlite3')
    if not Path(path).is_absolute():
        path = ROOT/path
    store = Store(path)
    try:
        Accounts(store, password, os.getenv('ADMIN_USERNAME','admin'))
    except ValueError as error:
        raise SystemExit(str(error)+'；首次启动请在 .env 配置 ADMIN_USERNAME 和至少 12 位 ADMIN_PASSWORD。') from None
    with store.db() as db:
        mode = db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
        if mode and mode[0] != ('demo' if demo else 'live'):
            raise SystemExit('演示与生产模式须使用不同数据库，请设置 DATABASE_PATH。')
        db.execute("INSERT OR REPLACE INTO meta VALUES('mode',?)", ('demo' if demo else 'live',))
    token = '' if hosted else os.getenv('TG_BOT_TOKEN', '')
    bot = Bot(store, Telegram(token) if token and not demo else None)
    if demo:
        seed_demo(store)
        bot.status = '演示模式 · 未调用 Telegram'
    elif token:
        threading.Thread(target=bot.run, daemon=True, name='telegram-polling').start()
    server = AppServer((os.getenv('HOST', '127.0.0.1'), int(os.getenv('PORT','8000'))), store, password, bot, demo)
    log.info('Community OS started on port %s (%s)', server.server_port, 'demo' if demo else 'live')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == '__main__':
    main()
