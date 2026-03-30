from celery import Celery
from backend.config import settings

celery_app = Celery(
    "coder_agent_tasks",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["backend.workers.tasks"],   # ← tells workers which modules to import
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # ── Global task timeout ────────────────────────────────────────
    # Raise SoftTimeLimitExceeded inside the task 30s before hard kill.
    # This allows the except block in run_agent_task to mark the task
    # as "failed" and broadcast the final state before the process dies.
    task_soft_time_limit=settings.TASK_TIMEOUT_SECONDS - 30,
    task_time_limit=settings.TASK_TIMEOUT_SECONDS,
    # Prevent a single task from starving other queue items
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
