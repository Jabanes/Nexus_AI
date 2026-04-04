"""
ElevenLabs Voice Provider — Cloud-based conversational AI with STT + TTS.

Audio pipeline:
  Input:  Browser WebM -> FFmpeg -> PCM16 LE 16kHz -> base64 -> ElevenLabs WS
  Output: ElevenLabs WS -> base64 PCM16 -> raw bytes -> Browser (decoded in JS)
"""

import asyncio
import base64
import json
import logging
import os
import traceback
from typing import Any, Dict, Optional

import websockets

from src.core.voice.base_provider import (
    IVoiceProvider,
    OnAudioCallback,
    OnErrorCallback,
    OnTranscriptCallback,
    TranscriptEvent,
    VoiceProviderConfig,
)

logger = logging.getLogger(__name__)

AUDIO_TAG_PCM16 = 0x02


class ElevenLabsProvider(IVoiceProvider):

    def __init__(self, config: VoiceProviderConfig):
        super().__init__(config)

        self.api_key = (
            config.provider_specific.get("api_key")
            or os.getenv("ELEVENLABS_API_KEY")
        )
        self.agent_id = (
            config.provider_specific.get("agent_id")
            or os.getenv("ELEVENLABS_AGENT_ID")
        )

        if not self.api_key:
            raise ValueError("ElevenLabs API key not configured (ELEVENLABS_API_KEY)")
        if not self.agent_id:
            raise ValueError("ElevenLabs Agent ID not configured (ELEVENLABS_AGENT_ID)")

        self.ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg")

        self.ws: Optional[Any] = None
        self.is_running = False
        self._input_transcoder: Optional[asyncio.subprocess.Process] = None
        self._tasks: list = []

        # Diagnostics
        self.audio_chunks_sent = 0
        self.audio_chunks_received = 0
        self.total_bytes_rx = 0
        self.total_bytes_tx = 0
        self.transcripts_received = 0
        self.conversation_id: Optional[str] = None

        logger.info(f"ElevenLabsProvider initialized (agent_id={self.agent_id})")

    async def connect(self) -> None:
        await self._start_input_transcoder()
        await self._connect_ws()

    async def send_audio(self, data: bytes) -> None:
        if self._input_transcoder and self._input_transcoder.stdin:
            try:
                self._input_transcoder.stdin.write(data)
                await self._input_transcoder.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass  # FFmpeg closed — not fatal, will restart if needed

    async def run(
        self,
        on_audio: OnAudioCallback,
        on_transcript: OnTranscriptCallback,
        on_error: OnErrorCallback,
    ) -> None:
        """
        Run streaming tasks. The WS receiver is the PRIMARY task — it runs
        until the conversation ends. The input pump is secondary — if it dies
        (FFmpeg EOF), we DON'T kill the conversation.
        """
        self.is_running = True

        task_input = asyncio.create_task(
            self._pump_pcm_to_ws(), name="el_input_pump"
        )
        task_ws = asyncio.create_task(
            self._pump_ws_receiver(on_audio, on_transcript, on_error),
            name="el_ws_receiver",
        )
        self._tasks = [task_input, task_ws]

        logger.info("ElevenLabsProvider: streaming tasks launched")

        # CRITICAL: Only exit when the WS receiver ends.
        # The input pump can die (FFmpeg EOF) without killing the conversation.
        try:
            await task_ws
        except Exception as e:
            logger.error(f"ElevenLabs WS receiver exception: {e}")
        finally:
            exc = task_ws.exception() if task_ws.done() and not task_ws.cancelled() else None
            logger.warning(f"ElevenLabs: WS receiver ended (exception={exc})")
            # Cancel input pump if still running
            if not task_input.done():
                task_input.cancel()

    async def stop(self) -> None:
        logger.info("ElevenLabsProvider stopping...")
        self.is_running = False

        if self._input_transcoder:
            try:
                self._input_transcoder.kill()
                await asyncio.wait_for(self._input_transcoder.wait(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass

        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass

        for t in self._tasks:
            if not t.done():
                t.cancel()

        logger.info("ElevenLabsProvider stopped")

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "provider": "elevenlabs",
            "conversation_id": self.conversation_id,
            "audio_chunks_sent": self.audio_chunks_sent,
            "audio_chunks_received": self.audio_chunks_received,
            "total_bytes_rx": self.total_bytes_rx,
            "total_bytes_tx": self.total_bytes_tx,
            "transcripts_received": self.transcripts_received,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _start_input_transcoder(self):
        self._input_transcoder = await asyncio.create_subprocess_exec(
            self.ffmpeg_path,
            "-hide_banner", "-loglevel", "error",
            "-f", "webm", "-i", "pipe:0",
            "-af", "volume=10dB",
            "-ar", "16000", "-ac", "1",
            "-f", "s16le", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info(f"ElevenLabs input transcoder started (PID={self._input_transcoder.pid})")

    async def _connect_ws(self):
        ws_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={self.agent_id}"

        logger.info(f"Connecting to ElevenLabs...")
        self.ws = await websockets.connect(
            ws_url,
            additional_headers={"xi-api-key": self.api_key},
            ping_interval=20,
            close_timeout=5,
        )

        try:
            init_msg = await asyncio.wait_for(self.ws.recv(), 15.0)
            data = json.loads(init_msg)
            if data.get("type") == "conversation_initiation_metadata":
                self.conversation_id = data.get("conversation_initiation_metadata_event", {}).get(
                    "conversation_id", "unknown"
                )
                logger.info(f"[ElevenLabs] Connected, conversation_id={self.conversation_id}")
            else:
                logger.warning(f"[ElevenLabs] Unexpected init: {data.get('type')}")
        except asyncio.TimeoutError:
            await self.ws.close()
            raise ConnectionError("ElevenLabs connection init timeout")
        except Exception as e:
            raise ConnectionError(f"ElevenLabs connection failed: {e}")

    async def _pump_pcm_to_ws(self):
        """Read PCM16 from FFmpeg, send to ElevenLabs. Non-fatal if this dies."""
        logger.info("ElevenLabs: input pump started")
        try:
            while self.is_running and self.ws:
                try:
                    pcm_chunk = await asyncio.wait_for(
                        self._input_transcoder.stdout.read(3200), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    continue  # No audio from mic — just keep waiting

                if not pcm_chunk:
                    logger.warning("ElevenLabs: FFmpeg stdout EOF, waiting...")
                    await asyncio.sleep(1)
                    continue  # DON'T exit — FFmpeg might resume

                b64_audio = base64.b64encode(pcm_chunk).decode("ascii")
                try:
                    await self.ws.send(json.dumps({"user_audio_chunk": b64_audio}))
                except websockets.exceptions.ConnectionClosed:
                    logger.info("ElevenLabs: WS closed during audio send")
                    break

                self.audio_chunks_sent += 1
                self.total_bytes_tx += len(pcm_chunk)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"ElevenLabs input pump error: {e}")
        finally:
            logger.info(f"ElevenLabs: input pump exited (chunks={self.audio_chunks_sent})")

    async def _pump_ws_receiver(
        self,
        on_audio: OnAudioCallback,
        on_transcript: OnTranscriptCallback,
        on_error: OnErrorCallback,
    ):
        """Read events from ElevenLabs WS. This is the PRIMARY task."""
        logger.info("ElevenLabs: WS receiver started")
        try:
            while self.is_running and self.ws:
                try:
                    raw = await self.ws.recv()
                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"ElevenLabs WS closed: code={e.code} reason={e.reason}")
                    break

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(f"ElevenLabs: non-JSON message: {raw[:100]}")
                    continue

                event_type = data.get("type", "")

                try:
                    if event_type == "audio":
                        audio_event = data.get("audio_event", {})
                        b64_audio = audio_event.get("audio_base_64", "")
                        if b64_audio:
                            pcm_bytes = base64.b64decode(b64_audio)
                            self.audio_chunks_received += 1
                            self.total_bytes_rx += len(pcm_bytes)
                            tagged_frame = bytes([AUDIO_TAG_PCM16]) + pcm_bytes
                            await on_audio(tagged_frame)

                    elif event_type == "user_transcript":
                        text = data.get("user_transcription_event", {}).get("user_transcript", "")
                        if text:
                            self.transcripts_received += 1
                            await on_transcript(TranscriptEvent(text=text, role="user", is_final=True))

                    elif event_type == "agent_response":
                        text = data.get("agent_response_event", {}).get("agent_response", "")
                        if text:
                            self.transcripts_received += 1
                            await on_transcript(TranscriptEvent(text=text, role="agent", is_final=True))

                    elif event_type == "interruption":
                        logger.info("[ElevenLabs] Barge-in detected")

                    elif event_type == "ping":
                        eid = data.get("ping_event", {}).get("event_id")
                        await self.ws.send(json.dumps({"type": "pong", "event_id": eid}))

                    elif event_type == "conversation_initiation_metadata":
                        pass

                    elif event_type == "agent_response_correction":
                        pass

                    elif event_type == "client_tool_call":
                        tool_name = data.get("client_tool_call", {}).get("tool_name", "")
                        logger.info(f"[ElevenLabs] Tool call requested: {tool_name}")

                    elif event_type == "agent_tool_response":
                        logger.info(f"[ElevenLabs] Tool response received")

                    else:
                        logger.info(f"[ElevenLabs] Event: {event_type}")

                except Exception as e:
                    # One bad event should NOT kill the whole receiver
                    logger.error(f"[ElevenLabs] Error handling event '{event_type}': {e}")
                    continue

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"ElevenLabs WS receiver error: {e}")
            logger.error(traceback.format_exc())
        finally:
            self.is_running = False
            logger.info(
                f"ElevenLabs: WS receiver exited "
                f"(audio_rx={self.audio_chunks_received} transcripts={self.transcripts_received})"
            )
