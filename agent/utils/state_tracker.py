import os
import json
import ast

from typing import Dict, Any, List

class StateTracker:
    def __init__(self, workspace_dir: str) -> None:
        self.workspace_dir = workspace_dir
        self.map_file = os.path.join(workspace_dir, "workspace_map.json")
        self.state: Dict[str, Any] = {
            "files": {},
            "dependencies": [],
            "architecture": "Not analyzed yet"
        }
        self.load()

    def load(self):
        if os.path.exists(self.map_file):
            try:
                with open(self.map_file, 'r') as f:
                    self.state = json.load(f)
            except:
                pass

    def save(self):
        with open(self.map_file, 'w') as f:
            json.dump(self.state, f, indent=2)

    def scan_workspace(self):
        """
        Scans all files in the workspace and extracts function/class structures dynamically.
        """
        self.state["files"] = {}
        for root, _, files in os.walk(self.workspace_dir):
            for file in files:
                if file == "workspace_map.json":
                    continue
                rel_path = os.path.relpath(os.path.join(root, file), self.workspace_dir)
                file_info: Dict[str, Any] = {"type": "file"}
                
                if file.endswith('.py'):
                    try:
                        with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                            node = ast.parse(f.read())
                        functions = [n.name for n in ast.walk(node) if isinstance(n, ast.FunctionDef)]
                        classes = [n.name for n in ast.walk(node) if isinstance(n, ast.ClassDef)]
                        imports = [n.names[0].name for n in ast.walk(node) if isinstance(n, ast.Import)]
                        imports_from = [n.module for n in ast.walk(node) if isinstance(n, ast.ImportFrom) and n.module]
                        file_info["functions"] = functions
                        file_info["classes"] = classes
                        file_info["imports"] = imports + imports_from
                    except:
                        file_info["status"] = "Syntax Error"
                        
                self.state["files"][rel_path] = file_info
                
        self.save()

    def get_map_json(self) -> str:
        """Returns the compressed workspace state representation for precise LLM prompting."""
        self.scan_workspace()
        return json.dumps(self.state, indent=2)
