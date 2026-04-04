"""
Voice Provider Factory — Resolves tenant config to a concrete provider instance.

Maps voice_settings["provider"] string to the appropriate IVoiceProvider class.
"""

import logging
from typing import Any, Dict

from src.core.voice.base_provider import IVoiceProvider, VoiceProviderConfig

logger = logging.getLogger(__name__)

# Registry of known providers (lazy imports to avoid circular deps)
_PROVIDER_REGISTRY: Dict[str, str] = {
    "nvidia_personaplex": "src.core.voice.personaplex_provider.PersonaPlexProvider",
    "elevenlabs": "src.core.voice.elevenlabs_provider.ElevenLabsProvider",
}


def create_voice_provider(
    voice_settings: Dict[str, Any],
    system_prompt: str,
) -> IVoiceProvider:
    """
    Create a voice provider instance from tenant configuration.

    Args:
        voice_settings: The voice_settings dict from tenant config.yaml
        system_prompt: The assembled system prompt for this session

    Returns:
        An initialized IVoiceProvider instance

    Raises:
        ValueError: If the provider name is not recognized
    """
    provider_name = voice_settings.get("provider", "nvidia_personaplex")

    config = VoiceProviderConfig(
        provider=provider_name,
        voice_id=voice_settings.get("voice_id", ""),
        language=voice_settings.get("language", "en-US"),
        system_prompt=system_prompt,
        provider_specific=voice_settings.get("provider_specific", {}),
    )

    # Resolve provider class
    class_path = _PROVIDER_REGISTRY.get(provider_name)
    if not class_path:
        raise ValueError(
            f"Unknown voice provider: '{provider_name}'. "
            f"Available: {list(_PROVIDER_REGISTRY.keys())}"
        )

    # Lazy import
    module_path, class_name = class_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    provider_class = getattr(module, class_name)

    logger.info(f"Creating voice provider: {provider_name} (voice_id={config.voice_id})")
    return provider_class(config)
