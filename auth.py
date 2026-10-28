"""Persistent administrators, server-side RBAC, versioned sessions and security audit."""
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timezone
from moderation import SETTINGS as MODERATION_SETTINGS

PERMISSIONS = {
    'dashboard.view': '数据总览',
    'groups.view': '查看社群', 'groups.manage': '修改社群设置', 'groups.disconnect':'解除与恢复社群连接',
    'points.view': '查看积分与历史', 'points.adjust': '手动增减积分',
    'points.configure': '修改奖励规则', 'points.export': '导出积分',
    'invites.view': '查看邀请', 'invites.manage': '撤销邀请链接', 'invites.export': '导出邀请 Excel', 'invites.attribute':'补记邀请（含积分奖励）',
    'draws.view': '查看抽奖', 'draws.manage': '管理与发布抽奖',
    'announcements.view': '查看公告', 'announcements.manage': '编辑与发布公告',
    'logs.view': '查看活动日志',
}
DEPENDENCIES = {p: p.split('.')[0]+'.view' for p in PERMISSIONS if not p.endswith('.view')}


class AccessDenied(Exception):
    pass


def normalized_permissions(values):
    if not isinstance(values, list) or any(not isinstance(p, str) or p not in PERMISSIONS for p in values):
        raise ValueError('权限列表无效；超级管理员权限不可授予普通管理员')
    result = set(values)
    result.update(DEPENDENCIES[p] for p in values if p in DEPENDENCIES)
    return sorted(result)


def password_digest(password, salt):
    if not isinstance(password, str) or len(password) > 256:
        raise ValueError('密码格式无效')
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def new_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('密码长度须为 12–256 位')
    salt = secrets.token_hex(16)
    return salt, password_digest(password, salt)


def public_user(row):
    if row is None:
        return None
    return {k: row[k] for k in ('id', 'username', 'name', 'role', 'active', 'version', 'created')} | {
        'permissions': sorted(PERMISSIONS) if row['role'] == 'superadmin' else json.loads(row['permissions'])}


def allowed(user, permission):
    return bool(user and (user['role'] == 'superadmin' or permission in user['permissions']))


def required_permissions(path, body=None):
    if path == '/api/moderation/status':
        return ['groups.manage']
    if path in ('/api/groups/disconnect','/api/groups/restore','/api/groups/disconnected'):
        return ['groups.disconnect']
    if path == '/api/invites/attribute':
        return ['invites.attribute']
    if path == '/api/settings':
        body = body or {}
        requirements = []
        if any(k in body for k in ('welcome', 'welcome_photo', 'block_links', 'block_photos', 'block_ads', 'keywords', 'verify', 'verify_minutes', 'welcome_delete_minutes', 'link_delete_minutes', 'language',*MODERATION_SETTINGS)):
            requirements.append('groups.manage')
        if any(k in body for k in ('checkin_points', 'invite_points')):
            requirements.append('points.configure')
        return requirements
    if path == '/api/revoke':
        return ['invites.manage']
    if path == '/api/export':
        return ['points.export']
    if path.startswith('/api/points/'):
        return ['points.adjust']
    if path.startswith('/api/draws/'):
        return ['draws.manage']
    if path.startswith('/api/announcements/'):
        return ['announcements.manage']
    return []


def redact_snapshot(data, user):
    """Keep shared group summary metadata but remove ungranted module records/config."""
    data = dict(data)
    for key, permission in {'ranking':'points.view', 'ledger':'points.view', 'invites':'invites.view',
                            'joins':'invites.view', 'logs':'logs.view', 'draws':'draws.view',
                            'announcements':'announcements.view', 'announcement_deliveries':'announcements.view',
                            'forum_topics':'announcements.view'}.items():
        if not allowed(user, permission):
            data[key] = []
    if not any(allowed(user, p) for p in ('groups.view', 'points.view', 'invites.view', 'draws.view')):
        data['members'] = []
    groups = []
    for group in data.get('groups', []):
        item = dict(group)
        if not allowed(user, 'groups.view'):
            for key in ('welcome', 'welcome_photo', 'block_links', 'block_photos', 'block_ads', 'keywords', 'verify', 'verify_minutes', 'welcome_delete_minutes', 'link_delete_minutes', 'language',*MODERATION_SETTINGS):
                item.pop(key, None)
        if not allowed(user, 'points.view'):
            item.pop('checkin_points', None)
            item.pop('invite_points', None)
        groups.append(item)
    data['groups'] = groups
    data['current_user'] = user
    return data


