"""Per-group message rewards with durable deduplication, daily caps and spam reversals."""
from datetime import datetime,timedelta,timezone
import hashlib
import html
import re
import time
import unicodedata

from moderation import moderation_reason,normalize

TZ=timezone(timedelta(hours=8))
SETTINGS=('chat_rewards_enabled','chat_reward_points','chat_daily_cap','chat_min_chars')


def current_time():return datetime.now(TZ)


def initialize(db):
    columns={r[1] for r in db.execute('PRAGMA table_info(groups)')}
    for name,default in zip(SETTINGS,(1,5,50,10)):
        if name not in columns:db.execute(f'ALTER TABLE groups ADD COLUMN {name} INTEGER NOT NULL DEFAULT {default}')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS engagement_messages(
      origin_group_id INTEGER,message_id INTEGER,group_id INTEGER NOT NULL,user_id INTEGER NOT NULL,
      day TEXT NOT NULL,event_time REAL NOT NULL,received_at REAL NOT NULL,signature TEXT NOT NULL,
      period TEXT NOT NULL,created TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'observed',
      points INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '',
      PRIMARY KEY(origin_group_id,message_id));
    CREATE INDEX IF NOT EXISTS engagement_user_day ON engagement_messages(group_id,user_id,day,signature);
    CREATE INDEX IF NOT EXISTS engagement_traffic ON engagement_messages(group_id,user_id,event_time);
    CREATE INDEX IF NOT EXISTS ledger_chat_day ON ledger(group_id,user_id,kind,substr(period,1,10));
    ''')


def content(text):
    # Letters/numbers count consistently across CJK and English; whitespace, format marks,
    # punctuation and emoji cannot be used to pad the minimum length.
    return ''.join(c for c in normalize(text) if unicodedata.category(c)[0] in ('L','N'))


def useful(text,minimum):
    value=content(text)
    if len(value)<minimum or not any(c.isalpha() for c in value) or len(set(value))<3:return False
    return not any(value==value[:n]*(len(value)//n) for n in range(1,min(5,len(value)//2)+1) if len(value)%n==0)


def _reverse(db,rows,reason):
    for row in rows:
        if row['status']!='credited':continue
        original=db.execute("SELECT points,created FROM ledger WHERE group_id=? AND user_id=? AND kind='chat' AND period=?",(row['group_id'],row['user_id'],row['period'])).fetchone()
        if original:
            changed=db.execute("INSERT OR IGNORE INTO ledger(group_id,user_id,points,kind,period,created) VALUES(?,?,?,'chat_reversal',?,?)",(row['group_id'],row['user_id'],-original['points'],row['period'],original['created'])).rowcount
            if changed:db.execute('INSERT INTO logs(group_id,user_id,kind,detail,created) VALUES(?,?,?,?,?)',(row['group_id'],row['user_id'],'发言积分撤销',f"-{original['points']} 积分 · message_id={row['message_id']} · {reason}",current_time().isoformat()))
        db.execute("UPDATE engagement_messages SET status='revoked',reason=? WHERE origin_group_id=? AND message_id=?",(reason,row['origin_group_id'],row['message_id']))


def reconcile_migration(db,gid):
    """Preserve physical message IDs while merging same-community deduplication and caps."""
    seen=set()
    rows=[dict(r) for r in db.execute("SELECT * FROM engagement_messages WHERE group_id=? AND status='credited' ORDER BY event_time,origin_group_id,message_id",(gid,))]
    for row in rows:
        key=(row['user_id'],row['day'],row['signature'])
        if key in seen:_reverse(db,[row],'社群升级合并后重复内容去重')
        seen.add(key)
    cap=db.execute('SELECT chat_daily_cap FROM groups WHERE id=?',(gid,)).fetchone()[0]
    excess=db.execute("SELECT user_id,substr(period,1,10) day,SUM(points) used FROM ledger WHERE group_id=? AND kind IN ('chat','chat_reversal') GROUP BY user_id,day HAVING used>?",(gid,cap)).fetchall()
    for day in excess:
        used=day['used']
        for row in db.execute("SELECT * FROM engagement_messages WHERE group_id=? AND user_id=? AND day=? AND status='credited' ORDER BY event_time DESC,message_id DESC",(gid,day['user_id'],day['day'])).fetchall():
            if used<=cap:break
            _reverse(db,[dict(row)],'社群升级合并后执行每日发言积分上限');used-=row['points']


class ChatRewards:
    def __init__(self,bot):self.bot,self.store=bot,bot.store

    def observe(self,group,message):
        from group_links import resolve
        if 'message_id' not in message or not message.get('from'):return
        current=current_time()
        timestamp=message.get('date')
        at=datetime.fromtimestamp(timestamp,TZ) if type(timestamp) in (int,float) else current
        if at>current+timedelta(minutes=5) or at<current-timedelta(days=1):return
        value=content(message.get('text',message.get('caption','')))
        signature=hashlib.sha256(value.encode()).hexdigest() if value else ''
        origin=message['chat']['id'];mid=message['message_id']
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            gid=resolve(db,group['id'])
            db.execute('INSERT OR IGNORE INTO engagement_messages(origin_group_id,message_id,group_id,user_id,day,event_time,received_at,signature,period,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (origin,mid,gid,message['from']['id'],at.date().isoformat(),at.timestamp(),time.time(),signature,f'{at.date().isoformat()}:{origin}:{mid}',at.isoformat()))
            recent=[dict(r) for r in db.execute('SELECT * FROM engagement_messages WHERE group_id=? AND user_id=? AND event_time>=? AND event_time<=?',(gid,message['from']['id'],at.timestamp()-10,at.timestamp()))]
            if len(recent)>8:
                _reverse(db,recent,'刷屏消息不计发言积分')
                for row in recent:
                    db.execute("UPDATE engagement_messages SET status='rejected',reason='刷屏消息不计分' WHERE origin_group_id=? AND message_id=? AND status='observed'",(row['origin_group_id'],row['message_id']))

    def exclude(self,gid,uid,mid,reason,origin_gid=None):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM engagement_messages WHERE group_id=? AND user_id=? AND message_id=?'+(' AND origin_group_id=?' if origin_gid is not None else '')+' ORDER BY event_time DESC LIMIT 1',
                               (gid,uid,mid,origin_gid) if origin_gid is not None else (gid,uid,mid)).fetchone()
            if not current:return
            rows=[dict(current)]
            if '刷屏' in reason:
                rows=[dict(r) for r in db.execute('SELECT * FROM engagement_messages WHERE group_id=? AND user_id=? AND event_time>=? AND event_time<=?',(gid,uid,current['event_time']-10,current['event_time']))]
            elif '重复' in reason and current['signature']:
                rows=[dict(r) for r in db.execute('SELECT * FROM engagement_messages WHERE group_id=? AND user_id=? AND signature=? AND event_time>=? AND event_time<=?',(gid,uid,current['signature'],current['event_time']-600,current['event_time']))]
            _reverse(db,rows,reason)
            for row in rows:db.execute("UPDATE engagement_messages SET status='rejected',reason=? WHERE origin_group_id=? AND message_id=? AND status<>'revoked'",(reason,row['origin_group_id'],row['message_id']))

    def award(self,group,message,edited=False):
        from group_links import resolve
        origin=message['chat']['id'];mid=message['message_id'];uid=message['from']['id']
        text=message.get('text',message.get('caption',''))
        reason=moderation_reason({**group,'block_ads':True,'block_links':True,'block_mentions':True,'block_buttons':True,'block_forwards':True},message)
        if reason or message.get('via_bot') or message.get('sender_chat'):
            self.exclude(group['id'],uid,mid,reason or '转发/机器人代发内容不计分',origin_gid=origin);return 0
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            gid=resolve(db,group['id'])
            config=db.execute('SELECT * FROM groups WHERE id=? AND active=1 AND disconnected=0',(gid,)).fetchone()
            row=db.execute('SELECT * FROM engagement_messages WHERE origin_group_id=? AND message_id=?',(origin,mid)).fetchone()
            if not row:return 0
            row=dict(row)
            if edited:
                if config and not useful(text,config['chat_min_chars']):_reverse(db,[row],'编辑后不再满足有效发言条件')
                return 0
            if not config or row['status']!='observed':return 0
            def reject(why):
                db.execute("UPDATE engagement_messages SET status='rejected',reason=? WHERE origin_group_id=? AND message_id=?",(why,origin,mid))
                return 0
            if not config['chat_rewards_enabled'] or not config['chat_reward_points'] or not config['chat_daily_cap']:return reject('发言奖励未启用')
            if text.lstrip().startswith('/') or text.strip().casefold()=='coinw' or not useful(text,config['chat_min_chars']):return reject('指令或未达到有效字符要求')
            arrival=db.execute('SELECT status FROM member_arrivals WHERE group_id=? AND user_id=?',(gid,uid)).fetchone()
            if config['verify'] and arrival and arrival['status'] in ('pending','timed_out','error','disconnected'):return reject('尚未完成入群验证')
            recent=[dict(r) for r in db.execute('SELECT * FROM engagement_messages WHERE group_id=? AND user_id=? AND event_time>=? AND event_time<=?',(gid,uid,row['event_time']-10,row['event_time']))]
            if len(recent)>8:
                _reverse(db,recent,'刷屏消息不计发言积分')
                return reject('刷屏消息不计分')
            duplicate=[dict(r) for r in db.execute("SELECT * FROM engagement_messages WHERE group_id=? AND user_id=? AND day=? AND signature=? AND (origin_group_id<>? OR message_id<>?) AND status IN ('credited','duplicate','revoked')",(gid,uid,row['day'],row['signature'],origin,mid))]
            if duplicate:
                _reverse(db,[r for r in duplicate if abs(row['event_time']-r['event_time'])<=10],'短时间重复刷屏不计分')
                db.execute("UPDATE engagement_messages SET status='duplicate',reason='同日重复内容不计分' WHERE origin_group_id=? AND message_id=?",(origin,mid))
                return 0
            used=db.execute("SELECT COALESCE(SUM(points),0) FROM ledger WHERE group_id=? AND user_id=? AND kind IN ('chat','chat_reversal') AND substr(period,1,10)=?",(gid,uid,row['day'])).fetchone()[0]
            points=config['chat_reward_points']
            if used+points>config['chat_daily_cap']:return reject('今日发言积分已达上限')
            inserted=db.execute("INSERT OR IGNORE INTO ledger(group_id,user_id,points,kind,period,created) VALUES(?,?,?,'chat',?,?)",(gid,uid,points,row['period'],row['created'])).rowcount
            if not inserted:return 0
            db.execute("UPDATE engagement_messages SET group_id=?,status='credited',points=? WHERE origin_group_id=? AND message_id=?",(gid,points,origin,mid))
            db.execute('INSERT INTO logs(group_id,user_id,kind,detail,created) VALUES(?,?,?,?,?)',(gid,uid,'有效发言',f'+{points} 积分 · {row["day"]} 发言积分 {used+points}/{config["chat_daily_cap"]} · message_id={mid}',current_time().isoformat()))
            return points

    def rankings(self,gid,uid):
        day=current_time().date().isoformat()
        with self.store.db() as db:
            db.execute('BEGIN')
            config=db.execute('SELECT title,chat_rewards_enabled,chat_reward_points,chat_daily_cap,chat_min_chars FROM groups WHERE id=?',(gid,)).fetchone()
            rows=[dict(r) for r in db.execute('''WITH scores AS (
              SELECT user_id,SUM(points) total FROM ledger WHERE group_id=? GROUP BY user_id HAVING total>0
            ), ranked AS (
              SELECT s.*,ROW_NUMBER() OVER(ORDER BY total DESC,s.user_id) position,
                COALESCE(m.name,CAST(s.user_id AS TEXT)) name
              FROM scores s LEFT JOIN members m ON m.group_id=? AND m.user_id=s.user_id
            ) SELECT * FROM ranked WHERE position<=10 OR user_id=? ORDER BY position''',(gid,gid,uid))]
            earned=db.execute("SELECT COALESCE(SUM(points),0) FROM ledger WHERE group_id=? AND user_id=? AND kind IN ('chat','chat_reversal') AND substr(period,1,10)=?",(gid,uid,day)).fetchone()[0]
        def label(name):return html.escape(''.join(c for c in str(name) if unicodedata.category(c) not in ('Cc','Cf'))[:40])
        lines=['🏆 <b>Community Points Rankings</b>',label(config['title']),'All-time · This group only','']
        top=[r for r in rows if r['position']<=10]
        for row in top:lines.append(f'{row["position"]}. <a href="tg://user?id={int(row["user_id"])}">{label(row["name"])}</a> — {row["total"]} points')
        if not top:lines.append('No points yet. Join the conversation to get started!')
        own=next((r for r in rows if r['user_id']==uid),None)
        rule=f'Chat rewards: +{config["chat_reward_points"]} per qualifying message ({config["chat_min_chars"]}+ letters/numbers).' if config['chat_rewards_enabled'] and config['chat_reward_points'] and config['chat_daily_cap'] else 'Chat rewards are currently disabled.'
        lines.extend(['',f'Your rank: #{own["position"]} · {own["total"]} points' if own else 'Your rank: Not ranked yet · 0 points',f'Today’s chat points: {earned}/{config["chat_daily_cap"]}',rule,'Daily reset: 00:00 (UTC+8). Includes check-in, invitation and chat points.'])
        return '\n'.join(lines)

    def prune(self):
        with self.store.db() as db:db.execute('DELETE FROM engagement_messages WHERE received_at<?',(time.time()-32*86400,))
