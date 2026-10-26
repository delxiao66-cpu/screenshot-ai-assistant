import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.api import tasks, websocket
from server.accounts import initialize as initialize_accounts, resolve as resolve_account, password_hash
from server.auth import valid_token
from server.config import ROOT, Settings
from server.database import Database
from server.services.agent_manager import AgentManager
from server.services.deepseek_client import DeepSeekClient
from server.services.task_manager import TaskManager


class RequestGuard:
    """Authenticate before multipart parsing; bound even chunked request bodies."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        limit = self.settings.max_bytes + 1024 * 1024
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > limit:
                return await JSONResponse({'error': '上传请求过大，请减少图片或分次上传。'}, 413)(scope, receive, send)
            chunks.append(chunk)
            if not message.get('more_body'):
                break
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}

        async def secure_send(message):
            if message['type'] == 'http.response.start':
                message['headers'].extend([
                    (b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'no-referrer'),
                    (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")])
            await send(message)
        await self.app(scope, replay, secure_send)


def create_app(settings: Settings | None = None, ai=None) -> FastAPI:
    config = settings or Settings()
    config.validate()

    @asynccontextmanager
    async def lifespan(application):
        for directory in (config.data_dir, config.tmp_dir, config.data_dir / 'logs'):
            await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        logger = logging.getLogger('assistant')
        logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(config.data_dir / 'logs' / 'app.log', maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
        # SDK wire logging must never expose request bodies or authentication.
        for name in ('openai', 'httpx', 'httpcore'):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        db = Database(config.data_dir / 'app.db')
        agents = AgentManager()
        manager = TaskManager(config, db, agents, ai or DeepSeekClient(config))
        await initialize_accounts(db)
        application.state.db, application.state.agents, application.state.tasks = db, agents, manager
        application.state.login_failures = {}
        application.state.dummy_password = password_hash('dummy-password-for-timing-only')
        try:
            await db.initialize()
            await manager.start()
            logger.info('server.started host=127.0.0.1 port=8765')
            yield
        finally:
            await agents.close()
            await manager.close()
            logger.info('server.stopped')
            logger.removeHandler(handler)
            handler.close()

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.state.settings = config
    application.add_middleware(RequestGuard, settings=config)

    @application.middleware('http')
    async def account_session(request, call_next):
        header = request.headers.get('Authorization', '')
        request.state.user = None
        if header.lower().startswith('bearer '):
            request.state.user = await resolve_account(application.state.db, header[7:].strip())
        return await call_next(request)

    @application.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({'error': exc.detail}, exc.status_code, headers=exc.headers)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        
        errors = exc.errors()
        if any(error.get('loc', [None])[-1] == 'password' and error.get('type') == 'string_too_short' for error in errors):
            message = '密码至少需要 10 个字符。'
        else:
            message = '请求参数无效，请检查输入内容。'
        return JSONResponse({'error': message}, 422)

    @application.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logging.getLogger('assistant').error('request.failed type=%s', type(exc).__name__)
        message = '数据库暂时不可用，请检查磁盘空间和目录权限。' if isinstance(exc, sqlite3.Error) else '操作失败，请检查服务日志与目录权限后重试。'
        return JSONResponse({'error': message}, 500)

    application.include_router(tasks.router)
    from server.accounts import router as accounts_router
    application.include_router(accounts_router)
    application.include_router(websocket.router)
    application.mount('/static', StaticFiles(directory=ROOT / 'server' / 'static'), name='static')

    @application.get('/')
    async def index():
        return FileResponse(ROOT / 'server' / 'static' / 'index.html')

    return application


app = create_app()
