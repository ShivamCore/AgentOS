"""agent/executor.py: parsing untrusted step output and applying it through authorized tools only."""

from __future__ import annotations

import json
import os

import pytest

from agent import sandbox as sb
from agent.executor import execute_step, extract_json_payload
from agent.tools.registry import registry


@pytest.fixture()
def ws(tmp_path):
    d = tmp_path / "ws"
    d.mkdir()
    return str(d)


@pytest.fixture()
def no_docker(monkeypatch):
    def boom(*a, **k):
        raise sb.SandboxUnavailableError("no docker (test)")

    monkeypatch.setattr(sb.DockerSandbox, "__init__", boom)
    monkeypatch.delenv(sb.UNSAFE_ENV_FLAG, raising=False)


class TestExtract:
    def test_documented_format(self):
        p = extract_json_payload('{"files":[{"path":"a.py","code":"x = 1\\n"}],"command":"python a.py"}')
        assert p == {"files": [{"path": "a.py", "code": "x = 1\n", "action": "write"}],
                     "commands": ["python a.py"], "error": None}

    def test_prose_around_json(self):
        p = extract_json_payload('Sure! {"files":[{"path":"a.py","code":"x=[1]"}],"command":null} hope it helps')
        assert p["error"] is None and p["files"][0]["path"] == "a.py"

    @pytest.mark.parametrize("text,needle", [
        ("", "not a JSON object"),
        ("[1,2]", "not a JSON object"),
        ('{"files": []}', "no files or commands"),
        ('{"files": [{"path": "a.py"}]}', "INVALID OUTPUT"),
        ('{"files": [{"path": "a.py", "code": "x", "action": "delete"}]}', "UNSUPPORTED ACTION"),
        ('{"commands": "python a.py"}', "must be a list"),
        (json.dumps({"files": [{"path": f"f{i}.py", "code": ""} for i in range(11)]}), "INVALID OUTPUT"),
    ])
    def test_malformed(self, text, needle):
        p = extract_json_payload(text)
        assert needle in p["error"]
        assert p["files"] == [] and p["commands"] == []


class TestExecuteStep:
    def test_writes_file(self, ws):
        r = execute_step({"files": [{"path": "pkg/a.py", "code": "x = 1\n"}], "commands": []}, ws)
        assert r["success"] and r["files_written"] == ["pkg/a.py"]
        assert open(os.path.join(ws, "pkg/a.py")).read() == "x = 1\n"

    @pytest.mark.parametrize("path", ["../escape.py", "/etc/passwd", "a/../../x.py"])
    def test_path_escape_rejected(self, ws, path):
        r = execute_step({"files": [{"path": path, "code": "x"}], "commands": []}, ws)
        assert not r["success"]
        assert not os.path.exists(os.path.join(os.path.dirname(ws), "escape.py"))

    def test_symlink_escape_rejected(self, ws, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, os.path.join(ws, "link"))
        r = execute_step({"files": [{"path": "link/x.py", "code": "x = 1"}], "commands": []}, ws)
        assert not r["success"] and not (outside / "x.py").exists()

    def test_syntax_error_rejected_before_write(self, ws):
        r = execute_step({"files": [{"path": "a.py", "code": "def ("}], "commands": []}, ws)
        assert not r["success"] and "SyntaxError" in r["stderr"]
        assert not os.path.exists(os.path.join(ws, "a.py"))

    def test_unauthorized_tool(self, ws):
        r = execute_step({"files": [{"path": "a.py", "code": "x=1"}], "commands": []}, ws, allowed_tools=["terminal"])
        assert not r["success"] and "file_write" in r["stderr"]
        r = execute_step({"files": [], "commands": ["python -V"]}, ws, allowed_tools=["file_write"])
        assert not r["success"] and "terminal" in r["stderr"]

    def test_file_scope(self, ws):
        r = execute_step({"files": [{"path": "other/a.py", "code": "x=1"}], "commands": []}, ws, file_scope=["src"])
        assert not r["success"] and "file_scope" in r["stderr"]
        r = execute_step({"files": [{"path": "src/a.py", "code": "x=1"}], "commands": []}, ws, file_scope=["src/"])
        assert r["success"]

    def test_size_limit(self, ws, monkeypatch):
        monkeypatch.setattr("agent.tools.filesystem.MAX_FILE_BYTES", 10)
        r = execute_step({"files": [{"path": "a.txt", "code": "x" * 11}], "commands": []}, ws)
        assert not r["success"] and "limit" in r["stderr"]

    def test_command_fails_closed_and_rolls_back_files(self, ws, no_docker):
        with open(os.path.join(ws, "keep.py"), "w") as f:
            f.write("original = True\n")
        r = execute_step({"files": [{"path": "keep.py", "code": "changed = True\n"},
                                    {"path": "new.py", "code": "y = 2\n"}],
                          "commands": ["python keep.py"]}, ws)
        assert not r["success"] and "fail-closed" in r["stderr"]
        assert open(os.path.join(ws, "keep.py")).read() == "original = True\n"
        assert not os.path.exists(os.path.join(ws, "new.py"))
        assert r["files_written"] == []


class TestRegistry:
    def test_unknown_tool(self, ws):
        assert "unknown tool" in registry.execute_tool("git", {"command": "status"}, ws).error

    def test_unknown_argument(self, ws):
        res = registry.execute_tool("file_write", {"file_path": "a", "content": "x", "mode": "a"}, ws)
        assert not res.success and "invalid arguments" in res.error

    def test_wrong_type(self, ws):
        assert not registry.execute_tool("file_write", {"file_path": 1, "content": "x"}, ws).success

    def test_allowlist(self, ws):
        res = registry.execute_tool("file_read", {"file_path": "a"}, ws, allowed=["file_write"])
        assert "not authorized" in res.error

    def test_every_tool_documents_side_effects(self):
        for t in registry.describe():
            assert t["name"] and t["description"] and t["side_effects"]
