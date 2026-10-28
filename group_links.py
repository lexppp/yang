"""Canonical group IDs, reversible connection removal and safe Telegram group migration."""
import json
import time


def initialize(db):
    for column, declaration in [('disconnected','INTEGER NOT NULL DEFAULT 0'),('chat_type',"TEXT NOT NULL DEFAULT ''"),('migration_probe','REAL NOT NULL DEFAULT 0')]:
        if column not in {r[1] for r in db.execute('PRAGMA table_info(groups)')}:
            db.execute(f'ALTER TABLE groups ADD COLUMN {column} {declaration}')
    if 'join_request' not in {r[1] for r in db.execute('PRAGMA table_info(invites)')}:
        db.execute('ALTER TABLE invites ADD COLUMN join_request INTEGER NOT NULL DEFAULT 0')
    if 'invite_url' not in {r[1] for r in db.execute('PRAGMA table_info(member_arrivals)')}:
        db.execute('ALTER TABLE member_arrivals ADD COLUMN invite_url TEXT')
    db.execute('CREATE TABLE IF NOT EXISTS group_migrations(old_id INTEGER PRIMARY KEY,new_id INTEGER NOT NULL,created REAL NOT NULL)')


def resolve(db, gid):
    seen=set()
    while gid not in seen:
        seen.add(gid)
        row=db.execute('SELECT new_id FROM group_migrations WHERE old_id=?',(gid,)).fetchone()
        if not row:
            return gid
        gid=row[0]
    raise ValueError('社群升级映射出现循环，请检查数据')


def visible_sql(column='group_id'):
    return f'{column} IN (SELECT id FROM groups WHERE disconnected=0 AND id NOT IN (SELECT old_id FROM group_migrations))'


def migrate(store, old, new):
    old,new=int(old),int(new)
    if old==new:
        return
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        target=resolve(db,new)
        if target==old:
            raise ValueError('社群升级映射无效')
        recorded=db.execute('SELECT new_id FROM group_migrations WHERE old_id=?',(old,)).fetchone()
        if recorded:
            if resolve(db,old)!=target:
                raise ValueError('社群已关联至其他升级目标')
            return
        source=db.execute('SELECT * FROM groups WHERE id=?',(old,)).fetchone()
        current=db.execute('SELECT * FROM groups WHERE id=?',(target,)).fetchone()
        if not source:
            db.execute('INSERT INTO group_migrations VALUES(?,?,?)',(old,target,time.time()))
            return
        if not current:
            values=dict(source);values.update(id=target,chat_type='supergroup')
            db.execute('INSERT INTO groups('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',list(values.values()))
        else:
            # Preserve settings already explicitly used on the new group. Combine only counters.
            db.execute('UPDATE groups SET messages=messages+?,last_activity=MAX(COALESCE(last_activity,\'\'),?),disconnected=MAX(disconnected,?),chat_type=\'supergroup\' WHERE id=?',
                       (source['messages'],source['last_activity'] or '',source['disconnected'],target))
        # A person can only be credited once across the old and new ID of the same community.
        for referral in db.execute('SELECT * FROM referrals WHERE group_id=?',(old,)).fetchall():
            existing=db.execute('SELECT * FROM referrals WHERE group_id=? AND user_id=?',(target,referral['user_id'])).fetchone()
            if existing:
                loser=existing if referral['created']<existing['created'] else referral
                db.execute("DELETE FROM ledger WHERE group_id=? AND user_id=? AND kind='invite' AND period=?",
                           (loser['group_id'],loser['inviter_id'],str(loser['user_id'])))
                if loser['group_id']==target:
                    db.execute('DELETE FROM referrals WHERE group_id=? AND user_id=?',(target,referral['user_id']))
        for table, keys in [('referrals',('user_id',)),('members',('user_id',)),('challenges',('user_id',)),('forum_topics',('topic_id',))]:
            db.execute(f'UPDATE OR IGNORE {table} SET group_id=? WHERE group_id=?',(target,old))
            db.execute(f'DELETE FROM {table} WHERE group_id=?',(old,))
        for row in db.execute('SELECT * FROM ledger WHERE group_id=?',(old,)).fetchall():
            existing=db.execute('SELECT * FROM ledger WHERE group_id=? AND user_id=? AND kind=? AND period=?',
                                (target,row['user_id'],row['kind'],row['period'])).fetchone()
            if existing and row['created']<existing['created']:
                db.execute('DELETE FROM ledger WHERE id=?',(existing['id'],))
                existing=None
            if existing:
                db.execute('DELETE FROM ledger WHERE id=?',(row['id'],))
            else:
                db.execute('UPDATE ledger SET group_id=? WHERE id=?',(target,row['id']))
        # bot_message_cleanup retains physical chat IDs: never retarget old message IDs.
        # Old basic-group message IDs must never be used to delete messages in the new group.
        db.execute('UPDATE member_arrivals SET prompt_id=NULL,nonce=NULL WHERE group_id=?',(old,))
        db.execute('UPDATE OR IGNORE member_arrivals SET group_id=? WHERE group_id=?',(target,old))
        db.execute('DELETE FROM member_arrivals WHERE group_id=?',(old,))
        for table in ('invites','logs','announcement_plans','announcement_deliveries'):
            db.execute(f'UPDATE {table} SET group_id=? WHERE group_id=?',(target,old))
        db.execute('UPDATE groups SET active=0 WHERE id=?',(old,))
        # Do not replay moderation actions against a migrated chat with mismatched message IDs.
        db.execute("UPDATE moderation_jobs SET status='cancelled' WHERE group_id=? AND status='pending'",(old,))
        db.execute('INSERT INTO group_migrations VALUES(?,?,?)',(old,target,time.time()))
    store.event(target,None,'社群升级合并',f'{old} → {target}；已迁移邀请链接及历史数据')


def disconnect(store, gid):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        gid=resolve(db,int(gid))
        if not db.execute('SELECT 1 FROM groups WHERE id=?',(gid,)).fetchone():
            raise ValueError('社群不存在')
        db.execute('UPDATE groups SET disconnected=1,active=0 WHERE id=?',(gid,))
        db.execute("UPDATE announcement_plans SET status='paused',next_run=NULL WHERE group_id=? AND status='scheduled'",(gid,))
        db.execute("UPDATE member_arrivals SET status='disconnected' WHERE group_id=? AND status='pending'",(gid,))
        db.execute("UPDATE bot_message_cleanup SET status='cancelled' WHERE group_id=? AND status='pending'",(gid,))
        db.execute("UPDATE moderation_jobs SET status='cancelled' WHERE group_id=? AND status='pending'",(gid,))
    return gid
