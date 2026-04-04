"""
ElevenLabs Voice Provider — Cloud-based conversational AI with STT + TTS.

Uses the ElevenLabs Conversational AI WebSocket API.
No GPU sidecar needed — all processing happens in the cloud.

Audio pipeline:
  Input:  Browser WebM -> FFmpeg -> PCM16 LE 16kHz -> base64 -> ElevenLabs WS
  Output: ElevenLabs WS -> base64 PCM16 -> FFmpeg -> Ogg/Opus -> Browser

The output is re-encoded to Ogg/Opus so the existing browser client
(WASM Opus decoder + AudioWorklet) works without changes.
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


class ElevenLabsProvider(IVoiceProvider):
    """
    Voice provider using ElevenLabs Conversational AI WebSocket API.

    Handles bidirectional audio streaming with automatic STT + TTS.
    Supports Hebrew and many other languages natively.
    """

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
        self._output_transcoder: Optional[asyncio.subprocess.Process] = None
        self._tasks: list = []

        # Diagnostics
        self.audio_chunks_sent = 0
        self.audio_chunks_received = 0
        self.total_bytes_rx = 0
        self.total_bytes_tx = 0
        self.transcripts_received = 0

        logger.info(f"ElevenLabsProvider initialized (agent_id={self.agent_id})")

    # ------------------------------------------------------------------
    # IVoiceProvider interface
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Start FFmpeg transcoders and connect to ElevenLabs WS."""
        await self._start_input_transcoder()
        await self._start_output_transcoder()
        await self._connect_ws()

    async def send_audio(self, data: bytes) -> None:
        """Write browser WebM audio to input FFmpeg stdin."""
        if self._input_transcoder and self._input_transcoder.stdin:
            self._input_transcoder.stdin.write(data)
            await self._input_transcoder.stdin.drain()

    async def run(
        self,
        on_audio: OnAudioCallback,
        on_transcript: OnTranscriptCallback,
        on_error: OnErrorCallback,
    ) -> None:
        """Run three internal tasks: input pump, WS receiver, output pump."""
        self.is_running = True

        task_input = asyncio.create_task(
            self._pump_pcm_to_ws(), name="el_input_pump"
        )
        task_ws = asyncio.create_task(
            self._pump_ws_receiver(on_transcript, on_error), name="el_ws_receiver"
        )
        task_output = asyncio.create_task(
            self._pump_output_to_browser(on_audio), name="el_output_pump"
        )
        self._tasks = [task_input, task_ws, task_output]

        logger.info("ElevenLabsProvider: streaming tasks launched")

        done, pending = await asyncio.wait(
            self._tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for t in done:
            exc = t.exception() if not t.cancelled() else None
            logger.warning(
                f"ElevenLabs FIRST_COMPLETED: task={t.get_name()} exception={exc}"
            )

    async def stop(self) -> None:
        logger.info("ElevenLabsProvider stopping...")
        self.is_running = False

        # Kill FFmpeg processes
        for name, proc in [
            ("input", self._input_transcoder),
            ("output", self._output_transcoder),
        ]:
            if proc:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning(f"FFmpeg {name} did not exit within 5s")
                except Exception:
                    pass

        # Close WS
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass

        for t in self._tasks:
            t.cancel()

        logger.info("ElevenLabsProvider stopped")

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "provider": "elevenlabs",
            "audio_chunks_sent": self.audio_chunks_sent,
            "audio_chunks_received": self.audio_chunks_received,
            "total_bytes_rx": self.total_bytes_rx,
            "total_bytes_tx": self.total_bytes_tx,
            "transcripts_received": self.transcripts_received,
        }

    # ------------------------------------------------------------------
    # Internal: connection
    # ------------------------------------------------------------------

    async def _start_input_transcoder(self):
        """FFmpeg: WebM stdin -> raw PCM16 LE 16kHz mono stdout."""
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

    async def _start_output_transcoder(self):
        """FFmpeg: raw PCM16 LE 16kHz mono stdin -> Ogg/Opus stdout (for browser)."""
        self._output_transcoder = await asyncio.create_subprocess_exec(
            self.ffmpeg_path,
            "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "24k",
            "-application", "lowdelay",
            "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info(f"ElevenLabs output transcoder started (PID={self._output_transcoder.pid})")

    async def _connect_ws(self):
        """Connect to ElevenLabs Conversational AI WebSocket."""
        ws_url = (
            f"wss://api.elevenlabs.io/v1/convai/conversation"
            f"?agent_id={self.agent_id}"
        )

        logger.info(f"Connecting to ElevenLabs: {ws_url}")
        self.ws = await websockets.connect(
            ws_url,
            additional_headers={"xi-api-key": self.api_key},
            ping_interval=20,
            close_timeout=5,
        )

        # Wait for conversation initiation metadata
        try:
            init_msg = await asyncio.wait_for(self.ws.recv(), 15.0)
            data = json.loads(init_msg)
            if data.get("type") == "conversation_initiation_metadata":
                conv_id = data.get("conversation_initiation_metadata_event", {}).get(
                    "conversation_id", "unknown"
                )
                logger.info(f"[ElevenLabs] Connected, conversation_id={conv_id}")
            else:
                logger.warning(f"[ElevenLabs] Unexpected init message: {data.get('type')}")
        except asyncio.TimeoutError:
            logger.error("[ElevenLabs] No init metadata after 15s")
            await self.ws.close()
            raise ConnectionError("ElevenLabs connection init timeout")
        except Exception as e:
            logger.error(f"[ElevenLabs] Init error: {e}")
            raise ConnectionError(f"ElevenLabs connection failed: {e}")

    # ------------------------------------------------------------------
    # Internal: streaming tasks
    # ------------------------------------------------------------------

    async def _pump_pcm_to_ws(self):
        """Read PCM16 from input FFmpeg, base64-encode, send to ElevenLabs WS."""
        logger.info("ElevenLabs: input pump started")
        try:
            while self.is_running and self.ws:
                # Read ~100ms of 16kHz mono PCM16 (3200 bytes)
                pcm_chunk = await self._input_transcoder.stdout.read(3200)
                if not pcm_chunk:
                    logger.warning("ElevenLabs: input FFmpeg stdout EOF")
                    break

                b64_audio = base64.b64encode(pcm_chunk).decode("ascii")
                msg = json.dumps({"user_audio_chunk": b64_audio})
                await self.ws.send(msg)

                self.audio_chunks_sent += 1
                self.total_bytes_tx += len(pcm_chunk)

        except Exception as e:
            logger.error(f"ElevenLabs input pump error: {e}")
            logger.error(traceback.format_exc())
        finally:
            logger.info(f"ElevenLabs: input pump exited (chunks_sent={self.audio_chunks_sent})")

    async def _pump_ws_receiver(
        self,
        on_transcript: OnTranscriptCallback,
        on_error: OnErrorCallback,
    ):
        """Read JSON events from ElevenLabs WS, dispatch audio + transcripts."""
        logger.info("ElevenLabs: WS receiver started")
        try:
            while self.is_running and self.ws:
                raw = await self.ws.recv()
                data = json.loads(raw)
                event_type = data.get("type", "")

                if event_type == "audio":
                    # Audio chunk — decode and write to output FFmpeg
                    audio_event = data.get("audio_event", {})
                    b64_audio = audio_event.get("audio_base_64", "")
                    if b64_audio:
                        pcm_bytes = base64.b64decode(b64_audio)
                        self.audio_chunks_received += 1
                        self.total_bytes_rx += len(pcm_bytes)

                        if self._output_transcoder and self._output_transcoder.stdin:
                            self._output_transcoder.stdin.write(pcm_bytes)
                            await self._output_transcoder.stdin.drain()

                elif event_type == "user_transcript":
                    transcript_event = data.get("user_transcription_event", {})
                    text = transcript_event.get("user_transcript", "")
                    if text:
                        self.transcripts_received += 1
                        await on_transcript(
                            TranscriptEvent(text=text, role="user", is_final=True)
                        )

                elif event_type == "agent_response":
                    response_event = data.get("agent_response_event", {})
                    text = response_event.get("agent_response", "")
                    if text:
                        self.transcripts_received += 1
                        await on_transcript(
                            TranscriptEvent(text=text, role="agent", is_final=True)
                        )

                elif event_type == "interruption":
                    logger.info("[ElevenLabs] Barge-in detected")

                elif event_type == "ping":
                    # Respond to keep-alive
                    pong = json.dumps({"type": "pong", "event_id": data.get("ping_event", {}).get("event_id")})
                    await self.ws.send(pong)

                elif event_type == "conversation_initiation_metadata":
                    # Already handled in connect, ignore duplicates
                    pass

                else:
                    logger.debug(f"[ElevenLabs] Unhandled event: {event_type}")

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"ElevenLabs WS closed: {e}")
        except Exception as e:
            logger.error(f"ElevenLabs WS receiver error: {e}")
            logger.error(traceback.format_exc())
            await on_error(e)
        finally:
            logger.info(
                f"ElevenLabs: WS receiver exited "
                f"(audio_rx={self.audio_chunks_received} transcripts={self.transcripts_received})"
            )

    async def _pump_output_to_browser(self, on_audio: OnAudioCallback):
        """Read Ogg/Opus from output FFmpeg, forward to browser via callback."""
        logger.info("ElevenLabs: output pump started")
        ogg_chunks_sent = 0
        try:
            while self.is_running:
                chunk = await self._output_transcoder.stdout.read(4096)
                if not chunk:
                    logger.warning("ElevenLabs: output FFmpeg stdout EOF")
                    break

                # Tag with 0x01 for browser compatibility (same as PersonaPlex)
                tagged_frame = b"\x01" + chunk
                await on_audio(tagged_frame)

                ogg_chunks_sent += 1
                if ogg_chunks_sent <= 5:
                    logger.info(
                        f"[ElevenLabs AUDIO_TX #{ogg_chunks_sent}] "
                        f"ogg size={len(chunk)} header={chunk[:4].hex()}"
                    )

        except Exception as e:
            logger.error(f"ElevenLabs output pump error: {e}")
            logger.error(traceback.format_exc())
        finally:
            logger.info(f"ElevenLabs: output pump exited (ogg_chunks={ogg_chunks_sent})")
