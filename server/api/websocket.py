import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from anyio import CancelScope

from server.auth import valid_token
from server.accounts import resolve as resolve_account

router = APIRouter()
log = logging.getLogger('assistant')


async def serve(ws: WebSocket, agent: bool):
    await ws.accept()
    manager = ws.app.state.agents
    try:
        auth = await asyncio.wait_for(ws.receive_json(), 5)
        account = await resolve_account(ws.app.state.db, auth.get('token', '')) if isinstance(auth, dict) else None
        if not isinstance(auth, dict) or auth.get('type') != 'auth' or not isinstance(auth.get('token'), str) or not account:
            await ws.close(code=4401, reason='访问令牌错误')
            return
        await manager.send(ws, {'type': 'auth.ok'})
        if agent:
            hello = await asyncio.wait_for(ws.receive_json(), 5)
            device_id = hello.get('device_id') if isinstance(hello, dict) else None
            if not isinstance(hello, dict) or hello.get('type') != 'agent.connect' or not isinstance(device_id, str) or not 1 <= len(device_id) <= 100:
                await ws.close(code=4400, reason='设备信息无效')
                return
            # A reconnect replaces this device's earlier socket.
            for old, old_id in tuple(manager.agents.items()):
                if old_id == device_id:
                    manager.agents.pop(old, None)
                    await old.close(code=4000, reason='设备已重新连接')
            manager.agents[ws] = device_id
            await manager.send(ws, {'type': 'agent.connected', 'device_id': device_id})
            await manager.broadcast('agent.connected', device_id=device_id)
            log.info('agent.connected')
        else:
            manager.controllers.add(ws)
            await manager.send(ws, {'type': 'agent.connected' if manager.agents else 'agent.disconnected',
                                    'device_ids': list(manager.agents.values())})
        while True:
            payload = await asyncio.wait_for(ws.receive_json(), 60)
            if not isinstance(payload, dict):
                await ws.close(code=4400, reason='消息格式无效')
                break
            kind = payload.get('type')
            if kind == 'ping':
                await manager.send(ws, {'type': 'pong'})
            elif kind == 'pong':
                continue
            elif agent and kind == 'capture.error':
                await ws.app.state.tasks.capture_error(str(payload.get('task_id', '')),
                    str(payload.get('capture_id', '')), ws, str(payload.get('message', '')))
            else:
                await manager.send(ws, {'type': 'error', 'message': '不支持的消息类型。'})
    except WebSocketDisconnect:
        log.info('websocket.disconnected')
    except Exception as exc:
        log.warning('websocket.error type=%s', type(exc).__name__)
        try:
            await ws.close(code=4400, reason='连接超时或消息无效')
        except RuntimeError:
            log.debug('websocket.already_closed')
    finally:
        device_id = manager.agents.pop(ws, None)
        manager.controllers.discard(ws)
        manager.send_locks.pop(ws, None)
        # A cancelled websocket handler must still release its pending capture.
        with CancelScope(shield=True):
            if agent:
                await ws.app.state.tasks.disconnected(ws)
            if device_id:
                await manager.broadcast('agent.disconnected', device_id=device_id, agent_online=bool(manager.agents))
                log.info('agent.disconnected')


@router.websocket('/ws/agent')
async def agent_socket(ws: WebSocket):
    await serve(ws, True)


@router.websocket('/ws/controller')
async def controller_socket(ws: WebSocket):
    await serve(ws, False)
