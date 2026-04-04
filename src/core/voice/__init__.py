"""
Voice Provider Package — Provider-agnostic voice streaming.

This package abstracts voice processing backends (PersonaPlex, ElevenLabs, etc.)
behind a common interface, allowing tenant configs to switch providers via YAML.
"""

from src.core.voice.base_provider import IVoiceProvider, VoiceProviderConfig, TranscriptEvent
from src.core.voice.bridge import VoiceBridge
from src.core.voice.provider_factory import create_voice_provider

__all__ = [
    "IVoiceProvider",
    "VoiceProviderConfig",
    "TranscriptEvent",
    "VoiceBridge",
    "create_voice_provider",
]
