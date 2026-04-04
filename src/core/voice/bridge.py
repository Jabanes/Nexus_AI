"""
Voice Bridge — Thin orchestrator between browser WebSocket and voice provider.

This replaces the monolithic AudioBridge. It knows nothing about specific
providers — it only shuttles bytes between the client and an IVoiceProvider.
"""

import asyncio
import logging
import traceback
from typing import Any, Optional

from fastapi import WebSocket

from src.core.voice.base_provider import IVoiceProvider, TranscriptEvent

logger = logging.getLogger(__name__)


class VoiceBridge:
    """
    Manages the bidirectional audio stream between a browser WebSocket
    and an IVoiceProvider implementation.
    """

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
        self._tasks: list = []

        logger.info(f"VoiceBridge initialized: session={session_id}")

    async def process_stream(self):
        """
        Main entry point. Connects to provider, starts bidirectional streaming.
        Blocks until one side disconnects or errors.
        """
        logger.info(f"VoiceBridge: starting stream for session={self.session_id}")
        try:
            # 1. Connect to the voice backend
            await self.provider.connect()
            logger.info("VoiceBridge: provider connected")

            self.is_running = True

            # 2. Launch two concurrent tasks
            task_client = asyncio.create_task(
                self._handle_client_input(), name="bridge_client_input"
            )
            task_provider = asyncio.create_task(
                self.provider.run(
                    on_audio=self._on_provider_audio,
                    on_transcript=self._on_provider_transcript,
                    on_error=self._on_provider_error,
                ),
                name="bridge_provider_run",
            )
            self._tasks = [task_client, task_provider]

            logger.info("VoiceBridge: streaming tasks launched")

            # 3. Wait for first completion
            done, pending = await asyncio.wait(
                self._tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                exc = t.exception() if not t.cancelled() else None
                logger.warning(
                    f"VoiceBridge FIRST_COMPLETED: task={t.get_name()} exception={exc}"
                )

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

                if "bytes" in msg:
                    data = msg["bytes"]
                    chunks_received += 1
                    await self.provider.send_audio(data)

                elif "type" in msg and msg["type"] == "websocket.disconnect":
                    logger.info("VoiceBridge: client sent disconnect")
                    self.is_running = False
                    break

                elif "text" in msg:
                    logger.debug(f"VoiceBridge: client text: {msg['text'][:100]}")

        except Exception as e:
            logger.error(f"VoiceBridge: client input error: {e}")
            logger.error(traceback.format_exc())
        finally:
            logger.info(f"VoiceBridge: client input exited (chunks_rx={chunks_received})")

    async def _on_provider_audio(self, data: bytes) -> None:
        """Callback: forward audio bytes from provider to browser."""
        try:
            await self.client_ws.send_bytes(data)
        except RuntimeError:
            logger.warning("VoiceBridge: client WS closed, stopping")
            self.is_running = False

    async def _on_provider_transcript(self, event: TranscriptEvent) -> None:
        """Callback: forward transcript to browser and record."""
        try:
            await self.client_ws.send_json({
                "type": "transcript",
                "role": event.role,
                "content": event.text,
                "is_final": event.is_final,
            })
        except RuntimeError:
            logger.warning("VoiceBridge: client WS closed during transcript send")
            self.is_running = False
            return

        # Record to session history
        if self.recorder:
            if event.role == "user":
                self.recorder.log_user_text(event.text)
            elif event.role == "agent":
                self.recorder.log_ai_text(event.text)

    async def _on_provider_error(self, error: Exception) -> None:
        """Callback: handle recoverable provider errors."""
        logger.error(f"VoiceBridge: provider error: {error}")
        if self.recorder:
            self.recorder.log_error("provider_error", str(error))

    async def stop(self):
        """Stop the bridge and provider."""
        logger.info("VoiceBridge stopping...")
        self.is_running = False
        await self.provider.stop()
        for t in self._tasks:
            t.cancel()
        logger.info("VoiceBridge stopped")
