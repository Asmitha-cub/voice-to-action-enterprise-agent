from fastapi import Header, HTTPException

from .config import settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    cfg = settings()
    if not cfg.auth_enabled:
        return
    if not cfg.app_api_key or x_api_key != cfg.app_api_key:
        raise HTTPException(status_code=401, detail='Invalid or missing API key.')