class Accounts:
    def __init__(self, store, bootstrap_password=None, bootstrap_username='admin'):
        self.store = store
        self.dummy_salt = secrets.token_hex(16)
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS administrators(
              id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('superadmin','admin')),
              salt TEXT NOT NULL, password_hash TEXT NOT NULL,
              permissions TEXT NOT NULL DEFAULT '[]', active INTEGER NOT NULL DEFAULT 1,
              version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS single_super_administrator
              ON administrators(role) WHERE role='superadmin';
            CREATE TABLE IF NOT EXISTS administrator_audit(
              id INTEGER PRIMARY KEY, actor_id INTEGER, actor TEXT NOT NULL, action TEXT NOT NULL,
              target_id INTEGER, target TEXT NOT NULL, detail TEXT NOT NULL, created TEXT NOT NULL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT id FROM administrators WHERE role='superadmin'").fetchone():
                username = self.username(bootstrap_username)
                salt, digest = new_password(bootstrap_password)
                db.execute('''INSERT INTO administrators(username,name,role,salt,password_hash,created)
                  VALUES(?,?,'superadmin',?,?,?)''', (username, '超级管理员', salt, digest, self.timestamp()))

    @staticmethod
    def timestamp():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def username(value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.-]{2,31}', value):
            raise ValueError('用户名须为 3–32 位英文字母、数字、点、下划线或短横线，以字母开头')
        return value.lower()

    @staticmethod
    def audit(db, actor, action, target, detail):
        db.execute('INSERT INTO administrator_audit(actor_id,actor,action,target_id,target,detail,created) VALUES(?,?,?,?,?,?,?)',
                   (actor['id'], actor['username'], action, target['id'], target['username'], detail, Accounts.timestamp()))

    def authenticate(self, username, password):
        username = str(username).lower()
        with self.store.db() as db:
            row = db.execute('SELECT * FROM administrators WHERE username=?', (username,)).fetchone()
        salt = row['salt'] if row else self.dummy_salt
        try:
            digest = password_digest(password, salt)
        except ValueError:
            return None
        if not row or not hmac.compare_digest(digest, row['password_hash']) or not row['active']:
            return None
        return public_user(row)

    def user(self, uid):
        with self.store.db() as db:
            return public_user(db.execute('SELECT * FROM administrators WHERE id=?', (uid,)).fetchone())

    def list(self, actor):
        self.super_only(actor)
        with self.store.db() as db:
            return [public_user(row) for row in db.execute('SELECT * FROM administrators ORDER BY id')]

    @staticmethod
    def super_only(actor):
        if not actor or actor['role'] != 'superadmin':
            raise AccessDenied('此操作仅限超级管理员')

    def create(self, actor, body):
        self.super_only(actor)
        if body.get('role', 'admin') != 'admin':
            raise AccessDenied('只能创建普通管理员，不能新增超级管理员')
        username = self.username(body.get('username'))
        name = body.get('name', username)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise ValueError('显示名称须为 1–60 字')
        permissions = normalized_permissions(body.get('permissions', []))
        salt, digest = new_password(body.get('password'))
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT id FROM administrators WHERE username=?', (username,)).fetchone():
                raise ValueError('该用户名已存在')
            result = db.execute('''INSERT INTO administrators(username,name,role,salt,password_hash,permissions,created)
              VALUES(?,?,'admin',?,?,?,?)''', (username,name.strip(),salt,digest,json.dumps(permissions),self.timestamp()))
            target = {'id':result.lastrowid,'username':username}
            self.audit(db, actor, '创建管理员', target, '权限：'+', '.join(permissions))
        return self.user(target['id'])

    def update(self, actor, body):
        self.super_only(actor)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            target = db.execute('SELECT * FROM administrators WHERE id=?', (int(body['id']),)).fetchone()
            if not target:
                raise ValueError('账号不存在')
            if target['role'] == 'superadmin':
                raise AccessDenied('超级管理员不能被停用、降权或由此入口修改')
            if 'role' in body and body['role'] != 'admin':
                raise AccessDenied('不能提升普通管理员为超级管理员')
            permissions = normalized_permissions(body['permissions']) if 'permissions' in body else json.loads(target['permissions'])
            active = body.get('active', bool(target['active']))
            if type(active) is not bool:
                raise ValueError('账号状态无效')
            name = body.get('name',target['name'])
            if not isinstance(name,str) or not 1 <= len(name.strip()) <= 60:
                raise ValueError('显示名称须为 1–60 字')
            db.execute('UPDATE administrators SET name=?,permissions=?,active=?,version=version+1 WHERE id=?',
                       (name.strip(),json.dumps(permissions),int(active),target['id']))
            self.audit(db,actor,'更新管理员',target,('启用' if active else '停用')+'；权限：'+', '.join(permissions))
        return self.user(target['id'])

    def reset_password(self, actor, body):
        self.super_only(actor)
        salt,digest = new_password(body.get('password'))
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            target = db.execute('SELECT * FROM administrators WHERE id=?',(int(body['id']),)).fetchone()
            if not target:
                raise ValueError('账号不存在')
            if target['role'] == 'superadmin':
                raise AccessDenied('超级管理员须通过个人修改密码入口操作')
            db.execute('UPDATE administrators SET salt=?,password_hash=?,version=version+1 WHERE id=?',(salt,digest,target['id']))
            self.audit(db,actor,'重置管理员密码',target,'旧会话已失效；未记录密码')

    def change_password(self, actor, body):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM administrators WHERE id=?',(actor['id'],)).fetchone()
            if not row or not hmac.compare_digest(password_digest(body.get('current_password'),row['salt']),row['password_hash']):
                raise AccessDenied('当前密码不正确')
            salt,digest = new_password(body.get('password'))
            db.execute('UPDATE administrators SET salt=?,password_hash=?,version=version+1 WHERE id=?',(salt,digest,actor['id']))
            self.audit(db,actor,'修改本人密码',row,'所有旧会话已失效；未记录密码')

    def audits(self, actor):
        self.super_only(actor)
        with self.store.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM administrator_audit ORDER BY id DESC LIMIT 100')]
