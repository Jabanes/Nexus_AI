from abc import ABC, abstractmethod
from typing import Dict, Any, List

class BaseTool(ABC):
    """
    The Single Source of Truth for tool execution.
    Every tenant tool MUST inherit from this class.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """The function name for the LLM (e.g., 'check_inventory')"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Instruction for the LLM on when/how to use this tool"""
        pass

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """JSON Schema definition of expected arguments"""
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """
        The actual logic. Connects to DB/API.
        Must return a string or serializable object.
        """
        pass