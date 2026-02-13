"""
Audio Bridge - Manages dual WebSocket connections for real-time audio streaming.

This module implements the core streaming logic that connects:
1. Client WebSocket (User's phone/browser)
2. NVIDIA PersonaPlex WebSocket (External Docker container)

Architecture Pattern: SIDECAR PROXY
- PersonaPlex runs as an external microservice (Docker container)
- This bridge acts as a proxy/transcoder between client and PersonaPlex
- Handles audio format conversion, buffering, and barge-in logic
"""
import asyncio
import logging
import os
import subprocess
from typing import Optional, Callable, TYPE_CHECKING, Dict, Any
from enum import Enum
import websockets
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

if TYPE_CHECKING:
    from src.core.history import SessionRecorder
    from src.core.orchestration.conversation_manager import ConversationManager

logger = logging.getLogger(__name__)


class AudioFormat(Enum):
    """Supported audio formats."""
    WEBM_OPUS = "webm_opus"
    WAV = "wav"
    PCM_16 = "pcm_16"  # Raw PCM 16-bit


class ConnectionState(Enum):
    """WebSocket connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    CLOSED = "closed"


class AudioBridge:
    """
    Manages bidirectional audio streaming between client and NVIDIA PersonaPlex.
    
    This class handles:
    - Dual WebSocket management (client + model)
    - Audio transcoding (client format <-> PCM for PersonaPlex)
    - Barge-in detection and handling
    - Error recovery and reconnection
    - Concurrent send/receive operations
    
    Architecture:
        [Client WS] <---> [AudioBridge] <---> [PersonaPlex WS]
                            |
                        [Transcoder]
                        [Buffer]
                        [Barge-in Handler]
    """
    
    def __init__(
        self,
        client_ws: WebSocket,
        tenant_id: str,
        session_id: str,
        conversation_session_id: str,
        system_prompt: str,
        voice_settings: Dict[str, Any],
        session_recorder: Optional['SessionRecorder'] = None
    ):
        """
        Initialize the AudioBridge.
        
        Args:
            client_ws: FastAPI WebSocket connection from client
            tenant_id: Tenant identifier for logging/routing
            session_id: Unique session identifier (WebSocket/call level)
            conversation_session_id: The ConversationManager session ID (for LLM calls)
            system_prompt: The system prompt for the persona
            voice_settings: Dict containing voice_id, language, etc.
            session_recorder: Optional SessionRecorder for capturing conversation history
        """
        self.client_ws = client_ws
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.conversation_session_id = conversation_session_id
        self.system_prompt = system_prompt
        self.voice_settings = voice_settings
        self.recorder = session_recorder
        
        # Helper to avoid circular imports if possible, or dependency injection
        from src.main import conversation_manager
        self.conversation_manager = conversation_manager
        
        # PersonaPlex connection
        self.model_ws: Optional[websockets.WebSocketClientProtocol] = None
        self.model_url = os.getenv("PERSONAPLEX_WS_URL", "ws://localhost:9000/v1/audio-stream")
        logger.info(f"AudioBridge initialized with Model URL: {self.model_url}")
        
        # Audio configuration
        self.sample_rate = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
        self.channels = int(os.getenv("AUDIO_CHANNELS", "1"))
        self.chunk_size = int(os.getenv("AUDIO_CHUNK_SIZE", "4096"))
        self.bit_depth = int(os.getenv("AUDIO_BIT_DEPTH", "16"))
        
        # State management
        self.client_state = ConnectionState.CONNECTED
        self.model_state = ConnectionState.DISCONNECTED
        self.is_running = False
        self.is_client_speaking = False
        self.is_model_speaking = False
        
        # Buffers and tasks
        self.client_buffer = asyncio.Queue(maxsize=50)
        self.model_buffer = asyncio.Queue(maxsize=50)
        self.tasks = []
        
        logger.info(
            f"AudioBridge initialized: session={session_id}, "
            f"tenant={tenant_id}, model_url={self.model_url}"
        )
    
    async def connect_model(self):
        """
        Connect to the NVIDIA PersonaPlex WebSocket (sidecar container).
        """
        timeout = int(os.getenv("PERSONAPLEX_CONNECT_TIMEOUT", "10"))
        max_attempts = int(os.getenv("PERSONAPLEX_MAX_RECONNECT_ATTEMPTS", "3"))
        retry_delay = int(os.getenv("PERSONAPLEX_RECONNECT_DELAY", "2"))
        
        try:
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(
                        f"Connecting to PersonaPlex (attempt {attempt}/{max_attempts}): "
                        f"{self.model_url}"
                    )
                    
                    self.model_state = ConnectionState.CONNECTING
                    
                    # Attempt WebSocket connection with timeout
                    # NOTE: We use ping_interval=None because PersonaPlex handles pings differently
                    self.model_ws = await asyncio.wait_for(
                        websockets.connect(
                            self.model_url,
                            ping_interval=None,
                            close_timeout=5
                        ),
                        timeout=timeout
                    )
                    
                    # --- HANDSHAKE ---
                    # PersonaPlex expects initial configuration
                    import json
                    handshake = {
                        "type": "session_config",
                        "system_prompt": self.system_prompt,
                        "voice_id": self.voice_settings.get("voice_id", "default"),
                        "language_code": self.voice_settings.get("language", "en-US")
                    }
                    await self.model_ws.send(json.dumps(handshake))
                    logger.info(f"Handshake sent to PersonaPlex: {handshake['voice_id']}")
                    
                    # Wait for server ready signal/ack if needed?
                    # For now, we assume if send succeeds, we are good.
                    
                    self.model_state = ConnectionState.CONNECTED
                    logger.info(f"Successfully connected to PersonaPlex: {self.model_url}")
                    return True
                    
                except ConnectionRefusedError:
                    logger.error(
                        f"PersonaPlex connection refused (attempt {attempt}). "
                        f"Is the Docker container running?"
                    )
                    self.model_state = ConnectionState.ERROR
                    
                except Exception as e:
                    logger.exception(f"PersonaPlex connection error (attempt {attempt}): {e}")
                    self.model_state = ConnectionState.ERROR
                
                # Wait before retry (except on last attempt)
                if attempt < max_attempts:
                    await asyncio.sleep(retry_delay)
            
            # If loop finishes, all attempts failed
            logger.warning(
                f"Failed to connect to PersonaPlex after {max_attempts} attempts. "
                "Ensure Docker container is running."
            )
            self.model_ws = None
            return False

        except Exception as e:
            logger.warning(f"Unexpected error connecting to PersonaPlex: {e}")
            logger.warning("Continuing in TEXT-ONLY mode (No TTS/Audio).")
            self.model_ws = None
            return False
            
    async def disconnect_model(self):
        """Gracefully disconnect from PersonaPlex."""
        if self.model_ws:
            try:
                await self.model_ws.close()
                logger.info("Disconnected from PersonaPlex")
            except Exception as e:
                logger.error(f"Error disconnecting from PersonaPlex: {e}")
            finally:
                self.model_ws = None
                self.model_state = ConnectionState.CLOSED
    
    async def transcode_to_pcm(self, audio_data: bytes, input_format: AudioFormat) -> bytes:
        """
        Transcode audio from client format to PCM for PersonaPlex.
        
        Args:
            audio_data: Raw audio bytes from client
            input_format: Format of input audio
            
        Returns:
            PCM audio bytes (16kHz, 16-bit, mono)
        """
        if input_format == AudioFormat.PCM_16:
            # Already in correct format
            return audio_data
        
        try:
            # Build FFmpeg command for transcoding
            if input_format == AudioFormat.WEBM_OPUS:
                input_args = ["-f", "webm", "-codec:a", "libopus"]
            elif input_format == AudioFormat.WAV:
                input_args = ["-f", "wav"]
            else:
                logger.warning(f"Unsupported input format: {input_format}, passing through")
                return audio_data
            
            # FFmpeg transcoding pipeline
            ffmpeg_cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                *input_args,
                "-i", "pipe:0",  # Input from stdin
                "-f", "s16le",  # Output format: signed 16-bit little-endian
                "-ar", str(self.sample_rate),  # Sample rate
                "-ac", str(self.channels),  # Channels
                "pipe:1"  # Output to stdout
            ]
            
            # Run FFmpeg as subprocess
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Send input and get output
            stdout, stderr = await process.communicate(input=audio_data)
            
            if process.returncode != 0:
                logger.error(f"FFmpeg transcoding failed: {stderr.decode()}")
                return b""  # Return empty on error
            
            logger.debug(f"Transcoded {len(audio_data)} bytes to {len(stdout)} bytes PCM")
            return stdout
            
        except Exception as e:
            logger.exception(f"Audio transcoding error: {e}")
            return b""
    
    async def transcode_from_pcm(self, pcm_data: bytes, output_format: AudioFormat) -> bytes:
        """
        Transcode audio from PCM to client format.
        
        Args:
            pcm_data: Raw PCM bytes from PersonaPlex
            output_format: Desired output format for client
            
        Returns:
            Transcoded audio bytes
        """
        if output_format == AudioFormat.PCM_16:
            # Already in correct format
            return pcm_data
        
        try:
            # Build FFmpeg command for encoding
            if output_format == AudioFormat.WEBM_OPUS:
                output_args = ["-f", "webm", "-codec:a", "libopus", "-b:a", "24k"]
            elif output_format == AudioFormat.WAV:
                output_args = ["-f", "wav"]
            else:
                logger.warning(f"Unsupported output format: {output_format}, passing through")
                return pcm_data
            
            # FFmpeg encoding pipeline
            ffmpeg_cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-f", "s16le",  # Input format
                "-ar", str(self.sample_rate),
                "-ac", str(self.channels),
                "-i", "pipe:0",
                *output_args,
                "pipe:1"
            ]
            
            # Run FFmpeg
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate(input=pcm_data)
            
            if process.returncode != 0:
                logger.error(f"FFmpeg encoding failed: {stderr.decode()}")
                return b""
            
            logger.debug(f"Encoded {len(pcm_data)} bytes PCM to {len(stdout)} bytes")
            return stdout
            
        except Exception as e:
            logger.exception(f"Audio encoding error: {e}")
            return b""
    
    async def handle_client_input(self):
        """
        Handle input from client (Text or Audio).
        
        For Text: Routes to ConversationManager -> TTS
        For Audio: Routes to ASR (Future) -> ConversationManager -> TTS
        """
        logger.info("Starting client input handler")
        
        try:
            while self.is_running:
                try:
                    # Receive data (Text or Bytes)
                    message = await asyncio.wait_for(
                        self.client_ws.receive(),
                        timeout=1.0
                    )
                    
                    # Check for disconnect message from ASGI layer
                    if message["type"] == "websocket.disconnect":
                        logger.info("Client sent disconnect message")
                        self.is_running = False
                        break
                    
                    elif message["type"] == "websocket.receive":
                        if "text" in message:
                            # Handle TEXT input (e.g. from Web/Mobile Client)
                            import json
                            try:
                                payload = json.loads(message["text"])
                                if payload.get("type") == "message":
                                    user_text = payload.get("content")
                                    await self.process_conversation_turn(user_text)
                            except json.JSONDecodeError:
                                logger.warning(f"Received invalid JSON text: {message['text']}")
                                
                        elif "bytes" in message:
                            # Handle AUDIO input
                            # TODO: Send to ASR service to get text
                            pass
                            
                except asyncio.TimeoutError:
                    continue
                except (WebSocketDisconnect, RuntimeError) as e:
                    # Client disconnected gracefully
                    logger.info(f"Client disconnected: {e}")
                    self.is_running = False
                    break
                except Exception as e:
                    logger.exception(f"Error in client input handler: {e}")
                    self.is_running = False
                    break
        finally:
            logger.info("Client input handler ended")

    async def process_conversation_turn(self, user_text: str):
        """
        Process a text turn through the Brain (ConversationManager).
        """
        logger.info(f"Processing turn: {user_text}")
        
        # 1. Stream response from Brain
        stream = self.conversation_manager.process_message_stream(
            self.conversation_session_id,
            user_text
        )
        
        async for chunk in stream:
            # 2. Send text back to client (for UI)
            if chunk["type"] == "text":
                await self.client_ws.send_json({
                    "type": "response_part",
                    "content": chunk["content"]
                })
                
                # 3. Send text to TTS (PersonaPlex)
                # TODO: Implement TTS streaming call here
                # await self.send_to_tts(chunk["content"])
            
            elif chunk["type"] == "error":
                await self.client_ws.send_json(chunk)

    # Replaces handle_client_to_model
    async def handle_client_to_model(self):
        return await self.handle_client_input()
    
    async def handle_model_to_client(self):
        """
        Stream audio from PersonaPlex model to client.
        
        This coroutine:
        1. Receives PCM audio from PersonaPlex
        2. Transcodes to client format
        3. Sends to client WebSocket
        4. Implements barge-in (interruption) handling
        """
        logger.info("Starting model->client audio stream")
        
        try:
            while self.is_running:
                if not self.model_ws:
                    # In text-mode, just wait for termination
                    await asyncio.sleep(1)
                    continue
                
                try:
                    # Receive PCM audio from PersonaPlex
                    pcm_data = await asyncio.wait_for(
                        self.model_ws.recv(),
                        timeout=1.0
                    )
                    
                    if not pcm_data:
                        continue
                    
                    # BARGE-IN LOGIC: Don't send model audio if client is speaking
                    if self.is_client_speaking:
                        logger.debug("Barge-in detected: Dropping model audio")
                        if self.recorder:
                            self.recorder.log_barge_in()
                        continue
                    
                    self.is_model_speaking = True
                    
                    # Transcode to client format
                    client_data = await self.transcode_from_pcm(
                        pcm_data,
                        AudioFormat.WEBM_OPUS
                    )
                    
                    if client_data:
                        # Send to client
                        await self.client_ws.send_bytes(client_data)
                        logger.debug(f"Sent {len(client_data)} bytes to client")
                        
                        # Log to session recorder
                        if self.recorder:
                            # Estimate duration from PCM data
                            duration_ms = int((len(pcm_data) / (2 * self.channels * self.sample_rate)) * 1000)
                            self.recorder.log_ai_audio(duration_ms, len(client_data))
                    
                    self.is_model_speaking = False
                    
                except asyncio.TimeoutError:
                    # No data from model, continue
                    continue
                    
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("PersonaPlex connection closed")
                    break
                    
                except Exception as e:
                    logger.error(f"Error in model->client stream: {e}")
                    break
                    
        finally:
            logger.info("Model->client stream ended")
    
    async def process_stream(self):
        """
        Main streaming loop - manages bidirectional audio flow.
        
        This method:
        1. Connects to PersonaPlex
        2. Starts concurrent send/receive tasks
        3. Handles graceful shutdown
        4. Manages error recovery
        """
        try:
            # Connect to PersonaPlex sidecar
            await self.connect_model()
            
            # Start streaming
            self.is_running = True
            logger.info(f"AudioBridge streaming started: session={self.session_id}")
            
            # Create concurrent tasks for bidirectional streaming
            client_to_model_task = asyncio.create_task(
                self.handle_client_to_model()
            )
            model_to_client_task = asyncio.create_task(
                self.handle_model_to_client()
            )
            
            self.tasks = [client_to_model_task, model_to_client_task]
            
            # Wait for either task to complete (or error)
            done, pending = await asyncio.wait(
                self.tasks,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Signal shutdown to all tasks
            self.is_running = False
            
            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            logger.info("AudioBridge streaming completed")
            
        except ConnectionError as e:
            logger.error(f"Failed to connect to PersonaPlex: {e}")
            # Send error message to client
            try:
                await self.client_ws.send_json({
                    "type": "error",
                    "code": "personaplex_unavailable",
                    "message": "Audio service temporarily unavailable. Please try again later."
                })
            except:
                pass
            raise
            
        except Exception as e:
            logger.exception(f"AudioBridge error: {e}")
            raise
            
        finally:
            # Cleanup
            self.is_running = False
            await self.disconnect_model()
            logger.info(f"AudioBridge closed: session={self.session_id}")
    
    async def stop(self):
        """Stop the audio bridge and cleanup resources."""
        logger.info(f"Stopping AudioBridge: session={self.session_id}")
        self.is_running = False
        
        # Cancel all tasks
        for task in self.tasks:
            if not task.done():
                task.cancel()
        
        # Wait for tasks to finish
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        
        # Disconnect from PersonaPlex
        await self.disconnect_model()
        
        logger.info(f"AudioBridge stopped: session={self.session_id}")
