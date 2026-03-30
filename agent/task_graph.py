from typing import List, Dict, Optional

class TaskNode:
    node_id: str
    description: str
    depends_on: List[str]
    priority: int
    status: str
    result: Optional[dict]
    stderr: Optional[str]
    files_modified: int

    def __init__(self, node_id: str, description: str, depends_on: Optional[List[str]] = None, priority: int = 0):
        if depends_on is None:
            depends_on = []
        self.node_id = node_id
        self.description = description
        self.depends_on = depends_on
        self.priority = priority
        self.status = "pending" # pending, running, completed, failed
        self.result = None
        self.stderr = None
        self.files_modified = 0
        
    def __repr__(self):
        return f"TaskNode(id={self.node_id}, status={self.status}, deps={self.depends_on})"

class TaskGraph:
    def __init__(self):
        self.nodes: Dict[str, TaskNode] = {}
        
    def add_node(self, node: TaskNode):
        self.nodes[node.node_id] = node
        
        # Cycle detection
        visited = set()
        path = set()
        
        def visit(n_id):
            if n_id in path:
                raise ValueError(f"Cycle detected involving node: {n_id}")
            if n_id in visited:
                return
            path.add(n_id)
            curr_node = self.nodes.get(n_id)
            if curr_node:
                for dep in curr_node.depends_on:
                    if dep in self.nodes:
                        visit(dep)
            path.remove(n_id)
            visited.add(n_id)
            
        for n_id in self.nodes:
            visit(n_id)
        
    def get_executable_nodes(self) -> List[TaskNode]:
        """Returns pending nodes whose dependencies are all completed."""
        executable = []
        for node in self.nodes.values():
            if node.status == "pending":
                can_run = True
                for dep_id in node.depends_on:
                    dep_node = self.nodes.get(dep_id)
                    if not dep_node or dep_node.status != "completed":
                        can_run = False
                        break
                if can_run:
                    executable.append(node)
        
        # Sort by priority, highest first
        executable.sort(key=lambda x: x.priority, reverse=True)
        return executable
        
    def is_complete(self) -> bool:
        return all(n.status == "completed" or n.status == "failed" for n in self.nodes.values())
        
    def has_failures(self) -> bool:
        return any(n.status == "failed" for n in self.nodes.values())
        
    def reset_failed(self):
        """Allows retry of failed nodes."""
        for n in self.nodes.values():
            if n.status == "failed":
                n.status = "pending"
