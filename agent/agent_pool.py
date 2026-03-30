import concurrent.futures
from typing import Callable, Any

class AgentPool:
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers)
        
    def submit(self, fn: Callable, *args, **kwargs) -> concurrent.futures.Future:
        """Schedules a function to run in a worker thread."""
        return self.executor.submit(fn, *args, **kwargs)
        
    def map(self, fn: Callable, *iterables):
        """Map a function over iterables concurrently."""
        return self.executor.map(fn, *iterables)
        
    def shutdown(self, wait: bool = True):
        self.executor.shutdown(wait=wait)
