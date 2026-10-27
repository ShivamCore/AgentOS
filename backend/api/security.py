"""Optional API-key authentication for mutating endpoints."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from backend.config import settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """
    If ``API_KEY`` is configured, mutating endpoints require ``X-API-Key``.
    With no key configured the API is intended for localhost use only
    (see docs/security.md).
    """
    expected = settings.API_KEY
    if not expected:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid X-API-Key",
        )
