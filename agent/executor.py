from rich.console import Console
from agent.utils.tools import write_file, run_command, validate_files, delete_file, rename_file
from agent.utils.validator import validate_syntax
from agent.llm import generate_text, extract_json_safely
import json
from typing import List, Dict, Any, Optional
import os
import sys
import re
import ast
import subprocess

console = Console()

def auto_repair_json(broken_text: str) -> str:
    # Ask LLM to fix syntax errors
    console.print("[yellow]Attempting to auto-repair broken JSON output...[/yellow]")
    prompt = f"The following JSON is malformed. Please fix it and output ONLY valid JSON matching the schema (files array, command key). Do not include any other text.\n\n{broken_text}"
    repaired_payload = generate_text(prompt, system_prompt="You are a strict JSON fixer. Output ONLY raw JSON.", temperature=0.1)
    
    start_idx = repaired_payload.find('{')
    end_idx = repaired_payload.rfind('}')
    
    if start_idx != -1 and end_idx != -1:
        return repaired_payload[start_idx:end_idx+1]
        
    return "{}"

def _sanitize_parsed_output(payload: dict) -> dict:
    # Post-parse sanitizer: converts LLM hallucinations to valid Python patterns.
    # We drop pure delete actions because the LLM is not trusted to delete source code.
    
    clean_files = []
    for f in payload.get("files", []):
        # Drop delete actions — LLM should never delete existing files
        action = f.get("action", "write")
        if action == "delete":
            console.print(f"[yellow]⚠ Sanitizer: dropped delete action for {f.get('path', '?')}[/yellow]")
            continue
        path = f.get("path", "")
        if path.endswith(".sh"):
            # Convert shell script path to Python file
            f = dict(f)  # don't mutate original
            f["path"] = path[:-3] + ".py"
            code = f.get("code", "")
            # Strip shebang line if present
            code = re.sub(r'^#!/.*?\n', '', code, flags=re.MULTILINE)
            f["code"] = code
        fixed_files.append(f)
    result["files"] = fixed_files

    fixed_cmds = []
    for cmd in result.get("commands", []):
        # Drop chmod/bash commands that target shell scripts
        if re.search(r'\b(chmod|bash)\b.*\.sh', cmd):
            continue
        # Rewrite `bash xyz.sh` → `python xyz.py`
        cmd = re.sub(r'\bbash\s+(\S+)\.sh\b', r'python \1.py', cmd)
        fixed_cmds.append(cmd)
    
    payload["commands"] = fixed_cmds
    payload["files"] = clean_files
    return payload

def extract_json_payload(text: str, repair_attempts: int = 1) -> dict:
    # Pull valid JSON block from raw LLM text stream
    parsed_payload: Dict[str, Any] = {"files": [], "commands": [], "action": "patch_file", "error": None}
    
    try:
        data = extract_json_safely(text)
        parsed_payload["action"] = data.get("action", "patch_file")
        
        for file_obj in data.get("files", []):
            parsed_payload["files"].append(file_obj)
            
        command = data.get("command", None)
        if command and isinstance(command, str) and command.strip():
            parsed_payload["commands"].append(command.strip())

        parsed_payload = _sanitize_parsed_output(parsed_payload)
            
    except Exception as e:
        console.print(f"[red]Failed to extract JSON natively: {e}[/red]")
        if repair_attempts > 0:
            repaired_text = auto_repair_json(text)
            return extract_json_payload(repaired_text, repair_attempts=repair_attempts-1)
        
        parsed_payload["error"] = "INVALID OUTPUT FORMAT: JSONDecodeError exhausted retries"
        
    return parsed_payload


