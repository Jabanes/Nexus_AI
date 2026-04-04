"""
LLM Provider Factory — Resolves configuration to a concrete LLM provider.
"""

import logging
import os
from typing import Optional

from src.core.llm.base_provider import ILLMProvider

logger = logging.getLogger(__name__)


def create_llm_provider(
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
) -> ILLMProvider:
    """
    Create an LLM provider instance.

    Args:
        provider_name: "gemini", etc. Defaults to LLM_PROVIDER env var or "gemini".
        model: Model name override. Defaults to provider-specific env var.

    Returns:
        An initialized ILLMProvider instance.

    Raises:
        ValueError: If provider is not recognized.
    """
    if provider_name is None:
        provider_name = os.getenv("LLM_PROVIDER", "gemini")

    if provider_name == "gemini":
        from src.core.llm.gemini_provider import GeminiProvider
        return GeminiProvider(model_name=model)
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider_name}'. Available: ['gemini']"
        )
