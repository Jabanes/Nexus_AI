"""
Voice Provider Interface — The contract all voice backends must implement.

Design decisions:
- run() uses callbacks rather than async iterators so providers can manage
  multiple internal tasks (FFmpeg + WS) and push events without the bridge
  needing to multiplex.
- provider_specific dict avoids schema pollution — only the concrete
  provider interprets its own keys.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional


@dataclass
class VoiceProviderConfig:
    """Configuration passed to a voice provider at construction time."""
    provider: str
    voice_id: str
    language: str = "en-US"
    system_prompt: str = ""
    provider_specific: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptEvent:
    """A transcript fragment emitted by the provider."""
    text: str
    role: str  # "user" or "agent"
    is_final: bool = True


# Callback type aliases for readability
OnAudioCallback = Callable[[bytes], Awaitable[None]]
OnTranscriptCallback = Callable[[TranscriptEvent], Awaitable[None]]
OnErrorCallback = Callable[[Exception], Awaitable[None]]


class IVoiceProvider(ABC):
    """
    Abstract base class for voice processing backends.

    Implementations handle all format conversion internally.
    The bridge never interprets audio bytes — it only shuttles them.
    """

    def __init__(self, config: VoiceProviderConfig):
        self.config = config

    @abstractmethod
    async def connect(self) -> None:
        """
        Establish the backend connection.

        PersonaPlex: WebSocket + GPU handshake.
        ElevenLabs: WebSocket + init metadata.

        Raises ConnectionError on failure.
        """
        ...

    @abstractmethod
    async def send_audio(self, data: bytes) -> None:
        """
        Feed raw browser audio (WebM chunks) into the provider.

        The provider is responsible for any internal format conversion
        (FFmpeg, base64 encoding, etc.).
        """
        ...

    @abstractmethod
    async def run(
        self,
        on_audio: OnAudioCallback,
        on_transcript: OnTranscriptCallback,
        on_error: OnErrorCallback,
    ) -> None:
        """
        Main receive loop. Runs until stop() is called or backend disconnects.

        Callbacks:
            on_audio: called with tagged binary frames ready for the browser
            on_transcript: called with transcript events
            on_error: called on recoverable errors
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown: close connections, kill subprocesses."""
        ...

    @abstractmethod
    def get_diagnostics(self) -> Dict[str, Any]:
        """Return provider-specific diagnostic counters."""
        ...
