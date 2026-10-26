import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException

from server.services.agent_manager import AgentManager
from tests import test_latest_task


class AgentSelectionTests(unittest.TestCase):
    def test_explicit_device_does_not_capture_on_latest_other_agent(self):
        manager = AgentManager()
        mac, browser = object(), object()
        manager.agents = {mac: 'macos_test', browser: 'extension_test'}
        self.assertIs(manager.select('macos_test'), mac)
        self.assertIs(manager.select(), browser)

    def test_disconnected_device_does_not_fall_back(self):
        manager = AgentManager()
        manager.agents[object()] = 'extension_test'
        with self.assertRaises(HTTPException) as result:
            manager.select('macos_missing')
        self.assertEqual(result.exception.status_code, 409)


class TargetedCaptureTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_latest_task.LatestTaskTests.asyncSetUp
    asyncTearDown = test_latest_task.LatestTaskTests.asyncTearDown

    async def test_capture_request_routes_to_named_mac(self):
        mac, browser = object(), object()
        self.tasks.agents.agents = {mac: 'macos_test', browser: 'extension_test'}
        self.tasks.agents.send = AsyncMock(return_value=True)
        task = await self.tasks.create('batch', 'local')
        captured = await self.tasks.request_capture(task['id'], 'macos_test')
        self.assertEqual(captured['status'], 'capturing')
        self.assertIs(self.tasks.pending[task['id']][1], mac)
        self.tasks.agents.send.assert_awaited_once()
        args = self.tasks.agents.send.call_args.args
        self.assertIs(args[0], mac)
        self.assertEqual(args[1]['type'], 'capture.request')
