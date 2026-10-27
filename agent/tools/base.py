from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


class ToolInput(BaseModel):
    """Base for every tool input schema: unknown arguments are rejected."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ToolResult(BaseModel):
    """Validated output contract shared by every tool."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    output: str = ""
    error: str | None = None
    data: dict[str, Any] = {}


class Tool(ABC):
    """
    A capability an agent may invoke.

    name         stable identifier referenced by plans and agent manifests
    side_effects human-readable description of what the tool can change
    """

    name: ClassVar[str]
    description: ClassVar[str]
    side_effects: ClassVar[str]
    input_schema: ClassVar[type[ToolInput]]

    @abstractmethod
    def execute(self, inputs: ToolInput, workspace_dir: str) -> ToolResult:
        """Run with already-validated inputs."""
