"""
AgentOS — Application Settings
================================
Single source of truth for all runtime configuration.
Uses pydantic-settings for automatic .env loading and type validation.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration values validated and documented at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Infrastructure ──────────────────────────────────────────────────────────
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL used by Celery broker and result backend.",
    )
    DATABASE_URL: str = Field(
        default="sqlite:///./saas_backend.db",
        description="SQLAlchemy database URL. Use postgresql://... for production.",
    )
    WORKSPACE_DIR: str = Field(
        default="./workspace",
        description="Root directory for per-task sandboxed workspaces.",
    )

    # ── Concurrency & Timeouts ───────────────────────────────────────────────────
    MAX_WORKERS: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Maximum concurrent agent threads per Celery worker.",
    )
    MAX_CONCURRENT_TASKS: int = Field(
        default=10,
        ge=1,
        description="Maximum simultaneous pending + running tasks (backpressure guard).",
    )
    TASK_TIMEOUT_SECONDS: int = Field(
        default=600,
        ge=30,
        description="Hard wall-clock limit per Celery task in seconds.",
    )

    STEP_MAX_RETRIES: int = Field(
        default=1,
        ge=0,
        le=5,
        description="Retries per DAG step after a failed attempt (the retry uses the debugger agent).",
    )
    STEP_TIMEOUT_SECONDS: int = Field(
        default=180,
        ge=5,
        description="Wall-clock limit for one DAG step attempt (LLM call + sandboxed execution).",
    )
    ARCHIVE_WORKSPACES: bool = Field(
        default=False,
        description="If true, compress and delete each task workspace when the run ends.",
    )
    API_KEY: str | None = Field(
        default=None,
        description="If set, mutating API endpoints require the header 'X-API-Key: <value>'.",
    )

    # ── Rate Limiting ────────────────────────────────────────────────────────────
    RATE_LIMIT_RPM: int = Field(
        default=10,
        ge=1,
        description="Per-IP requests per minute on POST /tasks/create.",
    )

    # ── Resource Caps ────────────────────────────────────────────────────────────
    MAX_FILE_BYTES: int = Field(
        default=1 * 1024 * 1024,
        description="Maximum size in bytes for a single generated file (default 1 MB).",
    )
    MAX_LOG_BYTES_PER_TASK: int = Field(
        default=2 * 1024 * 1024,
        description="Maximum total log bytes stored per task (default 2 MB).",
    )
    MAX_WORKSPACE_DISK_MB: int = Field(
        default=2 * 1024,
        description="Maximum total workspace disk usage in MB before pruning (default 2 GB).",
    )

    # ── CORS ─────────────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: str | list[str] = Field(
        default=["http://localhost:3000"],
        description="Allowed CORS origins. Never use ['*'] in production.",
    )

    # ── LLM ──────────────────────────────────────────────────────────────────────
    OLLAMA_URL: str = Field(
        default="http://localhost:11434/api/generate",
        description="Ollama generation endpoint.",
    )
    OLLAMA_TAGS_URL: str = Field(
        default="http://localhost:11434/api/tags",
        description="Ollama model listing endpoint.",
    )

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_origins(cls, v: str | list[str]) -> list[str]:
        """Allow ALLOWED_ORIGINS to be a comma-separated string in .env."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v


# Singleton — import from here everywhere
settings = Settings()
