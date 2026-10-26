"""Password accounts and revocable, hashed opaque sessions."""
import asyncio
import hashlib
import secrets
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix='/api')
SESSION_SECONDS = 7 * 24 * 3600


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f'scrypt${salt}${digest}'


def password_matches(password: str, stored: str) -> bool:
    try:
        algorithm, salt, expected = stored.split('$')
        if algorithm != 'scrypt':
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return secrets.compare_digest(actual, expected)
    except ValueError:
        return False


async def initialize(db):
    async with db.connect() as conn:
        await conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
          role TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at REAL NOT NULL);
        ''')
        await conn.execute("INSERT OR IGNORE INTO users VALUES('admin','admin','','admin',0,0)")
        columns = await (await conn.execute('PRAGMA table_info(tasks)')).fetchall()
        if 'owner_id' not in [row['name'] for row in columns]:
            await conn.execute("ALTER TABLE tasks ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'admin'")


async def resolve(db, token):
    if not token:
        return None
    rows = await db.rows('''SELECT u.id,u.username,u.role FROM sessions s JOIN users u ON s.user_id=u.id
        WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0 AND u.deleted=0''',
        (hashlib.sha256(token.encode()).hexdigest(), time.time()))
    return rows[0] if rows else None


def admin(request):
    if request.state.user['role'] != 'admin':
        raise HTTPException(403, '仅管理员可管理账户。')


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class NewUser(Login):
    password: str = Field(min_length=10, max_length=256)


class EditUser(BaseModel):
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=256)


@router.post('/auth/login')
async def login(body: Login, request: Request):
    db = request.app.state.db
    username = body.username.strip().lower()
    failures = request.app.state.login_failures
    stamp = time.monotonic()
    for key in list(failures):
        if stamp - failures[key][1] > 300:
            del failures[key]
    count, _ = failures.get(username, (0, stamp))
    if count >= 10 or sum(value[0] for value in failures.values()) >= 100:
        raise HTTPException(429, '登录尝试过多，请五分钟后重试。')
    failures[username] = (count + 1, stamp)
    rows = await db.rows('SELECT * FROM users WHERE username=?', (username,))
    user = rows[0] if rows else None
    valid = await asyncio.to_thread(password_matches, body.password,
        user['password_hash'] if user and user['password_hash'] else request.app.state.dummy_password)
    if not user or not valid or user['disabled'] or user['deleted']:
        raise HTTPException(401, '用户名或密码错误，或账户已停用。')
    failures.pop(username, None)
    token = secrets.token_urlsafe(32)
    await db.execute('DELETE FROM sessions WHERE expires_at<?', (time.time(),))
    await db.execute('INSERT INTO sessions VALUES(?,?,?)',
        (hashlib.sha256(token.encode()).hexdigest(), user['id'], time.time() + SESSION_SECONDS))
    return {'token': token, 'user': {key: user[key] for key in ('id', 'username', 'role')}, 'expires_in': SESSION_SECONDS}


@router.get('/auth/me')
async def me(request: Request):
    return request.state.user


@router.post('/auth/logout')
async def logout(request: Request):
    token = request.headers.get('authorization', '').partition(' ')[2]
    await request.app.state.db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
    await request.app.state.agents.revoke(request.state.user['id'])
    return {'message': '已退出登录。'}


@router.get('/admin/users')
async def users(request: Request):
    admin(request)
    return await request.app.state.db.rows('SELECT id,username,role,disabled FROM users WHERE deleted=0 ORDER BY role,username')


@router.post('/admin/users', status_code=201)
async def create_user(body: NewUser, request: Request):
    admin(request)
    username = body.username.strip().lower()
    if not username or not all(c.isalnum() or c in '_-.' for c in username):
        raise HTTPException(400, '用户名只能包含字母、数字、下划线、短横线或点。')
    hashed = await asyncio.to_thread(password_hash, body.password)
    import sqlite3
    try:
        await request.app.state.db.execute('INSERT INTO users VALUES(?,?,?,?,0,0)', (str(uuid4()), username, hashed, 'user'))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, '该用户名已存在（含已删除账户），请使用其他用户名。') from exc
    return {'message': '账户已创建。'}


@router.patch('/admin/users/{user_id}')
async def edit_user(user_id: str, body: EditUser, request: Request):
    admin(request)
    db = request.app.state.db
    rows = await db.rows('SELECT * FROM users WHERE id=? AND deleted=0', (user_id,))
    if not rows:
        raise HTTPException(404, '账户不存在。')
    if rows[0]['role'] == 'admin':
        raise HTTPException(409, '管理员账户请使用本机密码设置命令管理。')
    async with db.connect() as conn:
        if body.disabled is not None:
            await conn.execute('UPDATE users SET disabled=? WHERE id=?', (int(body.disabled), user_id))
        if body.password is not None:
            hashed = await asyncio.to_thread(password_hash, body.password)
            await conn.execute('UPDATE users SET password_hash=? WHERE id=?', (hashed, user_id))
        await conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
    await request.app.state.agents.revoke(user_id)
    async with request.app.state.tasks.lock:
        await request.app.state.tasks.supersede_locked(user_id)
    return {'message': '账户已更新，原登录已失效。'}


@router.delete('/admin/users/{user_id}')
async def delete_user(user_id: str, request: Request):
    await edit_user(user_id, EditUser(disabled=True), request)
    await request.app.state.db.execute('UPDATE users SET deleted=1,password_hash=? WHERE id=?', ('', user_id))
    return {'message': '账户已删除，历史归属记录保留。'}
