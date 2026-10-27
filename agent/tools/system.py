from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from ..sandbox import CommandRejectedError, SandboxUnavailableError, get_sandbox, parse_command
from .base import Tool, ToolInput, ToolResult


class TerminalInput(ToolInput):
    # Only the command is accepted. Timeouts, memory, network etc. are fixed by
    # server configuration (SandboxPolicy) and cannot be passed by an agent.
    command: str = Field(..., min_length=1, max_length=2000)


class TerminalTool(Tool):
    name: ClassVar[str] = "terminal"
    description: ClassVar[str] = "Run one allow-listed command (no shell) inside the Docker sandbox."
    side_effects: ClassVar[str] = "may modify files under the task workspace; no network"
    input_schema = TerminalInput

    def execute(self, inputs: TerminalInput, workspace_dir: str) -> ToolResult:  # type: ignore[override]
        try:
            argv = parse_command(inputs.command)
            sandbox = get_sandbox(workspace_dir)
        except (CommandRejectedError, SandboxUnavailableError) as exc:
            return ToolResult(success=False, error=str(exc), data={"rejected": True})
        res = sandbox.run(argv)
        return ToolResult(
            success=res.success,
            output=res.stdout,
            error=None if res.success else (res.stderr or f"exit code {res.exit_code}"),
            data=res.to_dict(),
        )
