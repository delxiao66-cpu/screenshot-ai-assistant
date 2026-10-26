import asyncio
import json
import logging
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile

from server.database import public_task
from server.services.image_processor import preprocess, read_images

log = logging.getLogger('assistant')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskManager:
    def __init__(self, settings, database, agents, ai):
        self.settings, self.db, self.agents, self.ai = settings, database, agents, ai
        self.lock = asyncio.Lock()
        self.jobs: dict[str, asyncio.Task] = {}
        self.latest_id: str | None = None
        self.pending: dict[str, tuple[str, object, float]] = {}
        self.background: list[asyncio.Task] = []

    def directory(self, task_id: str) -> Path:
        try:
            if str(UUID(task_id)) != task_id:
                raise ValueError('noncanonical')
        except ValueError as exc:
            raise HTTPException(404, '任务不存在。') from exc
        path = self.settings.tmp_dir / task_id
        if path.resolve().parent != self.settings.tmp_dir.resolve() or path.is_symlink():
            raise HTTPException(400, '临时图片路径不安全。')
        return path

    async def get(self, task_id: str) -> dict:
        self.directory(task_id)
        rows = await self.db.rows('SELECT * FROM tasks WHERE id=?', (task_id,))
        if not rows:
            raise HTTPException(404, '任务不存在或已清理。')
        return public_task(rows[0])

    async def images(self, task_id: str) -> list[dict]:
        await self.get(task_id)
        return await self.db.rows('SELECT * FROM images WHERE task_id=? ORDER BY sequence', (task_id,))

    async def notify(self, task_id: str, event='task.updated'):
        task = await self.get(task_id)
        await self.agents.broadcast(event, task_id=task_id, task=task)

    async def cleanup(self, task_id: str) -> bool:
        try:
            path = self.directory(task_id)
            if await asyncio.to_thread(path.exists):
                await asyncio.to_thread(shutil.rmtree, path)
            return True
        except Exception as exc:
            log.error('cleanup.failed task_id=%s type=%s', task_id, type(exc).__name__)
            return False

    async def prune(self):
        rows = await self.db.rows("SELECT id FROM tasks WHERE status IN ('completed','failed','superseded') ORDER BY created_at DESC LIMIT -1 OFFSET 200")
        for row in rows:
            if await self.cleanup(row['id']):
                await self.db.execute('DELETE FROM tasks WHERE id=?', (row['id'],))

    async def create(self, mode: str, source: str, reuse: bool = False) -> dict:
        async with self.lock:
            if mode == 'batch' and reuse:
                existing = await self.db.rows("SELECT * FROM tasks WHERE mode='batch' AND status IN ('collecting','capturing') ORDER BY created_at DESC LIMIT 1")
                if existing:
                    return public_task(existing[0])
            await self.supersede_locked()
            task_id = str(uuid4())
            self.latest_id = task_id
            stamp = now()
            await self.db.execute('INSERT INTO tasks(id,mode,source,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                                  (task_id, mode, source, 'collecting', stamp, stamp))
            await self.prune()
            await self.notify(task_id)
            log.info('task.created task_id=%s mode=%s', task_id, mode)
            return await self.get(task_id)

    async def supersede_locked(self):
        rows = await self.db.rows("SELECT id FROM tasks WHERE status IN ('collecting','capturing','processing')")
        for row in rows:
            task_id = row['id']
            pending = self.pending.pop(task_id, None)
            job = self.jobs.get(task_id)
            if job:
                job.cancel()
            await self.db.execute("UPDATE tasks SET status='superseded',error=?,completed_at=?,updated_at=? WHERE id=?",
                                  ('已被新任务替代。', now(), now(), task_id))
            if pending:
                await self.agents.send(pending[1], {'type': 'capture.cancel', 'task_id': task_id,
                                                  'capture_id': pending[0]})
            await self.cleanup(task_id)
            await self.notify(task_id, 'task.superseded')

    async def request_capture(self, task_id: str, device_id: str | None = None) -> dict:
        async with self.lock:
            task = await self.get(task_id)
            self.require_collecting(task)
            if task['image_count'] >= self.settings.max_images:
                raise HTTPException(413, '图片数量已达到上限。')
            if self.pending:
                raise HTTPException(409, 'Agent 正在截图，请等待当前截图完成。')
            ws = self.agents.select(device_id)
            capture_id = str(uuid4())
            self.pending[task_id] = (capture_id, ws, time.monotonic())
            await self.db.execute("UPDATE tasks SET status='capturing',error=NULL,updated_at=? WHERE id=?", (now(), task_id))
            if not await self.agents.send(ws, {'type': 'capture.request', 'task_id': task_id, 'capture_id': capture_id}):
                await self.capture_failed_locked(task_id, 'Agent 连接已断开，请重连后重试。')
                raise HTTPException(409, 'Agent 连接已断开，请重连后重试。')
            await self.notify(task_id)
            return await self.get(task_id)

    @staticmethod
    def require_collecting(task: dict):
        if task['status'] != 'collecting':
            raise HTTPException(409, '仅收集中的任务可以修改图片或开始分析。')

    async def capture_failed_locked(self, task_id: str, message: str):
        self.pending.pop(task_id, None)
        await self.db.execute("UPDATE tasks SET status='collecting',error=?,updated_at=? WHERE id=? AND status='capturing'", (message, now(), task_id))
        await self.notify(task_id)

    async def capture_error(self, task_id: str, capture_id: str, ws, message: str):
        async with self.lock:
            pending = self.pending.get(task_id)
            if pending and pending[0] == capture_id and pending[1] is ws:
                # Do not persist arbitrary client payloads, which may contain secrets.
                await self.capture_failed_locked(task_id, 'Agent 截图或上传失败，请检查客户端截图权限与连接后重试。')

    async def disconnected(self, ws):
        async with self.lock:
            for task_id, pending in list(self.pending.items()):
                if pending[1] is ws:
                    await self.capture_failed_locked(task_id, 'Agent 连接中断，请重新添加截图。')

    async def upload(self, task_id: str, uploads: list[UploadFile], capture_id: str | None) -> dict:
        async with self.lock:
            task = await self.get(task_id)
            capture = self.pending.get(task_id)
            if capture_id:
                if task['status'] != 'capturing' or not capture or capture[0] != capture_id:
                    raise HTTPException(409, '截图请求已过期，请重新截图。')
                if len(uploads) != 1:
                    raise HTTPException(400, '一次截图请求只能上传一张图片。')
            else:
                self.require_collecting(task)
            if not uploads:
                raise HTTPException(400, '请选择至少一张图片。')
            if task['image_count'] + len(uploads) > self.settings.max_images:
                raise HTTPException(413, f'每个任务最多 {self.settings.max_images} 张图片。')
            existing = await self.images(task_id)
            total = sum(item['size'] for item in existing)
            converted = []
            raw_total = 0
            for upload in uploads:
                raw = await upload.read(self.settings.max_bytes + 1)
                raw_total += len(raw)
                if raw_total > self.settings.max_bytes:
                    raise HTTPException(413, '本次上传原始图片总大小超过限制，请分次上传或缩小图片。')
                try:
                    data, width, height = await asyncio.to_thread(preprocess, raw)
                except ValueError as exc:
                    raise HTTPException(415, str(exc)) from exc
                total += len(data)
                # Base64 expands by 4/3; retain space for JSON and the system prompt.
                if total > self.settings.max_bytes or total > 35 * 1024 * 1024:
                    raise HTTPException(413, '处理后的图片总大小超过配置或视觉接口限制，请减少图片。')
                converted.append((str(uuid4()), data, width, height))
            directory = self.directory(task_id)
            await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
            written = []
            try:
                async with self.db.connect() as db:
                    for offset, (image_id, data, width, height) in enumerate(converted, 1):
                        path = directory / f'{image_id}.jpg'
                        written.append(path)
                        await asyncio.to_thread(path.write_bytes, data)
                        await db.execute('INSERT INTO images VALUES(?,?,?,?,?,?,?)',
                                         (image_id, task_id, task['image_count'] + offset, len(data), width, height, 'image/jpeg'))
                    await db.execute("UPDATE tasks SET status='collecting',image_count=?,updated_at=?,error=NULL WHERE id=?",
                                     (task['image_count'] + len(converted), now(), task_id))
            except BaseException:
                for path in written:
                    try:
                        await asyncio.to_thread(path.unlink, missing_ok=True)
                    except OSError as exc:
                        log.error('upload.rollback_cleanup task_id=%s type=%s', task_id, type(exc).__name__)
                raise
            self.pending.pop(task_id, None)
            log.info('images.added task_id=%s image_count=%s', task_id, task['image_count'] + len(converted))
            await self.notify(task_id, 'capture.completed' if capture_id else 'task.updated')
            if task['mode'] == 'quick':
                await self.analyze_locked(task_id)
            return await self.get(task_id)

    async def delete_images(self, task_id: str, image_id: str | None = None):
        async with self.lock:
            self.require_collecting(await self.get(task_id))
            items = await self.images(task_id)
            selected = [item for item in items if image_id is None or item['id'] == image_id]
            if image_id and not selected:
                raise HTTPException(404, '图片不存在。')
            for item in selected:
                await asyncio.to_thread((self.directory(task_id) / (item['id'] + '.jpg')).unlink, missing_ok=True)
            async with self.db.connect() as db:
                for item in selected:
                    await db.execute('DELETE FROM images WHERE id=?', (item['id'],))
                remaining = [item for item in items if item not in selected]
                for sequence, item in enumerate(remaining, 1):
                    await db.execute('UPDATE images SET sequence=? WHERE id=?', (sequence, item['id']))
                await db.execute('UPDATE tasks SET image_count=?,updated_at=? WHERE id=?', (len(remaining), now(), task_id))
            await self.notify(task_id)
            return await self.get(task_id)

    async def preview(self, task_id: str, image_id: str) -> bytes:
        async with self.lock:
            task = await self.get(task_id)
            if task['status'] not in ('collecting', 'capturing', 'processing'):
                raise HTTPException(409, '分析开始后不再提供截图预览。')
            items = await self.images(task_id)
            if not any(item['id'] == image_id for item in items):
                raise HTTPException(404, '图片不存在。')
            try:
                return await asyncio.to_thread((self.directory(task_id) / (image_id + '.jpg')).read_bytes)
            except FileNotFoundError as exc:
                raise HTTPException(404, '临时图片已清理。') from exc

    async def analyze_locked(self, task_id: str):
        task = await self.get(task_id)
        if task['status'] == 'processing':
            return
        self.require_collecting(task)
        if not task['image_count']:
            raise HTTPException(400, '任务没有图片，请先添加截图或上传图片。')
        if not self.settings.api_key or not self.settings.model:
            raise HTTPException(503, 'DeepSeek API Key 或模型未配置，请编辑 .env 后重启。')
        await self.db.execute("UPDATE tasks SET status='processing',updated_at=?,error=NULL WHERE id=?", (now(), task_id))
        self.latest_id = task_id
        job = asyncio.create_task(self.run_analysis(task_id))
        self.jobs[task_id] = job
        job.add_done_callback(lambda completed: self.jobs.pop(task_id, None))
        await self.notify(task_id, 'task.processing')

    async def analyze(self, task_id: str):
        async with self.lock:
            await self.analyze_locked(task_id)
            return await self.get(task_id)

    async def finish(self, task_id: str, result=None, raw=None, error=None, elapsed=None):
        cleaned = await self.cleanup(task_id)
        if not cleaned:
            error = (error + ' ' if error else '') + '临时图片清理失败，将定期重试，请检查文件占用和目录权限。'
        status = 'failed' if error else 'completed'
        await self.db.execute('UPDATE tasks SET status=?,result_json=?,raw_result=?,error=?,elapsed_ms=?,completed_at=?,updated_at=? WHERE id=?',
                              (status, json.dumps(result, ensure_ascii=False) if result is not None else None, raw, error, elapsed, now(), now(), task_id))
        await self.notify(task_id, 'task.' + status)
        await self.prune()

    async def cancel(self, task_id: str):
        async with self.lock:
            task = await self.get(task_id)
            if task['status'] not in ('collecting', 'capturing', 'processing'):
                raise HTTPException(409, '任务已经结束。')
            self.pending.pop(task_id, None)
            if task_id in self.jobs:
                self.jobs[task_id].cancel()
            await self.finish(task_id, error='任务已取消。')
            return await self.get(task_id)

    async def history(self, limit: int):
        rows = await self.db.rows("SELECT * FROM tasks WHERE status IN ('completed','failed','superseded') ORDER BY created_at DESC LIMIT ?", (limit,))
        return [public_task(row) for row in rows]

    async def clear_history(self):
        async with self.lock:
            rows = await self.db.rows("SELECT id FROM tasks WHERE status IN ('completed','failed','superseded')")
            for row in rows:
                if not await self.cleanup(row['id']):
                    raise HTTPException(500, '部分临时文件清理失败，请检查目录权限后重试。')
                await self.db.execute('DELETE FROM tasks WHERE id=?', (row['id'],))
            await self.agents.broadcast('history.cleared')

    async def run_analysis(self, task_id: str):
        start = time.monotonic()
        try:
            # 分析阶段不设本地时限，任务只在完成、失败或用户取消时结束。
            await self.db.execute('UPDATE tasks SET started_at=? WHERE id=?', (now(), task_id))
            items = await self.images(task_id)
            log.info('ai.started task_id=%s image_count=%s', task_id, len(items))
            images = await asyncio.to_thread(read_images, [self.directory(task_id) / (item['id'] + '.jpg') for item in items])
            result, raw = await self.ai.analyze(images)
            async with self.lock:
                task = await self.get(task_id)
                if self.latest_id == task_id and task['status'] == 'processing':
                    await self.finish(task_id, result=result, raw=raw, elapsed=int((time.monotonic() - start) * 1000))
                    log.info('ai.completed task_id=%s elapsed_ms=%s', task_id, int((time.monotonic() - start) * 1000))
        except asyncio.CancelledError:
            log.info('ai.cancelled task_id=%s', task_id)
            raise
        except Exception as exc:
            log.error('ai.failed task_id=%s type=%s', task_id, type(exc).__name__)
            message = str(exc) if isinstance(exc, ValueError) else '分析失败，请检查网络和服务日志。'
            try:
                async with self.lock:
                    task = await self.get(task_id)
                    if self.latest_id == task_id and task['status'] == 'processing':
                        await self.finish(task_id, error=message, elapsed=int((time.monotonic() - start) * 1000))
            except Exception as storage_exc:
                await self.cleanup(task_id)
                log.error('task.persist_failed task_id=%s type=%s', task_id, type(storage_exc).__name__)

    async def recover(self):
        rows = await self.db.rows("SELECT id FROM tasks WHERE status IN ('capturing','processing')")
        for row in rows:
            await self.finish(row['id'], error='服务曾中断，请重新创建任务。')
        await self.sweep()

    async def sweep(self):
        async with self.lock:
            for task_id, pending in list(self.pending.items()):
                if time.monotonic() - pending[2] > 45:
                    await self.capture_failed_locked(task_id, '截图请求超时，请检查截图客户端后重试。')
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            stale = await self.db.rows("SELECT id FROM tasks WHERE status='collecting' AND updated_at<?", (cutoff,))
            for row in stale:
                await self.finish(row['id'], error='超过 24 小时未操作，临时图片已清理。')
            live = {row['id'] for row in await self.db.rows("SELECT id FROM tasks WHERE status IN ('collecting','capturing','processing')")}
            for path in await asyncio.to_thread(lambda: list(self.settings.tmp_dir.iterdir())):
                if path.name not in live:
                    await self.cleanup(path.name)
            await self.prune()

    async def maintenance(self):
        while True:
            await asyncio.sleep(10)
            try:
                await self.sweep()
            except Exception as exc:
                log.error('maintenance.failed type=%s', type(exc).__name__)

    async def start(self):
        await self.recover()
        self.background = [asyncio.create_task(self.maintenance())]

    async def close(self):
        jobs = list(self.jobs.values()) + self.background
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        rows = await self.db.rows("SELECT id FROM tasks WHERE status IN ('processing','capturing')")
        for row in rows:
            await self.finish(row['id'], error='服务已停止，请重新创建任务。')
