import os
import subprocess
import shlex
from typing import Optional, Set

try:
    from rich.console import Console
    console = Console()
except ImportError:
    class _FallbackConsole:  # type: ignore[no-redef]
        def print(self, *args, **kwargs): pass
    console = _FallbackConsole()  # type: ignore[assignment]

# Read resource limits from env vars (same defaults as backend/config.py)
_MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(1 * 1024 * 1024)))

PROTECTED_FILES = ["main.py", "config.py"]
ALLOWED_COMMAND_PREFIXES = [
    "python", "python3", "pip", "pip3", "npm", "node", "uvicorn", "pytest",
    "ls", "echo", "mkdir", "cat", "touch", "cp", "mv", "chmod", "cd",
    "bash", "sh",   # needed for `bash run.sh` and `bash -c "..."` patterns
]

# NOTE: No longer a module-level global — pass task_cache per call to avoid
# cross-task false cache hits when workers share the same process.

def _assert_safe_path(rel_path: str, workspace_dir: str) -> str:
    """
    Resolves rel_path against workspace_dir and raises ValueError if the
    resulting absolute path escapes the sandbox.
    Returns the safe absolute path.
    """
    full_path = os.path.realpath(os.path.join(workspace_dir, rel_path))
    sandbox = os.path.realpath(workspace_dir)
    if not full_path.startswith(sandbox + os.sep) and full_path != sandbox:
        raise ValueError(f"Path traversal blocked: '{rel_path}' resolves outside sandbox.")
    return full_path


def write_file(rel_path: str, code: str, workspace_dir: str, dry_run: bool = False) -> str:
    """
    Safely writes a file. 
    Checks diffs to prevent unaltered rewrites. 
    Protects critical system files.
    Returns a status message.
    """
    # Normalize path
    if os.path.isabs(rel_path):
        rel_path = rel_path.lstrip('/')
    
    file_name = os.path.basename(rel_path)
    if file_name in PROTECTED_FILES:
        return f"[SKIPPED] Cannot overwrite protected file: {rel_path}"

    try:
        full_path = _assert_safe_path(rel_path, workspace_dir)
    except ValueError as e:
        return f"[BLOCKED] {e}"

    # ── FILE SIZE CAP ───────────────────────────────────────────
    payload_bytes = len(code.encode("utf-8"))
    if payload_bytes > _MAX_FILE_BYTES:
        return (
            f"[BLOCKED] Generated file '{rel_path}' is {payload_bytes // 1024}KB, "
            f"exceeding the {_MAX_FILE_BYTES // 1024}KB limit."
        )
    
    # Check if content is identical
    if os.path.exists(full_path):
        try:
            with open(full_path, "r") as f:
                existing_code = f.read()
            if existing_code == code:
                return f"[SKIPPED] File '{rel_path}' is already up-to-date. No changes made."
        except Exception as e:
            return f"[ERROR] Failed to read existing file '{rel_path}': {e}"
            
    if dry_run:
        return f"[DRY RUN] Would write {len(code)} bytes to {rel_path}"
        
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(code)
        return f"[SUCCESS] Wrote file: {rel_path}"
    except Exception as e:
        raise Exception(f"Failed to write file {rel_path}: {e}")

def delete_file(rel_path: str, workspace_dir: str, dry_run: bool = False) -> str:
    if os.path.basename(rel_path) in PROTECTED_FILES:
        return f"[SKIPPED] Cannot delete protected file: {rel_path}"

    try:
        full_path = _assert_safe_path(rel_path, workspace_dir)
    except ValueError as e:
        return f"[BLOCKED] {e}"
    if not os.path.exists(full_path):
        return f"[SKIPPED] File does not exist to delete: {rel_path}"
        
    if dry_run:
        return f"[DRY RUN] Would delete file: {rel_path}"
        
    try:
        os.remove(full_path)
        return f"[SUCCESS] Deleted file: {rel_path}"
    except Exception as e:
        return f"[ERROR] Failed to delete file: {e}"

