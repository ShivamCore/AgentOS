"""
Workspace Lifecycle Manager
============================

Handles per-task isolated workspace directories.

Design:
  - Each task gets its own directory: <WORKSPACE_DIR>/<task_id>/
  - This prevents cross-task file contamination.
  - On completion the workspace is either:
      - ARCHIVED  → compressed to <WORKSPACE_DIR>/archive/<task_id>.tar.gz
      - CLEANED   → deleted entirely
  - A global disk quota is enforced. If the workspace root exceeds
    MAX_WORKSPACE_DISK_MB the oldest archives are pruned automatically.

Usage (in tasks.py):
    from backend.utils.workspace_manager import WorkspaceManager
    wm = WorkspaceManager()
    task_dir = wm.create(task_id)
    ...
    wm.archive(task_id)   # or wm.cleanup(task_id)
"""

import os
import shutil
import tarfile
import logging
from datetime import datetime, timezone

from backend.config import settings

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """
    Manages the full lifecycle of per-task workspace directories.
    Thread-safe — each task operates in its own subdirectory.
    """

    def __init__(self) -> None:
        self.root       = os.path.realpath(settings.WORKSPACE_DIR)
        self.archive_dir = os.path.join(self.root, "_archive")
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)

    # ── Lifecycle ────────────────────────────────────────────────────

    def create(self, task_id: str) -> str:
        """
        Creates an isolated workspace directory for a task and returns
        its absolute path. Safe to call multiple times (idempotent).
        """
        task_dir = os.path.join(self.root, task_id)
        os.makedirs(task_dir, exist_ok=True)
        logger.info(f"[workspace] created: {task_dir}")
        return task_dir

    def cleanup(self, task_id: str) -> None:
        """
        Deletes the task workspace directory entirely.
        Used for failed tasks or when archiving is disabled.
        """
        task_dir = os.path.join(self.root, task_id)
        if os.path.exists(task_dir):
            shutil.rmtree(task_dir, ignore_errors=True)
            logger.info(f"[workspace] cleaned: {task_dir}")

    def archive(self, task_id: str) -> str | None:
        """
        Compresses the task workspace into a .tar.gz archive stored in
        <WORKSPACE_DIR>/_archive/<task_id>.tar.gz, then removes the
        original directory.  Returns the archive path or None on failure.
        """
        task_dir = os.path.join(self.root, task_id)
        if not os.path.exists(task_dir):
            return None

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        archive_path = os.path.join(self.archive_dir, f"{task_id}_{ts}.tar.gz")
        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(task_dir, arcname=task_id)
            shutil.rmtree(task_dir, ignore_errors=True)
            logger.info(f"[workspace] archived: {archive_path}")
            self._enforce_disk_quota()
            return archive_path
        except Exception as e:
            logger.warning(f"[workspace] archive failed for {task_id}: {e}")
            self.cleanup(task_id)
            return None

    # ── Disk Quota ───────────────────────────────────────────────────

    def total_disk_bytes(self) -> int:
        """Returns total bytes used by the workspace root (recursive)."""
        total = 0
        for dirpath, _, filenames in os.walk(self.root):
            for fname in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fname))
                except OSError:
                    pass
        return total

    def _enforce_disk_quota(self) -> None:
        """
        Prunes the oldest archives if total workspace disk usage exceeds
        MAX_WORKSPACE_DISK_MB. Archives are sorted by mtime (oldest first).
        """
        limit_bytes = settings.MAX_WORKSPACE_DISK_MB * 1024 * 1024
        used = self.total_disk_bytes()
        if used <= limit_bytes:
            return

        archives = sorted(
            [
                (f, os.path.getmtime(os.path.join(self.archive_dir, f)))
                for f in os.listdir(self.archive_dir)
                if f.endswith(".tar.gz")
            ],
            key=lambda x: x[1],
        )

        for fname, _ in archives:
            if self.total_disk_bytes() <= limit_bytes:
                break
            full = os.path.join(self.archive_dir, fname)
            try:
                os.remove(full)
                logger.warning(f"[workspace] quota pruned: {full}")
            except OSError as e:
                logger.error(f"[workspace] failed to prune {full}: {e}")

    def get_task_dir(self, task_id: str) -> str:
        """Returns the expected task directory path (may or may not exist)."""
        return os.path.join(self.root, task_id)
