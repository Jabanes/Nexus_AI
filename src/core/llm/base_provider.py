"""
LLM Provider Interface — The contract all LLM backends must implement.

Return type contracts:
- send_message: {"text": str, "function_calls": [{"name": str, "args": dict}]}
- send_message_stream: yields {"type": "text"|"function_call"|"error", ...}
- send_function_response: returns str
- send_function_response_stream: yields {"type": "text"|"function_call"|"error", ...}
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, List


class ILLMProvider(ABC):
    """Abstract base class for LLM backends (Gemini, OpenAI, Claude, etc.)."""

    @abstractmethod
    def create_chat_session(
        self, system_prompt: str, tools: List
    ) -> Any:
        """
        Create a new chat session with system prompt and tool definitions.

        Args:
            system_prompt: System instruction for the LLM
            tools: List of BaseTool instances

        Returns:
            An opaque chat session object (provider-specific)
        """
        ...

    @abstractmethod
    async def send_message(
        self, chat: Any, message: str
    ) -> Dict[str, Any]:
        """
        Send a message and get the full response.

        Returns:
            {"text": str, "function_calls": [{"name": str, "args": dict}]}
        """
        ...

    @abstractmethod
    async def send_message_stream(
        self, chat: Any, message: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream the response from the LLM.

        Yields:
            {"type": "text", "content": str}
            {"type": "function_call", "call": {"name": str, "args": dict}}
            {"type": "error", "error": str}
        """
        ...

    @abstractmethod
    async def send_function_response(
        self, chat: Any, function_name: str, function_response: Any
    ) -> str:
        """
        Send tool execution results back to the LLM.

        Returns:
            The LLM's follow-up text response
        """
        ...

    @abstractmethod
    async def send_function_response_stream(
        self, chat: Any, function_name: str, function_response: Any
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream the LLM's follow-up response after tool execution.

        Yields same chunk format as send_message_stream.
        """
        ...
