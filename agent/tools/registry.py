from typing import Dict, List, Type, Any
from .base import Tool
from .filesystem import FileReadTool, FileWriteTool
from .system import TerminalTool, GitTool

# Explicit declarative mapping of all permissible tools globally.
_AVAILABLE_TOOLS: List[Type[Tool]] = [
    FileReadTool,
    FileWriteTool,
    TerminalTool,
    GitTool
]

class ToolRegistry:
    """Manages the instantiation and scoped routing of execution tools globally mapping schema validations actively."""
    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        for T in _AVAILABLE_TOOLS:
            instance = T()
            self._tools[instance.name] = instance

    def get_tool(self, name: str) -> Tool:
        return self._tools.get(name)

    def execute_tool(self, name: str, inputs: dict, workspace_dir: str) -> dict:
        """Executes a tool by name, unpacking Pydantic schemas validating payloads intrinsically ensuring type-safety."""
        tool = self.get_tool(name)
        if not tool:
            return {"success": False, "error": f"Tool '{name}' not found natively in ToolRegistry definitions."}
        
        try:
            # Crucial: This enforces LLM hallucination resistance mapping types securely.
            validated = tool.input_schema(**inputs)
            return tool.execute(validated.model_dump(), workspace_dir=workspace_dir)
        except ValueError as ve:
            return {"success": False, "error": f"Schema Validation Payload Error mapping '{name}': {ve}"}
        except Exception as e:
            return {"success": False, "error": f"Internal Driver execution failure running '{name}': {str(e)}"}

# Global Export instantiated instantly
registry = ToolRegistry()
