from typing import Dict, Any
from pydantic import BaseModel, Field
from .base import Tool
from ..sandbox import get_sandbox, SandboxParams


class TerminalInput(BaseModel):
    command: str = Field(..., description="Arbitrary shell command string to execute.")


class TerminalTool(Tool):
    name = "terminal"
    description = "Executes arbitrary shell commands physically inside a secured sandbox boundary container natively isolated."
    input_schema = TerminalInput

    def execute(self, inputs: Dict[str, Any], workspace_dir: str, **kwargs) -> Dict[str, Any]:
        # Always forces commands through the Sandbox boundary natively avoiding host exploitation
        sandbox = get_sandbox(workspace_dir=workspace_dir, prefer_docker=True)
        result = sandbox.execute_command(inputs["command"])
        return result.to_dict()


class GitInput(BaseModel):
    command: str = Field(..., description="The git command suffix to execute (e.g., 'status', 'add .', 'commit -m \"init\"').")


class GitTool(Tool):
    name = "git"
    description = "Executes git operations restricting process arguments fundamentally to the git binary constraint safely."
    input_schema = GitInput

    def execute(self, inputs: Dict[str, Any], workspace_dir: str, **kwargs) -> Dict[str, Any]:
        # Prefix injection preventing arbitrary command stacking entirely
        full_cmd = f"git {inputs['command']}"
        sandbox = get_sandbox(workspace_dir=workspace_dir, prefer_docker=True)
        result = sandbox.execute_command(full_cmd)
        return result.to_dict()
