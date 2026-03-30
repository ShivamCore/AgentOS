"""
AgentOS CLI — Config system
============================
Reads from ~/.agentos/config.yaml, with env var overrides and sane defaults.
Config is created automatically on first run.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

# ── Config file location ──────────────────────────────────────────────────────
_CONFIG_DIR = Path.home() / ".agentos"
_CONFIG_FILE = _CONFIG_DIR / "config.yaml"

# ── Defaults ─────────────────────────────────────────────────────────────────
_DEFAULTS: dict = {
    "api_url": "http://localhost:8000",
    "default_model": "Auto",
    "default_task_type": "build_app",
    "poll_interval_seconds": 2,
    "request_timeout_seconds": 30,
}


def _load_file() -> dict:
    """Load YAML config, returning empty dict if file doesn't exist."""
    if not _CONFIG_FILE.exists():
        return {}
    try:
        with _CONFIG_FILE.open() as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_defaults() -> None:
    """Write default config on first run."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not _CONFIG_FILE.exists():
        with _CONFIG_FILE.open("w") as f:
            yaml.dump(_DEFAULTS, f, default_flow_style=False, sort_keys=True)


class Config:
    """
    Layered config: defaults < file < environment variables.
    Environment variables take precedence over everything.
    """

    def __init__(self) -> None:
        _write_defaults()
        file_cfg = _load_file()
        self._data = {**_DEFAULTS, **file_cfg}

    @property
    def api_url(self) -> str:
        return os.getenv("AGENTOS_API_URL", self._data.get("api_url", _DEFAULTS["api_url"])).rstrip("/")

    @property
    def default_model(self) -> str:
        return os.getenv("AGENTOS_MODEL", self._data.get("default_model", _DEFAULTS["default_model"]))

    @property
    def default_task_type(self) -> str:
        return self._data.get("default_task_type", _DEFAULTS["default_task_type"])

    @property
    def poll_interval(self) -> float:
        return float(self._data.get("poll_interval_seconds", _DEFAULTS["poll_interval_seconds"]))

    @property
    def request_timeout(self) -> int:
        return int(self._data.get("request_timeout_seconds", _DEFAULTS["request_timeout_seconds"]))

    @property
    def config_file(self) -> Path:
        return _CONFIG_FILE

    def as_dict(self) -> dict:
        return {
            "api_url": self.api_url,
            "default_model": self.default_model,
            "default_task_type": self.default_task_type,
            "poll_interval_seconds": self.poll_interval,
            "request_timeout_seconds": self.request_timeout,
            "config_file": str(self.config_file),
        }


# Singleton — import this everywhere
cfg = Config()
