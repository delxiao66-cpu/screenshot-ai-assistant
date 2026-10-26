import asyncio
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from server.config import Settings
from server.main import create_app
from server.services.deepseek_client import build_messages, parse_result
from server.services.image_processor import preprocess


RESULT = {'questions': [{'index': 1, 'answer': ['A']}]}


def image_bytes(color='white', fmt='PNG'):
    stream = io.BytesIO()
    Image.new('RGB', (60, 40), color).save(stream, fmt)
    return stream.getvalue()


class FakeAI:
    """Test-only model; never instantiated by application code."""
    def __init__(self):
        self.calls = []
        self.failure = False
        self.raw_only = False
        self.running = 0
        self.max_running = 0

    async def analyze(self, images):
        self.running += 1
        self.max_running = max(self.running, self.max_running)
        self.calls.append(images)
        try:
            await asyncio.sleep(0.08)
            if self.failure:
                raise ValueError('测试模型失败')
            return (None, '模型返回非 JSON 文本') if self.raw_only else (RESULT, json.dumps(RESULT))
        finally:
            self.running -= 1


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Settings(token='test-access-token', api_key='test-api-key', model='test-vision', data_dir=Path(self.temp.name))
        self.ai = FakeAI()
        self.app = create_app(self.config, self.ai)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.client.headers['Authorization'] = 'Bearer test-access-token'

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def task(self, mode='batch'):
        response = self.client.post('/api/tasks', json={'mode': mode})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def upload(self, task_id, colors=('red',), **kwargs):
        return self.client.post(f'/api/tasks/{task_id}/images', files=[('images', (f'{i}.png', image_bytes(color), 'image/png')) for i, color in enumerate(colors)], **kwargs)

    def wait_done(self, task_id):
        for _ in range(150):
            result = self.client.get(f'/api/tasks/{task_id}').json()
            if result['status'] in ('completed', 'failed'):
                return result
            time.sleep(0.02)
        self.fail('任务未结束')

    def test_all_api_auth_and_invalid_input(self):
        routes = [('GET', '/api/health'), ('GET', '/api/status'), ('POST', '/api/tasks'),
                  ('GET', '/api/tasks/x'), ('POST', '/api/tasks/x/capture'), ('POST', '/api/tasks/x/images'),
                  ('GET', '/api/tasks/x/images'), ('GET', '/api/tasks/x/images/y'),
                  ('DELETE', '/api/tasks/x/images/y'), ('DELETE', '/api/tasks/x/images'),
                  ('POST', '/api/tasks/x/analyze'), ('POST', '/api/tasks/x/cancel'),
                  ('GET', '/api/history'), ('DELETE', '/api/history')]
        for method, path in routes:
            response = self.client.request(method, path, headers={'Authorization': 'Bearer wrong'})
            self.assertEqual(response.status_code, 401, path)
            self.assertIn('error', response.json())
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertEqual(self.client.post('/api/tasks', json={'mode': 'bad'}).status_code, 422)
        self.assertEqual(self.client.get('/api/history?limit=201').status_code, 422)
        self.assertEqual(self.client.get('/api/tasks/not-a-uuid').status_code, 404)

    def test_batch_order_delete_atomic_upload_and_cleanup(self):
        task_id = self.task()
        self.assertEqual(self.client.post('/api/tasks', json={'mode': 'batch', 'reuse': True}).json()['id'], task_id)
        self.assertEqual(self.client.post(f'/api/tasks/{task_id}/analyze').status_code, 400)
        self.assertEqual(self.upload(task_id, ('red', 'green')).status_code, 200)
        self.assertEqual(self.upload(task_id, ('blue', 'yellow', 'white')).status_code, 200)
        items = self.client.get(f'/api/tasks/{task_id}/images').json()
        self.assertEqual([item['sequence'] for item in items], [1, 2, 3, 4, 5])
        self.assertEqual(self.client.get(f'/api/tasks/{task_id}/images/{items[0]["id"]}').status_code, 200)
        self.client.delete(f'/api/tasks/{task_id}/images/{items[1]["id"]}')
        items = self.client.get(f'/api/tasks/{task_id}/images').json()
        self.assertEqual([item['sequence'] for item in items], [1, 2, 3, 4])
        invalid = self.client.post(f'/api/tasks/{task_id}/images', files=[
            ('images', ('ok.png', image_bytes(), 'image/png')), ('images', ('bad.png', b'invalid', 'image/png'))])
        self.assertEqual(invalid.status_code, 415)
        self.assertEqual(self.client.get(f'/api/tasks/{task_id}').json()['image_count'], 4)
        self.assertEqual(len(self.ai.calls), 0)
        self.assertEqual(self.client.post(f'/api/tasks/{task_id}/analyze').status_code, 200)
        self.assertEqual(self.upload(task_id).status_code, 409)
        self.assertEqual(self.client.post(f'/api/tasks/{task_id}/analyze').status_code, 200)
        self.assertEqual(self.client.delete(f'/api/tasks/{task_id}/images').status_code, 409)
        result = self.wait_done(task_id)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result'], RESULT)
        self.assertEqual(len(self.ai.calls), 1)
        self.assertEqual(len(self.ai.calls[0]), 4)
        expected = [preprocess(image_bytes(color))[0] for color in ('red', 'blue', 'yellow', 'white')]
        self.assertEqual(self.ai.calls[0], expected)
        self.assertFalse((self.config.tmp_dir / task_id).exists())
        self.assertEqual(self.client.get(f'/api/tasks/{task_id}/images/{items[0]["id"]}').status_code, 409)

    def test_quick_auto_sequential_completed_tasks(self):
        first = self.task('quick')
        self.assertEqual(self.upload(first, ('red',)).status_code, 200)
        self.assertEqual(self.wait_done(first)['status'], 'completed')
        second = self.task('quick')
        self.assertEqual(self.upload(second, ('blue', 'green')).status_code, 200)
        self.assertEqual(self.wait_done(second)['status'], 'completed')
        self.assertEqual([len(call) for call in self.ai.calls], [1, 2])
        self.assertEqual(self.ai.max_running, 1)

    def test_limits_clear_cancel_and_missing_key(self):
        task_id = self.task()
        self.assertEqual(self.upload(task_id, tuple('red' for _ in range(20))).status_code, 200)
        self.assertEqual(self.upload(task_id).status_code, 413)
        self.assertEqual(self.client.delete(f'/api/tasks/{task_id}/images').json()['image_count'], 0)
        self.config.max_bytes = 10
        self.assertEqual(self.upload(task_id).status_code, 413)
        self.config.max_bytes = 41943040
        self.upload(task_id)
        self.config.api_key = ''
        self.assertEqual(self.client.post(f'/api/tasks/{task_id}/analyze').status_code, 503)
        self.assertEqual(self.client.get(f'/api/tasks/{task_id}').json()['status'], 'collecting')
        self.assertEqual(self.client.post(f'/api/tasks/{task_id}/cancel').json()['status'], 'failed')
        self.assertFalse((self.config.tmp_dir / task_id).exists())
        self.assertEqual(self.client.get('/api/history').json()[0]['id'], task_id)
        self.client.delete('/api/history')
        self.assertEqual(self.client.get('/api/history').json(), [])
        self.assertEqual(self.client.get(f'/api/tasks/{task_id}').status_code, 404)

    def test_model_failure_raw_fallback(self):
        self.ai.failure = True
        task_id = self.task('quick'); self.upload(task_id)
        self.assertEqual(self.wait_done(task_id)['status'], 'failed')
        self.assertFalse((self.config.tmp_dir / task_id).exists())
        self.ai.failure = False; self.ai.raw_only = True
        task_id = self.task('quick'); self.upload(task_id)
        result = self.wait_done(task_id)
        self.assertEqual(result['status'], 'completed')
        self.assertIsNone(result['result'])
        self.assertEqual(result['raw_result'], '模型返回非 JSON 文本')

    def test_websocket_auth_capture_and_late_upload(self):
        for path in ('/ws/agent', '/ws/controller'):
            with self.client.websocket_connect(path + '?token=test-access-token') as ws:
                ws.send_json({'type': 'ping'})
                with self.assertRaises(WebSocketDisconnect): ws.receive_json()
        task_id = self.task()
        self.assertEqual(self.client.post(f'/api/tasks/{task_id}/capture').status_code, 409)
        with self.client.websocket_connect('/ws/controller') as controller:
            controller.send_json({'type': 'auth', 'token': 'test-access-token'})
            self.assertEqual(controller.receive_json()['type'], 'auth.ok')
            self.assertEqual(controller.receive_json()['type'], 'agent.disconnected')
            with self.client.websocket_connect('/ws/agent') as agent:
                agent.send_json({'type': 'auth', 'token': 'test-access-token'})
                self.assertEqual(agent.receive_json()['type'], 'auth.ok')
                agent.send_json({'type': 'agent.connect', 'device_id': 'test-device'})
                self.assertEqual(agent.receive_json()['type'], 'agent.connected')
                self.assertEqual(controller.receive_json()['type'], 'agent.connected')
                agent.send_json({'type': 'ping'})
                self.assertEqual(agent.receive_json()['type'], 'pong')
                self.assertEqual(self.client.post(f'/api/tasks/{task_id}/capture').json()['status'], 'capturing')
                request = agent.receive_json()
                self.assertEqual(request['type'], 'capture.request')
                self.assertEqual(self.upload(task_id).status_code, 409)
                self.assertEqual(self.upload(task_id, headers={'X-Capture-Id': request['capture_id']}).status_code, 200)
                self.assertEqual(self.upload(task_id, headers={'X-Capture-Id': request['capture_id']}).status_code, 409)
                self.assertEqual(self.client.get(f'/api/tasks/{task_id}').json()['status'], 'collecting')
                self.client.post(f'/api/tasks/{task_id}/capture')
                agent.receive_json()
            for _ in range(30):
                task = self.client.get(f'/api/tasks/{task_id}').json()
                if task['status'] == 'collecting': break
                time.sleep(0.01)
            self.assertEqual(task['status'], 'collecting')
            self.assertIn('中断', task['error'])

    def test_recovery_stale_and_history_pruning(self):
        task_id = self.task(); self.upload(task_id)
        async def age_and_sweep():
            await self.app.state.db.execute("UPDATE tasks SET updated_at='2000-01-01' WHERE id=?", (task_id,))
            await self.app.state.tasks.sweep()
        self.client.portal.call(age_and_sweep)
        self.assertEqual(self.client.get(f'/api/tasks/{task_id}').json()['status'], 'failed')
        self.assertFalse((self.config.tmp_dir / task_id).exists())
        async def add_history():
            from uuid import uuid4
            async with self.app.state.db.connect() as db:
                for i in range(205):
                    await db.execute('INSERT INTO tasks(id,mode,source,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                        (str(uuid4()), 'quick', 'local', 'completed', f'2020-01-01T00:{i:03}', '2020-01-01'))
            await self.app.state.tasks.prune()
        self.client.portal.call(add_history)
        self.assertEqual(len(self.client.get('/api/history?limit=200').json()), 200)
        count = self.client.portal.call(self.app.state.db.rows, 'SELECT COUNT(*) AS n FROM tasks')
        self.assertEqual(count[0]['n'], 200)

    def test_cleanup_failure_is_visible_and_retried(self):
        task_id = self.task(); self.upload(task_id)
        with patch('server.services.task_manager.shutil.rmtree', side_effect=PermissionError('test')):
            result = self.client.post(f'/api/tasks/{task_id}/cancel').json()
            self.assertIn('清理失败', result['error'])
            self.assertTrue((self.config.tmp_dir / task_id).exists())
        self.client.portal.call(self.app.state.tasks.sweep)
        self.assertFalse((self.config.tmp_dir / task_id).exists())

    def test_sqlite_error_json(self):
        import sqlite3
        from unittest.mock import AsyncMock
        with patch.object(self.app.state.db, 'rows', AsyncMock(side_effect=sqlite3.OperationalError('test'))):
            result = self.client.get('/api/health')
            self.assertEqual(result.status_code, 500)
            self.assertIn('数据库', result.json()['error'])

    def test_capture_timeout_and_cancellation_reject_stale_upload(self):
        task_id = self.task()
        with self.client.websocket_connect('/ws/agent') as agent:
            agent.send_json({'type': 'auth', 'token': 'test-access-token'}); agent.receive_json()
            agent.send_json({'type': 'agent.connect', 'device_id': 'test-device'}); agent.receive_json()
            self.client.post(f'/api/tasks/{task_id}/capture')
            request = agent.receive_json()
            async def expire():
                capture_id, ws, stamp = self.app.state.tasks.pending[task_id]
                self.app.state.tasks.pending[task_id] = (capture_id, ws, stamp - 50)
                await self.app.state.tasks.sweep()
            self.client.portal.call(expire)
            self.assertEqual(self.client.get(f'/api/tasks/{task_id}').json()['status'], 'collecting')
            self.assertEqual(self.upload(task_id, headers={'X-Capture-Id': request['capture_id']}).status_code, 409)
            self.client.post(f'/api/tasks/{task_id}/capture')
            request = agent.receive_json()
            self.client.post(f'/api/tasks/{task_id}/cancel')
            self.assertEqual(self.upload(task_id, headers={'X-Capture-Id': request['capture_id']}).status_code, 409)

    def test_startup_recovers_interrupted_tasks_and_log_redaction(self):
        task_id = self.task(); self.upload(task_id)
        async def interrupted():
            await self.app.state.db.execute("UPDATE tasks SET status='processing' WHERE id=?", (task_id,))
            await self.app.state.tasks.recover()
        self.client.portal.call(interrupted)
        self.assertEqual(self.client.get(f'/api/tasks/{task_id}').json()['status'], 'failed')
        self.assertFalse((self.config.tmp_dir / task_id).exists())
        logs = (self.config.data_dir / 'logs' / 'app.log').read_text(encoding='utf-8')
        for secret in ('test-access-token', 'test-api-key', 'Authorization', 'base64'):
            self.assertNotIn(secret, logs)


class ProcessingTests(unittest.TestCase):
    def test_json_and_image_request(self):
        self.assertEqual(parse_result('```json\n' + json.dumps(RESULT) + '\n```'), RESULT)
        self.assertIsNone(parse_result('{"questions":"bad"}'))
        self.assertIsNone(parse_result('not json'))
        messages = build_messages([b'first', b'second'])
        self.assertEqual(len(messages), 2)
        self.assertEqual([item['image_url']['url'] for item in messages[1]['content'][1:]],
                         ['data:image/jpeg;base64,Zmlyc3Q=', 'data:image/jpeg;base64,c2Vjb25k'])

    def test_image_formats_orientation_and_resize(self):
        for fmt in ('PNG', 'JPEG', 'WEBP'):
            data, width, height = preprocess(image_bytes(fmt=fmt))
            self.assertEqual((width, height), (60, 40))
            self.assertTrue(data.startswith(b'\xff\xd8'))
        source = Image.new('RGB', (5000, 100), 'white')
        exif = Image.Exif(); exif[274] = 6
        stream = io.BytesIO(); source.save(stream, 'JPEG', exif=exif)
        _, width, height = preprocess(stream.getvalue())
        self.assertEqual(height, 3072)
        self.assertLess(width, height)


if __name__ == '__main__':
    unittest.main()
