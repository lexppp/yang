#!/usr/bin/env python3
"""One-shot X RSS -> Telegram worker; systemd timer supplies the schedule."""
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

ACCOUNT = 'CoinWOfficial'
CHANNEL = '@coinwofficialchannel'
BOT = 'CoinWCommunityHubBot'
FEED = 'http://127.0.0.1:1200/twitter/user/' + ACCOUNT
STATE = Path('/var/lib/coinw-x-forward')
PUBLIC = Path('/var/lib/coinw-x-status/status.json')


class Failure(Exception):
    pass


class TelegramFailure(Failure):
    def __init__(self, kind, delay=0):
        super().__init__(kind)
        self.kind, self.delay = kind, delay


class Content(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.images, self.hidden = [], [], 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ('p', 'div', 'br'):
            self.parts.append('\n')
        if tag == 'a':
            href = attrs.get('href', '')
            if href.startswith(('https://', 'http://')):
                self.parts.append(' ' + href + ' ')
        if tag == 'img':
            src = attrs.get('src', '')
            url = urlsplit(src)
            # Telegram downloads public X images; never forward arbitrary local URLs.
            if url.scheme == 'https' and url.hostname == 'pbs.twimg.com' and not url.username and url.port in (None, 443):
                if src not in self.images:
                    self.images.append(src)

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1
        if tag in ('p', 'div') and not self.hidden:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def parse_feed(body):
    if len(body) > 2_000_000 or b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise Failure('feed_rejected')
    try:
        root = ET.fromstring(body)
        if root.tag != 'rss':
            raise ValueError()
        posts = {}
        for item in root.findall('./channel/item'):
            url = urlsplit(item.findtext('link', '').strip())
            match = re.fullmatch(r'/CoinWOfficial/status/(\d+)/?', url.path, re.I)
            if url.hostname not in ('x.com', 'twitter.com', 'www.x.com', 'www.twitter.com') or not match:
                continue
            date = parsedate_to_datetime(item.findtext('pubDate', ''))
            if date.tzinfo is None:
                raise ValueError()
            content = Content()
            content.feed(item.findtext('description', '') or item.findtext('title', ''))
            text = '\n'.join(re.sub(r'[ \t]+', ' ', line).strip() for line in ''.join(content.parts).splitlines()).strip()
            if not text and not content.images:
                continue
            pid = match[1]
            posts[pid] = dict(id=pid, published=date.timestamp(), text=text, images=content.images,
                              url=f'https://x.com/{ACCOUNT}/status/{pid}')
        if not posts:
            raise ValueError()
        return sorted(posts.values(), key=lambda post: (post['published'], int(post['id'])))
    except (ValueError, TypeError, OverflowError, ET.ParseError):
        raise Failure('feed_invalid_or_no_posts') from None


def fetch_feed():
    try:
        # Only the fixed loopback RSSHub endpoint bypasses outbound proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(FEED, timeout=60) as response:
            body = response.read(2_000_001)
        return parse_feed(body)
    except (OSError, ValueError):
        raise Failure('rsshub_unavailable_check_login_or_rate_limit') from None


def telegram(token, method, data):
    if not re.fullmatch(r'\d+:[\w-]+', token, re.ASCII):
        raise Failure('existing_telegram_token_missing')
    request = urllib.request.Request('https://api.telegram.org/bot' + token + '/' + method,
                                     data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read(1_000_000))
    except urllib.error.HTTPError as error:
        if error.code == 429:
            try:
                delay = int(json.loads(error.read(65536)).get('parameters', {}).get('retry_after', 600))
            except (ValueError, TypeError, OSError):
                delay = 600
            raise TelegramFailure('rate_limited', max(600, delay)) from None
        raise TelegramFailure('uncertain' if error.code >= 500 else 'rejected') from None
    except (OSError, ValueError):
        # Telegram may have accepted a timed-out request. Never blindly resend.
        raise TelegramFailure('uncertain') from None
    if not result.get('ok'):
        if result.get('error_code') == 429:
            raise TelegramFailure('rate_limited', max(600, int(result.get('parameters', {}).get('retry_after', 600))))
        raise TelegramFailure('rejected')
    return result['result']


def preflight(call):
    me = call('getMe', {})
    if me.get('username', '').lower() != BOT.lower():
        raise Failure('unexpected_bot')
    chat = call('getChat', {'chat_id': CHANNEL})
    if chat.get('type') != 'channel' or chat.get('username', '').lower() != CHANNEL[1:].lower():
        raise Failure('unexpected_channel')
    member = call('getChatMember', {'chat_id': chat['id'], 'user_id': me['id']})
    if member.get('status') != 'creator' and not (member.get('status') == 'administrator' and member.get('can_post_messages')):
        raise Failure('bot_cannot_post')


def chunks(text, limit=3900):
    result, part, size = [], '', 0
    for char in text:
        width = 2 if ord(char) > 0xffff else 1
        if size + width > limit:
            result.append(part)
            part, size = '', 0
        part += char
        size += width
    if part:
        result.append(part)
    return result


def plan(post):
    button = {'inline_keyboard': [[{'text': 'View on X', 'url': post['url']}]]}
    steps = []
    text = post['text']
    caption = text if len(text.encode('utf-16-le')) // 2 <= 1000 and post['images'] else ''
    if text and not caption:
        for part in chunks(text):
            steps.append(('sendMessage', {'chat_id': CHANNEL, 'text': part, 'reply_markup': button,
                                          'link_preview_options': {'is_disabled': True}}))
    for index, image in enumerate(post['images']):
        data = {'chat_id': CHANNEL, 'photo': image, 'reply_markup': button}
        if index == 0 and caption:
            data['caption'] = caption
        steps.append(('sendPhoto', data))
    return steps


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries(post_id TEXT,part INTEGER,status TEXT NOT NULL,message_id INTEGER,
            PRIMARY KEY(post_id,part));
    ''')
    return db


def get(db, key, default='0'):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def put(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(value)))


def initialize(db, posts, now):
    if get(db, 'enabled_at', ''):
        return False
    # Starting/reinstalling must never backfill feed history.
    with db:
        put(db, 'enabled_at', now)
        put(db, 'next_fetch', now + 600)
        for post in posts:
            db.execute('INSERT OR IGNORE INTO posts VALUES(?,?,?)', (post['id'], json.dumps(post), 'baseline'))
    return True


def cycle(db, posts, call, now):
    if not get(db, 'enabled_at', ''):
        raise Failure('not_initialized')
    with db:
        # Durable sending marker survives process death; uncertain sends require manual inspection.
        db.execute("UPDATE deliveries SET status='uncertain' WHERE status='sending'")
        for post in posts:
            if float(get(db, 'enabled_at')) < post['published'] <= now + 60:
                db.execute('INSERT OR IGNORE INTO posts VALUES(?,?,?)', (post['id'], json.dumps(post), 'pending'))
    if now < float(get(db, 'next_send')):
        return
    for row in db.execute("SELECT * FROM posts WHERE status='pending' ORDER BY CAST(id AS INTEGER)").fetchall():
        steps = plan(json.loads(row['payload']))
        for index, (method, data) in enumerate(steps):
            delivery = db.execute('SELECT status FROM deliveries WHERE post_id=? AND part=?', (row['id'], index)).fetchone()
            if delivery and delivery[0] == 'sent':
                continue
            if delivery and delivery[0] in ('uncertain', 'failed'):
                raise Failure('delivery_requires_inspection_post_' + row['id'])
            with db:
                db.execute('INSERT OR REPLACE INTO deliveries VALUES(?,?,?,NULL)', (row['id'], index, 'sending'))
            try:
                result = call(method, data)
                if not isinstance(result, dict) or not isinstance(result.get('message_id'), int):
                    raise TelegramFailure('uncertain')
            except TelegramFailure as error:
                with db:
                    status = {'rate_limited': 'retry', 'rejected': 'failed'}.get(error.kind, 'uncertain')
                    db.execute('UPDATE deliveries SET status=? WHERE post_id=? AND part=?', (status, row['id'], index))
                    if error.delay:
                        put(db, 'next_send', now + error.delay)
                raise Failure('telegram_' + error.kind + '_post_' + row['id']) from None
            with db:
                db.execute("UPDATE deliveries SET status='sent',message_id=? WHERE post_id=? AND part=?", (result['message_id'], row['id'], index))
        with db:
            db.execute("UPDATE posts SET status='sent' WHERE id=?", (row['id'],))
            put(db, 'last_delivery', time.time())
        print('Forwarded post ' + row['id'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('initialize', 'run', 'status', 'preflight'))
    args = parser.parse_args()
    token = os.environ.get('TG_BOT_TOKEN', '')
    call = lambda method, data: telegram(token, method, data)
    if args.action == 'preflight':
        preflight(call)
        print('Verified bot identity and channel posting permission; no message sent.')
        return
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(connect(STATE / 'forward.sqlite3')) as db:
            if args.action == 'status':
                print('Enabled at UTC:', datetime.fromtimestamp(float(get(db, 'enabled_at')), timezone.utc).isoformat())
                print('Post states:', dict(db.execute('SELECT status,COUNT(*) FROM posts GROUP BY status').fetchall()))
                print('Delivery states:', dict(db.execute('SELECT status,COUNT(*) FROM deliveries GROUP BY status').fetchall()))
                return
            now = time.time()
            if args.action == 'initialize':
                if get(db, 'enabled_at', ''):
                    print('Existing activation and delivery history preserved.')
                    return
                preflight(call)
                posts = fetch_feed()
                if not any(now - 7 * 86400 <= post['published'] <= now + 60 for post in posts):
                    raise Failure('feed_has_no_recent_posts')
                if initialize(db, posts, now):
                    print('Baseline saved. Only posts published after this activation will be sent.')
                else:
                    print('Existing activation and delivery history preserved.')
            else:
                if now < float(get(db, 'next_fetch')):
                    print('Feed cooldown active.')
                    return
                with db:
                    put(db, 'next_fetch', now + 600)
                    put(db, 'last_check', now)
                try:
                    posts = fetch_feed()
                except Failure:
                    with db:
                        failures = int(get(db, 'feed_failures')) + 1
                        put(db, 'feed_failures', failures)
                        put(db, 'next_fetch', now + min(21600, 600 * 2 ** min(failures, 5)))
                    raise
                with db:
                    put(db, 'feed_failures', 0)
                preflight(call)
                cycle(db, posts, call, now)
                with db:
                    put(db, 'last_success', time.time())
                    put(db, 'last_error', '')
                print('Feed check completed.', flush=True)


def publish_status(error=None):
    """Publish only curated public-post metadata, never credentials or raw responses."""
    if not (STATE / 'forward.sqlite3').is_file():
        return
    with closing(connect(STATE / 'forward.sqlite3')) as db:
        if error:
            with db:
                put(db, 'last_error', error)
                put(db, 'last_error_at', time.time())
        report = {'version': 1, 'updated_at': time.time(), 'source': ACCOUNT, 'channel': CHANNEL,
                  'bot': BOT, 'interval_seconds': 600,
                  'counts': dict(db.execute('SELECT status,COUNT(*) FROM posts GROUP BY status').fetchall()),
                  'delivery_counts': dict(db.execute('SELECT status,COUNT(*) FROM deliveries GROUP BY status').fetchall()),
                  'posts': []}
        for key in ('enabled_at', 'last_check', 'last_success', 'last_delivery', 'next_fetch', 'next_send', 'last_error_at'):
            report[key] = float(get(db, key))
        report['last_error'] = get(db, 'last_error', '')
        for row in db.execute('SELECT * FROM posts ORDER BY CAST(id AS INTEGER) DESC LIMIT 100'):
            post = json.loads(row['payload'])
            deliveries = [dict(item) for item in db.execute('SELECT part,status,message_id FROM deliveries WHERE post_id=? ORDER BY part', (row['id'],))]
            report['posts'].append({'id': row['id'], 'published': post['published'], 'text': post['text'][:1200],
                                    'images': len(post['images']), 'status': row['status'], 'deliveries': deliveries})
    PUBLIC.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    PUBLIC.parent.chmod(0o755)
    fd, filename = tempfile.mkstemp(dir=PUBLIC.parent, prefix='.status-')
    try:
        with os.fdopen(fd, 'w') as output:
            os.fchmod(output.fileno(), 0o644)
            json.dump(report, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(filename, PUBLIC)
    finally:
        Path(filename).unlink(missing_ok=True)


if __name__ == '__main__':
    os.umask(0o077)
    failure = None
    try:
        main()
    except (Failure, OSError, ValueError, sqlite3.Error) as error:
        # API errors can include token-bearing URLs. Never print arbitrary exceptions.
        failure = str(error) if isinstance(error, Failure) else 'local_runtime_error'
        print('Stopped: ' + failure, flush=True)
        raise SystemExit(1)
    finally:
        if len(sys.argv) > 1 and sys.argv[1] in ('run', 'initialize'):
            try:
                publish_status(failure)
            except (OSError, ValueError, sqlite3.Error):
                print('Status report could not be published.', flush=True)
