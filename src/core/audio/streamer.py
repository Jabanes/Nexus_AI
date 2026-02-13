"""
Audio Bridge - Manages dual WebSocket connections for real-time audio streaming.
Architecture Pattern: DUAL PERSISTENT TRANSCODING
"""
import asyncio
import logging
import os
import urllib.parse
from typing import Optional, Dict, Any
from enum import Enum
import websockets
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)

class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSED = "closed"

class AudioBridge:
    def __init__(
        self,
        client_ws: WebSocket,
        tenant_id: str,
        session_id: str,
        conversation_session_id: str,
        system_prompt: str,
        voice_settings: Dict[str, Any],
        session_recorder: Optional[Any] = None
    ):
        self.client_ws = client_ws
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.conversation_session_id = conversation_session_id
        self.system_prompt = system_prompt
        self.voice_settings = voice_settings
        self.recorder = session_recorder
        
        # Audio Config
        self.ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg")
        self.model_url = os.getenv("PERSONAPLEX_WS_URL", "ws://localhost:9000")
        
        # State
        self.model_ws = None
        self.is_running = False
        
        # TRANSCODERS
        self.input_transcoder = None   # Client (WebM) -> Moshi (Opus 24k)
        self.output_transcoder = None  # Moshi (Opus) -> Client (PCM 24k)
        
        self.tasks = []
        
        logger.info(f"AudioBridge initialized: session={session_id}")

    async def connect_model(self):
        """Connect to PersonaPlex"""
        try:
            params = {
                "text_prompt": self.system_prompt,
                "voice_prompt": self.voice_settings.get("voice_id", "mimi_voice_0")
            }
            query = urllib.parse.urlencode(params)
            
            base_url = self.model_url.rstrip("/")
            if not base_url.endswith("/api/chat"):
                base_url += "/api/chat"
            
            ws_url = f"{base_url}?{query}"
            logger.info(f"Connecting to: {ws_url}")

            self.model_ws = await websockets.connect(
                ws_url, ping_interval=None, close_timeout=5
            )
            logger.info("✅ Connected to PersonaPlex")
            
        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
            raise

    async def start_transcoders(self):
        """Start BOTH FFmpeg processes"""
        try:
            # 1. INPUT: WebM -> Opus (24kHz forced for Moshi)
            self.input_transcoder = await asyncio.create_subprocess_exec(
                self.ffmpeg_path,
                "-hide_banner", "-loglevel", "error",
                "-f", "webm", "-i", "pipe:0",
                "-ar", "24000", "-ac", "1",       # FORCE 24kHz
                "-c:a", "libopus", "-b:a", "24k",
                "-f", "ogg", "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # 2. OUTPUT: Opus -> PCM (24kHz s16le for Browser)
            self.output_transcoder = await asyncio.create_subprocess_exec(
                self.ffmpeg_path,
                "-hide_banner", "-loglevel", "error",
                "-f", "ogg", "-i", "pipe:0",   # Input from Moshi
                "-f", "s16le",                 # Output raw PCM
                "-ar", "24000",                # 24kHz
                "-ac", "1",                    # Mono
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            logger.info("🔊 Dual FFmpeg transcoders started")
        except Exception as e:
            logger.error(f"Failed to start transcoders: {e}")
            raise

    async def handle_client_input(self):
        """WebM (Browser) -> Input Transcoder"""
        try:
            while self.is_running:
                msg = await self.client_ws.receive()
                
                if "bytes" in msg:
                    data = msg["bytes"]
                    if not data: continue
                    # Pipe to FFmpeg Input
                    if self.input_transcoder and self.input_transcoder.stdin:
                        self.input_transcoder.stdin.write(data)
                        await self.input_transcoder.stdin.drain()
                        
                elif "type" in msg and msg["type"] == "websocket.disconnect":
                    self.is_running = False
                    break
        except Exception as e:
            logger.error(f"Input loop error: {e}")

    async def handle_input_transcoder_output(self):
        """Input Transcoder -> Moshi"""
        try:
            while self.is_running and self.model_ws:
                chunk = await self.input_transcoder.stdout.read(4096)
                if not chunk: break
                # Send to Moshi with Prefix
                await self.model_ws.send(b'\x01' + chunk)
        except Exception as e:
            logger.error(f"Input pipe error: {e}")

    async def handle_model_output(self):
        """Moshi -> Output Transcoder OR Transcript"""
        try:
            while self.is_running and self.model_ws:
                msg = await self.model_ws.recv()
                
                if isinstance(msg, bytes) and len(msg) > 1:
                    kind = msg[0]
                    payload = msg[1:]
                    
                    if kind == 1: # Audio (Opus)
                        # Pipe to Output Transcoder
                        if self.output_transcoder and self.output_transcoder.stdin:
                            self.output_transcoder.stdin.write(payload)
                            await self.output_transcoder.stdin.drain()
                            
                    elif kind == 2: # Text
                        try:
                            text = payload.decode('utf-8')
                            # Send live text to client
                            await self.client_ws.send_json({
                                "type": "transcript", 
                                "content": text,
                                "sender": "assistant"
                            })
                            # Record for history
                            if self.recorder:
                                self.recorder.add_message("assistant", text)
                        except: pass
                        
        except Exception as e:
            logger.error(f"Model output loop error: {e}")

    async def handle_output_transcoder_output(self):
        """Output Transcoder (PCM) -> Client"""
        try:
            while self.is_running:
                if not self.output_transcoder or not self.output_transcoder.stdout:
                    await asyncio.sleep(0.1)
                    continue

                # Read PCM chunks
                chunk = await self.output_transcoder.stdout.read(4096)
                if not chunk: break
                
                # Send PCM to Client
                await self.client_ws.send_bytes(chunk)
        except Exception as e:
            logger.error(f"Output pipe error: {e}")

    async def process_stream(self):
        try:
            await self.connect_model()
            await self.start_transcoders()
            self.is_running = True
            
            self.tasks = [
                asyncio.create_task(self.handle_client_input()),           # Browser -> FFmpeg1
                asyncio.create_task(self.handle_input_transcoder_output()),# FFmpeg1 -> Moshi
                asyncio.create_task(self.handle_model_output()),           # Moshi -> FFmpeg2
                asyncio.create_task(self.handle_output_transcoder_output())# FFmpeg2 -> Browser
            ]
            
            await asyncio.wait(self.tasks, return_when=asyncio.FIRST_COMPLETED)
            
        finally:
            await self.stop()

    async def stop(self):
        self.is_running = False
        # Kill FFmpegs
        if self.input_transcoder:
            try: self.input_transcoder.kill()
            except: pass
        if self.output_transcoder:
            try: self.output_transcoder.kill()
            except: pass
            
        if self.model_ws:
            await self.model_ws.close()
            
        # Cancel tasks
        for t in self.tasks: t.cancel()
        logger.info("AudioBridge Stopped")