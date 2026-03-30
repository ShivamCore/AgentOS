# CHANGED: Added required system packages for hashing, logging, and typing dataclass rules.
import os
import hashlib
from dataclasses import dataclass
from typing import Optional
from agent.loader import AgentLoader, AgentManifest
from agent.llm import generate_text, extract_json_safely, DEFAULT_MODEL
from agent.utils.model_router import select_model as _route_model
from agent.planner.graph import TaskGraph, StepNode, parse_planner_dag

# CHANGED: SelectionResult structured data representing explicit runtime decisions (Item 2)
@dataclass
class SelectionResult:
    agent_name: str
    confidence: float
    reason: str
    manifest: AgentManifest

class AgentSelector:
    # CHANGED: Confidence Threshold (Item 2)
    CONFIDENCE_THRESHOLD = 0.6

    def __init__(self):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agents"))
        self.loader = AgentLoader(root_dir)

    def select_agent(self, task_type: str, task_id: str = "system") -> SelectionResult:
        self.loader.load_all() 
        
        # ── Deterministic routing hooks ───────────────
        if task_type == "plan":
            res = SelectionResult("planner", 0.95, "Explicit fallback match mapping task to plan.", self.loader.agents.get("planner"))
        elif task_type == "code":
            res = SelectionResult("coder", 0.90, "Explicit mapping to internal node code.", self.loader.agents.get("coder"))
        elif task_type == "debug":
            res = SelectionResult("debugger", 0.90, "System intercept to debugger.", self.loader.agents.get("debugger"))
        else:
            found = None
            for agent in self.loader.agents.values():
                if task_type in agent.capabilities:
                    found = agent
                    break
            
            if found:
                res = SelectionResult(found.name, 0.75, "Capability heuristics match", found)
            else:
                # CHANGED: Confidence strict check (Item 2)
                res = SelectionResult("planner", 0.30, f"Below {self.CONFIDENCE_THRESHOLD} threshold. Defaulted to Master Planner", self.loader.agents.get("planner"))

        # CHANGED: Hook to push logic into SQLite Database (Item 7)
        self.log_selection(task_id, task_type, res)
        return res

    def log_selection(self, task_id: str, input_hash_src: str, res: SelectionResult):
        # Local Imports ensuring clean start without immediate DB circular paths
        from backend.db.database import SessionLocal
        from backend.models.sql_models import AgentSelectionLogRecord
        
        input_hash = hashlib.md5(input_hash_src.encode()).hexdigest()
        try:
            with SessionLocal() as db:
                db.add(AgentSelectionLogRecord(
                    task_id=task_id,
                    input_hash=input_hash,
                    selected_agent=res.agent_name,
                    confidence=int(res.confidence * 100), # Model stores as Int 0-100 logically
                    reason=res.reason
                ))
                db.commit()
        except Exception as e:
            # Silent logging bounds since we don't want telemetry crashing orchestration
            pass

_global_selector = AgentSelector()

# CHANGED: Updated the API contract explicitly passing through DB tracking
def get_agent(task_type: str, task_id: str = "system") -> SelectionResult:
    return _global_selector.select_agent(task_type, task_id)

# CHANGED: Exported DB logs for FastApi query endpoint (Item 7 & 9)
def get_selection_log(limit: int = 50):
    from backend.db.database import SessionLocal
    from backend.models.sql_models import AgentSelectionLogRecord
    with SessionLocal() as db:
        logs = db.query(AgentSelectionLogRecord).order_by(AgentSelectionLogRecord.timestamp.desc()).limit(limit).all()
        return [{"task_id": l.task_id, "selected_agent": l.selected_agent, "confidence": l.confidence, "reason": l.reason, "timestamp": l.timestamp.isoformat() if l.timestamp else None} for l in logs]

# CHANGED: Function implementing Strict Prompt Token Boundary (Item 8)
def check_token_budget(prompt: str, max_input: int) -> str:
    est_tokens = len(prompt) // 4
    if est_tokens > max_input:
        print(f"WARNING: Token budget exceeded ({est_tokens} > {max_input}). Truncating prompt cleanly.")
        # Truncate to maximum characters safely
        return prompt[:max_input * 4]
    return prompt

def execute_markdown_agent(task_type: str, step_description: str, error_or_context: str = "", workspace_context: str = "", model: str = "Auto", stream_callback=None, task_id: str = "system") -> str:
    selection_res = get_agent(task_type, task_id)
    agent = selection_res.manifest
    
    if task_type == "code":
        example = '{"files":[{"path":"add.py","action":"write","code":"def add(a,b):\\n    return a+b"}],"command":null}'
        prompt = f"{example}\nNow output the JSON for this step: {step_description}\nMain task context: {error_or_context}"
        if workspace_context: prompt += f"\nExisting files: {workspace_context}"
        temp = 0.0
    elif task_type == "debug":
        prompt = f"MAIN TASK: {step_description}\n\nFAILING ERROR:\n{error_or_context}"
        if workspace_context: prompt += f"\n\nWORKSPACE STATE MAP:\n{workspace_context}"
        temp = 0.1
    else:
        prompt = f"Task: {step_description}\nContext: {error_or_context}\nWorkspace Context: {workspace_context}"
        temp = 0.2

    # CHANGED: Applies Token Budget truncation cleanly natively mapping LLM arguments
    safe_prompt = check_token_budget(prompt, agent.max_input_tokens)
        
    return generate_text(
        safe_prompt,
        system_prompt=agent.system_prompt,
        temperature=temp,
        model=_route_model(task_type=task_type) if (agent.model == "Auto" and model == "Auto") else (agent.model if agent.model != "Auto" else model),
        stream_callback=stream_callback,
        max_tokens=agent.max_output_tokens,
        task_type=task_type,
    )

def plan_markdown_task(task_description: str, model: str = "Auto", task_id: str = "system") -> TaskGraph:
    selection_res = get_agent("plan", task_id)
    agent = selection_res.manifest
    
    prompt = '{"task_id": "auto_generated", "steps": [{"step_id":"1","description":"Write add.py","required_tools":["file_write"],"preferred_agent":"coder","dependencies":[]}]}\nNow output the full strict JSON DAG plan for: ' + task_description
    safe_prompt = check_token_budget(prompt, agent.max_input_tokens)

    for _ in range(2):
        try:
            text = generate_text(
                safe_prompt,
                system_prompt=agent.system_prompt,
                temperature=0.0,
                model=_route_model(task_type="plan") if (agent.model == "Auto" and model == "Auto") else (agent.model if agent.model != "Auto" else model),
                max_tokens=agent.max_output_tokens,
                task_type="plan",
            )
            return parse_planner_dag(text, fallback_task_id=task_id)
        except Exception as e:
            print(f"Trapped planning fault mapping schema recursively: {e}")
            continue
            
    g = TaskGraph(task_id=task_id)
    g.nodes["1"] = StepNode(
        step_id="1", 
        description=task_description, 
        required_tools=["file_write", "terminal"], 
        preferred_agent="coder", 
        dependencies=[]
    )
    return g
