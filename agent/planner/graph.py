import json
import re
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class StepNode(BaseModel):
    """Represents a strictly enforced atomic task step within the Directed Acyclic Graph constraints."""
    step_id: str
    description: str
    required_tools: List[str] = Field(default_factory=list)
    preferred_agent: str = "coder"
    dependencies: List[str] = Field(default_factory=list)
    
    # State tracking attributes actively mutating across bounded orchestrator threads
    status: str = "pending"  # pending, running, completed, failed
    retries: int = 0
    error: Optional[str] = None
    output: Optional[str] = None


class TaskGraph(BaseModel):
    """Wrapper encapsulating strictly routed parallel executable trees dynamically mapping states natively."""
    task_id: str
    nodes: Dict[str, StepNode] = Field(default_factory=dict)
    
    def is_complete(self) -> bool:
        """Determines if the entire DAG topology has completed execution sequentially safely."""
        return all(n.status == "completed" for n in self.nodes.values())
        
    def has_failures(self) -> bool:
        """Determines if any irreversible terminal execution blockages disrupted the DAG natively."""
        return any(n.status == "failed" for n in self.nodes.values())
        
    def get_executable_nodes(self) -> List[StepNode]:
        """Returns topological leaves currently unblocked by parent prerequisite success resolutions reliably."""
        executable = []
        for node in self.nodes.values():
            if node.status == "pending":
                # Ensure all explicit explicit prerequisite steps terminated identically with completion mapping
                deps_met = all(self.nodes[d].status == "completed" for d in node.dependencies if d in self.nodes)
                if deps_met:
                    executable.append(node)
        return executable

def extract_json_from_text(text: str) -> str:
    """Robust extraction stripping Markdown wrappers completely natively isolating LLM payloads strictly."""
    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if match: return match.group(1)
    # Attempt primitive bracket capturing if code blocks missing natively
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1: return text[start:end+1]
    return text

def parse_planner_dag(llm_output: str, fallback_task_id: str) -> TaskGraph:
    """
    Parses LLM topological reasoning logic natively enforcing Pydantic bounds immediately stopping invalid node mapping.
    """
    clean_json = extract_json_from_text(llm_output)
    try:
        data = json.loads(clean_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner emitted irreversible invalid syntax generating JSONDecodeError: {str(e)}")
        
    task_id = data.get("task_id", fallback_task_id)
    if task_id == "auto_generated" or not task_id:
        task_id = fallback_task_id
        
    graph = TaskGraph(task_id=task_id)
    steps = data.get("steps", [])
    
    if not isinstance(steps, list):
        raise ValueError("Planner JSON schema violation: 'steps' root missing or formatted as invalid scalar type.")
        
    for payload in steps:
        node = StepNode(**payload)
        graph.nodes[node.step_id] = node
        
    # Clean dangling topological dependencies guarding runtime crashes securely
    for node in graph.nodes.values():
        node.dependencies = [d for d in node.dependencies if d in graph.nodes]
        
    return graph
