from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, UploadFile, File
from pydantic import BaseModel

from server.auth import require_token

router = APIRouter(prefix='/api', dependencies=[Depends(require_token)])


class CreateTask(BaseModel):
    reuse: bool = False
    mode: Literal['quick', 'batch'] = 'batch'
    source: Literal['controller', 'extension', 'local'] = 'controller'


@router.get('/health')
async def health(request: Request):
    await request.app.state.db.rows('SELECT 1')
    return {'status': 'ok'}


@router.get('/status')
async def status(request: Request):
    state = request.app.state
    active = await state.db.rows("SELECT * FROM tasks WHERE status IN ('collecting','capturing','processing') ORDER BY created_at DESC")
    from server.database import public_task
    return {'server': 'online', 'agent_online': bool(state.agents.agents),
            'device_ids': list(state.agents.agents.values()), 'max_images': state.settings.max_images,
            'max_task_image_bytes': state.settings.max_bytes,
            'deepseek_configured': bool(state.settings.api_key and state.settings.model),
            'thinking_mode': state.settings.thinking,
            'active_tasks': [public_task(row) for row in active]}


@router.post('/tasks', status_code=201)
async def create(body: CreateTask, request: Request):
    return await request.app.state.tasks.create(body.mode, body.source, body.reuse)


@router.get('/tasks/{task_id}')
async def get(task_id: str, request: Request):
    return await request.app.state.tasks.get(task_id)


@router.post('/tasks/{task_id}/capture')
async def capture(task_id: str, request: Request, device_id: str | None = Query(None, min_length=1, max_length=100)):
    return await request.app.state.tasks.request_capture(task_id, device_id)


@router.post('/tasks/{task_id}/images')
async def upload(task_id: str, request: Request, images: Annotated[list[UploadFile], File()],
                 x_capture_id: Annotated[str | None, Header()] = None):
    try:
        return await request.app.state.tasks.upload(task_id, images, x_capture_id)
    finally:
        for image in images:
            await image.close()


@router.get('/tasks/{task_id}/images')
async def images(task_id: str, request: Request):
    return await request.app.state.tasks.images(task_id)


@router.get('/tasks/{task_id}/images/{image_id}')
async def preview(task_id: str, image_id: str, request: Request):
    data = await request.app.state.tasks.preview(task_id, image_id)
    return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@router.delete('/tasks/{task_id}/images/{image_id}')
async def delete_image(task_id: str, image_id: str, request: Request):
    return await request.app.state.tasks.delete_images(task_id, image_id)


@router.delete('/tasks/{task_id}/images')
async def clear_images(task_id: str, request: Request):
    return await request.app.state.tasks.delete_images(task_id)


@router.post('/tasks/{task_id}/analyze')
async def analyze(task_id: str, request: Request):
    return await request.app.state.tasks.analyze(task_id)


@router.post('/tasks/{task_id}/cancel')
async def cancel(task_id: str, request: Request):
    return await request.app.state.tasks.cancel(task_id)


@router.get('/history')
async def history(request: Request, limit: int = Query(50, ge=1, le=200)):
    return await request.app.state.tasks.history(limit)


@router.delete('/history')
async def clear_history(request: Request):
    await request.app.state.tasks.clear_history()
    return {'message': '历史记录已清空。'}
