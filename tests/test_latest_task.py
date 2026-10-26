import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from PIL import Image

from server.config import Settings
from server.database import Database
from server.services.agent_manager import AgentManager
from server.services.deepseek_client import parse_result
from server.services.task_manager import TaskManager


class ControlledAI:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.return_late = False

    async def analyze(self, images):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.return_late:
                    raise
                await self.release.wait()
        return {'questions': [{'index': 1, 'answer': ['B']}]}, 'answer'


class LatestTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(token='test', api_key='test', model='test', data_dir=Path(self.temp.name))
        self.settings.tmp_dir.mkdir()
        self.db = Database(self.settings.data_dir / 'app.db')
        await self.db.initialize()
        self.ai = ControlledAI()
        self.tasks = TaskManager(self.settings, self.db, AgentManager(), self.ai)

    async def asyncTearDown(self):
        self.ai.release.set()
        await self.tasks.close()
        self.temp.cleanup()

    async def upload(self, task_id):
        image = io.BytesIO()
        Image.new('RGB', (40, 40), 'white').save(image, 'PNG')
        image.seek(0)
        upload = UploadFile(image, filename='test.png')
        try:
            return await self.tasks.upload(task_id, [upload], None)
        finally:
            await upload.close()

    async def test_new_task_cancels_old_and_discards_late_result(self):
        self.ai.return_late = True
        old = await self.tasks.create('quick', 'local')
        await self.upload(old['id'])
        await asyncio.wait_for(self.ai.started.wait(), 1)
        old_job = self.tasks.jobs[old['id']]
        new = await self.tasks.create('quick', 'local')
        await asyncio.wait_for(self.ai.cancelled.wait(), 1)
        self.assertEqual((await self.tasks.get(old['id']))['status'], 'superseded')
        self.assertFalse(self.tasks.directory(old['id']).exists())
        with self.assertRaises(HTTPException):
            await self.upload(old['id'])
        await self.upload(new['id'])
        new_job = self.tasks.jobs.get(new['id'])
        if new_job:
            await new_job
        self.ai.release.set()
        await old_job
        self.assertIsNone((await self.tasks.get(old['id']))['result'])
        self.assertEqual((await self.tasks.get(new['id']))['status'], 'completed')

    async def test_batch_reuse_idempotence_and_cancel_processing(self):
        batch = await self.tasks.create('batch', 'local')
        self.assertEqual((await self.tasks.create('batch', 'extension', True))['id'], batch['id'])
        await self.upload(batch['id'])
        await self.tasks.analyze(batch['id'])
        await asyncio.wait_for(self.ai.started.wait(), 1)
        await self.tasks.analyze(batch['id'])
        self.assertEqual(self.ai.calls, 1)
        result = await self.tasks.cancel(batch['id'])
        await asyncio.wait_for(self.ai.cancelled.wait(), 1)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(self.tasks.directory(batch['id']).exists())

    async def test_slow_analysis_is_not_stopped_and_cleans_images(self):
        # 分析阶段不再有本地时限，慢响应应当继续等待而不是被判超时。
        task = await self.tasks.create('quick', 'local')
        await self.upload(task['id'])
        await asyncio.wait_for(self.ai.started.wait(), 1)
        job = self.tasks.jobs.get(task['id'])
        await asyncio.sleep(0.3)
        self.ai.release.set()
        if job:
            await asyncio.wait_for(job, 5)
        result = await self.tasks.get(task['id'])
        self.assertEqual(result['status'], 'completed')
        self.assertIsNotNone(result['result'])
        self.assertFalse(self.tasks.directory(task['id']).exists())
        parsed = parse_result('{"questions":[{"index":1,"answer":["B"],"explanation":"omit"}]}')
        self.assertEqual(parsed, {'questions': [{'index': 1, 'answer': ['B']}]})
