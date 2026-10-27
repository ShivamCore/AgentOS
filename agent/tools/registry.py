"""
Tool registry and authorization.

``execute_tool`` is the only path from agent output to a side effect. It checks,
in order: the tool exists, the caller is authorized to use it, and the inputs
match the tool's strict schema. Unknown tools and unknown arguments are rejected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .base import Tool, ToolResult
from .filesystem import FileReadTool, FileWriteTool
from .system import TerminalTool

if TYPE_CHECKING:
    from collections.abc import Iterable

_AVAILABLE_TOOLS: tuple[type[Tool], ...] = (FileReadTool, FileWriteTool, TerminalTool)


class ToolRegistry:
    def __init__(self, tools: Iterable[type[Tool]] = _AVAILABLE_TOOLS) -> None:
        self._tools: dict[str, Tool] = {}
        for cls in tools:
            inst = cls()
            self._tools[inst.name] = inst

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def describe(self) -> list[dict[str, str]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "side_effects": t.side_effects,
            }
            for t in self._tools.values()
        ]

    def execute_tool(
        self,
        name: str,
        inputs: dict[str, Any],
        workspace_dir: str,
        allowed: Iterable[str] | None = None,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"unknown tool '{name}'")
        if allowed is not None and name not in set(allowed):
            return ToolResult(
                success=False, error=f"tool '{name}' is not authorized for this step"
            )
        if not isinstance(inputs, dict):
            return ToolResult(success=False, error="tool inputs must be an object")
        try:
            validated = tool.input_schema.model_validate(inputs)
        except ValidationError as exc:
            return ToolResult(
                success=False,
                error=f"invalid arguments for '{name}': {exc.errors(include_url=False)}",
            )
        try:
            result = tool.execute(validated, workspace_dir)
        except Exception as exc:  # a tool bug must not crash the scheduler thread
            return ToolResult(
                success=False, error=f"tool '{name}' raised {type(exc).__name__}: {exc}"
            )
        return ToolResult.model_validate(result.model_dump())


registry = ToolRegistry()
