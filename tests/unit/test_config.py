"""
tests/unit/test_config.py
==========================
Unit tests for backend/config.py (Settings, pydantic-settings)
"""

from __future__ import annotations

import os
from unittest.mock import patch


class TestSettingsValidation:
    def test_settings_instantiates_with_defaults(self):
        """Settings must instantiate with no env vars set beyond defaults."""
        from backend.config import Settings
        s = Settings()
        assert s.REDIS_URL is not None
        assert s.DATABASE_URL is not None

    def test_max_workers_has_floor_constraint(self):
        """MAX_WORKERS=0 must raise ValidationError (ge=1)."""
        from pydantic import ValidationError
        from backend.config import Settings

        with pytest.raises(ValidationError):
            Settings(MAX_WORKERS=0)

    def test_max_workers_has_ceiling_constraint(self):
        """MAX_WORKERS=999 must raise ValidationError (le=32)."""
        from pydantic import ValidationError
        from backend.config import Settings

        with pytest.raises(ValidationError):
            Settings(MAX_WORKERS=999)

    def test_rate_limit_rpm_minimum_is_one(self):
        """RATE_LIMIT_RPM=0 must raise ValidationError (ge=1)."""
        from pydantic import ValidationError
        from backend.config import Settings

        with pytest.raises(ValidationError):
            Settings(RATE_LIMIT_RPM=0)

    def test_all_fields_have_type_annotations(self):
        """Every field on Settings must have a declared type."""
        from backend.config import Settings

        for field_name, field_info in Settings.model_fields.items():
            assert field_info.annotation is not None, \
                f"Settings.{field_name} has no type annotation"

    def test_default_origins_not_wildcard(self):
        """Default ALLOWED_ORIGINS must not contain '*'. Wildcard CORS = security hole."""
        from backend.config import Settings
        s = Settings()
        assert "*" not in s.ALLOWED_ORIGINS, \
            "ALLOWED_ORIGINS must never default to ['*']"

    def test_allowed_origins_parses_comma_string(self):
        """ALLOWED_ORIGINS env var as comma-separated string must parse to a list."""
        from backend.config import Settings

        with patch.dict(os.environ, {"ALLOWED_ORIGINS": "http://a.com,http://b.com"}):
            s = Settings()

        assert isinstance(s.ALLOWED_ORIGINS, list)
        assert "http://a.com" in s.ALLOWED_ORIGINS
        assert "http://b.com" in s.ALLOWED_ORIGINS

    def test_env_file_encoding_is_utf8(self):
        """Settings must declare UTF-8 encoding for .env to handle unicode paths."""
        from backend.config import Settings
        config = Settings.model_config
        assert config.get("env_file_encoding") == "utf-8"

    def test_task_timeout_minimum(self):
        """TASK_TIMEOUT_SECONDS must be at least 30 seconds."""
        from pydantic import ValidationError
        from backend.config import Settings

        with pytest.raises(ValidationError):
            Settings(TASK_TIMEOUT_SECONDS=5)

    def test_settings_singleton_is_importable(self):
        """The module-level `settings` instance must be importable and usable."""
        from backend.config import settings
        assert settings is not None
        assert isinstance(settings.MAX_WORKERS, int)


import pytest
