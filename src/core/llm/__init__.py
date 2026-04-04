"""
LLM Provider Package — Provider-agnostic LLM integration.

Use create_llm_provider() to get the configured provider instance.
Never import a specific provider class directly from orchestration code.
"""

from src.core.llm.base_provider import ILLMProvider
from src.core.llm.factory import create_llm_provider

__all__ = ["ILLMProvider", "create_llm_provider"]
