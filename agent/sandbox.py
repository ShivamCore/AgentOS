import os
import subprocess
import shlex
import tempfile
import time
from typing import Dict, Any, Optional

try:
    import docker
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False


class SandboxParams:
    """Resource constraints for execution environments."""
    def __init__(self, 
                 timeout_sec: int = 120, 
                 max_memory_mb: int = 512, 
                 cpus: float = 1.0, 
                 network_disabled: bool = True):
        self.timeout_sec = timeout_sec
        self.max_memory_mb = max_memory_mb
        self.cpus = cpus
        self.network_disabled = network_disabled


class SandboxResult:
    """Structured output for any tool executed inside the sandbox."""
    def __init__(self, success: bool, stdout: str, stderr: str, exit_code: int, duration_sec: float):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.duration_sec = duration_sec

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_sec": round(self.duration_sec, 2)
        }


class ExecutionSandbox:
    """Base interface for an isolated execution environment."""
    def __init__(self, workspace_dir: str, params: SandboxParams = None):
        self.workspace_dir = os.path.realpath(workspace_dir)
        self.params = params or SandboxParams()

    def execute_command(self, cmd: str) -> SandboxResult:
        raise NotImplementedError


class SubprocessSandbox(ExecutionSandbox):
    """
    Fallback execution environment using native python subprocess.
    Relies on standard OS process limits.
    """
    def execute_command(self, cmd: str) -> SandboxResult:
        cmd_trimmed = cmd.strip()
        if not cmd_trimmed:
            return SandboxResult(True, "", "", 0, 0.0)

        try:
            parts = shlex.split(cmd_trimmed)
        except ValueError as e:
            return SandboxResult(False, "", f"Invalid command syntax: {e}", 1, 0.0)

        start_time = time.time()
        try:
            # We strictly enforce no shell=True injection vulnerabilities
            process = subprocess.run(
                parts,
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                timeout=self.params.timeout_sec
            )
            duration = time.time() - start_time
            success = process.returncode == 0
            
            return SandboxResult(
                success=success,
                stdout=process.stdout.strip(),
                stderr=process.stderr.strip(),
                exit_code=process.returncode,
                duration_sec=duration
            )
            
        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            # Standardizing timeout behaviors
            stdout = e.stdout.decode('utf-8') if e.stdout else ""
            stderr = e.stderr.decode('utf-8') if e.stderr else ""
            return SandboxResult(
                success=False, 
                stdout=stdout, 
                stderr=f"Execution Timed Out after {self.params.timeout_sec}s.\n{stderr}", 
                exit_code=124, 
                duration_sec=duration
            )
        except Exception as e:
            duration = time.time() - start_time
            return SandboxResult(False, "", f"Exception executing command: {str(e)}", 1, duration)


class DockerSandbox(ExecutionSandbox):
    """
    Containerized Sandbox using Docker SDK.
    Restricts CPU, RAM, Network, and mounts exactly the workspace dir.
    """
    def __init__(self, workspace_dir: str, params: SandboxParams = None):
        super().__init__(workspace_dir, params)
        if not DOCKER_AVAILABLE:
            raise RuntimeError("Docker SDK python payload not initialized. 'pip install docker' is required.")
        self.client = docker.from_env()
        # Default agent container image. Must have python and common OS utils natively.
        self.image = "python:3.11-slim"
        self._ensure_image()

    def _ensure_image(self):
        try:
            self.client.images.get(self.image)
        except docker.errors.ImageNotFound:
            # Note: This network call bypasses offline configurations initially but prevents missing images natively.
            self.client.images.pull(self.image)

    def execute_command(self, cmd: str) -> SandboxResult:
        start_time = time.time()
        
        # Hard limits imposed via Docker runtime abstraction mapping
        mem_limit = f"{self.params.max_memory_mb}m"
        network_mode = "none" if self.params.network_disabled else "bridge"
        # We specify the exact volume mount constraint mapping natively to workspace
        volumes = {
            self.workspace_dir: {'bind': '/workspace', 'mode': 'rw'}
        }
        
        try:
            # Use 'sh -c' wrapped command inside the container natively
            # The container execution is spun and killed instantly on termination
            container = self.client.containers.run(
                self.image,
                command=["sh", "-c", cmd],
                working_dir="/workspace",
                volumes=volumes,
                network_mode=network_mode,
                mem_limit=mem_limit,
                nano_cpus=int(self.params.cpus * 1e9),
                detach=True,
                auto_remove=False # We fetch logs first before manually cleaning
            )
            
            try:
                # Wait for container termination explicitly mapped to requested Timeout bounds
                result = container.wait(timeout=self.params.timeout_sec)
                exit_code = result.get('StatusCode', 1)
                
                # Fetch output streams mapping native standard out streams cleanly via SDK
                logs = container.logs(stdout=True, stderr=True, stream=False).decode('utf-8', errors='replace')
                
                # Simple heuristic to split stdout/stderr from raw SDK outputs
                return SandboxResult(
                    success=(exit_code == 0),
                    stdout=logs.strip() if exit_code == 0 else "",
                    stderr=logs.strip() if exit_code != 0 else "",
                    exit_code=exit_code,
                    duration_sec=time.time() - start_time
                )
                
            except Exception as wait_exc:
                duration = time.time() - start_time
                if "Read timed out" in str(wait_exc) or "timeout" in str(wait_exc).lower():
                    container.kill()
                    return SandboxResult(False, "", f"Execution Timed Out after {self.params.timeout_sec}s and container was killed.", 124, duration)
                raise wait_exc
                
            finally:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

        except Exception as e:
            return SandboxResult(False, "", f"Sandbox Initialization Error: {str(e)}", 1, time.time() - start_time)


def get_sandbox(workspace_dir: str, prefer_docker: bool = True, params: SandboxParams = None) -> ExecutionSandbox:
    """
    Factory resolving the most restrictive sandbox backend available natively.
    Degrades to Subprocess gracefully without causing application downtime.
    """
    if prefer_docker and DOCKER_AVAILABLE:
        try:
            return DockerSandbox(workspace_dir, params)
        except Exception:
            pass # Fall back to subprocess natively
    return SubprocessSandbox(workspace_dir, params)
