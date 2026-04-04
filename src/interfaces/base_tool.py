from abc import ABC, abstractmethod
from typing import Dict, Any, List


class BaseTool(ABC):
    """
    The Single Source of Truth for tool execution.
    Every tenant tool MUST inherit from this class.

    Tools can optionally receive per-tenant config via the constructor.
    This allows shared integration tools to be parameterized per tenant
    (e.g., different calendar_id for each business).
    """

    def __init__(self, config: Dict[str, Any] = None):
        self._config = config or {}

    @property
    def config(self) -> Dict[str, Any]:
        """Per-tenant configuration passed from config.yaml."""
        return self._config

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
