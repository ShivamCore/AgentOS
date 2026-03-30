import ast
import os
from rich.console import Console

console = Console()

def validate_syntax(workspace_dir: str, file_paths: list) -> dict:
    """
    Parses Python files using the AST module to detect syntax errors independently of execution.
    Returns: {"valid": bool, "syntax_errors": [...]}
    """
    errors = []
    
    for rel_path in file_paths:
        if not rel_path.endswith('.py'):
            continue
            
        full_path = os.path.join(workspace_dir, rel_path)
        if not os.path.exists(full_path):
            continue
            
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                code = f.read()
            ast.parse(code, filename=rel_path)
        except SyntaxError as e:
            err_msg = f"{rel_path}:{e.lineno}:{e.offset}: SyntaxError: {e.msg}"
            errors.append(err_msg)
        except Exception as e:
            errors.append(f"Validation exception on {rel_path}: {e}")
            
    if errors:
        return {"valid": False, "syntax_errors": errors}
    return {"valid": True, "syntax_errors": []}
