"""
Step action parsing and execution.

An LLM step response is untrusted. ``extract_json_payload`` parses it into a
strictly validated ``StepAction``; ``execute_step`` applies it through the tool
registry only:

* file writes  -> ``file_write`` tool (workspace-confined, size-capped), and only
  if ``file_write`` is authorized for the step and the path is inside the
  task's ``file_scope`` (when one is set);
* commands     -> ``terminal`` tool (Docker sandbox, fail-closed), only if
  ``terminal`` is authorized.

Nothing in this module runs a subprocess on the host. On failure, files written
during the attempt are restored to their previous content.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.llm import extract_json_safely
from agent.tools.filesystem import PathEscapeError, resolve_in_workspace
from agent.tools.registry import registry

MAX_FILES_PER_STEP = 10
MAX_COMMANDS_PER_STEP = 3


class FileOp(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str = Field(..., min_length=1, max_length=512)
    code: str
    action: Literal["write"] = "write"


class StepAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    files: list[FileOp] = Field(default_factory=list, max_length=MAX_FILES_PER_STEP)
    commands: list[str] = Field(default_factory=list, max_length=MAX_COMMANDS_PER_STEP)


def extract_json_payload(text: str) -> dict[str, Any]:
    """
    Parse raw model text into ``{"files": [...], "commands": [...], "error": str|None}``.
    Accepts the documented ``"command": str|null`` field as well as ``"commands": [...]``.
    """
    empty: dict[str, Any] = {"files": [], "commands": [], "error": None}
    data = extract_json_safely(text or "")
    if not isinstance(data, dict):
        return {**empty, "error": "INVALID OUTPUT FORMAT: response is not a JSON object"}
    raw = dict(data)
    commands = raw.get("commands")
    if commands is None:
        commands = []
    if not isinstance(commands, list):
        return {**empty, "error": "INVALID OUTPUT FORMAT: 'commands' must be a list"}
    single = raw.get("command")
    if isinstance(single, str) and single.strip():
        commands = [*commands, single.strip()]
    raw["commands"] = commands
    for f in raw.get("files") or []:
        if isinstance(f, dict) and f.get("action") not in (None, "write"):
            return {**empty, "error": f"UNSUPPORTED ACTION: file action {f.get('action')!r}; only 'write' is allowed"}
    try:
        action = StepAction.model_validate(raw)
    except ValidationError as exc:
        return {**empty, "error": f"INVALID OUTPUT FORMAT: {exc.errors(include_url=False)}"}
    if not action.files and not action.commands:
        return {**empty, "error": "INVALID OUTPUT FORMAT: no files or commands supplied"}
    return {"files": [f.model_dump() for f in action.files], "commands": action.commands, "error": None}


def _in_scope(rel_path: str, file_scope: list[str]) -> bool:
    if not file_scope:
        return True
    norm = os.path.normpath(rel_path)
    for scope in file_scope:
        s = os.path.normpath(scope.strip("/"))
        if s in ("", ".") or norm == s or norm.startswith(s + os.sep):
            return True
    return False


def execute_step(
    payload: dict[str, Any],
    workspace_dir: str,
    allowed_tools: Iterable[str] = ("file_write", "terminal"),
    file_scope: list[str] | None = None,
) -> dict[str, Any]:
    """Apply a parsed step action. Returns {success, stdout, stderr, files_written}."""
    allowed = set(allowed_tools)
    scope = file_scope or []
    report: dict[str, Any] = {"success": True, "stdout": "", "stderr": "", "files_written": []}
    out: list[str] = []
    files = payload.get("files", [])
    commands = payload.get("commands", [])

    def fail(msg: str) -> dict[str, Any]:
        report["success"] = False
        report["stderr"] = msg
        report["stdout"] = "\n".join(out)
        return report

    # Authorization and static checks before anything is written.
    if files and "file_write" not in allowed:
        return fail("tool 'file_write' is not authorized for this step")
    if commands and "terminal" not in allowed:
        return fail("tool 'terminal' is not authorized for this step")
    for f in files:
        path, code = f.get("path", ""), f.get("code", "")
        try:
            resolve_in_workspace(path, workspace_dir)
        except PathEscapeError as exc:
            return fail(str(exc))
        if not _in_scope(path, scope):
            return fail(f"path {path!r} is outside the task file_scope {scope}")
        if path.endswith(".py"):
            try:
                ast.parse(code, filename=path)
            except SyntaxError as exc:
                return fail(f"SyntaxError in generated {path} line {exc.lineno}: {exc.msg}")

    snapshots: dict[str, str | None] = {}
    for f in files:
        abs_path = resolve_in_workspace(f["path"], workspace_dir)
        if abs_path not in snapshots:
            try:
                with open(abs_path, encoding="utf-8") as fh:
                    snapshots[abs_path] = fh.read()
            except FileNotFoundError:
                snapshots[abs_path] = None
            except (OSError, UnicodeDecodeError):
                snapshots[abs_path] = None

    def rollback() -> None:
        for abs_path, original in snapshots.items():
            try:
                if original is None:
                    if os.path.exists(abs_path):
                        os.remove(abs_path)
                else:
                    with open(abs_path, "w", encoding="utf-8") as fh:
                        fh.write(original)
            except OSError:
                pass

    for f in files:
        res = registry.execute_tool(
            "file_write", {"file_path": f["path"], "content": f["code"]}, workspace_dir, allowed=allowed
        )
        if not res.success:
            rollback()
            return fail(res.error or "file_write failed")
        out.append(res.output)
        report["files_written"].append(f["path"])

    for cmd in commands:
        res = registry.execute_tool("terminal", {"command": cmd}, workspace_dir, allowed=allowed)
        out.append(f"$ {cmd}\n{res.output}".rstrip())
        if not res.success:
            rollback()
            report["files_written"] = []
            return fail(f"command failed: {cmd}\n{res.error or ''}".strip())

    report["stdout"] = "\n".join(out)
    return report
