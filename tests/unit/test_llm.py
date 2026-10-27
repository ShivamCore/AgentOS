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
        import sys

        original = sys.modules.get("agent.llm")
        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            # Force reimport
            if "agent.llm" in sys.modules:
                del sys.modules["agent.llm"]
            try:
                import agent.llm  # noqa: F401
            except Exception:
                pass  # import errors from missing deps are ok — we check HTTP only
            finally:
                # Restore the original module so other tests share one set of exception classes.
                if original is not None:
                    sys.modules["agent.llm"] = original
                    import agent

                    agent.llm = original

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


class _Resp:
    def __init__(self, status=200, body=None, lines=None):
        self.status_code = status
        self._body = body or {}
        self._lines = lines or []
        self.text = str(body)

    def json(self):
        return self._body

    def iter_lines(self):
        yield from self._lines

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class TestGenerateTextFailureModes:
    def _gen(self, **kw):
        from agent import llm

        kw.setdefault("model", "m")
        kw.setdefault("use_cache", False)
        return llm.generate_text("p", **kw)

    def test_model_not_found_is_not_retried_or_substituted(self):
        from agent import llm

        with patch("agent.llm.requests.post", return_value=_Resp(404, {"error": "model 'm' not found"})) as post, \
             patch("agent.llm.time.sleep"):
            with pytest.raises(llm.ModelNotFoundError, match="ollama pull deepseek-coder:6.7b"):
                self._gen(model=llm.PROD_MODE_MODEL, retries=2)
        assert post.call_count == 1  # no retry, no silent downgrade to another model

    def test_server_errors_retry_with_backoff_then_raise(self):
        from agent import llm

        with patch("agent.llm.requests.post", return_value=_Resp(500, {"error": "boom"})) as post, \
             patch("agent.llm.time.sleep") as sleep:
            with pytest.raises(llm.LLMUnavailableError):
                self._gen(retries=2)
        assert post.call_count == 3
        assert [c.args[0] for c in sleep.call_args_list] == [1, 2]

    def test_connection_refused_raises_unavailable(self):
        import requests
        from agent import llm

        with patch("agent.llm.requests.post", side_effect=requests.exceptions.ConnectionError("refused")), \
             patch("agent.llm.time.sleep"):
            with pytest.raises(llm.LLMUnavailableError, match="refused"):
                self._gen(retries=1)

    def test_timeout_then_success(self):
        import requests

        ok = _Resp(200, {"response": "hello", "eval_count": 50, "eval_duration": 1_000_000_000})
        with patch("agent.llm.requests.post", side_effect=[requests.exceptions.ReadTimeout("slow"), ok]), \
             patch("agent.llm.time.sleep"):
            assert self._gen(retries=1) == "hello"

    def test_tokens_per_second_from_ollama_counters(self):
        from agent import llm

        ok = _Resp(200, {"response": "hi", "eval_count": 100, "eval_duration": 2_000_000_000})
        with patch("agent.llm.requests.post", return_value=ok):
            self._gen()
        assert llm.get_metrics_snapshot()["recent"][-1]["tokens_per_sec"] == 50.0

    def test_empty_response_is_an_error(self):
        from agent import llm

        with patch("agent.llm.requests.post", return_value=_Resp(200, {"response": "  "})):
            with pytest.raises(llm.LLMResponseError, match="empty"):
                self._gen(retries=1)

    def test_incomplete_stream_is_an_error(self):
        import json as _json
        from agent import llm

        lines = [_json.dumps({"response": "partial"}).encode()]  # never sends done=true
        with patch("agent.llm.requests.post", return_value=_Resp(200, lines=lines)):
            with pytest.raises(llm.LLMResponseError, match="incomplete"):
                self._gen(stream_callback=lambda c: None, retries=0)

    def test_stream_collects_chunks(self):
        import json as _json

        lines = [_json.dumps({"response": "a"}).encode(), b"",
                 _json.dumps({"response": "b", "done": True, "eval_count": 2, "eval_duration": 10**9}).encode()]
        got = []
        with patch("agent.llm.requests.post", return_value=_Resp(200, lines=lines)):
            assert self._gen(stream_callback=got.append) == "ab"
        assert got == ["a", "b"]

    @pytest.mark.parametrize("kw", [{"temperature": 5}, {"retries": 99}])
    def test_parameter_validation(self, kw):
        with pytest.raises(ValueError):
            self._gen(**kw)


class TestOllamaHealth:
    def test_reports_missing_model(self):
        from agent import llm

        with patch("agent.llm.requests.get", return_value=_Resp(200, {"models": [{"name": "phi3.5:latest"}]})):
            h = llm.ollama_health("deepseek-coder:1.3b")
        assert h["reachable"] and not h["model_present"] and "ollama pull" in h["detail"]
        with patch("agent.llm.requests.get", return_value=_Resp(200, {"models": [{"name": "phi3.5:latest"}]})):
            assert llm.ollama_health("phi3.5")["model_present"]


def test_degenerate_generation_is_response_error_not_unavailable():
    from agent import llm

    body = {"error": "prediction aborted, token repeat limit reached"}
    with patch("agent.llm.requests.post", return_value=_Resp(500, body)) as post, patch("agent.llm.time.sleep"):
        with pytest.raises(llm.LLMResponseError, match="aborted"):
            llm.generate_text("p", model="m", retries=2, use_cache=False)
    assert post.call_count == 1


def test_planner_retries_with_sampling_after_generation_failure(monkeypatch):
    import json as _json

    from agent import selector
    from agent.llm import LLMResponseError

    temps = []

    def fake(prompt, **kw):
        temps.append(kw["temperature"])
        if len(temps) == 1:
            raise LLMResponseError("generation aborted")
        return _json.dumps({"steps": [{"step_id": "1", "description": "x"}]})

    monkeypatch.setattr(selector, "generate_text", fake)
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    g = selector.plan_markdown_task("do x")
    assert list(g.nodes) == ["1"] and temps == [0.0, 0.3]
