import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from openai import AsyncOpenAI

from server.config import Settings
from server.services.deepseek_client import DeepSeekClient
from tests.test_app import RESULT


class DeepSeekTests(unittest.IsolatedAsyncioTestCase):
    async def call_with_statuses(self, outcomes):
        calls = []

        async def handler(request):
            calls.append(json.loads(request.content))
            outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
            if outcome == 'timeout':
                raise httpx.ReadTimeout('test-only', request=request)
            if outcome == 'network':
                raise httpx.ConnectError('test-only', request=request)
            if outcome != 200:
                return httpx.Response(outcome, json={'error': {'message': 'test-only'}})
            return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1,
                'model': 'configured-model', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': json.dumps(RESULT)}}]})

        def client_factory(**kwargs):
            self.assertIsNone(kwargs['timeout'])
            self.assertEqual(kwargs['max_retries'], 0)
            return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

        settings = Settings(token='test', api_key='test', model='configured-model')
        with patch('server.services.deepseek_client.AsyncOpenAI', side_effect=client_factory), \
             patch('server.services.deepseek_client.asyncio.sleep', new=AsyncMock()):
            try:
                result = await DeepSeekClient(settings).analyze([b'one', b'two'])
            except ValueError as error:
                result = error
        return calls, result

    async def test_single_request_contains_all_images_and_configured_model(self):
        calls, result = await self.call_with_statuses([200])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['model'], 'configured-model')
        # 输出上限只做宽松兜底，不限制正常作答。
        self.assertEqual(calls[0]['max_tokens'], Settings(token='test', api_key='test', model='configured-model').max_output_tokens)
        self.assertGreaterEqual(calls[0]['max_tokens'], 8192)
        self.assertEqual(calls[0]['thinking'], {'type': 'disabled'})
        self.assertEqual(calls[0]['response_format'], {'type': 'json_object'})
        self.assertEqual(len(calls[0]['messages'][1]['content']), 3)
        self.assertEqual(result[0], RESULT)

    async def test_no_retry_network_timeout_429_5xx(self):
        for status in ('network', 'timeout', 429, 500, 503):
            with self.subTest(status=status):
                calls, result = await self.call_with_statuses([status, 200])
                self.assertEqual(len(calls), 1)
                self.assertIsInstance(result, ValueError)

    async def test_no_retry_on_bad_key_or_request(self):
        for status in (400, 401, 402, 403):
            calls, result = await self.call_with_statuses([status, 200])
            self.assertEqual(len(calls), 1)
            self.assertIsInstance(result, ValueError)
