import secrets

from fastapi import HTTPException, Request


def valid_token(provided: str, expected: str) -> bool:
    return bool(expected) and secrets.compare_digest(provided.encode(), expected.encode())


async def require_token(request: Request) -> None:
    if getattr(request.state, 'user', None):
        return
    raise HTTPException(401, '请先登录账号。', headers={'WWW-Authenticate': 'Bearer'})
