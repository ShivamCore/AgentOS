import os
from pydantic import BaseModel, Field
from typing import Dict, Any
from .base import Tool


class FileReadInput(BaseModel):
    file_path: str = Field(..., description="Path to the file to read, relative to workspace.")


class FileReadTool(Tool):
    name = "file_read"
    description = "Reads the entire content of a file from the workspace into memory."
    input_schema = FileReadInput

    def execute(self, inputs: Dict[str, Any], workspace_dir: str, **kwargs) -> Dict[str, Any]:
        # Simple security constraint forcing path resolution strictly into workspace
        safe_path = os.path.realpath(os.path.join(workspace_dir, inputs["file_path"]))
        if not safe_path.startswith(os.path.realpath(workspace_dir)):
            return {"success": False, "error": f"Path Traversal Blocked: {inputs['file_path']}"}

        try:
            with open(safe_path, "r", encoding="utf-8") as f:
                return {"success": True, "content": f.read()}
        except Exception as e:
            return {"success": False, "error": str(e)}


class FileWriteInput(BaseModel):
    file_path: str = Field(..., description="Path to the file to write, relative to the workspace mapping.")
    content: str = Field(..., description="Text content to inject entirely into the specified file.")


class FileWriteTool(Tool):
    name = "file_write"
    description = "Writes arbitrary content safely to a local file inside the bounded workspace."
    input_schema = FileWriteInput

    def execute(self, inputs: Dict[str, Any], workspace_dir: str, **kwargs) -> Dict[str, Any]:
        safe_path = os.path.realpath(os.path.join(workspace_dir, inputs["file_path"]))
        if not safe_path.startswith(os.path.realpath(workspace_dir)):
            return {"success": False, "error": f"Path Traversal Blocked: {inputs['file_path']}"}

        try:
            os.makedirs(os.path.dirname(safe_path), exist_ok=True)
            with open(safe_path, "w", encoding="utf-8") as f:
                f.write(inputs["content"])
            return {"success": True, "message": f"Successfully committed content to {inputs['file_path']}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
