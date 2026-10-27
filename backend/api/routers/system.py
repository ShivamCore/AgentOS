import os
import signal
import subprocess

from typing import Any

import requests

from fastapi import APIRouter, Depends, HTTPException

from agent.llm import OLLAMA_TAGS_URL
from backend.api.security import require_api_key


def _process_control_enabled() -> None:
    # Spawning/killing host processes from HTTP is disabled unless explicitly enabled.
    if os.getenv("AGENTOS_ENABLE_PROCESS_CONTROL") != "1":
        raise HTTPException(
            status_code=403,
            detail="process control is disabled (set AGENTOS_ENABLE_PROCESS_CONTROL=1)",
        )


router = APIRouter(prefix="/system", tags=["System Controller"])

system_state: dict[str, Any] = {
    "running": False,
    "redis_process": None,
    "celery_process": None,
}


@router.post(
    "/start", dependencies=[Depends(_process_control_enabled), Depends(require_api_key)]
)
def start_system() -> dict[str, str]:
    if system_state["running"]:
        return {"status": "running"}

    try:
        # Start Redis
        redis_proc = subprocess.Popen(["redis-server"])

        # Start Celery
        celery_proc = subprocess.Popen(
            ["celery", "-A", "backend.workers.celery_app", "worker", "--loglevel=info"]
        )

        system_state["redis_process"] = redis_proc
        system_state["celery_process"] = celery_proc
        system_state["running"] = True

        return {"status": "running"}
    except Exception as e:
        # Fallback cleanup on partial start failure
        stop_system()
        raise HTTPException(
            status_code=500, detail=f"Failed to start system: {e!s}"
        ) from e


@router.post(
    "/stop", dependencies=[Depends(_process_control_enabled), Depends(require_api_key)]
)
def stop_system() -> dict[str, str]:
    if not system_state["running"]:
        return {"status": "stopped"}

    try:
        # Stop Celery Safely
        if system_state["celery_process"]:
            try:
                os.kill(system_state["celery_process"].pid, signal.SIGTERM)
                system_state["celery_process"].wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass  # already exited or did not stop in time

        # Stop Redis Safely
        if system_state["redis_process"]:
            try:
                os.kill(system_state["redis_process"].pid, signal.SIGTERM)
                system_state["redis_process"].wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass  # already exited or did not stop in time

        system_state["celery_process"] = None
        system_state["redis_process"] = None
        system_state["running"] = False

        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to stop system: {e!s}"
        ) from e


@router.get("/status")
def get_system_status() -> dict[str, Any]:
    status = "running" if system_state["running"] else "stopped"
    redis_alive = (
        system_state["redis_process"] is not None
        and system_state["redis_process"].poll() is None
    )
    celery_alive = (
        system_state["celery_process"] is not None
        and system_state["celery_process"].poll() is None
    )

    return {"status": status, "redis": redis_alive, "worker": celery_alive}


@router.get("/health")
def health_check() -> dict[str, str]:
    """Validates API Gateway connectivity across Kubernetes/AWS ALBs."""
    return {"status": "ok"}


@router.get("/llm-health")
def llm_health_check() -> dict[str, str]:
    """Active network ping checking LLM tensor availability globally."""
    try:
        response = requests.get(OLLAMA_TAGS_URL, timeout=5)
        response.raise_for_status()
        return {"status": "ok", "message": "Ollama LLM Reachable"}
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"LLM Unavailable: {e!s}") from e


@router.get("/models")
def get_available_models() -> dict[str, Any]:
    """Fetches local Ollama models dynamically."""
    try:
        response = requests.get(OLLAMA_TAGS_URL, timeout=5)
        response.raise_for_status()
        data = response.json()
        models = [m.get("name") for m in data.get("models", [])]
        return {"models": models}
    except requests.exceptions.RequestException:
        return {"models": [], "error": "Ollama not running"}
