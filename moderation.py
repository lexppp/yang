"""Deterministic spam rules and persistent moderation actions; no model/API token cost."""
import hashlib
import re
import threading
import time
import unicodedata

BOOL_DEFAULTS={'block_mentions':1,'block_buttons':1,'block_forwards':1,'block_sender_chat':1,
               'block_repeats':1,'block_flood':1}
SETTINGS=(*BOOL_DEFAULTS,'allowed_mentions','spam_mute_minutes','spam_strikes')


def initialize(db):
    columns={r[1] for r in db.execute('PRAGMA table_info(groups)')}
    for name,default in BOOL_DEFAULTS.items():
        if name not in columns:db.execute(f'ALTER TABLE groups ADD COLUMN {name} INTEGER NOT NULL DEFAULT {default}')
    for name,decl in [('allowed_mentions',"TEXT NOT NULL DEFAULT ''"),('spam_mute_minutes','INTEGER NOT NULL DEFAULT 1440'),('spam_strikes','INTEGER NOT NULL DEFAULT 2')]:
        if name not in columns:db.execute(f'ALTER TABLE groups ADD COLUMN {name} {decl}')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS moderation_seen(
      group_id INTEGER,message_id INTEGER,user_id INTEGER,created REAL,signature TEXT,
      PRIMARY KEY(group_id,message_id));
    CREATE INDEX IF NOT EXISTS moderation_seen_user ON moderation_seen(group_id,user_id,created);
    CREATE TABLE IF NOT EXISTS moderation_violations(
      group_id INTEGER,message_id INTEGER,user_id INTEGER,reason TEXT,created REAL,
      PRIMARY KEY(group_id,message_id));
    CREATE INDEX IF NOT EXISTS moderation_violations_user ON moderation_violations(group_id,user_id,created);
    CREATE TABLE IF NOT EXISTS moderation_jobs(
      group_id INTEGER,message_id INTEGER,kind TEXT,user_id INTEGER,until_date INTEGER,
      created REAL,next_attempt REAL,attempts INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'pending',error TEXT NOT NULL DEFAULT '',
      PRIMARY KEY(group_id,message_id,kind));
    CREATE INDEX IF NOT EXISTS moderation_jobs_due ON moderation_jobs(status,next_attempt);
    ''')


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',str(text)).casefold()
                   if unicodedata.category(c) not in ('Cf','Mn','Me'))


def compact(text):
    return ''.join(c for c in normalize(text) if c.isalnum())


def split_words(text):
    return [normalize(w.strip()).lstrip('@') for w in re.split(r'[,，;；\n\s]+',text) if w.strip()]


def moderation_reason(group,message):
    text=normalize(message.get('text',message.get('caption','')))
    entities=message.get('entities',[])+message.get('caption_entities',[])
    buttons=[button for row in message.get('reply_markup',{}).get('inline_keyboard',[]) for button in row]
    if group.get('block_buttons',1) and any(any(k in b for k in ('url','login_url','web_app')) for b in buttons):
        return '按钮引流链接'
    if group.get('block_links') and (any(e.get('type') in ('url','text_link') for e in entities) or
        re.search(r'https?\s*://|tg://|(?:t|telegram)\s*\.\s*me/|www\s*\.|\b[\w-]+\s*\.\s*(?:com|net|org|io|xyz|top|cc|site|online|vip|app|me|info|biz|co|cn|ru|club|shop|fun|pro|link|finance)\b',text)):
        return '包含链接'
    if group.get('block_mentions',1) and not re.fullmatch(r'/(?:link|ink)(?:@[a-z0-9_]+)?',text.strip()):
        allowed=set(split_words(group.get('allowed_mentions','')))
        mentions=set(re.findall(r'(?<![\w@])@([a-z0-9_]{3,32})\b',text))
        for entity in entities:
            if entity.get('type')=='text_mention':
                user=entity.get('user',{})
                if 'id:'+str(user.get('id')) not in allowed and normalize(user.get('username','')) not in allowed:
                    return '成员提及引流（未在白名单）'
        if mentions-allowed:
            return '@用户名引流（未在白名单）'
    if group.get('block_forwards',1) and any(k in message for k in ('forward_origin','forward_date','forward_from','forward_from_chat')):
        return '普通成员转发消息'
    if group.get('block_photos') and ('photo' in message or message.get('document',{}).get('mime_type','').startswith('image/')):
        return '包含图片'
    if group.get('block_ads'):
        squeezed=compact(text)
        words=[w.strip() for w in re.split(r'[,，;；\n]',group.get('keywords','')) if w.strip()]
        for word in words:
            key=normalize(word)
            if not compact(key):continue
            if (re.search(r'(?<!\w)'+re.escape(key)+r'(?!\w)',text) if key.isascii() and key.isalnum() else compact(key) in squeezed):
                return '命中广告关键词'
        if any(w in squeezed for w in ('刷单','代实名','代做kyc','买卖账号','出售账号','收购账号','帮忙收钱','帮忙收款')):
            return '账号交易或代收款广告'
        if re.search(r'(?:日入|日赚|日收|每天(?:收入|赚)|每日(?:收入)?|一天(?:稳)?赚|月赚|月入)[\s:：+￥¥$€]*\d+',squeezed) or re.search(r'几分钟.{0,6}(?:搞|赚|挣)\s*\d+',text):
            return '高收益招揽广告'
        if re.search(r'(?:稳赚|稳拿|保本|黑产|搬砖|项目|带单)',text) and re.search(r'私[聊信]|看我|看简[介历]|看主页|跟我|带你|联系|老板|担保',text):
            return '收益招揽及私聊引流广告'
        if re.search(r'\b(?:wtb|buy(?:ing)?|sell(?:ing)?|buyers?|sellers?)\b',text) and re.search(r'\b(?:accounts?|kyc|identity verification)\b',text):
            return '英文账号/KYC买卖广告'
        if re.search(r'\b(?:guaranteed (?:profit|returns|income)|double your (?:money|investment))\b',text):
            return '英文保证收益广告'
    return None


class SpamGuard:
    def __init__(self,bot):
        self.bot,self.store=bot,bot.store
        self.lock=threading.RLock()

    def inspect(self,group,message,user_id=None,edited=False):
        """Called only for non-admin users or disallowed channel senders."""
        reason=moderation_reason(group,message)
        if message.get('sender_chat') and group.get('block_sender_chat',1):
            reason='外部频道身份发言'
        at=time.time();gid=group['id'];mid=message['message_id']
        with self.lock:
            with self.store.db() as db:
                text=compact(message.get('text',message.get('caption','')))
                signature=hashlib.sha256(text.encode()).hexdigest() if len(text)>=24 else ''
                if user_id is not None and not edited:
                    db.execute('INSERT OR IGNORE INTO moderation_seen VALUES(?,?,?,?,?)',(gid,mid,user_id,at,signature))
                    count=db.execute('SELECT COUNT(*) FROM moderation_seen WHERE group_id=? AND user_id=? AND created>?',(gid,user_id,at-10)).fetchone()[0]
                    repeated=db.execute('SELECT COUNT(*) FROM moderation_seen WHERE group_id=? AND user_id=? AND signature=? AND created>?',(gid,user_id,signature,at-600)).fetchone()[0] if signature else 0
                    if not reason and group.get('block_flood',1) and count>8:reason='10秒内超过8条消息（刷屏）'
                    if not reason and group.get('block_repeats',1) and repeated>=3:reason='10分钟内重复发布相同长文本'
                if not reason:return False
                inserted=db.execute('INSERT OR IGNORE INTO moderation_violations VALUES(?,?,?,?,?)',(gid,mid,user_id,reason,at)).rowcount
                db.execute("INSERT OR IGNORE INTO moderation_jobs(group_id,message_id,kind,user_id,created,next_attempt) VALUES(?,?,'delete',?,?,?)",(gid,mid,user_id,at,at))
                strikes=db.execute('SELECT COUNT(*) FROM moderation_violations WHERE group_id=? AND user_id=? AND created>?',(gid,user_id,at-86400)).fetchone()[0] if user_id is not None else 0
                mute=group.get('spam_mute_minutes',1440)
                if inserted and user_id is not None and mute and strikes>=group.get('spam_strikes',2):
                    db.execute("INSERT OR IGNORE INTO moderation_jobs(group_id,message_id,kind,user_id,until_date,created,next_attempt) VALUES(?,?,'mute',?,?,?,?)",(gid,mid,user_id,int(at+mute*60),at,at))
            if inserted:self.store.event(gid,user_id,'广告拦截',reason+f' · message_id={mid}；已安排删除'+('及禁言' if mute and strikes>=group.get('spam_strikes',2) and user_id is not None else ''))
        self.process(group_id=gid,message_id=mid)
        return True

    def process(self,group_id=None,message_id=None):
        if not self.bot.api:return
        with self.lock:
            with self.store.db() as db:
                query="SELECT * FROM moderation_jobs WHERE status='pending' AND next_attempt<=?"
                args=[time.time()]
                if group_id is not None:query+=' AND group_id=? AND message_id=?';args.extend((group_id,message_id))
                rows=[dict(r) for r in db.execute(query+" ORDER BY CASE kind WHEN 'delete' THEN 0 ELSE 1 END,next_attempt LIMIT 30",args)]
            for row in rows:
                key=(row['group_id'],row['message_id'],row['kind'])
                with self.store.db() as db:
                    group=db.execute('SELECT disconnected FROM groups WHERE id=?',(row['group_id'],)).fetchone()
                if not group or group['disconnected']:
                    self.finish(key,'cancelled');continue
                error=None;terminal=False
                try:
                    if row['kind']=='delete':
                        if time.time()-row['created']>=48*3600:
                            self.finish(key,'failed','已超过Telegram48小时删除窗口');continue
                        if row['user_id'] is not None:
                            member=self.bot.api.call('getChatMember',chat_id=row['group_id'],user_id=row['user_id'])
                            if member['status'] in ('administrator','creator'):
                                with self.store.db() as db:
                                    db.execute('DELETE FROM moderation_violations WHERE group_id=? AND message_id=?',(row['group_id'],row['message_id']))
                                self.finish(key,'cancelled');continue
                        self.bot.api.call('deleteMessage',chat_id=row['group_id'],message_id=row['message_id'])
                    else:
                        if row['until_date']<=time.time()+30:
                            self.finish(key,'cancelled','禁言时段已过，不延长或施加永久禁言');continue
                        member=self.bot.api.call('getChatMember',chat_id=row['group_id'],user_id=row['user_id'])
                        if member['status'] in ('administrator','creator','left','kicked'):
                            self.finish(key,'cancelled');continue
                        # Preserve stronger/longer existing restrictions; never shorten another admin's mute.
                        if member['status']=='restricted' and not member.get('can_send_messages',True):
                            existing=member.get('until_date',0)
                            if existing==0 or existing>=row['until_date']:
                                self.finish(key,'done');continue
                        self.bot.api.call('restrictChatMember',chat_id=row['group_id'],user_id=row['user_id'],permissions={'can_send_messages':False},until_date=row['until_date'],use_independent_chat_permissions=True)
                        self.store.event(row['group_id'],row['user_id'],'广告违规禁言',f"禁言至时间戳 {row['until_date']}；不踢出，可在Telegram由管理员解禁")
                except Exception as caught:
                    if row['kind']=='delete' and 'message to delete not found' in str(caught).lower():pass
                    else:error=caught
                if error is None:self.finish(key,'done');continue
                attempts=row['attempts']+1;detail=str(error)[:500]
                terminal=attempts>=24 or "message can't be deleted" in detail.lower() or bool(getattr(error,'parameters',{}).get('migrate_to_chat_id'))
                delay=min(30*2**min(attempts-1,7),3600)
                retry=getattr(error,'parameters',{}).get('retry_after')
                if type(retry) is int and retry>0:delay=max(delay,min(retry+1,86400))
                with self.store.db() as db:
                    db.execute('UPDATE moderation_jobs SET status=?,attempts=?,error=?,next_attempt=? WHERE group_id=? AND message_id=? AND kind=?',('failed' if terminal else 'pending',attempts,detail,time.time()+delay,*key))
                if attempts==1 or terminal:self.store.event(row['group_id'],row['user_id'],'广告处理失败' if terminal else '广告处理重试',row['kind']+' · '+detail+'；检查删除消息/限制成员权限和网络')

    def finish(self,key,status,error=''):
        with self.store.db() as db:db.execute('UPDATE moderation_jobs SET status=?,error=? WHERE group_id=? AND message_id=? AND kind=?',(status,error,*key))
        if status=='failed':self.store.event(key[0],None,'广告处理失败',error)

    def tick(self):
        self.process()
        with self.store.db() as db:
            db.execute('DELETE FROM moderation_seen WHERE created<?',(time.time()-600,))
            db.execute('DELETE FROM moderation_violations WHERE created<?',(time.time()-7*86400,))
            db.execute("DELETE FROM moderation_jobs WHERE status<>'pending' AND created<?",(time.time()-7*86400,))

    def status(self,gid):
        if not self.bot.api:raise ValueError('机器人尚未连接')
        with self.store.db() as db:
            group=db.execute('SELECT 1 FROM groups WHERE id=? AND disconnected=0',(gid,)).fetchone()
        if not group:raise ValueError('社群未接入或已解除连接')
        identity=self.bot.api.call('getMe')
        member=self.bot.api.call('getChatMember',chat_id=gid,user_id=identity['id'])
        admin=member.get('status')=='administrator'
        with self.store.db() as db:pending=db.execute("SELECT COUNT(*) FROM moderation_jobs WHERE group_id=? AND status='pending'",(gid,)).fetchone()[0]
        return {'username':identity.get('username',''),'administrator':admin,'delete_messages':admin and bool(member.get('can_delete_messages')),
                'restrict_members':admin and bool(member.get('can_restrict_members')),'pending_actions':pending,
                'receives_all_group_messages':admin or bool(identity.get('can_read_all_group_messages'))}
