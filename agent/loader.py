# CHANGED: Added required imports for atomic I/O, timestamping, threaded locks, and strict exceptions.
import os
import re
import tempfile
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, ValidationError

# CHANGED: Introduced specific Error classes for the CSP and schema loaders per specifications.
class SecurityError(Exception):
    pass

class LoaderError(Exception):
    pass

# CHANGED: Upgraded AgentManifest with schema_version, max_input_tokens, and max_output_tokens per Item 6 and Item 8.
class AgentManifest(BaseModel):
    name: str = ""
    role: str = ""
    model: str = "Auto"
    tools: List[str] = Field(default_factory=list)
    system_prompt: str = ""
    constraints: List[str] = Field(default_factory=list)
    memory_persistent: bool = False
    memory_scope: str = "task"
    schema_version: int = 1
    max_input_tokens: int = 8000
    max_output_tokens: int = 4000

# CHANGED: Auto-migrate utility to upgrade any v0 agents missing version flags.
def migrate_manifest(data: dict) -> dict:
    version = int(data.get("schema_version", 1))
    data["schema_version"] = 1
    if "max_input_tokens" not in data: data["max_input_tokens"] = 8000
    if "max_output_tokens" not in data: data["max_output_tokens"] = 4000
    return data

class AgentLoader:
    def __init__(self, agents_dir: str):
        self.agents_dir = agents_dir
        self.agents: Dict[str, AgentManifest] = {}
        # CHANGED: Added agent_status and threaded locks for per-agent states
        self.agent_status: Dict[str, dict] = {}
        self.locks: Dict[str, threading.Lock] = {}
        self.global_lock = threading.Lock()
        self.load_all()

    def get_lock(self, name: str):
        with self.global_lock:
            if name not in self.locks:
                self.locks[name] = threading.Lock()
            return self.locks[name]
            
    # CHANGED: Integrated Content Security Policy scanner parsing the file string directly.
    def check_security(self, content: str):
        if re.search(r'https?://', content):
            raise SecurityError("External URLs are strictly forbidden in agent definitions.")
        
        forbidden_code = ['subprocess.', 'os.', 'eval(', 'exec(']
        for bad in forbidden_code:
            if bad in content:
                raise SecurityError(f"Forbidden python systemic call detected: {bad}")
                
        injections = ["ignore previous", "disregard", "you are now", "new instruction"]
        lower_content = content.lower()
        for inj in injections:
            if inj in lower_content:
                raise SecurityError(f"Prompt injection sequence detected: {inj}")
                
    def parse_markdown(self, content: str) -> dict:
        # CHANGED: Trigger strict CSP before any regex memory assignments
        self.check_security(content)
        
        data = {
            "name": "", "role": "", "model": "Auto", "tools": [],
            "system_prompt": "", "constraints": []
        }
        
        title_match = re.search(r'^# Agent:\s*(.+)$', content, re.MULTILINE)
        if title_match:
            data["name"] = title_match.group(1).strip()
            
        sections = re.split(r'^##\s+(.+)$', content, flags=re.MULTILINE)
        if len(sections) > 1:
            for i in range(1, len(sections), 2):
                heading = sections[i].strip().lower()
                body = sections[i+1].strip()
                
                if heading == "role": data["role"] = body
                elif heading == "model": data["model"] = body
                elif heading == "tools": data["tools"] = [line.strip("- ").strip() for line in body.split("\n") if line.strip("- ").strip()]
                elif heading == "system prompt": data["system_prompt"] = body
                elif heading == "constraints": data["constraints"] = [line.strip("- ").strip() for line in body.split("\n") if line.strip("- ").strip()]
                elif heading == "memory":
                    lines = body.split("\n")
                    for line in lines:
                        if "persistent:" in line: data["memory_persistent"] = "true" in line.lower()
                        if "scope:" in line: data["memory_scope"] = line.split("scope:")[-1].strip()
                # CHANGED: Adding metadata ingestion mapped to the v1 schema
                elif heading == "config":
                    lines = body.split("\n")
                    for line in lines:
                        if "schema_version:" in line: data["schema_version"] = int(line.split(":")[-1].strip())
                        if "max_input_tokens:" in line: data["max_input_tokens"] = int(line.split(":")[-1].strip())
                        if "max_output_tokens:" in line: data["max_output_tokens"] = int(line.split(":")[-1].strip())

        return data

    # CHANGED: Built a lock-driven loader that captures exceptions silently, keeping the LAST KNOWN GOOD manifest actively loaded perfectly.
    def load_agent_file(self, filepath: str, filename: str):
        name = filename.replace(".md", "")
        lock = self.get_lock(name)
        
        with lock:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                    
                raw_data = self.parse_markdown(content)
                if not raw_data.get("name"):
                    raw_data["name"] = name
                    
                migrated_data = migrate_manifest(raw_data)
                manifest = AgentManifest(**migrated_data)
                
                self.agents[manifest.name] = manifest
                self.agent_status[name] = {
                    "name": manifest.name,
                    "loaded_at": datetime.utcnow().isoformat(),
                    "schema_version": manifest.schema_version,
                    "is_valid": True,
                    "error": None
                }
                
            except SecurityError as se:
                self.agent_status[name] = {
                    "name": name,
                    "loaded_at": datetime.utcnow().isoformat(),
                    "schema_version": getattr(self.agents.get(name), "schema_version", 1),
                    "is_valid": False,
                    "error": f"Security Error: {str(se)}"
                }
            except ValidationError as ve:
                self.agent_status[name] = {
                    "name": name,
                    "loaded_at": datetime.utcnow().isoformat(),
                    "schema_version": getattr(self.agents.get(name), "schema_version", 1),
                    "is_valid": False,
                    "error": f"Validation Error: {str(ve)}"
                }
            except Exception as e:
                self.agent_status[name] = {
                    "name": name,
                    "loaded_at": datetime.utcnow().isoformat(),
                    "schema_version": getattr(self.agents.get(name), "schema_version", 1),
                    "is_valid": False,
                    "error": f"System Error: {str(e)}"
                }

    def load_all(self):
        if not os.path.exists(self.agents_dir): return
        for filename in os.listdir(self.agents_dir):
            if filename.endswith(".md"):
                self.load_agent_file(os.path.join(self.agents_dir, filename), filename)

    # CHANGED: Export API for FastAPI telemetry
    def get_manifest_status(self) -> List[dict]:
        return list(self.agent_status.values())
        
    # CHANGED: Item 5: Safe Atomic Writes via tempfile & Python os.replace guaranteeing zero partial-reads
    def write_agent_file(self, name: str, content: str):
        lock = self.get_lock(name)
        filepath = os.path.join(self.agents_dir, f"{name}.md")
        
        with lock:
            fd, tmp_path = tempfile.mkstemp(dir=self.agents_dir, text=True)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(content)
                os.replace(tmp_path, filepath)
            except Exception as e:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise e


def load_agent_file(filepath: str) -> AgentManifest:
    """Module-level helper: load and validate a single agent .md file."""
    import os
    filename = os.path.basename(filepath)
    loader = AgentLoader(os.path.dirname(filepath))
    loader.load_agent_file(filepath, filename)
    name = filename.replace(".md", "")
    if name in loader.agents:
        return loader.agents[name]
    status = loader.agent_status.get(name, {})
    if not status.get("is_valid", True):
        raise SecurityError(status.get("error", "Unknown error loading agent file"))
    raise LoaderError(f"Failed to load agent from {filepath}")
