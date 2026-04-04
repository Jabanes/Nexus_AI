"""
PersonaPlex Voice Provider — NVIDIA GPU-accelerated audio processing.

Extracted from the original AudioBridge in src/core/audio/streamer.py.
Implements IVoiceProvider for the NVIDIA PersonaPlex sidecar container.

Wire protocol:
  0x00 = handshake / keepalive
  0x01 = audio (Ogg/Opus pages)
  0x02 = text (UTF-8 transcript)
"""

import asyncio
import logging
import os
import time
import traceback
import urllib.parse
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


class PersonaPlexProvider(IVoiceProvider):
    """
    Voice provider for NVIDIA PersonaPlex running as an external Docker sidecar.

    Requires FFmpeg for WebM -> Ogg/Opus transcoding on the input path.
    Output audio (Ogg pages from PersonaPlex) is forwarded as-is.
    """

    def __init__(self, config: VoiceProviderConfig):
        super().__init__(config)

        self.ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg")
        self.model_url = (
            config.provider_specific.get("ws_url")
            or os.getenv("PERSONAPLEX_WS_URL", "ws://localhost:9000")
        )

        self.model_ws: Optional[Any] = None
        self.is_running = False
        self.input_transcoder: Optional[asyncio.subprocess.Process] = None
        self._tasks: list = []

        # Diagnostic counters
        self.tag_counts: Dict[Any, int] = {0: 0, 1: 0, 2: 0, "unknown": 0}
        self.total_audio_bytes_rx = 0
        self.total_audio_bytes_tx = 0
        self.client_chunks_received = 0
        self.opus_packets_sent = 0
        self._last_diag_time = time.monotonic()

        logger.info(f"PersonaPlexProvider initialized (url={self.model_url})")

    # ------------------------------------------------------------------
    # IVoiceProvider interface
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to PersonaPlex WS and start FFmpeg transcoder."""
        await self._connect_model()
        await self._start_transcoder()

    async def send_audio(self, data: bytes) -> None:
        """Write browser WebM audio to FFmpeg stdin."""
        self.client_chunks_received += 1
        if self.input_transcoder and self.input_transcoder.stdin:
            self.input_transcoder.stdin.write(data)
            await self.input_transcoder.stdin.drain()

    async def run(
        self,
        on_audio: OnAudioCallback,
        on_transcript: OnTranscriptCallback,
        on_error: OnErrorCallback,
    ) -> None:
        """Run the two internal streaming tasks until one completes."""
        self.is_running = True

        task_transcoder = asyncio.create_task(
            self._pump_transcoder_to_model(), name="pp_transcoder_output"
        )
        task_model = asyncio.create_task(
            self._pump_model_output(on_audio, on_transcript, on_error),
            name="pp_model_output",
        )
        self._tasks = [task_transcoder, task_model]

        # Audio watchdog
        async def _watchdog():
            await asyncio.sleep(5)
            if self.tag_counts[1] == 0:
                logger.error("[PersonaPlex WATCHDOG] NO AUDIO RECEIVED AFTER 5 SECONDS")

        asyncio.create_task(_watchdog())

        logger.info("PersonaPlexProvider: streaming tasks launched")

        done, pending = await asyncio.wait(
            self._tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for t in done:
            exc = t.exception() if not t.cancelled() else None
            logger.warning(f"PersonaPlex FIRST_COMPLETED: task={t.get_name()} exception={exc}")

    async def stop(self) -> None:
        logger.info("PersonaPlexProvider stopping...")
        self.is_running = False

        # Kill FFmpeg
        if self.input_transcoder:
            try:
                self.input_transcoder.kill()
                await asyncio.wait_for(self.input_transcoder.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("FFmpeg process did not exit within 5s after kill")
            except Exception:
                pass
            # Capture FFmpeg stderr
            try:
                if self.input_transcoder.stderr:
                    stderr_output = await asyncio.wait_for(
                        self.input_transcoder.stderr.read(), timeout=2.0
                    )
                    if stderr_output:
                        logger.warning(f"FFmpeg stderr: {stderr_output.decode(errors='replace')}")
            except Exception:
                pass

        # Close PersonaPlex WS
        if self.model_ws:
            try:
                await self.model_ws.close()
            except Exception:
                pass

        # Cancel remaining tasks
        for t in self._tasks:
            t.cancel()

        logger.info("PersonaPlexProvider stopped")

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "provider": "nvidia_personaplex",
            "tag_counts": dict(self.tag_counts),
            "audio_bytes_rx": self.total_audio_bytes_rx,
            "audio_bytes_tx": self.total_audio_bytes_tx,
            "client_chunks_received": self.client_chunks_received,
            "opus_packets_sent": self.opus_packets_sent,
        }

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    async def _connect_model(self):
        """Connect to PersonaPlex WS and wait for 0x00 handshake."""
        params = {
            "voice_prompt": self.config.voice_id or "NATF0.pt",
            "text_prompt": self.config.system_prompt or "System",
        }
        query = urllib.parse.urlencode(params)
        base_url = self.model_url.rstrip("/").replace("/api/chat", "") + "/api/chat"
        ws_url = f"{base_url}?{query}"

        logger.info(f"Connecting to PersonaPlex: {ws_url[:200]}...")
        self.model_ws = await websockets.connect(
            ws_url, ping_interval=None, close_timeout=5
        )
        logger.info("[PersonaPlex] CONNECTED")

        # Wait for 0x00 handshake (GPU prompt loading — can take 15-60s)
        logger.info("[PersonaPlex] Waiting for handshake...")
        try:
            msg = await asyncio.wait_for(self.model_ws.recv(), 60.0)
            if isinstance(msg, bytes) and msg == b"\x00":
                logger.info("[PersonaPlex] HANDSHAKE RECEIVED — ready for audio")
            elif isinstance(msg, bytes):
                logger.warning(f"[PersonaPlex] Unexpected first message: 0x{msg.hex()}")
            else:
                logger.warning(f"[PersonaPlex] Unexpected text message: {repr(msg[:200])}")
        except asyncio.TimeoutError:
            logger.error("[PersonaPlex] NO HANDSHAKE AFTER 60s")
            await self.model_ws.close()
            raise ConnectionError("PersonaPlex handshake timeout after 60s")
        except Exception as e:
            logger.error(f"[PersonaPlex] Handshake error: {e}")
            raise

    async def _start_transcoder(self):
        """Start FFmpeg: WebM stdin -> Ogg/Opus stdout (24kHz mono)."""
        logger.info(f"Starting FFmpeg: {self.ffmpeg_path}")
        self.input_transcoder = await asyncio.create_subprocess_exec(
            self.ffmpeg_path,
            "-hide_banner", "-loglevel", "error",
            "-f", "webm", "-i", "pipe:0",
            "-af", "volume=15dB", "-ar", "24000", "-ac", "1",
            "-c:a", "libopus", "-b:a", "24k",
            "-application", "lowdelay", "-frame_duration", "60",
            "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info(f"FFmpeg input transcoder started (PID={self.input_transcoder.pid})")

    async def _pump_transcoder_to_model(self):
        """Read FFmpeg stdout (Ogg chunks) and forward to PersonaPlex WS."""
        logger.info("PersonaPlex: transcoder->model pump started")
        try:
            while self.is_running and self.model_ws:
                chunk = await self.input_transcoder.stdout.read(4096)
                if not chunk:
                    logger.warning("FFmpeg stdout EOF")
                    break
                self.opus_packets_sent += 1
                if self.opus_packets_sent <= 5:
                    logger.info(
                        f"[MODEL_TX #{self.opus_packets_sent}] "
                        f"ogg_chunk size={len(chunk)} header={chunk[:4].hex()}"
                    )
                await self.model_ws.send(b"\x01" + chunk)
        except Exception as e:
            logger.error(f"PersonaPlex transcoder->model pump error: {e}")
            logger.error(traceback.format_exc())
        finally:
            logger.info(f"PersonaPlex: transcoder->model pump exited (chunks_tx={self.opus_packets_sent})")

    async def _pump_model_output(
        self,
        on_audio: OnAudioCallback,
        on_transcript: OnTranscriptCallback,
        on_error: OnErrorCallback,
    ):
        """Read PersonaPlex WS and dispatch via callbacks."""
        logger.info("PersonaPlex: model->client pump started")
        _debug_rx_count = 0

        try:
            while self.is_running and self.model_ws:
                msg = await self.model_ws.recv()

                # Log first 10 raw frames
                _debug_rx_count += 1
                if _debug_rx_count <= 10:
                    if isinstance(msg, bytes):
                        logger.info(
                            f"[PersonaPlex RAW RX #{_debug_rx_count}] "
                            f"bytes={len(msg)} hex={msg[:32].hex()}"
                        )
                    elif isinstance(msg, str):
                        logger.info(f"[PersonaPlex RAW RX #{_debug_rx_count}] TEXT={msg}")

                self._maybe_log_diagnostics()

                if isinstance(msg, bytes):
                    if len(msg) == 0:
                        continue

                    tag = msg[0]
                    payload = msg[1:] if len(msg) > 1 else b""

                    logger.debug(
                        f"[PersonaPlex FRAME] tag=0x{tag:02x} "
                        f"payload_size={len(payload)}"
                    )

                    if tag == 0:
                        self.tag_counts[0] += 1
                        logger.debug(f"[PersonaPlex KEEPALIVE] size={len(msg)}")

                    elif tag == 1:
                        # Audio — Ogg pages forwarded as-is
                        self.tag_counts[1] += 1
                        if self.tag_counts[1] == 1:
                            logger.info("[PersonaPlex] FIRST AUDIO RECEIVED")

                        self.total_audio_bytes_rx += len(payload)
                        self.total_audio_bytes_tx += len(payload)

                        tagged_frame = b"\x01" + payload
                        await on_audio(tagged_frame)

                        if self.tag_counts[1] <= 10:
                            logger.info(
                                f"[AUDIO_TX] tag=0x01 size={len(payload)} "
                                f"header={payload[:4].hex()}"
                            )

                    elif tag == 2:
                        # Text transcript
                        self.tag_counts[2] += 1
                        try:
                            text = payload.decode("utf-8")
                            await on_transcript(
                                TranscriptEvent(text=text, role="agent", is_final=True)
                            )
                        except Exception as e:
                            logger.error(f"Text decode error: {e}")

                    else:
                        self.tag_counts["unknown"] += 1
                        logger.warning(f"Unknown tag 0x{tag:02x}, len={len(payload)}")

                elif isinstance(msg, str):
                    logger.info(f"Text frame from PersonaPlex: {msg[:200]}")

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"PersonaPlex WS closed: {e}")
        except Exception as e:
            logger.error(f"PersonaPlex model->client pump error: {e}")
            logger.error(traceback.format_exc())
        finally:
            self._force_log_diagnostics()
            logger.info(
                f"PersonaPlex: model->client pump exited "
                f"(tags: 0x00={self.tag_counts[0]} 0x01={self.tag_counts[1]} "
                f"0x02={self.tag_counts[2]})"
            )

    def _maybe_log_diagnostics(self):
        """Log diagnostic summary every ~2 seconds."""
        now = time.monotonic()
        if now - self._last_diag_time < 2.0:
            return
        self._last_diag_time = now

        avg_payload = (
            self.total_audio_bytes_rx // self.tag_counts[1]
            if self.tag_counts[1] > 0
            else 0
        )
        logger.info(
            f"[AUDIO DIAG] rx={self.total_audio_bytes_rx} "
            f"tx={self.total_audio_bytes_tx} pages={self.tag_counts[1]} "
            f"avg={avg_payload}"
        )

    def _force_log_diagnostics(self):
        self._last_diag_time = 0
        self._maybe_log_diagnostics()