def execute_step(payload: dict, workspace_dir: str, dry_run: bool = False, fast_mode: bool = False) -> dict:
    execution_report = {"success": True, "stdout": "", "stderr": ""}
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []
    
    # Validation step to ensure the agent outputs the right files
    expected_paths = []
    
    files = payload.get("files", [])
    commands = payload.get("commands", [])
    action = payload.get("action", "patch_file")
    
    # Snapshot files before we mutate them
    original_states: Dict[str, Optional[str]] = {}
    if action != "fix_command" and not dry_run:
        for file_action in files:
            rel_path = file_action.get("path")
            if not rel_path: continue
            abs_path = os.path.join(workspace_dir, rel_path)
            if os.path.exists(abs_path):
                with open(abs_path, 'r', encoding='utf-8') as f:
                    original_states[abs_path] = f.read()
            else:
                original_states[abs_path] = None
                
    # Pre-flight: Check AST to prevent trivial syntax crashes
    if action != "fix_command":
        for file_action in files:
            code = file_action.get("code", "")
            rel_path = file_action.get("path", "")
            if rel_path.endswith(".py") and code.strip():
                try:
                    ast.parse(code)
                except SyntaxError as e:
                    err_msg = f"SyntaxError in generated code for {rel_path} at line {e.lineno}"
                    console.print(f"[bold yellow]🛡️ {err_msg}[/bold yellow]")
                    execution_report["success"] = False
                    execution_report["stderr"] = err_msg
                    return execution_report
    
    
    # 1. Write Files
    if action != "fix_command":
        for file_action in files:
            rel_path = file_action.get("path", "")
            code = file_action.get("code", "")
            f_act = file_action.get("action", "write")
            
            if not rel_path:
                continue
                
            expected_paths.append(rel_path)
            
            try:
                if f_act == "delete":
                    # In headless server mode (Celery worker), auto-reject destructive
                    # actions instead of hanging on console.input() with no TTY.
                    if not sys.stdin.isatty():
                        err_msg = f"Auto-rejected destructive action in headless mode: delete {rel_path}"
                        console.print(f"[bold red]✗ {err_msg}[/bold red]")
                        execution_report["success"] = False
                        execution_report["stderr"] = err_msg
                        execution_report["stdout"] = "\n".join(stdout_lines)
                        return execution_report
                    user_input = console.input(f"[bold yellow]⚠️ CORE SYSTEM GUARDRAIL: Are you sure you want to delete {rel_path}? (yes/no): [/bold yellow]")
                    if user_input.lower() not in ["y", "yes"]:
                        err_msg = f"User aborted destructive action: delete {rel_path}"
                        console.print(f"[bold red]✗ {err_msg}[/bold red]")
                        execution_report["success"] = False
                        execution_report["stderr"] = err_msg
                        execution_report["stdout"] = "\n".join(stdout_lines)
                        return execution_report
                    status_msg = delete_file(rel_path, workspace_dir, dry_run)
                elif f_act == "rename":
                    new_path = file_action.get("new_path", "")
                    if not sys.stdin.isatty():
                        err_msg = f"Auto-rejected destructive action in headless mode: rename {rel_path}"
                        console.print(f"[bold red]✗ {err_msg}[/bold red]")
                        execution_report["success"] = False
                        execution_report["stderr"] = err_msg
                        execution_report["stdout"] = "\n".join(stdout_lines)
                        return execution_report
                    user_input = console.input(f"[bold yellow]⚠️ CORE SYSTEM GUARDRAIL: Are you sure you want to rename {rel_path} to {new_path}? (yes/no): [/bold yellow]")
                    if user_input.lower() not in ["y", "yes"]:
                        err_msg = f"User aborted destructive action: rename {rel_path}"
                        console.print(f"[bold red]✗ {err_msg}[/bold red]")
                        execution_report["success"] = False
                        execution_report["stderr"] = err_msg
                        execution_report["stdout"] = "\n".join(stdout_lines)
                        return execution_report
                    status_msg = rename_file(rel_path, new_path, workspace_dir, dry_run)
                else:
                    # Default: write action — use ToolRegistry for sandboxed file writing
                    from agent.tools.registry import registry
                    res = registry.execute_tool("file_write", {"file_path": rel_path, "content": code}, workspace_dir)
                    if not res.get("success"):
                        raise Exception(res.get("error", "Failed to write file via ToolRegistry"))
                    status_msg = res.get("message", f"Written: {rel_path}")

                console.print(f"[green]✓ {status_msg}[/green]")
                stdout_lines.append(status_msg)
            except Exception as e:
                err_msg = str(e)
                console.print(f"[red]✗ {err_msg}[/red]")
                execution_report["success"] = False
                execution_report["stderr"] = err_msg
                execution_report["stdout"] = "\n".join(stdout_lines)
                return execution_report
    else:
        console.print(f"[yellow]⚡ Action is '{action}', bypassing file modifications.[/yellow]")
            
    # 2. Run Commands
    # ── Command sanitizer: skip invalid LLM-generated commands ────────────────
    SKIP_PATTERNS = [
        "json.tool",        # LLM often emits `python -m json.tool some.py` on .py files
        "-m json",          # same pattern variant
        "json_tool",
    ]
    for cmd in commands:
        # Skip commands containing known-bad patterns on non-JSON files
        should_skip = any(pat in cmd for pat in SKIP_PATTERNS)
        if should_skip:
            # Only allow json.tool if the target file ends with .json
            file_arg = re.findall(r'\S+\.\w+', cmd)
            if not file_arg or not all(f.endswith('.json') for f in file_arg):
                console.print(f"[yellow]⚠ Skipping invalid command (json.tool on non-JSON target): {cmd}[/yellow]")
                continue  # skip, don't fail

        # ── Rewrite bare `pytest` → `python -m pytest` (bare pytest may not be in PATH)
        if re.match(r'^pytest\b', cmd.strip()):
            cmd = "python -m " + cmd.strip()
            console.print(f"[yellow]⚠ Rewrote bare pytest → {cmd}[/yellow]")

        # Rewrite `python3` → `python` (python3 may not be a standalone binary)
        if re.match(r'^python3\b', cmd.strip()):
            cmd = "python" + cmd.strip()[7:]
            console.print(f"[yellow]⚠ Rewrote python3 → {cmd}[/yellow]")

        console.print(f"[blue]▶ Running command: {cmd}[/blue]")
        
        from agent.tools.registry import registry
        cmd_result = registry.execute_tool("terminal", {"command": cmd}, workspace_dir)
        
        if not cmd_execution_report["success"]:
            console.print(f"[red]✗ Command failed: {cmd_result['stderr']}[/red]")
            execution_report["success"] = False
            execution_report["stderr"] = cmd_execution_report["stderr"]
            stdout_lines.append(f"Attempted: {cmd}\nOutput:\n{cmd_result['stdout']}")
            execution_report["stdout"] = "\n".join(stdout_lines)
            return execution_report
        else:
            console.print(f"[green]✓ Command execution completed.[/green]")
            stdout_lines.append(f"Command '{cmd}' executed.\nOutput:\n{cmd_result['stdout']}")
            if cmd_execution_report["stderr"]:
                stderr_lines.append(f"Warnings:\n{cmd_result['stderr']}")
                
    # 3. Post Execution Validation
    if not dry_run:
        py_files = [f.get("path") for f in files if f.get("path", "").endswith(".py")]
        if py_files:
            val_result = validate_syntax(workspace_dir, py_files)
            if not val_result["valid"]:
                err_msg = "AST Validation Failed:\n" + "\n".join(val_result["syntax_errors"])
                console.print(f"[bold red]✗ {err_msg}[/bold red]")
                execution_report["success"] = False
                execution_report["stderr"] = err_msg
                execution_report["stdout"] = "\n".join(stdout_lines)
                return execution_report
                
        # Semantic test execution step
        if fast_mode:
            console.print("[bold cyan]⚡ FAST_MODE Enabled: Bypassing deep AST Semantic Pytest validation.[/bold cyan]")
        else:
            pass
            for py_file in py_files:
                abs_path = os.path.join(workspace_dir, py_file)
                if os.path.exists(abs_path):
                    with open(abs_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    try:
                        tree = ast.parse(content)
                    except:
                        continue
                        
                    # 4.5 TEST TRUST VALIDATION: Trap trivial asserts
                    trivial_assert_found = False
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Assert):
                            if isinstance(node.test, ast.Constant) and getattr(node.test, "value", None) is True:
                                trivial_assert_found = True
                            elif isinstance(node.test, ast.Compare):
                                compare_node = node.test  # type: ast.Compare
                                if len(compare_node.comparators) == 1:
                                    left_val = compare_node.left
                                    right_val = compare_node.comparators[0]
                                    if isinstance(left_val, ast.Constant) and isinstance(right_val, ast.Constant):
                                        if getattr(left_val, "value", object()) == getattr(right_val, "value", object()):
                                            trivial_assert_found = True
                    if trivial_assert_found:
                        err_msg = f"Semantic Trust Error: Trivial or meaningless assert (e.g. `assert True` or `1==1`) detected in {py_file}. Must generate real logic tests."
                        console.print(f"[bold red]✗ {err_msg}[/bold red]")
                        execution_report["success"] = False
                        execution_report["stderr"] = err_msg
                        execution_report["stdout"] = "\n".join(stdout_lines)
                        break
                        
                    has_tests = any(isinstance(node, ast.Assert) for node in ast.walk(tree)) or \
                                any(isinstance(node, ast.FunctionDef) and node.name.startswith("test_") for node in ast.walk(tree))
                                
                    if has_tests:
                        if py_file == "main.py" and "FastAPI(" in content:
                            continue
                            
                        console.print(f"[bold blue]🩺 PyTest Integration: AST Test hooks detected in {py_file}, natively testing...[/bold blue]")
                        try:
                            res = subprocess.run(["pytest", py_file, "-v", "--tb=short"], cwd=workspace_dir, capture_output=True, text=True, timeout=10)
                            if res.returncode == 5:
                                res = subprocess.run(["python", py_file], cwd=workspace_dir, capture_output=True, text=True, timeout=10)
                                if res.returncode != 0:
                                    err_msg = f"Semantic AST Assertion Error in {py_file}:\n{res.stderr}\n{res.stdout}"
                                    console.print(f"[bold red]✗ {err_msg}[/bold red]")
                                    execution_report["success"] = False
                                    execution_report["stderr"] = err_msg
                                    execution_report["stdout"] = "\n".join(stdout_lines)
                                    break
                                else:
                                    console.print(f"[bold green]✓ Inline native assertions passed in {py_file}[/bold green]")
                                    stdout_lines.append(f"Semantic Validation Passed: {py_file}")
                            elif res.returncode != 0:
                                err_msg = f"Pytest Validation Error in {py_file}:\n{res.stderr}\n{res.stdout}"
                                console.print(f"[bold red]✗ {err_msg}[/bold red]")
                                execution_report["success"] = False
                                execution_report["stderr"] = err_msg
                                execution_report["stdout"] = "\n".join(stdout_lines)
                                break
                            else:
                                console.print(f"[bold green]✓ Pytest standard suites verified efficiently in {py_file}[/bold green]")
                                stdout_lines.append(f"Pytest Validation Passed: {py_file}")
                        except subprocess.TimeoutExpired:
                            err_msg = f"Pytest Execution Timeout: {py_file} strictly aborted after 10s execution limit."
                            console.print(f"[bold red]✗ {err_msg}[/bold red]")
                            execution_report["success"] = False
                            execution_report["stderr"] = err_msg
                            execution_report["stdout"] = "\n".join(stdout_lines)
                            break
                        
        if expected_paths:
            missing = validate_files(expected_paths, workspace_dir)
            if missing:
                err_msg = f"Validation Failed: Expected files were missing: {missing}"
                console.print(f"[red]✗ {err_msg}[/red]")
                execution_report["success"] = False
                execution_report["stderr"] = err_msg
            
    # Rollback System: Restore on failure & Free occupied ports
    if not execution_report["success"] and not dry_run and action != "fix_command":
        console.print("[bold yellow]↺ ROLLBACK: Cleaning potentially hung subprocesses on web ports...[/bold yellow]")
        pass
        for port in [8000, 3000, 5000, 8080]:
            try:
                pids = subprocess.check_output(["lsof", "-ti", f":{port}"]).decode().strip().split('\n')
                for pid in pids:
                    if pid.strip():
                        subprocess.run(["kill", "-9", pid.strip()])
            except Exception:
                pass
            
        console.print("[bold yellow]↺ ROLLBACK: Memory snapshots restoring files to pre-execution state...[/bold yellow]")
        for abs_path, orig_content in original_states.items():
            if orig_content is None:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            else:
                with open(abs_path, 'w', encoding='utf-8') as f:
                    f.write(orig_content)
                    
    execution_report["stdout"] = "\n".join(stdout_lines)
    execution_report["stderr"] = "\n".join(stderr_lines)
    return execution_report
