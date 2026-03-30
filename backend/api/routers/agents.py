from fastapi import APIRouter, HTTPException, Body
from typing import List, Any
from agent.selector import _global_selector, get_selection_log
from agent.loader import AgentLoader, AgentManifest, SecurityError, migrate_manifest
from pydantic import ValidationError
import os
import time

router = APIRouter()

@router.get("/status")
def get_agents_status():
    _global_selector.loader.load_all()
    return _global_selector.loader.get_manifest_status()

# CHANGED: Validates abstract markdown dynamically providing direct frontend UI syntax checking (Item 9)
@router.post("/validate")
def validate_agent_markdown(content: str = Body(..., media_type="text/plain")):
    try:
        _global_selector.loader.check_security(content)
        raw_data = _global_selector.loader.parse_markdown(content)
        migrated = migrate_manifest(raw_data)
        manifest = AgentManifest(**migrated)
        return {"is_valid": True, "error": None, "manifest": manifest.model_dump()}
    except SecurityError as se:
        return {"is_valid": False, "error": f"Security Violation: {se}"}
    except ValidationError as ve:
        return {"is_valid": False, "error": f"Schema Validation Error: {ve}"}
    except Exception as e:
        return {"is_valid": False, "error": f"System Error: {e}"}

# CHANGED: Added explicitly the Save endpoint to satisfy the Frontend file saving loops requested.
from pydantic import BaseModel
class SaveAgentPayload(BaseModel):
    name: str
    content: str
@router.post("/save")
def save_agent_file(payload: SaveAgentPayload):
    try:
        # Save atomically
        _global_selector.loader.write_agent_file(payload.name, payload.content)
        # Reload memory
        _global_selector.loader.load_agent_file(os.path.join(_global_selector.loader.agents_dir, f"{payload.name}.md"), f"{payload.name}.md")
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/{name}/content")
def get_agent_content(name: str):
    try:
        path = os.path.join(_global_selector.loader.agents_dir, f"{name}.md")
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="Agent file not found")
        with open(path, "r", encoding="utf-8") as f:
            return {"content": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/selection-log")
def fetch_selection_log():
    return get_selection_log(limit=50)

# CHANGED: Generates simulated Agent outputs evaluating specific profiles cleanly.
@router.post("/{name}/test")
def test_agent_endpoint(name: str, payload: dict = Body(...)):
    from agent.selector import execute_markdown_agent
    task_type = "code" if name == "coder" else "debug" if name == "debugger" else "plan"
    
    t0 = time.time()
    try:
        output = execute_markdown_agent(
            task_type=task_type,
            step_description=payload.get("step_description", "Test logic integration"),
            error_or_context=payload.get("context", ""),
        )
        duration_ms = int((time.time() - t0) * 1000)
        return {"task_id": "test_env", "agent_used": name, "output": output, "duration_ms": duration_ms, "error": None}
    except Exception as e:
        return {"task_id": "test_env", "agent_used": name, "output": "", "error": str(e)}
