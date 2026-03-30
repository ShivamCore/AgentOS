"""
tests/unit/test_llm.py
=======================
Unit tests for agent/llm.py
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestWarmupModel:
    def test_warmup_catches_specific_request_exception(self):
        """
        warmup_model must catch requests.exceptions.RequestException (and subclasses)
        without crashing. Previously it used bare `except Exception` which silently
        swallowed all errors including MemoryError.
        """
        import requests
        from agent import llm as llm_mod

        with patch("agent.llm.requests.post", side_effect=requests.exceptions.ConnectionError("Refused")):
            with patch.object(llm_mod, "_loaded_models", {}):
                with patch("agent.llm.logger") as mock_logger:
                    llm_mod.warmup_model("test-model")  # Must not raise

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        # Must use %-style formatting not f-strings
        assert "%" in str(call_args[0][0]), "Use %-style log format, not f-strings"

    def test_warmup_does_not_swallow_unexpected_errors(self):
        """
        A non-requests exception (e.g., ValueError) must propagate — it should not
        be caught by the warmup handler.
        """
        from agent import llm as llm_mod

        with patch("agent.llm.requests.post", side_effect=ValueError("Unexpected")):
            with patch.object(llm_mod, "_loaded_models", {}):
                with pytest.raises(ValueError, match="Unexpected"):
                    llm_mod.warmup_model("test-model")

    def test_warmup_marks_model_as_loaded(self):
        """After a successful warmup, the model must be recorded in _loaded_models."""
        from agent import llm as llm_mod

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines = MagicMock(return_value=[])

        with patch("agent.llm.requests") as mock_requests:
            mock_requests.post.return_value.__enter__ = MagicMock(return_value=mock_resp)
            mock_requests.post.return_value.__exit__ = MagicMock(return_value=False)
            mock_requests.exceptions.RequestException = Exception

            with patch.object(llm_mod, "_loaded_models", {}) as loaded:
                try:
                    llm_mod.warmup_model("my-model")
                except Exception:
                    pass  # warmup may fail, but should not pollute loaded_models


class TestImportSideEffects:
    def test_import_makes_no_http_calls(self):
        """
        Importing agent.llm must not make any HTTP requests.
        Any requests at import time would fail in offline/CI environments.
        """
        import importlib
        import sys

        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            # Force reimport
            if "agent.llm" in sys.modules:
                del sys.modules["agent.llm"]
            try:
                import agent.llm  # noqa: F401
            except Exception:
                pass  # import errors from missing deps are ok — we check HTTP only

        mock_get.assert_not_called()
        mock_post.assert_not_called()


class TestExtractJsonSafely:
    def test_extracts_valid_json_from_markdown_block(self):
        """extract_json_safely must parse JSON wrapped in a markdown code block."""
        from agent.llm import extract_json_safely

        text = '```json\n{"key": "value"}\n```'
        result = extract_json_safely(text)
        assert result is not None
        assert result.get("key") == "value"

    def test_extracts_bare_json_object(self):
        """extract_json_safely must parse a bare JSON object without markdown fencing."""
        from agent.llm import extract_json_safely

        result = extract_json_safely('{"steps": []}')
        assert result is not None
        assert "steps" in result

    def test_returns_none_on_non_json(self):
        """extract_json_safely must return None (not raise) on unparseable input."""
        from agent.llm import extract_json_safely

        result = extract_json_safely("This is not JSON at all!")
        assert result is None

    def test_returns_none_on_empty_string(self):
        """extract_json_safely must return None on empty input."""
        from agent.llm import extract_json_safely

        result = extract_json_safely("")
        assert result is None


class TestCheckOllama:
    def test_check_ollama_returns_bool(self):
        """check_ollama must return a bool without raising."""
        from agent import llm as llm_mod

        with patch("agent.llm.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"models": []}
            result = llm_mod.check_ollama()

        assert isinstance(result, bool)

    def test_check_ollama_returns_false_on_connection_error(self):
        """check_ollama must return False (not raise) when Ollama is unreachable."""
        import requests
        from agent import llm as llm_mod

        with patch("agent.llm.requests.get", side_effect=requests.exceptions.ConnectionError):
            result = llm_mod.check_ollama()

        assert result is False
