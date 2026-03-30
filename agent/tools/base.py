from abc import ABC, abstractmethod
from typing import Dict, Any, Type
from pydantic import BaseModel

class Tool(ABC):
    """Base Tool Interface standardizing Agent Action execution bounds natively."""
    name: str
    description: str
    input_schema: Type[BaseModel]

    @abstractmethod
    def execute(self, inputs: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Executes the tool logic with the provided valid schema inputs."""
        pass
