from __future__ import annotations

import os

from typing import ClassVar

from pydantic import Field

from .base import Tool, ToolInput, ToolResult

MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(1024 * 1024)))


class PathEscapeError(ValueError):
    pass


def resolve_in_workspace(rel_path: str, workspace_dir: str) -> str:
    """Resolve rel_path inside workspace_dir, rejecting absolute paths, traversal and symlink escapes."""
    if not rel_path or os.path.isabs(rel_path) or "\x00" in rel_path:
        raise PathEscapeError(f"path must be relative and non-empty: {rel_path!r}")
    root = os.path.realpath(workspace_dir)
    full = os.path.realpath(os.path.join(root, rel_path))
    if os.path.commonpath([root, full]) != root or full == root:
        raise PathEscapeError(f"path escapes workspace: {rel_path!r}")
    return full


class FileReadInput(ToolInput):
    file_path: str = Field(..., min_length=1, max_length=512)


class FileReadTool(Tool):
    name: ClassVar[str] = "file_read"
    description: ClassVar[str] = "Read a UTF-8 text file from the task workspace."
    side_effects: ClassVar[str] = "none"
    input_schema = FileReadInput

    def execute(self, inputs: FileReadInput, workspace_dir: str) -> ToolResult:  # type: ignore[override]
        try:
            path = resolve_in_workspace(inputs.file_path, workspace_dir)
        except PathEscapeError as exc:
            return ToolResult(success=False, error=str(exc))
        try:
            if os.path.getsize(path) > MAX_FILE_BYTES:
                return ToolResult(
                    success=False, error=f"file exceeds {MAX_FILE_BYTES} bytes"
                )
            with open(path, encoding="utf-8") as f:
                return ToolResult(success=True, output=f.read())
        except OSError as exc:
            return ToolResult(success=False, error=f"{type(exc).__name__}: {exc}")


class FileWriteInput(ToolInput):
    file_path: str = Field(..., min_length=1, max_length=512)
    content: str


class FileWriteTool(Tool):
    name: ClassVar[str] = "file_write"
    description: ClassVar[str] = (
        "Create or overwrite a UTF-8 text file inside the task workspace."
    )
    side_effects: ClassVar[str] = "writes one file under the task workspace"
    input_schema = FileWriteInput

    def execute(self, inputs: FileWriteInput, workspace_dir: str) -> ToolResult:  # type: ignore[override]
        size = len(inputs.content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            return ToolResult(
                success=False,
                error=f"content is {size} bytes; limit is {MAX_FILE_BYTES}",
            )
        try:
            path = resolve_in_workspace(inputs.file_path, workspace_dir)
        except PathEscapeError as exc:
            return ToolResult(success=False, error=str(exc))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".agentos-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(inputs.content)
            os.replace(tmp, path)
            return ToolResult(
                success=True,
                output=f"wrote {inputs.file_path} ({size} bytes)",
                data={"path": inputs.file_path, "bytes": size},
            )
        except OSError as exc:
            return ToolResult(success=False, error=f"{type(exc).__name__}: {exc}")
