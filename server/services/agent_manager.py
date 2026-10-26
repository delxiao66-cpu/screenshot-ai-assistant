import asyncio
import logging

from fastapi import HTTPException, WebSocket

log = logging.getLogger('assistant')


class AgentManager:
    def __init__(self):
        self.agents: dict[WebSocket, str] = {}
        self.controllers: set[WebSocket] = set()
        self.send_locks: dict[WebSocket, asyncio.Lock] = {}

    async def send(self, ws: WebSocket, payload: dict) -> bool:
        lock = self.send_locks.setdefault(ws, asyncio.Lock())
        try:
            async with lock:
                await asyncio.wait_for(ws.send_json(payload), 5)
            return True
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            log.warning('websocket.send_failed type=%s', type(exc).__name__)
            return False

    async def broadcast(self, event: str, **fields):
        clients = tuple(self.controllers)
        results = await asyncio.gather(*(self.send(ws, {'type': event, **fields}) for ws in clients))
        for ws, success in zip(clients, results):
            if not success:
                self.controllers.discard(ws)
                self.send_locks.pop(ws, None)

    def select(self, device_id: str | None = None) -> WebSocket:
        if device_id is not None:
            for ws, connected_id in reversed(tuple(self.agents.items())):
                if connected_id == device_id:
                    return ws
            raise HTTPException(409, '指定的截图 Agent 未连接，请重新连接客户端。')
        if not self.agents:
            raise HTTPException(409, '截图 Agent 未连接，请连接浏览器扩展或 macOS 客户端。')
        return next(reversed(self.agents))

    async def revoke(self, user_id: str):
        # Existing extension sockets use APP_TOKEN and remain available to the agent;
        # account revocation affects controller sessions and task authorization.
        return None

    async def close(self):
        for ws in tuple(self.agents) + tuple(self.controllers):
            try:
                await ws.close(code=1001)
            except Exception as exc:
                log.warning('websocket.close_failed type=%s', type(exc).__name__)
