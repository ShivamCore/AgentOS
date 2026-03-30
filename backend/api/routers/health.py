"""
/health and /health/resources endpoints.

Provides:
  GET /health          — liveness probe (for Docker HEALTHCHECK / k8s)
  GET /health/resources — queue depth + memory + CPU usage snapshot
"""
import os
import psutil
import redis as redis_pkg
from fastapi import APIRouter
from backend.config import settings
from backend.db.database import SessionLocal
from backend.models.sql_models import TaskRecord
from backend.utils.workspace_manager import WorkspaceManager

router = APIRouter(tags=["health"])
_wm = WorkspaceManager()

@router.get("/health")
def liveness():
    return {"status": "ok"}


@router.get("/health/resources")
def resource_monitor():
    """
    Returns current system resource usage and queue depth.
    Useful for dashboards and alerting.
    """
    # ── Queue depth from DB ────────────────────────────────────────
    db = SessionLocal()
    try:
        pending  = db.query(TaskRecord).filter(TaskRecord.status == "pending").count()
        running  = db.query(TaskRecord).filter(TaskRecord.status == "running").count()
        failed   = db.query(TaskRecord).filter(TaskRecord.status == "failed").count()
        completed = db.query(TaskRecord).filter(TaskRecord.status == "completed").count()
    finally:
        db.close()

    # ── Redis connectivity ─────────────────────────────────────────
    redis_ok = False
    try:
        r = redis_pkg.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        r.ping()
        redis_ok = True
    except Exception:
        pass

    # ── System resources ───────────────────────────────────────────
    cpu_percent  = psutil.cpu_percent(interval=0.1)
    mem          = psutil.virtual_memory()
    mem_used_mb  = round(mem.used / 1024 / 1024, 1)
    mem_total_mb = round(mem.total / 1024 / 1024, 1)
    mem_percent  = mem.percent

    # ── Workspace disk usage (via WorkspaceManager) ────────────────
    total_bytes   = _wm.total_disk_bytes()
    disk_used_mb  = round(float(total_bytes) / 1024.0 / 1024.0, 2)
    disk_quota_mb = float(settings.MAX_WORKSPACE_DISK_MB)
    disk_pct      = round(disk_used_mb / disk_quota_mb * 100, 1) if disk_quota_mb else 0.0
    archive_count = 0
    try:
        archive_count = len([f for f in os.listdir(_wm.archive_dir) if f.endswith(".tar.gz")])
    except OSError:
        pass

    return {
        "queue": {
            "pending": pending,
            "running": running,
            "failed": failed,
            "completed": completed,
            "capacity_remaining": max(0, settings.MAX_CONCURRENT_TASKS - pending - running),
        },
        "redis": {"connected": redis_ok},
        "system": {
            "cpu_percent": cpu_percent,
            "memory_used_mb": mem_used_mb,
            "memory_total_mb": mem_total_mb,
            "memory_percent": mem_percent,
        },
        "workspace": {
            "disk_used_mb": disk_used_mb,
            "disk_quota_mb": disk_quota_mb,
            "disk_used_pct": disk_pct,
            "archives_stored": archive_count,
        },
    }
