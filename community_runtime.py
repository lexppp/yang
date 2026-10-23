"""Persistent group verification and server-side Telegram announcement scheduling."""
from datetime import datetime, timedelta, timezone
import json
import random
import re
import secrets
import threading
import time
from urllib.parse import urlsplit,unquote
from group_links import resolve,visible_sql

TZ = timezone(timedelta(hours=8))
SEND_PERMISSIONS = {k: True for k in ('can_send_messages','can_send_audios','can_send_documents',
    'can_send_photos','can_send_videos','can_send_video_notes','can_send_voice_notes',
    'can_send_polls','can_send_other_messages','can_add_web_page_previews')}


def initialize(db):
    columns = {r[1] for r in db.execute('PRAGMA table_info(groups)')}
    if 'verify_minutes' not in columns:
        db.execute('ALTER TABLE groups ADD COLUMN verify_minutes INTEGER NOT NULL DEFAULT 4')
    for name, default in (('welcome_delete_minutes',4),('link_delete_minutes',2)):
        if name not in columns:
            db.execute(f'ALTER TABLE groups ADD COLUMN {name} INTEGER NOT NULL DEFAULT {default}')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS bot_message_cleanup(
      group_id INTEGER NOT NULL, message_id INTEGER NOT NULL, user_id INTEGER, kind TEXT NOT NULL,
      created_at REAL NOT NULL, delete_at REAL NOT NULL, next_attempt REAL NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
      last_error TEXT NOT NULL DEFAULT '', PRIMARY KEY(group_id,message_id));
    CREATE INDEX IF NOT EXISTS bot_message_cleanup_due ON bot_message_cleanup(status,next_attempt);
    CREATE TABLE IF NOT EXISTS member_arrivals(
      group_id INTEGER, user_id INTEGER, joined_at REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1,
      status TEXT NOT NULL, nonce TEXT, answer TEXT, expires REAL, prompt_id INTEGER,
      inviter_id INTEGER, restore_permissions TEXT, cleanup_after REAL NOT NULL DEFAULT 0,
      PRIMARY KEY(group_id,user_id));
    CREATE UNIQUE INDEX IF NOT EXISTS arrival_nonce ON member_arrivals(nonce);
    CREATE TABLE IF NOT EXISTS forum_topics(
      group_id INTEGER, topic_id INTEGER, name TEXT NOT NULL,
      PRIMARY KEY(group_id,topic_id));
    CREATE TABLE IF NOT EXISTS announcement_plans(
      id INTEGER PRIMARY KEY, title TEXT NOT NULL, group_id INTEGER NOT NULL, topic_id INTEGER,
      body TEXT NOT NULL, image TEXT NOT NULL DEFAULT '', pin INTEGER NOT NULL DEFAULT 0,
      mode TEXT NOT NULL, once_at TEXT, times TEXT NOT NULL DEFAULT '[]',
      start_date TEXT, end_date TEXT, status TEXT NOT NULL, next_run TEXT,
      last_error TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS announcement_deliveries(
      id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL, title TEXT NOT NULL,
      group_id INTEGER NOT NULL, topic_id INTEGER, scheduled_at TEXT NOT NULL,
      processed_at TEXT, status TEXT NOT NULL, message_id INTEGER,
      pin_status TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
      UNIQUE(plan_id,scheduled_at));
    ''')


def snapshot(db, gid=None):
    where, args = ('WHERE group_id=? AND '+visible_sql(), [gid]) if gid is not None else ('WHERE '+visible_sql(), [])
    plans = [dict(r) for r in db.execute(f'SELECT * FROM announcement_plans {where} ORDER BY id DESC LIMIT 200',args)]
    for p in plans:
        p['times'] = json.loads(p['times'])
    deliveries = [dict(r) for r in db.execute(f'SELECT * FROM announcement_deliveries {where} ORDER BY id DESC LIMIT 200',args)]
    topics = [dict(r) for r in db.execute(f'SELECT * FROM forum_topics {where} ORDER BY topic_id',args)]
    arrivals=[dict(r) for r in db.execute(f'''SELECT a.group_id,a.user_id,a.status,a.inviter_id,a.invite_url,a.joined_at,
      COALESCE(m.name,CAST(a.user_id AS TEXT)) name,
      (SELECT r.inviter_id FROM referrals r WHERE r.group_id=a.group_id AND r.user_id=a.user_id) credited_inviter_id,
      EXISTS(SELECT 1 FROM referrals r WHERE r.group_id=a.group_id AND r.user_id=a.user_id) counted
      FROM member_arrivals a LEFT JOIN members m ON m.group_id=a.group_id AND m.user_id=a.user_id
      WHERE {('a.group_id=? AND ' if gid is not None else '')+visible_sql('a.group_id')}
      ORDER BY a.joined_at DESC LIMIT 100''',args)]
    return dict(announcements=plans, announcement_deliveries=deliveries, forum_topics=topics,joins=arrivals)


def next_time(plan, after):
    if plan['mode'] == 'now':
        return after.isoformat()
    if plan['mode'] == 'once':
        date = datetime.fromisoformat(plan['once_at'])
        return date.isoformat() if date > after else None
    first = max(after.date(), datetime.fromisoformat(plan['start_date']).date())
    last = datetime.fromisoformat(plan['end_date']).date() if plan.get('end_date') else None
    times = json.loads(plan['times']) if isinstance(plan['times'],str) else plan['times']
    for day in (first, first+timedelta(days=1)):
        if last and day > last:
            return None
        for text in times:
            hour, minute = map(int,text.split(':'))
            value = datetime.combine(day,datetime.min.time(),TZ).replace(hour=hour,minute=minute)
            if value > after:
                return value.isoformat()
    return None


class CommunityRuntime:
    def __init__(self, bot, english):
        self.bot, self.store, self.english = bot, bot.store, english
        self.member_lock, self.announcement_lock = threading.RLock(), threading.RLock()
        self.cleanup_lock = threading.RLock()

    def schedule_cleanup(self, group, message, kind, uid=None):
        """Persist only IDs of our own welcome/link responses, never arbitrary group messages."""
        defaults={'welcome':4,'link':2}
        minutes=group.get(kind+'_delete_minutes',defaults[kind])
        if not minutes or not message:
            return
        mid=message.get('message_id')
        if type(mid) is not int or mid<=0:
            raise ValueError('Telegram did not return a valid message ID for cleanup')
        created=time.time()
        with self.store.db() as db:
            db.execute('INSERT OR IGNORE INTO bot_message_cleanup(group_id,message_id,user_id,kind,created_at,delete_at,next_attempt) VALUES(?,?,?,?,?,?,?)',
                       (group['id'],mid,uid,kind,created,created+minutes*60,created+minutes*60))

    def cleanup_messages(self):
        """Restart-safe, bounded retries. Physical chat/message IDs must never be migrated."""
        if not self.bot.api:
            return
        with self.cleanup_lock:
            at=time.time()
            with self.store.db() as db:
                rows=[dict(r) for r in db.execute("SELECT * FROM bot_message_cleanup WHERE status='pending' AND next_attempt<=? ORDER BY next_attempt LIMIT 20",(at,))]
            for row in rows:
                # Disconnect and migration use these locks too. Stop processing detached groups.
                with self.member_lock:
                    with self.store.db() as db:
                        canonical=resolve(db,row['group_id'])
                        managed=db.execute('SELECT disconnected FROM groups WHERE id=?',(canonical,)).fetchone()
                    if not managed or managed['disconnected']:
                        with self.store.db() as db:
                            db.execute("UPDATE bot_message_cleanup SET status='cancelled' WHERE group_id=? AND message_id=?",(row['group_id'],row['message_id']))
                        continue
                    error=None
                    if time.time()-row['created_at']>=48*3600:
                        error=ValueError('Telegram deletion window (48 hours) has expired')
                    else:
                        try:
                            self.bot.api.call('deleteMessage',chat_id=row['group_id'],message_id=row['message_id'])
                        except Exception as caught:
                            if 'message to delete not found' not in str(caught).lower():
                                error=caught
                    if error is None:
                        with self.store.db() as db:
                            db.execute('DELETE FROM bot_message_cleanup WHERE group_id=? AND message_id=?',(row['group_id'],row['message_id']))
                        continue
                    detail=str(error)[:500]
                    attempts=row['attempts']+1
                    terminal=attempts>=24 or time.time()-row['created_at']>=48*3600 or any(text in detail.lower() for text in ("message can't be deleted",'message cannot be deleted','migrated to a supergroup'))
                    delay=min(30*2**min(attempts-1,7),3600)
                    retry_after=getattr(error,'parameters',{}).get('retry_after')
                    if type(retry_after) is int and retry_after>0:
                        delay=max(delay,min(retry_after+1,86400))
                    with self.store.db() as db:
                        db.execute('UPDATE bot_message_cleanup SET attempts=?,status=?,last_error=?,next_attempt=? WHERE group_id=? AND message_id=?',
                                   (attempts,'failed' if terminal else 'pending',detail,time.time()+delay,row['group_id'],row['message_id']))
                    if attempts==1 or terminal:
                        label='欢迎语' if row['kind']=='welcome' else '邀请链接消息'
                        self.store.event(canonical,row['user_id'],'自动清理失败' if terminal else '自动清理重试',
                                         label+f" · message_id={row['message_id']} · "+detail+('；请检查删除消息权限或手动删除' if terminal else '；稍后自动重试，请检查删除消息权限和网络'))
            with self.store.db() as db:
                db.execute("DELETE FROM bot_message_cleanup WHERE status IN ('failed','cancelled') AND created_at<?",(time.time()-30*86400,))

    def inviter(self, gid, link):
        if not link:
            return None
        def code(url):
            parsed=urlsplit(url.strip())
            if parsed.hostname not in ('t.me','www.t.me','telegram.me'):
                return url.strip()
            path=unquote(parsed.path).rstrip('/')
            return path.replace('/joinchat/','/+',1)
        with self.store.db() as db:
            normalized=code(link)
            variants={link.strip(),'https://t.me'+normalized,'https://telegram.me'+normalized}
            if normalized.startswith('/+'):
                variants.add('https://t.me/joinchat/'+normalized[2:])
            rows=[dict(r) for r in db.execute('SELECT group_id,user_id,url FROM invites WHERE active=1 AND url IN ('+','.join('?' for _ in variants)+')',list(variants))]
        for row in rows:
            if code(row['url'])!=code(link):continue
            with self.store.db() as db:
                canonical=resolve(db,row['group_id'])
                target=resolve(db,gid)
            if canonical!=target:
                self.prove_migration(row['group_id'],target)
                with self.store.db() as db:canonical=resolve(db,row['group_id'])
            if canonical==target:return row['user_id']
        return None

    def prove_migration(self, old, expected=None):
        if not self.bot.api:return False
        new=None
        try:
            info=self.bot.api.call('getChat',chat_id=old)
            if isinstance(info,dict) and info.get('id') not in (None,old):new=info['id']
        except Exception as error:
            new=getattr(error,'parameters',{}).get('migrate_to_chat_id')
        if type(new) is not int or (expected is not None and new!=expected):return False
        with self.member_lock,self.announcement_lock:
            self.store.migrate_group(old,new)
        return True

    def probe_migrations(self):
        with self.store.db() as db:
            rows=db.execute("SELECT id FROM groups WHERE disconnected=0 AND id<0 AND id>-1000000000000 AND id NOT IN (SELECT old_id FROM group_migrations) AND migration_probe<? ORDER BY migration_probe LIMIT 10",(time.time()-3600,)).fetchall()
            for row in rows:db.execute('UPDATE groups SET migration_probe=? WHERE id=?',(time.time(),row['id']))
        for row in rows:self.prove_migration(row['id'])

    def award(self,gid,uid,inviter):
        if inviter is None:
            self.store.event(gid,uid,'邀请未归因','已入群/验证，但缺少可匹配的个人邀请链接；未猜测邀请人')
            return False
        if self.store.referral(gid,uid,inviter):return True
        self.store.event(gid,uid,'邀请未重复计分','自己邀请自己，或此成员在该社群已有有效邀请记录')
        return False

    def repair_referrals(self):
        with self.store.db() as db:
            rows=[dict(r) for r in db.execute("SELECT a.* FROM member_arrivals a WHERE status IN ('verified','not_required') AND (invite_url IS NOT NULL OR inviter_id IS NOT NULL) AND (inviter_id IS NULL OR inviter_id<>user_id) AND NOT EXISTS(SELECT 1 FROM referrals r WHERE r.group_id=a.group_id AND r.user_id=a.user_id) AND "+visible_sql('a.group_id'))]
        for row in rows:
            inviter=row['inviter_id'] if row['inviter_id'] is not None else self.inviter(row['group_id'],row['invite_url'])
            if inviter is None:continue
            with self.store.db() as db:db.execute('UPDATE member_arrivals SET inviter_id=? WHERE group_id=? AND user_id=?',(inviter,row['group_id'],row['user_id']))
            self.award(row['group_id'],row['user_id'],inviter)

    def manual_attribution(self, body, actor):
        gid,uid,invite_id=int(body['group_id']),int(body['user_id']),int(body['invite_id'])
        reason=body.get('reason','')
        if not isinstance(reason,str) or not 5<=len(reason.strip())<=300:
            raise ValueError('请填写5至300字的核实依据')
        with self.member_lock:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                gid=resolve(db,gid)
                row=db.execute("SELECT * FROM member_arrivals WHERE group_id=? AND user_id=? AND status IN ('verified','not_required') AND inviter_id IS NULL AND "+visible_sql(),(gid,uid)).fetchone()
                invite=db.execute('SELECT * FROM invites WHERE id=? AND group_id=?',(invite_id,gid)).fetchone()
                if not row or not invite or uid==invite['user_id']:
                    raise ValueError('请选择已验证但来源缺失的成员及其真实邀请人，不能自邀')
                if db.execute('SELECT 1 FROM referrals WHERE group_id=? AND user_id=?',(gid,uid)).fetchone():
                    raise ValueError('此成员已经计入邀请，不能重复补记')
                db.execute('UPDATE member_arrivals SET inviter_id=?,invite_url=? WHERE group_id=? AND user_id=?',
                           (invite['user_id'],invite['url'],gid,uid))
            credited=self.store.referral(gid,uid,invite['user_id'])
            self.store.event(gid,uid,'人工补记邀请',actor['username']+' 核实邀请人 '+str(invite['user_id'])+'；依据：'+reason.strip())
        return {'ok':True,'credited':credited}

    def observe_topic(self, message):
        topic = message.get('message_thread_id')
        if not topic:
            return
        name = (message.get('forum_topic_created') or message.get('forum_topic_edited') or {}).get('name')
        with self.store.db() as db:
            db.execute('INSERT INTO forum_topics VALUES(?,?,?) ON CONFLICT(group_id,topic_id) DO UPDATE SET name=CASE WHEN ? IS NULL THEN forum_topics.name ELSE excluded.name END',
                       (message['chat']['id'],topic,name or f'Topic {topic}',name))

    def welcome(self, group, user):
        try:
            self.bot.welcome(group,user)
        except Exception as error:
            self.store.event(group['id'],user['id'],'欢迎图片失败',str(error))
            # A bad photo must not suppress the welcome text or verification.
            if group.get('welcome_photo'):
                try:
                    self.bot.welcome({**group,'welcome_photo':''},user)
                except Exception as fallback:
                    self.store.event(group['id'],user['id'],'欢迎词失败',str(fallback))

    def arrive(self, group, user, source_time=None, inviter=None, invite_url=None):
        if user.get('is_bot'):
            return
        gid, uid = group['id'], user['id']
        source_time = source_time or time.time()
        with self.member_lock:
            with self.store.db() as db:
                managed=db.execute('SELECT disconnected FROM groups WHERE id=?',(gid,)).fetchone()
            if not managed or managed['disconnected']:return
            self.store.member(gid,user,True)
            with self.store.db() as db:
                old=db.execute('SELECT * FROM member_arrivals WHERE group_id=? AND user_id=?',(gid,uid)).fetchone()
                if old and old['active'] and source_time <= old['joined_at']+60:
                    if inviter is not None:
                        db.execute('UPDATE member_arrivals SET inviter_id=COALESCE(inviter_id,?) WHERE group_id=? AND user_id=?',(inviter,gid,uid))
                    if invite_url:
                        db.execute('UPDATE member_arrivals SET invite_url=COALESCE(invite_url,?) WHERE group_id=? AND user_id=?',(invite_url,gid,uid))
                    duplicate=dict(old)
                else:
                    duplicate=None
            if duplicate:
                if duplicate['status'] in ('verified','not_required') and inviter is not None:
                    self.award(gid,uid,inviter)
                return
            if old and old['prompt_id']:
                self.clean_prompt(dict(old))
            status='pending' if group['verify'] else 'not_required'
            with self.store.db() as db:
                db.execute('INSERT OR REPLACE INTO member_arrivals(group_id,user_id,joined_at,status,inviter_id,invite_url) VALUES(?,?,?,?,?,?)',
                           (gid,uid,source_time,status,inviter,invite_url))
            restricted=False
            if group['verify']:
                try:
                    member=self.bot.api.call('getChatMember',chat_id=gid,user_id=uid)
                    if member['status'] in ('administrator','creator'):
                        status='not_required'
                    else:
                        restore={k:v for k,v in member.items() if k.startswith('can_')} if member['status']=='restricted' else None
                        self.bot.api.call('restrictChatMember',chat_id=gid,user_id=uid,
                                          permissions={'can_send_messages':False},until_date=0,use_independent_chat_permissions=True)
                        restricted=True
                        with self.store.db() as db:
                            db.execute('UPDATE member_arrivals SET restore_permissions=? WHERE group_id=? AND user_id=?',
                                       (json.dumps(restore) if restore is not None else None,gid,uid))
                except Exception as error:
                    status='error'
                    self.store.event(gid,uid,'群内验证权限错误','需要超级群及限制成员权限；'+str(error))
            self.welcome(group,user)
            if restricted:
                a,b=random.randint(2,9),random.randint(2,9)
                nonce=secrets.token_hex(8)
                minutes=group.get('verify_minutes',4)
                with self.store.db() as db:
                    db.execute('UPDATE member_arrivals SET nonce=?,answer=?,expires=? WHERE group_id=? AND user_id=?',
                               (nonce,str(a+b),time.time()+minutes*60,gid,uid))
                choices=[a+b,a+b+1,a+b-1];random.shuffle(choices)
                try:
                    name=user.get('first_name','New member')
                    name=name if self.english(name) else 'New member'
                    result=self.bot.send(gid,f'{name}, please verify: {a} + {b} = ?\nYou have {minutes} minute(s). You remain muted until verified. This message will disappear when time expires.',
                        reply_markup={'inline_keyboard':[[{'text':str(n),'callback_data':f'gv:{nonce}:{n}'} for n in choices]]})
                    with self.store.db() as db:
                        db.execute('UPDATE member_arrivals SET prompt_id=? WHERE group_id=? AND user_id=?',(result['message_id'],gid,uid))
                except Exception as error:
                    self.store.event(gid,uid,'验证消息失败',str(error))
            else:
                with self.store.db() as db:
                    db.execute('UPDATE member_arrivals SET status=? WHERE group_id=? AND user_id=?',(status,gid,uid))
                if status=='not_required':
                    self.award(gid,uid,inviter)

    def leave(self, gid, user):
        self.store.member(gid,user,False)
        with self.store.db() as db:
            db.execute("UPDATE member_arrivals SET active=0,status='left' WHERE group_id=? AND user_id=?",(gid,user['id']))

    def clean_prompt(self, row):
        if not row.get('prompt_id'):
            return
        with self.store.db() as db:
            db.execute('UPDATE member_arrivals SET cleanup_after=? WHERE group_id=? AND user_id=?',(time.time()+30,row['group_id'],row['user_id']))
        try:
            self.bot.api.call('deleteMessage',chat_id=row['group_id'],message_id=row['prompt_id'])
        except Exception as error:
            if 'message to delete not found' not in str(error).lower():
                self.store.event(row['group_id'],row['user_id'],'验证消息清理失败',str(error))
                return
        with self.store.db() as db:
            db.execute('UPDATE member_arrivals SET prompt_id=NULL WHERE group_id=? AND user_id=? AND prompt_id=?',
                       (row['group_id'],row['user_id'],row['prompt_id']))

    def callback(self, cb):
        parts=cb.get('data','').split(':')
        if len(parts)!=3 or parts[0]!='gv':
            return
        def answer(text):
            self.bot.api.call('answerCallbackQuery',callback_query_id=cb['id'],text=text)
        with self.member_lock:
            with self.store.db() as db:
                row=db.execute('SELECT * FROM member_arrivals WHERE nonce=?',(parts[1],)).fetchone()
            if not row or row['user_id']!=cb['from']['id']:
                return answer('This verification belongs to another member or is no longer available.')
            row=dict(row)
            with self.store.db() as db:
                managed=db.execute('SELECT 1 FROM groups WHERE id=? AND disconnected=0',(row['group_id'],)).fetchone()
            if not managed:return answer('This group is no longer managed here. Please contact a group administrator.')
            if row['status']!='pending' or row['expires']<=time.time():
                self.expire()
                return answer('This verification has ended. Contact an administrator if you are still muted.')
            if not secrets.compare_digest(row['answer'],parts[2]):
                return answer('Incorrect answer. Please try again before time expires.')
            if row['restore_permissions'] is not None:
                permissions=json.loads(row['restore_permissions'])
            else:
                chat=self.bot.api.call('getChat',chat_id=row['group_id'])
                permissions=chat.get('permissions',SEND_PERMISSIONS)
            self.bot.api.call('restrictChatMember',chat_id=row['group_id'],user_id=row['user_id'],
                              permissions=permissions,until_date=0,use_independent_chat_permissions=True)
            with self.store.db() as db:
                db.execute("UPDATE member_arrivals SET status='verified' WHERE nonce=?",(row['nonce'],))
            self.award(row['group_id'],row['user_id'],row['inviter_id'])
            self.clean_prompt(row)
            self.store.event(row['group_id'],row['user_id'],'群内验证成功','已恢复群内发言权限；有效邀请仅奖励一次')
            answer('Verification successful. You may now participate in the community!')

    def expire(self):
        with self.member_lock:
            with self.store.db() as db:
                due=[dict(r) for r in db.execute("SELECT * FROM member_arrivals WHERE status='pending' AND expires<=? AND "+visible_sql(),(time.time(),))]
                db.execute("UPDATE member_arrivals SET status='timed_out' WHERE status='pending' AND expires<=? AND "+visible_sql(),(time.time(),))
                cleanup=[dict(r) for r in db.execute("SELECT * FROM member_arrivals WHERE status IN ('timed_out','verified','left') AND prompt_id IS NOT NULL AND cleanup_after<=? AND "+visible_sql(),(time.time(),))]
            for row in due:
                self.store.event(row['group_id'],row['user_id'],'验证超时','验证消息将移除，用户保持禁言，不踢出社群')
            for row in cleanup:
                self.clean_prompt(row)

    def save_announcement(self, body, actor):
        def text(key, limit, required=False):
            value=body.get(key,'')
            if not isinstance(value,str) or len(value)>limit or (required and not value.strip()):
                raise ValueError('公告字段无效：'+key)
            return value.strip()
        title=text('title',100,True);content=text('body',4096,True);image=text('image',1000)
        if not self.english(content):
            raise ValueError('Telegram announcement text must use English.')
        if image and (not (image.startswith('https://') or re.fullmatch(r'[\w-]+',image)) or len(content)>1024 or body.get('image_english_confirmed') is not True):
            raise ValueError('图片须为 HTTPS 地址或 /fileid，图文正文最多1024字，并确认图片为英文或无文字')
        gid=int(body['group_id']);topic=body.get('topic_id') or None
        if topic is not None and (type(topic) is not int or topic<1):
            raise ValueError('话题 ID 须为正整数')
        with self.store.db() as db:
            if not db.execute('SELECT 1 FROM groups WHERE id=? AND active=1 AND disconnected=0',(gid,)).fetchone():
                raise ValueError('请选择已连接社群')
        mode=body.get('mode','now')
        if mode not in ('now','once','daily'):
            raise ValueError('发布模式无效')
        if type(body.get('pin',False)) is not bool or type(body.get('activate',False)) is not bool:
            raise ValueError('发布开关无效')
        at=datetime.now(TZ);once=None;start=body.get('start_date') or at.date().isoformat();end=body.get('end_date') or None
        times=body.get('times',[])
        if mode=='once':
            once=datetime.fromisoformat(body.get('once_at',''))
            once=once.replace(tzinfo=TZ) if once.tzinfo is None else once.astimezone(TZ)
            once=once.isoformat()
        if mode=='daily':
            if not isinstance(times,list) or not 1<=len(times)<=24 or any(not isinstance(t,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',t) for t in times) or len(set(times))!=len(times):
                raise ValueError('每天设置1至24个不重复的 HH:mm 时间')
            datetime.fromisoformat(start)
            if end and (datetime.fromisoformat(end)<datetime.fromisoformat(start)):
                raise ValueError('结束日期不能早于开始日期')
        else:
            times=[]
        plan=dict(title=title,group_id=gid,topic_id=topic,body=content,image=image,pin=int(body.get('pin',False)),
                  mode=mode,once_at=once,times=json.dumps(sorted(times)),start_date=start,end_date=end)
        next_run=next_time(plan,at) if body.get('activate') else None
        if body.get('activate') and not next_run:
            raise ValueError('没有未来可执行的发布时间')
        if body.get('activate') and not self.bot.api:
            raise ValueError('机器人尚未真实连接，不能启用公告发布')
        plan.update(status='scheduled' if next_run else 'draft',next_run=next_run,last_error='',updated=at.isoformat())
        with self.announcement_lock, self.store.db() as db:
            if body.get('id'):
                pid=int(body['id'])
                if not db.execute('SELECT 1 FROM announcement_plans WHERE id=?',(pid,)).fetchone():
                    raise ValueError('公告不存在')
                db.execute('UPDATE announcement_plans SET '+','.join(k+'=?' for k in plan)+' WHERE id=?',[*plan.values(),pid])
            else:
                plan['created']=at.isoformat()
                pid=db.execute('INSERT INTO announcement_plans('+','.join(plan)+') VALUES('+','.join('?' for _ in plan)+')',list(plan.values())).lastrowid
        self.store.event(gid,None,'公告任务',actor['username']+' 保存公告 '+title)
        if mode=='now' and next_run:
            self.process_announcements()
        return {'ok':True,'id':pid}

    def announcement_action(self, action, pid):
        with self.announcement_lock, self.store.db() as db:
            row=db.execute('SELECT * FROM announcement_plans WHERE id=?',(int(pid),)).fetchone()
            if not row:
                raise ValueError('公告不存在')
            status={'pause':'paused','cancel':'cancelled','resume':'scheduled'}.get(action)
            if status is None:
                raise ValueError('公告操作无效')
            next_run=next_time(dict(row),datetime.now(TZ)) if action=='resume' else None
            if action=='resume' and (not next_run or not self.bot.api):
                raise ValueError('无可执行时间或机器人未连接')
            if action=='resume' and not db.execute('SELECT 1 FROM groups WHERE id=? AND disconnected=0 AND active=1',(row['group_id'],)).fetchone():
                raise ValueError('目标社群已解除连接或机器人未授权')
            db.execute('UPDATE announcement_plans SET status=?,next_run=?,updated=? WHERE id=?',(status,next_run,datetime.now(TZ).isoformat(),pid))
        return {'ok':True}

    def process_announcements(self, at=None):
        if not self.bot.api:
            return
        at=at or datetime.now(TZ)
        with self.announcement_lock:
            with self.store.db() as db:
                plans=[dict(r) for r in db.execute("SELECT * FROM announcement_plans WHERE status='scheduled' AND next_run<=? AND "+visible_sql()+" ORDER BY next_run LIMIT 20",(at.isoformat(),))]
            for plan in plans:
                due=plan['next_run']
                following=next_time(plan,at) if plan['mode']=='daily' else None
                with self.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    claimed=db.execute("INSERT OR IGNORE INTO announcement_deliveries(plan_id,title,group_id,topic_id,scheduled_at,status) VALUES(?,?,?,?,?,'sending')",
                        (plan['id'],plan['title'],plan['group_id'],plan['topic_id'],due)).rowcount
                    db.execute('UPDATE announcement_plans SET next_run=?,status=? WHERE id=?',(following,'scheduled' if following else 'completed',plan['id']))
                if not claimed:
                    continue  # Crash during delivery is never retried automatically; it could duplicate a real message.
                status,error,message_id,pin_status='sent','',None,''
                if (at-datetime.fromisoformat(due)).total_seconds()>300:
                    status='missed';error='服务器错过时段超过5分钟，未自动补发'
                else:
                    try:
                        params={'chat_id':plan['group_id']}
                        if plan['topic_id']:
                            params['message_thread_id']=plan['topic_id']
                        if plan['image']:
                            result=self.bot.api.call('sendPhoto',photo=plan['image'],caption=plan['body'],**params)
                        else:
                            result=self.bot.send(plan['group_id'],plan['body'],**({'message_thread_id':plan['topic_id']} if plan['topic_id'] else {}))
                        message_id=result['message_id']
                        with self.store.db() as db:
                            db.execute("UPDATE announcement_deliveries SET message_id=?,status='sent' WHERE plan_id=? AND scheduled_at=?",(message_id,plan['id'],due))
                        if plan['pin']:
                            try:
                                self.bot.api.call('pinChatMessage',chat_id=plan['group_id'],message_id=message_id,disable_notification=True)
                                pin_status='pinned'
                            except Exception as failure:
                                status='sent_pin_failed';pin_status='failed';error=str(failure)
                    except Exception as failure:
                        status='unknown';error='发送未确认，请先到群内核对，系统不会自动重发。'+str(failure)
                with self.store.db() as db:
                    db.execute('UPDATE announcement_deliveries SET status=?,error=?,message_id=?,pin_status=?,processed_at=? WHERE plan_id=? AND scheduled_at=?',
                        (status,error,message_id,pin_status,datetime.now(TZ).isoformat(),plan['id'],due))
                    db.execute('UPDATE announcement_plans SET last_error=? WHERE id=?',(error,plan['id']))
                self.store.event(plan['group_id'],None,'公告发布',plan['title']+' · '+status+(' · '+error if error else ''))

    def start(self):
        def loop(operation, interval):
            while True:
                try:
                    operation()
                except Exception:
                    self.store.event(None,None,'后台任务错误','请检查机器人权限、存储和 Telegram 网络')
                time.sleep(interval)
        threading.Thread(target=loop,args=(self.expire,1),daemon=True,name='group-verification-cleanup').start()
        threading.Thread(target=loop,args=(self.cleanup_messages,1),daemon=True,name='welcome-link-cleanup').start()
        threading.Thread(target=loop,args=(self.process_announcements,2),daemon=True,name='announcement-scheduler').start()
        threading.Thread(target=loop,args=(self.probe_migrations,300),daemon=True,name='group-migration-repair').start()
        threading.Thread(target=loop,args=(self.repair_referrals,30),daemon=True,name='invitation-credit-repair').start()
