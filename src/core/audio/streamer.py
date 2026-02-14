"""
Audio Bridge - With full lifecycle diagnostics.

Every task logs entry/exit/error. The FIRST_COMPLETED task is identified.
"""
import asyncio
import logging
import os
import time
import traceback
import urllib.parse
import json
from typing import Optional, Dict, Any
import websockets
from fastapi import WebSocket

from src.core.audio.ogg_parser import OggPageParser

logger = logging.getLogger(__name__)


class AudioBridge:
    def __init__(self, client_ws: WebSocket, tenant_id: str, session_id: str,
                 conversation_session_id: str, system_prompt: str,
                 voice_settings: Dict[str, Any],
                 session_recorder: Optional[Any] = None):
        self.client_ws = client_ws
        self.session_id = session_id
        self.system_prompt = system_prompt
        self.voice_settings = voice_settings
        self.recorder = session_recorder

        self.ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg")
        self.model_url = os.getenv("PERSONAPLEX_WS_URL", "ws://localhost:9000")

        self.model_ws = None
        self.is_running = False

        # Input pipeline: Browser WebM -> FFmpeg -> Ogg -> OggParser -> raw Opus -> Server
        self.input_transcoder = None
        self.input_ogg_parser = OggPageParser()

        # Output pipeline: Server Ogg -> OggParser -> raw Opus -> Browser
        self.output_ogg_parser = OggPageParser()

        self.tasks = []

        # Diagnostic counters
        self.tag_counts = {0: 0, 1: 0, 2: 0, 'unknown': 0}
        self.total_audio_bytes_rx = 0
        self.total_pcm_bytes_tx = 0
        self.decode_errors = 0
        self.last_diag_time = time.monotonic()
        self.client_chunks_received = 0
        self.opus_packets_sent = 0

        logger.info(f"AudioBridge initialized: session={session_id}")

    # ---------------------------------------------------------------
    # CONNECTION
    # ---------------------------------------------------------------
    async def connect_model(self):
        """Connect with URL Params, wait for 0x00 handshake."""
        params = {
            "voice_prompt": self.voice_settings.get("voice_id", "NATF0.pt"),
            "text_prompt": self.system_prompt or "System",
        }
        query = urllib.parse.urlencode(params)
        base_url = self.model_url.rstrip("/").replace("/api/chat", "") + "/api/chat"
        ws_url = f"{base_url}?{query}"

        logger.info(f"Connecting to: {ws_url[:200]}...")
        self.model_ws = await websockets.connect(
            ws_url, ping_interval=None, close_timeout=5
        )
        logger.info("[PersonaPlex WS] CONNECTED SUCCESSFULLY")
        logger.info(f"[PersonaPlex WS] URL = {ws_url}")
        logger.info(f"[PersonaPlex WS] STATE = {self.model_ws.state}")

        # Wait for 0x00 Handshake
        # PersonaPlex processes system prompts (voice + text) before sending 0x00.
        # This can take 15-30+ seconds depending on GPU. We MUST wait — sending
        # audio before handshake causes is_alive() to consume and discard our
        # Ogg stream headers, corrupting the input pipeline.
        logger.info("[PersonaPlex] Waiting for handshake (system prompts loading on GPU)...")
        try:
            msg = await asyncio.wait_for(self.model_ws.recv(), 60.0)
            if isinstance(msg, bytes) and msg == b'\x00':
                logger.info("[PersonaPlex] HANDSHAKE RECEIVED — ready for audio")
            elif isinstance(msg, bytes):
                logger.warning(f"[PersonaPlex] Unexpected first message: 0x{msg.hex()} (len={len(msg)})")
            else:
                logger.warning(f"[PersonaPlex] Unexpected text message: {repr(msg[:200])}")
        except asyncio.TimeoutError:
            logger.error("[PersonaPlex] NO HANDSHAKE AFTER 60s — aborting connection")
            await self.model_ws.close()
            raise ConnectionError("PersonaPlex handshake timeout after 60s")
        except Exception as e:
            logger.error(f"[PersonaPlex] Handshake read error: {e}")
            raise

    # ---------------------------------------------------------------
    # INPUT PIPELINE (Mic -> Server)
    # ---------------------------------------------------------------
    async def start_transcoders(self):
        """Start FFmpeg Pipeline (Input Only)."""
        logger.info(f"Starting FFmpeg: {self.ffmpeg_path}")
        self.input_transcoder = await asyncio.create_subprocess_exec(
            self.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "webm", "-i", "pipe:0",
            "-af", "volume=15dB", "-ar", "24000", "-ac", "1",
            "-c:a", "libopus", "-b:a", "24k",
            "-application", "lowdelay", "-frame_duration", "60",
            "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        logger.info("🔊 Input transcoder started (PID=%s)", self.input_transcoder.pid)

    async def handle_client_input(self):
        """WebM chunks from browser -> FFmpeg stdin."""
        logger.info("▶ TASK handle_client_input: STARTED")
        try:
            while self.is_running:
                msg = await self.client_ws.receive()
                if "bytes" in msg:
                    data = msg["bytes"]
                    self.client_chunks_received += 1
                    if self.input_transcoder and self.input_transcoder.stdin:
                        self.input_transcoder.stdin.write(data)
                        await self.input_transcoder.stdin.drain()
                elif "type" in msg and msg["type"] == "websocket.disconnect":
                    logger.info("Client sent disconnect message")
                    self.is_running = False
                    break
                elif "text" in msg:
                    # Browser might send JSON text (e.g., control messages)
                    logger.debug(f"Client text: {msg['text'][:100]}")
        except Exception as e:
            logger.error(f"⬛ TASK handle_client_input ERROR: {e}")
            logger.error(traceback.format_exc())
        finally:
            logger.info(f"⬛ TASK handle_client_input: EXITED (chunks_rx={self.client_chunks_received})")

    async def handle_input_transcoder_output(self):
        """FFmpeg stdout (Ogg) -> direct forward to PersonaPlex."""
        logger.info("▶ TASK handle_input_transcoder_output: STARTED")
        try:
            while self.is_running and self.model_ws:
                chunk = await self.input_transcoder.stdout.read(4096)
                if not chunk:
                    logger.warning("FFmpeg stdout returned empty — EOF")
                    break
                self.opus_packets_sent += 1
                if self.opus_packets_sent <= 5:
                    logger.info(
                        f"[MODEL_TX #{self.opus_packets_sent}] "
                        f"ogg_chunk size={len(chunk)} header={chunk[:4].hex()}"
                    )
                await self.model_ws.send(b'\x01' + chunk)
        except Exception as e:
            logger.error(f"⬛ TASK handle_input_transcoder_output ERROR: {e}")
            logger.error(traceback.format_exc())
        finally:
            logger.info(f"⬛ TASK handle_input_transcoder_output: EXITED (chunks_tx={self.opus_packets_sent})")

    # ---------------------------------------------------------------
    # OUTPUT PIPELINE (Server -> Browser)
    # ---------------------------------------------------------------
    async def handle_model_output(self):
        """Receive binary frames from PersonaPlex, decode, forward PCM."""
        logger.info("▶ TASK handle_model_output: STARTED")
        try:
            while self.is_running and self.model_ws:
                msg = await self.model_ws.recv()

                # --- PART 2: Log first 10 raw frames ---
                if not hasattr(self, '_debug_rx_count'):
                    self._debug_rx_count = 0
                self._debug_rx_count += 1
                if self._debug_rx_count <= 10:
                    if isinstance(msg, bytes):
                        logger.info(
                            f"[PersonaPlex RAW RX #{self._debug_rx_count}] "
                            f"bytes={len(msg)} hex={msg[:32].hex()}"
                        )
                    elif isinstance(msg, str):
                        logger.info(
                            f"[PersonaPlex RAW RX #{self._debug_rx_count}] TEXT={msg}"
                        )

                # Periodic diagnostics
                self._maybe_log_diagnostics()

                if isinstance(msg, bytes):
                    if len(msg) == 0:
                        continue

                    tag = msg[0]
                    payload = msg[1:] if len(msg) > 1 else b''

                    # --- PART 3: Log tag and payload analysis ---
                    logger.info(
                        f"[PersonaPlex FRAME] "
                        f"tag=0x{tag:02x} "
                        f"payload_size={len(payload)} "
                        f"header={payload[:4].hex() if payload else 'none'}"
                    )

                    if tag == 0:
                        self.tag_counts[0] += 1
                        # --- PART 5: Log keepalive ---
                        logger.info(
                            f"[PersonaPlex KEEPALIVE] size={len(msg)} hex={msg.hex()}"
                        )

                    elif tag == 1:
                        # Audio: PersonaPlex sends OGG PAGES here.
                        # We MUST forward them unmodified to the browser which expects Ogg container.
                        self.tag_counts[1] += 1

                        if self.tag_counts[1] == 1:
                            logger.info("[PersonaPlex] FIRST AUDIO RECEIVED")

                        self.total_audio_bytes_rx += len(payload)
                        self.total_pcm_bytes_tx += len(payload)  # Track bytes forwarded

                        # --- PART 4: Log audio detection ---
                        logger.info(
                            f"[PersonaPlex AUDIO DETECTED] "
                            f"ogg_header={payload[:4].hex()} "
                            f"size={len(payload)}"
                        )

                        logger.info(
                            f"[AUDIO RX] ogg_page size={len(payload)} total_rx={self.total_audio_bytes_rx} count={self.tag_counts[1]}"
                        )

                        # FORWARD WITH 0x01 TAG — NVIDIA wire protocol
                        tagged_frame = b"\x01" + payload
                        try:
                            await self.client_ws.send_bytes(tagged_frame)
                        except RuntimeError:
                            logger.warning("[BROWSER_TX] Client WS already closed, stopping output")
                            self.is_running = False
                            break

                        # Log first 10 frames sent to browser
                        if self.tag_counts[1] <= 10:
                            logger.info(
                                f"[BROWSER_TX] tag=0x01 size={len(payload)} header={payload[:4].hex()}"
                            )

                        logger.info(
                            f"[AUDIO TX → BROWSER] ogg_page size={len(payload)} total_tx={self.total_pcm_bytes_tx}"
                        )

                    elif tag == 2:
                        self.tag_counts[2] += 1
                        try:
                            text = payload.decode('utf-8')
                            try:
                                await self.client_ws.send_json({
                                    "type": "transcript",
                                    "content": text
                                })
                            except RuntimeError:
                                logger.warning("[TEXT_TX] Client WS already closed, stopping output")
                                self.is_running = False
                                break
                        except Exception as e:
                            logger.error(f"Text decode/send error: {e}")

                    else:
                        self.tag_counts['unknown'] += 1
                        logger.warning(f"Unknown tag 0x{tag:02x}, payload len={len(payload)}")

                elif isinstance(msg, str):
                    logger.info(f"Text frame from server: {msg[:200]}")

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"⬛ TASK handle_model_output: server closed connection: {e}")
        except Exception as e:
            logger.error(f"⬛ TASK handle_model_output ERROR: {e}")
            logger.error(traceback.format_exc())
        finally:
            logger.info(
                f"⬛ TASK handle_model_output: EXITED "
                f"(tags: 0x00={self.tag_counts[0]} 0x01={self.tag_counts[1]} "
                f"0x02={self.tag_counts[2]})"
            )

    def _maybe_log_diagnostics(self):
        """Log diagnostic summary every ~2 seconds."""
        now = time.monotonic()
        elapsed = now - self.last_diag_time
        if elapsed < 2.0:
            return
        self.last_diag_time = now

        avg_payload = (
            self.total_audio_bytes_rx // self.tag_counts[1]
            if self.tag_counts[1] > 0 else 0
        )

        logger.info(
            f"[AUDIO DIAG] "
            f"rx_bytes={self.total_audio_bytes_rx} "
            f"tx_bytes={self.total_pcm_bytes_tx} "
            f"pages={self.tag_counts[1]} "
            f"avg_page={avg_payload}"
        )

    # ---------------------------------------------------------------
    # LIFECYCLE
    # ---------------------------------------------------------------
    async def process_stream(self):
        logger.info("🚀 process_stream: STARTING")
        try:
            await self.connect_model()
            await self.start_transcoders()

            self.is_running = True

            task_client = asyncio.create_task(
                self.handle_client_input(), name="client_input"
            )
            task_transcoder = asyncio.create_task(
                self.handle_input_transcoder_output(), name="transcoder_output"
            )
            task_model = asyncio.create_task(
                self.handle_model_output(), name="model_output"
            )
            self.tasks = [task_client, task_transcoder, task_model]

            # --- PART 6: Audio watchdog ---
            async def audio_watchdog():
                await asyncio.sleep(5)
                if self.tag_counts[1] == 0:
                    logger.error(
                        "[PersonaPlex WATCHDOG] NO AUDIO RECEIVED AFTER 5 SECONDS"
                    )
            asyncio.create_task(audio_watchdog())

            logger.info("🔄 All 3 streaming tasks launched. Waiting...")

            done, pending = await asyncio.wait(
                self.tasks, return_when=asyncio.FIRST_COMPLETED
            )

            # Log which task finished first (the one that killed the session)
            for t in done:
                exc = t.exception() if not t.cancelled() else None
                logger.warning(
                    f"🛑 FIRST_COMPLETED: task={t.get_name()} "
                    f"exception={exc}"
                )
            for t in pending:
                logger.info(f"   still pending: task={t.get_name()}")

        except Exception as e:
            logger.error(f"process_stream error: {e}")
            logger.error(traceback.format_exc())
        finally:
            self._force_log_diagnostics()
            await self.stop()

    def _force_log_diagnostics(self):
        """Force a final diagnostic log."""
        self.last_diag_time = 0  # Reset to force log
        self._maybe_log_diagnostics()

    async def stop(self):
        logger.info("🔻 AudioBridge stopping...")
        self.is_running = False
        if self.input_transcoder:
            try:
                self.input_transcoder.kill()
            except Exception:
                pass
        if self.model_ws:
            try:
                await self.model_ws.close()
            except Exception:
                pass
        for t in self.tasks:
            t.cancel()
        logger.info("🔻 AudioBridge stopped")