"""
Voice Bridge — Thin orchestrator between browser WebSocket and voice provider.
"""

import asyncio
import logging
import traceback
from typing import Any, Optional

from fastapi import WebSocket

from src.core.voice.base_provider import IVoiceProvider, TranscriptEvent

logger = logging.getLogger(__name__)


class VoiceBridge:

    def __init__(
        self,
        client_ws: WebSocket,
        provider: IVoiceProvider,
        session_id: str,
        session_recorder: Optional[Any] = None,
    ):
        self.client_ws = client_ws
        self.provider = provider
        self.session_id = session_id
        self.recorder = session_recorder
        self.is_running = False
        self._client_task = None

        logger.info(f"VoiceBridge initialized: session={session_id}")

    async def process_stream(self):
        logger.info(f"VoiceBridge: starting stream for session={self.session_id}")
        try:
            await self.provider.connect()
            logger.info("VoiceBridge: provider connected")

            self.is_running = True

            # Client input runs as background task — if it dies, the call continues.
            # Only the PROVIDER controls the call lifecycle.
            self._client_task = asyncio.create_task(
                self._handle_client_input(), name="bridge_client_input"
            )

            # This blocks until the ElevenLabs conversation ends
            await self.provider.run(
                on_audio=self._on_provider_audio,
                on_transcript=self._on_provider_transcript,
                on_error=self._on_provider_error,
            )

            logger.info("VoiceBridge: provider run ended, sending end signal to browser")

            # Provider ended — give browser time to play remaining audio
            try:
                await self.client_ws.send_json({
                    "type": "conversation_ended",
                    "message": "Conversation complete.",
                })
            except Exception:
                pass

            await asyncio.sleep(3)

        except ConnectionError as e:
            logger.error(f"VoiceBridge: provider connection failed: {e}")
            raise
        except Exception as e:
            logger.error(f"VoiceBridge: process_stream error: {e}")
            logger.error(traceback.format_exc())
        finally:
            diagnostics = self.provider.get_diagnostics()
            logger.info(f"VoiceBridge: final diagnostics: {diagnostics}")
            await self.stop()

    async def _handle_client_input(self):
        """Read browser WebSocket messages and forward audio to provider."""
        logger.info("VoiceBridge: client input handler started")
        chunks_received = 0
        try:
            while self.is_running:
                msg = await self.client_ws.receive()
                msg_type = msg.get("type", "unknown")

                if "bytes" in msg:
                    chunks_received += 1
                    await self.provider.send_audio(msg["bytes"])

                elif msg_type == "websocket.disconnect":
                    code = msg.get("code", "?")
                    logger.info(f"VoiceBridge: browser disconnected (code={code})")
                    # DON'T set is_running = False — let the provider finish
                    break

                elif "text" in msg:
                    logger.debug(f"VoiceBridge: client text: {msg['text'][:100]}")

        except Exception as e:
            logger.warning(f"VoiceBridge: client input error: {type(e).__name__}: {e}")
        finally:
            logger.info(f"VoiceBridge: client input exited (chunks_rx={chunks_received})")

    async def _on_provider_audio(self, data: bytes) -> None:
        try:
            await self.client_ws.send_bytes(data)
        except Exception:
            pass  # Browser might have disconnected — don't crash the provider

    async def _on_provider_transcript(self, event: TranscriptEvent) -> None:
        logger.info(f"VoiceBridge transcript [{event.role}]: {event.text[:100]}")
        try:
            await self.client_ws.send_json({
                "type": "transcript",
                "role": event.role,
                "content": event.text,
                "is_final": event.is_final,
            })
        except Exception:
            pass  # Browser might have disconnected — don't crash the provider

        if self.recorder:
            if event.role == "user":
                self.recorder.log_user_text(event.text)
            elif event.role == "agent":
                self.recorder.log_ai_text(event.text)

    async def _on_provider_error(self, error: Exception) -> None:
        logger.error(f"VoiceBridge: provider error: {error}")
        if self.recorder:
            self.recorder.log_error("provider_error", str(error))

    async def stop(self):
        logger.info("VoiceBridge stopping...")
        self.is_running = False
        await self.provider.stop()
        if self._client_task and not self._client_task.done():
            self._client_task.cancel()
        logger.info("VoiceBridge stopped")