def rename_file(old_rel_path: str, new_rel_path: str, workspace_dir: str, dry_run: bool = False) -> str:
    if os.path.basename(old_rel_path) in PROTECTED_FILES or os.path.basename(new_rel_path) in PROTECTED_FILES:
        return f"[SKIPPED] Cannot mutate protected files: {old_rel_path} -> {new_rel_path}"
        
    old_full = os.path.join(workspace_dir, old_rel_path)
    new_full = os.path.join(workspace_dir, new_rel_path)
    
    if not os.path.exists(old_full):
        return f"[SKIPPED] Source file does not exist: {old_rel_path}"
        
    if dry_run:
        return f"[DRY RUN] Would rename: {old_rel_path} -> {new_rel_path}"
        
    try:
        os.makedirs(os.path.dirname(new_full), exist_ok=True)
        os.rename(old_full, new_full)
        return f"[SUCCESS] Renamed file: {old_rel_path} -> {new_rel_path}"
    except Exception as e:
        return f"[ERROR] Failed to rename file: {e}"

def run_command(cmd: str, workspace_dir: str, dry_run: bool = False,
                task_cache: Optional[Set[str]] = None) -> dict:
    """
    Executes a shell command whose first token MUST be in ALLOWED_COMMAND_PREFIXES.
    Uses a list invocation (no shell=True) to prevent argument-injection attacks.
    task_cache is a per-task set so different concurrent tasks never share cached results.
    """
    if task_cache is None:
        task_cache = set()  # fallback: isolated per call
    cmd_trimmed = cmd.strip()
    if not cmd_trimmed:
        return {"success": True, "stdout": "", "stderr": ""}

    try:
        parts = shlex.split(cmd_trimmed)
    except ValueError as e:
        return {"success": False, "stdout": "", "stderr": f"Invalid command syntax: {e}"}

    first_word = parts[0].lower()
    
    if first_word not in ALLOWED_COMMAND_PREFIXES:
        err_msg = f"Security Error: Command '{first_word}' is NOT in the allowlist. Allowed tools: {', '.join(ALLOWED_COMMAND_PREFIXES)}"
        console.print(f"[red]✗ {err_msg}[/red]")
        return {"success": False, "stdout": "", "stderr": err_msg}

    if cmd_trimmed in task_cache:
        console.print("[yellow][CACHED] Command already ran successfully this task.[/yellow]")
        return {"success": True, "stdout": f"[CACHED] {cmd_trimmed}", "stderr": ""}
        
    if dry_run:
        console.print("[yellow][DRY RUN] Skipping command execution.[/yellow]")
        return {"success": True, "stdout": f"[DRY RUN] Simulated execution of: {cmd}", "stderr": ""}
        
    # Dynamic timeout detection for network/install heavy commands
    timeout = 120
    if "install" in cmd.lower():
        timeout = 600
        console.print("[yellow]Network install detected. Increasing execution timeout to 10 minutes.[/yellow]")
        
    try:
        # Use list invocation — no shell=True — prevents argument injection
        process = subprocess.run(
            parts,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        stdout = process.stdout.strip()
        stderr = process.stderr.strip()

        if process.returncode != 0:
            err_msg = f"Command failed with exit code {process.returncode}."
            return {"success": False, "stdout": stdout, "stderr": f"{err_msg}\n{stderr}"}

        task_cache.add(cmd_trimmed)
        return {"success": True, "stdout": stdout, "stderr": stderr}

    except Exception as e:
        return {"success": False, "stdout": "", "stderr": f"Exception executing command: {e}"}

def validate_files(expected_rel_paths: list, workspace_dir: str) -> list:
    """
    Validates that the expected files were actually created.
    Returns a list of missing files.
    """
    missing = []
    for rel_path in expected_rel_paths:
        if os.path.isabs(rel_path):
            rel_path = rel_path.lstrip('/')
        full_path = os.path.join(workspace_dir, rel_path)
        if not os.path.exists(full_path):
            missing.append(rel_path)
    return missing
