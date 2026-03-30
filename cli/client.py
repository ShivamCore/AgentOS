"""
AgentOS CLI — API client
=========================
Thin, typed wrapper around the AgentOS REST API.
All HTTP calls go through here — no direct requests calls in command modules.
"""

from __future__ import annotations

import sys
from typing import Any

import requests
from requests import Response

from cli.config import cfg


class APIError(Exception):
    """Raised when the API returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API {status_code}: {detail}")


def _get(path: str, **kwargs: Any) -> dict:
    url = f"{cfg.api_url}{path}"
    try:
        r: Response = requests.get(url, timeout=cfg.request_timeout, **kwargs)
    except requests.ConnectionError:
        print(f"\n[error] Cannot connect to AgentOS at {cfg.api_url}")
        print("        Is the server running? Try: make dev  or  ./start.sh")
        sys.exit(1)
    if not r.ok:
        _raise(r)
    return r.json()  # type: ignore[no-any-return]


def _post(path: str, payload: dict, **kwargs: Any) -> dict:
    url = f"{cfg.api_url}{path}"
    try:
        r: Response = requests.post(
            url,
            json=payload,
            timeout=cfg.request_timeout,
            **kwargs,
        )
    except requests.ConnectionError:
        print(f"\n[error] Cannot connect to AgentOS at {cfg.api_url}")
        print("        Is the server running? Try: make dev  or  ./start.sh")
        sys.exit(1)
    if not r.ok:
        _raise(r)
    return r.json()  # type: ignore[no-any-return]


def _raise(r: Response) -> None:
    try:
        detail = r.json().get("detail", r.text)
    except Exception:
        detail = r.text
    raise APIError(r.status_code, str(detail))


# ── API methods ───────────────────────────────────────────────────────────────

def create_task(
    title: str,
    description: str,
    task_type: str,
    tech_stack: list[str],
    features: list[str],
    max_steps: int,
    max_time: int,
    risk_level: str,
    model: str,
) -> dict:
    return _post(
        "/tasks/create",
        {
            "title": title,
            "description": description,
            "task_type": task_type,
            "tech_stack": tech_stack,
            "features": features,
            "constraints": {
                "max_steps": max_steps,
                "max_time": max_time,
                "risk_level": risk_level,
            },
            "model": model,
        },
    )


def get_task(task_id: str) -> dict:
    return _get(f"/tasks/{task_id}")


def get_task_steps(task_id: str) -> list[dict]:
    data = _get(f"/tasks/{task_id}/steps")
    return data if isinstance(data, list) else []


def get_task_result(task_id: str) -> dict:
    return _get(f"/tasks/{task_id}/result")


def get_task_logs(task_id: str) -> list[dict]:
    data = _get(f"/logs/{task_id}")
    return data if isinstance(data, list) else []


def get_task_explain(task_id: str) -> dict:
    return _get(f"/tasks/{task_id}/explain")


def list_tasks(limit: int = 20) -> list[dict]:
    data = _get("/tasks")
    return data[:limit] if isinstance(data, list) else []


def retry_task(task_id: str) -> dict:
    return _post(f"/retry/{task_id}", {})


def health() -> dict:
    return _get("/health")
